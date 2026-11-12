"""Клиент Nightscout (httpx, async).

    async with NightscoutClient(url, token) as ns:
        server_ms = await ns.get_server_time()
        raw = await ns.get_entries(since=last_ts)
        readings = clean(raw)

Ошибки — только наши исключения, чтобы pipeline и бот ловили одно семейство:
    NSAuthError     401/403 — неверный токен (бот: «проверьте токен»)
    NSUnavailable   таймаут, нет соединения, 5xx, 404 (бот/watchdog: ns_unreachable)
    NSBadResponse   ответ не JSON или не того формата
Пустой ответ на entries — не ошибка, а [] (новых показаний нет).

Токен передаётся как ?token=... и НИКОГДА не попадает в логи и тексты ошибок.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

# httpx на уровне INFO пишет полный URL запроса — вместе с ?token=. Глушим.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
DEFAULT_COUNT = 288          # сутки при шаге 5 мин
MAX_COUNT_SINCE = 2000       # при запросе «с момента» — с запасом


class NSError(Exception):
    """Базовая ошибка клиента Nightscout."""


class NSAuthError(NSError):
    """401/403: токен неверный или без прав на чтение."""


class NSUnavailable(NSError):
    """Сервер недоступен: таймаут, нет соединения, 404/5xx."""


class NSBadResponse(NSError):
    """Ответ не похож на Nightscout."""


def _to_ms(since: datetime | int | float) -> int:
    if isinstance(since, datetime):
        if since.tzinfo is None:
            raise ValueError("since: нужен datetime с tzinfo (UTC)")
        return int(since.timestamp() * 1000)
    return int(since)


class NightscoutClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,  # для тестов: httpx.MockTransport
    ) -> None:
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("Адрес Nightscout должен начинаться с http:// или https://")
        self._token = token.strip() if token else None
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> NightscoutClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        if self._token:
            params["token"] = self._token
        try:
            resp = await self._http.get(path, params=params)
        except httpx.TimeoutException:
            raise NSUnavailable("Nightscout не ответил вовремя (таймаут)") from None
        except httpx.HTTPError as e:
            # str(e) может содержать URL с токеном — наружу отдаём только тип
            raise NSUnavailable(f"Нет соединения с Nightscout ({type(e).__name__})") from None

        if resp.status_code in (401, 403):
            raise NSAuthError(f"Nightscout отклонил токен (HTTP {resp.status_code})")
        if resp.status_code >= 400:
            raise NSUnavailable(f"Nightscout ответил HTTP {resp.status_code}")
        if not resp.content.strip():
            return None
        try:
            return resp.json()
        except ValueError:
            raise NSBadResponse("Ответ Nightscout — не JSON") from None

    async def get_entries(
        self, since: datetime | int | None = None, count: int | None = None
    ) -> list[dict[str, Any]]:
        """Сырые записи. since — строго после этого момента (datetime UTC или мс).

        Порядок как у Nightscout (новые первыми) — сортирует clean().
        """
        params: dict[str, Any] = {}
        if since is not None:
            params["find[date][$gt]"] = _to_ms(since)
            params["count"] = count or MAX_COUNT_SINCE
        else:
            params["count"] = count or DEFAULT_COUNT
        data = await self._get("/api/v1/entries.json", params)
        if data is None:
            return []
        if not isinstance(data, list):
            raise NSBadResponse("entries.json: ожидался список")
        return [e for e in data if isinstance(e, dict)]

    async def get_server_time(self) -> int:
        """serverTimeEpoch из /api/v1/status.json, мс."""
        data = await self._get("/api/v1/status.json")
        if not isinstance(data, dict):
            raise NSBadResponse("status.json: ожидался объект")
        ms = data.get("serverTimeEpoch")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
            raise NSBadResponse("status.json: нет serverTimeEpoch")
        return int(ms)

    async def check(self) -> None:
        """Проверка для онбординга (Dev 3): адрес живой и токен читает данные.

        Бросает NSAuthError / NSUnavailable / NSBadResponse; ничего не вернула — всё в порядке.
        """
        await self.get_server_time()
        await self.get_entries(count=1)

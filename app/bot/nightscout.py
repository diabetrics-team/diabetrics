"""
ВРЕМЕННЫЙ доступ к Nightscout для бота: проверка подключения в онбординге и данные для /now.

Владелец: Dev 3. Когда Dev 1 выложит app/ns/client.py (get_entries, get_server_time),
заменить вызовы в handlers/onboarding.py и handlers/now.py на его клиент и удалить этот файл.
"""
from __future__ import annotations

import httpx

TIMEOUT = 10.0  # секунд


class NsError(Exception):
    """Nightscout не отдал данные."""


class NsUnavailable(NsError):
    """Не отвечает, таймаут, ошибка сервера или по адресу вообще не Nightscout."""


class NsBadToken(NsError):
    """401: токен неверный или без токена не пускают."""


async def _get(url: str, path: str, token: str | None, params: dict | None = None):
    params = dict(params or {})
    if token:
        params["token"] = token
    try:
        # follow_redirects: многие сайты перенаправляют http:// на https://
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url + path, params=params)
    except httpx.HTTPError as e:
        # Без подробностей: в тексте ошибки httpx бывает адрес с токеном.
        raise NsUnavailable("нет соединения") from e
    if response.status_code == 401:
        raise NsBadToken()
    if response.status_code != 200:
        raise NsUnavailable(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as e:
        raise NsUnavailable("ответ не JSON") from e


async def get_server_time(url: str, token: str | None) -> int:
    """serverTimeEpoch из /api/v1/status.json, миллисекунды."""
    status = await _get(url, "/api/v1/status.json", token)
    try:
        return int(status["serverTimeEpoch"])
    except (KeyError, TypeError, ValueError) as e:
        raise NsUnavailable("нет serverTimeEpoch") from e


async def get_entries(url: str, token: str | None, count: int = 36) -> list[dict]:
    """Последние count записей, сырые (как отдаёт Nightscout). 36 записей ≈ 3 часа."""
    entries = await _get(url, "/api/v1/entries.json", token, {"count": count})
    if not isinstance(entries, list):
        raise NsUnavailable("неожиданный ответ")
    return entries


async def check(url: str, token: str | None) -> None:
    """
    Проверка для онбординга: это Nightscout и токен даёт читать показания.
    status.json в настоящем Nightscout бывает открыт без токена, поэтому читаем и entries.
    """
    await get_server_time(url, token)
    await get_entries(url, token, count=1)

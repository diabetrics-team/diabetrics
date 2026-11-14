"""
/health — внешняя проверка, что бот жив и pipeline крутится.

Владелец: Dev 1.

    GET http://<сервер>:8080/health
    200 {"status": "ok", ...}        — последний тик pipeline был недавно
    200 {"status": "starting", ...}  — процесс только запустился, первого тика ещё не было
    503 {"status": "stale", ...}     — pipeline давно не отрабатывал: процесс завис

Только стандартная библиотека (asyncio), без веб-фреймворка: ответ один, маршрут один.
Порт — HEALTH_PORT (по умолчанию 8080), 0 — выключить.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from app.pipeline import HEALTH, Health

log = logging.getLogger(__name__)


def status(health: Health, poll_seconds: float, now_mono: float | None = None) -> tuple[int, dict]:
    """(HTTP-код, тело). Тик считается свежим, если был не позже 3 периодов опроса + 30 с."""
    now_mono = time.monotonic() if now_mono is None else now_mono
    limit = 3 * poll_seconds + 30
    body: dict = {
        "users": health.users,
        "ns_down": health.ns_down,
        "errors_last_tick": health.errors,
        "last_tick_at": health.last_tick_at.isoformat() if health.last_tick_at else None,
        "uptime_s": round(now_mono - health.started_mono),
    }
    if health.last_tick_mono is None:
        body["seconds_since_tick"] = None
        if now_mono - health.started_mono <= limit:
            return 200, {"status": "starting", **body}
        return 503, {"status": "stale", **body}
    age = now_mono - health.last_tick_mono
    body["seconds_since_tick"] = round(age)
    return (200, {"status": "ok", **body}) if age <= limit else (503, {"status": "stale", **body})


_REASONS = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                  poll_seconds: float) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        parts = request_line.decode("latin-1").split()
        path = parts[1].split("?")[0] if len(parts) >= 2 else ""
        if path in ("/health", "/health/"):
            code, body = status(HEALTH, poll_seconds)
        else:
            code, body = 404, {"status": "not found"}
        payload = json.dumps(body, ensure_ascii=False).encode()
        writer.write(
            f"HTTP/1.1 {code} {_REASONS[code]}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload
        )
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def start(port: int, poll_seconds: float, host: str = "0.0.0.0") -> asyncio.Server | None:
    if port <= 0:
        return None
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, poll_seconds), host=host, port=port,
    )
    log.info("/health слушает порт %s", port)
    return server

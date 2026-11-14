"""
Настройка логов и вырезание секретов.

Владелец: Dev 1.

setup_logging() вызывается один раз при старте (app/main.py). Фильтр Redact стоит
на обработчиках корневого логгера, поэтому чистит записи ВСЕХ библиотек:
aiogram, httpx, APScheduler и наши. Вырезается:
- токен Nightscout в URL: ?token=... / &token=...
- заголовок api-secret
- токен Telegram-бота: 123456789:AA... (в том числе внутри /bot<токен>/ в URL)
"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    (re.compile(r"([?&]token=)[^&\s'\"]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(api-secret['\"]?\s*[:=]\s*['\"]?)[^\s,'\"}]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}"), "***"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, None
        if record.exc_info and not record.exc_text:
            # Трейсбек форматируем сразу, чтобы вырезать токен и из него.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, Redact) for f in handler.filters):
            handler.addFilter(Redact())
    # Шумные библиотеки: на INFO пишут каждый HTTP-запрос
    for name in ("httpx", "httpcore", "apscheduler.executors.default"):
        logging.getLogger(name).setLevel(logging.WARNING)

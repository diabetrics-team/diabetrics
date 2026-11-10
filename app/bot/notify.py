"""
Отправка уведомлений в Telegram.

Владелец: Dev 3. ЗАГЛУШКА: печатает в консоль, Telegram не нужен.
Заменить на: bot.send_message с текстом из texts.py по alert.kind и alert.value;
у CRITICAL — inline-кнопка «Я в порядке» с callback_data=f"ack:{alert.id}".
"""
from __future__ import annotations

from app.models import Alert, Level

ICONS = {Level.CRITICAL: "🔴", Level.IMPORTANT: "🟠", Level.INFO: "🔵"}


async def notify(alert: Alert, chat_id: int) -> None:
    value = f" {alert.value:.1f} ммоль/л" if alert.value is not None else ""
    button = "  [Я в порядке]" if alert.needs_ack else ""
    print(f"[notify → {chat_id}] {ICONS[alert.level]} {alert.kind}{value} "
          f"({alert.created_at:%H:%M} UTC){button}")


async def notify_trusted(alert: Alert, trusted_chat_id: int, minutes_without_ack: int) -> None:
    print(f"[notify_trusted → {trusted_chat_id}] {alert.kind} {alert.value}, "
          f"без ответа {minutes_without_ack} мин")

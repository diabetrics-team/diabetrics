"""
Эскалация критических уведомлений: повтор → доверенное лицо.

Владелец: Dev 2. ЗАГЛУШКА: ничего не повторяет и никого не будит.
Логика: нет «Я в порядке» 5 мин → REPEAT; N мин (по умолчанию 10) → NOTIFY_TRUSTED.
Тоже чистая функция: состояние хранится в самих Alert (в БД), не в памяти.
"""
from __future__ import annotations

from datetime import datetime

from app.models import Action, Alert

REPEAT_AFTER_MIN = 5
TRUSTED_AFTER_MIN = 10


def escalation_step(open_alerts: list[Alert], now: datetime) -> list[Action]:
    # open_alerts — критические без подтверждения. Возвращает, что отправить
    return []


def ack(alert: Alert, who: int, now: datetime) -> Alert:
    # Кнопка «Я в порядке». who — telegram id нажавшего. Сохраняет в БД pipeline
    alert.acked_at = now
    alert.acked_by = who
    return alert

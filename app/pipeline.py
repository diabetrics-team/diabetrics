"""
Основной цикл: Nightscout → clean → predict → evaluate → escalation → notify.

Владелец: Dev 1. ЗАГЛУШКА: прогоняет готовый JSON-сценарий без сети, БД и Telegram.
Нужна, чтобы с первого дня видеть, что модули стыкуются:

    python -m app.pipeline night_hypo

Заменить на: APScheduler раз в минуту, клиент Nightscout, БД, антиспам по recent_alerts.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

from app import clock
from app.alerts.engine import evaluate
from app.alerts.escalation import escalation_step
from app.bot.notify import notify
from app.forecast import predict
from app.models import Alert, ModeState
from app.ns.clean import clean

HISTORY = timedelta(hours=2)
DATA_DIR = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"


async def replay(scenario: str, mode: ModeState | None = None, chat_id: int = 0) -> list[Alert]:
    """Проигрывает сценарий по одному показанию, как будто они приходят в реальном времени."""
    readings = clean(json.loads((DATA_DIR / f"{scenario}.json").read_text()))
    mode = mode or ModeState()
    sent: list[Alert] = []
    for i, r in enumerate(readings):
        clock.freeze(r.ts)
        now = clock.now()
        history = [x for x in readings[: i + 1] if now - x.ts <= HISTORY]
        recent = [a for a in sent if now - a.created_at <= timedelta(hours=1)]
        for alert in evaluate(history, predict(history), mode, now, recent):
            alert.id = len(sent) + 1          # в настоящем pipeline id даёт БД
            sent.append(alert)
            await notify(alert, chat_id)
        open_alerts = [a for a in sent if a.needs_ack and a.acked_at is None]
        for action in escalation_step(open_alerts, now):
            print(f"[escalation] {action.type.value} → alert {action.alert.id}")
    clock.unfreeze()
    return sent


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "night_hypo"
    result = asyncio.run(replay(name))
    print(f"--- {name}: {len(result)} уведомлений")

"""
Основной цикл: Nightscout → clean → predict → evaluate → escalation → notify.

Владелец: Dev 1.

    tick()                         — один проход по всем пользователям (APScheduler в main.py)
    python -m app.pipeline --live URL [--token T]
                                   — обкатка без Telegram: опрос fake-NS, уведомления в консоль
    python -m app.pipeline night_hypo
                                   — прогон JSON-сценария без сети, БД и Telegram (replay)

Гарантии:
- одно показание не обрабатывается дважды: запрашиваем только новее последнего в БД,
  сохраняем с уникальностью (user_id, ts);
- открытые критические читаются из БД, поэтому эскалация переживает перезапуск;
- повтор и эскалация идемпотентны: даже если escalation_step вернёт REPEAT на каждом
  тике, повтор уйдёт не чаще REPEAT_EVERY, а доверенному — один раз;
- ошибка у одного пользователя не мешает остальным.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app import clock
from app.alerts.engine import evaluate
from app.alerts.escalation import escalation_step
from app.bot.notify import notify, notify_trusted
from app.forecast import predict
from app.models import ActionType, Alert, AlertKind, Level, ModeState
from app.ns.clean import clean

log = logging.getLogger(__name__)

HISTORY = timedelta(hours=2)          # сколько истории отдаём predict/evaluate
RECENT = timedelta(hours=1)           # recent_alerts для антиспама
FIRST_FETCH_COUNT = 36                # первый опрос нового пользователя: ~3 ч при шаге 5 мин
REPEAT_EVERY = timedelta(minutes=5)   # повтор критического не чаще
# Временная защита от спама, пока у Dev 2 нет антиспама (день 4): тот же kind
# не чаще раза в DEDUP_WINDOW. С антиспамом движка не конфликтует — просто дублирует.
DEDUP_WINDOW = timedelta(minutes=15)

DATA_DIR = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"


def _client(url: str, token: str | None) -> Any:
    from app.ns.client import NightscoutClient   # httpx нужен только живому режиму

    return NightscoutClient(url, token)


_RANK = {Level.INFO: 0, Level.IMPORTANT: 1, Level.CRITICAL: 2}


def _is_duplicate(alert: Alert, recent: list[Alert], now: datetime) -> bool:
    """Такое же уведомление того же или более высокого уровня уже было за DEDUP_WINDOW.

    Повышение уровня (no_data: важное на 20-й минуте → критическое на 30-й) проходит всегда.
    Пока критическое того же вида без «Я в порядке», новое не создаётся: повторы — дело
    эскалации, иначе у каждого дубля была бы своя цепочка до доверенного лица.
    """
    for a in recent:
        if a.kind != alert.kind:
            continue
        if a.needs_ack and a.acked_at is None:
            return True     # то же критическое ещё ждёт ответа — его повторяет эскалация
        if now - a.created_at < DEDUP_WINDOW and _RANK[Level(a.level)] >= _RANK[Level(alert.level)]:
            return True
    return False


# --------------------------------------------------------------------------- tick ---

async def tick(repo: Any = None, client_factory: Any = None) -> None:
    """Один проход по всем активным пользователям. repo/client_factory — подмена в тестах."""
    if repo is None:
        from app import db as repo
    client_factory = client_factory or _client
    for user in repo.active_users():
        try:
            await process_user(user, repo, client_factory)
        except Exception:
            # Ошибка одного пользователя (битые данные, баг в правиле) не роняет цикл.
            log.exception("pipeline: ошибка при обработке user_id=%s", user.id)


async def process_user(user: Any, repo: Any, client_factory: Any) -> list[Alert]:
    """Возвращает отправленные новые уведомления (для тестов и логов)."""
    from app.ns.client import NSError

    last_ts = repo.last_reading_ts(user.id)
    try:
        async with client_factory(user.ns_url, user.ns_token) as ns:
            if clock.uses_server_time():
                clock.sync_with_server(await ns.get_server_time())
            if last_ts is None:
                raw = await ns.get_entries(count=FIRST_FETCH_COUNT)
            else:
                raw = await ns.get_entries(since=last_ts)
    except NSError as e:
        # Отдельное уведомление ns_unreachable — watchdog, день 4.
        log.warning("pipeline: Nightscout user_id=%s: %s", user.id, e)
        if clock.uses_server_time():
            # Демо-часы идут по серверу; без связи «сейчас» неизвестно — иначе ложный no_data.
            return []
        raw = []            # прод: evaluate всё равно запускаем — no_data считается по времени

    new = repo.save_readings(user.id, [r for r in clean(raw) if last_ts is None or r.ts > last_ts])
    now = clock.now()
    if new:
        log.info("pipeline: user_id=%s +%d показаний, последнее %.1f в %s",
                 user.id, len(new), new[-1].mmol, f"{new[-1].ts:%H:%M}")

    history = repo.readings_since(user.id, now - HISTORY)
    mode = repo.get_mode(user.id)
    recent = repo.recent_alerts(user.id, now - RECENT)
    seen = {a.id for a in recent}
    recent += [a for a in repo.open_alerts(user.id) if a.id not in seen]   # открытые старше часа

    sent: list[Alert] = []
    for alert in evaluate(history, predict(history), mode, now, recent):
        if _is_duplicate(alert, recent, now):
            continue
        alert.user_id = user.id
        repo.save_alert(alert, sent_at=now)       # сначала БД: кнопке нужен alert.id
        message_id = await notify(alert, user.chat_id)
        if message_id is not None:
            repo.mark_sent(alert.id, now, message_id)
        recent.append(alert)
        sent.append(alert)

    await _escalate(user, repo, now)
    return sent


async def _escalate(user: Any, repo: Any, now: datetime) -> None:
    open_alerts = repo.open_alerts(user.id)
    if not open_alerts:
        return
    for action in escalation_step(open_alerts, now):
        alert = action.alert
        if action.type is ActionType.REPEAT:
            last = repo.last_sent_at(alert.id)
            if last is not None and now - last < REPEAT_EVERY:
                continue
            await notify(alert, user.chat_id)
            repo.mark_sent(alert.id, now)
        elif action.type is ActionType.NOTIFY_TRUSTED:
            if not repo.mark_escalated(alert.id, now):      # уже отправляли
                continue
            minutes = action.extra.get(
                "minutes_without_ack", int((now - alert.created_at).total_seconds() // 60)
            )
            chats = repo.trusted_chat_ids(user.id)
            if not chats:
                log.warning("pipeline: эскалация alert_id=%s, но доверенного лица нет", alert.id)
            for chat_id in chats:
                await notify_trusted(alert, chat_id, minutes)


# ------------------------------------------------------------------- живой прогон ---

async def run_live(url: str, token: str | None, every: float, db_url: str) -> None:
    """Обкатка без Telegram: демо-пользователь chat_id=0, уведомления печатаются в консоль."""
    from app import db

    db.configure(db_url)
    user = db.upsert_user(0, clock.now(), name="demo", ns_url=url, ns_token=token, is_active=True)
    print(f"[live] {url}  CLOCK={os.getenv('CLOCK', 'system')}  опрос раз в {every:g} с  "
          f"БД {db_url}  user_id={user.id}")
    while True:
        await tick()
        print(f"[live] {clock.now():%Y-%m-%d %H:%M} UTC  (×{clock.server_rate():.0f})")
        await asyncio.sleep(every)


# ------------------------------------------------------------- replay сценария ---

async def replay(scenario: str, mode: ModeState | None = None, chat_id: int = 0) -> list[Alert]:
    """Проигрывает JSON-сценарий по одному показанию, без сети и БД (для тестов)."""
    readings = clean(json.loads((DATA_DIR / f"{scenario}.json").read_text()))
    mode = mode or ModeState()
    sent: list[Alert] = []
    for i, r in enumerate(readings):
        clock.freeze(r.ts)
        now = clock.now()
        history = [x for x in readings[: i + 1] if now - x.ts <= HISTORY]
        recent = [a for a in sent if now - a.created_at <= RECENT or (a.needs_ack and a.acked_at is None)]
        for alert in evaluate(history, predict(history), mode, now, recent):
            if _is_duplicate(alert, recent, now):
                continue
            alert.id = len(sent) + 1          # в настоящем pipeline id даёт БД
            sent.append(alert)
            recent.append(alert)
            await notify(alert, chat_id)
        open_alerts = [a for a in sent if a.needs_ack and a.acked_at is None]
        for action in escalation_step(open_alerts, now):
            print(f"[escalation] {action.type.value} → alert {action.alert.id}")
    clock.unfreeze()
    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description="DiaBetrics pipeline без Telegram")
    ap.add_argument("scenario", nargs="?", default="night_hypo", help="replay JSON-сценария")
    ap.add_argument("--live", metavar="URL", help="опрашивать Nightscout / fake-NS по адресу")
    ap.add_argument("--token")
    ap.add_argument("--every", type=float, default=float(os.getenv("POLL_SECONDS", "5")))
    ap.add_argument("--db", default="sqlite:///data/live-test.db")
    a = ap.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.live:
        try:
            asyncio.run(run_live(a.live, a.token, a.every, a.db))
        except KeyboardInterrupt:
            pass
        return
    result = asyncio.run(replay(a.scenario))
    print(f"--- {a.scenario}: {len(result)} уведомлений")
    kinds = sorted({AlertKind(x.kind).value for x in result})
    print(f"    виды: {', '.join(kinds) or '—'}")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    sys.exit(main())

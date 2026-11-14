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
- состояние повтора и эскалации читается из БД;
- ошибка у одного пользователя не мешает остальным;
- watchdog: Nightscout не отвечает дольше NS_DOWN_ALERT_AFTER → одно важное ns_unreachable
  на сбой; состояние для /health — в HEALTH;
- пользователь заблокировал бота → is_active=False, его больше не опрашиваем;
- каждое уведомление, повтор, эскалация пишутся в лог (токены вырезает app/logs.py).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app import clock
from app.alerts.config import TRUSTED_AFTER_MINUTES
from app.alerts.engine import evaluate, history_start_for_hysteresis, last_low_recovery
from app.alerts.escalation import LOW_ESCALATION_KINDS, escalation_step
from app.bot.night import effective_mode
from app.bot.notify import notify, notify_trusted
from app.forecast import predict
from app.models import ActionType, Alert, AlertKind, Level, ModeState
from app.ns.clean import clean

log = logging.getLogger(__name__)

HISTORY = timedelta(hours=2)          # сколько истории отдаём predict/evaluate
RECENT = timedelta(hours=1)           # recent_alerts для антиспама
FIRST_FETCH_COUNT = 36                # первый опрос нового пользователя: ~3 ч при шаге 5 мин
NS_DOWN_ALERT_AFTER = timedelta(minutes=10)   # watchdog: столько без связи → ns_unreachable
TRUSTED_MAX_ATTEMPTS = 10   # попыток доставки доверенному на тревогу (одна на тик)

DATA_DIR = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"


def _client(url: str, token: str | None) -> Any:
    from app.ns.client import NightscoutClient   # httpx нужен только живому режиму

    return NightscoutClient(url, token)


# ------------------------------------------------------------------- состояние ---

@dataclass
class Health:
    """Что видно снаружи через /health. Время — настоящее (не clock.now): это про живость процесса."""

    started_mono: float = field(default_factory=time.monotonic)
    last_tick_mono: float | None = None
    last_tick_at: datetime | None = None
    users: int = 0
    ns_down: int = 0
    errors: int = 0               # пользователей с ошибкой на последнем тике


HEALTH = Health()
_ns_down_since: dict[int, datetime] = {}   # user_id → когда Nightscout перестал отвечать
_ns_down_alerted: set[int] = set()         # кому уже отправили ns_unreachable за этот сбой


def _is_blocked(error: Exception) -> bool:
    """Пользователь заблокировал бота (aiogram TelegramForbiddenError), без импорта aiogram."""
    return type(error).__name__ == "TelegramForbiddenError"


async def _send(user: Any, repo: Any, alert: Alert, *, repeat: bool = False) -> int | None:
    """notify() + лог. Ошибка Telegram не роняет обработку пользователя."""
    what = "repeat" if repeat else "alert"
    try:
        message_id = await notify(alert, user.chat_id)
    except Exception as e:
        if _is_blocked(e):
            repo.set_active(user.id, False)
            log.warning("user_id=%s заблокировал бота — опрос остановлен", user.id)
        else:
            log.exception("%s alert_id=%s user_id=%s: не отправлено", what, alert.id, user.id)
        return None
    log.info("%s alert_id=%s user_id=%s %s/%s value=%s",
             what, alert.id, user.id, Level(alert.level).value, AlertKind(alert.kind).value,
             alert.value)
    return message_id


async def _ns_failed(user: Any, repo: Any, error: Exception) -> None:
    now = clock.now()
    since = _ns_down_since.setdefault(user.id, now)
    down_for = now - since
    log.warning("Nightscout user_id=%s не отвечает %d мин: %s",
                user.id, down_for.total_seconds() // 60, error)
    if user.id in _ns_down_alerted or down_for < NS_DOWN_ALERT_AFTER:
        return
    alert = Alert(Level.IMPORTANT, AlertKind.NS_UNREACHABLE, now, user_id=user.id)
    repo.save_alert(alert, sent_at=now)
    message_id = await _send(user, repo, alert)
    if message_id is not None:
        repo.mark_sent(alert.id, now, message_id)
    _ns_down_alerted.add(user.id)


def _ns_ok(user: Any) -> None:
    since = _ns_down_since.pop(user.id, None)
    if since is not None:
        _ns_down_alerted.discard(user.id)
        log.info("Nightscout user_id=%s снова отвечает (не было связи %d мин)",
                 user.id, (clock.now() - since).total_seconds() // 60)


# --------------------------------------------------------------------------- tick ---

async def tick(repo: Any = None, client_factory: Any = None) -> None:
    """Один проход по всем активным пользователям. repo/client_factory — подмена в тестах."""
    if repo is None:
        from app import db as repo
    client_factory = client_factory or _client
    users = repo.active_users()
    errors = 0
    for user in users:
        try:
            await process_user(user, repo, client_factory)
        except Exception:
            # Ошибка одного пользователя (битые данные, баг в правиле) не роняет цикл.
            errors += 1
            log.exception("pipeline: ошибка при обработке user_id=%s", user.id)
    HEALTH.last_tick_mono = time.monotonic()
    HEALTH.last_tick_at = datetime.now(UTC)
    HEALTH.users, HEALTH.errors, HEALTH.ns_down = len(users), errors, len(_ns_down_since)


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
        await _ns_failed(user, repo, e)
        if clock.uses_server_time():
            # Демо-часы идут по серверу; без связи «сейчас» неизвестно — иначе ложный no_data.
            return []
        raw = []            # прод: evaluate всё равно запускаем — no_data считается по времени
    else:
        _ns_ok(user)

    new = repo.save_readings(user.id, [r for r in clean(raw) if last_ts is None or r.ts > last_ts])
    now = clock.now()
    if new:
        log.info("pipeline: user_id=%s +%d показаний, последнее %.1f в %s",
                 user.id, len(new), new[-1].mmol, f"{new[-1].ts:%H:%M}")

    history = repo.readings_since(user.id, now - HISTORY)
    if not history and last_ts is not None:
        # Даже после длительной потери сигнала нужно помнить последний замер.
        history = repo.readings_since(user.id, last_ts - HISTORY)
    mode = effective_mode(repo.get_mode(user.id), user, now)   # ночной режим по расписанию (Dev 3)
    recent = list({a.id: a for a in (
        repo.latest_episode_alerts(user.id, now)
        + repo.recent_alerts(user.id, now - RECENT)
        + repo.open_alerts(user.id)
    )}.values())
    since = history_start_for_hysteresis(history, recent, now)
    if since is not None:
        history = repo.readings_since(user.id, since)

    sent: list[Alert] = []
    for alert in evaluate(history, predict(history), mode, now, recent):
        alert.user_id = user.id
        repo.save_alert(alert, sent_at=now)       # сначала БД: кнопке нужен alert.id
        message_id = await _send(user, repo, alert)
        if message_id is not None:
            repo.mark_sent(alert.id, now, message_id)
        recent.append(alert)
        sent.append(alert)

    await _escalate(user, repo, now)
    return sent


async def _escalate(user: Any, repo: Any, now: datetime) -> None:
    open_alerts = [a for a in repo.open_alerts(user.id) if a.id is not None and a.escalated_at is None]
    repeated = {
        a.id for a in open_alerts
        if (last := repo.last_sent_at(a.id)) is not None and last > a.created_at
    }
    low_recovered_at = None
    low_episode_escalated = False
    if any(a.kind in LOW_ESCALATION_KINDS for a in open_alerts):
        low_history = [
            a for a in repo.recent_alerts(user.id, datetime.min.replace(tzinfo=UTC))
            if a.kind in LOW_ESCALATION_KINDS and a.created_at <= now
            and (a.escalated_at is not None or a in open_alerts)
        ]
        if low_history:
            since = min(a.created_at for a in low_history) - HISTORY
            low_recovered_at = last_low_recovery(repo.readings_since(user.id, since), now)
            low_episode_escalated = any(
                a.escalated_at is not None and a.escalated_at <= now
                and (low_recovered_at is None or a.created_at > low_recovered_at)
                for a in low_history
            )
    for action in escalation_step(
        open_alerts, now, repeated_alert_ids=repeated,
        low_recovered_at=low_recovered_at, low_episode_escalated=low_episode_escalated,
        trusted_after_minutes=getattr(user, "escalation_minutes", TRUSTED_AFTER_MINUTES),
    ):
        alert = action.alert
        if action.type is ActionType.REPEAT:
            message_id = await _send(user, repo, alert, repeat=True)
            repo.mark_sent(alert.id, now, message_id)
        elif action.type is ActionType.NOTIFY_TRUSTED:
            minutes = action.extra.get(
                "minutes_without_ack", int((now - alert.created_at).total_seconds() // 60)
            )
            await _notify_trusted_all(user, repo, alert, minutes, now)


async def _notify_trusted_all(user: Any, repo: Any, alert: Alert, minutes: int, now: datetime) -> None:
    """
    Эскалация доверенным лицам с учётом доставки по каждому контакту.

    escalated_at ставится, только когда всем доверенным доставлено (или попытки исчерпаны).
    Пока он пустой, escalation_step на следующем тике снова вернёт NOTIFY_TRUSTED, и мы
    повторим отправку тем, кому не дошло; кому уже доставили — пропускаем.
    Исключение из notify_trusted (таймаут, сеть, 5xx Telegram) — временный сбой, повторяем.
    Постоянные ошибки (доверенный заблокировал бота) notify_trusted обрабатывает сам и не
    бросает — повторять их бессмысленно.
    """
    chats = repo.trusted_chat_ids(user.id)
    if not chats:
        log.warning("escalation alert_id=%s: доверенного лица нет", alert.id)
        repo.mark_escalated(alert.id, now)
        return
    state = repo.trusted_delivery_state(alert.id)
    pending = []
    for chat_id in chats:
        delivered, attempts = state.get(chat_id, (False, 0))
        if delivered:
            continue
        if attempts >= TRUSTED_MAX_ATTEMPTS:
            continue
        pending.append(chat_id)

    for chat_id in pending:
        try:
            await notify_trusted(alert, chat_id, minutes)
        except Exception as e:
            attempts = repo.record_trusted_attempt(alert.id, chat_id, now, delivered=False)
            log.warning("escalation alert_id=%s → chat %s: не доставлено (%s), попытка %d из %d",
                        alert.id, chat_id, type(e).__name__, attempts, TRUSTED_MAX_ATTEMPTS)
            state[chat_id] = (False, attempts)
            continue
        repo.record_trusted_attempt(alert.id, chat_id, now, delivered=True)
        state[chat_id] = (True, state.get(chat_id, (False, 0))[1] + 1)
        log.info("escalation alert_id=%s user_id=%s → доверенному chat %s, без ответа %s мин",
                 alert.id, user.id, chat_id, minutes)

    undelivered = [c for c in chats if not state.get(c, (False, 0))[0]]
    exhausted = [c for c in undelivered if state.get(c, (False, 0))[1] >= TRUSTED_MAX_ATTEMPTS]
    if not undelivered or len(exhausted) == len(undelivered):
        repo.mark_escalated(alert.id, now)
        if exhausted:
            log.error("escalation alert_id=%s: не удалось доставить доверенным %s за %d попыток",
                      alert.id, exhausted, TRUSTED_MAX_ATTEMPTS)


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
    repeated: set[int] = set()
    for i, r in enumerate(readings):
        clock.freeze(r.ts)
        now = clock.now()
        history = [x for x in readings[: i + 1] if now - x.ts <= HISTORY]
        since = history_start_for_hysteresis(history, sent, now)
        if since is not None:
            history = [x for x in readings[: i + 1] if x.ts >= since]
        for alert in evaluate(history, predict(history), mode, now, sent):
            alert.id = len(sent) + 1          # в настоящем pipeline id даёт БД
            sent.append(alert)
            await notify(alert, chat_id)
        open_alerts = [a for a in sent if a.needs_ack and a.acked_at is None]
        recovered_at = last_low_recovery(history, now)
        episode_escalated = any(
            a.kind in LOW_ESCALATION_KINDS and a.escalated_at is not None
            and (recovered_at is None or a.created_at > recovered_at)
            for a in sent
        )
        for action in escalation_step(
            open_alerts, now, repeated_alert_ids=repeated,
            low_recovered_at=recovered_at, low_episode_escalated=episode_escalated,
        ):
            print(f"[escalation] {action.type.value} → alert {action.alert.id}")
            if action.type is ActionType.REPEAT:
                assert action.alert.id is not None
                repeated.add(action.alert.id)
            else:
                action.alert.escalated_at = now
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

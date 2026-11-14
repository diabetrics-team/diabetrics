"""
Прогон всех сценариев fake_cgm через настоящий pipeline.tick и сверка с scenarios.json.

Владелец: Dev 1.

Работает всё настоящее — clean, predict, evaluate, антиспам, эскалация, БД (SQLite в памяти).
Подменены только сеть (Nightscout читается прямо из fake_cgm/data/*.json) и Telegram
(уведомления собираются в список). Время идёт по минуте: clock.freeze.
У пользователя есть доверенное лицо, «Я в порядке» никто не нажимает — худший случай.

    python scripts/run_scenarios.py                    # все случаи, отчёт в консоль
    python scripts/run_scenarios.py --md report.md     # + отчёт в Markdown (для презентации)
    python scripts/run_scenarios.py --only night_hypo
    python scripts/run_scenarios.py --repo memory      # без SQLAlchemy: БД-подделка из тестов

Код выхода 1, если хоть одна проверка не прошла.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import clock, pipeline  # noqa: E402
from app.models import AlertKind, Level, Mode, ModeState  # noqa: E402
from app.ns.clean import clean  # noqa: E402

DATA = ROOT / "fake_cgm" / "data"
MSK = timezone(timedelta(hours=3))
USER_CHAT, TRUSTED_CHAT = 111, 999
TAIL = timedelta(minutes=60)     # после последнего показания: ловим no_data и эскалацию
REACTION = timedelta(minutes=5)  # через сколько пользователь включает режим после уведомления
NIGHT_OFF = ("00:00", "00:00")   # night_start == night_end — расписание выключено (как в /settings)

CRIT, IMP = Level.CRITICAL, Level.IMPORTANT
K = AlertKind


# ------------------------------------------------------------------ сбор результата ---

@dataclass
class Sent:
    at: datetime
    level: Level
    kind: AlertKind
    value: float | None
    alert_id: int | None
    to_trusted: bool = False

    @property
    def hhmm(self) -> str:
        return f"{self.at.astimezone(MSK):%H:%M}"


@dataclass
class Run:
    start: datetime
    data_end: datetime                     # время последнего показания сценария
    sent: list[Sent] = field(default_factory=list)
    points: int = 0

    def in_data(self, s: Sent) -> bool:
        """До конца данных. После — датчик «пропал», и no_data там правильный ответ."""
        return s.at <= self.data_end + timedelta(minutes=19)

    def new(self, kind: AlertKind | None = None, level: Level | None = None) -> list[Sent]:
        """Новые уведомления (без повторов эскалации и сообщений доверенному)."""
        seen: set[int | None] = set()
        out = []
        for s in self.sent:
            if s.to_trusted or (s.alert_id is not None and s.alert_id in seen):
                continue
            seen.add(s.alert_id)
            if (kind is None or s.kind is kind) and (level is None or s.level is level):
                out.append(s)
        return out

    def trusted(self) -> list[Sent]:
        return [s for s in self.sent if s.to_trusted]

    def first(self, kind: AlertKind, level: Level | None = None) -> Sent | None:
        found = self.new(kind, level)
        return found[0] if found else None


# ------------------------------------------------------------------- проверки ---

Check = Callable[[Run, dict], tuple[bool, str]]


def _t(facts: dict, key: str, day_of: datetime) -> datetime | None:
    """'01:50' из facts → datetime рядом с началом сценария (сценарий может перейти через полночь)."""
    hhmm = facts.get(key)
    if not hhmm:
        return None
    h, m = map(int, hhmm.split(":"))
    t = day_of.astimezone(MSK).replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t >= day_of - timedelta(minutes=1) else t + timedelta(days=1)


def none_of(*levels: Level) -> Check:
    """Пока идут данные — ни одного уведомления этих уровней."""
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        bad = [s for s in run.new() if s.level in levels and run.in_data(s)]
        names = "/".join(lv.value for lv in levels)
        return not bad, f"нет {names}" + (f" — пришло: {_fmt(bad)}" if bad else "")
    return check


def has(kind: AlertKind, level: Level, at_key: str | None = None, *, slack: int = 10,
        before_key: str | None = None) -> Check:
    """Есть kind/level. at_key — не позже facts[at_key] + slack мин; before_key — строго раньше."""
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        s = run.first(kind, level)
        label = f"{level.value} {kind.value}"
        if s is None:
            return False, f"{label} — НЕ ПРИШЛО"
        start = run.start
        if at_key:
            due = _t(facts, at_key, start)
            if due and s.at > due + timedelta(minutes=slack):
                return False, f"{label} в {s.hhmm} — поздно (ожидалось ≈{facts[at_key]})"
        if before_key:
            due = _t(facts, before_key, start)
            if due and s.at >= due:
                return False, f"{label} в {s.hhmm} — не раньше {facts[before_key]}"
        return True, f"{label} в {s.hhmm}"
    return check


def absent(kind: AlertKind, level: Level | None = None) -> Check:
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        bad = run.new(kind, level)
        label = f"{level.value + ' ' if level else ''}{kind.value}"
        return not bad, f"нет {label}" + (f" — пришло: {_fmt(bad)}" if bad else "")
    return check


def count(kind: AlertKind, n: int) -> Check:
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        got = run.new(kind)
        return len(got) == n, f"{kind.value} ровно {n} раз — пришло {len(got)}" + (
            f": {_fmt(got)}" if got else "")
    return check


def trusted(expected: bool) -> Check:
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        got = run.trusted()
        ok = bool(got) == expected
        if expected:
            return ok, f"доверенному лицу — {'в ' + got[0].hhmm if got else 'НЕ ушло'}"
        return ok, "доверенному не писали" + (f" — ушло: {_fmt(got)}" if got else "")
    return check


def points() -> Check:
    def check(run: Run, facts: dict) -> tuple[bool, str]:
        return run.points == facts["points"], f"показаний после очистки {run.points} (эталон {facts['points']})"
    return check


def _fmt(items: list[Sent]) -> str:
    return ", ".join(f"{s.hhmm} {s.level.value}/{s.kind.value}" for s in items[:6]) + (
        " …" if len(items) > 6 else "")


# ---------------------------------------------------------------- случаи прогона ---

@dataclass
class Case:
    scenario: str
    mode: Mode = Mode.NORMAL
    checks: list[Check] = field(default_factory=list)
    mode_on_first_low: Mode | None = None    # пользователь включает режим, прочитав первое low
    # Ночь по расписанию (Dev 3): в БД по умолчанию 23:00–07:00. В остальных случаях выключена,
    # иначе ночные сценарии всегда шли бы в «Ночном» и «Обычный» не проверялся бы.
    night_schedule: tuple[str, str] | None = None

    @property
    def title(self) -> str:
        m = self.mode_on_first_low or self.mode
        night = f" + ночь по расписанию {'–'.join(self.night_schedule)}" if self.night_schedule else ""
        return f"{self.scenario} · {m.value}{night}"


CASES = [
    Case("normal", checks=[none_of(CRIT, IMP)]),
    Case("night_hypo", checks=[
        has(K.LOW, IMP, "first_below_3_9"),
        has(K.VERY_LOW, CRIT, "first_below_3_0", slack=0),
        has(K.PREDICTED_LOW, CRIT, before_key="first_below_3_0"),
        trusted(True)]),
    Case("night_hypo", Mode.NIGHT, checks=[
        has(K.LOW, CRIT, "first_below_3_9"), absent(K.LOW, IMP), trusted(True)]),
    Case("night_hypo", night_schedule=("23:00", "07:00"), checks=[
        has(K.LOW, CRIT, "first_below_3_9"), absent(K.LOW, IMP)]),
    Case("meal", checks=[has(K.FAST_RISE, IMP, "first_fast_rise"), has(K.HIGH_1H, IMP, "above_10_0_for_1h_at")]),
    Case("meal", Mode.AFTER_MEAL, checks=[none_of(CRIT, IMP)]),
    Case("fast_drop", checks=[
        has(K.FAST_DROP, IMP, "first_fast_drop"), has(K.LOW, IMP, "first_below_3_9"),
        absent(K.VERY_LOW)]),
    Case("hypo_corrected", mode_on_first_low=Mode.AFTER_HYPO, checks=[
        count(K.LOW, 1), absent(K.STILL_LOW)]),
    Case("hypo_not_corrected", mode_on_first_low=Mode.AFTER_HYPO, checks=[
        has(K.LOW, IMP, "first_below_3_9"), has(K.STILL_LOW, CRIT)]),
    Case("hyper_long", checks=[
        has(K.HIGH_1H, IMP, "above_10_0_for_1h_at"), has(K.VERY_HIGH_1H, CRIT, "above_13_9_for_1h_at"),
        absent(K.FAST_RISE)]),
    Case("sensor_gap", checks=[has(K.NO_DATA, IMP), has(K.NO_DATA, CRIT)]),
    Case("noise_spike", checks=[none_of(CRIT), trusted(False)]),
    Case("borderline", checks=[has(K.LOW, IMP, "first_below_3_9")]),
    Case("meal_overbolus", checks=[
        has(K.FAST_RISE, IMP), has(K.HIGH_1H, IMP), has(K.LOW, IMP, "first_below_3_9"), absent(K.FAST_DROP)]),
    Case("meal_overbolus", Mode.AFTER_MEAL, checks=[
        has(K.LOW, IMP, "first_below_3_9"), absent(K.FAST_RISE), absent(K.HIGH_1H)]),
    Case("sensor_lost", checks=[has(K.NO_DATA, IMP), absent(K.NO_DATA, CRIT), none_of(CRIT)]),
    Case("dirty_data", checks=[points(), none_of(CRIT, IMP)]),
    Case("random_day", checks=[none_of(CRIT)]),
]


# ------------------------------------------------------------------ подмены ---

class FakeNS:
    """Отдаёт записи сценария, которые «уже случились» к clock.now()."""

    def __init__(self, entries: list[dict]) -> None:
        self.entries = entries

    def __call__(self, url, token):     # client_factory
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get_server_time(self) -> int:
        return int(clock.now().timestamp() * 1000)

    async def get_entries(self, since=None, count=None) -> list[dict]:
        now_ms = clock.now().timestamp() * 1000
        visible = [e for e in self.entries if e.get("date", 0) <= now_ms]
        if since is not None:
            visible = [e for e in visible if e.get("date", 0) > since.timestamp() * 1000]
        visible.sort(key=lambda e: -e.get("date", 0))
        return visible[:count] if count else visible


class DbRepo:
    """Настоящая app/db.py на SQLite в памяти."""

    def __init__(self, night: tuple[str, str]) -> None:
        from app import db

        self.db = db
        db.configure("sqlite:///:memory:")
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.user = db.upsert_user(USER_CHAT, t, name="Демо", ns_url="http://fake", tz="Europe/Moscow",
                                   night_start=night[0], night_end=night[1])
        db.create_invite(self.user.id, "demo-invite", t)
        db.accept_invite("demo-invite", TRUSTED_CHAT, "Доверенное лицо", t)

    def __getattr__(self, name):
        return getattr(self.db, name)

    def set_user_mode(self, state: ModeState, now: datetime) -> None:
        self.db.set_mode(self.user.id, state, now)


def memory_repo(night: tuple[str, str]):
    from tests.test_pipeline import U, FakeRepo

    class MemRepo(FakeRepo):
        def __init__(self) -> None:
            self.user = U(1, USER_CHAT)
            self.user.night_start, self.user.night_end = night     # как колонки users в БД
            self.user.tz = "Europe/Moscow"
            super().__init__([self.user])
            self.trusted[1] = [TRUSTED_CHAT]
            self.mode = ModeState()

        def get_mode(self, uid):
            return self.mode

        def set_active(self, uid, active):
            pass

        def set_user_mode(self, state: ModeState, now: datetime) -> None:
            self.mode = state

    return MemRepo()


# ------------------------------------------------------------------- прогон ---

async def run_case(case: Case, make_repo: Callable) -> Run:
    entries = json.loads((DATA / f"{case.scenario}.json").read_text(encoding="utf-8"))
    dates = [e["date"] for e in entries if isinstance(e.get("date"), (int, float))]
    start = datetime.fromtimestamp(min(dates) / 1000, timezone.utc)
    end = datetime.fromtimestamp(max(dates) / 1000, timezone.utc) + TAIL

    run = Run(start=start, data_end=end - TAIL, points=len(clean(entries)))
    repo = make_repo(case.night_schedule or NIGHT_OFF)
    repo.set_user_mode(ModeState(case.mode), start)

    async def fake_notify(alert, chat_id):
        run.sent.append(Sent(clock.now(), Level(alert.level), AlertKind(alert.kind), alert.value, alert.id))
        return None

    async def fake_trusted(alert, chat_id, minutes):
        run.sent.append(Sent(clock.now(), Level(alert.level), AlertKind(alert.kind), alert.value,
                             alert.id, to_trusted=True))
        return None

    pipeline.notify, pipeline.notify_trusted = fake_notify, fake_trusted
    pipeline._ns_down_since.clear()
    pipeline._ns_down_alerted.clear()
    ns = FakeNS(entries)
    mode_switched = False
    t = start
    while t <= end:
        clock.freeze(t)
        await pipeline.tick(repo, ns)
        low = run.first(K.LOW)
        if case.mode_on_first_low and not mode_switched and low and t >= low.at + REACTION:
            mode_switched = True
            repo.set_user_mode(ModeState(case.mode_on_first_low, until=t + timedelta(minutes=15)), t)
        t += timedelta(minutes=1)
    clock.unfreeze()
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--md", help="сохранить отчёт в Markdown")
    ap.add_argument("--only", help="только этот сценарий")
    ap.add_argument("--repo", choices=["db", "memory"], default="db")
    a = ap.parse_args()

    import logging

    logging.basicConfig(level=logging.ERROR)
    make_repo = DbRepo if a.repo == "db" else memory_repo
    facts = {k: v["facts"] for k, v in json.loads((DATA / "scenarios.json").read_text()).items()}
    expected = {k: v["expected"] for k, v in json.loads((DATA / "scenarios.json").read_text()).items()}

    store = "SQLite в памяти (app/db.py)" if a.repo == "db" else "подделка БД из тестов (--repo memory)"
    md = ["# Прогон сценариев fake_cgm через pipeline", "",
          "Настоящие clean → predict → evaluate → антиспам → эскалация; подменены только "
          f"Nightscout и Telegram. БД: {store}. Доверенное лицо подключено, «Я в порядке» "
          "никто не нажимает. Время — МСК.", "",
          "Сгенерировано: `python scripts/run_scenarios.py --md docs/scenarios-report.md`.", "",
          "| Сценарий · режим | Итог | Уведомления (новые) | Доверенному |", "|---|---|---|---|"]
    details: list[str] = []
    failed = 0
    for case in CASES:
        if a.only and case.scenario != a.only:
            continue
        run = asyncio.run(run_case(case, make_repo))
        results = [chk(run, facts[case.scenario]) for chk in case.checks]
        ok = all(r for r, _ in results)
        failed += not ok
        mark = "✅" if ok else "❌"
        print(f"{mark} {case.title}")
        for r, text in results:
            print(f"     {'ok ' if r else 'BAD'} {text}")
        new = run.new()
        md.append(f"| {case.title} | {mark} | {_fmt(new) or '—'} | "
                  f"{run.trusted()[0].hhmm if run.trusted() else '—'} |")
        details += [f"### {case.title} {mark}", "",
                    "Ожидание: " + "; ".join(expected[case.scenario]), ""]
        details += [f"- {'✅' if r else '❌'} {text}" for r, text in results]
        details += ["", "| Время | Уровень | Вид | Значение | Кому |", "|---|---|---|---|---|"]
        details += [f"| {s.hhmm} | {s.level.value} | {s.kind.value} | "
                    f"{'' if s.value is None else f'{s.value:.1f}'} | "
                    f"{'доверенному' if s.to_trusted else 'пользователю'} |" for s in run.sent] or ["| — | | | | |"]
        details.append("")

    total = sum(1 for c in CASES if not a.only or c.scenario == a.only)
    summary = f"Итого: {total - failed} из {total} случаев прошли."
    print(summary)
    if a.md:
        Path(a.md).write_text("\n".join(md + ["", summary, "", "## Подробно", ""] + details), encoding="utf-8")
        print(f"Отчёт: {a.md}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

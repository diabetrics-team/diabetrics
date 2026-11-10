"""
Общие типы DiaBetrics — контракт между модулями.

Владелец: Dev 1. Меняется только через PR с ревью двух человек,
потому что от этих классов зависят все четверо.

Соглашения:
- всё время — datetime в UTC (tzinfo=timezone.utc);
- сахар — ммоль/л (float);
- история показаний — список Reading от старых к новым.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum


class Level(StrEnum):
    CRITICAL = "critical"     # 🔴 приходит всегда, требует «Я в порядке»
    IMPORTANT = "important"   # 🟠
    INFO = "info"             # 🔵


class Mode(StrEnum):
    NORMAL = "normal"
    DND = "dnd"                   # Не беспокоить
    NIGHT = "night"               # Ночной (по расписанию)
    WORK = "work"                 # Учёба/работа
    AFTER_MEAL = "after_meal"     # временный, 2–3 ч
    AFTER_HYPO = "after_hypo"     # временный, 15 мин


TEMPORARY_MODES = {Mode.AFTER_MEAL, Mode.AFTER_HYPO}


class AlertKind(StrEnum):
    """Тип уведомления. Тексты для каждого — в bot/texts.py (Dev 3)."""
    LOW = "low"                       # < 3.9
    VERY_LOW = "very_low"             # < 3.0
    PREDICTED_LOW = "predicted_low"   # прогноз < 3.0 через 20 мин
    STILL_LOW = "still_low"           # < 3.9 через 15 мин после коррекции
    HIGH_1H = "high_1h"               # > 10.0 дольше часа
    VERY_HIGH_1H = "very_high_1h"     # > 13.9 дольше часа
    FAST_DROP = "fast_drop"
    FAST_RISE = "fast_rise"
    NO_DATA = "no_data"
    NS_UNREACHABLE = "ns_unreachable" # Nightscout не отвечает
    BACK_IN_RANGE = "back_in_range"
    DIGEST = "digest"                 # сводка подавленных уведомлений


@dataclass(frozen=True, slots=True)
class Reading:
    """Одно показание датчика после очистки (ns/clean.py)."""
    ts: datetime                  # UTC
    mmol: float
    direction: str | None = None  # как в Nightscout: "Flat", "SingleDown"...; None — не вычислим


@dataclass(frozen=True, slots=True)
class Forecast:
    """Результат predict() (Dev 4). Если прогноз построить нельзя — predict возвращает None."""
    mmol_20: float                # ожидаемый сахар через 20 мин
    mmol_30: float                # через 30 мин
    rate: float                   # ммоль/л в минуту, наклон за последние 15 мин (минус = падает)


@dataclass(slots=True)
class ModeState:
    kind: Mode = Mode.NORMAL
    until: datetime | None = None   # для временных режимов: когда выключится сам

    def active(self, now: datetime) -> Mode:
        # Режим с учётом автовыхода: истёкший временный режим = NORMAL
        if self.until is not None and now >= self.until:
            return Mode.NORMAL
        return self.kind


@dataclass(slots=True)
class Alert:
    """
    Уведомление. evaluate() создаёт его без id и user_id —
    их проставляет pipeline при сохранении в БД.
    """
    level: Level
    kind: AlertKind
    created_at: datetime            # UTC, из clock.now()
    value: float | None = None      # сахар в момент уведомления (или прогноз — для predicted_low)
    needs_ack: bool = False         # True для CRITICAL: нужна кнопка «Я в порядке»
    id: int | None = None
    user_id: int | None = None
    acked_at: datetime | None = None
    acked_by: int | None = None     # telegram id того, кто нажал (пользователь или доверенное лицо)
    escalated_at: datetime | None = None


class ActionType(Enum):
    REPEAT = "repeat"                   # повторить уведомление пользователю
    NOTIFY_TRUSTED = "notify_trusted"   # отправить доверенному лицу


@dataclass(frozen=True, slots=True)
class Action:
    """Результат escalation_step() (Dev 2): что pipeline должен отправить."""
    type: ActionType
    alert: Alert
    extra: dict = field(default_factory=dict)   # например, {"minutes_without_ack": 12}

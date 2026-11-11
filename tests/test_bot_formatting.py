"""
Сборка сообщений бота: уведомления, /now, режимы. Без Telegram и сети.

Владелец: Dev 3.
"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.bot import notify, texts
from app.bot.formatting import describe_mode, format_age, format_alert, format_duration, format_now
from app.forecast import predict
from app.models import Alert, AlertKind, Forecast, Level, Mode, ModeState, Reading
from app.ns.clean import clean

DATA = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


def load(name: str) -> list[Reading]:
    return clean(json.loads((DATA / f"{name}.json").read_text()))


# --- мелкие функции ---

@pytest.mark.parametrize(("minutes", "expected"), [
    (0, "0 мин"), (15, "15 мин"), (60, "1 ч"), (125, "2 ч 5 мин"), (-3, "0 мин"),
])
def test_format_duration(minutes, expected):
    assert format_duration(minutes) == expected


def test_format_age():
    assert format_age(T0, T0 + timedelta(seconds=30)) == "только что"
    assert format_age(T0, T0 + timedelta(minutes=5)) == "5 мин назад"
    assert format_age(T0, T0 + timedelta(minutes=95)) == "1 ч 35 мин назад"
    assert format_age(T0, T0 - timedelta(minutes=1)) == "только что"   # часы чуть разошлись


def test_describe_mode_temporary_shows_time_left_and_expires():
    state = ModeState(Mode.AFTER_MEAL, until=T0 + timedelta(minutes=100))
    assert describe_mode(state, T0) == "🍽 После еды, ещё 1 ч 40 мин"
    assert describe_mode(state, T0 + timedelta(minutes=101)) == "🟢 Обычный"   # автовыход
    assert describe_mode(ModeState(Mode.NIGHT), T0) == "🌙 Ночной"


# --- уведомления ---

@pytest.mark.parametrize("level", list(Level))
@pytest.mark.parametrize("kind", list(AlertKind))
def test_format_alert_every_kind_and_level(kind, level):
    text = format_alert(Alert(level, kind, T0, value=3.456))
    assert text.startswith({"critical": "🔴", "important": "🟠", "info": "🔵"}[level])
    assert "{" not in text and "}" not in text
    if "{value}" in texts.ALERTS[kind]:
        assert "3.5" in text   # округление до одного знака


def test_format_alert_without_value():
    text = format_alert(Alert(Level.IMPORTANT, AlertKind.NO_DATA, T0))
    assert "—" in text


def test_notify_without_bot_prints_to_console(capsys):
    notify.setup(None)
    alert = Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, value=2.8, needs_ack=True, id=1)
    asyncio.run(notify.notify(alert, chat_id=42))
    out = capsys.readouterr().out
    assert "[notify → 42] 🔴 Очень низкий сахар: 2.8 ммоль/л" in out
    assert "<b>" not in out and "[Я в порядке]" in out


# --- /now на сценариях fake_cgm ---

def test_now_fast_drop_shows_value_arrow_age_forecast_mode():
    readings = load("fast_drop")[:10]          # середина падения (13:45, DoubleDown)
    last = readings[-1]
    text = format_now(readings, predict(readings), ModeState(), last.ts + timedelta(minutes=3))
    assert f"<b>{last.mmol:.1f} ммоль/л</b>" in text
    assert any(arrow in text for arrow in "↓↘")
    assert "3 мин назад" in text
    assert "Через 20 мин" in text
    assert "Режим: 🟢 Обычный" in text


def test_now_stale_data_hides_forecast():
    readings = load("sensor_lost")             # датчик отвалился и не вернулся
    now = readings[-1].ts + timedelta(minutes=40)
    text = format_now(readings, Forecast(6.0, 6.0, 0.0), ModeState(), now)
    assert "Давно нет новых данных" in text and "Через 20 мин" not in text
    assert "40 мин назад" in text


def test_now_no_forecast_and_no_direction():
    readings = [Reading(T0, 5.54, None)]
    text = format_now(readings, None, ModeState(), T0)
    assert text.splitlines()[0] == "<b>5.5 ммоль/л</b>"   # без стрелки и лишнего пробела
    assert "Прогноза пока нет" in text


def test_now_right_after_sensor_gap_has_no_forecast_and_no_arrow():
    # scenarios.json, sensor_gap: «после возврата данных: тренд неизвестен, прогноза нет»
    readings = load("sensor_gap")
    back = next(i for i in range(1, len(readings)) if readings[i].ts - readings[i - 1].ts > timedelta(minutes=10))
    history = readings[: back + 1]
    text = format_now(history, predict(history), ModeState(), history[-1].ts + timedelta(minutes=1))
    assert "Прогноза пока нет" in text
    assert text.splitlines()[0].endswith("</b>"), "без стрелки тренда"


def test_now_empty():
    assert format_now([], None, ModeState(), T0) == "В Nightscout пока нет показаний."

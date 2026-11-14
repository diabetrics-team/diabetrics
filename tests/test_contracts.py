"""
Проверка, что модули стыкуются по контрактам app/models.py.
Эти тесты должны проходить и на заглушках, и на настоящих реализациях.
"""
import asyncio
import json
from pathlib import Path

import pytest

from app.alerts.engine import evaluate
from app.forecast import predict
from app.models import Alert, AlertKind, Forecast, Level, ModeState, Reading
from app.ns.clean import clean
from app.pipeline import replay

DATA = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"
META = json.loads((DATA / "scenarios.json").read_text())


def load(name: str) -> list[Reading]:
    return clean(json.loads((DATA / f"{name}.json").read_text()))


@pytest.mark.parametrize("name", list(META))
def test_clean_matches_facts(name):
    readings = load(name)
    assert len(readings) == META[name]["facts"]["points"]
    assert all(a.ts < b.ts for a, b in zip(readings, readings[1:])), "от старых к новым"
    assert all(r.ts.tzinfo is not None for r in readings), "время в UTC с tzinfo"


@pytest.mark.parametrize("name", list(META))
def test_predict_returns_forecast_or_none(name):
    readings = load(name)
    for i in range(len(readings)):
        f = predict(readings[: i + 1])
        assert f is None or isinstance(f, Forecast)
    assert predict([]) is None


@pytest.mark.parametrize("name", list(META))
def test_evaluate_returns_alerts(name):
    readings = load(name)
    alerts = evaluate(readings, predict(readings), ModeState(), readings[-1].ts, [])
    assert isinstance(alerts, list) and all(isinstance(a, Alert) for a in alerts)
    assert all(a.needs_ack == (a.kind is not AlertKind.PREDICTED_LOW) for a in alerts if a.level is Level.CRITICAL)


def test_end_to_end_night_hypo_gives_critical():
    sent = asyncio.run(replay("night_hypo"))
    assert any(a.level is Level.CRITICAL for a in sent)


def test_end_to_end_normal_is_quiet():
    sent = asyncio.run(replay("normal"))
    assert not [a for a in sent if a.level is not Level.INFO]

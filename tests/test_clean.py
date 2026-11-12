"""Крайние случаи clean(). Совпадение с facts.points по всем сценариям — в test_contracts.py."""
from datetime import UTC, datetime

import pytest

from app.ns.clean import clean

T0 = 1_794_169_500_000  # 2026-11-08T20:25:00Z, мс
MIN = 60_000


def sgv(ms, value, direction="Flat", type_="sgv"):
    return {"type": type_, "sgv": value, "date": ms, "direction": direction}


def test_mgdl_to_mmol_and_utc():
    [r] = clean([sgv(T0, 90)])
    assert r.mmol == pytest.approx(5.0)
    assert r.ts == datetime(2026, 11, 8, 20, 25, tzinfo=UTC)
    assert r.direction == "Flat"


def test_only_valid_sgv():
    raw = [
        sgv(T0, 100),
        {"type": "mbg", "mbg": 120, "date": T0 + 5 * MIN},   # глюкометр
        sgv(T0 + 10 * MIN, 5),                               # код ошибки датчика
        sgv(T0 + 15 * MIN, 39),
        sgv(T0 + 20 * MIN, 40),                              # 40 — ещё валидно
        sgv(T0 + 25 * MIN, None),
        sgv(T0 + 30 * MIN, "120"),
        {"type": "sgv", "sgv": 110},                         # без времени
        "мусор",
    ]
    assert [round(r.mmol * 18) for r in clean(raw)] == [100, 40]


def test_sorted_old_to_new():
    out = clean([sgv(T0 + 10 * MIN, 120), sgv(T0, 100), sgv(T0 + 5 * MIN, 110)])
    assert [round(r.mmol * 18) for r in out] == [100, 110, 120]


def test_duplicate_keeps_first_but_takes_direction():
    out = clean([sgv(T0, 100, direction=None), sgv(T0 + 8_000, 100), sgv(T0 + 5 * MIN, 104)])
    assert len(out) == 2
    assert out[0].direction == "Flat"


def test_no_direction_variants():
    raw = [sgv(T0, 100, None), sgv(T0 + 5 * MIN, 100, "NONE"), sgv(T0 + 10 * MIN, 100, "NOT COMPUTABLE")]
    assert [r.direction for r in clean(raw)] == [None, None, None]


def test_datestring_fallback():
    [r] = clean([{"type": "sgv", "sgv": 90, "dateString": "2026-11-08T20:25:00.000Z"}])
    assert r.ts == datetime(2026, 11, 8, 20, 25, tzinfo=UTC)


def test_empty():
    assert clean([]) == []

#!/usr/bin/env python3
"""
DiaBetrics — генератор фейковых данных CGM в формате Nightscout (v2).

Только стандартная библиотека Python 3.10+.

    python fake_cgm.py --list
    python fake_cgm.py --all --out-dir data
    python fake_cgm.py --scenario meal --out meal.json
    python fake_cgm.py --serve --scenario night_hypo --speed 60 --skip 150 --token secret

Мини-Nightscout (--serve):
    GET /api/v1/entries.json?count=N[&find[date][$gt]=<ms>]   новые первыми
    GET /api/v1/status.json                                   serverTimeEpoch (виртуальное «сейчас»)
    --token X  -> нужен ?token=X или заголовок api-secret: sha1(X), иначе 401 (как в настоящем NS)
    --speed K  -> сценарий идёт в K раз быстрее;  --skip M -> начать с M-й минуты сценария
Backend обязан брать «сейчас» из одного места (Clock): в проде — системные часы,
в dev/демо — serverTimeEpoch. Иначе таймеры режимов и эскалации разойдутся с данными.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STEP_MIN = 5
MGDL_PER_MMOL = 18.0
MIN_MMOL, MAX_MMOL = 2.2, 22.2          # диапазон датчика (40–400 mg/dL)
MSK = timezone(timedelta(hours=3))

# Пороги — те же, что в app/alerts/config.py. Эталон в scenarios.json считается по ним.
LOW, VERY_LOW, HIGH, VERY_HIGH = 3.9, 3.0, 10.0, 13.9
FAST_RATE = 0.17                         # ммоль/л/мин, наклон за последние 15 мин
ARTIFACT_RATE = 0.5                      # скачок быстрее — физиологически невозможен
GAP_MIN = 10                             # разрыв = между показаниями больше 10 мин

DIRECTIONS = [(3.0, "DoubleUp", 1), (2.0, "SingleUp", 2), (1.0, "FortyFiveUp", 3),
              (-1.0, "Flat", 4), (-2.0, "FortyFiveDown", 5), (-3.0, "SingleDown", 6)]


@dataclass
class Scenario:
    name: str
    description: str
    expected: list[str]                  # поведение движка словами; точное время — в facts
    start: str                           # "ЧЧ:ММ" МСК
    duration_min: int
    keyframes: list[tuple[int, float]] = field(default_factory=list)
    gaps: list[tuple[int, int]] = field(default_factory=list)
    spikes: list[tuple[int, float]] = field(default_factory=list)   # одиночные выбросы
    noise: float = 0.12
    random_day: bool = False
    dirty: bool = False                  # дубликаты, не-sgv записи, коды ошибок


SCENARIOS: dict[str, Scenario] = {s.name: s for s in [
    # ---- базовые -----------------------------------------------------------
    Scenario("normal", "Спокойный день в диапазоне 5–8 ммоль/л",
             ["ни одного критического и важного"],
             "09:00", 360, [(0, 6.2), (90, 7.4), (180, 6.0), (270, 7.0), (360, 6.4)]),
    Scenario("night_hypo", "Ночная гипогликемия: плавное падение до 2.7",
             ["важное low при first_below_3_9 (в Ночном режиме — сразу критическое)",
              "критическое very_low не позже first_below_3_0; предиктивное — раньше",
              "нет «Я в порядке» -> повтор -> доверенному лицу"],
             "23:00", 480,
             [(0, 7.0), (90, 6.2), (150, 4.8), (180, 4.0), (210, 3.4), (240, 2.9),
              (270, 2.7), (300, 2.8), (320, 4.2), (360, 5.6), (480, 6.3)]),
    Scenario("meal", "Завтрак ~60 г углеводов: 6 -> 12.8 за час -> возврат к 3 ч",
             ["Обычный режим: важное fast_rise, затем важное high > 1 ч",
              "«После еды»: ни одного уведомления (порог 13.9 не пересечён)"],
             "08:00", 240,
             [(0, 6.0), (15, 6.3), (35, 10.0), (60, 12.8), (90, 12.0), (130, 9.8),
              (180, 7.0), (240, 6.2)]),
    Scenario("fast_drop", "Быстрое падение 9.5 -> 4.0 за 30 мин, дно 3.6",
             ["важное fast_drop", "предиктивное: важное при текущем >= 5.0, критическое при < 5.0",
              "важное low; very_low нет (до 3.0 не доходит)"],
             "16:00", 120,
             [(0, 9.5), (20, 9.3), (35, 7.5), (50, 4.0), (60, 3.6), (80, 4.6), (120, 5.8)],
             noise=0.08),
    Scenario("hypo_corrected", "Гипо 3.3 -> быстрые углеводы -> 5.5 за 20 мин",
             ["важное low один раз", "в «После коррекции гипо»: повторов нет, перепроверка через 15 мин -> тишина"],
             "12:00", 120,
             [(0, 5.8), (25, 4.2), (40, 3.3), (45, 3.3), (55, 4.0), (65, 5.5), (120, 6.2)],
             noise=0.08),
    Scenario("hypo_not_corrected", "Гипо, коррекция не помогла: держится 3.4–3.6",
             ["важное low", "в «После коррекции гипо»: через 15 мин всё ещё < 3.9 -> критическое"],
             "12:00", 120,
             [(0, 5.8), (25, 4.2), (40, 3.4), (60, 3.5), (75, 3.6), (95, 3.4), (120, 4.5)],
             noise=0.06),
    Scenario("hyper_long", "Долгая гипергликемия: > 13.9 около 1.5 ч, рост медленный",
             ["важное high > 1 ч", "критическое very_high > 1 ч", "fast_rise НЕТ (рост медленнее порога)"],
             "14:00", 250,
             [(0, 8.0), (40, 11.0), (90, 14.3), (120, 15.3), (160, 14.6), (190, 12.0), (250, 8.5)]),
    Scenario("sensor_gap", "Сигнал пропал на 45 мин при последнем значении ниже 3.9",
             ["важное no_data через 20 мин", "критическое no_data через 30 мин (последнее значение низкое)",
              "после возврата данных: тренд неизвестен, прогноза нет"],
             "18:00", 150, [(0, 6.5), (30, 4.8), (50, 3.7), (95, 3.9), (150, 5.5)],
             gaps=[(55, 90)]),
    # ---- новые в v2: ложные тревоги и грязные данные -------------------------
    Scenario("noise_spike", "Ровные 6.5 и два одиночных выброса датчика (2.8 и 15.5)",
             ["НИ ОДНОГО критического, эскалации нет",
              "выброс = скачок > 0.5 ммоль/л/мин: ждать следующего показания"],
             "10:00", 210, [(0, 6.5), (210, 6.8)], spikes=[(60, 2.8), (150, 15.5)], noise=0.08),
    Scenario("borderline", "Сахар 40 мин колеблется вокруг порога 3.9 (3.7–4.1)",
             ["важное low РОВНО один раз + повторы по антиспаму, а не на каждое пересечение",
              "гистерезис: «вернулся в норму» только выше 4.2"],
             "15:00", 120,
             [(0, 5.5), (30, 4.0), (40, 3.75), (50, 4.05), (60, 3.75), (70, 4.05), (80, 3.75),
              (90, 4.3), (120, 5.2)], noise=0.03),
    Scenario("meal_overbolus", "Еда -> пик 11.5 -> лишний инсулин -> плавное падение до 3.4",
             ["«После еды» НЕ глушит низкие: важное low приходит",
              "Обычный режим: fast_rise и high > 1 ч; в «После еды» оба подавлены; fast_drop нет"],
             "13:00", 250,
             [(0, 6.0), (20, 6.5), (50, 11.5), (90, 10.5), (170, 3.4), (185, 3.5), (210, 5.0), (250, 6.0)],
             noise=0.06),
    Scenario("sensor_lost", "Датчик отвалился при нормальном сахаре и не вернулся",
             ["важное no_data через 20 мин, дальше повторы по антиспаму",
              "критического НЕТ (последнее значение в норме)"],
             "20:00", 180, [(0, 7.0), (60, 6.8), (180, 6.8)], gaps=[(65, 180)]),
    Scenario("dirty_data", "Нормальный сахар, но сырой ответ как в жизни: дубликаты, mbg, код ошибки",
             ["после очистки ровно facts.points показаний, ни одного уведомления",
              "клиент: только type=sgv, sgv >= 40, дедупликация по времени"],
             "11:00", 180, [(0, 6.0), (90, 7.2), (180, 6.3)], dirty=True),
    Scenario("random_day", "Случайный день: 3 приёма пищи, феномен утренней зари (зависит от seed)",
             ["для графиков, метрик TIR и оценки прогноза"],
             "00:00", 1440, random_day=True, noise=0.15),
]}


# --------------------------------------------------------------- генерация ---

def _smooth(a, b, t):
    return a + (b - a) * (1 - math.cos(math.pi * t)) / 2


def _keyframe_value(kf, m):
    for (m0, v0), (m1, v1) in zip(kf, kf[1:]):
        if m0 <= m <= m1:
            return _smooth(v0, v1, (m - m0) / (m1 - m0))
    return kf[-1][1]


def _meal_curve(t, amp, peak):
    if t <= 0:
        return 0.0
    x = t / peak
    return amp * x * math.exp(1 - x)


def _random_day_value(m, start_hour, p):
    hour = (start_hour + m / 60) % 24
    v = p["base"] + 0.6 * math.sin(2 * math.pi * m / 480 + p["phase"])
    if 4 <= hour <= 8:
        v += p["dawn"] * math.sin(math.pi * (hour - 4) / 4)
    return v + sum(_meal_curve(m - mm, amp, peak) for mm, amp, peak in p["meals"])


def _direction(rate):
    if rate is None:
        return "NOT COMPUTABLE", 9
    for threshold, name, code in DIRECTIONS:
        if rate > threshold:
            return name, code
    return "DoubleDown", 7


def _entry(ts: datetime, sgv: int, direction: str, trend: int, device="fake-cgm") -> dict:
    return {"_id": f"fake{int(ts.timestamp() * 1000)}{device[-1]}", "type": "sgv", "sgv": sgv,
            "date": int(ts.timestamp() * 1000),
            "dateString": ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "direction": direction, "trend": trend, "device": device}


def generate(sc: Scenario, date: datetime | None = None, seed: int = 42) -> list[dict]:
    """Сырые записи как в Nightscout /api/v1/entries.json, от старых к новым."""
    rnd = random.Random(seed)
    date = date or datetime.now(MSK)
    hh, mm = map(int, sc.start.split(":"))
    t0 = date.replace(hour=hh, minute=mm, second=0, microsecond=0)

    params = None
    if sc.random_day:
        params = {"base": rnd.uniform(5.5, 7.0), "phase": rnd.uniform(0, 2 * math.pi),
                  "dawn": rnd.uniform(0.8, 1.8),
                  "meals": [(60 * (h - hh) - mm + rnd.randint(-30, 30), rnd.uniform(*amp), rnd.uniform(45, 70))
                            for h, amp in [(8, (3.0, 5.0)), (13, (4.0, 6.5)), (19, (3.5, 6.0))]]}
    spikes = dict(sc.spikes)

    entries, prev, noise = [], None, 0.0
    for m in range(0, sc.duration_min + 1, STEP_MIN):
        if any(a <= m <= b for a, b in sc.gaps):
            prev = None
            continue
        clean = (_random_day_value(m, hh + mm / 60, params) if params else _keyframe_value(sc.keyframes, m))
        noise = 0.6 * noise + rnd.gauss(0, sc.noise)             # AR(1)-шум
        mmol = spikes.get(m, min(MAX_MMOL, max(MIN_MMOL, clean + noise)))
        sgv = round(mmol * MGDL_PER_MMOL)
        direction, trend = _direction(None if prev is None else (sgv - prev) / STEP_MIN)
        entries.append(_entry(t0 + timedelta(minutes=m), sgv, direction, trend))
        prev = sgv

    if sc.dirty:
        entries = _make_dirty(entries)
    return entries


def _make_dirty(entries: list[dict]) -> list[dict]:
    """То, что реально встречается в ответах Nightscout."""
    out = []
    for i, e in enumerate(entries):
        if i == 20:                                  # код ошибки датчика вместо значения
            e = {**e, "sgv": 5, "direction": "NOT COMPUTABLE", "trend": 9}
        if i == 9:                                   # запись без direction
            e = {k: v for k, v in e.items() if k not in ("direction", "trend")}
        out.append(e)
        if i % 7 == 3:                               # второй загрузчик прислал то же показание
            ts = datetime.fromtimestamp(e["date"] / 1000, timezone.utc) + timedelta(seconds=8)
            out.append(_entry(ts, e["sgv"], e.get("direction", "Flat"), e.get("trend", 4), "fake-cgm-2"))
        if i == 12:                                  # замер глюкометром — не sgv
            ts = datetime.fromtimestamp(e["date"] / 1000, timezone.utc) + timedelta(seconds=90)
            out.append({"_id": "fakembg", "type": "mbg", "mbg": 112, "date": int(ts.timestamp() * 1000),
                        "dateString": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "device": "meter"})
    return out


# ---------------------------------------------------------- эталон (facts) ---

def clean(entries: list[dict]) -> list[tuple[int, float]]:
    """Эталонная очистка: только sgv >= 40 mg/dL, без дублей (ближе 2.5 мин), по возрастанию времени."""
    out: list[tuple[int, float]] = []
    for e in sorted(entries, key=lambda e: e["date"]):
        if e.get("type") != "sgv" or e.get("sgv", 0) < 40:
            continue
        if out and e["date"] - out[-1][0] < 150_000:
            continue
        out.append((e["date"], e["sgv"] / MGDL_PER_MMOL))
    return out


def _hm(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, MSK).strftime("%H:%M")


def _slope(pts: list[tuple[int, float]]) -> float:
    """МНК-наклон, ммоль/л/мин."""
    xs = [(t - pts[0][0]) / 60000 for t, _ in pts]
    ys = [v for _, v in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def facts(entries: list[dict], sc: Scenario) -> dict:
    """Факты о данных, по которым Dev 2 и Dev 4 сверяют тесты. Время — МСК."""
    pts = clean(entries)
    rate = lambda a, b: (b[1] - a[1]) / ((b[0] - a[0]) / 60000)
    artifacts = [pts[i] for i in range(1, len(pts) - 1)
                 if abs(rate(pts[i - 1], pts[i])) > ARTIFACT_RATE and abs(rate(pts[i], pts[i + 1])) > ARTIFACT_RATE
                 and rate(pts[i - 1], pts[i]) * rate(pts[i], pts[i + 1]) < 0]
    good = [p for p in pts if p not in artifacts]

    def first(pred):
        return next((t for t, v in good if pred(v)), None)

    def lasted(pred, minutes=60):
        start = None
        for t, v in good:
            if pred(v):
                start = start or t
                if t - start >= minutes * 60000:
                    return t
            else:
                start = None
        return None

    slopes = []                                           # наклон по 4 точкам за 15 мин без разрывов
    for i in range(3, len(good)):
        w = good[i - 3:i + 1]
        if w[-1][0] - w[0][0] == 15 * 60000:
            slopes.append((w[-1][0], _slope(w)))
    gaps = [(a, b) for a, b in zip(pts, pts[1:]) if b[0] - a[0] > GAP_MIN * 60000]
    end_ms = entries[0]["date"] + sc.duration_min * 60000
    if end_ms - pts[-1][0] > GAP_MIN * 60000:              # данные кончились и не вернулись
        gaps.append((pts[-1], (end_ms, None)))

    vals = [v for _, v in good]
    return {
        "points": len(pts), "raw_entries": len(entries),
        "min": round(min(vals), 1), "max": round(max(vals), 1),
        "first_below_3_9": _hm(first(lambda v: v < LOW)),
        "first_below_3_0": _hm(first(lambda v: v < VERY_LOW)),
        "first_above_10_0": _hm(first(lambda v: v > HIGH)),
        "first_above_13_9": _hm(first(lambda v: v > VERY_HIGH)),
        "above_10_0_for_1h_at": _hm(lasted(lambda v: v > HIGH)),
        "above_13_9_for_1h_at": _hm(lasted(lambda v: v > VERY_HIGH)),
        "max_drop_rate": round(min((s for _, s in slopes), default=0), 2),
        "max_rise_rate": round(max((s for _, s in slopes), default=0), 2),
        "first_fast_drop": _hm(next((t for t, s in slopes if s <= -FAST_RATE), None)),
        "first_fast_rise": _hm(next((t for t, s in slopes if s >= FAST_RATE), None)),
        "artifacts": [{"at": _hm(t), "mmol": round(v, 1)} for t, v in artifacts],
        "gaps": [{"last_reading_at": _hm(a[0]), "last_mmol": round(a[1], 1),
                  "no_data_20min_at": _hm(a[0] + 20 * 60000), "no_data_30min_at": _hm(a[0] + 30 * 60000),
                  "data_back_at": _hm(b[0]) if b[1] is not None else None,
                  "minutes": (b[0] - a[0]) // 60000} for a, b in gaps],
    }


# ------------------------------------------------------- мини-Nightscout ---

def serve(entries: list[dict], port: int, speed: float, skip_min: int, token: str | None) -> None:
    first_ms = entries[0]["date"] + skip_min * 60000
    real_start = time.time()
    secret_hash = hashlib.sha1(token.encode()).hexdigest() if token else None

    def now_ms() -> int:
        return int(first_ms + (time.time() - real_start) * speed * 1000)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, payload, code=200):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            if token and q.get("token", [""])[0] != token and self.headers.get("api-secret") != secret_hash:
                return self._json({"status": 401, "message": "Unauthorized"}, 401)
            now, path = now_ms(), url.path.rstrip("/")
            if path in ("/api/v1/entries.json", "/api/v1/entries", "/api/v1/entries/sgv.json"):
                visible = [e for e in entries if e["date"] <= now]
                if path.endswith("sgv.json"):
                    visible = [e for e in visible if e.get("type") == "sgv"]
                if "find[date][$gt]" in q:
                    visible = [e for e in visible if e["date"] > int(q["find[date][$gt]"][0])]
                if "find[date][$gte]" in q:
                    visible = [e for e in visible if e["date"] >= int(q["find[date][$gte]"][0])]
                count = int(q.get("count", ["10"])[0])
                self._json(sorted(visible, key=lambda e: -e["date"])[:count])
            elif path in ("/api/v1/status.json", "/api/v1/status"):
                self._json({"status": "ok", "name": "fake-nightscout",
                            "serverTime": datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(),
                            "serverTimeEpoch": now, "settings": {"units": "mmol"}})
            else:
                self._json({"error": "not found"}, 404)

        def log_message(self, fmt, *args):
            print(f"[fake-ns] {fmt % args}")

    print(f"Fake Nightscout: http://localhost:{port}/api/v1/entries.json?count=10  x{speed}"
          f"{'  token required' if token else ''}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


# --------------------------------------------------------------------- CLI ---

def main() -> None:
    ap = argparse.ArgumentParser(description="Фейковые данные CGM в формате Nightscout")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scenario", default="random_day", choices=SCENARIOS)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--date", help="YYYY-MM-DD (по умолчанию сегодня)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=1337)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--skip", type=int, default=0, help="начать с N-й минуты сценария")
    ap.add_argument("--token", help="требовать токен, как настоящий Nightscout")
    a = ap.parse_args()
    date = datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=MSK) if a.date else None

    if a.list:
        for s in SCENARIOS.values():
            print(f"{s.name:20} {s.description}")
            for e in s.expected:
                print(f"{'':22}-> {e}")
        return
    if a.all:
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        meta = {}
        for s in SCENARIOS.values():
            data = generate(s, date, a.seed)
            (out / f"{s.name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
            meta[s.name] = {"description": s.description, "start": s.start, "duration_min": s.duration_min,
                            "expected": s.expected, "facts": facts(data, s)}
        (out / "scenarios.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        print(f"Готово: {len(SCENARIOS)} сценариев в {out}/")
        return

    data = generate(SCENARIOS[a.scenario], date, a.seed)
    if a.serve:
        serve(data, a.port, a.speed, a.skip, a.token)
    elif a.out:
        Path(a.out).write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"{len(data)} записей -> {a.out}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

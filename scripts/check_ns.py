"""Ручная проверка клиента: читает Nightscout (или fake_cgm) и печатает очищенные показания.

    python fake_cgm/fake_cgm.py --serve --scenario night_hypo --speed 60 --token secret
    python scripts/check_ns.py http://localhost:1337 --token secret
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ns.clean import clean  # noqa: E402
from app.ns.client import NightscoutClient, NSError  # noqa: E402


async def run(url: str, token: str | None, count: int) -> int:
    try:
        async with NightscoutClient(url, token) as ns:
            server_ms = await ns.get_server_time()
            raw = await ns.get_entries(count=count)
    except NSError as e:
        print(f"ОШИБКА: {type(e).__name__}: {e}")
        return 1

    readings = clean(raw)
    server = datetime.fromtimestamp(server_ms / 1000, tz=UTC)
    print(f"serverTime: {server.isoformat()}  сырых: {len(raw)}  чистых: {len(readings)}")
    for r in readings[-10:]:
        print(f"  {r.ts:%Y-%m-%d %H:%M} UTC  {r.mmol:5.1f} ммоль/л  {r.direction or '-'}")
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--token")
    p.add_argument("--count", type=int, default=50)
    a = p.parse_args()
    raise SystemExit(asyncio.run(run(a.url, a.token, a.count)))


if __name__ == "__main__":
    main()

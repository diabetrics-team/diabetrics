import asyncio
import json
from datetime import UTC, datetime

from app import health
from app.pipeline import Health


def h(**kw):
    x = Health(started_mono=1000.0)
    for k, v in kw.items():
        setattr(x, k, v)
    return x


def test_starting_then_stale_without_ticks():
    assert health.status(h(), 60, now_mono=1010)[0] == 200
    assert health.status(h(), 60, now_mono=1010)[1]["status"] == "starting"
    code, body = health.status(h(), 60, now_mono=1000 + 3 * 60 + 31)
    assert (code, body["status"]) == (503, "stale")


def test_fresh_and_stale_tick():
    t = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)
    fresh = h(last_tick_mono=2000.0, last_tick_at=t, users=2)
    code, body = health.status(fresh, 5, now_mono=2010)
    assert code == 200 and body["status"] == "ok" and body["users"] == 2
    assert body["last_tick_at"] == t.isoformat()
    assert health.status(fresh, 5, now_mono=2000 + 46)[0] == 503    # 3×5 + 30 = 45 с


def test_http_endpoint():
    async def go():
        server = await health.start(0, 5)
        assert server is None                                       # порт 0 — выключено
        server = await asyncio.start_server(
            lambda r, w: health._handle(r, w, 5), host="127.0.0.1", port=0)
        port = server.sockets[0].getsockname()[1]
        out = {}
        for path in ("/health", "/nope"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            await writer.drain()
            out[path] = await reader.read()
            writer.close()
        server.close()
        await server.wait_closed()
        return out

    out = asyncio.run(go())
    head, body = out["/health"].split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 ") and json.loads(body)["status"] in ("starting", "ok", "stale")
    assert out["/nope"].startswith(b"HTTP/1.1 404")

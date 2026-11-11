import asyncio
import json
from datetime import UTC, datetime

import pytest

httpx = pytest.importorskip("httpx")

from app.ns.client import (  # noqa: E402
    NightscoutClient,
    NSAuthError,
    NSBadResponse,
    NSUnavailable,
)

URL = "http://ns.test"
TOKEN = "secret-token-123"
MS = 1_794_169_500_000


def run(coro):
    return asyncio.run(coro)


def make(handler, token=TOKEN):
    return NightscoutClient(URL, token, transport=httpx.MockTransport(handler))


async def _call(client, method, *args, **kwargs):
    async with client:
        return await getattr(client, method)(*args, **kwargs)


def test_entries_since_and_token_in_query():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["params"] = dict(req.url.params)
        return httpx.Response(200, json=[{"type": "sgv", "sgv": 100, "date": MS + 60_000}])

    since = datetime.fromtimestamp(MS / 1000, tz=UTC)
    out = run(_call(make(handler), "get_entries", since=since))
    assert out == [{"type": "sgv", "sgv": 100, "date": MS + 60_000}]
    assert seen["path"] == "/api/v1/entries.json"
    assert seen["params"]["token"] == TOKEN
    assert seen["params"]["find[date][$gt]"] == str(MS)


def test_server_time():
    def handler(req):
        assert req.url.path == "/api/v1/status.json"
        return httpx.Response(200, json={"status": "ok", "serverTimeEpoch": MS})

    assert run(_call(make(handler), "get_server_time")) == MS


@pytest.mark.parametrize("code", [401, 403])
def test_auth_error(code):
    with pytest.raises(NSAuthError):
        run(_call(make(lambda r: httpx.Response(code)), "get_entries"))


def test_server_error():
    with pytest.raises(NSUnavailable):
        run(_call(make(lambda r: httpx.Response(502)), "get_entries"))


def test_timeout_does_not_leak_token():
    def handler(req):
        raise httpx.ReadTimeout(f"timed out {req.url}", request=req)

    with pytest.raises(NSUnavailable) as e:
        run(_call(make(handler), "get_entries"))
    assert TOKEN not in str(e.value)


def test_connect_error_does_not_leak_token():
    def handler(req):
        raise httpx.ConnectError(f"refused {req.url}", request=req)

    with pytest.raises(NSUnavailable) as e:
        run(_call(make(handler), "get_server_time"))
    assert TOKEN not in str(e.value)


@pytest.mark.parametrize("body", [b"", b"   ", b"[]"])
def test_empty_response_is_empty_list(body):
    assert run(_call(make(lambda r: httpx.Response(200, content=body)), "get_entries")) == []


def test_not_json():
    with pytest.raises(NSBadResponse):
        run(_call(make(lambda r: httpx.Response(200, content=b"<html>")), "get_entries"))


def test_status_without_time():
    h = lambda r: httpx.Response(200, content=json.dumps({"status": "ok"}).encode())  # noqa: E731
    with pytest.raises(NSBadResponse):
        run(_call(make(h), "get_server_time"))


def test_no_token_no_param():
    def handler(req):
        assert "token" not in req.url.params
        return httpx.Response(200, json=[])

    assert run(_call(make(handler, token=None), "get_entries")) == []


def test_bad_url():
    with pytest.raises(ValueError):
        NightscoutClient("ns.example.com")

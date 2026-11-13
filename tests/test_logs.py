import logging

from app.logs import Redact, redact

BOT = "1234567890:AAH_Fake-Token_abcdefghijklmnopqrstuvw"


def test_redact_ns_token_in_url():
    s = redact("GET https://ns.example.com/api/v1/entries.json?count=10&token=read-abc123 200")
    assert "read-abc123" not in s and "token=***" in s and "count=10" in s


def test_redact_first_query_param():
    assert redact("http://x/api?token=secret") == "http://x/api?token=***"


def test_redact_api_secret_header():
    s = redact("headers={'api-secret': 'deadbeef42'}")
    assert "deadbeef42" not in s


def test_redact_bot_token():
    s = redact(f"POST https://api.telegram.org/bot{BOT}/sendMessage")
    assert BOT not in s and "sendMessage" in s


def test_plain_text_untouched():
    s = "alert user_id=3 id=17 critical/very_low value=2.8 → chat 123456789"
    assert redact(s) == s


def test_filter_cleans_args_and_traceback(caplog):
    log = logging.getLogger("test.redact")
    handler = logging.StreamHandler()
    handler.addFilter(Redact())
    log.addHandler(handler)
    try:
        record = log.makeRecord("test.redact", logging.ERROR, __file__, 1,
                                "url %s", ("http://x?token=abc",), None)
        Redact().filter(record)
        assert record.getMessage() == "url http://x?token=***"
        try:
            raise ValueError("http://x?token=zzz")
        except ValueError:
            import sys
            rec = log.makeRecord("t", logging.ERROR, __file__, 1, "boom", (), sys.exc_info())
        Redact().filter(rec)
        assert "zzz" not in rec.exc_text
    finally:
        log.removeHandler(handler)

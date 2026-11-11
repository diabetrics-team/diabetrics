"""
Хендлеры бота на поддельном Telegram: онбординг, /now, /mode, notify с кнопкой.
Telegram заменён FakeSession (запросы записываются, а не отправляются),
Nightscout — подменёнными функциями. Сеть не нужна.

Владелец: Dev 3.
"""
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("aiogram")   # без aiogram эти тесты пропускаются, остальные работают

from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app import clock
from app.bot import nightscout, notify, storage, texts
from app.bot.main import create_bot, create_dispatcher
from app.models import Alert, AlertKind, Level, Mode

DATA = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"
USER = User(id=111, is_bot=False, first_name="Аня")
CHAT = Chat(id=111, type="private")
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
# Один Dispatcher на все тесты: роутеры — объекты модулей и подключаются только к одному.
DP = create_dispatcher()


class FakeSession(BaseSession):
    """Вместо HTTP-запросов в Telegram — запись в список."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.requests), date=T0, chat=CHAT, text=method.text)
        return True


class Tg:
    """Пользователь пишет боту и жмёт кнопки; смотрим, что бот отправил."""

    def __init__(self) -> None:
        self.bot = create_bot("123456:TEST")
        self.session = FakeSession()
        self.bot.session = self.session
        self.dp = DP
        self.n = 0

    async def _feed(self, **event) -> list:
        self.n += 1
        start = len(self.session.requests)
        await self.dp.feed_update(self.bot, Update(update_id=self.n, **event))
        return self.session.requests[start:]

    async def send(self, text: str) -> list:
        msg = Message(message_id=self.n + 1000, date=T0, chat=CHAT, from_user=USER, text=text)
        return await self._feed(message=msg)

    async def press(self, data: str) -> list:
        menu = Message(message_id=1, date=T0, chat=CHAT, text="…")
        cb = CallbackQuery(id=str(self.n), from_user=USER, chat_instance="ci", data=data, message=menu)
        return await self._feed(callback_query=cb)


def texts_of(requests: list) -> list[str]:
    return [r.text for r in requests if isinstance(r, (SendMessage, EditMessageText))]


def buttons_of(request) -> list[str]:
    return [b.callback_data for row in request.reply_markup.inline_keyboard for b in row]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state():
    storage.reset()
    DP.storage.storage.clear()   # сбросить шаги онбординга (FSM) от прошлого теста
    clock.freeze(T0)
    yield
    clock.unfreeze()
    notify.setup(None)
    storage.reset()


@pytest.fixture
def fake_ns(monkeypatch):
    """Nightscout: токен "good" подходит, адрес https://down.example не отвечает."""
    raw = json.loads((DATA / "fast_drop.json").read_text())[:10]
    last_ms = max(e["date"] for e in raw)

    def guard(url, token):
        if url == "https://down.example":
            raise nightscout.NsUnavailable("нет соединения")
        if token != "good":
            raise nightscout.NsBadToken()

    async def check(url, token):
        guard(url, token)

    async def get_server_time(url, token):
        guard(url, token)
        return last_ms + 3 * 60_000

    async def get_entries(url, token, count=36):
        guard(url, token)
        return sorted(raw, key=lambda e: -e["date"])[:count]

    monkeypatch.setattr(nightscout, "check", check)
    monkeypatch.setattr(nightscout, "get_server_time", get_server_time)
    monkeypatch.setattr(nightscout, "get_entries", get_entries)
    return datetime.fromtimestamp(last_ms / 1000, UTC)


def connect(token: str | None = "good") -> None:
    storage.save_user(storage.BotUser(USER.id, "https://my-cgm.example.com", token, "Europe/Moscow"))


# --- онбординг ---

def test_onboarding_happy_path(fake_ns):
    async def scenario():
        tg = Tg()
        sent = texts_of(await tg.send("/start"))
        assert sent[0].startswith("Привет, Аня!") and sent[1] == texts.ONBOARDING_ASK_URL

        reqs = await tg.send("my-cgm.example.com/")
        assert texts_of(reqs) == [texts.ONBOARDING_ASK_TOKEN]
        assert buttons_of(reqs[0]) == ["onb:no_token"]

        reqs = await tg.send("good")
        assert any(isinstance(r, DeleteMessage) for r in reqs), "сообщение с токеном удалено"
        assert texts_of(reqs) == [texts.ONBOARDING_CHECKING, texts.ONBOARDING_ASK_TZ]
        assert "tz:Europe/Moscow" in buttons_of(reqs[-1])

        assert "Часовой пояс: Москва (UTC+3)" in texts_of(await tg.press("tz:Europe/Moscow"))[0]

    run(scenario())
    user = storage.get_user(USER.id)
    assert (user.ns_url, user.ns_token, user.tz) == ("https://my-cgm.example.com", "good", "Europe/Moscow")


def test_onboarding_bad_url_bad_token_then_ok(fake_ns):
    async def scenario():
        tg = Tg()
        await tg.send("/start")
        assert texts_of(await tg.send("это не адрес")) == [texts.ONBOARDING_BAD_URL]
        await tg.send("https://my-cgm.example.com")
        assert texts.ONBOARDING_TOKEN_REJECTED in texts_of(await tg.send("wrong"))
        assert texts.ONBOARDING_TOKEN_REJECTED in texts_of(await tg.press("onb:no_token"))
        assert texts.ONBOARDING_ASK_TZ in texts_of(await tg.send("good"))

    run(scenario())


def test_onboarding_unreachable_asks_url_again(fake_ns):
    async def scenario():
        tg = Tg()
        await tg.send("/start")
        await tg.send("down.example")
        sent = texts_of(await tg.send("good"))
        assert "Не получается достучаться до https://down.example" in sent[-1]
        # снова шаг «адрес»: следующий текст — адрес, а не токен
        assert texts_of(await tg.send("https://my-cgm.example.com")) == [texts.ONBOARDING_ASK_TOKEN]

    run(scenario())


def test_start_when_connected_offers_reconnect(fake_ns):
    connect()

    async def scenario():
        reqs = await Tg().send("/start")
        assert "уже подключён: https://my-cgm.example.com" in texts_of(reqs)[1]
        assert buttons_of(reqs[1]) == ["onb:restart"]

    run(scenario())


def test_cancel_and_commands_during_onboarding(fake_ns):
    async def scenario():
        tg = Tg()
        await tg.send("/start")
        assert texts_of(await tg.send("/help")) == [texts.HELP], "команды работают посреди онбординга"
        assert texts_of(await tg.send("/cancel")) == [texts.CANCELLED]
        assert texts_of(await tg.send("https://my-cgm.example.com")) == [texts.UNKNOWN]
        assert texts_of(await tg.send("/cancel")) == [texts.NOTHING_TO_CANCEL]

    run(scenario())


# --- /now ---

def test_now_not_connected():
    assert texts_of(run(Tg().send("/now"))) == [texts.NOW_NOT_CONNECTED]


def test_now_connected(fake_ns):
    connect()
    clock.freeze(fake_ns + timedelta(minutes=3))
    text = texts_of(run(Tg().send("/now")))[0]
    assert "ммоль/л</b>" in text and "3 мин назад" in text
    assert "Через 20 мин" in text and "Режим: 🟢 Обычный" in text


def test_now_bad_token(fake_ns):
    connect(token="revoked")
    assert texts_of(run(Tg().send("/now"))) == [texts.ERROR_NS_BAD_TOKEN]


# --- /mode ---

def test_mode_after_meal_asks_duration_then_sets_until():
    async def scenario():
        tg = Tg()
        reqs = await tg.send("/mode")
        assert texts_of(reqs)[0].startswith("Сейчас: 🟢 Обычный")
        assert len(buttons_of(reqs[0])) == len(Mode)

        reqs = await tg.press("mode:after_meal:0")
        assert texts_of(reqs) == ["На сколько включить «🍽 После еды»?"]
        assert buttons_of(reqs[-1]) == ["mode:after_meal:120", "mode:after_meal:150", "mode:after_meal:180"]

        sent = texts_of(await tg.press("mode:after_meal:150"))
        assert sent == ["Режим «🍽 После еды» включён на 2 ч 30 мин. Потом вернусь к обычному."]
        assert texts_of(await tg.send("/mode"))[0].startswith("Сейчас: 🍽 После еды, ещё 2 ч 30 мин")

    run(scenario())
    state = storage.get_mode(USER.id)
    assert state.kind is Mode.AFTER_MEAL and state.until == T0 + timedelta(minutes=150)


def test_mode_after_hypo_is_15_min_and_night_is_permanent():
    async def scenario():
        tg = Tg()
        assert "на 15 мин" in texts_of(await tg.press("mode:after_hypo:0"))[0]
        assert texts_of(await tg.press("mode:night:0")) == ["Режим «🌙 Ночной» включён."]

    run(scenario())
    assert storage.get_mode(USER.id).kind is Mode.NIGHT and storage.get_mode(USER.id).until is None


def test_forged_mode_buttons_change_nothing():
    async def scenario():
        tg = Tg()
        assert texts_of(await tg.press("mode:bogus:0")) == []
        assert "На сколько" in texts_of(await tg.press("mode:after_meal:999"))[0]

    run(scenario())
    assert storage.get_mode(USER.id).kind is Mode.NORMAL


# --- notify и кнопки ---

def test_notify_critical_has_ack_button_others_dont():
    async def scenario():
        tg = Tg()
        notify.setup(tg.bot)
        await notify.notify(Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, needs_ack=True, id=7), 111)
        await notify.notify(Alert(Level.IMPORTANT, AlertKind.LOW, T0, 3.6, id=8), 111)
        return tg.session.requests

    critical, important = run(scenario())
    assert critical.chat_id == 111 and critical.text.startswith("🔴 <b>Очень низкий сахар: 2.8</b>")
    assert buttons_of(critical) == ["ack:7"]
    assert important.text.startswith("🟠") and important.reply_markup is None


def test_button_without_handler_gets_answer():
    reqs = run(Tg().press("ack:7"))
    assert [(type(r), r.text) for r in reqs] == [(AnswerCallbackQuery, texts.BUTTON_NOT_READY)]

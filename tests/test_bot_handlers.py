"""
Хендлеры бота на поддельном Telegram: онбординг, /now, /mode, /trusted, «Я в порядке»,
/graph, notify. Telegram заменён FakeSession (запросы записываются, а не отправляются),
Nightscout — подменёнными методами NightscoutClient, БД — SQLite в памяти (app/db.py Dev 1).
Сеть не нужна.

Владелец: Dev 3.
"""
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("aiogram")   # без aiogram эти тесты пропускаются, остальные работают

from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    EditMessageText,
    GetFile,
    GetMe,
    SendMessage,
    SendPhoto,
)
from aiogram.types import CallbackQuery, Chat, File, Message, PhotoSize, Update, User

from app import clock, db, pipeline
from app.bot import notify, storage, texts
from app.bot.handlers import graph, nutrilens
from app.bot.main import create_bot, create_dispatcher
from app.models import Alert, AlertKind, Level, Mode, ModeState, Reading
from app.ns.client import NightscoutClient, NSAuthError, NSUnavailable

DATA = Path(__file__).resolve().parent.parent / "fake_cgm" / "data"
USER = User(id=111, is_bot=False, first_name="Аня")
FRIEND = User(id=222, is_bot=False, first_name="Боря")
STRANGER = User(id=333, is_bot=False, first_name="Вася")
BOT_USERNAME = "diabetrics_test_bot"
T0 = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
# Один Dispatcher на все тесты: роутеры — объекты модулей и подключаются только к одному.
DP = create_dispatcher()


class FakeSession(BaseSession):
    """Вместо HTTP-запросов в Telegram — запись в список."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.blocked: set[int] = set()     # эти чаты «заблокировали бота»

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):   # скачивание файла (фото для NutriLens)
        yield b"\xff\xd8 fake jpeg " * 20

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage) and method.chat_id in self.blocked:
            raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u1", file_path="photos/1.jpg")
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="DiaBetrics", username=BOT_USERNAME)
        if isinstance(method, (SendMessage, SendPhoto)):
            chat = Chat(id=method.chat_id, type="private")
            sent = Message(message_id=len(self.requests), date=T0, chat=chat, text=getattr(method, "text", None))
            return sent.as_(bot)   # как в настоящем aiogram: у ответа можно вызвать .edit_text()
        return True


class Tg:
    """Люди пишут боту и жмут кнопки; смотрим, что бот отправил."""

    def __init__(self) -> None:
        self.bot = create_bot("123456:TEST")
        self.session = FakeSession()
        self.bot.session = self.session
        self.n = 0

    async def _feed(self, **event) -> list:
        self.n += 1
        start = len(self.session.requests)
        await DP.feed_update(self.bot, Update(update_id=self.n, **event))
        return self.session.requests[start:]

    async def send(self, text: str, user: User = USER) -> list:
        chat = Chat(id=user.id, type="private")
        msg = Message(message_id=self.n + 1000, date=T0, chat=chat, from_user=user, text=text)
        return await self._feed(message=msg)

    async def send_photo(self, user: User = USER) -> list:
        chat = Chat(id=user.id, type="private")
        photo = [PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600)]
        msg = Message(message_id=self.n + 1000, date=T0, chat=chat, from_user=user, photo=photo)
        return await self._feed(message=msg)

    async def press(self, data: str, user: User = USER, text: str = "…") -> list:
        menu = Message(message_id=1, date=T0, chat=Chat(id=user.id, type="private"), text=text)
        cb = CallbackQuery(id=str(self.n), from_user=user, chat_instance="ci", data=data, message=menu)
        return await self._feed(callback_query=cb)


def texts_of(requests: list, chat_id: int | None = None) -> list[str]:
    return [
        r.text for r in requests
        if isinstance(r, (SendMessage, EditMessageText)) and (chat_id is None or r.chat_id == chat_id)
    ]


def buttons_of(request) -> list[str]:
    return [b.callback_data for row in request.reply_markup.inline_keyboard for b in row]


def toasts_of(requests: list) -> list[str | None]:
    return [r.text for r in requests if isinstance(r, AnswerCallbackQuery)]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fresh_db():
    """Своя БД в памяти на каждый тест; состояние онбординга (FSM) — тоже с нуля."""
    storage.reset()                      # db.configure("sqlite:///:memory:")
    DP.storage.storage.clear()
    clock.freeze(T0)
    yield
    clock.unfreeze()
    notify.setup(None)


@pytest.fixture
def fake_ns(monkeypatch):
    """Nightscout: токен "good" подходит, адрес https://down.example не отвечает."""
    raw = json.loads((DATA / "fast_drop.json").read_text())[:10]
    last_ms = max(e["date"] for e in raw)

    def guard(ns: NightscoutClient) -> None:
        if str(ns._http.base_url).rstrip("/") == "https://down.example":
            raise NSUnavailable("нет соединения")
        if ns._token != "good":
            raise NSAuthError("HTTP 401")

    async def check(self):
        guard(self)

    async def get_server_time(self):
        guard(self)
        return last_ms + 3 * 60_000

    async def get_entries(self, since=None, count=None):
        guard(self)
        return sorted(raw, key=lambda e: -e["date"])[:count]

    monkeypatch.setattr(NightscoutClient, "check", check)
    monkeypatch.setattr(NightscoutClient, "get_server_time", get_server_time)
    monkeypatch.setattr(NightscoutClient, "get_entries", get_entries)
    return datetime.fromtimestamp(last_ms / 1000, UTC)


def connect(user: User = USER, token: str | None = "good") -> None:
    storage.save_user(storage.BotUser(user.id, "https://my-cgm.example.com", token, "Europe/Moscow",
                                      name=user.first_name))


def owner_id() -> int:
    return db.get_user_by_chat(USER.id).id


def bind_trusted(friend: User = FRIEND, code: str = "code-1") -> None:
    connect()
    db.create_invite(owner_id(), code, T0)
    db.accept_invite(code, friend.id, friend.first_name, T0)


def add_alert(*, escalated: bool = False, message_id: int | None = None) -> int:
    alert = Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, value=2.8, needs_ack=True,
                  user_id=owner_id(), escalated_at=T0 if escalated else None)
    db.save_alert(alert, sent_at=T0)
    if message_id is not None:
        db.mark_sent(alert.id, T0, message_id)
    return alert.id


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
        assert "tz:Asia/Yekaterinburg" in buttons_of(reqs[-1])

        assert "Часовой пояс: Екатеринбург (UTC+5)" in texts_of(await tg.press("tz:Asia/Yekaterinburg"))[0]

    run(scenario())
    user = storage.get_user(USER.id)
    assert (user.ns_url, user.ns_token, user.tz, user.name) == (
        "https://my-cgm.example.com", "good", "Asia/Yekaterinburg", "Аня")


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


# --- /trusted ---

def test_trusted_needs_connected_nightscout():
    assert texts_of(run(Tg().send("/trusted"))) == [texts.NOW_NOT_CONNECTED]


def test_trusted_invite_accept_and_off():
    connect()

    async def scenario():
        tg = Tg()
        invite = texts_of(await tg.send("/trusted"))[0]
        assert "10 мин" in invite
        link = re.search(rf"https://t\.me/{BOT_USERNAME}\?start=(invite_[\w-]+)", invite)
        assert link, invite

        reqs = await tg.send(f"/start {link.group(1)}", user=FRIEND)
        assert texts_of(reqs, FRIEND.id)[0].startswith("Вы — доверенное лицо для пользователя Аня.")
        assert buttons_of(reqs[0])[0].startswith("leave:")
        assert texts_of(reqs, USER.id) == ["✅ Боря теперь ваше доверенное лицо."]

        sent = texts_of(await tg.send("/trusted"))
        assert sent[0] == "Доверенные лица: Боря.\nОтвязать всех — /trusted off"
        assert "start=invite_" in sent[1], "можно пригласить ещё одного"
        assert texts_of(await tg.send(f"/start {link.group(1)}", user=STRANGER)) == [texts.INVITE_NOT_FOUND]

        reqs = await tg.send("/trusted off")
        assert texts_of(reqs, USER.id) == ["Готово, доверенные лица отвязаны: Боря."]
        assert texts_of(reqs, FRIEND.id) == ["Вы больше не доверенное лицо для пользователя Аня."]
        assert texts_of(await tg.send("/trusted off")) == [texts.TRUSTED_NOTHING_TO_REMOVE]

    run(scenario())
    assert db.trusted_chat_ids(owner_id()) == []


def test_own_link_does_not_burn_the_invite():
    connect()
    db.create_invite(owner_id(), "code-1", T0)
    assert texts_of(run(Tg().send("/start invite_code-1"))) == [texts.INVITE_OWN]
    reqs = run(Tg().send("/start invite_code-1", user=FRIEND))
    assert texts_of(reqs, FRIEND.id)[0].startswith("Вы — доверенное лицо"), "ссылка осталась рабочей"


def test_new_link_invalidates_old_and_several_trusted_allowed():
    connect()

    async def scenario():
        tg = Tg()
        old = re.search(r"start=(invite_[\w-]+)", texts_of(await tg.send("/trusted"))[0]).group(1)
        new = re.search(r"start=(invite_[\w-]+)", texts_of(await tg.send("/trusted"))[0]).group(1)
        assert texts_of(await tg.send(f"/start {old}", user=FRIEND)) == [texts.INVITE_NOT_FOUND]
        await tg.send(f"/start {new}", user=FRIEND)
        assert texts_of(await tg.send(f"/start {new}", user=FRIEND)) == [texts.INVITE_NOT_FOUND]
        third = re.search(r"start=(invite_[\w-]+)", texts_of(await tg.send("/trusted"))[1]).group(1)
        await tg.send(f"/start {third}", user=STRANGER)
        assert texts_of(await tg.send("/trusted"))[0] == "Доверенные лица: Боря, Вася.\nОтвязать всех — /trusted off"

    run(scenario())
    assert sorted(db.trusted_chat_ids(owner_id())) == [FRIEND.id, STRANGER.id]


def test_already_trusted_person_opens_new_link():
    bind_trusted()
    db.create_invite(owner_id(), "code-2", T0)
    assert texts_of(run(Tg().send("/start invite_code-2", user=FRIEND))) == [
        "Вы уже доверенное лицо для пользователя Аня."]


def test_trusted_can_leave_by_button():
    bind_trusted()
    leave = f"leave:{owner_id()}"

    async def scenario():
        tg = Tg()
        reqs = await tg.send("/trusted", user=FRIEND)          # у Бори нет своего Nightscout
        assert texts_of(reqs) == ["Вы — доверенное лицо для: Аня."]
        assert buttons_of(reqs[0]) == [leave]

        reqs = await tg.press(leave, user=FRIEND)
        assert texts_of(reqs, FRIEND.id) == ["Вы больше не доверенное лицо для пользователя Аня."]
        assert texts_of(reqs, USER.id) == ["Боря больше не ваше доверенное лицо. Пригласить другого — /trusted"]
        assert toasts_of(await tg.press(leave, user=FRIEND)) == [texts.TRUSTED_ALREADY_LEFT]

    run(scenario())
    assert db.trusted_chat_ids(owner_id()) == []


# --- «Я в порядке» ---

def test_ack_by_owner_dims_button_and_saves():
    connect()
    alert_id = add_alert()

    async def scenario():
        tg = Tg()
        reqs = await tg.press(f"ack:{alert_id}", text="🔴 Очень низкий сахар")
        assert toasts_of(reqs) == [texts.ACK_DONE]
        edited = [r for r in reqs if isinstance(r, EditMessageText)]
        assert edited[0].text == "🔴 Очень низкий сахар\n\n✅ Подтверждено в 15:00"   # 12:00 UTC = 15:00 МСК
        assert edited[0].reply_markup is None, "кнопка погасла"
        assert toasts_of(await tg.press(f"ack:{alert_id}")) == [texts.ACK_ALREADY]

    run(scenario())
    alert = db.get_alert(alert_id)
    assert (alert.acked_at, alert.acked_by) == (T0, USER.id)


def test_ack_by_trusted_notifies_owner_and_dims_owner_message():
    bind_trusted()
    alert_id = add_alert(escalated=True, message_id=555)

    async def scenario():
        reqs = await Tg().press(f"ack:{alert_id}", user=FRIEND)
        assert toasts_of(reqs) == [texts.ACK_DONE]
        assert texts_of(reqs, USER.id) == ["✅ Боря подтверждает: всё в порядке."]
        dim = [r for r in reqs if isinstance(r, EditMessageReplyMarkup)]
        assert (dim[0].chat_id, dim[0].message_id) == (USER.id, 555)

    run(scenario())
    assert db.get_alert(alert_id).acked_by == FRIEND.id


def test_ack_by_owner_after_escalation_tells_trusted():
    bind_trusted()
    alert_id = add_alert(escalated=True)
    reqs = run(Tg().press(f"ack:{alert_id}"))
    assert texts_of(reqs, FRIEND.id) == ["✅ Аня на связи: нажата кнопка «Я в порядке»."]


def test_ack_forbidden_for_strangers_and_unknown_alert():
    connect()
    alert_id = add_alert()
    assert toasts_of(run(Tg().press(f"ack:{alert_id}", user=STRANGER))) == [texts.ACK_FORBIDDEN]
    assert toasts_of(run(Tg().press("ack:9999"))) == [texts.ACK_NOT_FOUND]
    assert db.get_alert(alert_id).acked_at is None


# --- notify ---

def test_notify_critical_has_ack_button_others_dont():
    async def scenario():
        tg = Tg()
        notify.setup(tg.bot)
        await notify.notify(Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, needs_ack=True, id=7), 111)
        await notify.notify(Alert(Level.IMPORTANT, AlertKind.LOW, T0, 3.6, id=8), 111)
        return tg.session.requests

    critical, important = run(scenario())
    assert critical.chat_id == 111 and critical.text.startswith("🔴 15:00 · <b>Очень низкий сахар: 2.8</b>")
    assert buttons_of(critical) == ["ack:7"]
    assert important.text.startswith("🟠") and important.reply_markup is None


def test_notify_with_trusted_adds_hint_and_returns_message_id():
    bind_trusted()
    alert_id = add_alert()

    async def scenario():
        tg = Tg()
        notify.setup(tg.bot)
        alert = Alert(Level.CRITICAL, AlertKind.VERY_LOW, T0, 2.8, needs_ack=True, id=alert_id)
        message_id = await notify.notify(alert, USER.id)
        return tg.session.requests[0], message_id

    sent, message_id = run(scenario())
    assert sent.text.endswith("Если не ответите за 10 мин, я напишу доверенному лицу.")
    assert message_id == 1, "pipeline сохраняет его через db.mark_sent"


def test_notify_trusted_has_name_value_trend_minutes_and_button():
    bind_trusted()
    alert_id = add_alert()
    db.save_readings(owner_id(), [Reading(T0, 2.8, "SingleDown")])

    async def scenario():
        tg = Tg()
        notify.setup(tg.bot)
        alert = db.get_alert(alert_id)                    # как в pipeline: из БД, с user_id
        await notify.notify_trusted(alert, FRIEND.id, minutes_without_ack=12)
        return tg.session.requests[0]

    sent = run(scenario())
    assert sent.chat_id == FRIEND.id
    assert sent.text.startswith("🔴 <b>Аня не отвечает на уведомление уже 12 мин.</b>")
    assert "Сахар: 2.8 ммоль/л ↓" in sent.text
    button = sent.reply_markup.inline_keyboard[0][0]
    assert (button.text, button.callback_data) == (texts.BUTTON_TRUSTED_OK, f"ack:{alert_id}")


# --- /graph ---

def test_graph_not_ready_without_plot(monkeypatch):
    monkeypatch.setattr(graph, "load_plot", lambda: None)
    assert texts_of(run(Tg().send("/graph"))) == [texts.GRAPH_NOT_READY]


def test_graph_sends_picture_from_plot(monkeypatch, fake_ns):
    calls = []

    def fake_plot(readings, hours, tz):     # сигнатура как у app/plot.py Dev 4
        calls.append((len(readings), hours, tz))
        return b"\x89PNG fake"

    monkeypatch.setattr(graph, "load_plot", lambda: fake_plot)
    connect()
    clock.freeze(fake_ns + timedelta(minutes=3))

    async def scenario():
        tg = Tg()
        reqs = await tg.send("/graph")
        photo = next(r for r in reqs if isinstance(r, SendPhoto))
        assert photo.caption == "Сахар за 3 ч"
        assert buttons_of(photo) == ["graph:3", "graph:6", "graph:24"]
        await tg.press("graph:24")

    run(scenario())
    assert calls == [(10, 3, ZoneInfo("Europe/Moscow")), (10, 24, ZoneInfo("Europe/Moscow"))]   # объект, не строка


# --- стык с pipeline Dev 1 ---

class FakeNS:
    """Nightscout для pipeline: четыре показания по 2.8 ммоль/л (sgv 50) — очень низкий сахар."""

    SGV = 50

    def __init__(self, url, token):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def get_server_time(self):
        return int(T0.timestamp() * 1000)

    async def get_entries(self, since=None, count=None):
        return [{"type": "sgv", "sgv": self.SGV, "direction": "Flat",
                 "date": int((T0 - timedelta(minutes=5 * i)).timestamp() * 1000)} for i in range(4)]


def test_pipeline_sends_critical_with_button_and_saves_message_id(monkeypatch):
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()
    clock.freeze(T0)
    connect()

    async def scenario():
        tg = Tg()
        notify.setup(tg.bot)
        user = db.get_user_by_chat(USER.id)
        sent = await pipeline.process_user(user, db, FakeNS)
        return tg.session.requests, sent

    requests, sent = run(scenario())
    critical = [a for a in sent if a.kind is AlertKind.VERY_LOW]
    assert critical, sent
    message = next(r for r in requests if isinstance(r, SendMessage) and "Очень низкий" in r.text)
    assert buttons_of(message) == [f"ack:{critical[0].id}"]
    assert db.alert_message_id(critical[0].id) == message_id_of(requests, message)


def message_id_of(requests: list, request) -> int:
    """FakeSession выдаёт message_id по порядку запросов (см. make_request)."""
    return requests.index(request) + 1


# --- день 4: /settings ---

def test_settings_needs_connected_nightscout():
    assert texts_of(run(Tg().send("/settings"))) == [texts.NOW_NOT_CONNECTED]


def test_settings_change_escalation_night_and_tz():
    connect()

    async def scenario():
        tg = Tg()
        reqs = await tg.send("/settings")
        summary = texts_of(reqs)[0]
        assert "через 10 мин" in summary and "сам включается с 23:00 до 07:00" in summary
        assert "Москва (UTC+3)" in summary
        assert buttons_of(reqs[0]) == ["set:esc:", "set:night:", "set:tz:"]

        reqs = await tg.press("set:esc:")
        assert "повторится через 5 мин" in texts_of(reqs)[0]
        assert buttons_of(reqs[-1]) == ["set:esc:10", "set:esc:15", "set:esc:20", "set:esc:30", "set:menu:"]
        reqs = await tg.press("set:esc:20")
        assert toasts_of(reqs) == [texts.SETTINGS_SAVED] and "через 20 мин" in texts_of(reqs)[0]

        assert texts_of(await tg.press("set:night:")) == [texts.SETTINGS_ASK_NIGHT_START]
        reqs = await tg.press("set:night:2200")
        assert texts_of(reqs) == ["Ночной режим с 22:00. Во сколько выключать?"]
        assert "set:night:2200-0600" in buttons_of(reqs[-1])
        assert "с 22:00 до 06:00" in texts_of(await tg.press("set:night:2200-0600"))[0]
        assert "сам не включается" in texts_of(await tg.press("set:nightoff:"))[0]

        await tg.press("set:tz:")
        assert "Екатеринбург (UTC+5)" in texts_of(await tg.press("set:tz:Asia/Yekaterinburg"))[0]

    run(scenario())
    s = storage.get_settings(USER.id)
    assert (s.escalation_minutes, s.night_start, s.night_end, s.tz) == (20, "00:00", "00:00", "Asia/Yekaterinburg")
    assert db.get_user_by_chat(USER.id).escalation_minutes == 20, "pipeline берёт отсюда trusted_after_minutes"


def test_forged_settings_buttons_change_nothing():
    connect()

    async def scenario():
        tg = Tg()
        for data in ("set:esc:3", "set:night:2300-1200", "set:night:1500-0700", "set:tz:Mars/Base", "set:bogus:1"):
            assert texts_of(await tg.press(data)) == [], data

    run(scenario())
    s = storage.get_settings(USER.id)
    assert (s.escalation_minutes, s.night_start, s.night_end, s.tz) == (10, "23:00", "07:00", "Europe/Moscow")


# --- день 4: ночной режим по расписанию ---

def test_mode_menu_shows_schedule_and_scheduled_night():
    connect()
    storage.save_settings(USER.id, night_start="14:00", night_end="16:00")    # T0 = 15:00 МСК
    text = texts_of(run(Tg().send("/mode")))[0]
    assert text.startswith("Сейчас: 🌙 Ночной по расписанию, до 16:00")
    assert "🌙 Ночной включается сам с 14:00 до 16:00 — изменить: /settings" in text


def test_manual_mode_wins_over_schedule():
    connect()
    storage.save_settings(USER.id, night_start="14:00", night_end="16:00")
    storage.set_mode(USER.id, ModeState(Mode.AFTER_MEAL, until=T0 + timedelta(hours=2)))
    assert texts_of(run(Tg().send("/mode")))[0].startswith("Сейчас: 🍽 После еды, ещё 2 ч")


def test_now_shows_scheduled_night(fake_ns):
    connect()
    now = fake_ns + timedelta(minutes=3)                     # 16:48 МСК
    storage.save_settings(USER.id, night_start="16:00", night_end="18:00")
    clock.freeze(now)
    assert "Режим: 🌙 Ночной по расписанию, до 18:00" in texts_of(run(Tg().send("/now")))[0]


class LowNS(FakeNS):
    SGV = 66   # 3.7 ммоль/л — низкий, но не очень низкий


@pytest.mark.parametrize(("night", "level"), [
    (("14:00", "16:00"), Level.CRITICAL),     # T0 = 15:00 МСК — ночь: низкий сразу критический
    (("00:00", "00:00"), Level.IMPORTANT),    # расписание выключено
])
def test_pipeline_applies_night_schedule(monkeypatch, night, level):
    monkeypatch.setenv("CLOCK", "system")
    clock.reset()
    clock.freeze(T0)
    connect()
    storage.save_settings(USER.id, night_start=night[0], night_end=night[1])
    user = db.get_user_by_chat(USER.id)
    sent = run(pipeline.process_user(user, db, LowNS))
    low = [a for a in sent if a.kind is AlertKind.LOW]
    assert low and low[0].level is level, sent


# --- день 4: ошибки ---

def test_user_blocked_bot_is_deactivated_and_comes_back_with_start():
    connect()
    alert_id = add_alert()

    async def scenario():
        tg = Tg()
        tg.session.blocked.add(USER.id)
        notify.setup(tg.bot)
        message_id = await notify.notify(db.get_alert(alert_id), USER.id)
        assert message_id is None
        assert db.get_user_by_chat(USER.id).is_active is False
        assert db.active_users() == [], "pipeline больше не опрашивает"

        tg.session.blocked.clear()
        await tg.send("/start")                                 # разблокировал и вернулся
        assert db.get_user_by_chat(USER.id).is_active is True

    run(scenario())


def test_trusted_blocked_bot_owner_is_told():
    bind_trusted()
    alert_id = add_alert()

    async def scenario():
        tg = Tg()
        tg.session.blocked.add(FRIEND.id)
        notify.setup(tg.bot)
        result = await notify.notify_trusted(db.get_alert(alert_id), FRIEND.id, 10)
        return result, tg.session.requests

    result, requests = run(scenario())
    assert result is None
    assert any(t.startswith("⚠️ Не получилось написать доверенному лицу (Боря)") for t in texts_of(requests, USER.id))


def test_unexpected_error_gets_calm_answer(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("внутренняя ошибка с подробностями")

    monkeypatch.setattr(storage, "get_user", boom)
    reqs = run(Tg().send("/now"))
    assert texts_of(reqs) == [texts.ERROR_GENERIC], "подробности — в лог, не в чат"
    monkeypatch.setattr(db, "get_alert", boom)
    assert toasts_of(run(Tg().press("ack:1"))) == [texts.ERROR_GENERIC]


# --- день 4: NutriLens в боте ---

def test_food_photo_shows_kbju_and_meal_button(monkeypatch):
    async def analyze(image_bytes):
        assert len(image_bytes) > 100, "фото скачано"
        return {"name": "Паста <карбонара>", "kcal": 650, "protein": 25, "fat": 30, "carbs": 70}

    monkeypatch.setattr(nutrilens, "analyze_photo", analyze)

    async def scenario():
        tg = Tg()
        reqs = await tg.send_photo()
        assert texts_of(reqs)[0] == texts.NUTRI_ANALYZING
        result = next(r for r in reqs if isinstance(r, EditMessageText))
        assert result.text.startswith("🍽 <b>Паста &lt;карбонара&gt;</b>")      # имя экранировано
        assert "Углеводы <b>70 г</b>" in result.text
        assert buttons_of(result) == ["mode:after_meal:0"]

        reqs = await tg.press("mode:after_meal:0", text="🍽 Паста карбонара …")
        assert any(isinstance(r, EditMessageReplyMarkup) for r in reqs), "у оценки убрана только кнопка"
        assert not any(isinstance(r, EditMessageText) for r in reqs), "КБЖУ не затёрты"
        assert texts_of(reqs) == ["На сколько включить «🍽 После еды»?"]

    run(scenario())


@pytest.mark.parametrize(("result", "expected"), [(None, texts.NUTRI_NOT_RECOGNIZED), ("boom", texts.NUTRI_ERROR)])
def test_food_photo_failures(monkeypatch, result, expected):
    async def analyze(image_bytes):
        if result == "boom":
            raise RuntimeError("Vision API: 500, ключ sk-секрет")
        return result

    monkeypatch.setattr(nutrilens, "analyze_photo", analyze)
    reqs = run(Tg().send_photo())
    assert texts_of(reqs)[-1] == expected
    assert not any("sk-" in t for t in texts_of(reqs)), "текст ошибки не попадает в чат"

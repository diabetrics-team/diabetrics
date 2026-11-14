"""Генеральная репетиция демо — автоматически, без телефонов.

Владелец: Dev 3.

Проигрывает демо целиком и в том же порядке, что на выступлении, — двумя фазами:
  1) подготовка: fake-NS night_hypo в реальном времени с 23:00 (сахар спокойный) —
     Аня подключает Nightscout, Боря принимает приглашение;
  2) на сцене: fake-NS перезапускается на том же порту ускоренным с 01:45 — бот не трогаем,
     дальше гипо → повтор → сообщение Боре → «Связались» → /mode, /settings, /graph, фото еды.
Бот и pipeline — как в app/main.py, два «телефона» — Аня и Боря. Подменён только Telegram
(сообщения записываются), всё остальное настоящее: HTTP к fake-NS, БД, прогноз, движок,
эскалация, график, NutriLens.

    python scripts/demo_rehearsal.py                 # два прогона подряд, как требует TODO
    python scripts/demo_rehearsal.py --speed 30      # с той скоростью, что на сцене
    python scripts/demo_rehearsal.py --runs 1 -v     # один прогон, печатать все сообщения
    python scripts/demo_rehearsal.py --transcript out.json   # для скриншотов

Каждый прогон — с новой БД. Код выхода 0 — все шаги прошли.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from aiogram.client.session.base import BaseSession
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # запуск как скрипта: найти app

from app import clock, db, pipeline
from app.bot import notify
from app.bot.main import create_bot, create_dispatcher

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "secret"
ANYA = User(id=111, is_bot=False, first_name="Аня")
BORYA = User(id=222, is_bot=False, first_name="Боря")
NAMES = {ANYA.id: "Аня", BORYA.id: "Боря"}
WAIT_FOR_TRUSTED_MIN = 30   # минут сценария ждать сообщения Боре (эскалация — через 10)
DEMO_SKIP = 165             # на сцене сценарий начинается с 01:45 — гипо в 01:50


@dataclass
class Bubble:
    """Одно сообщение в чате — для проверок и скриншотов."""
    chat: int
    sender: str                      # "bot" | "user"
    text: str = ""                   # HTML, как отправлено
    buttons: list[list[str]] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)
    photo: str | None = None         # PNG в base64
    caption: str = ""
    deleted: bool = False
    message_id: int = 0
    step: str = ""
    versions: list[dict] = field(default_factory=list)   # как выглядело до каждой правки ботом

    def remember(self) -> None:
        self.versions.append({"text": self.text, "buttons": [row[:] for row in self.buttons]})


class RecordingSession(BaseSession):
    """Вместо Telegram: сообщения бота складываются в чаты, правки применяются к ним."""

    def __init__(self, chats: dict[int, list[Bubble]], step: list[str]) -> None:
        super().__init__()
        self.chats, self.step, self.next_id, self.toasts = chats, step, 1, []

    async def close(self) -> None:
        pass

    async def stream_content(self, *args, **kwargs):     # скачивание фото для NutriLens
        yield b"\xff\xd8 demo food photo " * 20

    def _find(self, chat: int, message_id: int) -> Bubble | None:
        return next((b for b in self.chats.get(chat, []) if b.message_id == message_id), None)

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetMe):
            return User(id=999, is_bot=True, first_name="DiaBetrics", username="diabetrics_demo_bot")
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="photos/1.jpg")
        if isinstance(method, (SendMessage, SendPhoto)):
            mid, self.next_id = self.next_id, self.next_id + 1
            kb = method.reply_markup.inline_keyboard if method.reply_markup else []
            bubble = Bubble(
                chat=method.chat_id, sender="bot", message_id=mid, step=self.step[0],
                text=getattr(method, "text", "") or "", caption=getattr(method, "caption", "") or "",
                buttons=[[b.text for b in row] for row in kb],
                callbacks=[b.callback_data for row in kb for b in row],
            )
            if isinstance(method, SendPhoto):
                bubble.photo = b64encode(method.photo.data).decode()
            self.chats.setdefault(method.chat_id, []).append(bubble)
            chat = Chat(id=method.chat_id, type="private")
            return Message(message_id=mid, date=datetime.now(), chat=chat, text=bubble.text).as_(bot)
        if isinstance(method, EditMessageText):
            if (b := self._find(method.chat_id, method.message_id)) is not None:
                b.remember()
                kb = method.reply_markup.inline_keyboard if method.reply_markup else []
                b.text, b.buttons = method.text, [[x.text for x in row] for row in kb]
                b.callbacks = [x.callback_data for row in kb for x in row]
            return True
        if isinstance(method, EditMessageReplyMarkup):
            if (b := self._find(method.chat_id, method.message_id)) is not None:
                b.remember()
                b.buttons, b.callbacks = [], []
            return True
        if isinstance(method, DeleteMessage):
            if (b := self._find(method.chat_id, method.message_id)) is not None:
                b.deleted = True
            return True
        if isinstance(method, AnswerCallbackQuery):
            self.toasts.append(method.text)
        return True


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_fake_ns(port: int, speed: float, skip: int) -> subprocess.Popen:
    cmd = [sys.executable, str(ROOT / "fake_cgm" / "fake_cgm.py"), "--serve", "--scenario", "night_hypo",
           "--speed", str(speed), "--skip", str(skip), "--token", TOKEN, "--port", str(port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("fake-NS не запустился")


class Demo:
    def __init__(self, dp, chats: dict[int, list[Bubble]], verbose: bool) -> None:
        self.dp, self.chats, self.verbose = dp, chats, verbose
        self.step = ["—"]
        self.bot = create_bot("123456:DEMO")
        self.session = RecordingSession(chats, self.step)
        self.bot.session = self.session
        self.n = 0

    def last(self, chat: int, n: int = 1) -> list[Bubble]:
        return [b for b in self.chats.get(chat, []) if b.sender == "bot"][-n:]

    async def _feed(self, **event) -> None:
        self.n += 1
        before = {c: len(v) for c, v in self.chats.items()}
        await self.dp.feed_update(self.bot, Update(update_id=self.n, **event))
        self.show_new(before)

    async def say(self, user: User, text: str) -> None:
        mid, self.session.next_id = self.session.next_id, self.session.next_id + 1
        self.chats.setdefault(user.id, []).append(Bubble(user.id, "user", text, message_id=mid, step=self.step[0]))
        msg = Message(message_id=mid, date=datetime.now(), chat=Chat(id=user.id, type="private"),
                      from_user=user, text=text)
        await self._feed(message=msg)

    async def photo(self, user: User) -> None:
        mid, self.session.next_id = self.session.next_id, self.session.next_id + 1
        self.chats.setdefault(user.id, []).append(Bubble(user.id, "user", "📷 фото еды", message_id=mid,
                                                         step=self.step[0]))
        msg = Message(message_id=mid, date=datetime.now(), chat=Chat(id=user.id, type="private"), from_user=user,
                      photo=[PhotoSize(file_id="f1", file_unique_id="u1", width=800, height=600)])
        await self._feed(message=msg)

    def show_new(self, before: dict[int, int]) -> None:
        if self.verbose:
            for chat, bubbles in self.chats.items():
                for b in bubbles[before.get(chat, 0):]:
                    if b.sender == "bot":
                        print(f"      бот → {NAMES[chat]}: {plain(b.text or b.caption)[:110]}")

    async def press(self, user: User, data: str) -> None:
        bubble = next((b for b in reversed(self.chats.get(user.id, [])) if data in b.callbacks), None)
        if bubble is None:
            raise AssertionError(f"у {NAMES[user.id]} нет кнопки {data}")
        msg = Message(message_id=bubble.message_id, date=datetime.now(),
                      chat=Chat(id=user.id, type="private"), text=plain(bubble.text))
        await self._feed(callback_query=CallbackQuery(id=str(self.n), from_user=user, chat_instance="c",
                                                      data=data, message=msg))


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&") \
        .replace("\n", " | ")


async def one_run(
    dp, db_url: str, speed: float, verbose: bool, port: int | None = None,
) -> tuple[bool, dict[int, list[Bubble]]]:
    port = port or free_port()
    ns = [start_fake_ns(port, speed=1, skip=0)]     # подготовка: реальное время, 23:00
    clock.reset()
    db.configure(db_url)
    dp.storage.storage.clear()         # шаги онбординга от прошлого прогона
    chats: dict[int, list[Bubble]] = {}
    demo = Demo(dp, chats, verbose)
    notify.setup(demo.bot)
    started, ok = time.monotonic(), True

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= condition
        mark = "✓" if condition else "✗"
        print(f"   {mark} {time.monotonic() - started:5.1f} с  {name}{'' if condition else '  — ' + detail}")

    def go_on_stage() -> None:
        """Как на сцене: остановить fake-NS и запустить ускоренным с 01:45 на том же порту."""
        ns[0].terminate()
        ns[0].wait(timeout=5)
        ns[0] = start_fake_ns(port, speed=speed, skip=DEMO_SKIP)

    try:
        await _steps(demo, chats, port, check, go_on_stage, speed)
    except Exception as e:  # noqa: BLE001 — репетиция должна сказать, где сломалось, а не упасть
        check(f"прогон прерван на шаге «{demo.step[0]}»", False, f"{type(e).__name__}: {e}")
    finally:
        ns[0].terminate()
        ns[0].wait(timeout=5)
        await demo.bot.session.close()
        notify.setup(None)
    return ok, chats


async def _steps(demo: Demo, chats: dict[int, list[Bubble]], port: int, check, go_on_stage, speed: float) -> None:
    demo.step[0] = "start"
    await demo.say(ANYA, "/start")
    check("Аня: приветствие и вопрос об адресе", "Пришлите адрес" in demo.last(ANYA.id)[0].text)
    demo.step[0] = "onboarding"
    await demo.say(ANYA, f"http://localhost:{port}")
    await demo.say(ANYA, TOKEN)
    await demo.press(ANYA, "tz:Europe/Moscow")
    token_bubble = next(b for b in chats[ANYA.id] if b.sender == "user" and b.text == TOKEN)
    check("Аня: Nightscout подключён, токен удалён из чата",
          demo.last(ANYA.id)[0].text.startswith("Готово!") and token_bubble.deleted,
          plain(demo.last(ANYA.id)[0].text))

    demo.step[0] = "trusted"
    await demo.say(ANYA, "/trusted")
    link = re.search(r"start=(invite_[\w-]+)", demo.last(ANYA.id)[0].text)
    demo.step[0] = "trusted_borya"
    await demo.say(BORYA, f"/start {link.group(1)}" if link else "/start")
    check("Боря принял приглашение, Аня получила подтверждение",
          demo.last(BORYA.id)[0].text.startswith("Вы — доверенное лицо")
          and "Боря теперь ваше доверенное лицо" in demo.last(ANYA.id)[0].text)

    demo.step[0] = "prep_tick"
    await pipeline.tick()                       # за кулисами pipeline уже работает
    calm = not any(b.text.startswith(("🔴", "🟠")) for b in chats[ANYA.id])
    check("подготовка: сахар спокойный, уведомлений нет", calm)

    demo.step[0] = "on_stage"
    go_on_stage()
    await pipeline.tick()                       # первый опрос после перезапуска fake-NS
    demo.step[0] = "now"
    await demo.say(ANYA, "/now")
    check("/now: сахар, прогноз, ночной режим по расписанию",
          "по расписанию" in demo.last(ANYA.id)[0].text, plain(demo.last(ANYA.id)[0].text))

    demo.step[0] = "alerts"
    first_critical = trusted_alert = None
    minute = 60 / speed                         # секунд реального времени в минуте сценария
    deadline = time.monotonic() + WAIT_FOR_TRUSTED_MIN * minute
    while time.monotonic() < deadline and trusted_alert is None:
        before = {c: len(v) for c, v in chats.items()}
        await pipeline.tick()
        demo.show_new(before)
        bot_to_anya = [b for b in chats[ANYA.id] if b.sender == "bot" and b.text.startswith("🔴")]
        if first_critical is None and bot_to_anya:
            first_critical = bot_to_anya[0]
            check("Ане пришло 🔴 критическое с кнопкой «Я в порядке»",
                  any(c.startswith("ack:") for c in first_critical.callbacks))
        trusted_alert = next((b for b in chats.get(BORYA.id, []) if "не отвечает" in b.text), None)
        await asyncio.sleep(minute)             # опрос раз в минуту сценария, как POLL_SECONDS
    repeats = [b for b in chats[ANYA.id] if b.sender == "bot" and b.text.startswith("🔴")]
    check("через 5 мин сценария — повтор Ане", len(repeats) >= 2, f"критических: {len(repeats)}")
    check("через 10 мин сценария — сообщение Боре", trusted_alert is not None,
          f"за {WAIT_FOR_TRUSTED_MIN} мин сценария не пришло")
    if trusted_alert is None:
        return

    demo.step[0] = "ack"
    ack = next(c for c in trusted_alert.callbacks if c.startswith("ack:"))
    anya_same_alert = [b for b in chats[ANYA.id] if ack in b.callbacks]   # первое + повтор
    await demo.press(BORYA, ack)
    check("Боря нажал кнопку → Ане «Боря подтверждает», кнопки погасли",
          any("Боря подтверждает" in b.text for b in demo.last(ANYA.id, 3))
          and not trusted_alert.callbacks and not anya_same_alert[-1].callbacks)

    demo.step[0] = "mode"
    await demo.say(ANYA, "/mode")
    check("/mode: меню режимов", demo.last(ANYA.id)[0].text.startswith("Сейчас:"))
    demo.step[0] = "settings"
    await demo.say(ANYA, "/settings")
    check("/settings: настройки", "Настройки" in demo.last(ANYA.id)[0].text)
    demo.step[0] = "graph"
    await demo.say(ANYA, "/graph")
    graph = demo.last(ANYA.id)[0]
    check("/graph: картинка PNG", graph.photo is not None and graph.photo.startswith("iVBORw0KGgo"))
    demo.step[0] = "nutrilens"
    await demo.photo(ANYA)
    check("фото еды → КБЖУ и кнопка «После еды»", demo.last(ANYA.id)[0].text.startswith("🍽"))


async def main() -> int:
    ap = argparse.ArgumentParser(description="Генеральная репетиция демо DiaBetrics")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--speed", type=float, default=60, help="ускорение fake-NS на сцене (на выступлении — 30)")
    ap.add_argument("--port", type=int, help="порт fake-NS (по умолчанию — любой свободный)")
    ap.add_argument("--db", help="одна БД на все прогоны (по умолчанию — новая на каждый)")
    ap.add_argument("--transcript", help="сохранить чаты последнего прогона в JSON (для скриншотов)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    os.environ["CLOCK"] = "nightscout"   # демо идёт по часам fake-NS; clock читает при первом вызове
    dp = create_dispatcher()           # один на процесс — роутеры подключаются к одному Dispatcher
    results, chats = [], {}
    with tempfile.TemporaryDirectory() as tmp:
        for run in range(1, a.runs + 1):
            url = a.db or f"sqlite:///{tmp}/demo-{run}.db"
            print(f"Прогон {run}/{a.runs}  (БД {url})")
            ok, chats = await one_run(dp, url, a.speed, a.verbose, a.port)
            results.append(ok)
    if a.transcript:
        Path(a.transcript).write_text(json.dumps(
            {NAMES[c]: [b.__dict__ for b in bs] for c, bs in chats.items()}, ensure_ascii=False, indent=1))
    print("ИТОГ:", "все прогоны без сбоев ✓" if all(results) else f"есть сбои ✗ {results}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

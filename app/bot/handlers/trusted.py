"""
Доверенные лица: /trusted (приглашение), /start invite_<код> (принять), /trusted off,
кнопка «Отписаться» у самого доверенного лица.

Владелец: Dev 3. Данные — функции app/db.py (Dev 1). Доверенных лиц у пользователя
может быть несколько: pipeline при эскалации пишет всем (db.trusted_chat_ids).
"""
from __future__ import annotations

import logging
import secrets
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.utils.deep_linking import create_start_link

from app import clock, db
from app.bot import texts
from app.bot.keyboards import LeaveCb, leave_keyboard

router = Router(name="trusted")
log = logging.getLogger(__name__)


async def send_safe(bot: Bot, chat_id: int, text: str) -> None:
    """Сообщение другому человеку. Если он заблокировал бота — не падаем, а пишем в лог."""
    try:
        await bot.send_message(chat_id, text)
    except TelegramAPIError as e:
        log.warning("не удалось отправить сообщение в чат %s: %s", chat_id, type(e).__name__)


def _name(name: str | None) -> str:
    return escape(name) if name else "пользователя"


def _names(names: list[str | None]) -> str:
    return ", ".join(escape(n) if n else "—" for n in names)


# Ссылка t.me/<бот>?start=invite_<код> приходит как "/start invite_<код>".
# Роутер trusted подключён ВЫШЕ start, иначе обычный /start перехватит приглашение.
@router.message(CommandStart(deep_link=True, magic=F.args.startswith("invite_")))
async def accept_invite(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.from_user is None:
        return
    me = message.from_user
    code = command.args.removeprefix("invite_")
    owner = db.invite_owner(code)
    if owner is None:
        await message.answer(texts.INVITE_NOT_FOUND)
        return
    if owner.chat_id == me.id:                    # проверка ДО accept: ссылка не сгорает
        await message.answer(texts.INVITE_OWN)
        return
    if me.id in db.trusted_chat_ids(owner.id):
        await message.answer(texts.INVITE_ALREADY.format(name=_name(owner.name)))
        return
    if db.accept_invite(code, me.id, me.first_name, clock.now()) is None:
        await message.answer(texts.INVITE_NOT_FOUND)  # кто-то успел принять раньше
        return
    await message.answer(
        texts.INVITE_ACCEPTED.format(name=_name(owner.name)),
        reply_markup=leave_keyboard([(owner.id, owner.name or "пользователя")]),
    )
    await send_safe(bot, owner.chat_id, texts.INVITE_ACCEPTED_OWNER.format(name=escape(me.first_name)))


@router.message(Command("trusted"))
async def trusted_cmd(message: Message, command: CommandObject, bot: Bot) -> None:
    if message.from_user is None:
        return
    me = message.from_user
    if (command.args or "").strip().lower() == "off":
        await _remove_all(message, bot)
        return

    user = db.get_user_by_chat(me.id)
    guarded = db.guarded_users(me.id)
    if user is None or user.ns_url is None:
        if not guarded:
            await message.answer(texts.NOW_NOT_CONNECTED)
    else:
        contacts = db.trusted_contacts(user.id)
        if contacts:
            await message.answer(texts.TRUSTED_CURRENT.format(names=_names([c.name for c in contacts])))
        now = clock.now()
        db.upsert_user(me.id, now, name=me.first_name)   # имя увидит доверенное лицо
        db.drop_pending_invites(user.id)                  # старые ссылки больше не работают
        code = secrets.token_urlsafe(9)   # 12 символов из A-Z a-z 0-9 _ - — подходит для deep link
        db.create_invite(user.id, code, now)
        link = await create_start_link(bot, f"invite_{code}")
        await message.answer(texts.TRUSTED_INVITE.format(minutes=user.escalation_minutes, link=link))
    if guarded:
        await message.answer(
            texts.TRUSTED_GUARDING.format(names=_names([u.name for u in guarded])),
            reply_markup=leave_keyboard([(u.id, u.name or "пользователя") for u in guarded]),
        )


async def _remove_all(message: Message, bot: Bot) -> None:
    user = db.get_user_by_chat(message.from_user.id)
    contacts = db.trusted_contacts(user.id) if user is not None else []
    if not contacts:
        await message.answer(texts.TRUSTED_NOTHING_TO_REMOVE)
        return
    db.remove_trusted(user.id)                    # заодно удаляет непринятые приглашения
    await message.answer(texts.TRUSTED_REMOVED.format(names=_names([c.name for c in contacts])))
    owner_name = _name(user.name or message.from_user.first_name)
    for contact in contacts:
        await send_safe(bot, contact.chat_id, texts.TRUSTED_REMOVED_NOTICE.format(name=owner_name))


@router.callback_query(LeaveCb.filter())
async def leave(callback: CallbackQuery, callback_data: LeaveCb, bot: Bot) -> None:
    # chat_id = нажавший: даже с поддельным owner_id удалится только его собственная связь
    if db.remove_trusted(callback_data.owner_id, chat_id=callback.from_user.id) == 0:
        await callback.answer(texts.TRUSTED_ALREADY_LEFT)
        return
    await callback.answer()
    owner = db.get_user(callback_data.owner_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(texts.TRUSTED_REMOVED_NOTICE.format(name=_name(owner.name)))
    await send_safe(bot, owner.chat_id,
                    texts.TRUSTED_LEFT_NOTICE.format(name=escape(callback.from_user.first_name)))

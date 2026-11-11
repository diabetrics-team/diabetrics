"""
Онбординг: адрес Nightscout → токен → проверка → часовой пояс. Плюс /cancel.

Владелец: Dev 3.
Шаги диалога — состояния FSM (машины состояний): бот помнит, на каком шаге пользователь,
и следующий текст понимает как адрес или как токен.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot import nightscout, storage, texts
from app.bot.keyboards import OnboardingCb, TzCb, no_token_keyboard, tz_keyboard

router = Router(name="onboarding")


class Onboarding(StatesGroup):
    url = State()
    token = State()
    tz = State()


def normalize_url(text: str) -> str | None:
    """«my-cgm.herokuapp.com/» -> «https://my-cgm.herokuapp.com». None — не похоже на адрес."""
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc or " " in text:
        return None
    return text.rstrip("/")


async def ask_url(message: Message, state: FSMContext) -> None:
    """Начало онбординга. Вызывается из /start и по кнопке «Подключить другой»."""
    await state.set_state(Onboarding.url)
    await message.answer(texts.ONBOARDING_ASK_URL)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(texts.CANCELLED)


@router.callback_query(OnboardingCb.filter(F.action == "restart"))
async def restart(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await ask_url(callback.message, state)


@router.message(Onboarding.url, F.text)
async def got_url(message: Message, state: FSMContext) -> None:
    url = normalize_url(message.text)
    if url is None:
        await message.answer(texts.ONBOARDING_BAD_URL)
        return
    await state.update_data(url=url)
    await state.set_state(Onboarding.token)
    await message.answer(texts.ONBOARDING_ASK_TOKEN, reply_markup=no_token_keyboard())


@router.message(Onboarding.token, F.text)
async def got_token(message: Message, state: FSMContext) -> None:
    token = message.text.strip()
    try:
        await message.delete()          # токен не должен остаться в истории чата
    except TelegramBadRequest:
        pass                            # уже удалено или слишком старое — не страшно
    await _check_and_save(message, message.from_user.id, state, token)


@router.callback_query(Onboarding.token, OnboardingCb.filter(F.action == "no_token"))
async def no_token(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _check_and_save(callback.message, callback.from_user.id, state, token=None)


async def _check_and_save(chat: Message, user_id: int, state: FSMContext, token: str | None) -> None:
    # chat — любое сообщение из нужного чата, чтобы ответить туда;
    # user_id берём отдельно: у сообщения с кнопкой автор — сам бот.
    url = (await state.get_data())["url"]
    await chat.answer(texts.ONBOARDING_CHECKING)
    try:
        await nightscout.check(url, token)
    except nightscout.NsBadToken:
        await chat.answer(texts.ONBOARDING_TOKEN_REJECTED, reply_markup=no_token_keyboard())
        return                          # остаёмся на шаге «токен»
    except nightscout.NsUnavailable:
        await state.set_state(Onboarding.url)
        await chat.answer(texts.ONBOARDING_UNREACHABLE.format(url=escape(url)))
        return
    storage.save_user(storage.BotUser(telegram_id=user_id, ns_url=url, ns_token=token))
    await state.set_state(Onboarding.tz)
    await chat.answer(texts.ONBOARDING_ASK_TZ, reply_markup=tz_keyboard())


@router.callback_query(Onboarding.tz, TzCb.filter())
async def got_tz(callback: CallbackQuery, callback_data: TzCb, state: FSMContext) -> None:
    await callback.answer()
    if callback_data.name not in texts.TIMEZONES:   # данные кнопки можно подделать
        return
    user = storage.get_user(callback.from_user.id)
    if user is not None:
        user.tz = callback_data.name
        storage.save_user(user)
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            texts.ONBOARDING_DONE.format(tz=texts.TIMEZONES[callback_data.name])
        )

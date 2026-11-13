"""
Обработка фото еды (NutriLens).

Владелец: Dev 4 (распознавание — app/nutrilens.py). Тексты и оформление — Dev 3 (texts.py):
HTML, как во всём боте; название блюда экранируется; подробности ошибки — в лог, а не в чат.
"""
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.types import Message

from app.bot import texts
from app.bot.keyboards import meal_keyboard
from app.nutrilens import analyze_photo

router = Router(name="nutrilens")
log = logging.getLogger(__name__)


@router.message(F.photo)
async def handle_food_photo(message: Message) -> None:
    # Сообщение-заглушка, чтобы пользователь видел, что бот работает
    msg = await message.answer(texts.NUTRI_ANALYZING)
    try:
        photo = message.photo[-1]                          # максимальное качество
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)   # BytesIO
        result = await analyze_photo(file_bytes.read())
        if not result:
            await msg.edit_text(texts.NUTRI_NOT_RECOGNIZED)
            return
        text = texts.NUTRI_RESULT.format(
            name=escape(str(result["name"])), kcal=result["kcal"],
            protein=result["protein"], fat=result["fat"], carbs=result["carbs"],
        )
    except Exception:
        # Telegram не отдал файл, Vision API упал, в ответе нет нужных полей…
        log.exception("NutriLens: не удалось обработать фото")
        await msg.edit_text(texts.NUTRI_ERROR)
        return
    # Кнопка «Включить „После еды“» — её обрабатывает handlers/mode.py (спросит длительность)
    await msg.edit_text(text, reply_markup=meal_keyboard())

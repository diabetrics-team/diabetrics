"""
Обработка фото еды (NutriLens).

Владелец: Dev 4.
"""
from aiogram import F, Router
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from app.nutrilens import analyze_photo

router = Router(name="nutrilens")


@router.message(F.photo)
async def handle_food_photo(message: Message) -> None:
    # Отправляем сообщение-заглушку, чтобы пользователь видел, что бот работает
    msg = await message.answer("🔍 Анализирую фото еды...")

    try:
        # Берем фото в максимальном качестве
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)

        # Скачиваем файл в память (BytesIO)
        file_bytes = await message.bot.download_file(file.file_path)

        # Вызываем нашу функцию (пока заглушку)
        result = await analyze_photo(file_bytes.read())

        if result:
            text = (
                f"🍽 **{result['name']}**\n\n"
                f"🔥 Калории: {result['kcal']} ккал\n"
                f"🥩 Белки: {result['protein']} г\n"
                f"🥑 Жиры: {result['fat']} г\n"
                f"🍞 Углеводы: {result['carbs']} г\n\n"
                f"⚠️ *Это оценка нейросети, не медицинский совет.*"
            )
            # Кнопка для включения режима "После еды"
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Включить 'После еды'", callback_data="mode:after_meal:0")]
            ])
            await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        else:
            await msg.edit_text("❌ Не удалось распознать еду на фото. Попробуйте другое фото.")

    except Exception as e:
        # Ловим любые ошибки (например, если Telegram не отдал файл)
        await msg.edit_text(f"Ошибка при обработке фото: {e}")
"""
Запуск Telegram-бота: python -m app.bot.main

Владелец: Dev 3. Пока бот запускается отдельно от pipeline.
Собрать их в один процесс (polling + планировщик) — вместе с Dev 1, когда появится pipeline v2.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot import texts
from app.bot.config import load_config
from app.bot.handlers import get_routers


def create_bot(token: str) -> Bot:
    # Отдельная функция, чтобы pipeline (Dev 1) мог создать такой же Bot для notify().
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_routers(*get_routers())
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    bot = create_bot(config.bot_token)
    dp = create_dispatcher()

    # Меню команд (кнопка «Меню» слева от поля ввода в Telegram).
    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in texts.COMMANDS.items()]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

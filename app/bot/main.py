"""
Запуск только Telegram-бота, без pipeline: python -m app.bot.main
Удобно, когда правишь интерфейс. Обычный запуск — бот вместе с pipeline: python -m app.main (Dev 1).

Владелец: Dev 3. create_bot и create_dispatcher использует app/main.py.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app import db
from app.bot import notify, texts
from app.bot.config import load_config
from app.bot.handlers import get_routers


def create_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    # Вызывать один раз на процесс: роутеры — объекты модулей и подключаются
    # только к одному Dispatcher (второй вызов упадёт с RuntimeError).
    dp = Dispatcher()
    dp.include_routers(*get_routers())
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()
    db.configure(config.database_url)                # как в app/main.py
    bot = create_bot(config.bot_token)
    notify.setup(bot)   # теперь notify() отправляет в Telegram, а не в консоль
    dp = create_dispatcher()

    # Меню команд (кнопка «Меню» слева от поля ввода в Telegram).
    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in texts.COMMANDS.items()]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

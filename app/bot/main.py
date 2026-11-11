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

from app.bot import notify, texts
from app.bot.config import load_config


def create_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def create_dispatcher() -> Dispatcher:
    # Вызывать один раз на процесс: роутеры — объекты модулей и подключаются
    # только к одному Dispatcher (второй вызов упадёт с RuntimeError).
    #
    # Хендлеры импортируются здесь, а не в начале файла: к этому моменту load_config()
    # уже загрузил .env, а app/clock.py (его импортирует handlers/now.py) читает CLOCK
    # из окружения в момент импорта. Иначе CLOCK=nightscout из .env не сработал бы.
    from app.bot.handlers import get_routers

    dp = Dispatcher()
    dp.include_routers(*get_routers())
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config()          # загружает .env — до create_dispatcher()
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

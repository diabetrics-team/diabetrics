"""Точка входа"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from bot import texts
from bot.config import load_config
from bot.handlers import get_routers


def create_bot(token: str) -> Bot:
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

    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in texts.COMMANDS.items()]
    )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

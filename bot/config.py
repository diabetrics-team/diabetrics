"""Настройки бота из переменных окружения """

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str


def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env "
            "и впишите токен от @BotFather."
        )
    return Config(bot_token=token)

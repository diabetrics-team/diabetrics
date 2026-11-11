"""
Настройки бота из переменных окружения (.env).

Владелец: Dev 3.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    bot_token: str


def load_config() -> Config:
    # Ищет .env в папке app/bot/ и выше по дереву, то есть найдёт .env в корне репозитория.
    # Уже заданные переменные окружения (например, из docker compose) не перезаписывает.
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен от @BotFather."
        )
    return Config(bot_token=token)

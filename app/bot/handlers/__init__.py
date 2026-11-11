"""
Список роутеров бота.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router

from app.bot.handlers import common, mode, now, onboarding, start


def get_routers() -> list[Router]:
    # Порядок важен: aiogram отдаёт событие первому подходящему хендлеру.
    # 1. Команды — первыми: /now или /help работают даже посреди онбординга.
    # 2. onboarding — после команд: в нём хендлеры «любой текст на шаге адреса/токена».
    # 3. common — последним: «ловушка» для всего остального.
    # Новые роутеры (trusted, фото для NutriLens от Dev 4...) — перед onboarding.
    return [
        start.router,
        now.router,
        mode.router,
        onboarding.router,
        common.router,
    ]

"""
Список роутеров бота.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router

from app.bot.handlers import common, start


def get_routers() -> list[Router]:
    # Порядок важен: aiogram отдаёт сообщение первому подходящему хендлеру.
    # Новые роутеры (now, mode, trusted, фото для NutriLens от Dev 4...) добавляйте
    # перед common: в нём «ловушка» для всех остальных сообщений.
    return [
        start.router,
        common.router,
    ]

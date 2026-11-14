"""
Список роутеров бота.

Владелец: Dev 3.
"""
from __future__ import annotations

from aiogram import Router

from app.bot.handlers import (
    ack,
    common,
    graph,
    mode,
    now,
    nutrilens,
    onboarding,
    settings,
    start,
    trusted,
)


def get_routers() -> list[Router]:
    # Порядок важен: aiogram отдаёт событие первому подходящему хендлеру.
    # 1. trusted — первым: в нём /start invite_<код>, иначе его перехватит обычный /start.
    # 2. Команды и кнопки: /now или /help работают даже посреди онбординга.
    # 3. onboarding — после команд: в нём хендлеры «любой текст на шаге адреса/токена».
    # 4. common — последним: «ловушка» для всего остального и общий обработчик ошибок.
    # Новые роутеры — перед onboarding.
    return [
        trusted.router,
        start.router,
        now.router,
        mode.router,
        graph.router,
        settings.router,
        ack.router,
        nutrilens.router,   # фото еды (Dev 4); без этой строки фото получали «Не понял»
        onboarding.router,
        common.router,
    ]

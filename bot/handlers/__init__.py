from aiogram import Router

from bot.handlers import common, start


def get_routers() -> list[Router]:
    return [
        start.router,
        common.router,
    ]

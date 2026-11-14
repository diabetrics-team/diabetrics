"""
Точка входа: один процесс — Telegram-бот (aiogram, polling) + pipeline (APScheduler).

    python -m app.main

Владелец: Dev 1. Бота собирает Dev 3 (app/bot/main.py: create_bot, create_dispatcher),
здесь только склейка: .env → БД → бот → планировщик → polling.
"""
from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv


async def main() -> None:
    load_dotenv()      # до импорта хендлеров и до первого clock.now()
    from app.logs import setup_logging

    setup_logging(os.getenv("LOG_LEVEL", "INFO"))   # до всего остального: токены вырезаются из логов
    log = logging.getLogger("diabetrics")

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from aiogram.types import BotCommand

    from app import clock, db, health, pipeline
    from app.bot import notify, texts
    from app.bot.config import load_config
    from app.bot.main import create_bot, create_dispatcher

    config = load_config()
    db_url = config.database_url
    poll = float(os.getenv("POLL_SECONDS", "60"))
    db.configure(db_url)
    health_server = await health.start(int(os.getenv("HEALTH_PORT", "8080")), poll)

    bot = create_bot(config.bot_token)
    notify.setup(bot)
    dp = create_dispatcher()
    await bot.set_my_commands(
        [BotCommand(command=cmd, description=desc) for cmd, desc in texts.COMMANDS.items()]
    )

    scheduler = AsyncIOScheduler(timezone="UTC")
    # max_instances=1: если тик затянулся (медленный Nightscout), следующий не наложится
    scheduler.add_job(pipeline.tick, "interval", seconds=poll, max_instances=1, coalesce=True,
                      id="pipeline")
    scheduler.start()
    log.info("DiaBetrics запущен: CLOCK=%s, опрос раз в %g с, БД %s, пользователей %d, "
             "открытых критических: %d",
             "nightscout" if clock.uses_server_time() else "system", poll, db_url,
             len(db.active_users()), len(db.open_alerts()))
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        if health_server is not None:
            health_server.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

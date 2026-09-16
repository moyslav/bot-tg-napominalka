"""Application entry point.

Builds the python-telegram-bot ``Application``, registers the handlers, wires up
the APScheduler job that fires the daily reminders, and starts polling.

Run with::

    python main.py

The bot token and settings are read from ``.env`` (see :mod:`config`).
"""

from __future__ import annotations

import logging

import db
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from config import config
from handlers.commands import build_handlers
from services.scheduler import run_daily_reminders

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Hour (24h, app timezone) at which the daily reminder scan runs.
DAILY_HOUR = 9


def _build_scheduler(bot) -> BackgroundScheduler:
    """Create a background scheduler that runs the reminder scan once a day."""
    scheduler = BackgroundScheduler(timezone=config.scheduler_timezone)
    scheduler.add_job(
        run_daily_reminders,
        CronTrigger(hour=DAILY_HOUR, minute=0, timezone=config.scheduler_timezone),
        args=[bot],
        id="daily_reminders",
        replace_existing=True,
    )
    return scheduler


def main() -> None:
    from telegram.ext import ApplicationBuilder

    logger.info("Initializing database at %s", config.db_path)
    db.init_db()

    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .build()
    )

    build_handlers(app)

    scheduler = _build_scheduler(app.bot)
    scheduler.start()
    logger.info("Scheduler started: daily reminders at %s:00 (%s)",
                DAILY_HOUR, config.scheduler_timezone)

    logger.info("Starting bot polling...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()

"""Application entry point.

Builds the python-telegram-bot ``Application``, registers the handlers, wires up
the APScheduler job that fires the daily reminders, and starts polling.

Run with::

    python main.py

The bot token and settings are read from ``.env`` (see :mod:`config`).
"""

from __future__ import annotations

import logging
import time

import db
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from config import config
from handlers.commands import build_handlers
from services.network import build_http2x_request
from services.scheduler import run_daily_reminders
from utils.logging_setup import get_log_path, setup_logging

# Configure logging before anything else logs, so startup messages are captured.
setup_logging()

logger = logging.getLogger(__name__)

# Hour (24h, app timezone) at which the daily reminder scan runs.
DAILY_HOUR = 9
# Seconds to wait before restarting polling after a transient network error.
RETRY_DELAY_SECONDS = 30


def _build_application():
    """Build a fresh Application with handlers configured.

    ``python-telegram-bot``'s ``run_polling`` creates and later tears down its own
    asyncio event loop, so a single Application instance cannot be reused after a
    failure. The retry loop therefore builds a new Application (and scheduler) on
    every attempt.
    """
    from telegram.ext import ApplicationBuilder

    builder = (
        ApplicationBuilder()
        .token(config.bot_token)
        .request(build_http2x_request(config))
    )

    if config.telegram_api_base_url:
        builder.base_url(config.telegram_api_base_url)
        logger.warning("Telegram API base URL override: %s", config.telegram_api_base_url)
    if config.telegram_file_base_url:
        builder.base_file_url(config.telegram_file_base_url)
        logger.warning("Telegram file base URL override: %s", config.telegram_file_base_url)

    app = builder.build()
    build_handlers(app)
    return app


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


def _run_polling_with_retry() -> None:
    """Run polling, rebuilding the Application on each transient network error.

    If the host loses connectivity to Telegram (for example a VPN/tunnel that
    drops temporarily, which previously hard-crashed the bot), log it and retry
    after a short pause instead of exiting.
    """
    while True:
        logger.info("Starting bot polling...")
        app = None
        scheduler = None
        try:
            app = _build_application()
            scheduler = _build_scheduler(app.bot)
            scheduler.start()
            logger.info("Scheduler started: daily reminders at %s:00 (%s)",
                        DAILY_HOUR, config.scheduler_timezone)

            app.run_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=False,
            )
            # run_polling only returns when the bot is stopped by the operator.
            logger.info("Polling stopped cleanly.")
            return
        except Exception as exc:  # noqa: BLE001 - we retry broadly
            logger.warning("Polling interrupted: %s%s",
                           exc.__class__.__name__,
                           (f" ({exc})" if str(exc) else ""))
            logger.info("Restarting polling in %s s...", RETRY_DELAY_SECONDS)
        finally:
            # Stop the scheduler; run_polling already shut down its own event
            # loop, so the old Application is simply dropped and GC'd.
            if scheduler is not None:
                try:
                    scheduler.shutdown(wait=False)
                except Exception:  # noqa: BLE001
                    pass

        time.sleep(RETRY_DELAY_SECONDS)


def main() -> None:
    logger.info("Initializing database at %s", config.db_path)
    db.init_db()

    logger.info("Bot log file: %s", get_log_path())
    _run_polling_with_retry()


if __name__ == "__main__":
    main()

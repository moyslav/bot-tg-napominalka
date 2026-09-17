"""Logging configuration for the bot.

Writes logs to both the console and a rotating file so that a long-running
process keeps a persistent, disk-bounded history. The file is UTF-8 encoded so
Cyrillic text in messages is not mangled.

Log file location is controlled by ``LOG_DIR`` / ``LOG_FILE`` env vars and
defaults to ``logs/bot.log`` inside the project directory. Rotating handlers
keep ``LOG_BACKUP_COUNT`` (default 7) previous files at ``LOG_MAX_BYTES`` each.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Root directory of the project (config.py lives at the project root).
PROJECT_DIR = Path(__file__).resolve().parent.parent

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
_LOG_DIR = Path(os.getenv("LOG_DIR", PROJECT_DIR / "logs"))
_LOG_FILE = _LOG_DIR / os.getenv("LOG_FILE", "bot.log")
_LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", "2_000_000"))
_LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "7"))

_SHORT_FORMAT = "%(asctime)s | %(levelname)-7s | %(message)s"
_VERBOSE_FORMAT = (
    "%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s"
)


def _setup_file_handler() -> logging.Handler | None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_VERBOSE_FORMAT))
        return handler
    except OSError as exc:  # e.g. unwritable directory
        logging.getLogger(__name__).warning(
            "Could not set up file logging at %s: %s", _LOG_FILE, exc
        )
        return None


def setup_logging() -> None:
    """Configure the root logger with console + rotating-file handlers."""
    level = getattr(logging, _LOG_LEVEL, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_SHORT_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(console)

    file_handler = _setup_file_handler()
    if file_handler is not None:
        root.addHandler(file_handler)

    # Reduce noisy third-party logs.
    for noisy in (
        "httpx",
        "httpcore",
        "apscheduler.executors.default",
        "telegram.vendor.ptb_urllib3.urllib3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured: level=%s, file=%s", _LOG_LEVEL, _LOG_FILE
    )


def get_log_path() -> Path:
    """Absolute path of the active log file (useful for user-facing messages)."""
    return _LOG_FILE

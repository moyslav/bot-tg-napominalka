import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Environment variable {name} is missing. "
            "Copy .env.example to .env and fill it in."
        )
    return value


class Config:
    """Central application configuration loaded from environment / .env file."""

    def __init__(self) -> None:
        self.bot_token: str = _required("BOT_TOKEN")
        self.db_path: Path = BASE_DIR / os.getenv("DB_PATH", "data/app.db")
        self.bot_username: str = os.getenv("BOT_USERNAME", "")
        self.scheduler_timezone: str = os.getenv("SCHEDULER_TIMEZONE", "Europe/Moscow")


config = Config()

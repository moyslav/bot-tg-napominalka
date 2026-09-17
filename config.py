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

        # Optional network overrides.
        # PROXY_URL points the bot's HTTP(S) traffic through a local/remote proxy,
        # which is how Telegram becomes reachable when the host itself cannot reach
        # api.telegram.org directly (e.g. it goes through tgwsproxy / a VPN).
        self.proxy_url: str | None = _clean_env("PROXY_URL")
        # Optional: override the Telegram API hostname (defaults to api.telegram.org).
        # Useful when a proxy/VPN cannot resolve Telegram DNS and you have a working
        # IP or a mirror you want to pin to.
        self.telegram_api_base_url: str | None = _clean_env("TELEGRAM_API_BASE_URL")
        self.telegram_file_base_url: str | None = _clean_env("TELEGRAM_FILE_BASE_URL")


def _clean_env(name: str) -> str | None:
    """Return a stripped env value, or ``None`` when empty."""
    value = os.getenv(name)
    return value.strip() if value else None


config = Config()

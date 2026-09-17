"""HTTPX request construction for the bot.

Ties together the network-related settings from :mod:`config` and the optional
SOCKS/proxy support so that the bot can reach Telegram even when the host cannot
reach ``api.telegram.org`` directly.

* ``PROXY_URL`` - e.g. ``socks5://127.0.0.1:1080`` for a local tgwsproxy/VPN
  tunnel, or ``http://host:port`` for an HTTP proxy. python-telegram-bot turns
  this into an ``httpx`` transport.
* ``TELEGRAM_API_BASE_URL`` / ``TELEGRAM_FILE_BASE_URL`` - optional overrides of
  the Telegram hostname (handy when DNS resolves Telegram to a blocked IP and a
  known-good IP / mirror must be pinned).

``build_request()`` returns an ``HTTPXRequest`` suitable for
``ApplicationBuilder.request()``.
"""

from __future__ import annotations

import logging
from typing import Any

from telegram.request import HTTPXRequest

from config import Config

logger = logging.getLogger(__name__)


def build_httpx_kwargs(config: Config) -> dict[str, Any]:
    """Assemble the kwargs handed to ``HTTPXRequest`` (and so to ``httpx``)."""
    kwargs: dict[str, Any] = {}

    if config.proxy_url:
        # Passing proxy as httpx_kwargs lets us combine it with the default
        # HTTPXRequest config; python-telegram-bot supports 'proxy' inside
        # httpx_kwargs as well.
        kwargs["proxy"] = config.proxy_url
        logger.warning("Using proxy: %s", _sanitize(config.proxy_url))

    return kwargs


def build_http2x_request(config: Config) -> HTTPXRequest:
    """Build and return the ``HTTPXRequest`` used by the Application."""
    httpx_kwargs = build_httpx_kwargs(config)
    return HTTPXRequest(
        httpx_kwargs=httpx_kwargs,
        # A bit more forgiving for tunneled connections.
        connect_timeout=15.0,
        read_timeout=15.0,
        write_timeout=15.0,
        pool_timeout=5.0,
    )


def _sanitize(proxy: str) -> str:
    """Redact credentials in a proxy URL for logging (keep the scheme/host)."""
    try:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(proxy)
        if parts.username or parts.password:
            host = parts.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            cleaned = urlunsplit((parts.scheme, host, parts.path or "/", "", ""))
            return cleaned
    except Exception:  # noqa: BLE001 - never fail logging
        pass
    return proxy

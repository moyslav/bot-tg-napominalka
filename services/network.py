"""HTTPX request construction and Telegram reachability for the bot.

Ties together the network settings from :mod:`config`, a SOCKS/HTTP proxy and a
built-in list of known Telegram API IPs so the bot works even when the host's DNS
resolves Telegram to a dead (blocked) address.

Background
----------
In some networks (notably Russia) Telegram is blocked on the network level:

* the ISP's DNS may hand back an unreachable Telegram IP (e.g. ``149.154.166.110``),
* or a local tunnel/VPN (such as tgwsproxy on ``127.0.0.1:1080``) exists but only
  relays *some* destinations, and the Telegram handshake can fail intermittently.

To cope, this module:

* keeps a list of known-good ``api.telegram.org`` IPs (``TELEGRAM_API_IPS``);
* at build time, probes them (directly or through the configured proxy) with
  ``getMe``-style request and picks the first one that answers;
* pins ``socket.getaddrinfo`` so ``api.telegram.org`` resolves to that IP, which
  makes the httpx layer (and python-telegram-bot) connect to the live endpoint
  regardless of what the system DNS says.

When no IP is reachable at the moment (tunnel down at night), the probe reports
failure and the caller (the polling retry loop) will build a fresh request later,
so the bot re-connects automatically once a route comes back.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any

import httpx
from telegram.request import HTTPXRequest

from config import Config

logger = logging.getLogger(__name__)

# A reasonable set of api.telegram.org / Telegram datacenter addresses.
# These are well-known, publicly listed Telegram API endpoints. The bot picks the
# first one that answers rather than trusting the (possibly poisoned) system DNS.
DEFAULT_TELEGRAM_IPS = [
    "149.154.167.220",
    "149.154.166.110",
    "149.154.167.51",
    "149.154.175.50",
    "149.154.175.53",
    "149.154.166.138",
    "91.108.56.130",
    "91.108.4.200",
]

_REACHABILITY_TIMEOUT = 6.0
_REACHABILITY_TRIES = 5


def telegram_api_ips(config: Config) -> list[str]:
    """Return the candidate IPs to try (from env ``TELEGRAM_API_IPS`` or defaults)."""
    raw = getattr(config, "telegram_api_ips", None)
    if not raw:
        return list(DEFAULT_TELEGRAM_IPS)
    return [ip.strip() for ip in raw.split(",") if ip.strip()]


def _is_reachable(proxy_url: str | None, api_ip: str, token: str) -> bool:
    """Try to reach ``api.telegram.org`` (as ``api_ip``) through the given proxy.

    The probe pins DNS to ``api_ip`` and issues a real ``getMe`` call, so a True
    result means this exact IP is live on the current route. A ``200``/``401``
    response is treated as reachable; any HTTP-level answer (<500) counts.
    """
    import asyncio

    # Pin this probe's resolution to the candidate IP, regardless of system DNS.
    original = socket.getaddrinfo

    def _probe_resolve(host, *args, **kwargs):
        if isinstance(host, str) and host == "api.telegram.org":
            return original(api_ip, *args, **kwargs)
        return original(host, *args, **kwargs)

    socket.getaddrinfo = _probe_resolve

    async def _check() -> bool:
        transport: httpx.AsyncBaseTransport | None = None
        try:
            if proxy_url:
                scheme = (proxy_url or "").split("://", 1)[0].lower()
                if scheme.startswith("socks"):
                    from httpx_socks import AsyncProxyTransport

                    transport = AsyncProxyTransport.from_url(proxy_url)
                else:
                    transport = httpx.AsyncHTTPTransport(proxy=proxy_url)
            url = f"https://api.telegram.org/bot{token}/getMe" if token else "https://api.telegram.org"
            kwargs: dict[str, Any] = {"timeout": _REACHABILITY_TIMEOUT}
            if transport is not None:
                kwargs["transport"] = transport
            async with httpx.AsyncClient(**kwargs) as client:
                # DPI desync is probabilistic, so retry a few times: a working IP
                # will pass on at least one of several attempts.
                for _ in range(_REACHABILITY_TRIES):
                    try:
                        resp = await client.get(url)
                        # 200 = reachable; 401 = wrong token but endpoint alive.
                        if resp.status_code < 500:
                            return True
                    except httpx.HTTPError:
                        pass
                return False
        finally:
            socket.getaddrinfo = original

    try:
        return asyncio.run(_check())
    except Exception as exc:  # noqa: BLE001
        logger.debug("reachability check for %s failed: %s", api_ip, exc)
        return False


def find_working_api_ip(config: Config) -> str | None:
    """Probe candidate Telegram IPs and return the first reachable one.

    Tries through the configured proxy when present, otherwise directly. Returns
    ``None`` when none answers (caller may retry later).
    """
    token = config.bot_token
    for api_ip in telegram_api_ips(config):
        if _is_reachable(config.proxy_url, api_ip, token):
            logger.info("Telegram API reachable via %s (proxy=%s)",
                        api_ip, bool(config.proxy_url))
            return api_ip
        logger.debug("Telegram IP %s not reachable, trying next...", api_ip)
    logger.warning("No reachable Telegram API IP found (is the proxy/VPN up?).")
    return None


def pin_telegram_dns(api_ip: str) -> None:
    """Make ``socket.getaddrinfo`` resolve `api.telegram.org` to ``api_ip``.

    This redirects all httpx/telegram traffic for the API host to the chosen
    address, working around poisoned system DNS.
    """
    original = socket.getaddrinfo
    if getattr(original, "_telegram_pinned", False):
        return  # already pinned once

    def _patched(host, *args, **kwargs):
        if isinstance(host, str) and host in ("api.telegram.org",):
            # Replace the hostname with the pinned IP for resolution.
            return original(api_ip, *args, **kwargs)
        return original(host, *args, **kwargs)

    _patched._telegram_pinned = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _patched  # type: ignore[assignment]
    logger.info("Pinned api.telegram.org -> %s", api_ip)


def build_httpx_kwargs(config: Config) -> dict[str, Any]:
    """Assemble the kwargs handed to ``HTTPXRequest`` (and so to ``httpx``)."""
    kwargs: dict[str, Any] = {}

    if config.proxy_url:
        kwargs["proxy"] = config.proxy_url
        logger.warning("Using proxy: %s", _sanitize(config.proxy_url))

    return kwargs


def build_http2x_request(config: Config) -> HTTPXRequest:
    """Build and return the ``HTTPXRequest`` used by the Application.

    When a custom ``TELEGRAM_API_BASE_URL`` is configured (e.g. a Bot API relay
    on Vercel), all traffic goes there directly, so the raw ``api.telegram.org``
    IP discovery/pinning is skipped. Timeouts follow the relay deployment
    (read/write can be slow on serverless warm-ups).
    """
    if not config.telegram_api_base_url:
        try:
            working = find_working_api_ip(config)
            if working:
                pin_telegram_dns(working)
        except Exception as exc:  # noqa: BLE001 - never block startup on discovery
            logger.warning("Telegram IP discovery skipped: %s", exc)

    httpx_kwargs = build_httpx_kwargs(config)
    return HTTPXRequest(
        httpx_kwargs=httpx_kwargs,
        connect_timeout=60.0,
        read_timeout=70.0,
        write_timeout=70.0,
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


def _is_valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False

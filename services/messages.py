"""Message builders for the various reminder / notice texts the bot can send.

Kept separate from the database layer so handlers and the scheduler can reuse the
same wording and formatting without duplicating it.

All builders take the *reference* date (usually "today" in the application
timezone) and compute the offset to each event internally, so the phrasing
("через 3 дня", "завтра", "сегодня", "вчера") is always correct regardless of
how far in the past or future an event is.

For friends we deliberately avoid raw ``@mention`` where it might leak a username
into a chat the user did not ask about; labels are shown as plain text instead.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from utils.formatting import format_date_short, humanize_diff, join_lines


def _days_until(event: sqlite3.Row, on_date: date) -> int:
    """Signed number of days from ``on_date`` to the event's date."""
    return (date.fromisoformat(event["event_date"]) - on_date).days


def self_reminder(event: sqlite3.Row, on_date: date) -> str:
    """A private reminder about the user's own upcoming date.

    Examples::

        ⏰ Напоминание!
        «др у мамы» — 17 сентября (через 3 дн.).

        ⏰ Напоминание!
        «собеседование» — сегодня.
    """
    days = _days_until(event, on_date)
    when = humanize_diff(days)
    return (
        f"⏰ Напоминание!\n"
        f"«{event['title']}» — {format_date_short(date.fromisoformat(event['event_date']))} ({when})."
    )


def friend_event_notice(event: sqlite3.Row, owner_label: str, on_date: date) -> str:
    """Notice sent to a friend about an event that belongs to their friend.

    Example: ``📣 У друга @katya скоро «др у мамы» · через 3 дн.``
    """
    days = _days_until(event, on_date)
    if days <= 0:
        hint = "сегодня"
    elif days == 1:
        hint = "завтра"
    else:
        hint = f"через {days} дн."
    return f"📣 У друга {owner_label} скоро «{event['title']}» · {hint}"


def friend_events_digest(friends_items: list[sqlite3.Row], on_date: date) -> str:
    """Combined digest of several of a friend's events in a single notice."""
    lines = ["👫 Ближайшие даты у друзей:"]
    for event in friends_items:
        owner = _owner_label(event["user_id"])
        lines.append(
            f"• {owner} — «{event['title']}»"
            f" · {humanize_diff(_days_until(event, on_date))}"
        )
    return join_lines(lines)


def friend_wishlist_blurb(owner_label: str, wish_texts: list[str]) -> str:
    """Short wishlist preview appended to a friend notice.

    Example::

        🎁 Вишлист @alice:
           Наушники
           Книга
           … и ещё 1 п.
    """
    if not wish_texts:
        return ""
    lines = [f"🎁 Вишлист {owner_label}:"]
    lines += wish_texts
    return "\n".join(lines)


def _owner_label(user_id: int) -> str:
    # Imported lazily to avoid a circular import between repos and this module.
    import repos

    return repos.user_display(user_id)

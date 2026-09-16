"""Daily (recurring) reminder logic.

The scheduler runs once a day (configurable hour, default 09:00 in the app timezone)
and walks every event that falls today or in the reminder horizon. For each event it:

* sends the owner a private reminder if this offset hasn't been sent yet,
* for every accepted friend of the owner, sends a gentle notice about the owner's
  upcoming event (batching the owner's events together so a friend does not get one
  message per event).

Deduplication is handled by the ``event_notifications`` table: each combination of
(event, kind, offset-days, recipient) is recorded once, so the bot never nags the
same person repeatedly about the same event, but will remind again for a *new*
offset (e.g. a fresh reminder at 3 days, then again at 1 day before).
"""

from __future__ import annotations

import collections
import logging
from datetime import date, timedelta

from telegram import Bot

import repos
from services import messages
from utils.formatting import today

logger = logging.getLogger(__name__)

# How many days before an event the reminders/notices start firing.
REMIND_DAYS = 3
# How far "after" today we still consider a date for the batch scan.
LOOKAHEAD_DAYS = 30


def run_daily_reminders(bot: Bot) -> int:
    """Scan and notify for events that are today, ``REMIND_DAYS`` ahead, or within
    the lookahead window. Returns the number of notification messages sent.

    This is intentionally safe to call any number of times a day: the
    deduplication table makes repeated runs idempotent.
    """
    today_date = today()

    # Events the owner might care about: today up to REMIND_DAYS ahead.
    window_end = today_date + timedelta(days=REMIND_DAYS)
    events = repos.db.rows(
        """
        SELECT * FROM events
        WHERE event_date >= ? AND event_date <= ?
        ORDER BY user_id, event_date
        """,
        (today_date.isoformat(), window_end.isoformat()),
    )

    sent = 0
    by_owner: dict[int, list] = collections.defaultdict(list)
    for event in events:
        by_owner[event["user_id"]].append(event)

    for owner_id, owner_events in by_owner.items():
        owner_label = _safe_label(owner_id)
        wish_blurb = messages.friend_wishlist_blurb(owner_label, repos.wishlist_lines(owner_id))

        # Private reminders.
        for event in owner_events:
            days_out = _days_out(event, today_date)
            if days_out is None:
                continue
            if repos.notification_sent(event["event_id"], "self", days_out, owner_id):
                continue
            try:
                bot.send_message(
                    chat_id=owner_id,
                    text=messages.self_reminder(event, today_date),
                )
                repos.record_notification(event["event_id"], "self", days_out, owner_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - notify and keep going
                logger.warning("self reminder to %s failed: %s", owner_id, exc)

        # Friend notices (only for events the owner actually has upcoming).
        for friend_id in repos.get_friends_ids(owner_id):
            pending: list = []
            for event in owner_events:
                days_out = _days_out(event, today_date)
                if days_out is None:
                    continue
                if repos.notification_sent(event["event_id"], "friend", days_out, friend_id):
                    continue
                pending.append(event)
            if not pending:
                continue
            try:
                owner_label = _safe_label(owner_id)
                if len(pending) == 1:
                    text = messages.friend_event_notice(pending[0], owner_label, today_date)
                else:
                    text = messages.friend_events_digest(pending, today_date)
                text += "\n" + wish_blurb if wish_blurb else ""
                bot.send_message(chat_id=friend_id, text=text)
                for event in pending:
                    repos.record_notification(event["event_id"], "friend",
                                              _days_out(event, today_date) or 0, friend_id)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("friend notice to %s failed: %s", friend_id, exc)

    logger.info("daily reminders: %s message(s) sent", sent)
    return sent


def _days_out(event, today_date: date) -> int | None:
    event_date = date.fromisoformat(event["event_date"])
    if event_date < today_date:
        return None
    days = (event_date - today_date).days
    if 0 <= days <= REMIND_DAYS:
        return days
    return None


def _safe_label(user_id: int) -> str:
    return repos.user_display(user_id)

"""Manual smoke test for the DB layer and the daily-reminder scheduler.

Not part of the application runtime. Run directly::

    python test_smoke.py

It uses a temporary SQLite file, a fake bot object that records sent messages,
and verifies the reminder/notice/text logic end to end without talking to
Telegram. On success it prints summary lines and ``SMOKE_OK``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

# Force UTF-8 console output so emoji/symbols don't break the report on Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "smoke_remind.db")

import db  # noqa: E402
import repos  # noqa: E402
from services.scheduler import run_daily_reminders  # noqa: E402
from utils.formatting import today  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    def send_message(self, chat_id, text: str, **kwargs):
        self.sent.append((chat_id, text))
        return None


def reset():
    try:
        os.remove(os.environ["DB_PATH"])
    except OSError:
        pass
    db.init_db()


def main() -> None:
    reset()

    repos.upsert_user(1, "alice", "Alice A.")
    repos.upsert_user(2, "bob", "Bob B.")

    in_two = (today() + timedelta(days=2)).isoformat()
    repos.add_event(1, "др у мамы", in_two)
    repos.add_event(1, "сессия", in_two)
    repos.add_wishlist_item(1, "Наушники")

    ok, msg = repos.send_friend_request(requester_id=1, addressee_id=2)
    print("friend request:", ok, msg)
    assert ok
    assert repos.respond_to_request(2, 1, True)
    assert repos.get_friends_ids(1) == [2], repos.get_friends_ids(1)

    print("wish preview:", repos.wishlist_lines(1))
    assert repos.wishlist_lines(1) == ["   Наушники"]

    bot = FakeBot()
    first = run_daily_reminders(bot)
    print("first run sent:", first)
    assert first == 3, f"expected 3 (2 self + 1 friend batching), got {first}"

    for chat_id, text in bot.sent:
        label = "SELF" if chat_id == 1 else "FRIEND"
        print(f"  [{label}] -> {chat_id}: {text!r}")

    # Second run must be a no-op thanks to deduplication.
    second = run_daily_reminders(bot)
    print("second run sent:", second)
    assert second == 0, f"expected 0 deduplicated sends, got {second}"

    print("\nSMOKE_OK")


if __name__ == "__main__":
    main()

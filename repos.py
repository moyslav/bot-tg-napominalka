"""Data-access helpers that wrap the raw SQL from :mod:`db` with the domain
concepts the bot works with: users, events (dates to remember), friendships and
wishlist items.

All query strings that interpolate user input use placeholders (``?``) rather
than string formatting. Every piece of user-supplied text is passed as a query
parameter, never concatenated into SQL, so the bot is safe from SQL injection.
"""

from __future__ import annotations

import sqlite3

import db

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def upsert_user(user_id: int, username: str | None, full_name: str | None) -> None:
    """Insert a user row, or refresh their username/full name if they already exist."""
    db.execute(
        """
        INSERT INTO users (user_id, username, full_name) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username  = COALESCE(excluded.username,  users.username),
            full_name = COALESCE(excluded.full_name, users.full_name)
        """,
        (user_id, username, full_name),
    )


def get_user(user_id: int) -> sqlite3.Row | None:
    return db.fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))


def get_user_by_username(username: str) -> sqlite3.Row | None:
    """Find a user by their Telegram @username (without the leading '@')."""
    cleaned = username.lstrip("@").strip()
    return db.fetchone("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (cleaned,))


def user_display(user_id: int) -> str:
    """Human-readable name for a user: @username (or full name / 'Пользователь')."""
    row = get_user(user_id)
    if row is None:
        return f"user{user_id}"
    if row["username"]:
        return "@" + row["username"]
    if row["full_name"]:
        return row["full_name"]
    return f"user{user_id}"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def add_event(user_id: int, title: str, event_date: str) -> int:
    """Store a date to remember (e.g. birthday, doctor's appointment). Returns its id."""
    return db.insert(
        "INSERT INTO events (user_id, title, event_date) VALUES (?, ?, ?)",
        (user_id, title.strip(), event_date),
    )


def list_events(user_id: int, *, upcoming_only: bool = False, today: str = "") -> list[sqlite3.Row]:
    """Return the user's events, newest first. Upcoming ones can be filtered on."""
    sql = "SELECT * FROM events WHERE user_id = ?"
    params: list = [user_id]
    if upcoming_only:
        sql += " AND event_date >= ?"
        params.append(today)
    sql += " ORDER BY event_date ASC"
    return db.rows(sql, tuple(params))


def get_friend_events(friends_ids: list[int]) -> list[sqlite3.Row]:
    """Return all events owned by the given list of users (used to show friends' dates)."""
    if not friends_ids:
        return []
    marks = ",".join("?" * len(friends_ids))
    return db.rows(
        f"SELECT * FROM events WHERE user_id IN ({marks}) ORDER BY event_date ASC",
        tuple(friends_ids),
    )


def get_event(event_id: int) -> sqlite3.Row | None:
    return db.fetchone("SELECT * FROM events WHERE event_id = ?", (event_id,))


def delete_event(event_id: int, owner_id: int) -> bool:
    """Delete an event only if the caller owns it. Returns True on success.

    Parameters are passed as bindings so a malicious value cannot alter the query.
    """
    return db.execute_affected(
        "DELETE FROM events WHERE event_id = ? AND user_id = ?",
        (event_id, owner_id),
    ) > 0


# ---------------------------------------------------------------------------
# Friendships
# ---------------------------------------------------------------------------


def send_friend_request(requester_id: int, addressee_id: int) -> tuple[bool, str]:
    """Create a friend request. Returns ``(created, message)`` so the caller can
    give the requester useful feedback instead of a generic error.

    A request can be sent again only after it was declined; if there is an
    accepted or still-pending relationship, no new row is inserted.
    """
    if requester_id == addressee_id:
        return False, "Нельзя добавить самого себя в друзья."

    existing = db.fetchone(
        "SELECT status, requester_id FROM friendships "
        "WHERE (requester_id = ? AND addressee_id = ?) "
        "   OR (requester_id = ? AND addressee_id = ?)",
        (requester_id, addressee_id, addressee_id, requester_id),
    )
    if existing:
        if existing["status"] == "accepted":
            return False, "Этот пользователь уже у вас в друзьях."
        if existing["status"] == "pending":
            if existing["requester_id"] == requester_id:
                return False, "Заявка уже была отправлена, дождитесь ответа."
            return False, "Заявка уже отправлена вам, откройте раздел заявок."
        # declined -> allow re-requesting with a fresh row
        db.execute(
            "DELETE FROM friendships "
            "WHERE (requester_id = ? AND addressee_id = ?) "
            "   OR (requester_id = ? AND addressee_id = ?)",
            (requester_id, addressee_id, addressee_id, requester_id),
        )

    db.execute(
        "INSERT INTO friendships (requester_id, addressee_id) VALUES (?, ?)",
        (requester_id, addressee_id),
    )
    return True, "Заявка отправлена."


def get_friend_requests(addressee_id: int) -> list[sqlite3.Row]:
    """Incoming pending requests for a user (join requesters' contact info)."""
    return db.rows(
        """
        SELECT f.requester_id, u.username, u.full_name, f.created_at
        FROM friendships f
        JOIN users u ON u.user_id = f.requester_id
        WHERE f.addressee_id = ? AND f.status = 'pending'
        ORDER BY f.created_at DESC
        """,
        (addressee_id,),
    )


def respond_to_request(addressee_id: int, requester_id: int, accept: bool) -> bool:
    """Accept or decline a pending request. Returns True if a change was made."""
    status = "accepted" if accept else "declined"
    return db.execute_affected(
        "UPDATE friendships SET status = ?, responded_at = datetime('now') "
        "WHERE requester_id = ? AND addressee_id = ? AND status = 'pending'",
        (status, requester_id, addressee_id),
    ) > 0


def get_friends_ids(user_id: int) -> list[int]:
    """Telegram ids of all users with whom ``user_id`` has an accepted friendship."""
    rows = db.rows(
        """
        SELECT requester_id, addressee_id FROM friendships WHERE status = 'accepted'
          AND (requester_id = ? OR addressee_id = ?)
        """,
        (user_id, user_id),
    )
    ids: set[int] = set()
    for row in rows:
        other = row["addressee_id"] if row["requester_id"] == user_id else row["requester_id"]
        ids.add(other)
    return sorted(ids)


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------


def add_wishlist_item(user_id: int, text: str) -> int:
    """Append an item to a user's wishlist (things to gift or buy for them)."""
    return db.insert(
        "INSERT INTO wishlist_items (user_id, text) VALUES (?, ?)",
        (user_id, text.strip()),
    )


def list_wishlist(user_id: int) -> list[sqlite3.Row]:
    return db.rows(
        "SELECT * FROM wishlist_items WHERE user_id = ? ORDER BY item_id ASC",
        (user_id,),
    )


def wishlist_lines(user_id: int, limit: int = 3) -> list[str]:
    """A short, human-readable preview of a user's wishlist (for friend notices)."""
    items = list_wishlist(user_id)
    truncated = items if len(items) <= limit else items[:limit]
    lines = [f"   {i['text']}" for i in truncated]
    if len(items) > limit:
        lines.append(f"   … и ещё {len(items) - limit} п. (см. /wishlist_friend)")
    return lines


def delete_wishlist_item(item_id: int, user_id: int) -> bool:
    return db.execute_affected(
        "DELETE FROM wishlist_items WHERE item_id = ? AND user_id = ?",
        (item_id, user_id),
    ) > 0


# ---------------------------------------------------------------------------
# Notifications (daily reminder bookkeeping)
# ---------------------------------------------------------------------------


def notification_sent(event_id: int, kind: str, days_out: int, sent_to: int) -> bool:
    """Has this reminder already been delivered for the given event/offset/user?"""
    row = db.fetchone(
        "SELECT 1 FROM event_notifications "
        "WHERE event_id = ? AND kind = ? AND days_out = ? AND sent_to_user = ?",
        (event_id, kind, days_out, sent_to),
    )
    return row is not None


def record_notification(event_id: int, kind: str, days_out: int, sent_to: int) -> None:
    db.execute(
        "INSERT OR IGNORE INTO event_notifications (event_id, kind, days_out, sent_to_user) "
        "VALUES (?, ?, ?, ?)",
        (event_id, kind, days_out, sent_to),
    )

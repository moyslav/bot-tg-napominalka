"""SQLite database connection and schema management.

The bot uses a single SQLite database file whose location is set through the
``DB_PATH`` environment variable (defaults to ``data/app.db`` inside the
project directory).

Design notes
------------
* A new connection is opened per operation via :func:`get_connection`. This is a
  deliberate trade-off in favour of robustness: SQLite connections are not thread-
  safe, and the bot mixes asyncio handlers with APScheduler background jobs, so it
  is safer to hand out short-lived connections than to share a single one.
* ``PRAGMA foreign_keys = ON`` is enforced so row-level cleanup (e.g. deleting a
  user) cascades to their events, friendships and wishlist items.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Iterator

from config import config

_SCHEMA: str = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,          -- Telegram user id
    username    TEXT,                          -- Telegram @username (without @)
    full_name   TEXT,                          -- first (and last) name as known to Telegram
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    title       TEXT NOT NULL,                -- e.g. 'др у мамы'
    event_date  TEXT NOT NULL,                -- stored as YYYY-MM-DD (date only)
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- A friend request. requester is the one who asks, addressee is the receiver.
CREATE TABLE IF NOT EXISTS friendships (
    requester_id   INTEGER NOT NULL,
    addressee_id   INTEGER NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','declined')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    responded_at   TEXT,
    PRIMARY KEY (requester_id, addressee_id),
    FOREIGN KEY (requester_id) REFERENCES users(user_id) ON DELETE CASCADE,
    FOREIGN KEY (addressee_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wishlist_items (
    item_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- Tracks which reminder messages have already been sent, so a user is not spammed
-- for the same event every single day.
--   event_id -> the event being reminded about
--   kind     -> 'self'  : a private reminder for the owner
--               'friend': a forwarded notice about a friend's event
--   days_out -> the offset (days until the event) at which this notice was sent
CREATE TABLE IF NOT EXISTS event_notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('self','friend')),
    days_out        INTEGER NOT NULL,
    sent_to_user    INTEGER NOT NULL,
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (event_id, kind, days_out, sent_to_user),
    FOREIGN KEY (event_id) REFERENCES events(event_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_events_user    ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_date    ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_friends_status ON friendships(status);
CREATE INDEX IF NOT EXISTS idx_wish_user      ON wishlist_items(user_id);
"""


def connect() -> sqlite3.Connection:
    """Open a new connection to the application database."""
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create the database file and all tables/indexes if they don't exist yet."""
    with closing(connect()) as conn, conn:
        conn.executescript(_SCHEMA)


def rows(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Run a SELECT query and return all matching rows."""
    with closing(connect()) as conn:
        return conn.execute(sql, params).fetchall()


def fetchone(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    """Run a SELECT query and return a single row (or ``None``)."""
    with closing(connect()) as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> None:
    """Run a write query inside a transaction."""
    with closing(connect()) as conn, conn:
        conn.execute(sql, params)


def insert(sql: str, params: tuple = ()) -> int:
    """Run an INSERT and return the auto-generated row id (``lastrowid``)."""
    with closing(connect()) as conn, conn:
        cur = conn.execute(sql, params)
        return int(cur.lastrowid)


def execute_affected(sql: str, params: tuple = ()) -> int:
    """Run a write query and return the number of affected rows."""
    with closing(connect()) as conn, conn:
        cur = conn.execute(sql, params)
        return int(cur.rowcount)


def execute_many(queries: list[tuple[str, tuple]]) -> None:
    """Run multiple write queries inside a single transaction."""
    with closing(connect()) as conn, conn:
        for sql, params in queries:
            conn.execute(sql, params)


def iter_rows(sql: str, params: tuple = ()) -> Iterator[sqlite3.Row]:
    """Yield rows from a SELECT one at a time (lazy cursor)."""
    with closing(connect()) as conn:
        for row in conn.execute(sql, params):
            yield row

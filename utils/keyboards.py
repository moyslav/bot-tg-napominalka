"""Shared UI helpers: keyboard builders and the conversation states used by the
``ConversationHandler`` for multi-step commands (adding an event, adding a
wishlist item, sending a friend request)."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Conversation states ------------------------------------------------------------------
(
    STATE_ADD_EVENT_TITLE,
    STATE_ADD_EVENT_DATE,
    STATE_ADD_WISH_TEXT,
    STATE_ADD_FRIEND_USERNAME,
) = range(4)


def main_menu() -> InlineKeyboardMarkup:
    """The main inline keyboard shown after the welcome / help."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Мои даты", callback_data="menu:events"),
                InlineKeyboardButton("➕ Добавить дату", callback_data="menu:add_event"),
            ],
            [
                InlineKeyboardButton("👥 Друзья", callback_data="menu:friends"),
                InlineKeyboardButton("🎁 Вишлист", callback_data="menu:wishlist"),
            ],
        ]
    )


def events_actions_kb(event_id: int) -> InlineKeyboardMarkup:
    """Per-event action buttons (used after picking an event from the list)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🗑 Удалить", callback_data=f"event:del:{event_id}"),
                InlineKeyboardButton("🔙 Назад", callback_data="menu:events"),
            ]
        ]
    )

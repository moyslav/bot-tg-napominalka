"""Telegram command, callback and conversation handlers.

Registered with the `Application` built in :mod:`main`. Handles:

- ``/start`` and ``/help`` (the main menu),
- ``/myevents``, ``/addevent``, ``/delevent``,
- ``/friends``, ``/addfriend`` and the accept/decline commands for incoming
  friend requests,
- ``/wishlist``, ``/addwish``, ``/delwish``,
- inline action buttons (callback queries).

Multi-step (stateful) flows use a ``ConversationHandler`` with the states
defined in :mod:`utils.keyboards`.
"""

from __future__ import annotations

import logging
import re

from telegram import CallbackQuery, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import repos
from utils.formatting import format_date, format_date_short, humanize_diff, parse_date, today
from utils.keyboards import (
    STATE_ADD_EVENT_DATE,
    STATE_ADD_EVENT_TITLE,
    STATE_ADD_FRIEND_USERNAME,
    STATE_ADD_WISH_TEXT,
    main_menu,
)

logger = logging.getLogger(__name__)

REMIND_DAYS_TEXT = "3 дня"

_MONTH_WORDS = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _ensure_user(update: Update) -> None:
    """Register (or update) the user who triggered the update in the DB."""
    user = update.effective_user
    if user is None:
        return
    full = user.full_name or None
    repos.upsert_user(user.id, user.username, full)


def _events_text(user_id: int) -> str:
    upcoming = repos.list_events(user_id, upcoming_only=True, today=today().isoformat())
    all_events = repos.list_events(user_id)
    lines: list[str] = []
    if upcoming:
        lines.append("📅 Ближайшие даты:")
        for e in upcoming:
            d = e["event_date"]
            lines.append(f"• {e['title']} — {format_date_short(d)}"
                         f" ({humanize_diff((today() - d).days)})")
    else:
        lines.append("Ближайших дат пока нет.")
    if all_events:
        lines.append("\nВсе даты:")
        for e in all_events:
            lines.append(f"  {e['event_id']}. {e['title']} — {format_date(e['event_date'])}")
        lines.append("\nДля удаления: /delevent <номер>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /start and /help
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ensure_user(update)
    text = (
        "👋 Привет! Я бот-напоминалка. Я запоминаю твои важные даты и напоминаю о них. "
        "Также можно добавляться в друзья по @username и вести общий вишлист.\n\n"
        "Что умею:\n"
        "• /addevent <название> <дата> — запомнить дату (например: /addevent др у мамы 25.03)\n"
        "• /myevents — мои даты\n"
        "• /delevent <номер> — удалить дату\n"
        "• /addfriend @username — отправить заявку в друзья\n"
        "• /friends — друзья и входящие заявки\n"
        "• /accept <id> · /decline <id> — ответить на заявку\n"
        "• /addwish <текст> — добавить в вишлист\n"
        "• /wishlist — мой вишлист\n"
        "• /delwish <номер> — удалить из вишлиста\n"
        "• /wishlist_friend @username — вишлист друга"
    )
    msg = update.effective_message
    if msg is None:
        return
    if update.effective_chat is None or update.effective_chat.type != "private":
        await msg.reply_text(text)
    else:
        await msg.reply_text(text, reply_markup=main_menu())
    await update.get_bot().delete_my_commands()  # not needed; kept as no-op guard


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def cmd_myevents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ensure_user(update)
    msg = update.effective_message
    if msg is None:
        return
    await msg.reply_text(_events_text(update.effective_user.id))


async def cmd_addevent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the add-event conversation, or finish directly if date was supplied."""
    _ensure_user(update)
    msg = update.effective_message
    if msg is None:
        return ConversationHandler.END
    raw = " ".join(context.args or [])
    if raw:
        await _finish_addevent(update, raw)
        return ConversationHandler.END
    await msg.reply_text("Отправь название события (например: «др у мамы»).")
    return STATE_ADD_EVENT_TITLE


async def _receive_event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["addevent_title"] = update.effective_message.text.strip()
    await update.effective_message.reply_text("Теперь укажи дату (например: 25.03 или 25.03.2026):")
    return STATE_ADD_EVENT_DATE


async def _receive_event_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    title = context.user_data.get("addevent_title", "")
    msg = update.effective_message
    parsed = parse_date(msg.text)
    if parsed is None:
        await msg.reply_text("Не удалось распознать дату. Попробуй ещё раз (например: 25.03.2026):")
        return STATE_ADD_EVENT_DATE
    if parsed < today():
        await msg.reply_text("Дата в прошлом. Укажи будущую дату.")
        return STATE_ADD_EVENT_DATE
    repos.add_event(update.effective_user.id, title, parsed.isoformat())
    await msg.reply_text(
        f"✅ Сохранено: «{title}» — {format_date(parsed)}. Напомню за {REMIND_DAYS_TEXT}."
    )
    return ConversationHandler.END


async def _finish_addevent(update: Update, raw: str) -> None:
    """Parse '<title> <date>' from a single string and store the event."""
    msg = update.effective_message
    title, date_str = _split_title_date(raw)
    parsed = parse_date(date_str) if date_str else None
    if not title or parsed is None:
        await msg.reply_text(
            "Не удалось распознать. Формат: /addevent <название> <дата> "
            "(например: /addevent др у мамы 25.03.2026)"
        )
        return
    if parsed < today():
        await msg.reply_text("Дата в прошлом. Укажи будущую дату.")
        return
    repos.add_event(update.effective_user.id, title, parsed.isoformat())
    await msg.reply_text(
        f"✅ Сохранено: «{title}» — {format_date(parsed)}. Напомню за {REMIND_DAYS_TEXT}."
    )


def _split_title_date(raw: str) -> tuple[str, str]:
    """Split a raw '/addevent <title> <date>' body into (title, dateToken).

    The heuristic treats the last whitespace-delimited token as the date if it
    looks like one; otherwise the whole string is the title and no date token is
    extracted (the conversational path will ask for it later).
    """
    parts = raw.strip().split()
    if not parts:
        return "", ""
    if _looks_like_date(parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return raw.strip(), ""


def _looks_like_date(token: str) -> bool:
    lower = token.lower()
    if any(m in lower for m in _MONTH_WORDS):
        return True
    return bool(
        re.fullmatch(r"\d{2}[./-]?\d{2}([./-]\d{4})?", token)
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}", token)
    )


async def cmd_delevent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    try:
        event_id = int(args[0])
    except (ValueError, IndexError):
        await update.effective_message.reply_text("Формат: /delevent <номер из /myevents>")
        return
    if repos.delete_event(event_id, update.effective_user.id):
        await update.effective_message.reply_text("🗑 Дата удалена.")
    else:
        await update.effective_message.reply_text("Дата не найдена.")


# ---------------------------------------------------------------------------
# Friends
# ---------------------------------------------------------------------------


async def cmd_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ensure_user(update)
    user_id = update.effective_user.id
    friends = repos.get_friends_ids(user_id)
    requests = repos.get_friend_requests(user_id)
    parts: list[str] = []
    if friends:
        parts.append("👥 Ваши друзья:")
        for fid in friends:
            parts.append(f"• {repos.user_display(fid)}")
    else:
        parts.append("У вас пока нет друзей. Добавьте кого-то: /addfriend @username")
    if requests:
        parts.append("\n📥 Входящие заявки:")
        for r in requests:
            pid = r["requester_id"]
            parts.append(f"• {repos.user_display(pid)} — /accept {pid} | /decline {pid}")
    await update.effective_message.reply_text("\n".join(parts))


async def cmd_addfriend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the add-friend conversation, or send the request directly."""
    _ensure_user(update)
    msg = update.effective_message
    if msg is None:
        return ConversationHandler.END
    if context.args:
        return await _send_friend_request(update, context, context.args[0])
    await msg.reply_text("Отправь @username человека, которому хочешь отправить заявку в друзья:")
    return STATE_ADD_FRIEND_USERNAME


async def _receive_friend_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await _send_friend_request(update, context, update.effective_message.text)


async def _send_friend_request(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: str) -> int:
    sender_id = update.effective_user.id
    target = repos.get_user_by_username(raw)
    if target is None:
        await update.effective_message.reply_text(
            f"Пользователь «{raw}» ещё не знаком боту. "
            "Попросите его написать /start, чтобы он появился в базе.",
        )
        return ConversationHandler.END
    ok, msg = repos.send_friend_request(sender_id, int(target["user_id"]))
    await update.effective_message.reply_text(msg)
    if ok:
        try:
            await context.bot.send_message(
                int(target["user_id"]),
                f"🔔 {repos.user_display(sender_id)} хочет добавить вас в друзья.\n"
                f"Принять: /accept {sender_id}\nОтклонить: /decline {sender_id}",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("friend-request notify failed: %s", exc)
    return ConversationHandler.END


async def cmd_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond(update, context, accept=True)


async def cmd_decline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _respond(update, context, accept=False)


async def _respond(update: Update, context: ContextTypes.DEFAULT_TYPE, accept: bool) -> None:
    args = context.args or []
    try:
        requester_id = int(args[0])
    except (ValueError, IndexError):
        await update.effective_message.reply_text("Формат: /accept <id> или /decline <id>")
        return
    user_id = update.effective_user.id
    ok = repos.respond_to_request(addressee_id=user_id, requester_id=requester_id, accept=accept)
    if not ok:
        await update.effective_message.reply_text("Такая заявка не найдена.")
        return
    word = "приняли" if accept else "отклонили"
    await update.effective_message.reply_text(
        f"✅ Вы {word} заявку от {repos.user_display(requester_id)}."
    )
    if accept:
        try:
            await context.bot.send_message(
                requester_id,
                f"🎉 {repos.user_display(user_id)} принял(а) вашу заявку! Вы теперь друзья.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("accept notify failed: %s", exc)


# ---------------------------------------------------------------------------
# Wishlist
# ---------------------------------------------------------------------------


async def cmd_wishlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ensure_user(update)
    items = repos.list_wishlist(update.effective_user.id)
    msg = update.effective_message
    if not items:
        await msg.reply_text("Ваш вишлист пуст. Добавьте желаемое: /addwish <текст>")
        return
    lines = ["🎁 Ваш вишлист:"]
    for i in items:
        lines.append(f"{i['item_id']}. {i['text']}")
    lines.append("\nУдалить: /delwish <номер>")
    await msg.reply_text("\n".join(lines))


async def cmd_addwish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _ensure_user(update)
    msg = update.effective_message
    if msg is None:
        return ConversationHandler.END
    if context.args:
        await _finish_addwish(update, " ".join(context.args))
        return ConversationHandler.END
    await msg.reply_text("Напиши, что добавить в вишлист:")
    return STATE_ADD_WISH_TEXT


async def _receive_wish_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await _finish_addwish(update, update.effective_message.text)
    return ConversationHandler.END


async def _finish_addwish(update: Update, text: str) -> None:
    if not text.strip():
        await update.effective_message.reply_text("Пустая запись, попробуй ещё раз:")
        return
    item_id = repos.add_wishlist_item(update.effective_user.id, text)
    await update.effective_message.reply_text(f"✅ Добавлено в вишлист (№{item_id}).")


async def cmd_delwish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    try:
        item_id = int(args[0])
    except (ValueError, IndexError):
        await update.effective_message.reply_text("Формат: /delwish <номер>")
        return
    if repos.delete_wishlist_item(item_id, update.effective_user.id):
        await update.effective_message.reply_text("🗑 Удалено из вишлиста.")
    else:
        await update.effective_message.reply_text("Пункт не найден.")


async def cmd_wishlist_friend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a friend's full wishlist (they must be confirmed friends first)."""
    _ensure_user(update)
    msg = update.effective_message
    if msg is None:
        return
    args = context.args or []
    if not args:
        await msg.reply_text("Формат: /wishlist_friend @username")
        return
    target = repos.get_user_by_username(args[0])
    if target is None:
        await msg.reply_text(f"Пользователь «{args[0]}» не найден в базе.")
        return
    user_id = update.effective_user.id
    if int(target["user_id"]) not in repos.get_friends_ids(user_id):
        await msg.reply_text("Вы можете просматривать вишлист только своих друзей.")
        return
    items = repos.list_wishlist(int(target["user_id"]))
    if not items:
        await msg.reply_text(f"Вишлист {repos.user_display(int(target['user_id']))} пуст.")
        return
    lines = [f"🎁 Вишлист {repos.user_display(int(target['user_id']))}:"]
    lines += [f"• {i['text']}" for i in items]
    await msg.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Callback (inline button) handling
# ---------------------------------------------------------------------------


async def _on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query: CallbackQuery = update.callback_query
    await query.answer()
    data = (query.data or "").split(":")
    if data[0] != "menu":
        await query.edit_message_text("Неизвестное действие.")
        return
    action = data[1] if len(data) > 1 else ""
    user_id = query.from_user.id
    if action == "events":
        text = _events_text(user_id)
    elif action == "add_event":
        text = "Используй /addevent <название> <дата>"
    elif action == "friends":
        text = "Используй /friends"
    elif action == "wishlist":
        text = "Используй /wishlist"
    else:
        text = "Главное меню · используй команды ниже"
    await query.edit_message_text(text, reply_markup=main_menu())


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def build_handlers(app) -> None:
    """Register all command/callback/conversation handlers on `Application`."""
    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addevent", cmd_addevent),
            CommandHandler("addfriend", cmd_addfriend),
            CommandHandler("addwish", cmd_addwish),
        ],
        states={
            STATE_ADD_EVENT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_event_title)
            ],
            STATE_ADD_EVENT_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_event_date)
            ],
            STATE_ADD_FRIEND_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_friend_username)
            ],
            STATE_ADD_WISH_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_wish_text)
            ],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
    )
    app.add_handler(conversation)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("myevents", cmd_myevents))
    app.add_handler(CommandHandler("delevent", cmd_delevent))
    app.add_handler(CommandHandler("friends", cmd_friends))
    app.add_handler(CommandHandler("accept", cmd_accept))
    app.add_handler(CommandHandler("decline", cmd_decline))
    app.add_handler(CommandHandler("wishlist", cmd_wishlist))
    app.add_handler(CommandHandler("delwish", cmd_delwish))
    app.add_handler(CommandHandler("wishlist_friend", cmd_wishlist_friend))
    app.add_handler(CallbackQueryHandler(_on_callback, pattern="^menu:"))


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_message is not None:
        await update.effective_message.reply_text("Отменено.")
    return ConversationHandler.END

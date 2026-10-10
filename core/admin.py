"""Admin panel: statistics, user management, upload logs and broadcast.

Access is limited to the Telegram user IDs listed in ADMIN_IDS. Callback
actions carry the `adm` prefix and are routed here from core.bot_client before
normal session handling, so the panel works independently of any user session.

Everything degrades gracefully: with no database configured, statistics are
unavailable and user/broadcast actions report that rather than crashing.
"""
import asyncio
import logging
from typing import Optional

from pyrogram import Client, enums
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, Message

from config import TEMP_ROOT, is_admin
from core import db, rich
from core import session as sessions
from utils import views
from utils.helpers import dir_size
from utils.keyboards import (
    USERS_PER_PAGE,
    admin_back_menu,
    admin_broadcast_cancel,
    admin_menu,
    admin_users_keyboard,
    to_bot_api,
    to_pyrogram,
)

logger = logging.getLogger(__name__)

# Pause between broadcast sends, and the longest FloodWait we will sit out for a
# single recipient before giving up on them.
BROADCAST_DELAY = 0.05
BROADCAST_MAX_FLOOD = 300

# user_id -> id of the admin panel message, so callbacks edit it in place.
_panel_message: dict[int, int] = {}
# Admins whose next plain message is a broadcast body.
_awaiting_broadcast: set[int] = set()


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

async def _send(
    client: Client,
    chat_id: int,
    screen: tuple[str, str],
    keyboard=None,
) -> Optional[int]:
    rich_html, plain_html = screen
    message_id = await rich.send(chat_id, rich_html, to_bot_api(keyboard))
    if message_id is not None:
        return message_id
    message = await client.send_message(
        chat_id,
        plain_html,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=to_pyrogram(keyboard),
    )
    return message.id


async def _edit(
    client: Client,
    chat_id: int,
    message_id: int,
    screen: tuple[str, str],
    keyboard=None,
) -> None:
    rich_html, plain_html = screen
    if await rich.edit(chat_id, message_id, rich_html, to_bot_api(keyboard)):
        return
    try:
        await client.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=plain_html,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=to_pyrogram(keyboard),
        )
    except MessageNotModified:
        pass
    except FloodWait as exc:
        logger.info("Skipping admin edit, FloodWait %ss", getattr(exc, "value", "?"))
    except Exception as exc:  # noqa: BLE001 - a dead panel message must not crash
        logger.warning("Could not update admin panel for %s: %s", chat_id, exc)


async def _panel_screen() -> tuple[str, str]:
    stats = await db.get_stats()
    temp_bytes = await asyncio.to_thread(dir_size, TEMP_ROOT)
    return views.admin_panel(stats, sessions.count(), temp_bytes)


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #

async def command(client: Client, message: Message) -> None:
    """/admin, /ban <id>, /unban <id>."""
    user_id = message.from_user.id if message.from_user else 0
    if not is_admin(user_id):
        await _send(client, user_id, views.admin_denied())
        return

    command_name = (message.command[0] if message.command else "").lower()
    if command_name in ("ban", "unban"):
        await _set_ban(client, message, banned=command_name == "ban")
        return
    if command_name in ("setdump", "unsetdump"):
        await _set_dump_chat(client, message, clear=command_name == "unsetdump")
        return

    _awaiting_broadcast.discard(user_id)
    message_id = await _send(client, user_id, await _panel_screen(), admin_menu())
    if message_id is not None:
        _panel_message[user_id] = message_id


async def handle_text(client: Client, message: Message) -> bool:
    """Consume a broadcast body from an admin. True means handled here."""
    user_id = message.from_user.id if message.from_user else 0
    if user_id not in _awaiting_broadcast:
        return False
    _awaiting_broadcast.discard(user_id)
    body = message.text or ""
    asyncio.create_task(_run_broadcast(client, user_id, body))
    return True


async def callback(client: Client, query: CallbackQuery, value: str) -> None:
    """Handle an `adm:` callback. `value` may itself be `name:arg:arg`."""
    user_id = query.from_user.id if query.from_user else 0
    if not is_admin(user_id):
        await query.answer("Admins only.", show_alert=True)
        return

    action, _, arg = value.partition(":")
    message_id = query.message.id if query.message else _panel_message.get(user_id)
    if message_id is None:
        await query.answer("That panel expired — send /admin again.", show_alert=True)
        return

    if action == "menu":
        await query.answer()
        await _edit(client, user_id, message_id, await _panel_screen(), admin_menu())
        _panel_message[user_id] = message_id
    elif action == "users":
        await query.answer()
        await _show_users(client, user_id, message_id, _as_int(arg, 0))
    elif action == "toggle":
        target_str, _, page_str = arg.partition(":")
        await _toggle(client, query, message_id, _as_int(target_str, 0), _as_int(page_str, 0))
    elif action == "logs":
        await query.answer()
        logs = await db.recent_logs(10)
        await _edit(client, user_id, message_id, views.admin_logs(logs), admin_back_menu())
    elif action == "broadcast":
        _awaiting_broadcast.add(user_id)
        await query.answer()
        await _edit(
            client, user_id, message_id,
            views.admin_broadcast_prompt(), admin_broadcast_cancel(),
        )
    elif action == "bcancel":
        _awaiting_broadcast.discard(user_id)
        await query.answer("Cancelled")
        await _edit(client, user_id, message_id, await _panel_screen(), admin_menu())
    elif action == "noop":
        await query.answer()
    else:
        await query.answer("Unknown action.", show_alert=True)


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Screens
# --------------------------------------------------------------------------- #

async def _show_users(client: Client, user_id: int, message_id: int, page: int) -> None:
    page = max(0, page)
    users, total = await db.list_users(skip=page * USERS_PER_PAGE, limit=USERS_PER_PAGE)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    if page > total_pages - 1:
        page = total_pages - 1
        users, total = await db.list_users(skip=page * USERS_PER_PAGE, limit=USERS_PER_PAGE)
    screen = views.admin_users(page + 1, total_pages, total)
    keyboard = admin_users_keyboard(users, page, total_pages)
    await _edit(client, user_id, message_id, screen, keyboard)
    _panel_message[user_id] = message_id


async def _toggle(
    client: Client, query: CallbackQuery, message_id: int, target: int, page: int
) -> None:
    if not target:
        await query.answer("Bad user reference.", show_alert=True)
        return
    banned_now = await db.is_banned(target)
    ok = await db.set_banned(target, not banned_now)
    if not ok:
        await query.answer("Database unavailable.", show_alert=True)
        return
    verb = "Unbanned" if banned_now else "Banned"
    await query.answer(f"{verb} {target}")
    await _show_users(client, query.from_user.id, message_id, page)


async def _set_ban(client: Client, message: Message, banned: bool) -> None:
    user_id = message.from_user.id
    args = message.command[1:] if message.command else []
    if not args or not args[0].lstrip("-").isdigit():
        usage = f"/{'ban' if banned else 'unban'} <user_id>"
        await _send(client, user_id, views.error("Usage", usage))
        return
    target = int(args[0])
    if not await db.set_banned(target, banned):
        await _send(client, user_id, views.error("Could not update", "Database unavailable."))
        return
    verb = "banned" if banned else "unbanned"
    await _send(
        client, user_id,
        (f"<h4>✅ User {verb}</h4>", f"✅ User <code>{target}</code> {verb}."),
    )


async def _set_dump_chat(client: Client, message: Message, clear: bool) -> None:
    """Point extracted-file delivery at a group/channel, or reset it."""
    user_id = message.from_user.id

    if clear:
        if not await db.set_dump_chat(None):
            await _send(client, user_id, views.error("Could not update", "Database unavailable."))
            return
        await _send(
            client, user_id,
            ("<h4>✅ Dump chat cleared</h4>",
             "✅ Dump cleared — extracted files go back to the user."),
        )
        return

    args = message.command[1:] if message.command else []
    if not args:
        current = await db.get_dump_chat()
        detail = (
            f"<code>{current}</code>" if current else "not set — files go to the user."
        )
        await _send(
            client, user_id,
            ("<h4>📤 Dump chat</h4>", f"📤 Current dump chat: {detail}"),
        )
        return

    raw = args[0]
    if not raw.lstrip("-").isdigit():
        await _send(
            client, user_id,
            views.error("Usage", "/setdump <chat_id>  ·  /unsetdump"),
        )
        return
    chat_id = int(raw)
    if not await db.set_dump_chat(chat_id):
        await _send(client, user_id, views.error("Could not update", "Database unavailable."))
        return

    note = ""
    try:
        await client.send_message(chat_id, "✅ This chat is now the UnzipBot dump target.")
    except Exception as exc:  # noqa: BLE001 - may lack rights, still keep the setting
        note = (
            f"\n⚠️ Could not post here yet ({type(exc).__name__}). "
            "Add the bot as an admin with post rights."
        )
    await _send(
        client, user_id,
        ("<h4>✅ Dump chat set</h4>",
         f"✅ Extracted files will now be sent to <code>{chat_id}</code>.{note}"),
    )


# --------------------------------------------------------------------------- #
# Broadcast
# --------------------------------------------------------------------------- #

async def _run_broadcast(client: Client, admin_id: int, body: str) -> None:
    targets = await db.all_user_ids()
    total = len(targets)
    if not total:
        await _send(client, admin_id, views.admin_broadcast_done(0, 0, 0))
        return

    status_id = await _send(client, admin_id, views.admin_broadcast_progress(0, total, 0, 0))
    step = max(1, total // 20)
    sent = failed = done = 0

    for uid in targets:
        try:
            await client.send_message(uid, body)  # no parse_mode: body is literal
            sent += 1
        except FloodWait as exc:
            wait = int(getattr(exc, "value", 0) or 0)
            if wait > BROADCAST_MAX_FLOOD:
                failed += 1
            else:
                await asyncio.sleep(wait + 1)
                try:
                    await client.send_message(uid, body)
                    sent += 1
                except Exception:  # noqa: BLE001
                    failed += 1
        except Exception as exc:  # noqa: BLE001 - one bad recipient must not stop the run
            failed += 1
            logger.debug("Broadcast to %s failed: %s", uid, exc)

        done += 1
        if status_id is not None and (done % step == 0 or done == total):
            await _edit(
                client, admin_id, status_id,
                views.admin_broadcast_progress(done, total, sent, failed),
            )
        await asyncio.sleep(BROADCAST_DELAY)

    final = views.admin_broadcast_done(total, sent, failed)
    if status_id is not None:
        await _edit(client, admin_id, status_id, final)
    else:
        await _send(client, admin_id, final)

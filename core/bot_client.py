import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Optional

from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import CallbackQuery, Message

from config import (
    API_HASH,
    API_ID,
    BANNER_URL,
    BOT_TOKEN,
    MAX_ARCHIVE_SIZE,
    MAX_FILES_PER_BATCH,
    MAX_TOTAL_EXTRACTED,
    PAGE_SIZE,
    PROGRESS_MIN_SIZE,
    SESSION_DIR,
    SLEEP_THRESHOLD,
    STATUS_EDIT_INTERVAL,
    TRANSFER_CONCURRENCY,
    WORKERS,
)
from core import admin, rich, session as sessions
from core.db import get_dump_chat, is_banned, log_upload, track_user
from core.extractor import (
    ArchiveError,
    BackendMissing,
    detect_archive_type,
    extract,
    list_entries,
    needs_password,
    probe,
)
from core.session import Session
from core.uploader import forget as forget_pacing, send_file
from core.validator import check_file
from core.volumes import assemble, detect_volume, missing_volumes
from utils import views
from utils.callback_data import make_callback, parse_callback
from utils.helpers import (
    cleanup_user,
    format_size,
    sanitize_filename,
    user_archive_dir,
    user_extracted_dir,
)
from utils.keyboards import (
    Keyboard,
    action_menu,
    file_list_keyboard,
    password_menu,
    to_bot_api,
    to_pyrogram,
)

logger = logging.getLogger(__name__)

bot: Optional[Client] = None

# A screen is (rich_html, plain_html) — see utils.views.
Screen = tuple[str, str]


def create_bot_client() -> Client:
    """Build the client. Called from inside the running loop to bind it correctly."""
    global bot
    # Keep the login on a persisted path when one is provided; a fresh session
    # file every restart means re-running bot authorization, which Telegram
    # rate-limits hard once redeploys overlap.
    session_name = "unzip_bot"
    if SESSION_DIR:
        session_path = Path(SESSION_DIR)
        session_path.mkdir(parents=True, exist_ok=True)
        session_name = str(session_path / "unzip_bot")
    bot = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        # Without this Pyrogram moves one chunk at a time and a 2 GB transfer
        # crawls regardless of available bandwidth.
        max_concurrent_transmissions=TRANSFER_CONCURRENCY,
        workers=WORKERS,
        sleep_threshold=SLEEP_THRESHOLD,
    )
    # Registration order is significant: within a handler group Pyrogram runs the
    # first matching handler only, so commands must be checked before the
    # catch-all text handler.
    bot.on_message(filters.private & filters.command(["start", "help", "cancel", "admin", "ban", "unban", "setdump", "unsetdump"]))(on_command)
    bot.on_message(filters.private & filters.document)(on_document)
    bot.on_message(filters.private & filters.text)(on_text)
    bot.on_callback_query()(on_callback)
    return bot


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

async def _reply(
    client: Client,
    chat_id: int,
    screen: Screen,
    keyboard: Optional[Keyboard] = None,
    photo: Optional[str] = None,
) -> Optional[int]:
    """Send a new message, preferring a rich one. Returns its message_id.

    A rich message embeds `photo` via <img>, so it is only sent separately on the
    plain-text path.
    """
    rich_html, plain_html = screen
    message_id = await rich.send(chat_id, rich_html, to_bot_api(keyboard))
    if message_id is not None:
        return message_id

    if photo:
        try:
            message = await client.send_photo(
                chat_id,
                photo=photo,
                caption=plain_html,
                parse_mode=enums.ParseMode.HTML,
                reply_markup=to_pyrogram(keyboard),
            )
            return message.id
        except Exception as exc:  # noqa: BLE001 - fall back to text if the URL fails
            logger.warning("Banner failed, sending text instead: %s", exc)

    message = await client.send_message(
        chat_id,
        plain_html,
        parse_mode=enums.ParseMode.HTML,
        reply_markup=to_pyrogram(keyboard),
    )
    return message.id


async def _set_status(
    client: Client,
    session: Session,
    screen: Screen,
    keyboard: Optional[Keyboard] = None,
    throttle: bool = False,
) -> None:
    """Rewrite the session's status message.

    Everything the user sees lives in this one message, addressed by id. An
    earlier version passed a CallbackQuery to a helper that called `.edit_text()`
    — a method CallbackQuery does not have — and swallowed the AttributeError, so
    no button ever changed anything on screen.

    `throttle=True` marks a progress update: droppable, and dropped whenever the
    previous edit was recent. Edits are rate-limited by Telegram just like sends,
    and a 200-file batch would otherwise emit thousands of them.
    """
    now = time.monotonic()
    if throttle and now - session.last_status_edit < STATUS_EDIT_INTERVAL:
        return
    session.last_status_edit = now

    rich_html, plain_html = screen
    markup = to_bot_api(keyboard)
    if await rich.edit(session.user_id, session.status_message_id, rich_html, markup):
        return

    try:
        await client.edit_message_text(
            chat_id=session.user_id,
            message_id=session.status_message_id,
            text=plain_html,
            parse_mode=enums.ParseMode.HTML,
            reply_markup=to_pyrogram(keyboard),
        )
    except MessageNotModified:
        pass
    except FloodWait as exc:
        # Never wait out a flood just to refresh a status line; skip this frame.
        logger.info("Skipping status edit, FloodWait %ss", getattr(exc, "value", "?"))
    except Exception as exc:  # noqa: BLE001 - a dead status message must not kill the job
        logger.warning("Could not update status for %s: %s", session.user_id, exc)


def _transfer_reporter(
    client: Client,
    session: Session,
    label: str,
    filename: str = "",
) -> Callable:
    """Build a Pyrogram progress callback that reports speed and ETA.

    This must be a coroutine *function*, not an object with an async `__call__`:
    Pyrogram decides how to invoke it with `inspect.iscoroutinefunction()`, which
    is False for a callable instance. It then ran the callback on a worker thread
    and discarded the coroutine it returned — "coroutine was never awaited", and
    no progress was ever displayed.
    """
    started = time.monotonic()

    async def report(current: int, total: int) -> None:
        if not total:
            return
        elapsed = time.monotonic() - started
        speed = current / elapsed if elapsed > 0.5 else 0.0
        eta = (total - current) / speed if speed > 0 else None
        await _set_status(
            client,
            session,
            views.transfer(label, current, total, speed, eta, filename),
            throttle=True,
        )

    return report


async def _fail(client: Client, session: Session, title: str, detail: str = "") -> None:
    """Report a terminal error and drop the session."""
    await _set_status(client, session, views.error(title, detail))
    forget_pacing(session.user_id)
    sessions.end(session.user_id)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

def _from_self(message: Message) -> bool:
    """True when an update is the bot's own outgoing message.

    Telegram does not normally echo a bot's messages back to it, but a
    duplicated/shared session or a business connection can. Without this guard
    the bot answers its own reply, which arrives as another text update, and
    loops forever.
    """
    if getattr(message, "outgoing", False):
        return True
    user = message.from_user
    return bool(user is not None and getattr(user, "is_self", False))


async def _guard_message(message: Message) -> bool:
    """Track the sender and block banned users. True means stop processing."""
    if _from_self(message):
        return True
    user = message.from_user
    if user is None:
        return True
    await track_user(user.id, user.username, user.first_name)
    if await is_banned(user.id):
        await message.reply_text("🚫 You are banned from using this bot.")
        return True
    return False


async def _guard_callback(callback: CallbackQuery) -> bool:
    user = callback.from_user
    if user is None:
        return True
    await track_user(user.id, user.username, user.first_name)
    if await is_banned(user.id):
        await callback.answer("🚫 You are banned.", show_alert=True)
        return True
    return False


async def on_command(client: Client, message: Message) -> None:
    if _from_self(message):
        return
    user_id = message.from_user.id
    command = (message.command[0] if message.command else "").lower()

    if command in ("admin", "ban", "unban"):
        await admin.command(client, message)
    elif await _guard_message(message):
        return
    elif command == "cancel":
        if sessions.get(user_id):
            forget_pacing(user_id)
            sessions.end(user_id)
            await _reply(client, user_id, views.cancelled())
        else:
            await message.reply_text("Nothing to cancel — send me an archive.")
    elif command == "help":
        await _reply(
            client,
            user_id,
            views.help_screen(MAX_ARCHIVE_SIZE, MAX_TOTAL_EXTRACTED, MAX_FILES_PER_BATCH),
        )
    else:
        await _reply(client, user_id, views.welcome(), photo=BANNER_URL)


async def on_document(client: Client, message: Message) -> None:
    user_id = message.from_user.id
    document = message.document

    if await _guard_message(message):
        return

    existing = sessions.get(user_id)
    if existing and existing.busy:
        await message.reply_text(
            "⏳ I'm still working on your last archive. Wait for it to finish, or send /cancel."
        )
        return

    filename = sanitize_filename(document.file_name or "")

    # `movie.zip.001` is a slice of an archive, not an archive. Collect the set
    # first and assemble it once the user says they're done.
    volume = detect_volume(filename)
    if volume:
        await _on_volume(client, message, existing, filename, volume)
        return

    archive_type = detect_archive_type(filename)
    if not archive_type:
        await message.reply_text(
            "🤔 That doesn't look like an archive I can open.\n"
            "Supported: zip, rar, 7z, tar, gz, bz2, xz\n"
            "Split sets too: <code>name.zip.001</code>, <code>name.part1.rar</code>",
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if document.file_size and document.file_size > MAX_ARCHIVE_SIZE:
        await message.reply_text(
            f"❌ That archive is {format_size(document.file_size)}. "
            f"My limit is {format_size(MAX_ARCHIVE_SIZE)}."
        )
        return

    # A fresh archive replaces whatever came before, including its temp files.
    cleanup_user(user_id)
    forget_pacing(user_id)
    status_id = await _reply(
        client, user_id, views.transfer("📥 Downloading", 0, document.file_size or 1, filename=filename)
    )
    archive_path = user_archive_dir(user_id) / filename
    session = sessions.start(user_id, archive_path, archive_type, status_id)

    downloaded = await _download(client, session, message, archive_path, filename)
    if downloaded is None:
        return

    session.archive_path = downloaded
    await _offer_actions(client, session, filename)


async def _download(
    client: Client,
    session: Session,
    message: Message,
    target: Path,
    label_name: str,
) -> Optional[Path]:
    """Download a document into `target`. None means it failed and was reported."""
    session.busy = True
    try:
        result = await client.download_media(
            message,
            file_name=str(target),
            progress=_transfer_reporter(client, session, "📥 Downloading", label_name),
        )
    except Exception as exc:  # noqa: BLE001 - network errors are expected here
        logger.warning("Download failed for %s: %s", session.user_id, exc)
        await _fail(client, session, "Download failed", str(exc))
        return None
    finally:
        session.busy = False

    if not result:
        await _fail(client, session, "Download failed", "Telegram returned no file.")
        return None
    # download_media may adjust the path (deduplicating names, for instance).
    return Path(result)


async def _offer_actions(client: Client, session: Session, filename: str) -> None:
    session.state = "WAIT_ACTION"
    await _set_status(
        client,
        session,
        views.received(filename, session.archive_path.stat().st_size),
        keyboard=action_menu(),
    )


async def _on_volume(
    client: Client,
    message: Message,
    existing: Optional[Session],
    filename: str,
    volume,
) -> None:
    """Collect one volume of a multi-part set."""
    user_id = message.from_user.id
    archive_type = detect_archive_type(volume.base)
    if not archive_type:
        await message.reply_text("🤔 I don't recognise the archive type in that split set.")
        return

    # Reuse the session while it is collecting the same set; otherwise start over.
    same_set = (
        existing is not None
        and existing.state == "COLLECTING"
        and existing.archive_path.name == volume.base
    )
    if same_set:
        session = existing
    else:
        cleanup_user(user_id)
        forget_pacing(user_id)
        status_id = await _reply(
            client, user_id,
            views.transfer("📥 Downloading part", 0, message.document.file_size or 1, filename=filename),
        )
        session = sessions.start(
            user_id, user_archive_dir(user_id) / volume.base, archive_type, status_id
        )
        session.volume_kind = volume.kind
        session.state = "COLLECTING"

    if message.document.file_size:
        collected = sum(p.stat().st_size for p in session.volumes.values() if p.exists())
        if collected + message.document.file_size > MAX_ARCHIVE_SIZE:
            await _fail(
                client, session, "Split set too large",
                f"Combined size would exceed {format_size(MAX_ARCHIVE_SIZE)}.",
            )
            return

    # RAR volumes must keep their own names for rarfile to follow the chain;
    # byte slices can be stored under any name since they get concatenated.
    part_path = user_archive_dir(user_id) / filename
    downloaded = await _download(client, session, message, part_path, filename)
    if downloaded is None:
        return

    session.volumes[volume.number] = downloaded
    await _show_volume_status(client, session)


async def _show_volume_status(client: Client, session: Session) -> None:
    have = set(session.volumes)
    missing = missing_volumes(have)
    keyboard = [
        [("🧩 Combine & Extract", make_callback("vol", "done"))],
        [("❌ Cancel", make_callback("act", "cancel"))],
    ]
    await _set_status(
        client,
        session,
        views.volumes_waiting(session.archive_path.name, have, missing),
        keyboard=keyboard,
    )


async def _on_volumes_done(
    client: Client, callback: CallbackQuery, session: Session, value: str
) -> None:
    if not session.volumes:
        await callback.answer("No parts received yet.", show_alert=True)
        return

    missing = missing_volumes(set(session.volumes))
    if missing:
        await callback.answer(
            "Missing part(s): " + ", ".join(f"{n:03d}" for n in missing[:10]) +
            "\nSend them first — an incomplete set can't be opened.",
            show_alert=True,
        )
        return

    await callback.answer()
    session.busy = True
    try:
        if session.volume_kind == "concat":
            await _set_status(client, session, (
                f"<h4>🧩 Joining {len(session.volumes)} parts…</h4>",
                f"🧩 Joining {len(session.volumes)} parts…",
            ))
            session.archive_path = await asyncio.to_thread(
                assemble, dict(session.volumes), session.archive_path
            )
        else:
            # A real RAR set: point at the first volume and let rarfile walk it.
            session.archive_path = session.volumes[min(session.volumes)]
        session.volumes.clear()
    except OSError as exc:
        await _fail(client, session, "Could not join the parts", str(exc))
        return
    finally:
        session.busy = False

    await _offer_actions(client, session, session.archive_path.name)


async def on_text(client: Client, message: Message) -> None:
    """A plain message is the password when we're waiting for one."""
    if _from_self(message):
        return
    user_id = message.from_user.id

    if await admin.handle_text(client, message):
        return
    if await _guard_message(message):
        return

    session = sessions.get(user_id)

    if not session or session.state != "WAIT_PASSWORD_REPLY":
        await message.reply_text("👋 Send me an archive file to extract. /help for details.")
        return

    session.password = message.text
    try:
        # Don't leave the password sitting in the chat history.
        await message.delete()
    except Exception:  # noqa: BLE001 - deletion needs rights we may not have
        pass

    await _open_archive(client, session)


async def on_callback(client: Client, callback: CallbackQuery) -> None:
    action, value = parse_callback(callback.data or "")

    if action == "adm":
        await admin.callback(client, callback, value)
        return

    if action == "noop":
        await callback.answer()
        return

    if await _guard_callback(callback):
        return

    session = sessions.get(callback.from_user.id)
    if not session:
        await callback.answer("That session expired — send the archive again.", show_alert=True)
        return

    if session.busy:
        await callback.answer("Still working on it…", show_alert=True)
        return

    # Keep the status message in sync with the keyboard the user just tapped, so
    # a stale message from an earlier archive can't be edited by mistake.
    if callback.message:
        session.status_message_id = callback.message.id

    handlers = {
        "act": _on_action,
        "pwd": _on_password_choice,
        "page": _on_page,
        "sel": _on_select,
        "ext": _on_extract,
        "vol": _on_volumes_done,
    }
    handler = handlers.get(action)
    if handler is None:
        await callback.answer("I don't know that button.", show_alert=True)
        return
    await handler(client, callback, session, value)


async def _on_action(client: Client, callback: CallbackQuery, session: Session, value: str) -> None:
    if value == "cancel":
        forget_pacing(session.user_id)
        await _set_status(client, session, views.cancelled())
        sessions.end(session.user_id)
        await callback.answer("Cancelled")
        return

    if value != "extract":
        await callback.answer("I don't know that button.", show_alert=True)
        return

    await callback.answer()
    if not await asyncio.to_thread(needs_password, session.archive_path, session.archive_type):
        # Nothing to ask about — skip straight to reading the archive.
        session.password = None
        await _open_archive(client, session)
        return

    session.state = "WAIT_PASSWORD_ASK"
    await _set_status(client, session, views.password_ask(), keyboard=password_menu())


async def _on_password_choice(
    client: Client, callback: CallbackQuery, session: Session, value: str
) -> None:
    if value == "yes":
        session.state = "WAIT_PASSWORD_REPLY"
        await callback.answer()
        await _set_status(client, session, views.password_prompt())
    elif value == "no":
        session.password = None
        await callback.answer()
        await _open_archive(client, session)
    else:
        await callback.answer("I don't know that button.", show_alert=True)


async def _on_page(client: Client, callback: CallbackQuery, session: Session, value: str) -> None:
    try:
        session.page = int(value)
    except ValueError:
        await callback.answer("Bad page number", show_alert=True)
        return
    await callback.answer()
    await _show_file_list(client, session)


async def _on_select(client: Client, callback: CallbackQuery, session: Session, value: str) -> None:
    if value == "all":
        session.selected = {i for i, item in enumerate(session.items) if not item["is_dir"]}
        toast = f"Selected {len(session.selected)} file(s)"
    elif value == "none":
        session.selected.clear()
        toast = "Selection cleared"
    else:
        try:
            index = int(value)
        except ValueError:
            await callback.answer("Bad selection", show_alert=True)
            return
        if not 0 <= index < len(session.items):
            await callback.answer("That file is no longer in the list.", show_alert=True)
            return
        if index in session.selected:
            session.selected.discard(index)
            toast = f"Removed {session.items[index]['name']}"
        else:
            session.selected.add(index)
            toast = f"Added {session.items[index]['name']}"

    # Redraw before answering so the checkbox visibly moves.
    await _show_file_list(client, session)
    await callback.answer(toast[:200])


# --------------------------------------------------------------------------- #
# Archive workflow
# --------------------------------------------------------------------------- #

async def _open_archive(client: Client, session: Session) -> None:
    """Verify the password, read the listing and show it."""
    session.busy = True
    session.state = "READING"
    try:
        await _set_status(client, session, views.opening())
        try:
            # Archive work is synchronous and can run for minutes; a thread keeps
            # the client responsive to pings and callbacks meanwhile.
            await asyncio.to_thread(
                probe, session.archive_path, session.archive_type, session.password
            )
            items = await asyncio.to_thread(
                list_entries, session.archive_path, session.archive_type, session.password
            )
        except BackendMissing as exc:
            # Not a damaged archive — the server just lacks the tool.
            await _fail(client, session, "Can't open RAR archives here", str(exc))
            return
        except ArchiveError as exc:
            title = (
                "Wrong password, or the archive is damaged"
                if session.password
                else "Could not open the archive"
            )
            await _fail(client, session, title, str(exc))
            return

        if not any(not item["is_dir"] for item in items):
            await _fail(client, session, "That archive has no files in it")
            return

        session.items = items
        session.page = 0
        session.selected.clear()
        session.state = "LISTING"
    finally:
        session.busy = False

    await _show_file_list(client, session)


async def _show_file_list(client: Client, session: Session) -> None:
    files = session.files
    total_pages = max(1, (len(session.items) + PAGE_SIZE - 1) // PAGE_SIZE)
    session.page = max(0, min(session.page, total_pages - 1))
    selected_size = sum(
        session.items[i]["size"] for i in session.selected if i < len(session.items)
    )
    await _set_status(
        client,
        session,
        views.file_list(
            total_files=len(files),
            total_size=sum(f["size"] for f in files),
            page=session.page + 1,
            total_pages=total_pages,
            selected_count=len(session.selected),
            selected_size=selected_size,
        ),
        keyboard=file_list_keyboard(session.items, session.page, PAGE_SIZE, session.selected),
    )


async def _on_extract(client: Client, callback: CallbackQuery, session: Session, mode: str) -> None:
    if mode == "selected":
        chosen = sorted(session.selected)
        if not chosen:
            await callback.answer("Select at least one file first.", show_alert=True)
            return
        members = [session.items[i]["raw"] for i in chosen]
        declared = sum(session.items[i]["size"] for i in chosen)
    elif mode == "all":
        members = [f["raw"] for f in session.files]
        declared = sum(f["size"] for f in session.files)
    else:
        await callback.answer("I don't know that button.", show_alert=True)
        return

    # The listing tells us the uncompressed size up front, so an over-limit
    # archive (or a zip bomb) is refused before anything touches the disk.
    if declared > MAX_TOTAL_EXTRACTED:
        await callback.answer(
            f"That would extract to {format_size(declared)}, over the "
            f"{format_size(MAX_TOTAL_EXTRACTED)} limit.",
            show_alert=True,
        )
        return

    # Any number of files is accepted: the selection is split into rounds of
    # MAX_FILES_PER_BATCH, and each round is extracted and sent before the next
    # starts. Disk use stays bounded and a failure in one round doesn't cost
    # the rounds already sent.
    chunks = [
        members[i:i + MAX_FILES_PER_BATCH]
        for i in range(0, len(members), MAX_FILES_PER_BATCH)
    ]
    if len(chunks) > 1:
        await callback.answer(
            f"{len(members)} files — sending them in {len(chunks)} rounds of "
            f"up to {MAX_FILES_PER_BATCH}.",
            show_alert=True,
        )
    else:
        await callback.answer()

    session.busy = True
    try:
        await _extract_and_send(client, session, chunks)
    finally:
        session.busy = False


async def _extract_and_send(
    client: Client,
    session: Session,
    chunks: list[list[str]],
) -> None:
    """Extract and upload the selection, one round at a time."""
    total_files = sum(len(chunk) for chunk in chunks)
    rounds = len(chunks)
    out_dir = user_extracted_dir(session.user_id)

    # Files go to the dump chat when one is configured, otherwise back to the user.
    dump_chat = await get_dump_chat()
    send_chat = dump_chat or session.user_id
    if dump_chat:
        logger.info(
            "Dumping %d file(s) from user %s to chat %s",
            total_files, session.user_id, dump_chat,
        )

    sent = 0
    done = 0
    extracted_total = 0
    failures: list[str] = []
    skipped_all: list[str] = []

    for round_no, members in enumerate(chunks, start=1):
        session.state = "EXTRACTING"
        await _set_status(client, session, views.extracting(len(members), round_no, rounds))

        # Each round reuses the same output directory; it is wiped before
        # extraction so a 500-file selection never needs 500 files of disk.
        shutil.rmtree(out_dir, ignore_errors=True)
        try:
            await asyncio.to_thread(
                extract, session.archive_path, out_dir, session.archive_type,
                members, session.password,
            )
        except ArchiveError as exc:
            await _fail(client, session, "Extraction failed", str(exc))
            return

        files, skipped, round_size = await asyncio.to_thread(_collect_files, out_dir)
        skipped_all.extend(skipped)
        extracted_total += round_size

        # The declared size was checked up front, but archives can lie — guard
        # on what actually landed on disk.
        if extracted_total > MAX_TOTAL_EXTRACTED:
            await _fail(
                client, session, "Over the size limit",
                f"Extracted {format_size(extracted_total)} so far, limit is "
                f"{format_size(MAX_TOTAL_EXTRACTED)}.",
            )
            return

        session.state = "UPLOADING"
        for path in files:
            done += 1
            size = path.stat().st_size
            # Show the batch counter first, then let big files draw their own bar.
            await _set_status(
                client, session,
                views.batch(done, total_files, path.name, sent, len(failures), round_no, rounds),
                throttle=done > 1,
            )
            reporter = None
            if size >= PROGRESS_MIN_SIZE:
                reporter = _transfer_reporter(
                    client, session, f"📤 Sending {done}/{total_files}", path.name
                )

            caption = f"<code>{rich.escape(path.name)}</code>"
            if dump_chat:
                caption += f"\n👤 <code>{session.user_id}</code>"
            error = await send_file(
                client, send_chat, path, caption=caption, progress=reporter,
            )
            if error:
                failures.append(f"{path.name}: {error}")
            else:
                sent += 1
            await log_upload({
                "user_id": session.user_id,
                "archive": session.archive_path.name,
                "file": path.name,
                "size": size,
                "status": "failed" if error else "success",
            })

    if done == 0 and skipped_all:
        await _fail(
            client, session,
            "Extraction produced no usable files",
            "; ".join(skipped_all[:5]),
        )
        return

    await _set_status(client, session, views.summary(sent, total_files, skipped_all, failures))
    forget_pacing(session.user_id)
    forget_pacing(send_chat)
    sessions.end(session.user_id)


def _collect_files(out_dir: Path) -> tuple[list[Path], list[str], int]:
    """Walk the output tree, separating usable files from unusable ones.

    A file that fails its check is skipped and named in the summary. It used to
    abort the whole batch and delete every sibling, throwing away good files
    because of one bad one.
    """
    files: list[Path] = []
    skipped: list[str] = []
    total = 0
    for root, _, names in os.walk(out_dir):
        for name in sorted(names):
            path = Path(root) / name
            problem = check_file(path)
            if problem:
                skipped.append(f"{name} ({problem})")
                continue
            total += path.stat().st_size
            files.append(path)
    return files, skipped, total

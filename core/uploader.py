"""Send extracted files back to the user, paced to survive Telegram's limits."""
import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from pyrogram import Client, enums
from pyrogram.errors import FloodWait

from config import (
    AUDIO_EXTENSIONS,
    MAX_FILE_SIZE,
    MAX_FLOOD_WAIT,
    STREAMABLE_VIDEO_EXTENSIONS,
    UPLOAD_INTERVAL,
)
from core.splitter import SplitError, split_file

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], object]

# Time of the last send per chat, so a long extraction can't burst.
_last_send: dict[int, float] = {}
# When a FloodWait is in force, nothing may be sent to that chat until then.
_cooldown_until: dict[int, float] = {}


async def _pace(chat_id: int) -> None:
    """Hold until this chat is allowed another message."""
    now = time.monotonic()
    earliest = max(
        _last_send.get(chat_id, 0.0) + UPLOAD_INTERVAL,
        _cooldown_until.get(chat_id, 0.0),
    )
    if earliest > now:
        await asyncio.sleep(earliest - now)
    _last_send[chat_id] = time.monotonic()


def forget(chat_id: int) -> None:
    """Drop pacing state once a chat's batch is done."""
    _last_send.pop(chat_id, None)
    _cooldown_until.pop(chat_id, None)


async def send_file(
    client: Client,
    chat_id: int,
    path: Path,
    caption: str = "",
    progress: Optional[ProgressCallback] = None,
) -> Optional[str]:
    """Upload `path`, splitting if needed. None on success, else an error message."""
    try:
        # Splitting is CPU- and disk-bound; off the event loop it goes, or the
        # client stops answering pings and Telegram drops the connection.
        parts = await asyncio.to_thread(split_file, path, MAX_FILE_SIZE)
    except SplitError as exc:
        return str(exc)
    except OSError as exc:
        return f"Could not read file: {exc}"

    total = len(parts)
    for index, part in enumerate(parts, start=1):
        part_caption = caption if total == 1 else f"{caption}\n📦 Part {index}/{total}".strip()
        error = await _send_one(client, chat_id, part, part_caption, progress)
        if error:
            return error
    return None


async def _send_one(
    client: Client,
    chat_id: int,
    path: Path,
    caption: str,
    progress: Optional[ProgressCallback],
) -> Optional[str]:
    suffix = path.suffix.lower()
    # Captions are built as HTML by the view layer.
    kwargs = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": enums.ParseMode.HTML,
        "progress": progress,
    }
    # Telegram sometimes rejects a video/audio send (a 400 about the file
    # itself); the same bytes go out fine as a plain document, so fall back
    # once before burning real retries.
    force_document = False

    for attempt in range(4):
        await _pace(chat_id)
        try:
            if not force_document and suffix in STREAMABLE_VIDEO_EXTENSIONS:
                await client.send_video(video=str(path), supports_streaming=True, **kwargs)
            elif not force_document and suffix in AUDIO_EXTENSIONS:
                await client.send_audio(audio=str(path), **kwargs)
            else:
                await client.send_document(document=str(path), force_document=True, **kwargs)
            return None

        except FloodWait as exc:
            wait = int(getattr(exc, "value", 0) or 0)
            if wait > MAX_FLOOD_WAIT:
                return f"Telegram asked for a {wait}s pause — too long to wait out"
            # Block every later send to this chat too, not just the retry.
            _cooldown_until[chat_id] = time.monotonic() + wait + 1
            logger.info("FloodWait %ss on %s (attempt %d)", wait, path.name, attempt + 1)
            if attempt == 3:
                return f"Rate limited by Telegram after {attempt + 1} attempts"

        except Exception as exc:  # noqa: BLE001 - retried with backoff below
            logger.error("Upload failed for %s (attempt %d): %s", path.name, attempt + 1, exc)
            if not force_document and (
                suffix in STREAMABLE_VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS
            ):
                force_document = True
                continue
            if attempt == 3:
                return f"{type(exc).__name__}: {exc}"
            # Transient network errors usually clear within seconds; mirror
            # bots ride them out with exponential backoff instead of giving up.
            await asyncio.sleep(min(2 ** attempt, 8))

    return "Upload failed after retries"

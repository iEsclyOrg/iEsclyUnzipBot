"""Bot API 10.1+ rich messages, with an automatic fallback to plain formatting.

Uploads stay on MTProto (Kurigram) because rich-message media only accepts
http(s) URLs, not uploaded files. Only the UI text travels this path, over the
HTTP Bot API, which is where `sendRichMessage` and `editMessageText`'s
`rich_message` parameter live. Bot API and MTProto share one message-id space,
so a message sent here can be edited here later.

Two things about this API are undocumented in the public reference: whether
`reply_markup` may accompany `rich_message`, and the exact error shape when it
may not. Rather than guess, the first failure of each operation disables rich
mode for the process and the caller falls back to plain HTML. That keeps the bot
working if the assumption is wrong.
"""
import html
import logging
from typing import Optional

import aiohttp

from config import BOT_TOKEN, RICH_UI

logger = logging.getLogger(__name__)

# Limits reported by builders targeting Bot API 10.2. Treated as guard rails:
# exceeding them is a client-side error, so the text is trimmed instead.
MAX_RICH_CHARS = 32768
MAX_RICH_BLOCKS = 500

_API = "https://api.telegram.org/bot{token}/{method}"

_session: Optional[aiohttp.ClientSession] = None
# None = untested, True = works, False = fall back permanently.
_supported: Optional[bool] = None if RICH_UI else False


def escape(text: str) -> str:
    """Escape text that goes inside rich HTML. Filenames are user-controlled."""
    return html.escape(str(text), quote=False)


def enabled() -> bool:
    return _supported is not False


async def _post(method: str, payload: dict) -> Optional[dict]:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
        )
    url = _API.format(token=BOT_TOKEN, method=method)
    async with _session.post(url, json=payload) as response:
        body = await response.json(content_type=None)
    if not body.get("ok"):
        raise RuntimeError(f"{body.get('error_code')}: {body.get('description')}")
    return body.get("result")


def _disable(method: str, error: Exception) -> None:
    global _supported
    if _supported is not False:
        logger.warning("Disabling rich UI — %s failed: %s", method, error)
        _supported = False


def _trim(rich_html: str) -> str:
    if len(rich_html) <= MAX_RICH_CHARS:
        return rich_html
    logger.warning("Rich message too long (%d chars); trimming", len(rich_html))
    return rich_html[:MAX_RICH_CHARS - 16] + "\n<p>…</p>"


async def send(chat_id: int, rich_html: str, reply_markup: Optional[dict] = None) -> Optional[int]:
    """Send a rich message. Returns its message_id, or None to use the fallback."""
    global _supported
    if _supported is False:
        return None
    payload: dict = {"chat_id": chat_id, "rich_message": {"html": _trim(rich_html)}}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        result = await _post("sendRichMessage", payload)
    except Exception as exc:  # noqa: BLE001 - any failure means "use plain text"
        _disable("sendRichMessage", exc)
        return None
    _supported = True
    return (result or {}).get("message_id")


async def edit(
    chat_id: int,
    message_id: int,
    rich_html: str,
    reply_markup: Optional[dict] = None,
) -> bool:
    """Edit a message's rich content. False means the caller should fall back."""
    global _supported
    if _supported is False:
        return False
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"html": _trim(rich_html)},
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        await _post("editMessageText", payload)
    except Exception as exc:  # noqa: BLE001
        if "not modified" in str(exc).lower():
            return True
        _disable("editMessageText(rich_message)", exc)
        return False
    _supported = True
    return True


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None

"""Keyboard definitions, kept independent of transport.

The same keyboard has to reach Telegram two ways: as Pyrogram objects over
MTProto, and as Bot API JSON when a rich message carries it. Rows are therefore
built as plain (label, callback_data) pairs and adapted at the edge.
"""
from typing import Optional

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils.callback_data import make_callback
from utils.helpers import format_size

# A keyboard is a list of rows; a row is a list of (label, callback_data).
Row = list[tuple[str, str]]
Keyboard = list[Row]

NOOP = "noop"


def to_pyrogram(keyboard: Optional[Keyboard]) -> Optional[InlineKeyboardMarkup]:
    if not keyboard:
        return None
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in keyboard
    ])


def to_bot_api(keyboard: Optional[Keyboard]) -> Optional[dict]:
    if not keyboard:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in keyboard
        ]
    }


def action_menu() -> Keyboard:
    return [
        [("📦 Extract Archive", make_callback("act", "extract"))],
        [("❌ Cancel", make_callback("act", "cancel"))],
    ]


def password_menu() -> Keyboard:
    return [
        [("🔑 Yes, it has a password", make_callback("pwd", "yes"))],
        [("🚫 No password", make_callback("pwd", "no"))],
        [("❌ Cancel", make_callback("act", "cancel"))],
    ]


def file_list_keyboard(
    items: list[dict],
    page: int,
    page_size: int,
    selected: set[int],
) -> Keyboard:
    """Paginated file list. `selected` holds indices into `items`."""
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    start = page * page_size

    rows: Keyboard = []
    for offset, item in enumerate(items[start:start + page_size]):
        index = start + offset
        if item["is_dir"]:
            # Directories are created implicitly by their contents; nothing to
            # toggle, so they render as inert labels.
            rows.append([(f"📁 {item['name']}"[:64], NOOP)])
        else:
            mark = "☑️" if index in selected else "⬜"
            label = f"{mark} {item['name']} ({format_size(item['size'])})"
            rows.append([(label[:64], make_callback("sel", str(index)))])

    nav: Row = []
    if page > 0:
        nav.append(("⬅️ Prev", make_callback("page", str(page - 1))))
    nav.append((f"{page + 1}/{total_pages}", NOOP))
    if page < total_pages - 1:
        nav.append(("Next ➡️", make_callback("page", str(page + 1))))
    rows.append(nav)

    rows.append([
        ("☑️ Select All", make_callback("sel", "all")),
        ("⬜ Clear", make_callback("sel", "none")),
    ])
    rows.append([
        ("📦 Extract All", make_callback("ext", "all")),
        (f"✅ Extract Selected ({len(selected)})", make_callback("ext", "selected")),
    ])
    rows.append([("❌ Cancel", make_callback("act", "cancel"))])
    return rows

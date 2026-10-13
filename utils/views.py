"""Screen text, in a rich variant and a plain fallback.

Every screen returns (rich_html, plain_html). Rich uses Bot API 10.1 blocks
(headings, tables, block quotations, lists, dividers); plain sticks to the
long-standing entity tags that MTProto accepts. Both escape filenames, which
come from the archive and may contain angle brackets.

Note on selection: rich messages have no checkbox block — checklists are an
unrelated Bot API feature — so selection state is shown by the inline keyboard
and mirrored in the summary text here.
"""
from typing import Optional

from config import BANNER_URL
from core.rich import escape
from utils.helpers import format_size


def welcome() -> tuple[str, str]:
    rich = (
        "<figure>"
        f"<img src='{BANNER_URL}'>"
        "<figcaption>UnzipBot</figcaption>"
        "</figure>"
        "<h3>UnzipBot</h3>"
        "<p>Send an archive. Pick what you want. Get it back.</p>"
        "<p><b>Formats</b> — <code>zip</code> <code>rar</code> <code>7z</code> "
        "<code>tar</code> <code>gz</code> <code>bz2</code> <code>xz</code></p>"
        "<p><b>Split sets</b> — <code>name.zip.001</code>, "
        "<code>name.part1.rar</code></p>"
        "<hr>"
        "<p><code>/help</code> for limits and commands.</p>"
    )
    plain = (
        "<b>UnzipBot</b>\n\n"
        "Send an archive. Pick what you want. Get it back.\n\n"
        "<b>Formats</b> — zip, rar, 7z, tar, gz, bz2, xz\n"
        "<b>Split sets</b> — <code>name.zip.001</code>, <code>name.part1.rar</code>\n\n"
        "<code>/help</code> for limits and commands."
    )
    return rich, plain


def help_screen(max_archive: int, max_extracted: int, max_files: int) -> tuple[str, str]:
    rich = (
        "<h3>Help</h3>"
        "<ol>"
        "<li>Send an archive as a document</li>"
        "<li>Tap <b>Extract Archive</b></li>"
        "<li>Enter the password if asked</li>"
        "<li>Tap files to select, or <b>Extract All</b></li>"
        "</ol>"
        "<h4>Limits</h4>"
        "<table bordered=''><tbody>"
        "<tr><th>Archive</th><th>Extracted</th><th>Files per round</th></tr>"
        f"<tr><td>{format_size(max_archive)}</td>"
        f"<td>{format_size(max_extracted)}</td><td>{max_files}</td></tr>"
        "</tbody></table>"
        "<blockquote>Select as many files as you like — big selections are sent "
        "in rounds automatically, paced to stay inside Telegram's rate limits."
        "</blockquote>"
        "<hr>"
        "<p><code>/start</code> — banner<br>"
        "<code>/cancel</code> — drop the current archive</p>"
    )
    plain = (
        "<b>Help</b>\n\n"
        "1. Send an archive as a document\n"
        "2. Tap <b>Extract Archive</b>\n"
        "3. Enter the password if asked\n"
        "4. Tap files to select, or <b>Extract All</b>\n\n"
        "<b>Limits</b>\n"
        f"Archive {format_size(max_archive)} · "
        f"Extracted {format_size(max_extracted)} · {max_files} files per batch\n\n"
        "<code>/start</code> — banner\n"
        "<code>/cancel</code> — drop the current archive"
    )
    return rich, plain


def received(filename: str, size: int) -> tuple[str, str]:
    name = escape(filename)
    rich = (
        "<h4>📦 Archive received</h4>"
        f"<p><code>{name}</code><br>{format_size(size)}</p>"
        "<p>What would you like to do?</p>"
    )
    plain = (
        f"📦 Received <code>{name}</code>\n{format_size(size)}\n\n"
        "What would you like to do?"
    )
    return rich, plain


def password_ask() -> tuple[str, str]:
    return (
        "<h4>🔒 Password protected</h4><p>Does this archive have a password?</p>",
        "🔒 <b>Password protected</b>\n\nDoes this archive have a password?",
    )


def password_prompt() -> tuple[str, str]:
    return (
        "<h4>🔑 Send the password</h4>"
        "<blockquote>I'll delete your message straight away and keep the password "
        "in memory only — it is never written to disk.</blockquote>",
        "🔑 <b>Send me the password</b>\n\n"
        "<i>I'll delete your message and keep the password in memory only.</i>",
    )


def opening() -> tuple[str, str]:
    return "<h4>🔍 Opening the archive…</h4>", "🔍 Opening the archive…"


def extracting(count: int, round_no: int = 1, rounds: int = 1) -> tuple[str, str]:
    suffix = f" — round {round_no}/{rounds}" if rounds > 1 else ""
    return (
        f"<h4>⏳ Extracting {count} file(s){suffix}…</h4>",
        f"⏳ Extracting {count} file(s){suffix}…",
    )


def cancelled() -> tuple[str, str]:
    return (
        "<h4>❌ Cancelled</h4><p>Send a new archive to start again.</p>",
        "❌ <b>Cancelled</b>\n\nSend a new archive to start again.",
    )


BAR_WIDTH = 14


def _bar(percent: int) -> str:
    filled = round(percent / 100 * BAR_WIDTH)
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _rate(speed: float) -> str:
    return f"{format_size(speed)}/s" if speed > 0 else "—"


def _eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds > 86400:
        return "—"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def transfer(
    label: str,
    current: int,
    total: int,
    speed: float = 0.0,
    eta: Optional[float] = None,
    filename: str = "",
) -> tuple[str, str]:
    """Progress screen for a download or upload."""
    percent = int(current * 100 / total) if total else 0
    bar = _bar(percent)
    moved = f"{format_size(current)} / {format_size(total)}"
    name = escape(filename)

    rich = [f"<h4>{label}</h4>"]
    if filename:
        rich.append(f"<p><code>{name}</code></p>")
    rich.append(f"<pre><code>{bar}  {percent}%</code></pre>")
    rich.append(
        "<table bordered=''><tbody>"
        "<tr><th>Moved</th><th>Speed</th><th>ETA</th></tr>"
        f"<tr><td>{moved}</td><td>{_rate(speed)}</td><td>{_eta(eta)}</td></tr>"
        "</tbody></table>"
    )

    plain = [label]
    if filename:
        plain.append(f"<code>{name}</code>")
    plain.append(f"<code>{bar}</code> {percent}%")
    plain.append(f"{moved} · {_rate(speed)} · ETA {_eta(eta)}")

    return "".join(rich), "\n".join(plain)


def batch(
    index: int,
    total: int,
    filename: str,
    sent: int,
    failed: int,
    round_no: int = 1,
    rounds: int = 1,
) -> tuple[str, str]:
    """Progress screen for a multi-file upload run."""
    percent = int((index - 1) * 100 / total) if total else 0
    bar = _bar(percent)
    name = escape(filename)
    tally = f"✅ {sent}" + (f" · ❌ {failed}" if failed else "")
    suffix = f" · round {round_no}/{rounds}" if rounds > 1 else ""

    rich = (
        f"<h4>📤 Sending file {index} of {total}</h4>"
        f"<p><code>{name}</code></p>"
        f"<pre><code>{bar}  {percent}%</code></pre>"
        f"<p>{tally}{suffix}</p>"
    )
    plain = (
        f"📤 <b>Sending {index}/{total}</b>{suffix}\n"
        f"<code>{name}</code>\n"
        f"<code>{bar}</code> {percent}%\n{tally}"
    )
    return rich, plain


def volumes_waiting(base: str, have: set[int], missing: list[int]) -> tuple[str, str]:
    """Prompt shown while a multi-volume set is still being collected."""
    name = escape(base)
    got = ", ".join(f"{n:03d}" for n in sorted(have))
    gaps = ", ".join(f"{n:03d}" for n in missing)

    rich = [
        "<h4>🧩 Multi-volume archive</h4>",
        f"<p>Collecting parts of <code>{name}</code></p>",
        f"<p><b>{len(have)}</b> part(s) received: <code>{got}</code></p>",
    ]
    plain = [
        "🧩 <b>Multi-volume archive</b>",
        f"Collecting parts of <code>{name}</code>",
        f"<b>{len(have)}</b> received: <code>{got}</code>",
    ]
    if missing:
        rich.append(f"<p>⚠️ Missing: <code>{gaps}</code></p>")
        plain.append(f"⚠️ Missing: <code>{gaps}</code>")
    rich.append(
        "<blockquote>Send the remaining parts, then tap <b>Combine &amp; Extract</b>."
        "</blockquote>"
    )
    plain.append("\nSend the remaining parts, then tap <b>Combine &amp; Extract</b>.")
    return "".join(rich), "\n".join(plain)


def file_list(
    total_files: int,
    total_size: int,
    page: int,
    total_pages: int,
    selected_count: int,
    selected_size: int,
) -> tuple[str, str]:
    selection = (
        f"{selected_count} selected · {format_size(selected_size)}"
        if selected_count
        else "Nothing selected yet"
    )
    rich = (
        "<h4>📁 Archive contents</h4>"
        "<table bordered=''>"
        "<tbody>"
        "<tr><th>Files</th><th>Size</th><th>Page</th></tr>"
        f"<tr><td>{total_files}</td><td>{format_size(total_size)}</td>"
        f"<td>{page}/{total_pages}</td></tr>"
        "</tbody>"
        "</table>"
        f"<p><b>{selection}</b></p>"
        "<blockquote>Tap a file to select or deselect it, then choose an "
        "action below.</blockquote>"
    )
    plain = (
        "<b>📁 Archive contents</b>\n\n"
        f"{total_files} file(s) · {format_size(total_size)}\n"
        f"Page {page}/{total_pages}\n"
        f"<b>{selection}</b>\n\n"
        "Tap a file to select or deselect it, then choose an action."
    )
    return rich, plain


def summary(
    sent: int,
    total: int,
    skipped: list[str],
    failures: list[str],
    note: str = "",
) -> tuple[str, str]:
    icon = "✅" if sent == total else "⚠️"
    rich = [f"<h4>{icon} Sent {sent} of {total} file(s)</h4>"]
    plain = [f"{icon} <b>Sent {sent}/{total} file(s)</b>"]

    for title, entries in (("Skipped", skipped), ("Failed", failures)):
        if not entries:
            continue
        shown = entries[:10]
        rich.append(f"<p><b>{title} ({len(entries)})</b></p><ul>")
        rich.extend(f"<li>{escape(entry)}</li>" for entry in shown)
        if len(entries) > len(shown):
            rich.append(f"<li>…and {len(entries) - len(shown)} more</li>")
        rich.append("</ul>")

        plain.append(f"\n<b>{title} ({len(entries)})</b>")
        plain.extend(f"• {escape(entry)}" for entry in shown)
        if len(entries) > len(shown):
            plain.append(f"• …and {len(entries) - len(shown)} more")

    if note:
        rich.append(f"<blockquote>{escape(note)}</blockquote>")
        plain.append(f"\n{escape(note)}")

    rich.append("<hr><p>Send another archive whenever you're ready.</p>")
    plain.append("\nSend another archive whenever you're ready.")
    return "".join(rich), "\n".join(plain)


def error(title: str, detail: str = "") -> tuple[str, str]:
    safe = escape(detail)
    rich = f"<h4>❌ {escape(title)}</h4>"
    plain = f"❌ <b>{escape(title)}</b>"
    if detail:
        rich += f"<pre><code>{safe}</code></pre>"
        plain += f"\n<code>{safe}</code>"
    return rich, plain


# --------------------------------------------------------------------------- #
# Admin panel
# --------------------------------------------------------------------------- #

def admin_panel(
    stats: Optional[dict],
    active_sessions: int = 0,
    temp_bytes: int = 0,
) -> tuple[str, str]:
    if stats:
        rich_body = (
            "<table bordered=''><tbody>"
            "<tr><th>Users</th><th>Banned</th><th>Uploads</th></tr>"
            f"<tr><td>{stats['users']}</td><td>{stats['banned']}</td>"
            f"<td>{stats['uploads']}</td></tr>"
            "<tr><th>Sent</th><th>Failed</th><th>Transferred</th></tr>"
            f"<tr><td>{stats['success']}</td><td>{stats['failed']}</td>"
            f"<td>{format_size(stats['bytes'])}</td></tr>"
            "</tbody></table>"
        )
        plain_body = (
            f"Users {stats['users']} · Banned {stats['banned']} · "
            f"Uploads {stats['uploads']}\n"
            f"Sent {stats['success']} · Failed {stats['failed']} · "
            f"{format_size(stats['bytes'])}"
        )
    else:
        rich_body = "<p>⚠️ Database not configured — statistics unavailable.</p>"
        plain_body = "⚠️ Database not configured — statistics unavailable."

    rich = (
        "<h4>🛠 Admin panel</h4>"
        + rich_body
        + f"<p>Active sessions: <b>{active_sessions}</b> · "
        f"Temp files: <b>{format_size(temp_bytes)}</b></p>"
    )
    plain = (
        "🛠 <b>Admin panel</b>\n"
        + plain_body
        + f"\nActive sessions: {active_sessions} · Temp files: {format_size(temp_bytes)}"
    )
    return rich, plain


def admin_users(page: int, total_pages: int, total_users: int) -> tuple[str, str]:
    rich = (
        "<h4>👥 Users</h4>"
        f"<p><b>{total_users}</b> tracked · page {page}/{total_pages}</p>"
        "<blockquote>Tap a user to ban or unban them.</blockquote>"
    )
    plain = (
        "👥 <b>Users</b>\n"
        f"{total_users} tracked · page {page}/{total_pages}\n\n"
        "Tap a user to ban or unban them."
    )
    return rich, plain


def admin_logs(logs: list[dict]) -> tuple[str, str]:
    if not logs:
        return (
            "<h4>📜 Recent uploads</h4><p>No uploads logged yet.</p>",
            "📜 <b>Recent uploads</b>\nNo uploads logged yet.",
        )
    rich = ["<h4>📜 Recent uploads</h4><ul>"]
    plain = ["📜 <b>Recent uploads</b>"]
    for log in logs:
        mark = "✅" if log.get("status") == "success" else "❌"
        name = escape(str(log.get("file", "?")))
        rich.append(f"<li>{mark} <code>{name}</code></li>")
        plain.append(f"{mark} {name}")
    rich.append("</ul>")
    return "".join(rich), "\n".join(plain)


def admin_broadcast_prompt() -> tuple[str, str]:
    return (
        "<h4>📢 Broadcast</h4>"
        "<p>Send the message you want delivered to every user.</p>"
        "<blockquote>Your next message is sent verbatim (no formatting) to all "
        "tracked users. Tap Cancel to abort.</blockquote>",
        "📢 <b>Broadcast</b>\n\nSend the message to deliver to every user.\n"
        "Your next message is sent verbatim. Tap Cancel to abort.",
    )


def admin_broadcast_progress(done: int, total: int, sent: int, failed: int) -> tuple[str, str]:
    return (
        f"<h4>📢 Broadcasting…</h4><p>{done}/{total}</p>"
        f"<p>✅ {sent} · ❌ {failed}</p>",
        f"📢 Broadcasting… {done}/{total}\n✅ {sent} · ❌ {failed}",
    )


def admin_broadcast_done(total: int, sent: int, failed: int) -> tuple[str, str]:
    return (
        f"<h4>✅ Broadcast finished</h4><p>{sent}/{total} sent · {failed} failed</p>",
        f"✅ <b>Broadcast finished</b>\n{sent}/{total} sent · {failed} failed",
    )


def admin_denied() -> tuple[str, str]:
    return (
        "<h4>⛔ Admins only</h4>",
        "⛔ <b>Admins only</b>",
    )

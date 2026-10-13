"""Checks for the progress-callback and rate-limiting fixes."""
import asyncio
import inspect
import time
from pathlib import Path
from unittest.mock import patch

import core.uploader as uploader
from core.bot_client import _transfer_reporter
from core.session import Session
from utils import views
from utils.keyboards import file_list_keyboard, to_bot_api, to_pyrogram

# --- 1. The progress callback must look like a coroutine function ----------- #
# Pyrogram branches on inspect.iscoroutinefunction(progress). False sends it to
# run_in_executor on a worker thread, which drops the returned coroutine.
session = Session(
    user_id=1, archive_path=Path("x.zip"), archive_type="zip", status_message_id=1
)
edits: list = []


async def fake_set_status(client, sess, screen, keyboard=None, throttle=False):
    edits.append((screen[1], throttle))


with patch("core.bot_client._set_status", fake_set_status):
    reporter = _transfer_reporter(None, session, "dl", "x.zip")
    assert inspect.iscoroutinefunction(reporter), "pyrogram would run this in a thread!"
    asyncio.run(reporter(50, 100))
assert edits and "50%" in edits[0][0], edits
assert edits[0][1] is True, "progress updates must be throttleable"
print("progress callback OK ->", edits[0][0].replace("\n", " | "))

# --- 2. Pacing spaces out sends, and FloodWait sets a shared cooldown ------- #
uploader.forget(42)
with patch.object(uploader, "UPLOAD_INTERVAL", 0.05):
    async def paced():
        start = time.monotonic()
        for _ in range(4):
            await uploader._pace(42)
        return time.monotonic() - start

    elapsed = asyncio.run(paced())
assert elapsed >= 0.15, f"4 sends should take >=3 intervals, took {elapsed:.3f}s"
print(f"pacing OK -> 4 sends took {elapsed:.3f}s at 0.05s interval")

uploader.forget(42)
uploader._cooldown_until[42] = time.monotonic() + 0.2
start = time.monotonic()
asyncio.run(uploader._pace(42))
waited = time.monotonic() - start
assert waited >= 0.18, f"cooldown not honoured, waited {waited:.3f}s"
print(f"floodwait cooldown OK -> waited {waited:.3f}s")
uploader.forget(42)
assert 42 not in uploader._cooldown_until and 42 not in uploader._last_send
print("pacing state cleanup OK")

# --- 3. Keyboard adapters agree, and dirs stay inert ------------------------ #
items = [
    {"name": "dir", "raw": "dir/", "size": 0, "is_dir": True},
    {"name": "a.txt", "raw": "a.txt", "size": 10, "is_dir": False},
    {"name": "b.txt", "raw": "b.txt", "size": 20, "is_dir": False},
]
kb = file_list_keyboard(items, 0, 10, {1})
flat = [b for row in kb for b in row]
assert ("📁 dir", "noop") in flat, flat
assert any(lbl.startswith("☑️ a.txt") for lbl, _ in flat), flat
assert any(lbl.startswith("⬜ b.txt") for lbl, _ in flat), flat
assert any(lbl == "✅ Extract Selected (1)" for lbl, _ in flat), flat

api = to_bot_api(kb)
pyro = to_pyrogram(kb)
assert len(api["inline_keyboard"]) == len(kb) == len(pyro.inline_keyboard)
assert api["inline_keyboard"][0][0]["callback_data"] == "noop"
print("keyboard adapters OK ->", len(kb), "rows both transports")

# --- 4. Filenames are escaped, not injected -------------------------------- #
rich_html, plain = views.summary(1, 2, ["<script>x</script> (empty)"], [])
assert "<script>" not in rich_html and "&lt;script&gt;" in rich_html, rich_html
assert "<script>" not in plain, plain
r2, p2 = views.received("<b>evil</b>.zip", 100)
assert "&lt;b&gt;evil&lt;/b&gt;.zip" in r2, r2
print("html escaping OK")

# --- 5. Oversized rich payloads are trimmed, not rejected ------------------ #
import core.rich as rich_mod
assert len(rich_mod._trim("x" * 40000)) <= rich_mod.MAX_RICH_CHARS
assert rich_mod._trim("short") == "short"
print("rich trim OK")

# --- 6. Empty-file diagnostics distinguish archive-empty from data loss ----- #
import tempfile
import shutil
from core.bot_client import _collect_files, _empty_diagnostic

work = Path(tempfile.mkdtemp())
(work / "full.mp4").write_bytes(b"DATA" * 100)
(work / "blank.mp4").write_bytes(b"")
files, skipped, total = _collect_files(work)
assert [p.name for p in files] == ["full.mp4"], files
assert skipped and skipped[0][0].name == "blank.mp4" and skipped[0][1] == "empty", skipped
assert total == 400, total

archive_empty = _empty_diagnostic(3, 0)
assert "0 bytes inside the archive" in archive_empty, archive_empty
data_loss = _empty_diagnostic(3, 1234)
assert "even though the archive lists" in data_loss, data_loss
assert _empty_diagnostic(0, 0) == ""
print("empty-file diagnostic OK ->", archive_empty)

# The note must reach both the rich and the plain summary.
rich_note, plain_note = views.summary(1, 4, ["blank.mp4 (empty)"], [], note=data_loss)
assert data_loss in plain_note and "please report" in rich_note, (rich_note, plain_note)
print("summary note rendering OK")

shutil.rmtree(work)

print("ALL FIX TESTS PASSED")

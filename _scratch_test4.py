"""Checks for the admin panel: config, views, keyboards, DB guards, routing."""
import asyncio
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config
from config import is_admin
from core import admin, db
from utils import views
from utils.callback_data import parse_callback
from utils.helpers import dir_size
from utils.keyboards import (
    USERS_PER_PAGE,
    admin_back_menu,
    admin_broadcast_cancel,
    admin_menu,
    admin_users_keyboard,
)

# --- 1. ADMIN_IDS parsing and membership ----------------------------------- #
os.environ["TMP_ADMINS"] = "1, 2 ;3 ,x,-5"
assert config._get_id_list("TMP_ADMINS") == frozenset({1, 2, 3, -5})
assert config._get_id_list("TMP_NONE_MISSING") == frozenset()
with patch.object(config, "ADMIN_IDS", frozenset({7, 9})):
    assert is_admin(7) and is_admin(9) and not is_admin(8)
print("admin id parsing OK")

# --- 2. Admin views render in both variants -------------------------------- #
stats = {"users": 12, "banned": 2, "uploads": 40, "success": 38, "failed": 2,
         "bytes": 3 * 1024 * 1024}
rich, plain = views.admin_panel(stats, active_sessions=3, temp_bytes=5 * 1024 * 1024)
assert "12" in plain and "38" in plain and "3.0 MB" in plain, plain
assert "5.0 MB" in plain and "Active sessions: 3" in plain, plain
assert "<table" in rich and "</table>" in rich, rich

rich, plain = views.admin_panel(None)
assert "Database not configured" in plain, plain
print("admin panel view OK ->", plain.replace("\n", " | "))

rich, plain = views.admin_users(1, 3, 20)
assert "20" in plain and "1/3" in plain, plain

rich, plain = views.admin_logs([
    {"file": "a<b>.txt", "status": "success"},
    {"file": "b.zip", "status": "failed"},
])
assert "&lt;b&gt;" in rich and "a<b>.txt" in plain, rich
assert "✅" in plain and "❌" in plain, plain
rich, plain = views.admin_logs([])
assert "No uploads" in plain, plain

rich, plain = views.admin_broadcast_prompt()
assert "Broadcast" in plain and "Cancel" in plain, plain
rich, plain = views.admin_broadcast_done(10, 8, 2)
assert "8/10" in plain and "2 failed" in plain, plain
rich, plain = views.admin_denied()
assert "Admins only" in plain, plain
print("admin sub-views OK")

# --- 3. Keyboards stay within Telegram's 64-byte callback cap -------------- #
def check(keyboard):
    for row in keyboard:
        for label, data in row:
            assert len(label.encode()) <= 64, (label, len(label.encode()))
            assert len(data.encode()) <= 64, (data, len(data.encode()))

check(admin_menu())
check(admin_back_menu())
check(admin_broadcast_cancel())
users = [{"_id": 111, "first_name": "Ann", "banned": False},
         {"_id": 222, "username": "bob", "banned": True}]
kb = admin_users_keyboard(users, page=1, total_pages=3)
check(kb)
flat = [data for row in kb for _, data in row]
assert "adm:toggle:111:1" in flat and "adm:toggle:222:1" in flat, flat
assert "adm:users:0" in flat and "adm:users:2" in flat, flat
print("admin keyboards OK")

# --- 4. Callback routing for adm: values ----------------------------------- #
action, value = parse_callback("adm:toggle:111:2")
assert action == "adm" and value == "toggle:111:2", (action, value)
sub, _, arg = value.partition(":")
assert sub == "toggle" and arg == "111:2", (sub, arg)
assert admin._as_int("7", 0) == 7 and admin._as_int("x", 5) == 5
print("callback routing OK")

# --- 5. Degraded database never raises ------------------------------------- #
db._disabled = True
assert asyncio.run(db.get_stats()) is None
assert asyncio.run(db.list_users(0, 8)) == ([], 0)
assert asyncio.run(db.set_banned(5, True)) is False
assert asyncio.run(db.is_banned(5)) is False
assert asyncio.run(db.all_user_ids()) == []
assert asyncio.run(db.recent_logs(5)) == []
print("degraded DB OK")

# --- 6. dir_size sums files, missing paths are zero ------------------------ #
tmp = Path("test_tmp4")
shutil.rmtree(tmp, ignore_errors=True)
(tmp / "sub").mkdir(parents=True)
(tmp / "a.bin").write_bytes(b"x" * 1000)
(tmp / "sub" / "b.bin").write_bytes(b"y" * 500)
assert dir_size(tmp) == 1500, dir_size(tmp)
assert dir_size(tmp / "nope") == 0
shutil.rmtree(tmp, ignore_errors=True)
print("dir_size OK")

# --- 7. Banned users are blocked by the guard ----------------------------- #
import core.bot_client as bc


async def _run():
    msg = SimpleNamespace(
        from_user=SimpleNamespace(id=42, username="u", first_name="U"),
        reply_text=_record_reply,
    )
    with patch.object(bc, "track_user", _noop), patch.object(bc, "is_banned", _yes):
        assert await bc._guard_message(msg) is True
    with patch.object(bc, "track_user", _noop), patch.object(bc, "is_banned", _no):
        assert await bc._guard_message(msg) is False
    assert replies and "banned" in replies[-1].lower(), replies


replies = []


async def _record_reply(text, **kwargs):
    replies.append(text)


async def _noop(*a, **k):
    return None


async def _yes(*a, **k):
    return True


async def _no(*a, **k):
    return False


asyncio.run(_run())
print("ban guard OK")

# --- 8. Broadcast bodies are sent verbatim, without parse_mode ------------- #
sent = []


class FakeClient:
    async def send_message(self, chat_id, text, **kwargs):
        sent.append((chat_id, text, kwargs))
        return SimpleNamespace(id=1)


async def fake_rich_send(chat_id, html, markup=None):
    return 999


async def fake_rich_edit(chat_id, mid, html, markup=None):
    return True


async def _broadcast_run():
    with patch.object(db, "all_user_ids", _targets), \
         patch.object(admin.rich, "send", fake_rich_send), \
         patch.object(admin.rich, "edit", fake_rich_edit):
        await admin._run_broadcast(FakeClient(), 42, "<b>hi</b>")


async def _targets():
    return [10, 20, 30]


asyncio.run(_broadcast_run())
assert [c[0] for c in sent] == [10, 20, 30], sent
assert all(c[1] == "<b>hi</b>" and "parse_mode" not in c[2] for c in sent), sent
print("broadcast verbatim OK ->", [c[0] for c in sent])

# --- 9. handle_text only consumes an armed admin --------------------------- #
async def fake_broadcast(client, admin_id, body):
    return None


async def _handle_run():
    admin._awaiting_broadcast.clear()
    admin._awaiting_broadcast.add(42)
    msg = SimpleNamespace(from_user=SimpleNamespace(id=42), text="hello all")
    with patch.object(admin, "_run_broadcast", fake_broadcast):
        assert await admin.handle_text(None, msg) is True
        assert 42 not in admin._awaiting_broadcast, "should disarm after handling"
        assert await admin.handle_text(None, msg) is False


asyncio.run(_handle_run())
print("broadcast arming OK")

print("ALL ADMIN TESTS PASSED")

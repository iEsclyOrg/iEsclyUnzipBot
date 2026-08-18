"""Checks for multi-volume support, speed knobs and the progress UI."""
import asyncio
import inspect
import shutil
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.volumes import assemble, detect_volume, missing_volumes
from core.splitter import _split_binary
from utils import views

# --- 1. Volume name detection ---------------------------------------------- #
cases = [
    ("movie.zip.001", ("movie.zip", 1, "concat")),
    ("movie.zip.002", ("movie.zip", 2, "concat")),
    ("data.7z.0012", ("data.7z", 12, "concat")),
    ("big.tar.gz.003", ("big.tar.gz", 3, "concat")),
    ("film.part1.rar", ("film.rar", 1, "rar")),
    ("film.part02.rar", ("film.rar", 2, "rar")),
    ("film.r00", ("film.rar", 2, "rar")),
    ("film.r01", ("film.rar", 3, "rar")),
    ("plain.zip", None),
    ("notes.txt", None),
]
for name, want in cases:
    got = detect_volume(name)
    got = (got.base, got.number, got.kind) if got else None
    assert got == want, (name, got, want)
print("volume detection OK")

assert missing_volumes({1, 2, 3}) == []
assert missing_volumes({1, 3, 5}) == [2, 4]
assert missing_volumes(set()) == []
print("gap detection OK ->", missing_volumes({1, 3, 5}))

# --- 2. Split then reassemble must round-trip exactly ---------------------- #
tmp = Path("test_tmp2")
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()

source = tmp / "payload.zip"
with zipfile.ZipFile(source, "w") as zf:
    for i in range(40):
        zf.writestr(f"file{i}.txt", f"content number {i} " * 200)
original = source.read_bytes()

parts = _split_binary(source, max_size=4096)
assert len(parts) > 3, len(parts)
# Names must match the .001 scheme the bot itself now recognises.
for part in parts:
    info = detect_volume(part.name)
    assert info is not None, part.name
    assert info.base == "payload.zip", info
print(f"split naming OK -> {len(parts)} parts, e.g. {parts[0].name}")

volumes = {detect_volume(p.name).number: p for p in parts}
rejoined = assemble(volumes, tmp / "rejoined.zip")
assert rejoined.read_bytes() == original, "round-trip corrupted the file"
with zipfile.ZipFile(rejoined) as zf:
    assert zf.testzip() is None and len(zf.namelist()) == 40
assert all(not p.exists() for p in parts), "slices should be freed after joining"
print("assemble round-trip OK -> byte-identical, zip still valid")

shutil.rmtree(tmp, ignore_errors=True)

# --- 3. Progress UI shows bar, speed and ETA ------------------------------- #
rich_html, plain = views.transfer("📥 Downloading", 512 * 1024 * 1024, 1024 * 1024 * 1024,
                                  speed=8 * 1024 * 1024, eta=64, filename="movie.zip")
assert "50%" in plain and "8.0 MB/s" in plain and "1m 04s" in plain, plain
assert "<pre><code>" in rich_html and "<table" in rich_html, rich_html
assert "█" * 7 in plain, plain
print("transfer UI OK ->", plain.replace("\n", " | "))

r2, p2 = views.batch(3, 10, "clip.mkv", sent=2, failed=0)
assert "3/10" in p2 and "✅ 2" in p2, p2
print("batch UI OK ->", p2.replace("\n", " | "))

r3, p3 = views.volumes_waiting("movie.zip", {1, 3}, [2])
assert "001, 003" in p3 and "Missing" in p3, p3
assert "&lt;" not in "movie.zip"
print("volume UI OK ->", p3.replace("\n", " | "))

# ETA formatting edges
assert views._eta(None) == "—" and views._eta(999999) == "—"
assert views._eta(45) == "45s" and views._eta(3725) == "1h 02m"
print("eta formatting OK")

# --- 4. Speed knobs are actually passed to the client --------------------- #
import core.bot_client as bc
from config import TRANSFER_CONCURRENCY

captured = {}


class FakeClient:
    def __init__(self, name, **kwargs):
        captured.update(kwargs)

    def on_message(self, *a, **k):
        return lambda f: f

    def on_callback_query(self, *a, **k):
        return lambda f: f


with patch.object(bc, "Client", FakeClient):
    bc.create_bot_client()
assert captured["max_concurrent_transmissions"] == TRANSFER_CONCURRENCY > 1, captured
print("speed knobs OK ->", {k: v for k, v in captured.items()
                            if k in ("max_concurrent_transmissions", "workers", "sleep_threshold")})

# --- 5. Progress callback is still a coroutine function ------------------- #
from core.session import Session

s = Session(user_id=1, archive_path=Path("x.zip"), archive_type="zip", status_message_id=1)
rep = bc._transfer_reporter(None, s, "dl", "x.zip")
assert inspect.iscoroutinefunction(rep), "pyrogram would run this on a thread!"
seen = []


async def fake(client, sess, screen, keyboard=None, throttle=False):
    seen.append(screen[1])


with patch.object(bc, "_set_status", fake):
    asyncio.run(rep(10, 100))
assert seen and "10%" in seen[0], seen
print("reporter still a coroutine fn OK")

print("ALL TESTS PASSED")

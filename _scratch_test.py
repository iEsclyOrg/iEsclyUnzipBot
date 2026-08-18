"""Throwaway checks for the archive pipeline."""
import io
import gzip
import shutil
import tarfile
import zipfile
from pathlib import Path

from core.extractor import (
    ArchiveError,
    detect_archive_type,
    extract,
    list_entries,
    needs_password,
    probe,
)

tmp = Path("test_tmp")
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()

for name, want in [
    ("a.tar.gz", "tar.gz"), ("a.tgz", "tgz"), ("a.zip", "zip"), ("a.7z", "7z"),
    ("a.txt.gz", "gz"), ("a.TAR.BZ2", "tar.bz2"), ("a.txt", None),
]:
    got = detect_archive_type(name)
    assert got == want, (name, got, want)
print("detect OK")

# zip holding a Windows-style backslash member: the old code listed it as
# "dir/nested.txt" and then extracted by that name, raising KeyError.
# ZipInfo normalises os.sep on construction, so the name is forced afterwards to
# mimic the third-party zip writers that actually emit these.
z = tmp / "win.zip"
with zipfile.ZipFile(z, "w") as zf:
    info = zipfile.ZipInfo("placeholder.txt")
    info.filename = "dir\\nested.txt"
    zf.writestr(info, "hello nested")
    zf.writestr("plain.txt", "hello plain")
    zf.writestr("sub/", "")
probe(z, "zip")
items = list_entries(z, "zip")
names = {i["name"] for i in items}
assert "dir/nested.txt" in names, names
# `raw` must be whatever the archive reports, untouched. zipfile rewrites os.sep
# when it builds ZipInfo, so on Windows this is already "/" while on Linux the
# backslash survives — extraction has to use `raw` either way or it KeyErrors.
entry = next(i for i in items if i["name"] == "dir/nested.txt")
with zipfile.ZipFile(z) as zf:
    assert entry["raw"] in zf.namelist(), (entry["raw"], zf.namelist())
out = tmp / "o1"
extract(z, out, "zip", members=[entry["raw"]])
assert (out / "dir" / "nested.txt").read_text() == "hello nested"
assert needs_password(z, "zip") is False
print("zip member/raw mapping + needs_password OK")

tg = tmp / "a.tar.gz"
(tmp / "src").mkdir()
(tmp / "src" / "one.txt").write_text("one")
(tmp / "src" / "two.txt").write_text("two")
with tarfile.open(tg, "w:gz") as tf:
    tf.add(tmp / "src", arcname="src")
probe(tg, "tar.gz")
files = [i for i in list_entries(tg, "tar.gz") if not i["is_dir"]]
assert len(files) == 2, files
extract(tg, tmp / "o2", "tar.gz", members=[files[0]["raw"]])
assert (tmp / "o2" / "src" / "one.txt").exists()
extract(tg, tmp / "o3", "tar.gz")
print("tar.gz OK")

# Plain single-file gzip: the old code fed this to tarfile.open() and failed.
sg = tmp / "note.txt.gz"
with gzip.open(sg, "wb") as f:
    f.write(b"solo payload")
probe(sg, "gz")
assert list_entries(sg, "gz") == [
    {"name": "note.txt", "raw": "note.txt", "size": 0, "is_dir": False}
], list_entries(sg, "gz")
extract(sg, tmp / "o4", "gz")
assert (tmp / "o4" / "note.txt").read_bytes() == b"solo payload"
print("solo gz OK")

evil = tmp / "evil.tar"
with tarfile.open(evil, "w") as tf:
    payload = b"pwned"
    info = tarfile.TarInfo("../escaped.txt")
    info.size = len(payload)
    tf.addfile(info, io.BytesIO(payload))
out5 = tmp / "o5"
try:
    extract(evil, out5, "tar")
except ArchiveError:
    pass
assert not (out5.parent / "escaped.txt").exists(), "path traversal NOT blocked"
print("tar traversal blocked OK")

bad = tmp / "bad.zip"
bad.write_bytes(b"not a zip at all")
try:
    probe(bad, "zip")
    raise AssertionError("corrupt zip should have raised")
except ArchiveError as exc:
    print("corrupt zip OK ->", str(exc)[:60])

shutil.rmtree(tmp, ignore_errors=True)
print("ALL EXTRACTOR TESTS PASSED")

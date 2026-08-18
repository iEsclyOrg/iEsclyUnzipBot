"""Archive inspection and extraction.

Every entry carries both a display `name` (normalised, forward slashes) and the
`raw` member name the archive itself uses. Extraction must use `raw`: a zip
built on Windows can hold backslash-separated names, and looking those up by
their normalised form raises KeyError.
"""
import bz2
import gzip
import logging
import lzma
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from config import (
    ARCHIVE_EXTENSIONS,
    RAR_TOOL_CANDIDATES,
    SOLO_COMPRESSED_TYPES,
    TAR_TYPES,
    UNRAR_TOOL,
)

logger = logging.getLogger(__name__)

try:
    import rarfile
except ImportError:
    rarfile = None

try:
    import py7zr
except ImportError:
    py7zr = None

# AES-encrypted zip entries use this synthetic compression method. The stdlib
# zipfile module cannot read them, so we detect it up front and say so.
_ZIP_AES_METHOD = 99

_SOLO_OPENERS: dict[str, Callable[..., Any]] = {
    "gz": gzip.open,
    "bz2": bz2.open,
    "xz": lzma.open,
}

# Added in 3.12 and backported to 3.11.4. Absent on older builds, where tar
# extraction stays unsandboxed and we can only warn.
_TAR_HAS_FILTER = hasattr(tarfile, "data_filter")


class ArchiveError(Exception):
    """Raised for any archive problem that should be shown to the user."""


class BackendMissing(ArchiveError):
    """A required external tool is absent. Nothing is wrong with the archive."""


_RAR_HELP = (
    "RAR support needs an external tool (unrar, unar, bsdtar or 7z) and none is "
    "installed on this server. On the Heroku Python buildpack, add the apt "
    "buildpack and the provided Aptfile; on the container stack the Dockerfile "
    "already installs it. Re-pack as .zip or .7z to extract it right now."
)

# Resolved once: None = not yet checked, "" = a working tool, else the reason.
_rar_problem: Optional[str] = None


def _rar_ready() -> None:
    """Point rarfile at a usable backend. Raises ArchiveError when there is none.

    rarfile shells out, so a missing binary surfaces as RarCannotExec deep inside
    an extraction. Reported verbatim that reads as "the archive is damaged",
    which sends people off debugging a file that is perfectly fine.
    """
    global _rar_problem
    if _rar_problem == "":
        return
    if _rar_problem:
        raise BackendMissing(_rar_problem)

    if rarfile is None:
        _rar_problem = "RAR support is unavailable: the rarfile package is not installed."
        raise BackendMissing(_rar_problem)

    candidates = (UNRAR_TOOL,) + RAR_TOOL_CANDIDATES if UNRAR_TOOL else RAR_TOOL_CANDIDATES
    for tool in candidates:
        if not shutil.which(tool):
            continue
        rarfile.UNRAR_TOOL = tool
        try:
            rarfile.tool_setup(force=True)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            logger.debug("RAR backend %s unusable: %s", tool, exc)
            continue
        logger.info("Using %s as the RAR backend", tool)
        _rar_problem = ""
        return

    _rar_problem = _RAR_HELP
    raise BackendMissing(_rar_problem)


def detect_archive_type(filename: str) -> Optional[str]:
    lowered = filename.lower()
    for ext in ARCHIVE_EXTENSIONS:
        if lowered.endswith(ext):
            return ext.lstrip(".")
    return None


def _normalize(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _entry(raw: str, size: int, is_dir: bool) -> dict[str, Any]:
    name = _normalize(raw)
    return {"name": name.rstrip("/") or name, "raw": raw, "size": size, "is_dir": is_dir}


def _solo_payload_name(path: Path) -> str:
    """`archive.txt.gz` holds a single member named `archive.txt`."""
    stem = path.name
    for ext in (".gz", ".bz2", ".xz"):
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem or "extracted.bin"


def needs_password(path: Path, archive_type: str) -> bool:
    """Whether the archive cannot be read without a password."""
    try:
        if archive_type == "zip":
            with zipfile.ZipFile(path) as zf:
                return any(info.flag_bits & 0x1 for info in zf.infolist())
        if archive_type == "rar" and rarfile:
            with rarfile.RarFile(path) as rf:
                return rf.needs_password()
        if archive_type == "7z" and py7zr:
            with py7zr.SevenZipFile(path) as sz:
                return sz.needs_password()
    except Exception:  # noqa: BLE001 - a broken archive is reported by probe()
        return False
    return False


def probe(path: Path, archive_type: str, password: Optional[str] = None) -> None:
    """Check the archive opens and the password (if any) is correct.

    Only the first stored file is read, which is enough to reject a wrong
    password. Full-archive corruption surfaces during extraction, so decoding
    every byte here would just double the work for large archives.

    Raises ArchiveError on failure.
    """
    try:
        if archive_type == "zip":
            _probe_zip(path, password)
        elif archive_type == "rar":
            _probe_rar(path, password)
        elif archive_type == "7z":
            _probe_7z(path, password)
        elif archive_type in SOLO_COMPRESSED_TYPES and not _is_tar(path):
            with _SOLO_OPENERS[archive_type](path, "rb") as fh:
                fh.read(1)
        elif archive_type in TAR_TYPES or archive_type in SOLO_COMPRESSED_TYPES:
            with tarfile.open(path, "r:*") as tf:
                tf.next()
        else:
            raise ArchiveError(f"Unsupported archive type: {archive_type}")
    except ArchiveError:
        raise
    except (RuntimeError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise ArchiveError(str(exc)) from exc
    except NotImplementedError as exc:
        raise ArchiveError(f"Unsupported compression method: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - backends raise library-specific errors
        raise ArchiveError(f"{type(exc).__name__}: {exc}") from exc


def _first_file(infos: list, size_of: Callable[[Any], int], is_dir: Callable[[Any], bool]):
    for info in infos:
        if not is_dir(info) and size_of(info) > 0:
            return info
    return None


def _probe_zip(path: Path, password: Optional[str]) -> None:
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if any(i.compress_type == _ZIP_AES_METHOD for i in infos):
            raise ArchiveError(
                "This zip uses AES encryption, which Python's zip reader cannot "
                "open. Please re-pack it as a .7z or as a ZipCrypto zip."
            )
        if password:
            zf.setpassword(password.encode())
        target = _first_file(infos, lambda i: i.file_size, lambda i: i.is_dir())
        if target is None:
            return
        with zf.open(target) as fh:
            fh.read(1)


def _probe_rar(path: Path, password: Optional[str]) -> None:
    _rar_ready()
    with rarfile.RarFile(path) as rf:
        if password:
            rf.setpassword(password)
        target = _first_file(rf.infolist(), lambda i: i.file_size, lambda i: i.is_dir())
        if target is None:
            return
        with rf.open(target) as fh:
            fh.read(1)


def _probe_7z(path: Path, password: Optional[str]) -> None:
    if not py7zr:
        raise ArchiveError("7z support is unavailable: the py7zr package is not installed.")
    with py7zr.SevenZipFile(path, mode="r", password=password) as sz:
        sz.test()


def _is_tar(path: Path) -> bool:
    """Distinguish `foo.tar.gz` mislabelled as `.gz` from a plain `foo.txt.gz`."""
    try:
        return tarfile.is_tarfile(path)
    except Exception:  # noqa: BLE001
        return False


def list_entries(path: Path, archive_type: str, password: Optional[str] = None) -> list[dict[str, Any]]:
    """Enumerate archive contents. Raises ArchiveError on failure."""
    try:
        if archive_type == "zip":
            with zipfile.ZipFile(path) as zf:
                if password:
                    zf.setpassword(password.encode())
                return [_entry(i.filename, i.file_size, i.is_dir()) for i in zf.infolist()]

        if archive_type == "rar":
            _rar_ready()
            with rarfile.RarFile(path) as rf:
                if password:
                    rf.setpassword(password)
                return [_entry(i.filename, i.file_size, i.is_dir()) for i in rf.infolist()]

        if archive_type == "7z":
            if not py7zr:
                raise ArchiveError("7z support is unavailable: the py7zr package is not installed.")
            with py7zr.SevenZipFile(path, mode="r", password=password) as sz:
                return [
                    _entry(i.filename, i.uncompressed or 0, i.is_directory)
                    for i in sz.list()
                ]

        if archive_type in SOLO_COMPRESSED_TYPES and not _is_tar(path):
            # A single compressed stream has no index; the uncompressed size is
            # only knowable by decompressing, so report 0 and let extraction tell.
            return [_entry(_solo_payload_name(path), 0, False)]

        if archive_type in TAR_TYPES or archive_type in SOLO_COMPRESSED_TYPES:
            with tarfile.open(path, "r:*") as tf:
                return [
                    _entry(m.name, m.size, m.isdir())
                    for m in tf.getmembers()
                    if m.isfile() or m.isdir()
                ]

        raise ArchiveError(f"Unsupported archive type: {archive_type}")

    except ArchiveError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f"{type(exc).__name__}: {exc}") from exc


def extract(
    path: Path,
    out_dir: Path,
    archive_type: str,
    members: Optional[list[str]] = None,
    password: Optional[str] = None,
) -> None:
    """Extract `members` (raw names), or everything when None.

    Raises ArchiveError on failure.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if archive_type == "zip":
            with zipfile.ZipFile(path) as zf:
                if password:
                    zf.setpassword(password.encode())
                # ZipFile.extract sanitises member paths itself.
                zf.extractall(out_dir, members=members)

        elif archive_type == "rar":
            _rar_ready()
            with rarfile.RarFile(path) as rf:
                if password:
                    rf.setpassword(password)
                rf.extractall(out_dir, members=members)

        elif archive_type == "7z":
            if not py7zr:
                raise ArchiveError("7z support is unavailable: the py7zr package is not installed.")
            with py7zr.SevenZipFile(path, mode="r", password=password) as sz:
                if members is None:
                    sz.extractall(out_dir)
                else:
                    sz.extract(out_dir, targets=members)

        elif archive_type in SOLO_COMPRESSED_TYPES and not _is_tar(path):
            target = out_dir / _solo_payload_name(path)
            with _SOLO_OPENERS[archive_type](path, "rb") as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)

        elif archive_type in TAR_TYPES or archive_type in SOLO_COMPRESSED_TYPES:
            with tarfile.open(path, "r:*") as tf:
                selected = [tf.getmember(name) for name in members] if members else None
                # filter="data" blocks absolute paths, "..", symlinks and device
                # nodes. Without it a crafted tar can write outside out_dir.
                if _TAR_HAS_FILTER:
                    tf.extractall(out_dir, members=selected, filter="data")
                else:
                    tf.extractall(out_dir, members=selected)

        else:
            raise ArchiveError(f"Unsupported archive type: {archive_type}")

    except ArchiveError:
        raise
    except KeyError as exc:
        raise ArchiveError(f"Entry not found in archive: {exc}") from exc
    except NotImplementedError as exc:
        raise ArchiveError(f"Unsupported compression method: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ArchiveError(f"{type(exc).__name__}: {exc}") from exc

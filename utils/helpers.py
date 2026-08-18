import re
import shutil
from pathlib import Path

from config import ARCHIVE_DIR, EXTRACTED_DIR

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def user_archive_dir(user_id: int) -> Path:
    path = ARCHIVE_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_extracted_dir(user_id: int) -> Path:
    path = EXTRACTED_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def cleanup_user(user_id: int) -> None:
    for base in (ARCHIVE_DIR / str(user_id), EXTRACTED_DIR / str(user_id)):
        shutil.rmtree(base, ignore_errors=True)


def sanitize_filename(name: str, fallback: str = "archive") -> str:
    """Strip directory components and characters that are illegal on Windows."""
    name = _UNSAFE_CHARS.sub("_", Path(name.replace("\\", "/")).name).strip(" .")
    return name[:200] or fallback


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

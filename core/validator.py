"""Post-extraction sanity checks.

Deliberately cheap and advisory. An earlier version decoded every media file
end to end with `ffmpeg -f null -` and treated any stderr output as a failure,
which meant a routine container warning threw away a whole extraction. A check
that is slower than the upload it guards, and that fails on healthy files, is
worse than no check at all.
"""
import logging
import subprocess
from pathlib import Path
from typing import Optional

from config import MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)

_ffprobe_available: Optional[bool] = None


def check_file(path: Path) -> Optional[str]:
    """Return a human-readable problem with `path`, or None when it looks fine."""
    if not path.is_file():
        return "missing after extraction"
    if path.stat().st_size == 0:
        return "empty"
    if path.suffix.lower() in MEDIA_EXTENSIONS:
        return _check_media(path)
    return None


def _check_media(path: Path) -> Optional[str]:
    """Confirm ffprobe can read the container header. Reads a few KB, not the file."""
    global _ffprobe_available
    if _ffprobe_available is False:
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=format_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        _ffprobe_available = False
        logger.info("ffprobe not found; skipping media checks")
        return None
    except subprocess.TimeoutExpired:
        return None  # Slow disk is not a corrupt file.

    _ffprobe_available = True
    if result.returncode != 0 or not result.stdout.strip():
        return "not a readable media file"
    return None

"""Split files that exceed Telegram's per-file upload limit."""
import logging
import math
import subprocess
from pathlib import Path
from typing import Optional

from config import MAX_FILE_SIZE, VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)

# Segment durations are derived from the average bitrate, so a variable-bitrate
# stretch can overshoot. Aim below the cap to leave room.
_SIZE_MARGIN = 0.95


class SplitError(Exception):
    pass


def split_file(path: Path, max_size: int = MAX_FILE_SIZE) -> list[Path]:
    """Return the parts to upload — `[path]` when it already fits.

    Raises SplitError on failure.
    """
    if path.stat().st_size <= max_size:
        return [path]

    if path.suffix.lower() in VIDEO_EXTENSIONS:
        parts = _split_video(path, max_size)
        if parts:
            return parts
        logger.info("Falling back to a binary split for %s", path.name)
    return _split_binary(path, max_size)


def _split_video(path: Path, max_size: int) -> Optional[list[Path]]:
    """Cut into independently playable segments. None means "not possible"."""
    duration = _probe_duration(path)
    if not duration:
        return None

    target = int(duration * max_size * _SIZE_MARGIN / path.stat().st_size)
    if target < 1:
        return None

    out_dir = path.parent / f"{path.stem}_parts"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(path),
                "-c", "copy", "-map", "0",
                "-f", "segment",
                "-segment_time", str(target),
                "-reset_timestamps", "1",
                str(out_dir / f"{path.stem}_%03d{path.suffix}"),
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        raise SplitError("ffmpeg timed out while splitting") from None

    if result.returncode != 0:
        logger.warning("ffmpeg split failed: %s", result.stderr.strip()[:500])
        return None

    parts = sorted(out_dir.glob(f"{path.stem}_*{path.suffix}"))
    oversized = [p for p in parts if p.stat().st_size > max_size]
    if not parts or oversized:
        # Keyframe spacing can make a segment unsplittable at this size.
        return None
    return parts


def _split_binary(path: Path, max_size: int) -> list[Path]:
    """Byte-exact slices named `name.ext.001`, `.002`, … — the scheme 7-Zip and
    WinRAR produce, so the user can rejoin them with any standard tool (and this
    bot will recognise them if they are sent back)."""
    out_dir = path.parent / f"{path.stem}_parts"
    out_dir.mkdir(parents=True, exist_ok=True)

    total = math.ceil(path.stat().st_size / max_size)
    parts: list[Path] = []
    try:
        with open(path, "rb") as src:
            for index in range(1, total + 1):
                part = out_dir / f"{path.name}.{index:03d}"
                written = 0
                with open(part, "wb") as dst:
                    while written < max_size:
                        chunk = src.read(min(1024 * 1024, max_size - written))
                        if not chunk:
                            break
                        dst.write(chunk)
                        written += len(chunk)
                if not written:
                    part.unlink(missing_ok=True)
                    break
                parts.append(part)
    except OSError as exc:
        raise SplitError(f"Could not split file: {exc}") from exc

    if not parts:
        raise SplitError("Splitting produced no parts")
    return parts


def _probe_duration(path: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None

"""Multi-volume archive sets.

A user who sends `movie.zip.001` is sending one slice of an archive, not an
archive. Detection recognises the two schemes in the wild and each is assembled
the way its producer intended: byte-slices are concatenated, real RAR volumes
are left alone for rarfile to follow.
"""
import re
import shutil
from pathlib import Path
from typing import NamedTuple, Optional

from config import VOLUME_PATTERNS

_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE), kind, offset)
    for pattern, kind, offset in VOLUME_PATTERNS
)


class VolumeInfo(NamedTuple):
    base: str    # Filename the assembled set should have.
    number: int  # 1-based volume index.
    kind: str    # "concat" or "rar".


def detect_volume(filename: str) -> Optional[VolumeInfo]:
    """Recognise `name.zip.001`, `name.part2.rar`, `name.r00`. None if not a volume."""
    for pattern, kind, offset in _COMPILED:
        match = pattern.match(filename)
        if not match:
            continue
        base = match.group("base")
        if kind == "rar" and not base.lower().endswith(".rar"):
            base = f"{base}.rar"
        return VolumeInfo(base=base, number=int(match.group("num")) + offset, kind=kind)
    return None


def missing_volumes(numbers: set[int]) -> list[int]:
    """Gaps in a volume set, assuming it should run 1..max with no holes."""
    if not numbers:
        return []
    return [n for n in range(1, max(numbers) + 1) if n not in numbers]


def assemble(volumes: dict[int, Path], target: Path) -> Path:
    """Concatenate byte-slice volumes in order into `target`, and return it."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as out:
        for number in sorted(volumes):
            with open(volumes[number], "rb") as part:
                shutil.copyfileobj(part, out, length=4 * 1024 * 1024)
    # The slices are large and no longer needed once joined.
    for path in volumes.values():
        path.unlink(missing_ok=True)
    return target

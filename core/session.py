"""Per-user session state.

Sessions are deliberately in-memory: every field points at temp files on local
disk, so a session that outlives the process is worthless. Keeping them here
also means the file list (which can be tens of thousands of entries) never has
to fit in a MongoDB document, and a button press costs no database round-trip.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import SESSION_TTL
from utils.helpers import cleanup_user


@dataclass
class Session:
    user_id: int
    archive_path: Path
    archive_type: str
    status_message_id: int
    state: str = "WAIT_ACTION"
    password: Optional[str] = None
    items: list[dict] = field(default_factory=list)
    page: int = 0
    selected: set[int] = field(default_factory=set)
    busy: bool = False
    touched_at: float = field(default_factory=time.monotonic)
    # Timestamp of the last status edit, used to throttle progress updates.
    last_status_edit: float = 0.0

    # Multi-volume collection. `volumes` maps 1-based volume number to its file;
    # `volume_kind` is "concat" (byte slices to join) or "rar" (leave alone).
    volumes: dict[int, Path] = field(default_factory=dict)
    volume_kind: Optional[str] = None

    def touch(self) -> None:
        self.touched_at = time.monotonic()

    @property
    def files(self) -> list[dict]:
        return [item for item in self.items if not item["is_dir"]]


_sessions: dict[int, Session] = {}


def get(user_id: int) -> Optional[Session]:
    session = _sessions.get(user_id)
    if session:
        session.touch()
    return session


def start(user_id: int, archive_path: Path, archive_type: str, status_message_id: int) -> Session:
    session = Session(
        user_id=user_id,
        archive_path=archive_path,
        archive_type=archive_type,
        status_message_id=status_message_id,
    )
    _sessions[user_id] = session
    return session


def end(user_id: int) -> None:
    _sessions.pop(user_id, None)
    cleanup_user(user_id)


def sweep_expired() -> int:
    """Drop sessions idle for longer than SESSION_TTL, freeing their temp files."""
    cutoff = time.monotonic() - SESSION_TTL
    stale = [
        user_id for user_id, session in _sessions.items()
        if not session.busy and session.touched_at < cutoff
    ]
    for user_id in stale:
        end(user_id)
    return len(stale)

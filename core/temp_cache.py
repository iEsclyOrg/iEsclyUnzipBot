"""In-memory cache for sensitive per-user data that must NOT be persisted."""
from typing import Optional

_passwords: dict[int, Optional[str]] = {}


def set_password(user_id: int, password: Optional[str]):
    _passwords[user_id] = password


def get_password(user_id: int) -> Optional[str]:
    return _passwords.get(user_id)


def clear_password(user_id: int):
    _passwords.pop(user_id, None)


def clear_all():
    _passwords.clear()

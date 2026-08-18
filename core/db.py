"""Upload logging. Optional: with no MONGO_URI configured the bot just skips it."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config import DB_NAME, MONGO_URI

logger = logging.getLogger(__name__)

_client = None
_db = None
_disabled = not MONGO_URI


def _get_db():
    global _client, _db
    if _db is None:
        from motor.motor_asyncio import AsyncIOMotorClient

        _client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _db = _client[DB_NAME]
    return _db


async def log_upload(data: dict[str, Any]) -> bool:
    if _disabled:
        return False
    data["timestamp"] = datetime.now(timezone.utc)
    try:
        await _get_db().upload_logs.insert_one(data)
        return True
    except Exception as exc:  # noqa: BLE001 - logging must never break an upload
        logger.warning("Could not log upload: %s", exc)
        return False


async def close_db() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None

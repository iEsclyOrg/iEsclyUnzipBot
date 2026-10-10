"""Upload logging, user tracking and admin queries.

Optional: with no MONGO_URI configured the bot just skips all persistence and
the admin panel reports that the database is unavailable.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from config import DB_NAME, DUMP_CHAT_ID, MONGO_URI

logger = logging.getLogger(__name__)

_client = None
_db = None
_disabled = not MONGO_URI

# Populated on first ban check, then kept in sync by set_banned() so the common
# path (every incoming update) never touches the database.
_banned_cache: Optional[set[int]] = None


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


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #

async def track_user(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    """Record/refresh a user. Never raises — a DB hiccup must not break a reply."""
    if _disabled:
        return
    now = datetime.now(timezone.utc)
    try:
        await _get_db().users.update_one(
            {"_id": user_id},
            {
                "$set": {"username": username, "first_name": first_name, "last_activity": now},
                "$setOnInsert": {"first_seen": now, "banned": False},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("track_user failed: %s", exc)


async def is_banned(user_id: int) -> bool:
    if _disabled:
        return False
    global _banned_cache
    if _banned_cache is None:
        try:
            _banned_cache = {
                doc["_id"]
                async for doc in _get_db().users.find({"banned": True}, {"_id": 1})
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("ban cache load failed: %s", exc)
            return False
    return user_id in _banned_cache


async def set_banned(user_id: int, banned: bool) -> bool:
    if _disabled:
        return False
    global _banned_cache
    try:
        await _get_db().users.update_one(
            {"_id": user_id},
            {
                "$set": {"banned": banned},
                "$setOnInsert": {"first_seen": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_banned failed: %s", exc)
        return False
    if _banned_cache is None:
        _banned_cache = set()
    if banned:
        _banned_cache.add(user_id)
    else:
        _banned_cache.discard(user_id)
    return True


async def list_users(skip: int = 0, limit: int = 10) -> tuple[list[dict], int]:
    if _disabled:
        return [], 0
    try:
        db = _get_db()
        total = await db.users.count_documents({})
        cursor = db.users.find({}).sort("last_activity", -1).skip(skip).limit(limit)
        users = [doc async for doc in cursor]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_users failed: %s", exc)
        return [], 0
    return users, total


async def all_user_ids() -> list[int]:
    if _disabled:
        return []
    try:
        return [doc["_id"] async for doc in _get_db().users.find({}, {"_id": 1})]
    except Exception as exc:  # noqa: BLE001
        logger.warning("all_user_ids failed: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# Stats / logs
# --------------------------------------------------------------------------- #

async def get_stats() -> Optional[dict]:
    """Aggregate counts for the admin panel. None when the DB is unavailable."""
    if _disabled:
        return None
    db = _get_db()
    try:
        users = await db.users.count_documents({})
        banned = await db.users.count_documents({"banned": True})
        uploads = await db.upload_logs.count_documents({})
        success = await db.upload_logs.count_documents({"status": "success"})
        failed = await db.upload_logs.count_documents({"status": "failed"})
        total_bytes = 0
        async for row in db.upload_logs.aggregate(
            [{"$group": {"_id": None, "bytes": {"$sum": "$size"}}}]
        ):
            total_bytes = int(row.get("bytes") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_stats failed: %s", exc)
        return None
    return {
        "users": users,
        "banned": banned,
        "uploads": uploads,
        "success": success,
        "failed": failed,
        "bytes": total_bytes,
    }


async def recent_logs(limit: int = 10) -> list[dict]:
    if _disabled:
        return []
    try:
        cursor = _get_db().upload_logs.find({}).sort("timestamp", -1).limit(limit)
        return [doc async for doc in cursor]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_logs failed: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# Settings (key/value) — used for the dump chat target
# --------------------------------------------------------------------------- #

async def set_setting(key: str, value) -> bool:
    if _disabled:
        return False
    try:
        await _get_db().settings.update_one(
            {"_id": key}, {"$set": {"value": value}}, upsert=True
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_setting(%s) failed: %s", key, exc)
        return False


async def get_setting(key: str, default=None):
    if _disabled:
        return default
    try:
        doc = await _get_db().settings.find_one({"_id": key})
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_setting(%s) failed: %s", key, exc)
        return default
    return doc.get("value") if doc else default


async def get_dump_chat() -> Optional[int]:
    """Chat id extracted files are delivered to, or None for 'send to the user'."""
    value = await get_setting("dump_chat")
    if value:
        return int(value)
    return DUMP_CHAT_ID or None


async def set_dump_chat(chat_id: Optional[int]) -> bool:
    return await set_setting("dump_chat", chat_id)


async def close_db() -> None:
    global _client, _db, _banned_cache
    if _client is not None:
        _client.close()
        _client = None
        _db = None
    _banned_cache = None

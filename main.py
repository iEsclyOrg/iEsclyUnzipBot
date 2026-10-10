import asyncio
import logging
import os

from config import ADMIN_IDS, API_HASH, API_ID, BOT_TOKEN, ensure_dirs
from core import bot_client, rich, session as sessions
from core.db import close_db
from health_server import start_health_server, stop_health_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Pyrogram narrates every connection at INFO level.
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logger = logging.getLogger("unzipbot")

SWEEP_INTERVAL = 300


async def _sweep_sessions() -> None:
    """Reclaim disk from sessions the user walked away from."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        dropped = sessions.sweep_expired()
        if dropped:
            logger.info("Cleared %d idle session(s)", dropped)


async def main() -> None:
    missing = [
        name for name, value in
        (("BOT_TOKEN", BOT_TOKEN), ("API_ID", API_ID), ("API_HASH", API_HASH))
        if not value
    ]
    if missing:
        raise SystemExit(f"Missing required environment variable(s): {', '.join(missing)}")

    ensure_dirs()
    runner = await start_health_server(port=int(os.getenv("PORT", "8000")))
    client = bot_client.create_bot_client()
    sweeper = asyncio.create_task(_sweep_sessions())
    started = False

    try:
        await client.start()
        started = True
        me = await client.get_me()
        logger.info("Running as @%s", me.username)
        logger.info("Admin IDs configured: %s", sorted(ADMIN_IDS) or "none")
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down")
    finally:
        sweeper.cancel()
        # Guard the stop: if start() raised, stopping an unstarted client raises
        # again and buries the error that actually mattered.
        if started:
            try:
                await client.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error while stopping the client: %s", exc)
        await stop_health_server(runner)
        await rich.close()
        await close_db()


if __name__ == "__main__":
    # uvloop is a drop-in, much faster event loop on Linux (where the bot is
    # deployed). It doesn't exist on Windows, so it's optional.
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "") or default)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = _get_int("API_ID", 0)
API_HASH = os.getenv("API_HASH", "")

# Optional. When empty the bot runs fine, it just does not log uploads.
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "unzip_bot")

# Telegram's MTProto limit for bots is 2000 MiB, not 2 GiB. Anything above
# 2097152000 bytes is rejected by the server, so files are split at that mark.
MAX_FILE_SIZE = _get_int("MAX_FILE_SIZE", 2000 * 1024 * 1024)
MAX_ARCHIVE_SIZE = _get_int("MAX_ARCHIVE_SIZE", 5 * 1024 * 1024 * 1024)
MAX_TOTAL_EXTRACTED = _get_int("MAX_TOTAL_EXTRACTED", 10 * 1024 * 1024 * 1024)

# Abandoned sessions (and their temp files) are dropped after this long.
SESSION_TTL = _get_int("SESSION_TTL", 3600)

# --- Transfer speed ------------------------------------------------------- #
# Pyrogram defaults max_concurrent_transmissions to 1, so a 2 GB file moves one
# chunk at a time and never uses the available bandwidth. Raising it transfers
# chunks in parallel — the single biggest win for download and upload speed.
# WZML-X-style mirror bots run this at 1000 on dedicated servers; each slot is
# an in-flight 512 KiB chunk, so 64 keeps worst-case memory near 32 MiB while
# still saturating any realistic link. Push it higher via the env var on a VPS.
TRANSFER_CONCURRENCY = _get_int("TRANSFER_CONCURRENCY", 1000)

# Handler concurrency. Pyrogram's default of 16 is fine; exposed for tuning.
WORKERS = _get_int("WORKERS", 64)

# FloodWaits shorter than this are absorbed by Pyrogram itself.
SLEEP_THRESHOLD = _get_int("SLEEP_THRESHOLD", 30)

# --- Rate limiting -------------------------------------------------------- #
# Telegram tolerates roughly one message per second to a chat. Sending a
# 200-file extraction flat out earns a FloodWait within seconds, so uploads are
# paced and status edits are throttled separately (edits share the same budget).
# For sizeable files the upload itself takes far longer than this interval, so
# the pacing only really matters for batches of tiny files.
UPLOAD_INTERVAL = float(os.getenv("UPLOAD_INTERVAL", "0.5"))
STATUS_EDIT_INTERVAL = float(os.getenv("STATUS_EDIT_INTERVAL", "4"))

# Below this size a file uploads fast enough that per-byte progress is just
# wasted edits; only the "n of m" counter is shown.
PROGRESS_MIN_SIZE = _get_int("PROGRESS_MIN_SIZE", 25 * 1024 * 1024)

# A FloodWait longer than this means waiting it out would outlive the dyno.
MAX_FLOOD_WAIT = _get_int("MAX_FLOOD_WAIT", 600)

# Uploads go out in rounds of this many files. A selection larger than this is
# no longer refused — it is split into rounds automatically and each round is
# extracted and sent before the next begins, so one bad file or a FloodWait
# never throws away the whole job.
MAX_FILES_PER_BATCH = _get_int("MAX_FILES_PER_BATCH", 100)

# Rich messages need the HTTP Bot API (uploads still go over MTProto).
# Set RICH_UI=0 to force the plain-text UI.
RICH_UI = os.getenv("RICH_UI", "1") not in ("0", "false", "False", "")

# Banner shown with /start.
BANNER_URL = os.getenv(
    "BANNER_URL",
    "https://4kwallpapers.com/images/walls/thumbs_3t/23149.png",
)

# rarfile shells out to an external tool. Candidates are tried in order; set
# UNRAR_TOOL to pin one. Without any of these installed, RAR is unavailable —
# see the deployment notes in README.md.
UNRAR_TOOL = os.getenv("UNRAR_TOOL", "")
RAR_TOOL_CANDIDATES = ("unrar", "unar", "bsdtar", "7z", "7za", "7zz")

TEMP_ROOT = Path(os.getenv("TEMP_ROOT", "./temp"))
ARCHIVE_DIR = TEMP_ROOT / "archives"
EXTRACTED_DIR = TEMP_ROOT / "extracted"

PAGE_SIZE = 10

# Longest extensions first: detection walks this list in order so that
# ".tar.gz" wins over ".gz".
ARCHIVE_EXTENSIONS = (
    ".tar.gz", ".tar.bz2", ".tar.xz",
    ".tgz", ".tbz2", ".txz",
    ".zip", ".rar", ".7z", ".tar",
    ".gz", ".bz2", ".xz",
)

# Multi-volume sets, as (regex, kind, number offset). Two schemes exist and they
# must be handled differently:
#
#  - "concat": 7-Zip/WinRAR "split to volumes" output — archive.zip.001,
#    archive.7z.002, … Each part is a raw byte slice, so the original file is
#    recovered by concatenating them in order.
#  - "rar": genuine RAR multi-volume — archive.part1.rar, archive.r00. These are
#    self-describing volumes; rarfile follows the chain itself given the first
#    one, and concatenating them would corrupt the set.
#
# The offset normalises each scheme onto 1-based numbering: `.r00` is the second
# physical volume because the plain `.rar` is the first.
VOLUME_PATTERNS = (
    (r"^(?P<base>.+\.(?:zip|7z|rar|tar|tar\.gz|tar\.bz2|tar\.xz|tgz|gz|bz2|xz))\.(?P<num>\d{2,4})$", "concat", 0),
    (r"^(?P<base>.+)\.part(?P<num>\d{1,4})\.rar$", "rar", 0),
    (r"^(?P<base>.+)\.r(?P<num>\d{2,3})$", "rar", 2),
)

# Archive types handled by the tarfile module.
TAR_TYPES = frozenset({"tar", "tar.gz", "tar.bz2", "tar.xz", "tgz", "tbz2", "txz"})

# Single-stream compressors: one payload, no directory listing.
SOLO_COMPRESSED_TYPES = frozenset({"gz", "bz2", "xz"})

VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".flv", ".ts"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".wma"})
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Containers Telegram can actually stream in-app. The rest upload as documents,
# which is what clients fall back to anyway — sending an .mkv as a video just
# gives the user a preview that will not play.
STREAMABLE_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v"})


def ensure_dirs() -> None:
    """Create the temp tree. Called at startup, not at import time."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)

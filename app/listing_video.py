"""Файлы видео и ссылки Kinescope в объявлениях агента."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from . import config

MAX_BYTES = 50_000_000
FILE_TYPES = {"mp4": "video/mp4", "webm": "video/webm"}
FILE_NAME = re.compile(r"[0-9a-f]{32}\.(?:mp4|webm)")
KINESCOPE = re.compile(
    r"(?i:https://(?:www\.)?kinescope\.io/)(?:embed/)?([a-zA-Z0-9_-]{6,64})/?(?:[?#][^\s]*)?"
)
log = logging.getLogger(__name__)


def kinescope_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    match = KINESCOPE.fullmatch(value) if len(value) <= 500 else None
    if not match:
        raise ValueError("Вставьте ссылку на видео с kinescope.io.")
    return f"https://kinescope.io/embed/{match[1]}"


def identify(prefix: bytes) -> str | None:
    if prefix[4:8] == b"ftyp" and re.fullmatch(rb"iso[0-9m]|mp4[12]|avc1|M4V |dash|MSNV", prefix[8:12]):
        return "mp4"
    if prefix.startswith(b"\x1a\x45\xdf\xa3") and b"\x42\x82\x84webm" in prefix:
        return "webm"
    return None


def root() -> Path:
    return Path(config.DB_PATH).parent / "listing-videos"


def file_url(listing_id: int, name: str | None) -> str | None:
    return f"/listing-videos/{listing_id}/{name}" if name and FILE_NAME.fullmatch(name) else None


def fields(row) -> dict:
    return {"video_url": file_url(row["id"], row["video_file"]), "kinescope_url": row["kinescope_url"]}


def discard(listing_id: int, name: str | None) -> None:
    if not name or not FILE_NAME.fullmatch(name):
        return
    try:
        (root() / str(listing_id) / name).unlink(missing_ok=True)
    except OSError:
        log.exception("Не удалось удалить старое видео объекта %s", listing_id)

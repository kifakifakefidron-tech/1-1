"""Забираем новые сообщения из Wappi по API (без переключения webhook —
старая система продолжает получать сообщения как раньше).

Профили задаются в .env: WAPPI_PROFILES=wa:<id>,tg:<id>,max:<id>
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import config, db, ingest

log = logging.getLogger(__name__)

BASE = "https://wappi.pro"
PREFIX = {"wa": "/api/sync", "tg": "/tapi/sync", "max": "/maxapi/sync"}
PAGE = 400
MAX_PAGES = 50
_shown_fields: set[str] = set()


def _get(path: str, params: dict) -> dict | list:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": config.WAPPI_TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _messages(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    for key in ("messages", "data", "result"):
        v = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(v, list):
            return v
    return []


def _is_group(source: str, m: dict) -> bool:
    chat = str(m.get("chatId") or m.get("chat_id") or "")
    if source == "wa":
        return chat.endswith("@g.us")
    if m.get("isGroup") or m.get("is_group"):
        return True
    ctype = str(m.get("chat_type") or m.get("chatType") or "").lower()
    if ctype in ("group", "supergroup", "channel", "chat"):
        return True
    return chat.startswith("-")


def _text(m: dict) -> str:
    for k in ("body", "text", "caption", "message"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _sender_phone(m: dict) -> str | None:
    raw = str(m.get("from") or m.get("sender") or m.get("author") or "")
    digits = "".join(ch for ch in raw.split("@")[0] if ch.isdigit())
    return digits if 10 <= len(digits) <= 12 else None


def poll_profile(conn: sqlite3.Connection, profile: str) -> int:
    """Забрать новые сообщения одного профиля. Возвращает число новых."""
    source, _, profile_id = profile.partition(":")
    if source not in PREFIX or not profile_id:
        log.error("Неверный профиль Wappi «%s», ожидается wa:<id>, tg:<id> или max:<id>", profile)
        return 0
    state_key = f"wappi_cursor:{profile}"
    cursor = int(db.get_state(conn, state_key) or 0)
    if not cursor:
        cursor = int(time.time()) - config.WAPPI_BACKFILL_HOURS * 3600
    since = datetime.fromtimestamp(cursor - 300, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    added, received, newest = 0, 0, cursor
    for page in range(MAX_PAGES):
        params = {"profile_id": profile_id, "limit": PAGE, "offset": page * PAGE, "date": since, "order": "asc"}
        try:
            batch = _messages(_get(f"{PREFIX[source]}/messages/all/get", params))
        except urllib.error.HTTPError as e:
            log.error("Wappi %s: HTTP %s %s", profile, e.code, e.read()[:300])
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            log.error("Wappi %s: %s", profile, e)
            break
        if batch and profile not in _shown_fields:  # один раз после запуска — для проверки формата
            _shown_fields.add(profile)
            log.info("Wappi %s: пример полей сообщения: %s", profile, sorted(batch[0].keys()))
        received += len(batch)
        for m in batch:
            if m.get("fromMe") and not config.WAPPI_INCLUDE_FROM_ME:
                continue
            if config.WAPPI_GROUPS_ONLY and not _is_group(source, m):
                continue
            if m.get("isDeleted"):
                continue
            text = _text(m)
            if len(text) < 30:
                continue
            ts = int(m.get("time") or m.get("timestamp") or time.time())
            newest = max(newest, ts)
            mid = ingest.add_message(
                conn, source=source, text=text, ts=ts, profile_id=profile_id,
                chat_id=str(m.get("chatId") or m.get("chat_id") or ""),
                chat_name=m.get("chat_name") or m.get("chatName") or m.get("chat_title"),
                msg_id=str(m.get("id") or ""), sender=_sender_phone(m), sender_name=m.get("senderName"),
            )
            if mid:
                added += 1
        if len(batch) < PAGE:
            break
    if newest > cursor:
        db.set_state(conn, state_key, str(newest))
    log.info("Wappi %s: получено %d, новых объявлений-кандидатов %d", profile, received, added)
    return added


def poll_all(conn: sqlite3.Connection) -> int:
    if not config.WAPPI_TOKEN or not config.WAPPI_PROFILES:
        return 0
    return sum(poll_profile(conn, p) for p in config.WAPPI_PROFILES)

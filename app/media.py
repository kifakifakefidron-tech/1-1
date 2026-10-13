"""Фото объектов из чатов (Wappi).

Агенты присылают объявление альбомом: текст (часто — подписью к первой картинке) и несколько фото подряд.
  * wappi._store запоминает каждую картинку из групп в chat_media (кто, в каком чате, когда);
  * attach() (из worker) находит объявление того же отправителя в том же чате в пределах ATTACH_WINDOW секунд и,
    если из этого сообщения получился ровно один объект, скачивает фото через Wappi
    (GET {api}/message/media/download → file_link, ссылка живёт ~10 дней; WhatsApp хранит файлы 20 дней)
    и добавляет в карточку (до MAX_PHOTOS). «Портянки» с несколькими объектами — не трогаем (чьё фото — непонятно).
  * Карточки, где фото ведёт агент сам (owner_user_id), не трогаем.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import urllib.request
from pathlib import Path

from . import config, db

log = logging.getLogger(__name__)
ATTACH_WINDOW = 180        # фото и текст объявления — в пределах 3 минут
WAIT_TEXT = 600            # текста рядом нет 10 минут — фото «сирота»
KEEP = 3 * 86400           # старше — уже не скачиваем
MAX_BYTES = 6_000_000
CHAT_PHOTOS = 1   # из чата — строго одно фото на объект (решение Артёма 13.10: альбомы и коллажи путали квартиры);
                  # больше — только у фида СТРЕЛ и своих объектов агентов
_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp"}


def kind(m: dict) -> str | None:
    """Это картинка? (WhatsApp: type=image; body — словарь с mimetype; Telegram/MAX — похожие поля)."""
    t = str(m.get("type") or m.get("message_type") or "").lower()
    body = m.get("body")
    mime = str(body.get("mimetype") or "") if isinstance(body, dict) else ""
    mime = mime or str(m.get("mimetype") or m.get("mime_type") or "")
    return "image" if t in ("image", "photo", "picture") or mime.startswith("image/") else None


def caption(m: dict) -> str:
    """Подпись к картинке (в ней часто весь текст объявления)."""
    body = m.get("body")
    for v in (m.get("caption"), body.get("caption") if isinstance(body, dict) else None):
        if isinstance(v, str) and v.strip():
            return v
    return ""


def remember(conn: sqlite3.Connection, source: str, profile_id: str, chat_id: str, sender: str | None,
             msg_id: str, ts: int | None) -> None:
    if not msg_id or not chat_id:
        return
    conn.execute("""INSERT OR IGNORE INTO chat_media (source, profile_id, chat_id, sender, msg_id, ts, status)
                    VALUES (?,?,?,?,?,?, 'new')""", (source, profile_id, chat_id, sender, msg_id, ts or int(time.time())))


def _listing_for(conn: sqlite3.Connection, r) -> tuple[str, int | None]:
    """Объявление этого агента, к которому относится фото: ('ok', id) | ('wait', None) | ('skip', None).

    Фото принадлежит ПОСЛЕДНЕМУ тексту этого агента в чате до него (подпись к фото — то же время), и только если
    между ними нет другого его текста: так фото следующей квартиры не уезжает к предыдущей."""
    m = conn.execute(
        """SELECT m.id, m.ts FROM messages m
           WHERE m.source = ? AND m.chat_id = ? AND COALESCE(m.sender, '') = COALESCE(?, '')
             AND m.ts <= ? + 5 AND m.ts >= ? - ?
           ORDER BY m.ts DESC LIMIT 1""", (r["source"], r["chat_id"], r["sender"], r["ts"], r["ts"], ATTACH_WINDOW)).fetchone()
    if m is None:
        return ("wait", None) if time.time() - r["ts"] < WAIT_TEXT else ("skip", None)
    lids = [x[0] for x in conn.execute("SELECT DISTINCT listing_id FROM listing_events WHERE message_id = ?", (m["id"],))]
    if len(lids) == 1:
        return "ok", lids[0]
    if len(lids) > 1:
        return "skip", None   # портянка: несколько объектов в одном сообщении — чьё фото, непонятно
    return ("wait", None) if time.time() - r["ts"] < WAIT_TEXT else ("skip", None)


def _download(r) -> bytes | None:
    from . import wappi
    profile = f"{r['source']}:{r['profile_id']}"
    token = config.WAPPI_TOKENS.get(r["source"])
    if not token:
        return None
    payload = wappi._call(profile, f"{wappi.PREFIX[r['source']]}/message/media/download",
                          {"profile_id": r["profile_id"], "message_id": r["msg_id"]}, token)
    link = (payload or {}).get("file_link") if isinstance(payload, dict) else None
    return _fetch(link) if link else None


def _fetch(link: str) -> bytes | None:
    req = urllib.request.Request(link, headers={"User-Agent": "1plus1assistant.ru/1.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        raw = resp.read(MAX_BYTES + 1)
    return raw if len(raw) <= MAX_BYTES else None


def attach(conn: sqlite3.Connection, limit: int = 15) -> dict:
    """Привязать и скачать новые фото (не больше limit за раз)."""
    out = {"photos": 0, "skipped": 0}
    rows = conn.execute("""SELECT * FROM chat_media WHERE status = 'new' AND ts > ? ORDER BY ts ASC, id ASC LIMIT 300""",
                        (int(time.time()) - KEEP,)).fetchall()
    for r in rows:
        if out["photos"] >= limit:
            break
        state, lid = _listing_for(conn, r)
        if state == "wait":
            continue
        if state == "skip":
            conn.execute("UPDATE chat_media SET status = 'skip' WHERE id = ?", (r["id"],))
            out["skipped"] += 1
            continue
        row = conn.execute("SELECT photos, owner_user_id, source, is_active FROM listings WHERE id = ?", (lid,)).fetchone()
        photos = json.loads(row["photos"] or "[]") if row else []
        if row is None or row["owner_user_id"] or row["source"] != "chat" or len(photos) >= CHAT_PHOTOS:
            conn.execute("UPDATE chat_media SET status = 'skip', listing_id = ? WHERE id = ?", (lid, r["id"]))
            continue
        db.flush(conn)   # не держать базу, пока качаем
        try:
            raw = _download(r)
        except Exception as e:  # noqa: BLE001
            log.warning("фото из чата %s: %s", r["msg_id"], e)
            raw = None
        ext = next((e for magic, e in _MAGIC.items() if raw and raw.startswith(magic)), None)
        if not raw or not ext or len(raw) < 2000:
            conn.execute("UPDATE chat_media SET status = 'fail', listing_id = ? WHERE id = ?", (lid, r["id"]))
            continue
        name = hashlib.sha1(raw).hexdigest()[:16] + "." + ext
        folder = Path(config.PHOTOS_DIR) / str(lid)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(raw)
        url = f"/photos/{lid}/{name}"
        row = conn.execute("SELECT photos FROM listings WHERE id = ?", (lid,)).fetchone()
        photos = json.loads(row["photos"] or "[]")
        if url not in photos and len(photos) < CHAT_PHOTOS:
            photos.append(url)
            conn.execute("UPDATE listings SET photos = ? WHERE id = ?", (json.dumps(photos), lid))
            out["photos"] += 1
        conn.execute("UPDATE chat_media SET status = 'done', listing_id = ?, url = ? WHERE id = ?", (lid, url, r["id"]))
        conn.commit()
    conn.commit()
    return out


def trim_chat_photos(conn: sqlite3.Connection) -> int:
    """Один раз: у объявлений из чатов оставить только первое фото (раньше брали весь альбом — бывали чужие коллажи).
    Фид СТРЕЛ, свои объекты и фото, которые агент загрузил сам (owner_user_id), не трогаем."""
    if db.get_state(conn, "chat_photos_1") == "1":
        return 0
    n = 0
    for lid, photos in conn.execute("""SELECT id, photos FROM listings WHERE source = 'chat' AND owner_user_id IS NULL
                                       AND photos IS NOT NULL AND photos != '[]'""").fetchall():
        lst = json.loads(photos or "[]")
        if len(lst) > CHAT_PHOTOS:
            conn.execute("UPDATE listings SET photos = ? WHERE id = ?", (json.dumps(lst[:CHAT_PHOTOS]), lid))
            n += 1
    db.set_state(conn, "chat_photos_1", "1")
    conn.commit()
    log.info("фото из чатов: оставлено по одному у %d объектов", n)
    return n

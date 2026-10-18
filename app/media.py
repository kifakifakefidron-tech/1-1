"""Фото объектов из чатов (Wappi).

Агенты присылают объявление альбомом: текст (часто — подписью к первой картинке) и несколько фото подряд.
  * wappi._store запоминает каждую картинку из групп в chat_media (кто, в каком чате, когда);
  * attach() (из worker) — СТРОГО (18.10, Артём: «никаких 20 секунд — только сразу»): только фото с подписью-объявлением
    (картинка и текст — ОДНО сообщение) и,
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
    """Объявление, к которому относится фото: ('ok', id) | ('wait', None) | ('skip', None).

    18.10 (Артём: «должно подтягиваться, только если фото отправлено вместе с текстом — фото и описание; если просто
    текст — никакое фото не подгружается»). Поэтому фото берём ТОЛЬКО:
      * если текст объявления — подпись к этой самой картинке (сообщение с тем же msg_id).
    Картинки без подписи (в том числе остальные фото альбома) и отдельное текстовое сообщение — не связываем."""
    m = conn.execute("SELECT id, ts FROM messages WHERE source = ? AND msg_id = ?", (r["source"], r["msg_id"])).fetchone()
    if m is None:   # подпись к фото сохраняется тем же опросом — подождём чуть-чуть, потом — «сирота»
        return ("wait", None) if time.time() - r["ts"] < 120 else ("skip", None)
    lids = [x[0] for x in conn.execute("SELECT DISTINCT listing_id FROM listing_events WHERE message_id = ?", (m["id"],))]
    if len(lids) == 1:
        return "ok", lids[0]
    if len(lids) > 1:
        return "skip", None   # портянка: несколько объектов в одном сообщении — чьё фото, непонятно
    return ("wait", None) if time.time() - r["ts"] < WAIT_TEXT else ("skip", None)


def strict_cleanup(conn: sqlite3.Connection) -> dict | None:
    """Один раз (state media_strict_v3, 18.10 — Артём: «проверить все загруженные объекты и удалить фото, если не
    соответствует: в чатах тысячи сообщений, по времени могли подобраться какие угодно; фид СТРЕЛ не трогаем»).

    Проверяется КАЖДОЕ фото у КАЖДОГО объявления из чатов (на сайте и в архиве): фото остаётся, только если оно
    скачано из сообщения, текст которого (подпись к этой самой картинке) и дал этот объект. Всё остальное — фото без
    записи, откуда оно, фото «рядом по времени», альбомы, портянки, фото, чьё сообщение уже не проверить, — удаляется
    (и файл тоже). Не трогаем: фид СТРЕЛ, свои объекты агентов (source='own') и карточки, где фото ведёт агент
    (owner_user_id — он загружал их сам)."""
    if db.get_state(conn, "media_strict_v3") == "1":
        return None
    rep = {"checked": 0, "kept": 0, "removed": 0, "listings": 0, "ts": int(time.time()), "examples": []}
    rows = conn.execute("""SELECT id, title, photos FROM listings WHERE source = 'chat' AND COALESCE(owner_user_id, 0) = 0
                           AND photos IS NOT NULL AND photos NOT IN ('', '[]')""").fetchall()
    for lr in rows:
        lid = lr["id"]
        photos = json.loads(lr["photos"] or "[]")
        keep = []
        for u in photos:
            rep["checked"] += 1
            cm = conn.execute("SELECT * FROM chat_media WHERE url = ? AND status = 'done' ORDER BY id DESC LIMIT 1", (u,)).fetchone()
            state, owner = _listing_for(conn, cm) if cm is not None else ("skip", None)
            if state == "ok" and owner == lid:
                keep.append(u)
                continue
            if cm is not None:
                conn.execute("UPDATE chat_media SET status = 'skip' WHERE id = ?", (cm["id"],))
            _drop_file(conn, lid, u)
        if len(keep) != len(photos):
            conn.execute("UPDATE listings SET photos = ? WHERE id = ?", (json.dumps(keep), lid))
            rep["removed"] += len(photos) - len(keep)
            rep["listings"] += 1
            if len(rep["examples"]) < 30:
                rep["examples"].append({"id": lid, "title": lr["title"], "removed": len(photos) - len(keep)})
        rep["kept"] += len(keep)
    db.set_state(conn, "media_strict_v3", "1")
    db.set_state(conn, "media_strict_report", json.dumps(rep, ensure_ascii=False))
    conn.commit()
    log.info("фото из чатов: проверено %s, оставлено %s, удалено %s у %s объектов",
             rep["checked"], rep["kept"], rep["removed"], rep["listings"])
    return rep


def _drop_file(conn: sqlite3.Connection, lid: int, url: str) -> None:
    """Файл фото из папки объекта — удалить, если больше ни у кого его нет."""
    if not url.startswith(f"/photos/{lid}/"):
        return
    if conn.execute("SELECT 1 FROM listings WHERE id != ? AND photos LIKE ?", (lid, f'%"{url}"%')).fetchone():
        return
    try:
        (Path(config.PHOTOS_DIR) / str(lid) / url.rsplit("/", 1)[-1]).unlink(missing_ok=True)
    except OSError:
        pass


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
        if conn.execute("SELECT 1 FROM banned WHERE kind = 'photo' AND key = ?", (url,)).fetchone():
            continue   # это фото админ уже удалял у объекта
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

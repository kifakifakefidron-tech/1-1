"""Подборки для клиента: агент собирает объекты и отправляет клиенту одну красивую ссылку /c/<код>.

Клиент видит фото, цены, параметры, адрес, карту и ТОЛЬКО контакт агента, который сделал подборку:
ни телефонов других агентов, ни исходных сообщений, ни ссылок на чаты. Создавать подборки — с доступом (подписка).
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time

from . import parser

MAX_COLLECTIONS = 200
MAX_ITEMS = 60


def _phone(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10:
        return "+7" + d
    return (v or "").strip()[:30]


def _default_contact(conn: sqlite3.Connection, user) -> tuple[str, str]:
    """Контакт по умолчанию: имя из кабинета и подтверждённый номер агента."""
    ph = conn.execute("SELECT phone FROM agent_phones WHERE user_id = ? ORDER BY verified_at DESC LIMIT 1",
                      (user["id"],)).fetchone()
    return (user["name"] or "").strip(), (ph[0] if ph else "")


def mine(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id) AS n
                             FROM collections c WHERE c.user_id = ? ORDER BY c.updated DESC""", (user_id,)):
        d = dict(r)
        d["items"] = [dict(x) for x in conn.execute(
            """SELECT i.listing_id, i.note, i.pos, l.title, l.price, l.deal, l.is_active, l.photos
               FROM collection_items i JOIN listings l ON l.id = i.listing_id
               WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (r["id"],))]
        for it in d["items"]:
            photos = json.loads(it.pop("photos") or "[]")
            it["photo"] = photos[0] if photos else None
        out.append(d)
    return out


def save(conn: sqlite3.Connection, user, body: dict) -> dict:
    title = str(body.get("title") or "").strip()[:120] or "Подборка для вас"
    note = str(body.get("note") or "").strip()[:2000]
    now = int(time.time())
    cid = body.get("id")
    if cid:
        if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (int(cid), user["id"])).fetchone():
            return {"error": "Подборка не найдена."}
        conn.execute("""UPDATE collections SET title = ?, note = ?, contact_name = ?, contact_phone = ?, updated = ?
                        WHERE id = ? AND user_id = ?""",
                     (title, note, str(body.get("contact_name") or "").strip()[:100], _phone(str(body.get("contact_phone") or "")),
                      now, int(cid), user["id"]))
    else:
        if conn.execute("SELECT COUNT(*) FROM collections WHERE user_id = ?", (user["id"],)).fetchone()[0] >= MAX_COLLECTIONS:
            return {"error": "Слишком много подборок — удалите старые."}
        name, phone = _default_contact(conn, user)
        cid = conn.execute("""INSERT INTO collections (user_id, token, title, note, contact_name, contact_phone, created, updated)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (user["id"], secrets.token_urlsafe(9), title, note, body.get("contact_name") or name,
                            _phone(str(body.get("contact_phone") or phone)), now, now)).lastrowid
    if body.get("listing_id"):
        set_item(conn, user["id"], int(cid), int(body["listing_id"]), True, commit=False)
    conn.commit()
    return next(c for c in mine(conn, user["id"]) if c["id"] == int(cid))


def delete(conn: sqlite3.Connection, user_id: int, cid: int) -> None:
    if conn.execute("DELETE FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).rowcount:
        conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (cid,))
    conn.commit()


def set_item(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, add: bool,
             note: str | None = None, commit: bool = True) -> dict:
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return {"error": "Подборка не найдена."}
    if add:
        if not conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone():
            return {"error": "Объект не найден."}
        n = conn.execute("SELECT COUNT(*) FROM collection_items WHERE collection_id = ?", (cid,)).fetchone()[0]
        if n >= MAX_ITEMS and not conn.execute("SELECT 1 FROM collection_items WHERE collection_id = ? AND listing_id = ?",
                                               (cid, listing_id)).fetchone():
            return {"error": f"В подборке не больше {MAX_ITEMS} объектов."}
        conn.execute("""INSERT INTO collection_items (collection_id, listing_id, pos, note, added) VALUES (?,?,?,?,?)
                        ON CONFLICT(collection_id, listing_id) DO UPDATE SET note = COALESCE(excluded.note, note)""",
                     (cid, listing_id, n, (note or "").strip()[:1000] if note is not None else None, int(time.time())))
    else:
        conn.execute("DELETE FROM collection_items WHERE collection_id = ? AND listing_id = ?", (cid, listing_id))
    conn.execute("UPDATE collections SET updated = ? WHERE id = ?", (int(time.time()), cid))
    if commit:
        conn.commit()
    return {"ok": True}


def move(conn: sqlite3.Connection, user_id: int, cid: int, order: list[int]) -> dict:
    """Новый порядок объектов (список id)."""
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return {"error": "Подборка не найдена."}
    for pos, lid in enumerate(order[:MAX_ITEMS]):
        conn.execute("UPDATE collection_items SET pos = ? WHERE collection_id = ? AND listing_id = ?", (pos, cid, int(lid)))
    conn.commit()
    return {"ok": True}


def with_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[int]:
    """В каких подборках уже есть объект (для окна объекта)."""
    return [r[0] for r in conn.execute("""SELECT c.id FROM collections c JOIN collection_items i ON i.collection_id = c.id
                                           WHERE c.user_id = ? AND i.listing_id = ?""", (user_id, listing_id))]


def public(conn: sqlite3.Connection, token: str, count_view: bool = True) -> dict | None:
    """Для клиента: без телефонов агентов из объявлений, без исходного текста и чатов."""
    c = conn.execute("SELECT * FROM collections WHERE token = ?", (token,)).fetchone()
    if c is None:
        return None
    if count_view:
        try:
            conn.execute("UPDATE collections SET views = views + 1, viewed_at = ? WHERE id = ?", (int(time.time()), c["id"]))
            conn.commit()
        except sqlite3.OperationalError:   # база занята — просмотр не засчитаем, страницу покажем
            conn.rollback()
    items = []
    for r in conn.execute("""SELECT l.*, i.note AS item_note FROM collection_items i JOIN listings l ON l.id = i.listing_id
                             WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (c["id"],)):
        items.append({
            "id": r["id"], "title": r["title"], "type": r["type"], "deal": r["deal"], "price": r["price"],
            "price_m2": r["price_m2"], "rooms": r["rooms"], "room_kind": r["room_kind"], "area": r["area"], "land": r["land"],
            "floor": r["floor"], "floors": r["floors"], "district": r["district"], "complex": r["complex"],
            "settlement": r["settlement"], "street": r["street"], "house": r["house"],
            "lat": r["lat"] if r["geo_status"] != "approx" else None, "lon": r["lon"] if r["geo_status"] != "approx" else None,
            "photos": json.loads(r["photos"] or "[]"),
            "description": parser.strip_phones(r["description"] or "")[:3000],
            "note": r["item_note"] or "", "is_active": r["is_active"]})
    return {"title": c["title"], "note": c["note"], "contact_name": c["contact_name"], "contact_phone": c["contact_phone"],
            "updated": c["updated"], "items": items}

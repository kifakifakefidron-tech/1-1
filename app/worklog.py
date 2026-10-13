"""Журнал работы агента по объекту: заметка / звонок / показ — с привязкой к клиенту и подборке.

Одна запись видна сразу в трёх местах: в окне объекта, в карточке клиента (история) и в подборке (у этого объекта).
Номера телефонов здесь не храним: контакт клиента живёт только в его карточке (clients.phone), подборка ссылается на
клиента, запись журнала — на клиента и подборку. Так один и тот же номер нигде не повторяется.
"""
from __future__ import annotations

import sqlite3
import time

KINDS = {"note": "Заметка", "call": "Звонок", "show": "Показ", "meet": "Встреча"}
MAX_TEXT = 2000


def _pick(conn: sqlite3.Connection, user_id: int, pick_id) -> sqlite3.Row | None:
    if not str(pick_id or "").isdigit():
        return None
    return conn.execute("SELECT id, title, client_id FROM collections WHERE id = ? AND user_id = ?", (int(pick_id), user_id)).fetchone()


def _client(conn: sqlite3.Connection, user_id: int, client_id) -> sqlite3.Row | None:
    if not str(client_id or "").isdigit():
        return None
    return conn.execute("SELECT id, name FROM clients WHERE id = ? AND user_id = ?", (int(client_id), user_id)).fetchone()


def add(conn: sqlite3.Connection, user_id: int, listing_id: int, text: str, kind: str = "note",
        client_id=None, pick_id=None) -> dict:
    text = str(text or "").strip()[:MAX_TEXT]
    kind = kind if kind in KINDS else "note"
    if not text and kind == "note":
        return {"error": "Напишите заметку."}
    if not conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone():
        return {"error": "Объект не найден."}
    pick = _pick(conn, user_id, pick_id)
    client = _client(conn, user_id, client_id)
    if client is None and pick is not None and pick["client_id"]:   # подборка клиента — значит и заметка про этого клиента
        client = _client(conn, user_id, pick["client_id"])
    now = int(time.time())
    nid = conn.execute("""INSERT INTO work_notes (user_id, listing_id, client_id, collection_id, kind, text, ts)
                          VALUES (?,?,?,?,?,?,?)""",
                       (user_id, listing_id, client["id"] if client else None, pick["id"] if pick else None, kind, text, now)).lastrowid
    if client is not None:   # объект — у клиента; показ — сразу «Показ» (вперёд только)
        from . import clients
        cur = conn.execute("SELECT status FROM client_items WHERE client_id = ? AND listing_id = ?",
                           (client["id"], listing_id)).fetchone()
        if kind == "show":
            clients.set_pair(conn, user_id, client["id"], listing_id, "show", pick_id=pick["id"] if pick else None,
                             auto=True, commit=False)
        elif cur is None:
            clients.set_pair(conn, user_id, client["id"], listing_id, "matched", commit=False)
    conn.commit()
    return {"ok": True, "id": nid}


def delete(conn: sqlite3.Connection, user_id: int, note_id: int) -> None:
    conn.execute("DELETE FROM work_notes WHERE id = ? AND user_id = ?", (note_id, user_id))
    conn.commit()


_SELECT = """SELECT n.id, n.ts, n.kind, n.text, n.listing_id, n.client_id, n.collection_id,
                    c.name AS client_name, k.title AS pick_title, l.title AS l_title, l.complex AS l_complex
             FROM work_notes n LEFT JOIN clients c ON c.id = n.client_id AND c.user_id = n.user_id
             LEFT JOIN collections k ON k.id = n.collection_id AND k.user_id = n.user_id
             LEFT JOIN listings l ON l.id = n.listing_id"""


def _rows(conn: sqlite3.Connection, where: str, params: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(f"{_SELECT} WHERE {where} ORDER BY n.ts DESC, n.id DESC LIMIT 300", params)]


def for_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[dict]:
    return _rows(conn, "n.user_id = ? AND n.listing_id = ?", (user_id, listing_id))


def for_client(conn: sqlite3.Connection, user_id: int, client_id: int) -> list[dict]:
    return _rows(conn, "n.user_id = ? AND n.client_id = ?", (user_id, client_id))


def for_pick(conn: sqlite3.Connection, user_id: int, pick_id: int, client_id: int | None) -> dict[int, list[dict]]:
    """Записи по объектам подборки: сделанные в этой подборке и про её клиента."""
    rows = _rows(conn, "n.user_id = ? AND (n.collection_id = ? OR (? IS NOT NULL AND n.client_id = ?))",
                 (user_id, pick_id, client_id, client_id))
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["listing_id"], []).append(r)
    return out


def context(conn: sqlite3.Connection, user_id: int, listing_id: int) -> dict:
    """Для выбора «к кому заметка» в окне объекта: клиенты (сначала те, у кого этот объект) и подборки."""
    has_c = {r[0] for r in conn.execute("SELECT client_id FROM client_items WHERE listing_id = ?", (listing_id,))}
    clients = [{"id": r["id"], "name": r["name"], "has": r["id"] in has_c}
               for r in conn.execute("""SELECT id, name FROM clients WHERE user_id = ? AND archived = 0
                                        ORDER BY updated DESC LIMIT 300""", (user_id,))]
    clients.sort(key=lambda c: not c["has"])
    has_p = {r[0] for r in conn.execute("SELECT collection_id FROM collection_items WHERE listing_id = ?", (listing_id,))}
    picks = [{"id": r["id"], "title": r["title"], "client_id": r["client_id"], "has": r["id"] in has_p}
             for r in conn.execute("SELECT id, title, client_id FROM collections WHERE user_id = ? ORDER BY updated DESC LIMIT 200",
                                   (user_id,))]
    picks.sort(key=lambda p: not p["has"])
    return {"clients": clients, "picks": picks}

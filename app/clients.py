"""CRM агента: клиенты (покупатели, продавцы, арендаторы, арендодатели) и сделки по этапам.

Клиент — карточка: контакт, кто он, что ищет (сделка, комнаты, бюджет, районы), этап, следующий шаг (дата + что сделать),
объекты клиента (предложен → понравился → показан / отказ), история (заметки, смена этапа, объекты, события планера).
Подбор объектов — тем же поиском, что на сайте, по пожеланиям клиента. Всё видно только самому агенту.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import geo, rules

KINDS = {"buyer": "Покупатель", "seller": "Продавец", "tenant": "Арендатор", "landlord": "Арендодатель"}
STAGES = {"new": "Новый", "search": "Подбор", "shows": "Показы", "talks": "Переговоры", "deposit": "Задаток",
          "deal": "Сделка", "lost": "Отказ"}
ITEM_STATUS = {"offered": "Предложен", "liked": "Понравился", "shown": "Показан", "rejected": "Отказ"}
MAX_CLIENTS = 2000


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("rooms", "districts"):
        d[k] = json.loads(d.get(k) or "[]")
    return d


def _log(conn: sqlite3.Connection, cid: int, kind: str, text: str, ts: int | None = None) -> None:
    conn.execute("INSERT INTO client_log (client_id, ts, kind, text) VALUES (?,?,?,?)", (cid, ts or int(time.time()), kind, text[:2000]))


def _own(conn: sqlite3.Connection, user_id: int, cid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clients WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone()


def board(conn: sqlite3.Connection, user_id: int, q: str = "") -> dict:
    """Все клиенты (для доски и списка) + сколько объектов у каждого."""
    rows = conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM client_items i WHERE i.client_id = c.id) AS n_items,
                                  (SELECT COUNT(*) FROM client_items i WHERE i.client_id = c.id AND i.status = 'liked') AS n_liked
                           FROM clients c WHERE c.user_id = ? ORDER BY c.archived, COALESCE(c.next_ts, 9e9), c.updated DESC""",
                        (user_id,)).fetchall()
    items = [_row(r) for r in rows]
    if q:
        ql = q.lower().replace("ё", "е")
        digits = re.sub(r"\D", "", q)
        items = [c for c in items if ql in (c["name"] or "").lower().replace("ё", "е") or
                 (digits and digits in re.sub(r"\D", "", c["phone"] or "")) or ql in (c["note"] or "").lower()]
    return {"items": items, "stages": STAGES, "kinds": KINDS, "item_status": ITEM_STATUS, "now": int(time.time())}


def _clean(body: dict) -> dict:
    kind = body.get("kind") if body.get("kind") in KINDS else "buyer"
    stage = body.get("stage") if body.get("stage") in STAGES else "new"
    deal = "rent" if body.get("deal") == "rent" or kind in ("tenant", "landlord") else "sale"

    def money(v):
        s = re.sub(r"[^\d,.]", "", str(v or "")).replace(",", ".")
        try:
            x = float(s)
        except ValueError:
            return None
        if deal == "sale":
            x = x * 1e6 if x < 1000 else x
        else:
            x = x * 1000 if x < 1000 else x
        return int(x) if x > 0 else None
    rooms = sorted({int(x) for x in (body.get("rooms") or []) if str(x).isdigit() and 0 <= int(x) <= 5})
    districts = []
    for d in body.get("districts") or []:
        cd = geo.canonical_district(str(d))
        if cd and cd not in districts:
            districts.append(cd)
    try:
        next_ts = int(body.get("next_ts")) if body.get("next_ts") else None
    except (TypeError, ValueError):
        next_ts = None
    phone = rules.normalize_phone(str(body.get("phone") or "")) or str(body.get("phone") or "").strip()[:30] or None
    return {"name": str(body.get("name") or "").strip()[:120], "phone": phone, "kind": kind, "stage": stage, "deal": deal,
            "budget_min": money(body.get("budget_min")), "budget_max": money(body.get("budget_max")),
            "rooms": json.dumps(rooms), "districts": json.dumps(districts, ensure_ascii=False),
            "note": str(body.get("note") or "").strip()[:4000], "next_ts": next_ts,
            "next_text": str(body.get("next_text") or "").strip()[:200], "source": str(body.get("source") or "").strip()[:60],
            "archived": 1 if body.get("archived") else 0}


def save(conn: sqlite3.Connection, user_id: int, body: dict) -> dict:
    v = _clean(body)
    if not v["name"] and not v["phone"]:
        return {"error": "Укажите имя или телефон клиента."}
    v["name"] = v["name"] or v["phone"]
    now = int(time.time())
    cid = body.get("id")
    if cid:
        old = _own(conn, user_id, int(cid))
        if old is None:
            return {"error": "Клиент не найден."}
        conn.execute("""UPDATE clients SET name=:name, phone=:phone, kind=:kind, stage=:stage, deal=:deal, budget_min=:budget_min,
                        budget_max=:budget_max, rooms=:rooms, districts=:districts, note=:note, next_ts=:next_ts,
                        next_text=:next_text, source=:source, archived=:archived, updated=:now,
                        stage_ts = CASE WHEN stage != :stage THEN :now ELSE stage_ts END
                        WHERE id=:id AND user_id=:uid""", {**v, "now": now, "id": int(cid), "uid": user_id})
        if old["stage"] != v["stage"]:
            _log(conn, int(cid), "stage", f"Этап: {STAGES[old['stage']]} → {STAGES[v['stage']]}")
        if v["next_ts"] and (old["next_ts"] != v["next_ts"] or old["next_text"] != v["next_text"]):
            _log(conn, int(cid), "next", f"Следующий шаг: {v['next_text'] or 'связаться'}")
    else:
        if conn.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_CLIENTS:
            return {"error": "Слишком много клиентов — отправьте старых в архив и удалите."}
        cid = conn.execute("""INSERT INTO clients (user_id, name, phone, kind, stage, deal, budget_min, budget_max, rooms, districts,
                              note, next_ts, next_text, source, archived, created, updated, stage_ts)
                              VALUES (:uid, :name, :phone, :kind, :stage, :deal, :budget_min, :budget_max, :rooms, :districts,
                              :note, :next_ts, :next_text, :source, :archived, :now, :now, :now)""",
                           {**v, "uid": user_id, "now": now}).lastrowid
        _log(conn, cid, "new", f"Новый клиент: {KINDS[v['kind']].lower()}")
    conn.commit()
    return detail(conn, user_id, int(cid))


def set_stage(conn: sqlite3.Connection, user_id: int, cid: int, stage: str) -> dict:
    old = _own(conn, user_id, cid)
    if old is None or stage not in STAGES:
        return {"error": "Клиент не найден."}
    if old["stage"] != stage:
        now = int(time.time())
        conn.execute("UPDATE clients SET stage = ?, stage_ts = ?, updated = ? WHERE id = ?", (stage, now, now, cid))
        _log(conn, cid, "stage", f"Этап: {STAGES[old['stage']]} → {STAGES[stage]}")
        conn.commit()
    return {"ok": True}


def delete(conn: sqlite3.Connection, user_id: int, cid: int) -> None:
    if _own(conn, user_id, cid):
        conn.execute("DELETE FROM client_items WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM client_log WHERE client_id = ?", (cid,))
        conn.execute("UPDATE planner_events SET client_id = NULL WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM clients WHERE id = ?", (cid,))
        conn.commit()


def detail(conn: sqlite3.Connection, user_id: int, cid: int) -> dict:
    c = _own(conn, user_id, cid)
    if c is None:
        return {"error": "Клиент не найден."}
    items = []
    for r in conn.execute("""SELECT i.status, i.note, i.ts, l.id, l.title, l.price, l.deal, l.complex, l.district, l.street,
                                    l.house, l.photos, l.is_active
                             FROM client_items i JOIN listings l ON l.id = i.listing_id WHERE i.client_id = ?
                             ORDER BY i.ts DESC""", (cid,)):
        photos = json.loads(r["photos"] or "[]")
        items.append({k: r[k] for k in ("id", "title", "price", "deal", "complex", "district", "street", "house", "is_active",
                                        "status", "note", "ts")} | {"photo": photos[0] if photos else None})
    log = [dict(r) for r in conn.execute("SELECT ts, kind, text FROM client_log WHERE client_id = ? ORDER BY ts DESC, id DESC LIMIT 200",
                                         (cid,))]
    events = [dict(r) for r in conn.execute("""SELECT id, kind, title, ts, done, listing_id FROM planner_events
                                               WHERE user_id = ? AND client_id = ? ORDER BY ts DESC LIMIT 50""", (user_id, cid))]
    return {"client": _row(c), "items": items, "log": log, "events": events}


def set_item(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, status: str | None) -> dict:
    """Объект клиенту: status — предложен/понравился/показан/отказ; None — убрать."""
    if _own(conn, user_id, cid) is None:
        return {"error": "Клиент не найден."}
    lst = conn.execute("SELECT id, title, complex FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if lst is None:
        return {"error": "Объект не найден."}
    name = f"ЖК {lst['complex']}, {lst['title']}" if lst["complex"] else lst["title"]
    now = int(time.time())
    if status is None:
        conn.execute("DELETE FROM client_items WHERE client_id = ? AND listing_id = ?", (cid, listing_id))
        _log(conn, cid, "object", f"Убран объект: {name}")
    else:
        status = status if status in ITEM_STATUS else "offered"
        old = conn.execute("SELECT status FROM client_items WHERE client_id = ? AND listing_id = ?", (cid, listing_id)).fetchone()
        conn.execute("""INSERT INTO client_items (client_id, listing_id, status, ts) VALUES (?,?,?,?)
                        ON CONFLICT(client_id, listing_id) DO UPDATE SET status = excluded.status, ts = excluded.ts""",
                     (cid, listing_id, status, now))
        if old is None or old["status"] != status:
            _log(conn, cid, "object", f"{ITEM_STATUS[status]}: {name}")
        # объект клиента — и в избранное агента (рабочий список, планер)
        conn.execute("INSERT OR IGNORE INTO favorites (user_id, listing_id, created, price_at) SELECT ?, id, ?, price FROM listings WHERE id = ?",
                     (user_id, now, listing_id))
    conn.execute("UPDATE clients SET updated = ? WHERE id = ?", (now, cid))
    conn.commit()
    return {"ok": True}


def add_note(conn: sqlite3.Connection, user_id: int, cid: int, text: str) -> dict:
    if _own(conn, user_id, cid) is None:
        return {"error": "Клиент не найден."}
    text = str(text or "").strip()
    if not text:
        return {"error": "Пустая заметка."}
    _log(conn, cid, "note", text)
    conn.execute("UPDATE clients SET updated = ? WHERE id = ?", (int(time.time()), cid))
    conn.commit()
    return {"ok": True}


def match_query(c: dict) -> dict:
    """Пожелания клиента → параметры поиска сайта (/api/listings)."""
    q = {"deal": c.get("deal") or "sale"}
    rooms = c.get("rooms") if isinstance(c.get("rooms"), list) else json.loads(c.get("rooms") or "[]")
    districts = c.get("districts") if isinstance(c.get("districts"), list) else json.loads(c.get("districts") or "[]")
    if rooms:
        q["rooms"] = rooms
    if districts:
        q["district"] = districts
    if c.get("budget_min"):
        q["price_min"] = c["budget_min"]
    if c.get("budget_max"):
        q["price_max"] = int(c["budget_max"] * 1.05)   # чуть выше бюджета — торг
    return q


def for_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[dict]:
    """Кому из клиентов уже предложен объект (для окна объекта)."""
    return [dict(r) for r in conn.execute("""SELECT c.id, c.name, i.status FROM client_items i JOIN clients c ON c.id = i.client_id
                                             WHERE c.user_id = ? AND i.listing_id = ?""", (user_id, listing_id))]


def summary(conn: sqlite3.Connection, user_id: int) -> dict:
    """Для кабинета: клиенты по этапам и ближайшие следующие шаги."""
    st = {k: 0 for k in STAGES}
    for s, n in conn.execute("SELECT stage, COUNT(*) FROM clients WHERE user_id = ? AND archived = 0 GROUP BY stage", (user_id,)):
        st[s] = n
    nxt = [dict(r) for r in conn.execute("""SELECT id, name, next_ts, next_text, stage FROM clients
                                            WHERE user_id = ? AND archived = 0 AND next_ts IS NOT NULL
                                            ORDER BY next_ts LIMIT 5""", (user_id,))]
    return {"stages": [{"key": k, "label": STAGES[k], "n": st[k]} for k in STAGES], "next": nxt,
            "total": sum(st.values())}

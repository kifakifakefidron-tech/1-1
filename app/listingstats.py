"""Статистика объектов для подтверждённого агента (19.10, приёмка п. 2 — «морковка» для подтверждения номера):
сколько раз открывали карточку, открывали номер, добавляли в избранное и подборки клиентам.

Просмотры: каждый процесс сайта копит их в памяти (один человек — один просмотр объекта в день) и раз в 30 с одним
запросом дописывает в listing_views (объект, день, число). Так сайт не пишет в базу на каждое открытие карточки.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
FLUSH_S = 30
_lock = threading.Lock()
_buf: dict[tuple[str, int], int] = {}
_seen: set[tuple[str, int, str]] = set()
_state = {"flush": time.time(), "day": ""}


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS listing_views (listing_id INTEGER NOT NULL, day TEXT NOT NULL,
                    n INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (listing_id, day))""")


def _today() -> str:
    return datetime.now(MSK).strftime("%Y-%m-%d")


def record(conn: sqlite3.Connection, listing_id: int, who: str) -> None:
    """Открыли карточку объекта (who — кто: аккаунт или адрес). Повтор того же человека за день не считаем."""
    day = _today()
    with _lock:
        if _state["day"] != day:
            _seen.clear()
            _state["day"] = day
        key = (day, listing_id, who)
        if key in _seen:
            return
        if len(_seen) > 200_000:
            _seen.clear()
        _seen.add(key)
        _buf[(day, listing_id)] = _buf.get((day, listing_id), 0) + 1
        due = time.time() - _state["flush"] > FLUSH_S
    if due:
        flush(conn)


def flush(conn: sqlite3.Connection) -> None:
    with _lock:
        if not _buf:
            _state["flush"] = time.time()
            return
        items = list(_buf.items())
        _buf.clear()
        _state["flush"] = time.time()
    try:
        _ensure(conn)
        conn.executemany("""INSERT INTO listing_views (listing_id, day, n) VALUES (?, ?, ?)
                            ON CONFLICT(listing_id, day) DO UPDATE SET n = n + excluded.n""",
                         [(lid, day, n) for (day, lid), n in items])
        conn.commit()
    except sqlite3.OperationalError:   # база занята — вернём в копилку, допишем в следующий раз
        conn.rollback()
        with _lock:
            for k, n in items:
                _buf[k] = _buf.get(k, 0) + n


def for_listings(conn: sqlite3.Connection, rows: list) -> dict[int, dict]:
    """По объектам агента: просмотры за 30 и 7 дней, открытия номера за 30 дней, в избранном, в подборках клиентам.
    («у других агентов дешевле» убрано 19.10 по решению Артёма — сравнение ошибалось.)"""
    _ensure(conn)
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    d30 = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    d7 = (datetime.now(MSK) - timedelta(days=7)).strftime("%Y-%m-%d")
    now = int(time.time())
    out = {i: {"views": 0, "views7": 0, "phone": 0, "fav": 0, "picks": 0} for i in ids}
    for lid, n30, n7 in conn.execute(f"""SELECT listing_id, SUM(n), SUM(CASE WHEN day >= ? THEN n ELSE 0 END) FROM listing_views
                                         WHERE listing_id IN ({q}) AND day >= ? GROUP BY listing_id""", [d7, *ids, d30]):
        out[lid]["views"], out[lid]["views7"] = n30, n7
    for lid, n in conn.execute(f"SELECT listing_id, COUNT(*) FROM phone_views WHERE listing_id IN ({q}) AND ts >= ? GROUP BY 1",
                               [*ids, now - 30 * 86400]):
        out[lid]["phone"] = n
    for lid, n in conn.execute(f"SELECT listing_id, COUNT(*) FROM favorites WHERE listing_id IN ({q}) GROUP BY 1", ids):
        out[lid]["fav"] = n
    for lid, n in conn.execute(f"SELECT listing_id, COUNT(*) FROM collection_items WHERE listing_id IN ({q}) GROUP BY 1", ids):
        out[lid]["picks"] = n
    return out


def summary(conn: sqlite3.Connection, user_id: int) -> dict:
    """Для кабинета: всего по объектам агента за 7 дней и три самых просматриваемых."""
    from . import agent
    try:
        rows = [r for r in agent.my_listings(conn, user_id) if r["is_active"]]
    except Exception:  # noqa: BLE001
        rows = []
    verified = bool(conn.execute("SELECT 1 FROM agent_phones WHERE user_id = ?", (user_id,)).fetchone())
    st = for_listings(conn, rows[:200]) if rows else {}
    top = sorted(rows, key=lambda r: -(st.get(r["id"], {}).get("views", 0)))[:3]
    return {"verified": verified, "listings": len(rows),
            "views7": sum(s["views7"] for s in st.values()), "views": sum(s["views"] for s in st.values()),
            "phone": sum(s["phone"] for s in st.values()), "picks": sum(s["picks"] for s in st.values()),
            "top": [{"id": r["id"], "title": r["title"], "complex": r["complex"], "price": r["price"], "deal": r["deal"],
                     **st.get(r["id"], {})} for r in top]}


RECENT_KEEP = 40


def remember(conn: sqlite3.Connection, user_id: int, listing_id: int) -> None:
    """24.10: «Недавно смотрели» — человек открыл объект. База занята — пропускаем (это не главное)."""
    try:
        conn.execute("""INSERT INTO recent_views (user_id, listing_id, ts) VALUES (?, ?, ?)
                        ON CONFLICT(user_id, listing_id) DO UPDATE SET ts = excluded.ts""", (user_id, listing_id, int(time.time())))
        conn.execute("""DELETE FROM recent_views WHERE user_id = ? AND listing_id NOT IN
                        (SELECT listing_id FROM recent_views WHERE user_id = ? ORDER BY ts DESC LIMIT ?)""",
                     (user_id, user_id, RECENT_KEEP))
        conn.commit()
    except sqlite3.OperationalError:
        conn.rollback()


def recent(conn: sqlite3.Connection, user_id: int, limit: int = 8) -> list[dict]:
    out = []
    # 24.10c: поля как у ленты главной — карточка на сайте одна (web/lcard.js)
    for r in conn.execute("""SELECT l.id, l.title, l.complex, l.district, l.settlement, l.street, l.house, l.price, l.price_m2,
                                    l.deal, l.photos, l.is_active, l.price_doubt, l.first_seen, l.last_seen, v.ts
                             FROM recent_views v JOIN listings l ON l.id = v.listing_id
                             WHERE v.user_id = ? ORDER BY v.ts DESC LIMIT ?""", (user_id, limit)):
        ph = json.loads(r["photos"] or "[]")
        out.append({k: r[k] for k in ("id", "title", "complex", "district", "settlement", "street", "house", "price", "price_m2",
                                      "deal", "is_active", "first_seen", "last_seen", "ts")}
                   | {"photo": ph[0] if ph else None, "photos": ph[:10], "price_doubt": bool(r["price_doubt"])})
    return out

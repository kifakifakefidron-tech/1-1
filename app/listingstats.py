"""Статистика объектов для подтверждённого агента (19.10, приёмка п. 2 — «морковка» для подтверждения номера):
сколько раз открывали карточку, открывали номер, добавляли в избранное и подборки клиентам, и не дешевле ли такой же
объект у других агентов.

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
    """По объектам агента: просмотры за 30 и 7 дней, открытия номера за 30 дней, в избранном, в подборках клиентам,
    и разница с самым дешёвым таким же объектом у других агентов (hooks.same_elsewhere — строгое сравнение)."""
    from . import hooks
    _ensure(conn)
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    d30 = (datetime.now(MSK) - timedelta(days=30)).strftime("%Y-%m-%d")
    d7 = (datetime.now(MSK) - timedelta(days=7)).strftime("%Y-%m-%d")
    now = int(time.time())
    out = {i: {"views": 0, "views7": 0, "phone": 0, "fav": 0, "picks": 0, "cheaper": None} for i in ids}
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
    for r in rows:
        if not r["is_active"] or not r["price"] or r["source"] == "feed":
            continue
        try:
            same = hooks.same_elsewhere(conn, r)
        except Exception:  # noqa: BLE001 — сравнение — подсказка, без него статистика всё равно нужна
            same = []
        prices = [s["price"] for s in same if s.get("price")]
        if prices and min(prices) < r["price"]:
            out[r["id"]]["cheaper"] = {"diff": r["price"] - min(prices), "n": len(prices)}
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
            "cheaper": sum(1 for s in st.values() if s["cheaper"]),
            "top": [{"id": r["id"], "title": r["title"], "complex": r["complex"], "price": r["price"], "deal": r["deal"],
                     **st.get(r["id"], {})} for r in top]}

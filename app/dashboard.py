"""Кабинет риелтора одним запросом: день (события), работа с объектами (CRM), уведомления, рынок,
погода и ставки (app/widgets.py). Тяжёлое (рынок) — с кэшем на 10 минут."""
from __future__ import annotations

import time
from datetime import datetime

from . import agent, clients, market, widgets
from .planner import MSK, events

STATUS_LABELS = {"call": "Звонок", "show": "Показ", "think": "Думает", "refuse": "Отказ"}
_market_cache: dict = {"t": 0.0, "v": None}


def _market(conn) -> dict:
    if time.time() - _market_cache["t"] > 600 or _market_cache["v"] is None:
        cur = market.current(conn, "sale")
        top = sorted((g for (kind, _), g in cur["groups"].items() if kind == "d"), key=lambda g: -g["n"])[:5]
        _market_cache.update(t=time.time(), v={"city": cur["city"], "districts": top})
    return _market_cache["v"]


def build(conn, user) -> dict:
    now = int(time.time())
    today = datetime.fromtimestamp(now, MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(today.timestamp())
    uid = user["id"]
    agenda = events(conn, uid, t0, t0 + 2 * 86400)
    overdue = conn.execute("SELECT COUNT(*) FROM planner_events WHERE user_id = ? AND done = 0 AND ts < ?",
                           (uid, t0)).fetchone()[0]
    crm = {k: 0 for k in STATUS_LABELS}
    for (st,) in conn.execute("SELECT status FROM notes WHERE user_id = ? AND status IS NOT NULL", (uid,)):
        if st in crm:
            crm[st] += 1
    fav = conn.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (uid,)).fetchone()[0]
    notices = [dict(r) for r in conn.execute(
        """SELECT id, kind, title, body, url, ts, read FROM notices WHERE user_id = ? ORDER BY ts DESC LIMIT 4""", (uid,))]
    unread = conn.execute("SELECT COUNT(*) FROM notices WHERE user_id = ? AND read = 0", (uid,)).fetchone()[0]
    saved = conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (uid,)).fetchone()[0]
    picks = conn.execute("SELECT COUNT(*), COALESCE(SUM(views), 0) FROM collections WHERE user_id = ?", (uid,)).fetchone()
    try:
        mine = len(agent.my_listings(conn, uid))
    except Exception:  # noqa: BLE001 — номер ещё не подтверждён
        mine = 0
    new_today = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1 AND first_seen >= ?", (t0,)).fetchone()[0]
    return {
        "now": now, "today": t0,
        "agenda": agenda, "overdue": overdue,
        "crm": [{"key": k, "label": v, "n": crm[k]} for k, v in STATUS_LABELS.items()], "favorites": fav,
        "notices": notices, "unread": unread, "saved": saved,
        "picks": {"n": picks[0], "views": picks[1]}, "mine": mine, "new_today": new_today,
        "market": _market(conn),
        "clients": clients.summary(conn, uid),
        "widgets": widgets.all_widgets(conn),
    }

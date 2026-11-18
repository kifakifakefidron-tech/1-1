"""Кабинет риелтора одним запросом: день (события), работа с объектами (CRM), уведомления, рынок,
погода и ставки (app/widgets.py). Тяжёлое (рынок) — с кэшем на 10 минут."""
from __future__ import annotations

import time
from datetime import datetime

from . import accounts, agent, clients, market, notices, widgets
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
    access = accounts.has_access(user)
    # 24.10d: «Мой день» — неделя календаря (пн–вс) и список запланированного на 14 дней вперёд
    monday = t0 - today.weekday() * 86400
    agenda = events(conn, uid, min(monday, t0), max(monday + 7 * 86400, t0 + 14 * 86400)) if access else []
    overdue = conn.execute("SELECT COUNT(*) FROM planner_events WHERE user_id = ? AND done = 0 AND ts < ?",
                           (uid, t0)).fetchone()[0] if access else 0
    crm = {k: 0 for k in STATUS_LABELS}
    for (st,) in conn.execute("SELECT status FROM notes WHERE user_id = ? AND status IS NOT NULL AND ?", (uid, access)):
        if st in crm:
            crm[st] += 1
    fav = conn.execute("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (uid,)).fetchone()[0]
    entries = notices.items(conn, uid, 4, access=access)
    unread = notices.unread(conn, uid, access=access)
    saved = conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (uid,)).fetchone()[0] if access else 0
    picks = conn.execute("SELECT COUNT(*), COALESCE(SUM(views), 0) FROM collections WHERE user_id = ?", (uid,)).fetchone() if access else (0, 0)
    try:
        mine = len(agent.my_listings(conn, uid))
    except Exception:  # noqa: BLE001 — номер ещё не подтверждён
        mine = 0
    new_today = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1 AND catalog_migrated = 0 AND first_seen >= ?", (t0,)).fetchone()[0]
    return {
        "now": now, "today": t0, "week_start": monday, "access": access,
        "agenda": agenda, "overdue": overdue,
        "crm": [{"key": k, "label": v, "n": crm[k]} for k, v in STATUS_LABELS.items()], "favorites": fav,
        "notices": entries, "unread": unread, "saved": saved,
        "picks": {"n": picks[0], "views": picks[1]}, "mine": mine, "new_today": new_today,
        "market": _market(conn),
        "clients": clients.summary(conn, uid) if access else None,
        # 19.10: запросы покупателей — в кабинете наверху (всем с подпиской), «под ваши объекты» — по подтверждённым номерам
        "requests": __import__("app.buyers", fromlist=["summary"]).summary(conn, uid) if access
                    else {"locked": True},
        "widgets": widgets.all_widgets(conn),
    }

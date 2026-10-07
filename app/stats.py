"""Подробная статистика для админки: объявления (добавлено/снято по дням и причинам), сообщения и чаты,
качество разбора, пользователи и их действия. Только чтение. Сутки — по Москве/Краснодару (UTC+3)."""
from __future__ import annotations

import sqlite3
import statistics
import time

from . import config, db

TZ = 3 * 3600
REASONS = {"stale": "не присылали 20 дней", "sold": "агент отметил «продано»", "feed": "снято с сайта СТРЕЛ",
           "chat": "чат отключён", "expired": "кончился срок своего объявления", "other": "другое (жалоба, копия СТРЕЛ, чужой город)"}
TYPES = {"flat": "Квартиры", "new": "Квартиры", "room": "Комнаты", "house": "Дома", "land": "Участки", "commercial": "Коммерция"}
VISIBLE = "is_active = 1 AND (district IS NOT NULL OR complex IS NOT NULL OR street IS NOT NULL OR settlement IS NOT NULL)"


def day_start(now: int) -> int:
    return (now + TZ) // 86400 * 86400 - TZ


def collect(conn: sqlite3.Connection, now: int | None = None) -> dict:
    now = int(now or time.time())
    today = day_start(now)
    d7, d30 = today - 6 * 86400, today - 29 * 86400   # «за 7 дней» — сегодня и 6 предыдущих
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0] or 0   # noqa: E731
    periods = {"today": today, "yesterday": today - 86400, "d7": d7, "d30": d30}

    def counts(col: str, where: str = "1") -> dict:
        return {"today": one(f"SELECT COUNT(*) FROM listings WHERE {where} AND {col} >= ?", today),
                "yesterday": one(f"SELECT COUNT(*) FROM listings WHERE {where} AND {col} >= ? AND {col} < ?",
                                 today - 86400, today),
                "d7": one(f"SELECT COUNT(*) FROM listings WHERE {where} AND {col} >= ?", d7),
                "d30": one(f"SELECT COUNT(*) FROM listings WHERE {where} AND {col} >= ?", d30)}

    # ─── объявления ───
    listings = {
        "visible": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE}"),
        "active": one("SELECT COUNT(*) FROM listings WHERE is_active = 1"),
        "added": counts("first_seen"),
        "removed": counts("removed_at", "is_active = 0"),
        # повторные публикации уже известных объектов (репосты, тот же объект снова) — объекты «живые»
        "reposted": {k: one("SELECT COUNT(DISTINCT listing_id) FROM listing_events WHERE match != 'new' AND ts >= ?"
                            + (" AND ts < ?" if k == "yesterday" else ""), *((v, today) if k == "yesterday" else (v,)))
                     for k, v in periods.items()},
        "price_down_d7": one("""SELECT COUNT(*) FROM listings WHERE is_active = 1 AND prev_price > price
                                AND price_changed_at >= ?""", d7),
        "price_up_d7": one("""SELECT COUNT(*) FROM listings WHERE is_active = 1 AND prev_price < price
                              AND price_changed_at >= ?""", d7),
        "removed_reasons": [{"reason": REASONS.get(r[0] or "other", r[0]), "today": r[1], "d7": r[2]} for r in conn.execute(
            """SELECT removed_reason, SUM(removed_at >= ?), COUNT(*) FROM listings
               WHERE is_active = 0 AND removed_at >= ? GROUP BY 1 ORDER BY 3 DESC""", (today, d7))],
        "by_source": {r[0]: r[1] for r in conn.execute(f"SELECT source, COUNT(*) FROM listings WHERE {VISIBLE} GROUP BY 1")},
    }
    # по дням за 14 дней: добавлено / снято / сообщений
    days = []
    for i in range(13, -1, -1):
        a, b = today - i * 86400, today - (i - 1) * 86400
        days.append({"day": a, "added": one("SELECT COUNT(*) FROM listings WHERE first_seen >= ? AND first_seen < ?", a, b),
                     "removed": one("SELECT COUNT(*) FROM listings WHERE is_active = 0 AND removed_at >= ? AND removed_at < ?", a, b),
                     "messages": one("SELECT COUNT(*) FROM messages WHERE ts >= ? AND ts < ?", a, b)})
    # что лежит на сайте: тип × сделка
    mix: dict[str, dict] = {}
    for r in conn.execute(f"SELECT type, deal, COUNT(*) n FROM listings WHERE {VISIBLE} GROUP BY 1, 2"):
        row = mix.setdefault(TYPES.get(r["type"], r["type"]), {"sale": 0, "rent": 0})
        row[r["deal"]] = row.get(r["deal"], 0) + r["n"]
    top_districts = [dict(r) for r in conn.execute(
        f"SELECT district AS name, COUNT(*) n FROM listings WHERE {VISIBLE} AND district IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 15")]
    top_complexes = [dict(r) for r in conn.execute(
        f"SELECT complex AS name, COUNT(*) n FROM listings WHERE {VISIBLE} AND complex IS NOT NULL GROUP BY cxkey(complex) ORDER BY 2 DESC LIMIT 15")]
    m2 = [r[0] for r in conn.execute(f"""SELECT price_m2 FROM listings WHERE {VISIBLE} AND deal = 'sale'
                                         AND type IN ('flat', 'new') AND price_m2 BETWEEN 30000 AND 1000000""")]
    prices = {
        "flat_m2_median": int(statistics.median(m2)) if m2 else None,
        "flat_sale_median": int(statistics.median(p)) if (p := [r[0] for r in conn.execute(
            f"SELECT price FROM listings WHERE {VISIBLE} AND deal = 'sale' AND type IN ('flat', 'new') AND price > 0")]) else None,
        "rent_median": int(statistics.median(p)) if (p := [r[0] for r in conn.execute(
            f"SELECT price FROM listings WHERE {VISIBLE} AND deal = 'rent' AND type IN ('flat', 'new') AND price > 0")]) else None,
    }

    # ─── качество разбора ───
    quality = {
        "no_district": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND district IS NULL"),
        "flats_no_complex": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND type IN ('flat','new') AND complex IS NULL"),
        "no_map": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND lat IS NULL"),
        "approx_map": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND geo_status = 'approx'"),
        "no_price": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND (price IS NULL OR price = 0)"),
        "no_area": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND area IS NULL AND type IN ('flat','new','room','commercial')"),
        "no_photo": one(f"SELECT COUNT(*) FROM listings WHERE {VISIBLE} AND photos = '[]'"),
        "hidden_no_place": one("""SELECT COUNT(*) FROM listings WHERE is_active = 1 AND district IS NULL AND complex IS NULL
                                  AND street IS NULL AND settlement IS NULL"""),
        "admin_fixed": one("SELECT COUNT(*) FROM listings WHERE admin_fixed = 1"),
        "learned_rules": one("SELECT COUNT(*) FROM learned_rules"),
        "learned_points": one("SELECT COUNT(*) FROM geo_learned"),
    }

    # ─── сообщения и чаты ───
    msg_kinds = {r[0] or "—": r[1] for r in conn.execute(
        "SELECT CASE WHEN status = 'skipped' THEN 'skipped' WHEN status = 'error' THEN 'error' ELSE kind END, COUNT(*) "
        "FROM messages WHERE ts >= ? GROUP BY 1", (d7,))}
    messages = {
        "today": one("SELECT COUNT(*) FROM messages WHERE ts >= ?", today),
        "d7": one("SELECT COUNT(*) FROM messages WHERE ts >= ?", d7),
        "queue": one("SELECT COUNT(*) FROM messages WHERE status = 'new'"),
        "errors_d7": one("SELECT COUNT(*) FROM messages WHERE status = 'error' AND ts >= ?", d7),
        "kinds_d7": msg_kinds,   # listing | request | other | skipped | error
        "by_source_d7": {r[0]: r[1] for r in conn.execute("SELECT source, COUNT(*) FROM messages WHERE ts >= ? GROUP BY 1", (d7,))},
        "chats_active_d7": one("SELECT COUNT(DISTINCT source || chat_id) FROM messages WHERE ts >= ?", d7),
        "chats_blocked": one("SELECT COUNT(*) FROM chats WHERE blocked = 1"),
    }
    top_chats = [dict(r) for r in conn.execute(
        """SELECT COALESCE(NULLIF(c.name, ''), NULLIF(m.chat_name, ''), m.chat_id) AS name, m.source,
                  COUNT(*) AS messages, SUM(m.kind = 'listing') AS listings_msgs,
                  (SELECT COUNT(*) FROM listing_events e JOIN messages m2 ON m2.id = e.message_id
                    WHERE m2.source = m.source AND m2.chat_id = m.chat_id AND e.match = 'new' AND m2.ts >= ?) AS new_objects
           FROM messages m LEFT JOIN chats c ON c.source = m.source AND c.chat_id = m.chat_id
           WHERE m.ts >= ? GROUP BY m.source, m.chat_id ORDER BY new_objects DESC, messages DESC LIMIT 15""", (d7, d7))]

    # ─── пользователи ───
    users = {
        "total": one("SELECT COUNT(*) FROM users"),
        "new": {"today": one("SELECT COUNT(*) FROM users WHERE created >= ?", today),
                "d7": one("SELECT COUNT(*) FROM users WHERE created >= ?", d7),
                "d30": one("SELECT COUNT(*) FROM users WHERE created >= ?", d30)},
        "active": {"today": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", today),
                   "d7": one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", d7)},
        "trial": one("SELECT COUNT(*) FROM users WHERE trial_until > ? AND paid_until <= ? AND is_admin = 0", now, now),
        "paid": one("SELECT COUNT(*) FROM users WHERE paid_until > ?", now),
        "access_ends_3d": one("""SELECT COUNT(*) FROM users WHERE is_admin = 0 AND MAX(trial_until, paid_until) > ?
                                 AND MAX(trial_until, paid_until) < ?""", now, now + 3 * 86400),
        "expired": one("SELECT COUNT(*) FROM users WHERE is_admin = 0 AND MAX(trial_until, paid_until) BETWEEN 1 AND ?", now),
        "phone_views": {"today": one("SELECT COUNT(*) FROM phone_views WHERE ts >= ?", today),
                        "d7": one("SELECT COUNT(*) FROM phone_views WHERE ts >= ?", d7)},
        "phone_viewers_d7": one("SELECT COUNT(DISTINCT user_id) FROM phone_views WHERE ts >= ?", d7),
        "favorites": one("SELECT COUNT(*) FROM favorites"),
        "favorites_d7": one("SELECT COUNT(*) FROM favorites WHERE created >= ?", d7),
        "saved_searches": one("SELECT COUNT(*) FROM saved_searches WHERE active = 1"),
        "notices_d7": one("SELECT COUNT(*) FROM notices WHERE ts >= ?", d7),
        "notes": one("SELECT COUNT(*) FROM notes"),
        "agents_verified": one("SELECT COUNT(DISTINCT user_id) FROM agent_phones"),
        "own_listings": one("SELECT COUNT(*) FROM listings WHERE source = 'own' AND is_active = 1"),
        "agent_edits_d7": one("SELECT COUNT(*) FROM listing_edits WHERE ts >= ?", d7),
        "complaints_new": one("SELECT COUNT(*) FROM complaints WHERE status = 'new'"),
        "optouts": one("SELECT COUNT(*) FROM optout_phones"),
    }
    most_viewed = [dict(r) for r in conn.execute(
        """SELECT l.id, l.title, l.price, l.deal, COUNT(*) AS views FROM phone_views v JOIN listings l ON l.id = v.listing_id
           WHERE v.ts >= ? GROUP BY v.listing_id ORDER BY views DESC LIMIT 10""", (d7,))]
    most_fav = [dict(r) for r in conn.execute(
        """SELECT l.id, l.title, l.price, l.deal, COUNT(*) AS n FROM favorites f JOIN listings l ON l.id = f.listing_id
           WHERE l.is_active = 1 GROUP BY f.listing_id ORDER BY n DESC LIMIT 10""")]

    # ─── фоновая работа ───
    tick = int(db.get_state(conn, "worker_tick") or 0)
    system = {"worker_tick": tick, "worker_ok": now - tick < 15 * 60 if tick else None,
              "feed_synced": int(db.get_state(conn, "feed_synced") or 0),
              "wappi_enabled": config.WAPPI_ENABLED, "now": now, "today": today}
    return {"listings": listings, "days": days, "mix": mix, "top_districts": top_districts,
            "top_complexes": top_complexes, "prices": prices, "quality": quality, "messages": messages,
            "top_chats": top_chats, "users": users, "most_viewed": most_viewed, "most_fav": most_fav,
            "system": system, "periods": periods}

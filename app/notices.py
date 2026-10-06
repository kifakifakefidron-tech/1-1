"""Уведомления на сайте (страница /notifications и колокольчик в шапке).

Что приходит:
  * search  — новые объекты по поиску, за которым человек следит;
  * price   — изменилась цена объекта из избранного (снизилась/выросла);
  * gone    — объект из избранного снят с сайта;
  * sub     — доступ к номерам скоро закончится / закончился;
  * own     — свой объект агента: «ещё актуален?» (с кнопками) и «снят по сроку».
Письма на почту дублируют самое важное (новые объекты по поиску, снижение цены, актуальность своего объекта).
"""
from __future__ import annotations

import json
import sqlite3
import time

DAY = 86400
KEEP_DAYS = 60
ICONS = {"search": "🔔", "price": "₽", "gone": "✕", "sub": "★", "own": "🏠"}


def add(conn: sqlite3.Connection, user_id: int, kind: str, title: str, body: str = "", *,
        url: str | None = None, listing_id: int | None = None, actions: list[dict] | None = None,
        dedup_s: int = DAY) -> bool:
    """Добавить уведомление. Такое же (тот же вид и объект/ссылка) за последние сутки не дублируем."""
    now = int(time.time())
    dup = conn.execute("""SELECT 1 FROM notices WHERE user_id = ? AND kind = ? AND COALESCE(listing_id, 0) = ?
                          AND COALESCE(url, '') = ? AND title = ? AND ts > ?""",
                       (user_id, kind, listing_id or 0, url or "", title, now - dedup_s)).fetchone()
    if dup:
        return False
    conn.execute("""INSERT INTO notices (user_id, ts, kind, title, body, url, listing_id, actions)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (user_id, now, kind, title[:200], body[:1000], url, listing_id,
                  json.dumps(actions, ensure_ascii=False) if actions else None))
    return True


def unread(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM notices WHERE user_id = ? AND read = 0", (user_id,)).fetchone()[0]


def items(conn: sqlite3.Connection, user_id: int, limit: int = 100) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT n.*, l.photos, l.is_active FROM notices n LEFT JOIN listings l ON l.id = n.listing_id
                             WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?""", (user_id, limit)):
        d = {k: r[k] for k in ("id", "ts", "kind", "title", "body", "url", "listing_id", "read", "is_active")}
        d["actions"] = json.loads(r["actions"]) if r["actions"] else []
        photos = json.loads(r["photos"] or "[]") if r["photos"] else []
        d["photo"] = photos[0] if photos else None
        d["icon"] = ICONS.get(r["kind"], "•")
        out.append(d)
    return out


def mark_read(conn: sqlite3.Connection, user_id: int, notice_id: int | None = None) -> None:
    if notice_id:
        conn.execute("UPDATE notices SET read = 1 WHERE user_id = ? AND id = ?", (user_id, notice_id))
    else:
        conn.execute("UPDATE notices SET read = 1 WHERE user_id = ? AND read = 0", (user_id,))
    conn.commit()


def cleanup(conn: sqlite3.Connection) -> int:
    n = conn.execute("DELETE FROM notices WHERE ts < ?", (int(time.time()) - KEEP_DAYS * DAY,)).rowcount
    conn.commit()
    return n


def check_subscriptions(conn: sqlite3.Connection, fmt_day) -> int:
    """Доступ к номерам заканчивается через ≤2 дня или закончился — одно уведомление на каждое событие."""
    now = int(time.time())
    made = 0
    for u in conn.execute("""SELECT id, MAX(trial_until, paid_until) AS until, sub_notified FROM users
                             WHERE is_admin = 0 AND blocked = 0 AND MAX(trial_until, paid_until) > ?""",
                          (now - 3 * DAY,)).fetchall():
        until = u["until"]
        if now < until <= now + 2 * DAY:
            tag, title = f"soon:{until}", f"Доступ к номерам закончится {fmt_day(until)}"
            body = "Продлите подписку, чтобы и дальше видеть телефоны агентов."
        elif until <= now:
            tag, title = f"end:{until}", "Доступ к номерам закончился"
            body = "Телефоны агентов снова скрыты. Продлите подписку в личном кабинете."
        else:
            continue
        if u["sub_notified"] == tag:
            continue
        add(conn, u["id"], "sub", title, body, url="/?cabinet=1")
        conn.execute("UPDATE users SET sub_notified = ? WHERE id = ?", (tag, u["id"]))
        made += 1
    conn.commit()
    return made

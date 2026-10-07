"""Планер агента: показы, созвоны, встречи и задачи в календаре + свои заметки.

* событие можно привязать к объекту (из окна объекта «📅 Запланировать») и к контакту агента;
* напоминание за N минут — уведомлением на сайте (колокольчик) и письмом;
* календарь телефона: личная ссылка-подписка .ics (iPhone / Google Календарь сами подтягивают новые события).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from . import config

KINDS = {"show": "🏠 Показ", "call": "📞 Созвон", "meet": "🤝 Встреча", "task": "✅ Задача"}
REMINDS = (0, 15, 30, 60, 180, 1440)
MSK = timezone(timedelta(hours=3))
MAX_EVENTS = 5000
MAX_NOTES = 500


def _clean_phone(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10:
        return "+7" + d
    return (v or "").strip()[:30]


def _event_row(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in r.keys() if not k.startswith("l_")}
    if r["listing_id"]:
        photos = json.loads(r["l_photos"] or "[]") if r["l_photos"] else []
        d["listing"] = {"id": r["listing_id"], "title": r["l_title"], "price": r["l_price"], "deal": r["l_deal"],
                        "complex": r["l_complex"], "district": r["l_district"], "street": r["l_street"],
                        "house": r["l_house"], "photo": photos[0] if photos else None, "is_active": r["l_active"]}
    return d


_SELECT = """SELECT e.*, l.title AS l_title, l.price AS l_price, l.deal AS l_deal, l.complex AS l_complex,
                    l.district AS l_district, l.street AS l_street, l.house AS l_house, l.photos AS l_photos,
                    l.is_active AS l_active
             FROM planner_events e LEFT JOIN listings l ON l.id = e.listing_id"""


def events(conn: sqlite3.Connection, user_id: int, frm: int, to: int) -> list[dict]:
    rows = conn.execute(f"{_SELECT} WHERE e.user_id = ? AND e.ts >= ? AND e.ts < ? ORDER BY e.ts LIMIT 2000",
                        (user_id, frm, to)).fetchall()
    return [_event_row(r) for r in rows]


def overview(conn: sqlite3.Connection, user_id: int, now: int | None = None) -> dict:
    """Для страницы: просроченные (не выполненные), ближайшие 60 дней, отметки дней на 3 месяца, заметки."""
    now = int(now or time.time())
    today = datetime.fromtimestamp(now, MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = int(today.timestamp())
    overdue = [_event_row(r) for r in conn.execute(
        f"{_SELECT} WHERE e.user_id = ? AND e.done = 0 AND e.ts < ? ORDER BY e.ts DESC LIMIT 100", (user_id, t0))]
    upcoming = events(conn, user_id, t0, t0 + 60 * 86400)
    days = {}
    for r in conn.execute("""SELECT ts, kind, done FROM planner_events WHERE user_id = ? AND ts >= ? AND ts < ?""",
                          (user_id, t0 - 62 * 86400, t0 + 120 * 86400)):
        day = datetime.fromtimestamp(r[0], MSK).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(r[1])
    return {"overdue": overdue, "upcoming": upcoming, "days": days, "notes": notes(conn, user_id),
            "favorites": favorites(conn, user_id),
            "object_notes": object_notes(conn, user_id), "now": now, "today": t0,
            "feed": feed_url(user_id), "kinds": KINDS}


def favorites(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Объекты для выбора в событии: избранное (и «в работе»), свежие сверху."""
    out = []
    for r in conn.execute("""SELECT l.id, l.title, l.price, l.deal, l.complex, l.district, l.street, l.house, l.photos,
                                    l.phones, l.is_active, n.status
                             FROM listings l LEFT JOIN favorites f ON f.listing_id = l.id AND f.user_id = ?
                             LEFT JOIN notes n ON n.listing_id = l.id AND n.user_id = ?
                             WHERE f.user_id IS NOT NULL OR n.status IS NOT NULL
                             ORDER BY COALESCE(n.ts, f.created) DESC LIMIT 300""", (user_id, user_id)):
        photos = json.loads(r["photos"] or "[]")
        out.append({"id": r["id"], "title": r["title"], "price": r["price"], "deal": r["deal"], "complex": r["complex"],
                    "district": r["district"], "street": r["street"], "house": r["house"],
                    "photo": photos[0] if photos else None, "is_active": r["is_active"], "status": r["status"]})
    return out


def for_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[dict]:
    """Запланированное по объекту (для окна объекта)."""
    return [{"id": r[0], "kind": r[1], "ts": r[2], "title": r[3], "done": r[4]} for r in conn.execute(
        """SELECT id, kind, ts, title, done FROM planner_events WHERE user_id = ? AND listing_id = ?
           AND ts >= ? ORDER BY ts LIMIT 10""", (user_id, listing_id, int(time.time()) - 86400))]


def save_event(conn: sqlite3.Connection, user_id: int, body: dict) -> dict:
    """Создать или изменить событие. Возвращает событие или {'error': …}."""
    kind = body.get("kind") if body.get("kind") in KINDS else "show"
    try:
        ts = int(body.get("ts"))
    except (TypeError, ValueError):
        return {"error": "Укажите дату и время."}
    if not 1_600_000_000 < ts < 4_000_000_000:
        return {"error": "Неверная дата."}
    title = str(body.get("title") or "").strip()[:200] or KINDS[kind].split(" ", 1)[1]
    try:
        dur = max(5, min(int(body.get("dur_min") or 60), 24 * 60))
        remind = int(body.get("remind_min") if body.get("remind_min") is not None else 60)
    except (TypeError, ValueError):
        dur, remind = 60, 60
    remind = remind if remind in REMINDS else 60
    listing_id = body.get("listing_id")
    try:
        listing_id = int(listing_id) if listing_id else None
    except (TypeError, ValueError):
        listing_id = None
    if listing_id and not conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone():
        listing_id = None
    if listing_id:   # в планере объекты выбираются из избранного — запланированный объект туда и кладём
        conn.execute("""INSERT OR IGNORE INTO favorites (user_id, listing_id, created, price_at)
                        SELECT ?, id, ?, price FROM listings WHERE id = ?""", (user_id, int(time.time()), listing_id))
    vals = dict(kind=kind, title=title, ts=ts, dur_min=dur, listing_id=listing_id,
                contact_name=str(body.get("contact_name") or "").strip()[:100],
                contact_phone=_clean_phone(str(body.get("contact_phone") or "")),
                place=str(body.get("place") or "").strip()[:200],
                note=str(body.get("note") or "").strip()[:3000], remind_min=remind,
                done=1 if body.get("done") else 0)
    now = int(time.time())
    eid = body.get("id")
    if eid:
        old = conn.execute("SELECT ts, remind_min FROM planner_events WHERE id = ? AND user_id = ?",
                           (int(eid), user_id)).fetchone()
        if old is None:
            return {"error": "Событие не найдено."}
        # перенесли время или напоминание — напомнить заново
        reminded = "0" if (old["ts"] != ts or old["remind_min"] != remind) else "reminded"
        conn.execute(f"""UPDATE planner_events SET kind=:kind, title=:title, ts=:ts, dur_min=:dur_min, listing_id=:listing_id,
                            contact_name=:contact_name, contact_phone=:contact_phone, place=:place, note=:note,
                            remind_min=:remind_min, done=:done, reminded={reminded}, updated=:now
                         WHERE id=:id AND user_id=:uid""", {**vals, "now": now, "id": int(eid), "uid": user_id})
    else:
        if conn.execute("SELECT COUNT(*) FROM planner_events WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_EVENTS:
            return {"error": "Слишком много событий — удалите старые."}
        cur = conn.execute("""INSERT INTO planner_events (user_id, kind, title, ts, dur_min, listing_id, contact_name,
                                  contact_phone, place, note, remind_min, done, reminded, created, updated)
                              VALUES (:uid, :kind, :title, :ts, :dur_min, :listing_id, :contact_name, :contact_phone,
                                  :place, :note, :remind_min, :done, 0, :now, :now)""", {**vals, "uid": user_id, "now": now})
        eid = cur.lastrowid
    conn.commit()
    r = conn.execute(f"{_SELECT} WHERE e.id = ?", (int(eid),)).fetchone()
    return _event_row(r)


def set_done(conn: sqlite3.Connection, user_id: int, eid: int, done: bool) -> None:
    conn.execute("UPDATE planner_events SET done = ?, updated = ? WHERE id = ? AND user_id = ?",
                 (1 if done else 0, int(time.time()), eid, user_id))
    conn.commit()


def delete_event(conn: sqlite3.Connection, user_id: int, eid: int) -> None:
    conn.execute("DELETE FROM planner_events WHERE id = ? AND user_id = ?", (eid, user_id))
    conn.commit()


# ─── свои заметки ──────────────────────────────────────────────────────────
def notes(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, text, pinned, color, created, updated FROM planner_notes WHERE user_id = ? ORDER BY pinned DESC, updated DESC",
        (user_id,))]


def object_notes(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Заметки и статусы к объектам — тоже здесь, чтобы всё было в одном месте."""
    return [dict(r) for r in conn.execute(
        """SELECT n.listing_id, n.text, n.status, n.ts, l.title, l.price, l.deal, l.is_active FROM notes n
           JOIN listings l ON l.id = n.listing_id WHERE n.user_id = ? ORDER BY n.ts DESC LIMIT 300""", (user_id,))]


def save_note(conn: sqlite3.Connection, user_id: int, body: dict) -> dict:
    text = str(body.get("text") or "").strip()[:5000]
    color = body.get("color") if body.get("color") in ("", "yellow", "green", "red", "blue") else ""
    pinned = 1 if body.get("pinned") else 0
    now = int(time.time())
    nid = body.get("id")
    if nid:
        if not text:
            conn.execute("DELETE FROM planner_notes WHERE id = ? AND user_id = ?", (int(nid), user_id))
            conn.commit()
            return {"deleted": True}
        conn.execute("UPDATE planner_notes SET text = ?, pinned = ?, color = ?, updated = ? WHERE id = ? AND user_id = ?",
                     (text, pinned, color, now, int(nid), user_id))
    else:
        if not text:
            return {"error": "Пустая заметка."}
        if conn.execute("SELECT COUNT(*) FROM planner_notes WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_NOTES:
            return {"error": "Слишком много заметок — удалите старые."}
        nid = conn.execute("""INSERT INTO planner_notes (user_id, text, pinned, color, created, updated)
                              VALUES (?,?,?,?,?,?)""", (user_id, text, pinned, color, now, now)).lastrowid
    conn.commit()
    return dict(conn.execute("SELECT id, text, pinned, color, created, updated FROM planner_notes WHERE id = ?",
                             (int(nid),)).fetchone())


# ─── напоминания ───────────────────────────────────────────────────────────
def remind(conn: sqlite3.Connection, send=None, now: int | None = None) -> int:
    """Из worker: пора напомнить — уведомление на сайте и письмо. Каждое событие — один раз."""
    from . import notices
    now = int(now or time.time())
    rows = conn.execute(f"""{_SELECT.replace('SELECT e.*', 'SELECT e.*, u.email AS u_email')}
                            JOIN users u ON u.id = e.user_id
                            WHERE e.done = 0 AND e.reminded = 0 AND e.remind_min > 0
                              AND e.ts - e.remind_min * 60 <= ? AND e.ts > ? - 3600""", (now, now)).fetchall()
    n = 0
    for r in rows:
        when = datetime.fromtimestamp(r["ts"], MSK)
        day = "сегодня" if when.date() == datetime.fromtimestamp(now, MSK).date() else when.strftime("%d.%m")
        title = f"{KINDS.get(r['kind'], '•')}: {r['title']} — {day} в {when.strftime('%H:%M')}"
        body = " · ".join(x for x in (r["contact_name"], r["contact_phone"], r["place"], r["l_title"]) if x)
        notices.add(conn, r["user_id"], "plan", title, body, url=f"/planner?event={r['id']}",
                    listing_id=r["listing_id"], dedup_s=60)
        conn.execute("UPDATE planner_events SET reminded = 1 WHERE id = ?", (r["id"],))
        conn.commit()
        if send and r["u_email"]:
            try:
                send(r["u_email"], f"Напоминание: {title}",
                     f"{title}\n{body}\n{(r['note'] or '').strip()}\n\nПланер: {config.SITE_URL}/planner?event={r['id']}\n")
            except Exception:  # noqa: BLE001 — письмо не ушло, на сайте уведомление есть
                pass
        n += 1
    return n


# ─── календарь телефона (.ics) ─────────────────────────────────────────────
def feed_token(user_id: int) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"ics:{user_id}".encode(), hashlib.sha256).hexdigest()[:24]


def feed_url(user_id: int) -> str:
    return f"/planner/{user_id}-{feed_token(user_id)}.ics"


def check_feed(user_id: int, token: str) -> bool:
    return hmac.compare_digest(feed_token(user_id), token or "")


def _ics_text(v: str) -> str:
    return (v or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Строки .ics не длиннее 75 байт — перенос с пробелом."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > 74:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def ics(conn: sqlite3.Connection, user_id: int, event_id: int | None = None) -> str:
    """Календарь в формате .ics: одно событие или все за −30…+365 дней (для подписки)."""
    now = int(time.time())
    if event_id:
        rows = conn.execute(f"{_SELECT} WHERE e.user_id = ? AND e.id = ?", (user_id, event_id)).fetchall()
    else:
        rows = conn.execute(f"{_SELECT} WHERE e.user_id = ? AND e.ts >= ? AND e.ts < ? ORDER BY e.ts",
                            (user_id, now - 30 * 86400, now + 365 * 86400)).fetchall()
    fmt = lambda ts: datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")   # noqa: E731
    host = config.SITE_URL.split("//")[-1].strip("/") or "1plus1"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//1+1//planner//RU", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:1+1 · показы и созвоны", "X-WR-TIMEZONE:Europe/Moscow"]
    for r in rows:
        desc = "\n".join(x for x in (
            f"{r['contact_name']} {r['contact_phone']}".strip(), r["l_title"] or "",
            f"{config.SITE_URL}/?open={r['listing_id']}" if r["listing_id"] else "", r["note"] or "") if x)
        place = r["place"] or ", ".join(x for x in (r["l_complex"] and f"ЖК {r['l_complex']}",
                                                     r["l_street"] and f"ул. {r['l_street']} {r['l_house'] or ''}".strip()) if x)
        lines += ["BEGIN:VEVENT", f"UID:plan-{r['id']}@{host}", f"DTSTAMP:{fmt(r['updated'] or now)}",
                  f"DTSTART:{fmt(r['ts'])}", f"DTEND:{fmt(r['ts'] + (r['dur_min'] or 60) * 60)}",
                  _fold(f"SUMMARY:{_ics_text(('✓ ' if r['done'] else '') + KINDS.get(r['kind'], '') + ': ' + r['title'])}")]
        if desc:
            lines.append(_fold(f"DESCRIPTION:{_ics_text(desc)}"))
        if place:
            lines.append(_fold(f"LOCATION:{_ics_text(place)}"))
        if r["remind_min"] and not r["done"]:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(r['title'])}",
                      f"TRIGGER:-PT{r['remind_min']}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

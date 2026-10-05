"""Приём сообщений: сохранить → разобрать → склеить с существующими объектами."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time

from . import config, dedupe, parser
from .rules import TYPE_LABELS
from .textnorm import norm, words

log = logging.getLogger(__name__)


def text_hash(text: str) -> str:
    return hashlib.sha1(words(text).encode()).hexdigest()[:24]


def _fmt_num(x: float | None) -> str:
    if x is None:
        return ""
    return f"{x:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def make_title(o: dict) -> str:
    t = o.get("type")
    rooms = o.get("rooms")
    parts: list[str] = []
    if t in ("flat", "new"):
        head = "Студия" if rooms == 0 else (f"{rooms}-к квартира" if rooms else "Квартира")
        if t == "new":
            head += " (новостройка)"
        parts.append(head)
    elif t == "room":
        parts.append("Комната")
    elif t == "house":
        parts.append("Дом")
    elif t == "land":
        parts.append("Участок")
    else:
        parts.append(TYPE_LABELS.get(t or "", "Объект"))
    if o.get("area"):
        parts.append(f"{_fmt_num(o['area'])} м²")
    if o.get("land"):
        parts.append(f"{'участок ' if t == 'house' else ''}{_fmt_num(o['land'])} сот.")
    if o.get("floor"):
        parts.append(f"{o['floor']}/{o['floors']} эт." if o.get("floors") else f"{o['floor']} эт.")
    if o.get("deal") == "rent":
        parts.insert(0, "Аренда:")
    return ", ".join(parts).replace("Аренда:,", "Аренда:")


def make_search_text(o: dict) -> str:
    bits = [o.get("title"), o.get("complex"), o.get("district"), o.get("settlement"),
            o.get("street"), o.get("house"), o.get("description"), o.get("fragment")]
    if o.get("complex"):
        bits.append("жк " + o["complex"])
    return norm(" ".join(str(b) for b in bits if b))


def _reindex(conn: sqlite3.Connection, listing_id: int, text: str) -> None:
    conn.execute("DELETE FROM listings_fts WHERE rowid = ?", (listing_id,))
    conn.execute("INSERT INTO listings_fts(rowid, search_text) VALUES (?, ?)", (listing_id, text))


def _price_m2(price, area) -> int | None:
    if price and area and area > 5:
        return int(round(price / area))
    return None


def save_object(conn: sqlite3.Connection, o: parser.ParsedObject, message_id: int | None, ts: int) -> tuple[int, str]:
    """Создаёт новый объект или обновляет найденный дубль. Возвращает (id, как сопоставили)."""
    fh = dedupe.fragment_hash(o.fragment)
    match, why = dedupe.find_match(conn, o, fh, ts)
    d = o.to_dict()

    if match is None:
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        cur = conn.execute(
            """INSERT INTO listings (type, deal, rooms, area, land, floor, floors, price, price_m2,
                   district, complex, settlement, street, house, title, description, fragment, phones,
                   first_seen, last_seen, seen_count, is_active, search_text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?)""",
            (d["type"], d["deal"], d["rooms"], d["area"], d["land"], d["floor"], d["floors"], d["price"],
             _price_m2(d["price"], d["area"]), d["district"], d["complex"], d["settlement"], d["street"],
             d["house"], d["title"], d["description"], d["fragment"], json.dumps(d["phones"], ensure_ascii=False),
             ts, ts, d["search_text"]),
        )
        lid = cur.lastrowid
        why = "new"
    else:
        lid = match["id"]
        cur_d = dict(match)
        # Заполняем пропуски, обновляем цену на более свежую. Описание
        # не затираем коротким — берём более содержательное.
        merged = dict(cur_d)
        for k in ("rooms", "area", "land", "floor", "floors", "district", "complex", "settlement", "street", "house"):
            if merged.get(k) in (None, "") and d.get(k) not in (None, ""):
                merged[k] = d[k]
        if ts >= cur_d["last_seen"] and d["price"]:
            merged["price"] = d["price"]
        if len(d["description"] or "") > len(cur_d["description"] or "") * 1.2:
            merged["description"] = d["description"]
        if len(d["fragment"] or "") > len(cur_d["fragment"] or ""):
            merged["fragment"] = d["fragment"]
        phones = json.loads(cur_d["phones"] or "[]")
        for p in d["phones"]:
            if p not in phones:
                phones.append(p)
        merged["phones"] = json.dumps(phones, ensure_ascii=False)
        merged["title"] = make_title(merged)
        merged["search_text"] = make_search_text(merged)
        geo_reset = (merged["street"] != cur_d["street"] or merged["house"] != cur_d["house"]
                     or merged["complex"] != cur_d["complex"])
        conn.execute(
            """UPDATE listings SET rooms=?, area=?, land=?, floor=?, floors=?, price=?, price_m2=?, district=?,
                   complex=?, settlement=?, street=?, house=?, title=?, description=?, fragment=?, phones=?,
                   last_seen=MAX(last_seen, ?), first_seen=MIN(first_seen, ?), seen_count=seen_count+1,
                   is_active=1, search_text=?, geo_status=CASE WHEN ? THEN 'pending' ELSE geo_status END
               WHERE id=?""",
            (merged["rooms"], merged["area"], merged["land"], merged["floor"], merged["floors"], merged["price"],
             _price_m2(merged["price"], merged["area"]), merged["district"], merged["complex"], merged["settlement"],
             merged["street"], merged["house"], merged["title"], merged["description"], merged["fragment"],
             merged["phones"], ts, ts, merged["search_text"], geo_reset, lid),
        )
        d["search_text"] = merged["search_text"]
    _reindex(conn, lid, d["search_text"])
    conn.execute(
        "INSERT INTO listing_events (listing_id, message_id, ts, price, phones, fragment_hash, match) VALUES (?,?,?,?,?,?,?)",
        (lid, message_id, ts, d["price"], json.dumps(d["phones"]), fh, why),
    )
    return lid, why


def add_message(conn: sqlite3.Connection, *, source: str, text: str, ts: int | None = None,
                profile_id: str | None = None, chat_id: str | None = None, chat_name: str | None = None,
                msg_id: str | None = None, sender: str | None = None, sender_name: str | None = None) -> int | None:
    """Сохраняет сообщение. None — если такое уже есть."""
    ts = int(ts or time.time())
    try:
        cur = conn.execute(
            """INSERT INTO messages (source, profile_id, chat_id, chat_name, msg_id, sender, sender_name, text, text_hash, ts)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source, profile_id, chat_id, chat_name, msg_id or text_hash(text) + str(ts), sender, sender_name,
             text, text_hash(text), ts),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def process_message(conn: sqlite3.Connection, message_id: int, use_llm: bool | None = None) -> dict:
    m = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if m is None:
        return {"status": "missing"}

    # Тот же текст уже разбирали (его разослали по десяткам чатов) — не тратим
    # нейросеть второй раз, просто отмечаем, что объект снова присылали.
    prev = conn.execute(
        "SELECT id, kind FROM messages WHERE text_hash = ? AND status = 'done' AND id != ? ORDER BY id LIMIT 1",
        (m["text_hash"], m["id"]),
    ).fetchone()
    if prev is not None:
        listing_ids = [r["listing_id"] for r in conn.execute(
            "SELECT DISTINCT listing_id FROM listing_events WHERE message_id = ?", (prev["id"],))]
        for lid in listing_ids:
            conn.execute(
                "UPDATE listings SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, is_active = 1 WHERE id = ?",
                (m["ts"], lid))
            conn.execute(
                "INSERT INTO listing_events (listing_id, message_id, ts, match) VALUES (?,?,?, 'repost')",
                (lid, m["id"], m["ts"]))
        conn.execute("UPDATE messages SET status='done', kind=?, objects=? WHERE id=?",
                     (prev["kind"], len(listing_ids), m["id"]))
        conn.commit()
        return {"status": "repost", "listings": listing_ids}

    try:
        kind, objects = parser.parse(m["text"], sender_phone=m["sender"], use_llm=use_llm)
    except Exception as e:  # noqa: BLE001 — одно кривое сообщение не должно останавливать поток
        log.exception("Ошибка разбора сообщения %s", m["id"])
        conn.execute("UPDATE messages SET status='error', error=? WHERE id=?", (str(e)[:500], m["id"]))
        conn.commit()
        return {"status": "error", "error": str(e)}

    results = []
    for o in objects:
        lid, why = save_object(conn, o, m["id"], m["ts"])
        results.append({"listing_id": lid, "match": why})
    conn.execute("UPDATE messages SET status='done', kind=?, objects=? WHERE id=?", (kind, len(objects), m["id"]))
    conn.commit()
    return {"status": "done", "kind": kind, "results": results}


def process_pending(conn: sqlite3.Connection, limit: int = 200, use_llm: bool | None = None,
                    budget_s: float | None = None, now: int | None = None) -> int:
    """Разобрать очередь: сначала самые свежие сообщения. budget_s — не дольше стольких
    секунд за раз, чтобы большая очередь (первый запуск) не задерживала новые сообщения."""
    now = int(now or time.time())
    # Старше STALE_DAYS — объект всё равно сразу ушёл бы в архив; не тратим нейросеть
    conn.execute("UPDATE messages SET status='skipped' WHERE status='new' AND ts < ?",
                 (now - config.STALE_DAYS * 86400,))
    conn.commit()
    started = time.monotonic()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM messages WHERE status='new' ORDER BY ts DESC, id DESC LIMIT ?", (limit,))]
    done = 0
    for i in ids:
        process_message(conn, i, use_llm=use_llm)
        done += 1
        if budget_s is not None and time.monotonic() - started > budget_s:
            break
    return done


def archive_stale(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = int(now or time.time())
    cur = conn.execute("UPDATE listings SET is_active = 0 WHERE is_active = 1 AND last_seen < ?",
                       (now - config.STALE_DAYS * 86400,))
    conn.commit()
    return cur.rowcount

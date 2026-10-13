"""Приём сообщений: сохранить → разобрать → склеить с существующими объектами."""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import shutil
import time
from pathlib import Path

from . import config, dedupe, learning, osmaddr, parser, rules
from .rules import TYPE_LABELS, rooms_mask
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
        kind = o.get("room_kind")
        if rooms == 0 or kind == "studio":
            head = "Студия"
        elif kind == "euro" and rooms:
            head = f"Евро-{rooms}"
        elif kind == "mini" and rooms:
            head = f"Мини-{rooms}"
        else:
            head = f"{rooms}-к квартира" if rooms else "Квартира"
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
    # Правки админа (ЖК/район) — до поиска дублей: объявление совпадёт с уже исправленной карточкой
    osmaddr.apply(conn, o)      # опечатка в улице («Командоская») — до правил и дублей: одна улица, а не две
    learning.apply(conn, o)
    fh = dedupe.fragment_hash(o.fragment)
    d = o.to_dict()
    ban = conn.execute("SELECT listing_id FROM banned WHERE kind = 'frag' AND key = ?", (fh,)).fetchone()
    if ban is not None:   # этот текст админ удалил навсегда
        return ban[0] or 0, "banned"
    if d.get("phones") and set(d["phones"]) & banned_phones(conn):   # номер удалён админом целиком
        return 0, "banned"
    learned_fix(conn, fh, d)   # этот же текст админ уже поправлял (цена, продажа/аренда) — сразу правильно
    if d.get("article"):
        own = conn.execute("SELECT id FROM listings WHERE source = 'feed' AND article = ? AND is_active = 1",
                           (d["article"],)).fetchone()
        if own is not None:
            conn.execute(
                "INSERT INTO listing_events (listing_id, message_id, ts, price, phones, fragment_hash, match) "
                "VALUES (?,?,?,?,?,?,'strely')", (own["id"], message_id, ts, d["price"], json.dumps(d["phones"]), fh))
            return own["id"], "strely"
    match, why = dedupe.find_match(conn, o, fh, ts)
    if match is not None and match["hidden_reason"] == "admin":   # тот же объект, удалённый админом
        return match["id"], "banned"

    if match is None:
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        cur = conn.execute(
            """INSERT INTO listings (type, deal, rooms, area, land, floor, floors, price, price_m2,
                   district, complex, settlement, street, house, title, description, fragment, phones,
                   first_seen, last_seen, seen_count, is_active, search_text, room_kind, rooms_mask, article,
                   extra_districts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?,?,?,?)""",
            (d["type"], d["deal"], d["rooms"], d["area"], d["land"], d["floor"], d["floors"], d["price"],
             _price_m2(d["price"], d["area"]), d["district"], d["complex"], d["settlement"], d["street"],
             d["house"], d["title"], d["description"], d["fragment"], json.dumps(d["phones"], ensure_ascii=False),
             ts, ts, d["search_text"], d["room_kind"], rooms_mask(d["room_kind"], d["rooms"]), d["article"],
             json.dumps(d.get("extra_districts") or [], ensure_ascii=False)),
        )
        lid = cur.lastrowid
        why = "new"
    else:
        lid = match["id"]
        cur_d = dict(match)
        # Заполняем пропуски, обновляем цену на более свежую. Описание
        # не затираем коротким — берём более содержательное.
        merged = dict(cur_d)
        for k in ("rooms", "room_kind", "area", "land", "floor", "floors", "district", "complex", "settlement",
                  "street", "house", "article"):
            if merged.get(k) in (None, "") and d.get(k) not in (None, ""):
                merged[k] = d[k]
        # Карточкой управляет агент (кабинет): его цену меняет только более свежий пост, описание не трогаем
        owned = bool(cur_d.get("owner_user_id"))
        if ts >= cur_d["last_seen"] and d["price"] and ts > (cur_d.get("owner_edited_at") or 0):
            if not cur_d.get("price_fixed") or d["price"] not in (cur_d.get("price_wrong"), cur_d["price"]):
                merged["price"] = d["price"]   # исправленную админом цену меняет только действительно новая цена
        # Описание берём более полное, но не теряем то, где назван ЖК/улица карточки
        # (иначе в карточке «ЖК Мечта», а в описании про ЖК ни слова)
        def mentions(desc: str | None) -> int:
            w = words(desc or "")
            return sum(1 for v in (merged.get("complex"), merged.get("street")) if v and words(v) and words(v) in w)
        if not owned and (mentions(d["description"]) > mentions(cur_d["description"]) or (
                mentions(d["description"]) == mentions(cur_d["description"])
                and len(d["description"] or "") > len(cur_d["description"] or "") * 1.2)):
            merged["description"] = d["description"]
        # Агент отметил «продано» — повторы старого поста объект не возвращают (только новый пост через 3 дня)
        active = 0 if cur_d.get("sold_at") and ts < cur_d["sold_at"] + 3 * 86400 else 1
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
                   is_active=?, sold_at=CASE WHEN ? THEN NULL ELSE sold_at END, search_text=?, geo_status=CASE WHEN ? AND geo_status NOT IN ('manual', 'feed') THEN 'pending' ELSE geo_status END,
                   room_kind=?, rooms_mask=?, article=?, extra_districts=?
               WHERE id=?""",
            (merged["rooms"], merged["area"], merged["land"], merged["floor"], merged["floors"], merged["price"],
             _price_m2(merged["price"], merged["area"]), merged["district"], merged["complex"], merged["settlement"],
             merged["street"], merged["house"], merged["title"], merged["description"], merged["fragment"],
             merged["phones"], ts, ts, active, active, merged["search_text"], geo_reset,
             merged.get("room_kind"), rooms_mask(merged.get("room_kind"), merged["rooms"]), merged.get("article"),
             json.dumps(list(dict.fromkeys(json.loads(cur_d.get("extra_districts") or "[]") + (d.get("extra_districts") or []))),
                        ensure_ascii=False), lid),
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
        conn.rollback()   # неудачная вставка оставляет открытую запись — иначе база занята для всех
        return None


def process_message(conn: sqlite3.Connection, message_id: int, use_llm: bool | None = None) -> dict:
    m = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if m is not None and m["status"] == "new" and (conn.execute(
            "SELECT 1 FROM banned WHERE kind = 'hash' AND key = ?", (m["text_hash"],)).fetchone()
            or (m["sender"] and rules.normalize_phone(str(m["sender"]).split("@")[0]) in banned_phones(conn))):
        conn.execute("UPDATE messages SET status = 'skipped', kind = 'banned' WHERE id = ?", (m["id"],))
        conn.commit()
        return {"status": "banned"}
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
                "UPDATE listings SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, "
                "is_active = CASE WHEN hidden_reason = 'admin' THEN 0 ELSE 1 END WHERE id = ?",
                (m["ts"], lid))
            conn.execute(
                "INSERT INTO listing_events (listing_id, message_id, ts, match) VALUES (?,?,?, 'repost')",
                (lid, m["id"], m["ts"]))
        conn.execute("UPDATE messages SET status='done', kind=?, objects=? WHERE id=?",
                     (prev["kind"], len(listing_ids), m["id"]))
        if prev["kind"] == "request":
            from . import buyers
            buyers.save(conn, m)
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
    if kind == "request":   # «куплю/ищу» — в ленту запросов покупателей
        from . import buyers
        buyers.save(conn, m)
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
        time.sleep(0.01)  # уступаем процессор сайту, пока разбираем большую очередь
        if budget_s is not None and time.monotonic() - started > budget_s:
            break
    return done


def learned_fix(conn: sqlite3.Connection, fh: str, d: dict) -> None:
    for kind, val in conn.execute("SELECT kind, value FROM learned_rules WHERE kind IN ('frag_price', 'frag_deal') AND key = ?", (fh,)):
        v = json.loads(val)
        if kind == "frag_price" and v.get("price"):
            d["price"] = int(v["price"])
        elif kind == "frag_deal" and v.get("deal") in ("sale", "rent"):
            d["deal"] = v["deal"]


def learn_listing_fix(conn: sqlite3.Connection, listing_id: int, kind: str, value: dict, label: str) -> int:
    """Запомнить правку админа для всех текстов этого объекта: повторы и тот же текст в других подборках — сразу верно."""
    hashes = [r[0] for r in conn.execute(
        "SELECT DISTINCT fragment_hash FROM listing_events WHERE listing_id = ? AND fragment_hash IS NOT NULL", (listing_id,))]
    now = int(time.time())
    for h in hashes:
        conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES (?,?,?,?,1,?)
                        ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label, n = n + 1, ts = excluded.ts""",
                     (kind, h, json.dumps(value, ensure_ascii=False), label, now))
    conn.commit()
    return len(hashes)


def set_price(conn: sqlite3.Connection, listing_id: int, price: int, user_id: int | None = None) -> dict:
    """Админ исправил цену: сохранить, запомнить ошибочную (её больше не принимаем) и выучить для этого текста."""
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if r is None:
        return {"error": "не найдено"}
    old = r["price"]
    conn.execute("""UPDATE listings SET price = ?, price_m2 = ?, price_fixed = 1,
                    price_wrong = CASE WHEN ? != ? THEN ? ELSE price_wrong END WHERE id = ?""",
                 (price, _price_m2(price, r["area"]), old or 0, price, old, listing_id))
    if user_id:
        conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                     (listing_id, user_id, int(time.time()), "price", json.dumps(old), json.dumps(price)))
    conn.commit()
    n = learn_listing_fix(conn, listing_id, "frag_price", {"price": price, "wrong": old},
                          f"{r['title']}: цена {old or '—'} → {price}")
    return {"ok": True, "price": price, "learned": n}


def ban_listing(conn: sqlite3.Connection, listing_id: int) -> dict:
    """Админ удалил объект навсегда: скрыть и запомнить его тексты — повторы и тот же объект больше не появятся."""
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if r is None:
        return {"error": "не найдено"}
    now = int(time.time())
    keys = [("frag", h[0]) for h in conn.execute(
        "SELECT DISTINCT fragment_hash FROM listing_events WHERE listing_id = ? AND fragment_hash IS NOT NULL", (listing_id,))]
    keys += [("hash", h[0]) for h in conn.execute(
        """SELECT DISTINCT m.text_hash FROM listing_events e JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ? AND (SELECT COUNT(DISTINCT e2.listing_id) FROM listing_events e2
                                       WHERE e2.message_id = m.id) = 1""", (listing_id,))]   # портянки не баним целиком
    if r["ext_id"]:
        keys.append(("feed", r["ext_id"]))
    conn.executemany("INSERT OR IGNORE INTO banned (kind, key, listing_id, title, ts) VALUES (?,?,?,?,?)",
                     [(k, v, listing_id, r["title"], now) for k, v in keys])
    conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'admin' WHERE id = ?", (listing_id,))
    conn.commit()
    return {"ok": True, "keys": len(keys)}


def banned_phones(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT key FROM banned WHERE kind = 'phone'")}


def ban_phone(conn: sqlite3.Connection, phone: str) -> dict:
    """Админ: всё от этого номера — скрыть объекты и запросы, удалить сообщения; новые не принимать."""
    phone = rules.normalize_phone(phone or "")
    if not phone:
        return {"error": "Номер не распознан."}
    if phone == rules.normalize_phone(config.FEED_PHONE or ""):
        return {"error": "Это номер партнёра (объекты СТРЕЛ из фида) — его нельзя удалить целиком."}
    now = int(time.time())
    conn.execute("INSERT OR IGNORE INTO banned (kind, key, title, ts) VALUES ('phone', ?, ?, ?)", (phone, phone, now))
    listings = conn.execute("""UPDATE listings SET is_active = 0, hidden_reason = 'phone'
                               WHERE EXISTS (SELECT 1 FROM json_each(listings.phones) j WHERE j.value = ?)
                                 AND COALESCE(hidden_reason, '') != 'admin'""", (phone,)).rowcount
    requests = conn.execute("""UPDATE buyer_requests SET is_active = 0
                               WHERE EXISTS (SELECT 1 FROM json_each(buyer_requests.phones) j WHERE j.value = ?)""",
                            (phone,)).rowcount
    digits = phone[-10:]
    gone = []
    for m in conn.execute("SELECT id, text, sender FROM messages WHERE status != 'new' OR status = 'new'"):
        snd = rules.normalize_phone(str(m["sender"] or "").split("@")[0]) if m["sender"] else None
        if snd == phone or (digits in "".join(ch for ch in (m["text"] or "") if ch.isdigit())
                            and phone in rules.extract_phones(m["text"] or "")):
            gone.append(m["id"])
    for i in range(0, len(gone), 500):
        part = gone[i:i + 500]
        conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?' * len(part))})", part)
    conn.commit()
    return {"ok": True, "phone": phone, "listings": listings, "requests": requests, "messages": len(gone)}


def unban_phone(conn: sqlite3.Connection, phone: str) -> None:
    phone = rules.normalize_phone(phone or "") or phone
    conn.execute("DELETE FROM banned WHERE kind = 'phone' AND key = ?", (phone,))
    conn.execute("""UPDATE listings SET is_active = 1, hidden_reason = NULL
                    WHERE hidden_reason = 'phone' AND EXISTS (SELECT 1 FROM json_each(listings.phones) j WHERE j.value = ?)""",
                 (phone,))
    conn.execute("""UPDATE buyer_requests SET is_active = 1
                    WHERE EXISTS (SELECT 1 FROM json_each(buyer_requests.phones) j WHERE j.value = ?)""", (phone,))
    conn.commit()


def remove_photo(conn: sqlite3.Connection, listing_id: int, url: str) -> dict:
    """Админ убрал фото у объекта (чужое, с номером…). Запоминаем адрес в banned (kind='photo', без listing_id —
    чтобы объект не попал в «Удалённые»): фид СТРЕЛ и фото из чатов это фото больше не вернут."""
    row = conn.execute("SELECT photos, title FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if row is None:
        return {"error": "Объект не найден."}
    photos = json.loads(row["photos"] or "[]")
    if url not in photos:
        return {"error": "У объекта нет такого фото."}
    photos.remove(url)
    conn.execute("UPDATE listings SET photos = ? WHERE id = ?", (json.dumps(photos, ensure_ascii=False), listing_id))
    conn.execute("INSERT OR REPLACE INTO banned (kind, key, listing_id, title, ts) VALUES ('photo', ?, NULL, ?, ?)",
                 (url, f"фото объекта #{listing_id}", int(time.time())))
    conn.commit()
    return {"ok": True, "photos": photos}


def banned_photos(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT key FROM banned WHERE kind = 'photo'")}


def unban_listing(conn: sqlite3.Connection, listing_id: int) -> None:
    conn.execute("DELETE FROM banned WHERE listing_id = ?", (listing_id,))
    conn.execute("UPDATE listings SET is_active = 1, hidden_reason = NULL, last_seen = MAX(last_seen, ?) WHERE id = ?",
                 (int(time.time()), listing_id))
    conn.commit()


def fix_rent_rule(conn: sqlite3.Connection) -> dict:
    """Один раз после нового правила аренды: «аренда» без коммунальных платежей — дорогие в продажу, дешёвые скрыть."""
    to_sale = hidden = 0
    for r in conn.execute("""SELECT id, price, fragment, description FROM listings
                             WHERE deal = 'rent' AND deal_fixed = 0 AND source = 'chat'""").fetchall():
        if rules.has_communal(r["fragment"] or r["description"] or ""):
            continue
        if r["price"] and r["price"] >= 1_000_000:
            row = dict(conn.execute("SELECT * FROM listings WHERE id = ?", (r["id"],)).fetchone())
            row["deal"] = "sale"
            row["title"] = make_title(row)
            conn.execute("UPDATE listings SET deal = 'sale', title = ? WHERE id = ?", (row["title"], r["id"]))
            to_sale += 1
        else:
            conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'rent_rule' WHERE id = ? AND is_active = 1", (r["id"],))
            hidden += 1
    conn.commit()
    return {"to_sale": to_sale, "hidden": hidden}


def archive_stale(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = int(now or time.time())
    # Объекты фида СТРЕЛ не устаревают сами — их снимает сам фид
    cur = conn.execute("UPDATE listings SET is_active = 0 WHERE is_active = 1 AND source = 'chat' AND last_seen < ?",
                       (now - config.STALE_DAYS * 86400,))
    conn.commit()
    return cur.rowcount


def cleanup_old(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Раз в сутки: объекты, пролежавшие в архиве ARCHIVE_DAYS (сняли/устарели и не появились снова), — удалить;
    сообщения старше KEEP_DAYS — тоже."""
    now = int(now or time.time())
    edge = now - config.KEEP_DAYS * 86400
    arch = now - config.ARCHIVE_DAYS * 86400
    # удалённые админом и «всё от номера» не стираем: по ним узнаём тот же объект в новых сообщениях
    ids = [r[0] for r in conn.execute("""SELECT id FROM listings WHERE is_active = 0
                                         AND COALESCE(removed_at, last_seen + ?) < ?
                                         AND COALESCE(hidden_reason, '') NOT IN ('admin', 'phone')""",
                                      (config.STALE_DAYS * 86400, arch))]
    for i in ids:
        conn.execute("DELETE FROM listings_fts WHERE rowid = ?", (i,))
        conn.execute("DELETE FROM listing_events WHERE listing_id = ?", (i,))
        conn.execute("DELETE FROM listings WHERE id = ?", (i,))
        shutil.rmtree(Path(config.PHOTOS_DIR) / str(i), ignore_errors=True)  # фото, загруженные агентом
    cur = conn.execute("DELETE FROM messages WHERE ts < ? AND status != 'new'", (edge,))
    conn.commit()
    return {"listings": len(ids), "messages": cur.rowcount}

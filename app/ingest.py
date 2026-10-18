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
    if o.get("multi_units"):   # «этажи 2 и 6»: в посте несколько квартир — этаж одной из них не выдаём за общий
        parts.append(f"{o['multi_units']} кв. в посте")
    elif o.get("floor"):
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
    # состояние «с ремонтом / без ремонта» — по тому же тексту (фильтр «Ремонт»)
    conn.execute("""UPDATE listings SET repair = CASE WHEN json_type(admin_fields, '$.repair') IS NOT NULL
                    THEN NULLIF(json_extract(admin_fields, '$.repair'), '') ELSE ? END WHERE id = ?""",
                 (rules.detect_repair(text), listing_id))   # ремонт, поправленный админом, не пересчитываем


REPAIR_V = "4"


def backfill_repair(conn: sqlite3.Connection) -> int:
    """Один раз после обновления правил (state repair_v): проставить состояние всем объектам."""
    from . import db
    if db.get_state(conn, "repair_v") == REPAIR_V:
        return 0
    n = 0
    for r in conn.execute("SELECT id, search_text FROM listings").fetchall():
        conn.execute("UPDATE listings SET repair = ? WHERE id = ?", (rules.detect_repair(r["search_text"]), r["id"]))
        n += 1
    db.set_state(conn, "repair_v", REPAIR_V)
    conn.commit()
    return n


def _price_m2(price, area) -> int | None:
    if price and area and area > 5:
        return int(round(price / area))
    return None


def save_object(conn: sqlite3.Connection, o: parser.ParsedObject, message_id: int | None, ts: int) -> tuple[int, str]:
    """Создаёт новый объект или обновляет найденный дубль. Возвращает (id, как сопоставили)."""
    # Правки админа (ЖК/район) — до поиска дублей: объявление совпадёт с уже исправленной карточкой
    osmaddr.apply(conn, o)      # опечатка в улице («Командоская») — до правил и дублей: одна улица, а не две
    learning.apply(conn, o)
    if o.complex:   # «Краснодар», «Жилой квартал»… — не ЖК; тот же ЖК в другом написании — принятое в базе название
        from . import cxnorm, geoaudit
        o.complex = cxnorm.with_number(o.complex, o.fragment)   # «Самолёт» + в тексте «Самолёт-1» → «Самолёт 1»
        o.complex = None if geoaudit.is_junk_complex(o.complex) else cxnorm.canonical(conn, o.complex)
    fh = dedupe.fragment_hash(o.fragment)
    d = o.to_dict()
    from .rules import multi_units
    d["multi_units"] = multi_units(o.fragment) or None
    from .pricefix import check as price_check   # «Цена 5400» = 5,4 млн; 1,6 млрд за двушку — цена «под вопросом»
    d["price"], d["price_doubt"] = price_check(d["price"], d["deal"], d["type"], d["area"])   # склейку повторов это не меняет (P3): этаж остаётся как разобран
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
    else:   # 18.10: наш номер без артикула (портянка, пост без «Артикул») — тот же объект с сайта? → к его карточке
        from . import ownmatch
        m_ = conn.execute("SELECT source, msg_id FROM messages WHERE id = ?", (message_id,)).fetchone()
        with_photo = bool(m_ and m_["msg_id"] and conn.execute(   # фото с подписью: сообщение само — картинка
            "SELECT 1 FROM chat_media WHERE source = ? AND msg_id = ?", (m_["source"], m_["msg_id"])).fetchone())
        fid = ownmatch.feed_match(conn, d, with_photo)
        if fid:
            conn.execute(
                "INSERT INTO listing_events (listing_id, message_id, ts, price, phones, fragment_hash, match) "
                "VALUES (?,?,?,?,?,?,'strely')", (fid, message_id, ts, d["price"], json.dumps(d["phones"]), fh))
            conn.execute("UPDATE listings SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1 WHERE id = ?", (ts, fid))
            return fid, "strely"
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
                   extra_districts, phone_roles, description_ts, added_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1,?,?,?,?,?,?,?,?)""",
            (d["type"], d["deal"], d["rooms"], d["area"], d["land"], d["floor"], d["floors"], d["price"],
             _price_m2(d["price"], d["area"]), d["district"], d["complex"], d["settlement"], d["street"],
             d["house"], d["title"], d["description"], d["fragment"], json.dumps(d["phones"], ensure_ascii=False),
             ts, ts, d["search_text"], d["room_kind"], rooms_mask(d["room_kind"], d["rooms"]), d["article"],
             json.dumps(d.get("extra_districts") or [], ensure_ascii=False),
             json.dumps(d.get("phone_roles") or {}, ensure_ascii=False), ts, int(time.time())),
        )
        lid = cur.lastrowid
        if d.get("multi_units") or d.get("price_doubt"):
            conn.execute("UPDATE listings SET multi_units = ?, price_doubt = ? WHERE id = ?",
                         (d.get("multi_units"), 1 if d.get("price_doubt") else None, lid))
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
        for k, v in json.loads(cur_d.get("admin_fields") or "{}").items():   # поправленное админом — главнее повтора
            if k in merged and k != "repair":
                merged[k] = v
        # Карточкой управляет агент (кабинет): его цену меняет только более свежий пост, описание не трогаем
        owned = bool(cur_d.get("owner_user_id"))
        new_price = False
        # Акт 14.10, Б2: повтор другого типа/комнатности, с ценой «под вопросом» или дальше 25 % от прежней —
        # цену карточки не перебивает (обычно это чужой объект, склеенный по похожему тексту)
        other = ((d.get("type") and cur_d.get("type") and d["type"] != cur_d["type"] and {d["type"], cur_d["type"]} != {"flat", "new"})
                 or (d.get("rooms") is not None and cur_d.get("rooms") is not None and d["rooms"] != cur_d["rooms"]))
        jump = bool(d["price"] and cur_d["price"] and not cur_d.get("price_doubt")
                    and abs(d["price"] - cur_d["price"]) / cur_d["price"] > 0.25)
        if ts >= cur_d["last_seen"] and d["price"] and ts > (cur_d.get("owner_edited_at") or 0) \
                and not other and not jump and not (d.get("price_doubt") and not cur_d.get("price_doubt")):
            if not cur_d.get("price_fixed") or d["price"] not in (cur_d.get("price_wrong"), cur_d["price"]):
                new_price = d["price"] != cur_d["price"]
                merged["price"] = d["price"]   # исправленную админом цену меняет только действительно новая цена
        # Описание берём более полное, но не теряем то, где назван ЖК/улица карточки
        # (иначе в карточке «ЖК Мечта», а в описании про ЖК ни слова)
        def mentions(desc: str | None) -> int:
            w = words(desc or "")
            return sum(1 for v in (merged.get("complex"), merged.get("street")) if v and words(v) and words(v) in w)
        if not owned and d["description"] and (new_price or mentions(d["description"]) > mentions(cur_d["description"]) or (
                mentions(d["description"]) == mentions(cur_d["description"])
                and len(d["description"] or "") > len(cur_d["description"] or "") * 1.2)):
            merged["description"] = d["description"]   # новая цена — значит, и текст этого (свежего) поста
        # Агент отметил «продано» — повторы старого поста объект не возвращают (только новый пост через 3 дня)
        active = 0 if cur_d.get("sold_at") and ts < cur_d["sold_at"] + 3 * 86400 else 1
        if (new_price and not owned and d["fragment"]) or len(d["fragment"] or "") > len(cur_d["fragment"] or ""):
            merged["fragment"] = d["fragment"]
        roles = json.loads(cur_d.get("phone_roles") or "{}")
        for p, role in (d.get("phone_roles") or {}).items():
            if roles.get(p) != "text":   # «в тексте» важнее «прислал»
                roles[p] = role
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
                   room_kind=?, rooms_mask=?, article=?, extra_districts=?, phone_roles=?,
                   description_ts=CASE WHEN ? THEN ? ELSE description_ts END,
                   price_doubt=CASE WHEN ? THEN ? ELSE price_doubt END
               WHERE id=?""",
            (merged["rooms"], merged["area"], merged["land"], merged["floor"], merged["floors"], merged["price"],
             _price_m2(merged["price"], merged["area"]), merged["district"], merged["complex"], merged["settlement"],
             merged["street"], merged["house"], merged["title"], merged["description"], merged["fragment"],
             merged["phones"], ts, ts, active, active, merged["search_text"], geo_reset,
             merged.get("room_kind"), rooms_mask(merged.get("room_kind"), merged["rooms"]), merged.get("article"),
             json.dumps(list(dict.fromkeys(json.loads(cur_d.get("extra_districts") or "[]") + (d.get("extra_districts") or []))),
                        ensure_ascii=False), json.dumps(roles, ensure_ascii=False),
             merged["description"] != cur_d["description"], ts,
             merged["price"] != cur_d["price"], 1 if d.get("price_doubt") else None, lid),
        )
        d["search_text"] = merged["search_text"]
    _reindex(conn, lid, d["search_text"])
    # «Под задатком», «забронирована» — с продажи снят сразу; новый пост без задатка возвращает объект
    from .rules import under_deposit
    if under_deposit(o.fragment):
        conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'deposit' WHERE id = ? AND source NOT IN ('own', 'feed')", (lid,))
    else:
        conn.execute("UPDATE listings SET hidden_reason = NULL WHERE id = ? AND hidden_reason = 'deposit'", (lid,))
    conn.execute(
        "INSERT INTO listing_events (listing_id, message_id, ts, price, phones, fragment_hash, match) VALUES (?,?,?,?,?,?,?)",
        (lid, message_id, ts, d["price"], json.dumps(d["phones"]), fh, why),
    )
    return lid, why


def hide_deposits(conn: sqlite3.Connection) -> int:
    """Один раз (state deposit_v1): уже собранные объекты «под задатком» — снять с сайта."""
    from .rules import under_deposit
    n = 0
    for r in conn.execute("""SELECT id, fragment, description FROM listings WHERE is_active = 1
                             AND source NOT IN ('own', 'feed')""").fetchall():
        if under_deposit(r["fragment"] or r["description"]):
            conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'deposit' WHERE id = ?", (r["id"],))
            n += 1
    conn.commit()
    return n


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
                "is_active = CASE WHEN hidden_reason IN ('admin', 'deposit') THEN 0 ELSE 1 END WHERE id = ?",
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
    for kind, val in conn.execute("SELECT kind, value FROM learned_rules WHERE kind IN ('frag_price', 'frag_deal', 'frag_fields') AND key = ?", (fh,)):
        v = json.loads(val)
        if kind == "frag_price" and v.get("price"):
            d["price"] = int(v["price"])
        elif kind == "frag_deal" and v.get("deal") in ("sale", "rent"):
            d["deal"] = v["deal"]
        elif kind == "frag_fields":   # 18.10: этот текст админ уже поправлял (тип, комнаты, площадь, этаж, адрес…)
            for k, x in v.items():
                if k in FIELD_SPEC and k != "repair":
                    d[k] = x


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


# 18.10 (Артём: «возможность изменения админом всех характеристик в одном месте — много ошибок»): поле → проверка значения
def _f(lo, hi, cast=float):
    def v(x):
        if x in (None, ""):
            return None
        try:
            x = cast(str(x).replace(",", ".").replace(" ", ""))
        except ValueError:
            raise ValueError("число") from None
        if not lo <= x <= hi:
            raise ValueError(f"от {lo} до {hi}")
        return x
    return v


def _s(n):
    return lambda x: (str(x).strip()[:n] or None) if x not in (None, "") else None


def _choice(*opts):
    def v(x):
        if x in (None, ""):
            return None
        if x not in opts:
            raise ValueError("выберите из списка")
        return x
    return v


FIELD_SPEC = {
    "type": (_choice(*TYPE_LABELS.keys()), "тип"), "deal": (_choice("sale", "rent"), "сделка"),
    "rooms": (_f(0, 9, int), "комнаты"), "room_kind": (_choice("studio", "classic", "euro", "mini"), "планировка"),
    "area": (_f(5, 20000), "площадь"), "land": (_f(0.1, 100000), "участок"),
    "floor": (_f(1, 99, int), "этаж"), "floors": (_f(1, 99, int), "этажность"),
    "street": (_s(120), "улица"), "house": (_s(20), "дом"), "settlement": (_s(80), "населённый пункт"),
    "repair": (_choice("yes", "no", "none"), "ремонт"),
}


def set_fields(conn: sqlite3.Connection, listing_id: int, fields: dict, user_id: int | None = None) -> dict:
    """Админ поправил характеристики объекта. Сохраняем, пишем в журнал, запоминаем: повторы этого объявления
    не перетирают (listings.admin_fields), а тот же текст в других сообщениях сразу приходит верным (frag_fields)."""
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if r is None:
        return {"error": "не найдено"}
    ch = {}
    for k, raw in (fields or {}).items():
        if k not in FIELD_SPEC:
            continue
        try:
            val = FIELD_SPEC[k][0](raw)
        except ValueError as e:
            return {"error": f"{FIELD_SPEC[k][1].capitalize()}: {e}"}
        cur = r["repair"] if k == "repair" else r[k]
        if k == "repair":
            val = None if val in (None, "none") else val
        if val != cur:
            ch[k] = val
    if not ch:
        return {"ok": True, "changed": {}}
    d = dict(r) | ch
    sets = {k: v for k, v in ch.items() if k != "repair"}
    sets["title"] = make_title(d)
    sets["price_m2"] = _price_m2(d["price"], d["area"])
    sets["rooms_mask"] = rooms_mask(d.get("room_kind"), d.get("rooms"))
    adm = json.loads(r["admin_fields"] or "{}")
    adm.update({k: ("" if (k == "repair" and v is None) else v) for k, v in ch.items()})
    sets["admin_fields"] = json.dumps(adm, ensure_ascii=False)
    sets["admin_fixed"] = 1
    if "street" in ch or "house" in ch:
        sets["geo_status"] = r["geo_status"] if r["geo_status"] in ("manual", "feed") else "pending"   # новый адрес — искать точку заново
    conn.execute(f"UPDATE listings SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), listing_id))
    now = int(time.time())
    for k, v in ch.items():
        conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?,?,?,?,?,?, 'админ')",
                     (listing_id, user_id or 0, now, k, json.dumps(r["repair"] if k == "repair" else r[k], ensure_ascii=False),
                      json.dumps(v, ensure_ascii=False)))
    st = make_search_text(d)
    conn.execute("UPDATE listings SET search_text = ? WHERE id = ?", (st, listing_id))
    _reindex(conn, listing_id, st)
    learn_listing_fix(conn, listing_id, "frag_fields", {k: v for k, v in adm.items() if k != "repair"},
                      "характеристики: " + ", ".join(FIELD_SPEC[k][1] for k in ch))
    conn.commit()
    return {"ok": True, "changed": ch}


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


def ban_phone(conn: sqlite3.Connection, phone: str, block: bool = True) -> dict:
    """Админ: всё от этого номера — скрыть объекты и запросы, удалить сообщения; block=True — и новые не принимать
    никогда. block=False — удалить только уже собранные объекты (повторы их не вернут), новые объявления принимаются."""
    phone = rules.normalize_phone(phone or "")
    if not phone:
        return {"error": "Номер не распознан."}
    if phone == rules.normalize_phone(config.FEED_PHONE or ""):
        return {"error": "Это номер партнёра (объекты СТРЕЛ из фида) — его нельзя удалить целиком."}
    now = int(time.time())
    if not block:
        listings = conn.execute("""UPDATE listings SET is_active = 0, hidden_reason = 'admin'
                                   WHERE EXISTS (SELECT 1 FROM json_each(listings.phones) j WHERE j.value = ?)
                                     AND source NOT IN ('own', 'feed')""", (phone,)).rowcount
        conn.commit()
        return {"ok": True, "phone": phone, "listings": listings, "requests": 0, "messages": 0, "blocked": False}
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
    return {"ok": True, "phone": phone, "listings": listings, "requests": requests, "messages": len(gone), "blocked": True}


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


def fix_rent_leak(conn: sqlite3.Connection) -> dict:
    """18.10 (Артём: «в аренду постоянно попадают продажи — «11699т.р» это цена продажи»): в портянке «сдам … +КУ» у
    одного объекта делал арендой все объекты сообщения. Один раз: аренда из чатов, у которой в СВОЁМ куске текста явная
    продажа («#продам», «в ДКП», ипотека, задаток, цена от 1,5 млн) и нет «сдам/аренда», — обратно в продажу (с журналом)."""
    from . import db
    moved, ex = 0, []
    for r in conn.execute("""SELECT * FROM listings WHERE deal = 'rent' AND deal_fixed = 0 AND COALESCE(admin_fixed, 0) = 0
                             AND source = 'chat'""").fetchall():
        frag = r["fragment"] or r["description"] or ""
        if not frag or rules.has_rent_words(frag) or not rules.sale_markers(frag, r["type"]):
            continue
        row = dict(r)
        row["deal"] = "sale"
        price = r["price"]
        p = rules.extract_price(frag)
        if p and p >= 300_000:
            price = row["price"] = p
        row["title"] = make_title(row)
        conn.execute("UPDATE listings SET deal = 'sale', price = ?, title = ? WHERE id = ?", (price, row["title"], r["id"]))
        conn.execute("""INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src)
                        VALUES (?, 0, ?, 'deal', 'rent', 'sale', 'проверка аренды')""", (r["id"], int(time.time())))
        moved += 1
        if len(ex) < 30:
            ex.append({"id": r["id"], "title": row["title"]})
    conn.commit()
    rep = {"moved": moved, "examples": ex, "ts": int(time.time())}
    db.set_state(conn, "rent_leak_report", json.dumps(rep, ensure_ascii=False))
    return rep


def archive_stale(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = int(now or time.time())
    # Объекты фида СТРЕЛ не устаревают сами — их снимает сам фид
    # срок — от последнего сообщения в чатах, но не раньше, чем от появления на 1+1 (история Wappi, старые посты)
    cur = conn.execute("""UPDATE listings SET is_active = 0 WHERE is_active = 1 AND source = 'chat'
                          AND MAX(last_seen, COALESCE(added_at, 0)) < ?""", (now - config.STALE_DAYS * 86400,))
    conn.commit()
    return cur.rowcount


def cleanup_old(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Раз в сутки: объекты, пролежавшие в архиве ARCHIVE_DAYS (сняли/устарели и не появились снова), — удалить;
    сообщения старше KEEP_DAYS — тоже."""
    now = int(now or time.time())
    edge = now - config.KEEP_DAYS * 86400
    arch = now - config.ARCHIVE_DAYS * 86400
    # удалённые админом и «всё от номера» не стираем: по ним узнаём тот же объект в новых сообщениях
    # удаляем ТОЛЬКО то, что пролежало в архиве ARCHIVE_DAYS с момента снятия (и не моложе срока жизни целиком)
    life = now - (config.STALE_DAYS + config.ARCHIVE_DAYS) * 86400
    ids = [r[0] for r in conn.execute("""SELECT id FROM listings WHERE is_active = 0
                                         AND COALESCE(removed_at, last_seen + ?) < ?
                                         AND MAX(last_seen, COALESCE(added_at, 0)) < ?
                                         AND COALESCE(hidden_reason, '') NOT IN ('admin', 'phone')""",
                                      (config.STALE_DAYS * 86400, arch, life))]
    for i in ids:
        conn.execute("DELETE FROM listings_fts WHERE rowid = ?", (i,))
        conn.execute("DELETE FROM listing_events WHERE listing_id = ?", (i,))
        conn.execute("DELETE FROM listings WHERE id = ?", (i,))
        shutil.rmtree(Path(config.PHOTOS_DIR) / str(i), ignore_errors=True)  # фото, загруженные агентом
    cur = conn.execute("DELETE FROM messages WHERE ts < ? AND status != 'new'", (edge,))
    if ids:   # счётчик для отчёта «Жизнь объектов» (админка → Обзор)
        from . import db
        db.set_state(conn, "deleted_listings", str(int(db.get_state(conn, "deleted_listings") or 0) + len(ids)))
    conn.commit()
    return {"listings": len(ids), "messages": cur.rowcount}


def life_fix(conn: sqlite3.Connection, now: int | None = None) -> dict | None:
    """17.10 (Артём: «сервис живёт ~15 дней — удаляться ничего не должно»). Один раз (state life_v1):
    1) объектам без дня появления на 1+1 (added_at) ставим «сегодня» — дальше 15 дней на сайте им гарантированы;
    2) снятые как «устаревшие» за последние ARCHIVE_DAYS (не проданные, не скрытые админом, чатом, задатком и т. п.) —
       возвращаем на сайт: их снимали по дате старого сообщения из истории чатов, а не по жизни на 1+1."""
    from . import db
    if db.get_state(conn, "life_v1"):
        return None
    now = int(now or time.time())
    back = conn.execute("""UPDATE listings SET is_active = 1, added_at = ? WHERE is_active = 0 AND source = 'chat'
                           AND removed_reason = 'stale' AND hidden_reason IS NULL AND sold_at IS NULL
                           AND removed_at >= ?""", (now, now - config.ARCHIVE_DAYS * 86400)).rowcount
    stamped = conn.execute("UPDATE listings SET added_at = ? WHERE added_at IS NULL", (now,)).rowcount
    rep = {"restored": back, "stamped": stamped, "ts": now}
    db.set_state(conn, "life_v1", json.dumps(rep))
    conn.commit()
    log.info("Жизнь объектов: вернули на сайт %s, отметили день появления %s", back, stamped)
    return rep


RULES_FIX_V = "1"


def rules_fix(conn: sqlite3.Connection) -> dict | None:
    """18.10 (аудит Артёма по скриншотам): один раз (state rules_fix) поправить уже сохранённые объекты по новым правилам —
    «35,6/11/16» (площадь/этаж/этажность), «1/3 часть дома» — не этаж, «Е-2 КВ» — евро-2, «Обр Сбербанка 1,7 млн» — не цена
    («Всего 6,1 млн» — цена), лишний перенос строки в номере дома. Правленое админом/агентом не трогаем; каждая правка —
    в журнал (listing_edits, src «проверка разбора», можно откатить)."""
    import re
    from . import db
    if db.get_state(conn, "rules_fix") == RULES_FIX_V:
        return None
    now, n = int(time.time()), {}
    rows = conn.execute("""SELECT * FROM listings WHERE source = 'chat' AND COALESCE(admin_fixed, 0) = 0
                           AND COALESCE(owner_user_id, 0) = 0 AND fragment IS NOT NULL""").fetchall()
    for r in rows:
        frag, ch = rules.normalize_digits(r["fragment"]), {}
        af = rules.area_floor(frag)
        if af and (r["area"] is None or r["floor"] is None or (r["floor"], r["floors"]) != (af[1], af[2])):
            ch.update(area=af[0], floor=af[1], floors=af[2])
        if r["house"] and r["house"] != r["house"].strip():
            ch["house"] = r["house"].strip()
        if r["floor"] and r["floors"] and re.search(rf"(?<!\d){r['floor']}\s*/\s*{r['floors']}\s*(?:част|дол)", frag, re.I):
            ch.update(floor=None, floors=None)
        if r["room_kind"] == "classic" and re.search(r"(?<![а-яё])е\s*-\s*[1-6]\s*(?:кв|к\b|ккв)", frag.lower()):
            ch["room_kind"] = "euro"
        cols = r.keys()
        price_locked = ("price_fixed" in cols and r["price_fixed"])
        if not price_locked and re.search(r"(?i)\bобр\b|\bвсего\b|\bитого\b", frag):
            p = rules.extract_price(frag, r["deal"] or "sale")
            if p and r["price"] and p != r["price"]:
                ch["price"] = p
        if not ch:
            continue
        d = dict(r) | ch
        ch["title"] = make_title(d)
        sets = dict(ch)
        if "price" in ch or "area" in ch:
            sets["price_m2"] = _price_m2(d["price"], d["area"])
        if "room_kind" in ch:
            sets["rooms_mask"] = rooms_mask(d["room_kind"], d["rooms"])
        conn.execute(f"UPDATE listings SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), r["id"]))
        for k in ch:
            if k == "title":
                continue
            conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?,0,?,?,?,?, 'проверка разбора')",
                         (r["id"], now, k, json.dumps(r[k], ensure_ascii=False), json.dumps(ch[k], ensure_ascii=False)))
            n[k] = n.get(k, 0) + 1
        st = make_search_text(d)
        conn.execute("UPDATE listings SET search_text = ? WHERE id = ?", (st, r["id"]))
        _reindex(conn, r["id"], st)
    db.set_state(conn, "rules_fix", RULES_FIX_V)
    db.set_state(conn, "rules_fix_report", json.dumps(n, ensure_ascii=False))
    conn.commit()
    return n

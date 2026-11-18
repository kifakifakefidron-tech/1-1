"""Импорт раздела «Все объекты» из «Яблони». Один объект — одна строка таблицы."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
import time
import urllib.request
from urllib.parse import urlsplit

from . import config, db, geo, rules
from .textnorm import norm, words

PREFIX = "appletv:"
SYNCED = "appletv_synced"
REPLACED = "appletv_chat"
REMOVED = "appletv_removed"
MAX_BYTES = 8_000_000
log = logging.getLogger(__name__)


def numbers() -> tuple[tuple[str, ...], str | None]:
    slava = tuple(dict.fromkeys(rules.normalize_phone(n) for n in config.APPLET_SLAVA_PHONES.split(",") if n.strip()))
    artem = rules.normalize_phone(config.APPLET_ARTEM_PHONE)
    return (slava, artem) if len(slava) == 2 and all(slava) and artem and artem not in slava else ((), None)


def active_numbers(conn: sqlite3.Connection) -> set[str]:
    if not config.APPLET_CSV_URL or not db.get_state(conn, SYNCED):
        return set()
    slava, artem = numbers()
    return {*slava, artem} if artem else set()


def contact_labels() -> dict[str, tuple[str, bool]]:
    slava, artem = numbers()
    if not artem:
        return {}
    return {slava[0]: ("Вячеслав Цветков", False), slava[1]: ("Вячеслав Цветков · WhatsApp", True),
            artem: ("Артём · Stayl", False)}


def _phones(text: str) -> set[str]:
    return {p for _, _, p in rules.find_phones(text)}


def only_catalog_phones(phones, managed: set[str]) -> bool:
    if not managed:
        return False
    actual = {rules.normalize_phone(p) for p in phones}
    return bool(actual) and actual <= managed


def drops_chat(text: str, sender: str | None, managed: set[str]) -> bool:
    if not managed:
        return False
    contacts = _phones(text)
    sender = rules.normalize_phone((sender or "").split("@", 1)[0])
    scoped = only_catalog_phones(contacts, managed) or (not contacts and sender in managed)
    return scoped and not rules.is_buyer_request(text)


def _signed(text: str, name: str, phones: set[str]) -> bool:
    return name in norm(text) and bool(_phones(text) & phones)


def _listing_type(value: str) -> tuple[str, int | None, str | None]:
    v = norm(value)
    if v in ("ст", "студия"):
        return "flat", 0, "studio"
    if v.startswith(("дом", "дуплекс", "таунхаус")):
        return "house", None, None
    if v.startswith("коммерц"):
        return "commercial", None, None
    m = re.fullmatch(r"(мини|е|e)?\s*([1-9])\s*(?:к)?", v)
    if m:
        return "flat", int(m[2]), {"мини": "mini", "е": "euro", "e": "euro", None: "classic"}[m[1]]
    raise ValueError("Неизвестный тип объекта в таблице «Яблоня»")


def _sheet_row(r: list[str], phones: list[str]) -> dict:
    from . import parser

    code, room, area_text, price_text = r[2], r[3], r[10], r[11]
    otype, rooms, room_kind = _listing_type(room)
    amount = re.fullmatch(r"(?:р\.|₽)?\s*([\d\s]+)(?:\s*₽)?", price_text)
    price = int(re.sub(r"\s", "", amount[1])) if amount else 0
    if 0 < price < 100_000:
        price *= 1000
    if price <= 0:
        raise ValueError("В опубликованной строке «Яблони» нет корректной цены")
    area = float(area_text.replace(",", ".").replace(" ", ""))
    # Только открытые поля. Колонки с номерами квартир, ключами и владельцами не попадают в карточку.
    place = "\n".join(filter(None, (f"Район {r[4]}" if r[4] else "", f"ЖК {r[6]}" if r[6] else "",
                                   f"Адрес: {r[5]}" if r[5] else "")))
    p = parser._build(None, place, place, None).to_dict()
    house_in_address = re.search(r"\s+(\d[\d/а-яa-z-]*(?:\s*(?:к(?:орпус)?|стр)\.?\s*\d+[а-яa-z]?)?)$", r[5], re.I)
    if house_in_address and not p["house"]:
        p["house"] = house_in_address[1]
        p["street"] = p["street"] or re.sub(r"(?i)^ул\.?\s+", "", r[5][:house_in_address.start()]).strip()
    district = geo.canonical_district(r[4]) or p["district"] or (r[4] or None)
    complex_name = (geo.resolve_complex(r[6]).name if geo.resolve_complex(r[6]) else geo.pretty_name(r[6])) if r[6] else None
    floor, floors = rules.extract_floor(r[9])
    if floor is None and re.fullmatch(r"\d{1,2}", r[9]):
        floor = int(r[9])
    # В исходном описании бывает код сейфа. Показываем только нормализованное состояние.
    condition = rules.detect_repair(r[13])
    if condition is None and norm(r[13]).startswith("рем"):
        condition = "yes"
    description = "Без ремонта" if condition == "no" else "С ремонтом" if condition == "yes" else ""
    url = r[29].splitlines()[0].strip() if r[29] else ""
    parsed = urlsplit(url)
    url = url if parsed.scheme == "https" and parsed.hostname == "arrowsrealty.ru" and "/tproduct/" in parsed.path else None
    if code:
        identifier = "article:" + code
    else:
        identity = [words(x) for x in (r[4], r[5], r[6], r[8], r[9], area_text, room, r[13] if not r[8] else "")]
        identifier = "row:" + hashlib.sha256("\0".join(identity).encode()).hexdigest()[:24]
    return {"ext_id": PREFIX + identifier, "article": code or None, "type": otype, "deal": "sale", "rooms": rooms,
            "room_kind": room_kind, "floor": floor, "floors": floors, "area": area, "land": None,
            "district": district, "complex": complex_name, "settlement": p["settlement"], "street": p["street"],
            "house": p["house"], "description": description, "fragment": place, "repair": condition,
            "price": price, "phones": phones, "url": url}


def parse_csv(body: bytes) -> list[dict]:
    slava, artem = numbers()
    if not artem:
        raise ValueError("Укажите два номера Вячеслава и номер Артёма")
    result = {}
    slava_seen = artem_seen = False
    # A:AM — тот же лист и диапазон, из которых строятся первые подразделы «Все объекты» на /appletv.
    for row in csv.reader(io.StringIO(body.decode("utf-8-sig"), newline=""), strict=True):
        if not any(row):
            continue
        if len(row) < 39:
            raise ValueError("Неполный CSV «Яблони»: ожидался лист A:AM")
        r = [c.strip() for c in row]
        s = _signed(r[30], "вячеслав цветков", set(slava))
        a = _signed(r[31], "артем", {artem})
        slava_seen |= s
        artem_seen |= a
        if not (s or a) or not r[3]:
            continue  # подписи-пустышки в незаполненных строках
        from_site = bool(r[2] or re.search(r"\b(?:артикул\w*|презентаци\w*|фото(?:графи\w*)?)\b", norm(r[31])))
        phones = [*(slava if s else ()), *([artem] if a and not from_site else [])]
        if not phones:
            continue
        item = _sheet_row(r, phones)
        old = result.setdefault(item["ext_id"], item)
        if old != item:
            raise ValueError("В «Яблоне» две разные строки имеют один ключ объекта")
    if not (slava_seen and artem_seen):
        raise ValueError("В CSV «Яблони» не найдены оба раздела агентов")
    return list(result.values())


def sync(conn: sqlite3.Connection, body: bytes, now: int | None = None) -> dict:
    from . import cxnorm, geoaudit
    from .ingest import _price_m2, _reindex, make_search_text, make_title

    now = int(now or time.time())
    slava, artem = numbers()
    signature = hashlib.sha256(body + repr((slava, artem, db.get_state(conn, "feed_synced"))).encode()).hexdigest()
    if db.get_state(conn, "appletv_snapshot") == signature:
        return {"unchanged": True}
    items = parse_csv(body)  # сначала проверяем весь снимок, потом меняем базу
    managed = {*slava, artem}
    stats = dict(total=len(items), added=0, updated=0, hidden=0, chat_replaced=0)
    with conn:
        initial = not db.get_state(conn, SYNCED)
        existing = {r["ext_id"]: r for r in conn.execute("SELECT * FROM listings WHERE ext_id LIKE 'appletv:%'")}
        yml = {r["article"]: r for r in conn.execute("""SELECT id, article, photos, url FROM listings
             WHERE source='feed' AND ext_id LIKE 'feed:%' AND is_active=1 AND article IS NOT NULL
             GROUP BY article HAVING COUNT(*) = 1""")}
        banned = {r[0] for r in conn.execute("SELECT key FROM banned WHERE kind='feed'")}
        seen = set()
        for item in items:
            ext = item["ext_id"]
            seen.add(ext)
            if ext in banned:
                continue
            prev = existing.get(ext)
            if prev and prev["hidden_reason"] in ("admin", "phone"):
                continue
            site = yml.get(item["article"])
            if item["complex"]:
                item["complex"] = None if geoaudit.is_junk_complex(item["complex"]) else cxnorm.canonical(conn, item["complex"])
            item.update(title=make_title(item), photos=site["photos"] if site else "[]",
                        price_m2=_price_m2(item["price"], item["area"]),
                        rooms_mask=rules.rooms_mask(item["room_kind"], item["rooms"]),
                        url=site["url"] if site and site["url"] else item["url"], phones=json.dumps(item["phones"]))
            item["search_text"] = make_search_text(item)
            cols = tuple(item)
            if prev:
                lid = prev["id"]
                if not prev["is_active"] or any(prev[k] != item[k] for k in cols):
                    conn.execute(f"UPDATE listings SET {', '.join(c + '=?' for c in cols)}, is_active=1, hidden_reason=NULL, "
                                 "last_seen=?, sold_at=NULL WHERE id=?", (*item.values(), now, prev["id"]))
                    _reindex(conn, prev["id"], item["search_text"])
                    stats["updated"] += 1
            else:
                lid = conn.execute(f"INSERT INTO listings ({', '.join(cols)}, source,first_seen,last_seen,catalog_migrated) "
                                   f"VALUES ({','.join('?' * len(cols))},'feed',?,?,?)", (*item.values(), now, now, initial)).lastrowid
                _reindex(conn, lid, item["search_text"])
                stats["added"] += 1
            if site:
                conn.execute("""INSERT OR IGNORE INTO dup_pairs(a,b,kind,status,created_at)
                                VALUES (?,?,'applet_article','market',?)""",
                             (min(lid, site["id"]), max(lid, site["id"]), now))
        for ext, old in existing.items():
            if ext not in seen and old["is_active"]:
                conn.execute("UPDATE listings SET is_active=0,hidden_reason=? WHERE id=?", (REMOVED, old["id"]))
                stats["hidden"] += 1
        if initial:
            for old in conn.execute("SELECT id, phones FROM listings WHERE source='chat' AND is_active=1").fetchall():
                if only_catalog_phones(json.loads(old["phones"] or "[]"), managed):
                    conn.execute("UPDATE listings SET is_active=0,hidden_reason=? WHERE id=?", (REPLACED, old["id"]))
                    stats["chat_replaced"] += 1
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?,?)", (SYNCED, str(now)))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('appletv_snapshot',?)", (signature,))
        if any(stats[k] for k in ("added", "updated", "hidden", "chat_replaced")):
            conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('cache_ver',?)", (str(time.time()),))
    return stats


def maybe_sync(conn: sqlite3.Connection) -> dict | None:
    if not config.APPLET_CSV_URL:
        return None
    now = int(time.time())
    if now - int(db.get_state(conn, "appletv_attempt") or 0) < config.APPLET_SYNC_SECONDS:
        return None
    db.set_state(conn, "appletv_attempt", str(now))
    request = urllib.request.Request(config.APPLET_CSV_URL, headers={"User-Agent": "1plus1-search/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("CSV «Яблони» больше допустимого размера")
        result = sync(conn, body, now)
        return None if result.get("unchanged") else result
    except Exception as e:  # noqa: BLE001 — сохраняем последний успешный снимок
        log.warning("Синхронизация «Яблони» отложена: %s", type(e).__name__)
        return {"error": str(e)[:200] if isinstance(e, ValueError) else type(e).__name__}

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

from . import config, db, geo, rules
from .stats import day_start
from .textnorm import norm, words

PREFIX = "appletv:"
STAYL = PREFIX + "stayl:"
SYNCED = "appletv_synced"
SPLIT = "appletv_separate_offers"
FORMAT = "2"
IMPORTED_FORMAT = "appletv_import_format"
REPLACED = "appletv_chat"
REMOVED = "appletv_removed"
MAX_BYTES = 8_000_000
SYNC_HOURS = (8, 15, 19)  # Москва
RETRY_SECONDS = 10 * 60
PRIVATE_LINE = re.compile(
    r"\b(?:ключи|сейф\w*|гнездыш\w*|домофон\w*|парол\w*|паспорт\w*|владелец|фио|доступ)\b"
    r"|\bключ\s+(?:от|у|в|под|лежит)\b"
    r"|\b(?:код|кв\.?|квартира)\s*(?:(?:[:№#-]|no)\s*|\s+)\d+"
    r"|\b(?:собственник|контакт|телефон)\s*[:—]"
)
AD_FOOTER = re.compile(r"^(?:цена\b|артикул\b|фото\s*(?:\+|и)\s*презентаци|презентация\s+на\s+сайте|"
                       r"[a-z]{2}\s*:\s*\d)")
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


def exclude_catalog_contacts(phones: list[str], roles: dict, managed: set[str]) -> tuple[list[str], dict]:
    return ([p for p in phones if rules.normalize_phone(p) not in managed],
            {p: role for p, role in roles.items() if rules.normalize_phone(p) not in managed})


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


def _ad_description(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        label = re.sub(r"^[\W_]+", "", norm(rules.normalize_digits(line)))
        if re.match(r"^(?:вячеслав\s+цветков|артем)\b", label):
            break
        if not re.search(r"[а-яa-z]", label) or _phones(line) or PRIVATE_LINE.search(label):
            continue
        if AD_FOOTER.match(label) or "https://" in label or "http://" in label:
            continue
        lines.append(line)
    return "\n".join(lines)


def _sheet_row(r: list[str]) -> dict:
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
    # Для классификации ремонта используем структурированную колонку, а не текст объявления.
    condition_text = norm(r[13])
    condition = rules.detect_repair(r[13])
    if condition is None and condition_text.startswith("рем"):
        condition = "yes"
    finish = ("Предчистовая отделка" if re.search(r"\bпчо\b|предчистов", condition_text)
              else "Частичный ремонт" if "частичный ремонт" in condition_text
              else {"no": "Без ремонта", "yes": "С ремонтом"}.get(condition))
    repair_context = [finish] if finish else []
    if re.search(r"(?:^|/)меб(?:ель)?(?:/|$)", condition_text):
        repair_context.append("Есть мебель")
    if re.search(r"(?:^|/)тех(?:ника)?(?:/|$)", condition_text):
        repair_context.append("Есть техника")
    if norm(r[15]) == "нет":
        repair_context.append("Без обременений")
    if code:
        identifier = "article:" + code
    else:
        identity = [words(x) for x in (r[4], r[5], r[6], r[8], r[9], area_text, room, r[13] if not r[8] else "")]
        identifier = "row:" + hashlib.sha256("\0".join(identity).encode()).hexdigest()[:24]
    return {"ext_id": PREFIX + identifier, "article": code or None, "type": otype, "deal": "sale", "rooms": rooms,
            "room_kind": room_kind, "floor": floor, "floors": floors, "area": area, "land": None,
            "district": district, "complex": complex_name, "settlement": p["settlement"], "street": p["street"],
            "house": p["house"], "fragment": place, "repair": condition,
            "price": price, "repair_context": "\n".join(repair_context)}


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
        if not s and not (a and not from_site):
            continue
        row_data = _sheet_row(r)
        row_id = row_data["ext_id"]
        subscriptions = []
        if s:
            subscriptions.append((row_id, list(slava), r[30]))
        if a and not from_site:
            subscriptions.append((STAYL + row_id.removeprefix(PREFIX), [artem], r[31]))
        for ext, phones, text in subscriptions:
            item = {**row_data, "ext_id": ext, "phones": phones, "description": _ad_description(text)}
            if result.setdefault(ext, item) != item:
                raise ValueError("В «Яблоне» две разные строки имеют один ключ объекта")
    if not (slava_seen and artem_seen):
        raise ValueError("В CSV «Яблони» не найдены оба раздела агентов")
    return list(result.values())


def sync(conn: sqlite3.Connection, body: bytes, now: int | None = None) -> dict:
    from . import cxnorm, geoaudit
    from .ingest import _price_m2, _reindex, make_search_text, make_title

    now = int(now or time.time())
    slava, artem = numbers()
    signature = hashlib.sha256(body + repr((FORMAT, slava, artem, db.get_state(conn, "feed_synced"))).encode()).hexdigest()
    if db.get_state(conn, "appletv_snapshot") == signature and db.get_state(conn, IMPORTED_FORMAT) == FORMAT:
        return {"unchanged": True}
    items = parse_csv(body)  # сначала проверяем весь снимок, потом меняем базу
    managed = {*slava, artem}
    stats = dict(total=len(items), added=0, updated=0, hidden=0, chat_replaced=0)
    with conn:
        initial = not db.get_state(conn, SYNCED)
        splitting = not db.get_state(conn, SPLIT)
        existing = {r["ext_id"]: r for r in conn.execute("SELECT * FROM listings WHERE ext_id LIKE 'appletv:%'")}
        yml = {r["article"]: r["id"] for r in conn.execute("""SELECT id, article FROM listings
             WHERE source='feed' AND ext_id LIKE 'feed:%' AND is_active=1 AND article IS NOT NULL
             GROUP BY article HAVING COUNT(*) = 1""")}
        banned = {r[0] for r in conn.execute("SELECT key FROM banned WHERE kind='feed'")}
        seen = set()
        saved = {}
        for item in items:
            ext = item["ext_id"]
            seen.add(ext)
            if ext in banned:
                continue
            prev = existing.get(ext)
            if prev and prev["hidden_reason"] in ("admin", "phone"):
                continue
            if prev and prev["price_fixed"] and item["price"] == prev["price_wrong"]:
                item["price"] = prev["price"]
            if item["complex"]:
                item["complex"] = None if geoaudit.is_junk_complex(item["complex"]) else cxnorm.canonical(conn, item["complex"])
            item.update(title=make_title(item), photos="[]", url=None,
                        price_m2=_price_m2(item["price"], item["area"]),
                        rooms_mask=rules.rooms_mask(item["room_kind"], item["rooms"]),
                        phones=json.dumps(item["phones"]))
            repair_context = item.pop("repair_context")
            item["search_text"] = make_search_text(item)
            repair_text = make_search_text({**item, "description": repair_context})
            cols = tuple(item)
            if prev:
                lid = prev["id"]
                if not prev["is_active"] or any(prev[k] != item[k] for k in cols):
                    conn.execute(f"UPDATE listings SET {', '.join(c + '=?' for c in cols)}, is_active=1, hidden_reason=NULL, "
                                 "last_seen=?, sold_at=NULL WHERE id=?", (*item.values(), now, prev["id"]))
                    _reindex(conn, prev["id"], item["search_text"], repair_text=repair_text)
                    stats["updated"] += 1
            else:
                old_combined = splitting and ext.startswith(STAYL) and PREFIX + ext.removeprefix(STAYL) in existing
                lid = conn.execute(f"INSERT INTO listings ({', '.join(cols)}, source,first_seen,last_seen,catalog_migrated) "
                                   f"VALUES ({','.join('?' * len(cols))},'feed',?,?,?)",
                                   (*item.values(), now, now, initial or old_combined)).lastrowid
                _reindex(conn, lid, item["search_text"], repair_text=repair_text)
                stats["added"] += 1
            saved[ext] = lid
            if site := yml.get(item["article"]):
                conn.execute("""INSERT OR IGNORE INTO dup_pairs(a,b,kind,status,created_at)
                                VALUES (?,?,'applet_article','market',?)""",
                             (*sorted((lid, site)), now))
        for ext, lid in saved.items():
            if ext.startswith(STAYL) and (slava_id := saved.get(PREFIX + ext.removeprefix(STAYL))):
                conn.execute("""INSERT OR IGNORE INTO dup_pairs(a,b,kind,status,created_at)
                                VALUES (?,?,'applet_row','market',?)""", (*sorted((lid, slava_id)), now))
        for ext, old in existing.items():
            if ext not in seen and old["is_active"]:
                conn.execute("UPDATE listings SET is_active=0,hidden_reason=? WHERE id=?", (REMOVED, old["id"]))
                stats["hidden"] += 1
        if splitting:
            for old in conn.execute("""SELECT id, phones, phone_roles FROM listings WHERE source='chat' AND is_active=1
                AND ((json_valid(phones) AND EXISTS (SELECT 1 FROM json_each(phones) WHERE value IN (?, ?, ?)))
                     OR (json_valid(phone_roles) AND EXISTS (SELECT 1 FROM json_each(phone_roles) WHERE key IN (?, ?, ?))))""",
                                    (*slava, artem, *slava, artem)).fetchall():
                phones = json.loads(old["phones"] or "[]")
                cleaned, roles = exclude_catalog_contacts(phones, json.loads(old["phone_roles"] or "{}"), managed)
                if phones and not cleaned:
                    conn.execute("UPDATE listings SET is_active=0,hidden_reason=? WHERE id=?", (REPLACED, old["id"]))
                    stats["chat_replaced"] += 1
                else:
                    conn.execute("UPDATE listings SET phones=?,phone_roles=? WHERE id=?",
                                 (json.dumps(cleaned), json.dumps(roles), old["id"]))
            conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?, '1')", (SPLIT,))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?,?)", (SYNCED, str(now)))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES (?,?)", (IMPORTED_FORMAT, FORMAT))
        conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('appletv_snapshot',?)", (signature,))
        if splitting or any(stats[k] for k in ("added", "updated", "hidden", "chat_replaced")):
            conn.execute("INSERT OR REPLACE INTO state(key,value) VALUES ('cache_ver',?)", (str(time.time()),))
    return stats


def maybe_sync(conn: sqlite3.Connection) -> dict | None:
    if not config.APPLET_CSV_URL:
        return None
    now = int(time.time())
    today = day_start(now)
    due = max((today + hour * 3600 for hour in SYNC_HOURS if today + hour * 3600 <= now),
              default=today - (24 - SYNC_HOURS[-1]) * 3600)
    completed = int(db.get_state(conn, "appletv_completed_slot") or 0)
    attempt_key = f"appletv_attempt_at_{FORMAT}"
    attempt = int(db.get_state(conn, attempt_key) or 0)
    refresh_format = db.get_state(conn, IMPORTED_FORMAT) != FORMAT
    if (completed >= due and not refresh_format) or (attempt >= due and now - attempt < RETRY_SECONDS):
        return None
    db.set_state(conn, attempt_key, str(now))
    request = urllib.request.Request(config.APPLET_CSV_URL, headers={"User-Agent": "1plus1-search/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("CSV «Яблони» больше допустимого размера")
        result = sync(conn, body, now)
        db.set_state(conn, "appletv_completed_slot", str(due))
        return result
    except Exception as e:  # noqa: BLE001 — сохраняем последний успешный снимок
        log.warning("Синхронизация «Яблони» отложена: %s", type(e).__name__)
        return {"error": str(e)[:200] if isinstance(e, ValueError) else type(e).__name__}

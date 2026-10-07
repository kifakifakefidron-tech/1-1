"""Запросы покупателей из чатов («куплю», «ищу», «есть клиент») — раньше выбрасывались.

Сохраняем запрос с разобранными пожеланиями (тип, комнаты, бюджет, районы, ЖК) и сводим с объектами:
в окне объекта — «под этот объект есть N покупателей», на странице /requests — лента запросов
со ссылкой «подходящие объекты». Телефоны — как у объявлений: только с доступом.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import config, geo, parser, rules
from .textnorm import norm, words

STALE_DAYS = 20


# ─── разбор запроса ────────────────────────────────────────────────────────
def _districts(text: str) -> list[str]:
    """Все районы, названные в запросе («ФМР или ЮМР»)."""
    w = f" {words(text)} "
    out = []
    for alias, canon in geo._ALIASES:
        idx = w.find(f" {alias} ")
        if idx < 0:
            continue
        if alias in geo._AMBIGUOUS and not geo._DISTRICT_MARKER.search(w[:idx + 1].rstrip() + " "):
            continue
        if canon not in out:
            out.append(canon)
        w = w.replace(f" {alias} ", " ")
    return out[:8]


def _complexes(text: str) -> list[str]:
    """ЖК из запроса: первый — как в объявлениях, следующие — только из справочника («ЖК Мозаика или Свобода»)."""
    out, rest = [], text
    for _ in range(5):
        cx = geo.find_complex_in_text(rest)
        if not cx or cx.name in out or (out and geo.resolve_complex(cx.name) is None):
            break
        out.append(cx.name)
        rest = re.sub(re.escape(cx.name), " ", rest, flags=re.I)
    return out


_ROOM_RANGE = re.compile(r"\b([1-5])\s*(?:-|–|или|/|,|и)\s*([1-5])\s*-?\s*(?:х\s*)?(?:к\b|кк|ком|комн|комнатн)", re.I)


_ROOM_ONE = re.compile(r"(?<![\d/.,])([1-5])\s*-?\s*(?:х\s*)?(?:к\b|кк|ком\b|комн|комнатн)", re.I)
_ROOM_WORDS = [(r"\bоднушк|\bоднокомнатн", 1), (r"\bдвушк|\bдвухкомнатн", 2), (r"\bтр[её]шк|\bтр[её]хкомнатн", 3)]


def _rooms_mask(text: str) -> int:
    t = norm(text)
    mask = 0
    if re.search(r"\bстуди", t):
        mask |= 1
    for m in _ROOM_RANGE.finditer(t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        for n in range(a, b + 1):
            mask |= 1 << min(n, 4)
    for m in _ROOM_ONE.finditer(t):   # «студию или 1к», «2к или 3к»
        mask |= 1 << min(int(m.group(1)), 4)
    for rx, n in _ROOM_WORDS:
        if re.search(rx, t):
            mask |= 1 << n
    if not mask:
        rk = rules.extract_room_kind(text)
        if rk:
            mask = rules.rooms_mask(rk[0], rk[1])
        else:
            n = rules.extract_rooms(text)
            if n is not None:
                mask = 1 << min(n, 4)
    return mask


_BUDGET = re.compile(r"(?:\bдо|\bбюджет\w*|не\s+дороже|не\s+более|\bmax|\bмакс\w*|в\s+пределах|\bза)\s*[:\-–]?\s*"
                     r"(\d+(?:[.,]\d+)?)\s*(млн|млн\.|м\b|миллион\w*|т\.?\s?р|тыс\w*|к\b|000)?", re.I)
_FROM = re.compile(r"\bот\s*(\d+(?:[.,]\d+)?)\s*(млн|м\b|т\.?\s?р|тыс\w*|к\b)?", re.I)


def _money(num: str, unit: str | None, deal: str) -> int | None:
    try:
        v = float(num.replace(",", "."))
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit.startswith("м"):
        v *= 1e6
    elif unit.startswith("т") or unit == "к" or unit == "000":
        v *= 1000
    elif deal == "rent":
        v = v * 1000 if v < 1000 else v           # «до 35» — тысяч в месяц
    else:
        v = v * 1e6 if v < 100 else v * 1000 if v < 100_000 else v   # «до 6,5» — млн; «до 6500» — тысяч
    v = int(v)
    if deal == "rent":
        return v if 3_000 <= v <= 2_000_000 else None
    return v if 300_000 <= v <= 1_000_000_000 else None


def parse_request(text: str) -> dict:
    t = rules.normalize_digits(text)
    n = norm(t)
    deal = "rent" if re.search(r"\b(?:сниму|снимем|арендую|арендуем|аренд\w*|на длительн|посуточн|помесячн)", n) else "sale"
    typ = rules.detect_type(t)
    mask = _rooms_mask(t)
    if typ in (None, "new") and mask:
        typ = "flat"
    price_max = None
    for m in _BUDGET.finditer(t):
        tail = t[m.end(): m.end() + 6].lower()
        if re.match(r"\s*(?:м2|м²|кв|сот|эт|этаж|км|мин|лет|год)", tail):
            continue
        price_max = _money(m.group(1), m.group(2), deal)
        if price_max:
            break
    price_min = None
    fm = _FROM.search(t)
    if fm and not re.match(r"\s*(?:м2|м²|кв|сот|эт)", t[fm.end(): fm.end() + 5].lower()):
        price_min = _money(fm.group(1), fm.group(2), deal)
        if price_max and price_min and price_min >= price_max:
            price_min = None
    return {"deal": deal, "type": "flat" if typ == "new" else typ, "rooms_mask": mask, "price_max": price_max,
            "price_min": price_min, "districts": _districts(t), "complexes": _complexes(t),
            "phones": rules.extract_phones(t)}


# ─── сохранение ────────────────────────────────────────────────────────────
def save(conn: sqlite3.Connection, m: sqlite3.Row) -> int | None:
    """Сообщение-запрос → запись в buyer_requests (повтор того же текста — только «ещё присылали»)."""
    text = (m["text"] or "").strip()
    if len(text) < 15:
        return None
    hit = conn.execute("SELECT id FROM buyer_requests WHERE text_hash = ?", (m["text_hash"],)).fetchone()
    if hit:
        conn.execute("""UPDATE buyer_requests SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, is_active = 1
                        WHERE id = ?""", (m["ts"], hit[0]))
        return hit[0]
    p = parse_request(text)
    if not p["phones"] and m["sender"]:
        ph = rules.normalize_phone(str(m["sender"]).split("@")[0])
        if ph:
            p["phones"] = [ph]
    # тот же человек с тем же запросом другими словами — тоже повтор
    for r in conn.execute("""SELECT id, phones, deal, type, rooms_mask, districts FROM buyer_requests
                             WHERE is_active = 1 AND deal = ? AND rooms_mask = ? AND COALESCE(type, '') = ?""",
                          (p["deal"], p["rooms_mask"], p["type"] or "")):
        if p["phones"] and set(json.loads(r["phones"])) & set(p["phones"]) and json.loads(r["districts"]) == p["districts"]:
            conn.execute("UPDATE buyer_requests SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, text = ? WHERE id = ?",
                         (m["ts"], text[:3000], r["id"]))
            return r["id"]
    cur = conn.execute(
        """INSERT INTO buyer_requests (message_id, text_hash, text, chat_name, deal, type, rooms_mask, price_min, price_max,
                                       districts, complexes, phones, first_seen, last_seen, seen_count, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
        (m["id"], m["text_hash"], text[:3000], m["chat_name"], p["deal"], p["type"], p["rooms_mask"], p["price_min"],
         p["price_max"], json.dumps(p["districts"], ensure_ascii=False), json.dumps(p["complexes"], ensure_ascii=False),
         json.dumps(p["phones"]), m["ts"], m["ts"]))
    return cur.lastrowid


def backfill(conn: sqlite3.Connection) -> int:
    """Один раз: запросы из уже разобранных сообщений (раньше их выбрасывали)."""
    n = 0
    for m in conn.execute("SELECT * FROM messages WHERE kind = 'request' AND status = 'done' ORDER BY ts").fetchall():
        if save(conn, m):
            n += 1
    conn.commit()
    return n


def archive(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = int(now or time.time())
    n = conn.execute("UPDATE buyer_requests SET is_active = 0 WHERE is_active = 1 AND last_seen < ?",
                     (now - STALE_DAYS * 86400,)).rowcount
    conn.commit()
    return n


# ─── сведение запросов и объектов ──────────────────────────────────────────
_GROUP = {"new": "flat"}


def matches(req: dict, o: dict) -> bool:
    """Подходит ли объект под запрос (мягко: бюджет +10 %, район или ЖК — если указаны)."""
    if req["deal"] != o.get("deal"):
        return False
    if req["type"] and _GROUP.get(o.get("type"), o.get("type")) != req["type"]:
        return False
    if req["rooms_mask"] and o.get("rooms_mask") and not (req["rooms_mask"] & o["rooms_mask"]):
        return False
    price = o.get("price")
    if req["price_max"] and (not price or price > req["price_max"] * 1.1):
        return False
    if req["price_min"] and price and price < req["price_min"] * 0.9:
        return False
    d, cx = req["districts"], req["complexes"]
    if d or cx:
        o_d = {o.get("district")} | set(o.get("extra_districts") or [])
        ok = bool(o_d & set(d))
        if cx and o.get("complex"):
            ok = ok or geo.complex_key(o["complex"]) in {geo.complex_key(c) for c in cx}
        if not ok:
            return False
    return True


def _row(r: sqlite3.Row, with_contacts: bool) -> dict:
    d = {k: r[k] for k in ("id", "deal", "type", "rooms_mask", "price_min", "price_max", "first_seen", "last_seen",
                           "seen_count")}
    d["districts"] = json.loads(r["districts"] or "[]")
    d["complexes"] = json.loads(r["complexes"] or "[]")
    phones = json.loads(r["phones"] or "[]")
    d["text"] = r["text"] if with_contacts else parser.strip_phones(r["text"])
    if with_contacts:
        d["phones"] = phones
    else:
        d["phones_count"] = len(phones)
    return d


def for_listing(conn: sqlite3.Connection, o: dict, with_contacts: bool, limit: int = 30) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM buyer_requests WHERE is_active = 1 AND deal = ? ORDER BY last_seen DESC LIMIT 2000",
                          (o.get("deal"),)):
        req = {"deal": r["deal"], "type": r["type"], "rooms_mask": r["rooms_mask"], "price_max": r["price_max"],
               "price_min": r["price_min"], "districts": json.loads(r["districts"] or "[]"),
               "complexes": json.loads(r["complexes"] or "[]")}
        if matches(req, o):
            out.append(_row(r, with_contacts))
            if len(out) >= limit:
                break
    return out


def search_url(r: dict) -> str:
    """Подходящие объекты на главной: те же пожелания фильтрами."""
    from urllib.parse import urlencode
    q = []
    if r["deal"] == "rent":
        q.append(("deal", "rent"))
    if r["type"]:
        q.append(("type", r["type"]))
    rooms = [str(i) for i in range(5) if r["rooms_mask"] & (1 << i)]
    if rooms:
        q.append(("rooms", "|".join(rooms)))
    if r["price_max"]:
        q.append(("pmax", f"{r['price_max'] * 1.1 / (1000 if r['deal'] == 'rent' else 1e6):g}"))
    if r["districts"]:
        q.append(("district", "|".join(r["districts"])))
    if r["complexes"]:
        q.append(("complex", "|".join(r["complexes"])))
    return "/?" + urlencode(q)


def feed(conn: sqlite3.Connection, with_contacts: bool, deal: str = "", district: str = "", rooms: int | None = None,
         q: str = "", page: int = 1, size: int = 30) -> dict:
    where, params = ["is_active = 1"], []
    if deal in ("sale", "rent"):
        where.append("deal = ?"); params.append(deal)
    if district:
        where.append("EXISTS (SELECT 1 FROM json_each(districts) j WHERE j.value = ?)"); params.append(district)
    if rooms is not None and 0 <= rooms <= 4:
        where.append("(rooms_mask & ?) != 0"); params.append(1 << rooms)
    if q:
        where.append("text LIKE ?"); params.append(f"%{q.strip()[:60]}%")
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM buyer_requests WHERE {w}", params).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM buyer_requests WHERE {w} ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                        params + [size, (max(1, page) - 1) * size]).fetchall()
    items = []
    for r in rows:
        d = _row(r, with_contacts)
        d["url"] = search_url(d)
        items.append(d)
    districts = [x[0] for x in conn.execute(
        "SELECT j.value, COUNT(*) n FROM buyer_requests, json_each(districts) j WHERE is_active = 1 GROUP BY 1 ORDER BY 2 DESC LIMIT 40")]
    return {"total": total, "items": items, "districts": districts, "page": page,
            "pages": (total + size - 1) // size, "stale_days": STALE_DAYS, "access": with_contacts,
            "site": config.SITE_URL}

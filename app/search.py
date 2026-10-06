"""Поиск объектов: фильтры + точный текстовый поиск.

Текстовый поиск ищет слова целиком (с учётом окончаний по началу слова):
«движение» найдёт «ЖК Движение», но не тысячу случайных участков, как раньше,
когда искалось «похожее» по всем длинным текстам.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from . import geo
from .rules import TYPE_LABELS
from .textnorm import norm, words

SORTS = {
    "new": "l.last_seen DESC, l.id DESC",
    "price_asc": "l.price IS NULL, l.price ASC, l.id DESC",
    "price_desc": "l.price IS NULL, l.price DESC, l.id DESC",
    "price_m2": "l.price_m2 IS NULL, l.price_m2 ASC, l.id DESC",
    "area_desc": "l.area IS NULL, l.area DESC, l.id DESC",
}


@dataclass
class Query:
    q: str = ""
    types: list[str] = field(default_factory=list)
    deal: str = "sale"
    rooms: list[int] = field(default_factory=list)   # 0 студия … 4 = «4 и больше»
    price_min: int | None = None
    price_max: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    land_min: float | None = None
    land_max: float | None = None
    districts: list[str] = field(default_factory=list)
    complexes: list[str] = field(default_factory=list)
    not_first: bool = False
    not_last: bool = False
    fresh_days: int | None = None
    new_days: int | None = None   # только впервые появившиеся за N дней («Новое сегодня»)
    since: int | None = None   # только появившиеся позже (кнопка «Обновить»)
    sort: str = "new"
    page: int = 1
    size: int = 30


# ─── разбор параметров адреса ──────────────────────────────────────────────
def num_param(v: str | None, cast=float):
    if v in (None, ""):
        return None
    try:
        return cast(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _list(qp, key: str) -> list[str]:
    vals: list[str] = []
    for v in qp.getlist(key):
        vals += [x for x in v.split(",") if x]
    return vals


def query_from_params(qp) -> Query:
    """Параметры адреса (?q=…&rooms=1,2…) → Query. qp — QueryParams Starlette."""
    rooms = [int(x) for x in _list(qp, "rooms") if x.isdigit()]
    return Query(
        q=qp.get("q", "")[:200],
        types=[t for t in _list(qp, "type") if t in TYPE_LABELS],
        deal="rent" if qp.get("deal") == "rent" else "sale",
        rooms=rooms,
        price_min=num_param(qp.get("price_min"), int), price_max=num_param(qp.get("price_max"), int),
        area_min=num_param(qp.get("area_min")), area_max=num_param(qp.get("area_max")),
        land_min=num_param(qp.get("land_min")), land_max=num_param(qp.get("land_max")),
        districts=_list(qp, "district"), complexes=_list(qp, "complex"),
        not_first=qp.get("not_first") == "1", not_last=qp.get("not_last") == "1",
        fresh_days=num_param(qp.get("fresh_days"), int), new_days=num_param(qp.get("new_days"), int),
        since=num_param(qp.get("since"), int),
        sort=qp.get("sort", "new"),
        page=num_param(qp.get("page"), int) or 1,
        size=num_param(qp.get("size"), int) or 30,
    )


# ─── страницы и поиск ──────────────────────────────────────────────────────
def _stem(t: str) -> str:
    """Окончания: «мозаике» → «мозаик» (ищем по началу слова)."""
    if len(t) > 5 and not t.isdigit():
        t = re.sub(r"(ами|ями|ого|ему|ому|ыми|ими|ах|ях|ой|ей|ом|ем|ам|ям|ую|юю|ая|яя|ые|ие|ый|ий|ов|ев|а|я|у|ю|е|ы|и|о)$", "", t)
    return t


_ROOM_WORDS = [(r"\bстуди\w*", 0), (r"\bоднушк\w*|\bоднокомнатн\w*", 1), (r"\bдвушк\w*|\bдвухкомнатн\w*", 2),
               (r"\bтр[её]шк\w*|\bтр[её]хкомнатн\w*", 3), (r"\bчетыр[её]хкомнатн\w*", 4)]
_ROOM_NUM = re.compile(r"\b(?:евро\s*-?\s*)?([1-5])\s*-?\s*(?:х\s*)?(?:к|кк|ккв|кв|ком|комн\w*|комнатн\w*)\b|\bевро\s*-?\s*([1-5])\b")
_STOP = {"жк", "ул", "улица", "мкр", "мкрн", "микрорайон", "район", "р", "н", "в", "на", "и", "квартира",
         "квартиру", "квартиры", "кв", "продажа", "продам", "купить"}


def interpret(q: str) -> tuple[str | None, list[int]]:
    """Строка поиска → (запрос FTS, комнаты).

    «2к фмр» → комнаты [2] + район ФМР во всех написаниях (фмр, фестивальный, фестивалка…);
    остальные слова ищутся по началу слова, все вместе."""
    w = f" {norm(q)} "
    rooms: list[int] = []
    for rx, n in _ROOM_WORDS:
        if re.search(rx, w):
            rooms.append(n)
            w = re.sub(rx, " ", w)
    for m in re.finditer(r"\bмини\s*-?\s*([1-4])\b", w):
        n = int(m.group(1))
        rooms += [n - 1, n]
    w = re.sub(r"\bмини\s*-?\s*([1-4])\b", " ", w)
    for m in re.finditer(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", w):
        rooms.append(int(m.group(1) or m.group(2)) - 1)
    w = re.sub(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", " ", w)
    for m in _ROOM_NUM.finditer(w):
        rooms.append(int(m.group(1) or m.group(2)))
    w = _ROOM_NUM.sub(" ", w)

    groups: list[str] = []
    # Районы: любое написание → все написания этого района
    for alias, canon in geo._ALIASES:
        a = f" {alias} "
        if a in f" {' '.join(w.split())} ":
            names = {words(canon)} | {al for al, c in geo._ALIASES if c == canon}
            groups.append("(" + " OR ".join(f'"{n}"' if " " in n else f'"{_stem(n)}"*' for n in sorted(names)) + ")")
            w = f" {' '.join(w.split())} ".replace(a, " ")

    tokens = [t for t in re.split(r"[^0-9a-zа-я]+", w) if t]
    tokens = [t for t in tokens if (len(t) >= 2 or t.isdigit()) and t not in _STOP]
    groups += [f'"{_stem(t)}"*' for t in tokens]
    return (" AND ".join(groups) or None), sorted(set(rooms))


def _fts_query(q: str) -> str | None:
    return interpret(q)[0]


def _where(qr: Query, now: int) -> tuple[str, list]:
    w = ["l.is_active = 1", "l.deal = ?"]
    p: list = [qr.deal]
    fts, q_rooms = interpret(qr.q) if qr.q else (None, [])
    if q_rooms and not qr.rooms:  # «2к» в строке поиска = фильтр «2 комнаты»
        qr = Query(**{**qr.__dict__, "rooms": q_rooms})
    if qr.types:
        types = list(qr.types) + (["new"] if "flat" in qr.types else [])  # старые записи «новостройка» = квартиры
        w.append(f"l.type IN ({','.join('?' * len(types))})")
        p += types
    if qr.rooms:
        mask = 0
        for r in qr.rooms:
            mask |= 1 << max(0, min(r, 4))
        w.append("(l.rooms_mask & ?) != 0")
        p.append(mask)
    for col, lo, hi in (("price", qr.price_min, qr.price_max), ("area", qr.area_min, qr.area_max),
                        ("land", qr.land_min, qr.land_max)):
        if lo is not None:
            w.append(f"l.{col} >= ?"); p.append(lo)
        if hi is not None:
            w.append(f"l.{col} <= ?"); p.append(hi)
    if qr.districts:
        w.append(f"l.district IN ({','.join('?' * len(qr.districts))})"); p += qr.districts
    if qr.complexes:
        keys = sorted({geo.complex_key(c) for c in qr.complexes if geo.complex_key(c)})
        w.append(f"cxkey(l.complex) IN ({','.join('?' * len(keys))})"); p += keys
    if qr.not_first:
        w.append("(l.floor IS NULL OR l.floor > 1)")
    if qr.not_last:
        w.append("(l.floor IS NULL OR l.floors IS NULL OR l.floor < l.floors)")
    if qr.fresh_days:
        w.append("l.last_seen >= ?"); p.append(now - qr.fresh_days * 86400)
    if qr.new_days:
        w.append("l.first_seen >= ?"); p.append(now - qr.new_days * 86400)
    if qr.since:
        w.append("l.first_seen > ?"); p.append(qr.since)
    if fts:
        w.append("l.id IN (SELECT rowid FROM listings_fts WHERE listings_fts MATCH ?)"); p.append(fts)
    return " AND ".join(w), p


PUBLIC_FIELDS = ("id", "type", "deal", "rooms", "area", "land", "floor", "floors", "price", "price_m2",
                 "district", "complex", "settlement", "street", "house", "title", "description",
                 "first_seen", "last_seen", "seen_count", "lat", "lon", "source", "url", "room_kind", "article",
                 "is_active", "expires_at",
                 "prev_price", "price_changed_at")


def mask_phone(p: str) -> str:
    """+79181112233 → «+7 918 •••-••-••»: видно, что номер есть, но не сам номер."""
    d = "".join(ch for ch in p if ch.isdigit())
    return f"+7 {d[1:4]} •••-••-••" if len(d) == 11 else "+7 ••• •••-••-••"


def row_to_item(r: sqlite3.Row, with_contacts: bool) -> dict:
    d = {k: r[k] for k in PUBLIC_FIELDS}
    d["photos"] = json.loads(r["photos"] or "[]")
    phones = json.loads(r["phones"] or "[]")
    if with_contacts:
        d["phones"] = phones
        d["fragment"] = r["fragment"]
    else:
        d["phones_masked"] = [mask_phone(p) for p in phones]
    return d


def search(conn: sqlite3.Connection, qr: Query, now: int, with_contacts: bool) -> dict:
    where, params = _where(qr, now)
    total = conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {where}", params).fetchone()[0]
    size = max(1, min(qr.size, 100))
    page = max(1, qr.page)
    # Объекты СТРЕЛ сортируются вместе со всеми, без вывода вперёд (решение 07.10)
    order = SORTS.get(qr.sort, SORTS["new"])
    rows = conn.execute(
        f"SELECT * FROM listings l WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "pages": (total + size - 1) // size,
        "items": [row_to_item(r, with_contacts) for r in rows],
    }


def map_points(conn: sqlite3.Connection, qr: Query, now: int) -> dict:
    """Точки для карты по тем же фильтрам + сколько объектов без точки (чтобы было видно, что не потеряны)."""
    where, params = _where(qr, now)
    rows = conn.execute(
        f"""SELECT l.id, l.lat, l.lon, l.price, l.title, l.source, l.complex, l.district, l.street,
                   l.geo_status = 'approx' AS approx, json_extract(l.photos, '$[0]') AS photo
            FROM listings l WHERE {where} AND l.lat IS NOT NULL LIMIT 20000""",
        params,
    ).fetchall()
    total, pending = conn.execute(
        f"SELECT COUNT(*), SUM(l.lat IS NULL AND l.geo_status = 'pending') FROM listings l WHERE {where}", params).fetchone()
    return {"points": [dict(r) for r in rows], "total": total, "pending": pending or 0}


def facets(conn: sqlite3.Connection, qr: Query, now: int) -> dict:
    """Счётчики для фильтров: районы и ЖК с учётом остальных фильтров."""
    base = Query(**{**qr.__dict__, "districts": [], "complexes": []})
    where, params = _where(base, now)
    districts = conn.execute(
        f"SELECT l.district AS name, COUNT(*) AS n FROM listings l WHERE {where} AND l.district IS NOT NULL GROUP BY 1 ORDER BY n DESC",
        params).fetchall()
    # Разные написания одного ЖК — одна строка в фильтре (самое частое написание)
    complexes = conn.execute(
        f"""SELECT name, n FROM (
                SELECT cxkey(l.complex) AS k, l.complex AS name, SUM(COUNT(*)) OVER (PARTITION BY cxkey(l.complex)) AS n,
                       ROW_NUMBER() OVER (PARTITION BY cxkey(l.complex) ORDER BY COUNT(*) DESC) AS rn
                FROM listings l WHERE {where} AND l.complex IS NOT NULL GROUP BY k, l.complex)
            WHERE rn = 1 ORDER BY n DESC LIMIT 300""",
        params).fetchall()
    return {"districts": [dict(r) for r in districts], "complexes": [dict(r) for r in complexes]}


def listing_detail(conn: sqlite3.Connection, listing_id: int, with_contacts: bool) -> dict | None:
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if r is None:
        return None
    d = row_to_item(r, with_contacts)
    ev = conn.execute(
        """SELECT e.ts, e.price, e.match, m.chat_name, m.sender_name, m.source
           FROM listing_events e LEFT JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ? ORDER BY e.ts DESC LIMIT 50""", (listing_id,)).fetchall()
    history = []
    for e in ev:
        h = {"ts": e["ts"], "price": e["price"], "match": e["match"]}
        if with_contacts:
            h.update(chat=e["chat_name"], sender=e["sender_name"], source=e["source"])
        history.append(h)
    d["history"] = history
    d["chats"] = conn.execute(
        """SELECT COUNT(DISTINCT m.chat_id) FROM listing_events e JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ?""", (listing_id,)).fetchone()[0]
    return d

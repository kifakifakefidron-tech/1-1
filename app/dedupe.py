"""Склейка дублей: одно и то же объявление от разных агентов, из разных чатов
и с разных номеров Wappi должно стать ОДНИМ объектом.

Что было не так в старой системе:
  * сравнение «похожих» шло только внутри одного подключённого номера;
  * без адреса объявление вообще не проверялось на дубль;
  * разные телефоны агентов давали разные «отпечатки».

Здесь сравниваем со всей базой по характеристикам объекта. Телефон помогает,
но не обязателен.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .parser import ParsedObject
from .textnorm import words

TYPE_GROUPS = {"flat": ("flat", "new"), "new": ("flat", "new"), "room": ("room",),
               "house": ("house",), "land": ("land",), "commercial": ("commercial",)}
LOOKBACK_DAYS = 120


def fragment_hash(fragment: str) -> str:
    w = words(fragment)
    return hashlib.sha1(w.encode()).hexdigest()[:20]


def _close(a: float | None, b: float | None, rel: float, abs_: float) -> bool | None:
    """True/False — совпало/различается; None — нечего сравнивать."""
    if a is None or b is None:
        return None
    return abs(a - b) <= max(abs_, rel * max(a, b))


def _eq(a, b) -> bool | None:
    if a is None or b is None or a == "" or b == "":
        return None
    return words(str(a)) == words(str(b))


def _house_eq(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    na = re.sub(r"[^0-9а-яa-z/]", "", a.lower())
    nb = re.sub(r"[^0-9а-яa-z/]", "", b.lower())
    return na == nb


def same_object(o: ParsedObject, row: sqlite3.Row) -> tuple[bool, str]:
    """Решение «это тот же объект» + причина (для отладки)."""
    rooms = None if o.rooms is None or row["rooms"] is None else o.rooms == row["rooms"]
    floor = None if o.floor is None or row["floor"] is None else o.floor == row["floor"]
    area = _close(o.area, row["area"], 0.03, 1.5)
    land = _close(o.land, row["land"], 0.04, 0.3)
    street = _eq(o.street, row["street"])
    house = _house_eq(o.house, row["house"])
    cx = _eq(o.complex, row["complex"])
    district = _eq(o.district, row["district"])
    settlement = _eq(o.settlement, row["settlement"])
    phones_row = set(json.loads(row["phones"] or "[]"))
    phone = bool(phones_row & set(o.phones)) if o.phones and phones_row else None
    price = _close(o.price, row["price"], 0.15, 0)

    # Явные различия — это разные объекты (две квартиры в одном доме).
    for k, v in (("комнаты", rooms), ("этаж", floor), ("площадь", area), ("участок", land),
                 ("ЖК", cx), ("район", district), ("дом", house if street else None)):
        if v is False:
            return False, f"разные: {k}"

    addr = bool(street and house)
    sizes = [v for v in (area, land) if v is not None]
    size_ok = bool(sizes) and all(sizes)
    details = [v for v in (rooms, floor) if v is not None]
    details_ok = bool(details) and all(details)

    if o.type in ("flat", "new", "room"):
        if addr and (size_ok or details_ok):
            return True, "адрес + параметры"
        if cx and size_ok and details_ok:
            return True, "ЖК + площадь + этаж/комнаты"
        if (district or street) and size_ok and rooms and floor:
            return True, "район/улица + площадь + комнаты + этаж"
        if phone and size_ok and details_ok and price is not False:
            return True, "телефон + площадь + этаж/комнаты"
        return False, "мало совпадений"

    if o.type == "house":
        loc = addr or street or settlement or district or cx
        if loc and area and (land is not False) and (price is not False or phone):
            return True, "место + площадь дома"
        return False, "мало совпадений"

    if o.type == "land":
        loc = addr or (street and house is not False) or settlement or cx
        if loc and land and (price is not False or phone):
            return True, "место + сотки"
        if district and land and phone:
            return True, "район + сотки + телефон"
        return False, "мало совпадений"

    # коммерция
    if (addr or cx or street) and area and (price is not False or phone):
        return True, "адрес + площадь"
    if phone and area and price:
        return True, "телефон + площадь + цена"
    return False, "мало совпадений"


def find_match(conn: sqlite3.Connection, o: ParsedObject, frag_hash: str, now: int) -> tuple[sqlite3.Row | None, str]:
    # 1) Дословный репост того же текста
    row = conn.execute(
        """SELECT l.* FROM listing_events e JOIN listings l ON l.id = e.listing_id
           WHERE e.fragment_hash = ? ORDER BY e.ts DESC LIMIT 1""",
        (frag_hash,),
    ).fetchone()
    if row is not None and row["type"] in TYPE_GROUPS.get(o.type or "", ()) and row["deal"] == o.deal:
        return row, "repost"

    # 2) Тот же объект по характеристикам — по всей базе
    groups = TYPE_GROUPS.get(o.type or "", (o.type,))
    params: list = [*groups, o.deal, now - LOOKBACK_DAYS * 86400]
    cond = ""
    if o.area:
        cond += " AND (area IS NULL OR area BETWEEN ? AND ?)"
        params += [o.area * 0.95 - 1.5, o.area * 1.05 + 1.5]
    if o.land:
        cond += " AND (land IS NULL OR land BETWEEN ? AND ?)"
        params += [o.land * 0.95 - 0.3, o.land * 1.05 + 0.3]
    if o.rooms is not None:
        cond += " AND (rooms IS NULL OR rooms = ?)"
        params.append(o.rooms)
    rows = conn.execute(
        f"""SELECT * FROM listings
            WHERE type IN ({','.join('?' * len(groups))}) AND deal = ? AND last_seen >= ? {cond}
            ORDER BY last_seen DESC LIMIT 400""",
        params,
    ).fetchall()
    for r in rows:
        ok, why = same_object(o, r)
        if ok:
            return r, why
    return None, ""

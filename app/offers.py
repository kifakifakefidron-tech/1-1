"""Группы объявлений одной квартиры для поиска. Исходные объявления не меняем."""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from itertools import combinations
import json
import re
import sqlite3

from . import dedupe, geo, phonegroups, rules
from .textnorm import words


CHAT_PHOTO = re.compile(r"/photos/\d+/([a-f0-9]{16})\.[a-z0-9]+$")
GROUPED_NUMBER = re.compile(r"(?<!\d)\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)")
MATCH_FIELDS = ("id, type, deal, rooms, room_kind, area, land, floor, floors, complex, street, house, "
                "settlement, price, price_doubt, phones, title, last_seen")


def _tokens(raw: str) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except ValueError:
        return []
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _photo_keys(raw: str) -> set[str]:
    return {match[1] for url in _tokens(raw) if (match := CHAT_PHOTO.fullmatch(url))}


def contact_keys(row) -> set[str]:
    return phonegroups.canon_set(_tokens(row["phones"]))


def property_type(value):
    return "flat" if value in ("flat", "new") else value


@lru_cache(maxsize=4096)
def street_key(value):
    return " ".join(re.sub(r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе)\b",
                           " ", words(value)).split())


@lru_cache(maxsize=4096)
def house_key(value):
    return re.sub(r"\s+", "", (value or "").casefold())


def place_keys(row):
    return geo.complex_key(row["complex"]), street_key(row["street"])


def known_price(row):
    return None if row["price_doubt"] else row["price"]


def _different(a, b):
    return bool(a and b and a != b)


def _conflicts(a, b, place_a, place_b):
    return (any(_different(x, y) for x, y in zip(place_a, place_b))
            or _different(house_key(a["house"]), house_key(b["house"]))
            or _different(words(a["settlement"]), words(b["settlement"]))
            or _different(a["room_kind"], b["room_kind"])
            or any(a[k] is not None and b[k] is not None and a[k] != b[k]
                   for k in ("rooms", "floor", "floors"))
            or dedupe._close(a["area"], b["area"], 0.03, 2) is False
            or dedupe._close(a["land"], b["land"], 0.04, 0.3) is False)


def similar_signals(a, b, place_a, place_b):
    """Совпадения для подсказки и ручной проверки. Цена без характеристик не считается."""
    if not any(x and x == y for x, y in zip(place_a, place_b)) or _conflicts(a, b, place_a, place_b):
        return []
    labels = []
    area = dedupe._close(a["area"], b["area"], 0.03, 2) is True
    floor = a["floor"] is not None and a["floor"] == b["floor"]
    if area:
        labels.append("Близкая площадь")
    if floor:
        labels.append("Тот же этаж")
    if a["floors"] is not None and a["floors"] == b["floors"]:
        labels.append("Та же этажность")
    if (area or floor) and dedupe._close(known_price(a), known_price(b), 0.07, 0):
        labels.append("Близкая цена")
    return labels if len(labels) >= 2 else []


def _address_key(row):
    if row["type"] not in ("flat", "new"):
        return None
    house = house_key(row["house"])
    street = street_key(row["street"])
    if not (street and house and row["area"] and row["rooms"] is not None and row["floor"] is not None):
        return None
    return row["deal"], words(row["settlement"]), street, house, row["rooms"], row["floor"], row["area"]


@lru_cache(maxsize=2048)
def _property_text(raw):
    plain = rules.remove_phones(raw or "")
    return GROUPED_NUMBER.sub(lambda m: "".join(m[0].split()), plain)


def _matching_photos(a, b, shared, single_photo):
    if len(shared) >= 2:
        return True
    if not shared & single_photo:
        return False
    ta = _property_text(a["fragment"] or a["description"])
    tb = _property_text(b["fragment"] or b["description"])
    return min(len(words(ta).split()), len(words(tb).split())) >= 20 and dedupe.text_twin(ta, tb)


def is_site(row) -> bool:
    return row["source"] == "feed" and (row["ext_id"] or "").startswith("feed:")


def hidden_ids(conn: sqlite3.Connection, user_id: int | None) -> set[int]:
    if not user_id:
        return set()
    return {row[0] for row in conn.execute("SELECT listing_id FROM hidden_listings WHERE user_id = ?", (user_id,))}


def publication(row) -> tuple[int, int, int]:
    return row["first_seen"], row["catalog_migrated"], row["id"]


def archived_publications(conn: sqlite3.Connection, lid: int | None = None) -> dict[int, tuple[int, int, int]]:
    """Первая публикация подтверждённого дубля учитывается и после снятия предложения."""
    by_id = {}
    endpoint = " AND (a.id = ? OR b.id = ?)" if lid is not None else ""
    for active_id, first_seen, migrated, old_id in conn.execute(f"""SELECT
        CASE WHEN a.is_active THEN a.id ELSE b.id END,
        CASE WHEN a.is_active THEN b.first_seen ELSE a.first_seen END,
        CASE WHEN a.is_active THEN b.catalog_migrated ELSE a.catalog_migrated END,
        CASE WHEN a.is_active THEN b.id ELSE a.id END
        FROM dup_pairs p JOIN listings a ON a.id = p.a JOIN listings b ON b.id = p.b
        WHERE p.status = 'market' AND a.deal = b.deal AND a.is_active != b.is_active{endpoint}""",
        (lid, lid) if lid is not None else ()):
        published = first_seen, migrated, old_id
        by_id[active_id] = min(published, by_id.get(active_id, published))
    return by_id


def first_publication(rows, archived: dict) -> tuple[int, int, int]:
    published = min(map(publication, rows))
    return min((published, *(archived[row["id"]] for row in rows if row["id"] in archived)))


def _build_groups(conn: sqlite3.Connection):
    """Автоматические группы требуют попарного совпадения. Для подтверждённых пар допустима связь через общее предложение."""
    decisions = {(a, b): status for a, b, status in conn.execute("""
        SELECT a, b, status FROM dup_pairs WHERE status IN ('market', 'diff', 'split')
        AND NOT (status = 'market' AND kind = 'offer_photo' AND COALESCE(user_id, 0) = 0)""")}
    rows = {r["id"]: r for r in conn.execute("""SELECT l.id, l.source, l.type, l.deal, l.settlement, l.street, l.house,
        l.complex, l.rooms, l.room_kind, l.floor, l.floors, l.area, l.land, l.photos, l.phones, l.price, l.price_doubt, l.last_seen,
        l.ext_id, l.district, l.repair, l.price_m2, l.first_seen, l.catalog_migrated, l.fragment, l.description,
        CASE WHEN l.source = 'own' THEN (SELECT MAX(e.ts) FROM listing_edits e
            WHERE e.listing_id = l.id AND e.field = 'confirm' AND e.new = '"yes"' AND e.reverted = 0) END AS confirmed_at
        FROM listings l WHERE l.is_active = 1 AND (
            (l.type IN ('flat', 'new') AND l.street IS NOT NULL AND l.house IS NOT NULL
             AND CASE WHEN json_valid(l.photos) THEN json_array_length(l.photos) >= 1 ELSE 0 END)
            OR l.id IN (SELECT a FROM dup_pairs WHERE status = 'market' UNION SELECT b FROM dup_pairs WHERE status = 'market'))""")}
    approved = sorted((a, b) for a, b in decisions if decisions[a, b] == "market" and a in rows and b in rows
                      and rows[a]["deal"] == rows[b]["deal"])
    pairs = set(approved)
    buckets = defaultdict(list)
    photo_addresses = defaultdict(set)
    places = {lid: place_keys(row) for lid, row in rows.items()}
    phonegroups.refresh(conn)
    for lid, row in rows.items():
        key = _address_key(row)
        if key is None:
            continue
        photos = _photo_keys(row["photos"])
        phones = contact_keys(row)
        for photo in photos:
            photo_addresses[photo].add(key)
        if photos and phones:
            buckets[key].append((lid, photos, phones))
    single_photo = {photo for photo, addresses in photo_addresses.items() if len(addresses) == 1}
    for members in buckets.values():
        for (a, photos_a, phones_a), (b, photos_b, phones_b) in combinations(members, 2):
            pair = (min(a, b), max(a, b))
            if pair not in decisions and not phones_a & phones_b \
                    and not _conflicts(rows[a], rows[b], places[a], places[b]) \
                    and _matching_photos(rows[a], rows[b], photos_a & photos_b, single_photo):
                pairs.add(pair)

    def consistent(a, b, confirmed):
        pair = min(a, b), max(a, b)
        return pair in pairs or (confirmed and pair not in decisions
                                and property_type(rows[a]["type"]) == property_type(rows[b]["type"])
                                and not _conflicts(rows[a], rows[b], places[a], places[b]))

    linked = {}
    manual_groups = set()
    for a, b in approved + sorted(pairs.difference(approved)):
        left, right = linked.get(a, (a,)), linked.get(b, (b,))
        confirmed = decisions.get((a, b)) == "market" or left in manual_groups or right in manual_groups
        if left != right and all(consistent(i, j, confirmed) for i in left for j in right):
            group = tuple(sorted(left + right))
            linked.update(dict.fromkeys(group, group))
            if confirmed:
                manual_groups.add(group)
    auto = {(a, b) for a, b in pairs.difference(approved) if a in linked and linked[a] == linked.get(b)}
    return rows, set(linked.values()), auto


def photo_matches(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """Связи, которые можно сохранить после снятия одного из предложений."""
    return _build_groups(conn)[2]


def groups(conn: sqlite3.Connection, hide_user: int | None = None) -> dict:
    rows, groups, _ = _build_groups(conn)
    hidden = hidden_ids(conn, hide_user)
    result = {}
    for ids in groups:
        active = [rows[lid] for lid in ids if lid not in hidden]
        if len(active) < 2:
            continue
        group = tuple(sorted(active, key=lambda r: (not is_site(r), not r["confirmed_at"], -r["last_seen"], -r["id"])))
        result.update(dict.fromkeys((row["id"] for row in group), group))
    return result


def for_listing(conn: sqlite3.Connection, row: sqlite3.Row, hide_user: int | None = None):
    lid = row["id"]
    if conn.execute("SELECT 1 FROM dup_pairs WHERE status = 'market' AND (a = ? OR b = ?)", (lid, lid)).fetchone():
        return groups(conn, hide_user).get(lid)
    photos = _photo_keys(row["photos"])
    if photos and _address_key(row) is not None:
        shared = " OR ".join("instr(photos, ?) > 0" for _ in photos)
        if conn.execute(f"""SELECT 1 FROM listings WHERE is_active = 1 AND id != ?
            AND type IN ('flat', 'new') AND deal = ? AND rooms = ? AND floor = ? AND area = ?
            AND street IS NOT NULL AND house IS NOT NULL AND ({shared}) LIMIT 1""",
            (lid, row["deal"], row["rooms"], row["floor"], row["area"], *photos)).fetchone():
            return groups(conn, hide_user).get(lid)
    return None


def summary(group, matching: set[int] | None = None) -> dict:
    offers = [{"id": r["id"], "price": known_price(r), "last_seen": r["last_seen"],
               "source_label": "СТРЕЛЫ" if is_site(r) else None,
               "site": is_site(r),
               "confirmed_at": r["confirmed_at"], "matches": matching is None or r["id"] in matching} for r in group]
    offers.sort(key=lambda offer: (not offer["site"], not offer["matches"]))
    prices = [offer["price"] for offer in offers if offer["matches"] and offer["price"] is not None]
    return {"offers": offers, "matching": sum(offer["matches"] for offer in offers),
            "min_price": min(prices, default=None), "price_range": bool(prices and min(prices) != max(prices))}

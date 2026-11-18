"""Группы объявлений одной квартиры для поиска. Исходные объявления не меняем."""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import json
import re
import sqlite3

from . import geo, phonegroups
from .textnorm import words


CHAT_PHOTO = re.compile(r"/photos/\d+/([a-f0-9]{16})\.[a-z0-9]+$")


def _tokens(raw: str) -> list[str]:
    try:
        values = json.loads(raw or "[]")
    except ValueError:
        return []
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _photo_keys(raw: str) -> set[str]:
    return {match[1] for url in _tokens(raw) if (match := CHAT_PHOTO.fullmatch(url))}


def _address_key(row):
    if row["type"] not in ("flat", "new"):
        return None
    house = re.sub(r"\s+", "", (row["house"] or "").lower())
    street = words(row["street"])
    if not (street and house and row["area"] and row["rooms"] is not None and row["floor"] is not None):
        return None
    return row["deal"], words(row["settlement"]), street, house, row["rooms"], row["floor"], row["area"]


def _compatible(a, b) -> bool:
    for field in ("floors", "room_kind"):
        if a[field] and b[field] and a[field] != b[field]:
            return False
    return not (a["complex"] and b["complex"] and geo.complex_key(a["complex"]) != geo.complex_key(b["complex"]))


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


def groups(conn: sqlite3.Connection, hide_user: int | None = None) -> dict:
    """Каждый участник группы должен совпадать с каждым. Решение «разные» отменяет автоматическую связь."""
    decisions = {(a, b): status for a, b, status in conn.execute(
        "SELECT a, b, status FROM dup_pairs WHERE status IN ('market', 'diff', 'split')")}
    rows = {r["id"]: r for r in conn.execute("""SELECT l.id, l.source, l.type, l.deal, l.settlement, l.street, l.house,
        l.complex, l.rooms, l.room_kind, l.floor, l.floors, l.area, l.photos, l.phones, l.price, l.price_doubt, l.last_seen,
        l.ext_id, l.district, l.repair, l.price_m2, l.first_seen, l.catalog_migrated,
        CASE WHEN l.source = 'own' THEN (SELECT MAX(e.ts) FROM listing_edits e
            WHERE e.listing_id = l.id AND e.field = 'confirm' AND e.new = '"yes"' AND e.reverted = 0) END AS confirmed_at
        FROM listings l WHERE l.is_active = 1 AND (
            (l.type IN ('flat', 'new') AND l.street IS NOT NULL AND l.house IS NOT NULL
             AND CASE WHEN json_valid(l.photos) THEN json_array_length(l.photos) >= 2 ELSE 0 END)
            OR l.id IN (SELECT a FROM dup_pairs WHERE status = 'market' UNION SELECT b FROM dup_pairs WHERE status = 'market'))""")}
    approved = sorted((a, b) for a, b in decisions if decisions[a, b] == "market" and a in rows and b in rows
                      and rows[a]["deal"] == rows[b]["deal"])
    pairs = set(approved)
    buckets = defaultdict(list)
    phonegroups.refresh(conn)
    for lid, row in rows.items():
        key = _address_key(row)
        if key is None:
            continue
        photos = _photo_keys(row["photos"])
        phones = phonegroups.canon_set(_tokens(row["phones"]))
        if len(photos) >= 2 and phones:
            buckets[key].append((lid, photos, phones))
    for members in buckets.values():
        for (a, photos_a, phones_a), (b, photos_b, phones_b) in combinations(members, 2):
            pair = (min(a, b), max(a, b))
            if pair not in decisions and not phones_a & phones_b and len(photos_a & photos_b) >= 2 \
                    and _compatible(rows[a], rows[b]):
                pairs.add(pair)

    linked = {}
    for a, b in approved + sorted(pairs.difference(approved)):
        left, right = linked.get(a, (a,)), linked.get(b, (b,))
        if left != right and all((min(i, j), max(i, j)) in pairs for i in left for j in right):
            group = tuple(sorted(left + right))
            linked.update(dict.fromkeys(group, group))
    hidden = hidden_ids(conn, hide_user)
    result = {}
    for ids in set(linked.values()):
        active = [rows[lid] for lid in ids if lid not in hidden]
        if len(active) < 2:
            continue
        group = tuple(sorted(active, key=lambda r: (not is_site(r), not r["confirmed_at"], -r["last_seen"], -r["id"])))
        result.update(dict.fromkeys((row["id"] for row in group), group))
    return result


def for_listing(conn: sqlite3.Connection, row: sqlite3.Row, hide_user: int | None = None):
    lid = row["id"]
    can_match = row["type"] in ("flat", "new") and _address_key(row) is not None and len(_photo_keys(row["photos"])) >= 2
    if not can_match and not conn.execute("SELECT 1 FROM dup_pairs WHERE status = 'market' AND (a = ? OR b = ?)", (lid, lid)).fetchone():
        return None
    return groups(conn, hide_user).get(lid)


def summary(group, matching: set[int] | None = None) -> dict:
    offers = [{"id": r["id"], "price": None if r["price_doubt"] else r["price"], "last_seen": r["last_seen"],
               "source_label": "СТРЕЛЫ" if is_site(r) else None,
               "site": is_site(r),
               "confirmed_at": r["confirmed_at"], "matches": matching is None or r["id"] in matching} for r in group]
    offers.sort(key=lambda offer: (not offer["site"], not offer["matches"]))
    prices = [offer["price"] for offer in offers if offer["matches"] and offer["price"] is not None]
    return {"offers": offers, "matching": sum(offer["matches"] for offer in offers),
            "min_price": min(prices, default=None), "price_range": bool(prices and min(prices) != max(prices))}

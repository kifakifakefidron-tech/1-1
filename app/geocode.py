"""Координаты для карты через OpenStreetMap Nominatim (бесплатно, без ключа,
не чаще 1 запроса в секунду). Результаты кэшируются в базе."""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger(__name__)

URL = "https://nominatim.openstreetmap.org/search"
UA = "StrelySearch/1.0 (arrowsrealty.ru)"
CENTER = (45.0355, 38.9753)  # Краснодар
MAX_KM = 70


def _km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def build_query(row: sqlite3.Row) -> str | None:
    if row["street"]:
        place = row["settlement"] or "Краснодар"
        return f"{place}, {row['street']} {row['house'] or ''}".strip()
    if row["complex"]:
        return f"ЖК {row['complex']}, Краснодар"
    if row["settlement"]:
        return f"{row['settlement']}, Краснодарский край"
    return None


def lookup(conn: sqlite3.Connection, query: str) -> tuple[float, float] | None:
    hit = conn.execute("SELECT lat, lon FROM geocache WHERE query = ?", (query,)).fetchone()
    if hit is not None:
        return (hit["lat"], hit["lon"]) if hit["lat"] is not None else None
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "ru", "accept-language": "ru"}
    req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001
        log.warning("Геокодер: %s", e)
        return None
    finally:
        time.sleep(1.1)
    coords = None
    if data:
        lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
        if _km(CENTER, (lat, lon)) <= MAX_KM:
            coords = (lat, lon)
    conn.execute("INSERT OR REPLACE INTO geocache(query, lat, lon, ts) VALUES (?,?,?,?)",
                 (query, coords[0] if coords else None, coords[1] if coords else None, int(time.time())))
    conn.commit()
    return coords


def run(conn: sqlite3.Connection, limit: int = 30) -> int:
    if config.GEOCODER != "nominatim":
        return 0
    rows = conn.execute(
        "SELECT * FROM listings WHERE geo_status = 'pending' AND is_active = 1 ORDER BY last_seen DESC LIMIT ?",
        (limit,)).fetchall()
    done = 0
    for r in rows:
        q = build_query(r)
        coords = lookup(conn, q) if q else None
        conn.execute("UPDATE listings SET lat=?, lon=?, geo_status=? WHERE id=?",
                     (coords[0] if coords else None, coords[1] if coords else None,
                      "ok" if coords else ("none" if q else "skip"), r["id"]))
        conn.commit()
        done += 1
    return done

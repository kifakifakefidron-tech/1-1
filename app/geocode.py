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
MAX_KM = 70                  # для посёлков края
# Границы Краснодара с пригородами (Яблоновский, Новая Адыгея, Знаменский, Пашковский…):
# адрес «в Краснодаре» за этими границами — ошибка геокодера (одноимённая улица в другом городе)
CITY_BOX = (44.93, 38.75, 45.22, 39.30)   # юг, запад, север, восток


def in_city(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and CITY_BOX[0] <= lat <= CITY_BOX[2] and CITY_BOX[1] <= lon <= CITY_BOX[3]


def _city_query(query: str) -> bool:
    return query.rstrip().endswith("Краснодар")


def _km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def candidates(row: sqlite3.Row) -> list[tuple[str, str]]:
    """Запросы к геокодеру от точного к примерному: (запрос, 'ok' | 'approx').
    Если дом не нашёлся — берём улицу, ЖК, посёлок, а в крайнем случае центр района:
    лучше примерная точка на карте, чем объект, которого на карте нет совсем."""
    out: list[tuple[str, str]] = []
    place = row["settlement"] or "Краснодар"
    if row["street"] and row["house"]:
        out.append((f"{place}, {row['street']} {row['house']}", "ok"))
    if row["complex"]:
        out.append((f"ЖК {row['complex']}, Краснодар", "ok"))
    if row["street"]:
        out.append((f"{place}, {row['street']}", "approx"))
    if row["settlement"]:
        out.append((f"{row['settlement']}, Краснодарский край", "approx"))
    if row["district"] and not row["settlement"]:
        d = row["district"]
        out.append((f"микрорайон {d}, Краснодар", "approx"))
        out.append((f"{d}, Краснодар", "approx"))
    return out


def build_query(row: sqlite3.Row) -> str | None:
    c = candidates(row)
    return c[0][0] if c else None


def lookup(conn: sqlite3.Connection, query: str) -> tuple[float, float] | None:
    hit = conn.execute("SELECT lat, lon FROM geocache WHERE query = ?", (query,)).fetchone()
    if hit is not None:
        return (hit["lat"], hit["lon"]) if hit["lat"] is not None else None
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "ru", "accept-language": "ru"}
    if _city_query(query):   # ищем только внутри Краснодара — не в Сочи и не в Ростове
        params.update(viewbox=f"{CITY_BOX[1]},{CITY_BOX[2]},{CITY_BOX[3]},{CITY_BOX[0]}", bounded=1)
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
        if (in_city(lat, lon) if _city_query(query) else _km(CENTER, (lat, lon)) <= MAX_KM):
            coords = (lat, lon)
    conn.execute("INSERT OR REPLACE INTO geocache(query, lat, lon, ts) VALUES (?,?,?,?)",
                 (query, coords[0] if coords else None, coords[1] if coords else None, int(time.time())))
    conn.commit()
    return coords


def run(conn: sqlite3.Connection, limit: int = 30) -> int:
    if config.GEOCODER != "nominatim":
        return 0
    # Один раз после обновления: объекты, для которых раньше не нашли точку, пробуем ещё раз — уже с запасными вариантами
    if not conn.execute("SELECT 1 FROM state WHERE key = 'geo_v2'").fetchone():
        conn.execute("UPDATE listings SET geo_status = 'pending' WHERE geo_status IN ('none', 'skip') AND is_active = 1")
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('geo_v2', '1')")
        conn.commit()
    # Один раз: точки «в Краснодаре», которые геокодер поставил в другой город, и все примерные — ищем заново
    if not conn.execute("SELECT 1 FROM state WHERE key = 'geo_v3'").fetchone():
        fix_wrong_points(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('geo_v3', '1')")
        conn.commit()
    rows = conn.execute(
        "SELECT * FROM listings WHERE geo_status = 'pending' AND is_active = 1 ORDER BY last_seen DESC LIMIT ?",
        (limit,)).fetchall()
    done = 0
    for r in rows:
        coords, status = None, "skip"
        for q, quality in candidates(r):
            coords = lookup(conn, q)
            if coords:
                status = quality
                break
            status = "none"
        conn.execute("UPDATE listings SET lat=?, lon=?, geo_status=? WHERE id=?",
                     (coords[0] if coords else None, coords[1] if coords else None, status, r["id"]))
        conn.commit()
        done += 1
    return done


def fix_wrong_points(conn: sqlite3.Connection) -> int:
    """Убрать точки «в Краснодаре» за пределами города (и из кэша), примерные — пересчитать."""
    bad = 0
    for q in conn.execute("SELECT query, lat, lon FROM geocache WHERE lat IS NOT NULL").fetchall():
        if (_city_query(q["query"]) and not in_city(q["lat"], q["lon"])) or q["query"].startswith("микрорайон "):
            conn.execute("DELETE FROM geocache WHERE query = ?", (q["query"],))
    for r in conn.execute("""SELECT id, lat, lon, settlement, geo_status FROM listings
                             WHERE lat IS NOT NULL AND source != 'feed' AND geo_status != 'manual'""").fetchall():
        wrong = (not r["settlement"] and not in_city(r["lat"], r["lon"])) or \
                (r["settlement"] and _km(CENTER, (r["lat"], r["lon"])) > MAX_KM)
        if wrong or r["geo_status"] == "approx":
            conn.execute("UPDATE listings SET lat = NULL, lon = NULL, geo_status = 'pending' WHERE id = ?", (r["id"],))
            bad += wrong
    # Фид партнёра: координаты из фида; явно чужие (дальше 70 км) — не показываем
    for r in conn.execute("SELECT id, lat, lon FROM listings WHERE lat IS NOT NULL AND source = 'feed' "
                          "AND geo_status != 'manual'").fetchall():
        if _km(CENTER, (r["lat"], r["lon"])) > MAX_KM:
            conn.execute("UPDATE listings SET lat = NULL, lon = NULL, geo_status = 'pending' WHERE id = ?", (r["id"],))
            bad += 1
    conn.commit()
    return bad

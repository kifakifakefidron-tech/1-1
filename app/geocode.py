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

from . import config, db, geo
from .textnorm import words

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
    db.flush(conn)   # не держать базу, пока ждём ответа геокодера
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


# ─── обучение на правках админа ────────────────────────────────────────────
_STREET_WORDS = r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе|пл|площадь)\b"


def _street_key(v: str | None) -> str:
    import re
    return " ".join(re.sub(_STREET_WORDS, " ", words(v or "")).split())


def learn_keys(row) -> list[tuple[str, str]]:
    """Ключи адреса объекта: точный дом и ЖК. (ключ, подпись для админки)"""
    keys = []
    street, house = _street_key(row["street"]), words(row["house"] or "").replace(" ", "")
    if street and house:
        from .learning import place_key
        keys.append((f"addr:{place_key(row['settlement'])}|{street}|{house}", f"{row['street']}, {row['house']}"))
    if row["complex"]:
        k = geo.complex_key(row["complex"])
        if k:
            keys.append((f"cx:{k}", f"ЖК {row['complex']}"))
    return keys


def learned_point(conn: sqlite3.Connection, row, kinds: tuple = ("addr", "cx")) -> tuple[float, float] | None:
    """Выученная точка: сначала точный дом, потом ЖК (kinds — какие из них смотреть)."""
    for key, _ in learn_keys(row):
        if key.split(":", 1)[0] not in kinds:
            continue
        hit = conn.execute("SELECT lat, lon FROM geo_learned WHERE key = ?", (key,)).fetchone()
        if hit:
            return hit["lat"], hit["lon"]
    return None


def learn(conn: sqlite3.Connection, row, lat: float, lon: float) -> int:
    """Админ поставил точку вручную → запоминаем её для этого дома (точно) и ЖК (среднее по правкам),
    и сразу переносим на неё остальные объекты с тем же адресом. Возвращает, сколько объектов поправили."""
    now = int(time.time())
    has_house = any(k.startswith("addr:") for k, _ in learn_keys(row))
    for key, label in learn_keys(row):
        if key.startswith("cx:") and has_house:
            continue   # точка дома — не центр ЖК (иначе «центр ЖК» = первый поправленный дом)
        if key.startswith("addr:"):
            conn.execute("""INSERT INTO geo_learned (key, lat, lon, n, ts, label) VALUES (?,?,?,1,?,?)
                            ON CONFLICT(key) DO UPDATE SET lat = excluded.lat, lon = excluded.lon, n = n + 1,
                            ts = excluded.ts""", (key, lat, lon, now, label))
        else:   # ЖК — среднее по всем правкам его домов
            conn.execute("""INSERT INTO geo_learned (key, lat, lon, n, ts, label) VALUES (?,?,?,1,?,?)
                            ON CONFLICT(key) DO UPDATE SET lat = (lat * n + excluded.lat) / (n + 1),
                            lon = (lon * n + excluded.lon) / (n + 1), n = n + 1, ts = excluded.ts""",
                         (key, lat, lon, now, label))
    # Остальные объекты с тем же адресом (кроме поправленных вручную) — на выученную точку
    fixed = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND id != ? AND geo_status NOT IN ('manual', 'feed')
                             AND (complex IS NOT NULL OR (street IS NOT NULL AND house IS NOT NULL))""",
                          (row["id"],)).fetchall():
        pt = learned_point(conn, r)
        if pt and (r["lat"], r["lon"]) != pt:
            conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = 'learned', geo_src = 'learned' WHERE id = ?",
                         (*pt, r["id"]))
            fixed += 1
    # Ответ геокодера для этого адреса был неверным — заменяем его в кэше, чтобы он не вернулся
    for q, quality in candidates(row):
        if quality == "ok":
            conn.execute("INSERT OR REPLACE INTO geocache(query, lat, lon, ts) VALUES (?,?,?,?)", (q, lat, lon, now))
            break
    conn.commit()
    return fixed


def _district_from_map(conn: sqlite3.Connection, r, coords) -> None:
    """Района в объявлении нет, а точная точка есть — район по карте районов."""
    from . import districtmap
    if not districtmap.auto_enabled():
        return
    hit = districtmap.district_at(conn, *coords)
    if hit and hit[1] == "district":
        from .ingest import _reindex, make_search_text
        st = make_search_text({**dict(r), "district": hit[0]})
        conn.execute("UPDATE listings SET district = ?, district_src = 'map', search_text = ? WHERE id = ? AND district IS NULL",
                     (hit[0], st, r["id"]))
        _reindex(conn, r["id"], st)


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
    from .autolearn import Known
    known = Known(conn) if rows else None
    for r in rows:
        coords, status, src = None, "skip", None
        house = bool(r["street"] and r["house"])
        hit = known.point(r, cx=not house)   # ваши правки, тот же дом в фиде, ЖК на карте — без геокодера
        if not hit and house:
            # ТЗ «админка-гео» P2.2: дом указан — сначала ищем ДОМ по адресу, центр ЖК — только если дом не нашёлся
            q, quality = next(iter(candidates(r)), (None, None))
            coords = lookup(conn, q) if q and quality == "ok" else None
            if coords and known.plausible(r, *coords):
                conn.execute("UPDATE listings SET lat=?, lon=?, geo_status='ok', geo_src='geocoder' WHERE id=?", (*coords, r["id"]))
                if not r["district"]:
                    _district_from_map(conn, r, coords)
                conn.commit()
                done += 1
                continue
            coords = None
            hit = known.point(r)
        if hit:
            conn.execute("UPDATE listings SET lat=?, lon=?, geo_status=?, geo_src=? WHERE id=?", (*hit, r["id"]))
            if not r["district"]:
                _district_from_map(conn, r, hit[:2])
            conn.commit()
            done += 1
            continue
        center = known.district_center(r["district"]) if r["district"] and not r["settlement"] else None
        for q, quality in candidates(r):
            if center and q.endswith(f"{r['district']}, Краснодар"):   # район без дома — центр нашего контура
                coords, status, src = center, "approx", "map-district"
                break
            coords = lookup(conn, q)
            if coords and not known.plausible(r, *coords):   # ответ спорит с ЖК/районом объявления — не верим
                log.info("Геокодер: %s → %s не сходится с ЖК/районом, пропускаю", q, coords)
                coords = None
            if coords:
                place = r["settlement"] or "Краснодар"
                src = ("geocoder-cx" if q.startswith("ЖК ") else "geocoder" if quality == "ok"
                       else "geocoder-street" if r["street"] and q == f"{place}, {r['street']}"
                       else "geocoder-place" if r["settlement"] and q.startswith(f"{r['settlement']},") else "geocoder-district")
                status = quality if src == "geocoder" else "approx"
                break
            status = "none"
        conn.execute("UPDATE listings SET lat=?, lon=?, geo_status=?, geo_src=? WHERE id=?",
                     (coords[0] if coords else None, coords[1] if coords else None, status, src, r["id"]))
        if coords and status == "ok" and not r["district"]:
            _district_from_map(conn, r, coords)
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
                             WHERE lat IS NOT NULL AND source != 'feed' AND geo_status NOT IN ('manual', 'learned')""").fetchall():
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

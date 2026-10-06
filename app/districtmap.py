"""Сверка районов по карте районов (контуры районов → район по точке объекта).

Источник контуров — файл, который админ загружает сам (карта, полученная с разрешения владельца,
или наша собственная). Сайт neagent.info отдаёт полную карту только своей странице — эту защиту
мы НЕ обходим (07.10). Контуры хранятся в district_polygons и на сайте не публикуются.

  * сверка (admin «Сверка с картой»): ЖК и улицы, у которых наш район ≠ району по карте, и объекты
    без района, у которых точка на карте есть → админ подтверждает «Принять» или «Оставить как есть»;
  * дальше автоматически: объект получил точную точку на карте, а района в объявлении нет →
    район по карте (geocode.run).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import xml.etree.ElementTree as ET

from . import config, geo

SOURCE_PAGE = ""   # откуда карта (для подписи в админке)
_SETTLEMENT = re.compile(r"^(?:ст\.|х\.|хутор|аул|шапсугское)", re.IGNORECASE)
_cache: dict = {"t": 0.0, "polys": []}


def _clean(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip()


def our_name(name: str) -> tuple[str, str]:
    """Название с карты → (наше название района, вид: district | settlement)."""
    base = _clean(name)
    inner = re.findall(r"\((.*?)\)", name)
    for cand in (base, re.sub(r"^(?:п\.|пос\.)\s*", "", base), *inner):
        d = geo.canonical_district(cand)
        if d:
            return d, "district"
    return base, ("settlement" if _SETTLEMENT.match(base) else "district")


def _parse(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    out = []
    for obj in root.iter():
        if not obj.tag.endswith("GeoObject"):
            continue
        name = next((e.text for e in obj if e.tag.endswith("name") and e.text), None)
        rings = [e.text for e in obj.iter() if e.tag.endswith("posList") and e.text]
        if not name or not rings:
            continue
        polys = []
        for ring in rings:
            nums = [float(x) for x in ring.split()]
            pts = [(nums[i + 1], nums[i]) for i in range(0, len(nums) - 1, 2)]   # в файле «долгота широта»
            if len(pts) >= 3:
                polys.append(pts)
        if polys:
            out.append({"source_name": name, "polys": polys})
    return out


def _area(pts) -> float:
    return abs(sum(a[1] * b[0] - b[1] * a[0] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def import_map(conn: sqlite3.Connection, xml_bytes: bytes | None = None) -> dict:
    """Скачать карту районов и сохранить контуры. Недостающие районы добавляются как свои."""
    from . import learning
    learning.sync_districts(conn)
    if xml_bytes is None:
        raise ValueError("Нужен файл карты районов")
    items = _parse(xml_bytes)
    if not items:
        raise ValueError("В карте районов не нашлось ни одного района")
    added = []
    conn.execute("DELETE FROM district_polygons WHERE source = 'neagent'")
    for it in items:
        name, kind = our_name(it["source_name"])
        if kind == "district" and not geo.canonical_district(name):
            learning.add_district(conn, name)          # «9-я Тихая», «Катюша», «Рубероидный»…
            added.append(name)
        lats = [p[0] for poly in it["polys"] for p in poly]
        lons = [p[1] for poly in it["polys"] for p in poly]
        conn.execute("""INSERT INTO district_polygons (name, source_name, kind, source, polys, area,
                        lat_min, lat_max, lon_min, lon_max, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                     (name, it["source_name"], kind, "neagent", json.dumps(it["polys"]),
                      sum(_area(p) for p in it["polys"]), min(lats), max(lats), min(lons), max(lons), int(time.time())))
    conn.commit()
    _cache["t"] = 0
    return {"districts": len(items), "added": added}


def _polys(conn: sqlite3.Connection) -> list:
    if time.time() - _cache["t"] > 300:
        _cache.update(t=time.time(), polys=[
            (r["name"], r["kind"], json.loads(r["polys"]), r["area"], (r["lat_min"], r["lat_max"], r["lon_min"], r["lon_max"]))
            for r in conn.execute("SELECT * FROM district_polygons ORDER BY area")])
    return _cache["polys"]


def _inside(lat: float, lon: float, pts) -> bool:
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if (xi > lon) != (xj > lon) and lat < (yj - yi) * (lon - xi) / ((xj - xi) or 1e-12) + yi:
            inside = not inside
        j = i
    return inside


def district_at(conn: sqlite3.Connection, lat: float | None, lon: float | None) -> tuple[str, str] | None:
    """Район по точке: (название, вид). Если контуры пересекаются — самый маленький (он точнее)."""
    if lat is None or lon is None:
        return None
    for name, kind, polys, _area_, (a, b, c, d) in _polys(conn):   # уже отсортированы по площади
        if a <= lat <= b and c <= lon <= d and any(_inside(lat, lon, p) for p in polys):
            return name, kind
    return None


def loaded(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM district_polygons").fetchone()[0]


# ─── сверка ────────────────────────────────────────────────────────────────
_EXACT = "geo_status IN ('ok', 'manual', 'learned')"   # только точные точки, не «примерно по району»


def reconcile(conn: sqlite3.Connection) -> dict:
    """Расхождения нашей базы с картой районов: по ЖК, по улицам и объекты без района."""
    ignored = {r[0] for r in conn.execute("SELECT key FROM reconcile_ignore")}
    rows = conn.execute(f"""SELECT id, title, district, complex, street, house, settlement, lat, lon, admin_fixed
                            FROM listings WHERE is_active = 1 AND lat IS NOT NULL AND {_EXACT}""").fetchall()
    by_cx: dict[str, dict] = {}
    by_st: dict[str, dict] = {}
    empty = []
    for r in rows:
        hit = district_at(conn, r["lat"], r["lon"])
        if not hit or hit[1] != "district":
            continue
        theirs = hit[0]
        if not r["district"]:
            empty.append({"id": r["id"], "title": r["title"], "map": theirs,
                          "addr": " · ".join(x for x in (r["complex"] and "ЖК " + r["complex"],
                                                          r["street"] and f"ул. {r['street']}{', ' + r['house'] if r['house'] else ''}") if x)})
        if r["complex"]:
            g = by_cx.setdefault(geo.complex_key(r["complex"]), {"name": r["complex"], "ours": {}, "map": {}, "ids": []})
        elif r["street"]:
            g = by_st.setdefault(r["street"].lower(), {"name": r["street"], "ours": {}, "map": {}, "ids": []})
        else:
            continue
        g["ours"][r["district"] or "—"] = g["ours"].get(r["district"] or "—", 0) + 1
        g["map"][theirs] = g["map"].get(theirs, 0) + 1
        g["ids"].append(r["id"])

    def diffs(groups, kind):
        out = []
        for g in groups.values():
            top_map = max(g["map"], key=g["map"].get)
            top_ours = max(g["ours"], key=g["ours"].get)
            if top_map == top_ours and len(g["ours"]) == 1:
                continue
            key = f"{kind}:{g['name'].lower()}:{top_map}"
            if key in ignored:
                continue
            out.append({"kind": kind, "name": g["name"], "key": key, "map": top_map,
                        "map_all": sorted(g["map"].items(), key=lambda x: -x[1]),
                        "ours": sorted(g["ours"].items(), key=lambda x: -x[1]), "count": len(g["ids"])})
        return sorted(out, key=lambda x: -x["count"])
    return {"complexes": diffs(by_cx, "complex"), "streets": diffs(by_st, "street"),
            "empty": empty[:500], "empty_total": len(empty), "checked": len(rows), "loaded": loaded(conn),
            "source": SOURCE_PAGE}


def accept(conn: sqlite3.Connection, kind: str, name: str, district: str | None = None) -> dict:
    """Принять район по карте. ЖК — правило «ЖК → район» на будущее и все его объекты.
    Улица — каждому её объекту район по его точке (длинные улицы проходят через несколько районов)."""
    from . import learning
    from .ingest import _reindex, make_search_text
    if kind == "complex":
        return learning.move(conn, "complex", name, None, district)
    n = 0
    for r in conn.execute(f"""SELECT * FROM listings WHERE is_active = 1 AND lat IS NOT NULL AND {_EXACT}
                              AND lower(street) = ? AND complex IS NULL""", (name.lower(),)).fetchall():
        hit = district_at(conn, r["lat"], r["lon"])
        if hit and hit[1] == "district" and hit[0] != r["district"]:
            d = {**dict(r), "district": hit[0]}
            d["search_text"] = make_search_text(d)
            conn.execute("UPDATE listings SET district = ?, search_text = ? WHERE id = ?", (hit[0], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            n += 1
    conn.commit()
    return {"listings": n}


def fill_empty(conn: sqlite3.Connection) -> int:
    """Всем объектам без района, у которых есть точная точка, — район по карте."""
    from .ingest import _reindex, make_search_text
    n = 0
    for r in conn.execute(f"SELECT * FROM listings WHERE is_active = 1 AND district IS NULL AND lat IS NOT NULL AND {_EXACT}").fetchall():
        hit = district_at(conn, r["lat"], r["lon"])
        if hit and hit[1] == "district":
            d = {**dict(r), "district": hit[0]}
            d["search_text"] = make_search_text(d)
            conn.execute("UPDATE listings SET district = ?, search_text = ? WHERE id = ?", (hit[0], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            n += 1
    conn.commit()
    return n


def ignore(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("INSERT OR IGNORE INTO reconcile_ignore (key, ts) VALUES (?, ?)", (key, int(time.time())))
    conn.commit()


def auto_enabled() -> bool:
    return config.DISTRICT_FROM_MAP

"""Самообучение: точки на карте и районы из того, что сервис уже знает, — без интернета и без админа.

Источники точки (от надёжного к примерному):
  ваши правки (geo_learned) → тот же дом в фиде СТРЕЛ → ЖК: центр контура ЖК на карте районов
  или середина точек фида этого ЖК → (дальше геокодер, его ответ проверяется: geocode.run + plausible)
  → центр контура района (примерно).
Источники района для объектов без района:
  контур района по точной точке, «голосование» объявлений того же дома и того же ЖК
  (объекты фида и поправленные админом весят втрое). Если источники спорят — не ставим: решает админ.
Откуда взято — listings.geo_src / district_src (видно на /fix, очередь «Поставил сервис»).
"""
from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections import Counter, defaultdict

from . import districtmap, geocode, learning, osmaddr

log = logging.getLogger(__name__)

EXACT = ("ok", "manual", "learned", "feed")
CX_SPREAD_KM = 1.5      # точки фида одного ЖК дальше этого от середины — ЖК «размазан», ему не верим
CX_FAR_KM = 2.0         # ответ геокодера дальше этого от точки ЖК — ошибка геокодера
OUT_OF_DISTRICT_KM = 1.0  # точка дальше этого за контуром района из объявления — ошибка
GENERIC_CX = {"жилой квартал", "жилой комплекс", "жилой дом", "квартал", "парк", "центр"}   # слово, а не название: точку не ставим
GEO_SRC = {"feed-house": "тот же дом в фиде СТРЕЛ", "feed-cx": "ЖК по объектам фида СТРЕЛ",
           "map-cx": "центр ЖК на карте районов", "map-district": "центр района на карте (примерно)",
           "osm-house": "дом в справочнике адресов OSM", "osm-near": "соседний дом той же улицы (OSM)",
           "osm-street": "середина улицы (OSM, примерно)",
           "learned": "ваши правки точек", "geocoder": "поиск по адресу (проверен)"}
DISTRICT_SRC = {"map": "по карте районов (точка внутри контура)", "vote-house": "так в других объявлениях этого дома",
                "vote-cx": "так в других объявлениях этого ЖК"}


def _centroid(ring) -> tuple[float, float]:
    """Центр контура (по площади); если контур вогнутый и центр вне его — ближайшая к центру вершина."""
    a = cx = cy = 0.0
    for (y1, x1), (y2, x2) in zip(ring, ring[1:] + ring[:1]):
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < 1e-12:
        c = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    else:
        c = (cy / (3 * a), cx / (3 * a))
    if not districtmap._inside(c[0], c[1], ring):
        c = min(ring, key=lambda p: (p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2)
    return round(c[0], 6), round(c[1], 6)


def _main_ring(polys):
    return max(polys, key=districtmap._area)


def _seg_km(p, a, b) -> float:
    """Расстояние от точки до отрезка, км (плоское приближение — для Краснодара хватает)."""
    k = math.cos(math.radians(p[0]))
    px, py, ax, ay, bx, by = p[1] * k, p[0], a[1] * k, a[0], b[1] * k, b[0]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy) * 111.2


class Known:
    """Всё, что сервис знает о местах, — собирается один раз за проход."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.house: dict[str, tuple[float, float]] = {}
        self.cx: dict[str, tuple[tuple[float, float], str]] = {}
        self.district_poly: dict[str, list] = {}
        cx_pts: dict[str, list] = defaultdict(list)
        for r in conn.execute("""SELECT street, house, settlement, complex, lat, lon FROM listings
                                 WHERE source = 'feed' AND geo_status = 'feed' AND lat IS NOT NULL"""):
            p = (r["lat"], r["lon"])
            k = learning.addr_key(r["street"], r["house"], r["settlement"])
            if k:
                self.house.setdefault(k, p)
            k = learning.cx_key(r["complex"])
            if k:
                cx_pts[k].append(p)
        for name, kind, polys, _a, _bb in districtmap._polys(conn):
            if kind == "complex":
                k = learning.cx_key(name)
                if k and k not in self.cx:
                    self.cx[k] = (_centroid(_main_ring(polys)), "map-cx")
            elif kind == "district" and name not in self.district_poly:
                self.district_poly[name] = polys
        for k, pts in cx_pts.items():
            mid = (sorted(p[0] for p in pts)[len(pts) // 2], sorted(p[1] for p in pts)[len(pts) // 2])
            spread = sorted(geocode._km(mid, p) for p in pts)[len(pts) // 2]
            mid = (round(mid[0], 6), round(mid[1], 6))
            if k not in self.cx:
                if spread <= CX_SPREAD_KM:
                    self.cx[k] = (mid, "feed-cx")
            elif geocode._km(self.cx[k][0], mid) > CX_FAR_KM:
                # Контур ЖК на карте и объекты фида спорят (одноимённый ЖК в другом месте, «Жилой квартал»…):
                # фиду верим, если в нём хотя бы 2 объекта рядом друг с другом, иначе этому ЖК — никакой точки
                if len(pts) >= 2 and spread <= CX_SPREAD_KM:
                    self.cx[k] = (mid, "feed-cx")
                else:
                    del self.cx[k]
        for k in [k for k in self.cx if k.removeprefix("cx:") in GENERIC_CX]:
            del self.cx[k]

    def cx_point(self, row):
        k = learning.cx_key(row["complex"])
        return self.cx.get(k) if k else None

    def point(self, row) -> tuple[float, float, str, str] | None:
        """(lat, lon, geo_status, geo_src) из уже известного — без геокодера."""
        pt = geocode.learned_point(self.conn, row)
        if pt:
            return pt[0], pt[1], "learned", "learned"
        k = learning.addr_key(row["street"], row["house"], row["settlement"])
        if k and k in self.house:
            return *self.house[k], "ok", "feed-house"
        osm = osmaddr.point(self.conn, row["street"], row["house"], row["settlement"]) if row["street"] else None
        if osm and osm[2] == "ok" and self.plausible(row, osm[0], osm[1]):   # дом из справочника OSM
            return osm
        hit = self.cx_point(row)
        if hit:
            return *hit[0], "ok", hit[1]
        if osm and self.plausible(row, osm[0], osm[1]):   # дома нет — середина улицы (примерно)
            return osm
        return None

    def district_center(self, name: str | None):
        polys = self.district_poly.get(name or "")
        return _centroid(_main_ring(polys)) if polys else None

    def km_outside(self, name: str, lat: float, lon: float) -> float | None:
        """На сколько км точка за контуром района (0 — внутри; None — контура нет)."""
        polys = self.district_poly.get(name)
        if not polys:
            return None
        if any(districtmap._inside(lat, lon, ring) for ring in polys):
            return 0.0
        return min(_seg_km((lat, lon), a, b) for ring in polys for a, b in zip(ring, ring[1:] + ring[:1]))

    def plausible(self, row, lat: float, lon: float) -> bool:
        """Ответ геокодера не спорит с тем, что известно: рядом с ЖК и внутри (почти) района из объявления."""
        hit = self.cx_point(row)
        if hit and geocode._km(hit[0], (lat, lon)) > CX_FAR_KM:
            return False
        if row["district"] and not row["settlement"]:
            out = self.km_outside(row["district"], lat, lon)
            if out is not None and out > OUT_OF_DISTRICT_KM:
                return False
        return True


# ─── точки ────────────────────────────────────────────────────────────────
def fill_points(conn: sqlite3.Connection, known: Known | None = None) -> int:
    """Объекты без точки / с примерной точкой → точка из известного (фид, ЖК, ваши правки)."""
    known = known or Known(conn)
    n = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1
                             AND geo_status IN ('pending', 'none', 'skip', 'approx')""").fetchall():
        hit = known.point(r)
        if hit and (r["lat"], r["lon"]) != hit[:2]:
            conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ?, geo_src = ? WHERE id = ?",
                         (*hit, r["id"]))
            n += 1
    conn.commit()
    return n


def recheck_geocoder(conn: sqlite3.Connection, known: Known | None = None) -> int:
    """Точки геокодера, которые спорят с ЖК или районом из объявления, — ищем заново (с проверкой)."""
    known = known or Known(conn)
    n = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND lat IS NOT NULL
                             AND geo_status IN ('ok', 'approx') AND geo_src IS NULL""").fetchall():
        if not known.plausible(r, r["lat"], r["lon"]):
            conn.execute("UPDATE listings SET lat = NULL, lon = NULL, geo_status = 'pending' WHERE id = ?", (r["id"],))
            n += 1
    conn.commit()
    return n


def fix_streets(conn: sqlite3.Connection) -> int:
    """Опечатки в улицах уже собранных объектов («Командоская» → «Командорская»); точку ищем заново."""
    from .ingest import _reindex, make_search_text
    n = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND street IS NOT NULL AND admin_fixed = 0
                             AND source != 'own'""").fetchall():
        new = osmaddr.fix_street(conn, r["street"])
        if not new or new == r["street"]:
            continue
        d = {**dict(r), "street": new}
        d["search_text"] = make_search_text(d)
        conn.execute("""UPDATE listings SET street = ?, search_text = ?,
                        geo_status = CASE WHEN geo_status IN ('manual', 'learned', 'feed') THEN geo_status ELSE 'pending' END
                        WHERE id = ?""", (new, d["search_text"], r["id"]))
        _reindex(conn, r["id"], d["search_text"])
        n += 1
    conn.commit()
    return n


# ─── районы ───────────────────────────────────────────────────────────────
def _votes(conn: sqlite3.Connection):
    by_house: dict[str, Counter] = defaultdict(Counter)
    by_cx: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute("""SELECT street, house, settlement, complex, district, source, admin_fixed FROM listings
                             WHERE is_active = 1 AND district IS NOT NULL AND settlement IS NULL"""):
        w = 3 if r["source"] == "feed" or r["admin_fixed"] else 1
        k = learning.addr_key(r["street"], r["house"], r["settlement"])
        if k:
            by_house[k][r["district"]] += w
        k = learning.cx_key(r["complex"])
        if k:
            by_cx[k][r["district"]] += w
    return by_house, by_cx


def _winner(c: Counter | None) -> tuple[str, int] | None:
    """Район, за который ≥3/4 голосов и хотя бы 2 голоса (объект фида/поправленный админом — 3 голоса)."""
    if not c:
        return None
    name, w = c.most_common(1)[0]
    total = sum(c.values())
    return (name, w) if w >= 2 and w / total >= 0.75 else None


def candidates(conn: sqlite3.Connection, row, votes=None) -> list[tuple[str, str]]:
    """Районы-кандидаты для объекта: [(район, источник)] — по карте и по соседним объявлениям."""
    by_house, by_cx = votes or _votes(conn)
    out = []
    if row["lat"] is not None and row["geo_status"] in EXACT:
        hit = districtmap.district_at(conn, row["lat"], row["lon"])
        if hit and hit[1] == "district":
            out.append((hit[0], "map"))
    k = learning.addr_key(row["street"], row["house"], row["settlement"])
    w = _winner(by_house.get(k)) if k else None
    if w:
        out.append((w[0], "vote-house"))
    k = learning.cx_key(row["complex"])
    w = _winner(by_cx.get(k)) if k else None
    if w:
        out.append((w[0], "vote-cx"))
    return out


def fill_districts(conn: sqlite3.Connection) -> dict:
    """Объекты без района: ставим район, если источники согласны. Спорные — остаются админу."""
    from .ingest import _reindex, make_search_text, make_title
    votes = _votes(conn)
    done = disputed = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND district IS NULL AND settlement IS NULL
                             AND admin_fixed = 0 AND source != 'own'""").fetchall():
        cands = candidates(conn, r, votes)
        if not cands:
            continue
        if len({d for d, _ in cands}) > 1:
            disputed += 1
            continue
        d = {**dict(r), "district": cands[0][0]}
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        conn.execute("UPDATE listings SET district = ?, district_src = ?, title = ?, search_text = ? WHERE id = ?",
                     (d["district"], cands[0][1], d["title"], d["search_text"], r["id"]))
        _reindex(conn, r["id"], d["search_text"])
        done += 1
    conn.commit()
    return {"districts": done, "disputed": disputed}


def hints(conn: sqlite3.Connection, row) -> list[dict]:
    """Подсказки для экрана разбора: районы по карте и по соседним объявлениям."""
    return [{"name": d, "why": {"map": "по карте районов", "vote-house": "так в объявлениях этого дома",
                                "vote-cx": "так в объявлениях этого ЖК"}[src]} for d, src in candidates(conn, row)]


# ─── один проход (из worker) ─────────────────────────────────────────────
def run(conn: sqlite3.Connection) -> dict:
    t = time.time()
    known = Known(conn)
    out = {}
    if conn.execute("SELECT value FROM state WHERE key = 'autolearn_v1'").fetchone() is None:
        out["recheck"] = recheck_geocoder(conn, known)   # один раз: старые ответы геокодера — на проверку
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('autolearn_v1', '1')")
        conn.commit()
    ver = conn.execute("SELECT value FROM state WHERE key = 'osm_addr_ts'").fetchone()
    done = conn.execute("SELECT value FROM state WHERE key = 'osm_street_fix'").fetchone()
    if ver and (done is None or done[0] != ver[0]):   # справочник адресов обновился — исправить опечатки в улицах
        out["streets"] = fix_streets(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('osm_street_fix', ?)", (ver[0],))
        conn.commit()
    out["points"] = fill_points(conn, known)
    out.update(fill_districts(conn))
    out["s"] = round(time.time() - t, 2)
    return out


def summary(conn: sqlite3.Connection) -> dict:
    """Для админки: что сервис поставил сам."""
    g = dict(conn.execute("""SELECT geo_src, COUNT(*) FROM listings WHERE is_active = 1 AND geo_src IS NOT NULL
                             GROUP BY geo_src""").fetchall())
    d = dict(conn.execute("""SELECT district_src, COUNT(*) FROM listings WHERE is_active = 1 AND district_src IS NOT NULL
                             GROUP BY district_src""").fetchall())
    return {"points": [{"src": k, "label": GEO_SRC.get(k, k), "n": v} for k, v in g.items()],
            "districts": [{"src": k, "label": DISTRICT_SRC.get(k, k), "n": v} for k, v in d.items()]}

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
import re
import sqlite3
import time
from collections import Counter, defaultdict

from . import districtmap, geo, geocode, learning, osmaddr
from .textnorm import words

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


_CX_TAIL = re.compile(r"(?:\s+(?:очередь|оч|литер|лит|корпус|корп|секция|дом|этап|фаза|жк))*(?:\s+\d+[а-я]?)+$"
                      r"|\s+(?:очередь|оч|литер|лит|этап)$")


def cx_base_key(name: str | None) -> str | None:
    """Основное название ЖК — для точки и района: «Самолёт 2», «Самолет-3 очередь», «Инсити (Российская улица, 267/3к1)»
    → «самолет», «инсити». Сам ЖК в карточке не меняем — только узнаём, что это тот же комплекс."""
    if not name:
        return None
    w = words(re.sub(r"\(.*?\)", " ", name))
    w = re.sub(r"^(?:жк|ж к|жилой комплекс|жилой квартал)\s+", "", w)
    for _ in range(3):   # «самолет 3 очередь» → «самолет 3» → «самолет»
        w = _CX_TAIL.sub("", w).strip()
    if len(w) < 3 or w in GENERIC_CX:
        return None
    rec = geo.resolve_complex(w)
    if rec:   # в справочнике бывает «Урал (Ural)» — скобки и номера убираем и у него
        w2 = words(re.sub(r"\(.*?\)", " ", rec.name))
        for _ in range(3):
            w2 = _CX_TAIL.sub("", w2).strip()
        w = w2 or w
    return "cxb:" + w


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
        # «Самолёт 2» / «Самолёт 3» → точка ЖК «Самолёт»: контур или середина объектов фида всех очередей
        self.cx_base: dict[str, tuple[tuple[float, float], str]] = {}
        base_pts: dict[str, list] = defaultdict(list)
        for name, kind, polys, _a, _bb in districtmap._polys(conn):
            if kind == "complex":
                b = cx_base_key(name)
                if b and b not in self.cx_base:
                    self.cx_base[b] = (_centroid(_main_ring(polys)), "map-cx")
        for r in conn.execute("""SELECT complex, lat, lon FROM listings WHERE source = 'feed' AND geo_status = 'feed'
                                 AND lat IS NOT NULL AND complex IS NOT NULL"""):
            b = cx_base_key(r["complex"])
            if b:
                base_pts[b].append((r["lat"], r["lon"]))
        for b, pts in base_pts.items():
            mid = (sorted(p[0] for p in pts)[len(pts) // 2], sorted(p[1] for p in pts)[len(pts) // 2])
            spread = sorted(geocode._km(mid, p) for p in pts)[len(pts) // 2]
            if b in self.cx_base:
                if geocode._km(self.cx_base[b][0], mid) > CX_FAR_KM:   # контур и фид спорят — не верим никому
                    del self.cx_base[b]
            elif spread <= CX_SPREAD_KM:
                self.cx_base[b] = ((round(mid[0], 6), round(mid[1], 6)), "feed-cx")

    def cx_point(self, row):
        k = learning.cx_key(row["complex"])
        if k and k in self.cx:
            return self.cx[k]
        b = cx_base_key(row["complex"])
        return self.cx_base.get(b) if b else None

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


STREET_FIX_V = 2   # 2 — улица по одной фамилии («Жигуленко» → «Евгении Жигуленко»)


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
        b = cx_base_key(r["complex"])
        if b and b != k:
            by_cx[b][r["district"]] += w
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
    if not w:   # «Самолёт 2» без своих голосов — как у ЖК «Самолёт» в целом
        b = cx_base_key(row["complex"])
        w = _winner(by_cx.get(b)) if b else None
    if w:
        out.append((w[0], "vote-cx"))
    return out


def fill_districts(conn: sqlite3.Connection) -> dict:
    """Объекты без района: ставим район, если источники согласны. Спорные — остаются админу."""
    from .ingest import _reindex, make_search_text, make_title
    votes = _votes(conn)
    done = disputed = 0
    conn.execute("UPDATE listings SET place_conflict = NULL WHERE place_conflict LIKE 'votes:%'")
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND district IS NULL AND settlement IS NULL
                             AND admin_fixed = 0 AND source != 'own'""").fetchall():
        cands = candidates(conn, r, votes)
        if not cands:
            continue
        if len({d for d, _ in cands}) > 1:
            disputed += 1
            why = {"map": "по вашей карте", "vote-house": "по объявлениям этого дома", "vote-cx": "по объявлениям этого ЖК"}
            conn.execute("UPDATE listings SET place_conflict = ? WHERE id = ?",
                         ("votes:" + "; ".join(f"{why[s]} — «{d}»" for d, s in cands), r["id"]))
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


# ─── перепроверка всех объектов (справочник адресов + нарисованные районы) ─
SERVICE_UID = 0          # «пользователь» сервиса в журнале listing_edits (откат — rollback_recheck)
NEAR_BORDER_KM = 0.15    # дом у самой границы района из объявления — район агента не трогаем
HOUSE_SRC = ("osm-house", "osm-near", "feed-house", "learned")
RECHECK_EVERY_S = 3600
FEED_FAR_KM = 1.0        # метка фида дальше этого от своего дома по справочнику — в «Спорные»


def _has_rule(rs: dict, row) -> bool:
    """Район объекта задан вашим правилом (дом / ЖК / улица) — его перепроверка не трогает."""
    return any(k and (kind, k) in rs for kind, k in (
        ("addr", learning.addr_key(row["street"], row["house"], row["settlement"])),
        ("cx_district", learning.cx_key(row["complex"])), ("street", learning.street_key(row["street"], row["settlement"]))))


def _log(conn, lid: int, field: str, old, new, now: int) -> None:
    import json
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                 (lid, SERVICE_UID, now, field, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False)))


def recheck_all(conn: sqlite3.Connection) -> dict:
    """Все объекты заново:
      1) точка — дом из справочника адресов / фида / ваших правок лучше прежней точки геокодера или примерной;
      2) район — по вашим нарисованным районам: точка дома в другом районе (дальше 150 м от границы района из
         объявления) → район по карте; у района из объявления нет контура → нарисованный добавляется вторым;
      3) квартира без ЖК, а дом внутри контура известного ЖК → этот ЖК.
    Не трогаем: поправленное вами (admin_fixed), районы по вашим правилам, объекты фида СТРЕЛ, станицы/посёлки.
    Каждая замена района/ЖК — в журнале listing_edits (user_id = 0), откат — rollback_recheck."""
    import json

    from . import geo
    from .ingest import _reindex, make_search_text, make_title
    known = Known(conn)
    rep: Counter = Counter()
    now = int(time.time())
    # 1) точки
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND geo_status NOT IN ('manual', 'learned', 'feed')
                             AND COALESCE(geo_src, '') NOT IN ('osm-house', 'osm-near', 'feed-house', 'learned')""").fetchall():
        hit = known.point(r)
        if not hit or hit[3] not in HOUSE_SRC + ("map-cx", "feed-cx"):
            continue
        if hit[3] in ("map-cx", "feed-cx") and r["geo_status"] == "ok":
            continue   # точка ЖК не лучше уже найденной точки дома
        if r["lat"] is None or r["geo_status"] != "ok" or geocode._km((r["lat"], r["lon"]), hit[:2]) > 0.05:
            conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ?, geo_src = ? WHERE id = ?", (*hit, r["id"]))
            rep["points"] += 1
    conn.commit()
    # 2) районы и 3) ЖК — по точке
    rs = learning.rules(conn, fresh=True)
    conn.execute("UPDATE listings SET place_conflict = NULL WHERE place_conflict LIKE 'rule:%' OR place_conflict LIKE 'feed:%'")
    # Точка из фида СТРЕЛ далеко от своего же адреса (в справочнике адресов есть этот дом) — ошибка в координатах на сайте
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND source = 'feed' AND geo_status = 'feed'
                             AND street IS NOT NULL AND house IS NOT NULL""").fetchall():
        hit = osmaddr.point(conn, r["street"], r["house"], r["settlement"])
        if hit and hit[3] == "osm-house":
            km = geocode._km((r["lat"], r["lon"]), hit[:2])
            if km > FEED_FAR_KM:
                conn.execute("UPDATE listings SET place_conflict = ? WHERE id = ?",
                             (f"feed:метка из фида СТРЕЛ стоит в {km:.1f} км от дома «{r['street']}, {r['house']}» по справочнику "
                              "адресов — поставьте точку здесь (запомнится) или поправьте координаты на сайте СТРЕЛ", r["id"]))
                rep["feed_far"] += 1
    # что вы откатили — больше не трогаем (иначе каждый час возвращалось бы снова)
    undone = {(r[0], r[1]) for r in conn.execute(
        "SELECT listing_id, field FROM listing_edits WHERE user_id = ? AND reverted = 1", (SERVICE_UID,))}
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND lat IS NOT NULL AND admin_fixed = 0
                             AND source NOT IN ('feed', 'own') AND settlement IS NULL
                             AND geo_status IN ('ok', 'manual', 'learned')""").fetchall():
        d = dict(r)
        changed = False
        hit = districtmap.district_at(conn, r["lat"], r["lon"])
        new = hit[0] if hit and hit[1] == "district" else None
        extra = json.loads(r["extra_districts"] or "[]")
        cur = r["district"]
        if new and new != cur and new not in extra and (r["id"], "district") not in undone                 and (r["id"], "extra_districts") not in undone:
            if cur is None:
                d["district"], d["district_src"] = new, "map"
                _log(conn, r["id"], "district", cur, new, now)
                rep["districts_filled"] += 1
                changed = True
            elif _has_rule(rs, r):
                rep["kept_rule"] += 1
                out = known.km_outside(cur, r["lat"], r["lon"])
                if out is None or out > NEAR_BORDER_KM:   # ваше правило и ваша карта спорят — решите, что верно
                    conn.execute("UPDATE listings SET place_conflict = ? WHERE id = ?",
                                 (f"rule:по вашему правилу — «{cur}», а дом на вашей карте — в «{new}»", r["id"]))
            else:
                out = known.km_outside(cur, r["lat"], r["lon"])
                if out is None:   # у района из объявления нет контура (подрайон?) — нарисованный добавляем вторым
                    d["extra_districts"] = json.dumps(extra + [new], ensure_ascii=False)
                    _log(conn, r["id"], "extra_districts", extra, extra + [new], now)
                    rep["districts_extra"] += 1
                    changed = True
                elif out <= NEAR_BORDER_KM:
                    rep["kept_border"] += 1
                else:
                    d["district"], d["district_src"] = new, "map"
                    _log(conn, r["id"], "district", cur, new, now)
                    rep["districts_changed"] += 1
                    changed = True
        if not r["complex"] and r["type"] in ("flat", "new") and r["geo_src"] in HOUSE_SRC + (None, "geocoder")                 and (r["id"], "complex") not in undone:
            cx = districtmap.complex_at(conn, r["lat"], r["lon"])
            rec = geo.resolve_complex(cx) if cx else None
            if rec and learning.cx_key(rec.name).removeprefix("cx:") not in GENERIC_CX:
                d["complex"] = rec.name
                _log(conn, r["id"], "complex", None, rec.name, now)
                rep["complexes"] += 1
                changed = True
        if changed:
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            conn.execute("""UPDATE listings SET district = ?, district_src = ?, extra_districts = ?, complex = ?, title = ?,
                            search_text = ? WHERE id = ?""", (d["district"], d["district_src"], d["extra_districts"],
                                                             d["complex"], d["title"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
    conn.commit()
    out = dict(rep)
    out["ts"] = now
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('recheck_report', ?)", (json.dumps(out),))
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('recheck_ts', ?)", (str(now),))
    conn.commit()
    return out


def rollback_recheck(conn: sqlite3.Connection, since: int = 0) -> int:
    """Вернуть всё, что поменяла перепроверка (район, второй район, ЖК). Возвращает число объектов."""
    import json

    from .ingest import _reindex, make_search_text, make_title
    rows = conn.execute("""SELECT * FROM listing_edits WHERE user_id = ? AND reverted = 0 AND ts >= ?
                           AND field IN ('district', 'extra_districts', 'complex') ORDER BY id DESC""",
                        (SERVICE_UID, since)).fetchall()
    touched = set()
    for e in rows:
        r = conn.execute("SELECT * FROM listings WHERE id = ?", (e["listing_id"],)).fetchone()
        if r is None or r["admin_fixed"]:
            continue   # вы уже поправили объект сами — ваше важнее
        old = json.loads(e["old"])
        if e["field"] == "extra_districts":
            old = json.dumps(old or [], ensure_ascii=False)
        conn.execute(f"UPDATE listings SET {e['field']} = ?, district_src = NULL WHERE id = ?", (old, r["id"]))
        conn.execute("UPDATE listing_edits SET reverted = 1 WHERE id = ?", (e["id"],))
        touched.add(r["id"])
    for lid in touched:
        d = dict(conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone())
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        conn.execute("UPDATE listings SET title = ?, search_text = ? WHERE id = ?", (d["title"], d["search_text"], lid))
        _reindex(conn, lid, d["search_text"])
    conn.commit()
    return len(touched)


def maybe_recheck(conn: sqlite3.Connection) -> dict | None:
    """Раз в час — и сразу, если поменялись нарисованные районы или справочник адресов."""
    key = "|".join(str(x) for x in conn.execute(
        """SELECT COUNT(*), MAX(ts) FROM district_polygons""").fetchone()) + "|" + (
        (conn.execute("SELECT value FROM state WHERE key = 'osm_addr_ts'").fetchone() or [""])[0])
    last_key = (conn.execute("SELECT value FROM state WHERE key = 'recheck_key'").fetchone() or [""])[0]
    last_ts = int((conn.execute("SELECT value FROM state WHERE key = 'recheck_ts'").fetchone() or [0])[0] or 0)
    if key == last_key and time.time() - last_ts < RECHECK_EVERY_S:
        return None
    out = recheck_all(conn)
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('recheck_key', ?)", (key,))
    conn.commit()
    return out


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
    want = f"{ver[0]}:{STREET_FIX_V}" if ver else None
    if ver and (done is None or done[0] != want):   # справочник обновился или правила улиц стали умнее — пройти заново
        out["streets"] = fix_streets(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('osm_street_fix', ?)", (want,))
        conn.commit()
    out["points"] = fill_points(conn, known)
    out.update(fill_districts(conn))
    rc = maybe_recheck(conn)   # все объекты — по справочнику адресов и вашим районам (раз в час и после правок карты)
    if rc:
        out["recheck_all"] = {k: v for k, v in rc.items() if k != "ts"}
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

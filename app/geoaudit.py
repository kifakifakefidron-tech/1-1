"""Проверка гео-данных (ТЗ «админка-гео», P0.0): отчёт → решение админа в /learn → применение с журналом.

Отчёт ничего не меняет:
  * правила «улица → район» и «ЖК → район» против карты районов: как распределены объекты по контурам под ТОЧНОЙ
    точкой дома; правило, у которого в своём районе < 70 % таких объектов, — кандидат на отключение;
  * ЖК-мусор: общие слова и города, принятые за ЖК («Краснодар», «Москва», «Жилой квартал», «Победа», «Южный»…);
  * «улицы»-подписи полей («Площадь», «Этаж»…) — их ставила нейросеть;
  * испорченные правила: район в правиле — склеенная строка («Губернский/Губернский/Любимово») или повтор в доп. районах;
  * выученные точки домов без населённого пункта (ключ addr:|улица|дом = Краснодар): есть ли такой дом в Краснодаре
    по справочнику адресов и не стоит ли точка далеко от него;
  * выученные точки ЖК, которые на самом деле точка одного дома, — пересчёт: центр контура ЖК или середина известных домов.
Применение — только выбранное админом; каждое изменение объекта — в журнал listing_edits (src = «чистка правил»),
откат — по объекту (окно объекта → «История») или из админки.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter

from . import geo, geocode, learning, osmaddr, parser

SHARE_OK = 0.7       # правило верно, если в его районе по карте ≥ 70 % объектов с точкой дома
MIN_POINTS = 3       # меньше точек — судить не по чему («мало данных»)
# Не ЖК: города, страны, общие слова (стоп-лист — ингест тоже не принимает их за ЖК)
JUNK_CX = {"краснодар", "москва", "россия", "кубань", "жилой квартал", "жилой комплекс", "новостройка", "новостройки",
           # акт 14.10, С2: районы и общие слова, принятые за ЖК
           "панорама", "молодежный", "музыкальный", "большой", "знаменский", "прогресс", "победа", "сити", "метальникова",
           "победа", "южный", "центр", "центральный", "город", "дом", "квартал", "мкр", "микрорайон", "сочи", "анапа",
           "краснодарский край", "юг", "север", "восток", "запад"}
SRC = "чистка правил"


_JUNK_KEYS = {x.replace(" ", "") for x in JUNK_CX}


def is_junk_complex(name: str | None) -> bool:
    """Не ЖК: стоп-лист или название района (если такого ЖК нет в справочнике ЖК)."""
    k = geo.complex_key(name)
    if not k:
        return False
    if k in _JUNK_KEYS:
        return True
    w = geo.words(name or "")
    w = re.sub(r"^(?:жк|ж к)\s+", "", w)
    # «ФМР», «Микрорайон Красная площадь» — это район, а не ЖК («Аврора», «Немецкая деревня» бывают и ЖК — не трогаем)
    return bool(geo.canonical_district(w)) and geo.resolve_complex(w) is None and (
        len(w) <= 4 or bool(re.match(r"(?:микрорайон|мкр|мкрн|район|р н)\s", w)))


def _rule_rows(conn: sqlite3.Connection, kind: str):
    return conn.execute("SELECT id, kind, key, value, label, n, ts FROM learned_rules WHERE kind = ?", (kind,)).fetchall()


def rules_report(conn: sqlite3.Connection) -> list[dict]:
    """Каждое правило «улица → район» и «ЖК → район»: объекты, распределение по контурам, совпадает ли с большинством."""
    rows = conn.execute("""SELECT id, street, settlement, complex, district, lat, lon, geo_status, geo_src FROM listings
                           WHERE is_active = 1 AND (street IS NOT NULL OR complex IS NOT NULL)""").fetchall()
    by_st: dict[str, list] = {}
    by_cx: dict[str, list] = {}
    for r in rows:
        k = learning.street_key(r["street"], r["settlement"])
        if k:
            by_st.setdefault(k, []).append(r)
        k = learning.cx_key(r["complex"])
        if k:
            by_cx.setdefault(k, []).append(r)
    out = []
    for kind, groups in (("street", by_st), ("cx_district", by_cx)):
        for rule in _rule_rows(conn, kind):
            target = json.loads(rule["value"]).get("district")
            objs = groups.get(rule["key"], [])
            dist = Counter(d for d in (learning._house_district(conn, r) for r in objs) if d)
            n = sum(dist.values())
            share = dist.get(target, 0) / n if n else None
            status = "few" if n < MIN_POINTS else ("bad" if share < SHARE_OK else "ok")
            out.append({"id": rule["id"], "kind": kind, "label": rule["label"], "target": target, "objects": len(objs),
                        "points": n, "share": round(share, 2) if share is not None else None, "status": status,
                        "dist": dict(dist.most_common(5)), "applied": rule["n"]})
    order = {"bad": 0, "few": 1, "ok": 2}
    out.sort(key=lambda x: (order[x["status"]], -(x["points"] or 0)))
    return out


def junk_report(conn: sqlite3.Connection) -> list[dict]:
    names = Counter()
    for (cx,) in conn.execute("SELECT complex FROM listings WHERE is_active = 1 AND complex IS NOT NULL"):
        if is_junk_complex(cx):
            names[cx] += 1
    return [{"name": n, "listings": k} for n, k in names.most_common()]


def label_streets(conn: sqlite3.Connection) -> list[dict]:
    return [{"id": r["id"], "street": r["street"], "title": r["title"]} for r in conn.execute(
        "SELECT id, street, title FROM listings WHERE is_active = 1 AND street IS NOT NULL").fetchall()
        if parser.is_field_label(r["street"])]


def broken_rules(conn: sqlite3.Connection) -> list[dict]:
    """Правила, у которых район — не один известный район (склейка через «/», повтор в доп. районах, подпись не совпадает)."""
    out = []
    for r in conn.execute("SELECT id, kind, value, label FROM learned_rules WHERE kind IN ('addr', 'street', 'cx_district')"):
        v = json.loads(r["value"])
        if not v.get("district") and not v.get("extra"):
            continue
        d, extra = learning.clean_districts(v.get("district"), v.get("extra"))
        fixed = {**v, "district": d, "extra": extra}
        label = learning.rule_label(r["kind"], r["label"], fixed)
        if (d, extra) != (v.get("district"), v.get("extra") or []) or label != r["label"]:
            out.append({"id": r["id"], "label": r["label"], "new_label": label, "district": v.get("district"),
                        "new_district": d, "extra": extra})
    return out


NEAR_M = 300   # выученная точка дома ближе этого к дому из справочника адресов — верна
CITY_KEYS = "(key LIKE 'addr:|%' OR key LIKE 'addr:краснодар|%')"   # точки домов в Краснодаре (старые — без посёлка)


def _addr_parts(key: str) -> tuple[str, str, str]:
    place, street, house = key.removeprefix("addr:").split("|", 2)
    return place, street, house


def points_report(conn: sqlite3.Connection) -> list[dict]:
    """Выученные точки домов без населённого пункта (= Краснодар): проверка по справочнику адресов OSM."""
    if not osmaddr.loaded(conn):
        return []
    out = []
    for r in conn.execute(f"SELECT key, lat, lon, label, n FROM geo_learned WHERE {CITY_KEYS}").fetchall():
        _place, street, house = _addr_parts(r["key"])
        hit = osmaddr.point(conn, street, house, None)
        if hit and hit[3] == "osm-house":
            m = geocode._km((r["lat"], r["lon"]), hit[:2]) * 1000
            status = "ok" if m <= NEAR_M else "far"
        elif hit:
            m, status = None, "nohouse"     # улица в Краснодаре есть, такого дома в справочнике нет — не судим
        else:
            m, status = None, "nostreet"    # в Краснодаре такой улицы нет — это, скорее всего, посёлок
        n = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1 AND geo_src = 'learned' AND lat = ? AND lon = ?",
                         (r["lat"], r["lon"])).fetchone()[0]
        out.append({"key": r["key"], "label": r["label"], "status": status, "m": round(m) if m is not None else None,
                    "listings": n})
    order = {"far": 0, "nostreet": 1, "nohouse": 2, "ok": 3}
    out.sort(key=lambda x: order[x["status"]])
    return out


HOUSE_SRC = ("osm-house", "feed-house", "learned", "manual")


def _cx_center(conn, known, cx_key: str, names: list[str]) -> tuple[tuple[float, float] | None, str]:
    """Центр ЖК: контур ЖК на карте, иначе середина разных известных домов этого ЖК (≥ 2 дома), иначе нет."""
    hit = known.cx.get(cx_key)
    if hit and hit[1] == "map-cx":
        return hit[0], "контур ЖК"
    houses: dict[str, tuple[float, float]] = {}
    if names:
        q = ",".join("?" * len(names))
        for r in conn.execute(f"""SELECT street, house, settlement, lat, lon FROM listings WHERE is_active = 1
                                  AND lat IS NOT NULL AND street IS NOT NULL AND house IS NOT NULL AND complex IN ({q})
                                  AND (geo_status IN ('feed', 'manual') OR geo_src IN ('osm-house', 'feed-house'))""",
                              names).fetchall():
            k = learning.addr_key(r["street"], r["house"], r["settlement"])
            if k and k not in houses:
                houses[k] = (r["lat"], r["lon"])
    if len(houses) >= 2:
        pts = list(houses.values())
        return ((round(sum(p[0] for p in pts) / len(pts), 6), round(sum(p[1] for p in pts) / len(pts), 6)),
                f"середина {len(pts)} домов")
    return None, "нет контура ЖК и известных домов — точку ЖК забыть"


def cx_points_report(conn: sqlite3.Connection) -> list[dict]:
    """Выученные точки ЖК, совпадающие с точкой одного дома (первый поправленный дом стал «центром ЖК»)."""
    from .autolearn import Known
    known = Known(conn)
    houses = [(r["lat"], r["lon"]) for r in conn.execute("SELECT lat, lon FROM geo_learned WHERE key LIKE 'addr:%'")]
    names_by_key: dict[str, list[str]] = {}
    for (cx,) in conn.execute("SELECT DISTINCT complex FROM listings WHERE complex IS NOT NULL"):
        k = learning.cx_key(cx)
        if k:
            names_by_key.setdefault(k, []).append(cx)
    out = []
    for r in conn.execute("SELECT key, lat, lon, label, n FROM geo_learned WHERE key LIKE 'cx:%'").fetchall():
        one_house = any(geocode._km((r["lat"], r["lon"]), h) < 0.03 for h in houses)
        if not one_house and r["n"] > 1:
            continue
        new, how = _cx_center(conn, known, r["key"], names_by_key.get(r["key"], []))
        if new and geocode._km(new, (r["lat"], r["lon"])) < 0.03:
            continue   # и так в центре
        out.append({"key": r["key"], "label": r["label"], "n": r["n"], "one_house": one_house, "old": [r["lat"], r["lon"]],
                    "new": list(new) if new else None, "how": how,
                    "listings": conn.execute("""SELECT COUNT(*) FROM listings WHERE is_active = 1 AND geo_src = 'learned'
                                                AND lat = ? AND lon = ?""", (r["lat"], r["lon"])).fetchone()[0]})
    return out


def risky_aliases(conn: sqlite3.Connection) -> list[dict]:
    """Псевдонимы ЖК с риском: «Гарантия» → «Гарантия на Обрывной», когда есть и «Гарантия на Дежнева»;
    или исходное название — это район («Солнечный»)."""
    keys = {geo.complex_key(c) for (c,) in conn.execute("SELECT DISTINCT complex FROM listings WHERE complex IS NOT NULL")}
    keys |= {geo.complex_key(c.name) for _k, c in geo._complexes()}
    keys.discard(None)
    out = []
    for r in conn.execute("SELECT id, key, label FROM learned_rules WHERE kind = 'cx_alias'").fetchall():
        src = r["key"].removeprefix("cx:").replace(" ", "")
        longer = sorted(k for k in keys if k != src and k.startswith(src))
        if len(longer) >= 2:
            out.append({"id": r["id"], "label": r["label"], "why": f"есть {len(longer)} разных ЖК с таким началом"})
        elif geo.canonical_district(src):
            out.append({"id": r["id"], "label": r["label"], "why": "это ещё и название района"})
    return out


def auto_cleanup(conn: sqlite3.Connection) -> dict | None:
    """Один раз (state geo_cleanup_v1, акт приёмки 14.10, С2–С3): всё, что нашла проверка, применяется само —
    правила против карты, ЖК-мусор, «улицы»-подписи, испорченные правила, сомнительные точки домов, точки ЖК из одного
    дома, районы-дубли, псевдонимы ЖК с риском; ключи точек — с населённым пунктом. Каждое изменение объекта — в журнале
    правок (откат по объекту), итог — в админке «Обзор»."""
    from . import db
    if db.get_state(conn, "geo_cleanup_v1"):
        return None
    rep = report(conn)
    res = apply(conn, [r["id"] for r in rep["rules"] if r["status"] == "bad"], [j["name"] for j in rep["junk"]],
                bool(rep["labels"]), broken=[b["id"] for b in rep["broken"]], points=[p["key"] for p in rep["points"]],
                cx_points=[c["key"] for c in rep["cx_points"]], dupes=[d["name"] for d in rep["dupes"]])
    risky = risky_aliases(conn)
    for a in risky:
        learning.forget(conn, a["id"])
    res["aliases"] = len(risky)
    res["city_keys"] = learning.migrate_city_keys(conn)
    res["bad_rules"] = [r["label"] for r in rep["rules"] if r["status"] == "bad"][:60]
    res["junk_names"] = [j["name"] for j in rep["junk"]]
    res["ts"] = int(time.time())
    db.set_state(conn, "geo_cleanup_report", json.dumps(res, ensure_ascii=False))
    db.set_state(conn, "geo_cleanup_v1", "1")
    conn.commit()
    return res


DAILY_MIN_POINTS = 5     # ежедневная уборка: правило с 5+ домами на карте…
DAILY_MAX_SHARE = 0.5    # …из которых в «своём» районе меньше половины, — явно ошибочное, отключаем само


def daily(conn: sqlite3.Connection) -> dict | None:
    """Раз в сутки (state geo_daily_day): явно ошибочные правила «улица/ЖК → район» (5+ домов на вашей карте, в своём
    районе < 50 %), ЖК-мусор, одно написание ЖК. Каждое изменение объекта — в журнале правок, итог — в «Обзоре»."""
    from . import cxnorm, db
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    if db.get_state(conn, "geo_daily_day") == day:
        return None
    rr = rules_report(conn)
    bad = [r for r in rr if r["points"] >= DAILY_MIN_POINTS and (r["share"] or 0) < DAILY_MAX_SHARE]
    res = apply(conn, [r["id"] for r in bad], [j["name"] for j in junk_report(conn)], False)
    res["cx_names"] = cxnorm.run(conn, apply=True).get("listings", 0)
    res["bad_rules"] = [r["label"] for r in bad][:40]
    res["ts"] = int(time.time())
    db.set_state(conn, "geo_daily_report", json.dumps(res, ensure_ascii=False))
    db.set_state(conn, "geo_daily_day", day)
    conn.commit()
    return res


def district_dupes(conn: sqlite3.Connection) -> list[dict]:
    """Район записан не тем именем, под которым он в справочнике («Молодёжный» при «Молодежный/Витаминкомбинат»)."""
    learning.sync_districts(conn)
    out = []
    for d, n in conn.execute("""SELECT district, COUNT(*) FROM listings WHERE district IS NOT NULL
                                GROUP BY district ORDER BY 2 DESC""").fetchall():
        canon = geo.canonical_district(d)
        if canon and canon != d:
            out.append({"name": d, "to": canon, "listings": n})
    return out


def empty_contours(conn: sqlite3.Connection) -> list[str]:
    """Нарисованные районы, в которых нет ни одного объекта (по району или по точке)."""
    from . import districtmap
    used = {d for (d,) in conn.execute("SELECT DISTINCT district FROM listings WHERE is_active = 1 AND district IS NOT NULL")}
    for (ex,) in conn.execute("SELECT extra_districts FROM listings WHERE is_active = 1 AND extra_districts LIKE '[\"%'"):
        used.update(json.loads(ex))
    return sorted({n for n, kind, *_ in districtmap._polys(conn) if kind == "district"} - used)


def report(conn: sqlite3.Connection) -> dict:
    rr = rules_report(conn)
    pts = points_report(conn)
    return {"rules": rr, "bad": sum(1 for r in rr if r["status"] == "bad"), "junk": junk_report(conn),
            "labels": label_streets(conn), "broken": broken_rules(conn),
            "points": [p for p in pts if p["status"] in ("far", "nostreet")],
            "points_total": conn.execute(f"SELECT COUNT(*) FROM geo_learned WHERE {CITY_KEYS}").fetchone()[0],
            "points_ok": sum(1 for p in pts if p["status"] == "ok"),
            "points_unknown": sum(1 for p in pts if p["status"] == "nohouse"),
            "cx_points": cx_points_report(conn), "osm_loaded": bool(osmaddr.loaded(conn)),
            "dupes": district_dupes(conn), "empty_contours": empty_contours(conn)}


def _log(conn, lid: int, field: str, old, new, now: int) -> None:
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?,?,?,?,?,?,?)",
                 (lid, 0, now, field, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), SRC))


def _redecide(conn, r) -> str | None:
    """Район без отключённого правила: контур под точкой дома, иначе район из текста объявления, иначе прежний."""
    d_map = learning._house_district(conn, r)
    if d_map:
        return d_map
    return geo.find_district_in_text(r["fragment"] or r["description"] or "") or r["district"]


def apply(conn: sqlite3.Connection, rule_ids: list[int], junk: list[str], labels: bool, broken: list[int] | None = None,
          points: list[str] | None = None, cx_points: list[str] | None = None, dupes: list[str] | None = None) -> dict:
    from .ingest import _reindex, make_search_text, make_title
    now = int(time.time())
    rep = Counter()
    # испорченные правила: район и подпись — из значения; объекты с испорченным районом — на чистый
    fixes = {b["id"]: b for b in broken_rules(conn)}
    for rid in broken or []:
        b = fixes.get(int(rid))
        if not b:
            continue
        row = conn.execute("SELECT kind, value FROM learned_rules WHERE id = ?", (b["id"],)).fetchone()
        v = {**json.loads(row["value"]), "district": b["new_district"], "extra": b["extra"]}
        conn.execute("UPDATE learned_rules SET value = ?, label = ? WHERE id = ?",
                     (json.dumps(v, ensure_ascii=False), b["new_label"], b["id"]))
        rep["broken"] += 1
        if b["district"] and b["district"] != b["new_district"]:
            for r in conn.execute("SELECT * FROM listings WHERE district = ?", (b["district"],)).fetchall():
                d = {**dict(r), "district": b["new_district"]}
                ex = json.loads(r["extra_districts"] or "[]")
                ex2 = [x for x in dict.fromkeys(ex + b["extra"]) if x != b["new_district"]]
                d["title"] = make_title(d)
                d["search_text"] = make_search_text(d)
                conn.execute("UPDATE listings SET district = ?, extra_districts = ?, title = ?, search_text = ? WHERE id = ?",
                             (b["new_district"], json.dumps(ex2, ensure_ascii=False), d["title"], d["search_text"], r["id"]))
                _reindex(conn, r["id"], d["search_text"])
                _log(conn, r["id"], "district", r["district"], b["new_district"], now)
                if ex2 != ex:
                    _log(conn, r["id"], "extra_districts", ex, ex2, now)
                rep["listings"] += 1
    # районы-дубли: объекты, правила, контуры — на имя из справочника (каждый объект — в журнал)
    dd = {x["name"]: x["to"] for x in district_dupes(conn)} if dupes else {}
    for name in dupes or []:
        to = dd.get(name)
        if not to:
            continue
        # rename_district тут не подходит: справочник уже считает старое имя тем же районом
        for r in conn.execute("SELECT * FROM listings WHERE district = ?", (name,)).fetchall():
            d = {**dict(r), "district": to}
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            conn.execute("UPDATE listings SET district = ?, title = ?, search_text = ? WHERE id = ?",
                         (to, d["title"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            _log(conn, r["id"], "district", name, to, now)
            rep["listings"] += 1
        for r in conn.execute("SELECT id, extra_districts FROM listings WHERE extra_districts LIKE ?", (f'%"{name}"%',)).fetchall():
            ex = json.loads(r["extra_districts"])
            conn.execute("UPDATE listings SET extra_districts = ? WHERE id = ?",
                         (json.dumps(list(dict.fromkeys(to if x == name else x for x in ex)), ensure_ascii=False), r["id"]))
        conn.execute("UPDATE district_polygons SET name = ? WHERE name = ? AND kind = 'district'", (to, name))
        for r in conn.execute("SELECT id, kind, value, label FROM learned_rules WHERE value LIKE ?", (f"%{name}%",)).fetchall():
            v = json.loads(r["value"])
            if v.get("district") == name or name in (v.get("extra") or []):
                v["district"], v["extra"] = learning.clean_districts(to if v.get("district") == name else v.get("district"),
                                                                     [to if x == name else x for x in v.get("extra") or []])
                conn.execute("UPDATE learned_rules SET value = ?, label = ? WHERE id = ?",
                             (json.dumps(v, ensure_ascii=False), learning.rule_label(r["kind"], r["label"], v), r["id"]))
        rep["dupes"] += 1
    # выученные точки домов, которых в Краснодаре нет или они далеко от дома, — забыть, объекты — в «Спорные»
    pt_rows = {p["key"]: p for p in points_report(conn)} if points else {}
    for key in points or []:
        p = pt_rows.get(key)
        row = conn.execute("SELECT lat, lon FROM geo_learned WHERE key = ?", (key,)).fetchone()
        if not p or not row or p["status"] == "ok":
            continue
        conn.execute("DELETE FROM geo_learned WHERE key = ?", (key,))
        why = ("такой улицы в Краснодаре по справочнику адресов нет" if p["status"] == "nostreet"
               else f"дом по справочнику адресов в {p['m']} м от неё" if p["status"] == "far" else "дома нет в справочнике")
        n = conn.execute("""UPDATE listings SET place_conflict = ? WHERE is_active = 1 AND admin_fixed = 0
                            AND geo_src = 'learned' AND lat = ? AND lon = ?""",
                         (f"point:выученная точка «{p['label']}» без населённого пункта — {why}; проверьте точку и посёлок",
                          row["lat"], row["lon"])).rowcount
        rep["points"] += 1
        rep["to_queue"] += n
    # точки ЖК, которые на самом деле точка одного дома, — центр контура ЖК / середина домов (нет — забываем)
    cx_rows = {c["key"]: c for c in cx_points_report(conn)} if cx_points else {}
    for key in cx_points or []:
        c = cx_rows.get(key)
        if not c:
            continue
        if c["new"]:
            conn.execute("UPDATE geo_learned SET lat = ?, lon = ?, ts = ? WHERE key = ?", (*c["new"], now, key))
        else:
            conn.execute("DELETE FROM geo_learned WHERE key = ?", (key,))
        for r in conn.execute("""SELECT id, street, house, settlement, lat, lon, geo_status, geo_src FROM listings
                                 WHERE is_active = 1 AND admin_fixed = 0 AND geo_src = 'learned' AND lat = ? AND lon = ?""",
                              tuple(c["old"])).fetchall():
            k = learning.addr_key(r["street"], r["house"], r["settlement"])
            if k and conn.execute("SELECT 1 FROM geo_learned WHERE key = ?", (k,)).fetchone():
                continue   # это и правда точка дома этого объекта
            old = [r["lat"], r["lon"], r["geo_status"], r["geo_src"]]
            new = [*c["new"], "approx", "learned-cx"] if c["new"] else [None, None, "pending", None]
            conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ?, geo_src = ? WHERE id = ?", (*new, r["id"]))
            _log(conn, r["id"], "point", old, new, now)
            rep["listings"] += 1
        rep["cx_points"] += 1
    rules_by_id = {r["id"]: r for r in conn.execute("SELECT * FROM learned_rules WHERE kind IN ('street', 'cx_district')")}
    for rid in rule_ids:
        rule = rules_by_id.get(int(rid))
        if rule is None:
            continue
        target = json.loads(rule["value"]).get("district")
        learning.forget(conn, rule["id"])
        rep["rules"] += 1
        for r in conn.execute("SELECT * FROM listings WHERE is_active = 1 AND admin_fixed = 0 AND district = ?", (target,)).fetchall():
            k = learning.street_key(r["street"], r["settlement"]) if rule["kind"] == "street" else learning.cx_key(r["complex"])
            if k != rule["key"]:
                continue
            d = dict(r)
            learning.apply(conn, d)   # остальные правила (дом, ЖК) — как обычно
            new = d["district"] if d["district"] != target else _redecide(conn, r)
            if new != r["district"]:
                d["district"] = new
                d["title"] = make_title(d)
                d["search_text"] = make_search_text(d)
                conn.execute("UPDATE listings SET district = ?, district_src = NULL, title = ?, search_text = ? WHERE id = ?",
                             (new, d["title"], d["search_text"], r["id"]))
                _reindex(conn, r["id"], d["search_text"])
                _log(conn, r["id"], "district", r["district"], new, now)
                rep["listings"] += 1
    for name in junk:
        if not is_junk_complex(name):
            continue
        ids = [r[0] for r in conn.execute("SELECT id FROM listings WHERE is_active = 1 AND complex = ?", (name,))]
        for lid in ids:
            _log(conn, lid, "complex", name, None, now)
        res = learning.rename_complex(conn, name, None)   # «это не ЖК» — везде и на будущее
        rep["junk"] += 1
        rep["listings"] += res.get("listings", 0)
    if labels:
        for x in label_streets(conn):
            r = conn.execute("SELECT * FROM listings WHERE id = ?", (x["id"],)).fetchone()
            d = {**dict(r), "street": None, "house": None}
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            conn.execute("""UPDATE listings SET street = NULL, house = NULL, title = ?, search_text = ?,
                            geo_status = CASE WHEN geo_status IN ('manual', 'feed', 'learned') THEN geo_status ELSE 'pending' END
                            WHERE id = ?""", (d["title"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            _log(conn, r["id"], "street", r["street"], None, now)
            rep["labels"] += 1
    conn.commit()
    return dict(rep)

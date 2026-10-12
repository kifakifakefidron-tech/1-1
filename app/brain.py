"""«Обучение» — один сервис разбора и обучения: одна очередь, одна цепочка решений, всё выученное в одном месте.

Как сервис решает, где объект (от главного к запасному; так работают и новые объявления, и самообучение,
и ежечасная перепроверка — autolearn.recheck_all):
  район:  ваши правки объекта → ваши правила (дом / ЖК / улица) → ваши нарисованные районы (по точке дома)
          → соседние объявления того же дома / ЖК → текст объявления и справочник;
  точка:  ваши точки → тот же дом в фиде СТРЕЛ → справочник адресов OSM (дом, соседний дом) → контур ЖК
          → поиск по адресу (проверяется по ЖК и району) → середина улицы / центр района (примерно).
Когда источники спорят, сервис не выбирает сам — объект попадает в очередь «Спорные» (listings.place_conflict).
Любая правка на экране разбора (/fix) учит сервис: правило (learning.learn), точка (geocode.learn), цена/сделка
(ingest.learned_fix) — и сразу применяется к похожим объектам.
"""
from __future__ import annotations

import json
import sqlite3

from . import autolearn, districtmap, learning

_AUTO_GEO = "('feed-house', 'feed-cx', 'map-cx', 'map-district', 'osm-house', 'osm-near', 'osm-street')"
VISIBLE = ("l.is_active = 1 AND (l.district IS NOT NULL OR l.complex IS NOT NULL OR l.street IS NOT NULL "
           "OR l.settlement IS NOT NULL)")

# Единая очередь — по важности. mode: что на экране разбора главное (place — район/ЖК, geo — точка); показываются оба.
QUEUES: list[dict] = [
    {"key": "conflict", "mode": "place", "label": "Спорные",
     "hint": "Источники не сходятся (ваше правило и ваша карта, карта и соседние объявления) — решите вы",
     "where": "l.place_conflict IS NOT NULL AND l.admin_fixed = 0"},
    {"key": "nodistrict", "mode": "place", "label": "Без района",
     "hint": "Не находятся фильтром по району",
     "where": "l.district IS NULL AND l.settlement IS NULL AND l.admin_fixed = 0"},
    {"key": "nogeo", "mode": "geo", "label": "Нет на карте",
     "hint": "Адрес не нашёлся ни в справочнике, ни поиском",
     "where": "l.lat IS NULL AND l.geo_status IN ('none', 'skip', 'pending')"},
    {"key": "approx", "mode": "geo", "label": "Точка примерная",
     "hint": "Стоит по улице или району — уточните дом",
     "where": "l.geo_status = 'approx'"},
    {"key": "auto", "mode": "place", "label": "Проверить решения сервиса",
     "hint": "Район или точку сервис поставил сам — проверьте выборочно",
     "where": f"((l.district_src IS NOT NULL AND l.admin_fixed = 0) OR (l.geo_src IN {_AUTO_GEO} "
              "AND l.geo_status NOT IN ('manual', 'feed')))"},
    {"key": "fixed", "mode": "place", "label": "Поправлено вами", "hint": "Ваши правки — можно пересмотреть",
     "where": "l.admin_fixed = 1"},
]
QUEUE = {q["key"]: q for q in QUEUES}
# старые ссылки (/fix?mode=geo&kind=none, вкладки админки, письма) → единая очередь
ALIASES = {("geo", "none"): "nogeo", ("geo", "pending"): "nogeo", ("geo", "approx"): "approx", ("geo", "auto"): "auto",
           ("place", "nodistrict"): "nodistrict", ("place", "nocomplex"): "nodistrict", ("place", "auto"): "auto",
           ("place", "fixed"): "fixed"}


def queue_key(mode: str | None, kind: str | None) -> str:
    if kind in QUEUE:
        return kind
    return ALIASES.get((mode or "place", kind or ""), "conflict" if not kind else "nodistrict")


def counts(conn: sqlite3.Connection) -> dict:
    return {q["key"]: conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {VISIBLE} AND {q['where']}").fetchone()[0]
            for q in QUEUES}


def overview(conn: sqlite3.Connection) -> dict:
    """Сводка для страницы «Обучение»: что готово, что ждёт вас, как сервис решает и что выучил."""
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    total = one(f"SELECT COUNT(*) FROM listings l WHERE {VISIBLE}")
    ready = one(f"""SELECT COUNT(*) FROM listings l WHERE {VISIBLE} AND l.lat IS NOT NULL
                    AND l.geo_status IN ('ok', 'manual', 'learned', 'feed')
                    AND (l.district IS NOT NULL OR l.settlement IS NOT NULL) AND l.place_conflict IS NULL""")
    c = counts(conn)
    geo = dict(conn.execute(f"""SELECT CASE WHEN l.geo_status IN ('manual', 'learned') THEN 'yours'
                                       WHEN l.geo_status = 'feed' OR l.geo_src IN ('feed-house', 'feed-cx') THEN 'feed'
                                       WHEN l.geo_src IN ('osm-house', 'osm-near') THEN 'osm'
                                       WHEN l.geo_src = 'map-cx' THEN 'cx'
                                       WHEN l.geo_status = 'approx' THEN 'approx'
                                       WHEN l.lat IS NOT NULL THEN 'geocoder' ELSE 'none' END, COUNT(*)
                                FROM listings l WHERE {VISIBLE} GROUP BY 1""").fetchall())
    dist = dict(conn.execute(f"""SELECT CASE WHEN l.admin_fixed = 1 THEN 'yours'
                                        WHEN l.district_src = 'map' THEN 'map'
                                        WHEN l.district_src IN ('vote-house', 'vote-cx') THEN 'votes'
                                        WHEN l.district IS NOT NULL OR l.settlement IS NOT NULL THEN 'text' ELSE 'none' END,
                                        COUNT(*) FROM listings l WHERE {VISIBLE} GROUP BY 1""").fetchall())
    rules = dict(conn.execute("SELECT kind, COUNT(*) FROM learned_rules GROUP BY kind").fetchall())
    rep = conn.execute("SELECT value FROM state WHERE key = 'recheck_report'").fetchone()
    # районы из объявлений, которых нет на вашей карте: их объекты не проверяются по карте
    drawn = {n for n, kind, *_ in districtmap._polys(conn) if kind == "district"}
    uncontoured = [{"name": d, "n": n} for d, n in conn.execute(
        f"""SELECT l.district, COUNT(*) FROM listings l WHERE {VISIBLE} AND l.district IS NOT NULL
            GROUP BY l.district ORDER BY 2 DESC""").fetchall() if d not in drawn]
    return {
        "uncontoured": uncontoured,
        "total": total, "ready": ready, "queues": [{k: q[k] for k in ("key", "label", "hint", "mode")} | {"n": c[q["key"]]}
                                                   for q in QUEUES],
        "pipeline": {
            "district": [
                {"key": "yours", "label": "Ваши правки объекта", "n": dist.get("yours", 0)},
                {"key": "rules", "label": "Ваши правила: дом, ЖК, улица → район",
                 "n": sum(rules.get(k, 0) for k in ("addr", "cx_district", "street", "cx_alias")), "unit": "правил"},
                {"key": "map", "label": "Ваши нарисованные районы (по точке дома)", "n": dist.get("map", 0)},
                {"key": "votes", "label": "Соседние объявления того же дома / ЖК", "n": dist.get("votes", 0)},
                {"key": "text", "label": "Текст объявления и справочник", "n": dist.get("text", 0)},
                {"key": "none", "label": "Не определён — в очереди", "n": dist.get("none", 0), "bad": True},
            ],
            "point": [
                {"key": "yours", "label": "Ваши точки", "n": geo.get("yours", 0)},
                {"key": "feed", "label": "Фид СТРЕЛ (тот же дом / ЖК)", "n": geo.get("feed", 0)},
                {"key": "osm", "label": "Справочник адресов OSM (дом)", "n": geo.get("osm", 0)},
                {"key": "cx", "label": "Контур ЖК", "n": geo.get("cx", 0)},
                {"key": "geocoder", "label": "Поиск по адресу (проверен по ЖК и району)", "n": geo.get("geocoder", 0)},
                {"key": "approx", "label": "Примерно: улица или район", "n": geo.get("approx", 0), "warn": True},
                {"key": "none", "label": "Нет точки — в очереди", "n": geo.get("none", 0), "bad": True},
            ],
        },
        "knowledge": {
            "rules": sum(rules.values()), "points": one("SELECT COUNT(*) FROM geo_learned"),
            "districts_drawn": one("SELECT COUNT(*) FROM district_polygons WHERE kind = 'district' AND source = 'admin'"),
            "complex_contours": one("SELECT COUNT(*) FROM district_polygons WHERE kind = 'complex'"),
            "osm_addr": one("SELECT COUNT(*) FROM osm_addr"),
            "prices": rules.get("frag_price", 0) + rules.get("frag_deal", 0),
        },
        "recheck": json.loads(rep[0]) | {"active_edits": one(
            "SELECT COUNT(*) FROM listing_edits WHERE user_id = ? AND reverted = 0", autolearn.SERVICE_UID)} if rep else None,
    }


# ─── всё выученное — одним списком ─────────────────────────────────────────
KNOW_KINDS = {"addr": "Дом", "cx_alias": "Название ЖК", "cx_district": "ЖК → район", "street": "Улица → район",
              "frag_price": "Цена", "frag_deal": "Продажа / аренда", "point": "Точка на карте"}


def knowledge(conn: sqlite3.Connection, kind: str = "", q: str = "", limit: int = 500) -> list[dict]:
    out = []
    if kind in ("", "point"):
        for r in conn.execute("SELECT key, label, lat, lon, n, ts FROM geo_learned ORDER BY ts DESC LIMIT ?", (limit,)):
            out.append({"type": "point", "id": r["key"], "kind": "point", "kind_label": KNOW_KINDS["point"],
                        "label": r["label"] or r["key"], "n": r["n"], "ts": r["ts"], "lat": r["lat"], "lon": r["lon"]})
    if kind != "point":
        sql = "SELECT id, kind, key, label, n, ts FROM learned_rules" + (" WHERE kind = ?" if kind else "") + \
              " ORDER BY ts DESC LIMIT ?"
        for r in conn.execute(sql, ((kind, limit) if kind else (limit,))):
            out.append({"type": "rule", "id": r["id"], "kind": r["kind"], "kind_label": KNOW_KINDS.get(r["kind"], r["kind"]),
                        "label": r["label"] or r["key"], "n": r["n"], "ts": r["ts"]})
    if q:
        ql = q.lower().replace("ё", "е")
        out = [x for x in out if ql in (x["label"] or "").lower().replace("ё", "е")]
    out.sort(key=lambda x: -(x["ts"] or 0))
    return out[:limit]


def forget(conn: sqlite3.Connection, typ: str, ident) -> bool:
    """Забыть выученное. Уже поправленные объекты не откатываются — только новые не будут так решаться."""
    if typ == "rule":
        learning.forget(conn, int(ident))
        return True
    if typ == "point":
        conn.execute("DELETE FROM geo_learned WHERE key = ?", (str(ident),))
        conn.commit()
        return True
    return False

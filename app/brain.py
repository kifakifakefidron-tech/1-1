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

# точный дом (справочник, фид, ваши правки) — надёжно, не проверяем; сомнительно — соседний дом, улица, центр ЖК/района
_AUTO_GEO = "('feed-cx', 'map-cx', 'geocoder-cx', 'map-district', 'osm-near', 'osm-street')"
_CX_GEO = "('feed-cx', 'map-cx', 'geocoder-cx')"
_SURE_GEO = "('osm-house', 'feed-house', 'learned')"
SAMPLE_PER_SOURCE = 40   # ТЗ P4.0.1: в день по каждому источнику — 30–50 случайных решений, а не все подряд
# Откуда решение сервиса (для выборки и оценки точности): район «по соседям», район по примерной точке, источник точки
AUTO_SRC = (f"(CASE WHEN l.district_src IN ('vote-house', 'vote-cx') THEN 'district:' || l.district_src "
            f"WHEN l.district_src = 'map' AND COALESCE(l.geo_src, '') NOT IN {_SURE_GEO} "
            f"AND l.geo_status NOT IN ('manual', 'learned', 'feed') THEN 'district:map-approx' "
            f"ELSE 'point:' || l.geo_src END)")
_AUTO_ALL = (f"((l.admin_fixed = 0 AND (l.district_src IN ('vote-house', 'vote-cx') OR (l.district_src = 'map' "
             f"AND COALESCE(l.geo_src, '') NOT IN {_SURE_GEO} AND l.geo_status NOT IN ('manual', 'learned', 'feed')))) "
             f"OR (l.geo_src IN {_AUTO_GEO} AND l.geo_status NOT IN ('manual', 'feed') "
             # центр ЖК при известном доме — не в проверку: такой дом сервис ищет сам (geocode.run: дом раньше ЖК)
             f"AND NOT (l.geo_src IN {_CX_GEO} AND l.street IS NOT NULL AND l.house IS NOT NULL)))")
SRC_LABEL = {"district:vote-house": "Район «как у соседей по дому»", "district:vote-cx": "Район «как у соседей по ЖК»",
             "district:map-approx": "Район по примерной точке", "point:feed-cx": "Точка — центр ЖК по фиду",
             "point:map-cx": "Точка — центр контура ЖК", "point:geocoder-cx": "Точка — ЖК по поиску",
             "point:map-district": "Точка — центр района", "point:osm-near": "Точка — соседний дом",
             "point:osm-street": "Точка — середина улицы"}
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
     "hint": f"Выборка на сегодня: до {SAMPLE_PER_SOURCE} случайных решений по каждому источнику (соседний дом, улица, "
             "центр ЖК, район «по соседям»). «Сохранить» без правок = «верно» — так считается точность источника",
     "where": f"""l.id IN (SELECT id FROM (SELECT l.id, ROW_NUMBER() OVER (PARTITION BY {AUTO_SRC}
                  ORDER BY (l.id * 7919 + CAST(strftime('%Y%j', 'now') AS INTEGER) * 104729) % 1000003) AS rn
                  FROM listings l WHERE {VISIBLE} AND {_AUTO_ALL}
                  AND NOT EXISTS (SELECT 1 FROM auto_reviews a WHERE a.listing_id = l.id)) WHERE rn <= {SAMPLE_PER_SOURCE})"""},
    {"key": "repair", "mode": "place", "label": "Ремонт: проверьте",
     "hint": "В тексте говорят о состоянии, но сервис не уверен: с ремонтом, без ремонта или не указано. Ваш ответ — правило "
             "для этого текста во всех чатах, по ответам видно, где правило ошибается",
     "where": "l.repair_doubt = 1 AND json_type(l.admin_fields, '$.repair') IS NULL AND l.type IN ('flat', 'new', 'house', 'room')"},
    {"key": "fixed", "mode": "place", "label": "Поправлено вами", "hint": "Ваши правки — можно пересмотреть",
     "where": "l.admin_fixed = 1"},
]
QUEUE = {q["key"]: q for q in QUEUES}
# старые ссылки (/fix?mode=geo&kind=none, вкладки админки, письма) → единая очередь
ALIASES = {("geo", "none"): "nogeo", ("geo", "pending"): "nogeo", ("geo", "approx"): "approx", ("geo", "auto"): "auto",
           ("place", "nodistrict"): "nodistrict", ("place", "nocomplex"): "nodistrict", ("place", "auto"): "auto",
           ("place", "fixed"): "fixed"}


def auto_source(conn: sqlite3.Connection, lid: int) -> str | None:
    """Источник решения сервиса для объекта (None — решение не сервиса или надёжное)."""
    r = conn.execute(f"SELECT {AUTO_SRC} FROM listings l WHERE l.id = ? AND {_AUTO_ALL}", (lid,)).fetchone()
    return r[0] if r else None


def record_review(conn: sqlite3.Connection, lid: int, src: str | None, ok: bool) -> None:
    """Админ посмотрел решение сервиса из выборки: верно (сохранил без правок) или поправил."""
    if src:
        import time
        conn.execute("INSERT OR REPLACE INTO auto_reviews (listing_id, src, ok, ts) VALUES (?,?,?,?)",
                     (lid, src, int(ok), int(time.time())))


def accuracy(conn: sqlite3.Connection) -> list[dict]:
    """Точность источников по вашим ответам (за 90 дней)."""
    import time
    rows = conn.execute("""SELECT src, COUNT(*), SUM(ok) FROM auto_reviews WHERE ts > ? GROUP BY src ORDER BY 2 DESC""",
                        (int(time.time()) - 90 * 86400,)).fetchall()
    return [{"src": s, "label": SRC_LABEL.get(s, s), "checked": n, "ok": k or 0, "share": round((k or 0) / n, 2)}
            for s, n, k in rows]


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
        "accuracy": accuracy(conn),
        "total": total, "ready": ready, "queues": [{k: q[k] for k in ("key", "label", "hint", "mode")} | {"n": c[q["key"]]}
                                                   for q in QUEUES],
        "pipeline": {
            "district": [
                {"key": "yours", "label": "Ваши правки объекта", "n": dist.get("yours", 0)},
                {"key": "rules", "label": "Ваше правило «дом → район»", "n": rules.get("addr", 0), "unit": "правил"},
                {"key": "map", "label": "Ваши нарисованные районы (только по точке дома)", "n": dist.get("map", 0)},
                {"key": "cxrules", "label": "Ваши правила «ЖК → район» (когда точки дома нет)",
                 "n": rules.get("cx_district", 0) + rules.get("cx_alias", 0), "unit": "правил"},
                {"key": "votes", "label": "Соседние объявления того же дома / ЖК", "n": dist.get("votes", 0)},
                {"key": "text", "label": "Текст объявления и справочник", "n": dist.get("text", 0)},
                {"key": "strules", "label": "Ваши правила «улица → район» (нет ни района в тексте, ни точки дома)",
                 "n": rules.get("street", 0), "unit": "правил"},
                {"key": "none", "label": "Не определён — в очереди", "n": dist.get("none", 0), "bad": True},
            ],
            "point": [
                {"key": "yours", "label": "Ваши точки", "n": geo.get("yours", 0)},
                {"key": "feed", "label": "Фид СТРЕЛ (тот же дом / ЖК)", "n": geo.get("feed", 0)},
                {"key": "osm", "label": "Справочник адресов OSM (дом)", "n": geo.get("osm", 0)},
                {"key": "geocoder", "label": "Поиск по адресу (проверен по ЖК и району)", "n": geo.get("geocoder", 0)},
                {"key": "cx", "label": "Центр ЖК (дома нет)", "n": geo.get("cx", 0)},
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

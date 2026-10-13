"""Качество гео-данных (ТЗ «админка-гео», P4.2–P4.3 и «Проверка»): те же цифры, что в аудите 13.10.

  * metrics — сейчас: без точки, примерные (улица/район), центр ЖК, без района, квартиры без ЖК, без улицы,
    точка дома вне всех контуров, район ≠ контур под точкой дома, без района при точке в контуре, одинаковые объекты
    (ЖК/адрес + площадь + этаж + цена — это норма: разные агенты, владелец и брат; считаем только для наблюдения);
  * snapshot — раз в сутки в geo_quality_daily (динамика на /learn);
  * weekly — раз в неделю отчёт админу: что сервис поменял сам, сколько в очередях, топ-10 правил;
  * python -m tools.geo_audit [путь к базе] — то же в консоли (для сверки до/после этапов).
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter

from . import brain, db, districtmap, notices
from .search import GEO_LEVEL

# подписи и «хорошо — меньше»; цель ТЗ после P0–P3: без района < 2 %, расхождения < 30
LABELS = {"total": "Объектов", "no_point": "Без точки на карте", "vague": "Точка примерная (улица/район)",
          "complex_level": "Точка — центр ЖК", "no_district": "Без района", "flats_no_complex": "Квартиры без ЖК",
          "no_street": "Без улицы", "outside": "Точка дома вне всех районов", "mismatch": "Район ≠ контур под точкой дома",
          "empty_in_contour": "Без района, хотя точка в контуре", "same_groups": "Один объект в нескольких карточках (норма)"}


def metrics(conn: sqlite3.Connection) -> dict:
    vis = brain.VISIBLE
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    out = {
        "total": one(f"SELECT COUNT(*) FROM listings l WHERE {vis}"),
        "no_point": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND l.lat IS NULL"),
        "vague": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND {GEO_LEVEL} IN ('street', 'district')"),
        "complex_level": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND {GEO_LEVEL} = 'complex'"),
        "no_district": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND l.district IS NULL AND l.settlement IS NULL"),
        "flats_no_complex": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND l.type IN ('flat', 'new') AND l.complex IS NULL"),
        "no_street": one(f"SELECT COUNT(*) FROM listings l WHERE {vis} AND l.street IS NULL"),
    }
    outside = mismatch = empty = 0
    if any(kind == "district" for _n, kind, *_ in districtmap._polys(conn)):
        for r in conn.execute(f"""SELECT l.lat, l.lon, l.district, l.extra_districts, l.settlement FROM listings l
                                  WHERE {vis} AND {GEO_LEVEL} = 'house'""").fetchall():
            hit = districtmap.district_at(conn, r["lat"], r["lon"])
            d = hit[0] if hit and hit[1] == "district" else None
            if d is None:
                outside += not r["settlement"]
            elif r["district"] is None and not r["settlement"]:
                empty += 1
            elif r["district"] and d != r["district"] and d not in json.loads(r["extra_districts"] or "[]"):
                mismatch += 1
    out.update(outside=outside, mismatch=mismatch, empty_in_contour=empty)
    groups = Counter(conn.execute(f"""SELECT COALESCE(l.complex, l.street || ' ' || COALESCE(l.house, '')), l.area, l.floor,
                                              l.price FROM listings l WHERE {vis} AND l.area IS NOT NULL
                                              AND (l.complex IS NOT NULL OR l.street IS NOT NULL)""").fetchall())
    out["same_groups"] = sum(1 for n in groups.values() if n > 1)
    return out


def outside_points(conn: sqlite3.Connection, limit: int = 3000) -> list[dict]:
    """Точки домов (в Краснодаре, не посёлки) вне всех нарисованных районов: дыры между районами и ненарисованные места."""
    vis = brain.VISIBLE
    out = []
    for r in conn.execute(f"""SELECT l.id, l.lat, l.lon, l.title, l.district, l.street, l.house FROM listings l
                              WHERE {vis} AND {GEO_LEVEL} = 'house' AND l.settlement IS NULL""").fetchall():
        hit = districtmap.district_at(conn, r["lat"], r["lon"])
        if not (hit and hit[1] == "district"):
            out.append(dict(r))
            if len(out) >= limit:
                break
    return out


def snapshot(conn: sqlite3.Connection) -> bool:
    """Раз в сутки (по Москве) — снимок для динамики."""
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 3600))
    if conn.execute("SELECT 1 FROM geo_quality_daily WHERE day = ?", (day,)).fetchone():
        return False
    conn.execute("INSERT OR REPLACE INTO geo_quality_daily (day, data) VALUES (?, ?)", (day, json.dumps(metrics(conn))))
    conn.commit()
    return True


def dashboard(conn: sqlite3.Connection) -> dict:
    """Для /learn: сейчас, неделю назад и по дням (14)."""
    now = metrics(conn)
    days = [(d, json.loads(v)) for d, v in conn.execute(
        "SELECT day, data FROM geo_quality_daily ORDER BY day DESC LIMIT 14").fetchall()][::-1]
    week = days[-8][1] if len(days) >= 8 else (days[0][1] if days else None)
    return {"now": now, "week_ago": week, "days": days, "labels": LABELS}


def weekly(conn: sqlite3.Connection, force: bool = False) -> str | None:
    """Раз в неделю — отчёт админам (уведомление на сайте + Telegram/почта через notify): что сервис поменял сам,
    сколько новых в очередях, топ-10 правил по числу применений."""
    now = int(time.time())
    if not force and now - int(db.get_state(conn, "geo_week_ts") or 0) < 7 * 86400:
        return None
    since = now - 7 * 86400
    edits = conn.execute("""SELECT COALESCE(src, 'сервис'), field, COUNT(*) FROM listing_edits
                            WHERE user_id = 0 AND ts > ? AND reverted = 0 GROUP BY 1, 2 ORDER BY 3 DESC""", (since,)).fetchall()
    reverted = conn.execute("SELECT COUNT(*) FROM listing_edits WHERE user_id = 0 AND ts > ? AND reverted = 1",
                            (since,)).fetchone()[0]
    names = {"district": "район", "extra_districts": "второй район", "complex": "ЖК", "point": "точка", "street": "улица"}
    c = brain.counts(conn)
    top = conn.execute("SELECT label, n FROM learned_rules ORDER BY n DESC, ts DESC LIMIT 10").fetchall()
    m = metrics(conn)
    lines = ["Отчёт «Обучение и разбор» за неделю", "",
             "Сервис поменял сам:" if edits else "Сервис сам ничего не менял."]
    lines += [f"  {src}: {names.get(f, f)} — {n}" for src, f, n in edits]
    if reverted:
        lines.append(f"  вы откатили: {reverted}")
    lines += ["", "Ждут вас: " + ", ".join(f"{q['label'].lower()} {c[q['key']]}" for q in brain.QUEUES
                                          if q["key"] != "fixed" and c[q["key"]]) or "очереди пусты"]
    lines += ["", f"Без района: {m['no_district']} из {m['total']}; район ≠ карта: {m['mismatch']}; "
                  f"без точки: {m['no_point']}; примерных точек: {m['vague']}"]
    if top:
        lines += ["", "Чаще всего срабатывают правила:"] + [f"  {lb} — {n}" for lb, n in top]
    text = "\n".join(lines)
    for (uid,) in conn.execute("SELECT id FROM users WHERE is_admin = 1").fetchall():
        notices.add(conn, uid, "sub", "Отчёт за неделю: обучение и разбор", text[:1500], url="/learn")
    db.set_state(conn, "geo_week_ts", str(now))
    conn.commit()
    try:
        from . import notify
        notify.send(conn, text)
    except Exception:  # noqa: BLE001
        pass
    return text

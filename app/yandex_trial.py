"""Проба Яндекс Геокодера: сравнить с нашими точками и OpenStreetMap на одних и тех же адресах.

Ничего не записывает в базу — только печатает отчёт (по бесплатным условиям Яндекса результаты
нельзя хранить). Ключ — YANDEX_GEOCODER_KEY в .env (кабинет разработчика Яндекса,
«JavaScript API и HTTP Геокодер»).

    docker compose exec web python -m app.yandex_trial 100
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.parse
import urllib.request

from . import db, districtmap, geo, geocode

URL = "https://geocode-maps.yandex.ru/1.x/"
SITE = os.getenv("SITE_DOMAIN", "").strip()


def yandex(query: str, key: str) -> dict | None:
    """Один запрос к Яндексу: {lat, lon, precision, kind, text, district} или None."""
    b = geocode.CITY_BOX
    params = {"apikey": key, "geocode": query, "format": "json", "lang": "ru_RU", "results": 1}
    if geocode._city_query(query):   # ищем только в Краснодаре
        params.update(bbox=f"{b[1]},{b[0]}~{b[3]},{b[2]}", rspn=1)
    with urllib.request.urlopen(f"{URL}?{urllib.parse.urlencode(params)}", timeout=15) as resp:
        return parse(json.loads(resp.read()))


def parse(data: dict) -> dict | None:
    found = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember") or []
    if not found:
        return None
    g = found[0]["GeoObject"]
    lon, lat = map(float, g["Point"]["pos"].split())
    meta = g.get("metaDataProperty", {}).get("GeocoderMetaData", {})
    parts = (meta.get("Address") or {}).get("Components") or []
    # у Яндекса микрорайон — последний компонент вида district («Прикубанский округ», «микрорайон Гидрострой»)
    dists = [p["name"] for p in parts if p.get("kind") == "district"]
    return {"lat": lat, "lon": lon, "precision": meta.get("precision"), "kind": meta.get("kind"),
            "text": meta.get("text"), "district": dists[-1] if dists else None}


def nominatim(query: str) -> tuple[float, float] | None:
    """Свежий ответ OpenStreetMap (мимо кэша и без записи)."""
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "ru", "accept-language": "ru"}
    b = geocode.CITY_BOX
    if geocode._city_query(query):
        params.update(viewbox=f"{b[1]},{b[2]},{b[3]},{b[0]}", bounded=1)
    req = urllib.request.Request(f"{geocode.URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": geocode.UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001
        return None
    finally:
        time.sleep(1.1)   # правило OpenStreetMap: не чаще раза в секунду
    return (float(data[0]["lat"]), float(data[0]["lon"])) if data else None


def sample(conn: sqlite3.Connection, n: int) -> list[tuple[str, sqlite3.Row]]:
    """Три группы: «правда» (точки, поправленные админом, и координаты из фида СТРЕЛ), точные точки геокодера,
    объекты без точки или с примерной."""
    base = "SELECT * FROM listings WHERE is_active = 1 AND settlement IS NULL"
    third = max(1, n // 3)
    groups = [
        ("admin", f"{base} AND geo_status IN ('manual', 'learned', 'feed') AND (street IS NOT NULL OR complex IS NOT NULL)"),
        ("ok", f"{base} AND geo_status = 'ok' AND street IS NOT NULL AND house IS NOT NULL"),
        ("none", f"{base} AND (lat IS NULL OR geo_status = 'approx') AND (street IS NOT NULL OR complex IS NOT NULL)"),
    ]
    out, seen = [], set()
    for i, (name, sql) in enumerate(groups):
        want = n - len(out) if i == len(groups) - 1 else third
        for r in conn.execute(f"{sql} ORDER BY random() LIMIT ?", (want * 2,)).fetchall():
            q = geocode.build_query(r)
            if q and q not in seen and len([1 for g, _ in out if g == name]) < want:
                seen.add(q)
                out.append((name, r))
    return out


def _m(a, b) -> int | None:
    return round(geocode._km(a, b) * 1000) if a and b else None


def _same_district(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    return (geo.canonical_district(a) or a.lower()) == (geo.canonical_district(b) or b.lower())


def run(conn: sqlite3.Connection, n: int, key: str, out=print) -> dict:
    rows = sample(conn, n)
    out(f"Проверяю {len(rows)} адресов (Яндекс + OpenStreetMap, ~{len(rows) * 1.3 / 60:.0f} мин)…")
    res, ya_time, errors = [], [], 0
    for i, (grp, r) in enumerate(rows, 1):
        q = geocode.build_query(r)
        t0 = time.perf_counter()
        try:
            y = yandex(q, key)
        except Exception as e:  # noqa: BLE001
            errors += 1
            out(f"  Яндекс не ответил на «{q}»: {e}")
            if errors >= 3:
                out("Три ошибки подряд — проверьте ключ (новый ключ включается до 15 минут). Останавливаюсь.")
                break
            continue
        ya_time.append(time.perf_counter() - t0)
        osm = nominatim(q)
        ours = (r["lat"], r["lon"]) if r["lat"] is not None else None
        ya = (y["lat"], y["lon"]) if y else None
        map_d = districtmap.district_at(conn, *ya) if ya else None
        res.append({"grp": grp, "id": r["id"], "q": q, "title": r["title"], "district": r["district"],
                    "geo_status": r["geo_status"], "ya": y, "ya_m": _m(ya, ours), "osm_m": _m(osm, ours),
                    "osm_found": osm is not None, "ya_osm_m": _m(ya, osm),
                    "ya_district_ok": _same_district(y and y["district"], r["district"]),
                    "map_district": map_d[0] if map_d else None})
        if i % 10 == 0:
            out(f"  …{i} из {len(rows)}")
    report(res, ya_time, out)
    return {"checked": len(res), "errors": errors}


def report(res: list[dict], ya_time: list[float], out=print) -> None:
    exact = ("exact", "number")
    pct = lambda a, b: f"{a} из {b} ({round(100 * a / b) if b else 0} %)"   # noqa: E731
    out("\n══════ ИТОГ ПРОБЫ ЯНДЕКСА ══════")
    if ya_time:
        out(f"Скорость Яндекса: в среднем {statistics.mean(ya_time):.2f} с на адрес (OpenStreetMap — не быстрее 1 с по правилам)")
    adm = [x for x in res if x["grp"] == "admin"]
    if adm:
        out(f"\n1) Точки, которые точно верны (ваши правки и координаты из фида СТРЕЛ) — {len(adm)} шт. "
            "Насколько близко попали:")
        for name, k in (("Яндекс", "ya_m"), ("OpenStreetMap", "osm_m")):
            d = [x[k] for x in adm if x[k] is not None]
            near = len([v for v in d if v <= 150])
            out(f"   {name:14} нашёл {len(d)}, ближе 150 м к правде: {pct(near, len(adm))}"
                + (f", в среднем ошибка {round(statistics.median(d))} м" if d else ""))
    ok = [x for x in res if x["grp"] == "ok"]
    if ok:
        d = [x["ya_m"] for x in ok if x["ya_m"] is not None]
        out(f"\n2) Адреса с домом, где у нас уже есть точка — {len(ok)} шт.")
        out(f"   Яндекс совпал с нашей точкой (до 150 м): {pct(len([v for v in d if v <= 150]), len(ok))}")
        out(f"   Расходится больше 500 м: {len([v for v in d if v > 500])} — их стоит глянуть глазами (список ниже)")
    none = [x for x in res if x["grp"] == "none"]
    if none:
        ya_ex = len([x for x in none if x["ya"] and x["ya"]["precision"] in exact])
        out(f"\n3) Объекты без точки или с примерной точкой — {len(none)} шт.")
        out(f"   Яндекс нашёл точный дом: {pct(ya_ex, len(none))}")
        out(f"   Яндекс нашёл хоть что-то (улицу, ЖК): {pct(len([x for x in none if x['ya']]), len(none))}")
        out(f"   OpenStreetMap нашёл хоть что-то: {pct(len([x for x in none if x['osm_found']]), len(none))}")
    dd = [x for x in res if x["ya_district_ok"] is not None]
    if dd:
        out(f"\n4) Район у Яндекса совпал с нашим: {pct(len([x for x in dd if x['ya_district_ok']]), len(dd))}")
    bad = sorted([x for x in res if x["ya_m"] and x["ya_m"] > 500], key=lambda x: -x["ya_m"])[:25]
    if bad:
        out("\nРасхождения больше 500 м (кто прав — откройте обе ссылки):")
        for x in bad:
            link = f"https://{SITE}/?open={x['id']}" if SITE else f"объект {x['id']}"
            ya = x["ya"]
            out(f" • {x['q']}  —  {x['ya_m']} м  [{x['geo_status']}]\n"
                f"   Яндекс: {ya['text']} ({ya['precision']})\n"
                f"   у нас: {link}\n"
                f"   Яндекс Карты: https://yandex.ru/maps/?text={urllib.parse.quote(x['q'])}")
    out("\nНичего в базу не записано.")


def main() -> None:
    key = os.getenv("YANDEX_GEOCODER_KEY", "").strip()
    if not key:
        print("Нет ключа: впишите YANDEX_GEOCODER_KEY=… в .env и перезапустите (docker compose up -d).")
        sys.exit(1)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    conn = db.get()
    try:
        run(conn, min(n, 300), key)   # не больше 300 — бесплатный лимит Яндекса 1000 в сутки
    finally:
        conn.rollback()


if __name__ == "__main__":
    main()

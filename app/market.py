"""Аналитика рынка: медиана цены м² (продажа) и цены аренды по районам и ЖК, динамика за 7 и 30 дней.

Динамику считаем по ежедневным «снимкам» (таблица market_daily, раз в сутки из worker): сравниваем сегодняшнюю
медиану с ближайшим снимком неделю и месяц назад. Только квартиры, только объекты на сайте.
"""
from __future__ import annotations

import sqlite3
import statistics
import time

from . import geo
from .stats import VISIBLE, day_start

MIN_N = {"d": 5, "cx": 3}   # меньше объектов — медиана случайная, не показываем
ROOMS = [(0, "Студии"), (1, "1-комнатные"), (2, "2-комнатные"), (3, "3-комнатные"), (4, "4+ комнат")]


def _value(r, deal: str) -> int | None:
    """Продажа — цена м², аренда — цена в месяц."""
    if deal == "sale":
        return r["price_m2"] if r["price_m2"] and 30_000 <= r["price_m2"] <= 1_000_000 else None
    return r["price"] if r["price"] and 5_000 <= r["price"] <= 1_000_000 else None


def current(conn: sqlite3.Connection, deal: str) -> dict:
    """Сейчас: {('d', район) | ('cx', ключ ЖК): {name, median, price, n}} + по городу и по комнатам."""
    groups: dict[tuple, dict] = {}
    city, by_rooms = [], {}
    for r in conn.execute(f"""SELECT district, complex, price, price_m2, rooms, room_kind FROM listings
                              WHERE {VISIBLE} AND deal = ? AND type IN ('flat', 'new')""", (deal,)):
        v = _value(r, deal)
        if v is None:
            continue
        city.append((v, r["price"]))
        rk = 0 if r["room_kind"] == "studio" or r["rooms"] == 0 else min(r["rooms"] or 0, 4) or None
        if rk is not None:
            by_rooms.setdefault(rk, []).append((v, r["price"]))
        if r["district"]:
            g = groups.setdefault(("d", r["district"]), {"name": r["district"], "vals": [], "names": {}})
            g["vals"].append((v, r["price"]))
        if r["complex"]:
            g = groups.setdefault(("cx", geo.complex_key(r["complex"])), {"vals": [], "names": {}})
            g["names"][r["complex"]] = g["names"].get(r["complex"], 0) + 1
            g["vals"].append((v, r["price"]))
    out = {}
    for (kind, key), g in groups.items():
        if len(g["vals"]) < MIN_N[kind] or not key:
            continue
        name = g.get("name") or max(g["names"], key=g["names"].get)   # самое частое написание ЖК
        out[(kind, key)] = {"name": name, "median": int(statistics.median(v for v, _ in g["vals"])),
                            "price": int(statistics.median(p for _, p in g["vals"] if p)), "n": len(g["vals"])}
    summary = {"median": int(statistics.median(v for v, _ in city)) if city else None,
               "price": int(statistics.median(p for _, p in city if p)) if city else None, "n": len(city),
               "rooms": [{"label": label, "median": int(statistics.median(v for v, _ in by_rooms[k])),
                          "price": int(statistics.median(p for _, p in by_rooms[k] if p)), "n": len(by_rooms[k])}
                         for k, label in ROOMS if len(by_rooms.get(k, [])) >= 3]}
    return {"groups": out, "city": summary}


def snapshot(conn: sqlite3.Connection, now: int | None = None, force: bool = False) -> bool:
    """Раз в сутки записать медианы (для динамики). Возвращает True, если записали."""
    day = time.strftime("%Y-%m-%d", time.gmtime(day_start(int(now or time.time())) + 3 * 3600))
    if not force and conn.execute("SELECT 1 FROM market_daily WHERE day = ? LIMIT 1", (day,)).fetchone():
        return False
    rows = []
    for deal in ("sale", "rent"):
        cur = current(conn, deal)
        for (kind, key), g in cur["groups"].items():
            rows.append((day, deal, kind, key, g["name"], g["median"], g["price"], g["n"]))
        if cur["city"]["median"]:
            rows.append((day, deal, "city", "", "Краснодар", cur["city"]["median"], cur["city"]["price"], cur["city"]["n"]))
    conn.execute("DELETE FROM market_daily WHERE day = ?", (day,))
    conn.executemany("""INSERT INTO market_daily (day, deal, kind, key, name, median, price, n)
                        VALUES (?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    return True


def _past(conn: sqlite3.Connection, deal: str, days_ago: int, now: int) -> tuple[str | None, dict]:
    """Ближайший снимок не позже чем days_ago дней назад: (день, {(kind, key): median})."""
    edge = time.strftime("%Y-%m-%d", time.gmtime(day_start(now) + 3 * 3600 - days_ago * 86400))
    day = conn.execute("SELECT MAX(day) FROM market_daily WHERE deal = ? AND day <= ?", (deal, edge)).fetchone()[0]
    if not day:
        return None, {}
    return day, {(r[0], r[1]): r[2] for r in conn.execute(
        "SELECT kind, key, median FROM market_daily WHERE deal = ? AND day = ?", (deal, day))}


def report(conn: sqlite3.Connection, deal: str = "sale", now: int | None = None) -> dict:
    now = int(now or time.time())
    deal = "rent" if deal == "rent" else "sale"
    snapshot(conn, now)   # первый заход за день — заодно снимок (если worker ещё не сделал)
    cur = current(conn, deal)
    d7_day, d7 = _past(conn, deal, 7, now)
    d30_day, d30 = _past(conn, deal, 30, now)
    pct = lambda new, old: round((new / old - 1) * 100, 1) if old else None   # noqa: E731

    def rows(kind: str) -> list[dict]:
        out = []
        for (k, key), g in cur["groups"].items():
            if k != kind:
                continue
            out.append({**g, "key": key, "ch7": pct(g["median"], d7.get((k, key))), "ch30": pct(g["median"], d30.get((k, key)))})
        return sorted(out, key=lambda x: -x["n"])

    city = cur["city"]
    city["ch7"] = pct(city["median"], d7.get(("city", ""))) if city["median"] else None
    city["ch30"] = pct(city["median"], d30.get(("city", ""))) if city["median"] else None
    return {"deal": deal, "city": city, "districts": rows("d"), "complexes": rows("cx"),
            "since7": d7_day, "since30": d30_day,
            "first_day": conn.execute("SELECT MIN(day) FROM market_daily").fetchone()[0]}

"""Нормализация названий ЖК (ТЗ v2, P1.7): одно название на один ЖК — в карточках, фильтрах и подборе.

«Самолет 2» = «Самолёт 2» = «ЖК САМОЛЕТ-2» (ключ geo.complex_key: ё/е, регистр, «ЖК», кавычки, тире); «Самолет 1» =
«Самолет» (первая очередь — основное название), если основное есть в базе. Какое написание оставить: из справочника ЖК,
иначе то, что поправил админ, иначе самое частое (при равенстве — с «ё»). Разовый прогон (worker, state cx_norm_v2: + разрядка «О Т Р А Ж Е Н И Е», «3 очередь» = «3», адрес в скобках; ключи правил и точек ЖК — заново) пишет
отчёт в state cx_norm_report (админка «Обзор»); новые объявления получают принятое написание в ingest.save_object.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import defaultdict

from . import geo

_FIRST = re.compile(r"\s+1$")


def _key(name: str | None, known: set[str] | None = None) -> str | None:
    k = geo.complex_key(name)
    if not k:
        return None
    base = _FIRST.sub("", k)
    if base != k and (known is None or base in known):   # «X 1» → «X», если такой ЖК есть
        return base
    return k


def _pick(variants: dict[str, tuple[int, int]]) -> str:
    """variants: написание → (сколько объектов, сколько поправлено админом)."""
    for name in variants:
        rec = geo.resolve_complex(name)
        if rec and geo.complex_key(rec.name) == geo.complex_key(name):
            return rec.name
    return max(variants, key=lambda n: (variants[n][1] > 0, variants[n][0], "ё" in n.lower(), -len(n)))


def run(conn: sqlite3.Connection, apply: bool = False) -> dict:
    rows = conn.execute("""SELECT complex, COUNT(*) AS n, SUM(admin_fixed) AS fx FROM listings
                           WHERE complex IS NOT NULL AND complex != '' GROUP BY complex""").fetchall()
    keys = {geo.complex_key(r["complex"]) for r in rows}
    groups: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    for r in rows:
        k = _key(r["complex"], keys)
        if k:
            groups[k][r["complex"]] = (r["n"], r["fx"] or 0)
    merged, changed = [], 0
    for k, variants in groups.items():
        canon = geo.pretty_name(_pick(variants))   # разрядка и адрес в скобках — убираем
        if len(variants) < 2 and canon in variants:
            continue
        others = {n: v[0] for n, v in variants.items() if n != canon}
        merged.append({"name": canon, "variants": others})
        if apply:
            for n in others:
                changed += conn.execute("UPDATE listings SET complex = ? WHERE complex = ?", (canon, n)).rowcount
    if apply:
        conn.commit()
    merged.sort(key=lambda m: -sum(m["variants"].values()))
    return {"groups": len(merged), "listings": changed if apply else sum(sum(m["variants"].values()) for m in merged),
            "examples": merged[:40], "applied": apply}


def canonical(conn: sqlite3.Connection, name: str | None) -> str | None:
    """Новое объявление: если такой ЖК уже есть в базе под другим написанием — берём принятое."""
    if not name:
        return name
    k = _key(name)
    if not k:
        return name
    best, best_n = None, -1
    for r in conn.execute("""SELECT complex, COUNT(*) AS n FROM listings WHERE complex IS NOT NULL AND complex != ''
                             GROUP BY complex"""):   # несколько сотен ЖК — сравниваем ключи в Python (lower() в SQLite не знает кириллицу)
        if r["n"] > best_n and _key(r["complex"]) == k:
            best, best_n = r["complex"], r["n"]
    return best or name


def _rekey(conn: sqlite3.Connection) -> int:
    """Ключи ЖК в правилах и выученных точках — по новому ключу (разрядка, «3 очередь» = «3»)."""
    n = 0
    for table, cols in (("learned_rules", "kind = 'cx_alias' OR kind = 'cx_district'"), ("geo_learned", "key LIKE 'cx:%'")):
        idcol = "id" if table == "learned_rules" else "rowid"
        for r in conn.execute(f"SELECT {idcol} AS i, key FROM {table} WHERE {cols}").fetchall():
            new = geo.complex_key(r["key"].removeprefix("cx:"))
            if not new or f"cx:{new}" == r["key"]:
                continue
            try:
                conn.execute(f"UPDATE {table} SET key = ? WHERE {idcol} = ?", (f"cx:{new}", r["i"]))
                n += 1
            except sqlite3.IntegrityError:   # такой ключ уже есть — старый дубль убираем
                conn.execute(f"DELETE FROM {table} WHERE {idcol} = ?", (r["i"],))
    return n


def maybe_run(conn: sqlite3.Connection) -> dict | None:
    if conn.execute("SELECT 1 FROM state WHERE key = 'cx_norm_v2'").fetchone():
        return None
    res = run(conn, apply=True)
    res["rekeyed"] = _rekey(conn)
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('cx_norm_report', ?)",
                 (json.dumps({**res, "ts": int(time.time())}, ensure_ascii=False),))
    conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('cx_norm_v2', '1')")
    conn.commit()
    return res

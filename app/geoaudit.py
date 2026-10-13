"""Проверка гео-данных (ТЗ «админка-гео», P0.0): отчёт → решение админа в /learn → применение с журналом.

Отчёт ничего не меняет:
  * правила «улица → район» и «ЖК → район» против карты районов: как распределены объекты по контурам под ТОЧНОЙ
    точкой дома; правило, у которого в своём районе < 70 % таких объектов, — кандидат на отключение;
  * ЖК-мусор: общие слова и города, принятые за ЖК («Краснодар», «Москва», «Жилой квартал», «Победа», «Южный»…);
  * «улицы»-подписи полей («Площадь», «Этаж»…) — их ставила нейросеть.
Применение — только выбранное админом; каждое изменение объекта — в журнал listing_edits (src = «чистка правил»),
откат — по объекту (окно объекта → «История») или из админки.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter

from . import geo, learning, parser

SHARE_OK = 0.7       # правило верно, если в его районе по карте ≥ 70 % объектов с точкой дома
MIN_POINTS = 3       # меньше точек — судить не по чему («мало данных»)
# Не ЖК: города, страны, общие слова (стоп-лист — ингест тоже не принимает их за ЖК)
JUNK_CX = {"краснодар", "москва", "россия", "кубань", "жилой квартал", "жилой комплекс", "новостройка", "новостройки",
           "победа", "южный", "центр", "центральный", "город", "дом", "квартал", "мкр", "микрорайон", "сочи", "анапа",
           "краснодарский край", "юг", "север", "восток", "запад"}
SRC = "чистка правил"


def is_junk_complex(name: str | None) -> bool:
    k = geo.complex_key(name)
    return bool(k) and k in JUNK_CX


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


def report(conn: sqlite3.Connection) -> dict:
    rr = rules_report(conn)
    no_place = conn.execute("SELECT COUNT(*) FROM geo_learned WHERE key LIKE 'addr:|%'").fetchone()[0]
    return {"rules": rr, "bad": sum(1 for r in rr if r["status"] == "bad"), "junk": junk_report(conn),
            "labels": label_streets(conn), "points_no_settlement": no_place}


def _log(conn, lid: int, field: str, old, new, now: int) -> None:
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?,?,?,?,?,?,?)",
                 (lid, 0, now, field, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), SRC))


def _redecide(conn, r) -> str | None:
    """Район без отключённого правила: контур под точкой дома, иначе район из текста объявления, иначе прежний."""
    d_map = learning._house_district(conn, r)
    if d_map:
        return d_map
    return geo.find_district_in_text(r["fragment"] or r["description"] or "") or r["district"]


def apply(conn: sqlite3.Connection, rule_ids: list[int], junk: list[str], labels: bool) -> dict:
    from .ingest import _reindex, make_search_text, make_title
    now = int(time.time())
    rep = Counter()
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

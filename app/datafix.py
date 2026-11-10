"""10.11 — разовая проверка объектов по отчёту проверки («портит данные»):

Д1. Аренда «20000₽ + залог», «35 тыс + залог … Арендатор 50/50» стала продажей за 20–35 млн: «+ залог» и комиссия с
    арендатора теперь — аренда (rules.rent_hint), а цена, написанная в рублях («20000₽», «35 тыс»), не умножается на 1000.
Д2. «50/50», «70/30», «100/0» (раздел комиссии) разбирались как этаж — теперь нет (rules._split_fraction).
Город. Адлер, Сочи, Москва, «вид на море» в тексте — объект не в Краснодаре (region.foreign_text) → с сайта.

Правленное админом (admin_fixed, deal_fixed, price_fixed, admin_fields), фид и свои объекты агентов не трогаем.
Журнал — listing_edits с src «проверка 10.11» (откат по объекту — окно объекта → история). Отчёт — state datafix_1110_report.
`python -m app.datafix` — посмотреть, что изменится, без изменений; worker запускает один раз (state datafix_1110)."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time

from . import db, region, rules

SRC = "проверка 10.11"
STATE = "datafix_1110"


def _log(conn, lid: int, field: str, old, new, now: int) -> None:
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?, 0, ?, ?, ?, ?, ?)",
                 (lid, now, field, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), SRC))


def _rent_price(frag: str, price: int | None) -> int | None:
    p = rules.extract_price(frag, "rent", strict=True)
    if p:
        return p
    if price and price >= 1_000_000 and price % 1000 == 0 and 3_000 <= price // 1000 <= 3_000_000:
        return price // 1000
    return price if price and price < 3_000_000 else None


def _split_floor(frag: str, floor: int, floors: int) -> bool:
    """Этаж объекта взят из «50/50» — раздела комиссии."""
    for m in re.finditer(rf"(?<![\d/])(?P<f>{floor})[ \t]*/[ \t]*(?P<t>{floors})(?![\d/])", frag):
        if rules._split_fraction(frag, m, floor, floors):
            return True
    return False


def run(conn: sqlite3.Connection, apply: bool = True) -> dict:
    from .ingest import make_title
    now = int(time.time())
    rep = {"rent": 0, "price_doubt": 0, "floor": 0, "foreign": 0, "examples": [], "ts": now}

    def ex(kind: str, r, text: str) -> None:
        if len(rep["examples"]) < 40:
            rep["examples"].append({"kind": kind, "id": r["id"], "title": r["title"], "what": text})

    rows = conn.execute("""SELECT * FROM listings WHERE source = 'chat' AND COALESCE(admin_fixed, 0) = 0
                           AND COALESCE(owner_user_id, 0) = 0""").fetchall()
    for r in rows:
        frag = r["fragment"] or r["description"] or ""
        if not frag:
            continue
        keys = r.keys()
        fields = {}
        try:
            fields = json.loads(r["admin_fields"] or "{}") if "admin_fields" in keys else {}
        except (TypeError, ValueError):
            pass
        row, changes = dict(r), {}
        # Д1: аренда, ставшая продажей за «миллионы»
        if r["deal"] == "sale" and not r["deal_fixed"] and "deal" not in fields:
            literal = bool(r["price"]) and r["price"] >= 1_000_000 and r["price"] % 1000 == 0 \
                and rules.explicit_rub(frag, r["price"] // 1000)
            if rules.rent_hint(frag) or (literal and (rules.has_rent_words(frag) or rules.has_communal(frag))):
                price = r["price"] if (r["price_fixed"] or "price" in fields) else _rent_price(frag, r["price"])
                changes["deal"] = ("sale", "rent")
                if price != r["price"]:
                    changes["price"] = (r["price"], price)
                rep["rent"] += 1
                ex("rent", r, f"продажа {r['price'] or '—'} ₽ → аренда {price or '—'} ₽/мес")
            elif literal and not r["price_fixed"] and "price" not in fields and not r["price_doubt"]:
                changes["price_doubt"] = (r["price_doubt"], 1)   # «20000₽» у продажи — цена точно не 20 млн
                rep["price_doubt"] += 1
                ex("price", r, f"{r['price']} ₽ — в тексте {r['price'] // 1000} ₽: «Цена уточняется»")
        # Д2: этаж из «50/50»
        if r["floor"] and r["floors"] and "floor" not in fields and _split_floor(frag, r["floor"], r["floors"]):
            nf, nt = rules.extract_floor(frag) if r["type"] != "land" else (None, None)
            if (nf, nt) != (r["floor"], r["floors"]):
                changes["floor"] = (r["floor"], nf)
                changes["floors"] = (r["floors"], nt)
                rep["floor"] += 1
                ex("floor", r, f"этаж {r['floor']}/{r['floors']} → {nf or '—'}/{nt or '—'}")
        # другой город — не снимаем, а район «Не Краснодар» (region.mark_foreign; снятые прошлой версией 'foreign' — обратно)
        fp = region.foreign_place(frag) if (r["is_active"] or r["hidden_reason"] == "foreign") else None
        if fp and r["district"] != fp[0]:
            rep["foreign"] += 1
            ex("foreign", r, f"район «{fp[0]}»" + (f", {fp[1]}" if fp[1] else ""))
            if apply:
                _log(conn, r["id"], "district", r["district"], fp[0], now)
                region.mark_foreign(conn, r["id"], frag)
        if not changes or not apply:
            continue
        for k, (_, v) in changes.items():
            row[k] = v
        if any(k in changes for k in ("deal", "price", "floor", "floors")):
            row["title"] = make_title(row)
            changes["title"] = (r["title"], row["title"])
        if "deal" in changes or "price" in changes:
            row["price_m2"] = None if row["deal"] == "rent" else row.get("price_m2")
            changes["price_m2"] = (r["price_m2"], row["price_m2"])
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE listings SET {sets} WHERE id = ?", (*[v for _, v in changes.values()], r["id"]))
        for k in ("deal", "price", "floor", "floors"):
            if k in changes:
                _log(conn, r["id"], k, changes[k][0], changes[k][1], now)
    if apply:
        conn.commit()
        db.set_state(conn, "datafix_1110_report", json.dumps(rep, ensure_ascii=False))
    return rep


def maybe_run(conn: sqlite3.Connection) -> dict | None:
    if db.get_state(conn, "foreign_nomap_v1") != "1":   # «Не Краснодар» — убрать с карты (раз)
        region.clear_foreign_points(conn)
        db.set_state(conn, "foreign_nomap_v1", "1")
    if db.get_state(conn, STATE) == "1":
        if db.get_state(conn, STATE_FOREIGN) == "1":
            return None
        rep = foreign_again(conn)   # 10.11 вечер: первая версия снимала объекты других городов — вернуть с районом «Не Краснодар»
        db.set_state(conn, STATE_FOREIGN, "1")
        return rep
    rep = run(conn)
    db.set_state(conn, STATE, "1")
    db.set_state(conn, STATE_FOREIGN, "1")
    return rep


STATE_FOREIGN = "datafix_1110_foreign"


def foreign_again(conn: sqlite3.Connection) -> dict:
    """Снятые как 'foreign' и снятые по населённому пункту (hidden_reason 'chat', но есть сообщение из обычного чата) —
    обратно на сайт с районом «Не Краснодар»; активные про другой город — тоже этот район."""
    now = int(time.time())
    rep = {"rent": 0, "price_doubt": 0, "floor": 0, "foreign": 0, "examples": [], "ts": now}
    old = json.loads(db.get_state(conn, "datafix_1110_report") or "null") or {}
    for k in ("rent", "price_doubt", "floor"):
        rep[k] = old.get(k, 0)
    rep["examples"] = [e for e in old.get("examples", []) if e.get("kind") != "foreign"][:30]
    stale = now - __import__("app.config", fromlist=["STALE_DAYS"]).STALE_DAYS * 86400
    rows = conn.execute("""SELECT id, title, fragment, description, district, settlement, hidden_reason, is_active, last_seen, added_at
                           FROM listings WHERE source = 'chat' AND COALESCE(admin_fixed, 0) = 0 AND COALESCE(owner_user_id, 0) = 0
                           AND (is_active = 1 OR hidden_reason = 'foreign' OR (hidden_reason = 'chat' AND settlement IS NOT NULL))""").fetchall()
    blocked = region.blocked_set(conn, fresh=True)
    for r in rows:
        frag = r["fragment"] or r["description"] or ""
        by_place = r["hidden_reason"] == "chat" and region.is_foreign(r["settlement"])
        if r["hidden_reason"] == "chat" and not by_place:
            continue
        if by_place:   # снят только из-за посёлка? Если все сообщения — из выключенных/чужих чатов, оставляем снятым
            ok = conn.execute("""SELECT m.source, m.chat_id, m.chat_name FROM listing_events e JOIN messages m ON m.id = e.message_id
                                 WHERE e.listing_id = ?""", (r["id"],)).fetchall()
            if not any(not region.is_foreign(m["chat_name"]) and (m["source"], m["chat_id"] or "") not in blocked for m in ok):
                continue
        fp = region.foreign_place(frag) or ((region.NOT_KRD, None) if by_place else None)
        if not fp:
            if r["hidden_reason"] == "foreign":   # по новым правилам не другой город — просто вернуть
                conn.execute("UPDATE listings SET hidden_reason = NULL, is_active = CASE WHEN MAX(last_seen, COALESCE(added_at, 0)) >= ? "
                             "THEN 1 ELSE 0 END WHERE id = ?", (stale, r["id"]))
            continue
        fresh = max(r["last_seen"] or 0, r["added_at"] or 0) >= stale
        conn.execute("""UPDATE listings SET district = ?, district_src = 'foreign', extra_districts = '[]',
                        settlement = COALESCE(settlement, ?), hidden_reason = NULL,
                        lat = CASE WHEN geo_status IN ('manual', 'feed') OR district = ? THEN lat ELSE NULL END,
                        lon = CASE WHEN geo_status IN ('manual', 'feed') OR district = ? THEN lon ELSE NULL END,
                        geo_status = CASE WHEN geo_status IN ('manual', 'feed') OR district = ? THEN geo_status ELSE 'manual' END,
                        is_active = CASE WHEN is_active = 1 OR ? THEN 1 ELSE 0 END WHERE id = ?""",
                     (fp[0], fp[1], fp[0], fp[0], fp[0], int(fresh), r["id"]))
        if r["district"] != fp[0]:
            _log(conn, r["id"], "district", r["district"], fp[0], now)
        rep["foreign"] += 1
        if len(rep["examples"]) < 40:
            rep["examples"].append({"kind": "foreign", "id": r["id"], "title": r["title"],
                                    "what": f"район «{fp[0]}»" + (f", {fp[1]}" if fp[1] else "")})
    conn.commit()
    db.set_state(conn, "datafix_1110_report", json.dumps(rep, ensure_ascii=False))
    return rep


if __name__ == "__main__":
    c = db.get()
    res = run(c, apply="--apply" in sys.argv)
    print(json.dumps({k: v for k, v in res.items() if k != "examples"}, ensure_ascii=False))
    for e in res["examples"]:
        print(f"  {e['kind']:8} #{e['id']}: {e['title']} — {e['what']}")

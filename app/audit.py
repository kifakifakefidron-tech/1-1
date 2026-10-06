"""Проверка базы: не теряются ли объекты при отборе по комнатам, ЖК, на карте и т. п.

Запуск на сервере:
    docker compose exec worker python -m app.audit          — только отчёт
    docker compose exec worker python -m app.audit --fix    — отчёт и исправление
"""
from __future__ import annotations

import re
import sys

from . import db, geo, region, rules
from .textnorm import words
from .ingest import _reindex, make_search_text, make_title


def _complex_in(name: str, text: str) -> bool:
    """ЖК назван в тексте: все значимые слова названия есть в тексте (с учётом окончаний), или строкой шапки."""
    tw = f" {words(text)} "
    # «DOGMA PARK (Догма парк)» — годится любое из написаний: снаружи или в скобках
    for variant in [re.sub(r"\(.*?\)", " ", name), *re.findall(r"\((.*?)\)", name)]:
        name_w = re.sub(r"^(?:жк|ж к)\s+", "", words(variant))
        toks = [t for t in name_w.split() if len(t) >= 3 or t.isdigit()]
        if toks and all(f" {t[:max(3, len(t) - 2)]}" in tw for t in toks):
            return True
    pl = geo.place_from_lines(text)
    return bool(pl.get("complex") and geo.complex_key(pl["complex"].name) == geo.complex_key(name))


def _show(title: str, n: int, examples: list[str] | None = None) -> None:
    print(f"  {'✓' if n == 0 else '•'} {title}: {n}")
    for e in (examples or [])[:5]:
        print(f"      – {e[:140]}")


def run(fix: bool = False, conn=None) -> dict:
    own_conn = conn is None
    conn = conn or db.connect()
    out: dict = {}
    act = conn.execute("SELECT * FROM listings WHERE is_active = 1").fetchall()
    print(f"\nОбъектов на сайте: {len(act)}")
    for r in conn.execute("SELECT type, COUNT(*) n FROM listings WHERE is_active = 1 GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {r['type']}: {r['n']}")

    # ── Комнаты ──
    print("\nКОМНАТЫ (кнопки «Студия/1/2/3/4+»)")
    bad_mask, no_rooms, fixable = [], [], []
    for r in act:
        if r["type"] not in ("flat", "new"):
            continue
        want = rules.rooms_mask(r["room_kind"], r["rooms"])
        if r["rooms"] is not None and r["rooms_mask"] != want:
            bad_mask.append(r)
        if r["rooms"] is None:
            no_rooms.append(r)
            text = f"{r['title']}\n{r['fragment'] or r['description'] or ''}"
            rk = rules.extract_room_kind(text)
            if rk:
                fixable.append((r, rk))
    _show("квартир с неверной отметкой для фильтра (не находились по кнопкам)", len(bad_mask),
          [r["title"] for r in bad_mask])
    _show("квартир без числа комнат (не попадают ни в одну кнопку)", len(no_rooms),
          [(r["fragment"] or r["description"] or r["title"]).replace("\n", " ") for r in no_rooms])
    _show("из них число комнат есть в тексте — можно восстановить", len(fixable),
          [f"{r['title']} → {kind} {n}" for r, (kind, n) in fixable])
    out["rooms"] = {"bad_mask": len(bad_mask), "no_rooms": len(no_rooms), "fixable": len(fixable)}

    # ── ЖК ──
    print("\nЖК (фильтр «ЖК»)")
    cx_missing = []
    for r in act:
        if r["complex"] or r["source"] == "feed":
            continue
        rec = geo.find_complex_in_text(r["fragment"] or r["description"] or "")
        if rec:
            cx_missing.append((r, rec))
    _show("объектов, где ЖК назван в тексте, но не проставлен", len(cx_missing),
          [f"{r['title']} → ЖК {rec.name}" for r, rec in cx_missing])
    # ЖК, которого нет ни в одном сообщении этого объекта (раньше приклеивался при склейке дублей
    # или нейросеть «угадывала») — снимаем, чтобы объект не показывался в чужом ЖК
    cx_wrong = []
    for r in act:
        if not r["complex"] or r["source"] != "chat":
            continue
        texts = [r["fragment"] or "", r["description"] or ""] + [m[0] for m in conn.execute(
            """SELECT m.text FROM listing_events e JOIN messages m ON m.id = e.message_id
               WHERE e.listing_id = ? LIMIT 30""", (r["id"],))]
        if not any(_complex_in(r["complex"], t) for t in texts):
            cx_wrong.append(r)
    _show("объектов с ЖК, которого нет в тексте объявлений", len(cx_wrong),
          [f"{r['title']} · ЖК {r['complex']}" for r in cx_wrong])
    out["complex_wrong"] = len(cx_wrong)
    groups = conn.execute("""SELECT cxkey(complex) k, COUNT(DISTINCT complex) v, GROUP_CONCAT(DISTINCT complex) names
                             FROM listings WHERE is_active = 1 AND complex IS NOT NULL GROUP BY k HAVING v > 1""").fetchall()
    print(f"  ✓ разные написания одного ЖК теперь считаются одним ЖК: {len(groups)} групп")
    for g in groups[:5]:
        print(f"      – {g['names']}")
    out["complex_missing"] = len(cx_missing)

    # ── Районы ──
    print("\nРАЙОНЫ")
    bad_d = [r for r in act if r["district"] and geo.canonical_district(r["district"]) != r["district"]]
    _show("районов не из справочника (дубли в фильтре)", len(bad_d), [r["district"] for r in bad_d])
    out["bad_district"] = len(bad_d)
    # Район не проставлен, хотя следует из ЖК, улицы или прямо назван в тексте — при фильтре по району терялся
    no_d = []
    for r in act:
        if r["district"] or r["source"] == "feed":
            continue
        rec = geo.resolve_complex(r["complex"]) if r["complex"] else None
        d = (rec.district if rec else None) or geo.district_by_street(r["street"]) \
            or geo.find_district_in_text(r["fragment"] or r["description"] or "")
        if d:
            no_d.append((r, d))
    _show("объектов без района, хотя район понятен из ЖК/улицы/текста", len(no_d),
          [f"{r['title']} → {d}" for r, d in no_d])
    out["district_missing"] = len(no_d)

    # ── Аренда ──
    print("\nАРЕНДА")
    bad_rent = [r for r in act if r["deal"] == "rent" and r["source"] == "chat"
                and rules.detect_deal(f"{r['fragment'] or ''} {r['description'] or ''}") != "rent"]
    _show("в «аренде», хотя в тексте нет «сдам/аренда»", len(bad_rent),
          [(r["fragment"] or r["title"]).replace("\n", " ") for r in bad_rent])
    out["bad_rent"] = len(bad_rent)

    # ── Без адреса ──
    print("\nБЕЗ АДРЕСА, ЖК И РАЙОНА (такие не показываются)")
    no_place = [r for r in act if not (r["district"] or r["complex"] or r["street"] or r["settlement"])]
    place_fix = []
    for r in no_place:
        pl = geo.place_from_lines(r["fragment"] or r["description"] or "")
        if pl:
            place_fix.append((r, pl))
    _show("всего без адреса", len(no_place))
    _show("из них адрес найден отдельной строкой («Есенина», «АКВАРЕЛИ-3») — восстановим", len(place_fix),
          [f"{r['title']} → {', '.join(str(getattr(v, 'name', v)) for v in pl.values() if v)}" for r, pl in place_fix])
    out["no_place"] = len(no_place)

    # ── Карта ──
    print("\nКАРТА")
    for r in conn.execute("""SELECT geo_status, COUNT(*) n, SUM(lat IS NOT NULL) g FROM listings
                             WHERE is_active = 1 GROUP BY 1 ORDER BY 2 DESC"""):
        label = {"ok": "точный адрес", "approx": "примерно (улица/район)", "pending": "ещё ищем",
                 "none": "адрес не нашёлся", "skip": "в объявлении нет адреса"}.get(r["geo_status"], r["geo_status"])
        print(f"  {label}: {r['n']} (на карте {r['g'] or 0})")
    no_geo = sum(1 for r in act if r["lat"] is None)
    print(f"  Всего на карте: {len(act) - no_geo} из {len(act)}")
    out["no_geo"] = no_geo

    # ── Другие города ──
    print("\nДРУГИЕ ГОРОДА (Сочи, Адлер, Сириус, Туапсе…)")
    chats = conn.execute("SELECT name FROM chats WHERE foreign_place = 1 ORDER BY name").fetchall()
    _show("чатов других городов (не берём)", len(chats), [c["name"] for c in chats])
    known = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    print(f"  названий чатов известно: {known}")

    if fix:
        print("\nИСПРАВЛЯЮ…")
        for r in bad_mask:
            conn.execute("UPDATE listings SET rooms_mask = ? WHERE id = ?",
                         (rules.rooms_mask(r["room_kind"], r["rooms"]), r["id"]))
        for r, (kind, n) in fixable:
            d = {**dict(r), "rooms": n, "room_kind": kind}
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            conn.execute("UPDATE listings SET rooms = ?, room_kind = ?, rooms_mask = ?, title = ?, search_text = ? WHERE id = ?",
                         (n, kind, rules.rooms_mask(kind, n), d["title"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
        for r, rec in cx_missing:
            d = {**dict(r), "complex": rec.name, "district": r["district"] or rec.district}
            d["search_text"] = make_search_text(d)
            conn.execute("""UPDATE listings SET complex = ?, district = ?, search_text = ?,
                            geo_status = CASE WHEN lat IS NULL THEN 'pending' ELSE geo_status END WHERE id = ?""",
                         (d["complex"], d["district"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
        for r in bad_d:
            conn.execute("UPDATE listings SET district = ? WHERE id = ?", (geo.canonical_district(r["district"]), r["id"]))
        for r in cx_wrong:
            rec = geo.resolve_complex(r["complex"])
            # район, взятый из этого ЖК, тоже снимаем — если он не назван в тексте сам
            drop_d = rec and rec.district == r["district"] and not geo.find_district_in_text(r["fragment"] or "")
            d = {**dict(r), "complex": None, "district": None if drop_d else r["district"]}
            d["search_text"] = make_search_text(d)
            conn.execute("UPDATE listings SET complex = NULL, district = ?, search_text = ?, geo_status = 'pending' "
                         "WHERE id = ?", (d["district"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
        for r, d in no_d:
            st = make_search_text({**dict(r), "district": d})
            conn.execute("UPDATE listings SET district = ?, search_text = ? WHERE id = ?", (d, st, r["id"]))
            _reindex(conn, r["id"], st)
        for r, pl in place_fix:
            cx = pl.get("complex")
            d = {**dict(r), "complex": cx.name if cx else None, "street": pl.get("street"), "house": pl.get("house"),
                 "settlement": pl.get("settlement")}
            d["district"] = pl.get("district") or (cx.district if cx else None) or geo.district_by_street(d["street"])
            d["search_text"] = make_search_text(d)
            conn.execute("""UPDATE listings SET complex = ?, street = ?, house = ?, settlement = ?, district = ?,
                            search_text = ?, geo_status = 'pending' WHERE id = ?""",
                         (d["complex"], d["street"], d["house"], d["settlement"], d["district"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
        for r in bad_rent:
            conn.execute("UPDATE listings SET deal = 'sale', title = ? WHERE id = ?",
                         (make_title({**dict(r), "deal": "sale"}), r["id"]))
        conn.execute("UPDATE listings SET geo_status = 'pending' WHERE is_active = 1 AND lat IS NULL AND geo_status IN ('none','skip')")
        conn.commit()
        print("  других городов скрыто:", region.hide_foreign(conn))
        print("  готово. Точки на карте для оставшихся найдутся за ближайшие часы (≈1 объект в секунду).")
    if own_conn:
        conn.close()
    return out


if __name__ == "__main__":
    run(fix="--fix" in sys.argv)

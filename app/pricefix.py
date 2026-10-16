"""Проверка цены (акт приёмки 14.10, Б1–Б2).

Б1. Риелторы пишут цену в тысячах («Цена 5400», «11 200р»), нейросеть иногда так и оставляла — в базе оказывались
квартиры за 5 400 ₽ и двушки за 1,6 млрд. Правило: цена должна попадать в разумный коридор для типа объекта
(и по ₽/м², если площадь известна). Не попадает — пробуем «это тысячи» (×1000) или «лишние нули» (÷1000);
подошёл ровно один вариант — берём его, иначе цена «под вопросом» (listings.price_doubt = 1): такие объекты
не участвуют в сортировках по цене, «Горячем», «от … ₽» на карте и ценовых фильтрах, а в карточке — плашка.

Б2. Склейка повторов не меняет цену, если пост — другого типа/комнатности или цена отличается больше чем на 25 %
(ingest.save_object). Разово: цена карточки сверяется с ценой в её же тексте; расходится больше чем на 25 % —
берём цену из текста (если она правдоподобна). Каждая замена — в журнал listing_edits (src «проверка цены»), откат
по объекту.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter

from . import db, rules

SRC = "проверка цены"
MERGE_MAX_JUMP = 0.25   # склейка: новая цена дальше 25 % от прежней — не перебивает (скорее всего, другой объект)

# Абсолютные пределы (₽): продажа / аренда в месяц
ABS = {
    "sale": {"flat": (500_000, 300_000_000), "new": (500_000, 300_000_000), "room": (150_000, 30_000_000),
             "house": (500_000, 500_000_000), "land": (100_000, 3_000_000_000), "commercial": (300_000, 3_000_000_000)},
    "rent": {"flat": (5_000, 500_000), "new": (5_000, 500_000), "room": (3_000, 150_000),
             "house": (10_000, 1_500_000), "land": (3_000, 3_000_000), "commercial": (5_000, 10_000_000)},
}
# Коридор ₽ за м² (продажа) — где площадь известна
PER_M2 = {"flat": (30_000, 800_000), "new": (30_000, 800_000), "room": (20_000, 800_000),
          "house": (8_000, 800_000), "commercial": (5_000, 1_500_000)}


def plausible(price: int | None, deal: str | None, type_: str | None, area: float | None) -> bool:
    if not price:
        return False
    lo, hi = ABS.get(deal or "sale", ABS["sale"]).get(type_ or "flat", ABS.get(deal or "sale", ABS["sale"])["flat"])
    if not lo <= price <= hi:
        return False
    if (deal or "sale") == "sale" and area and area > 8 and type_ in PER_M2:
        a, b = PER_M2[type_]
        return a <= price / area <= b
    return True


def check(price: int | None, deal: str | None, type_: str | None, area: float | None) -> tuple[int | None, bool]:
    """(цена, под вопросом?): правдоподобная — как есть; иначе единственный правдоподобный вариант ×1000 / ÷1000."""
    if not price:
        return price, False
    if plausible(price, deal, type_, area):
        return price, False
    variants = {v for v in (price * 1000, price * 1_000_000 if price < 1000 else 0, price // 1000 if price >= 1_000_000 else 0)
                if v and plausible(v, deal, type_, area)}
    if len(variants) == 1:
        return variants.pop(), False
    return price, True


def text_price(description: str | None, deal: str | None) -> int | None:
    return rules.extract_price(description or "", deal or "sale", strict=True) if description else None


def _log(conn, lid: int, old, new, now: int) -> None:
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src) VALUES (?,?,?,?,?,?,?)",
                 (lid, 0, now, "price", json.dumps(old), json.dumps(new), SRC))


def run(conn: sqlite3.Connection, apply: bool = True, examples: int = 20) -> dict:
    """Все объекты: масштаб цены и цена из своего текста. apply=False — только отчёт."""
    from .ingest import _price_m2
    now = int(time.time())
    rep: Counter = Counter()
    shown: list[dict] = []
    for r in conn.execute("""SELECT id, title, type, deal, area, price, description, price_fixed, owner_user_id, source
                             FROM listings WHERE price IS NOT NULL""").fetchall():
        if r["price_fixed"] or r["owner_user_id"] or r["source"] in ("own", "feed"):
            continue
        new, why = r["price"], None
        tp = text_price(r["description"], r["deal"])
        if tp:
            tp2, tp_doubt = check(tp, r["deal"], r["type"], r["area"])
            if not tp_doubt and abs(tp2 - r["price"]) / r["price"] > MERGE_MAX_JUMP:
                new, why = tp2, "text"          # Б2: цена перебита чужим постом — возвращаем цену своего текста
        if why is None:
            fixed, doubt = check(r["price"], r["deal"], r["type"], r["area"])
            if fixed != r["price"]:
                new, why = fixed, "scale"       # Б1: тысячи / лишние нули
            elif doubt:
                rep["doubt"] += 1
                if apply:
                    conn.execute("UPDATE listings SET price_doubt = 1 WHERE id = ?", (r["id"],))
                continue
        if apply:
            conn.execute("UPDATE listings SET price_doubt = 0 WHERE id = ? AND price_doubt = 1", (r["id"],))
        if why is None:
            continue
        rep[why] += 1
        if len(shown) < examples:
            shown.append({"id": r["id"], "title": r["title"], "before": r["price"], "after": new, "why": why})
        if apply:
            conn.execute("UPDATE listings SET price = ?, price_m2 = ?, price_doubt = 0 WHERE id = ?",
                         (new, _price_m2(new, r["area"]), r["id"]))
            _log(conn, r["id"], r["price"], new, now)
    if apply:
        conn.commit()
    return {"scale": rep["scale"], "text": rep["text"], "doubt": rep["doubt"], "examples": shown, "applied": apply,
            "ts": now}


def maybe_run(conn: sqlite3.Connection) -> dict | None:
    """Один раз (state price_fix_v1) — отчёт в state price_fix_report (админка «Обзор»)."""
    if db.get_state(conn, "price_fix_v1"):
        return None
    res = run(conn, apply=True)
    db.set_state(conn, "price_fix_report", json.dumps(res, ensure_ascii=False))
    db.set_state(conn, "price_fix_v1", "1")
    conn.commit()
    return res

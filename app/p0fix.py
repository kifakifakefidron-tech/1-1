"""Разовые проверки и исправления этапа P0 (ТЗ v2). Сначала — отчёт, потом --apply.

  python -m app.p0fix phones            — у скольких объектов в описании остался номер, примеры до/после
  python -m app.p0fix phones --apply    — вырезать номера из описаний, добавить их в номера объекта (роль «в тексте»)
  python -m app.p0fix prices            — где цена в карточке и цена в тексте расходятся больше чем на 3 %

На сервере: docker compose exec web python -m app.p0fix phones
"""
from __future__ import annotations

import json
import sqlite3
import sys

from . import db, parser, rules

PRICE_GAP = 0.03


def text_price(description: str | None, deal: str) -> int | None:
    """Цена, которая написана в тексте объявления (как её понял бы разбор)."""
    return rules.extract_price(description or "", deal) if description else None


def price_gap(price: int | None, description: str | None, deal: str) -> int | None:
    """Цена из текста, если она отличается от цены карточки больше чем на 3 %; иначе None."""
    tp = text_price(description, deal)
    if not tp or not price:
        return None
    return tp if abs(tp - price) / price > PRICE_GAP else None


def phones_report(conn: sqlite3.Connection, apply: bool = False, examples: int = 15) -> dict:
    rows = conn.execute("SELECT id, description, phones, phone_roles FROM listings WHERE description IS NOT NULL").fetchall()
    hit, shown = 0, []
    for r in rows:
        found = rules.extract_phones(r["description"])
        if not found:
            continue
        hit += 1
        new = parser.strip_phones(r["description"])
        if len(shown) < examples:
            line = next((ln for ln in r["description"].splitlines() if rules.find_phones(ln)), "")
            a = max(0, rules.find_phones(line)[0][0] - 50)   # кусок вокруг номера
            piece = line[a:a + 130]
            shown.append({"id": r["id"], "before": piece, "after": parser.strip_phones(piece), "phones": found})
        if apply:
            phones = json.loads(r["phones"] or "[]")
            roles = json.loads(r["phone_roles"] or "{}")
            for p in found:
                if p not in phones:
                    phones.append(p)
                roles[p] = "text"
            conn.execute("UPDATE listings SET description = ?, phones = ?, phone_roles = ? WHERE id = ?",
                         (new, json.dumps(phones, ensure_ascii=False), json.dumps(roles, ensure_ascii=False), r["id"]))
    if apply:
        conn.commit()
    return {"listings": len(rows), "with_phone_in_text": hit, "applied": apply, "examples": shown}


def prices_report(conn: sqlite3.Connection, examples: int = 30) -> dict:
    out = []
    n = 0
    for r in conn.execute("""SELECT id, title, complex, price, deal, description FROM listings
                             WHERE is_active = 1 AND price IS NOT NULL ORDER BY last_seen DESC"""):
        tp = price_gap(r["price"], r["description"], r["deal"])
        if tp:
            n += 1
            if len(out) < examples:
                out.append({"id": r["id"], "title": (f"ЖК {r['complex']}, " if r["complex"] else "") + r["title"],
                            "card": r["price"], "text": tp})
    return {"mismatch": n, "examples": out}


def main(argv: list[str]) -> None:
    conn = db.get()
    what = argv[0] if argv else "phones"
    if what == "phones":
        res = phones_report(conn, apply="--apply" in argv)
        print(f"Объектов с описанием: {res['listings']}; с номером в тексте: {res['with_phone_in_text']}"
              f"{' — ИСПРАВЛЕНО' if res['applied'] else ' (отчёт, ничего не менялось; чтобы исправить — добавьте --apply)'}")
        for e in res["examples"]:
            print(f"  #{e['id']}  {e['phones']}\n     было: {e['before']}\n    стало: {e['after']}")
    elif what == "prices":
        res = prices_report(conn)
        print(f"Цена карточки ≠ цене в тексте (больше 3 %): {res['mismatch']}")
        for e in res["examples"]:
            money = lambda v: f"{v:,}".replace(",", " ")   # noqa: E731
            print(f"  #{e['id']}  {e['title'][:60]} — в карточке {money(e['card'])} ₽, в тексте {money(e['text'])} ₽")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

"""24.10j (Артём: «одинаковый текст, другая цена — создаётся новое объявление»): разовая уборка уже созданных дублей.

Карточки из чатов с тем же текстом, теми же номерами В ТЕКСТЕ и тем же местом/комнатами, где поменялась только цена
(dedupe.text_twin), склеиваются в самую раннюю: избранное, клиенты, подборки, планер и история повторов переезжают туда
(ownmatch._move), цена и текст — из самого свежего сообщения, остальные карточки скрываются (hidden_reason 'dup_text').
Запуск — один раз из worker (state dup_text_v1), отчёт — state dup_text_report. Проверка без изменений: `python -m app.dupfix`."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict

from . import db, geo
from .dedupe import _close, text_twin
from .ownmatch import _move
from .textnorm import words


def _place(r) -> str | None:
    if r["complex"]:
        return "cx:" + (geo.complex_key(r["complex"]) or "")
    if r["street"]:
        return "st:" + words(r["street"]) + ":" + words(r["house"] or "")
    return None


def _twins(a, b) -> bool:
    for k in ("rooms", "floor", "floors"):
        if a[k] is not None and b[k] is not None and a[k] != b[k]:
            return False
    if _close(a["area"], b["area"], 0.03, 1.5) is False or _close(a["land"], b["land"], 0.04, 0.3) is False:
        return False
    if a["house"] and b["house"] and words(a["house"]) != words(b["house"]):
        return False
    if text_twin(a["fragment"], b["fragment"]):
        return True
    return bool(_close(a["price"], b["price"], 0.10, 0)) and text_twin(a["fragment"], b["fragment"], short_ok=True)


def find_main(conn: sqlite3.Connection, row) -> int | None:
    """24.10l: копия, склеенная до появления merged_into, — найти её основную карточку (тот же текст и место, на сайте)."""
    p = _place(row)
    if not p:
        return None
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND id != ? AND deal = ? AND rooms IS ? AND fragment IS NOT NULL
                             AND (complex = ? OR street = ?) ORDER BY first_seen LIMIT 200""",
                          (row["id"], row["deal"], row["rooms"], row["complex"], row["street"])).fetchall():
        if _place(r) == p and _twins(r, row):
            return r["id"]
    return None


def run(conn: sqlite3.Connection, apply: bool = False) -> dict:
    rows = conn.execute("""SELECT * FROM listings WHERE source = 'chat' AND is_active = 1 AND hidden_reason IS NULL
                           AND COALESCE(owner_user_id, 0) = 0 AND fragment IS NOT NULL""").fetchall()
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        p = _place(r)
        if p:
            groups[(r["type"] if r["type"] != "new" else "flat", r["deal"], r["rooms"], p)].append(r)
    merged, examples = 0, []
    for items in groups.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda r: (r["first_seen"] or 0, r["id"]))
        used = set()
        for i, a in enumerate(items):
            if a["id"] in used:
                continue
            twins = [b for b in items[i + 1:] if b["id"] not in used and _twins(a, b)]
            if not twins:
                continue
            used.update(b["id"] for b in twins)
            merged += len(twins)
            if len(examples) < 40:
                examples.append({"keep": a["id"], "merged": [b["id"] for b in twins], "title": a["title"]})
            if not apply:
                continue
            newest = max([a, *twins], key=lambda r: (r["last_seen"] or 0, r["id"]))
            for b in twins:
                _move(conn, b["id"], a["id"])
                conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'dup_text' WHERE id = ?", (b["id"],))
            fixed = "price_fixed" in a.keys() and a["price_fixed"]   # цену поправил админ — не трогаем
            if newest["id"] != a["id"] and not fixed:
                phones = sorted(set(json.loads(a["phones"] or "[]")) | set(json.loads(newest["phones"] or "[]")))
                conn.execute("""UPDATE listings SET price = ?, price_m2 = ?, fragment = ?, description = ?, phones = ? WHERE id = ?""",
                             (newest["price"], newest["price_m2"], newest["fragment"], newest["description"],
                              json.dumps(phones, ensure_ascii=False), a["id"]))
    if apply:
        conn.commit()
    return {"merged": merged, "examples": examples, "applied": apply}


def maybe_run(conn: sqlite3.Connection) -> dict | None:
    if db.get_state(conn, "dup_text_v1"):
        return None
    res = run(conn, apply=True)
    db.set_state(conn, "dup_text_report", json.dumps({**res, "ts": int(time.time())}, ensure_ascii=False))
    db.set_state(conn, "dup_text_v1", "1")
    conn.commit()
    return res


if __name__ == "__main__":
    r = run(db.get(), apply=False)
    print(f"Найдено дублей (тот же текст и номера, другая цена): {r['merged']}")
    for e in r["examples"]:
        print(f"  оставим {e['keep']}, склеим {e['merged']} — {e['title']}")

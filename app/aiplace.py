"""16.11 (Артём: «нейросеть работает — хорошая идея сверки и выбора»): нейросеть ВЫБИРАЕТ место из списков.

Объявление без района (и без посёлка), правила ничего не нашли → нейросети даём текст (без телефонов и имён) и
короткие списки: улицы из справочника OSM, похожие на слова текста; ЖК из справочника, похожие на слова текста;
все районы. Она выбирает только из списков и обязана привести слова из текста («quote»), на которые опирается.
Сервис проверяет: выбранное есть в списке, цитата есть в тексте, номер дома есть в тексте. Заполняются только
пустые поля, district_src='ai' — такие решения попадают в выборочную проверку «Проверить решения сервиса».
Каждое объявление спрашиваем один раз (listings.ai_checked). Без ключа DeepSeek — ничего не делает.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from . import db, geo, llm, osmaddr
from .parser import mask_names
from .rules import mask_phones
from .textnorm import words

log = logging.getLogger(__name__)

PER_CYCLE = 5        # объявлений за цикл worker
BUDGET_S = 40        # и не дольше этого
STEM = 5             # «Ставропольской» ~ «Ставропольская»: сравниваем первые буквы слова

SYSTEM = """Ты помогаешь найти место объекта недвижимости в Краснодаре по тексту объявления агента.
Выбирай ТОЛЬКО из присланных списков — точное написание из списка. Если в тексте место прямо не названо — null.
НЕ угадывай по знанию города: район можно выбрать, только если он (или его народное название) написан в тексте.
Ответ — JSON: {"district": строка|null, "street": строка|null, "house": строка|null, "complex": строка|null,
"quote": "слова из текста, по которым ты выбрал (дословно)"}. house — номер дома, только если он написан в тексте."""


def _stems(text: str) -> set[str]:
    return {w[:STEM] for w in words(text).split() if len(w) >= STEM and not w.isdigit()}


def candidates(conn: sqlite3.Connection, text: str) -> dict:
    st = _stems(text)
    streets = []
    if osmaddr.loaded(conn):
        idx = osmaddr.index(conn)
        for k, s in idx["streets"].items():
            if "" in s["pts"] and any(w[:STEM] in st for w in k.split() if len(w) >= STEM):
                streets.append(s["name"])
    cxs = list(dict.fromkeys(cx.name for key, cx in geo._complexes()
                             if any(w[:STEM] in st for w in key.split() if len(w) >= STEM)))
    return {"streets": sorted(streets)[:25], "complexes": cxs[:25], "districts": geo.all_district_names()}


def _in(text_w: str, value: str | None) -> bool:
    v = words(value or "")
    return bool(v) and f" {v} " in f" {text_w} "


def ask(conn: sqlite3.Connection, text: str) -> dict | None:
    """Спросить нейросеть (текст уже без телефонов и имён) и проверить ответ. None — нечего ставить."""
    c = candidates(conn, text)
    safe = mask_names(mask_phones(text, "[телефон]"), "[имя]")
    user = (f"Текст объявления:\n{safe[:3000]}\n\nУлицы: {json.dumps(c['streets'], ensure_ascii=False)}\n"
            f"ЖК: {json.dumps(c['complexes'], ensure_ascii=False)}\nРайоны: {json.dumps(c['districts'], ensure_ascii=False)}")
    db.flush(conn)
    a = llm.ask_json(SYSTEM, user, max_tokens=300)
    tw = words(text)
    quote = words(str(a.get("quote") or ""))
    if not quote or f" {quote} " not in f" {tw} ":
        return None   # не смогла показать слова из текста — значит угадывала
    out = {}
    if a.get("street") in c["streets"]:
        out["street"] = a["street"]
        h = str(a.get("house") or "").strip()
        if h and _in(tw, h):
            out["house"] = h
    if a.get("complex") in c["complexes"]:
        out["complex"] = a["complex"]
    if a.get("district") in c["districts"]:
        out["district"] = a["district"]
    return out or None


def run(conn: sqlite3.Connection) -> dict:
    if not llm.available():
        return {}
    from .ingest import _reindex, make_search_text, make_title
    t0, done, asked = time.time(), 0, 0
    rows = conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND source = 'chat' AND admin_fixed = 0
                           AND district IS NULL AND settlement IS NULL AND ai_checked IS NULL
                           AND fragment IS NOT NULL ORDER BY last_seen DESC LIMIT ?""", (PER_CYCLE,)).fetchall()
    for r in rows:
        if time.time() - t0 > BUDGET_S:
            break
        asked += 1
        try:
            got = ask(conn, r["fragment"]) or {}
        except llm.LLMError as e:
            log.info("Нейросеть (место) не ответила: %s", e)
            break
        d = dict(r)
        upd = {k: v for k, v in got.items() if not d.get(k)}
        if "house" in upd and not (d.get("street") or upd.get("street")):
            upd.pop("house")
        now = int(time.time())
        if upd:
            for f, v in upd.items():
                conn.execute("""INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src)
                                VALUES (?, 0, ?, ?, ?, ?, 'нейросеть')""",
                             (r["id"], now, f, json.dumps(d.get(f), ensure_ascii=False), json.dumps(v, ensure_ascii=False)))
            d.update(upd)
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            sets = {**upd, "title": d["title"], "search_text": d["search_text"]}
            if upd.get("district"):
                sets["district_src"] = "ai"
            if (upd.get("street") or upd.get("complex")) and r["geo_status"] not in ("manual", "learned", "feed"):
                sets["geo_status"] = "pending"
            conn.execute(f"UPDATE listings SET {', '.join(k + ' = ?' for k in sets)}, ai_checked = 1 WHERE id = ?",
                         (*sets.values(), r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            done += 1
        else:
            conn.execute("UPDATE listings SET ai_checked = 1 WHERE id = ?", (r["id"],))
        conn.commit()
    return {"asked": asked, "filled": done} if asked else {}

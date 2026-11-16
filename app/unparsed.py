"""16.11 (Артём, п. 8: «что сервис не понял — чтобы я объяснял»): строки объявлений, похожие на адрес, которые
сервис не смог прочитать. Берём объявления на сайте без района (и без посёлка) и без точного дома; из их текста —
строки со словами-метками места («ул.», «район», «рядом с», «напротив», «угол», «ост.»…), а у объявлений, которых
вообще не видно на сайте (нет ни района, ни ЖК, ни улицы), — первые строки. Одинаковые строки (цифры не важны)
собираются вместе: «N объявлений». Админ объясняет фразу один раз (app/placehints.learn_phrase) — и она
подставляется всем объявлениям с этими словами, сейчас и потом.
"""
from __future__ import annotations

import re
import sqlite3
import time

from . import placehints
from .textnorm import words

_MARK = re.compile(r"(?:^|\s)(?:ул|улица|улице|пр|пр кт|проспект|пер|переулок|проезд|бульвар|шоссе|р н|район|районе|мкр|"
                   r"микрорайон|рядом|возле|около|напротив|недалеко|остановк\w*|ост|угол|углу|пересечени\w*|перекрест\w*|"
                   r"ориентир|адрес|жк|кп|пос|поселок|ст ца|станица|х|хутор|снт|днт)(?:\s|$)")
_SKIP = re.compile(r"(?:цена|стоимость|млн|тыс|руб|₽|этаж|площад|м2|кв м|комн|студи|ремонт|мебел|техник|ипотек|"
                   r"дкп|задат|обремен|собственн|торг|звоните|пишите|whatsapp|ватсап|телеграм|тел)")
_PHONE = re.compile(r"(?:\+?\d[\s()-]*){10,}")
_cache: dict = {"t": 0}


def _lines(text: str, hidden: bool) -> list[str]:
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip(" \t•-–—*·:;,.!")
        if not 4 <= len(line) <= 120 or _PHONE.search(line):
            continue
        w = words(line)
        if len(w) < 4 or not re.search(r"[а-я]{3}", w):
            continue
        if _SKIP.search(w) and (len(line) > 60 or not re.search(r"(?:^|\s)(?:ул|улица|район|р н|мкр|рядом|возле|напротив|угол)(?:\s|$)", w)):
            continue
        if _MARK.search(w) or (hidden and len(out) < 2):
            out.append(line)
    return out[:4]


def report(conn: sqlite3.Connection, limit: int = 120, fresh: bool = False) -> list[dict]:
    """[{key, line, n, hidden, ids}] — самые частые первыми. Кэш 10 минут."""
    placehints.phrases(conn)
    sig = placehints._phr.get("sig")   # объяснили новую фразу — отчёт пересчитывается сразу
    if not fresh and time.time() - _cache["t"] < 600 and _cache.get("sig") == sig:
        return _cache["items"][:limit]
    taught = list(placehints.phrases(conn))
    groups: dict[str, dict] = {}
    for r in conn.execute("""SELECT id, fragment, district, complex, street, settlement FROM listings
                             WHERE is_active = 1 AND source = 'chat' AND admin_fixed = 0 AND fragment IS NOT NULL
                             AND district IS NULL AND settlement IS NULL
                             ORDER BY last_seen DESC LIMIT 4000""").fetchall():
        hidden = not (r["complex"] or r["street"])
        for line in _lines(r["fragment"], hidden):
            w = words(line)
            if any(f" {t} " in f" {w} " for t in taught):
                continue
            key = re.sub(r"\d+", "#", w)
            g = groups.setdefault(key, {"key": key, "line": line, "n": 0, "hidden": 0, "ids": []})
            if r["id"] in g["ids"]:
                continue
            g["n"] += 1
            g["hidden"] += int(hidden)
            if len(g["ids"]) < 8:
                g["ids"].append(r["id"])
    items = sorted(groups.values(), key=lambda g: (-g["n"], -g["hidden"]))
    _cache.update(t=time.time(), sig=sig, items=items)
    return items[:limit]


def total(conn: sqlite3.Connection) -> int:
    return len(report(conn, limit=100000))

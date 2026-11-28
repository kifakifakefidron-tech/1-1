"""Склейка дублей: одно и то же объявление от разных агентов, из разных чатов
и с разных номеров Wappi должно стать ОДНИМ объектом.

Что было не так в старой системе:
  * сравнение «похожих» шло только внутри одного подключённого номера;
  * без адреса объявление вообще не проверялось на дубль;
  * разные телефоны агентов давали разные «отпечатки».

Здесь сравниваем со всей базой по характеристикам объекта. Телефон помогает,
но не обязателен.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import sqlite3

from . import rules
from .parser import ParsedObject
from .textnorm import norm, words

TYPE_GROUPS = {"flat": ("flat", "new"), "new": ("flat", "new"), "room": ("room",),
               "house": ("house",), "land": ("land",), "commercial": ("commercial",)}
LOOKBACK_DAYS = 120
PRICE_REVISION_MATCH = "тот же текст с новой ценой"


def fragment_hash(fragment: str) -> str:
    w = words(fragment)
    return hashlib.sha1(w.encode()).hexdigest()[:20]


def _close(a: float | None, b: float | None, rel: float, abs_: float) -> bool | None:
    """True/False — совпало/различается; None — нечего сравнивать."""
    if a is None or b is None:
        return None
    return abs(a - b) <= max(abs_, rel * max(a, b))


def _eq(a, b) -> bool | None:
    if a is None or b is None or a == "" or b == "":
        return None
    return words(str(a)) == words(str(b))


def _house_eq(a: str | None, b: str | None) -> bool | None:
    if not a or not b:
        return None
    na = re.sub(r"[^0-9а-яa-z/]", "", a.lower())
    nb = re.sub(r"[^0-9а-яa-z/]", "", b.lower())
    return na == nb


_PRICE_WORDS = {"млн", "тыс", "т", "р", "руб", "рублей", "тр", "миллиона", "миллионов", "миллион", "тысяч"}
_PRICE_BEFORE = {"цена", "цены", "стоимость", "за"}
_PRICE_NOTICES = {"снижение цены", "цена снижена", "новая цена"}
_PRICE_LINE_LABELS = {"", "цена", "стоимость", "новая цена"}
_GROUPED_NUMBER = re.compile(r"(?<!\d)\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)")


@functools.lru_cache(maxsize=8192)
def _phones_in(text: str) -> tuple:
    """26.10 (нагрузка): номера в тексте — один раз на текст (поиск идёт со сдвигом по символу и дорог)."""
    from .rules import find_phones
    return tuple(find_phones(text))


@functools.lru_cache(maxsize=8192)
def _words_list(text: str) -> tuple:
    return tuple(words(text).split())


def has_price_notice(text: str) -> bool:
    return any(words(line) in _PRICE_NOTICES for line in text.splitlines())


@functools.lru_cache(maxsize=8192)
def strip_price_notices(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if words(line) not in _PRICE_NOTICES)


def text_twin(a: str | None, b: str | None, short_ok: bool = False) -> bool:
    """Фрагменты совпадают слово в слово, кроме нескольких чисел (и слов вроде «млн»): цену поменяли, остальное — то же.
    short_ok — короткий текст («ЖК Абрикосово, евро-2, 6 млн») тоже, если вызывающий проверил место и цену."""
    import difflib
    # 24.10j (Артём): дубль — если совпадают номера В ТЕКСТЕ (кто переслал — неважно). Номера — часть текста: другой номер в тексте —
    # другой агент, отдельная карточка; номеров нет в обоих — совпадает сам текст
    from .phonegroups import canon_set   # 25.10: номера одного агента (phonegroups) — один номер
    a, b = strip_price_notices(a or ""), strip_price_notices(b or "")
    la, lb = len(_words_list(a or "")), len(_words_list(b or ""))
    if min(la, lb) < (4 if short_ok else 12) or abs(la - lb) > 8:   # 26.10: коротко или длина сильно разная — дальше не считаем
        return False
    pa, pb = _phones_in(a or ""), _phones_in(b or "")
    if canon_set(p[2] for p in pa) != canon_set(p[2] for p in pb):   # (начало, конец, номер)
        return False

    def cut(t, spans):   # номера уже сравнили — из слов их убираем (другой номер того же агента — не «другие цифры»)
        for s0, e0, _ in sorted(spans, reverse=True):
            t = t[:s0] + " " + t[e0:]
        return _GROUPED_NUMBER.sub(lambda m: "".join(m[0].split()), t)
    ta, tb = words(cut(a or "", pa)).split(), words(cut(b or "", pb)).split()
    if min(len(ta), len(tb)) < (4 if short_ok else 12):   # короткий текст — слишком легко совпасть случайно
        return False
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    changed = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op != "equal":
            part = ta[i1:i2] + tb[j1:j2]
            # изменённое число должно быть ЦЕНОЙ: рядом «млн / тыс / руб» или перед ним «цена» (площадь, этаж — другое объявление)
            nxt = {ta[i2] if i2 < len(ta) else "", tb[j2] if j2 < len(tb) else ""}
            prv = {ta[i1 - 1] if i1 else "", tb[j1 - 1] if j1 else ""}
            if not (nxt & _PRICE_WORDS or prv & _PRICE_BEFORE or set(part) & _PRICE_WORDS):
                return False
            changed += part
    if not changed:
        return True
    if len(changed) > 6 or sm.ratio() < (0.7 if short_ok else 0.9):
        return False
    return all(t.isdigit() or t in _PRICE_WORDS for t in changed)


@functools.lru_cache(maxsize=8192)
def price_revision_key(fragment: str, deal: str, price: int | None) -> tuple[str, str] | None:
    """Сопоставимая подпись, если подтверждена ровно одна цена объекта."""
    text = rules.normalize_digits(strip_price_notices(fragment))
    span = rules.explicit_price_span(text, deal)
    if not span or span[0] != price:
        return None
    _, start, end = span
    before, after = text[:start], text[end:]
    leading, _, label = before.rpartition("\n")
    tail, _, following = after.partition("\n")
    if words(label) in _PRICE_LINE_LABELS and not words(tail):
        body = norm(f"{leading}\n{following}")
        return ("line", body) if len(words(body).split()) >= 12 else None
    if len(words(f"{before} {after}").split()) >= 12:
        return norm(before), norm(after)
    return None


def price_revision_time(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    """Первая публикация текущей версии цены. Репосты старого текста не меняют её дату."""
    ts = conn.execute("""SELECT MIN(ts) FROM listing_events WHERE listing_id=? AND fragment_hash=? AND price=?""",
                      (row["id"], fragment_hash(row["fragment"] or ""), row["price"])).fetchone()[0]
    return ts if ts is not None else (row["description_ts"] or row["first_seen"])


def is_price_revision(fragment: str, price: int | None, row: sqlite3.Row) -> bool:
    if not price or price == row["price"] or row["price_doubt"]:
        return False
    signature = price_revision_key(fragment, row["deal"], price)
    return bool(signature and signature == price_revision_key(row["fragment"] or "", row["deal"], row["price"]))


def same_object(o: ParsedObject, row: sqlite3.Row) -> tuple[bool, str]:
    """Решение «это тот же объект» + причина (для отладки)."""
    from . import offers

    rooms = None if o.rooms is None or row["rooms"] is None else o.rooms == row["rooms"]
    floor = None if o.floor is None or row["floor"] is None else o.floor == row["floor"]
    area = _close(o.area, row["area"], 0, 2)
    land = _close(o.land, row["land"], 0.04, 0.3)
    street_a, street_b = offers.street_key(o.street), offers.street_key(row["street"])
    house_a, house_b = offers.house_key(o.house), offers.house_key(row["house"])
    street = street_a == street_b if street_a and street_b else None
    house = house_a == house_b if house_a and house_b else None
    cx = _eq(o.complex, row["complex"])
    district = _eq(o.district, row["district"])
    settlement = _eq(o.settlement, row["settlement"])
    from .phonegroups import canon_set
    phones_row = canon_set(json.loads(row["phones"] or "[]"))
    phones_new = canon_set(o.phones)
    if phones_row != phones_new and phones_row.isdisjoint(phones_new):
        return False, "другой агент"

    for field, result in (("комнаты", rooms), ("этаж", floor), ("площадь", area), ("участок", land),
                          ("ЖК", cx), ("район", district), ("улица", street),
                          ("дом", house if street else None), ("населённый пункт", settlement)):
        if result is False:
            return False, f"разные: {field}"

    if (phones_row & phones_new and o.type in ("flat", "new", "room") and floor
            and ((street and house and area) or (cx and rooms and o.area and o.area == row["area"]))
            and (phones_row == phones_new or (row["source"] == "chat" and not row["owner_user_id"]))):
        if is_price_revision(o.fragment, o.price, row):
            return True, PRICE_REVISION_MATCH
        if (street and house and o.price == row["price"] and not row["price_doubt"]
                and (has_price_notice(o.fragment) or has_price_notice(row["fragment"] or ""))):
            key = price_revision_key(o.fragment, o.deal, o.price)
            if key is not None and key == price_revision_key(row["fragment"] or "", row["deal"], row["price"]):
                return True, "тот же текст с пометкой об изменении цены"
    if phones_row != phones_new:
        return False, "другой агент"
    if o.article and o.article == row["article"] and phones_row:
        return True, "тот же артикул и контакт"
    if text_twin(o.fragment, row["fragment"]):
        return True, "тот же текст, другая цена"
    detailed = (street and house) or (rooms and floor and area)
    if detailed and (cx or street) and _close(o.price, row["price"], 0.10, 0) and text_twin(
            o.fragment, row["fragment"], short_ok=True):
        return True, "тот же короткий текст и характеристики"
    return False, "отдельное объявление"


def find_match(conn: sqlite3.Connection, o: ParsedObject, frag_hash: str, now: int) -> tuple[sqlite3.Row | None, str]:
    from . import offers, phonegroups
    phonegroups.refresh(conn)
    # 1) Дословный репост того же текста
    rows = conn.execute(
        """SELECT l.* FROM listing_events e JOIN listings l ON l.id = e.listing_id
           WHERE e.fragment_hash = ? AND l.source != 'feed' GROUP BY l.id ORDER BY MAX(e.ts) DESC""",
        (frag_hash,),
    )
    phones = phonegroups.canon_set(o.phones)
    reposts = {}
    for row in rows:
        previous = phonegroups.canon_set(json.loads(row["phones"] or "[]"))
        if row["type"] in TYPE_GROUPS.get(o.type or "", ()) and row["deal"] == o.deal and phones == previous:
            original = offers.merged_listing(conn, row)
            reposts.setdefault(original["id"], row)
    if reposts:
        return (next(iter(reposts.values())), "repost") if len(reposts) == 1 else (None, "")

    # 2) Тот же объект по характеристикам — по всей базе
    groups = TYPE_GROUPS.get(o.type or "", (o.type,))
    params: list = [*groups, o.deal, now - LOOKBACK_DAYS * 86400]
    cond = ""
    if o.area:
        cond += " AND (area IS NULL OR area BETWEEN ? AND ?)"
        params += [o.area * 0.95 - 1.5, o.area * 1.05 + 1.5]
    if o.land:
        cond += " AND (land IS NULL OR land BETWEEN ? AND ?)"
        params += [o.land * 0.95 - 0.3, o.land * 1.05 + 0.3]
    if o.rooms is not None:
        cond += " AND (rooms IS NULL OR rooms = ?)"
        params.append(o.rooms)
    rows = conn.execute(
        f"""SELECT * FROM listings
            WHERE type IN ({','.join('?' * len(groups))}) AND deal = ? AND last_seen >= ? AND source != 'feed' {cond}
            ORDER BY last_seen DESC LIMIT 400""",
        params,
    ).fetchall()
    found = {}
    for r in rows:
        ok, why = same_object(o, r)
        if not ok:
            continue
        if r["hidden_reason"] == offers.MERGED_REPEAT:
            current = offers.merged_listing(conn, r)
            if current["is_active"] and current["source"] != "feed":
                matches_current, current_why = same_object(o, current)
                if matches_current:
                    r, why = current, current_why
        original = offers.merged_listing(conn, r)
        found.setdefault(original["id"], (r, why))
    return next(iter(found.values())) if len(found) == 1 else (None, "")

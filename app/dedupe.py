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

from .parser import ParsedObject
from .textnorm import words

TYPE_GROUPS = {"flat": ("flat", "new"), "new": ("flat", "new"), "room": ("room",),
               "house": ("house",), "land": ("land",), "commercial": ("commercial",)}
LOOKBACK_DAYS = 120


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


# 24.10j (Артём: «текст 1 в 1 с другого номера, но цена другая — создаётся новое объявление»): тот же текст, где поменялись
# только числа (цена) — это переслали то же объявление, а не «рынок» от другого агента. Склеиваем, даже если номера разные.
_PRICE_WORDS = {"млн", "тыс", "т", "р", "руб", "рублей", "тр", "миллиона", "миллионов", "миллион", "тысяч"}
_PRICE_BEFORE = {"цена", "цены", "стоимость", "за"}


@functools.lru_cache(maxsize=8192)
def _phones_in(text: str) -> tuple:
    """26.10 (нагрузка): номера в тексте — один раз на текст (поиск идёт со сдвигом по символу и дорог)."""
    from .rules import find_phones
    return tuple(find_phones(text))


@functools.lru_cache(maxsize=8192)
def _words_list(text: str) -> tuple:
    return tuple(words(text).split())


def text_twin(a: str | None, b: str | None, short_ok: bool = False) -> bool:
    """Фрагменты совпадают слово в слово, кроме нескольких чисел (и слов вроде «млн»): цену поменяли, остальное — то же.
    short_ok — короткий текст («ЖК Абрикосово, евро-2, 6 млн») тоже, если вызывающий проверил место и цену."""
    import difflib
    # 24.10j (Артём): дубль — если совпадают номера В ТЕКСТЕ (кто переслал — неважно). Номера — часть текста: другой номер в тексте —
    # другой агент, отдельная карточка; номеров нет в обоих — совпадает сам текст
    from .phonegroups import canon_set   # 25.10: номера одного агента (phonegroups) — один номер
    la, lb = len(_words_list(a or "")), len(_words_list(b or ""))
    if min(la, lb) < (4 if short_ok else 12) or abs(la - lb) > 8:   # 26.10: коротко или длина сильно разная — дальше не считаем
        return False
    pa, pb = _phones_in(a or ""), _phones_in(b or "")
    if canon_set(p[2] for p in pa) != canon_set(p[2] for p in pb):   # (начало, конец, номер)
        return False

    def cut(t, spans):   # номера уже сравнили — из слов их убираем (другой номер того же агента — не «другие цифры»)
        for s0, e0, _ in sorted(spans, reverse=True):
            t = t[:s0] + " " + t[e0:]
        return t
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


def same_object(o: ParsedObject, row: sqlite3.Row) -> tuple[bool, str]:
    """Решение «это тот же объект» + причина (для отладки)."""
    rooms = None if o.rooms is None or row["rooms"] is None else o.rooms == row["rooms"]
    floor = None if o.floor is None or row["floor"] is None else o.floor == row["floor"]
    area = _close(o.area, row["area"], 0.03, 1.5)
    land = _close(o.land, row["land"], 0.04, 0.3)
    street = _eq(o.street, row["street"])
    house = _house_eq(o.house, row["house"])
    cx = _eq(o.complex, row["complex"])
    district = _eq(o.district, row["district"])
    settlement = _eq(o.settlement, row["settlement"])
    from .phonegroups import canon_set
    phones_row = canon_set(json.loads(row["phones"] or "[]"))
    phone = bool(phones_row & canon_set(o.phones)) if o.phones and phones_row else None
    price = _close(o.price, row["price"], 0.15, 0)

    # Тот же текст (поменялась разве что цена) — одно объявление, с какого бы номера его ни переслали.
    # 24.10m (пример Артёма 10678/10564): ПРОВЕРЯЕМ ДО «явных различий» — один и тот же текст «Гондаря Беличенко 86» разобрался
    # то улицей Гондаря, то Беличенко, и правило «разные улицы» делало две карточки. Площадь/этаж в тексте разные — это не близнецы.
    if text_twin(o.fragment, row["fragment"]):
        return True, "тот же текст, другая цена"

    # Явные различия — это разные объекты (две квартиры в одном доме).
    for k, v in (("комнаты", rooms), ("этаж", floor), ("площадь", area), ("участок", land),
                 ("ЖК", cx), ("район", district), ("улица", street), ("дом", house if street else None)):
        if v is False:
            return False, f"разные: {k}"
    # короткий текст («ЖК Абрикосово, ЕВРО 2 квартира, 6 млн») — только если место совпало и цена почти та же (до 10 %)
    if (cx or street) and _close(o.price, row["price"], 0.10, 0) and text_twin(o.fragment, row["fragment"], short_ok=True):
        return True, "тот же короткий текст, цена почти та же"
    # Один и тот же объект от разных агентов — это рынок: показываем отдельно.
    # Склеиваем только повторы одного агента (тот же номер телефона).
    if not phone:
        return False, "другой агент"

    addr = bool(street and house)
    sizes = [v for v in (area, land) if v is not None]
    size_ok = bool(sizes) and all(sizes)
    details = [v for v in (rooms, floor) if v is not None]
    details_ok = bool(details) and all(details)

    if o.type in ("flat", "new", "room"):
        # 24.10 (уровень 1, решение Артёма): тот же агент, то же место, те же комнаты и этаж (у обоих указаны), площадь не спорит —
        # один объект, даже если текст и цена другие. Всё, что «почти совпадает», — в очередь «Возможно, повтор» (dupreview)
        if (cx or street) and rooms and floor:
            return True, "тот же агент + место + комнаты + этаж"
        if addr and (size_ok or details_ok):
            return True, "адрес + параметры"
        if cx and size_ok and details_ok:
            return True, "ЖК + площадь + этаж/комнаты"
        if (district or street) and size_ok and rooms and floor:
            return True, "район/улица + площадь + комнаты + этаж"
        if phone and size_ok and details_ok and price is not False:
            return True, "телефон + площадь + этаж/комнаты"
        return False, "мало совпадений"

    if o.type == "house":
        loc = addr or street or settlement or district or cx
        if loc and area and (land is not False) and (price is not False or phone):
            return True, "место + площадь дома"
        return False, "мало совпадений"

    if o.type == "land":
        loc = addr or (street and house is not False) or settlement or cx
        if loc and land and (price is not False or phone):
            return True, "место + сотки"
        if district and land and phone:
            return True, "район + сотки + телефон"
        return False, "мало совпадений"

    # коммерция
    if (addr or cx or street) and area and (price is not False or phone):
        return True, "адрес + площадь"
    if phone and area and price:
        return True, "телефон + площадь + цена"
    return False, "мало совпадений"


def find_match(conn: sqlite3.Connection, o: ParsedObject, frag_hash: str, now: int) -> tuple[sqlite3.Row | None, str]:
    from . import phonegroups
    phonegroups.refresh(conn)
    # 1) Дословный репост того же текста
    row = conn.execute(
        """SELECT l.* FROM listing_events e JOIN listings l ON l.id = e.listing_id
           WHERE e.fragment_hash = ? ORDER BY e.ts DESC LIMIT 1""",
        (frag_hash,),
    ).fetchone()
    if row is not None and row["type"] in TYPE_GROUPS.get(o.type or "", ()) and row["deal"] == o.deal:
        return row, "repost"

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
    found = []
    for r in rows:
        ok, why = same_object(o, r)
        if ok:
            found.append((r, why))
    if len(found) > 1:   # 24.10: подходят несколько карточек (вы решили, что это разные квартиры) — та, чей текст ближе
        import difflib
        ta = words(o.fragment or "").split()
        found.sort(key=lambda f: -difflib.SequenceMatcher(None, ta, words(f[0]["fragment"] or "").split(), autojunk=False).ratio())
    return found[0] if found else (None, "")

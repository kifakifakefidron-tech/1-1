"""Поиск объектов: фильтры + точный текстовый поиск.

Текстовый поиск ищет слова целиком (с учётом окончаний по началу слова):
«движение» найдёт «ЖК Движение», но не тысячу случайных участков, как раньше,
когда искалось «похожее» по всем длинным текстам.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

from . import geo
from .rules import TYPE_LABELS
from .textnorm import norm, words

SORTS = {   # цена «под вопросом» (pricefix) в сортировках по цене — в самом конце
    "new": "l.last_seen DESC, l.id DESC",
    "price_asc": "l.price IS NULL OR l.price_doubt IS 1, l.price ASC, l.id DESC",
    "price_desc": "l.price IS NULL OR l.price_doubt IS 1, l.price DESC, l.id DESC",
    "price_m2": "l.price_m2 IS NULL OR l.price_doubt IS 1, l.price_m2 ASC, l.id DESC",
    "area_desc": "l.area IS NULL, l.area DESC, l.id DESC",
    "market": "l.market_diff IS NULL, l.market_diff ASC, l.id DESC",   # ниже рынка — первыми
}
# состояние — по тексту объявления (search_text: нижний регистр, ё → е)
COND_SQL = {"pcho": "(l.search_text LIKE '%пчо%' OR l.search_text LIKE '%предчистов%' OR l.search_text LIKE '%white box%')",
            "repair": "(l.search_text LIKE '%ремонт%' AND l.search_text NOT LIKE '%без ремонт%' AND l.search_text NOT LIKE '%под ремонт%')",
            "furniture": "(l.search_text LIKE '%мебел%' OR l.search_text LIKE '%меблир%')"}


@dataclass
class Query:
    q: str = ""
    types: list[str] = field(default_factory=list)
    deal: str = "sale"
    rooms: list[int] = field(default_factory=list)   # 0 студия … 4 = «4 и больше»
    price_min: int | None = None
    price_max: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    land_min: float | None = None
    land_max: float | None = None
    districts: list[str] = field(default_factory=list)
    complexes: list[str] = field(default_factory=list)
    not_first: bool = False
    not_last: bool = False
    fresh_days: int | None = None
    new_days: int | None = None   # только впервые появившиеся за N дней («Новое сегодня»)
    since: int | None = None   # только появившиеся позже (кнопка «Обновить»)
    hl: int | None = None      # из уведомления: появившиеся позже этого — первыми в списке
    hot: bool = False          # «🔥 Горячее»: цена снизилась за неделю, ниже рынка, «срочно»
    sort: str = "new"
    hide_user: int | None = None   # не показывать объекты, которые этот человек скрыл для себя
    # подбор под клиента (ТЗ v2)
    exclude_client: int | None = None   # без объектов, уже связанных с клиентом, и тех, что «не подходит»
    with_photo: bool = False
    cond: list[str] = field(default_factory=list)   # pcho | repair | furniture — по тексту объявления
    no_encumbrance: bool = False
    mortgage: bool = False              # клиенту нужна ипотека — без «только наличные»
    budget_near: int | None = None      # сортировка «ближе к бюджету»
    page: int = 1
    size: int = 30


# ─── разбор параметров адреса ──────────────────────────────────────────────
def num_param(v: str | None, cast=float):
    if v in (None, ""):
        return None
    try:
        return cast(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _list(qp, key: str) -> list[str]:
    vals: list[str] = []
    for v in qp.getlist(key):
        vals += [x for x in v.split(",") if x]
    return vals


def query_from_params(qp) -> Query:
    """Параметры адреса (?q=…&rooms=1,2…) → Query. qp — QueryParams Starlette."""
    rooms = [int(x) for x in _list(qp, "rooms") if x.isdigit()]
    return Query(
        q=qp.get("q", "")[:200],
        types=[t for t in _list(qp, "type") if t in TYPE_LABELS],
        deal="rent" if qp.get("deal") == "rent" else "sale",
        rooms=rooms,
        price_min=num_param(qp.get("price_min"), int), price_max=num_param(qp.get("price_max"), int),
        area_min=num_param(qp.get("area_min")), area_max=num_param(qp.get("area_max")),
        land_min=num_param(qp.get("land_min")), land_max=num_param(qp.get("land_max")),
        districts=_list(qp, "district"), complexes=_list(qp, "complex"),
        not_first=qp.get("not_first") == "1", not_last=qp.get("not_last") == "1",
        fresh_days=num_param(qp.get("fresh_days"), int), new_days=num_param(qp.get("new_days"), int),
        since=num_param(qp.get("since"), int), hl=num_param(qp.get("hl"), int), hot=qp.get("hot") == "1",
        sort=qp.get("sort", "new"),
        page=num_param(qp.get("page"), int) or 1,
        size=num_param(qp.get("size"), int) or 30,
    )


# ─── страницы и поиск ──────────────────────────────────────────────────────
def _stem(t: str) -> str:
    """Окончания: «мозаике» → «мозаик» (ищем по началу слова)."""
    if len(t) > 5 and not t.isdigit():
        t = re.sub(r"(ами|ями|ого|ему|ому|ыми|ими|ах|ях|ой|ей|ом|ем|ам|ям|ую|юю|ая|яя|ые|ие|ый|ий|ов|ев|а|я|у|ю|е|ы|и|о)$", "", t)
    return t


_ROOM_WORDS = [(r"\bстуди\w*", 0), (r"\bоднушк\w*|\bоднокомнатн\w*", 1), (r"\bдвушк\w*|\bдвухкомнатн\w*", 2),
               (r"\bтр[её]шк\w*|\bтр[её]хкомнатн\w*", 3), (r"\bчетыр[её]хкомнатн\w*", 4)]
_ROOM_NUM = re.compile(r"\b(?:евро\s*-?\s*)?([1-5])\s*-?\s*(?:х\s*)?(?:к|кк|ккв|кв|ком|комн\w*|комнатн\w*)\b|\bевро\s*-?\s*([1-5])\b")
_STOP = {"жк", "ул", "улица", "мкр", "мкрн", "микрорайон", "район", "р", "н", "в", "на", "и", "квартира",
         "квартиру", "квартиры", "кв", "продажа", "продам", "купить"}


def interpret(q: str) -> tuple[str | None, list[int]]:
    """Строка поиска → (запрос FTS, комнаты).

    «2к фмр» → комнаты [2] + район ФМР во всех написаниях (фмр, фестивальный, фестивалка…);
    остальные слова ищутся по началу слова, все вместе."""
    w = f" {norm(q)} "
    rooms: list[int] = []
    for rx, n in _ROOM_WORDS:
        if re.search(rx, w):
            rooms.append(n)
            w = re.sub(rx, " ", w)
    for m in re.finditer(r"\bмини\s*-?\s*([1-4])\b", w):
        n = int(m.group(1))
        rooms += [n - 1, n]
    w = re.sub(r"\bмини\s*-?\s*([1-4])\b", " ", w)
    for m in re.finditer(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", w):
        rooms.append(int(m.group(1) or m.group(2)) - 1)
    w = re.sub(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", " ", w)
    for m in _ROOM_NUM.finditer(w):
        rooms.append(int(m.group(1) or m.group(2)))
    w = _ROOM_NUM.sub(" ", w)

    groups: list[str] = []
    # Районы: любое написание → все написания этого района
    for alias, canon in geo._ALIASES:
        a = f" {alias} "
        if a in f" {' '.join(w.split())} ":
            names = {words(canon)} | {al for al, c in geo._ALIASES if c == canon}
            groups.append("(" + " OR ".join(f'"{n}"' if " " in n else f'"{_stem(n)}"*' for n in sorted(names)) + ")")
            w = f" {' '.join(w.split())} ".replace(a, " ")

    tokens = [t for t in re.split(r"[^0-9a-zа-я]+", w) if t]
    tokens = [t for t in tokens if (len(t) >= 2 or t.isdigit()) and t not in _STOP]
    groups += [f'"{_stem(t)}"*' for t in tokens]
    return (" AND ".join(groups) or None), sorted(set(rooms))


def _fts_query(q: str) -> str | None:
    return interpret(q)[0]

# ─── «Пишите как в чате»: фраза → фильтры (акт приёмки 14.10, Б3) ──────────
_DEAL_RENT = re.compile(r"\b(?:аренд\w*|сдам|сдаю|сда[её]тся|сдается|снять|сниму|посуточн\w*|в наем|наём|найм\w*)\b")
_DEAL_SALE = re.compile(r"\b(?:продаж\w*|продам|прода[её]тся|продается|купить|куплю|покупк\w*)\b")
_TYPE_WORDS = [(r"\b(?:коммерц\w*|офис\w*|помещени\w*|магазин\w*|склад\w*|псн)\b", "commercial"),
               (r"\b(?:участ(?:ок|ки|ка)\w*|земл[яи]\w*|земельн\w*|ижс)\b", "land"),
               (r"\b(?:дом|дома|домик\w*|коттедж\w*|таунхаус\w*|дуплекс\w*)\b", "house"),
               (r"\b(?:комнат[ау]|комнаты|комната)\b(?!\s*\d)", "room"),
               (r"\b(?:квартир\w*|кв-?р\w*)\b", "flat")]
_NUM = r"(\d+(?:[.,]\d+)?)"
_UNIT = r"\s*(млн|миллион\w*|лям\w*|м(?!\s*(?:2|²|кв))|тыс\w*|т\.?р\.?|т|к|р|руб\w*|₽)?\b"
_AREA_UNIT = r"\s*(?:м2|м²|кв\.?\s*м\w*|квм|метр\w*|кв\b|м\b(?=\s*(?:2|²|кв)))"
_COND_WORDS = [(r"\b(?:с\s+)?ремонт\w*\b", "repair"), (r"\b(?:с\s+)?мебел\w*|\bмеблир\w*", "furniture"),
               (r"\bпчо\b|\bпредчистов\w*|\bwhite\s*box\b", "pcho")]
_ROOM_LABEL = {0: "студия", 1: "1 комн", 2: "2 комн", 3: "3 комн", 4: "4+ комн"}
_TYPE_LABEL = {"flat": "квартира", "house": "дом", "land": "участок", "room": "комната", "commercial": "коммерция"}


def _money(num: str, unit: str | None, deal: str) -> int | None:
    try:
        v = float(num.replace(",", "."))
    except ValueError:
        return None
    u = (unit or "").lower()
    if u.startswith(("млн", "миллион", "лям", "м")):
        v *= 1_000_000
    elif u.startswith(("тыс", "т", "к")):
        v *= 1_000
    elif not u or u.startswith(("р", "₽")):
        if deal == "rent":
            v = v * 1000 if v < 1000 else v            # «до 40» в аренде = 40 тыс.
        else:
            v = v * 1_000_000 if v < 1000 else v * 1000 if v < 100_000 else v   # «до 7» = 7 млн, «до 7000» = 7 млн
    return int(v)


def _fmt_money(v: int, deal: str) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:g}".replace(".", ",") + " млн"
    return f"{v // 1000} тыс" if v >= 1000 else f"{v} ₽"


def _fuzzy_district(tok: str) -> str | None:
    """Опечатка в названии района: «фрм» → ФМР (перестановка или одна буква в слове от 5 букв)."""
    from .osmaddr import _typos
    if len(tok) < 3 or tok.isdigit():
        return None
    hits = set()
    for alias, canon in geo._ALIASES:
        if " " in alias or abs(len(alias) - len(tok)) > 1 or len(alias) < 3:
            continue
        d = _typos(tok, alias)
        if d == 1 and (len(tok) >= 5 or sorted(tok) == sorted(alias)):   # короткие («фрм») — только перестановка букв
            hits.add(canon)
    return hits.pop() if len(hits) == 1 else None


def parse_phrase(q: str, deal: str = "sale") -> dict:
    """«2к фмр до 7 млн», «однушка юмр до 5», «3к не первый этаж», «аренда 1к», «самолет 1» → фильтры.
    Что не поняли — ищется по тексту объявлений (fts). chips — как поняли (для показа над выдачей)."""
    w = f" {norm(q)} "
    out: dict = {"deal": None, "types": [], "rooms": [], "price_min": None, "price_max": None, "area_min": None,
                 "area_max": None, "districts": [], "complexes": [], "not_first": False, "not_last": False,
                 "cond": [], "chips": []}
    chip = lambda label, frag: out["chips"].append({"label": label, "drop": " ".join(frag.split())})  # noqa: E731

    def take(rx, fn):
        nonlocal w
        for m in list(re.finditer(rx, w)):
            if fn(m) is not False:
                w = w.replace(m.group(0), " ", 1)

    take(_DEAL_RENT, lambda m: (out.update(deal="rent"), chip("аренда", m.group(0))))
    take(_DEAL_SALE, lambda m: (out.update(deal=out["deal"] or "sale"), chip("продажа", m.group(0))) if not out["deal"] else False)
    eff = out["deal"] or deal
    # этаж
    take(r"\bне\s+(?:на\s+)?(?:перв\w*|1\s*-?\s*(?:й|ом)?)(?:\s+этаж\w*)?\b", lambda m: (out.update(not_first=True), chip("не первый этаж", m.group(0))))
    take(r"\bне\s+(?:на\s+)?последн\w*(?:\s+этаж\w*)?\b", lambda m: (out.update(not_last=True), chip("не последний этаж", m.group(0))))
    # площадь: «от 40 м2», «до 60 кв», «40-60 м2»
    def area(m, key):
        out[key] = float(m.group(1).replace(",", "."))
        chip(("от " if key == "area_min" else "до ") + f"{out[key]:g} м²", m.group(0))
    take(rf"\b(?:от|больше|не меньше)\s*{_NUM}{_AREA_UNIT}", lambda m: area(m, "area_min"))
    take(rf"\b(?:до|меньше|не больше)\s*{_NUM}{_AREA_UNIT}", lambda m: area(m, "area_max"))
    # цена: «до 7 млн», «от 5», «5-7 млн», «до 30 тыс»
    def rng(m):
        lo, hi = _money(m.group(1), m.group(3), eff), _money(m.group(2), m.group(3), eff)
        if lo and hi and lo < hi:
            out.update(price_min=lo, price_max=hi)
            chip(f"{_fmt_money(lo, eff)} – {_fmt_money(hi, eff)}", m.group(0))
            return None
        return False
    take(rf"\b{_NUM}\s*[-–—]\s*{_NUM}{_UNIT}(?=\s|$)", lambda m: rng(m) if m.group(3) else False)
    def one(m, key, word):
        v = _money(m.group(1), m.group(2), eff)
        if not v:
            return False
        out[key] = v
        chip(f"{word} {_fmt_money(v, eff)}", m.group(0))
    take(rf"\b(?:до|не дороже|дешевле|макс\w*|max)\s*{_NUM}{_UNIT}", lambda m: one(m, "price_max", "до"))
    take(rf"\b(?:от|дороже|мин\w*|min)\s*{_NUM}{_UNIT}", lambda m: one(m, "price_min", "от"))
    # комнаты
    rooms: list[int] = []
    for rx, n in _ROOM_WORDS:
        if re.search(rx, w):
            rooms.append(n)
            chip(_ROOM_LABEL[n], re.search(rx, w).group(0))
            w = re.sub(rx, " ", w)
    for m in list(re.finditer(r"\bмини\s*-?\s*([1-4])\b", w)):
        n = int(m.group(1)); rooms += [n - 1, n]; chip(f"мини-{n}", m.group(0))
    w = re.sub(r"\bмини\s*-?\s*([1-4])\b", " ", w)
    for m in list(re.finditer(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", w)):
        n = int(m.group(1) or m.group(2)); rooms.append(n - 1); chip(f"евро-{n}", m.group(0))
    w = re.sub(r"\bевро\s*-?\s*([2-6])\b|\b([2-6])\s*-?\s*евро\b", " ", w)
    for m in list(_ROOM_NUM.finditer(w)):
        n = int(m.group(1) or m.group(2)); rooms.append(n); chip(_ROOM_LABEL[min(n, 4)], m.group(0))
    w = _ROOM_NUM.sub(" ", w)
    out["rooms"] = sorted(set(min(r, 4) for r in rooms))
    # тип
    for rx, t in _TYPE_WORDS:
        m = re.search(rx, w)
        if m:
            if t not in out["types"] and not (t == "flat" and out["rooms"]):
                out["types"].append(t)
                chip(_TYPE_LABEL[t], m.group(0))
            w = re.sub(rx, " ", w)
    # состояние
    for rx, c in _COND_WORDS:
        m = re.search(rx, w)
        if m and not re.search(r"\b(?:без|под)\s+" + rx.lstrip(r"\b"), w):
            out["cond"].append(c)
            chip({"repair": "с ремонтом", "furniture": "с мебелью", "pcho": "ПЧО"}[c], m.group(0))
            w = re.sub(rx, " ", w)
    # ЖК: «жк X», или название из справочника; номер очереди сразу за названием — часть названия («самолет 1»)
    toks = w.split()
    i = 0
    used: set[int] = set()
    while i < len(toks):
        explicit = toks[i] in ("жк", "жилой") and i + 1 < len(toks)
        start = i + 1 if explicit else i
        best = None
        for n in (3, 2, 1):
            cand = " ".join(toks[start:start + n])
            if len(cand) < 4 or cand.isdigit() or cand in _STOP or geo.canonical_district(cand):
                continue
            rec = geo.resolve_complex(cand)
            if rec or (explicit and n == 1):
                best = (n, rec.name if rec else cand.title())
                break
        if best:
            n, name = best
            j = start + n
            if j < len(toks) and re.fullmatch(r"\d{1,2}", toks[j]) and 0 < int(toks[j]) < 30:
                name, j = f"{name} {toks[j]}", j + 1
            out["complexes"].append(name)
            chip(f"ЖК {name}", " ".join(toks[i:j]))
            used.update(range(i, j))
            i = j
        else:
            i += 1
    toks = [t for k, t in enumerate(toks) if k not in used]
    # районы: любое написание, в т. ч. из двух слов и с опечаткой
    w = f" {' '.join(toks)} "
    for alias, canon in geo._ALIASES:
        if f" {alias} " in w and canon not in out["districts"]:
            out["districts"].append(canon)
            chip(canon, alias)
            w = w.replace(f" {alias} ", " ")
    rest = []
    for t in w.split():
        d = _fuzzy_district(t) if not t.isdigit() and t not in _STOP else None
        if d and d not in out["districts"]:
            out["districts"].append(d)
            chip(d, t)
        elif not d:
            rest.append(t)
    tokens = [t for t in re.split(r"[^0-9a-zа-я]+", " ".join(rest)) if t]
    tokens = [t for t in tokens if (len(t) >= 2 or t.isdigit()) and t not in _STOP and t not in _NOISE]
    out["fts"] = " AND ".join(f'"{_stem(t)}"*' for t in tokens) or None
    out["text"] = " ".join(tokens)
    return out


_NOISE = {"этаж", "этажа", "этаже", "эт", "млн", "тыс", "руб", "рублей", "до", "от", "не", "с", "без", "под", "за", "по",
          "м2", "кв", "метров", "цена", "ищу", "нужна", "нужен", "нужно", "срочно", "хочу", "или"}



HOT_MARKET = -25   # «Горячее» (17.10: должно быть 5–8 % базы): дешевле медианы м² на 25–45 % по 15+ сравнимым
HOT_FLOOR = -45    # ещё дешевле — скорее ошибка в цене или площади, чем находка


def _where(qr: Query, now: int) -> tuple[str, list]:
    # Без адреса/ЖК/района/посёлка объект не найти ни фильтром, ни на карте — не показываем
    w = ["l.is_active = 1", "l.deal = ?",
         "(l.district IS NOT NULL OR l.complex IS NOT NULL OR l.street IS NOT NULL OR l.settlement IS NOT NULL)"]
    p: list = [qr.deal]
    fts = None
    if qr.q:   # «2к фмр до 7 млн» — фраза как фильтры; что поставлено фильтрами явно — главнее
        ph = parse_phrase(qr.q, qr.deal)
        fts = ph["fts"]
        upd = {k: ph[k] for k in ("types", "rooms", "districts", "complexes", "cond") if ph[k] and not getattr(qr, k)}
        upd |= {k: ph[k] for k in ("price_min", "price_max", "area_min", "area_max") if ph[k] is not None and getattr(qr, k) is None}
        upd |= {k: True for k in ("not_first", "not_last") if ph[k]}
        if ph["deal"]:
            upd["deal"] = ph["deal"]
            p = [ph["deal"]]
        if upd:
            qr = Query(**{**qr.__dict__, **upd})
    if qr.types:
        types = list(qr.types) + (["new"] if "flat" in qr.types else [])  # старые записи «новостройка» = квартиры
        w.append(f"l.type IN ({','.join('?' * len(types))})")
        p += types
    if qr.rooms:
        mask = 0
        for r in qr.rooms:
            mask |= 1 << max(0, min(r, 4))
        w.append("(l.rooms_mask & ?) != 0")
        p.append(mask)
    for col, lo, hi in (("price", qr.price_min, qr.price_max), ("area", qr.area_min, qr.area_max),
                        ("land", qr.land_min, qr.land_max)):
        if lo is not None:
            w.append(f"l.{col} >= ?"); p.append(lo)
        if hi is not None:
            w.append(f"l.{col} <= ?"); p.append(hi)
        if col == "price" and (lo is not None or hi is not None):
            w.append("l.price_doubt IS NOT 1")   # цена под вопросом — в ценовой фильтр не попадает
    if qr.districts:   # основной район или один из дополнительных (объект на границе районов)
        q = ",".join("?" * len(qr.districts))
        w.append(f"(l.district IN ({q}) OR EXISTS (SELECT 1 FROM json_each(l.extra_districts) j WHERE j.value IN ({q})))")
        p += qr.districts + qr.districts
    if qr.complexes:
        keys = sorted({geo.complex_key(c) for c in qr.complexes if geo.complex_key(c)})
        w.append(f"cxkey(l.complex) IN ({','.join('?' * len(keys))})"); p += keys
    if qr.not_first:
        w.append("(l.floor IS NULL OR l.floor > 1)")
    if qr.not_last:
        w.append("(l.floor IS NULL OR l.floors IS NULL OR l.floor < l.floors)")
    if qr.exclude_client:
        w.append("""NOT EXISTS (SELECT 1 FROM client_items ci WHERE ci.client_id = ? AND ci.listing_id = l.id)
                    AND NOT EXISTS (SELECT 1 FROM client_hidden ch WHERE ch.client_id = ? AND ch.listing_id = l.id)""")
        p += [qr.exclude_client, qr.exclude_client]
    if qr.with_photo:
        w.append("json_array_length(COALESCE(l.photos, '[]')) > 0")
    conds = [COND_SQL[c] for c in qr.cond if c in COND_SQL]
    if conds:
        w.append("(" + " OR ".join(conds) + ")")
    if qr.no_encumbrance:
        w.append("NOT (l.search_text LIKE '%обрем%' AND l.search_text NOT LIKE '%без обрем%' AND l.search_text NOT LIKE '%обременен нет%'"
                 " AND l.search_text NOT LIKE '%обременения нет%' AND l.search_text NOT LIKE '%обременений нет%')")
    if qr.mortgage:
        w.append("NOT (l.search_text LIKE '%только налич%' OR l.search_text LIKE '%без ипотек%' OR l.search_text LIKE '%ипотека не%')")
    if qr.fresh_days:
        w.append("l.last_seen >= ?"); p.append(now - qr.fresh_days * 86400)
    if qr.new_days:
        w.append("l.first_seen >= ?"); p.append(now - qr.new_days * 86400)
    if qr.since:
        w.append("l.first_seen > ?"); p.append(qr.since)
    if qr.hot:
        # акт 14.10, С6: «Горячее» — 5–8 % базы: цена снизилась за неделю, заметно ниже рынка или «срочно» и дешевле рынка
        w.append(f"""l.price_doubt IS NOT 1 AND ((l.prev_price >= l.price * 1.03 AND l.prev_price < l.price * 1.5 AND l.price_changed_at >= ?)
                     OR (l.market_diff BETWEEN {HOT_FLOOR} AND {HOT_MARKET} AND l.last_seen >= ?))""")
        p += [now - 7 * 86400, now - 7 * 86400]
    if fts:
        w.append("l.id IN (SELECT rowid FROM listings_fts WHERE listings_fts MATCH ?)"); p.append(fts)
    if qr.hide_user:
        w.append("NOT EXISTS (SELECT 1 FROM hidden_listings h WHERE h.user_id = ? AND h.listing_id = l.id)"); p.append(qr.hide_user)
    return " AND ".join(w), p


# Агент подтвердил свой номер в 1+1 (код со своего номера) — объявление с этим номером «от проверенного агента»
# ТЗ «админка-гео» P2.1: насколько точна точка. house — дом; complex — центр ЖК; street — середина улицы / посёлок;
# district — центр района; none — точки нет. На карте: дом — меткой, ЖК — одной меткой ЖК с числом объектов,
# улица/район — не показываем (в списке «адрес уточняется»). Район по точке решает только house.
HOUSE_SRCS = ("osm-house", "osm-near", "feed-house", "learned", "geocoder")
COMPLEX_SRCS = ("map-cx", "feed-cx", "learned-cx", "geocoder-cx", "stacked")   # stacked — в точке 3+ разных домов
DISTRICT_SRCS = ("map-district", "geocoder-district")
GEO_LEVEL = f"""(CASE WHEN l.lat IS NULL THEN 'none'
    WHEN l.geo_status = 'manual' THEN 'house'
    WHEN l.geo_src IN {COMPLEX_SRCS} THEN 'complex'
    WHEN l.geo_status = 'feed' THEN 'house'
    WHEN l.geo_src IN {DISTRICT_SRCS} THEN 'district'
    WHEN l.geo_status = 'approx' OR l.geo_src IN ('osm-street', 'geocoder-street', 'geocoder-place') THEN 'street'
    ELSE 'house' END)"""


def geo_level(lat, status: str | None, src: str | None) -> str:
    """То же, что GEO_LEVEL, — в Python."""
    if lat is None:
        return "none"
    if status == "manual":
        return "house"
    if src in COMPLEX_SRCS:
        return "complex"
    if status == "feed":
        return "house"
    if src in DISTRICT_SRCS:
        return "district"
    if status == "approx" or src in ("osm-street", "geocoder-street", "geocoder-place"):
        return "street"
    return "house"


VERIFIED = """(l.source = 'own' OR EXISTS (SELECT 1 FROM json_each(l.phones) j JOIN agent_phones a ON a.phone = j.value))"""

PUBLIC_FIELDS = ("id", "type", "deal", "rooms", "area", "land", "floor", "floors", "price", "price_m2",
                 "district", "complex", "settlement", "street", "house", "title", "description",
                 "first_seen", "last_seen", "lat", "lon", "source", "url", "room_kind", "article",
                 "is_active", "expires_at",
                 "prev_price", "price_changed_at")


def mask_phone(p: str) -> str:
    """+79181112233 → «+7 918 •••-••-••»: видно, что номер есть, но не сам номер."""
    d = "".join(ch for ch in p if ch.isdigit())
    return f"+7 {d[1:4]} •••-••-••" if len(d) == 11 else "+7 ••• •••-••-••"


def row_to_item(r: sqlite3.Row, with_contacts: bool) -> dict:
    d = {k: r[k] for k in PUBLIC_FIELDS}
    from .parser import guard_phones, public_text
    d["description"] = guard_phones(d["description"])   # номеров в описании не отдаём никому — они в контакте
    if not with_contacts:   # списки и все без доступа: без имён агентов, комиссии и «номеров с опечатками» (152-ФЗ)
        d["description"] = public_text(d["description"])
    keys = r.keys()
    d["price_doubt"] = bool(r["price_doubt"]) if "price_doubt" in keys else False
    d["geo_level"] = geo_level(r["lat"], r["geo_status"], r["geo_src"]) if "geo_src" in keys and "lat" in keys else None
    d["verified"] = bool(r["verified"]) if "verified" in r.keys() else None
    d["photos"] = json.loads(r["photos"] or "[]")
    d["extra_districts"] = json.loads(r["extra_districts"] or "[]")
    phones = json.loads(r["phones"] or "[]")
    if with_contacts:
        d["phones"] = phones
        d["fragment"] = r["fragment"]
    else:
        d["phones_masked"] = [mask_phone(p) for p in phones]
    return d


def search(conn: sqlite3.Connection, qr: Query, now: int, with_contacts: bool) -> dict:
    where, params = _where(qr, now)
    total = conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {where}", params).fetchone()[0]
    size = max(1, min(qr.size, 100))
    page = max(1, qr.page)
    # Объекты СТРЕЛ сортируются вместе со всеми, без вывода вперёд (решение 07.10)
    order = SORTS.get(qr.sort, SORTS["new"])
    if qr.sort == "budget" and qr.budget_near:   # ближе к бюджету клиента
        order = f"l.price IS NULL, ABS(l.price - {int(qr.budget_near)}) ASC, l.id DESC"
    if qr.hl:   # новое в сохранённом поиске — наверх
        order = f"(l.first_seen > {int(qr.hl)}) DESC, " + order
    rows = conn.execute(
        f"SELECT l.*, {VERIFIED} AS verified FROM listings l WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [size, (page - 1) * size],
    ).fetchall()
    return {
        "total": total,
        "page": page,
        "pages": (total + size - 1) // size,
        "items": [row_to_item(r, with_contacts) for r in rows],
    }


def map_points(conn: sqlite3.Connection, qr: Query, now: int) -> dict:
    """Точки для карты по тем же фильтрам + сколько объектов без точки (чтобы было видно, что не потеряны)."""
    where, params = _where(qr, now)
    rows = conn.execute(
        f"""SELECT l.id, l.lat, l.lon, l.price, l.complex, {GEO_LEVEL} AS level, l.price_doubt IS 1 AS doubt
            FROM listings l WHERE {where} AND l.lat IS NOT NULL LIMIT 20000""",
        params,
    ).fetchall()
    total, pending = conn.execute(
        f"SELECT COUNT(*), SUM(l.lat IS NULL AND l.geo_status = 'pending') FROM listings l WHERE {where}", params).fetchone()
    points, hidden = [], 0
    groups: dict[str, dict] = {}
    for r in rows:
        if r["level"] in ("street", "district"):
            hidden += 1          # середина улицы / центр района — не метка: «адрес уточняется», объект в списке
        elif r["level"] == "complex" and r["complex"]:
            k = geo.complex_key(r["complex"]) or r["complex"]
            g = groups.setdefault(k, {"complex": r["complex"], "lat": round(r["lat"], 5), "lon": round(r["lon"], 5), "n": 0,
                                      "price_min": None})
            g["n"] += 1
            if r["price"] and not r["doubt"] and (g["price_min"] is None or r["price"] < g["price_min"]):
                g["price_min"] = r["price"]
        else:   # 17.10: только то, что нужно метке (ценник); заголовок, фото и адрес окошко берёт из /api/listings/{id}
            points.append({"id": r["id"], "lat": round(r["lat"], 5), "lon": round(r["lon"], 5), "price": r["price"],
                           "level": r["level"]})
    return {"points": points, "complexes": sorted(groups.values(), key=lambda g: -g["n"]), "total": total,
            "pending": pending or 0, "approx_hidden": hidden}


def facets(conn: sqlite3.Connection, qr: Query, now: int) -> dict:
    """Счётчики для фильтров: районы и ЖК с учётом остальных фильтров."""
    base = Query(**{**qr.__dict__, "districts": [], "complexes": []})
    where, params = _where(base, now)
    districts = conn.execute(
        f"""SELECT name, COUNT(*) AS n FROM (
                SELECT l.district AS name FROM listings l WHERE {where} AND l.district IS NOT NULL
                UNION ALL
                SELECT j.value FROM listings l, json_each(l.extra_districts) j WHERE {where})
            GROUP BY 1 ORDER BY n DESC""",
        params + params).fetchall()
    # Разные написания одного ЖК — одна строка в фильтре (самое частое написание). База считает по написаниям,
    # а склеиваем по ключу ЖК уже в Python: так ключ считается для сотни названий, а не для каждого объекта
    groups: dict[str, list] = {}
    for name, n in conn.execute(f"SELECT l.complex, COUNT(*) FROM listings l WHERE {where} AND l.complex IS NOT NULL "
                                f"GROUP BY l.complex", params):
        g = groups.setdefault(geo.complex_key(name), [name, n, 0])
        if n > g[1]:
            g[0], g[1] = name, n
        g[2] += n
    complexes = sorted(({"name": name, "n": total} for name, _, total in groups.values()), key=lambda c: -c["n"])[:300]
    return {"districts": [dict(r) for r in districts], "complexes": complexes}


def listing_detail(conn: sqlite3.Connection, listing_id: int, with_contacts: bool,
                   is_admin: bool = False) -> dict | None:
    r = conn.execute(f"SELECT l.*, {VERIFIED} AS verified FROM listings l WHERE l.id = ?", (listing_id,)).fetchone()
    if r is None:
        return None
    d = row_to_item(r, with_contacts)
    ev = conn.execute(
        """SELECT e.ts, e.price, e.match, m.chat_name, m.sender_name, m.source
           FROM listing_events e LEFT JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ? ORDER BY e.ts DESC LIMIT 50""", (listing_id,)).fetchall()
    # Кто и в какой чат присылал — видит только администратор
    history = []
    for e in ev if is_admin else []:
        history.append({"ts": e["ts"], "price": e["price"], "match": e["match"],
                        "chat": e["chat_name"], "sender": e["sender_name"], "source": e["source"]})
    d["history"] = history
    d["price_history"] = [{"ts": h[0], "price": h[1]} for h in conn.execute(
        "SELECT ts, price FROM price_history WHERE listing_id = ? ORDER BY ts, rowid LIMIT 100", (listing_id,))]
    d["seen_count"] = r["seen_count"] if is_admin else None
    d["geo_approx"] = d["geo_level"] != "house"   # примерное место — на мини-карте кругом, а не меткой
    if is_admin:
        d["geo_status"] = r["geo_status"]
        d["admin_fixed"] = r["admin_fixed"]
    d["chats"] = conn.execute(
        """SELECT COUNT(DISTINCT m.chat_id) FROM listing_events e JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ?""", (listing_id,)).fetchone()[0] if is_admin else None
    return d

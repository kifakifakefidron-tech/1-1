"""Детерминированный разбор объявления правилами (без нейросети).

Используется двумя способами:
  1. как основной разбор, если ключ нейросети не задан;
  2. как проверка/страховка результата нейросети: единицы площади (сотки vs м²),
     цена, телефоны, этаж — то, что правила достают надёжнее.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textnorm import norm

TYPES = ("flat", "new", "room", "house", "land", "commercial")
TYPE_LABELS = {
    "flat": "Квартира",
    "new": "Новостройка",
    "room": "Комната",
    "house": "Дом",
    "land": "Участок",
    "commercial": "Коммерция",
}


@dataclass
class Fields:
    type: str | None = None
    deal: str = "sale"          # sale | rent
    rooms: int | None = None    # 0 = студия, 5 = 5 и больше
    area: float | None = None   # м² (квартира, дом, помещение)
    land: float | None = None   # сотки
    floor: int | None = None
    floors: int | None = None
    price: int | None = None    # ₽ (за аренду — в месяц)
    street: str | None = None
    house: str | None = None
    phones: list[str] = field(default_factory=list)
    is_request: bool = False    # «куплю/ищу» — запрос покупателя, не объект


# ─── цена ──────────────────────────────────────────────────────────────────
_NUM = r"\d{1,3}(?:[   .,']\d{3})+|\d+(?:[.,]\d+)?"
_PRICE_RE = re.compile(
    rf"(?P<num>{_NUM})\s*(?P<unit>млн\.?|миллион\w*|млрд|т\.?\s?р\.?|тыс\.?\s?(?:руб\w*|р\.?)?|тысяч\w*|к\b)?\s*(?P<cur>₽|руб\w*|р\.?(?=\s|$|[,;!)*])|rub)?",
    re.IGNORECASE,
)
_PER_M2 = re.compile(r"^\s*(?:₽|руб\w*|р\.?)?\s*(?:/|за|за\s1)\s*(?:м2|м²|м\b|м\.|кв|сот|метр|квадрат)", re.IGNORECASE)
_PRICE_WORD = re.compile(r"цен[аы]|стоимост|продаж|прода[её]тся|₽|руб|млн|т\.?р", re.IGNORECASE)


def _to_number(raw: str) -> float | None:
    s = raw.replace(" ", " ").replace(" ", " ").replace("'", " ")
    if re.fullmatch(r"\d{1,3}(?:[ .,]\d{3})+", s):
        return float(re.sub(r"[ .,]", "", s))
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def extract_price(text: str, deal: str = "sale") -> int | None:
    """Самая правдоподобная цена объекта в тексте (₽)."""
    best: tuple[int, int] | None = None  # (score, value)
    for m in _PRICE_RE.finditer(text):
        num = _to_number(m.group("num"))
        if num is None:
            continue
        unit = (m.group("unit") or "").lower()
        cur = m.group("cur")
        tail = text[m.end(): m.end() + 12]
        if _PER_M2.match(tail) or re.match(r"\s*(?:м2|м²|кв\.?\s?м|сот|га|эт|этаж|комн|мин|км|год|лет|%)", tail, re.I):
            continue
        before = text[max(0, m.start() - 3): m.start()]
        if re.search(r"[+\d]$", before):  # кусок телефона/даты
            continue
        if unit.startswith(("млн", "миллион")):
            value = num * 1_000_000
        elif unit.startswith("млрд"):
            value = num * 1_000_000_000
        elif unit.startswith(("т", "к")):
            value = num * 1_000
        else:
            value = num
        value = int(round(value))
        lo, hi = (5_000, 3_000_000) if deal == "rent" else (300_000, 3_000_000_000)
        if not lo <= value <= hi:
            continue
        # 89181234567 — это телефон, а не 89 млрд
        if not unit and re.fullmatch(r"[78]\d{10}", m.group("num").replace(" ", "")):
            continue
        ctx = text[max(0, m.start() - 25): m.end() + 5]
        score = (3 if unit or cur else 0) + (2 if _PRICE_WORD.search(ctx) else 0) + (1 if " " in m.group("num") else 0)
        if score == 0:
            continue
        if best is None or score > best[0]:
            best = (score, value)
    return best[1] if best else None


# ─── площадь ───────────────────────────────────────────────────────────────
_AREA_RE = re.compile(
    r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>м2|м²|м\.?\s?кв|кв\.?\s?м\.?|квм|кв\b|метр\w*|м\b)",
    re.IGNORECASE,
)
_AREA_LABEL_RE = re.compile(
    r"(?:площадь|пл\.|s\s*=|общая)\s*(?:дома|квартиры|помещения|общая)?\s*[:=-]?\s*(?P<num>\d+(?:[.,]\d+)?)(?!\s*(?:сот|га))",
    re.IGNORECASE,
)
_LAND_RE = re.compile(r"(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>сот\w*|сот\.?|га\b|гектар\w*)", re.IGNORECASE)


def extract_areas(text: str) -> tuple[float | None, float | None]:
    """(площадь м², участок в сотках). Сотки и метры не путаем."""
    land = None
    for m in _LAND_RE.finditer(text):
        v = _to_number(m.group("num"))
        if v is None:
            continue
        if m.group("unit").lower().startswith(("га", "гект")):
            v *= 100
        if 0.5 <= v <= 100_000:
            land = v
            break
    area = None
    for m in _AREA_RE.finditer(text):
        v = _to_number(m.group("num"))
        if v and 8 <= v <= 20_000:
            area = v
            break
    if area is None:
        m = _AREA_LABEL_RE.search(text)
        if m:
            v = _to_number(m.group("num"))
            if v and 8 <= v <= 20_000:
                area = v
    return area, land


# ─── комнаты, этаж ─────────────────────────────────────────────────────────
_ROOM_WORDS = {
    "однушк": 1, "однокомн": 1, "двушк": 2, "двухкомн": 2, "двухк": 2,
    "трешк": 3, "трёшк": 3, "трехкомн": 3, "трёхкомн": 3, "четырехкомн": 4, "четырёхкомн": 4,
}
_ROOMS_RE = re.compile(r"(?<![\d/])(?P<n>[1-9])\s*-?\s*(?:х\s*)?(?:к\b|кк\b|к\.|ккв|комн\w*|кв\b|-?ка\b)", re.IGNORECASE)
_EURO_RE = re.compile(r"евро\s*-?\s*(?P<n>[1-6])|(?P<n2>[1-6])\s*-?\s*евро", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(?<![\d/])(?P<f>\d{1,2})\s*/\s*(?P<t>\d{1,2})(?![\d/])(?:\s*эт\w*)?", re.IGNORECASE)
_FLOOR2_RE = re.compile(r"(?P<f>\d{1,2})\s*(?:-?й|-?ом)?\s*эт(?:аж)?\w*\s*(?:из\s*(?P<t>\d{1,2}))?", re.IGNORECASE)
_FLOOR3_RE = re.compile(r"этаж\w*\s*[:\-]?\s*(?P<f>\d{1,2})\s*(?:(?:/|из)\s*(?P<t>\d{1,2}))?", re.IGNORECASE)


def extract_rooms(text: str) -> int | None:
    t = norm(text)
    if re.search(r"\bстуди", t):
        return 0
    m = _EURO_RE.search(t)
    if m:
        return int(m.group("n") or m.group("n2"))
    for stem, n in _ROOM_WORDS.items():
        if stem in t:
            return n
    m = _ROOMS_RE.search(t)
    if m:
        n = int(m.group("n"))
        return min(n, 5)
    return None


def extract_floor(text: str) -> tuple[int | None, int | None]:
    for rx in (_FLOOR_RE, _FLOOR3_RE, _FLOOR2_RE):
        m = rx.search(text)
        if m:
            f = int(m.group("f"))
            t = int(m.group("t")) if m.group("t") else None
            if t is not None and (f > t or t > 60):
                continue
            if 0 < f <= 60:
                return f, t
    m = re.search(r"(?:этажност\w*|этажей)\s*[:\-]?\s*(\d{1,2})", text, re.I)
    return None, (int(m.group(1)) if m else None)


# ─── телефоны ──────────────────────────────────────────────────────────────
_PHONE_RE = re.compile(r"(?:\+?7|8)[\s\-‐(]*\d{3}[\s\-‐)]*\d{3}[\s\-‐]*\d{2}[\s\-‐]*\d{2}(?!\d)")


def normalize_phone(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 10 and d[0] == "9":
        d = "7" + d
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    return None


def extract_phones(text: str) -> list[str]:
    out: list[str] = []
    for m in _PHONE_RE.finditer(text):
        p = normalize_phone(m.group(0))
        if p and p not in out:
            out.append(p)
    return out


# ─── адрес ─────────────────────────────────────────────────────────────────
_STREET_RE = re.compile(
    r"(?:\bул(?:ица)?\.?|\bпр(?:-?к?т|оспект)\.?|\bпер(?:еулок)?\.?|\bб-?р\.?|\bбульвар|\bпроезд|\bшоссе)\s*"
    r"(?P<name>[А-ЯЁA-Z0-9][\wЁё.\- ]{2,40}?)"
    r"(?:\s*,?\s*(?:д\.?\s*)?(?P<house>\d{1,4}\s?[а-яА-Я]?(?:/\d{1,3})?(?:\s?к(?:орп)?\.?\s?\d)?))?(?=[,.;\n)]|\s{2}|$|\s+(?:эт|жк|д\b|район|мкр|\d+\s*[кк]))",
)


def extract_street(text: str) -> tuple[str | None, str | None]:
    m = _STREET_RE.search(text)
    if not m:
        return None, None
    name = re.sub(r"\s+", " ", m.group("name")).strip(" .-")
    house = (m.group("house") or "").replace(" ", "") or None
    if len(name) < 3:
        return None, None
    return name, house


# ─── тип, сделка, запрос ───────────────────────────────────────────────────
def detect_type(text: str) -> str | None:
    t = norm(text)
    if re.search(r"\b(?:офис|помещени|псн|склад|магазин|торгов|коммерц|арендный бизнес|готовый бизнес|кафе|автомойк|салон красоты|здание)", t):
        return "commercial"
    has_house = re.search(r"\b(?:дом|домовлад|коттедж|таунхаус|дуплекс|полдома|пол дома|дача|дачу)\b", t)
    if has_house:
        return "house"
    if re.search(r"\b(?:участ|зем(?:ля|ельн)|ижс|снт|лпх|сот(?:ок|ки|\.|\b))", t):
        return "land"
    if re.search(r"\b(?:комнат[ау]\b|гостинк|койко)", t) and not re.search(r"\d\s*-?\s*комнат", t):
        return "room"
    if re.search(r"\b(?:новострой|от застройщик|переуступк|дду\b|котлован|сдача\s+(?:в\s+)?\d|сдан в|черновая|предчистов)", t):
        return "new"
    if re.search(r"\b(?:квартир|студи|кв\b|однушк|двушк|трешк|евро\s*-?\d|\d\s*-?\s*к\b|\d\s*-?\s*комн)", t):
        return "flat"
    return None


def detect_deal(text: str) -> str:
    t = norm(text)
    if re.search(r"\b(?:сдам|сдается|сдаётся|сдаю|в аренду|аренда|снять|посуточн|долгосроч)", t) and not re.search(r"\bарендн\w* бизнес", t):
        return "rent"
    return "sale"


def is_buyer_request(text: str) -> bool:
    t = norm(text)[:160]
    return bool(re.search(r"^(?:[^\w]*)(?:куплю|ищу|ищем|нужн[аоы]|требуется|подберу|подберите|запрос|в поиске|рассмотр\w+ варианты|есть клиент|есть покупател)", t))


# ─── сборка ────────────────────────────────────────────────────────────────
def extract(text: str) -> Fields:
    f = Fields()
    f.is_request = is_buyer_request(text)
    f.deal = detect_deal(text)
    f.type = detect_type(text)
    f.area, f.land = extract_areas(text)
    f.rooms = extract_rooms(text) if f.type in (None, "flat", "new", "house") else None
    if f.type in ("land", "commercial"):
        f.rooms = None
    f.floor, f.floors = extract_floor(text) if f.type not in ("land",) else (None, None)
    f.price = extract_price(text, f.deal)
    f.street, f.house = extract_street(text)
    f.phones = extract_phones(text)
    if f.type is None and f.rooms is not None:
        f.type = "flat"
    if f.type == "land" and f.area and not f.land and f.area <= 100 and re.search(r"сот", text, re.I):
        f.land, f.area = f.area, None
    return f


# ─── разбиение мультиобъектных сообщений ──────────────────────────────────
_SEP_RE = re.compile(r"\n\s*\n|\n\s*[-–—=_*•·~]{3,}\s*\n|\n(?=\s*(?:\d{1,2}[.)]|[🔹🔸▪️➡️✅🏠🔥📍⭐️💥🏡]\s*\S))")


def split_objects(text: str) -> list[str]:
    """Режем сообщение на объекты, только если в нём реально несколько объектов.

    Абзацы одного объекта (заголовок / описание / цена / контакты) не дробим:
    кусок считается объектом, только если в нём есть своя цена.
    """
    parts = [p.strip() for p in _SEP_RE.split(text or "") if p and p.strip()]
    if len(parts) <= 1:
        return [text.strip()] if text and text.strip() else []
    with_price = [i for i, p in enumerate(parts) if extract_price(p) is not None]
    if len(with_price) <= 1:
        return [text.strip()]
    objects: list[str] = []
    header: list[str] = []
    current: list[str] = []
    for i, p in enumerate(parts):
        if i in with_price:
            current.append(p)
            objects.append("\n".join(current))
            current = []
        else:
            if not objects and not current and i < with_price[0] and not extract_areas(p)[0]:
                header.append(p)
            else:
                current.append(p)
    tail = "\n".join(current).strip()  # контакты в конце — общие для всех объектов
    head = "\n".join(header).strip()
    result = []
    for o in objects:
        chunk = o
        if head and len(head) < 300:
            chunk = head + "\n" + chunk
        if tail and len(tail) < 300:
            chunk = chunk + "\n" + tail
        result.append(chunk)
    return result

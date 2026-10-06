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
_NUM = r"\d{1,3}(?:[   .,']\d{3})+(?![\d])|\d+(?:[.,]\d+)?"
_PRICE_RE = re.compile(
    rf"(?P<num>{_NUM})\s*(?P<unit>млн\.?|миллион\w*|лям\w*|лимон\w*|🍋|млрд|т\.?\s?р\.?|тыс\.?\s?(?:руб\w*|р\.?)?|тысяч\w*|к\b)?\s*(?P<cur>₽|руб\w*|р\.?(?=\s|$|[,;!)*])|rub)?",
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


_NOT_PRICE_BEFORE = re.compile(
    r"(?:обрем\w*|обремен\w*|дкп|договор\w*|задат\w*|аванс\w*|комисс\w*|кэшб\w*|взнос\w*|доплат\w*|"
    r"остат\w*|маткап\w*|мат\.?\s*кап\w*|сертификат\w*|налог\w*|коммунал\w*|ку\b|залог\w*|депозит\w*)"
    r"[^\n\d]{0,12}$",
    re.IGNORECASE,
)
# Слова перед числом в той же строке: «это цена» / «это не цена объекта»
_PRICE_WORD_LINE = re.compile(r"цен[аеуы]|стоимост\w*|💰|🪙|прода\w*\s+за|за\s*$", re.IGNORECASE)
_NOT_PRICE_WORD = re.compile(
    r"обрем\w*|обремен\w*|дкп|договор\w*|разбивк\w*|задат\w*|аванс\w*|комисс\w*|кэшб\w*|взнос\w*|доплат\w*|"
    r"остат\w*|долг\w*|ипотек\w*|маткап\w*|мат\.?\s*кап\w*|сертификат\w*|налог\w*|коммунал\w*|залог\w*|депозит\w*|"
    r"кредит\w*|рассрочк\w*|первоначал\w*|пв\b|ку\b",
    re.IGNORECASE,
)
_KEYCAP_RE = re.compile("(\\d)️?⃣")


def normalize_digits(text: str) -> str:
    """«4⃣9⃣0⃣0⃣» → «4900» (цифры-эмодзи), «5. 600 т р» → «5600 т р»."""
    text = _KEYCAP_RE.sub(r"\1", text or "")
    text = re.sub(r"\*+", "", text)
    # «5  500 000» (двойной пробел) → «5 500 000»
    text = re.sub(r"(\d)[   ]{2,}(?=\d{3}\b)", r"\1 ", text)
    # «5700🔑🍋» → «5700🍋»: значки между ценой и «лимоном»/«мешком»
    text = re.sub(r"(\d)[ \t]*(?:[^\w\s.,:;()\-+/🍋💰🪙]️?[ \t]*){1,3}(?=[🍋💰])", r"\1", text)
    return re.sub(r"(\d)[.,]\s(\d{3})(?=\s*(?:т\.?\s?р|тыс|000|₽|руб))", r"\1\2", text, flags=re.IGNORECASE)


_ARTICLE_RE = re.compile(r"артикул\w*\s*[:№#\-]?\s*(\d{1,7})", re.IGNORECASE)


def extract_article(text: str) -> str | None:
    """«Артикул: 337» — номер объекта на сайте СТРЕЛ."""
    m = _ARTICLE_RE.search(text or "")
    return m.group(1) if m else None


_PRICE_LABEL_BEFORE = re.compile(r"(?:цен[аы]|стоимост\w*|💰|🪙)\s*[:\-–—]?\s*$", re.IGNORECASE)


def extract_price(text: str, deal: str = "sale", strict: bool = False) -> int | None:
    """Самая правдоподобная цена объекта в тексте (₽).

    strict=True — только «настоящая» цена: с единицей (млн, т.р., ₽) или словом
    «цена/стоимость». Так «Обрем: сбер 3800» и «В дкп 1350» ценой не считаются."""
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
        if re.search(r"[+\d]\Z", before):  # кусок телефона/даты (\Z: «$» сработал бы и перед переносом строки)
            continue
        # Что написано в строке перед числом: последнее ключевое слово решает.
        # «Обрем: Альфа-банк 2800 млн», «📄 ДКП — 6.5 млн», «Разбивка 6100» — не цена объекта;
        # «Цена: 4650.000», «💰 7500», «ЦЕНА — 8,4 млн» — цена.
        prefix = text[text.rfind("\n", 0, m.start()) + 1: m.start()]
        prefix = re.split(r"[.!;|](?:\s|$)", prefix)[-1]  # только текущее предложение: «Ипотека возможна. 7 500 000»
        pos = max((x.end() for x in _PRICE_WORD_LINE.finditer(prefix)), default=-1)
        neg = max((x.end() for x in _NOT_PRICE_WORD.finditer(prefix)), default=-1)
        end = max(m.end("num"), m.end("unit"), m.end("cur"))  # без пробелов/переноса после числа
        after_bag = bool(re.match(r"[ \t]*(?:т\.?[ \t]?р\.?[ \t]*)?💰", text[end: end + 8]))  # та же строка
        if neg > pos and not after_bag:
            continue
        if re.match(r"[ \t]*(?:т\.?[ \t]?р\.?|тыс\.?|млн\.?|₽|руб\w*)?[ \t,]*(?:в\s+дкп|в\s+договор|обрем|разбивк|задат)",
                    text[end: end + 30], re.IGNORECASE):
            continue
        labelled = pos > neg or after_bag
        if unit.startswith(("млн", "миллион", "лям", "лимон", "🍋")):
            # «7,8🍋» = 7,8 млн; «11000 🍋» = 11 млн (в тысячах); «7 800 000🍋» — уже рубли
            value = num * 1_000_000 if num < 1000 else num * 1000 if num < 100_000 else num
        elif unit.startswith("млрд"):
            value = num * 1_000_000_000
        elif unit.startswith(("т", "к")):
            value = num * 1_000
        else:
            value = num
        value = int(round(value))
        lo, hi = (5_000, 3_000_000) if deal == "rent" else (300_000, 3_000_000_000)
        # «Цена 3200» — риелторы пишут цену в тысячах: это 3 200 000
        if not unit and not cur and labelled and value < lo <= value * 1000 <= hi:
            value *= 1000
        if not lo <= value <= hi:
            continue
        # 89181234567 — это телефон, а не 89 млрд
        if not unit and re.fullmatch(r"[78]\d{10}", m.group("num").replace(" ", "")):
            continue
        ctx = text[max(0, m.start() - 25): m.end() + 5]
        word = bool(_PRICE_WORD.search(ctx))
        if strict and not (unit or cur or labelled or word):
            continue
        # Подписанная цена («Цена: …», 💰) важнее всего остального в тексте
        score = (5 if labelled else 0) + (3 if unit or cur else 0) + (1 if word else 0) + (1 if " " in m.group("num") else 0)
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
# «2к», «2-х комн», «1-я квартира», «1-ая кв.», «3-ех комнатная»
_ROOMS_RE = re.compile(r"(?<![\d/.,])(?P<n>[1-9])\s*-?\s*(?:(?:х\s*)?(?:к\b|кк\b|к\.|ккв|комн\w*|ком\.?(?=\s*кв)|кв\b|-?ка\b)"
                       r"|(?:х|ая|я|ех|ух|ой|ую)\s*(?:кв\b|квартир\w*|комн\w*|к\b))",
                       re.IGNORECASE)
_FLOOR_OF_RE = re.compile(r"(?<![\d/.,])(?P<f>\d{1,2})[ \t]*из[ \t]*(?P<t>\d{1,2})(?![\d.,]|[ \t]*(?:комн|кв|м))", re.IGNORECASE)
_EURO_RE = re.compile(r"евро\s*-?\s*(?P<n>[1-6])|(?P<n2>[1-6])\s*-?\s*евро", re.IGNORECASE)
_FLOOR_RE = re.compile(r"(?<![\d/])(?P<f>\d{1,2})[ \t]*/[ \t]*(?P<t>\d{1,2})(?![\d/])(?:[ \t]*эт\w*)?", re.IGNORECASE)
# «5 этаж», «5-й эт. из 9» (но не «16-этажный», «этажей 16»)
_FLOOR2_RE = re.compile(r"(?<![\d/])(?P<f>\d{1,2})[ \t]*(?:-?й|-?ом)?[ \t]*эт(?!ажн|ажей|ажност)(?:аж)?\w*\.?[ \t]*(?:из[ \t]*(?P<t>\d{1,2}))?",
                        re.IGNORECASE)
# «Этаж 5/9», «этаж: 5 из 9»
_FLOOR3_RE = re.compile(r"этаж(?!н|ей|ност)\w*\.?[ \t]*[:\-]?[ \t]*(?P<f>\d{1,2})(?!\d)(?![\d.,]*[ \t]*(?:м|кв|сот))[ \t]*(?:(?:/|из)[ \t]*(?P<t>\d{1,2}))?",
                        re.IGNORECASE)


_MINI_RE = re.compile(r"\bмини\s*-?\s*(?P<n>[1-4])(?!\d)")
_EURO_KIND_RE = re.compile(r"\bевро\s*-?\s*(?P<n>[1-6])(?!\d)|\b(?P<n2>[1-6])\s*-?\s*евро|\bе(?P<n3>[1-6])\s*(?:кв|к\b|ккв|-?ка\b)")


def extract_room_kind(text: str) -> tuple[str, int] | None:
    """Тип планировки: («studio», 0) / («mini», 2) / («euro», 2) / («classic», 2)."""
    t = norm(text)
    m = _MINI_RE.search(t)
    if m:
        return "mini", int(m.group("n"))
    m = _EURO_KIND_RE.search(t)
    if m:
        return "euro", int(m.group("n") or m.group("n2") or m.group("n3"))
    if re.search(r"\bстуди", t):
        return "studio", 0
    n = _classic_rooms(t)
    return ("classic", n) if n is not None else None


def rooms_mask(kind: str | None, n: int | None) -> int:
    """Какие кнопки «Комнаты» показывают объект (бит 0 — студии, 1..3, 4 — «4+»).

    Студии = студии и мини-1; «1» = мини-1, 1-к, евро-2, мини-2; «2» = мини-2, 2-к, евро-3, мини-3…"""
    if n is None:
        return 0

    def bit(k: int) -> int:
        return 1 << max(0, min(k, 4))

    if kind == "studio" or n == 0:
        return bit(0)
    if kind == "euro":
        return bit(n - 1)
    if kind == "mini":
        return bit(n - 1) | bit(n)
    return bit(n)


def extract_rooms(text: str) -> int | None:
    rk = extract_room_kind(text)
    return rk[1] if rk else None


def _classic_rooms(t: str) -> int | None:
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


_ADDR_BEFORE = re.compile(r"(?:\bул\b|\bулиц|\bпр-?к?т\b|\bпер\b|\bпроезд|\bб-р\b|\bд\.|\bдом\b|\bшоссе|\bобход|\bлит)", re.IGNORECASE)


def _is_address_fraction(text: str, m: re.Match) -> bool:
    """«Домбайская 17/25», «39/2к7», «1/4к15» — номер дома, а не этаж."""
    line_start = text.rfind("\n", 0, m.start()) + 1
    if _ADDR_BEFORE.search(text[line_start: m.start()]):
        return True
    return bool(re.match(r"\s?(?:[кk]\s?\d|лит|корп|стр)", text[m.end(): m.end() + 6], re.IGNORECASE))


def extract_floor(text: str) -> tuple[int | None, int | None]:
    # Сначала явное «этаж 5/9», «5 этаж из 9», потом просто «5/9» (но не номер дома)
    for rx in (_FLOOR3_RE, _FLOOR2_RE, _FLOOR_OF_RE, _FLOOR_RE):
        for m in rx.finditer(text):
            if rx is _FLOOR_RE and _is_address_fraction(text, m):
                continue
            f = int(m.group("f"))
            t = int(m.group("t")) if m.group("t") else None
            if t is not None and (f > t or t > 60):
                continue
            if 0 < f <= 60:
                if t is None and rx is not _FLOOR_RE:
                    # «Этаж 17» без этажности — этажность поищем отдельно («17/25 эт.», «этажей 25»)
                    m2 = re.search(r"(?:этажност\w*|этажей)\s*[:\-]?\s*(\d{1,2})\b|(\d{1,2})\s*-?\s*этажн", text, re.I)
                    n = int(m2.group(1) or m2.group(2)) if m2 else None
                    if n and f <= n <= 60:
                        t = n
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
    r"(?:\s*,?\s*(?:\bд\.?\s*)?(?P<house>\d{1,4}\s?[а-яА-Я]?(?:/\d{1,3})?(?:\s?к(?:орп)?\.?\s?\d)?))?(?=[,.;:\n)]|\s{2}|$|\s+\(|\s+(?:эт|жк|д\b|район|мкр|\d+\s*[кк]))",
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
_HEAD_TYPES = [
    ("flat", r"\b(?:квартир|студи|однушк|двушк|тр[её]шк|евро\s*-?\d|мини\s*-?\d|\d\s*-?\s*к\.?\s*кв|\d\s*-?\s*к\b|е\d\s*кв)"),
    ("house", r"\b(?:дом|домовлад\w*|коттедж\w*|таунхаус\w*|дуплекс\w*|полдома|пол дома|дача|дачу)\b"),
    ("land", r"\b(?:участ\w*|земл\w*|земельн\w*|ижс|снт|лпх)\b"),
    ("commercial", r"\b(?:офис|помещени|псн|склад|магазин|торгов|коммерц|арендный бизнес|готовый бизнес|кафе|автомойк|салон красоты|здание)"),
    ("room", r"\b(?:комнат[ау]\b|гостинк|койко)"),
]


def _type_by_words(t: str) -> str | None:
    if re.search(r"\b(?:офис|помещени|псн|склад|магазин|торгов|коммерц|арендный бизнес|готовый бизнес|кафе|автомойк|салон красоты|здание)", t):
        return "commercial"
    if re.search(r"\b(?:дом|домовлад|коттедж|таунхаус|дуплекс|полдома|пол дома|дача|дачу)\b", t):
        return "house"
    if re.search(r"\b(?:участ|зем(?:ля|ельн)|ижс|снт|лпх|сот(?:ок|ки|\.|\b))", t):
        return "land"
    if re.search(r"\b(?:комнат[ау]\b|гостинк|койко)", t) and not re.search(r"\d\s*-?\s*комнат", t):
        return "room"
    if re.search(r"\b(?:квартир|студи|кв\b|однушк|двушк|трешк|евро\s*-?\d|мини\s*-?\d|\d\s*-?\s*к\b|\d\s*-?\s*комн|"
                 r"новострой|от застройщик|переуступк|дду\b|котлован|черновая|предчистов)", t):
        return "flat"  # новостройка — тоже квартира
    return None


def detect_type(text: str) -> str | None:
    """Тип объекта. Сначала смотрим первые строки — там пишут, ЧТО продаётся
    («ЕВРО 3-к КВАРТИРА», «ДОМ В КП…»); слова дальше по тексту («дом кирпичный»,
    «технические помещения») тип не перебивают."""
    lines = [ln for ln in (text or "").splitlines() if re.search(r"\w", ln)]
    head = norm(" ".join(lines[:3]))[:200]
    for kind, rx in _HEAD_TYPES:
        if re.search(rx, head):
            return kind
    return _type_by_words(norm(text))


def detect_deal(text: str) -> str:
    t = norm(text)
    if (re.search(r"\b(?:сдам|сдается|сдаётся|сдаю|сдаем|сдаём|сдаются|в аренду|аренда|арендa|посуточн|долгосрочн\w* аренд|"
                  r"на длительный срок|помесячно|в месяц|/мес|руб\.?/мес|т\.?р\.?/мес)", t)
            and not re.search(r"\bарендн\w* бизнес|\bарендатор\w* (?:есть|оплачива)|\bс арендатор", t)):
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
    # «ЖК Золотая Линия, 35,1 м², 4/4 эт.» — без слова «квартира», но это квартира
    if f.type is None and f.area and (f.floor or re.search(r"\bж/?к\b", norm(text))):
        f.type = "flat"
    if f.type == "land" and f.area and not f.land and f.area <= 100 and re.search(r"сот", text, re.I):
        f.land, f.area = f.area, None
    return f


# ─── разбиение мультиобъектных сообщений ──────────────────────────────────
_SEP_RE = re.compile(r"\n\s*\n|\n\s*[-–—=_*•·~]{3,}\s*\n|\n(?=\s*(?:\d{1,2}[.)](?![\d,])|[🔹🔸▪️➡️✅🏠🔥📍⭐️💥🏡]\s*\S))")


# Строки, которые идут ПОСЛЕ цены и ещё относятся к тому же объекту
_TAIL_RE = re.compile(
    r"^[\W_]*(?:торг|заклад|обмен|ипотек|возмож|рассмотр|срочн|собственн|без\s+комисс|комисс|"
    r"документ|подробн|звон|пиш|тел|whats|ватсап|вацап|чистая|свободная|ключи|показ|оплат|"
    r"кэшб|агент|риелтор|\+?[78][\s(\-]*\d)|^[\W_]*\S{0,3}[\W_]*$|обсужда\w*[\s.!]*$",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"^[\W_]*(?:продаю|продаются|продажа|в\s+продаже|подборка|актуальн|прайс|варианты|объекты)\b|:\s*$",
                        re.IGNORECASE)


def _is_header(s: str) -> bool:
    """Общая шапка подборки: «Продаю квартиры:», «📣СРОЧНО📣» — без параметров объекта."""
    s = s.strip()
    if _has_size(s):
        return False
    if _HEADER_RE.search(s):
        return True
    return len(s) < 40 and not re.search(r"\d|\bж/?к\b|\bул\b|улиц|этаж", s, re.IGNORECASE)


def _has_price(s: str) -> bool:
    return extract_price(s, strict=True) is not None or extract_price(s, "rent", strict=True) is not None


def _has_size(s: str) -> bool:
    area, land = extract_areas(s)
    return area is not None or land is not None or extract_rooms(s) is not None


def _split_by_lines(text: str) -> list[str]:
    """Портянка без пустых строк между объектами: объект заканчивается своей ценой
    (и короткими хвостами вроде «Заклад обсуждаем»), дальше начинается следующий."""
    lines = text.splitlines()
    prices = [i for i, ln in enumerate(lines) if _has_price(ln)]
    if len(prices) < 2:
        return [text.strip()]
    # Шапка для всех объектов: «Продаю квартиры в ЖК …:» до первого объекта
    header: list[str] = []
    start = 0
    while start < prices[0] and _HEADER_RE.search(lines[start].strip()) and not _has_size(lines[start]):
        header.append(lines[start].strip())
        start += 1
    segments: list[list[str]] = []
    for k, p in enumerate(prices):
        nxt = prices[k + 1] if k + 1 < len(prices) else len(lines)
        j = p + 1
        if k + 1 < len(prices):
            while j < nxt and lines[j].strip() and _TAIL_RE.search(lines[j].strip()):
                j += 1
        else:
            j = len(lines)
        segments.append(lines[start:j])
        start = j
    # Кусок без площади/комнат — не отдельный объект (например, «цена с мебелью …»)
    merged: list[list[str]] = []
    for seg in segments:
        if merged and not _has_size("\n".join(seg)):
            merged[-1] += seg
        else:
            merged.append(seg)
    if len(merged) < 2:
        return [text.strip()]
    # Общие контакты в конце (строки с телефоном после последней цены) — всем объектам
    last = merged[-1]
    tail_from = len(last)
    while tail_from > 0 and not _has_price(last[tail_from - 1]):
        tail_from -= 1
    tail = [ln for ln in last[tail_from:] if ln.strip()]
    common_tail = tail if tail and extract_phones("\n".join(tail)) and len("\n".join(tail)) < 300 else []
    if common_tail:
        merged[-1] = last[:tail_from]
    out = []
    for seg in merged:
        chunk = "\n".join(header + [ln for ln in seg] + common_tail).strip()
        if chunk:
            out.append(chunk)
    return out


def split_objects(text: str) -> list[str]:
    """Режем сообщение на объекты, только если в нём реально несколько объектов.

    Абзацы одного объекта (заголовок / описание / цена / контакты) не дробим:
    кусок считается объектом, только если в нём есть своя цена. Если объекты
    идут сплошным текстом без пустых строк — режем по строкам с ценой.
    """
    text = (text or "").strip()
    if not text:
        return []
    n_prices = sum(1 for ln in text.splitlines() if _has_price(ln))
    if n_prices < 2:
        return [text]
    parts = [p.strip() for p in _SEP_RE.split(text) if p and p.strip()]
    with_price = [i for i, p in enumerate(parts) if _has_price(p)]
    # Пустые строки делят не на все объекты (в каком-то куске две цены) — режем по строкам
    if len(with_price) < n_prices:
        return _split_by_lines(text)
    if len(with_price) <= 1:
        return [text]
    objects: list[str] = []
    header: list[str] = []
    current: list[str] = []
    for i, p in enumerate(parts):
        if i in with_price:
            current.append(p)
            objects.append("\n".join(current))
            current = []
        else:
            # Шапка — только «Продаю …:», «📣СРОЧНО📣» и т.п. до первого объекта,
            # а не первые строки самого объекта («✅ 1 к.кв.», «✅ ЖК …»)
            if not objects and not current and i < with_price[0] and _is_header(p):
                header.append(p)
            else:
                current.append(p)
    tail = "\n".join(current).strip()
    # Хвост с телефоном — общие контакты для всех; остальное — продолжение последнего объекта
    if tail and not (extract_phones(tail) and len(tail) < 300):
        objects[-1] = objects[-1] + "\n" + tail
        tail = ""
    head = "\n".join(header).strip()
    result = []
    for o in objects:
        chunk = o
        if head and len(head) < 300:
            chunk = head + "\n" + chunk
        if tail:
            chunk = chunk + "\n" + tail
        result.append(chunk)
    return result

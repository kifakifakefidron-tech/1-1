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
    r"(?:обр\b\.?|обрем\w*|обремен\w*|дкп|договор\w*|задат\w*|аванс\w*|комисс\w*|кэшб\w*|взнос\w*|доплат\w*|"
    r"остат\w*|маткап\w*|мат\.?\s*кап\w*|сертификат\w*|налог\w*|коммунал\w*|ку\b|залог\w*|депозит\w*)"
    r"[^\n\d]{0,12}$",
    re.IGNORECASE,
)
# Слова перед числом в той же строке: «это цена» / «это не цена объекта»
_PRICE_WORD_LINE = re.compile(r"цен[аеуы]|стоимост\w*|💰|🪙|прода\w*\s+за|за\s*$|\bвсего\b|\bитого\b", re.IGNORECASE)
_NOT_PRICE_WORD = re.compile(
    r"\bобр\b\.?|обрем\w*|обремен\w*|дкп|договор\w*|разбивк\w*|задат\w*|аванс\w*|комисс\w*|кэшб\w*|взнос\w*|доплат\w*|"
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
        after = text[end: end + 30]
        # «6 млн. Задаток обсуждаем» — «задаток» уже в следующем предложении: цена остаётся ценой
        if re.match(r"[ \t]*(?:т\.?[ \t]?р\.?|тыс\.?|млн\.?|₽|руб\w*)?[ \t,]*(?:в\s+дкп|в\s+договор|обрем|разбивк|задат)",
                    after, re.IGNORECASE) and not (re.match(r"[ \t]*(?:т\.?[ \t]?р|тыс|млн|руб\w*)?\.[ \t]+[А-ЯЁ]", after)
                                                   or (text[end - 1] == "." and re.match(r"[ \t]+[А-ЯЁ]", after))):
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
_AREA_SPLIT_RE = re.compile(
    r"(?<![\d/])(?P<num>\d+(?:[.,]\d+)?)\s*/\s*\d+(?:[.,]\d+)?(?:\s*/\s*\d+(?:[.,]\d+)?)?\s*(?:м2|м²|кв\.?\s?м\.?|квм|м\b)",
    re.IGNORECASE)


# «35,6/11/16», «42/5/9 (+балкон)» — площадь/этаж/этажность одной записью (этаж ≤ этажности; «38/20/12» — это
# общая/жилая/кухня: 20 > 12, не подходит)
_AREA_FLOOR_RE = re.compile(r"(?<![\d/.,])(?P<a>\d{2,3}(?:[.,]\d{1,2})?)[ \t]*/[ \t]*(?P<f>\d{1,2})[ \t]*/[ \t]*(?P<t>\d{1,2})(?![\d/.,])"
                            r"(?![ \t]*(?:м2|м²|кв|м\b))")


def area_floor(text: str) -> tuple[float, int, int] | None:
    for m in _AREA_FLOOR_RE.finditer(text or ""):
        a, f, t = _to_number(m.group("a")), int(m.group("f")), int(m.group("t"))
        if a and 12 <= a <= 1000 and 0 < f <= t <= 60:
            return a, f, t
    return None


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
    af = area_floor(text)
    if af:
        return af[0], land
    # «Площадь: 38/20/12м» — общая/жилая/кухня: берём первую (общую), а не кухню перед «м»
    m = _AREA_SPLIT_RE.search(text)
    if m:
        v = _to_number(m.group("num"))
        if v and 8 <= v <= 20_000:
            return v, land
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
_EURO_KIND_RE = re.compile(r"\bевро\s*-?\s*(?P<n>[1-6])(?!\d)|\b(?P<n2>[1-6])\s*-?\s*евро|\bе\s*-?\s*(?P<n3>[1-6])\s*(?:кв|к\b|ккв|-?ка\b)")


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


_MULTI_FLOOR = re.compile(
    r"этаж(?:и|ах)\s*[:\-]?\s*(\d{1,2})(?:\s*,\s*\d{1,2})*\s*(?:и|,)\s*(\d{1,2})\b"      # «этажи 2 и 6», «этажи 2, 6»
    r"|\bна\s+(\d{1,2})(?:\s*,\s*\d{1,2})*\s*и\s*(\d{1,2})\s*-?\s*(?:м\s+)?этаж", re.I)   # «на 3 и 7 этажах»


def multi_units(text: str) -> int:
    """Сколько квартир на разных этажах в одном посте («этажи 2 и 6» → 2); 0 — одна."""
    m = _MULTI_FLOOR.search(text or "")
    if not m:
        return 0
    nums = re.findall(r"\d{1,2}", m.group(0))
    return len(set(nums)) if len(set(nums)) > 1 else 0


# Объект уже под задатком / забронирован — с продажи снят (акт 14.10). «Задаток обсуждаем», «задаток от 50 т.р.» —
# это условия сделки, не снятие.
_DEPOSIT = re.compile(
    r"\b(?:под\s+задат\w*|на\s+задат\w*|в\s+задатк\w*|задат\w*\s+(?:уже\s+)?(?:внес[её]н\w*|внесли|получ\w*|взят\w*|есть)"
    r"|(?:внес[её]н\w*|внесли|взяли|взят\w*|получ\w*|есть)\s+задат\w*|забронирован\w*|в\s+брони|на\s+брони|бронь\s+(?:до|внес)\w*)",
    re.IGNORECASE)


def under_deposit(text: str | None) -> bool:
    return bool(_DEPOSIT.search(text or ""))


def extract_floor(text: str) -> tuple[int | None, int | None]:
    # Сначала явное «этаж 5/9», «5 этаж из 9», потом просто «5/9» (но не номер дома)
    for rx in (_FLOOR3_RE, _FLOOR2_RE, _FLOOR_OF_RE):
        if rx.search(text):
            break
    else:
        af = area_floor(text)   # «35,6/11/16» — этаж 11 из 16 (а не «6/11»)
        if af:
            return af[1], af[2]
    for rx in (_FLOOR3_RE, _FLOOR2_RE, _FLOOR_OF_RE, _FLOOR_RE):
        for m in rx.finditer(text):
            if rx is _FLOOR_RE and _is_address_fraction(text, m):
                continue
            if rx is _FLOOR_RE and re.match(r"[ \t]*(?:част|дол[яиь])", text[m.end(): m.end() + 10], re.I):
                continue   # «1/3 часть жилого дома», «1/2 доли» — доля, не этаж
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
# Телефон в любом написании агентов: +7/7/8 в начале или 10 цифр с девятки; скобки, пробелы, дефисы (любые тире),
# точки; цифры после кода — любыми группами («8(918)33-260-33», «8.918.123.45.67», «9181234567»). Перевод строки
# разделителем не считается — номер не склеивается из двух строк (цена + этаж и т. п.).
_SEP = r"[ \t\u00a0\-‐‑–—.]{0,2}"
_PHONE_RE = re.compile(
    r"(?<![\d+])(?P<pre>\+\s?7|7|8)?" + _SEP + r"\(?" + _SEP + r"(?P<code>[3-9]" + _SEP + r"\d" + _SEP + r"\d)" + _SEP + r"\)?" + _SEP
    + r"\d(?:" + _SEP + r"\d){6}(?!\d)")
# Подписи перед номером, которые тоже убираем вместе с ним: «WA:», «тел.», «📞», «Telegram -» …
_PHONE_LABEL_RE = re.compile(
    r"(?:(?:тел(?:ефон)?|т|моб|сот|whats\s?app|wa|ватсап|вотсап|tg|telegram|телеграм|max|макс|viber|вайбер|звоните|пишите)"
    r"\.?\s*[:\-–—]?\s*|[\U0001F4DE\U0001F4E9\U0001F4F1\u260E\u2706\U0001F4F2\U0001F4E7]\uFE0F?\s*)+$", re.I)


def find_phones(text: str) -> list[tuple[int, int, str]]:
    """Все номера в тексте: (начало, конец, «+7…»). Без кода 7/8 — только мобильные (с девятки)."""
    out, pos, text = [], 0, text or ""
    while True:   # сдвиг по одному символу: отвергнутое совпадение («6 300 000 8 9…») не должно «съесть» номер
        m = _PHONE_RE.search(text, pos)
        if m is None:
            return out
        p = normalize_phone(m.group(0))
        if p and (m.group("pre") or m.group("code").startswith("9")):
            out.append((m.start(), m.end(), p))
            pos = m.end()
        else:
            pos = m.start() + 1


def mask_phones(text: str, repl: str) -> str:
    """Номера → метка (для нейросети: ей номера не нужны)."""
    out, pos = [], 0
    for a, b, _ in find_phones(text or ""):
        out.append(text[pos:a] + repl)
        pos = b
    return "".join(out) + (text or "")[pos:]


def remove_phones(text: str) -> str:
    """Вырезает номера вместе с подписью перед ними («📩WA:8(918)…» → «»)."""
    if not text:
        return text or ""
    found = find_phones(text)
    if not found:
        return text
    parts, pos = [], 0
    for a, b, _ in found:
        head = text[pos:a]
        lab = _PHONE_LABEL_RE.search(head)
        parts.append(head[:lab.start()] if lab else head)
        pos = b
    parts.append(text[pos:])
    return "".join(parts)


def normalize_phone(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == 10 and d[0] == "9":
        d = "7" + d
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    return None


def extract_phones(text: str) -> list[str]:
    out: list[str] = []
    for _, _, p in find_phones(text):
        if p not in out:
            out.append(p)
    return out


# ─── адрес ─────────────────────────────────────────────────────────────────
_STREET_RE = re.compile(
    r"(?:\bул(?:ица)?\.?|\bпр(?:-?к?т|оспект)\.?|\bпер(?:еулок)?\.?|\bб-?р\.?|\bбульвар|\bпроезд|\bшоссе)\s*"
    r"(?P<name>[А-ЯЁA-Z0-9][\wЁё.\- ]{2,40}?)"
    r"(?:\s*,?\s*(?:\bд\.?\s*)?(?P<house>\d{1,4}\s?[а-яА-Я]?(?:/\d{1,3})?(?:\s?к(?:орп)?\.?\s?\d)?))?(?=[,.;:\n)]|\s{2}|$|\s+\(|\s+(?i:эт|жк|д\b|район|мкр|\d+\s*[кк]))",
)


# Тип улицы ПОСЛЕ названия: «Ленинский переулок 14», «Виноградная улица, 133», «Ростовское шоссе 30».
# Название — 1–3 слова с большой буквы, последнее — как у названий улиц (Ленинский, Красная, Тургенева…)
_STREET_POST_RE = re.compile(
    r"(?:^|(?<=[\s,:;(]))(?P<name>[А-ЯЁ][а-яё]+(?:[\s-][А-ЯЁа-яё][а-яё]+){0,2}?)\s+"
    r"(?:улица|ул\.?|переулок|пер\.?|проезд|пр-д|проспект|пр-кт|бульвар|б-р|шоссе|тупик)(?![а-яё])"
    r"(?:[ \t]*,?[ \t]*(?:д\.?[ \t]*)?(?P<house>\d{1,4}(?:[ \t]?[а-яА-Я](?![а-яё\d]))?(?:/\d{1,3})?(?:[ \t]?к(?:орп)?\.?[ \t]?\d)?))?",
)
_POST_NAME_END = re.compile(r"(?:ая|яя|ой|ий|ый|ое|ее|ин|ын|ов|ев|ёв|ова|ева|ёва|ина|ына|ского|цкого|ых|их|ого|его|ая)$", re.IGNORECASE)


def _street_postfix(text: str) -> tuple[str | None, str | None]:
    for m in _STREET_POST_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group("name")).strip(" .-")
        if len(name) >= 3 and _POST_NAME_END.search(name.split()[-1]):
            return name, (m.group("house") or "").replace(" ", "") or None
    return None, None


def extract_street(text: str) -> tuple[str | None, str | None]:
    m = _STREET_RE.search(text)
    pm = next((x for x in _STREET_POST_RE.finditer(text)
               if _POST_NAME_END.search(x.group("name").split()[-1])), None)
    # «Ленинский переулок 14 к2»: тип улицы стоит после названия — иначе «переулок 14 к2» прочитался бы как улица «14 к»
    if not m or (pm and pm.start() < m.start()):
        return _street_postfix(text)
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


_COMMUNAL = re.compile(r"\bком\.?\s*(?:плат|услуг)|\bкоммунал|\bкомунал|\bку\b|\bк/у\b|\bжку\b|"
                       r"\bсч[её]тчик|свет\s*(?:,|и)?\s*вод|за\s+свет|\bквартплат")


def has_communal(text: str) -> bool:
    """Упомянуты коммунальные платежи («+ком платежи», «КУ», «коммуналка», «счётчики») — признак настоящей аренды."""
    return bool(_COMMUNAL.search(norm(text).replace("ё", "е")))


def has_rent_words(text: str) -> bool:
    return detect_deal(text, need_communal=False) == "rent"


def detect_deal(text: str, need_communal: bool = True) -> str:
    """Аренда — только если «сдам/сдаётся/аренда…» И упомянуты коммунальные платежи (решение Артёма 07.10:
    без этого в аренду попадало много лишнего — «дом сдаётся в 2026», продажи с арендатором и т.п.)."""
    t = norm(text)
    if need_communal and not has_communal(text):
        return "sale"
    if (re.search(r"\b(?:сдам|сдается|сдаётся|сдаю|сдаем|сдаём|сдаются|в аренду|аренда|арендa|посуточн|долгосрочн\w* аренд|"
                  r"на длительный срок|помесячно|в месяц|/мес|руб\.?/мес|т\.?р\.?/мес)", t)
            and not re.search(r"\bарендн\w* бизнес|\bарендатор\w* (?:есть|оплачива)|\bс арендатор", t)):
        return "rent"
    return "sale"


_SALE_WORDS = re.compile(r"(?:#|\b)(?:продам|продаю|продаем|продаём|прода[её]тся|продаются|продажа|в\s+дкп|дкп|по\s+дкп|ипотек\w*|"
                         r"обременен\w*|задаток|материнск\w*|маткапитал\w*|собственник\w*\s+(?:один|1|2|два)|"
                         r"(?:1|один|одн[аи]|2|два)\s+собственник)", re.I)


def sale_markers(text: str, otype: str | None = None) -> bool:
    """Явная продажа у самого объекта: «продам», «#продам», «в ДКП», ипотека, задаток… или цена продажи (≥ 1,5 млн у
    квартир/домов/участков — аренда столько не стоит). Тогда «аренда» из соседнего объявления сообщения не берётся."""
    t = norm(text).replace("ё", "е")
    if _SALE_WORDS.search(t) and not re.search(r"\b(?:сдам|сдаю|сда[её]тся|в аренду)\b", t):
        return True
    p = extract_price(text)
    return bool(p and p >= 1_500_000 and otype != "commercial")


def is_buyer_request(text: str) -> bool:
    """Запрос покупателя (решение Артёма 07.10): слово «запрос», «ищу» или «куплю» в любом месте текста —
    кроме «фото по запросу» / «ищу покупателя» (это продавцы). Плюс прежние зачины: «нужна», «есть клиент»…"""
    t = norm(text)
    if re.search(r"^(?:[^\w]*)(?:ищем|нужн[аоы]|требуется|подберу|подберите|в поиске|рассмотр\w+ варианты|есть клиент|есть покупател)", t[:160]):
        return True
    t = re.sub(r"\b(?:по|на|в ответ на|под)\s+запрос\w*", " ", t)          # «фото/цена по запросу»
    if re.search(r"\bзапрашива\w*|\bзапросит\w*|\bуточн\w* по (?:номеру|телефону|тел)", t):   # «цену запросить по номеру»
        t = re.sub(r"\bзапрос\w*", " ", t)
    t = re.sub(r"\b(?:ищу|ищем)\s+(?:покупател|клиент\w* на|арендатор)\w*", " ", t)   # продавец ищет покупателя
    return bool(re.search(r"\b(?:запрос|запросы|запроса|ищу|куплю)\b", t))


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


# ─── состояние: с ремонтом / без ремонта (17.10, Артём: «без ремонта — это ПЧО, черновая, без отделки и т. п.») ───
# Сначала ищем «без ремонта» (оно важнее: «ПЧО, ремонт под себя» — без ремонта), потом «с ремонтом». Нет ни того,
# ни другого — состояние не указано (NULL): такой объект не попадает ни в один из двух фильтров.
_NOREPAIR = re.compile(
    r"\bпчо\b|п\s*/\s*ч\s*о|\bпредчистов|\bпред\s*чистов|\bпод\s+чистов|white\s*-?\s*box|\bвайт\s*-?\s*бокс|\bвайтбокс"
    r"|\bчернов\w*|\bстроительн\w*\s+отделк|\bбез\s+(?:\w+(?:ого|его|ой|ей)\s+)?(?:ремонт|отделк)"
    r"|\bстрой\s*-?\s*вариант|\bсер(?:ая|ый|ой)\s+(?:отделк|каркас|ключ)|\bсерый\s+каркас|\bв\s+коробк\w*|\bголый\s+бетон"
    r"|\bпод\s+(?:\w+(?:ый|ий|ой)\s+)?ремонт|\bпод\s+отделк|\bтребу\w*\s+(?:\w+(?:ого|его|ый|ий|ой)\s+)?(?:ремонт|вложени|освежени|косметик)"
    r"|\bнуж\w*\s+(?:\w+(?:ый|ий|ой)\s+)?ремонт|\bнужно\s+(?:делать|сделать)\s+ремонт|\bпод\s+реконструкц"
    r"|\bремонт\w*\s+(?:не\s+делал|не\s+законч|не\s+доделан|в\s+процессе|отсутств|нет\b|требуется|нужен|стар\w*|советск\w*|бабушкин\w*)"
    r"|\bначат\w*\s+ремонт|\bнезаконченн\w*\s+ремонт|\bнедоделанн\w*\s+ремонт"
    r"|\bголы[ея]\s+стен|\bбетонн\w*\s+стен|\bв\s+бетоне\b"
    r"|\bот\s+застройщика\s+без|\bбабушкин\w*\s+ремонт|\bсоветск\w*\s+ремонт|\bстар\w*\s+ремонт|\bубит\w*\b"
    r"|\bсостояние\s*[:\-–—]?\s*(?:под\s+ремонт|требует|плохое|удовлетворит|среднее|старое)|\bв\s+плохом\s+состоян"
    r"|\bотделк\w*\s*[:\-–—]?\s*(?:нет|отсутств|чернов|предчистов|white)")
_REPAIR = re.compile(
    r"\bевро\s*-?\s*ремонт|\bремонт\w*|\bчистов\w*|\bс\s+отделк|\bотделк\w*\s+(?:под\s+ключ|выполнен|сделан|от\s+застройщик)"
    r"|\bдизайнерск\w*|\bдизайн\s*-?\s*проект|\bавторск\w*\s+(?:ремонт|дизайн)"
    r"|\b(?:заезжай|заходи|въезжай|заезжа\w*|заход\w*|въезжа\w*)\s+и\s+жив\w*|\bжилое\s+состояние"
    r"|\b(?:хорошее|отличное|идеальное|прекрасное)\s+состояние|\bсостояние\s*[:\-–—]?\s*(?:отличное|хорошее|идеальное|жилое)"
    r"|\bв\s+(?:хорошем|отличном|идеальном)\s+состоян|\bмебел\w*\s*(?:,|и)\s*(?:бытов\w*\s+)?техник")
# капремонт дома, ремонт подъезда/крыши — это про дом, не про квартиру
_BUILDING_REPAIR = re.compile(r"(?:кап(?:итальн\w*)?\.?\s*-?\s*)?ремонт\w*\s+(?:в\s+)?(?:дом[ае]?\b|крыш\w*|подъезд\w*|кровл\w*|фасад\w*|лифт\w*|мкд\b)"
                              r"|\bкапремонт\w*")


def detect_repair(text: str | None) -> str | None:
    """'no' — без ремонта (ПЧО, черновая, без отделки, под ремонт, требует ремонта, старый ремонт), 'yes' — с ремонтом,
    None — в тексте не сказано."""
    t = (text or "").lower().replace("ё", "е")
    if not t:
        return None
    t = _BUILDING_REPAIR.sub(" ", t)
    if _NOREPAIR.search(t):
        return "no"
    if _REPAIR.search(t):
        return "yes"
    return None

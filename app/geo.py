"""Справочник Краснодара: районы (как их называют риелторы), ЖК и улицы.

Источник ЖК и улиц — база знаний старого проекта (geo_kb.sqlite, выгружена в
data/geo_kb_raw.json). Районы приведены к одному списку: в старой базе один и
тот же район встречался в 2–3 падежных вариантах («Восточно-Кругликовский» /
«Восточно-Кругликовской»), отсюда путаница в фильтрах.

Главное правило: район и ЖК ставим только если они следуют из текста —
прямо названы, либо однозначно выводятся из названного ЖК или улицы.
Ничего «по знанию Краснодара» не угадываем.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .textnorm import contains_phrase, words

DATA = Path(__file__).parent / "data" / "geo_kb_raw.json"

# Канонические районы. Первое имя — как показываем; дальше — как пишут в чатах.
DISTRICTS: list[tuple[str, list[str]]] = [
    ("ФМР", ["фмр", "фестивальный", "фестивалка", "фестивальный микрорайон"]),
    ("ЦМР", ["цмр", "центр", "центральный", "центральный район", "центральный микрорайон"]),
    ("ШМР", ["шмр", "школьный", "школьный микрорайон"]),
    ("ЧМР", ["чмр", "черемушки", "микрорайон черемушки"]),
    ("ГМР", ["гмр", "гидрострой", "гидростроителей", "микрорайон гидростроителей"]),
    ("ПМР", ["пмр", "пашковский", "пашковка", "пашковский микрорайон"]),
    ("КМР", ["кмр", "комсомольский", "комсомольский микрорайон"]),
    ("ЮМР", ["юмр", "юбилейный", "юбилейный микрорайон"]),
    ("СМР", ["смр", "славянский", "славянка", "славянский микрорайон"]),
    ("РИП", ["рип", "зип", "зип рип", "завод радиоизмерительных приборов", "радиоизмерительных приборов"]),
    ("ЗИП-2 (Завод измерительных приборов)", ["зип 2", "завод измерительных приборов"]),
    ("ККБ", ["ккб", "краевая больница", "краевая клиническая больница", "краевой клинической больницы"]),
    ("ХБК", ["хбк", "хлопчато-бумажный комбинат", "хлопчатобумажный комбинат", "бумажный комбинат"]),
    ("КСК", ["кск", "камвольно-суконный комбинат", "камвольный"]),
    ("ТЭЦ", ["тэц", "теплоэлектроцентраль"]),
    ("Энка (Маршала Жукова)", ["энка", "маршала жукова", "им маршала жукова"]),
    ("Немецкая деревня", ["немецкая деревня", "немка"]),
    ("40 лет Победы", ["40 лет победы", "40 лет", "40-летия победы", "сорок лет"]),
    ("Музыкальный", ["музыкальный", "музыкальный микрорайон", "мкр музыкальный"]),
    ("Восточно-Кругликовский", ["восточка", "вкмр", "восточно-кругликовский", "восточно-кругликовской"]),
    ("Западный обход", ["западный обход", "ближний западный обход", "дальний западный обход", "бзо"]),
    ("Калинино", ["калинино", "калинина"]),
    ("Витаминкомбинат", ["витаминкомбинат", "витамин"]),
    ("Авиагородок", ["авиагородок"]),
    ("Дубинка", ["дубинка"]),
    ("Табачная фабрика", ["табачка", "табачная фабрика", "табачной фабрики"]),
    ("Кожзавод", ["кожзавод"]),
    ("Покровка", ["покровка"]),
    ("Сельхоз (КубГАУ)", ["сельхоз", "сельскохозяйственный институт", "кубгау", "карасунский"]),
    ("Северный", ["северный", "северный микрорайон"]),
    ("Горгаз", ["горгаз"]),
    ("Горхутор", ["горхутор"]),
    ("Горогороды", ["горогороды"]),
    ("Ростовское шоссе", ["ростовское шоссе"]),
    ("Вавилова", ["вавилова", "имени вавилова", "н и вавилова"]),
    ("Баскет-холл", ["баскет-холл", "баскет холл", "баскет-холла"]),
    ("Солнечный / Губернский", ["губернский", "солнечный", "стадион фк краснодар", "стадиона фк краснодар"]),
    ("Знаменский", ["знаменский"]),
    ("Новознаменский", ["новознаменский", "поселка новознаменский"]),
    ("Пригородный", ["пригородный"]),
    ("Индустриальный", ["индустриальный"]),
    ("Тихая поляна", ["тихая поляна"]),
    ("Плодородный", ["плодородный", "плодородный-2", "плодородный 2"]),
    ("9-й километр", ["9 километр", "9-й километр", "девятый километр"]),
    ("2-я площадка", ["2-я площадка", "вторая площадка"]),
    ("Молодёжный", ["молодежный"]),
    ("Вишнёвый сад", ["вишневый сад"]),
    ("Курортный", ["курортный"]),
    ("Сосновый бор", ["сосновый бор"]),
    ("Берёзовый", ["березовый"]),
    ("Красная площадь", ["красная площадь", "тц красная площадь"]),
    ("Галерея", ["трц галерея", "галерея"]),
    ("Демьяна Бедного", ["демьяна бедного"]),
    ("Уральская", ["уральская", "уральской"]),
    ("Ставропольская", ["ставропольская", "ставропольской"]),
    ("Ремонтно-механический завод", ["ремонтно-механического завода", "рмз"]),
    ("Петра Метальникова", ["петра метальникова", "метальникова", "п метальникова"]),
    ("п. Краснодарский", ["п краснодарский", "пос краснодарский", "поселок краснодарский", "краснодарский поселок"]),
    ("п. Российский", ["п российский", "пос российский", "поселок российский"]),
    ("п. Южный", ["п южный", "пос южный", "поселок южный"]),
    ("п. Северный", ["п северный", "пос северный", "поселок северный"]),
    ("Афипский", ["афипский", "пгт афипский"]),
    ("х. Ленина", ["х ленина", "хутор ленина", "хут ленина"]),
    # Пригород и Адыгея — объекты оттуда идут потоком, держим отдельно.
    ("Новая Адыгея", ["новая адыгея"]),
    ("Яблоновский", ["яблоновский", "яблоновка", "яблоновке"]),
    ("Энем", ["энем"]),
    ("Тахтамукай", ["тахтамукай"]),
    ("Новобжегокай", ["новобжегокай"]),
    ("Динская", ["динская", "динской"]),
    ("Елизаветинская", ["елизаветинская"]),
    ("Старокорсунская", ["старокорсунская"]),
    ("Белозёрный", ["белозерный"]),
]

# Слова, которые слишком часто встречаются как обычные слова, — их принимаем
# как район только рядом с маркером («мкр», «р-н», «район»).
_AMBIGUOUS = {"центр", "северный", "солнечный", "витамин", "галерея", "молодежный",
              "курортный", "школьный", "музыкальный", "индустриальный", "березовый",
              "40 лет", "калинина", "уральская", "ставропольская", "пригородный"}

_DISTRICT_MARKER = re.compile(r"(?:^|\s)(?:мкр|мкрн|микрорайон|р-н|р н|район|рн|в районе)\s*$")


@dataclass
class Complex:
    name: str
    district: str | None


_CUSTOM: list[tuple[str, list[str]]] = []   # районы, добавленные админом (таблица custom_districts)


def _alias_table() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for canon, aliases in DISTRICTS + _CUSTOM:
        for a in aliases + [canon]:
            w = words(a)
            if w:
                out.append((w, canon))
    # длинные алиасы раньше коротких: «зип 2» раньше «зип»
    out.sort(key=lambda x: -len(x[0]))
    return out


_ALIASES = _alias_table()


def canonical_district(name: str | None) -> str | None:
    """Имя района в любом написании → каноническое, иначе None."""
    w = words(name)
    if not w:
        return None
    w = re.sub(r"^(?:микрорайон|мкр|мкрн|район|р н|имени|им)\s+", "", w)
    w = re.sub(r"\s+(?:микрорайон|мкр|район)$", "", w)
    for alias, canon in _ALIASES:
        if w == alias:
            return canon
    return None


def find_district_in_text(text: str) -> str | None:
    """Район, прямо названный в тексте (с учётом жаргона: ФМР, Восточка, РИП…)."""
    w = words(text)
    for alias, canon in _ALIASES:
        idx = f" {w} ".find(f" {alias} ")
        if idx < 0:
            continue
        if alias in _AMBIGUOUS:
            before = f" {w} "[:idx + 1]
            if not _DISTRICT_MARKER.search(before.rstrip() + " "):
                continue
        return canon
    return None


@lru_cache(maxsize=1)
def _raw() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _complexes() -> list[tuple[str, Complex]]:
    out = []
    for c in _raw()["complexes"]:
        key = words(re.sub(r"\(.*?\)", " ", c["norm"])) or words(c["norm"])
        if len(key) < 3:
            continue
        district = canonical_district(c.get("district")) or canonical_district(c.get("district_hint"))
        out.append((key, Complex(name=pretty_name(c["name"]), district=district)))
        # «Sport Village (Спортивная деревня 2)» — ищем и по тексту в скобках
        inner = re.findall(r"\((.*?)\)", c["norm"])
        for alt in inner:
            k2 = words(alt)
            if len(k2) >= 4:
                out.append((k2, Complex(name=pretty_name(c["name"]), district=district)))
    out.sort(key=lambda x: -len(x[0]))
    return out


_JK_MARKER = r"(?:жк|ж к|жилой комплекс|жилом комплексе|кп|коттеджный поселок)"


def resolve_complex(name: str | None) -> Complex | None:
    """Имя ЖК (как его вернула нейросеть или нашли в тексте) → запись справочника."""
    w = words(name)
    w = re.sub(rf"^{_JK_MARKER}\s+", "", w)
    if len(w) < 3:
        return None
    for key, cx in _complexes():
        if w == key:
            return cx
    return None


def find_complex_in_text(text: str) -> Complex | None:
    """ЖК, явно названный в тексте после «ЖК»/«ж/к». Без маркера не принимаем:
    «Мозаика», «Движение», «Южане» — обычные слова, на них и сыпались ошибки."""
    w = f" {words(text)} "
    for key, cx in _complexes():
        if re.search(rf"\s{_JK_MARKER}\s+{re.escape(key)}\s", w):
            return cx
    m = re.search(rf"\s{_JK_MARKER}\s+([0-9a-zа-я]+(?:\s[0-9a-zа-я]+)?)\s", w)
    if m:
        # ЖК, которого нет в справочнике: берём 1–2 слова после маркера,
        # но не «ул», «дом», «студия» и т.п. («ЖК Сармат ул. …» → «Сармат»)
        parts = m.group(1).split()
        while parts and parts[-1] in _NOT_COMPLEX_WORDS:
            parts.pop()
        if parts and parts[0] not in _NOT_COMPLEX_WORDS:
            return Complex(name=pretty_name(" ".join(parts)), district=None)
    return None


_NOT_COMPLEX_WORDS = {"ул", "улица", "д", "дом", "этаж", "эт", "студия", "кв", "квартира", "литер", "лит", "корпус",
                      "к", "пр", "проспект", "пер", "мкр", "район", "р", "н", "в", "на", "и", "сдан", "сдача", "от",
                      "1к", "2к", "3к", "евро", "продам", "продаю", "продается"}


def pretty_name(name: str) -> str:
    """«ЗОЛОТАЯ ЛИНИЯ» / «золотая линия» → «Золотая Линия»; «URAL» оставляем."""
    name = " ".join(name.strip().strip("«»\"'").split())
    if name.isupper() and re.search(r"[А-ЯЁ]", name) or name.islower():
        name = name.title()
    return name


@lru_cache(maxsize=1)
def _streets() -> list[tuple[str, list[str]]]:
    out = []
    for s in _raw()["streets"]:
        base = re.sub(r"\(.*?\)", " ", s["name"])
        base = re.sub(r"\b(?:улица|ул|проспект|пр-т|переулок|пер|проезд|бульвар|шоссе|площадь|тупик|линия)\b", " ", base)
        key = words(base)
        if len(key) < 4:
            continue
        districts = sorted({d for d in (canonical_district(x) for x in s["districts"]) if d})
        out.append((key, districts))
    out.sort(key=lambda x: -len(x[0]))
    return out


def district_by_street(street: str | None) -> str | None:
    """Район по улице — только если улица целиком в одном районе."""
    w = words(street)
    w = re.sub(r"\b(?:улица|ул|проспект|пр т|переулок|пер|проезд|бульвар|шоссе|площадь)\b", " ", w)
    w = words(w)
    if len(w) < 4:
        return None
    for key, districts in _streets():
        if w == key or contains_phrase(w, key):
            return districts[0] if len(districts) == 1 else None
    return None


def all_district_names() -> list[str]:
    """Все районы по алфавиту (включая добавленные админом)."""
    return sorted({d for d, _ in DISTRICTS + _CUSTOM}, key=lambda x: x.lower().replace("ё", "е"))


def set_custom_districts(items: list[tuple[str, list[str]]]) -> None:
    """Подключить районы, добавленные админом: они сразу узнаются в тексте, фильтре и поиске."""
    global _CUSTOM, _ALIASES
    known = {d for d, _ in DISTRICTS}
    new = [(n, a) for n, a in items if n not in known]
    if new != _CUSTOM:
        _CUSTOM = new
        _ALIASES = _alias_table()


def complex_key(name: str | None) -> str | None:
    """Ключ ЖК для фильтра: «ЖК Самолёт-2», «самолет 2» и «Самолет 2» — один и тот же ЖК."""
    if not name:
        return None
    w = re.sub(rf"^{_JK_MARKER}\s+", "", words(name))
    rec = resolve_complex(w)
    return words(rec.name) if rec else w


# ─── адрес отдельной строкой (без «ул.» и «ЖК») ─────────────────────────────
# Агенты часто пишут шапку так:  «АКВАРЕЛИ-3» / «Есенина» / «Очаковская 13» / «Молодежный».
# Строку принимаем, только если она ЦЕЛИКОМ — название из справочника (ЖК, район, улица),
# поэтому обычные слова внутри предложений («мозаика на стене») сюда не попадут.
_LINE_TAIL = re.compile(r"\s+(?:очередь|оч|литер|лит|корпус|корп|секция|дом)(?:\s+\d+\w*)*$|\s+\d+\s*(?:очередь|оч)$")
_HOUSE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<house>\d{1,3}(?:\s*[а-я](?![а-я\d]))?(?:\s*(?:к|корп|лит)\s*\d+)?(?:\s+\d{1,3}(?:\s*к\s*\d+)?)?)$")
_STREET_MARK = re.compile(r"^(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе)\s+")
_SETTLEMENT_RE = re.compile(
    r"(?:^|[\s,(])(?i:ст-ца|ст\.|станица|х\.|хут\.|хутор|пос\.|посёлок|поселок|пгт|аул|село|с\.|п\.|г\.|город)\s*"
    r"(?P<name>[А-ЯЁ][А-ЯЁа-яё]+(?:[ -][А-ЯЁ][А-ЯЁа-яё]+)?)")
# «Краеведа Соловьева», «5я Дорожная», «Красных Партизан»: 1–3 слова, последнее — с окончанием улицы
_STREET_LIKE = re.compile(r"^(?:\d{1,2}\s*я\s+)?(?:[а-я]+\s+){0,2}[а-я]{3,}(?:ая|ой|ий|ый|ого|его|ова|ева|ина|ына|ская|цкая|ского|кого|на|ва|нко)$")
# Одно слово-фамилия в родительном падеже без номера дома: «Мусоргского», «Дунаевского», «Прокофьева»
_STREET_SURNAME = re.compile(r"^[а-я]{4,}(?:ского|цкого|ова|ева|ёва|ина|ына)$")
_NOT_STREET = {"цена", "этаж", "этажей", "тел", "телефон", "площадь", "студия", "квартира", "евро", "кухня", "ремонт",
               "комнат", "комнатная", "дом", "участок", "литер", "корпус", "секция", "подъезд", "очередь", "сдача",
               "стоимость", "продажа", "аренда", "собственник", "собственника", "взнос", "ипотека", "задаток", "мин",
               "минут", "минута", "остановка", "школа", "сад", "кв", "сотка", "сотки", "соток", "года", "год",
               "общая", "жилая", "полная", "чистовая", "черновая", "предчистовая", "отделка"}
_SKIP_LINE = {"ремонт", "мебель", "техника", "продажа", "продам", "срочно", "студия", "квартира", "дом", "участок"}


def _complex_from_line(w: str) -> Complex | None:
    w = _LINE_TAIL.sub("", w).strip()
    if len(w) < 4 or w in _SKIP_LINE:
        return None
    for key, cx in _complexes():
        if w == key:
            return cx
    # «Акварели 3»: в справочнике есть «Акварели 1» — та же серия ЖК, другая очередь
    m = re.match(r"^(.+?)\s+(\d{1,2})$", w)
    if m and len(m.group(1)) >= 4 and not (set(m.group(1).split()) & (_NOT_STREET | _SKIP_LINE)):
        base = m.group(1)
        for key, cx in _complexes():
            if key == base or key.startswith(base + " "):
                return Complex(name=pretty_name(w), district=cx.district)
    return None


def _street_from_line(w: str, raw: str) -> tuple[str, str | None] | None:
    w = _STREET_MARK.sub("", w)
    m = _HOUSE_RE.match(w)
    name, house = (m.group("name"), m.group("house")) if m else (w, None)
    if len(name) < 4 or name in _SKIP_LINE:
        return None
    known = any(name == key for key, _ in _streets())
    # Улицы нет в справочнике, но строка — явно «Название + дом»: «Краеведа Соловьева 2к2», «5я Дорожная 68к1»
    looks = not (set(name.split()) & _NOT_STREET) and (
        (bool(house) and bool(_STREET_LIKE.match(name))) or bool(_STREET_SURNAME.match(name)))
    if not (known or looks):
        return None
    # Номер дома — как написано в строке («62/1», «2к2», «1/4к21»), без числа из названия («5я Дорожная»)
    hm = re.search(r"(?<![\w])\d+(?:/\d+)?(?:\s*[а-яА-Я](?![а-яА-Я]))?(?:\s*/?\s*(?:к|корп\.?|лит\.?)\s*\d+)?\s*$", raw.strip())
    pretty = re.sub(r"^(\d+)\s*[яЯ]\s+", r"-я ", pretty_name(name))   # «5Я Дорожная» → «5-я Дорожная»
    return pretty, (hm.group(0).replace(" ", "") if house and hm else None)


def place_from_lines(text: str, max_lines: int = 6) -> dict:
    """ЖК / район / улица / посёлок, записанные отдельной строкой в шапке объявления."""
    out: dict = {}
    for raw in [ln for ln in text.splitlines() if ln.strip()][:max_lines]:
        raw = re.sub(r"^\s*\[[^\]]*\]\s*[^:]{0,40}:\s*", "", raw)   # «[06.10, 10:30] наш офис:»
        w = words(raw)
        if not w or len(w) > 40:
            continue
        w = re.sub(rf"^{_JK_MARKER}\s+", "", w)
        # Порядок: район («Молодежный») → улица («Есенина 5») → ЖК («Самолет», «Акварели 3»)
        if "district" not in out:
            for alias, canon in _ALIASES:
                if w == alias:
                    out["district"] = canon
                    break
            if "district" in out:
                continue
        if "street" not in out:
            st = _street_from_line(w, raw)
            if st:
                out["street"], out["house"] = st
                continue
        if "complex" not in out:
            cx = _complex_from_line(w)
            if cx:
                out["complex"] = cx
    for m in _SETTLEMENT_RE.finditer(text):
        name = pretty_name(m.group("name").lower()) if m.group("name").isupper() else m.group("name")
        if canonical_district(name) or words(name).startswith("краснодар"):
            continue
        out["settlement"] = name
        break
    return out


def street_districts(street: str | None) -> list[str]:
    """Все районы, через которые проходит улица (для подсказок админу)."""
    w = words(re.sub(r"\b(?:улица|ул|проспект|пр т|переулок|пер|проезд|бульвар|шоссе|площадь)\b", " ", words(street or "")))
    w = " ".join(w.split())
    if len(w) < 4:
        return []
    for key, districts in _streets():
        if w == key:
            return districts
    return []


def suggestions(text: str, street: str | None, current_complex: str | None = None) -> dict:
    """Подсказки для разбора: районы и ЖК с объяснением «откуда»."""
    ds: list[dict] = []

    def add_d(name, why):
        if name and name not in [x["name"] for x in ds]:
            ds.append({"name": name, "why": why})
    add_d(find_district_in_text(text), "названо в тексте")
    pl = place_from_lines(text)
    add_d(pl.get("district"), "строка в шапке")
    cxs: list[dict] = []
    for rec, why in ((find_complex_in_text(text), "«ЖК …» в тексте"), (pl.get("complex"), "строка в шапке"),
                     (resolve_complex(current_complex) if current_complex else None, "сейчас")):
        if rec and rec.name not in [x["name"] for x in cxs]:
            cxs.append({"name": rec.name, "why": why})
            add_d(rec.district, f"по ЖК {rec.name}")
    for d in street_districts(street):
        add_d(d, "по улице")
    return {"districts": ds[:8], "complexes": cxs[:5]}

"""Сообщение из чата → список объектов.

Нейросеть (если задан ключ) делит сообщение на объекты и вытаскивает поля;
правила и справочник проверяют результат. Всё, чего нет в тексте, выбрасываем.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from . import geo, llm, rules
from .textnorm import words

log = logging.getLogger(__name__)


@dataclass
class ParsedObject:
    type: str | None
    deal: str
    rooms: int | None
    area: float | None
    land: float | None
    floor: int | None
    floors: int | None
    price: int | None
    district: str | None
    complex: str | None
    settlement: str | None
    street: str | None
    house: str | None
    description: str
    fragment: str
    phones: list[str] = field(default_factory=list)
    phone_roles: dict = field(default_factory=dict)   # {"+7…": "text" | "sender"} — в тексте / прислал в чат
    room_kind: str | None = None      # studio | classic | euro | mini
    article: str | None = None        # «Артикул: 337» — объект есть на сайте СТРЕЛ
    extra_districts: list[str] = field(default_factory=list)   # ещё районы (правила админа: объект на границе)

    def to_dict(self) -> dict:
        return asdict(self)


def _num(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(round(n)) if n is not None else None


def _in_text(value: str | None, text_words: str) -> bool:
    w = words(value)
    return bool(w) and f" {w} " in f" {text_words} "


def _clean_description(fragment: str) -> str:
    lines = []
    for line in fragment.splitlines():
        s = line.strip(" *_•·-–—\t")
        if not s:
            continue
        if rules.extract_phones(s) and len(s) < 60:
            continue  # строка только с телефоном
        if re.match(r"^(?:звоните|пишите|тел|телефон|whatsapp|ватсап|подробности)\b", s, re.I):
            continue
        lines.append(s)
    return "\n".join(lines)[:1200]


_CONTACT_TAIL = re.compile(r"[\s,;:]*(?:тел(?:ефон)?\.?|т\.|звоните|пишите|whatsapp|ватсап|wa)?[\s:,;]*$", re.I)


def strip_phones(description: str) -> str:
    """Убирает телефоны агентов из описания (с подписями «WA:», «тел.», «📞»): описание видят и гости.
    Номера при этом не теряются — они в listings.phones (контакт объекта)."""
    out = []
    for line in (description or "").splitlines():
        if rules.find_phones(line):
            line = re.sub(r"[ \t]{2,}", " ", _CONTACT_TAIL.sub("", rules.remove_phones(line))).strip(" ,;:-–—\t")
            if not re.sub(r"[\W_]+", "", line):   # от строки остались только значки
                continue
        out.append(line)
    return "\n".join(out).strip()


# ─── текст для клиента (подборка /c/…): без кухни агентов ─────────────────────────────────────────────
# Строка убирается целиком, если в ней: ДКП, обременение, залог/задаток, комиссия («+ Ваши», «оплачиваю», 🍋),
# ключи/сейф, «пишите/звоните/по номеру», прайс, «для коллег», цена (её клиент видит крупно в карточке).
_CLIENT_DROP = re.compile(
    r"дкп|обрем|заклад|задат|комисси|оплачива|вознагра|\bваши\b|\+\s*ваш|для\s+коллег|коллегам|агент|риэлт|риелт|"
    r"ключ|в\s+сейфе|гнезд|пиш(?:ите|у)|звон(?:ите|ить)|по\s+(?:этому\s+)?номеру|контакт|прайс|в\s+лс\b|"
    r"\bцена\b|\bстоимост|\bторг\b|разбивк|\U0001F34B", re.I)
# Аббревиатуры, которые остаются заглавными, и слова, после которых название пишем с большой буквы
_CLIENT_ABBR = {"ЖК", "ПЧО", "ИЖС", "СНТ", "ДНТ", "КП", "ТЦ", "ТРЦ", "ФМР", "ЦМР", "ЮМР", "ЧМР", "ГМР", "СМР", "ККБ", "РИП",
                "ЗИП", "ХБК", "МЖК", "КСК", "ЕВРО", "АГВ", "ЛСР", "ССК", "ВТБ", "СВО"}
_CLIENT_NAME_AFTER = {"жк", "п.", "пос.", "поселок", "посёлок", "ул.", "улица", "район", "мкр", "мкр.", "ст.", "станица", "х.",
                      "хутор", "пр.", "пр-т", "проспект", "пер.", "переулок", "кп"}
_CLIENT_KEEP = set(" .,;:!?()«»\"'%/№+-–—=×*²³°₽\n")


def _client_chars(line: str) -> str:
    """Только буквы (кириллица/латиница), цифры и обычная пунктуация: значки ♟ ➳ ✔️ 🔑 💯 убираем."""
    out = []
    for ch in line:
        if ch.isdigit() or ch in _CLIENT_KEEP or ("а" <= ch.lower() <= "я") or ch.lower() == "ё" or ("a" <= ch.lower() <= "z"):
            out.append(ch)
        else:
            out.append(" ")
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip(" ,;:-–—*=")


def _client_case(line: str) -> str:
    """КАПС → обычный регистр, аббревиатуры (ЖК, ПЧО, ФМР…) и названия после «ЖК/ул./п.» — с большой буквы."""
    words_ = line.split(" ")
    all_letters = re.sub(r"[^A-Za-zА-ЯЁа-яё]", "", line)
    shouty = len(all_letters) >= 6 and sum(ch.isupper() for ch in all_letters) >= len(all_letters) * 0.6   # строка капсом
    out, prev = [], ""
    for i, w in enumerate(words_):
        letters = re.sub(r"[^A-Za-zА-ЯЁа-яё]", "", w)
        if letters.upper() in _CLIENT_ABBR:
            out.append(w)
        elif letters and letters == letters.upper() and (len(letters) >= 3 or shouty):
            low = w.lower()
            if i == 0 or prev.lower() in _CLIENT_NAME_AFTER or prev.endswith((".", "!", "?")):
                low = re.sub(r"[a-zа-яё]", lambda m: m.group(0).upper(), low, count=1)
            out.append(low)
        else:
            out.append(w)
        prev = w
    res = " ".join(out)
    return re.sub(r"^[a-zа-яё]", lambda m: m.group(0).upper(), res)


def client_text(description: str | None) -> str:
    """Чистый текст объявления для клиента агента: без телефонов, ДКП, обременений, комиссии, ключей, значков,
    КАПСа и имени агента в конце. Сырой текст агента клиенту не отдаём никогда."""
    lines, seen = [], set()
    for raw in strip_phones(description or "").splitlines():
        if _CLIENT_DROP.search(raw):
            continue
        line = _client_case(_client_chars(raw))
        if not re.search(r"[A-Za-zА-ЯЁа-яё0-9]", line):
            continue
        key = line.lower()
        if key in seen:          # «Заклад обсуждаем» ×2 и т. п.
            continue
        seen.add(key)
        lines.append(line)
    # имя агента одной строкой в самом конце («➳ Артём»)
    while lines and re.fullmatch(r"[А-ЯЁA-Z][а-яёa-z]{2,14}", lines[-1]):
        lines.pop()
    return "\n".join(lines).strip()


def guard_phones(text: str | None) -> str:
    """Последняя проверка перед выдачей наружу (/api/listings, /api/c/…): номеров в описании быть не должно."""
    return strip_phones(text) if text and rules.find_phones(text) else (text or "")


def _build(obj: dict | None, fragment: str, text: str, sender_phone: str | None,
           single: bool = True) -> ParsedObject:
    obj = obj or {}
    # В подборке ЖК/улицу/район, названные нейросетью, проверяем по куску этого объекта
    text_words = words(text if single else fragment)
    r = rules.extract(fragment)

    otype = obj.get("type") if obj.get("type") in rules.TYPES else None
    otype = otype or r.type
    if otype == "new":  # отдельного типа «новостройка» нет — это квартира
        otype = "flat"
    # Аренда — только если в тексте прямо написано «сдам», «аренда», «сдаётся»… (нейросеть иногда
    # ставила аренду обычным продажам). Шапка подборки «Сдаю:» относится ко всем объектам в ней.
    deal = "rent" if r.deal == "rent" or rules.detect_deal(text) == "rent" else "sale"
    # «Сдам», но без коммунальных платежей — не аренда; если и цена «арендная» (меньше 300 тыс.) — объект не берём
    rent_words = deal == "sale" and rules.has_rent_words(fragment)

    area = _num(obj.get("area_m2"))
    land = _num(obj.get("land_sotki"))
    # Сотки ≠ м². Если нейросеть записала сотки в метры — поправляем по тексту.
    if r.land and area and abs(area - r.land) < 0.01 and not r.area:
        area, land = None, r.land
    area = area if area is not None else r.area
    land = land if land is not None else r.land
    if otype in ("flat", "new", "room"):
        land = None

    # Цена: явно подписанная цена из текста куска надёжнее ответа нейросети
    # (нейросеть иногда берёт обременение или «сумму в ДКП»)
    strict_price = rules.extract_price(fragment, deal, strict=True)
    price = strict_price or _int(obj.get("price_rub"))
    if price is not None and deal == "sale" and price < 300_000:
        price = price * 1000 if 300 <= price < 100_000 and not rent_words else None
    if (price is None or price < 1000) and not rent_words:
        price = r.price

    # Комнаты: тип планировки (студия/мини/евро/обычная) по тексту надёжнее нейросети
    rk = rules.extract_room_kind(fragment)
    room_kind = rk[0] if rk else None
    rooms = obj.get("rooms")
    rooms = rk[1] if rk else (_int(rooms) if rooms is not None else r.rooms)
    if rooms is not None:
        rooms = max(0, min(rooms, 5))
        room_kind = room_kind or ("studio" if rooms == 0 else "classic")
    if otype in ("land", "commercial"):
        rooms = room_kind = None

    floor = r.floor or _int(obj.get("floor"))
    floors = r.floors or _int(obj.get("floors"))
    if floor and floors and floor > floors:
        floor, floors = None, None

    # ЖК: только если он реально написан в тексте
    complex_rec = None
    cx_name = obj.get("complex")
    if cx_name and _in_text(re.sub(r"(?i)^\s*(?:жк|ж/к)\s+", "", cx_name), text_words):
        complex_rec = geo.resolve_complex(cx_name) or geo.Complex(
            name=geo.pretty_name(re.sub(r"(?i)^\s*(?:жк|ж/к)\s+", "", cx_name)), district=None)
    if complex_rec is None:
        complex_rec = geo.find_complex_in_text(fragment)
    # Адрес отдельной строкой в шапке: «АКВАРЕЛИ-3», «Есенина», «Очаковская 13», «Молодежный»
    lines_place = geo.place_from_lines(fragment)
    if complex_rec is None and lines_place.get("complex"):
        complex_rec = lines_place["complex"]

    street = obj.get("street") if _in_text(obj.get("street"), text_words) and not is_field_label(obj.get("street")) else None
    street = street or r.street
    house = obj.get("house") or r.house
    if house and not _in_text(str(house), text_words):
        house = r.house
    if not street and lines_place.get("street"):
        street, house = lines_place["street"], house or lines_place.get("house")

    # Район: 1) прямо назван в куске; 2) назван нейросетью и есть в тексте;
    # 3) из ЖК по справочнику; 4) из улицы, если улица целиком в одном районе.
    district = geo.find_district_in_text(fragment) or lines_place.get("district")
    if not district and obj.get("district") and _in_text(obj.get("district"), text_words):
        # Только районы из справочника — иначе «Фестивальный», «ФМР», «фестивалка» стали бы разными районами
        district = geo.canonical_district(obj.get("district"))
    if not district and complex_rec and complex_rec.district:
        district = complex_rec.district
    if not district and street:
        district = geo.district_by_street(street)

    settlement = obj.get("settlement") if _in_text(obj.get("settlement"), text_words) else None
    settlement = settlement or lines_place.get("settlement")

    phones = r.phones or rules.extract_phones(text)
    sender = rules.normalize_phone(sender_phone) if sender_phone else None
    roles = {p: "text" for p in phones}
    if sender:
        roles.setdefault(sender, "sender")   # прислал в чат (если номера нет в тексте — он и есть контакт)
    if not phones and sender:
        phones = [sender]

    description = strip_phones(_clean_description(fragment))

    return ParsedObject(
        type=otype, deal=deal, rooms=rooms, area=area, land=land, floor=floor, floors=floors,
        price=price, district=district, complex=complex_rec.name if complex_rec else None,
        settlement=settlement, street=street, house=house,
        description=description, fragment=fragment.strip(), phones=phones, phone_roles=roles,
        room_kind=room_kind, article=rules.extract_article(fragment),
    )


# Подписи полей объявления — не улицы («Площадь: 45 м²», «Этаж 3/9», «Цена 5 млн»): нейросеть иногда отдаёт их улицей
FIELD_LABELS = {"площадь", "общая площадь", "площадь дома", "этаж", "этажность", "цена", "стоимость", "комнат", "комнаты",
                "состояние", "ремонт", "район", "адрес", "жк", "тип", "отделка", "балкон", "санузел", "кухня", "участок"}


def is_field_label(street: str | None) -> bool:
    return bool(street) and words(street).strip() in FIELD_LABELS


def address_from_text(fragment: str) -> tuple[str | None, str | None]:
    """Улица и дом из куска объявления — теми же правилами, что при разборе (без нейросети)."""
    street, house = rules.extract_street(fragment)
    if not street:
        lp = geo.place_from_lines(fragment)
        street, house = lp.get("street"), lp.get("house") or house
    return street, house


def _llm_fragments(objs: list[dict], text: str, chunks: list[str]) -> list[str]:
    """Кусок текста каждого объекта по номерам строк из ответа нейросети."""
    lines = text.splitlines()
    out = []
    for i, obj in enumerate(objs):
        frag = ""
        rng = obj.get("lines")
        if isinstance(rng, list) and len(rng) == 2:
            a, b = _int(rng[0]), _int(rng[1])
            if a and b and 1 <= a <= b <= len(lines):
                frag = "\n".join(lines[a - 1: b]).strip()
        if not frag:
            frag = chunks[i] if len(chunks) == len(objs) else text
        out.append(frag)
    return out


def _is_meaningful(o: ParsedObject) -> bool:
    has_size = any(v is not None for v in (o.area, o.land, o.rooms))
    return o.price is not None and o.type is not None and has_size


def parse(text: str, sender_phone: str | None = None, use_llm: bool | None = None) -> tuple[str, list[ParsedObject]]:
    """Возвращает (kind, objects). kind: listing | request | other."""
    text = rules.normalize_digits(text).strip()
    if len(text) < 15:
        return "other", []
    chunks = rules.split_objects(text)

    if rules.is_buyer_request(text):   # «запрос / ищу / куплю» — в ленту запросов, нейросеть не нужна
        return "request", []

    if use_llm is None:
        use_llm = llm.available()

    if use_llm:
        try:
            # Номера телефонов в нейросеть не отправляем (DeepSeek — зарубежный сервис, это была бы
            # трансграничная передача персональных данных). Номера достаём сами правилами.
            # Замена построчная — номера строк для ответа нейросети не сдвигаются.
            res = llm.parse_message(rules.mask_phones(text, "[телефон]"))
        except llm.LLMError as e:
            log.warning("Нейросеть недоступна, разбираю правилами: %s", e)
        else:
            kind = res.get("kind") if res.get("kind") in ("listing", "request", "other") else "other"
            if kind != "listing":
                return kind, []
            objs = [o for o in res.get("objects") or [] if isinstance(o, dict)]
            frags = _llm_fragments(objs, text, chunks)
            # Нейросеть увидела меньше объектов, чем правила, — режем правилами
            if len(chunks) > len(objs):
                log.info("Нейросеть нашла %d объектов, правила — %d: беру разбивку правил", len(objs), len(chunks))
            else:
                single = len(objs) == 1
                out = []
                for obj, frag in zip(objs, frags):
                    subs = rules.split_objects(frag)
                    if len(subs) > 1:  # в куске нейросети несколько цен — делим правилами
                        out += [_build(None, sub, text, sender_phone, single=False) for sub in subs]
                    else:
                        out.append(_build(obj, frag, text, sender_phone, single=single))
                out = [po for po in out if _is_meaningful(po)]
                return ("listing" if out else "other"), out

    # Разбор только правилами
    single = len(chunks) == 1
    out = [po for po in (_build(None, c, text, sender_phone, single=single) for c in chunks) if _is_meaningful(po)]
    return ("listing" if out else "other"), out

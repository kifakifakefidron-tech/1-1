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
        # 18.10: чистка значков могла «собрать» номер с опечаткой («677-19&17» → «677-19 17») — вырезаем ещё раз,
        # и «номера с опечатками» (9+ цифр подряд) тоже
        cut = _DIGITS_RE.sub("", strip_phones(line))
        line = cut.strip(" ,.-—:;") if cut != line else line
        if not re.search(r"[A-Za-zА-ЯЁа-яё0-9]", line) or _PHONE_ONLY_LEFT.fullmatch(line):
            continue
        # строка — только число / цена («5100», «7550 💰», «Цена 5 100 000») — цена клиенту видна в карточке (акт 14.10, С5)
        if re.fullmatch(r"(?i)\s*(?:цена|стоимость)?\s*[:\-–—]?\s*[\d\s.,]+\s*(?:млн|тыс|т\.?р\.?|р\.?|руб\w*|₽)?\.?\s*", line):
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


# ─── открытый текст объявления (без входа, превью, поисковики): без имён и «номеров с опечатками» ───
_FIRST_NAMES = sorted(set(['Александр', 'Алексей', 'Анатолий', 'Андрей', 'Антон', 'Аркадий', 'Арсен', 'Арсений', 'Артём', 'Артем', 'Артур', 'Богдан', 'Борис', 'Вадим', 'Валентин', 'Валерий', 'Василий', 'Виктор', 'Виталий', 'Владимир', 'Владислав', 'Вячеслав', 'Геннадий', 'Георгий', 'Герман', 'Глеб', 'Григорий', 'Давид', 'Даниил', 'Данил', 'Денис', 'Дмитрий', 'Евгений', 'Егор', 'Иван', 'Игорь', 'Илья', 'Кирилл', 'Константин', 'Лев', 'Леонид', 'Максим', 'Марк', 'Матвей', 'Михаил', 'Никита', 'Николай', 'Олег', 'Павел', 'Пётр', 'Петр', 'Роман', 'Руслан', 'Сергей', 'Станислав', 'Степан', 'Тимофей', 'Тимур', 'Фёдор', 'Федор', 'Эдуард', 'Юрий', 'Ярослав', 'Саша', 'Дима', 'Женя', 'Лёша', 'Леша', 'Миша', 'Коля', 'Серёжа', 'Сережа', 'Вова', 'Паша', 'Толя', 'Витя', 'Слава', 'Рома', 'Анастасия', 'Анна', 'Алина', 'Алёна', 'Алена', 'Алла', 'Альбина', 'Анжела', 'Антонина', 'Валентина', 'Валерия', 'Вера', 'Вероника', 'Виктория', 'Галина', 'Дарья', 'Диана', 'Ева', 'Евгения', 'Екатерина', 'Елена', 'Елизавета', 'Жанна', 'Зинаида', 'Злата', 'Инна', 'Ирина', 'Карина', 'Кира', 'Кристина', 'Ксения', 'Лариса', 'Лидия', 'Лилия', 'Любовь', 'Людмила', 'Маргарита', 'Марина', 'Мария', 'Милана', 'Надежда', 'Наталья', 'Наталия', 'Нина', 'Оксана', 'Олеся', 'Ольга', 'Полина', 'Раиса', 'Регина', 'Светлана', 'София', 'Софья', 'Таисия', 'Тамара', 'Татьяна', 'Ульяна', 'Эвелина', 'Элина', 'Юлия', 'Яна', 'Настя', 'Аня', 'Катя', 'Лена', 'Наташа', 'Оля', 'Таня', 'Света', 'Юля', 'Маша', 'Даша', 'Ира', 'Вика', 'Люда', 'Галя', 'Лиля', 'Ксюша', 'Алёнка', 'Марианна', 'Снежана', 'Эльвира', 'Ангелина', 'Арина', 'Варвара', 'Василиса', 'Камила', 'Ольга', 'Любава']), key=len, reverse=True)
_NAME_RE = re.compile(r"(?<![А-Яа-яЁё])(?:" + "|".join(_FIRST_NAMES) + r")(?![а-яё])"
                      r"(?:\s+[А-ЯЁ][а-яё]+(?:ов|ев|ёв|ин|ын|ский|цкий|ова|ева|ина|ына|ская|цкая|ко|юк|ук|ян|дзе))?")
# 9+ цифр подряд без пробелов («8918384538», «892825773125») или «8/+7» и 9+ цифр с разделителями — похоже на номер
_DIGITS_RE = re.compile(r"(?<!\d)\+?\d[\d()\-]{8,}\d(?!\d)|(?<!\d)(?:\+7|8)(?:[\s\-()]*\d){9,}(?!\d)")


_PHONE_ONLY_LEFT = re.compile(r"(?i)(?:т|тел\.?|телефон|wa|tg|max|звоните|пишите)\s*[:\-–—]?\s*")


_COMMISSION_RE = re.compile(r"(?i)плач\w*|комисс\w*|вознагражд\w*|агентск\w*|\d+[.,]?\d*\s*%|процент\w*\s+(?:агент|коллег)")


def mask_names(text: str | None, repl: str = "") -> str:
    """Имена агентов («Наталия», «Вячеслав Цветков») — убрать (для нейросети — заменить на [имя])."""
    return _NAME_RE.sub(repl, text or "")


def public_text(description: str | None) -> str:
    """Текст объявления для тех, у кого нет доступа к контактам, и для превью/поисковиков: как для клиента
    (без ДКП, комиссии, обременений), плюс без имён агентов и любых цифровых хвостов, похожих на номер."""
    t = client_text(description)
    t = "\n".join(ln for ln in t.splitlines() if not _COMMISSION_RE.search(ln))   # «Плачу 3%» — условия коллег не наружу
    t = _DIGITS_RE.sub("", mask_names(t))
    lines = [" ".join(ln.split()).strip(" ,.-—:;") for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if re.search(r"[А-Яа-яЁё0-9]", ln)).strip()


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
    # 18.10: в портянке «сдам … +КУ» у ОДНОГО объекта больше не делает арендой все остальные — общий текст сообщения
    # считается, только если аренда в шапке (первые строки до объекта) или у самого объекта есть «сдам/аренда».
    deal = "rent" if r.deal == "rent" or rules.detect_deal(fragment) == "rent" else "sale"
    if deal == "sale" and rules.detect_deal(text) == "rent" and (single or rules.has_rent_words(fragment)
                                                                 or rules.has_rent_words(text.split(fragment[:40])[0][:300])):
        deal = "rent"
    if deal == "rent" and rules.sale_markers(fragment, otype):
        deal = "sale"
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
        settlement=settlement, street=street, house=(house or "").strip() or None,
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
            # нейросеть (зарубежная) получает текст без телефонов и имён агентов — меньше чужих ПДн за границу
            res = llm.parse_message(mask_names(rules.mask_phones(text, "[телефон]"), "[имя]"))
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

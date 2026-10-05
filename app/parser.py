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
    """Убирает телефоны агентов из описания: его видят и покупатели без кода доступа."""
    out = []
    for line in description.splitlines():
        if rules.extract_phones(line):
            line = _CONTACT_TAIL.sub("", rules._PHONE_RE.sub("", line)).rstrip()
        out.append(line)
    return "\n".join(out).strip()


def _locate_fragment(candidate: str | None, text: str, chunks: list[str]) -> str:
    """Дословный кусок исходного текста про объект."""
    if candidate:
        cw = words(candidate)
        if cw and cw in words(text):
            # найдём точную подстроку в оригинале (с учётом регистра/пробелов)
            idx = text.find(candidate.strip()[:40])
            if idx >= 0:
                return text[idx: idx + len(candidate.strip())] if candidate.strip() in text else candidate.strip()
            return candidate.strip()
        # LLM переписал текст своими словами — выберем ближайший кусок правил
        cand_set = set(cw.split())
        best = max(chunks, key=lambda c: len(cand_set & set(words(c).split())), default=text)
        return best
    return chunks[0] if chunks else text


def _build(obj: dict | None, fragment: str, text: str, sender_phone: str | None) -> ParsedObject:
    obj = obj or {}
    text_words = words(text)
    r = rules.extract(fragment)

    otype = obj.get("type") if obj.get("type") in rules.TYPES else None
    otype = otype or r.type
    deal = obj.get("deal") if obj.get("deal") in ("sale", "rent") else r.deal

    area = _num(obj.get("area_m2"))
    land = _num(obj.get("land_sotki"))
    # Сотки ≠ м². Если нейросеть записала сотки в метры — поправляем по тексту.
    if r.land and area and abs(area - r.land) < 0.01 and not r.area:
        area, land = None, r.land
    area = area if area is not None else r.area
    land = land if land is not None else r.land
    if otype in ("flat", "new", "room"):
        land = None

    price = _int(obj.get("price_rub"))
    if price is None or price < 1000:
        price = r.price

    rooms = obj.get("rooms")
    rooms = _int(rooms) if rooms is not None else r.rooms
    if rooms is not None:
        rooms = max(0, min(rooms, 5))
    if otype in ("land", "commercial"):
        rooms = None

    floor = _int(obj.get("floor")) or r.floor
    floors = _int(obj.get("floors")) or r.floors
    if floor and floors and floor > floors:
        floor, floors = None, None

    # ЖК: только если он реально написан в тексте
    complex_rec = None
    cx_name = obj.get("complex")
    if cx_name and _in_text(re.sub(r"(?i)^\s*(?:жк|ж/к)\s+", "", cx_name), text_words):
        complex_rec = geo.resolve_complex(cx_name) or geo.Complex(name=cx_name.strip().strip("«»\""), district=None)
    if complex_rec is None:
        complex_rec = geo.find_complex_in_text(fragment)

    street = obj.get("street") if _in_text(obj.get("street"), text_words) else None
    street = street or r.street
    house = obj.get("house") or r.house
    if house and not _in_text(str(house), text_words):
        house = r.house

    # Район: 1) прямо назван в куске; 2) назван нейросетью и есть в тексте;
    # 3) из ЖК по справочнику; 4) из улицы, если улица целиком в одном районе.
    district = geo.find_district_in_text(fragment)
    if not district and obj.get("district") and _in_text(obj.get("district"), text_words):
        district = geo.canonical_district(obj.get("district")) or obj.get("district").strip()
    if not district and complex_rec and complex_rec.district:
        district = complex_rec.district
    if not district and street:
        district = geo.district_by_street(street)

    settlement = obj.get("settlement") if _in_text(obj.get("settlement"), text_words) else None

    phones = r.phones or rules.extract_phones(text)
    if not phones and sender_phone:
        p = rules.normalize_phone(sender_phone)
        if p:
            phones = [p]

    description = strip_phones((obj.get("description") or "").strip() or _clean_description(fragment))

    return ParsedObject(
        type=otype, deal=deal, rooms=rooms, area=area, land=land, floor=floor, floors=floors,
        price=price, district=district, complex=complex_rec.name if complex_rec else None,
        settlement=settlement, street=street, house=house,
        description=description, fragment=fragment.strip(), phones=phones,
    )


def _is_meaningful(o: ParsedObject) -> bool:
    has_size = any(v is not None for v in (o.area, o.land, o.rooms))
    return o.price is not None and o.type is not None and has_size


def parse(text: str, sender_phone: str | None = None, use_llm: bool | None = None) -> tuple[str, list[ParsedObject]]:
    """Возвращает (kind, objects). kind: listing | request | other."""
    text = (text or "").strip()
    if len(text) < 15:
        return "other", []
    chunks = rules.split_objects(text)

    if use_llm is None:
        use_llm = llm.available()

    if use_llm:
        try:
            res = llm.parse_message(text)
            kind = res.get("kind") if res.get("kind") in ("listing", "request", "other") else "other"
            if kind != "listing":
                return kind, []
            out = []
            for obj in res.get("objects") or []:
                if not isinstance(obj, dict):
                    continue
                frag = _locate_fragment(obj.get("fragment"), text, chunks)
                po = _build(obj, frag, text, sender_phone)
                if _is_meaningful(po):
                    out.append(po)
            return ("listing" if out else "other"), out
        except llm.LLMError as e:
            log.warning("Нейросеть недоступна, разбираю правилами: %s", e)

    # Разбор только правилами
    if rules.is_buyer_request(text):
        return "request", []
    out = [po for po in (_build(None, c, text, sender_phone) for c in chunks) if _is_meaningful(po)]
    return ("listing" if out else "other"), out

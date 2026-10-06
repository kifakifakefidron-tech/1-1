"""Один запрос к DeepSeek на одно сообщение.

Старая система гоняла каждое сообщение через 5–7 последовательных запросов, и
каждый следующий мог потерять или исказить найденное предыдущим. Здесь — один
запрос со строгой схемой ответа, а всё, что надёжнее делается правилами
(справочник районов/ЖК, телефоны, единицы площади), делается правилами.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from . import config

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты разбираешь сообщения из риелторских чатов Краснодара.
Строки сообщения пронумерованы: «12| текст».
Верни ТОЛЬКО JSON без пояснений:
{"kind": "listing" | "request" | "other",
 "objects": [{
   "lines": [первая, последняя],
   "type": "flat" | "new" | "room" | "house" | "land" | "commercial",
   "deal": "sale" | "rent",
   "rooms": число или null (студия = 0),
   "area_m2": число или null,
   "land_sotki": число или null,
   "floor": число или null, "floors": число или null,
   "price_rub": целое число в рублях или null (для аренды — в месяц),
   "complex": "название ЖК" или null,
   "district": "район/микрорайон как написано" или null,
   "settlement": "населённый пункт, если не Краснодар" или null,
   "street": "улица без слова ул." или null, "house": "номер дома" или null
 }]}

Правила:
- kind=listing — продают или сдают объект(ы). kind=request — кто-то ищет/покупает. kind=other — всё остальное (реклама, болтовня, услуги). Для request и other objects = [].
- ОДИН объект = ОДНА квартира/дом/участок/помещение со своей ценой. Подборку («портянку») обязательно дели: каждый объект отдельно.
- lines — номера первой и последней строки, где описан ЭТОТ объект (от ЖК/адреса до цены и строк сразу после неё вроде «Заклад обсуждаем»). Диапазоны разных объектов не пересекаются. Общую шапку и контакты в конце в диапазон не включай.
- Несколько вариантов одного проекта с одной площадью («две спальни — 6,3 млн, три спальни — 6,6 млн») — это один объект, цена — меньшая.
- Пиши ТОЛЬКО то, что прямо есть в тексте. Не угадывай ЖК и район «по знанию города». Нет в тексте — null.
- «сот», «соток», «сотки» — это land_sotki, НЕ area_m2. «га» = 100 соток.
- type=new — новостройка, переуступка, ДДУ, от застройщика, дом не сдан. Обычная вторичка — flat.
- Дом на участке: type=house, area_m2 — площадь дома, land_sotki — участок.
- Цена: «7,9 млн» = 7900000, «6500 т.р.» = 6500000, «7,8🍋» = 7800000. Риелторы пишут цену в тысячах:
  «Цена 3200» при продаже квартиры = 3200000, «5600 💰» = 5600000.
- НЕ цена объекта: обременение («Обрем 450 Сбербанк»), сумма в ДКП («В дкп 1350»), задаток, комиссия, кэшбэк, цена за м².
- «5/16» и «Этаж 5/16» — этаж/этажность; «ул. Домбайская 17/25», «39/2к7» — номер дома, не этаж.
"""


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.DEEPSEEK_API_KEY)


def numbered(text: str, limit: int = 8000) -> str:
    """Текст с номерами строк: нейросеть отвечает «объект — строки 3–9» вместо
    переписывания текста (так ответ короткий и не обрезается на длинных подборках)."""
    out, size = [], 0
    for i, line in enumerate(text.splitlines(), 1):
        row = f"{i}| {line}"
        size += len(row) + 1
        if size > limit:
            break
        out.append(row)
    return "\n".join(out)


def parse_message(text: str, retries: int = 2) -> dict:
    """Возвращает словарь по схеме из SYSTEM_PROMPT. Бросает LLMError."""
    body = {
        "model": config.DEEPSEEK_MODEL,
        "temperature": 0,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": numbered(text)},
        ],
    }
    req = urllib.request.Request(
        config.DEEPSEEK_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise LLMError("ответ не объект")
            parsed.setdefault("objects", [])
            return parsed
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError, LLMError) as e:
            last = e
            log.warning("DeepSeek: попытка %s не удалась: %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    raise LLMError(str(last))

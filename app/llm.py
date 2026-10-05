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
Верни ТОЛЬКО JSON без пояснений:
{"kind": "listing" | "request" | "other",
 "objects": [{
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
   "street": "улица без слова ул." или null, "house": "номер дома" или null,
   "description": "1–2 предложения о самом объекте: ремонт, состояние, особенности; без цены, телефонов и рекламы",
   "fragment": "дословный кусок исходного текста про этот объект"
 }]}

Правила:
- kind=listing — продают или сдают объект(ы). kind=request — кто-то ищет/покупает. kind=other — всё остальное (реклама, болтовня, услуги). Для request и other objects = [].
- Если в сообщении несколько объектов (прайс, подборка) — верни каждый отдельно, у каждого свой fragment.
- Пиши ТОЛЬКО то, что прямо есть в тексте. Не угадывай ЖК и район «по знанию города». Нет в тексте — null.
- «сот», «соток», «сотки» — это land_sotki, НЕ area_m2. «га» = 100 соток.
- type=new — новостройка, переуступка, ДДУ, от застройщика, дом не сдан. Обычная вторичка — flat.
- Дом на участке: type=house, area_m2 — площадь дома, land_sotki — участок.
- Цена: «7,9 млн» = 7900000, «6500 т.р.» = 6500000. Цену за м² не путай с ценой объекта.
"""


class LLMError(RuntimeError):
    pass


def available() -> bool:
    return bool(config.DEEPSEEK_API_KEY)


def parse_message(text: str, retries: int = 2) -> dict:
    """Возвращает словарь по схеме из SYSTEM_PROMPT. Бросает LLMError."""
    body = {
        "model": config.DEEPSEEK_MODEL,
        "temperature": 0,
        "max_tokens": 2500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text[:6000]},
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

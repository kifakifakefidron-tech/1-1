"""Разбор одного сообщения правилами (без нейросети)."""
from app import parser, rules
from app.ingest import make_title


def parse(text):
    return parser.parse(text, use_llm=False)


def test_land_sotki_not_square_meters():
    # Старая система: «Площадь: 20 сот» → «Участок, 20 м²»
    kind, objs = parse("Продам участок. Площадь: 20 сот, ИЖС, свет по меже. Цена 1 500 000 руб. 89183334455")
    assert kind == "listing"
    [o] = objs
    assert o.type == "land"
    assert o.land == 20
    assert o.area is None
    assert make_title(o.to_dict()) == "Участок, 20 сот."


def test_land_in_khutor_gets_no_invented_complex_or_district():
    # Старая система приписывала участку в хуторе «ЖК МОЗАИКА» и чужой район
    _, [o] = parse("Участок 20 сот, х. Ленина, ИЖС. Цена 2,8 млн. 89183334455")
    assert o.land == 20
    assert o.price == 2_800_000
    assert o.complex is None
    assert o.district == "х. Ленина"  # из текста, а не «Теплоэлектроцентраль» по догадке


def test_multi_object_price_list_is_split():
    text = (
        "Продаю квартиры в ЖК Мозаика:\n\n"
        "1-к 38 м², 5/16 эт. — 4,2 млн руб.\n\n"
        "2-к 56 м², 9/16 эт. — 6,1 млн руб.\n\n"
        "3-к 80 м², 12/16 эт. — 8,9 млн руб.\n\n"
        "Тел. 8 918 000-11-22"
    )
    kind, objs = parse(text)
    assert kind == "listing"
    assert [(o.rooms, o.area, o.floor, o.price) for o in objs] == [
        (1, 38, 5, 4_200_000), (2, 56, 9, 6_100_000), (3, 80, 12, 8_900_000)]
    # Шапка («ЖК Мозаика») и контакт в конце достаются каждому объекту
    assert all(o.complex and "Мозаика" in o.complex for o in objs)
    assert all(o.phones == ["+79180001122"] for o in objs)


def test_single_object_description_not_cut_at_blank_lines():
    text = ("2-к квартира 58 м², 7/16 эт.\n\n"
            "Сделан ремонт, остаётся мебель и техника.\n\n"
            "Цена 7,5 млн руб.")
    _, objs = parse(text)
    assert len(objs) == 1
    assert "ремонт" in objs[0].description
    assert "Цена 7,5 млн" in objs[0].description


def test_buyer_request_is_not_a_listing():
    kind, objs = parse("Куплю 2-к квартиру в ЖК Мозаика до 7 млн руб, наличные. 89181234567")
    assert kind == "request"
    assert objs == []


def test_rent_price_per_month():
    _, [o] = parse("Сдам 1-к квартиру на долгий срок, 40 м2, 5/9 эт. 25 000 руб/мес. 89184445566")
    assert o.deal == "rent"
    assert o.price == 25_000
    assert o.rooms == 1
    assert make_title(o.to_dict()).startswith("Аренда:")


def test_phone_is_not_a_price():
    assert rules.extract_price("Звоните 89181234567") is None


def test_agent_phone_removed_from_public_description():
    _, [o] = parse("Продаётся 2-к квартира, 58 м², 7/16 эт. Цена 7,5 млн руб. Тел 8 918 111-22-33")
    assert o.phones == ["+79181112233"]
    assert "111-22-33" not in o.description
    assert "Тел" not in o.description
    assert "7,5 млн" in o.description


# ─── «Портянки» из реальных чатов ──────────────────────────────────────────
PORTYANKA = """ЖК САМОЛЕТ
ул. Западный обход 39/2к7
Студия 21м2.   7/16
Ремонт. Метель
Без обрем. В дкп 1350
Цена 3 600 000
Заклад обсуждаем
ЖК СВОБОДА
ул. Домбайская 17/25
Студия 20м2.  Этаж 17
ПЧО
Обрем: 450 Сбербанк
Вся сумма в дкп
Цена 3200
Заклад обсуждаем"""


def test_portyanka_without_blank_lines_is_split():
    _, objs = parse(PORTYANKA)
    assert [(o.area, o.floor, o.floors, o.price) for o in objs] == [
        (21, 7, 16, 3_600_000), (20, 17, None, 3_200_000)]
    assert "СВОБОДА" not in objs[0].description
    assert "САМОЛЕТ" not in objs[1].description
    assert "Заклад обсуждаем" in objs[0].description


def test_emoji_digits_and_encumbrance():
    text = ("📣СРОЧНО📣\n✅ 1 к.кв.\n✅ ЖК \"Славянка\", ул.Заполярная 39 к10\n✅ 2 этаж\n✅ 38 кв.м.\n"
            "🔥Цена: 4⃣9⃣0⃣0⃣ т.р.🔥\n✅ 2 к.кв.\n✅ ЖК «Красных Партизан», ул.Заполярная 35 к6\n✅ 7 этаж\n"
            "✅ 56 кв.м.\n✅ Обременение 300 т.р/ВСЯ СУММА В ДОГОВОРЕ\n🔥Цена: 6⃣6⃣5⃣0⃣ т.р.🔥")
    _, objs = parse(text)
    assert [(o.rooms, o.area, o.floor, o.price) for o in objs] == [(1, 38, 2, 4_900_000), (2, 56, 7, 6_650_000)]


def test_realtor_price_shorthands():
    for text, price in [("Студия 24 м², 3/9 эт. Цена 7,8🍋", 7_800_000),
                        ("1к 38 м2, 5/16 эт.\n7️⃣5️⃣5️⃣0️⃣ 💰", 7_550_000),
                        ("2к 56 м2, 7/9 эт. Вся сумма в договоре. цена 27500тр", 27_500_000),
                        ("2к 42 кв м, 9 этаж\n🔥Цена : 5. 600 т р 🔥", 5_600_000)]:
        _, [o] = parse(text)
        assert o.price == price, text


def test_house_number_is_not_floor():
    _, [o] = parse("Студия 20м2, ЖК Свобода, ул. Домбайская 17/25, Этаж 17. Цена 3 200 000")
    assert (o.floor, o.floors) == (17, None)


def test_llm_line_ranges(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "parse_message", lambda text: {"kind": "listing", "objects": [
        {"lines": [1, 7], "type": "flat", "deal": "sale", "rooms": 0, "area_m2": 21, "price_rub": 3600000,
         "complex": "Самолет"},
        {"lines": [8, 15], "type": "flat", "deal": "sale", "rooms": 0, "area_m2": 20, "price_rub": 450000,
         "complex": "Самолет"},  # нейросеть ошиблась: обременение вместо цены и чужой ЖК
    ]})
    _, objs = parser.parse(PORTYANKA, use_llm=True)
    assert [(o.area, o.price) for o in objs] == [(21, 3_600_000), (20, 3_200_000)]
    assert objs[1].complex != "Самолет"
    assert objs[1].description.startswith("ЖК СВОБОДА")


def test_llm_merged_objects_are_split_by_rules(monkeypatch):
    from app import llm
    monkeypatch.setattr(llm, "parse_message", lambda text: {"kind": "listing", "objects": [
        {"lines": [1, 15], "type": "flat", "deal": "sale", "rooms": 0, "area_m2": 21, "price_rub": 3600000}]})
    _, objs = parser.parse(PORTYANKA, use_llm=True)
    assert len(objs) == 2


def test_phones_not_sent_to_llm(monkeypatch):
    from app import llm
    seen = {}
    monkeypatch.setattr(llm, "parse_message", lambda text: seen.update(text=text) or {"kind": "listing", "objects": [
        {"lines": [1, 1], "type": "flat", "deal": "sale", "rooms": 1, "area_m2": 38, "price_rub": 4500000}]})
    _, [o] = parser.parse("1-к 38 м², 5/9 эт. 4,5 млн руб. Тел 8 918 111-22-33", use_llm=True)
    assert "111-22-33" not in seen["text"] and "[телефон]" in seen["text"]
    assert o.phones == ["+79181112233"]


def test_complex_filter_matches_spelling_variants(conn):
    from app import search
    from app.ingest import save_object
    from app.parser import parse
    from .conftest import NOW
    for i, cx in enumerate(["ЖК Самолёт-2", "самолет 2", "Самолет 2"]):
        for o in parse(f"1-к квартира 40 м², {i + 2}/9 эт. {cx}. {4 + i} млн руб. 8918000000{i}", use_llm=False)[1]:
            o.complex = cx
            save_object(conn, o, None, NOW - i)
    f = search.facets(conn, search.Query(), NOW)["complexes"]
    assert len(f) == 1 and f[0]["n"] == 3
    assert search.search(conn, search.Query(complexes=["самолет 2"]), NOW, False)["total"] == 3


def test_rooms_ordinal_forms():
    from app import rules
    assert rules.extract_rooms('ЖК "Мирный" 1-я квартира, 6/6 этаж 33.40 кв. м.') == 1
    assert rules.extract_rooms("15/22 этаж 1-ая кв. 30.50 кв.м") == 1
    assert rules.extract_rooms("3-ех комн.кв") == 3
    assert rules.extract_rooms("Продам 2 квартиры") is None
    assert rules.extract_rooms("этаж 5 квартира 40м") is None

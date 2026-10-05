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
    assert o.district is None


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

"""Схема комнат (студии/мини/евро), объекты СТРЕЛ первыми, артикул вместо дублей."""
from app import feed, ingest, parser, search

from .conftest import NOW

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<yml_catalog date="2026-10-06"><shop><offers>
<offer id="111"><vendor>♟Е2 КВ♟</vendor><vendorCode>658</vendorCode><price>11900.00</price>
<name>ЖК ДОСТОЯНИЕ</name><url>https://arrowsrealty.ru/tproduct/111</url>
<picture>https://static.tildacdn.com/a.jpg</picture>
<description>♟Е2 КВ♟&lt;br /&gt;➳ ЖК ДОСТОЯНИЕ&lt;br /&gt;➵ 60м2, Этаж: 13/22&lt;br /&gt;Цена - 11 900</description>
<param name="Жилой комплекс">ЖК ДОСТОЯНИЕ</param></offer>
</offers></shop></yml_catalog>""".encode()

PORTYANKA = """ЖК ОТКРЫТИЕ
ЕВРО2
45М
15ЭТАЖ
РЕМОНТ МЕБЕЛЬ ТЕХНИКА
5700🔑🍋
🌸🌸🌸🌸🌸🌸
ЖК УЛЫБКА
СТУДИЯ
27М
18 ЭТАЖ
3600🍋🔑"""


def test_portyanka_with_emoji_between_price_and_lemon():
    _, objs = parser.parse(PORTYANKA, use_llm=False)
    assert [(o.room_kind, o.rooms, o.area, o.floor, o.price) for o in objs] == [
        ("euro", 2, 45, 15, 5_700_000), ("studio", 0, 27, 18, 3_600_000)]


def test_room_buttons_follow_mini_euro_scheme(conn, add):
    for text in ("Студия 24 м², 5/9 эт. 3,2 млн руб.", "Мини-1 26 м², 3/9 эт. 3,5 млн руб.",
                 "1-к квартира 38 м², 3/9 эт. 4,5 млн руб.", "Евро-2 40 м², 4/9 эт. 5 млн руб.",
                 "Мини-2 35 м², 2/9 эт. 4,8 млн руб.", "2-к квартира 56 м², 7/9 эт. 6,5 млн руб.",
                 "Евро-3 60 м², 8/9 эт. 7,5 млн руб."):
        add(text)

    def titles(rooms):
        res = search.search(conn, search.Query(rooms=rooms, sort="price_asc"), NOW, False)
        return [i["title"].split(",")[0] for i in res["items"]]

    assert titles([0]) == ["Студия", "Мини-1"]
    assert titles([1]) == ["Мини-1", "1-к квартира", "Мини-2", "Евро-2"]
    assert titles([2]) == ["Мини-2", "2-к квартира", "Евро-3"]


def test_feed_first_and_chat_copy_by_article_hidden(conn, add):
    add("Евро-2 40 м², 4/9 эт., ЖК Новый. 5 млн руб. 89181112233")
    # Ваше объявление из чата с артикулом — пришло раньше фида
    add("♟Е2 КВ♟\nЖК ДОСТОЯНИЕ\n60м2, Этаж: 13/22\nЦена - 11 900\n(Артикул: 658)\n8(961)857-17-72")
    feed.sync(conn, FEED, now=NOW)
    items = search.search(conn, search.Query(), NOW, False)["items"]
    assert [i["source"] for i in items] == ["feed", "chat"]  # копия скрыта, СТРЕЛЫ первыми
    assert items[0]["room_kind"] == "euro" and items[0]["price"] == 11_900_000
    # То же объявление после загрузки фида — сразу засчитывается за объект СТРЕЛ
    r = add("♟Е2 КВ♟ ЖК ДОСТОЯНИЕ 60м2, Этаж: 13/22. Цена - 11 900. Артикул 658. 89618571772")
    assert r["results"][0]["match"] == "strely"
    assert search.search(conn, search.Query(), NOW, False)["total"] == 2


def test_message_without_article_stays_separate(conn, add):
    feed.sync(conn, FEED, now=NOW)
    add("Евро-2 60м2, Этаж: 13/22, ЖК Достояние. Цена 11,9 млн. 89618571772")
    assert search.search(conn, search.Query(), NOW, False)["total"] == 2
    assert ingest.make_title({"type": "flat", "rooms": 2, "room_kind": "mini", "area": 35}) == "Мини-2, 35 м²"

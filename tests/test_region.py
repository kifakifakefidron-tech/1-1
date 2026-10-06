"""Пока только Краснодар: чаты Сочи/Адлера/Сириуса/Туапсе не берём, их объекты не показываем."""
from app import region, search, wappi

from .conftest import NOW

AD = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"


def test_is_foreign():
    assert region.is_foreign("Недвижимость СОЧИ Адлер")
    assert region.is_foreign("Сириус | квартиры")
    assert region.is_foreign("Туапсе риелторы")
    assert not region.is_foreign("Риелторы Краснодара ФМР")
    assert not region.is_foreign(None)


def test_foreign_chat_messages_not_stored(conn):
    region.save_chat(conn, "wa", "111@g.us", "Недвижимость Сочи")
    m = {"id": "a1", "body": AD, "chatId": "111@g.us", "time": NOW}
    assert wappi._store(conn, "wa", "p", m) == (False, NOW)
    m2 = {"id": "a2", "body": AD, "chatId": "222@g.us", "time": NOW}
    assert wappi._store(conn, "wa", "p", m2)[0]


def test_hide_listings_only_from_foreign_chats(conn, add):
    add(AD, chat="Квартиры Адлер Сириус")
    add("Студия 25 м², 3/9 эт., 3,1 млн. 89180000001", chat="Риелторы Краснодар")
    assert search.search(conn, search.Query(), NOW, False)["total"] == 2
    assert region.hide_foreign(conn)["hidden"] == 1
    items = search.search(conn, search.Query(), NOW, False)["items"]
    assert [i["title"] for i in items] == ["Студия, 25 м², 3/9 эт."]

"""Сохранение в базу, склейка дублей и поиск."""
from app import search

from .conftest import NOW

AGENT_A = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"
AGENT_B = "2-комнатная в ЖК Мозаика! Площадь 58 кв.м, этаж 7 из 16. Цена 7 400 000 руб. Звоните 89995556677"


def listings(conn):
    return conn.execute("SELECT * FROM listings ORDER BY id").fetchall()


def test_same_flat_from_different_agents_is_one_listing(conn, add):
    a = add(AGENT_A, chat="Чат 1", sender="Ирина")
    b = add(AGENT_B, chat="Чат 2", sender="Олег")
    assert a["results"][0]["match"] == "new"
    assert b["results"][0]["listing_id"] == a["results"][0]["listing_id"]
    [row] = listings(conn)
    assert row["seen_count"] == 2
    assert row["price"] == 7_400_000  # свежая цена
    assert "+79181112233" in row["phones"] and "+79995556677" in row["phones"]


def test_different_floors_in_same_complex_are_different(conn, add):
    add(AGENT_A)
    add(AGENT_A.replace("7/16", "12/16").replace("89181112233", "89180000000"))
    assert len(listings(conn)) == 2


def test_different_rooms_same_area_are_different(conn, add):
    add(AGENT_A)
    add(AGENT_A.replace("2-к", "3-к"))
    assert len(listings(conn)) == 2


def test_exact_repost_skips_parsing(conn, add):
    add(AGENT_A)
    r = add(AGENT_A, chat="Другой чат")
    assert r["status"] == "repost"
    [row] = listings(conn)
    assert row["seen_count"] == 2


def test_land_from_different_agents_is_one_listing(conn, add):
    add("Участок 6 сот, ул. Садовая 15, ИЖС, газ рядом. 1,9 млн руб. 89183334455")
    add("Продам землю 6 соток по ул. Садовая 15. Цена 1 900 000 руб. Тел 89187776655")
    assert len(listings(conn)) == 1


def test_neighbour_plots_are_different(conn, add):
    add("Участок 6 сот, ул. Садовая 15, ИЖС. 1,9 млн руб. 89183334455")
    add("Участок 6 сот, ул. Садовая 17, ИЖС. 1,9 млн руб. 89183334455")
    assert len(listings(conn)) == 2


def test_request_creates_nothing(conn, add):
    r = add("Ищу 1-к квартиру в ФМР до 5 млн. 89181234567")
    assert r["kind"] == "request"
    assert listings(conn) == []


def test_search_is_by_whole_words(conn, add):
    add("Студия 24 м2, ЖК Движение, 3/24 этаж, 3 200 000 руб. +7 900 222-33-44")
    add(AGENT_A)
    add("Участок 6 сот, ИЖС, хорошая дорога. 1,9 млн руб. 89181110000")
    res = search.search(conn, search.Query(q="движение"), NOW, with_contacts=False)
    assert res["total"] == 1
    assert "Движение" in res["items"][0]["complex"]
    # С окончанием тоже находит
    assert search.search(conn, search.Query(q="в мозаике"), NOW, False)["total"] == 1


def test_filters_and_contacts_visibility(conn, add):
    add(AGENT_A)
    add("Студия 24 м2, ЖК Движение, 3/24 этаж, 3 200 000 руб. +7 900 222-33-44")
    add("Сдам 1-к квартиру, 40 м2, 5/9 эт. 25 000 руб/мес. 89184445566")
    q = search.Query(rooms=[0], price_max=4_000_000)
    res = search.search(conn, q, NOW, with_contacts=False)
    assert res["total"] == 1 and res["items"][0]["rooms"] == 0
    assert "phones" not in res["items"][0]
    assert search.search(conn, search.Query(deal="rent"), NOW, False)["total"] == 1
    full = search.search(conn, q, NOW, with_contacts=True)
    assert full["items"][0]["phones"] == ["+79002223344"]


def test_smart_search_rooms_and_district_jargon(conn, add):
    add("Продаётся 2-к квартира 58 м², 7/16 эт., Фестивальный, ул. Тургенева 100. 7,5 млн руб.")
    add("Продаётся 1-к квартира 38 м², 3/9 эт., Фестивальный, ул. Тургенева 102. 4,5 млн руб.")
    add("Продаётся 2-к квартира 60 м², 5/9 эт., ЮМР, ул. Рождественская 10. 6,5 млн руб.")
    res = search.search(conn, search.Query(q="2к фмр"), NOW, False)
    assert [i["rooms"] for i in res["items"]] == [2]
    assert search.search(conn, search.Query(q="однушка фестивалка"), NOW, False)["total"] == 1
    assert search.search(conn, search.Query(q="двушка"), NOW, False)["total"] == 2


def test_stale_listing_hidden_after_20_days(conn, add):
    from app import ingest
    add(AGENT_A, ts=NOW - 25 * 86400)
    assert ingest.archive_stale(conn, now=NOW) == 1
    assert search.search(conn, search.Query(), NOW, False)["total"] == 0

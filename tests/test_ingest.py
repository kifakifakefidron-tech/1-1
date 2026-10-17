"""Сохранение в базу, склейка дублей и поиск."""
from app import search

from .conftest import NOW

AGENT_A = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"
AGENT_B = "2-комнатная в ЖК Мозаика! Площадь 58 кв.м, этаж 7 из 16. Цена 7 400 000 руб. Звоните 89995556677"


def listings(conn):
    return conn.execute("SELECT * FROM listings ORDER BY id").fetchall()


def test_same_flat_from_different_agents_stays_separate(conn, add):
    """Разные агенты с одним объектом — это рынок, показываем каждого."""
    add(AGENT_A, chat="Чат 1", sender="Ирина")
    add(AGENT_B, chat="Чат 2", sender="Олег")
    assert len(listings(conn)) == 2


def test_same_agent_reposting_is_one_listing(conn, add):
    """Тот же агент (тот же номер) прислал объект ещё раз другим текстом — склеиваем."""
    a = add(AGENT_A, chat="Чат 1")
    b = add(AGENT_B.replace("89995556677", "89181112233"), chat="Чат 2")
    assert b["results"][0]["listing_id"] == a["results"][0]["listing_id"]
    [row] = listings(conn)
    assert row["seen_count"] == 2
    assert row["price"] == 7_400_000  # свежая цена


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


def test_land_repost_by_same_agent_is_one_listing(conn, add):
    add("Участок 6 сот, ул. Садовая 15, ИЖС, газ рядом. 1,9 млн руб. 89183334455")
    add("Продам землю 6 соток по ул. Садовая 15. Цена 1 900 000 руб. Тел 89183334455")
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
    add("Сдам 1-к квартиру, 40 м2, 5/9 эт., ФМР. 25 000 руб/мес + коммуналка. 89184445566")
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


def test_stale_listing_hidden_after_15_days(conn, add):
    """17.10: без повтора 15 дней — в архив; срок не раньше, чем от появления на 1+1 (старые сообщения из истории чатов)."""
    from app import config, ingest
    assert config.STALE_DAYS == 15 and config.ARCHIVE_DAYS == 25
    lid = add(AGENT_A, ts=NOW - 25 * 86400)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET added_at = ? WHERE id = ?", (NOW - 2 * 86400, lid))   # появился на 1+1 два дня назад
    assert ingest.archive_stale(conn, now=NOW) == 0                     # старое сообщение — но на сайте всего 2 дня
    assert ingest.archive_stale(conn, now=NOW + 14 * 86400) == 1        # 16 дней на 1+1 без повтора — в архив
    assert search.search(conn, search.Query(), NOW + 14 * 86400, False)["total"] == 0
    # в архиве 25 дней — не удаляем раньше
    conn.execute("UPDATE listings SET removed_at = ? WHERE id = ?", (NOW + 14 * 86400, lid))
    assert ingest.cleanup_old(conn, now=NOW + 30 * 86400)["listings"] == 0
    assert ingest.cleanup_old(conn, now=NOW + 40 * 86400)["listings"] == 1


def test_life_fix_restores_early_archived(conn, add):
    from app import ingest
    lid = add(AGENT_A, ts=NOW - 18 * 86400)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET added_at = NULL WHERE id = ?", (lid,))
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (lid,))   # сняли как «устаревший» по дате сообщения
    rep = ingest.life_fix(conn)
    assert rep["restored"] == 1
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 1
    assert ingest.life_fix(conn) is None                                     # один раз


def test_admin_ban_listing_never_returns(conn, add):
    from app import ingest
    text = "2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ФМР. 7,5 млн руб. 89181112233"
    lid = add(text)["results"][0]["listing_id"]
    ingest.ban_listing(conn, lid)
    add(text)                                     # репост того же текста
    add("Двушка 58 м², этаж 7/16, ЖК Мозаика. Цена 7,4 млн. 89181112233")   # тот же объект другими словами
    rows = conn.execute("SELECT is_active FROM listings").fetchall()
    assert [r[0] for r in rows] == [0]            # ни старый не вернулся, ни новый не появился
    ingest.unban_listing(conn, lid)
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 1


def test_ban_phone_hides_everything_and_blocks_new(conn, add):
    from app import ingest
    a = add("2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ФМР. 7,5 млн руб. 89181112233")["results"][0]["listing_id"]
    b = add("Студия 25 м², 3/9 эт., ЖК Свобода. 3,2 млн руб. 89181112233")["results"][0]["listing_id"]
    keep = add("1-к квартира 38 м², 5/9 эт., ЖК Абрикосово. 4,5 млн. 89184445566")["results"][0]["listing_id"]
    res = ingest.ban_phone(conn, "8 918 111-22-33")
    assert res["listings"] == 2 and res["messages"] == 2
    assert {r[0]: r[1] for r in conn.execute("SELECT id, is_active FROM listings")} == {a: 0, b: 0, keep: 1}
    r = add("Евро-2 40 м², 2/5 эт., ЖК Мозаика. 6 млн. 89181112233")   # новое от этого номера — не берём
    assert r["results"][0]["match"] == "banned"
    ingest.unban_phone(conn, "+79181112233")
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0] == 3


def test_fix_rent_rule_once(conn, add):
    from app import ingest
    lid = add("Сдам 1-к квартиру, 40 м2, 5/9 эт., ФМР. 25 000 руб/мес + коммуналка. 89184445566")["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET fragment = 'Сдам 1-к, 25 000 руб/мес', description = 'Сдам 1-к' WHERE id = ?", (lid,))
    assert ingest.fix_rent_rule(conn) == {"to_sale": 0, "hidden": 1}


def test_admin_price_fix_is_learned(conn, add):
    from app import ingest
    text = "2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ФМР. Цена 7 500 000. 89181112233"
    lid = add(text)["results"][0]["listing_id"]
    ingest.set_price(conn, lid, 6_900_000)
    # тот же текст в другом сообщении (портянка) — цена уже исправленная, объявление склеивается
    add("Подборка:\n" + text + "\n\nЕщё объект: студия 25 м², 3/9, ЖК Свобода. 3,2 млн. 89184445566")
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 6_900_000
    # повтор с прежней ошибочной ценой исправленную не перебивает, новая цена — принимается
    add("Двушка 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн. 89181112233")
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 6_900_000
    add("Двушка 58 м², 7/16 эт., ЖК Мозаика. Снизили! 6,7 млн. 89181112233")
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 6_700_000


def test_feed_phone_cannot_be_banned(conn, monkeypatch):
    from app import config, ingest
    monkeypatch.setattr(config, "FEED_PHONE", "+79618571772")
    assert "партнёра" in ingest.ban_phone(conn, "8 961 857 17 72")["error"]

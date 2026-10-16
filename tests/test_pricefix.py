"""Акт приёмки 14.10: Б1 — цены в тысячах / лишние нули, Б2 — склейка не перебивает цену чужим постом."""
import time

from app import db, ingest, pricefix, search


def test_scale_and_doubt():
    assert pricefix.check(11_200, "sale", "flat", 40) == (11_200_000, False)      # «цена — 11 200р» = 11,2 млн
    assert pricefix.check(5_400, "sale", "flat", 38) == (5_400_000, False)
    assert pricefix.check(6_500_000, "sale", "flat", 50) == (6_500_000, False)
    assert pricefix.check(1_600_000_000, "sale", "flat", 73.9)[1] is True          # 1,6 млрд за двушку — под вопросом
    assert pricefix.check(45_000, "rent", "flat", 40) == (45_000, False)           # аренда — как есть
    assert pricefix.check(135_000_000, "sale", "flat", 87) == (135_000_000, True)


def _add(conn, text, mid, ts):
    m = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=mid, ts=ts)
    return ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]


def test_merge_does_not_override_price_with_far_price():
    conn = db.connect(":memory:")
    now = int(time.time())
    a = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. Цена 6 млн. 89180000701", "p1", now - 100)
    b = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. Цена 19,8 млн. 89180000701", "p2", now)
    assert a == b and conn.execute("SELECT price FROM listings WHERE id = ?", (a,)).fetchone()[0] == 6_000_000
    c = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. Цена 5,7 млн. 89180000701", "p3", now + 10)
    assert c == a and conn.execute("SELECT price FROM listings WHERE id = ?", (a,)).fetchone()[0] == 5_700_000


def test_one_time_run_returns_text_price_and_flags_doubt():
    conn = db.connect(":memory:")
    now = int(time.time())
    a = _add(conn, "2-к квартира 72 м², 16/17 эт., ул. Жигуленко 25. 7550 💰. Обр. Уралсиб 3600 т.р. 89180000711", "q1", now)
    b = _add(conn, "1-к квартира 40 м², 2/9 эт., ул. Тестовая 3. Цена 5 млн. 89180000712", "q2", now)
    conn.execute("UPDATE listings SET price = 3600000 WHERE id = ?", (a,))         # перебито чужим постом
    conn.execute("UPDATE listings SET price = 135000000, description = 'однушка' WHERE id = ?", (b,))
    conn.commit()
    rep = pricefix.run(conn)
    assert rep["text"] == 1 and rep["doubt"] == 1
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (a,)).fetchone()[0] == 7_550_000
    assert conn.execute("SELECT field, src FROM listing_edits WHERE listing_id = ?", (a,)).fetchone()[:] == ("price", "проверка цены")
    items = search.search(conn, search.Query(sort="price_desc"), now, False)["items"]
    assert items[-1]["id"] == b and items[-1]["price_doubt"]          # под вопросом — в конце сортировки по цене
    assert b not in [i["id"] for i in search.search(conn, search.Query(price_max=10**10), now, False)["items"]]


def test_complex_numbers_variants_and_junk():
    """Акт 14.10, С1–С2: «Самолёт 1» — отдельный ЖК и номер не теряется; варианты написания — один ЖК; районы — не ЖК."""
    from app import cxnorm, geo, geoaudit
    assert geo.complex_key("Самолёт 1") != geo.complex_key("Самолёт")
    for a, b in (("Акварели2", "Акварели 2"), ("СказкаГрад", "Сказка град"), ("ХорошаяПогода", "Хорошая погода"),
                 ("Новыйгород", "Новый Город"), ("Времена Года3", "Времена года 3"), ("URAL (Урал)", "Урал (Ural)"),
                 ("Альфа (Кино)", "Альфа")):
        assert geo.complex_key(a) == geo.complex_key(b), (a, b)
    assert geo.complex_key("Гарантия на Обрывной") != geo.complex_key("Гарантия на Дежнева")
    assert cxnorm.with_number("Самолет", "ЖК Самолёт-1, студия") == "Самолет 1"
    assert cxnorm.with_number("Самолет", "ЖК Самолет 2 комн") == "Самолет"
    assert cxnorm.with_number("Достояние", "ЖК «Достояние» / 15/22 этаж") == "Достояние"
    for junk in ("Панорама", "Краснодар", "Молодежный", "Музыкальный", "Жилой квартал", "Большой", "Южный", "Знаменский",
                 "Прогресс", "Победа", "Москва", "Сити", "Метальникова", "ФМР"):
        assert geoaudit.is_junk_complex(junk), junk
    for real in ("Самолет", "Мозаика", "Любимово", "Аврора", "Губернский"):
        assert not geoaudit.is_junk_complex(real), real


def test_phrase_search_equals_filters():
    """Акт 14.10, Б3: фраза из строки поиска = те же фильтры."""
    conn = db.connect(":memory:")
    now = int(time.time())
    _add(conn, "2-к квартира 50 м², 3/9 эт., ФМР, ул. Тургенева 10. 6,5 млн. 89180000801", "s1", now)
    _add(conn, "2-к квартира 52 м², 4/9 эт., ФМР, ул. Тургенева 12. 12 млн. 89180000802", "s2", now)
    _add(conn, "1-к квартира 38 м², 1/9 эт., ЮМР, ул. Ставропольская 5. 4,5 млн. 89180000803", "s3", now)
    _add(conn, "Сдам 1-к квартиру 40 м², 3/9 эт., ЮМР, ул. Ставропольская 7. 30 тыс + ком платежи. 89180000804", "s4", now)
    total = lambda **kw: search.search(conn, search.Query(**kw), now, False)["total"]  # noqa: E731
    assert total(q="2к фмр до 7 млн") == total(rooms=[2], districts=["ФМР"], price_max=7_000_000) == 1
    assert total(q="однушка юмр до 5") == 1
    assert total(q="аренда 1к") == 1 and total(q="фрм 2к") == 2
    ph = search.parse_phrase("3к не первый этаж")
    assert ph["rooms"] == [3] and ph["not_first"] and not ph["fts"]
    assert search.parse_phrase("самолет 1")["complexes"] == ["Самолет 1"]

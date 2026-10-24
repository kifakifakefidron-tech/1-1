"""24.10j (Артём: «одно и то же сообщение 1 в 1 не должно создавать новых объектов»): защита от повторов."""
import time

from app import db, ingest

T = ("Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ул. Тургенева 10. Евроремонт, кухня-гостиная. 7,5 млн. 89181112233")


def _send(conn, text, chat="c1", mid=None, ts=None, sender=None):
    m = ingest.add_message(conn, source="wa", text=text, chat_id=chat, msg_id=mid or f"{chat}-{time.time_ns()}",
                           ts=ts or int(time.time()), sender=sender)
    return ingest.process_message(conn, m, use_llm=False)


def _count(conn):
    return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]


def test_same_text_in_other_chats_is_one_object():
    conn = db.connect(":memory:")
    first = _send(conn, T, "c1")
    for chat in ("c2", "c3", "c4"):
        r = _send(conn, T, chat)
        assert r["status"] == "repost"
    assert _count(conn) == 1
    lid = first["results"][0]["listing_id"]
    assert conn.execute("SELECT seen_count FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 4


def test_same_text_with_emoji_spaces_case_is_one_object():
    conn = db.connect(":memory:")
    _send(conn, T)
    variants = ["🔥🔥 " + T + " ✅", T.upper(), T.replace(" ", "  ").replace(".", ".\n"), "‼️" + T.replace("ё", "е") + "!!!"]
    for v in variants:
        assert _send(conn, v, "c9")["status"] == "repost", v
    assert _count(conn) == 1


def test_same_object_inside_other_listing_post_is_not_duplicated():
    conn = db.connect(":memory:")
    _send(conn, T)
    other = ("1-к квартира 38 м², 5/9 эт., ЮМР, ул. Ставропольская 200. 4,2 млн. 89180002222")
    _send(conn, other + "\n\n" + T, "c5")   # тот же текст объекта — внутри другой «портянки»
    assert _count(conn) == 2


def test_repost_returns_archived_object_to_site():
    conn = db.connect(":memory:")
    lid = _send(conn, T)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (lid,))   # ушёл в архив по сроку
    conn.commit()
    _send(conn, T, "c2")
    assert _count(conn) == 1
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 1


def test_repost_after_object_was_deleted_creates_it_again():
    """Объект удалили по сроку (из архива), а сообщение-оригинал ещё в базе: повтор должен снова дать объект, а не потеряться."""
    conn = db.connect(":memory:")
    lid = _send(conn, T)["results"][0]["listing_id"]
    conn.execute("DELETE FROM listing_events WHERE listing_id = ?", (lid,))
    conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
    conn.commit()
    _send(conn, T, "c2")
    assert _count(conn) == 1


def test_same_text_from_other_sender_is_same_object():
    conn = db.connect(":memory:")
    _send(conn, T, sender="79180000001@c.us")
    _send(conn, T, "c2", sender="79180000002@c.us")
    assert _count(conn) == 1


def test_only_price_changed_updates_same_object():
    conn = db.connect(":memory:")
    lid = _send(conn, T)["results"][0]["listing_id"]
    _send(conn, T.replace("7,5 млн", "7,2 млн"), "c2")
    assert _count(conn) == 1
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 7_200_000


T2 = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ул. Тургенева 10. Евроремонт, кухня-гостиная 🔥 7,5 млн ✅"


def test_same_text_new_price_from_other_number_is_same_object():
    """24.10j (Артём): текст 1 в 1 с другого номера, но цена другая — та же карточка, цена обновилась."""
    conn = db.connect(":memory:")
    lid = _send(conn, T2, sender="79180000001@c.us")["results"][0]["listing_id"]
    r = _send(conn, T2.replace("7,5 млн", "7,2 млн"), "c2", sender="79180000002@c.us")
    assert r["results"][0]["listing_id"] == lid and _count(conn) == 1
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 7_200_000


def test_template_text_other_flat_stays_separate():
    """Тот же шаблон текста, но другая площадь и этаж — это другая квартира, не склеиваем."""
    conn = db.connect(":memory:")
    _send(conn, T2, sender="79180000001@c.us")
    _send(conn, T2.replace("58 м², 7/16", "64 м², 9/16").replace("7,5 млн", "8,1 млн"), "c2", sender="79180000002@c.us")
    assert _count(conn) == 2


def test_other_agent_own_text_same_flat_stays_separate():
    """Разные агенты своими словами про тот же объект — по-прежнему отдельные карточки (рынок)."""
    conn = db.connect(":memory:")
    _send(conn, T2, sender="79180000001@c.us")
    _send(conn, "Двушка 58 кв.м в ЖК Мозаика, Тургенева 10, 7 этаж из 16. Отличный ремонт, всё остаётся. Цена 7,4 млн.", "c2",
          sender="79180000002@c.us")
    assert _count(conn) == 2


def test_short_same_text_other_number_close_price_is_same_object():
    """Пример Артёма: «ЖК Абрикосово / ЕВРО 2 квартира / 6 млн» и то же с 5,99 млн с другого номера — одна карточка."""
    conn = db.connect(":memory:")
    a = "ЖК Абрикосово\nЕВРО 2 квартира\n5,99 млн"
    lid = _send(conn, a, sender="79180000001@c.us")["results"][0]["listing_id"]
    _send(conn, a.replace("5,99 млн", "6 млн"), "c2", sender="79180000002@c.us")
    assert _count(conn) == 1
    _send(conn, a.replace("5,99 млн", "8 млн"), "c3", sender="79180000003@c.us")   # цена сильно другая — уже не он
    assert _count(conn) == 2
    assert lid


def test_similar_elsewhere_without_area_and_floor():
    """«Похожие у других агентов» — и у объявлений без площади и этажа: то же место, комнаты, этажность и цена рядом."""
    from app import hooks
    conn = db.connect(":memory:")
    def add(phone, price, floors=16, area=None):
        return conn.execute("""INSERT INTO listings (type, deal, title, rooms, complex, price, floors, area, phones, first_seen, last_seen,
                               seen_count, is_active, source, search_text) VALUES ('flat','sale','Евро-2',2,'Абрикосово',?,?,?,?,1,1,1,1,'chat','')""",
                            (price, floors, area, f'["{phone}"]')).lastrowid
    a = add("+79180000001", 6_000_000, area=58)
    b = add("+79180000002", 5_900_000, area=58)   # этажа нет ни у кого: площадь + этажность + цена
    add("+79180000003", 5_950_000, floors=9, area=58)   # другой дом по этажности — не он
    conn.commit()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (a,)).fetchone()
    assert [x["id"] for x in hooks.same_elsewhere(conn, row)] == [b]


def test_same_text_new_price_phone_in_text_decides():
    """24.10j (Артём): «телефоны должны совпадать в тексте, а не кто отправил»."""
    conn = db.connect(":memory:")
    base = T2.replace(" ✅", " ✅ 89181112233")
    _send(conn, base, sender="79180000001@c.us")
    _send(conn, base.replace("7,5 млн", "7 млн"), "c2", sender="79180000002@c.us")   # тот же номер в тексте (сдвинулся), другой отправитель
    assert _count(conn) == 1
    _send(conn, base.replace("7,5 млн", "7,1 млн").replace("89181112233", "89184445566"), "c3", sender="79180000001@c.us")
    assert _count(conn) == 2   # в тексте другой номер — другой агент, отдельная карточка


def test_dupfix_merges_existing_twins():
    """Разовая уборка: уже созданные карточки «тот же текст и номер, другая цена» — в одну, цена — свежая."""
    from app import dupfix
    conn = db.connect(":memory:")
    ids = []
    for i, (price, ts) in enumerate((("5,99", 100), ("6", 200))):
        ids.append(conn.execute("""INSERT INTO listings (type, deal, title, rooms, complex, price, fragment, phones, first_seen, last_seen,
                                   seen_count, is_active, source, search_text) VALUES ('flat','sale','Евро-2',2,'Абрикосово',?,?,'[]',?,?,1,1,'chat','')""",
                                (5_990_000 if i == 0 else 6_000_000, f"ЖК Абрикосово\nЕВРО 2 квартира\n{price} млн", ts, ts)).lastrowid)
    conn.execute("""INSERT INTO listings (type, deal, title, rooms, complex, price, fragment, phones, first_seen, last_seen, seen_count,
                    is_active, source, search_text) VALUES ('flat','sale','Евро-2',2,'Абрикосово',6100000,'ЖК Абрикосово 2 комн 61 м 3 этаж ремонт 6,1 млн','[]',300,300,1,1,'chat','')""")
    conn.commit()
    r = dupfix.run(conn, apply=True)
    assert r["merged"] == 1
    keep = conn.execute("SELECT is_active, price FROM listings WHERE id = ?", (ids[0],)).fetchone()
    assert tuple(keep) == (1, 6_000_000)
    assert conn.execute("SELECT hidden_reason FROM listings WHERE id = ?", (ids[1],)).fetchone()[0] == "dup_text"
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0] == 2


def test_similar_price_counts_only_with_area_or_floor():
    """24.10j (Артём): «похожие» — цена засчитывается, только если совпала площадь или этаж (одна этажность + цена — не похожие)."""
    from app import hooks
    conn = db.connect(":memory:")
    def add(phone, price, floors=16, area=None, floor=None):
        return conn.execute("""INSERT INTO listings (type, deal, title, rooms, complex, price, floors, area, floor, phones, first_seen,
                               last_seen, seen_count, is_active, source, search_text) VALUES ('flat','sale','Евро-2',2,'Абрикосово',?,?,?,?,?,1,1,1,1,'chat','')""",
                            (price, floors, area, floor, f'["{phone}"]')).lastrowid
    a = add("+79180000001", 6_000_000, area=58)
    add("+79180000002", 5_900_000)                    # только этажность и цена — уже не похожие
    c = add("+79180000003", 6_100_000, area=58.5)     # площадь + цена (+ этажность)
    conn.commit()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (a,)).fetchone()
    assert [x["id"] for x in hooks.same_elsewhere(conn, row)] == [c]

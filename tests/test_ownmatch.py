"""18.10: свои объекты СТРЕЛ (наш номер) — одна карточка: пост / портянка без артикула → к объекту с сайта."""
import json

from app import db, ingest, ownmatch, search

NOW = 1_792_000_000


def _feed(conn, **kw):
    v = dict(type="flat", deal="sale", rooms=2, area=54.0, floor=4, floors=9, price=6_200_000, street="Гагарина", house="73",
             complex=None, district="ФМР", title="2-к квартира, 54 м², 4/9 эт.", photos=json.dumps(["/p/1.jpg"] * 8))
    v.update(kw)
    cur = conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, floors, price, street, house, complex, district, title,
                          photos, phones, first_seen, last_seen, seen_count, is_active, source, ext_id, search_text)
                          VALUES (:type, :deal, :rooms, :area, :floor, :floors, :price, :street, :house, :complex, :district, :title,
                          :photos, '["+79618571772"]', 1, 1, 1, 1, 'feed', :ext, '')""", {**v, "ext": f"feed:{v['street']}{v['area']}"})
    conn.commit()
    return cur.lastrowid


def _add(conn, text, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"om{n[0]}", ts=NOW)
    return ingest.process_message(conn, mid, use_llm=False)


def test_own_post_without_article_sticks_to_site_card():
    conn = db.connect(":memory:")
    fid = _feed(conn)
    r = _add(conn, "2к квартира ФМР ул. Гагарина 73, 54 м², этаж 4/9. Цена 6,2 млн. 8 (961) 857-17-72")
    assert r["results"][0]["listing_id"] == fid
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE source = 'chat' AND is_active = 1").fetchone()[0] == 0
    # чужой номер с тем же объектом — отдельная карточка (рынок), как и раньше
    r2 = _add(conn, "2к квартира ФМР ул. Гагарина 73, 54 м², этаж 4/9. Цена 6,3 млн. 8 918 111-22-33")
    assert r2["results"][0]["listing_id"] != fid


def test_own_different_object_not_stuck():
    conn = db.connect(":memory:")
    fid = _feed(conn)
    r = _add(conn, "2к квартира ФМР ул. Гагарина 75, 54 м², этаж 4/9. Цена 6,2 млн. 89618571772")   # другой дом
    assert r["results"][0]["listing_id"] != fid


def test_run_cleans_existing_own_copies():
    conn = db.connect(":memory:")
    fid = _feed(conn)
    # копия попала раньше (до правила): создаём её «как было» — вставкой
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, floors, price, street, house, title, phones, first_seen,
                    last_seen, seen_count, is_active, source, search_text) VALUES ('flat','sale',2,54.5,4,9,6150000,'Гагарина','73',
                    'копия','["+79618571772"]',1,1,3,1,'chat','')""")
    conn.commit()
    rep = ownmatch.run(conn)
    assert rep["to_feed"] == 1
    assert search.search(conn, search.Query(), NOW, True)["total"] == 1
    assert conn.execute("SELECT seen_count FROM listings WHERE id = ?", (fid,)).fetchone()[0] == 4


def test_portyanka_line_sticks_others_stay():
    conn = db.connect(":memory:")
    fid = _feed(conn)
    text = ("2к ФМР ул. Гагарина 73, 54 м², 4/9 эт. Цена 6200\n"
            "1к ЮМР ул. Чекистов 8, 34 м², 3/10 эт. Цена 5499\n"
            "Звоните 8 961 857-17-72")
    r = _add(conn, text)
    ids = [x["listing_id"] for x in r["results"]]
    assert fid in ids and len(set(ids)) == 2      # строка про Гагарина 73 — к карточке с сайта, Чекистов — своя карточка


def test_own_chat_duplicates_merge():
    conn = db.connect(":memory:")
    for txt in ("1к ЮМР ул. Чекистов 8, 34 м², этаж 3/10. Цена 5,5 млн. 89618571772",):
        _add(conn, txt)
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, floors, price, title, phones, photos, first_seen, last_seen,
                    seen_count, is_active, source, search_text) VALUES ('flat','sale',1,34.0,3,10,5500000,'строка портянки',
                    '["+79618571772"]','[]',1,1,1,1,'chat','')""")
    conn.commit()
    rep = ownmatch.run(conn)
    assert rep["merged"] == 1
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0] == 1


def test_photo_post_always_sticks_or_hides():
    """Решение Артёма: всё наше с картинкой уже есть на сайте — пост с фото отдельной карточкой не бывает."""
    conn = db.connect(":memory:")
    fid = _feed(conn, area=54.0, price=6_200_000)
    # пост с фото, разобран неточно (площадь 56 вместо 54, улица не распознана) — строго не совпал бы, мягко — да
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, price, title, phones, photos, first_seen, last_seen,
                    seen_count, is_active, source, search_text) VALUES ('flat','sale',2,56.0,6000000,'пост с фото',
                    '["+79618571772"]','["/photos/1/a.jpg"]',1,1,1,1,'chat','')""")
    # пост с фото, которому на сайте пары нет совсем — 24.10l: ОСТАЁТСЯ на сайте (снимаем только продано/задаток/срок/вручную), в отчёте админу
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, price, title, phones, photos, first_seen, last_seen,
                    seen_count, is_active, source, search_text) VALUES ('house','sale',NULL,120.0,15000000,'дом с фото',
                    '["+79618571772"]','["/photos/2/b.jpg"]',1,1,1,1,'chat','')""")
    conn.commit()
    rep = ownmatch.run(conn)
    assert rep == {"to_feed": 1, "merged": 0, "nopair": 1}
    active = [r[0] for r in conn.execute("SELECT title FROM listings WHERE is_active = 1 AND source = 'chat'")]
    assert active == ["дом с фото"]


def test_admin_set_fields_sticks_and_learns():
    conn = db.connect(":memory:")
    text = "2к квартира ФМР ул. Красная 10, 54 м², этаж 4/9. Цена 6,2 млн. 89180001122"
    lid = _add(conn, text)["results"][0]["listing_id"]
    r = ingest.set_fields(conn, lid, {"area": "56,5", "floor": 5, "repair": "no", "room_kind": "euro"}, 1)
    assert set(r["changed"]) == {"area", "floor", "repair", "room_kind"}
    row = conn.execute("SELECT area, floor, repair, room_kind, admin_fixed FROM listings WHERE id = ?", (lid,)).fetchone()
    assert tuple(row) == (56.5, 5, "no", "euro", 1)
    _add(conn, text)   # повтор того же текста — правки не перетирает
    row = conn.execute("SELECT area, floor, repair FROM listings WHERE id = ?", (lid,)).fetchone()
    assert tuple(row) == (56.5, 5, "no")
    assert ingest.set_fields(conn, lid, {"floor": 120})["error"].startswith("Этаж")


def test_archived_own_copy_tied_to_site_card():
    conn = db.connect(":memory:")
    fid = _feed(conn)
    conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, floors, price, street, house, title, phones, first_seen,
                    last_seen, seen_count, is_active, source, search_text) VALUES ('flat','sale',2,54.5,4,9,6150000,'Гагарина','73',
                    'архивная копия','["+79618571772"]',1,1,2,0,'chat','')""")
    conn.commit()
    ownmatch.run(conn)
    r = conn.execute("SELECT is_active, hidden_reason FROM listings WHERE source = 'chat'").fetchone()
    assert (r[0], r[1]) == (0, "strely")
    assert conn.execute("SELECT seen_count FROM listings WHERE id = ?", (fid,)).fetchone()[0] == 3


def test_repost_does_not_revive_hidden_copy():
    conn = db.connect(":memory:")
    text = "2к квартира ЮМР ул. Ставропольская 10, 54 м², этаж 4/9. Цена 6,2 млн. 89181112233"
    _add(conn, text)
    conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'own_dup' WHERE source = 'chat'")
    conn.commit()
    _add(conn, text)   # тот же текст ещё раз (репост)
    assert conn.execute("SELECT is_active FROM listings WHERE source = 'chat'").fetchone()[0] == 0
    conn.execute("UPDATE listings SET hidden_reason = NULL WHERE source = 'chat'")   # ушёл в архив по сроку
    conn.commit()
    _add(conn, text)
    assert conn.execute("SELECT is_active FROM listings WHERE source = 'chat'").fetchone()[0] == 1

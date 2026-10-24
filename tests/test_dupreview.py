"""24.10 (Артём: «сомневается — в спорные, разберу сам и обучу»): очередь «Возможно, повтор» (app/dupreview.py)."""
import time

from app import accounts, db, dupreview, ingest

A = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. Евроремонт, кухня-гостиная, вид на парк. 7,5 млн. 89181112233"
B = "Срочно! Двушка в ЖК Мозаика, 7 этаж из 16, 58 кв.м. Хозяин снизил цену. Документы готовы. 7,3 млн. 89181112233"
NO_FLOOR = "Двухкомнатная в ЖК Мозаика, хороший ремонт, мебель остаётся, рядом школа. 7,4 млн. 89181112233"


def _send(conn, text, chat="c1"):
    m = ingest.add_message(conn, source="wa", text=text, chat_id=chat, msg_id=f"{chat}-{time.time_ns()}", ts=int(time.time()))
    return ingest.process_message(conn, m, use_llm=False)


def _lid(r):
    return r["results"][0]["listing_id"]


def _active(conn):
    return [r[0] for r in conn.execute("SELECT id FROM listings WHERE is_active = 1 ORDER BY id")]


def test_same_agent_place_rooms_floor_is_one_card_even_with_other_text():
    conn = db.connect(":memory:")
    a = _lid(_send(conn, A))
    b = _lid(_send(conn, B, "c2"))
    assert a == b
    assert len(_active(conn)) == 1


def test_floor_missing_goes_to_queue_not_merged():
    conn = db.connect(":memory:")
    a = _lid(_send(conn, A))
    c = _lid(_send(conn, NO_FLOOR, "c2"))
    assert a != c
    res = dupreview.run(conn)
    assert res["queued"] == 1 and not res["auto"]
    assert len(_active(conn)) == 2          # сам не склеивает — решает админ
    p = dupreview.pairs(conn)["pairs"][0]
    assert p["kind"] == "nodetail" and {p["a"]["id"], p["b"]["id"]} == {a, c}


def test_admin_same_merges_and_moves_favorites_split_restores():
    conn = db.connect(":memory:")
    a = _lid(_send(conn, A))
    c = _lid(_send(conn, NO_FLOOR, "c2"))
    uid = accounts.upsert_email_user(conn, "x@y.ru")["id"]
    conn.execute("INSERT INTO favorites (user_id, listing_id, created) VALUES (?, ?, ?)", (uid, c, int(time.time())))
    dupreview.run(conn)
    pid = dupreview.pairs(conn)["pairs"][0]["id"]
    res = dupreview.decide(conn, pid, "same", keep=a)
    assert res["ok"] and res["keep"] == a
    assert _active(conn) == [a]
    assert conn.execute("SELECT merged_into, hidden_reason FROM listings WHERE id = ?", (c,)).fetchone()[:] == (a, "dup_same")
    assert conn.execute("SELECT listing_id FROM favorites WHERE user_id = ?", (uid,)).fetchone()[0] == a
    assert dupreview.split(conn, pid)
    assert _active(conn) == [a, c]
    assert conn.execute("SELECT status FROM dup_pairs WHERE id = ?", (pid,)).fetchone()[0] == "split"
    dupreview.scan(conn, full=True)          # разделили — больше не спрашиваем и сами не склеиваем
    assert dupreview.open_count(conn) == 0 and len(_active(conn)) == 2


def test_admin_diff_is_remembered():
    conn = db.connect(":memory:")
    _send(conn, A)
    _send(conn, NO_FLOOR, "c2")
    dupreview.run(conn)
    pid = dupreview.pairs(conn)["pairs"][0]["id"]
    assert dupreview.decide(conn, pid, "diff")["status"] == "diff"
    dupreview.scan(conn, full=True)
    assert dupreview.open_count(conn) == 0
    st = {s["kind"]: s for s in dupreview.stats(conn)}
    assert st["nodetail"]["diff"] == 1


def test_different_floor_or_other_agent_is_not_asked():
    conn = db.connect(":memory:")
    _send(conn, A)
    _send(conn, A.replace("7/16", "9/16").replace("вид на парк", "вид во двор"), "c2")   # другая квартира того же агента
    _send(conn, NO_FLOOR.replace("89181112233", "89184445566"), "c3")                  # другой агент — рынок
    dupreview.run(conn)
    assert dupreview.open_count(conn) == 0
    assert len(_active(conn)) == 3


def test_trusted_kind_merges_by_itself():
    conn = db.connect(":memory:")
    _send(conn, A)
    _send(conn, NO_FLOOR, "c2")
    dupreview.set_trust(conn, "nodetail", True)
    res = dupreview.run(conn)
    assert res["auto"] == 1 and len(_active(conn)) == 1
    assert dupreview.set_trust(conn, "agent", True) == ["nodetail"]   # «наверняка» — и так сами; доверять можно только сомнительным


def test_classify_similar_text_needs_same_text_phones():
    base = {"id": 1, "type": "flat", "deal": "sale", "rooms": 2, "room_kind": None, "floor": None, "floors": None, "area": None,
            "land": None, "complex": "Мозаика", "street": None, "house": None, "district": None, "settlement": None,
            "price": 7_500_000, "phones": '["+79181112233"]'}
    t1 = ("Продаётся двухкомнатная квартира в ЖК Мозаика, евроремонт, кухня-гостиная, вид на парк, рядом школа и садик, "
          "парковка во дворе, документы готовы, один собственник 89181112233")
    t2 = t1.replace("рядом школа и садик", "рядом школа, садик и магазины")
    a = dict(base, fragment=t1)
    b = dict(base, id=2, phones='["+79187776655"]', fragment=t2)
    c = dupreview.classify(a, b)
    assert c and c[0] == "similar"
    d = dict(b, fragment=t2.replace("89181112233", "89187776655"))   # другой номер в тексте — другой агент
    assert dupreview.classify(a, d) is None

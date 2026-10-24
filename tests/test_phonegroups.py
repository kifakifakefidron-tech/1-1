"""25.10 (Артём): номера одного агента — объекты с них совмещаем (app/phonegroups.py)."""
import time

from app import db, dupreview, ingest, phonegroups

T = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. Евроремонт, кухня-гостиная, вид на парк. 7,5 млн. {ph}"


def _send(conn, text, chat="c1"):
    m = ingest.add_message(conn, source="wa", text=text, chat_id=chat, msg_id=f"{chat}-{time.time_ns()}", ts=int(time.time()))
    return ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]


def _active(conn):
    return conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1").fetchone()[0]


def test_default_group_merges_same_text_from_other_number():
    conn = db.connect(":memory:")
    a = _send(conn, T.format(ph="8 918 147-71-14"))
    b = _send(conn, T.format(ph="+7 960 498 83 03"), "c2")
    assert a == b and _active(conn) == 1


def test_other_number_outside_group_stays_separate():
    conn = db.connect(":memory:")
    _send(conn, T.format(ph="89181477114"))
    _send(conn, T.format(ph="89180001122"), "c2")
    assert _active(conn) == 2


def test_saving_groups_rechecks_existing_cards():
    conn = db.connect(":memory:")
    phonegroups.save(conn, "")   # групп нет — две карточки
    _send(conn, T.format(ph="89180001122"))
    _send(conn, T.format(ph="89180003344").replace("7,5 млн", "7,3 млн"), "c2")
    dupreview.run(conn)
    assert _active(conn) == 2
    gs = phonegroups.save(conn, "+7 918 000-11-22, 8 918 000 33 44\nмусор")
    assert gs == [["+79180001122", "+79180003344"]]
    res = dupreview.run(conn)
    assert res["auto"] == 1 and _active(conn) == 1


def test_add_merges_with_existing_group():
    conn = db.connect(":memory:")
    phonegroups.save(conn, "+79180001122, +79180003344")
    gs = phonegroups.add(conn, ["8 918 000 33 44", "+7 928 555-66-77"])
    assert gs == [["+79180001122", "+79180003344", "+79285556677"]]
    gs = phonegroups.add(conn, ["+79990001111", "+79990002222"])
    assert len(gs) == 2
    assert phonegroups.listing(conn)[0]["phones"][0]["pretty"] == "+7 918 000-11-22"

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

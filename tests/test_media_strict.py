"""18.10: фото из чата — только если отправлено вместе с текстом (подпись к этой самой картинке), без окон по времени."""
import time

from app import db, ingest, media


def _msg(conn, msg_id, text, ts, sender="79180001122"):
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="g1", msg_id=msg_id, ts=ts, sender=sender)
    ingest.process_message(conn, mid, use_llm=False)
    return mid


def _img(conn, msg_id, ts, sender="79180001122"):
    media.remember(conn, "wa", "p", "g1", sender, msg_id, ts)
    return conn.execute("SELECT * FROM chat_media WHERE msg_id = ?", (msg_id,)).fetchone()


def test_photo_only_with_caption():
    conn = db.connect(":memory:")
    now = int(time.time()) - 1000
    text = "2к квартира ФМР ул. Красная 10, 54 м², этаж 4/9. Цена 6,2 млн. 89180001122"
    # 1) фото с подписью: картинка и текст — одно сообщение
    _img(conn, "cap1", now)
    _msg(conn, "cap1", text, now)
    assert media._listing_for(conn, conn.execute("SELECT * FROM chat_media WHERE msg_id='cap1'").fetchone())[0] == "ok"
    # 2) другая картинка без подписи даже через 2 секунды — не связываем («только сразу»)
    assert media._listing_for(conn, _img(conn, "alb2", now + 2))[0] == "skip"
    # 3) просто текст, а картинка отдельно через минуту — не связываем
    _msg(conn, "txt3", "1к квартира ЮМР ул. Ставропольская 5, 38 м², этаж 2/5. Цена 4 млн. 89180001122", now + 400)
    assert media._listing_for(conn, _img(conn, "lone4", now + 460))[0] == "skip"

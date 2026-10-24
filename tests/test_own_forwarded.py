"""24.10l (Артём: «объекты сняты, а присылались сегодня»): сотрудник СТРЕЛ переслал чужой объект — это НЕ объект СТРЕЛ."""
import json
import time

from app import db, ownmatch


def test_forwarded_by_own_number_is_not_own():
    assert not ownmatch.is_own(json.dumps(["+79180000009"]), json.dumps({"+79180000009": "text", "+79618571772": "sender"}))
    assert ownmatch.is_own(json.dumps(["+79618571772"]), json.dumps({"+79618571772": "text"}))
    assert ownmatch.is_own(json.dumps(["+79618571772"]), json.dumps({"+79618571772": "sender"}))   # текст без номера — прислал СТРЕЛ


def test_unhide_forwarded_once():
    conn = db.connect(":memory:")
    now = int(time.time())
    def add(phones, reason, last):
        return conn.execute("""INSERT INTO listings (type, deal, title, phones, phone_roles, first_seen, last_seen, seen_count, is_active,
                               source, search_text, hidden_reason) VALUES ('flat','sale','2-к',?,?,?,?,1,0,'chat','',?)""",
                            (json.dumps(phones), json.dumps({}), last, last, reason)).lastrowid
    a = add(["+79180000009"], "strely_photo", now)               # чужой объект, переслал СТРЕЛ — вернуть
    b = add(["+79618571772"], "strely_photo", now)               # объект СТРЕЛ «с фото без пары» — тоже вернуть (правило убрано)
    d = add(["+79618571772"], "strely", now)                     # настоящая копия объекта СТРЕЛ — остаётся приклеенной
    c = add(["+79180000008"], "strely", now - 40 * 86400)        # чужой, старый — в обычный архив
    conn.commit()
    r = ownmatch.unhide_forwarded(conn, now)
    assert r["returned"] == 2 and r["archived"] == 1
    st = {i: tuple(conn.execute("SELECT is_active, hidden_reason FROM listings WHERE id = ?", (i,)).fetchone()) for i in (a, b, c, d)}
    assert st[a] == (1, None) and st[b] == (1, None) and st[c] == (0, None) and st[d] == (0, "strely")
    assert ownmatch.unhide_forwarded(conn, now) == {}   # только один раз


def test_gone_reason_texts():
    from app import server
    conn = db.connect(":memory:")
    lid = conn.execute("""INSERT INTO listings (type, deal, title, phones, first_seen, last_seen, seen_count, is_active, source, search_text,
                          hidden_reason) VALUES ('flat','sale','2-к','[]',1,1,1,0,'chat','','deposit')""").lastrowid
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    assert server._gone_reason(row)["title"] == "Объект под задатком"

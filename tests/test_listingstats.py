"""19.10: статистика объектов агента — просмотры (один человек — один раз в день), номер, избранное, подборки."""
import time

from app import db, listingstats


def test_views_dedup_and_counts():
    listingstats._buf.clear(); listingstats._seen.clear()   # просмотры из других тестов этого процесса
    conn = db.connect(":memory:")
    lid = conn.execute("""INSERT INTO listings (type, deal, title, price, first_seen, last_seen, seen_count, is_active, source, search_text)
                          VALUES ('flat','sale','2-к', 6000000, 1, 1, 1, 1, 'chat', '')""").lastrowid
    conn.commit()
    for who in ("a", "a", "b", "c"):
        listingstats.record(conn, lid, who)
    listingstats.flush(conn)
    conn.execute("INSERT INTO phone_views (user_id, listing_id, ts) VALUES (1, ?, ?)", (lid, int(time.time())))
    conn.commit()
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    st = listingstats.for_listings(conn, [row])[lid]
    assert st["views"] == 3 and st["views7"] == 3 and st["phone"] == 1 and st["picks"] == 0

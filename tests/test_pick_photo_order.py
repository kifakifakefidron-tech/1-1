"""18.10: фото в подборке — порядок перетаскиванием и «скрыть от клиента»; клиент видит фото в этом порядке."""
import json

from app import accounts, db, picks


def _setup():
    conn = db.connect(":memory:")
    uid = accounts.create_promo_user(conn)["id"]
    lid = conn.execute("""INSERT INTO listings (type, deal, title, photos, first_seen, last_seen, seen_count, is_active, source, search_text)
                          VALUES ('flat','sale','2-к', ?, 1, 1, 1, 1, 'feed', '')""", (json.dumps(["/a.jpg", "/b.jpg", "/c.jpg"]),)).lastrowid
    cid = conn.execute("INSERT INTO collections (user_id, title, token, created, updated) VALUES (?, 'П', 'tok1', 1, 1)", (uid,)).lastrowid
    conn.execute("INSERT INTO collection_items (collection_id, listing_id, pos, added) VALUES (?, ?, 0, 1)", (cid, lid))
    conn.execute("UPDATE collection_items SET photos = ? WHERE listing_id = ?", (json.dumps(["/photos/pk/own.jpg"]), lid))
    conn.commit()
    return conn, uid, cid, lid


def test_order_hide_and_public_view():
    conn, uid, cid, lid = _setup()
    assert picks.ordered_photos(["/photos/pk/own.jpg"], ["/a.jpg", "/b.jpg", "/c.jpg"], None)[0][0] == "/photos/pk/own.jpg"
    r = picks.item_photo(conn, uid, cid, lid, order=["/c.jpg", "/a.jpg"])
    assert r["visible"][:2] == ["/c.jpg", "/a.jpg"]
    r = picks.item_photo(conn, uid, cid, lid, hide="/b.jpg")
    assert "/b.jpg" not in r["visible"] and r["hidden"] == ["/b.jpg"]
    pub = picks.public(conn, "tok1", count_view=False)
    assert pub["items"][0]["photos"] == ["/c.jpg", "/a.jpg", "/photos/pk/own.jpg"]
    r = picks.item_photo(conn, uid, cid, lid, unhide="/b.jpg")
    assert r["visible"][-1] == "/b.jpg"
    assert picks.item_photo(conn, uid, cid, lid, order=["/evil.jpg"])["visible"][0] == "/c.jpg"   # чужие адреса не принимаем

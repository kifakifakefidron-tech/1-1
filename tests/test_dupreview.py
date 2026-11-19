import json

from app import dupreview, offers


def listing(conn, phone="+79000000001"):
    return conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, street, house,
        title, phones, price, first_seen, last_seen)
        VALUES ('flat','sale',2,54,16,'Домбайская','59','Квартира',?,6800000,1,2)""",
        (json.dumps([phone]),)).lastrowid


def pair(conn, a, b, *, score=0.8, status="open", kind="similar"):
    return conn.execute("""INSERT INTO dup_pairs (a,b,kind,score,status,created_at) VALUES (?,?,?,?,?,1)""",
                        (*sorted((a, b)), kind, score, status)).lastrowid


def test_comparison_includes_candidates_beyond_first_page_without_transitive_matches(conn):
    a, b, c, d, e = [listing(conn) for _ in range(5)]
    pair(conn, a, b, score=1)
    for _ in range(32):
        pair(conn, listing(conn), listing(conn))
    pair(conn, a, c, score=0.7)
    pair(conn, a, d, score=0.6)
    pair(conn, b, e, score=0.5)
    group = dupreview.comparison(conn, [])["group"]
    assert group["anchor"]["id"] == a
    assert [p["card"]["id"] for p in group["candidates"]] == [b, c, d]
    assert dupreview.comparison(conn, [a])["group"]["anchor"]["id"] != a
    assert dupreview.comparison(conn, [], anchor=b)["group"]["anchor"]["id"] == b


def test_queue_starts_from_first_publication_even_if_old_listing_was_refreshed(conn):
    a, b, c, d, e = [listing(conn) for _ in range(5)]
    pair(conn, a, b, score=1)
    pair(conn, c, d, score=0.6)
    pair(conn, c, e, score=0.5)
    conn.execute("UPDATE listings SET last_seen=90 WHERE id=?", (b,))
    conn.execute("UPDATE listings SET first_seen=25, last_seen=25 WHERE id=?", (d,))
    conn.execute("UPDATE listings SET first_seen=30, last_seen=30 WHERE id=?", (e,))
    group = dupreview.comparison(conn, [])["group"]
    assert group["anchor"]["id"] == c
    assert [p["card"]["id"] for p in group["candidates"]] == [e, d]
    assert dupreview.comparison(conn, [c])["group"]["anchor"]["id"] == a


def test_batch_saves_only_selected_and_keeps_other_agents_data(conn):
    a, b, c, d = [listing(conn, f"+7900000000{i}") for i in range(1, 5)]
    for lid in (b, c, d):
        pair(conn, a, lid, kind="offer_review")
    before = list(map(tuple, conn.execute("SELECT id, phones, price, photos, is_active FROM listings")))
    conn.commit()
    assert not dupreview.decide_comparison(conn, a, [b, c], "same", 1)["ok"]
    result = dupreview.decide_comparison(conn, a, [b, c], "market", 1)
    assert result == {"ok": True, "status": "market", "count": 2}
    assert {r["id"] for r in offers.groups(conn)[a]} == {a, b, c}
    assert dupreview.decide_comparison(conn, a, [b, d], "diff", 1)["stale"]
    assert dupreview.open_count(conn) == 1
    assert list(map(tuple, conn.execute("SELECT id, phones, price, photos, is_active FROM listings"))) == before
    assert dupreview.decide_comparison(conn, a, [d], "diff", 1)["count"] == 1


def test_batch_merge_respects_rejections_and_can_keep_a_selected_candidate(conn):
    a, b, c, d = [listing(conn) for _ in range(4)]
    for lid in (b, c, d):
        pair(conn, a, lid)
    pair(conn, b, d, status="diff")
    conn.commit()
    assert not dupreview.decide_comparison(conn, a, [b, d], "same", 1, keep=b)["ok"]
    assert dupreview.open_count(conn) == 3
    assert dupreview.decide_comparison(conn, a, [b, c], "same", 1, keep=b)["count"] == 2
    assert [r[0] for r in conn.execute("SELECT id FROM listings WHERE is_active=1 ORDER BY id")] == [b, d]
    assert [(r[0], r[1]) for r in conn.execute("SELECT keep_id, drop_id FROM dup_pairs WHERE status='same' ORDER BY drop_id")] == [
        (b, a), (b, c)]
    assert conn.execute("SELECT status FROM dup_pairs WHERE a=? AND b=?", (b, d)).fetchone()[0] == "diff"

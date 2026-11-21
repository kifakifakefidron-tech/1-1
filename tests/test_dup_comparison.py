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



def test_queue_starts_from_first_publication_even_if_old_listing_was_refreshed(conn):
    a, b, c, d, e = [listing(conn) for _ in range(5)]
    p_old = pair(conn, a, b, score=1)
    p_first = pair(conn, c, e, score=0.5)
    p_next = pair(conn, c, d, score=0.6)
    conn.execute("UPDATE listings SET last_seen=90 WHERE id=?", (b,))
    conn.execute("UPDATE listings SET first_seen=25, last_seen=25 WHERE id=?", (d,))
    conn.execute("UPDATE listings SET first_seen=30, last_seen=30 WHERE id=?", (e,))
    assert [p["id"] for p in dupreview.pairs(conn)["pairs"]] == [p_first, p_next, p_old]
    assert [p["id"] for p in dupreview.pairs(conn, limit=1, skip=[p_first])["pairs"]] == [p_next]


def test_rejected_pairs_leave_the_other_candidates_for_review(conn):
    a, b, c = [listing(conn, f"+7900000000{i}") for i in range(1, 4)]
    p1 = pair(conn, a, b)
    p2 = pair(conn, a, c)
    p3 = pair(conn, b, c)
    before = list(map(tuple, conn.execute("SELECT * FROM listings")))
    assert dupreview.decide(conn, p1, "diff", 1)["ok"]
    assert dupreview.decide(conn, p2, "diff", 1)["ok"]
    assert [p["id"] for p in dupreview.pairs(conn)["pairs"]] == [p3]
    assert dupreview.decide(conn, p3, "market", 1)["ok"]
    assert set(offers.groups(conn)) == {b, c}
    assert list(map(tuple, conn.execute("SELECT * FROM listings"))) == before
    assert dupreview.decide(conn, p3, "diff", 1)["stale"]


def test_shared_link_with_missing_details_goes_to_an_independent_pair(conn):
    a, b, c = [listing(conn, f"+7900000000{i}") for i in range(1, 4)]
    conn.execute("UPDATE listings SET floor=NULL WHERE id=?", (c,))
    p1 = pair(conn, a, b)
    p2 = pair(conn, a, c)
    before = list(map(tuple, conn.execute("SELECT * FROM listings")))
    assert dupreview.decide(conn, p1, "market", 1)["ok"]
    assert dupreview.decide(conn, p2, "market", 1)["ok"]
    assert set(offers.groups(conn)) == {a, b}
    peer = dupreview.pairs(conn, limit=1)["pairs"][0]
    assert {peer["a"]["id"], peer["b"]["id"]} == {b, c}
    assert peer["kind"] == "offer_peer"
    assert dupreview.decide(conn, peer["id"], "market", 1)["ok"]
    assert set(offers.groups(conn)) == {a, b, c}
    assert list(map(tuple, conn.execute("SELECT * FROM listings"))) == before


def test_historical_merge_can_still_be_split(conn):
    import time
    a, b = [listing(conn) for _ in range(2)]
    p = pair(conn, a, b, status="auto")
    conn.execute("UPDATE listings SET is_active=0, hidden_reason='dup_same', merged_into=?, last_seen=? WHERE id=?",
                 (a, int(time.time()), b))
    conn.execute("UPDATE dup_pairs SET keep_id=?, drop_id=?, undo=? WHERE id=?", (a, b, '{"drop_active":1}', p))
    assert dupreview.split(conn, p, 1)
    assert conn.execute("SELECT is_active, merged_into FROM listings WHERE id=?", (b,)).fetchone()[:] == (1, None)
    assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (p,)).fetchone()[0] == "split"


def test_review_separates_listing_versions_from_other_agents(conn):
    from app import search

    old = listing(conn)
    new = listing(conn)
    other = listing(conn, "+79000000002")
    conn.execute("UPDATE listings SET last_seen=3, first_seen=3, price=7100000, floor=NULL WHERE id=?", (new,))
    repeat = pair(conn, old, new)
    market = pair(conn, old, other)
    assert not dupreview.decide(conn, repeat, "market", 1)["ok"]
    assert dupreview.decide(conn, market, "market", 1)["status"] == "market"
    assert dupreview.decide(conn, repeat, "repeat", 1, keep_id=new)["status"] == "repeat"

    def result():
        return search.search(conn, search.Query(), 4, False, group_offers=True)

    linked = result()
    assert linked["total"] == 1
    card = linked["items"][0]
    assert card["first_seen"] == 1
    assert {item["id"]: item["price"] for item in card["offer_group"]["offers"]}[new] == 7100000
    assert {item["id"] for item in card["offer_group"]["offers"]} == {new, other}
    assert not conn.execute("SELECT is_active FROM listings WHERE id=?", (old,)).fetchone()[0]
    assert dupreview.pairs(conn, "done")["counts"]["done"] == 2
    assert dupreview.decide(conn, repeat, "reopen", 1)["status"] == "open"
    assert result()["total"] == 2


def test_legacy_repeat_uses_one_version_when_contacts_are_missing(conn):
    a, b = [listing(conn) for _ in range(2)]
    conn.execute("UPDATE listings SET phones='[]' WHERE id IN (?,?)", (a, b))
    conn.execute("UPDATE dup_pairs SET status='repeat' WHERE id=?", (pair(conn, a, b),))
    group = offers.groups(conn)[a]
    reps = offers.representatives(group, set())
    assert reps[a]["id"] == reps[b]["id"] == b


def test_price_revision_scan_preserves_cards_and_existing_decisions(conn):
    text = ("Краснодар, ЖК Губернский, ул. Домбайская 59. Квартира с ремонтом, мебелью и техникой. "
            "Цена {price} ₽, возможен торг. В ДКП {contract} млн.")

    def card(price, *, phone="+79000000001", contract=4, prefix=""):
        lid = listing(conn, phone)
        conn.execute("UPDATE listings SET price=?, fragment=?, first_seen=?, last_seen=? WHERE id=?",
                     (price, prefix + text.format(price=f"{price:,}".replace(",", " "), contract=contract), lid, lid, lid))
        return lid

    a, b = card(6800000), card(6900000)
    other_agent = card(7000000, phone="+79000000002")
    other_contract = card(7100000, contract=5)
    d, e = card(6800000, prefix="Ключи в агентстве. "), card(6900000, prefix="Ключи в агентстве. ")
    prior = pair(conn, d, e)
    conn.execute("UPDATE dup_pairs SET status='diff' WHERE id=?", (prior,))
    conn.commit()
    before = list(map(tuple, conn.execute("SELECT * FROM listings ORDER BY id")))
    assert dupreview.scan_price_revisions(conn) == {"checked": 6, "pairs": 2, "marked": 1}
    result = dupreview.pairs(conn, "price")
    assert result["price_scan_ready"] and result["counts"]["price"] == 1
    found = result["pairs"][0]
    assert (found["kind"], found["a"]["id"], found["b"]["id"]) == ("price_revision", a, b)
    assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (prior,)).fetchone()[0] == "diff"
    assert not conn.execute("SELECT 1 FROM dup_pairs WHERE a IN (?,?) OR b IN (?,?)",
                            (other_agent, other_contract, other_agent, other_contract)).fetchone()
    assert list(map(tuple, conn.execute("SELECT * FROM listings ORDER BY id"))) == before
    assert dupreview.scan_price_revisions(conn)["marked"] == 0


def test_price_revision_scan_recovers_remaining_versions_after_chosen_merge(conn):
    text = "Цена{}🍋\nЖК Губернский, квартира с ремонтом, мебелью, техникой, отдельной спальней и видом на парк."
    a, b, c = [listing(conn) for _ in range(3)]
    for lid, price in ((a, 6800), (b, 6900), (c, 7100)):
        conn.execute("UPDATE listings SET fragment=?, price=?, first_seen=?, last_seen=? WHERE id=?",
                     (text.format(price), price * 1000, lid, lid, lid))
    conn.commit()
    assert dupreview.scan_price_revisions(conn)["marked"] == 2
    pid = conn.execute("SELECT id FROM dup_pairs WHERE a=? AND b=?", (a, c)).fetchone()[0]
    assert dupreview.decide(conn, pid, "repeat", user_id=1, keep_id=a)["ok"]
    conn.commit()
    assert not dupreview.pairs(conn, "price")["price_scan_ready"]
    assert dupreview.scan_price_revisions(conn)["marked"] == 1
    found = dupreview.pairs(conn, "price")["pairs"]
    assert len(found) == 1 and {found[0]["a"]["id"], found[0]["b"]["id"]} == {a, b}

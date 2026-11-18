import json
import time

from app import hooks, offers, search


def listing(conn, phone, *, price=6800000, floor=16, house="59", media=("a", "b"), deal="sale"):
    now = int(time.time())
    row = conn.execute("""INSERT INTO listings(type, deal, rooms, room_kind, area, floor, floors, street, house,
        complex, price, title, first_seen, last_seen, phones) VALUES ('flat', ?, 2, 'classic', 54, ?, 24,
        'Домбайская', ?, 'Губернский', ?, '2-к квартира', ?, ?, ?)""",
        (deal, floor, house, price, now, now, json.dumps([phone])))
    lid = row.lastrowid
    conn.execute("UPDATE listings SET photos = ? WHERE id = ?",
                 (json.dumps([f"/photos/{lid}/{key * 16}.jpg" for key in media]), lid))
    return lid


def decide(conn, a, b, status):
    conn.execute("INSERT INTO dup_pairs(a,b,kind,status,created_at) VALUES (?,?,'test',?,0)", (a, b, status))


def test_groups_paginate_and_only_show_prices_that_match(conn):
    a = listing(conn, "79000000001")
    b = listing(conn, "79000000002", price=7100000)
    c = listing(conn, "79000000003", house="61", price=6750000)
    qr = search.Query(price_max=6900000, size=1, sort="price_asc")
    result = search.search(conn, qr, int(time.time()), False, group_offers=True)
    assert result["total"] == result["pages"] == 2
    assert result["items"][0]["id"] == c
    qr.page = 2
    item = search.search(conn, qr, int(time.time()), False, group_offers=True)["items"][0]
    group = item["offer_group"]
    assert item["id"] == a
    assert group["matching"] == 1 and group["min_price"] == 6800000 and not group["price_range"]
    assert [(r["id"], r["matches"]) for r in group["offers"]] == [(a, True), (b, False)]
    assert "7900000000" not in json.dumps(group)
    qr.price_max = None
    assert search.search(conn, qr, int(time.time()), False, group_offers=True)["total"] == 2
    assert search.search(conn, qr, int(time.time()), False)["total"] == 3


def test_shared_layout_alone_and_other_addresses_stay_separate(conn):
    a = listing(conn, "79000000001")
    listing(conn, "79000000002", media=("a", "c"))
    listing(conn, "79000000003", floor=17)
    listing(conn, "79000000004", house=None)
    listing(conn, "79000000005", deal="rent")
    listing(conn, "79000000001")
    assert a not in offers.groups(conn)


def test_explicit_links_and_rejections_prevent_false_chains(conn):
    a = listing(conn, "79000000001", media=())
    b = listing(conn, "79000000002")
    c = listing(conn, "79000000003")
    decide(conn, a, b, "market")
    decide(conn, a, c, "diff")
    index = offers.groups(conn)
    assert {row["id"] for row in index[a]} == {a, b}
    assert c not in index
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (b,))
    assert not offers.groups(conn)


def test_groups_honor_hidden_offers_and_real_owner_confirmation(conn):
    a = listing(conn, "79000000001")
    b = listing(conn, "79000000002")
    now = int(time.time())
    conn.execute("INSERT INTO users(id, created) VALUES (1, ?)", (now,))
    conn.execute("UPDATE listings SET source = 'own' WHERE id = ?", (a,))
    conn.execute("INSERT INTO listing_edits(listing_id,user_id,ts,field,new) VALUES (?,1,?,'confirm',?)", (a, now, '"yes"'))
    group = offers.summary(offers.groups(conn)[a])
    assert [(r["id"], r["confirmed_at"]) for r in group["offers"]] == [(a, now), (b, None)]
    conn.execute("INSERT INTO hidden_listings(user_id,listing_id,ts) VALUES (1,?,?)", (b, now))
    assert not offers.groups(conn, hide_user=1)


def test_strely_first_with_own_media_and_one_market_basis(conn, monkeypatch):
    now = int(time.time())
    site = listing(conn, "79000000001", price=5_400_000, media=("a", "b"))
    other = listing(conn, "79000000002", price=5_400_000, media=("c", "d"))
    conn.execute("UPDATE listings SET source='feed',ext_id='feed:1',price_m2=100000 WHERE id=?", (site,))
    conn.execute("""UPDATE listings SET source='feed',ext_id='appletv:article:1',price_m2=80000,area=67.5,
                    repair='no',last_seen=? WHERE id=?""", (now + 100, other))
    decide(conn, site, other, "market")
    for i in range(20):
        lid = listing(conn, f"7900000001{i:02}", house=str(i + 100), price=5_400_000, media=())
        conn.execute("UPDATE listings SET price_m2=100000,repair='no' WHERE id=?", (lid,))
    monkeypatch.setattr(hooks, "_market", {"t": 0, "data": {}})
    group = offers.groups(conn)[other]
    assert [row["id"] for row in group] == [site, other]
    main_market = hooks.offer_market(conn, group, 5_400_000)
    assert main_market["diff"] == 0 and main_market["price"] == 5_400_000 and main_market["n"] == 21
    assert hooks.offer_market(conn, group, 4_320_000)["diff"] == -20
    conn.execute("UPDATE listings SET price=4320000,price_m2=64000 WHERE id=?", (other,))
    result = search.search(conn, search.Query(price_max=5_000_000), now, False, group_offers=True)
    item = result["items"][0]
    assert item["id"] == other and item["photos"][0] == f"/photos/{other}/" + "c" * 16 + ".jpg"
    assert item["market"]["diff"] == -20
    assert [(o["id"], o["matches"]) for o in item["offer_group"]["offers"]] == [(site, False), (other, True)]
    item = next(i for i in search.search(conn, search.Query(), now, False, group_offers=True)["items"] if i.get("offer_group"))
    assert item["id"] == site and item["photos"][0] == f"/photos/{site}/" + "a" * 16 + ".jpg"

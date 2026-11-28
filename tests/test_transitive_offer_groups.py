import json

from app import offers


def _add_offer(conn, number, *, floor=16):
    return conn.execute("""INSERT INTO listings(type,deal,rooms,area,floor,floors,street,house,price,title,
                                                first_seen,last_seen,phones,description,photos)
                           VALUES ('flat','sale',2,54,?,24,'Домбайская','59',?,'Квартира',1,1,?,?,?)""",
                        (floor, 6800000 + number * 100000, json.dumps([f"+7900000000{number}"]),
                         f"Описание от агента {number}", json.dumps([f"/photos/{number}/image.jpg"]))).lastrowid


def _save_link(conn, a, b, status, *, user_id=1):
    conn.execute("""INSERT INTO dup_pairs(a,b,kind,status,created_at,decided_at,user_id)
                    VALUES (?,?,'offer_review',?,1,2,?)""", (a, b, status, user_id))


def test_manual_offer_links_infer_membership_despite_missing_fields(conn):
    a, b, c = _add_offer(conn, 1), _add_offer(conn, 2), _add_offer(conn, 3, floor=None)
    _save_link(conn, a, b, "market")
    _save_link(conn, a, c, "market")
    evidence = "SELECT id,price,phones,description,photos FROM listings ORDER BY id"
    before = list(map(tuple, conn.execute(evidence)))

    group = offers.groups(conn)[a]
    assert {card["id"] for card in group} == {a, b, c}
    assert {item["id"] for item in offers.representatives(group, set()).values()} == {a, b, c}
    confirmed = offers.confirmed_group_ids(conn)
    assert confirmed[a] == confirmed[b] == confirmed[c]
    assert list(map(tuple, conn.execute(evidence))) == before


def test_manual_offer_links_respect_an_existing_different_decision(conn):
    a, b, c = _add_offer(conn, 1), _add_offer(conn, 2), _add_offer(conn, 3, floor=None)
    _save_link(conn, a, b, "market")
    _save_link(conn, a, c, "market")
    _save_link(conn, b, c, "diff")

    assert {card["id"] for card in offers.groups(conn)[a]} == {a, b}
    confirmed = offers.confirmed_group_ids(conn)
    assert confirmed[b] != confirmed.get(c)


def test_manual_offer_links_respect_a_conflicting_known_floor(conn):
    a, b, c = _add_offer(conn, 1, floor=None), _add_offer(conn, 2), _add_offer(conn, 3, floor=17)
    _save_link(conn, a, b, "market")
    _save_link(conn, a, c, "market")

    assert {card["id"] for card in offers.groups(conn)[a]} == {a, b}
    confirmed = offers.confirmed_group_ids(conn)
    assert confirmed[b] != confirmed.get(c)


def test_an_automatic_link_alone_cannot_infer_membership_with_missing_details(conn):
    a, b, c = _add_offer(conn, 1), _add_offer(conn, 2), _add_offer(conn, 3, floor=None)
    _save_link(conn, a, b, "market")
    _save_link(conn, a, c, "market", user_id=0)

    assert {card["id"] for card in offers.groups(conn)[a]} == {a, b}
    assert c not in offers.confirmed_group_ids(conn)

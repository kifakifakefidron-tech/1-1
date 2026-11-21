import json
import time

from app import config, dupfix, dupreview, hooks, ingest, offers, search


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
    c = listing(conn, "79000000003", house="61", price=6750000, media=("d", "e"))
    qr = search.Query(price_max=6900000, size=1, sort="price_asc")
    result = search.search(conn, qr, int(time.time()), False, group_offers=True)
    assert result["total"] == result["pages"] == 2
    assert result["offer_total"] == 2
    assert result["items"][0]["id"] == c
    qr.page = 2
    item = search.search(conn, qr, int(time.time()), False, group_offers=True)["items"][0]
    group = item["offer_group"]
    assert item["id"] == a
    assert group["matching"] == 1 and group["min_price"] == 6800000 and not group["price_range"]
    assert [(r["id"], r["matches"]) for r in group["offers"]] == [(a, True), (b, False)]
    assert "7900000000" not in json.dumps(group)
    qr.price_max = None
    now = int(time.time())
    unfiltered = search.search(conn, qr, now, False, group_offers=True)
    assert unfiltered["total"] == unfiltered["pages"] == 2
    assert unfiltered["offer_total"] == search.catalog_counts(conn, now)["sale_offers"] == 3
    assert search.catalog_counts(conn, now)["new_offers_24h"] == 3
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


def test_new_offers_cannot_republish_a_known_property(conn):
    now = int(time.time())
    older = listing(conn, "79000000001", price=6_900_000, media=())
    newer = listing(conn, "79000000002", price=6_500_000, media=())
    fresh = listing(conn, "79000000003", price=6_000_000, house="61", media=())
    imported = listing(conn, "79000000004", house="62", media=())
    born = now - 3 * 86400
    conn.execute("UPDATE listings SET first_seen=? WHERE id=?", (born, older))
    conn.execute("UPDATE listings SET catalog_migrated=1 WHERE id=?", (imported,))
    decide(conn, older, newer, "market")

    def items(**kw):
        return search.search(conn, search.Query(**kw), now, False, group_offers=True)["items"]

    # Дешёвое новое предложение проходит фильтр, но сохраняет дату старой карточки.
    matched = items(price_max=6_600_000, hl=now - 60)
    assert [row["id"] for row in matched] == [fresh, newer]
    assert matched[1]["first_seen"] == born
    assert [row["id"] for row in items(new_days=1)] == [fresh, newer]
    assert [row["id"] for row in items(since=now - 60)] == [fresh, newer]
    assert items(new_days=1)[1]["first_seen"] == born
    assert items(new_days=1)[1]["offer_group"]["matching"] == 1
    assert [row["id"] for row in items()] == [fresh, newer, imported]
    recent = search.search(conn, search.Query(new_days=1), now, False, group_offers=True)
    assert recent["offer_total"] == search.catalog_counts(conn, now)["new_offers_24h"] == 2
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (older,))
    assert [row["id"] for row in items(new_days=1)] == [fresh, newer]
    assert items()[1]["first_seen"] == born


def test_new_offers_exclude_revisions_linked_through_archived_versions(conn):
    now = int(time.time())
    original = listing(conn, "79000000001", media=())
    intermediate = listing(conn, "79000000001", price=6900000, media=())
    current = listing(conn, "79000000001", price=7000000, media=())
    fresh = listing(conn, "79000000002", house="61", media=())
    conn.execute("UPDATE listings SET first_seen=? WHERE id=?", (now - 3 * 86400, original))
    conn.execute("UPDATE listings SET is_active=0 WHERE id IN (?, ?)", (original, intermediate))
    decide(conn, original, intermediate, "repeat")
    decide(conn, intermediate, current, "repeat")

    stats = search.catalog_counts(conn, now)
    result = search.search(conn, search.Query(new_days=1), now, False, group_offers=True)
    assert stats["sale_offers"] == 2 and stats["new_offers_24h"] == result["offer_total"] == 1
    assert [row["id"] for row in result["items"]] == [fresh]


DETAIL = ("Двухкомнатная квартира на Домбайской улице, чистый подъезд, кухня с выходом на застеклённую лоджию. "
          "Окна выходят во двор на детскую площадку. Отдельная гардеробная справа от входа. Показ после шести.")


def test_recheck_links_one_photo_with_property_text_and_queues_weak_matches(conn):
    a = listing(conn, "79000000001", media=("a", "b"), price=6800000)
    b = listing(conn, "79000000002", media=("a",), price=7400000)
    c = listing(conn, "79000000003", media=("a", "c"))
    rejected = listing(conn, "79000000004", media=("a",))
    wrong_floor = listing(conn, "79000000005", floor=17, media=())
    for lid, phone, price in ((a, "79000000001", "6 800 000"), (b, "79000000002", "7 400 000"),
                              (rejected, "79000000004", "6 800 000")):
        conn.execute("UPDATE listings SET description=? WHERE id=?", (f"{DETAIL} Цена {price} руб. {phone}", lid))
    conn.execute("UPDATE listings SET first_seen=1 WHERE id=?", (a,))
    decide(conn, a, rejected, "diff")
    decide(conn, b, rejected, "diff")
    evidence = "SELECT id, photos, description, phones, price, first_seen FROM listings ORDER BY id"
    before = list(map(tuple, conn.execute(evidence)))

    assert dupreview.recheck_offers(conn, batch=1) == {"rows": 1, "linked": 0, "queued": 0}
    assert dupreview.recheck_offers(conn)["linked"] == 1
    assert dupreview.recheck_offers(conn) == {}
    assert list(map(tuple, conn.execute(evidence))) == before
    assert {row["id"] for row in offers.groups(conn)[a]} == {a, b}
    assert c not in offers.groups(conn)
    assert not conn.execute("SELECT 1 FROM dup_pairs WHERE a=? OR b=?", (wrong_floor, wrong_floor)).fetchone()
    status = conn.execute("SELECT status, kind FROM dup_pairs WHERE a=? AND b=?", (a, b)).fetchone()
    assert tuple(status) == ("market", "offer_photo")
    assert conn.execute("SELECT status FROM dup_pairs WHERE a=? AND b=?", (a, rejected)).fetchone()[0] == "diff"

    review = conn.execute("SELECT id FROM dup_pairs WHERE a=? AND b=? AND status='open'", (a, c)).fetchone()
    assert dupreview.decide(conn, review["id"], "market", user_id=1) == {"ok": True, "status": "market"}
    assert {row["id"] for row in offers.groups(conn)[a]} == {a, b, c}
    assert list(map(tuple, conn.execute(evidence))) == before
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (a,))
    assert offers.archived_publications(conn, b)[b][0] == 1


def test_pair_scan_finds_older_properties_and_ignores_doubtful_price(conn):
    a = listing(conn, "79000000001", media=())
    b = listing(conn, "79000000002", media=())
    conn.execute("UPDATE listings SET type='new', street='ул. Домбайская' WHERE id=?", (a,))
    conn.execute("UPDATE listings SET last_seen=1, price_doubt=1 WHERE id=?", (b,))
    conn.executemany("""INSERT INTO listings(type, deal, rooms, area, floor, floors, street, house, price,
        title, first_seen, last_seen) VALUES ('flat','sale',2,54,16,24,'Красная',?,6800000,'Другая квартира',1,2)""",
        ((str(i),) for i in range(630)))
    assert dupreview.recheck_offers(conn, batch=2)["queued"] == 1
    pair = conn.execute("SELECT a,b,why FROM dup_pairs WHERE status='open'").fetchone()
    assert (pair["a"], pair["b"]) == (a, b) and "Близкая цена" not in pair["why"]


def test_reposted_text_with_separate_agent_phones_cannot_merge_original_data(conn):
    announcement = "Продам 2-к квартиру 54 м², 16/24 этаж, Краснодар, ЖК Губернский, ул. Домбайская 59. " + DETAIL
    now = int(time.time())
    for i, (phone, price) in enumerate((("79000000001", "6 800 000"), ("79000000002", "6 800 000"),
                                       ("79000000001", "6 900 000"))):
        mid = ingest.add_message(conn, source="wa", text=f"{announcement} Цена {price} руб.",
                                 ts=now + i, msg_id=f"distinct-{i}", sender=phone)
        assert ingest.process_message(conn, mid, use_llm=False)["status"] == "done"
    rows = list(conn.execute("SELECT id, price, phones FROM listings ORDER BY id"))
    assert [(row["price"], json.loads(row["phones"])) for row in rows] == [
        (6900000, ["+79000000001"]), (6800000, ["+79000000002"])]
    assert dupfix.run(conn)["merged"] == 0
    assert dupreview.scan(conn)["auto"] == 0
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0] == 2


_COMPACT_PRICE_TEXT = ("Ул. Тепличная 94\n1-к квартира, 43 м², 6/7 этаж\n"
                       "Ремонт, мебель, техника. Полная сумма в договоре. Быстрый выход на сделку.")


def _import_priced_post(conn, text, sender, ts, suffix):
    mid = ingest.add_message(conn, source="wa", text=text, ts=ts, msg_id=f"compact-price-{suffix}", sender=sender)
    result = ingest.process_message(conn, mid, use_llm=False)
    assert result["status"] == "done"
    return result["results"][0]["listing_id"]


def test_compact_price_updates_same_listing_without_transferring_contacts(conn):
    now = int(time.time())
    sender = "79000000001"
    original = _import_priced_post(conn, f"{_COMPACT_PRICE_TEXT}\nЦена3450🍋", sender, now, "first")
    other_agent = _import_priced_post(conn, f"{_COMPACT_PRICE_TEXT}\nЦена3470🍋", "79000000002", now + 1, "other")
    new_text = f"{_COMPACT_PRICE_TEXT}\nЦена3400🍋"
    assert _import_priced_post(conn, new_text, sender, now + 2, "revision") == original
    other_floor = _import_priced_post(conn, new_text.replace("6/7", "5/7"), sender, now + 3, "other-floor")
    assert len({original, other_agent, other_floor}) == 3
    rows = [(r["id"], r["price"], json.loads(r["phones"])) for r in conn.execute("SELECT * FROM listings ORDER BY id")]
    assert rows == [(original, 3400000, ["+79000000001"]), (other_agent, 3470000, ["+79000000002"]),
                    (other_floor, 3400000, ["+79000000001"])]
    assert conn.execute("SELECT first_seen FROM listings WHERE id=?", (original,)).fetchone()[0] == now
    assert [r[0] for r in conn.execute("SELECT price FROM listing_events WHERE listing_id=? ORDER BY ts", (original,))] == [
        3450000, 3400000,
    ]


def test_compact_price_does_not_change_two_possible_candidates(conn):
    now = int(time.time())
    first = _import_priced_post(conn, f"{_COMPACT_PRICE_TEXT}\nЦена3450🍋", "79000000001", now, "first")
    second = conn.execute("""INSERT INTO listings (type,deal,rooms,area,floor,floors,street,house,price,
        fragment,phones,title,first_seen,last_seen)
        SELECT type,deal,rooms,area,floor,floors,street,house,3460000,replace(fragment,'3450','3460'),phones,title,
               first_seen,last_seen FROM listings WHERE id=?""", (first,)).lastrowid
    latest = _import_priced_post(conn, f"{_COMPACT_PRICE_TEXT}\nЦена3400🍋", "79000000001", now + 1, "ambiguous")
    assert latest not in (first, second)
    assert [r[0] for r in conn.execute("SELECT price FROM listings ORDER BY id")] == [3450000, 3460000, 3400000]


def test_confirmed_group_shows_each_agent_once_and_counts_one_property(conn, monkeypatch):
    monkeypatch.setattr(config, "APPLET_SLAVA_PHONES", "+79000000011,+79000000012")
    monkeypatch.setattr(config, "APPLET_ARTEM_PHONE", "+79000000013")
    site = listing(conn, "+79000000013", price=7250000)
    stayl = listing(conn, "+79000000013", price=7250000, media=())
    slava = listing(conn, "+79000000011", price=7250000, media=())
    slava_again = listing(conn, "+79000000012", price=7250000, media=())
    agent = listing(conn, "+79000000014", price=7200000, media=("c", "d"))
    for lid, ext in ((site, "feed:original"), (stayl, "appletv:stayl:row:x"), (slava, "appletv:article:x"),
                     (slava_again, "appletv:row:x")):
        conn.execute("UPDATE listings SET source='feed', ext_id=? WHERE id=?", (ext, lid))
    now = int(time.time())
    conn.execute("UPDATE listings SET first_seen=?, last_seen=? WHERE id=?", (now - 172800, now, site))
    for lid in (stayl, slava, slava_again, agent):
        decide(conn, site, lid, "market")
    listing(conn, "+79000000015", deal="rent")
    group = offers.groups(conn)[site]
    mapped = offers.representatives(group, set())
    assert mapped[stayl]["id"] == site and mapped[slava]["id"] == mapped[slava_again]["id"]
    unfiltered = search.search(conn, search.Query(), now, False, group_offers=True)
    assert unfiltered["total"] == 1 and unfiltered["items"][0]["id"] == site
    assert unfiltered["offer_total"] == 3
    assert [p["id"] for p in unfiltered["items"][0]["offer_group"]["offers"]] == [site, agent, slava_again]
    filtered = search.search(conn, search.Query(price_max=7220000), now, False, group_offers=True)
    assert filtered["items"][0]["id"] == agent
    assert filtered["offer_total"] == 1
    assert search.catalog_counts(conn, now) == {
        "sale": 1, "rent": 1, "new_24h": 1, "sale_offers": 3, "rent_offers": 1, "new_offers_24h": 3,
    }
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0] == 6


def test_reused_photos_across_different_addresses_are_not_proof(conn):
    for i, house in enumerate(("59", "59", "61"), 1):
        listing(conn, f"+7900000000{i}", house=house)
    assert not offers.groups(conn)
    assert dupreview.recheck_offers(conn)["queued"] == 1


def test_ingest_does_not_union_partly_overlapping_contact_lists(conn):
    from app import parser

    now = int(time.time())
    text = "Продам 2-к квартиру 54 м², 16/24 этаж, ЖК Губернский, ул. Домбайская 59. " + DETAIL + " Цена 6 800 000 руб."
    _, (a,) = parser.parse(text, use_llm=False)
    _, (b,) = parser.parse(text, use_llm=False)
    a.phones = ["+79000000001", "+79000000002"]
    b.phones = ["+79000000001", "+79000000003"]
    first, _ = ingest.save_object(conn, a, None, now)
    second, _ = ingest.save_object(conn, b, None, now + 1)
    assert first != second
    assert [json.loads(row[0]) for row in conn.execute("SELECT phones FROM listings ORDER BY id")] == [a.phones, b.phones]

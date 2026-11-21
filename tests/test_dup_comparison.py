import json
from contextlib import closing

from app import accounts, config, db, dedupe, dupreview, llm, offers, picks, repeat_refs, search


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


def _price_version(conn, first_seen, price, *, last_seen=None, phones='["+79000000001"]'):
    text = ("ЖК Губернский, ул. Домбайская 59. Квартира с ремонтом, мебелью и техникой. "
            f"Цена {price} ₽, возможен торг. В ДКП 4 млн.")
    lid = conn.execute("""INSERT INTO listings(type,deal,title,rooms,area,floor,street,house,price,
        phones,first_seen,last_seen,fragment,description)
        VALUES ('flat','sale','Квартира',2,54,16,'Домбайская','59',?,?,?,?,?,?)""",
        (price, phones, first_seen, last_seen or first_seen, text, text)).lastrowid
    conn.execute("INSERT INTO listing_events(listing_id,ts,price,fragment_hash) VALUES (?,?,?,?)",
                 (lid, first_seen, price, dedupe.fragment_hash(text)))
    return lid


def test_auto_price_keeps_newest_version_and_combines_history_and_photos(conn):
    older = _price_version(conn, 10, 6900000, last_seen=90)
    middle = _price_version(conn, 20, 6800000)
    newest = _price_version(conn, 30, 6850000)
    uid = accounts.upsert_email_user(conn, "price-photo@example.test")["id"]
    conn.execute("INSERT INTO favorites(user_id,listing_id,created) VALUES (?,?,?)", (uid, older, 11))
    conn.executemany("UPDATE listings SET photos=? WHERE id=?", [
        (json.dumps([f"https://example.test/photo-{lid}.jpg", "https://example.test/shared.jpg"]), lid)
        for lid in (older, middle, newest)
    ])
    assert dupreview.scan_price_revisions(conn, apply=True) == {"checked": 3, "pairs": 0, "marked": 0, "merged": 2}
    assert conn.execute("SELECT id FROM listings WHERE is_active=1").fetchone()[0] == newest
    assert conn.execute("SELECT listing_id FROM favorites WHERE user_id=?", (uid,)).fetchone()[0] == newest
    assert {tuple(r) for r in conn.execute("SELECT id,merged_into FROM listings WHERE is_active=0")} == {
        (older, newest), (middle, newest),
    }
    detail = search.listing_detail(conn, newest, False, True)
    assert detail["price"] == 6850000
    assert detail["photos"] == [f"https://example.test/photo-{newest}.jpg", "https://example.test/shared.jpg",
                                f"https://example.test/photo-{older}.jpg", f"https://example.test/photo-{middle}.jpg"]
    assert [p["price"] for p in detail["price_history"]] == [6900000, 6800000, 6850000]
    assert [p["price"] for p in detail["history"]] == [6900000, 6800000, 6850000]
    assert offers.archived_publications(conn, newest)[newest][0] == 10
    assert dupreview.scan_price_revisions(conn, apply=True)["merged"] == 0
    decisions = dupreview.pairs(conn, "done")
    assert decisions["price_auto"] == 2 and decisions["counts"]["price"] == 0
    pid = decisions["pairs"][0]["id"]
    assert dupreview.decide(conn, pid, "reopen", user_id=uid)["ok"]
    assert dupreview.scan_price_revisions(conn, apply=True)["merged"] == 0
    assert [p["price"] for p in search.listing_detail(conn, newest, False)["price_history"]] == [6900000, 6850000]


def test_auto_price_respects_existing_decisions_and_manual_corrections(conn):
    for i, status in enumerate(("diff", "market", "split", "open", None), 1):
        phones = f'["+7900000000{i}"]'
        a = _price_version(conn, 10, 6900000, phones=phones)
        b = _price_version(conn, 20, 6800000, phones=phones)
        if status:
            pid = pair(conn, a, b, status=status)
            conn.execute("UPDATE dup_pairs SET user_id=1 WHERE id=?", (pid,))
        else:
            conn.execute("UPDATE listings SET price_fixed=1 WHERE id=?", (a,))
    assert dupreview.scan_price_revisions(conn, apply=True)["merged"] == 0
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0] == 10
    assert [r[0] for r in conn.execute("SELECT status FROM dup_pairs WHERE user_id=1 ORDER BY id")] == [
        "diff", "market", "split", "open",
    ]


def test_model_suggestion_is_cached_and_compared_with_the_admin_verdict(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b = listing(conn, "+79000000001"), listing(conn, "+79000000002")
    conn.execute("UPDATE listings SET fragment=? WHERE id=?",
                 ("ЖК Губернский. Двухкомнатная, 54 кв. м. Тел. +7 900 000-00-01", a))
    pid = pair(conn, a, b)
    prompts = []

    def model(system, content, max_tokens):
        prompts.append(content)
        assert dupreview.ask_ai(conn, pid) == {"ok": True, "pending": True}
        assert "+7 900 000-00-01" not in content and "+79000000002" not in content
        return {"verdict": "market", "confidence": 0.93, "why": "Площадь, этаж и дом совпадают, контакты разные."}

    monkeypatch.setattr(llm, "ask_json", model)
    result = dupreview.ask_ai(conn, pid)
    assert result["ok"] and result["ai"]["verdict"] == "market"
    assert dupreview.ask_ai(conn, pid) == result and len(prompts) == 1
    assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == "open"
    assert dupreview.pairs(conn, "open")["pairs"][0]["ai"]["confidence"] == 0.93
    conn.execute("UPDATE listings SET floor=17 WHERE id=?", (b,))
    assert dupreview.pairs(conn, "open")["pairs"][0]["ai"] is None
    assert dupreview.ask_ai(conn, pid)["ok"] and len(prompts) == 2
    assert dupreview.decide(conn, pid, "market", user_id=1)["ok"]
    result = dupreview.pairs(conn, "done")
    assert result["pairs"][0]["ai"]["verdict"] == "market"
    assert result["ai_review"] == {"compared": 1, "agreed": 1}


def test_model_timeout_does_not_resolve_pair_and_can_be_retried(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    pid = pair(conn, listing(conn), listing(conn))

    def failure(*args, **kwargs):
        raise llm.LLMError("timeout")

    monkeypatch.setattr(llm, "ask_json", failure)
    assert "error" in dupreview.ask_ai(conn, pid)
    assert tuple(conn.execute("SELECT status,ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()) == ("open", None)
    monkeypatch.setattr(llm, "ask_json", lambda *a, **kw: {"verdict": "unsure", "why": "Не указан корпус"})
    assert dupreview.ask_ai(conn, pid)["ai"]["verdict"] == "unsure"


def test_repeat_suggestion_keeps_newest_version_even_if_the_old_message_was_forwarded(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old = _price_version(conn, 10, 6900000, last_seen=90)
    fresh = _price_version(conn, 20, 6800000)
    conn.execute("UPDATE listings SET area=52 WHERE id=?", (fresh,))
    pid = pair(conn, old, fresh)
    calls = []

    def model(system, content, max_tokens):
        calls.append(content)
        assert "до 2 м² включительно" in system
        assert json.loads(content)["разница площади в м²"] == 2
        return {"verdict": "repeat", "confidence": 0.94, "why": "Совпали дом, этаж и детали квартиры."}

    monkeypatch.setattr(llm, "ask_json", model)
    ai = dupreview.ask_ai(conn, pid)["ai"]
    assert (ai["keep_id"], ai["keep_version_ts"]) == (fresh, 20)
    saved = {k: v for k, v in ai.items() if k not in ("keep_id", "keep_version_ts")}
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(saved), pid))
    row = dupreview.pairs(conn, "open")["pairs"][0]
    assert row["a"]["id"] == fresh and row["b"]["id"] == old
    assert row["area_in_tolerance"] and row["ai"]["keep_id"] == fresh
    assert dupreview.ask_ai(conn, pid)["ai"] == ai and len(calls) == 1


def test_manual_duplicate_review_accepts_area_difference_of_at_most_two_square_metres(conn):
    a, b = listing(conn), listing(conn)
    conn.execute("UPDATE listings SET area=36.1 WHERE id=?", (a,))
    conn.execute("UPDATE listings SET area=38.1 WHERE id=?", (b,))

    def rows():
        return conn.execute("SELECT * FROM listings WHERE id IN (?,?) ORDER BY id", (a, b)).fetchall()

    left, right = rows()
    assert dupreview.classify(left, right)
    assert "Близкая площадь" in offers.similar_signals(left, right, offers.place_keys(left), offers.place_keys(right))
    conn.execute("UPDATE listings SET area=104 WHERE id=?", (a,))
    conn.execute("UPDATE listings SET area=106.1 WHERE id=?", (b,))
    left, right = rows()
    assert dupreview.classify(left, right) is None
    assert not offers.similar_signals(left, right, offers.place_keys(left), offers.place_keys(right))


def test_repeat_merge_preserves_reactions_and_combines_saved_duplicates(conn, monkeypatch):
    old, keep = listing(conn), listing(conn)
    uid = accounts.upsert_email_user(conn, "saved-duplicates@example.test")["id"]
    client = conn.execute("INSERT INTO clients(user_id,name,created,updated) VALUES(?,'Клиент',1,1)", (uid,)).lastrowid
    collection = conn.execute("""INSERT INTO collections(user_id,client_id,token,title,created,updated)
        VALUES(?,?,'saved-pick','Подборка',1,1)""", (uid, client)).lastrowid
    conn.executemany("INSERT INTO favorites(user_id,listing_id,created) VALUES(?,?,1)", [(uid, old), (uid, keep)])
    conn.executemany("INSERT INTO notes(user_id,listing_id,text,ts) VALUES(?,?,?,1)",
                     [(uid, old, "Старая заметка"), (uid, keep, "Новая заметка")])
    conn.executemany("""INSERT INTO collection_items(collection_id,listing_id,note,price,text,added)
        VALUES(?,?,?,?,?,1)""", [(collection, old, "Первая заметка", 6500000, "Текст для клиента"),
                                (collection, keep, "Вторая заметка", None, None)])
    conn.executemany("INSERT INTO client_items(client_id,listing_id,status,ts) VALUES(?,?,?,1)",
                     [(client, old, "show"), (client, keep, "opened")])
    conn.executemany("""INSERT INTO pick_reactions(collection_id,listing_id,kind,text,device,ts) VALUES(?,?,?,?,?,?)""",
                     [(collection, keep, "like", None, "browser", 1),
                      (collection, old, "want_show", "В субботу", "browser", 2),
                      (collection, old, "question", "Когда можно?", "browser", 3)])
    assert dupreview.decide(conn, pair(conn, old, keep), "repeat", uid, keep_id=keep)["ok"]
    assert [r[0] for r in conn.execute("SELECT listing_id FROM favorites")] == [keep]
    assert tuple(conn.execute("SELECT listing_id,text FROM notes").fetchone()) == (keep, "Новая заметка\n\nСтарая заметка")
    assert [tuple(r) for r in conn.execute("SELECT listing_id,status,reaction,reaction_text FROM client_items")] == [
        (keep, "show", "want_show", "В субботу")]
    assert [r[0] for r in conn.execute("SELECT listing_id FROM pick_reactions ORDER BY id")] == [keep] * 3
    item, = picks.public(conn, "saved-pick", count_view=False, device="browser")["items"]
    assert (item["id"], item["price"], item["description"], item["reaction"]) == (
        keep, 6500000, "Текст для клиента", "want_show")
    assert item["note"] == "Вторая заметка\n\nПервая заметка"
    monkeypatch.setattr(picks, "_mail_agent", lambda *a: None)
    assert picks.react(conn, "saved-pick", old, "like", device="browser")["ok"]
    assert picks.public(conn, "saved-pick", count_view=False, device="browser")["items"][0]["reaction"] == "like"


def test_repair_preserves_old_reaction_and_cleans_a_chain_of_repeats(conn):
    old, middle, keep = (listing(conn) for _ in range(3))
    uid = accounts.upsert_email_user(conn, "old-reactions@example.test")["id"]
    collection = conn.execute("""INSERT INTO collections(user_id,token,title,created,updated)
        VALUES(?,'old-pick','Подборка',1,1)""", (uid,)).lastrowid
    conn.executemany("INSERT INTO collection_items(collection_id,listing_id,added) VALUES(?,?,1)",
                     [(collection, old), (collection, keep)])
    conn.execute("""INSERT INTO pick_reactions(collection_id,listing_id,kind,device,ts)
        VALUES(?,?,'like','browser',1)""", (collection, old))
    conn.executemany("UPDATE listings SET is_active=0,hidden_reason='dup_repeat',merged_into=? WHERE id=?",
                     [(middle, old), (keep, middle)])
    conn.commit()
    assert repeat_refs.repair(conn) == 1
    assert repeat_refs.repair(conn) == 0
    item, = picks.public(conn, "old-pick", count_view=False, device="browser")["items"]
    assert (item["id"], item["reaction"], item["is_active"]) == (keep, "like", 1)


def test_auto_review_applies_confident_verdicts_and_keeps_manual_cases(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def example(house, *, different_agents=False):
        a = listing(conn)
        b = listing(conn, "+79000000002" if different_agents else "+79000000001")
        conn.execute("UPDATE listings SET house=?,first_seen=? WHERE id IN (?,?)", (str(house), house, a, b))
        return a, b, pair(conn, a, b)

    older, fresh, repeat = example(11)
    conn.execute("UPDATE listings SET last_seen=20 WHERE id=?", (fresh,))
    _, _, low = example(12)
    _, _, unsure = example(13)
    cross_left, cross_right, cross = example(14, different_agents=True)
    market_left, market_right, market = example(15, different_agents=True)
    _, _, diff = example(16)
    answers = {"11": ("repeat", 0.8), "12": ("repeat", 0.799), "13": ("unsure", 0.99),
               "14": ("repeat", 0.94), "15": ("market", 0.91), "16": ("diff", 0.85)}
    calls = []

    def model(system, content, max_tokens):
        house = json.loads(content)["объявления"][0]["house"]
        calls.append(house)
        verdict, confidence = answers[house]
        return {"verdict": verdict, "confidence": confidence}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.ask_ai(conn, repeat)["ai"]["confidence"] == 0.8
    assert dupreview.auto_review(conn) == {"applied": 3, "manual": 3, "retry": 0}
    assert calls == ["11", "16", "15", "14", "13", "12"]
    done = dupreview.pairs(conn, "done")
    assert done["ai_auto"] == 3
    assert {row["id"]: row["status"] for row in done["pairs"] if row["ai_applied"]} == {
        repeat: "repeat", market: "market", diff: "diff"}
    assert tuple(conn.execute("SELECT is_active,merged_into FROM listings WHERE id=?", (older,)).fetchone()) == (0, fresh)
    for left, right in ((cross_left, cross_right), (market_left, market_right)):
        assert conn.execute("SELECT SUM(is_active) FROM listings WHERE id IN (?,?)", (left, right)).fetchone()[0] == 2
    manual = {p["id"]: p for p in dupreview.pairs(conn)["pairs"]}
    assert set(manual) == {low, unsure, cross}
    assert manual[cross]["ai"]["manual_reason"]
    assert dupreview.decide(conn, repeat, "reopen", user_id=1)["ok"]
    conn.commit()
    assert dupreview.auto_review(conn) == {}
    assert dupreview.review_ai(conn, repeat)["ai"]["verdict"] == "repeat"
    assert dupreview.pairs(conn)["counts"]["open"] == 4


def test_auto_review_ignores_confident_response_for_changed_listing(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b = listing(conn), listing(conn)
    pid = pair(conn, a, b)

    def model(system, content, max_tokens):
        conn.execute("UPDATE listings SET floor=17 WHERE id=?", (b,))
        conn.commit()
        return {"verdict": "repeat", "confidence": 0.99}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.review_ai(conn, pid)["stale"]
    assert dupreview.pairs(conn)["pairs"][0]["ai"] is None
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 2


def test_backlog_only_reviews_the_initial_queue_and_reuses_previous_results(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def candidate(house, created):
        a, b = listing(conn), listing(conn)
        conn.execute("UPDATE listings SET house=?,first_seen=? WHERE id IN (?,?)", (house, created, a, b))
        return pair(conn, a, b)

    old = candidate("10", 1)
    recent = candidate("20", 2)
    low = candidate("30", 3)
    reopened = candidate("40", 4)
    conn.execute("UPDATE dup_pairs SET user_id=1 WHERE id=?", (reopened,))
    calls, new_pairs = [], []

    def model(system, content, max_tokens):
        house = json.loads(content)["объявления"][0]["house"]
        calls.append(house)
        if house == "20":
            new_pairs.append(candidate("50", 5))
            conn.commit()
        return {"verdict": "diff", "confidence": {"10": 0.79, "20": 0.8, "30": 0.79}[house]}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.ask_ai(conn, low)["ai"]["confidence"] == 0.79
    assert dupreview.review_backlog(conn) == {"total": 2, "applied": 1, "manual": 1, "changed": 0}
    assert calls == ["30", "20", "10"]
    assert dict(conn.execute("SELECT id,status FROM dup_pairs")) == {
        old: "open", recent: "diff", low: "open", reopened: "open", new_pairs[0]: "open"}
    assert conn.execute("SELECT ai FROM dup_pairs WHERE id=?", (new_pairs[0],)).fetchone()[0] is None


def test_backlog_waits_for_worker_and_retries_after_failed_request(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b = listing(conn), listing(conn)
    pid = pair(conn, a, b)
    clock = [1000.0]
    sleeps, calls = [], []
    monkeypatch.setattr(dupreview.time, "time", lambda: clock[0])
    signature = dupreview._ai_signature([dupreview._card(conn, lid) for lid in (a, b)])
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?",
                 (json.dumps({"pending": True, "ts": 1000, "signature": signature}), pid))
    conn.commit()
    assert dupreview.auto_review(conn) == {}

    def wait(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
        if len(sleeps) == 1:
            conn.execute("UPDATE dup_pairs SET ai=NULL WHERE id=?", (pid,))
            conn.commit()

    def model(system, content, max_tokens):
        calls.append(clock[0])
        if len(calls) == 1:
            raise llm.LLMError("timeout")
        return {"verdict": "diff", "confidence": 0.9}

    monkeypatch.setattr(dupreview.time, "sleep", wait)
    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.review_backlog(conn) == {"total": 1, "applied": 1, "manual": 0, "changed": 0}
    assert sleeps == [3] + [60] * 5
    assert calls == [1003, 1303]
    assert float(db.get_state(conn, dupreview._AI_RETRY_AT)) == 1303


def test_backlog_saves_model_response_after_a_concurrent_sqlite_write(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    path = str(tmp_path / "backlog.db")
    with closing(db.connect(path)) as conn, closing(db.connect(path)) as writer:
        conn.execute("PRAGMA busy_timeout=0")
        first = pair(conn, listing(conn), listing(conn))
        second = pair(conn, listing(conn), listing(conn))
        conn.commit()
        calls, delays = [], []

        def model(system, content, max_tokens):
            assert not conn.in_transaction
            calls.append(content)
            if len(calls) == 1:
                writer.execute("BEGIN IMMEDIATE")
                writer.execute("INSERT INTO state(key,value) VALUES ('worker_write','saved')")
            return {"verdict": "diff", "confidence": 0.9}

        def wait(seconds):
            assert not conn.in_transaction
            delays.append(seconds)
            if len(delays) == 3:
                writer.commit()

        monkeypatch.setattr(llm, "ask_json", model)
        monkeypatch.setattr(dupreview.time, "sleep", wait)
        assert dupreview.review_backlog(conn) == {"total": 2, "applied": 2, "manual": 0, "changed": 0}
        assert delays == [2, 2, 2]
        assert len(calls) == 2
        assert dict(conn.execute("SELECT id,status FROM dup_pairs")) == {first: "diff", second: "diff"}
        assert dupreview.pairs(conn, "done")["ai_auto"] == 2
        assert db.get_state(conn, "worker_write") == "saved"


def test_background_retries_applying_a_saved_decision_when_sqlite_is_busy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    path = str(tmp_path / "auto.db")
    with closing(db.connect(path)) as conn, closing(db.connect(path)) as writer:
        conn.execute("PRAGMA busy_timeout=0")
        pid = pair(conn, listing(conn), listing(conn))
        calls = []

        def model(system, content, max_tokens):
            calls.append(content)
            return {"verdict": "repeat", "confidence": 0.8}

        monkeypatch.setattr(llm, "ask_json", model)
        assert dupreview.ask_ai(conn, pid)["ok"]
        writer.execute("BEGIN IMMEDIATE")
        waits = []

        def wait(seconds):
            assert not conn.in_transaction
            waits.append(seconds)
            writer.commit()

        monkeypatch.setattr(dupreview.time, "sleep", wait)
        assert dupreview.auto_review(conn) == {"applied": 1, "manual": 0, "retry": 0}
        assert len(calls) == len(waits) == 1
        assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == "repeat"

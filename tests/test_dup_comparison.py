import json
from contextlib import closing

from app import accounts, config, db, dedupe, dupreview, ingest, llm, offers, parser, phonegroups, picks, repeat_refs, rules, search


def listing(conn, phone="+79000000001"):
    return conn.execute("""INSERT INTO listings (type, deal, rooms, area, floor, street, house,
        title, phones, price, first_seen, last_seen)
        VALUES ('flat','sale',2,54,16,'Домбайская','59','Квартира',?,6800000,1,2)""",
        (json.dumps([phone]),)).lastrowid


def pair(conn, a, b, *, score=0.8, status="open", kind="similar"):
    return conn.execute("""INSERT INTO dup_pairs (a,b,kind,score,status,created_at) VALUES (?,?,?,?,?,1)""",
                        (*sorted((a, b)), kind, score, status)).lastrowid


def test_multilisting_contacts_belong_to_their_own_blocks():
    text = ("Продаётся 2-комнатная квартира 58 м², этаж 7/16.\n"
            "ул. Домбайская 59, ремонт.\nЦена 7 500 000 рублей, звонить +7 900 000-00-01.\n\n"
            "Продаётся 1-комнатная квартира 39 м², этаж 2/10.\n"
            "ул. Восточно-Кругликовская 13, мебель.\nЦена 5 100 000 рублей.")
    _, objects = parser.parse(text, use_llm=False)
    assert [(item.price, item.phones) for item in objects] == [(7500000, ["+79000000001"]), (5100000, [])]
    _, objects = parser.parse(text + "\n\nКонтакты для всех объектов: +7 900 000-00-02", use_llm=False)
    assert objects[1].phones == ["+79000000002"]


def test_ambiguous_price_keeps_both_existing_prices(conn):
    a, b = listing(conn), listing(conn)
    stem = ("Краснодар улица Домбайская двухкомнатная квартира 54 м² этаж 16 "
            "мебель и техника остаются окна во двор цена ")
    conn.execute("UPDATE listings SET fragment=? WHERE id=?", (stem + "6 800 000 руб", a))
    conn.execute("UPDATE listings SET house='61',price=6900000,fragment=? WHERE id=?",
                 (stem + "6 900 000 руб", b))
    assert dupreview.decide(conn, pair(conn, a, b), "diff", 1)["ok"]
    obj = parser.ParsedObject(type="flat", deal="sale", rooms=2, area=54, land=None, floor=16, floors=None,
                              street="Домбайская", house=None, district=None, complex=None, settlement=None,
                              phones=["+79000000001"], price=7000000, fragment=stem + "7 000 000 руб",
                              description=stem + "7 000 000 руб")
    added, action = ingest.save_object(conn, obj, None, 10)
    assert added not in (a, b) and action == "new"
    assert [row["price"] for row in conn.execute("SELECT price FROM listings ORDER BY id")] == [6800000, 6900000, 7000000]


def test_new_evidence_requeues_old_hold_and_reopens_conflicting_group(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b = listing(conn), listing(conn, "+79000000002")
    pid = pair(conn, a, b)
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {"object": "unknown", "confidence": 0.5})
    result = dupreview.review_ai(conn, pid)
    assert result["ai"]["verdict"] == "unsure" and result["status"] == "unconfirmed"
    assert dupreview.open_count(conn) == 0
    assert not dupreview._auto_review_ids(conn, 10)
    conn.execute("UPDATE listings SET last_seen=40 WHERE id=?", (b,))
    assert not dupreview._auto_review_ids(conn, 10)
    conn.execute("UPDATE listings SET photos='[\"/photos/2/aaaaaaaaaaaaaaaa.jpg\"]' WHERE id=?", (b,))
    assert dupreview._auto_review_ids(conn, 10) == [pid]
    conn.execute("UPDATE listings SET fragment='Квартира №105' WHERE id IN (?,?)", (a, b))
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "different", "confidence": 0.98})
    assert dupreview.review_ai(conn, pid)["applied"]
    assert set(offers.groups(conn)) == {a, b}
    conn.execute("UPDATE listings SET floor=14,admin_fixed=1 WHERE id=?", (b,))
    assert not offers.groups(conn)
    assert dupreview.recheck_changed(conn)["reopened"] == 1
    assert dupreview._auto_review_ids(conn, 10) == [pid]


def _confirmed_versions(conn):
    older, newer = listing(conn), listing(conn)
    text = ("2-комнатная квартира 54 м², 16 этаж. Краснодар, ул. Домбайская 59. "
            "Окна выходят во двор, остаётся мебель и техника, документы готовы.\n")
    conn.execute("UPDATE listings SET fragment=? WHERE id=?",
                 (text + "Цена 6 800 000 рублей\nТел. +79000000001", older))
    conn.execute("UPDATE listings SET fragment=?,price=6700000,first_seen=5,last_seen=5 WHERE id=?",
                 (text + "Цена 6 700 000 рублей\nТел. +79000000001", newer))
    return older, newer, pair(conn, older, newer)


def _address_versions(conn, *, building="30/6", unit=350, other_phone="+79000000001"):
    old, fresh = listing(conn), listing(conn, other_phone)
    body = ("ЖК Лучший\nМини-2к.кв.,\n18/19 этаж, S=46 кв.м.\n"
            "Ул. Ростовское шоссе д. {house}, кв. {unit}, сейф по запросу\n"
            "Новый ремонт, мебель, техника\nРазбивка 4900\n")
    first = body.format(house=f"{building}к2", unit=350) + "Цена: 7 400 000"
    second = "‼️Снижение цены‼️\nЦена: 7 300 000\n" + body.format(house=f"{building}, корп. 2", unit=unit)
    conn.execute("""UPDATE listings SET street='Ростовское шоссе',house=?,complex='Лучший',
                    area=46,floor=18,floors=19 WHERE id IN (?,?)""", (building, old, fresh))
    conn.execute("UPDATE listings SET fragment=?,price=7400000,last_seen=50 WHERE id=?", (first, old))
    conn.execute("UPDATE listings SET fragment=?,house=?,price=7300000,first_seen=20,last_seen=20 WHERE id=?",
                 (second, f"{building}к2", fresh))
    return old, fresh, pair(conn, old, fresh, kind="text")


def test_review_extracts_unit_and_corpus_from_original_description(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old, fresh, pid = _address_versions(conn)

    def model(system, payload, max_tokens):
        cards = json.loads(payload)["объявления"]
        assert all(c["номер квартиры в тексте"] == 350 and c["номер дома с корпусом"] == "30/6к2" for c in cards)
        return {"object": "same", "agent": "same", "confidence": 0.97}

    monkeypatch.setattr(llm, "ask_json", model)
    saved = dupreview.review_ai(conn, pid)
    assert saved["applied"] and saved["ai"]["keep_id"] == fresh
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh
    assert conn.execute("SELECT price FROM listings WHERE id=?", (fresh,)).fetchone()[0] == 7300000
    assert rules.extract_street("ул. Ивана Беличенко 86, Корп. 12") == ("Ивана Беличенко", "86к12")
    assert rules.extract_street("Ростовское шоссе д. 30/6, корпус 12") == ("Ростовское", "30/6к12")
    for text in ("кв. 46 м²", "кв. 18/19 этаж", "кв. 36,7 м2", "кв. 18 этаж", "1 к.кв. 54"):
        assert offers.unit_number({"text": text}) is None, text


def test_different_unit_or_corpus_blocks_automatic_merge(conn):
    old, fresh, _ = _address_versions(conn, unit=351)
    cards = [dupreview._card(conn, lid) for lid in (old, fresh)]
    assert offers.identity_conflict(*cards)
    a, b = cards
    b["text"] = b["text"].replace("кв. 351", "кв. 350").replace("корп. 2", "корп. 12")
    b["house"] = "30/6к12"
    assert offers.identity_conflict(a, b)
    assert dupreview._review_guard(conn, cards, {"verdict": "repeat"}) == "Есть расхождение в характеристиках квартиры."
    assert dupreview._review_guard(conn, cards, {"verdict": "diff"}) is None


def test_review_compares_copy_excluding_proven_price_and_price_notice(conn):
    old, fresh, _ = _confirmed_versions(conn)
    a, b = (dupreview._card(conn, lid) for lid in (old, fresh))
    stem = a["text"].split("\n")[0] + "\nСумма в ДКП 5 000 000 ₽\n"
    a["text"] = stem + "Цена 6 800 000 рублей"
    b["text"] = "‼️Цена снижена‼️\nНовая цена: 6 700 000 ₽\n" + stem
    assert dupreview._review_text_matches(a, b)
    incomplete = [dict(card, house=None) for card in (a, b)]
    for card in incomplete:
        card["text"] = card["text"].replace("Домбайская 59.", "Домбайская.")
    assert not dupreview._same_unit_proof(*incomplete, for_repeat=True)
    for card, directory in zip(incomplete, (1, 2)):
        card["photos"] = [f"/photos/{directory}/{letter * 16}.jpg" for letter in ("a", "b")]
    assert dupreview._same_unit_proof(*incomplete, for_repeat=True)
    assert not dupreview._same_unit_proof(*incomplete, for_repeat=False)
    b["text"] = b["text"].replace("5 000 000", "5 100 000")
    assert not dupreview._review_text_matches(a, b)


def _save_unconfirmed_review(conn, pid, verdict="repeat", confidence=0.97):
    ids = conn.execute("SELECT a,b FROM dup_pairs WHERE id=?", (pid,)).fetchone()
    cards = [dupreview._card(conn, lid) for lid in ids]
    ai = {"verdict": verdict, "confidence": confidence, "applied": False,
          "signature": dupreview._ai_signature(cards), "closure_reason": "Нужно подтвердить, что это одна квартира."}
    conn.execute("UPDATE dup_pairs SET status='unconfirmed',decided_at=15,ai=? WHERE id=?", (json.dumps(ai), pid))


def test_recheck_saved_reviews_audits_and_applies_without_model_requests(conn, monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("Saved recommendations require no model requests")

    monkeypatch.setattr(llm, "ask_json", unexpected_call)
    old, fresh, repeat = _address_versions(conn)
    one, two, market = _address_versions(conn, building="31/6", other_phone="+79000000002")
    _, _, mismatch = _address_versions(conn, building="32/6", unit=351)
    _, _, low_confidence = _address_versions(conn, building="33/6")
    _, _, changed = _address_versions(conn, building="34/6")
    _, manually_split, manual = _address_versions(conn, building="35/6")
    assert dupreview.decide(conn, manual, "diff", user_id=1)["ok"]
    for pid, verdict, confidence in ((repeat, "repeat", 0.97), (market, "market", 0.96),
                                     (mismatch, "repeat", 0.99), (low_confidence, "repeat", 0.69),
                                     (changed, "repeat", 0.96)):
        _save_unconfirmed_review(conn, pid, verdict, confidence)
    conn.execute("UPDATE dup_pairs SET ai=json_set(ai,'$.signature','old') WHERE id=?", (changed,))
    conn.commit()
    initial = list(conn.execute("SELECT * FROM dup_pairs ORDER BY id"))
    conn.execute("PRAGMA query_only=ON")
    audit = dupreview.recheck_unconfirmed(conn)
    assert audit["checked"] == 5 and audit["would_apply"] == {"repeat": 1, "market": 1, "linked": 0}
    assert audit["held"] == 2 and audit["changed"] == 1
    assert list(conn.execute("SELECT * FROM dup_pairs ORDER BY id")) == initial
    conn.execute("PRAGMA query_only=OFF")
    result = dupreview.recheck_unconfirmed(conn, apply=True)
    assert result["applied"] == audit["would_apply"] and result["held_by_reason"] == audit["held_by_reason"]
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh
    group = offers.groups(conn)[one]
    assert {row["id"] for row in group} == {one, two}
    assert conn.execute("SELECT status,user_id FROM dup_pairs WHERE id=?", (manual,)).fetchone()[:] == ("diff", 1)
    assert conn.execute("SELECT is_active FROM listings WHERE id=?", (manually_split,)).fetchone()[0]
    assert dupreview.recheck_unconfirmed(conn)["would_apply"] == {"repeat": 0, "market": 0, "linked": 0}


def test_recent_review_uses_decision_dates_and_updates_offer_statuses(conn, monkeypatch):
    now = int(dupreview.time.time())
    since = now - 48 * 3600
    monkeypatch.setattr(dupreview.time, "time", lambda: now)

    def record(house, decided_at, *, status="unconfirmed", verdict="linked", unit=350, confidence=0.97):
        a, b, pid = _address_versions(conn, building=f"{house}/6", unit=unit, other_phone="+79000000002")
        _save_unconfirmed_review(conn, pid, verdict, confidence)
        conn.execute("UPDATE dup_pairs SET status=?,decided_at=?,ai=json_set(ai,'$.applied',?) WHERE id=?",
                     (status, decided_at, status != "unconfirmed", pid))
        return a, b, pid

    _, _, held = record(31, now)
    _, _, linked = record(32, since, status="linked")
    _, _, older = record(33, since - 1)
    _, _, future = record(34, now + 1)
    _, _, manual = record(35, now)
    conn.execute("UPDATE dup_pairs SET user_id=1 WHERE id=?", (manual,))
    _, _, different = record(36, now, status="diff", verdict="diff", unit=351)
    _, _, questionable = record(37, now, status="diff", verdict="diff")
    _, _, weak = record(38, now, confidence=0.69)
    a, b, merged = _confirmed_versions(conn)
    _save_unconfirmed_review(conn, merged)
    conn.execute("UPDATE dup_pairs SET status='open' WHERE id=?", (merged,))
    assert dupreview.decide(conn, merged, "repeat", keep_id=b)["ok"]
    conn.execute("UPDATE dup_pairs SET ai=json_set(ai,'$.applied',1) WHERE id=?", (merged,))
    _, _, stale = record(39, now, status="linked")
    conn.execute("UPDATE dup_pairs SET ai=json_set(ai,'$.signature','old') WHERE id=?", (stale,))
    _, _, market = record(40, now, status="market", verdict="market")
    conn.commit()

    def unexpected(*args, **kwargs):
        raise AssertionError("Rechecking saved decisions requires no model requests")

    monkeypatch.setattr(llm, "ask_json", unexpected)
    previous_pairs = conn.execute("SELECT * FROM dup_pairs ORDER BY id").fetchall()
    previous_listings = conn.execute("SELECT * FROM listings ORDER BY id").fetchall()
    conn.execute("PRAGMA query_only=ON")
    audit = dupreview.recheck_recent(conn, 48)
    assert (audit["since_ts"], audit["until_ts"], audit["checked"]) == (since, now, 8)
    assert audit["previous"] == {"unconfirmed": 2, "repeat": 1, "diff": 2, "market": 1, "linked": 2}
    assert audit["would_apply"] == {"repeat": 0, "diff": 0, "market": 2, "linked": 0}
    assert (audit["held"], audit["unchanged"], audit["already_merged"], audit["needs_review"], audit["stale"]) == (
        1, 2, 1, 1, 1)
    assert conn.execute("SELECT * FROM dup_pairs ORDER BY id").fetchall() == previous_pairs
    conn.execute("PRAGMA query_only=OFF")
    applied = dupreview.recheck_recent(conn, 48, apply=True)
    assert applied["applied"] == audit["would_apply"]
    assert applied["needs_review_by_reason"] == {"Есть подтверждения, что объявления относятся к одной квартире.": 1}
    assert dict(conn.execute("SELECT id,status FROM dup_pairs")) == {
        held: "market", linked: "market", older: "unconfirmed", future: "unconfirmed", manual: "unconfirmed",
        different: "diff", questionable: "diff", weak: "unconfirmed", merged: "repeat", stale: "linked", market: "market",
    }
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (a,)).fetchone()[0] == b
    assert conn.execute("SELECT * FROM listings ORDER BY id").fetchall() == previous_listings
    assert not any(dupreview.recheck_recent(conn, 48)["would_apply"].values())


def test_recent_review_skips_newer_admin_decision_during_replay(conn, monkeypatch):
    now = int(dupreview.time.time())
    a, b, pid = _address_versions(conn, other_phone="+79000000002")
    _save_unconfirmed_review(conn, pid, "market")
    conn.execute("UPDATE dup_pairs SET decided_at=? WHERE id=?", (now, pid))
    conn.commit()
    write = dupreview._write_review

    def with_admin_update(conn, operation, *, wait_for_db):
        conn.execute("UPDATE dup_pairs SET status='diff',user_id=1 WHERE id=?", (pid,))
        conn.commit()
        return write(conn, operation, wait_for_db=wait_for_db)

    monkeypatch.setattr(dupreview, "_write_review", with_admin_update)
    report = dupreview.recheck_recent(conn, 48, apply=True)
    assert report["checked"] == report["stale"] == 1
    assert not any(report["applied"].values())
    assert conn.execute("SELECT status,user_id FROM dup_pairs WHERE id=?", (pid,)).fetchone()[:] == ("diff", 1)
    assert a not in offers.groups(conn) and b not in offers.groups(conn)


def test_fresh_recent_audit_includes_manual_and_merged_pairs_and_resumes(conn, monkeypatch, tmp_path):
    from tools import audit_recent_dups

    now = 2000000
    clock = [now]
    monkeypatch.setattr(audit_recent_dups.time, "time", lambda: clock[0])
    monkeypatch.setattr(audit_recent_dups.time, "sleep", lambda seconds: None)
    conn.execute("INSERT INTO users(id,created) VALUES (1,1)")

    def record(house, when, status, user=0):
        a, b, pid = _address_versions(conn, building=f"{house}/6", other_phone="+79000000002")
        conn.execute("UPDATE dup_pairs SET status=?,decided_at=?,user_id=? WHERE id=?", (status, when, user, pid))
        return a, b, pid

    a, b, merged = record(41, now, "open")
    conn.execute("UPDATE listings SET photos=? WHERE id=?", ('["https://example.test/first.jpg"]', a))
    message = ingest.add_message(conn, source="wa", profile_id="test", chat_id="sale", msg_id="merge-audit",
                                 sender="79000000001@c.us", text="Объявление", ts=now - 50)
    conn.execute("INSERT INTO listing_events(listing_id,message_id,ts) VALUES (?,?,?)", (a, message, now - 50))
    assert dupreview.decide(conn, merged, "repeat", user_id=1, keep_id=b)["ok"]
    _, _, manual = record(42, now - 2, "diff", user=1)
    _, _, held = record(43, now - 48 * 3600, "unconfirmed")
    _, _, old = record(44, now - 48 * 3600 - 1, "market")
    _, _, open_pair = record(45, now, "open")
    gone, _, unavailable = record(46, now - 4, "split", user=1)
    conn.execute("DELETE FROM listings WHERE id=?", (gone,))
    conn.commit()
    conn.execute("PRAGMA query_only=ON")
    prior = conn.execute("SELECT * FROM dup_pairs ORDER BY id").fetchall()
    previous_cards = conn.execute("SELECT * FROM listings ORDER BY id").fetchall()
    calls = []
    disconnected = [True]

    def model(system, content, max_tokens):
        payload = json.loads(content)
        cards = payload["объявления"]
        house = cards[0]["house"]
        calls.append(house)
        assert "сверка" in payload and cards[0]["text"].startswith("1| ")
        if house == "41/6":
            assert not payload["есть общий номер в двух карточках"] and not payload["совпавших фото"]
        if house == "42/6" and disconnected[0]:
            raise llm.LLMError("Временный сбой")
        return {"object": "same", "agent": "different", "confidence": 0.69 if house == "43/6" else 0.98}

    monkeypatch.setattr(llm, "ask_json", model)
    path = tmp_path / "audit.jsonl"
    with path.open("a+", encoding="utf-8") as report:
        first = audit_recent_dups.audit(conn, report, 48)
        report.write('{"id":')
    assert (first["total"], first["reviewed"], first["remaining"], first["unavailable"]) == (4, 2, 1, 1)
    assert calls == ["41/6", "42/6", "42/6", "42/6", "43/6"]
    disconnected[0] = False
    clock[0] += 72 * 3600
    with path.open("a+", encoding="utf-8") as report:
        finished = audit_recent_dups.audit(conn, report, 48)
    assert finished["until_ts"] == now and finished["remaining"] == 0 and finished["reviewed"] == 3
    assert finished["prior_status_by_new_recommendation"] == {
        "repeat": {"market": 1}, "diff": {"market": 1}, "unconfirmed": {"unsure": 1}}
    header, *rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {entry["id"] for entry in header["pairs"]} == {merged, manual, held, unavailable}
    assert all(entry["id"] not in (old, open_pair) for entry in rows)
    assert rows[0]["without_transferred_details"] and rows[0]["manual"]
    assert calls == ["41/6", "42/6", "42/6", "42/6", "43/6", "42/6"]
    assert conn.execute("SELECT * FROM dup_pairs ORDER BY id").fetchall() == prior
    assert conn.execute("SELECT * FROM listings ORDER BY id").fetchall() == previous_cards


def test_market_accepts_detailed_description_and_keeps_each_offer(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old, fresh = listing(conn), listing(conn, "+79000000002")
    first = ("ул. Домбайская 59\n2 комнаты, 16 этаж, площадь 54 м²\n"
             "Панорамные окна кухни выходят на озеро, утеплённая лоджия.\nЦена 6 800 000 ₽")
    second = ("2 комнаты, 16 этаж, площадь 54,6 м²\nул. Домбайская 59\n"
              "Панорамные окна кухни выходят на озеро, утеплённая лоджия.\nЦена 7 100 000 ₽")
    conn.execute("UPDATE listings SET fragment=?,photos=? WHERE id=?",
                 (first, json.dumps(["https://example.test/first.jpg"]), old))
    conn.execute("UPDATE listings SET fragment=?,area=54.6,price=7100000,photos=? WHERE id=?",
                 (second, json.dumps(["https://example.test/second.jpg"]), fresh))
    pid = pair(conn, old, fresh, kind="offer_review")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "different", "confidence": 0.70})
    details = "SELECT id, is_active, price, phones, photos, fragment FROM listings ORDER BY id"
    before = conn.execute(details).fetchall()
    outcome = dupreview.review_ai(conn, pid)
    assert outcome["applied"] and outcome["status"] == "market"
    assert conn.execute(details).fetchall() == before
    result = search.search(conn, search.Query(), 4, False, group_offers=True)
    assert len(result["items"]) == 1
    assert {item["id"]: item["price"] for item in result["items"][0]["offer_group"]["offers"]} == {
        old: 6800000, fresh: 7100000,
    }
    conn.execute("UPDATE dup_pairs SET kind='offer_photo' WHERE id=?", (pid,))
    assert {item["id"] for item in offers.groups(conn)[old]} == {old, fresh}


def test_fresh_review_merges_land_reposts_with_a_new_price(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old, fresh = listing(conn), listing(conn)
    original = ("Сочи, ул. Фабрициуса. Участок 18,6 сотки, ИЖС, собственность. "
                "Небольшой уклон, закрытая территория, асфальтированная подъездная дорога. "
                "Все коммуникации рядом, есть канализация. ГПЗУ на руках, под три коттеджа.")
    conn.execute("""UPDATE listings SET type='land',rooms=NULL,floor=NULL,area=NULL,
                    land=18.6,street='Фабрициуса',settlement='Сочи',house=NULL WHERE id IN (?,?)""", (old, fresh))
    conn.execute("UPDATE listings SET fragment=?,price=40000000,last_seen=50 WHERE id=?",
                 (original + "\nЦена 40 000 000 рублей", old))
    conn.execute("UPDATE listings SET fragment=?,price=35000000,first_seen=20,last_seen=20 WHERE id=?",
                 (original + "\nЦена 35 000 000 рублей", fresh))
    pid = pair(conn, old, fresh, kind="text")
    _save_unconfirmed_review(conn, pid, "repeat", 0.95)
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "same", "confidence": 0.95, "evidence": []})
    result = dupreview.review_ai(conn, pid, refresh=True)
    assert result["applied"] and result["ai"]["keep_id"] == fresh
    assert tuple(conn.execute("SELECT id,price FROM listings WHERE is_active=1").fetchone()) == (fresh, 35000000)
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh


def _cards_with_address_in_the_description(conn, *, second_house=4):
    a, b = listing(conn), listing(conn, "+79000000002")
    first = ("Район парка Краснодар Аверкиева, 4\n3к этаж 7/16 78.0 м²\n"
             "Новый ремонт в ванной и комнате, коридор под ремонт\nЦена 7 800 000 ₽")
    second = (f"Район парка Краснодар Аверкиева, {second_house}\n3к этаж 7/16 78.2 м²\n"
              "В ванной ремонт свежий, коридор требует отделки\nЦена 7 850 000 ₽")
    conn.execute("""UPDATE listings SET street=NULL,house=NULL,area=78,rooms=3,floor=7,floors=16,
                    complex='Восточно-Кругликовский' WHERE id IN (?,?)""", (a, b))
    conn.execute("UPDATE listings SET fragment=?,price=7800000 WHERE id=?", (first, a))
    conn.execute("UPDATE listings SET fragment=?,area=78.2,price=7850000 WHERE id=?", (second, b))
    return a, b, pair(conn, a, b, kind="offer_review")


def test_model_can_prove_a_match_using_both_source_descriptions(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _cards_with_address_in_the_description(conn)
    assert not dupreview._same_offer_property(*(dupreview._card(conn, lid) for lid in (a, b)))

    def answer(system, payload, max_tokens):
        assert '"left_line":7' in system
        cards = json.loads(payload)["объявления"]
        assert all("house" not in card for card in cards)
        assert all(card["text"].startswith("1| Район парка Краснодар Аверкиева, 4\n2| ") for card in cards)
        return {"object": "same", "agent": "different", "confidence": 0.90, "evidence": [
            {"kind": "address", "left_line": 1, "right_line": 1, "left": "Аверкиева, 4", "right": "Аверкиева, 4"},
            {"kind": "condition", "left_line": 3, "right_line": 3},
        ]}

    monkeypatch.setattr(llm, "ask_json", answer)
    result = dupreview.review_ai(conn, pid)
    assert result["applied"] and result["status"] == "market" and len(result["ai"]["evidence"]) == 2
    detail = result["ai"]["evidence"][1]
    assert (detail["left_id"], detail["right_id"], detail["left_line"], detail["right_line"]) == (a, b, 3, 3)
    assert detail["left"] == "Новый ремонт в ванной и комнате, коридор под ремонт"
    assert {item["id"]: item["price"] for item in offers.groups(conn)[a]} == {a: 7800000, b: 7850000}


def test_model_cannot_borrow_address_from_an_alternative_or_ignore_a_source_conflict(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _cards_with_address_in_the_description(conn, second_house=8)
    answer = {"object": "same", "agent": "different", "confidence": 0.99, "evidence": [
        {"kind": "address", "left_line": 1, "right_line": 1, "left": "Аверкиева, 4", "right": "Аверкиева, 4"},
        {"kind": "condition", "left_line": 3, "right_line": 3},
        {"kind": "address", "left_line": 1, "right_line": 2, "left": "Аверкиева, 4", "right": "Аверкиева, 8"},
        {"kind": "condition", "left_line": True, "right_line": 99},
    ]}
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: answer)
    outcome = dupreview.review_ai(conn, pid)
    assert outcome["status"] == "unconfirmed" and len(outcome["ai"]["evidence"]) == 1
    assert dupreview.decide(conn, pid, "reopen")["ok"]
    answer["evidence"][0]["right"] = "Аверкиева, 8"
    outcome = dupreview.review_ai(conn, pid, checked_since=outcome["ai"]["ts"] + 0.000001)
    assert outcome["status"] == "unconfirmed"
    assert outcome["ai"]["closure_reason"] == "Есть расхождение в характеристиках квартиры."
    assert not offers.groups(conn)


def test_numbered_source_citation_cannot_drop_part_of_the_house_number():
    first, second = "Аверкиева, 4/2", "Аверкиева, 4, корпус 3"
    texts = [dupreview._numbered_review_text(text) for text in (first, second)]
    cards = [{"id": 1, "text": first}, {"id": 2, "text": second}]
    for left, right in [("Аверкиева, 4", second), (first, "Аверкиева, 4"), (first, "Аверкиева, 4, корпус")]:
        assert not dupreview._grounded_quotes([{"kind": "address", "left_line": 1, "right_line": 1,
                                               "left": left, "right": right}], cards, texts)
    trimmed = dupreview._numbered_review_text("Описание\n" + "а" * 2982 + "\nАверкиева, 48")
    assert trimmed.startswith("1| Описание\n2| ") and "Аверкиева" not in trimmed


def test_fact_comparison_exposes_source_discrepancies_without_changing_card_data(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _cards_with_address_in_the_description(conn)
    first = "Студия 20,1 м²\nул. Школьная, 55, корпус 2\n18 этаж из 19\nДКП 3 млн\nЦена 4 000 000 ₽"
    second = "Студия 22 м²\n18 этаж из 19\nКвартира № 27\nЦена 4 200 000 ₽"
    conn.execute("UPDATE listings SET street='Школьная',floor=18,floors=19,rooms=0 WHERE id IN (?,?)", (a, b))
    conn.execute("UPDATE listings SET fragment=?,area=21.5,price=4100000,house='55' WHERE id=?", (first, a))
    conn.execute("UPDATE listings SET fragment=?,area=22,price=4200000,house='55 к2' WHERE id=?", (second, b))
    other = listing(conn)
    conn.execute("UPDATE listings SET floor=2,fragment='Текст альтернативы, который модели передавать не нужно' WHERE id=?", (other,))
    pair(conn, a, other)

    def answer(system, content, max_tokens):
        payload = json.loads(content)
        table = payload["сверка"]
        assert table["поля карточек"]["этаж"] == [18, 18, "совпало"]
        assert table["поля карточек"]["комнаты"] == [0, 0, "совпало"]
        assert table["распознано в исходниках"]["площадь, м²"] == [20.1, 22, "разница 1.9 м², в пределах допуска"]
        assert table["распознано в исходниках"]["номер квартиры"] == [None, 27, "только B"]
        assert table["расхождения [в карточке, в исходнике]"] == {
            "A": {"дом и корпус": ["55", "55к2"], "площадь, м²": [21.5, 20.1], "цена, ₽": [4100000, 4000000]},
        }
        assert payload["альтернатива"]["id"] == other
        assert payload["альтернатива"]["расхождения [в объявлении, в альтернативе]"]["A"]["этаж"] == [18, 2]
        assert "Текст альтернативы" not in content
        return {"object": "unknown", "agent": "unknown", "confidence": 0.5, "evidence": [
            {"kind": "area", "left_line": 1, "right_line": 1},
        ]}

    monkeypatch.setattr(llm, "ask_json", answer)
    outcome = dupreview.ask_ai(conn, pid)
    assert outcome["ai"]["evidence"][0]["left"] == "Студия 20,1 м²"
    assert conn.execute("SELECT area,price,house FROM listings WHERE id=?", (a,)).fetchone()[:] == (21.5, 4100000, "55")


def test_review_keeps_verified_legacy_quotes_for_saved_responses(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _cards_with_address_in_the_description(conn)
    cards = [dupreview._card(conn, lid) for lid in (a, b)]
    ai = {"verdict": "market", "object": "same", "agent": "different", "confidence": 0.95,
          "signature": dupreview._ai_signature(cards), "evidence_version": 1, "evidence": [
              {"kind": "address", "left": "Аверкиева, 4", "right": "Аверкиева, 4"},
              {"kind": "condition", "left": "ремонт в ванной и комнате, коридор под ремонт",
               "right": "В ванной ремонт свежий, коридор требует отделки"},
          ]}
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(ai), pid))

    def unexpected_call(*args, **kwargs):
        raise AssertionError("Используем сохранённый ответ.")

    monkeypatch.setattr(llm, "ask_json", unexpected_call)
    assert dupreview.review_ai(conn, pid)["status"] == "market"


def test_refresh_backlog_reconsiders_closed_pairs_and_resumes_without_new_calls(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    _, _, linked = _address_versions(conn, building="35/6", other_phone="+79000000002")
    _save_unconfirmed_review(conn, linked, "unsure", 0.50)
    old, fresh, separate = _address_versions(conn, building="36/6", other_phone="+79000000002")
    conn.execute("UPDATE listings SET fragment='',house='36/6к2' WHERE id IN (?,?)", (old, fresh))
    _, _, manual = _address_versions(conn, building="37/6", other_phone="+79000000002")
    assert dupreview.decide(conn, manual, "diff", user_id=1)["ok"]
    inactive, _, gone = _address_versions(conn, building="38/6", other_phone="+79000000002")
    _save_unconfirmed_review(conn, gone, "market")
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (inactive,))
    request_count = []

    def answer(system, payload, max_tokens):
        description = json.loads(payload)["объявления"][0]
        request_count.append(description["house"])
        return {"object": "same" if description["house"] == "35/6" else "different", "agent": "different",
                "confidence": 0.97, "evidence": []}

    monkeypatch.setattr(llm, "ask_json", answer)
    result = dupreview.review_backlog(conn, refresh=True)
    assert (result["total"], result["applied"], result["unconfirmed"]) == (2, 1, 1)
    assert result["by_decision"] == {"repeat": 0, "diff": 0, "market": 1, "linked": 0}
    assert result["held_by_reason"] == {"DeepSeek не подтвердил конкретное расхождение между объектами.": 1}
    assert dict(conn.execute("SELECT id,status FROM dup_pairs")) == {
        linked: "market", separate: "unconfirmed", manual: "diff", gone: "unconfirmed",
    }
    assert dupreview.review_backlog(conn, refresh=True)["already_checked"] == 1
    assert request_count == ["35/6", "36/6к2"]


def test_refresh_returns_saved_decision_after_a_model_timeout(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    _, _, pid = _address_versions(conn, other_phone="+79000000002")
    _save_unconfirmed_review(conn, pid, "market")
    previous = conn.execute("SELECT * FROM dup_pairs WHERE id=?", (pid,)).fetchone()

    def timeout(*args, **kwargs):
        raise llm.LLMError("timeout")

    monkeypatch.setattr(llm, "ask_json", timeout)
    assert dupreview.review_ai(conn, pid, refresh=True)["retryable"]
    assert conn.execute("SELECT * FROM dup_pairs WHERE id=?", (pid,)).fetchone() == previous


def test_confident_market_with_generic_template_stays_separate(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    first, second = listing(conn), listing(conn, "+79000000002")
    generic = ("улица Домбайская 59\nДвухкомнатная квартира, 16 этаж, площадь 54 м².\n"
               "Хороший ремонт. Мебель, техника. Вся сумма в ДКП. Цена 6 800 000 ₽")
    conn.execute("UPDATE listings SET fragment=? WHERE id IN (?,?)", (generic, first, second))
    pid = pair(conn, first, second, kind="offer_review")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "different", "confidence": 0.99})
    outcome = dupreview.review_ai(conn, pid)
    assert outcome["ai"]["verdict"] == "market" and outcome["status"] == "unconfirmed"
    assert not offers.groups(conn)


def test_saved_offer_review_uses_details_with_partial_address_and_current_contacts(conn, monkeypatch):
    first, second = listing(conn), listing(conn, "+79000000002")
    shared = ("ЖК Акварели 2\n2-комнатная\n19 этаж\n50,6 по выписке + балкон\nРемонт\n"
              "Кухня с техникой: холодильник, посудомоечная машина, сплиты.")
    conn.execute("""UPDATE listings SET complex='Акварели 2',area=50.6,floor=19,street=NULL,house=NULL
                    WHERE id=?""", (first,))
    conn.execute("""UPDATE listings SET complex='Акварели 2',area=50.6,floor=19,street='Тепличная',house='62/1к5'
                    WHERE id=?""", (second,))
    conn.execute("UPDATE listings SET fragment=?,price=7150000 WHERE id=?", (shared + "\nЦена 7 150 000 ₽", first))
    conn.execute("UPDATE listings SET fragment=?,price=7100000 WHERE id=?",
                 ("ул. Тепличная 62/1, корпус 5, кв. 501\n" + shared + "\nЦена 7 100 000 ₽", second))
    pid = pair(conn, first, second, kind="offer_review")
    _save_unconfirmed_review(conn, pid, "linked", 0.90)
    conn.commit()

    def unexpected(*args, **kwargs):
        raise AssertionError("Saved recommendations require no model requests")

    monkeypatch.setattr(llm, "ask_json", unexpected)
    audit = dupreview.recheck_unconfirmed(conn)
    assert audit["would_apply"] == {"repeat": 0, "market": 1, "linked": 0}
    assert dupreview.recheck_unconfirmed(conn, apply=True)["applied"] == audit["would_apply"]
    saved = dupreview.pairs(conn, "done")["pairs"][0]
    assert saved["status"] == "market" and saved["ai"]["model_verdict"] == "linked" and saved["ai"]["agent"] == "different"


def test_market_distinguishes_unit_numbers_formatted_with_unicode_spaces(conn):
    first, second = listing(conn), listing(conn, "+79000000002")
    text = "ул. Домбайская 59, кв."
    conn.execute("UPDATE listings SET fragment=? WHERE id=?", (text + "\u00a011", first))
    conn.execute("UPDATE listings SET fragment=? WHERE id=?", (text + "\u202f11", second))
    cards = [dupreview._card(conn, lid) for lid in (first, second)]
    assert [offers.unit_number(c) for c in cards] == [11, 11]
    ai = {"verdict": "market", "confidence": 0.97}
    assert dupreview._review_reason(conn, cards, ai) is None
    cards[1]["text"] = text + "\u202f12"
    assert dupreview._review_reason(conn, cards, ai) == "Есть расхождение в характеристиках квартиры."


def test_exact_copy_sent_from_two_numbers_can_keep_unknown_agent(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    first, second = listing(conn), listing(conn, "+79000000002")
    before = "ул. Домбайская 59, кв. 11\nУютная квартира с окнами на озеро."
    for lid, phone in ((first, "+79000000001"), (second, "+79000000002")):
        conn.execute("UPDATE listings SET fragment=?,phone_roles=? WHERE id=?",
                     (before, json.dumps({phone: "sender"}), lid))
    pid = pair(conn, first, second, kind="offer_review")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "unknown", "confidence": 0.97})
    result = dupreview.review_ai(conn, pid)
    assert result["applied"] and result["status"] == "linked" and result["ai"]["agent"] == "unknown"
    assert {r["id"] for r in offers.groups(conn)[first]} == {first, second}


def test_recheck_saved_review_preserves_an_admin_decision_on_another_version(conn):
    old, fresh, pid = _address_versions(conn)
    archived = listing(conn)
    earlier = pair(conn, archived, fresh)
    assert dupreview.decide(conn, earlier, "diff", user_id=1)["ok"]
    conn.execute("UPDATE listings SET is_active=0,hidden_reason=?,merged_into=? WHERE id=?",
                 (offers.MERGED_REPEAT, old, archived))
    _save_unconfirmed_review(conn, pid)
    report = dupreview.recheck_unconfirmed(conn, apply=True)
    assert report["applied"]["repeat"] == 0
    assert report["held_by_reason"] == {"Связь противоречит решению по другой версии объявления.": 1}
    assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == "unconfirmed"


def test_recheck_rolls_back_reopening_if_photos_cannot_be_moved(conn, monkeypatch):
    old, fresh, pid = _address_versions(conn)
    _save_unconfirmed_review(conn, pid)
    state = conn.execute("SELECT * FROM dup_pairs WHERE id=?", (pid,)).fetchone()

    def unavailable(*args):
        raise OSError("photo storage unavailable")

    monkeypatch.setattr(dupreview, "_merge_photos", unavailable)
    report = dupreview.recheck_unconfirmed(conn, apply=True)
    assert report["held_by_reason"] == {"Не удалось перенести фотографии.": 1}
    assert conn.execute("SELECT * FROM dup_pairs WHERE id=?", (pid,)).fetchone() == state
    assert conn.execute("SELECT SUM(is_active) FROM listings WHERE id IN (?,?)", (old, fresh)).fetchone()[0] == 2


def test_safe_automatic_repeat_undo_restores_refs_and_keeps_later_reactions(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "same", "confidence": 0.95})
    old, fresh, pid = _confirmed_versions(conn)
    conn.execute("INSERT INTO users(id,created) VALUES (1,1)")
    conn.execute("INSERT INTO collections (id,user_id,token,title,created,updated) VALUES (1,1,'restore','Подборка',1,1)")
    conn.execute("INSERT INTO collection_items (collection_id,listing_id,added) VALUES (1,?,1)", (old,))
    conn.execute("INSERT INTO favorites (user_id,listing_id,created) VALUES (1,?,1),(1,?,2)", (old, fresh))
    conn.execute("INSERT INTO pick_reactions (collection_id,listing_id,kind,ts) VALUES (1,?,'like',1)", (old,))
    conn.execute("INSERT INTO notes (user_id,listing_id,text,ts) VALUES (1,?,'old',1),(1,?,'new',2)", (old, fresh))
    assert dupreview.review_ai(conn, pid)["applied"]
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh
    assert {row["price"] for row in search.listing_detail(conn, fresh, False)["price_history"]} == {6700000, 6800000}
    assert conn.execute("SELECT listing_id FROM pick_reactions").fetchone()[0] == fresh
    conn.execute("INSERT INTO pick_reactions (collection_id,listing_id,kind,ts) VALUES (1,?,'question',3)", (fresh,))
    conn.execute("UPDATE notes SET text='updated',ts=3 WHERE listing_id=?", (fresh,))
    assert dupreview.decide(conn, pid, "reopen", user_id=1)["ok"]
    assert [tuple(row) for row in conn.execute("SELECT listing_id,created FROM favorites ORDER BY listing_id")] == [
        (old, 1), (fresh, 2)]
    assert [tuple(row) for row in conn.execute("SELECT listing_id,text FROM notes ORDER BY listing_id")] == [
        (old, "old"), (fresh, "updated")]
    assert [row[0] for row in conn.execute("SELECT listing_id FROM pick_reactions ORDER BY id")] == [old, old]
    assert conn.execute("SELECT listing_id FROM collection_items").fetchone()[0] == old
    assert not dupreview._auto_review_ids(conn, 10)


def test_unconfirmed_pair_checks_new_chat_sender_only_once(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old, fresh, pid = _confirmed_versions(conn)
    conn.execute("UPDATE listings SET fragment=replace(fragment,?,?),phones=? WHERE id=?",
                 ("+79000000001", "+79000000002", '["+79000000002"]', fresh))
    calls = []

    def model(system, content, max_tokens):
        calls.append(json.loads(content)["номер объявления совпал с отправителем другой карточки"])
        return {"object": "same", "agent": "same", "confidence": 0.97}

    def post(number, timestamp):
        msg = ingest.add_message(conn, source="wa", profile_id="test", chat_id="sale", msg_id=str(timestamp),
                                 sender=number, text="Повтор", ts=timestamp)
        conn.execute("INSERT INTO listing_events(listing_id,message_id,ts) VALUES (?,?,?)", (fresh, msg, timestamp))

    monkeypatch.setattr(llm, "ask_json", model)
    post("79000000002@c.us", 5)
    assert dupreview.review_ai(conn, pid)["status"] == "unconfirmed"
    post("79000000002@c.us", 6)
    assert dupreview.auto_review(conn) == {} and calls == [False]
    post("79000000001@c.us", 7)
    assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 0, "retry": 0}
    assert calls == [False, True]
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh


def test_detach_offer_from_photo_group_keeps_other_offers_and_all_listing_data(conn):
    a, b, c = [listing(conn, f"+7900000000{i}") for i in (1, 2, 3)]
    for lid in (a, b, c):
        photos = [f"/photos/{lid}/{name}.jpg" for name in ("aa" * 8, "bb" * 8)]
        conn.execute("UPDATE listings SET photos=?,price=? WHERE id=?", (json.dumps(photos), 6800000 + lid * 25000, lid))
    conn.execute("INSERT INTO users(id,created) VALUES (1,1)")
    assert set(offers.groups(conn)) == {a, b, c}
    query = "SELECT id,is_active,price,phones,photos FROM listings ORDER BY id"
    before = conn.execute(query).fetchall()
    assert dupreview.detach_offer(conn, b, user_id=1)["listing_id"] == b
    assert set(offers.groups(conn)) == {a, c}
    assert conn.execute(query).fetchall() == before
    assert [tuple(row) for row in conn.execute("SELECT a,b,status,kind,user_id FROM dup_pairs ORDER BY a,b")] == [
        (a, b, "split", "manual_unlink", 1), (b, c, "split", "manual_unlink", 1)]
    assert dupreview.detach_offer(conn, b, user_id=1)["stale"]
    assert not dupreview._auto_review_ids(conn, 10)


def test_card_undo_restores_latest_merge_and_blocks_relinking(conn, monkeypatch):
    monkeypatch.setattr(dupreview.time, "time", lambda: 12345)
    old, fresh, last_pid = _confirmed_versions(conn)
    other = listing(conn, "+79000000002")
    earlier_pid = pair(conn, other, fresh)
    conn.execute("INSERT INTO users(id,created) VALUES (1,1)")
    conn.execute("INSERT INTO favorites(user_id,listing_id,created) VALUES (1,?,1)", (old,))
    for lid in (old, fresh, other):
        conn.execute("UPDATE listings SET photos=? WHERE id=?", (json.dumps([f"https://example.test/{lid}.jpg"]), lid))

    assert dupreview.decide(conn, earlier_pid, "repeat", user_id=1, keep_id=fresh)["ok"]
    assert dupreview.decide(conn, last_pid, "repeat", user_id=1, keep_id=fresh)["ok"]
    assert dupreview.latest_merge(conn, fresh)["id"] == last_pid
    assert dupreview.undo_card_merge(conn, fresh, earlier_pid, user_id=1)["stale"]
    assert dupreview.undo_card_merge(conn, old, last_pid, user_id=1)["stale"]

    result = dupreview.undo_card_merge(conn, fresh, last_pid, user_id=1)
    assert result == {"ok": True, "status": "split", "listing_id": old}
    assert dupreview.latest_merge(conn, fresh)["id"] == earlier_pid
    assert conn.execute("SELECT listing_id FROM favorites").fetchone()[0] == old
    assert dupreview._card(conn, old)["is_active"]
    assert dupreview._card(conn, fresh)["photos"] == [f"https://example.test/{fresh}.jpg", f"https://example.test/{other}.jpg"]
    assert conn.execute("SELECT status,kind,user_id FROM dup_pairs WHERE id=?", (last_pid,)).fetchone()[:] == (
        "split", "manual_unlink", 1)
    assert dupreview._excluded_family_join(conn, [old], [fresh], repeat=True)
    assert not dupreview._auto_review_ids(conn, 10)
    assert dupreview.decide(conn, last_pid, "reopen", user_id=1)["ok"]
    assert dupreview.pairs(conn)["pairs"][0]["manual_override"]


def test_read_only_audit_estimates_backlog_without_writing(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    _, _, pid = _confirmed_versions(conn)
    a, b = listing(conn), listing(conn, "+79000000002")
    weak = pair(conn, a, b, kind="offer_review")
    c, d = listing(conn), listing(conn, "+79000000002")
    conn.execute("UPDATE listings SET house='61' WHERE id IN (?,?)", (c, d))
    unknown = pair(conn, c, d, kind="offer_review")

    def answer(system, payload, max_tokens):
        evidence = json.loads(payload)
        if evidence["объявления"][0]["house"] == "61":
            return {"object": "same", "agent": "unknown", "confidence": 0.72, "why": "Нужно проверить агентство."}
        shared = evidence["совпали номера прямо в текстах"]
        return {"object": "same", "agent": "same" if shared else "different", "confidence": 0.99}

    monkeypatch.setattr(llm, "ask_json", answer)
    conn.commit()
    conn.execute("PRAGMA query_only=ON")
    report = dupreview.audit_backlog(conn, 10)
    assert report["would_apply"] == {"repeat": 1, "diff": 0, "market": 0, "linked": 0} and report["held"] == 2
    assert report["model_decisions"] == {"repeat": 1, "diff": 0, "market": 1, "linked": 1, "unsure": 0}
    assert report["held_by_reason"]["Нужно подтвердить квартиру по её номеру, фотографиям или совпадающему подробному описанию."] == 1
    assert {item["id"] for item in report["pairs"]} == {pid, weak, unknown}
    assert next(item for item in report["pairs"] if item["id"] == unknown)["model_reason"] == "Нужно проверить агентство."
    assert conn.execute("SELECT COUNT(*) FROM dup_pairs WHERE status='open' AND ai IS NULL").fetchone()[0] == 3
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 6


def _offers_with_an_unknown_contact(conn, *, detail="Предчистовая отделка. Вид на Догма-Парк"):
    a, b = listing(conn), listing(conn)
    first = ("● Ивана Беличенко 103 ЖК Самолет 5\n▸2 к.кв.     14/16 МК\nПлощадь: 51/28/11м\n"
             "Цена: 6 450 000₽\nПредчистовая отделка. Вид на Догма-Парк")
    second = ("Район Западный Обход. Самолет\n● Ивана Беличенко 103\n▸2 к.кв.     14/16 МК ЖК Самолет 5\n"
              f"Площадь: 51/28/11м\nЦена: 6.450.000\n{detail}")
    conn.execute("""UPDATE listings SET area=51,floor=14,floors=16,street='Ивана Беличенко',house='103',
                    district='Западный обход',complex='Самолет 5',price=6450000 WHERE id IN (?,?)""", (a, b))
    conn.execute("UPDATE listings SET fragment=?,photos=? WHERE id=?",
                 (first, json.dumps(["https://example.test/known.jpg"]), a))
    conn.execute("UPDATE listings SET fragment=?,phones='[]',last_seen=30,photos=? WHERE id=?",
                 (second, json.dumps(["https://example.test/unknown.jpg"]), b))
    return a, b, pair(conn, a, b, kind="offer_review")


def test_partial_model_answer_links_known_property_and_revisits_legacy_unsure(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _offers_with_an_unknown_contact(conn)
    details = "SELECT id, is_active, price, phones, photos FROM listings ORDER BY id"
    before = [tuple(r) for r in conn.execute(details)]
    old = {"verdict": "unsure", "confidence": 0.95, "why": "Совпадает квартира. Агент неизвестен.",
           "signature": dupreview._ai_signature([dupreview._card(conn, lid) for lid in (a, b)])}
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(old), pid))
    calls = []

    def model(system, content, max_tokens):
        payload = json.loads(content)
        assert payload["сервис подтвердил совпадение квартиры по проверяемым признакам"]
        assert payload["контакты карточек"] == "неясно"
        calls.append(1)
        return {"object": "same", "agent": "unknown", "confidence": 0.95}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.review_backlog(conn) == {"total": 1, "applied": 1, "unconfirmed": 0, "changed": 0}
    assert [tuple(r) for r in conn.execute(details)] == before and calls == [1]
    history = dupreview.pairs(conn, "done")
    item, = history["pairs"]
    assert item["status"] == item["ai"]["verdict"] == "linked" and item["ai_applied"]
    assert (item["ai"]["object"], item["ai"]["agent"], history["ai_auto"]) == ("same", "unknown", 1)
    assert history["counts"]["open"] == 0 and history["counts"]["done"] == 1
    assert history["stats"][0]["linked"] == 1
    summary = offers.summary(offers.groups(conn)[a])
    assert [offer["id"] for offer in summary["offers"]] == [a, b]
    assert summary["offers"][1]["contact_unknown"] is True

    conn.execute("UPDATE listings SET floor=15 WHERE id=?", (b,))
    assert not offers.groups(conn)
    assert conn.execute("SELECT status,ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()[:] == ("open", None)


def test_unknown_agent_does_not_link_similar_flats_or_override_manual_decisions(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _offers_with_an_unknown_contact(conn, detail="С ремонтом. Вид во двор.")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "unknown", "confidence": 0.99})
    result = dupreview.review_ai(conn, pid)
    assert not result["applied"] and result["ai"]["closure_reason"]
    assert dupreview._auto_review_ids(conn, -1) == []
    assert not offers.groups(conn)

    conn.execute("UPDATE listings SET fragment=replace(fragment,'С ремонтом. Вид во двор.',"
                 "'Предчистовая отделка. Вид на Догма-Парк') WHERE id=?", (b,))
    assert dupreview.review_ai(conn, pid)["applied"]
    assert dupreview.decide(conn, pid, "reopen", user_id=1)["ok"]
    assert not offers.groups(conn) and not dupreview.review_ai(conn, pid).get("applied")
    assert dupreview.open_count(conn) == 1


def test_review_trusts_verified_contacts_and_checks_conflicting_property_evidence(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _offers_with_an_unknown_contact(conn)
    answer = {"object": "different", "agent": "different", "confidence": 0.98}
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: answer)
    result = dupreview.review_ai(conn, pid)
    assert not result["applied"] and result["ai"]["closure_reason"].startswith("Есть подтверждения")
    assert result["status"] == "unconfirmed" and dupreview.open_count(conn) == 0

    conn.execute("UPDATE listings SET photos='[]' WHERE id=?", (b,))
    answer["object"] = "same"
    result = dupreview.review_ai(conn, pid)
    assert result["applied"] and result["ai"]["verdict"] == "linked" and result["ai"]["agent"] == "unknown"
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE id IN (?,?) AND is_active=1", (a, b)).fetchone()[0] == 2


def test_repeat_accepts_same_number_when_sender_is_the_saved_contact(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "same", "confidence": 0.95})
    old, fresh, pid = _confirmed_versions(conn)
    fragment = conn.execute("SELECT fragment FROM listings WHERE id=?", (fresh,)).fetchone()[0]
    conn.execute("UPDATE listings SET fragment=?,phone_roles=? WHERE id=?",
                 (fragment.rsplit("\n", 1)[0], json.dumps({"+79000000001": "sender"}), fresh))
    result, = dupreview.inspect_review_pairs(conn, [pid])
    assert result["text_matches"] and result["confirmed_contact"] and result["property_evidence"]
    assert not result["shared_number_in_both_texts"] and result["sender_used_as_contact"] == [False, True]
    assert result["hold_if_repeat"] is None and dupreview.review_ai(conn, pid)["applied"]
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh


def test_price_only_repeat_with_context_contact_reuses_saved_model_verdict(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    declared, sender, forwarder = "+79000000051", "+79000000052", "+79000000053"
    phonegroups.save(conn, f"{declared}, {sender}")
    old, fresh = listing(conn, declared), listing(conn, sender)
    body = ("● Артюшкова 27 ЖК Московский\n▸2 к.кв.    16/16 Блок\n"
            "Площадь: 56/19/14м\nЦена: {}₽\nСтарый ремонт. Вся мебель и техника")
    conn.execute("""UPDATE listings SET street='Артюшкова',house='27',complex='Московский',
                    area=56,floors=16 WHERE id IN (?,?)""", (old, fresh))
    conn.execute("UPDATE listings SET fragment=?,price=6900000,phone_roles=?,last_seen=50 WHERE id=?",
                 (body.format("6 900 000"), json.dumps({declared: "text", forwarder: "sender", sender: "sender"}), old))
    conn.execute("UPDATE listings SET fragment=?,phone_roles=?,first_seen=25,last_seen=25 WHERE id=?",
                 (body.format("6 800 000"), json.dumps({sender: "sender"}), fresh))
    pid = pair(conn, old, fresh, kind="text")
    calls = []

    def model(system, content, max_tokens):
        calls.append(1)
        return {"verdict": "repeat", "confidence": 0.97}

    monkeypatch.setattr(llm, "ask_json", model)
    ai = dupreview.ask_ai(conn, pid)["ai"]
    assert ai["keep_id"] == fresh
    ai["manual_reason"] = "Нужно подтвердить общий контакт агента."
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(ai), pid))

    check, = dupreview.inspect_review_pairs(conn, [pid])
    assert check["text_matches"] and check["confirmed_contact"] and check["property_evidence"]
    assert not check["shared_number_in_both_texts"] and check["hold_if_repeat"] is None
    assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 0, "retry": 0}
    assert len(calls) == 1
    survivor = conn.execute("SELECT id,price,phones FROM listings WHERE is_active=1").fetchone()
    assert tuple(survivor) == (fresh, 6800000, json.dumps([sender]))
    decision, = dupreview.pairs(conn, "done")["pairs"]
    assert decision["merged"] and decision["ai_applied"]


def test_contact_hold_is_revisited_once_when_all_numbers_differ(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    declared, sender, other_sender = "+79000000054", "+79000000055", "+79000000056"
    old, fresh, pid = _confirmed_versions(conn)
    for lid, phone, sending_number in ((old, declared, other_sender), (fresh, sender, sender)):
        text = conn.execute("SELECT fragment FROM listings WHERE id=?", (lid,)).fetchone()[0]
        conn.execute("UPDATE listings SET phones=?,phone_roles=?,fragment=? WHERE id=?",
                     (json.dumps([phone]), json.dumps({phone: "text", sending_number: "sender"}),
                      text.rsplit("\n", 1)[0], lid))
    calls = []

    def model(system, content, max_tokens):
        calls.append(1)
        return {"verdict": "repeat", "confidence": 0.97}

    monkeypatch.setattr(llm, "ask_json", model)
    ai = dupreview.ask_ai(conn, pid)["ai"]
    ai["manual_reason"] = "Нужно подтвердить общий контакт агента."
    ai["contact_review_version"] = 1
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(ai), pid))
    assert dupreview.auto_review(conn) == {"applied": 0, "unconfirmed": 1, "retry": 0}
    assert dupreview.auto_review(conn) == {}
    assert len(calls) == 1 and dupreview.open_count(conn) == 0
    assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == "unconfirmed"


def test_repeat_accepts_same_chat_sender_with_different_advertised_numbers(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def model(system, content, max_tokens):
        payload = json.loads(content)
        assert payload["один отправитель"] and payload["есть общий номер в двух карточках"]
        assert payload["контакты карточек"] == "совпадают"
        assert not payload["совпали номера прямо в текстах"]
        assert not payload["номер объявления совпал с отправителем другой карточки"]
        assert "79000000003" not in content
        return {"object": "same", "agent": "different", "confidence": 0.99}

    monkeypatch.setattr(llm, "ask_json", model)
    old, fresh, pid = _confirmed_versions(conn)
    for lid, contact in ((old, "+79000000001"), (fresh, "+79000000002")):
        conn.execute("""UPDATE listings SET phones=?,phone_roles=?,fragment=replace(fragment,?,?) WHERE id=?""",
                     (json.dumps([contact]), json.dumps({contact: "text"}), "+79000000001", contact, lid))
        message = ingest.add_message(conn, source="wa", profile_id="test", chat_id="sale", msg_id=str(lid),
                                     sender="79000000003@c.us", text="Повтор объявления", ts=lid)
        conn.execute("INSERT INTO listing_events(listing_id,message_id,ts) VALUES (?,?,?)", (lid, message, lid))
    result, = dupreview.inspect_review_pairs(conn, [pid])
    assert result["text_matches"] and result["confirmed_contact"] and result["property_evidence"]
    assert result["shared_number_in_review"] and result["hold_if_repeat"] is None
    decision = dupreview.review_ai(conn, pid)
    assert decision["applied"] and decision["ai"]["agent"] == "same"
    survivor = conn.execute("SELECT id,price,phones FROM listings WHERE is_active=1").fetchone()
    assert tuple(survivor) == (fresh, 6700000, json.dumps(["+79000000002"]))


def test_advertised_number_can_match_sender_on_either_card(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    calls = []

    def model(system, content, max_tokens):
        payload = json.loads(content)
        assert payload["контакты карточек"] == "совпадают"
        assert payload["номер объявления совпал с отправителем другой карточки"]
        assert not payload["один отправитель"] and not payload["совпали номера прямо в текстах"]
        calls.append(payload)
        return {"object": "same", "agent": "unknown", "confidence": 0.96}

    monkeypatch.setattr(llm, "ask_json", model)
    for sender_on_fresh in (True, False):
        old, fresh, pid = _confirmed_versions(conn)
        sender_card, advertised_card = (fresh, old) if sender_on_fresh else (old, fresh)
        conn.execute("UPDATE listings SET fragment=replace(fragment,?,?) WHERE id=?",
                     ("+79000000001", "8 (900) 000-00-01", advertised_card))
        conn.execute("UPDATE listings SET fragment=replace(fragment,?,?),phones=?,phone_roles=? WHERE id=?",
                     ("+79000000001", "+79000000007", '["+79000000007"]',
                      '{"+79000000007":"text","+79000000001":"sender"}', sender_card))
        assert dupreview.review_ai(conn, pid)["applied"]
        kept = conn.execute("SELECT price,phones FROM listings WHERE id=? AND is_active=1", (fresh,)).fetchone()
        assert tuple(kept) == (6700000, '["+79000000007"]' if sender_on_fresh else '["+79000000001"]')
        assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh
    assert len(calls) == 2


def test_backlog_reuses_saved_review_for_a_newly_recognized_contact(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def model(*args, **kwargs):
        raise AssertionError("Готовую оценку квартиры можно применить без нового запроса.")

    monkeypatch.setattr(llm, "ask_json", model)
    old, fresh, pid = _confirmed_versions(conn)
    conn.execute("UPDATE listings SET fragment=replace(fragment,?,?),phones=?,phone_roles=? WHERE id=?",
                 ("+79000000001", "+79000000009", '["+79000000009"]',
                  '{"+79000000009":"text","+79000000001":"sender"}', fresh))
    cards = [dupreview._card(conn, lid) for lid in (old, fresh)]
    saved = {"verdict": "market", "object": "same", "agent": "different", "confidence": 0.95,
             "manual_reason": "Нужны подтверждения квартиры и самостоятельных предложений обоих агентов.",
             "signature": dupreview._ai_signature(cards), "contact_review_version": 1}
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(saved), pid))
    assert dupreview.review_backlog(conn) == {"total": 1, "applied": 1, "unconfirmed": 0, "changed": 0}
    decided, = dupreview.pairs(conn, "done")["pairs"]
    assert decided["ai_applied"] and decided["ai"]["verdict"] == "repeat" and decided["keep"] == fresh
    assert decided["ai"]["agent"] == "same" and dupreview.auto_review(conn) == {}


def test_shared_sender_does_not_override_different_apartments(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "same", "confidence": 0.99})
    old, fresh, pid = _confirmed_versions(conn)
    conn.execute("UPDATE listings SET fragment=replace(fragment,?,?),phones=?,phone_roles=?,floor=17 WHERE id=?",
                 ("+79000000001", "+79000000009", '["+79000000009"]',
                  '{"+79000000009":"text","+79000000001":"sender"}', fresh))
    result = dupreview.review_ai(conn, pid)
    assert not result["applied"] and result["ai"]["closure_reason"].startswith("Есть расхождение")
    assert dupreview.open_count(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE id IN (?,?) AND is_active=1", (old, fresh)).fetchone()[0] == 2


def test_repeat_checks_recorded_contact_and_omitted_unit_details(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {
        "object": "same", "agent": "same", "confidence": 0.95})
    phone = "+79000000001"
    old, fresh = listing(conn, phone), listing(conn, phone)
    pid = pair(conn, old, fresh)
    start = "Двухкомнатная квартира\nДомбайская 59\nПлощадь 54 м²\nЭтаж 16\n"
    body = "Хороший ремонт\nМебель и техника остаются\nВходная дверь заменена\nБалкон с ремонтом\nЦена 6 800 000 рублей"
    for lid, fragment in ((old, start + "Подъезд 3\nКвартира 106\n" + body), (fresh, start + body)):
        conn.execute("UPDATE listings SET fragment=?,phone_roles=?,first_seen=? WHERE id=?",
                     (fragment, json.dumps({phone: "text"}), lid, lid))
    first, = dupreview.inspect_review_pairs(conn, [pid])
    assert first["text_matches"] and first["property_evidence"] and first["confirmed_contact"]
    assert first["recorded_contact_matches_sender"] == [False, False]
    for lid in (old, fresh):
        message_id = ingest.add_message(conn, source="wa", profile_id="test", chat_id="property",
                                        msg_id=str(lid), sender=phone, text=start + body, ts=lid)
        conn.execute("INSERT INTO listing_events(listing_id,message_id,ts) VALUES (?,?,?)", (lid, message_id, lid))
    second, = dupreview.inspect_review_pairs(conn, [pid])
    assert second["recorded_contact_matches_sender"] == [True, True]
    assert second["confirmed_contact"] and second["hold_if_repeat"] is None

    other = listing(conn, phone)
    conn.execute("UPDATE listings SET fragment=? WHERE id=?",
                 (start + "Подъезд 3\nКвартира 107\n" + body, other))
    pair(conn, fresh, other)
    different = pair(conn, old, other)
    blocked, distinct = dupreview.inspect_review_pairs(conn, [pid, different])
    assert blocked["hold_if_repeat"] == "Есть другая карточка этого агента, которая тоже подходит к сообщению."
    assert distinct["hold_if_repeat"] == "Есть расхождение в характеристиках квартиры."
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (other,))
    cached = dupreview.ask_ai(conn, pid)["ai"]
    cached["manual_reason"] = "Нужно подтвердить, что это одна квартира."
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(cached), pid))
    assert pid in dupreview._auto_review_ids(conn, -1)
    conn.execute("DELETE FROM state WHERE key='dup_repeat_details_v1'")
    db._migrate(conn)
    assert pid in dupreview._auto_review_ids(conn, -1)

    def unexpected_call(*args, **kwargs):
        raise AssertionError("The cached recommendation should be reused")

    monkeypatch.setattr(llm, "ask_json", unexpected_call)
    assert dupreview.review_ai(conn, pid)["applied"]
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (old,)).fetchone()[0] == fresh



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


def test_auto_price_leaves_pairs_waiting_for_manual_merge(conn):
    old = _price_version(conn, 10, 6900000)
    fresh = _price_version(conn, 20, 6800000)
    pid = pair(conn, old, fresh, kind="price_revision")
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps({"verdict": "repeat", "confidence": 0.99}), pid))
    assert dupreview.scan_price_revisions(conn, apply=True)["merged"] == 0
    assert tuple(conn.execute("SELECT status,drop_id FROM dup_pairs WHERE id=?", (pid,)).fetchone()) == ("open", None)
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 2


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


def test_auto_review_finishes_every_pair_and_keeps_weak_matches_separate(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def example(house, *, different_agents=False):
        a = listing(conn)
        b = listing(conn, "+79000000002" if different_agents else "+79000000001")
        conn.execute("UPDATE listings SET house=?,first_seen=? WHERE id IN (?,?)", (str(house), house, a, b))
        return a, b, pair(conn, a, b)

    older, fresh, repeat = _confirmed_versions(conn)
    _, _, low = example(12)
    _, _, unsure = example(13)
    cross_left, cross_right, cross = example(14, different_agents=True)
    market_left, market_right, market = example(15, different_agents=True)
    conn.execute("UPDATE listings SET fragment='Квартира №15' WHERE id IN (?,?)", (market_left, market_right))
    _, _, diff = example(16)
    answers = {"59": ("repeat", 0.98), "12": ("diff", 0.699), "13": ("unsure", 0.99),
               "14": ("repeat", 0.94), "15": ("market", 0.70), "16": ("diff", 0.70)}
    calls = []

    def model(system, content, max_tokens):
        house = json.loads(content)["объявления"][0]["house"]
        calls.append(house)
        verdict, confidence = answers[house]
        return {"verdict": verdict, "confidence": confidence}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.ask_ai(conn, repeat)["ai"]["confidence"] == 0.98
    assert dupreview.auto_review(conn) == {"applied": 3, "unconfirmed": 3, "retry": 0}
    assert calls == ["59", "16", "15", "14", "13", "12"]
    done = dupreview.pairs(conn, "done")
    assert done["ai_auto"] == done["ai_unconfirmed"] == 3 and done["counts"]["open"] == 0
    assert {row["id"]: row["status"] for row in done["pairs"] if row["ai_applied"]} == {
        market: "market", diff: "diff", repeat: "repeat"}
    assert {row["id"] for row in done["pairs"] if row["status"] == "unconfirmed"} == {low, unsure, cross}
    assert all(row["ai"]["closure_reason"] for row in done["pairs"] if row["status"] == "unconfirmed")
    for left, right in ((cross_left, cross_right), (market_left, market_right)):
        assert conn.execute("SELECT SUM(is_active) FROM listings WHERE id IN (?,?)", (left, right)).fetchone()[0] == 2
    assert tuple(conn.execute("SELECT is_active,merged_into FROM listings WHERE id=?", (older,)).fetchone()) == (0, fresh)
    assert dupreview.decide(conn, repeat, "reopen", user_id=1)["ok"]
    conn.commit()
    assert dupreview.auto_review(conn) == {}
    assert dupreview.review_ai(conn, repeat)["ai"]["verdict"] == "repeat"
    pending = dupreview.pairs(conn)
    assert pending["counts"]["open"] == 1 and pending["pairs"][0]["manual_override"]


def test_auto_review_reuses_legacy_recommendation_when_advertised_number_is_now_the_sender(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    main, alternate, forwarder = "+79000000011", "+79000000012", "+79000000013"
    old, fresh = listing(conn, main), listing(conn, alternate)
    text = "Яблоновка\nул. Гагарина 131\n1 комнатная квартира\n32,8 м², этаж 4 из 5\nРемонт, мебель, техника"
    conn.execute("""UPDATE listings SET rooms=1,area=32.8,floor=4,floors=5,street='Гагарина',
                    house='131',price=3100000,fragment=? WHERE id IN (?,?)""", (text, old, fresh))
    conn.execute("UPDATE listings SET phones=?,phone_roles=?,district='ФМР',last_seen=50 WHERE id=?",
                 (json.dumps([main, alternate]), json.dumps({main: "text", alternate: "text", forwarder: "sender"}), old))
    conn.execute("""UPDATE listings SET phone_roles=?,district='Яблоновский',first_seen=20,last_seen=21
                    WHERE id=?""", (json.dumps({main: "sender", alternate: "sender"}), fresh))
    pid = pair(conn, old, fresh, kind="agent")
    calls = []

    def model(system, content, max_tokens):
        payload = json.loads(content)
        calls.append(payload)
        assert payload["контакты карточек"] == "совпадают"
        assert payload["один отправитель"] is False
        return {"verdict": "repeat", "confidence": 0.98}

    monkeypatch.setattr(llm, "ask_json", model)
    saved = dupreview.ask_ai(conn, pid)["ai"]
    saved["manual_reason"] = "Проверьте контакты и принадлежность объявлений одному агенту."
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(saved), pid))
    assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 0, "retry": 0}
    assert len(calls) == 1
    result, = dupreview.pairs(conn, "done")["pairs"]
    assert (result["status"], result["ai_applied"], result["keep"]) == ("repeat", True, fresh)
    survivor = conn.execute("SELECT id,district,phones FROM listings WHERE is_active=1").fetchone()
    assert tuple(survivor) == (fresh, "Яблоновский", json.dumps([alternate]))


def test_auto_review_leaves_overlapping_contacts_with_different_texts_separate(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    shared, left, right, forwarder = (f"+7900000002{i}" for i in range(1, 5))
    a, b = listing(conn, left), listing(conn, right)
    for lid, number in ((a, left), (b, right)):
        conn.execute("UPDATE listings SET phones=?,phone_roles=? WHERE id=?",
                     (json.dumps([shared, number]), json.dumps({forwarder: "sender"}), lid))
    conn.execute("UPDATE listings SET fragment='Домбайская 59, 54 м². В квартире сделан ремонт.' WHERE id=?", (a,))
    conn.execute("UPDATE listings SET fragment='Домбайская 59, 54 м². Квартира без ремонта.' WHERE id=?", (b,))
    pid = pair(conn, a, b, kind="agent")
    calls = []

    def model(system, content, max_tokens):
        payload = json.loads(content)
        calls.append(payload)
        assert payload["контакты карточек"] == "совпадают"
        assert payload["один отправитель"]
        return {"verdict": "repeat", "confidence": 0.98}

    monkeypatch.setattr(llm, "ask_json", model)
    saved = dupreview.ask_ai(conn, pid)["ai"]
    saved["manual_reason"] = "Уточните, принадлежат ли контактные номера одному агенту."
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(saved), pid))
    assert dupreview.auto_review(conn) == {"applied": 0, "unconfirmed": 1, "retry": 0}
    assert len(calls) == 1
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 2
    result, = dupreview.pairs(conn, "done")["pairs"]
    assert result["ai"]["verdict"] == "repeat" and result["status"] == "unconfirmed"


def test_auto_review_recommends_new_price_with_shared_contact_and_matching_text(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    old_extra, new_extra, shared = "+79000000031", "+79000000032", "+79000000033"
    old, fresh = listing(conn, old_extra), listing(conn, new_extra)
    body = ("Краснодар, ул. Домбайская 59. Двухкомнатная квартира 54 м² на шестнадцатом этаже. "
            "Окна выходят во двор, остаются мебель и техника.")
    versions = ((old, old_extra, 6800000, "6 800 000", 1), (fresh, new_extra, 6750000, "6 750 000", 20))
    for lid, extra, price, caption, seen in versions:
        fragment = f"{body}\nЦена {caption} руб.\nТел. {shared}\nWhatsApp: {extra}"
        conn.execute("UPDATE listings SET phones=?,fragment=?,price=?,first_seen=?,last_seen=? WHERE id=?",
                     (json.dumps([shared, extra]), fragment, price, seen, seen, lid))
    pid = pair(conn, old, fresh, kind="agent")
    calls = []

    def model(system, content, max_tokens):
        calls.append(json.loads(content)["контакты карточек"])
        return {"verdict": "repeat", "confidence": 0.98}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 0, "retry": 0}
    assert dupreview.auto_review(conn) == {}
    assert calls == ["совпадают"]
    survivor = conn.execute("SELECT id,price,phones FROM listings WHERE is_active=1").fetchone()
    assert tuple(survivor) == (fresh, 6750000, json.dumps([shared, new_extra]))
    result, = dupreview.pairs(conn, "done")["pairs"]
    assert result["merged"] and result["ai_applied"] and result["keep"] == fresh


def test_auto_review_merges_confirmed_versions_instead_of_splitting_one_agency(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _confirmed_versions(conn)
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kw: {"verdict": "market", "confidence": 0.95})
    decision = dupreview.review_ai(conn, pid)
    assert decision["applied"] and decision["ai"]["verdict"] == "repeat"
    assert decision["ai"]["model_verdict"] == "market"
    assert decision["ai"]["keep_id"] == b
    assert tuple(conn.execute("SELECT status,keep_id,drop_id FROM dup_pairs").fetchone()) == ("repeat", b, a)
    assert dupreview.auto_review(conn) == {}


def test_auto_review_still_requires_common_contact_for_verbatim_reposts(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    proposals = []

    def model(system, content, max_tokens):
        payload = json.loads(content)
        assert payload["контакты карточек"] == "различаются"
        proposals.append(payload["совпал весь исходный текст, номера взяты только у отправителей"])
        return {"verdict": "repeat", "confidence": 0.98}

    monkeypatch.setattr(llm, "ask_json", model)
    for case in ("same", "emoji", "declared", "verified"):
        left, right = (f"+790000000{51 + 2 * len(proposals) + offset:02d}" for offset in (0, 1))
        a, b = listing(conn, left), listing(conn, right)
        text = "🏠 Домбайская 59. Двухкомнатная квартира, 54 м², 16 этаж. Мебель остаётся. 🌿"
        conn.execute("UPDATE listings SET fragment=?,phone_roles=? WHERE id=?",
                     (text, json.dumps({left: "sender"}), a))
        conn.execute("UPDATE listings SET fragment=?,phone_roles=? WHERE id=?",
                     (text.replace("🌿", "🌳") if case == "emoji" else text,
                      json.dumps({right: "text" if case == "declared" else "sender"}), b))
        if case == "verified":
            for i, phone in enumerate((left, right)):
                uid = accounts.upsert_email_user(conn, f"verified-ad-{i}@example.test")["id"]
                conn.execute("INSERT INTO agent_phones(phone,user_id,verified_at) VALUES(?,?,1)", (phone, uid))
        pid = pair(conn, a, b)
        if case == "same":
            ai = dupreview.ask_ai(conn, pid)["ai"]
            ai["manual_reason"] = "Уточните, принадлежат ли контактные номера одному агенту."
            conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(ai), pid))
        result = dupreview.review_ai(conn, pid)
        assert not result.get("applied") and result["ai"]["verdict"] == "repeat"
        assert result["status"] == "unconfirmed"
    assert proposals == [True, False, False, False]


def test_auto_review_ignores_confident_response_for_changed_listing(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b = listing(conn), listing(conn)
    pid = pair(conn, a, b)

    def model(system, content, max_tokens):
        conn.execute("UPDATE listings SET floor=17 WHERE id=?", (b,))
        conn.commit()
        return {"verdict": "diff", "confidence": 0.99}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.review_ai(conn, pid)["stale"]
    assert dupreview.pairs(conn)["pairs"][0]["ai"] is None
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 2


def test_auto_review_retries_answer_without_a_verdict(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {"why": "Только описание."})
    pid = _confirmed_versions(conn)[2]
    assert dupreview.review_ai(conn, pid)["retryable"]
    assert conn.execute("SELECT status,ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()[:] == ("open", None)
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {"object": "same", "agent": "same", "confidence": 0.98})
    assert dupreview.review_ai(conn, pid)["applied"]


def test_auto_review_keeps_photos_and_proceeds_when_merge_fails(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    a, b, pid = _confirmed_versions(conn)
    conn.execute("UPDATE listings SET photos='[\"/photos/1/aaaaaaaaaaaaaaaa.jpg\"]' WHERE id=?", (a,))
    x, y = listing(conn), listing(conn)
    conn.execute("UPDATE listings SET house='61' WHERE id IN (?,?)", (x, y))
    other = pair(conn, x, y)

    def model(system, content, max_tokens):
        verdict = "repeat" if json.loads(content)["объявления"][0]["house"] == "59" else "diff"
        return {"verdict": verdict, "confidence": 0.95}

    def unavailable(*args):
        raise OSError("photo storage unavailable")

    monkeypatch.setattr(llm, "ask_json", model)
    monkeypatch.setattr(dupreview, "_merge_photos", unavailable)
    assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 1, "retry": 0}
    results = {p["id"]: p for p in dupreview.pairs(conn, "done")["pairs"]}
    assert results[pid]["status"] == "unconfirmed" and "фотографии" in results[pid]["ai"]["closure_reason"]
    assert results[other]["status"] == "diff"
    assert conn.execute("SELECT SUM(is_active) FROM listings").fetchone()[0] == 4
    assert dupreview._card(conn, a)["photos"] and not dupreview._card(conn, b)["photos"]


def test_backlog_only_reviews_the_initial_queue_and_reuses_previous_results(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")

    def candidate(house, created):
        a, b = listing(conn), listing(conn)
        conn.execute("UPDATE listings SET house=?,first_seen=? WHERE id IN (?,?)", (house, created, a, b))
        return pair(conn, a, b)

    old = candidate("10", 1)
    recent = candidate("20", 2)
    cached = candidate("30", 3)
    reopened = candidate("40", 4)
    conn.execute("UPDATE dup_pairs SET user_id=1 WHERE id=?", (reopened,))
    calls, new_pairs = [], []

    def model(system, content, max_tokens):
        house = json.loads(content)["объявления"][0]["house"]
        calls.append(house)
        if house == "20":
            new_pairs.append(candidate("50", 5))
            conn.commit()
        return {"verdict": "diff", "confidence": {"10": 0.699, "20": 0.70, "30": 0.79}[house]}

    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.ask_ai(conn, cached)["ai"]["confidence"] == 0.79
    assert dupreview.review_backlog(conn) == {"total": 3, "applied": 2, "unconfirmed": 1, "changed": 0}
    assert calls == ["30", "20", "10"]
    assert dict(conn.execute("SELECT id,status FROM dup_pairs")) == {
        old: "unconfirmed", recent: "diff", cached: "diff", reopened: "open", new_pairs[0]: "open"}
    assert conn.execute("SELECT ai FROM dup_pairs WHERE id=?", (new_pairs[0],)).fetchone()[0] is None


def test_recheck_open_refreshes_held_pairs_and_respects_current_evidence(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    clock = [1000.25]
    monkeypatch.setattr(dupreview.time, "time", lambda: clock[0])

    def candidate(house, first_seen=1):
        a, b = listing(conn), listing(conn, "+79000000002")
        conn.execute("UPDATE listings SET house=?,first_seen=? WHERE id IN (?,?)", (house, first_seen, a, b))
        return a, b, pair(conn, a, b)

    older, fresh, repeat = _confirmed_versions(conn)
    _, _, weak = candidate("61")
    _, _, manual = candidate("63")
    inactive, _, archived = candidate("65")
    _, _, decided = candidate("67")
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (inactive,))
    conn.execute("UPDATE dup_pairs SET status='diff' WHERE id=?", (decided,))
    conn.execute("INSERT INTO users(id,created) VALUES (1,1)")
    conn.execute("UPDATE dup_pairs SET user_id=1 WHERE id=?", (manual,))

    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {"verdict": "unsure", "confidence": 0.5})
    for pid in (repeat, weak, manual):
        assert dupreview.ask_ai(conn, pid)["ai"]["verdict"] == "unsure"
    assert dupreview._auto_review_ids(conn, 10) == [repeat, weak]
    _, original = conn.execute("SELECT id,ai FROM dup_pairs WHERE id=?", (repeat,)).fetchone()
    original = json.loads(original)
    original.update(verdict="repeat", confidence=0.97, manual_reason="Нужно подтвердить, что это одна квартира.")
    conn.execute("UPDATE dup_pairs SET ai=? WHERE id=?", (json.dumps(original), repeat))
    conn.commit()

    calls, arrived = [], []

    def model(system, content, max_tokens):
        house = json.loads(content)["объявления"][0]["house"]
        calls.append(house)
        if house == "59":
            arrived.append(candidate("69", first_seen=10)[2])
            conn.commit()
            return {"verdict": "repeat", "confidence": 0.95}
        if house == "61":
            return {"verdict": "market", "confidence": 0.98}
        return {"verdict": "diff", "confidence": 0.99}

    clock[0] += 0.5
    monkeypatch.setattr(llm, "ask_json", model)
    assert dupreview.review_backlog(conn, recheck=True) == {"total": 2, "applied": 1, "unconfirmed": 1, "changed": 0}
    assert calls == ["59", "61"]
    statuses = dict(conn.execute("SELECT id,status FROM dup_pairs"))
    assert statuses == {repeat: "repeat", weak: "unconfirmed", manual: "open", archived: "open",
                        decided: "diff", arrived[0]: "open"}
    assert conn.execute("SELECT merged_into FROM listings WHERE id=?", (older,)).fetchone()[0] == fresh
    ai = dict(conn.execute("SELECT id,ai FROM dup_pairs"))
    assert json.loads(ai[weak])["closure_reason"]
    assert json.loads(ai[manual])["verdict"] == "unsure"
    assert ai[arrived[0]] is None


def test_recheck_retry_preserves_the_previous_recommendation(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    clock = [1000.25]
    monkeypatch.setattr(dupreview.time, "time", lambda: clock[0])
    pid = pair(conn, listing(conn), listing(conn))
    monkeypatch.setattr(llm, "ask_json", lambda *args, **kwargs: {"verdict": "unsure", "confidence": 0.5})
    previous = dupreview.ask_ai(conn, pid)["ai"]
    stored = conn.execute("SELECT ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0]
    clock[0] += 1

    def unavailable(*args, **kwargs):
        lease = json.loads(conn.execute("SELECT ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0])
        assert lease["pending"] and lease["previous_ai"] == stored
        raise llm.LLMError("timeout")

    monkeypatch.setattr(llm, "ask_json", unavailable)
    assert dupreview.ask_ai(conn, pid, checked_since=clock[0])["retryable"]
    assert conn.execute("SELECT ai FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == stored
    assert dupreview.ask_ai(conn, pid)["ai"] == previous

    calls = []

    def answer(*args, **kwargs):
        calls.append(1)
        return {"verdict": "diff", "confidence": 0.8}

    monkeypatch.setattr(llm, "ask_json", answer)
    result = dupreview.ask_ai(conn, pid, checked_since=clock[0])
    assert result["ai"]["verdict"] == "diff"
    assert dupreview.ask_ai(conn, pid, checked_since=clock[0]) == result
    assert len(calls) == 1


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
    assert dupreview.review_backlog(conn) == {"total": 1, "applied": 1, "unconfirmed": 0, "changed": 0}
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
        assert dupreview.review_backlog(conn) == {"total": 2, "applied": 2, "unconfirmed": 0, "changed": 0}
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
            return {"verdict": "diff", "confidence": 0.70}

        monkeypatch.setattr(llm, "ask_json", model)
        assert dupreview.ask_ai(conn, pid)["ok"]
        writer.execute("BEGIN IMMEDIATE")
        waits = []

        def wait(seconds):
            assert not conn.in_transaction
            waits.append(seconds)
            writer.commit()

        monkeypatch.setattr(dupreview.time, "sleep", wait)
        assert dupreview.auto_review(conn) == {"applied": 1, "unconfirmed": 0, "retry": 0}
        assert len(calls) == len(waits) == 1
        assert conn.execute("SELECT status FROM dup_pairs WHERE id=?", (pid,)).fetchone()[0] == "diff"

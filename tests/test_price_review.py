import json
import time

from app import accounts, agent, brain, hooks, ingest, llm, p0fix, pricefix, search


def doubtful(conn, text, price):
    message_id = ingest.add_message(conn, source="wa", sender="+79001112233", text=text)
    result = ingest.process_message(conn, message_id, use_llm=False)
    lid = result["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET price=?,price_doubt=1 WHERE id=?", (price, lid))
    conn.commit()
    return lid


def test_admin_confirms_existing_amount_and_teaches_the_format(conn, monkeypatch):
    text = "Продам 2-к квартиру 52 м². ЖК Мозаика, ул. Тургенева 50.\nЦена 5400"
    lid = doubtful(conn, text, 5_400_000)
    conn.execute("UPDATE listings SET parse_dispute=? WHERE id=?",
                 (json.dumps({"price": {"ai": 5400}, "floor": {"rules": 3}}), lid))
    assert brain.counts(conn)["price"] == 1
    assert hooks.annotate(conn, search.search(conn, search.Query(), int(time.time()), False)["items"])[0]["price_doubt"]
    assert not search.search(conn, search.Query(price_max=6_000_000), int(time.time()), False)["items"]
    result = ingest.set_price(conn, lid, 5_400_000, user_id=42)
    assert result["price_doubt"] is False and result["learned"] == 1 and result["learned_notation"]
    row = conn.execute("SELECT price,price_doubt,price_fixed,parse_dispute FROM listings WHERE id=?", (lid,)).fetchone()
    assert tuple(row)[:3] == (5_400_000, None, 1)
    assert json.loads(row["parse_dispute"]) == {"floor": {"rules": 3}}
    assert brain.counts(conn)["price"] == 0
    assert search.search(conn, search.Query(price_max=6_000_000), int(time.time()), False)["items"][0]["id"] == lid
    assert pricefix.learned_price(conn, "Цена 5600", "sale", "flat", 52) == 5_600_000
    assert pricefix.learned_price(conn, "Цена 5600 ₽", "sale", "flat", 52) is None
    assert pricefix.learned_price(conn, "Цена 5600\nЦена 5700", "sale", "flat", 52) is None
    assert pricefix.learned_price(conn, "Цена 5600", "rent", "flat", 52) is None

    def model(text, *, price_hints):
        assert "тысячи рублей" in price_hints[0] and "[телефон]" in text
        return {"kind": "listing", "objects": [{"lines": [1, 2], "type": "flat", "deal": "sale",
                                               "rooms": 2, "area_m2": 51, "price_rub": 5600}]}

    monkeypatch.setattr(llm, "parse_message", model)
    new_post = ingest.add_message(conn, source="tg", text="2-к квартира 51 м², ул. Тургенева 80, +79001113344\nЦена 5600")
    parsed = ingest.process_message(conn, new_post, use_llm=True)
    new_id = parsed["results"][0]["listing_id"]
    assert conn.execute("SELECT price FROM listings WHERE id=?", (new_id,)).fetchone()[0] == 5_600_000
    assert not pricefix.learn_notation(conn, "Цена 5900", "sale", 5_900)
    assert not pricefix.model_hints(conn, "Цена 5600")


def test_agents_correct_price_without_teaching_global_rules(conn):
    lid = doubtful(conn, "2-к квартира 52 м², ул. Тургенева 50.\nЦена 5400", 5400)
    user = accounts.upsert_email_user(conn, "test-price@example.test")
    conn.execute("UPDATE users SET paid_until=? WHERE id=?", (int(time.time()) + 86400, user["id"]))
    conn.execute("UPDATE listings SET owner_user_id=? WHERE id=?", (user["id"], lid))
    user = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    assert agent.edit_listing(conn, user, lid, {"price": "5,6"})[0]
    assert tuple(conn.execute("SELECT price,price_doubt FROM listings WHERE id=?", (lid,)).fetchone()) == (5_600_000, None)
    assert conn.execute("SELECT COUNT(*) FROM learned_rules WHERE kind IN ('frag_price','price_notation')").fetchone()[0] == 0


def test_text_price_mismatch_enters_queue_and_reopens_when_amount_changes(conn):
    lid = doubtful(conn, "Продам 2-к квартиру 52 м². ЖК Мозаика, ул. Тургенева 50.\nЦена 5 700 000 ₽", 5_400_000)
    archived = doubtful(conn, "Продам дом 110 м², ул. Красная 30.\nЦена 8 500 000 ₽", 8_000_000)
    conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (archived,))
    assert brain.counts(conn)["price"] == 1  # Объект с двумя причинами учитывается один раз.
    conn.execute("UPDATE listings SET price_doubt=NULL, district=NULL, complex=NULL, street=NULL, settlement=NULL WHERE id=?", (lid,))
    conn.commit()

    assert brain.counts(conn)["price"] == 1
    examples = p0fix.prices_report(conn)
    assert examples["mismatch"] == 1
    assert examples["examples"][0]["id"] == lid
    assert examples["examples"][0]["text"] == 5_700_000
    assert p0fix.price_gap(5_000_000, "Цена 5 150 000 ₽", "sale") is None

    assert ingest.set_price(conn, lid, 5_400_000, user_id=42)["ok"]
    assert brain.counts(conn)["price"] == p0fix.prices_report(conn)["mismatch"] == 0
    conn.execute("UPDATE listings SET description=? WHERE id=?", ("Цена 5 900 000 ₽", lid))
    conn.commit()
    assert brain.counts(conn)["price"] == p0fix.prices_report(conn)["mismatch"] == 1
    assert p0fix.prices_report(conn)["examples"][0]["text"] == 5_900_000

    assert ingest.set_price(conn, lid, 5_900_000, user_id=42)["ok"]
    assert brain.counts(conn)["price"] == p0fix.prices_report(conn)["mismatch"] == 0

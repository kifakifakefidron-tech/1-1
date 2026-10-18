"""Запросы покупателей: разбор пожеланий, сохранение из чата, сведение с объектами."""
from app import buyers


def test_parse_request():
    p = buyers.parse_request("Куплю 1-2к квартиру в ФМР или ЮМР до 6,5 млн, срочно! 89181112233")
    assert p["rooms_mask"] == 0b110 and p["price_max"] == 6_500_000 and p["districts"] == ["ФМР", "ЮМР"]
    assert p["phones"] == ["+79181112233"] and p["deal"] == "sale" and p["type"] == "flat"
    assert buyers.parse_request("Ищу студию или 1к в ЖК Мозаика до 5 млн")["rooms_mask"] == 0b11
    r = buyers.parse_request("Сниму 1к до 30 тыс")
    assert r["deal"] == "rent" and r["price_max"] == 30_000
    assert buyers.parse_request("Запрос: 3к от 8 до 12 млн")["price_min"] == 8_000_000


def test_request_saved_and_matched(conn, add):
    lid = add("2-к квартира 58 м², 7/16 эт., ФМР, ЖК Мозаика. 6,2 млн руб. 89181112233")["results"][0]["listing_id"]
    r = add("Куплю 2к в ФМР до 6,5 млн, наличка. 89184445566")
    assert r["kind"] == "request"
    assert add("Куплю 2к в ФМР до 6,5 млн, наличка. 89184445566")["status"] == "repost"
    row = conn.execute("SELECT * FROM buyer_requests").fetchone()
    assert row["seen_count"] == 2
    o = dict(conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone())
    found = buyers.for_listing(conn, o, with_contacts=False)
    assert len(found) == 1 and "phones" not in found[0] and "4445566" not in found[0]["text"]
    assert buyers.for_listing(conn, {**o, "price": 9_000_000}, False) == []   # дороже бюджета
    feed = buyers.feed(conn, True)
    assert feed["total"] == 1 and feed["items"][0]["phones"] == ["+79184445566"]


def _user(conn, email):
    return conn.execute("INSERT INTO users (email, created) VALUES (?, 0)", (email,)).lastrowid


def test_agent_publishes_request_and_owner_notified(conn, add):
    """19.10: агент публикует запрос клиента → подтверждённый агент с подходящим объектом получает «под ваш объект есть покупатель»."""
    from app import clients
    lid = add("2-к квартира 58 м², 7/16 эт., ФМР, ЖК Мозаика. 6,2 млн руб. 89181112233")["results"][0]["listing_id"]
    owner, buyer_agent = _user(conn, "own@x.ru"), _user(conn, "agent@x.ru")
    conn.execute("INSERT INTO agent_phones (phone, user_id, verified_at) VALUES ('+79181112233', ?, 0)", (owner,))
    cid = clients.save(conn, buyer_agent, {"name": "Анна Иванова", "phone": "+79990001122", "kind": "buyer",
                                           "rooms": [2], "districts": ["ФМР"], "budget_max": "6,5"})["client"]["id"]
    c = clients.detail(conn, buyer_agent, cid)["client"]
    assert buyers.publish(conn, buyer_agent, c).get("need_verify")       # без подтверждённого номера — нельзя
    conn.execute("INSERT INTO agent_phones (phone, user_id, verified_at) VALUES ('+79185550000', ?, 0)", (buyer_agent,))
    res = buyers.publish(conn, buyer_agent, c, "Срочно, наличные. Звонить 89997776655")
    assert res["ok"] and "Анна" not in res["text"] and "99900" not in res["text"] and "7776655" not in res["text"]
    assert "ФМР" in res["text"] and "2 комн." in res["text"]
    assert buyers.publish(conn, buyer_agent, c)["id"] == res["id"]       # повтор — тот же запрос, продлён
    assert buyers.notify_matches(conn) == 1
    n = conn.execute("SELECT * FROM notices WHERE user_id = ? AND kind = 'buyer'", (owner,)).fetchone()
    assert n["listing_id"] == lid and "покупатель" in n["title"]
    assert not conn.execute("SELECT 1 FROM notices WHERE user_id = ? AND kind = 'buyer'", (buyer_agent,)).fetchone()
    assert buyers.notify_matches(conn) == 0                              # второй раз не шлём
    feed = buyers.feed(conn, True)
    item = [i for i in feed["items"] if i["source"] == "agent"][0]
    assert item["phones"] == ["+79185550000"]                            # контакт — агента, не клиента
    assert buyers.published(conn, buyer_agent, cid)
    assert buyers.unpublish(conn, buyer_agent, cid) and buyers.published(conn, buyer_agent, cid) is None
    seller = clients.save(conn, buyer_agent, {"name": "Пётр", "kind": "seller"})["client"]["id"]
    assert "error" in buyers.publish(conn, buyer_agent, clients.detail(conn, buyer_agent, seller)["client"])

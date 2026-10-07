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
    assert feed["total"] == 1 and feed["items"][0]["phones"] == ["+79184445566"] and "district=" in feed["items"][0]["url"]

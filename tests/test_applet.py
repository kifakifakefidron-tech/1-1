import csv
import io
import json
import time

from app import applet, config, feed, ingest, parser, search, stats


SLAVA = ("+79000000011", "+79000000012")
ARTEM = "+79000000013"


def configure(monkeypatch):
    monkeypatch.setattr(config, "APPLET_CSV_URL", "https://example.test/sheet.csv")
    monkeypatch.setattr(config, "APPLET_SLAVA_PHONES", ",".join(SLAVA))
    monkeypatch.setattr(config, "APPLET_ARTEM_PHONE", ARTEM)
    monkeypatch.setattr(config, "FEED_PHONE", ARTEM)


def values(article="", price="6000", addr="Западный Обход 39/1 к6", presentation=False):
    r = [""] * 39
    r[2], r[3], r[4], r[5], r[6] = article, "Е2", "Западный обход", addr, "Зелёный театр"
    r[8], r[9], r[10], r[11], r[13] = "177", "7/16", "38", f"р.{price}", "Рем/меб/тех"
    r[16], r[27], r[29] = "Сейф 9999", "79000000019", "Контакты владельца и код от сейфа 9999"
    r[30] = f"❌Е2 КВ❌\nЦена 4600\nВячеслав Цветков\n{SLAVA[0]}\n{SLAVA[1]}"
    r[31] = f"♟Е2 КВ♟\n{'ФОТО+ПРЕЗЕНТАЦИЯ' if presentation else ''}\nАртём\n{ARTEM}"
    return r


def snapshot(*rows):
    file = io.StringIO(newline="")
    csv.writer(file).writerows(rows)
    return file.getvalue().encode()


def test_catalog_scope_price_and_private_columns(monkeypatch):
    configure(monkeypatch)
    items = applet.parse_csv(snapshot(values("733"), values(addr="Западный Обход 39/2"),
                                     values(addr="Западный Обход 39/3", presentation=True)))
    assert items[0]["price"] == 6_000_000 and items[0]["house"] == "39/1 к6"
    assert items[0]["floor"] == 7 and items[0]["area"] == 38
    assert items[0]["phones"] == list(SLAVA)
    assert items[1]["phones"] == [*SLAVA, ARTEM] and items[2]["phones"] == list(SLAVA)
    assert all("9999" not in json.dumps(item) and "79000000019" not in json.dumps(item) for item in items)


def test_refresh_keeps_yml_and_updates_or_removes_source_row(conn, monkeypatch):
    configure(monkeypatch)
    a, b = values("733"), values(addr="Западный Обход 39/2")
    yml = b'''<yml_catalog><shop><offers><offer id="site733"><price>6000000</price><vendorCode>733</vendorCode>
      <description>2 flat</description><picture>https://example.test/public.jpg</picture></offer></offers></shop></yml_catalog>'''
    feed.sync(conn, yml)
    original = conn.execute("SELECT id FROM listings WHERE ext_id='feed:site733'").fetchone()[0]
    result = applet.sync(conn, snapshot(a, b))
    assert result["added"] == 2
    entry = conn.execute("SELECT id,photos FROM listings WHERE ext_id='appletv:article:733'").fetchone()
    assert json.loads(entry["photos"]) == ["https://example.test/public.jpg"]
    assert conn.execute("SELECT status FROM dup_pairs WHERE a=? AND b=?", (original, entry["id"])).fetchone()[0] == "market"
    feed.sync(conn, yml)
    assert conn.execute("SELECT is_active,phones FROM listings WHERE id=?", (entry["id"],)).fetchone()[0] == 1
    assert json.loads(conn.execute("SELECT phones FROM listings WHERE id=?", (entry["id"],)).fetchone()[0]) == list(SLAVA)
    try:
        applet.sync(conn, b"invalid,fragment")
    except ValueError:
        pass
    else:
        raise AssertionError("Усечённый CSV принят")
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE source='feed' AND is_active=1").fetchone()[0] == 3
    a[11] = "р.6150"
    assert applet.sync(conn, snapshot(a))["hidden"] == 1
    assert conn.execute("SELECT price FROM listings WHERE id=?", (entry["id"],)).fetchone()[0] == 6_150_000
    assert conn.execute("SELECT is_active FROM listings WHERE id=?", (original,)).fetchone()[0] == 1
    assert conn.execute("SELECT hidden_reason FROM listings WHERE ext_id LIKE 'appletv:row:%'").fetchone()[0] == applet.REMOVED


def test_initial_replace_is_not_activity_and_authors_skip_before_parsing(conn, monkeypatch):
    configure(monkeypatch)
    now = int(time.time())
    for phone in (SLAVA[0], "79000000019"):
        conn.execute("""INSERT INTO listings(type,deal,title,phones,first_seen,last_seen)
            VALUES ('flat','sale','Копия',?,?,?)""", (json.dumps([phone]), now, now))
    before = stats.collect(conn, now)["listings"]
    assert applet.sync(conn, snapshot(values("733")), now)["chat_replaced"] == 1
    after = stats.collect(conn, now)["listings"]
    assert after["added"] == before["added"] and after["removed"] == before["removed"]
    assert not search.search(conn, search.Query(new_days=1), now, False)["items"]
    assert conn.execute("SELECT is_active FROM listings WHERE id=2").fetchone()[0] == 1

    def no_parse(*args, **kwargs):
        raise AssertionError("Объявление владельца ушло в парсер")

    monkeypatch.setattr(parser, "parse", no_parse)
    for source, sender, text in (("wa", SLAVA[0] + "@c.us", "Продаётся квартира"), ("tg", None, f"Продам квартиру {SLAVA[1]}"),
                                 ("max", ARTEM, "Продам дом")):
        mid = ingest.add_message(conn, source=source, text=text, sender=sender, ts=now)
        assert ingest.process_message(conn, mid)["status"] == "appletv"
    assert not applet.drops_chat("Ищу 2к квартиру в ФМР для клиента", ARTEM, applet.active_numbers(conn))

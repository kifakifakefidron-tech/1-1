import csv
import io
import json
import time
from datetime import datetime, timedelta, timezone

from app import agent, applet, config, db, dupreview, feed, ingest, parser, search, stats


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
    r[30] = (f"❌Е2 КВ❌\n▪️ Состояние: РЕМ/МЕБ/ТЕХ\n▪️ Показ по договорённости\n"
             f"Ключи в сейфе, код 9999\nКвартира №177\nЦена 4600\nВячеслав Цветков\n{SLAVA[0]}\n{SLAVA[1]}")
    r[31] = (f"♟Е2 КВ♟\n▪️ Техника остаётся при продаже\n"
             f"{'ФОТО+ПРЕЗЕНТАЦИЯ' if presentation else ''}\nАртём\n{ARTEM}")
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
    assert [item["phones"] for item in items] == [list(SLAVA), list(SLAVA), [ARTEM], list(SLAVA)]
    assert items[2]["ext_id"].startswith(applet.STAYL)
    assert items[0]["description"].splitlines() == ["❌Е2 КВ❌", "▪️ Состояние: РЕМ/МЕБ/ТЕХ", "▪️ Показ по договорённости"]
    assert items[2]["description"] == "♟Е2 КВ♟\n▪️ Техника остаётся при продаже"
    assert all("177" not in item["description"] and "4600" not in item["description"] for item in items)
    assert all("9999" not in json.dumps(item) and "79000000019" not in json.dumps(item) for item in items)


def test_catalog_review_reads_full_ad_and_links_only_same_private_unit(conn, monkeypatch):
    configure(monkeypatch)
    a, b, different = values("733"), values(), values("735")
    different[8] = "178"
    applet.sync(conn, snapshot(a, b, different))
    source = conn.execute("SELECT id, fragment, description FROM listings WHERE ext_id='appletv:article:733'").fetchone()
    assert "Показ по договорённости" not in source["fragment"]
    card = dupreview._card(conn, source["id"])
    assert card["text"] == source["description"]
    assert "Показ по договорённости" in card["text"] and "9999" not in card["text"]
    assert card["text_from"] == "catalog"
    other = conn.execute("SELECT id FROM listings WHERE ext_id='appletv:article:735'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM dup_pairs WHERE kind='applet_unit' AND status='market'").fetchone()[0] >= 1
    assert not conn.execute("SELECT 1 FROM dup_pairs WHERE a=? OR b=?", (other, other)).fetchone()
    b[8] = "179"
    applet.sync(conn, snapshot(a, b, different))
    assert conn.execute("SELECT is_active FROM listings WHERE id=?", (source["id"],)).fetchone()[0] == 1


def test_feed_article_links_only_the_partner_contact_and_preserves_chat_rows(conn, monkeypatch):
    configure(monkeypatch)
    now = int(time.time())
    for phone in (ARTEM, SLAVA[0]):
        conn.execute("""INSERT INTO listings(type,deal,title,phones,first_seen,last_seen,article)
                        VALUES ('flat','sale','Предложение из чата',?,?,?,'733')""",
                     (json.dumps([phone]), now, now))
    yml = b'<yml_catalog><shop><offers><offer id="site733"><price>6000000</price><vendorCode>733</vendorCode><description>2 flat</description></offer></offers></shop></yml_catalog>'
    stats = feed.sync(conn, yml)
    assert stats["chat_offers_linked"] == 1
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0] == 3
    pair = conn.execute("SELECT a,b,status FROM dup_pairs WHERE kind='feed_article'").fetchone()
    assert tuple(pair) == (1, 3, "market")


def test_refresh_keeps_yml_and_updates_or_removes_source_row(conn, monkeypatch):
    configure(monkeypatch)
    a, b = values("733"), values(addr="Западный Обход 39/2")
    yml = b'''<yml_catalog><shop><offers><offer id="site733"><price>6000000</price><vendorCode>733</vendorCode>
      <description>2 flat</description><picture>https://example.test/public.jpg</picture></offer></offers></shop></yml_catalog>'''
    feed.sync(conn, yml)
    original = conn.execute("SELECT id FROM listings WHERE ext_id='feed:site733'").fetchone()[0]
    result = applet.sync(conn, snapshot(a, b))
    assert result["added"] == 3
    entry = conn.execute("SELECT id,photos FROM listings WHERE ext_id='appletv:article:733'").fetchone()
    assert json.loads(entry["photos"]) == []
    assert json.loads(conn.execute("SELECT photos FROM listings WHERE id=?", (original,)).fetchone()[0]) == [
        "https://example.test/public.jpg"]
    assert conn.execute("SELECT status FROM dup_pairs WHERE a=? AND b=?", (original, entry["id"])).fetchone()[0] == "market"
    assert conn.execute("SELECT COUNT(*) FROM dup_pairs WHERE kind='applet_row'").fetchone()[0] == 1
    feed.sync(conn, yml)
    assert conn.execute("SELECT is_active,phones FROM listings WHERE id=?", (entry["id"],)).fetchone()[0] == 1
    assert json.loads(conn.execute("SELECT phones FROM listings WHERE id=?", (entry["id"],)).fetchone()[0]) == list(SLAVA)
    try:
        applet.sync(conn, b"invalid,fragment")
    except ValueError:
        pass
    else:
        raise AssertionError("Усечённый CSV принят")
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE source='feed' AND is_active=1").fetchone()[0] == 4
    a[11] = "р.6150"
    assert applet.sync(conn, snapshot(a))["hidden"] == 2
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


def test_upgrade_splits_existing_contacts_without_new_listing_stats(conn, monkeypatch):
    configure(monkeypatch)
    body = snapshot(values())
    slava, *_ = applet.parse_csv(body)
    now = int(time.time())
    conn.execute("""INSERT INTO listings(type,deal,title,phones,first_seen,last_seen,source,ext_id,photos,catalog_migrated)
                    VALUES ('flat','sale','Объект',?,?,?,'feed',?,'["old_photo"]',1)""",
                 (json.dumps([*SLAVA, ARTEM]), now, now, slava["ext_id"]))
    conn.execute("""INSERT INTO listings(type,deal,title,phones,phone_roles,first_seen,last_seen)
                    VALUES ('flat','sale','Чужой объект',?,?,?,?)""",
                 (json.dumps([ARTEM, "+79000000019"]), json.dumps({SLAVA[0]: "sender"}), now, now))
    conn.commit()
    before = stats.collect(conn, now)["listings"]["added"]
    db.set_state(conn, applet.SYNCED, str(now - 60))
    conn.execute("DELETE FROM state WHERE key='appletv_media_cleanup'")
    db._migrate(conn)
    old = conn.execute("SELECT photos,phones FROM listings WHERE ext_id=?", (slava["ext_id"],)).fetchone()
    assert json.loads(old["photos"]) == [] and json.loads(old["phones"]) == list(SLAVA)
    assert applet.sync(conn, body)["added"] == 1
    entries = conn.execute("SELECT phones,photos FROM listings ORDER BY id").fetchall()
    assert [json.loads(entry["phones"]) for entry in entries] == [list(SLAVA), ["+79000000019"], [ARTEM]]
    assert all(json.loads(entry["photos"]) == [] for entry in entries)
    assert stats.collect(conn, now)["listings"]["added"] == before
    assert applet.sync(conn, body)["unchanged"]


def test_sync_uses_moscow_slots_and_retries_only_failed_slot(conn, monkeypatch):
    configure(monkeypatch)
    body = snapshot(values("733"))
    moscow = timezone(timedelta(hours=3))
    now = 0
    calls = []
    failing = False

    def fetch(request, timeout):
        calls.append(now)
        if failing:
            raise TimeoutError
        return io.BytesIO(body)

    monkeypatch.setattr(applet.time, "time", lambda: now)
    monkeypatch.setattr(applet.urllib.request, "urlopen", fetch)

    def at(hour, minute=0, day=18):
        nonlocal now
        now = int(datetime(2026, 11, day, hour, minute, tzinfo=moscow).timestamp())
        return applet.maybe_sync(conn)

    # Вчерашний запуск уже выполнен, до 08:00 новый импорт не нужен.
    yesterday = int(datetime(2026, 11, 17, 19, tzinfo=moscow).timestamp())
    db.set_state(conn, "appletv_completed_slot", str(yesterday))
    db.set_state(conn, applet.IMPORTED_FORMAT, applet.FORMAT)
    assert at(7, 59) is None and not calls
    assert at(8)["added"] == 1
    assert at(14, 59) is None and len(calls) == 1
    assert at(15) == {"unchanged": True} and len(calls) == 2
    completed = db.get_state(conn, "appletv_completed_slot")
    failing = True
    assert at(19)["error"] == "TimeoutError"
    assert at(19, 9) is None and len(calls) == 3
    assert db.get_state(conn, "appletv_completed_slot") == completed
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE is_active=1").fetchone()[0] == 1
    failing = False
    assert at(19, 10) == {"unchanged": True} and len(calls) == 4
    assert at(7, 59, day=19) is None and len(calls) == 4
    assert at(8, day=19) == {"unchanged": True} and len(calls) == 5


def test_new_description_format_refreshes_existing_offer_between_slots(conn, monkeypatch):
    configure(monkeypatch)
    body = snapshot(values("733"), values(addr="Западный Обход 39/2"))
    now = int(datetime(2026, 11, 18, 16, tzinfo=timezone(timedelta(hours=3))).timestamp())
    applet.sync(conn, body, now - 3600)
    before = conn.execute("SELECT id,price,area,floor,phones,repair,repair_doubt FROM listings ORDER BY id").fetchall()
    assert before[0]["repair"] == "yes"
    conn.execute("UPDATE listings SET description=''")
    db.set_state(conn, applet.IMPORTED_FORMAT, "1")
    db.set_state(conn, "appletv_snapshot", "old snapshot signature")
    db.set_state(conn, "appletv_completed_slot", str(now - 3600))
    monkeypatch.setattr(applet.time, "time", lambda: now)
    monkeypatch.setattr(applet.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(body))

    result = applet.maybe_sync(conn)
    assert result["updated"] == 3 and result["added"] == result["hidden"] == 0
    after = conn.execute("SELECT id,price,area,floor,phones,repair,repair_doubt FROM listings ORDER BY id").fetchall()
    assert after == before
    descriptions = [row[0] for row in conn.execute("SELECT description FROM listings ORDER BY id")]
    assert ["Показ по договорённости" in desc for desc in descriptions] == [True, True, False]
    assert "Техника остаётся при продаже" in descriptions[2]
    assert applet.maybe_sync(conn) is None


def test_sync_does_not_lift_existing_properties(conn, monkeypatch):
    configure(monkeypatch)
    now = int(time.time())
    old = now - 4 * 86400

    def yml(ext, price, article="733", house=10):
        return (f'<offer id="{ext}"><price>{price}</price><vendorCode>{article}</vendorCode>'
                f'<description>2-к квартира 38 м², ЖК Зелёный театр, ул. Тургенева {house}.</description></offer>')

    def feed_body(*records):
        return ("<yml_catalog><shop><offers>" + "".join(records) + "</offers></shop></yml_catalog>").encode()

    feed.sync(conn, feed_body(yml("original", 6_000_000)), old)
    old_apple = values("734", addr="Западный Обход 39/2")
    applet.sync(conn, snapshot(old_apple), old + 80)
    conn.execute("INSERT INTO users(id, created) VALUES (1, ?)", (old,))
    conn.executemany("INSERT INTO agent_phones(user_id,phone,verified_at) VALUES (1,?,?)",
                     [(ARTEM, now), (SLAVA[0], now)])
    conn.executemany("""INSERT INTO listings (source,type,deal,title,rooms,area,street,price,phones,first_seen,last_seen,owner_user_id)
                        VALUES (?, 'flat','sale', ?, 2, 38, 'Тургенева', ?, ?, ?, ?, ?)""",
                     [("chat", "Объект из чата", 7_100_000, "[]", now - 3000, now, None),
                      ("own", "Мой объект", 7_500_000, "[]", now - 2000, now - 2000, 1)])
    conn.commit()

    before = {r["ext_id"]: (r["id"], r["first_seen"]) for r in conn.execute("SELECT * FROM listings")}
    result = feed.sync(conn, feed_body(yml("changed-key", 6_200_000), yml("fresh", 8_000_000, "111", 22)), now)
    assert result["updated"] == 1 and result["added"] == 1 and result["hidden"] == 0
    old_apple[11] = "р.6100"
    updated = applet.sync(conn, snapshot(old_apple, values("735", addr="Западный Обход 39/3")), now + 1)
    assert updated["updated"] == updated["added"] == 1
    after = {r["ext_id"]: (r["id"], r["first_seen"]) for r in conn.execute("SELECT * FROM listings")}
    assert after["feed:changed-key"] == before["feed:original"]
    assert after["appletv:article:734"] == before["appletv:article:734"]
    expected = [after["appletv:article:735"][0], after["feed:fresh"][0]]
    qr = search.Query(new_days=1)
    assert [r["id"] for r in search.search(conn, qr, now + 1, False, group_offers=True)["items"] if r["source"] == "feed"] == expected
    assert [r["id"] for r in agent.my_listings(conn, 1)][:2] == expected

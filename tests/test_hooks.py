"""Фишки: подписка на поиск с письмом, снижение цены в избранном, «продают ещё N агентов»,
цена к рынку, заметки, «Новое сегодня»."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, hooks, ingest, search, server

NOW = int(time.time())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    hooks._market["t"] = 0
    conn = db.get()
    with TestClient(server.app) as c:
        yield c, conn
    conn.close()
    db._local.conn = None


def _add(conn, text, ts=None, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, ts=ts or NOW, chat_id=f"c{n[0]}", msg_id=f"m{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def _login(c, conn, email="u@example.ru"):
    u = accounts.upsert_email_user(conn, email)
    c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
    return u


def test_saved_search_sends_new_objects_once(env):
    c, conn = env
    _login(c, conn)
    r = c.post("/api/saved", json={"query": "deal=sale&rooms=2&page=3&view=map"})
    assert r.status_code == 200 and r.json()["saved"]["title"] == "Продажа · 2 комн."
    assert c.post("/api/saved", json={"query": "rooms=2&deal=sale"}).status_code == 400   # уже следим
    conn.execute("UPDATE saved_searches SET checked_at = ?, notice_checked_at = ?", (NOW - 100, NOW - 100))
    conn.commit()
    _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001", ts=NOW)
    _add(conn, "1-к квартира 35 м², 3/9 эт. 4 млн руб. 89180000002", ts=NOW)
    mails = []
    assert hooks.check_saved(conn, lambda to, subj, body: mails.append(body) or True) == 1
    assert "2-к квартира" in mails[0] and "1-к" not in mails[0] and "/saved/off?s=" in mails[0]
    assert hooks.check_saved(conn, lambda *a: mails.append(a) or True) == 0   # не чаще раза в 3 часа
    sid = hooks.list_saved(conn, 1)[0]["id"]
    assert "больше не придут" in c.get(f"/saved/off?s={sid}&t={hooks.unsub_token(sid)}").text
    assert hooks.list_saved(conn, 1) == []


def test_price_drop_in_favorites(env):
    c, conn = env
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001", ts=NOW - 100)
    _login(c, conn)
    c.post(f"/api/favorites/{lid}")
    _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 5,7 млн руб. 89180000001", ts=NOW)
    row = conn.execute("SELECT price, prev_price FROM listings WHERE id = ?", (lid,)).fetchone()
    assert (row["price"], row["prev_price"]) == (5_700_000, 6_000_000)
    mails = []
    assert hooks.check_price_drops(conn, lambda to, subj, body: mails.append(body) or True) == 1
    assert "6 млн ₽ → 5,7 млн ₽" in mails[0]
    assert hooks.check_price_drops(conn, lambda *a: mails.append(a) or True) == 0


def test_same_object_other_agents_and_market(env):
    c, conn = env
    a = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001")
    _add(conn, "2-к квартира 50,5 м², 3/9 эт., ЖК Мозаика. 6,2 млн руб. 89180000002")
    _add(conn, "2-к квартира 50 м², 7/9 эт., ЖК Мозаика. 7 млн руб. 89180000003")   # другой этаж
    d = c.get(f"/api/listings/{a}").json()
    assert [s["price"] for s in d["same"]] == [6_200_000]
    assert d["market"]["base"] == "2-к в ЖК Мозаика" and d["market"]["n"] == 3


def test_notes_private(env):
    c, conn = env
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт. 6 млн руб. 89180000001")
    _login(c, conn)
    c.post(f"/api/notes/{lid}", json={"text": "Звонил, ждут показ в субботу"})
    assert c.get(f"/api/listings/{lid}").json()["note"] == "Звонил, ждут показ в субботу"
    c.cookies.clear()
    _login(c, conn, "other@example.ru")
    assert c.get(f"/api/listings/{lid}").json()["note"] == ""


def test_new_today_filter(env):
    c, conn = env
    _add(conn, "2-к квартира 50 м², 3/9 эт., ФМР. 6 млн руб. 89180000001", ts=NOW - 5 * 86400)
    _add(conn, "1-к квартира 35 м², 3/9 эт., ФМР. 4 млн руб. 89180000002", ts=NOW)
    assert search.search(conn, search.Query(new_days=1), NOW, False)["total"] == 1


def test_share_link_preview_and_strict_same(env):
    c, conn = env
    # 24.10j: разные агенты — своими словами (одинаковый текст с другой ценой теперь склеивается в одну карточку)
    a = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика, ул. Тургенева 10. 6 млн руб. 89180000001")
    _add(conn, "Продаю двушку 50 кв.м на 3 этаже из 9, ЖК Мозаика, ул. Тургенева 10, хороший ремонт. 6,1 млн руб. 89180000002")
    _add(conn, "Срочно 2к 50м 3/9 ЖК Мозаика ул. Красная 5, без вложений. 6,2 млн руб. 89180000003")   # другая улица
    _add(conn, "Двухкомнатная 50 м² ЖК Мозаика ул. Тургенева 10, этаж 5 из 9. 6,05 млн руб. 89180000004")   # другой этаж
    assert [s["price"] for s in c.get(f"/api/listings/{a}").json()["same"]] == [6_100_000]
    page = c.get(f"/?open={a}").text
    assert 'og:title' in page and "6 млн ₽" in page and "8918" not in page


def test_notices_page_feed(env):
    from app import agent, notices
    c, conn = env
    u = _login(c, conn)
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт., ФМР. 6 млн руб. 89180000001", ts=NOW - 100)
    c.post(f"/api/favorites/{lid}")
    c.post("/api/saved", json={"query": "deal=sale&rooms=1", "page": "?rooms=1&view=map&open=5"})
    conn.execute("UPDATE saved_searches SET checked_at = ?, notice_checked_at = ?", (NOW - 100, NOW - 100))
    conn.commit()
    _add(conn, "1-к квартира 35 м², 3/9 эт., ФМР. 4 млн руб. 89180000002", ts=NOW)
    conn.execute("UPDATE listings SET price = 6500000 WHERE id = ?", (lid,))   # цена выросла
    conn.commit()
    hooks.run(conn, None, every_s=0)
    kinds = {n["kind"]: n for n in c.get("/api/notices").json()["items"]}
    assert kinds["search"]["url"].startswith("/?rooms=1&saved=") and "&since=" in kinds["search"]["url"]
    assert kinds["price"]["title"].startswith("Цена выросла")
    assert c.get("/api/me").json()["me"]["unread"] == 2
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (lid,))   # объект сняли
    conn.commit()
    hooks.run(conn, None, every_s=0)
    assert "gone" in {n["kind"] for n in c.get("/api/notices").json()["items"]}
    c.post("/api/notices", json={})
    assert c.get("/api/notices").json()["unread"] == 0
    # доступ заканчивается через день
    conn.execute("UPDATE users SET trial_until = 0, paid_until = ? WHERE id = ?", (NOW + 86400, u["id"]))
    conn.commit()
    assert notices.check_subscriptions(conn, lambda ts: "завтра") == 1
    assert notices.check_subscriptions(conn, lambda ts: "завтра") == 0
    assert "notifications" in c.get("/notifications").text or c.get("/notifications").status_code == 200


def test_site_notice_even_if_email_already_sent(env):
    """Письмо ушло (например, старой версией сайта) — уведомление на сайте всё равно появляется."""
    from app import notices
    c, conn = env
    u = _login(c, conn)
    c.post("/api/saved", json={"query": "deal=sale&rooms=2", "page": "?rooms=2"})
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт., ФМР. 6 млн руб. 89180000001", ts=NOW - 50)
    c.post(f"/api/favorites/{lid}")
    # «старая версия»: письмо уже отправлено, отметки почты сдвинуты
    conn.execute("UPDATE saved_searches SET sent_at = ?, checked_at = ?, notice_checked_at = NULL, created = ?",
                 (NOW, NOW, NOW - 100))
    conn.execute("UPDATE listings SET price = 5500000 WHERE id = ?", (lid,))
    conn.execute("UPDATE favorites SET notified_price = 5500000")
    conn.commit()
    mails = []
    hooks.run(conn, lambda *a: mails.append(a) or True, every_s=0)
    kinds = sorted(n["kind"] for n in notices.items(conn, u["id"]))
    assert kinds == ["price", "search"] and mails == []      # на сайте есть, повторных писем нет
    # открыл «Уведомления» — новые появляются сразу, без ожидания фоновой проверки
    _add(conn, "2-к квартира 60 м², 5/9 эт., ЮМР. 7 млн руб. 89180000009", ts=int(time.time()) + 5)
    titles = [n["title"] for n in c.get("/api/notices").json()["items"]]
    assert titles.count("Новые объекты по поиску: 1") == 2


def test_old_saved_search_url_rebuilt_from_params():
    from urllib.parse import parse_qs, urlsplit
    from app import hooks
    s = {"id": 7, "page_query": None,
         "params": "deal=sale&district=%D0%A4%D0%9C%D0%A0&district=%D0%AE%D0%9C%D0%A0&price_max=7500000&rooms=1%2C2&type=flat"}
    q = parse_qs(urlsplit(hooks.search_url(s)).query)
    assert q["district"] == ["ФМР|ЮМР"] and q["pmax"] == ["7.5"] and q["rooms"] == ["1|2"] and q["saved"] == ["7"]
    rent = {"id": 8, "page_query": "", "params": "deal=rent&price_max=35000"}
    q = parse_qs(urlsplit(hooks.search_url(rent)).query)
    assert q["deal"] == ["rent"] and q["pmax"] == ["35"]
    # служебные отметки из адреса страницы не попадают в ссылку
    assert hooks.search_url({"id": 9, "page_query": "rooms=2&open=5&saved=3&since=1", "params": ""}) == "/?rooms=2&saved=9"


def test_note_status_crm(conn, add):
    from app import accounts, hooks
    lid = add("2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233")["results"][0]["listing_id"]
    u = accounts.upsert_email_user(conn, "crm@example.ru")
    hooks.set_note(conn, u["id"], lid, status="call")
    hooks.set_note(conn, u["id"], lid, text="перезвонить в пятницу")   # текст не сбрасывает статус
    assert hooks.get_status(conn, u["id"], lid) == "call" and hooks.get_note(conn, u["id"], lid) == "перезвонить в пятницу"
    assert accounts.me(conn, accounts.get_user(conn, u["id"]))["statuses"] == {lid: "call"}
    hooks.set_note(conn, u["id"], lid, status="bogus")
    assert hooks.get_status(conn, u["id"], lid) is None
    hooks.set_note(conn, u["id"], lid, text="")
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0   # ни текста, ни статуса — запись убрана


def test_hot_flag_and_count_match_filter():
    """29.10 (Артём: «Горячее» — варианты 3 + 4): отметка на карточке и число на кнопке — по тому же правилу, что фильтр."""
    import time as _t
    from app import db, hooks, ingest, search
    conn = db.connect(":memory:")
    now = int(_t.time())
    m = ingest.add_message(conn, source="wa", text="2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ул. Тургенева 10. 7,5 млн. 89181112233",
                           chat_id="c", msg_id="h1", ts=now)
    lid = ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET prev_price = 7800000, price_changed_at = ? WHERE id = ?", (now - 3600, lid))   # −4 % — мало
    conn.commit()
    item = search.search(conn, search.Query(), now, False)["items"][0]
    assert hooks.annotate(conn, [item])[0]["hot"] is None   # 29.10: снижение меньше 10 % — не горячее
    conn.execute("UPDATE listings SET prev_price = 8400000 WHERE id = ?", (lid,))   # −10,7 %
    conn.commit()
    item = search.search(conn, search.Query(), now, False)["items"][0]
    hooks.annotate(conn, [item])
    assert item["hot"] == "drop"
    assert search.facets(conn, search.Query(), now)["hot"] == 1
    assert search.search(conn, search.Query(hot=True), now, False)["total"] == 1
    conn.execute("UPDATE listings SET price_changed_at = ? WHERE id = ?", (now - 10 * 86400, lid))   # снижали давно — уже не горячее
    conn.commit()
    item = search.search(conn, search.Query(), now, False)["items"][0]
    assert hooks.annotate(conn, [item])[0]["hot"] is None and search.facets(conn, search.Query(), now)["hot"] == 0


def test_market_compares_same_rooms_and_falls_back_to_district():
    """31.10 (Артём): «дешевле рынка» — только с похожими по комнатам (и по ремонту, если похожих хватает);
    в маленьком ЖК — по району."""
    from app import db, hooks, ingest
    conn = db.connect(":memory:")
    now = int(time.time())
    k = 0

    def put(text):
        nonlocal k
        k += 1
        m = ingest.add_message(conn, source="wa", text=f"{text} 8918{k:07d}", chat_id="c", msg_id=f"m{k}", ts=now)
        return ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]

    for i in range(16):   # студии в ЖК Мозаика дорогие за метр
        put(f"Студия {25 + i % 3} м², {2 + i % 9}/16 эт., ЖК Мозаика, ул. Тургенева {i + 1}. {5 + i / 100:.2f} млн.")
    for i in range(16):   # 2-к в том же районе (Кожзавод), но без ЖК
        put(f"2-к квартира {60 + i % 3} м², {2 + i % 9}/16 эт., район Кожзавод, ул. Коммунаров {i + 1}. {7 + i / 100:.2f} млн.")
    two = put("2-к квартира 60 м², 5/16 эт., ЖК Мозаика, ул. Тургенева 50. 6 млн.")   # 100 тыс./м²
    hooks._market["t"] = 0
    row = dict(conn.execute("SELECT * FROM listings WHERE id = ?", (two,)).fetchone())
    mk = hooks.market_for(conn, row, hooks.HOT_MIN_N)
    # раньше двушку сравнивали со студиями ЖК (≈ 200 тыс./м²) — «на 50 % дешевле»; теперь — с 2-к района (≈ 116 тыс./м²)
    assert mk["level"] == "d" and mk["rooms"] == "2-к" and mk["base"].startswith("2-к в районе") and -20 < mk["diff"] < -5
    assert hooks.room_group({"room_kind": "euro", "rooms": 2}) == 1 and hooks.room_group({"room_kind": "studio", "rooms": 0}) == "s"


def test_hot_card_uses_saved_market_diff():
    """31.10 (Артём: «в фильтре «Горячие» карточки без рамки»): рамка и процент — по той же записанной цифре, что фильтр."""
    from app import db, hooks, ingest, search
    conn = db.connect(":memory:")
    now = int(time.time())
    m = ingest.add_message(conn, source="wa", text="1-к квартира 40 м², 3/9 эт., ФМР, ул. Тургенева 7. 4 млн. 89180000777",
                           chat_id="c", msg_id="h1", ts=now)
    lid = ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET market_diff = -20 WHERE id = ?", (lid,))   # фильтр видит −20 %, пересчёт на лету — нет
    conn.commit()
    items = search.search(conn, search.Query(hot=True), now, False)["items"]
    assert [i["id"] for i in items] == [lid]
    it = hooks.annotate(conn, items)[0]
    assert it["hot"] == "market" and it["market_diff"] == -20 and "price_doubt" not in it


def test_explain_repair_steps():
    """31.10 (Артём): окно разбора «Ремонт» показывает, какой шаг решил и что выключено."""
    from app import db, ingest
    conn = db.connect(":memory:")
    m = ingest.add_message(conn, source="wa", text="2-к квартира 50 м², 3/9 эт., ФМР, ул. Тургенева 9. ПЧО, евроремонт в подъезде"
                           " сделан, капремонт дома. 6 млн. 89180000778", chat_id="c", msg_id="r1", ts=int(time.time()))
    lid = ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    w = ingest.explain_repair(conn, r)
    st = {s["name"]: s for s in w["steps"]}
    assert w["result"] == "без ремонта" and st["Правило «без ремонта»"]["state"] == "decide" and "«пчо»" in st["Правило «без ремонта»"]["note"]
    assert st["Ваша правка"]["state"] == "none" and st["Про дом, не про квартиру"]["state"] == "off"
    ingest.learn_repair_phrase(conn, "пчо", "yes")
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    st = {s["name"]: s for s in ingest.explain_repair(conn, r)["steps"]}
    assert st["Выученные слова"]["state"] == "decide" and st["Правило «без ремонта»"]["state"] != "decide"


def test_repair_phrases_whole_words_and_generic_off():
    """31.10 (Артём: «правила, которые я добавил, не действуют»): слова — только целиком; одно общее слово («ремонт») не решает;
    объект, посчитанный по старым словам, пересчитывается при открытии в разборе."""
    from app import db, ingest
    conn = db.connect(":memory:")
    now = int(time.time())

    def put(text, n):
        m = ingest.add_message(conn, source="wa", text=text + f" 8918000{n:04d}", chat_id="c", msg_id=f"g{n}", ts=now)
        return ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]
    a = put("Студия 25 м², 3/9 эт., ФМР, ул. Тургенева 1. Отделка ремонт мебель. 5,1 млн.", 1)
    b = put("1-к 40 м², 3/9 эт., ФМР, ул. Тургенева 2. Квартира без ремонта. 4 млн.", 2)
    assert "error" in ingest.learn_repair_phrase(conn, "ремонт", "yes")   # общее слово — не принимаем
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('repair_phrase', 'ремонт', '{"repair": "yes"}', '', 1, 1)""")
    conn.commit()
    ingest.backfill_repair(conn)
    rep = lambda i: conn.execute("SELECT repair FROM listings WHERE id = ?", (i,)).fetchone()[0]
    assert rep(b) == "no"   # старое «ремонт = с ремонтом» больше не делает «без ремонта» ремонтом
    ingest.learn_repair_phrase(conn, "ремонт мебель", "yes")
    assert rep(a) == "yes"
    conn.execute("UPDATE listings SET repair = 'no', repair_doubt = 1 WHERE id = ?", (a,))   # как будто считали по старым словам
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (a,)).fetchone()
    w = ingest.explain_repair(conn, r)
    assert w["fixed"] and w["result"] == "с ремонтом" and rep(a) == "yes"
    assert any(s["name"] == "Слишком общие слова" for s in w["steps"])
    assert ingest.repair_by_phrases("свежий евроремонта нет", {"ремонт": "no"})[0] != "no"   # «ремонт» не находится внутри слова

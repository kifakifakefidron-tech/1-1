"""Подборки для клиента: создать, добавить объект, публичная страница без чужих телефонов."""
import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, ingest, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "x")   # кабинет «настроен» — доступ проверяется
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    mid = ingest.add_message(conn, source="manual", text="2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. Звоните 89181112233")
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    with TestClient(server.app) as c:
        yield c, conn, lid
    conn.close()
    db._local.conn = None


def _login(c, conn, paid=True):
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    conn.execute("UPDATE users SET trial_until = 0, name = 'Ирина' WHERE id = ?", (u["id"],))
    conn.commit()
    if paid:
        accounts.extend(conn, u["id"], 7)
    c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))


def test_pick_flow_and_public_page_has_no_agent_phones(env):
    c, conn, lid = env
    _login(c, conn)
    p = c.post("/api/picks", json={"title": "Для Ольги", "listing_id": lid, "contact_phone": "8 900 111 22 33"}).json()["pick"]
    assert p["n"] == 1 and p["contact_name"] == "Ирина" and p["contact_phone"] == "+79001112233"
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "add": True, "note": "Лучший по цене"})
    assert c.get(f"/api/picks?listing={lid}").json()["with"] == [p["id"]]
    pub = c.get(f"/api/c/{p['token']}").json()
    assert pub["items"][0]["note"] == "Лучший по цене"
    assert "1112233" not in str(pub).replace("+79001112233", "") and "phones" not in pub["items"][0]   # чужих телефонов нет
    page = c.get(f"/c/{p['token']}")
    assert page.status_code == 200 and 'og:title" content="Подобрали для вас"' in page.text and "Для Ольги" not in page.text
    views = lambda: tuple(conn.execute("SELECT views, views_unique FROM collections").fetchone())  # noqa: E731
    assert views() == (0, 0)                      # открыл сам агент (вошёл) — не просмотр клиента
    from starlette.testclient import TestClient
    phone_ua = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"}
    with TestClient(server.app) as guest:
        guest.get(f"/api/c/{p['token']}", headers={"user-agent": "WhatsApp/2.23.20.0 A"})          # превью в мессенджере
        guest.get(f"/api/c/{p['token']}", headers={"user-agent": "TelegramBot (like TwitterBot)"})
        guest.get(f"/api/c/{p['token']}?as=agent", headers=phone_ua)                                # «Как видит клиент»
        assert views() == (0, 0)
        guest.get(f"/api/c/{p['token']}", headers=phone_ua)                                          # клиент открыл
        guest.get(f"/api/c/{p['token']}", headers=phone_ua)                                          # и ещё раз в тот же день
        assert views() == (2, 1)
    with TestClient(server.app) as other_phone:
        other_phone.get(f"/api/c/{p['token']}", headers={"user-agent": "Mozilla/5.0 (Linux; Android 14) Chrome/120 Mobile"})   # другое устройство
    assert views() == (3, 2)
    c.request("DELETE", "/api/picks", json={"id": p["id"]})
    assert c.get(f"/api/c/{p['token']}").status_code == 404


def test_picks_need_subscription(env):
    c, conn, lid = env
    _login(c, conn, paid=False)
    assert c.post("/api/picks", json={"title": "x"}).status_code == 403


def test_pick_custom_text_without_phones(env):
    c, conn, lid = env
    _login(c, conn)
    p = c.post("/api/picks", json={"title": "Тест", "listing_id": lid}).json()["pick"]
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "text": "Отличная двушка, звоните 8 918 111-22-33"})
    pub = c.get(f"/api/c/{p['token']}").json()
    assert pub["items"][0]["description"].startswith("Отличная двушка") and "918" not in pub["items"][0]["description"]
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "text": ""})   # вернуть исходный
    assert "ЖК Мозаика" in c.get(f"/api/c/{p['token']}").json()["items"][0]["description"]


def test_cookie_consent_sets_and_revokes(tmp_path, monkeypatch):
    """17.10: без согласия необязательные cookie не ставятся; «Принять все» — ставятся; «Только необходимые» — удаляются."""
    from starlette.testclient import TestClient
    from app import config, db, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    with TestClient(server.app) as c:
        c.get("/?ref=abc")
        assert c.cookies.get("ref") is None
        r = c.post("/api/c/consent", json={"choice": "all", "ref": "abc"})
        assert r.json()["choice"] == "all" and c.cookies.get("ck") == "all" and c.cookies.get("ref") == "abc"
        c.post("/api/c/consent", json={"choice": "need"})
        assert c.cookies.get("ck") == "need" and c.cookies.get("ref") is None
        assert db.get().execute("SELECT COUNT(*) FROM cookie_consents").fetchone()[0] == 2
    db._local.conn = None


def test_pick_own_price_and_hidden_price(env):
    """17.10: своя цена для клиента (в объявлении — другая) и «Скрыть цену» — клиент видит «по запросу»."""
    c, conn, lid = env
    _login(c, conn)
    p = c.post("/api/picks", json={"title": "Цена", "listing_id": lid, "contact_phone": "8 900 111 22 33"}).json()["pick"]
    orig = conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0]
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "add": True, "price": 7_250_000})
    it = c.get(f"/api/c/{p['token']}?as=agent").json()["items"][0]
    assert it["price"] == 7_250_000 and not it["price_hidden"]
    mine = [x for x in c.get("/api/picks").json()["items"] if x["id"] == p["id"]][0]["items"][0]
    assert mine["my_price"] == 7_250_000 and mine["price"] == orig        # агент видит и свою, и цену из объявления
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "add": True, "hide_price": True})
    it = c.get(f"/api/c/{p['token']}?as=agent").json()["items"][0]
    assert it["price"] is None and it["price_m2"] is None and it["price_hidden"]
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "add": True, "hide_price": False, "price": 0})
    assert c.get(f"/api/c/{p['token']}?as=agent").json()["items"][0]["price"] == orig


def test_repair_filter():
    from app import db, ingest, search
    conn = db.connect(":memory:")
    for i, t in enumerate(["2-к квартира 50 м², 3/9 эт., ФМР, ул. Красная 10. Евроремонт. 6 млн. 89180000001",
                           "2-к квартира 52 м², 4/9 эт., ФМР, ул. Красная 12. Без ремонта. 5 млн. 89180000002",
                           "2-к квартира 51 м², 5/9 эт., ФМР, ул. Красная 14. Черновая отделка. 4,5 млн. 89180000003"]):
        ingest.process_message(conn, ingest.add_message(conn, source="manual", text=t, msg_id=str(i)), use_llm=False)
    prices = lambda cond: sorted(x["price"] for x in search.search(conn, search.Query(cond=[cond]), 0, True)["items"])  # noqa: E731
    assert prices("repair") == [6_000_000]
    assert prices("norepair") == [4_500_000, 5_000_000]


def test_pick_own_photos_first_for_client(env, tmp_path, monkeypatch):
    """17.10: свои фото агента к объекту в подборке — клиент видит их первыми; убрать — пропадают."""
    import base64
    from app import config
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path / "photos"))
    c, conn, lid = env
    _login(c, conn)
    p = c.post("/api/picks", json={"title": "Фото", "listing_id": lid, "contact_phone": "8 900 111 22 33"}).json()["pick"]
    jpg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 3000).decode()
    r = c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "photo": jpg}).json()
    assert r["ok"] and r["photos"][0].startswith("/photos/pk/")
    own = r["photos"][0]
    assert c.get(f"/api/c/{p['token']}?as=agent").json()["items"][0]["photos"][0] == own
    mine = [x for x in c.get("/api/picks").json()["items"] if x["id"] == p["id"]][0]["items"][0]
    assert mine["my_photos"] == [own] and mine["photo"] == own
    assert c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "photo": "data:image/jpeg;base64,AAAA"}).status_code == 400
    c.post(f"/api/picks/{p['id']}/items", json={"listing_id": lid, "photo_del": own})
    assert own not in c.get(f"/api/c/{p['token']}?as=agent").json()["items"][0]["photos"]


def test_compare_page(env):
    c, conn, lid = env
    r = c.get("/compare")
    assert r.status_code == 200 and "compare.js" in r.text


def test_detect_repair_phrases():
    from app import rules
    no = ["ПЧО, сдача 2026", "предчистовая отделка", "white box", "черновая", "без отделки", "без ремонта", "под ремонт",
          "требует косметического ремонта", "бабушкин ремонт", "ремонт в процессе", "в бетоне", "ремонт под себя, сейчас пчо"]
    yes = ["евроремонт", "ремонт. мебель", "свежий ремонт", "чистовая отделка", "заезжай и живи", "с ремонтом",
           "без обременений, евроремонт", "под ипотеку, ремонт свежий", "ремонт под ключ"]
    assert [t for t in no if rules.detect_repair(t) != "no"] == []
    assert [t for t in yes if rules.detect_repair(t) != "yes"] == []
    assert rules.detect_repair("2к 54 м 6 млн") is None


def test_repair_stricter_and_doubt():
    """24.10 (Артём): одно слово «ремонт» — не «с ремонтом»; «ремонт под себя», «можно сделать ремонт» — без ремонта;
    сомнительные тексты — в очередь «Ремонт»."""
    from app import rules
    assert rules.detect_repair("2к, ремонт, мебель, техника") == "yes"           # пункт списка
    assert rules.detect_repair("Состояние: ремонт") == "yes"
    assert rules.detect_repair("можно сделать ремонт по своему вкусу") == "no"
    assert rules.detect_repair("мебель и техника остаются") is None
    assert rules.detect_repair("стоимость ремонта обсуждаем") is None and rules.repair_doubt("стоимость ремонта обсуждаем")
    assert not rules.repair_doubt("2к 54 м 6 млн") and not rules.repair_doubt("евроремонт")


def test_admin_repair_answer_teaches_same_text():
    """24.10: ответ в очереди «Ремонт» снимает «под вопросом» и запоминается для того же текста."""
    import time

    from app import db, ingest
    conn = db.connect(":memory:")
    text = "2-к квартира 50 м², 3/9 эт., ФМР, ул. Тургенева 10. Стоимость ремонта обсуждаем. 6 млн. 89180000931"
    m = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id="r1", ts=int(time.time()))
    lid = ingest.process_message(conn, m, use_llm=False)["results"][0]["listing_id"]
    assert conn.execute("SELECT repair, repair_doubt FROM listings WHERE id = ?", (lid,)).fetchone()[:] == (None, 1)
    assert ingest.set_fields(conn, lid, {"repair": "no"}, 1)["ok"]
    assert conn.execute("SELECT repair, repair_doubt FROM listings WHERE id = ?", (lid,)).fetchone()[:] == ("no", 0)
    assert conn.execute("SELECT COUNT(*) FROM learned_rules WHERE kind = 'frag_repair'").fetchone()[0] >= 1


def test_new_pick_is_client():
    """24.10 (Артём): подборка всегда для клиента — имя создаёт клиента и его подборку; то же имя ещё раз — та же подборка."""
    from app import accounts, db, picks
    conn = db.connect(":memory:")
    u = accounts.upsert_email_user(conn, "a@x.ru")
    a = picks.for_client_name(conn, u, "Жора")
    assert a["made_client"] and a["client_name"] == "Жора"
    b = picks.for_client_name(conn, u, "  жора ")
    assert b["id"] == a["id"] and not b["made_client"]
    assert conn.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (u["id"],)).fetchone()[0] == 1
    assert "error" in picks.for_client_name(conn, u, "  ")

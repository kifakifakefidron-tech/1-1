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

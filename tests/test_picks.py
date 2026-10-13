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
        other_phone.get(f"/api/c/{p['token']}", headers=phone_ua)                                    # другое устройство
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

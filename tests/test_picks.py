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
    assert "918" not in str(pub) and "phones" not in pub["items"][0]   # чужих телефонов нет
    page = c.get(f"/c/{p['token']}")
    assert page.status_code == 200 and 'og:title" content="Для Ольги"' in page.text
    assert conn.execute("SELECT views FROM collections").fetchone()[0] == 1
    c.request("DELETE", "/api/picks", json={"id": p["id"]})
    assert c.get(f"/api/c/{p['token']}").status_code == 404


def test_picks_need_subscription(env):
    c, conn, lid = env
    _login(c, conn, paid=False)
    assert c.post("/api/picks", json={"title": "x"}).status_code == 403

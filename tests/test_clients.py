"""CRM агента: клиенты по этапам, объекты клиенту, история, подбор, связь с планером. Чужие клиенты недоступны."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, ingest, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    other = accounts.upsert_email_user(conn, "other@example.ru")
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn, other
    conn.close()
    db._local.conn = None


def add(conn, text, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"cl{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def test_client_flow(env):
    c, conn, other = env
    a = add(conn, "2-к квартира 58 м², 7/16 эт., ФМР, ул. Ставропольская 10. 7,5 млн руб. 89181112233")
    add(conn, "1-к квартира 40 м², 3/9 эт., ЦМР, ул. Красная 5. 5 млн. 89184445566")
    r = c.post("/api/clients", json={"name": "Ирина", "phone": "8 918 000-11-22", "kind": "buyer", "rooms": [2],
                                     "districts": ["фмр"], "budget_max": "8"}).json()
    cid = r["client"]["id"]
    assert r["client"]["phone"] == "+79180001122" and r["client"]["districts"] == ["ФМР"] and r["client"]["budget_max"] == 8_000_000
    # подбор по пожеланиям: 2-к в ФМР до 8 млн
    m = c.get(f"/api/clients/{cid}/match").json()
    assert [x["id"] for x in m["items"]] == [a]
    assert c.post(f"/api/clients/{cid}/items", json={"listing_id": a, "status": "liked"}).json()["ok"]
    assert c.post("/api/clients", json={"id": cid, "stage": "shows"}).json()["ok"]
    assert c.post(f"/api/clients/{cid}/note", json={"text": "Хочет высокий этаж"}).json()["ok"]
    ev = c.post("/api/planner", json={"kind": "show", "ts": int(time.time()) + 3600, "listing_id": a, "client_id": cid}).json()
    assert ev["event"]["client_id"] == cid
    d = c.get(f"/api/clients?id={cid}").json()
    assert d["items"][0]["status"] == "liked" and len(d["events"]) == 1
    texts = " | ".join(x["text"] for x in d["log"])
    assert "Понравился" in texts and "Показы" in texts and "высокий этаж" in texts and "Показ" in texts
    assert c.get(f"/api/clients/{cid}/match").json()["items"] == []          # уже у клиента — не предлагаем снова
    assert c.get(f"/api/clients?listing={a}").json()["items"][0]["name"] == "Ирина"
    assert c.get("/api/dashboard").json()["clients"]["total"] == 1
    # чужой агент клиента не видит
    c.cookies.set(server.SESSION, accounts.create_session(conn, other["id"]))
    assert c.get(f"/api/clients?id={cid}").status_code == 404
    assert c.get("/api/clients").json()["items"] == []
    assert c.post(f"/api/clients/{cid}/items", json={"listing_id": a, "status": "liked"}).status_code == 400


def test_client_needs_name_and_page(env):
    c, _, _ = env
    assert c.post("/api/clients", json={"kind": "seller"}).status_code == 400
    assert c.get("/clients").status_code == 200

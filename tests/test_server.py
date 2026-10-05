"""API сайта: код доступа скрывает телефоны агентов от покупателей."""
import pytest
from starlette.testclient import TestClient

from app import config, db, ingest, server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "секрет")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    mid = ingest.add_message(conn, source="manual",
                             text="Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233")
    ingest.process_message(conn, mid, use_llm=False)
    with TestClient(server.app) as c:
        yield c
    conn.close()
    db._local.conn = None


def test_page_and_static(client):
    assert "1+1" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_public_sees_no_phones_until_login(client):
    items = client.get("/api/listings").json()["items"]
    assert len(items) == 1 and "phones" not in items[0]
    lid = items[0]["id"]
    assert "phones" not in client.get(f"/api/listings/{lid}").json()

    assert client.post("/api/login", json={"code": "не тот"}).status_code == 403
    assert client.post("/api/login", json={"code": "секрет"}).json() == {"ok": True}

    d = client.get(f"/api/listings/{lid}").json()
    assert d["phones"] == ["+79181112233"]
    assert client.get("/api/meta").json()["access"] is True


def test_facets_and_missing_listing(client):
    f = client.get("/api/facets").json()
    assert any("Мозаика" in c["name"] for c in f["complexes"])
    assert client.get("/api/listings/99999").status_code == 404


def test_manual_add_needs_code(client):
    assert client.post("/api/messages", json={"text": "Студия 24 м2, 3 200 000 руб."}).status_code == 403

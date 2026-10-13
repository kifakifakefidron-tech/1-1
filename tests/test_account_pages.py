"""13.10: настройки аккаунта, подборки на нейтральном адресе, запросы покупателей — только админу,
счётчики в меню, перенос события в планере, «В работе»."""
import base64
import time

import pytest
from starlette.testclient import TestClient

from app import config, db, ingest, server

PNG = "data:image/png;base64," + base64.b64encode(bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 2000).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "секрет")
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    conn = db.get()
    mid = ingest.add_message(conn, source="manual",
                             text="Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233")
    ingest.process_message(conn, mid, use_llm=False)
    with TestClient(server.app) as c:
        c.post("/api/login", json={"code": "секрет"})
        yield c
    conn.close()
    db._local.conn = None


def test_settings_save_and_validate(client):
    s = client.get("/api/settings").json()["settings"]
    assert s["pick_color"] == "gold" and s["cabinet_order"] == []
    r = client.post("/api/settings", json={"pick_color": "violet", "pick_brand": "Агентство «Дом»", "pick_phone": "8 918 111-22-33",
                                           "pick_cover": "https://evil.example/x.jpg", "cabinet_order": ["wFx", "wDay", "<bad>"]}).json()
    st = r["settings"]
    assert st["pick_color"] == "violet" and st["pick_brand"] == "Агентство «Дом»" and st["pick_phone"] == "+79181112233"
    assert st["pick_cover"] == ""                       # чужую ссылку как обложку не принимаем
    assert st["cabinet_order"] == ["wFx", "wDay"]
    assert client.post("/api/settings", json={"pick_color": "#12ab34"}).json()["settings"]["pick_color"] == "#12ab34"
    cover = client.post("/api/settings", json={"cover": PNG}).json()["settings"]["pick_cover"]
    assert cover.startswith("/photos/0/") and client.get(cover).status_code == 200


def test_pick_page_neutral_and_pick_domain(client, monkeypatch):
    lid = client.get("/api/listings").json()["items"][0]["id"]
    client.post("/api/settings", json={"pick_color": "sky", "pick_brand": "Моё агентство"})
    p = client.post("/api/picks", json={"title": "Для Анны", "listing_id": lid}).json()["pick"]
    page = client.get(f"/c/{p['token']}").text
    assert "1+1" not in page and "Для Анны" in page
    pub = client.get(f"/api/c/{p['token']}").json()
    assert pub["style"] == {"color": "sky", "cover": "", "brand": "Моё агентство"}
    # нейтральный адрес: с основного — переадресация, ссылка в управлении — на него
    monkeypatch.setattr(config, "PICK_DOMAIN", "podborka.example")
    monkeypatch.setattr(config, "PICK_URL", "https://podborka.example")
    r = client.get(f"/c/{p['token']}", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == f"https://podborka.example/c/{p['token']}"
    assert client.get(f"/c/{p['token']}", headers={"host": "podborka.example"}).status_code == 200
    assert client.get("/api/picks").json()["items"][0]["url"] == f"https://podborka.example/c/{p['token']}"


def test_buyer_requests_admin_only(client):
    assert client.get("/api/requests").status_code == 403
    assert client.get("/requests", follow_redirects=False).status_code == 303


def test_counts_and_planner_move(client):
    lid = client.get("/api/listings").json()["items"][0]["id"]
    client.post(f"/api/notes/{lid}", json={"status": "show"})
    now = int(time.time())
    ev = client.post("/api/planner", json={"kind": "call", "title": "Перезвонить", "ts": now - 3 * 86400, "color": "rose"}).json()["event"]
    assert ev["color"] == "rose"
    c = client.get("/api/me").json()["me"]["counts"]
    assert c["work"] == 1 and c["plan_overdue"] == 1
    assert client.post("/api/planner", json={"id": ev["id"], "ts": now + 3600}).status_code == 200   # перетащили на сегодня
    c = client.get("/api/me").json()["me"]["counts"]
    assert c["plan_overdue"] == 0
    stats = client.get(f"/api/planner?from={now - 86400}&to={now + 86400}").json()["stats"]
    assert stats["by_kind"] == {"call": 1}


def test_new_pages_open(client):
    for path in ("/work", "/my", "/my/edit", "/settings", "/planner"):
        assert client.get(path).status_code == 200, path
    fav = client.get("/api/favorites").json()["items"]
    assert fav and fav[0]["status"] == "show" if fav else True


def test_hide_listing_for_me_only(client):
    lid = client.get("/api/listings").json()["items"][0]["id"]
    assert client.post("/api/hidden", json={"id": lid, "hide": True, "reason": "sold"}).status_code == 200
    assert client.get("/api/listings").json()["total"] == 0                     # у меня — пропал
    assert client.get("/api/hidden").json()["items"][0]["reason"] == "sold"
    with TestClient(server.app) as other:                                        # у другого — виден
        assert other.get("/api/listings").json()["total"] == 1
    client.post("/api/hidden", json={"id": lid, "hide": False})
    assert client.get("/api/listings").json()["total"] == 1

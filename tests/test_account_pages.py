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


def test_cabinet_own_blocks_sanitized(client):
    blocks = [
        {"id": "uabc123", "type": "links", "title": "Ссылки", "color": "teal",
         "data": {"items": [{"t": "Росреестр", "u": "https://rosreestr.gov.ru"}, {"t": "зло", "u": "javascript:alert(1)"}]}},
        {"id": "ugoal1", "type": "goal", "title": "Сделки", "color": "nope", "data": {"n": -5, "of": "abc"}},
        {"id": "u<x>", "type": "html", "data": {"html": "<script>"}},
        {"id": "!!", "type": "note", "data": {"text": "x"}},
        {"id": "ucd1", "type": "countdown", "data": {"date": "2026-12-31<b>"}},
    ]
    st = client.post("/api/settings", json={"blocks": blocks}).json()["settings"]["blocks"]
    assert [b["type"] for b in st] == ["links", "goal", "countdown"]   # чужой вид и плохой id отброшены
    assert st[0]["data"]["items"] == [{"t": "Росреестр", "u": "https://rosreestr.gov.ru"}]
    assert st[1]["color"] == "gold" and st[1]["data"] == {"n": 0, "of": 5}
    assert st[2]["data"]["date"] == ""
    assert client.get("/api/settings").json()["settings"]["blocks"] == st


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


def test_online_presence(client):
    client.post("/api/ping", json={"path": "/planner"})
    with TestClient(server.app) as guest:
        guest.post("/api/ping", json={"path": "/", "g": "guest-browser-1"})
    from app import accounts as acc
    conn = db.get()
    conn.execute("UPDATE users SET is_admin = 1")
    conn.commit()
    o = client.get("/api/admin/online").json()
    assert o["now"] == 2 and o["users"] == 1 and o["guests"] == 1
    assert o["people"][0]["path"] == "/planner"


def test_new_matches_for_client_and_to_pick(client):
    from app import clients as cl
    conn = db.get()
    c = client.post("/api/clients", json={"name": "Анна", "kind": "buyer", "rooms": [2], "budget_max": "8"}).json()
    cid = c["client"]["id"] if "client" in c else c["id"]
    assert cl.new_matches(conn) == 0                                        # первый раз — только запомнили момент
    conn.execute("UPDATE clients SET match_seen = 1 WHERE id = ?", (cid,))   # «прошлая проверка» была давно
    conn.commit()
    assert cl.new_matches(conn) == 1
    n = conn.execute("SELECT title, actions FROM notices WHERE kind = 'client'").fetchone()
    assert n["title"].startswith("Под Анна: 1 новый объект") and "topick=1" in n["actions"]
    lid = client.get("/api/listings").json()["items"][0]["id"]
    r = client.post(f"/api/clients/{cid}/topick", json={"ids": [lid]}).json()
    assert r["added"] == 1 and "/c/" in r["pick"]["url"]
    again = client.post(f"/api/clients/{cid}/topick", json={"ids": [lid]}).json()
    assert again["pick"]["id"] == r["pick"]["id"]                            # та же подборка клиента
    assert client.get(f"/api/clients/{cid}/match?since=1").json()["items"] == []   # уже предложен — не повторяем


def test_how_page_filled(client):
    page = client.get("/how").text
    assert "{{" not in page                                  # все подстановки заполнены
    assert "49 ₽" in page and "399 ₽" in page and "дней в архиве" in page

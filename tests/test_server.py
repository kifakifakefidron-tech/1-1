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
    r = client.post("/api/login", json={"code": "секрет"}).json()
    assert r["ok"] and r["me"]["access"]  # вход по коду — обычный аккаунт с кабинетом

    d = client.get(f"/api/listings/{lid}").json()
    assert d["phones"] == ["+79181112233"]
    assert client.get("/api/meta").json()["access"] is True


def test_facets_and_missing_listing(client):
    f = client.get("/api/facets").json()
    assert any("Мозаика" in c["name"] for c in f["complexes"])
    assert client.get("/api/listings/99999").status_code == 404


def test_manual_add_needs_code(client):
    assert client.post("/api/messages", json={"text": "Студия 24 м2, 3 200 000 руб."}).status_code == 403


def test_promo_login_has_cabinet_and_logout(client):
    me = client.post("/api/login", json={"code": "секрет"}).json()["me"]
    lid = client.get("/api/listings").json()["items"][0]["id"]
    assert client.post(f"/api/favorites/{lid}").json() == {"favorite": True}
    assert client.get("/api/me").json()["me"]["favorites"] == [lid]
    client.post("/api/auth/logout")
    assert client.get("/api/me").json()["me"] is None
    assert "phones" not in client.get(f"/api/listings/{lid}").json()


def test_since_counts_only_new(client):
    import time
    total = client.get("/api/listings").json()["total"]
    assert client.get(f"/api/listings?since={int(time.time()) - 3600}").json()["total"] == total
    assert client.get(f"/api/listings?since={int(time.time()) + 60}").json()["total"] == 0


def test_info_pages(client):
    for path, word in (("/how", "Как работает 1+1"), ("/privacy", "152-ФЗ"), ("/terms", "Пользовательское соглашение")):
        r = client.get(path)
        assert r.status_code == 200 and word in r.text and "{{" not in r.text


def test_map_reports_points_and_missing(tmp_path, monkeypatch):
    from app import db, geocode, ingest, search
    conn = db.connect(":memory:")
    for i, t in enumerate(["2-к квартира 50 м², 3/9 эт., ул. Красная 10. 6 млн. 89180000001",
                           "Студия 25 м², 3/9 эт., ФМР. 3 млн. 89180000002",
                           "1-к квартира 38 м², 2/5 эт. 4 млн. 89180000003"]):
        ingest.process_message(conn, ingest.add_message(conn, source="manual", text=t, msg_id=str(i)), use_llm=False)
    monkeypatch.setattr(geocode.config, "GEOCODER", "nominatim")
    asked = []
    def fake(c, q):
        asked.append(q)
        return (45.0, 39.0) if "Красная 10" in q or q.startswith("микрорайон ФМР") else None
    monkeypatch.setattr(geocode, "lookup", fake)
    geocode.run(conn)
    res = search.map_points(conn, search.Query(), 0)
    assert res["total"] == 2 and len(res["points"]) == 2   # без адреса/района объект не показывается вовсе
    assert sorted(p["approx"] for p in res["points"]) == [0, 1]   # ФМР — примерная точка по району


def test_geocoder_rejects_points_outside_krasnodar(monkeypatch):
    import json as _json

    from app import db, geocode
    conn = db.connect(":memory:")

    class R:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return _json.dumps(self.data).encode()

    sochi = [{"lat": "43.58", "lon": "39.72"}]
    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda req, timeout: R(sochi))
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    assert geocode.lookup(conn, "Краснодар, Ленина 5") is None          # в Сочи — не наш адрес
    krd = [{"lat": "45.04", "lon": "38.98"}]
    monkeypatch.setattr(geocode.urllib.request, "urlopen", lambda req, timeout: R(krd))
    assert geocode.lookup(conn, "Краснодар, Красная 10") == (45.04, 38.98)

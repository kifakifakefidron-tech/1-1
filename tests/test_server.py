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

    assert client.post("/api/login", json={"consent": True, "terms": True, "agent": True, "code": "не тот"}).status_code == 403
    r = client.post("/api/login", json={"consent": True, "terms": True, "agent": True, "code": "секрет"}).json()
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
    me = client.post("/api/login", json={"consent": True, "terms": True, "agent": True, "code": "секрет"}).json()["me"]
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
    for path, word in (("/how", "Все объекты из риелторских чатов"), ("/privacy", "152-ФЗ"), ("/terms", "Пользовательское соглашение")):
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
    assert res["total"] == 2   # без адреса/района объект не показывается вовсе
    # ТЗ «админка-гео» P2.1: центр района — не метка на карте («адрес уточняется», объект — в списке)
    assert len(res["points"]) == 1 and res["points"][0]["level"] == "house" and res["approx_hidden"] == 1


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


def test_admin_moves_point_and_it_sticks(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import accounts, config, db, geocode, ingest, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    text = "2-к квартира 50 м², 3/9 эт., ул. Красная 10. 6 млн. 89180000001"
    lid = ingest.process_message(conn, ingest.add_message(conn, source="manual", text=text), use_llm=False)["results"][0]["listing_id"]
    u = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        assert c.post(f"/api/admin/listings/{lid}/geo", json={"lat": 45.05, "lon": 38.99}).status_code == 403
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        assert c.post(f"/api/admin/listings/{lid}/geo", json={"lat": 55.7, "lon": 37.6}).status_code == 400  # Москва
        assert c.post(f"/api/admin/listings/{lid}/geo", json={"lat": 45.05, "lon": 38.99}).json()["geo_status"] == "manual"
        assert c.get(f"/api/listings/{lid}").json()["geo_status"] == "manual"
    # повтор объявления с изменённым адресом и пересчёт точек не трогают ручную точку
    ingest.process_message(conn, ingest.add_message(conn, source="manual", text=text + " дом 12", msg_id="2"), use_llm=False)
    geocode.fix_wrong_points(conn)
    assert tuple(conn.execute("SELECT lat, lon, geo_status FROM listings WHERE id = ?", (lid,)).fetchone()) == (45.05, 38.99, "manual")
    conn.close()
    db._local.conn = None


def test_admin_point_teaches_other_and_future_listings(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import accounts, config, db, geocode, ingest, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "GEOCODER", "nominatim")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    asked = []
    monkeypatch.setattr(geocode, "lookup", lambda c, q: asked.append(q) or (44.99, 38.94))   # геокодер «ошибается»
    conn = db.get()

    def add(text, mid):
        r = ingest.process_message(conn, ingest.add_message(conn, source="manual", text=text, msg_id=mid), use_llm=False)
        return r["results"][0]["listing_id"]
    a = add("2-к квартира 50 м², 3/9 эт., ЖК Мозаика, ул. Тургенева 10. 6 млн. 89180000001", "1")
    b = add("1-к квартира 38 м², 5/9 эт., ул. Тургенева 10. 4 млн. 89180000002", "2")       # тот же дом
    geocode.run(conn)
    u = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        res = c.post(f"/api/admin/listings/{a}/geo", json={"lat": 45.05, "lon": 38.99}).json()
    assert res["applied"] == 1 and "Тургенева, 10" in res["learned"]
    assert tuple(conn.execute("SELECT lat, lon, geo_status FROM listings WHERE id = ?", (b,)).fetchone()) == (45.05, 38.99, "learned")
    # новое объявление по тому же дому — сразу на выученную точку, без геокодера
    asked.clear()
    c2 = add("Студия 25 м², 2/9 эт., ул. Тургенева, д. 10. 3 млн. 89180000003", "3")
    geocode.run(conn)
    assert tuple(conn.execute("SELECT lat, lon, geo_status FROM listings WHERE id = ?", (c2,)).fetchone()) == (45.05, 38.99, "learned")
    assert asked == []
    conn.close()
    db._local.conn = None


def test_email_code_limit_is_per_address_not_shared(client, monkeypatch):
    """За Docker все посетители видны с одного адреса — лимит кодов должен быть на почту, а не общий на всех."""
    from app import mailer
    monkeypatch.setattr(mailer, "available", lambda: True)
    monkeypatch.setattr(mailer, "send_code", lambda to, code: True)
    for i in range(8):   # восемь разных людей подряд — всем приходит код
        assert client.post("/api/auth/email/start", json={"consent": True, "terms": True, "agent": True, "email": f"user{i}@example.ru"}).status_code == 200
    import time as _t
    from app import db
    db.get().execute("UPDATE email_codes SET created = created - 120")
    db.get().commit()
    codes = [client.post("/api/auth/email/start", json={"consent": True, "terms": True, "agent": True, "email": "user0@example.ru"}).status_code for _ in range(6)]
    assert 429 in codes   # один адрес — не больше 5 кодов в час


def test_guest_limits_1810():
    """18.10 (приёмка п. 8): гость — первые 5 страниц и карта кружками без области; приблизил — точки только в области."""
    import importlib
    from app import server
    importlib.reload(server) if False else None
    from starlette.testclient import TestClient
    c = TestClient(server.app)
    r = c.get("/api/listings?page=6").json()
    assert r.get("guest_limit") and r["items"] == []
    m = c.get("/api/map").json()
    assert m.get("guest") and m["points"] == [] and "agg" in m


def test_watch_house_filter_1810():
    """19.10: «следить за этим домом» — фильтр улица + дом (регистр буквы дома не важен) и название подписки."""
    from app import hooks, search
    from app.rules import TYPE_LABELS
    q = search.Query(street="Красная", house="10б")
    w, p = search._where(q, 0)
    assert "l.street = ?" in w and "10Б" in p and "10б" in p
    assert "ул. Красная, 10Б" in hooks.describe("deal=sale&street=Красная&house=10Б", TYPE_LABELS)


def test_admin_users_list_all_with_last_visit(tmp_path, monkeypatch):
    """24.10: админка «Пользователи» — все аккаунты, последний заход, сводка, сортировка по заходу."""
    from starlette.testclient import TestClient

    from app import accounts, config, db, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    boss = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (boss["id"],))
    for i in range(3):
        u = accounts.upsert_email_user(conn, f"agent{i}@example.ru")
        conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (1000 + i, u["id"]))
    conn.commit()
    with TestClient(server.app) as c:
        assert c.get("/api/admin/users").status_code == 403
        c.cookies.set(server.SESSION, accounts.create_session(conn, boss["id"]))
        d = c.get("/api/admin/users?sort=last").json()
        assert d["summary"]["total"] == 4 and d["total"] == 4
        assert all("last_seen" in x and "n_clients" in x and "login" in x for x in d["items"])
        emails = [x["email"] for x in d["items"]]
        assert emails.index("agent2@example.ru") < emails.index("agent0@example.ru")   # недавно заходившие — выше


def test_ping_counts_guests_without_cookie_consent(tmp_path, monkeypatch):
    """27.10: гостя без согласия на cookie тоже считаем — анонимной меткой дня; роботов — нет."""
    from starlette.testclient import TestClient
    from app import config, db, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "v.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    with TestClient(server.app) as c:
        h = {"user-agent": "Mozilla/5.0 (iPhone) Mobile"}
        c.post("/api/ping", json={"path": "/"}, headers=h)            # гость без согласия (без метки браузера)
        c.post("/api/ping", json={"path": "/how"}, headers=h)         # тот же гость — то же посещение
        c.post("/api/ping", json={"path": "/"}, headers={"user-agent": "TelegramBot (like TwitterBot)"})   # робот
    rows = conn.execute("SELECT key, user_id, mobile, pages FROM visits_daily").fetchall()
    assert len(rows) == 1 and rows[0]["user_id"] is None and rows[0]["mobile"] == 1 and rows[0]["pages"] == 2
    assert conn.execute("SELECT COUNT(*) FROM presence").fetchone()[0] == 1
    o = server._online(conn)
    assert o["guests"] == 1 and o["days"][0]["total"] == 1
    conn.close()
    db._local.conn = None

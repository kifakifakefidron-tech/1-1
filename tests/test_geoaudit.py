"""ТЗ «админка-гео» P0.0: правило против карты районов находится; отключение перерешает объекты по контуру под домом,
пишет журнал правок, каждую правку можно откатить по объекту. ЖК-мусор не принимается."""
import json
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, districtmap, geoaudit, ingest, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    districtmap._cache["t"] = 0
    conn = db.get()
    u = accounts.upsert_email_user(conn, "admin@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None
    districtmap._cache["t"] = 0


def poly(conn, name, lat0, lat1, lon0, lon1):
    ring = [[lat0, lon0], [lat0, lon1], [lat1, lon1], [lat1, lon0]]
    conn.execute("""INSERT INTO district_polygons (name, kind, source, polys, area, lat_min, lat_max, lon_min, lon_max, ts)
                    VALUES (?, 'district', 'admin', ?, ?, ?, ?, ?, ?, 0)""",
                 (name, json.dumps([ring]), (lat1 - lat0) * (lon1 - lon0), lat0, lat1, lon0, lon1))


def add(conn, text, lat, lon, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"ga{n[0]}", ts=int(time.time()))
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = 'ok', geo_src = 'osm-house' WHERE id = ?", (lat, lon, lid))
    return lid


def test_bad_street_rule_found_and_disabled_with_journal(env):
    c, conn = env
    poly(conn, "ФМР", 45.00, 45.02, 38.90, 38.95)
    poly(conn, "ЦМР", 45.02, 45.04, 38.90, 38.95)
    conn.commit()
    ids = [add(conn, f"2-к квартира 5{i} м², {i + 1}/9 эт., ул. Тестовая {10 + i}. 6 млн. 8918000010{i}", 45.03, 38.92) for i in range(4)]
    # ошибочное правило: «Тестовая → ФМР», хотя все дома по карте в ЦМР
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('street', ?, ?, 'ул. Тестовая → ФМР', 3, 0)""",
                 ("street:|тестовая", json.dumps({"district": "ФМР"})))
    conn.execute("UPDATE listings SET district = 'ФМР'")
    conn.commit()
    rep = c.get("/api/admin/geo-audit").json()
    bad = [r for r in rep["rules"] if r["status"] == "bad"]
    assert len(bad) == 1 and bad[0]["dist"] == {"ЦМР": 4} and bad[0]["share"] == 0
    res = c.post("/api/admin/geo-audit", json={"rules": [bad[0]["id"]]}).json()
    assert res["rules"] == 1 and res["listings"] == 4
    assert {r[0] for r in conn.execute("SELECT district FROM listings")} == {"ЦМР"}
    hist = c.get(f"/api/admin/edits?listing={ids[0]}").json()["items"]
    assert hist[0]["field"] == "district" and hist[0]["old"] == "ФМР" and hist[0]["new"] == "ЦМР" and hist[0]["who"] == "чистка правил"
    assert c.post("/api/admin/edits", json={"id": hist[0]["id"]}).json()["ok"]
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (ids[0],)).fetchone()[0] == "ФМР"


def test_junk_complex_not_accepted():
    assert geoaudit.is_junk_complex("ЖК Краснодар") and geoaudit.is_junk_complex("жилой квартал")
    assert not geoaudit.is_junk_complex("Самолёт 2")

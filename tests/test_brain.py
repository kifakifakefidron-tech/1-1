"""«Обучение»: единая очередь, спорные объекты, сводка и всё выученное одним списком."""
import pytest
from starlette.testclient import TestClient

from app import accounts, autolearn, brain, config, db, districtmap, ingest, learning, server

KAT = [[(45.00, 38.90), (45.00, 38.92), (45.02, 38.92), (45.02, 38.90)]]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    districtmap._cache.update(t=0.0, polys=[])
    conn = db.get()
    u = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None
    districtmap._cache.update(t=0.0, polys=[])


def add(conn, text, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"b{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def test_old_links_map_to_one_queue():
    assert brain.queue_key("geo", "none") == "nogeo" and brain.queue_key("geo", "pending") == "nogeo"
    assert brain.queue_key("place", "nodistrict") == "nodistrict" and brain.queue_key(None, "conflict") == "conflict"


def test_rule_vs_map_conflict_goes_to_queue_and_admin_resolves(env):
    c, conn = env
    districtmap.save_polygon(conn, "Катюша", "district", KAT)
    lid = add(conn, "2-к квартира 50 м², 3/9 эт., ФМР, ЖК Мозаика. 6 млн. 89180000071")
    conn.execute("UPDATE listings SET lat = 45.01, lon = 38.91, geo_status = 'ok', geo_src = 'osm-house', district = 'ФМР' WHERE id = ?",
                 (lid,))
    conn.commit()
    learning.learn(conn, conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone(), "Мозаика", "ФМР",
                   {"cx_district": True})
    autolearn.recheck_all(conn)
    assert brain.counts(conn)["conflict"] == 1
    d = c.get("/api/admin/fix?kind=conflict").json()
    assert d["item"]["id"] == lid and "Катюша" in d["item"]["conflict"] and d["counts"]["conflict"] == 1
    assert [q["key"] for q in d["queues"]][0] == "conflict"
    # вы решили — объект уходит из спорных
    assert c.post(f"/api/admin/listings/{lid}/place", json={"complex": "Мозаика", "district": "Катюша"}).json()["ok"]
    assert brain.counts(conn)["conflict"] == 0


def test_learn_overview_and_knowledge(env):
    c, conn = env
    lid = add(conn, "2-к квартира 58 м², 7/16 эт., ул. Ставропольская 10. 7,5 млн руб. 89181112233")
    c.post(f"/api/admin/listings/{lid}/place", json={"district": "ЦМР", "learn": {"addr": True, "street": True}})
    c.post(f"/api/admin/listings/{lid}/geo", json={"lat": 45.03, "lon": 38.97})
    ov = c.get("/api/admin/learn").json()
    assert ov["total"] >= 1 and ov["knowledge"]["rules"] >= 2 and ov["knowledge"]["points"] >= 1
    assert [s["key"] for s in ov["pipeline"]["district"]][:3] == ["yours", "rules", "map"]
    items = c.get("/api/admin/learn/knowledge").json()["items"]
    assert {x["type"] for x in items} == {"rule", "point"}
    found = c.get("/api/admin/learn/knowledge?q=ставроп").json()["items"]
    assert found and all("Ставроп" in x["label"] for x in found)
    rule = next(x for x in items if x["type"] == "rule")
    assert c.post("/api/admin/learn/knowledge", json={"type": "rule", "id": rule["id"]}).json()["ok"]
    point = next(x for x in items if x["type"] == "point")
    assert c.post("/api/admin/learn/knowledge", json={"type": "point", "id": point["id"]}).json()["ok"]
    left = c.get("/api/admin/learn/knowledge").json()["items"]
    assert len(left) == len(items) - 2
    assert c.get("/learn").status_code == 200

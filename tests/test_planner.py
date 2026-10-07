"""Планер: показы и созвоны, напоминания, календарь телефона (.ics), свои заметки."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, ingest, planner, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    mid = ingest.add_message(conn, source="manual", text="2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233")
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    user = accounts.upsert_email_user(conn, "plan@example.ru")
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, user["id"]))
        yield c, conn, user, lid
    conn.close()
    db._local.conn = None


def test_event_from_listing_goes_to_favorites_and_reminds(env):
    c, conn, user, lid = env
    ts = int(time.time()) + 30 * 60
    r = c.post("/api/planner", json={"kind": "show", "ts": ts, "listing_id": lid, "contact_phone": "8 918 111-22-33",
                                     "remind_min": 60, "note": "ключи у консьержа"})
    ev = r.json()["event"]
    assert ev["contact_phone"] == "+79181112233" and ev["listing"]["id"] == lid
    assert lid in accounts.me(conn, accounts.get_user(conn, user["id"]))["favorites"]   # объект — в избранном
    assert c.get(f"/api/listings/{lid}").json()["plans"][0]["id"] == ev["id"]
    sent = []
    assert planner.remind(conn, lambda to, subj, body: sent.append(subj)) == 1
    assert planner.remind(conn) == 0   # второй раз не напоминаем
    assert sent and "Показ" in sent[0]
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE kind = 'plan'").fetchone()[0] == 1
    # перенесли время — напомним заново
    c.post("/api/planner", json={**ev, "ts": ts + 600})
    assert planner.remind(conn) == 1
    # готово / удалить
    c.post("/api/planner", json={"id": ev["id"], "done": True})
    ov = c.get("/api/planner").json()
    assert ov["upcoming"][0]["done"] == 1 and ov["favorites"][0]["id"] == lid
    c.request("DELETE", "/api/planner", json={"id": ev["id"]})
    assert c.get("/api/planner").json()["upcoming"] == []


def test_ics_feed_and_single(env):
    c, conn, user, lid = env
    ev = c.post("/api/planner", json={"kind": "call", "ts": int(time.time()) + 86400, "title": "Позвонить Ольге, ЖК Мозаика"}).json()["event"]
    one = c.get(f"/api/planner/{ev['id']}.ics")
    assert one.status_code == 200 and "BEGIN:VEVENT" in one.text and "TRIGGER:-PT60M" in one.text
    feed = c.get(c.get("/api/planner").json()["feed"])
    assert feed.status_code == 200 and r"Позвонить Ольге\, ЖК Мозаика" in feed.text
    assert c.get(f"/planner/{user['id']}-wrongtoken.ics").status_code == 404


def test_planner_notes(env):
    c, conn, user, lid = env
    n = c.post("/api/planner/notes", json={"text": "Купить ключницу", "pinned": True}).json()["note"]
    assert c.get("/api/planner").json()["notes"][0]["text"] == "Купить ключницу"
    c.post("/api/planner/notes", json={"id": n["id"], "text": ""})   # пустой текст — удалить
    assert c.get("/api/planner").json()["notes"] == []
    assert c.post("/api/planner", json={"kind": "show", "ts": "завтра"}).status_code == 400

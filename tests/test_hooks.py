"""Фишки: подписка на поиск с письмом, снижение цены в избранном, «продают ещё N агентов»,
цена к рынку, заметки, «Новое сегодня»."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, hooks, ingest, search, server

NOW = int(time.time())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    hooks._market["t"] = 0
    conn = db.get()
    with TestClient(server.app) as c:
        yield c, conn
    conn.close()
    db._local.conn = None


def _add(conn, text, ts=None, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, ts=ts or NOW, chat_id=f"c{n[0]}", msg_id=f"m{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def _login(c, conn, email="u@example.ru"):
    u = accounts.upsert_email_user(conn, email)
    c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
    return u


def test_saved_search_sends_new_objects_once(env):
    c, conn = env
    _login(c, conn)
    r = c.post("/api/saved", json={"query": "deal=sale&rooms=2&page=3&view=map"})
    assert r.status_code == 200 and r.json()["saved"]["title"] == "Продажа · 2 комн."
    assert c.post("/api/saved", json={"query": "rooms=2&deal=sale"}).status_code == 400   # уже следим
    conn.execute("UPDATE saved_searches SET checked_at = ?", (NOW - 100,))
    conn.commit()
    _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001", ts=NOW)
    _add(conn, "1-к квартира 35 м², 3/9 эт. 4 млн руб. 89180000002", ts=NOW)
    mails = []
    assert hooks.check_saved(conn, lambda to, subj, body: mails.append(body) or True) == 1
    assert "2-к квартира" in mails[0] and "1-к" not in mails[0] and "/saved/off?s=" in mails[0]
    assert hooks.check_saved(conn, lambda *a: mails.append(a) or True) == 0   # не чаще раза в 3 часа
    sid = hooks.list_saved(conn, 1)[0]["id"]
    assert "больше не придут" in c.get(f"/saved/off?s={sid}&t={hooks.unsub_token(sid)}").text
    assert hooks.list_saved(conn, 1) == []


def test_price_drop_in_favorites(env):
    c, conn = env
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001", ts=NOW - 100)
    _login(c, conn)
    c.post(f"/api/favorites/{lid}")
    _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 5,7 млн руб. 89180000001", ts=NOW)
    row = conn.execute("SELECT price, prev_price FROM listings WHERE id = ?", (lid,)).fetchone()
    assert (row["price"], row["prev_price"]) == (5_700_000, 6_000_000)
    mails = []
    assert hooks.check_price_drops(conn, lambda to, subj, body: mails.append(body) or True) == 1
    assert "6 млн ₽ → 5,7 млн ₽" in mails[0]
    assert hooks.check_price_drops(conn, lambda *a: mails.append(a) or True) == 0


def test_same_object_other_agents_and_market(env):
    c, conn = env
    a = _add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика. 6 млн руб. 89180000001")
    _add(conn, "2-к квартира 50,5 м², 3/9 эт., ЖК Мозаика. 6,2 млн руб. 89180000002")
    _add(conn, "2-к квартира 50 м², 7/9 эт., ЖК Мозаика. 7 млн руб. 89180000003")   # другой этаж
    d = c.get(f"/api/listings/{a}").json()
    assert [s["price"] for s in d["same"]] == [6_200_000]
    assert d["market"]["base"] == "ЖК Мозаика" and d["market"]["n"] == 3


def test_notes_private(env):
    c, conn = env
    lid = _add(conn, "2-к квартира 50 м², 3/9 эт. 6 млн руб. 89180000001")
    _login(c, conn)
    c.post(f"/api/notes/{lid}", json={"text": "Звонил, ждут показ в субботу"})
    assert c.get(f"/api/listings/{lid}").json()["note"] == "Звонил, ждут показ в субботу"
    c.cookies.clear()
    _login(c, conn, "other@example.ru")
    assert c.get(f"/api/listings/{lid}").json()["note"] == ""


def test_new_today_filter(env):
    c, conn = env
    _add(conn, "2-к квартира 50 м², 3/9 эт., ФМР. 6 млн руб. 89180000001", ts=NOW - 5 * 86400)
    _add(conn, "1-к квартира 35 м², 3/9 эт., ФМР. 4 млн руб. 89180000002", ts=NOW)
    assert search.search(conn, search.Query(new_days=1), NOW, False)["total"] == 1

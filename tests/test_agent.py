"""Кабинет агента: подтверждение номера кодом из мессенджера, «Мои объявления», правка только своего,
свой объект на 30 дней с письмом «актуален?», журнал правок с откатом, фото."""
import base64
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, agent, bot, config, db, ingest, server, wappi

AD = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 2000


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setattr(config, "ACCESS_CODE", "")
    monkeypatch.setattr(config, "VERIFY_NUMBER", "+79990000000")
    monkeypatch.setattr(config, "VERIFY_CHANNELS", ["max", "wa"])
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    monkeypatch.setattr(bot, "say", lambda *a, **k: None)
    conn = db.get()
    mid = ingest.add_message(conn, source="manual", text=AD)
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    with TestClient(server.app) as c:
        yield c, conn, lid
    conn.close()
    db._local.conn = None


def _login(c, conn, email="agent@example.ru", paid=True):
    user = accounts.upsert_email_user(conn, email)
    conn.execute("UPDATE users SET trial_until = 0 WHERE id = ?", (user["id"],))
    if paid:
        accounts.extend(conn, user["id"], 7)
    c.cookies.set(server.SESSION, accounts.create_session(conn, user["id"]))
    return accounts.get_user(conn, user["id"])


def _verify(c, conn, phone="+7 918 111-22-33", sender="79181112233"):
    res = c.post("/api/agent/verify", json={"phone": phone}).json()
    wappi._pending_cache["t"] = 0
    msg = {"id": f"x{res['code']}", "body": f"1+1-{res['code']}", "chatId": f"{sender}@c.us",
           "from": f"{sender}@c.us", "time": int(time.time())}
    wappi._store(conn, "wa", "p1", msg)
    return res


def test_verify_phone_by_code_from_own_number(env):
    c, conn, lid = env
    _login(c, conn)
    res = _verify(c, conn)
    assert res["send_to"] == "+79990000000"
    assert c.get("/api/agent").json()["phones"] == ["+79181112233"]
    assert c.get("/api/agent/verify?phone=89181112233").json()["status"] == "ok"
    # Мой объект виден в «Моих объявлениях» и его можно править
    items = c.get("/api/agent").json()["items"]
    assert [i["id"] for i in items] == [lid] and items[0]["can_edit"]


def test_code_from_other_number_does_not_verify(env):
    c, conn, lid = env
    _login(c, conn)
    _verify(c, conn, sender="79005554433")   # код прислали с чужого номера
    assert c.get("/api/agent").json()["phones"] == []
    assert c.post(f"/api/agent/listings/{lid}", json={"price": 7}).status_code == 403


def test_edit_price_and_sold_and_log(env):
    c, conn, lid = env
    _login(c, conn)
    _verify(c, conn)
    r = c.post(f"/api/agent/listings/{lid}", json={"price": "6,9", "description": "Срочно! Звоните 8-918-111-22-33"})
    assert r.status_code == 200
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["price"] == 6_900_000 and "918" not in row["description"]
    assert row["owner_user_id"]
    # Повтор старого поста из чата не перетирает цену агента
    mid = ingest.add_message(conn, source="wa", text=AD, ts=int(time.time()) - 3600, chat_id="c2", msg_id="m2")
    ingest.process_message(conn, mid, use_llm=False)
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 6_900_000
    # Продано — объект пропадает из поиска
    c.post(f"/api/agent/listings/{lid}", json={"status": "sold"})
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 0
    fields = [r["field"] for r in conn.execute("SELECT field FROM listing_edits WHERE listing_id = ?", (lid,))]
    assert {"price", "description", "status"} <= set(fields)


def test_other_agent_cannot_edit_owned_card(env):
    c, conn, lid = env
    _login(c, conn)
    _verify(c, conn)
    c.post(f"/api/agent/listings/{lid}", json={"price": 7})
    # Второй аккаунт не может подтвердить тот же номер и не может править
    c.cookies.clear()
    _login(c, conn, email="other@example.ru")
    r = c.post("/api/agent/verify", json={"phone": "89181112233"})
    assert r.status_code == 400 and "другим аккаунтом" in r.json()["detail"]
    assert c.post(f"/api/agent/listings/{lid}", json={"price": 1}).status_code == 403


def test_own_listing_needs_subscription_and_phone(env):
    c, conn, _ = env
    _login(c, conn, paid=False)
    r = c.post("/api/agent/listings", json={"type": "flat", "price": 5})
    assert r.status_code == 400 and "подпиской" in r.json()["detail"]
    c.cookies.clear()
    _login(c, conn, email="paid@example.ru")
    r = c.post("/api/agent/listings", json={"type": "flat", "price": 5})
    assert "подтвердите" in r.json()["detail"]


def test_own_listing_lifecycle(env, monkeypatch):
    c, conn, _ = env
    _login(c, conn)
    _verify(c, conn)
    r = c.post("/api/agent/listings", json={"type": "flat", "deal": "sale", "rooms": "1", "area": "38",
                                            "floor": "5", "floors": "9", "price": "4,2",
                                            "district": "ФМР", "description": "Хорошая квартира"})
    assert r.status_code == 200, r.text
    lid = r.json()["id"]
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["source"] == "own" and row["price"] == 4_200_000 and row["title"].startswith("1-к квартира")
    assert row["expires_at"] > time.time() + 29 * 86400
    assert c.get(f"/api/listings/{lid}").json()["phones"] == ["+79181112233"]
    assert lid in [i["id"] for i in c.get("/api/listings?q=хорошая").json()["items"]]
    # Фото
    data = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    photos = c.post(f"/api/agent/listings/{lid}/photos", json={"data": data}).json()["photos"]
    assert len(photos) == 1 and c.get(photos[0]).content == JPEG
    assert c.request("DELETE", f"/api/agent/listings/{lid}/photos", json={"url": photos[0]}).json()["photos"] == []
    # За 3 дня до срока — письмо «актуален?»
    sent = []
    conn.execute("UPDATE listings SET expires_at = ? WHERE id = ?", (int(time.time()) + 86400, lid))
    conn.commit()
    assert agent.expire_own(conn, lambda to, subj, body: sent.append(body))["asked"] == 1
    assert "/agent/confirm" in sent[0]
    # «Да» — продлили
    page = c.get(f"/agent/confirm?l={lid}&a=yes&t={agent.confirm_token(lid, 'yes')}")
    assert "продлён" in page.text
    assert conn.execute("SELECT expires_at FROM listings WHERE id = ?", (lid,)).fetchone()[0] > time.time() + 29 * 86400
    # Чужая/поддельная ссылка не работает; «Нет» — снят сразу
    assert "неверна" in c.get(f"/agent/confirm?l={lid}&a=no&t=bad").text
    c.get(f"/agent/confirm?l={lid}&a=no&t={agent.confirm_token(lid, 'no')}")
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 0


def test_own_listing_expires_if_ignored(env):
    c, conn, _ = env
    _login(c, conn)
    _verify(c, conn)
    lid = c.post("/api/agent/listings", json={"type": "land", "price": "900000", "land": "6"}).json()["id"]
    conn.execute("UPDATE listings SET expires_at = ?, confirm_sent = ? WHERE id = ?",
                 (int(time.time()) - 1, int(time.time()) - 3 * 86400, lid))
    conn.commit()
    assert agent.expire_own(conn)["expired"] == 1


def test_admin_reverts_edit(env, monkeypatch):
    c, conn, lid = env
    _login(c, conn)
    _verify(c, conn)
    c.post(f"/api/agent/listings/{lid}", json={"price": 6})
    conn.execute("UPDATE users SET is_admin = 1")
    conn.commit()
    edits = c.get("/api/admin/edits").json()["items"]
    eid = next(e["id"] for e in edits if e["field"] == "price")
    assert c.post("/api/admin/edits", json={"id": eid}).json()["ok"]
    assert conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 7_500_000

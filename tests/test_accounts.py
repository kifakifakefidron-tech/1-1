"""Личный кабинет: вход через Telegram-бота с номером, пробная неделя (раз на номер),
почта, избранное, лимит открытий номеров, промокоды, «убрать мой номер», админка."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, bot, config, db, ingest, server

AD = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "ACCESS_CODE", "")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(config, "TELEGRAM_BOT_USERNAME", "oneplusone_bot")
    monkeypatch.setattr(config, "TELEGRAM_ADMIN", "ArtemAndreevic1")
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    said = []
    monkeypatch.setattr(bot, "say", lambda chat, text, kb=None: said.append((chat, text)))
    conn = db.get()
    db.set_state(conn, "tg_ok", str(int(time.time())))  # бот на связи с Telegram
    mid = ingest.add_message(conn, source="manual", text=AD)
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    with TestClient(server.app) as c:
        yield c, conn, lid, said
    conn.close()
    db._local.conn = None


def _tg_login(c, conn, tg_id=555, username="buyer", phone="+79001234567"):
    token = c.post("/api/auth/tg/start").json()["token"]
    msg = {"chat": {"id": tg_id, "type": "private"}, "from": {"id": tg_id, "username": username, "first_name": "Иван"}}
    bot.handle(conn, {"message": {**msg, "text": f"/start login_{token}"}})
    bot.handle(conn, {"message": {**msg, "contact": {"user_id": tg_id, "phone_number": phone}}})
    return c.get(f"/api/auth/tg/status?t={token}").json()


def test_phone_hidden_without_login(env):
    c, conn, lid, _ = env
    d = c.get(f"/api/listings/{lid}").json()
    assert "phones" not in d
    assert d["phones_masked"] == ["+7 918 •••-••-••"]
    assert "phones" not in c.get("/api/listings").json()["items"][0]


def test_telegram_login_gives_trial_week_once_per_phone(env):
    c, conn, lid, said = env
    res = _tg_login(c, conn)
    assert res["status"] == "ok"
    me = res["me"]
    assert me["access"] and me["phone_confirmed"]
    assert me["trial_until"] > time.time() + 6 * 86400
    assert c.get(f"/api/listings/{lid}").json()["phones"] == ["+79181112233"]
    # Тот же номер на другом Telegram — второй пробной недели нет
    c.post("/api/auth/logout")
    res2 = _tg_login(c, conn, tg_id=777, username="other", phone="+79001234567")
    assert res2["status"] == "ok" and not res2["me"]["access"]
    assert "phones" not in c.get(f"/api/listings/{lid}").json()


def test_foreign_contact_rejected(env):
    c, conn, _, said = env
    token = c.post("/api/auth/tg/start").json()["token"]
    msg = {"chat": {"id": 1, "type": "private"}, "from": {"id": 1, "username": "x"}}
    bot.handle(conn, {"message": {**msg, "text": f"/start login_{token}"}})
    bot.handle(conn, {"message": {**msg, "contact": {"user_id": 999, "phone_number": "+79001112233"}}})
    assert c.get(f"/api/auth/tg/status?t={token}").json()["status"] == "need_phone"
    assert "СВОЙ" in said[-1][1]


def test_admin_by_telegram_username(env):
    c, conn, _, _ = env
    me = _tg_login(c, conn, tg_id=1, username="ArtemAndreevic1", phone="+79618571772")["me"]
    assert me["is_admin"] and me["access"] and me["views_limit"] is None
    assert c.get("/api/admin/overview").status_code == 200


def test_admin_api_closed_for_users(env):
    c, conn, _, _ = env
    assert c.get("/api/admin/overview").status_code == 403
    _tg_login(c, conn)
    assert c.get("/api/admin/users").status_code == 403


def test_phone_views_limit(env, monkeypatch):
    c, conn, lid, _ = env
    monkeypatch.setattr(config, "PHONE_VIEWS_PER_DAY", 1)
    mid = ingest.add_message(conn, source="manual", text=AD.replace("7/16", "3/9").replace("89181112233", "89180000000"))
    lid2 = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    _tg_login(c, conn)
    assert c.get(f"/api/listings/{lid}").json()["phones"]
    assert c.get(f"/api/listings/{lid}").json()["phones"]  # тот же объект повторно — не считается
    d = c.get(f"/api/listings/{lid2}").json()
    assert d["phones"] == [] and d["phones_limit"] is True


def test_favorites(env):
    c, conn, lid, _ = env
    assert c.post(f"/api/favorites/{lid}").status_code == 401
    _tg_login(c, conn)
    assert c.post(f"/api/favorites/{lid}").json() == {"favorite": True}
    assert [i["id"] for i in c.get("/api/favorites").json()["items"]] == [lid]
    assert c.get("/api/me").json()["me"]["favorites"] == [lid]
    assert c.post(f"/api/favorites/{lid}").json() == {"favorite": False}


def test_promo_code(env):
    c, conn, _, _ = env
    code = accounts.create_promo(conn, days=30, max_uses=1)
    _tg_login(c, conn, phone="+79005550000")
    r = c.post("/api/promo", json={"code": code.lower()}).json()
    assert r["ok"] and r["me"]["paid_until"] > time.time() + 36 * 86400  # неделя пробная + 30 дней
    assert c.post("/api/promo", json={"code": code}).status_code == 400


def test_agent_optout_hides_number(env):
    c, conn, lid, said = env
    msg = {"chat": {"id": 9, "type": "private"}, "from": {"id": 9, "username": "agent"}}
    bot.handle(conn, {"message": {**msg, "text": "/start optout"}})
    bot.handle(conn, {"message": {**msg, "contact": {"user_id": 9, "phone_number": "79181112233"}}})
    assert "скрыт" in said[-1][1]
    assert c.get(f"/api/listings/{lid}").json()["phones_masked"] == []
    _tg_login(c, conn)
    assert c.get(f"/api/listings/{lid}").json()["phones"] == []


def test_email_login(env, monkeypatch):
    c, conn, _, _ = env
    sent = {}
    from app import mailer
    monkeypatch.setattr(mailer, "available", lambda: True)
    monkeypatch.setattr(mailer, "send_code", lambda to, code: sent.update(to=to, code=code) or True)
    assert c.post("/api/auth/email/start", json={"email": "Agent@Mail.ru"}).json() == {"ok": True}
    assert c.post("/api/auth/email/verify", json={"email": "agent@mail.ru", "code": "000000"}).status_code in (400,)
    r = c.post("/api/auth/email/verify", json={"email": "agent@mail.ru", "code": sent["code"]}).json()
    assert r["ok"] and r["me"]["email"] == "agent@mail.ru"
    assert r["me"]["access"] and r["me"]["trial_until"] > time.time() + 6 * 86400  # неделя бесплатно при первом входе
    # Повторный вход тем же адресом — вторую неделю не даёт
    from app import accounts as acc
    before = acc.get_user(conn, r["me"]["id"])["trial_until"]
    acc.upsert_email_user(conn, "agent@mail.ru")
    assert acc.get_user(conn, r["me"]["id"])["trial_until"] == before


def test_telegram_button_hidden_when_bot_offline(env):
    c, conn, _, _ = env
    db.set_state(conn, "tg_ok", "0")
    assert c.get("/api/meta").json()["tg_login"] is False
    assert c.post("/api/auth/tg/start").status_code == 503


def test_admin_actions(env):
    c, conn, _, _ = env
    _tg_login(c, conn, tg_id=1, username="ArtemAndreevic1", phone="+79618571772")
    buyer = accounts.upsert_email_user(conn, "buyer@mail.ru")
    assert c.post(f"/api/admin/users/{buyer['id']}", json={"action": "extend", "days": 14}).json()["ok"]
    assert accounts.has_access(accounts.get_user(conn, buyer["id"]))
    code = c.post("/api/admin/promos", json={"days": 7, "max_uses": 5, "note": "коллеги"}).json()["code"]
    assert any(p["code"] == code for p in c.get("/api/admin/promos").json()["items"])
    assert c.post("/api/admin/optouts", json={"phone": "8 918 111-22-33"}).json()["ok"]
    assert c.get("/api/admin/optouts").json()["items"][0]["phone"] == "+79181112233"


def test_payments_disabled_by_default(env):
    c, conn, _, _ = env
    _tg_login(c, conn)
    assert c.post("/api/pay").status_code == 503
    assert c.post("/api/payments/yookassa", json={"object": {"id": "x"}}).status_code == 404

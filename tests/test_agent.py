"""Кабинет агента: подтверждение номера кодом из мессенджера, «Мои объявления», правка только своего,
свой объект на 20 дней с письмом «актуален?», журнал правок с откатом, фото."""
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
    r = c.post("/api/agent/verify", json={"phone": "89181112233"})   # начать можно — подтвердит только владелец номера
    assert r.status_code == 200 and r.json()["code"]
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
    assert row["expires_at"] > time.time() + 19 * 86400
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
    assert conn.execute("SELECT expires_at FROM listings WHERE id = ?", (lid,)).fetchone()[0] > time.time() + 19 * 86400
    # Чужая/поддельная ссылка не работает; «Нет» — снят сразу
    assert "неверна" in c.get(f"/agent/confirm?l={lid}&a=no&t=bad").text
    c.get(f"/agent/confirm?l={lid}&a=no&t={agent.confirm_token(lid, 'no')}")
    assert conn.execute("SELECT is_active FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 0


def test_own_listing_expires_if_ignored(env):
    c, conn, _ = env
    _login(c, conn)
    _verify(c, conn)
    lid = c.post("/api/agent/listings", json={"type": "land", "price": "900000", "land": "6", "district": "ФМР"}).json()["id"]
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


def test_own_actuality_reminders_10_5_2_1(env):
    """Свой объект живёт 20 дней; «ещё актуален?» — за 10, 5, 2 и 1 день до конца, каждое по одному разу."""
    from app import notices
    c, conn, _ = env
    u = _login(c, conn)
    _verify(c, conn)
    lid = c.post("/api/agent/listings", json={"type": "land", "price": "900000", "land": "6", "district": "ФМР"}).json()["id"]
    now = int(time.time())
    own = lambda: [n for n in notices.items(conn, u["id"]) if n["kind"] == "own"]  # noqa: E731
    assert agent.expire_own(conn)["asked"] == 0 and not own()            # 20 дней впереди — рано
    for days, total in ((10, 1), (9, 1), (5, 2), (2, 3), (1, 4)):
        conn.execute("UPDATE listings SET expires_at = ? WHERE id = ?", (now + days * 86400 - 60, lid))
        conn.commit()
        agent.expire_own(conn)
        assert len(own()) == total, days
    assert len(own()[0]["actions"]) == 2
    # продлили — напоминания начнутся заново
    c.get(f"/agent/confirm?l={lid}&a=yes&t={agent.confirm_token(lid, 'yes')}")
    assert conn.execute("SELECT confirm_stage FROM listings WHERE id = ?", (lid,)).fetchone()[0] is None


def test_own_listing_needs_place(env):
    c, conn, _ = env
    _login(c, conn)
    _verify(c, conn)
    r = c.post("/api/agent/listings", json={"type": "flat", "price": "5"})
    assert r.status_code == 400 and "район" in r.json()["detail"]


def test_referral_no_bonus_since_2510(env, monkeypatch):
    """25.10 (Артём: «никаких больше подарков»): приглашение запоминается, но дней пригласившему не добавляется."""
    c, conn, _ = env
    inviter = _login(c, conn, email="boss@example.ru")
    code = c.get("/api/me").json()["me"]["ref"]["code"]
    before = accounts.get_user(conn, inviter["id"])["paid_until"]
    c.cookies.clear()
    c.get(f"/?ref={code}")                       # пришёл по ссылке, но cookie не разрешил — не запоминаем
    assert c.cookies.get("ref") is None
    c.post("/api/c/consent", json={"choice": "all", "ref": code})   # «Принять все» — приглашение запомнили
    assert c.cookies.get("ref") == code
    monkeypatch.setattr(accounts, "check_email_code", lambda *a: (True, ""))   # вход по почте без письма
    c.post("/api/auth/email/verify", json={"consent": True, "terms": True, "agent": True, "email": "new@example.ru", "code": "1"})
    new = conn.execute("SELECT * FROM users WHERE email = 'new@example.ru'").fetchone()
    assert new["referred_by"] == inviter["id"]
    server._hits.clear()   # лимит «подтверждений в час» общий для всех тестов
    _verify(c, conn, phone="+7 918 222-33-44", sender="79182223344")   # подтвердил номер — бонуса больше нет
    assert accounts.get_user(conn, inviter["id"])["paid_until"] == before


def test_admin_phone_base(env):
    c, conn, lid = env
    u = _login(c, conn)
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    lst = c.get("/api/admin/phones").json()["items"]
    assert lst and lst[0]["phone"] == "+79181112233"
    d = c.get("/api/admin/phones?phone=89181112233").json()
    assert d["phone"] == "+79181112233" and d["listings"][0]["id"] == lid and d["banned"] is False
    r = c.post(f"/api/admin/listings/{lid}/action", json={"action": "price", "price": "6,9"})
    assert r.status_code == 200 and conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 6_900_000


def test_describe_uses_only_facts_and_strips_phones(monkeypatch):
    """«Написать красиво»: в запрос к нейросети уходят параметры объекта, телефоны из ответа вырезаются."""
    from app import config, llm
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "x")
    seen = {}

    def fake(system, user, **kw):
        seen["user"] = user
        return "2-к квартира в ЖК Свобода\n— 58 м², 5/16 этаж\nЗвоните: 8 918 111-22-33"

    monkeypatch.setattr(llm, "ask_text", fake)
    text, why = agent.describe({"type": "flat", "deal": "sale", "rooms": "2", "area": "58", "floor": "5", "floors": "16",
                                "complex": "Свобода", "price": "6,5", "description": "Тел 89181112233, хороший ремонт"})
    assert why == "ok" and "ЖК: Свобода" in seen["user"] and "Площадь: 58 м²" in seen["user"]
    assert "89181112233" not in seen["user"] and "111-22-33" not in text
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    assert agent.describe({"type": "flat"})[0] is None


def test_verify_by_call(env, monkeypatch):
    """17.10: подтверждение звонком (SMS.ru callcheck) — основной способ: позвонил со своего номера → номер подтверждён."""
    from app import callcheck, config
    c, conn, _ = env
    _login(c, conn, email="caller@example.ru")
    monkeypatch.setattr(config, "SMSRU_API_ID", "test")
    state = {"st": "400"}
    def fake(path, **p):
        if path == "add":
            assert p["phone"] == "79181234567"
            return {"status": "OK", "check_id": "777-1", "call_phone": "78005008275", "call_phone_pretty": "+7 (800) 500-8275"}
        return {"status": "OK", "check_status": state["st"]}
    monkeypatch.setattr(callcheck, "_get", fake)
    assert "call" in c.get("/api/agent").json()["verify_methods"]
    r = c.post("/api/agent/verify", json={"phone": "8 918 123-45-67", "method": "call"}).json()
    assert r["call_phone"] == "+78005008275" and r["method"] == "call"
    assert c.get("/api/agent/verify?phone=%2B79181234567&method=call").json()["status"] == "pending"
    state["st"] = "401"
    conn.execute("UPDATE phone_codes SET checked_at = NULL")
    conn.commit()
    assert c.get("/api/agent/verify?phone=%2B79181234567&method=call").json()["status"] == "ok"
    assert c.get("/api/agent").json()["phones"] == ["+79181234567"]


def test_always_verified_numbers(env):
    """18.10: номера Артёма/агентства — всегда «Проверенный агент», без подтверждения."""
    from app import ingest, search
    c, conn, _ = env
    mid = ingest.add_message(conn, source="wa", text="2-к квартира 58 м², 7/16 эт., ФМР, ул. Ставропольская 10. 7,5 млн. +7 918 332-60-33",
                             chat_id="c", msg_id="av1")
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    assert search.listing_detail(conn, lid, True)["verified"] is True


def test_login_by_phone_call(env, monkeypatch):
    """18.10: вход по номеру — звонок со своего номера; уже подтверждённый номер входит в свой аккаунт (без дублей)."""
    from starlette.testclient import TestClient
    from app import callcheck, config, server
    c, conn, _ = env
    monkeypatch.setattr(config, "SMSRU_API_ID", "test")
    st = {"s": "400"}
    def fake(path, **p):
        if path == "add":
            return {"status": "OK", "check_id": "c-1", "call_phone": "78005008275", "call_phone_pretty": "+7 (800) 500-8275"}
        return {"status": "OK", "check_status": st["s"]}
    monkeypatch.setattr(callcheck, "_get", fake)
    with TestClient(server.app) as g:
        assert g.get("/api/meta").json().get("phone_login") is True
        assert g.post("/api/auth/phone", json={"phone": "+7 918 555-44-33"}).status_code == 400   # без согласия
        r = g.post("/api/auth/phone", json={"phone": "+7 918 555-44-33", "consent": True, "terms": True, "agent": True}).json()
        tid = r["tid"]
        assert g.get(f"/api/auth/phone?tid={tid}").json()["status"] == "pending"
        st["s"] = "401"
        conn.execute("UPDATE phone_codes SET checked_at = NULL"); conn.commit()
        res = g.get(f"/api/auth/phone?tid={tid}").json()
        assert res["status"] == "ok"
        me = g.get("/api/me").json()["me"]
        assert me and g.get("/api/agent").json()["phones"] == ["+79185554433"]
        assert g.get(f"/api/auth/phone?tid={tid}").json()["status"] == "used"   # повторно по той же метке не войти
        uid = me["id"]
    with TestClient(server.app) as g2:   # второй вход тем же номером — тот же аккаунт
        tid = g2.post("/api/auth/phone", json={"phone": "89185554433", "consent": True, "terms": True, "agent": True}).json()["tid"]
        conn.execute("UPDATE phone_codes SET checked_at = NULL"); conn.commit()
        assert g2.get(f"/api/auth/phone?tid={tid}").json()["status"] == "ok"
        assert g2.get("/api/me").json()["me"]["id"] == uid


def test_accounts_merge_by_email_and_phone(env, monkeypatch):
    """18.10: один человек — один аккаунт. Вошёл по номеру (новый аккаунт), привязал почту прежнего аккаунта →
    объединились: избранное, клиенты, подтверждённые номера — в старшем, доступ — самый поздний."""
    from app import accounts, mailer
    c, conn, _ = env
    old = accounts.upsert_email_user(conn, "agent@old.ru")
    conn.execute("UPDATE users SET paid_until = 2000000000 WHERE id = ?", (old["id"],))
    conn.commit()
    accounts.toggle_favorite(conn, old["id"], 5)
    new = accounts.upsert_phone_user(conn, "+79185550001")          # вошёл по номеру — пока другой аккаунт
    assert new["id"] != old["id"]
    c.cookies.set(server.SESSION, accounts.create_session(conn, new["id"]))
    monkeypatch.setattr(mailer, "available", lambda: True)
    sent = {}
    monkeypatch.setattr(mailer, "send_code", lambda to, code: sent.update(code=code) or True)
    assert c.post("/api/account/email", json={"email": "agent@old.ru"}).json()["ok"]
    r = c.post("/api/account/email", json={"email": "agent@old.ru", "code": sent["code"]}).json()
    assert r["merged"] is True and r["me"]["id"] == old["id"]
    me = c.get("/api/me").json()["me"]                               # та же сессия — уже в объединённом аккаунте
    assert me["id"] == old["id"] and me["email"] == "agent@old.ru" and me["phones"] == ["+79185550001"]
    assert 5 in me["favorites"] and me["paid_until"] == 2000000000
    assert accounts.get_user(conn, new["id"]) is None
    # теперь вход по этому номеру ведёт в тот же аккаунт
    assert accounts.upsert_phone_user(conn, "+79185550001")["id"] == old["id"]


def test_signup_checkboxes_apply_to_phone_confirmed_later(env, monkeypatch):
    """25.10: галочки агента в окне входа → номер, подтверждённый потом, сразу с согласием (больше ничего не спрашиваем)."""
    from app import consent
    c, conn, _ = env
    monkeypatch.setattr(accounts, "check_email_code", lambda *a: (True, ""))
    r = c.post("/api/auth/email/verify", json={"consent": True, "terms": True, "agent": True, "email": "ag@example.ru", "code": "1",
                                              "publish": True, "license": True})
    assert r.status_code == 200
    uid = conn.execute("SELECT id FROM users WHERE email = 'ag@example.ru'").fetchone()[0]
    assert consent.signup_choice(conn, uid)["publish"] is True
    server._hits.clear()
    _verify(c, conn, phone="+7 918 333-44-55", sender="79183334455")
    cur = consent.current(conn, "+79183334455")
    assert cur and cur["publish"] == 1 and cur["license"] == 1
    assert c.get("/api/agent/consent").json()["signup"]["license"] is True



def test_login_needs_third_checkbox(env, monkeypatch):
    """25.10 (Артём): согласие на показ номера и на тексты/фото — обязательная третья галочка при входе."""
    c, conn, _ = env
    monkeypatch.setattr(accounts, "check_email_code", lambda *a: (True, ""))
    r = c.post("/api/auth/email/verify", json={"consent": True, "terms": True, "email": "x3@example.ru", "code": "1"})
    assert r.status_code == 400 and "три галочки" in r.json()["detail"]
    r = c.post("/api/auth/email/verify", json={"consent": True, "terms": True, "agent": True, "email": "x3@example.ru", "code": "1"})
    assert r.status_code == 200
    from app import consent
    uid = conn.execute("SELECT id FROM users WHERE email = 'x3@example.ru'").fetchone()[0]
    ch = consent.signup_choice(conn, uid)
    assert ch["publish"] is True and ch["license"] is True

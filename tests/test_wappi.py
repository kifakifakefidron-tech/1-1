"""Опрос Wappi: свой токен на тип аккаунта, WhatsApp — одним запросом,
Telegram/MAX — через список чатов; личные чаты пропускаем, прочитанным не отмечаем."""
import json

from app import config, wappi

from .conftest import NOW

AD = "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"


def test_each_profile_uses_its_own_token(conn, monkeypatch):
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "token-wa", "tg": "token-tg", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1", "tg:p2", "max:p3"])
    calls = []

    def fake_get(path, params, token):
        calls.append((path, params["profile_id"], token))
        if path == "/api/sync/messages/all/get":
            return {"messages": [{"id": "m1", "chatId": "123@g.us", "time": NOW, "senderName": "Ирина", "body": AD}]}
        return {"dialogs": []}

    monkeypatch.setattr(wappi, "_get", fake_get)
    assert wappi.poll_all(conn) == 1
    # MAX без токена пропущен, остальные — каждый со своим токеном
    assert calls == [("/api/sync/messages/all/get", "p1", "token-wa"),
                     ("/tapi/sync/chats/get", "p2", "token-tg")]


def test_whatsapp_stops_at_old_history(conn, monkeypatch):
    """Wappi отдаёт всю историю, не глядя на дату — берём только свежее и дальше не листаем."""
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "t", "tg": "", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1"])
    monkeypatch.setattr(wappi, "PAGE", 2)
    pages = []

    def fake_get(path, params, token):
        pages.append(params["offset"])
        assert params["order"] == "desc"
        return {"messages": [
            {"id": "new", "chatId": "1@g.us", "time": NOW - 60, "body": AD},
            {"id": "old", "chatId": "1@g.us", "time": NOW - 90 * 86400, "body": AD + " старое"},
        ]}

    monkeypatch.setattr(wappi, "_get", fake_get)
    assert wappi.poll_all(conn) == 1
    assert pages == [0]  # до второй страницы не дошли
    assert [r["msg_id"] for r in conn.execute("SELECT msg_id FROM messages")] == ["new"]


def test_queue_newest_first_and_too_old_skipped(conn):
    from app import ingest
    old = ingest.add_message(conn, source="wa", text=AD, ts=NOW - 60 * 86400, msg_id="a")
    mid = ingest.add_message(conn, source="wa", text=AD.replace("7/16", "3/16"), ts=NOW - 7200, msg_id="b")
    new = ingest.add_message(conn, source="wa", text=AD.replace("7/16", "9/16"), ts=NOW - 60, msg_id="c")
    assert ingest.process_pending(conn, limit=1, use_llm=False, now=NOW) == 1
    status = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM messages")}
    assert status == {old: "skipped", mid: "new", new: "done"}


def test_private_chats_are_skipped(conn, monkeypatch):
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "t", "tg": "", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1"])
    monkeypatch.setattr(wappi, "_get", lambda *a: [{
        "id": "m2", "chatId": "79181112233@c.us", "time": NOW, "body": AD + " — личное сообщение клиенту"}])
    assert wappi.poll_all(conn) == 0


def test_max_reads_group_chats_and_converts_milliseconds(conn, monkeypatch):
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "", "tg": "", "max": "tm"})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["max:p3"])
    monkeypatch.setattr(wappi.time, "sleep", lambda s: None)
    asked = []

    def fake_get(path, params, token):
        if path.endswith("/chats/get"):
            return {"dialogs": [
                {"id": "g1", "name": "Риелторы Краснодара", "isGroup": True, "last_timestamp": NOW * 1000},
                {"id": "d1", "name": "Клиент", "isGroup": False, "type": "DIALOG", "last_timestamp": NOW * 1000},
                {"id": "g0", "name": "Старая группа", "isGroup": True, "last_timestamp": (NOW - 30 * 86400) * 1000},
            ]}
        asked.append((params["chat_id"], params["mark_all"]))
        return {"messages": [{"id": "x1", "chatId": "g1", "time": NOW * 1000, "from": "79181112233", "body": AD}]}

    monkeypatch.setattr(wappi, "_get", fake_get)
    assert wappi.poll_all(conn) == 1
    assert asked == [("g1", "false")]  # только свежая группа, прочитанным не отмечаем
    m = conn.execute("SELECT * FROM messages").fetchone()
    assert m["ts"] == NOW
    assert m["chat_name"] == "Риелторы Краснодара"
    assert m["source"] == "max"


def test_telegram_alert_once_per_period(conn, monkeypatch):
    from app import db, notify
    sent = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setattr(notify, "_api", lambda method, payload: sent.append(payload["text"]) or {"ok": True})
    db.set_state(conn, "tg_admin_chat", "123")
    notify.alert(conn, "silence", "Нет сообщений")
    notify.alert(conn, "silence", "Нет сообщений")
    assert len(sent) == 1
    notify.resolve(conn, "silence", "Снова идут")
    assert sent[-1].startswith("✅")


def test_bot_remembers_only_admin(conn, monkeypatch):
    from app import bot, db
    monkeypatch.setattr(config, "TELEGRAM_ADMIN", "ArtemAndreevic1")
    monkeypatch.setattr(bot, "say", lambda *a, **k: None)
    bot.remember_admin(conn, {"from": {"id": 7, "username": "stranger"}, "chat": {"id": 7}})
    assert db.get_state(conn, "tg_admin_chat") is None
    bot.remember_admin(conn, {"from": {"id": 42, "username": "artemandreevic1"}, "chat": {"id": 42}})
    assert db.get_state(conn, "tg_admin_chat") == "42"


def test_verify_codes_while_collection_paused(conn, monkeypatch):
    """Сбор на паузе, но агент ждёт подтверждения номера: код из лички засчитывается, объявления из групп — нет."""
    from app import accounts, agent
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "t", "tg": "", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1"])
    monkeypatch.setattr(config, "WAPPI_ENABLED", False)
    monkeypatch.setattr(config, "VERIFY_CHANNELS", ["wa"])   # механизм тот же для MAX (по умолчанию — только MAX)
    wappi._pending_cache["t"] = 0
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    info, err = agent.start_verification(conn, u["id"], "+7 918 111-22-33")
    assert err is None

    def fake_get(path, params, token):
        return {"messages": [
            {"id": "c1", "chatId": "79181112233@c.us", "time": NOW, "body": f"1+1-{info['code']}"},
            {"id": "g1", "chatId": "1@g.us", "time": NOW, "body": AD},
        ]}

    monkeypatch.setattr(wappi, "_get", fake_get)
    assert wappi.poll_verify_only(conn) == 1
    assert agent.verified_phones(conn, u["id"]) == ["+79181112233"]
    assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0   # из групп ничего не сохранили
    assert wappi.poll_verify_only(conn) == 0                                  # больше никто не ждёт — Wappi не трогаем


def test_verify_check_now_max(conn, monkeypatch):
    """Кнопка «Я отправил — проверить» (MAX): личка читается сразу; код с чужого номера — понятное объяснение."""
    from app import accounts, agent
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "", "tg": "", "max": "t"})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["max:p1"])
    monkeypatch.setattr(config, "VERIFY_CHANNELS", ["max"])
    monkeypatch.setattr(wappi.time, "sleep", lambda s: None)
    wappi.seen_codes.clear()
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    info, _ = agent.start_verification(conn, u["id"], "+7 918 111-22-33")
    sender = {"v": "79990001122"}

    def fake_get(path, params, token):
        if path.endswith("/chats/get"):   # личный чат MAX: id без минуса, номер — в поле phone
            return {"dialogs": [{"id": "205160429", "last_timestamp": NOW * 1000}]}
        return {"messages": [{"id": "m1", "chatId": "205160429", "time": NOW * 1000, "from": sender["v"],
                              "phone": sender["v"], "body": f"1+1-{info['code']}"}]}

    monkeypatch.setattr(wappi, "_get", fake_get)
    res = agent.check_result(conn, u["id"], info["phone"], wappi.seen_codes, wappi.check_now(conn))
    assert res["status"] == "pending" and "+7 999 000-11-22" in res["detail"]
    sender["v"] = "79181112233"
    res = agent.check_result(conn, u["id"], info["phone"], wappi.seen_codes, wappi.check_now(conn))
    assert res["status"] == "ok" and agent.verified_phones(conn, u["id"]) == ["+79181112233"]


def test_verify_check_not_configured(conn, monkeypatch):
    from app import accounts, agent
    monkeypatch.setattr(config, "WAPPI_PROFILES", [])
    u = accounts.upsert_email_user(conn, "agent2@example.ru")
    info, _ = agent.start_verification(conn, u["id"], "+7 918 111-22-44")
    res = agent.check_result(conn, u["id"], info["phone"], [], wappi.check_now(conn))
    assert res["status"] == "pending" and "не работает" in res["detail"]


def test_album_photos_attach_to_listing(conn, monkeypatch, tmp_path):
    """Объявление подписью к фото + ещё фото альбомом: текст разбирается, фото попадают в карточку."""
    from app import ingest, media
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "t", "tg": "", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1"])
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path / "photos"))
    img = lambda mid, t, cap="": {"id": mid, "type": "image", "chatId": "1@g.us", "from": "79181112233@c.us", "time": t,
                                  "body": {"mimetype": "image/jpeg", "caption": cap}}
    msgs = [img("a1", NOW - 30, AD), img("a2", NOW - 20), img("a3", NOW - 10),
            {"id": "x", "type": "image", "chatId": "1@g.us", "from": "79990000000@c.us", "time": NOW, "body": {"mimetype": "image/jpeg"}}]

    def fake_get(path, params, token):
        if path.endswith("/message/media/download"):
            return {"file_link": f"https://files.example/{params['message_id']}"}
        return {"messages": msgs}

    monkeypatch.setattr(wappi, "_get", fake_get)
    monkeypatch.setattr(media, "_fetch", lambda link: b"\xff\xd8\xff" + link.encode() * 200)
    assert wappi.poll_all(conn) == 1                          # текст из подписи к фото — объявление
    assert conn.execute("SELECT COUNT(*) FROM chat_media").fetchone()[0] == 4
    ingest.process_pending(conn)
    res = media.attach(conn)
    lid = conn.execute("SELECT listing_id FROM listing_events").fetchone()[0]
    photos = json.loads(conn.execute("SELECT photos FROM listings WHERE id = ?", (lid,)).fetchone()[0])
    assert res["photos"] == 3 and len(photos) == 3            # фото чужого номера к объекту не прилипло

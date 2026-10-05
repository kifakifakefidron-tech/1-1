"""Опрос Wappi: свой токен на тип аккаунта, WhatsApp — одним запросом,
Telegram/MAX — через список чатов; личные чаты пропускаем, прочитанным не отмечаем."""
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

"""Опрос Wappi: у каждого типа аккаунта свой токен, сообщения из групп попадают в базу."""
from app import config, wappi

from .conftest import NOW


def test_each_profile_uses_its_own_token(conn, monkeypatch):
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "token-wa", "tg": "token-tg", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1", "tg:p2", "max:p3"])
    calls = []

    def fake_get(path, params, token):
        calls.append((path, params["profile_id"], token))
        if path.startswith("/api/"):
            return {"messages": [{
                "id": "m1", "chatId": "123@g.us", "time": NOW, "senderName": "Ирина",
                "body": "Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233",
            }]}
        return {"messages": []}

    monkeypatch.setattr(wappi, "_get", fake_get)
    assert wappi.poll_all(conn) == 1
    # MAX без токена пропущен, остальные — каждый со своим токеном
    assert calls == [("/api/sync/messages/all/get", "p1", "token-wa"),
                     ("/tapi/sync/messages/all/get", "p2", "token-tg")]


def test_private_chats_are_skipped(conn, monkeypatch):
    monkeypatch.setattr(config, "WAPPI_TOKENS", {"wa": "t", "tg": "", "max": ""})
    monkeypatch.setattr(config, "WAPPI_PROFILES", ["wa:p1"])
    monkeypatch.setattr(wappi, "_get", lambda *a: [{
        "id": "m2", "chatId": "79181112233@c.us", "time": NOW,
        "body": "Продаётся 2-к квартира 58 м², 7/16 эт. 7,5 млн руб. Личное сообщение клиенту",
    }])
    assert wappi.poll_all(conn) == 0

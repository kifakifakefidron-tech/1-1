"""11.11: выгрузка новых запросов покупателей в Google Таблицу рассылки."""
import time

import pytest
from starlette.testclient import TestClient

from app import config, db, ingest, reqexport, server

KEY = "k" * 32


def _req(conn, text, sender_name="Ольга", chat="Риелторы ФМР", n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="max", text=text, ts=int(time.time()) - 100 + n[0], chat_id="c",
                             chat_name=chat, msg_id=f"rq{n[0]}", sender_name=sender_name)
    r = ingest.process_message(conn, mid, use_llm=False)
    assert r["kind"] == "request", r
    return conn.execute("SELECT id FROM buyer_requests ORDER BY id DESC LIMIT 1").fetchone()[0]


def test_export_filters_and_cursor(conn):
    a = _req(conn, "Куплю 2к в ФМР до 7 млн, наличные. 89184445566")
    _req(conn, "Ищу дом в ЮМР до 15 млн, без телефона в тексте")                     # нет номера — пропуск
    _req(conn, "Куплю студию в ЦМР до 5 млн. 89181112233", sender_name="+7 918 111-22-33")
    _req(conn, "Ищу участок в Новой Адыгее до 3 млн. 89187770000")
    uid = conn.execute("INSERT INTO users (email, created) VALUES ('a@x.ru', 0)").lastrowid
    conn.execute("INSERT INTO agent_phones (phone, user_id, verified_at) VALUES ('+79187770000', ?, 0)", (uid,))  # уже пользователь

    d = reqexport.export(conn, 0)
    phones = [i["phone"] for i in d["items"]]
    assert phones == ["+79184445566", "+79181112233"]
    first = d["items"][0]
    assert first["name"] == "Ольга" and first["source"] == "MAX Риелторы ФМР"
    assert "445566" not in first["text"] and "ФМР" in first["text"]               # номер — не в цитате
    assert d["items"][1]["name"] == ""                                              # номер вместо имени — не имя
    assert d["skipped"]["no_phone"] == 1 and d["skipped"]["user_or_hidden"] == 1
    assert reqexport.export(conn, d["last_id"])["items"] == []                       # курсор: только новее
    assert reqexport.export(conn, a)["items"][0]["phone"] == "+79181112233"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    _req(conn, "Куплю 2к в ФМР до 7 млн. 89184445566")
    with TestClient(server.app) as c:
        yield c
    conn.close()
    db._local.conn = None


def test_export_needs_key(client, monkeypatch):
    monkeypatch.setattr(config, "EXPORT_KEY", "")
    assert client.get("/api/export/requests").status_code == 404                    # ключа в .env нет — выгрузки нет
    monkeypatch.setattr(config, "EXPORT_KEY", KEY)
    assert client.get("/api/export/requests").status_code == 403
    assert client.get("/api/export/requests", headers={"X-Export-Key": "wrong"}).status_code == 403
    r = client.get("/api/export/requests?after=0", headers={"X-Export-Key": KEY})
    assert r.status_code == 200 and r.json()["items"][0]["phone"] == "+79184445566"

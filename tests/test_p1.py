"""Этап P1 (ТЗ v2): клиент — центр. Подборка создаётся с клиентом, подбор без повторов, один статус на пару,
реакции клиента со страницы подборки, этап двигается сам, удаление подборки уносит её статусы."""
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, ingest, server

PHONE_UA = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    conn = db.get()
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    conn.execute("UPDATE users SET paid_until = ? WHERE id = ?", (int(time.time()) + 86400, u["id"]))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None


def add(conn, text, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"p1{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def test_client_flow_pick_match_reactions(env):
    c, conn = env
    a = add(conn, "2-к квартира 58 м², 7/16 эт., ФМР, ул. Ставропольская 10, ремонт. 6 300 000 руб. 89181112233")
    b = add(conn, "2-к квартира 55 м², 1/9 эт., ФМР, ул. Ставропольская 20, ремонт. 6 000 000 руб. 89184445566")
    d_ = add(conn, "2-к квартира 60 м², 5/9 эт., ФМР, ул. Ставропольская 30, ремонт. 6 700 000 руб. 89187778899")
    r = c.post("/api/clients", json={"name": "Вася", "phone": "8 918 000-11-22", "rooms": [2], "districts": ["ФМР"],
                                     "budget_max": "6,5", "wishes": {"not_first": True, "cond": ["repair"], "junk": 1}}).json()
    cid = r["client"]["id"]
    assert r["client"]["wishes"] == {"not_first": True, "cond": ["repair"]}
    assert [p["title"] for p in r["picks"]] == ["Для Вася"]            # подборка создалась сама
    # подбор: бюджет строгий (6,7 млн не входит), первый этаж исключён пожеланием
    m = c.get(f"/api/clients/{cid}/match").json()
    assert [x["id"] for x in m["items"]] == [a] and m["total"] == 1
    # «Не подходит» — пропадает из подбора, счётчик честный, раздел «Скрытые»
    c.post(f"/api/clients/{cid}/hide", json={"ids": [a]})
    assert c.get(f"/api/clients/{cid}/match").json()["total"] == 0
    assert [x["id"] for x in c.get(f"/api/clients/{cid}/hidden").json()["items"]] == [a]
    c.post(f"/api/clients/{cid}/unhide", json={"ids": [a]})
    # в подборку → «Отправлен», этап клиента сам → «Подбор», в избранное не кладём
    r = c.post(f"/api/clients/{cid}/topick", json={"ids": [a]}).json()
    pick = r["pick"]
    d = c.get(f"/api/clients?id={cid}").json()
    assert {x["id"]: x["status"] for x in d["items"]} == {a: "added"} and d["client"]["stage"] == "search"   # 24.10b: «Добавлен»
    c.post("/api/picks", json={"sent_token": pick["url"].rsplit("/", 1)[1]})   # ссылку скопировали → «Отправлен»
    d = c.get(f"/api/clients?id={cid}").json()
    assert {x["id"]: x["status"] for x in d["items"]} == {a: "sent"}
    assert conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0] == 0
    assert c.get(f"/api/clients/{cid}/match").json()["total"] == 0          # уже у клиента — в подборе не повторяется
    token = pick["url"].rsplit("/", 1)[1]
    # клиент открыл (не агент) → «Открыл»; ответил «Нравится» → «Понравился», история, уведомление агенту
    with TestClient(server.app) as guest:
        guest.get(f"/api/c/{token}", headers=PHONE_UA)
        assert conn.execute("SELECT status FROM client_items WHERE listing_id = ?", (a,)).fetchone()[0] == "opened"
        rr = guest.post(f"/api/c/{token}/react", json={"listing_id": a, "kind": "like"}, headers=PHONE_UA).json()
        assert rr == {"ok": True, "kind": "like"}
        assert guest.get(f"/api/c/{token}", headers=PHONE_UA).json()["items"][0]["reaction"] == "like"
        assert guest.post(f"/api/c/{token}/react", json={"listing_id": b, "kind": "like"}).status_code == 400   # не в подборке
        assert guest.post(f"/api/c/{token}/react", json={"listing_id": a, "kind": "question"}).status_code == 400   # пустой вопрос
    d = c.get(f"/api/clients?id={cid}").json()
    it = d["items"][0]
    assert it["status"] == "liked" and it["reaction"] == "like"
    assert any("Ответ клиента — Нравится" in x["text"] for x in d["log"])
    kinds = [n["title"] for n in c.get("/api/notices").json()["items"]]
    assert any("«Нравится»" in t for t in kinds) and any("открыл" in t for t in kinds)
    # статус в подборке руками → запись «Вася: показ» в журнале объекта
    c.post(f"/api/picks/{pick['id']}/items", json={"listing_id": a, "status": "show"})
    assert c.get(f"/api/worklog?listing={a}").json()["items"][0]["text"] == "Вася: показ"
    # подборки без клиента статус не принимают
    other = c.post("/api/picks", json={"title": "Без клиента", "listing_id": b}).json()["pick"]
    assert c.post(f"/api/picks/{other['id']}/items", json={"listing_id": b, "status": "liked"}).status_code == 400
    # убрали объект из подборки — статусы подборки и её записи уходят; «показ» агента остаётся
    c.post(f"/api/picks/{pick['id']}/items", json={"listing_id": a, "add": False})
    d = c.get(f"/api/clients?id={cid}").json()
    assert {x["id"]: x["status"] for x in d["items"]} == {a: "show"}
    assert c.get(f"/api/worklog?listing={a}").json()["items"] == []


def test_delete_pick_forgets_its_statuses_and_manual_stage_is_kept(env):
    c, conn = env
    a = add(conn, "1-к квартира 40 м², 3/9 эт., ЦМР, ул. Красная 5. 5 млн. 89184445566")
    cid = c.post("/api/clients", json={"name": "Оля", "stage": "talks"}).json()["client"]["id"]
    c.post("/api/clients", json={"id": cid, "name": "Оля", "stage": "talks"})
    pid = c.post(f"/api/clients/{cid}/add", json={"listing_id": a}).json()["pick"]["id"]
    d = c.get(f"/api/clients?id={cid}").json()
    assert d["items"][0]["status"] == "added" and d["client"]["stage"] == "talks"   # этап агент ставил сам — не трогаем
    c.request("DELETE", "/api/picks", json={"id": pid})
    d = c.get(f"/api/clients?id={cid}").json()
    assert d["items"] == [] and any("Убран из подборки" in x["text"] for x in d["log"])


def test_complex_names_merged(env):
    from app import cxnorm
    c, conn = env
    a = add(conn, "Студия 25 м², 5/16 эт., ЖК Самолёт 2, ул. Западный обход 39. 3 600 000 руб. 89181112233")
    b = add(conn, "Студия 24 м², 7/16 эт., ЖК Самолёт 2, ул. Западный обход 39. 3 650 000 руб. 89184445566")
    conn.execute("UPDATE listings SET complex = 'Самолёт 2' WHERE id = ?", (a,))
    conn.execute("UPDATE listings SET complex = 'САМОЛЕТ-2' WHERE id = ?", (b,))
    conn.commit()
    rep = cxnorm.run(conn, apply=True)
    names = {r[0] for r in conn.execute("SELECT DISTINCT complex FROM listings WHERE id IN (?, ?)", (a, b))}
    assert len(names) == 1 and rep["groups"] == 1
    assert cxnorm.canonical(conn, "самолет 2") == names.pop()


def test_p2_contact_hidden_address_and_similar(env):
    c, conn = env
    a = add(conn, "2-к квартира 58 м², 7/16 эт., ФМР, ул. Ставропольская 10, дом 5. 6 300 000 руб. 89181112233")
    b = add(conn, "2-к квартира 57 м², 9/16 эт., ФМР, ул. Ставропольская 12. 6 500 000 руб. 89184445566")
    conn.execute("UPDATE listings SET lat = 45.0123, lon = 38.9876, geo_status = 'osm-house' WHERE id = ?", (a,))
    conn.execute("UPDATE users SET name = 'Коллега (по коду)'")
    conn.commit()
    p = c.post("/api/picks", json={"title": "Тест", "listing_id": a}).json()["pick"]
    assert p["contact_name"] == ""                       # служебное имя в подборку не попадает
    token = p["url"].rsplit("/", 1)[1]
    c.post("/api/picks", json={"id": p["id"], "hide_address": True})
    it = c.get(f"/api/c/{token}?as=agent").json()["items"][0]
    assert it["house"] is None and it["approx"] and abs(it["lat"] - 45.0123) < 0.003 and it["lat"] != 45.0123
    # объект сняли — клиенту без описания; агенту — похожие и замена на то же место
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (a,))
    conn.commit()
    assert c.get(f"/api/c/{token}?as=agent").json()["items"][0]["description"] == ""
    sims = c.get(f"/api/picks/{p['id']}/similar?listing={a}").json()["items"]
    assert [x["id"] for x in sims] == [b]
    c.post(f"/api/picks/{p['id']}/items", json={"replace": a, "listing_id": b})
    assert [x["listing_id"] for x in c.get("/api/picks").json()["items"][0]["items"]] == [b]

"""Обучение на правках админа: ЖК и район. Главное — новые объявления о том же объекте получают
исправленные ЖК/район ДО проверки на дубли и склеиваются с исправленной карточкой, а не плодят новые."""
import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, ingest, learning, search, server

from .conftest import NOW


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    u = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None


def add(conn, text, n=[0], ts=None):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, ts=ts or NOW - 1000 + n[0], chat_id="c", msg_id=f"m{n[0]}")
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]


def row(conn, lid):
    return conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()


def test_alias_fix_applies_to_new_and_existing_and_merges(env):
    c, conn = env
    a = add(conn, "САМОЛЕТ\nстудия 25м 5/16 *3700тр*\nТел. 89181000001")["listing_id"]
    b = add(conn, "САМОЛЕТ\n1ккв 36м 3/16 4300тр\nТел. 89181000002")["listing_id"]
    assert row(conn, a)["complex"] == "Самолет"
    # Админ: «Самолет» у этих агентов — это ЖК «Самолёт-3» в районе ЮМР; запомнить
    res = c.post(f"/api/admin/listings/{a}/place", json={
        "complex": "Самолет 3", "district": "ЮМР", "learn": {"alias": True, "cx_district": True}}).json()
    assert res["applied"] == 1 and len(res["learned"]) == 2
    assert (row(conn, b)["complex"], row(conn, b)["district"]) == ("Самолет 3", "ЮМР")     # уже собранный — поправлен
    # Новое объявление того же агента о той же студии со «старым» ЖК — склеивается с исправленной карточкой
    r = add(conn, "САМОЛЕТ\nстудия 25м 5/16 *3650тр*\nТел. 89181000001")
    assert r["listing_id"] == a and r["match"] != "new"
    assert row(conn, a)["complex"] == "Самолет 3" and row(conn, a)["price"] == 3_650_000
    assert search.search(conn, search.Query(districts=["ЮМР"]), NOW, False)["total"] == 2


def test_not_a_complex_and_house_rule(env):
    c, conn = env
    a = add(conn, "Выше\nул. Жигуленко 25\n2ккв 72м 16/17\n7550тр\nТел. 89181000003")["listing_id"]
    assert row(conn, a)["complex"] == "Выше"
    c.post(f"/api/admin/listings/{a}/place", json={"complex": "", "district": "ЧМР",
                                                    "learn": {"alias": True, "addr": True}})
    assert (row(conn, a)["complex"], row(conn, a)["district"]) == (None, "ЧМР")
    # дом Жигуленко 25 выучен: новое объявление без ЖК и района получает район
    b = add(conn, "2-к квартира 70 м², 10/17 эт., ул. Жигуленко, д. 25. 7,4 млн. 89180000099")["listing_id"]
    assert row(conn, b)["district"] == "ЧМР"
    # правило можно забыть
    rules = c.get("/api/admin/place-queue?kind=fixed").json()["rules"]
    assert len(rules) == 2
    c.post("/api/admin/place-queue", json={"forget": rules[0]["id"]})
    assert len(learning.list_rules(conn)) == 1


def test_admin_fix_survives_audit(env):
    from app import audit
    c, conn = env
    a = add(conn, "2-к квартира 50 м², 3/9 эт., ул. Тургенева 10. 6 млн. 89180000001")["listing_id"]
    c.post(f"/api/admin/listings/{a}/place", json={"complex": "Мозаика", "district": "ФМР", "learn": {}})
    audit.run(fix=True, conn=conn)          # ЖК нет в тексте, но его поставил админ — не снимаем
    assert row(conn, a)["complex"] == "Мозаика"

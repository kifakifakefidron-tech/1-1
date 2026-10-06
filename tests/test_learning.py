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


def test_fix_queue_skips_and_suggestions(env):
    c, conn = env
    ids = [add(conn, f"2-к квартира {50 + i} м², 3/9 эт., ул. Ставропольская {10 + i}. 6 млн. 8918000001{i}")["listing_id"]
           for i in range(3)]
    first = c.get("/api/admin/fix?mode=place&kind=nodistrict").json()
    assert first["left"] == 3 and first["item"]["suggest"]["districts"]          # подсказки районов по улице
    a = first["item"]["id"]
    # пропустили a — следующий другой, a не возвращается, пока есть непросмотренные
    b = c.get(f"/api/admin/fix?mode=place&kind=nodistrict&exclude={a}").json()["item"]["id"]
    c.post(f"/api/admin/listings/{b}/place", json={"district": "ЧМР", "learn": {}})
    nxt = c.get(f"/api/admin/fix?mode=place&kind=nodistrict&exclude={a},{b}").json()
    assert nxt["item"]["id"] not in (a, b) and nxt["left"] == 2
    # поправленный «без района» больше не возвращается в очередь
    c.post(f"/api/admin/listings/{nxt['item']['id']}/place", json={"district": "", "learn": {}})
    assert c.get("/api/admin/fix?mode=place&kind=nodistrict").json()["left"] == 1
    assert "fix.js" in c.get("/fix").text


def test_street_learns_district_and_custom_district(env):
    from app import geo
    c, conn = env
    a = add(conn, "2-к квартира 50 м², 3/9 эт., ул. Новаторов 7. 6 млн. 89180000021")["listing_id"]
    # своего района нет в справочнике — добавляем
    r = c.post("/api/admin/districts", json={"name": "Новые Сады", "aliases": "новосады"}).json()
    assert "Новые Сады" in r["districts"] and r["districts"] == sorted(r["districts"], key=lambda x: x.lower())
    c.post(f"/api/admin/listings/{a}/place", json={"district": "Новые Сады", "learn": {"street": True}})
    # другая квартира на той же улице (другой дом) — сразу в этом районе
    b = add(conn, "1-к квартира 38 м², 5/9 эт., ул. Новаторов 15. 4 млн. 89180000022")["listing_id"]
    assert row(conn, b)["district"] == "Новые Сады"
    # новый район узнаётся в тексте и в фильтре
    assert geo.canonical_district("новосады") == "Новые Сады"
    assert search.search(conn, search.Query(districts=["Новые Сады"]), NOW, False)["total"] == 2
    item = c.get(f"/api/admin/fix?mode=place&kind=nodistrict&id={b}").json()
    assert "Новые Сады" in item["districts"] and item["complexes"]


def test_rename_move_and_several_districts(env):
    from app import geo
    c, conn = env
    a = add(conn, "2-к квартира 50 м², 3/9 эт., ФМР, ул. Тургенева 10. 6 млн. 89180000031")["listing_id"]
    b = add(conn, "1-к квартира 38 м², 5/9 эт., ЖК Мозаика, ФМР. 4 млн. 89180000032")["listing_id"]
    # объект на границе: основной ФМР + ещё ЦМР — находится по фильтру любого из них
    c.post(f"/api/admin/listings/{a}/place", json={"district": "ФМР", "extra": ["ЦМР"], "learn": {}})
    assert a in [i["id"] for i in search.search(conn, search.Query(districts=["ЦМР"]), NOW, False)["items"]]
    facets = {f["name"]: f["n"] for f in search.facets(conn, search.Query(), NOW)["districts"]}
    assert facets["ЦМР"] == 1 and facets["ФМР"] == 2
    # перенести ЖК в другой район — сразу и для будущих объявлений
    assert c.post("/api/admin/districts", json={"action": "move", "kind": "complex", "name": "Мозаика",
                                                "from": "ФМР", "to": "ЮМР"}).json()["listings"] == 1
    assert row(conn, b)["district"] == "ЮМР"
    b2 = add(conn, "Студия 25 м², 2/9 эт., ЖК Мозаика. 3 млн. 89180000033")["listing_id"]
    assert row(conn, b2)["district"] == "ЮМР"
    # перенести улицу: объекты этой улицы из ФМР — в ГМР
    c.post("/api/admin/districts", json={"action": "move", "kind": "street", "name": "Тургенева", "from": "ФМР", "to": "ГМР"})
    assert row(conn, a)["district"] == "ГМР"
    detail = c.get("/api/admin/districts?name=ГМР").json()
    assert detail["streets"][0]["name"] == "Тургенева"
    # переименовать район: объекты, фильтр, текст («юмр» всё ещё узнаётся)
    r = c.post("/api/admin/districts", json={"action": "rename", "old": "ЮМР", "new": "Юбилейный"}).json()
    assert r["listings"] == 2 and "Юбилейный" in r["districts"] and "ЮМР" not in r["districts"]
    assert row(conn, b)["district"] == "Юбилейный"
    assert geo.canonical_district("юмр") == "Юбилейный"
    b3 = add(conn, "Студия 26 м², 3/9 эт., ЖК Мозаика. 3,1 млн. 89180000034")["listing_id"]
    assert row(conn, b3)["district"] == "Юбилейный"          # правило «ЖК → район» тоже переименовалось


def test_directory_lists_and_admin_only(env):
    c, conn = env
    add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Мозаика, ФМР. 6 млн. 89180000041")
    add(conn, "1-к квартира 38 м², 5/9 эт., ул. Тургенева 12, ФМР. 4 млн. 89180000042")
    cx = c.get("/api/admin/districts?dir=complex").json()["items"]
    moz = next(x for x in cx if x["name"] == "Мозаика")
    assert moz["count"] == 1 and moz["districts"][0][0] == "ФМР"
    st = c.get("/api/admin/districts?dir=street").json()["items"]
    assert st[0]["street"] == "Тургенева"
    # всё это — только для администратора
    c.cookies.clear()
    for url in ("/api/admin/districts?dir=complex", "/api/admin/fix?mode=place", "/api/admin/place-queue",
                "/api/admin/geo-queue"):
        assert c.get(url).status_code in (401, 403)
    assert c.post("/api/admin/districts", json={"action": "rename", "old": "ФМР", "new": "X"}).status_code in (401, 403)
    assert c.post("/api/admin/listings/1/place", json={"district": "ЦМР"}).status_code in (401, 403)
    assert c.post("/api/admin/listings/1/geo", json={"lat": 45.0, "lon": 39.0}).status_code in (401, 403)

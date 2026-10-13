"""Самообучение: точки и районы из того, что сервис уже знает (фид, контуры ЖК и районов, соседние объявления)."""
import pytest

from app import autolearn, districtmap

FMR = [[(45.04, 38.99), (45.04, 39.02), (45.06, 39.02), (45.06, 38.99)]]
MOZAIKA = [[(45.050, 39.000), (45.050, 39.004), (45.053, 39.004), (45.053, 39.000)]]


@pytest.fixture(autouse=True)
def _fresh_polys():
    districtmap._cache.update(t=0.0, polys=[])
    yield
    districtmap._cache.update(t=0.0, polys=[])


def _lid(res):
    return res["results"][0]["listing_id"]


def _row(conn, lid):
    return conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()


def test_point_from_complex_contour_and_feed_house(conn, add):
    districtmap.save_polygon(conn, "Мозаика", "complex", MOZAIKA)
    a = _lid(add("2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233"))
    # объект фида с точкой — тот же дом в чате получает его точку
    b = _lid(add("1-к квартира 40 м², 3/9 эт., ул. Тургенева 33. 5 млн. 89184445566"))
    conn.execute("""INSERT INTO listings (source, type, deal, street, house, lat, lon, geo_status, is_active, first_seen,
                    last_seen, title) VALUES ('feed', 'flat', 'sale', 'Тургенева', '33', 45.07, 38.97, 'feed', 1, 1, 1, 'фид')""")
    conn.commit()
    assert autolearn.fill_points(conn) == 2
    ra, rb = _row(conn, a), _row(conn, b)
    assert ra["geo_src"] == "map-cx" and 45.050 < ra["lat"] < 45.053 and 39.0 < ra["lon"] < 39.004
    assert (rb["lat"], rb["lon"], rb["geo_src"]) == (45.07, 38.97, "feed-house")


def test_geocoder_answer_checked_against_district(conn, add):
    districtmap.save_polygon(conn, "ФМР", "district", FMR)
    lid = _lid(add("2-к квартира 50 м², 3/9 эт., ФМР, ул. Новаторов 7. 6 млн. 89180000061"))
    known = autolearn.Known(conn)
    r = _row(conn, lid)
    assert r["district"] == "ФМР"
    assert known.plausible(r, 45.05, 39.00)          # внутри
    assert known.plausible(r, 45.065, 39.00)         # чуть за границей (≈0,5 км) — допускаем
    assert not known.plausible(r, 45.20, 38.80)      # другой конец города — ошибка геокодера
    assert known.district_center("ФМР") is not None


def test_district_by_votes_and_by_map(conn, add):
    # три объявления ЖК Мозаика с районом → четвёртому без района ставим тот же район
    for i in range(3):
        add(f"{i + 1}-к квартира {40 + i} м², {i + 2}/16 эт., ЖК Мозаика, ФМР. {5 + i} млн. 8918111223{i}")
    lid = _lid(add("Студия 25 м², 5/16 эт., ЖК Мозаика. 3,9 млн. 89187770000"))
    conn.execute("UPDATE listings SET district = NULL WHERE id = ?", (lid,))
    conn.commit()
    res = autolearn.fill_districts(conn)
    r = _row(conn, lid)
    assert res["districts"] == 1 and r["district"] == "ФМР" and r["district_src"] == "vote-cx"
    # точка внутри контура района — район по карте
    districtmap.save_polygon(conn, "Катюша", "district", [[(45.00, 38.90), (45.00, 38.92), (45.02, 38.92), (45.02, 38.90)]])
    o = _lid(add("Дом 120 м², участок 4 сот., ул. Тихорецкая 5. 9 млн. 89181112299"))
    conn.execute("UPDATE listings SET district = NULL, lat = 45.01, lon = 38.91, geo_status = 'ok' WHERE id = ?", (o,))
    conn.commit()
    autolearn.fill_districts(conn)
    assert (_row(conn, o)["district"], _row(conn, o)["district_src"]) == ("Катюша", "map")


def test_disputed_district_is_left_to_admin(conn, add):
    for i in range(3):
        add(f"{i + 1}-к квартира {40 + i} м², {i + 2}/16 эт., ЖК Мозаика, ФМР. {5 + i} млн. 8918111223{i}")
    districtmap.save_polygon(conn, "Катюша", "district", [[(45.00, 38.90), (45.00, 38.92), (45.02, 38.92), (45.02, 38.90)]])
    lid = _lid(add("Студия 25 м², 5/16 эт., ЖК Мозаика. 3,9 млн. 89187770000"))
    conn.execute("UPDATE listings SET district = NULL, lat = 45.01, lon = 38.91, geo_status = 'ok' WHERE id = ?", (lid,))
    conn.commit()
    res = autolearn.fill_districts(conn)   # по карте — Катюша, по соседям — ФМР: спор
    assert res["disputed"] == 1 and _row(conn, lid)["district"] is None


def test_fix_queue_groups_and_applies_to_group(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import accounts, config, db, ingest, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()

    def add(text, n=[0]):
        n[0] += 1
        mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"g{n[0]}")
        return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    ids = [add(f"{i + 1}-к квартира {40 + i} м², {i + 2}/16 эт., ЖК Новый Мир, ул. Мира {i + 1}. {5 + i} млн. 8918555000{i}")
           for i in range(3)]
    one = add("Дом 120 м², участок 4 сот., ул. Тихорецкая 5. 9 млн. 89181112299")
    conn.execute(f"UPDATE listings SET district = NULL WHERE id IN ({','.join(map(str, ids + [one]))})")
    u = accounts.upsert_email_user(conn, "boss@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        d = c.get("/api/admin/fix?mode=place&kind=nodistrict").json()
        assert d["group"] and d["group"]["count"] == 3 and d["item"]["id"] in ids   # самая большая группа — первой
        others = [i for i in d["group"]["ids"] if i != d["item"]["id"]]
        r = c.post(f"/api/admin/listings/{d['item']['id']}/place",
                   json={"complex": "Новый Мир", "district": "ФМР", "group_ids": others, "group_same_place": True}).json()
        assert r["ok"] and r["applied"] >= 2
        assert {_row(conn, i)["district"] for i in ids} == {"ФМР"}
        # группа разобрана — дальше одиночный объект без группы
        d = c.get("/api/admin/fix?mode=place&kind=nodistrict").json()
        assert d["item"]["id"] == one and d["group"] is None
        # район под точкой — по контурам
        districtmap.save_polygon(conn, "ФМР", "district", FMR)
        assert c.get("/api/admin/fix?at=45.05,39.0").json()["district"] == "ФМР"
    conn.close()
    db._local.conn = None


KAT = [[(45.00, 38.90), (45.00, 38.92), (45.02, 38.92), (45.02, 38.90)]]


def _put(conn, lid, lat, lon, district="ФМР"):
    conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = 'ok', geo_src = 'osm-house', district = ? WHERE id = ?",
                 (lat, lon, district, lid))
    conn.commit()


def test_recheck_all_moves_district_by_drawn_map_and_rolls_back(conn, add):
    districtmap.save_polygon(conn, "ФМР", "district", FMR)
    districtmap.save_polygon(conn, "Катюша", "district", KAT)
    far = _lid(add("2-к квартира 50 м², 3/9 эт., ФМР, ул. Новаторов 7. 6 млн. 89180000061"))
    _put(conn, far, 45.01, 38.91)                       # дом в Катюше, в объявлении «ФМР» → Катюша
    border = _lid(add("1-к квартира 40 м², 2/9 эт., ФМР, ул. Новаторов 9. 5 млн. 89180000062"))
    districtmap.save_polygon(conn, "ЦМР", "district", [[(45.06, 38.99), (45.06, 39.02), (45.08, 39.02), (45.08, 38.99)]])
    _put(conn, border, 45.0605, 39.00)                  # дом в ЦМР, но в 55 м от ФМР — район агента оставляем
    sub = _lid(add("Студия 25 м², 2/9 эт., Гидрострой, ул. Тихая 3. 3 млн. 89180000063"))
    _put(conn, sub, 45.01, 38.91, "Гидрострой")          # у Гидростроя контура нет → Катюша вторым районом
    rep = autolearn.recheck_all(conn)
    assert rep["districts_changed"] == 1 and rep["kept_border"] == 1 and rep["districts_extra"] == 1
    assert _row(conn, far)["district"] == "Катюша" and _row(conn, far)["district_src"] == "map"
    assert _row(conn, border)["district"] == "ФМР"
    assert _row(conn, sub)["district"] == "Гидрострой" and "Катюша" in _row(conn, sub)["extra_districts"]
    # откат — всё как было, и повторная перепроверка откаченное не трогает
    assert autolearn.rollback_recheck(conn) == 2
    assert _row(conn, far)["district"] == "ФМР" and "Катюша" not in _row(conn, sub)["extra_districts"]
    rep = autolearn.recheck_all(conn)
    assert not rep.get("districts_changed") and _row(conn, far)["district"] == "ФМР"


def test_recheck_keeps_admin_rules_and_fixes(conn, add):
    from app import learning
    districtmap.save_polygon(conn, "ФМР", "district", FMR)
    districtmap.save_polygon(conn, "Катюша", "district", KAT)
    a = _lid(add("2-к квартира 50 м², 3/9 эт., ФМР, ЖК Мозаика. 6 млн. 89180000071"))
    _put(conn, a, 45.01, 38.91)
    learning.learn(conn, _row(conn, a), "Мозаика", "ФМР", {"cx_district": True})   # ваше правило: ЖК Мозаика → ФМР
    b = _lid(add("1-к квартира 40 м², 2/9 эт., ФМР, ул. Новаторов 9. 5 млн. 89180000072"))
    _put(conn, b, 45.01, 38.91)
    conn.execute("UPDATE listings SET admin_fixed = 1 WHERE id = ?", (b,))
    conn.commit()
    h = _lid(add("2-к квартира 52 м², 4/9 эт., ФМР, ул. Новаторов 11. 6 млн. 89180000073"))
    _put(conn, h, 45.01, 38.91)
    learning.learn(conn, _row(conn, h), None, "ФМР", {"addr": True})   # правило дома — главнее контура
    rep = autolearn.recheck_all(conn)
    # ТЗ «админка-гео» P0.0.1: контур под точкой дома главнее правила «ЖК → район»; правило дома и правка админа — главнее контура
    assert _row(conn, a)["district"] == "Катюша" and _row(conn, b)["district"] == "ФМР"
    assert rep.get("kept_rule") == 1 and _row(conn, h)["district"] == "ФМР"

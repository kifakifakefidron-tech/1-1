"""16.11: подсказки места (угол улиц, ориентир, объяснение админа, чат, стык районов) и спор правил с нейросетью."""
import json
import time

import pytest

from app import autolearn, brain, districtmap, geo, ingest, osmaddr, parser, placehints

TSV = "\n".join(
    [f"45.05{i:02d}\t38.97{i:02d}\tКрасная улица\t{i + 1}\t" for i in range(20)]          # идёт на северо-восток
    + [f"45.0510\t{38.960 + i / 1000:.4f}\tСеверная улица\t{i + 1}\t" for i in range(20)]   # пересекает Красную у 45.051
    + [f"45.10{i:02d}\t39.10\tСтавропольская улица\t{i + 1}\t" for i in range(5)]        # далеко — не пересекается
    + [f"45.01\t38.91\tТестовая улица\t{i}\t" for i in range(1000)])

PLACES = "\n".join(
    ["45.0400\t38.9700\tГалерея\t\tmall\t\t\t", "45.0450\t38.9800\tМБОУ СОШ № 101\t\t\tschool\t\t",
     "45.0300\t38.9600\tСеверный рынок\t\t\tmarketplace\t\t"]
    + [f"45.{i:04d}\t38.9\tШкола № {200 + i}\t\t\tschool\t\t" for i in range(60)])


@pytest.fixture(autouse=True)
def _fresh():
    for c in (osmaddr._index, placehints._places, placehints._phr, placehints._hint_cache, placehints._cross_cache):
        c.clear()
    placehints._chat["t"] = 0
    districtmap._cache["t"] = 0
    yield
    districtmap._cache["t"] = 0


def poly(conn, name, lat0, lat1, lon0, lon1):
    ring = [[lat0, lon0], [lat0, lon1], [lat1, lon1], [lat1, lon0]]
    conn.execute("""INSERT INTO district_polygons (name, kind, source, polys, area, lat_min, lat_max, lon_min, lon_max, ts)
                    VALUES (?, 'district', 'admin', ?, ?, ?, ?, ?, ?, 0)""",
                 (name, json.dumps([ring]), (lat1 - lat0) * (lon1 - lon0), lat0, lat1, lon0, lon1))
    conn.commit()


def test_corner_only_if_streets_cross(conn):
    osmaddr.load(conn, TSV)
    c = placehints.corner(conn, "2-к квартира на углу Красной и Северной, 5 этаж")
    assert c and {c["a"], c["b"]} == {"Красная", "Северная"} and abs(c["lat"] - 45.051) < 0.001
    assert placehints.corner(conn, "Красная / Северная, дом кирпичный")
    assert placehints.corner(conn, "угол Ставропольской и Северной") is None   # улицы далеко друг от друга


def test_landmarks(conn):
    assert placehints.load_places(conn, PLACES) > 50
    lm = placehints.landmark(conn, "Студия рядом с ТЦ Галерея, 3 мин пешком")
    assert lm and lm["kind"] == "mall" and lm["lat"] == 45.04
    assert placehints.landmark(conn, "у трц галерее")["kind"] == "mall"
    assert placehints.landmark(conn, "Рядом школа 101 и садик")["lat"] == 45.045
    assert placehints.landmark(conn, "пять минут до Северного рынка")["kind"] == "market"
    assert placehints.landmark(conn, "Рядом с домом парк и школа") is None
    assert placehints.place_keys("school", "Гимназия на улице Красной № 23") == ["гимназия 23"]


def test_landmark_gives_point_and_district(conn, add):
    placehints.load_places(conn, PLACES)
    poly(conn, "Тест-район", 45.03, 45.05, 38.96, 38.99)
    r = add("1-к квартира 38 м², 4/9 эт., рядом с ТЦ Галерея. 4,5 млн. 89180000011")
    lid = r["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET district = NULL, complex = NULL, street = NULL, geo_status = 'pending' WHERE id = ?", (lid,))
    conn.commit()
    autolearn.fill_points(conn)
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    assert (row["lat"], row["geo_src"], row["geo_status"]) == (45.04, "osm-landmark", "approx")
    assert row["district"] == "Тест-район" and row["district_src"] == "landmark"


def test_explained_phrase_applies_now_and_later(conn, add):
    lid = add("2-к квартира 52 м², 3/9 эт., за Красной площадью. 6 млн. 89180000021")["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET district = NULL WHERE id = ?", (lid,))
    conn.commit()
    d = geo.canonical_district("ЦМР")
    res = placehints.learn_phrase(conn, "за Красной площадью", {"district": d}, user_id=1)
    assert res["ok"] and res["changed"] == 1
    row = conn.execute("SELECT district, district_src FROM listings WHERE id = ?", (lid,)).fetchone()
    assert tuple(row) == (d, "phrase")
    assert conn.execute("SELECT src FROM listing_edits WHERE listing_id = ?", (lid,)).fetchone()[0] == "объяснение адреса"
    lid2 = add("Студия 24 м², 7/16 эт., за красной площадью, 3,9 млн. 89180000022")["results"][0]["listing_id"]
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (lid2,)).fetchone()[0] == d
    assert placehints.learn_phrase(conn, "ул", {"district": d}).get("error")
    assert placehints.learn_phrase(conn, "какие-то слова", {}).get("error")


def test_chat_district_is_weak_fallback(conn):
    d = geo.canonical_district("ФМР")
    conn.execute("INSERT INTO chats (source, chat_id, name) VALUES ('wa', 'fmr', 'Квартиры ФМР — продажа')")
    conn.commit()
    mid = ingest.add_message(conn, source="wa", text="2-к квартира 50 м², 3/9 эт., 6 млн. 89180000031",
                             chat_id="fmr", msg_id="c1", ts=int(time.time()))
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET district = NULL WHERE id = ?", (lid,))
    conn.commit()
    autolearn.fill_districts(conn)
    row = conn.execute("SELECT district, district_src FROM listings WHERE id = ?", (lid,)).fetchone()
    assert tuple(row) == (d, "chat")
    from app import search
    item = search.listing_detail(conn, lid, True)
    assert item["district_note"] == placehints.WEAK["chat"]


def test_border_house_searchable_in_both(conn, add):
    poly(conn, "Тест-1", 45.00, 45.02, 38.90, 38.95)
    poly(conn, "Тест-2", 45.02, 45.04, 38.90, 38.95)
    lid = add("2-к квартира 50 м², 3/9 эт., ул. Тестовая 5. 6 млн. 89180000041")["results"][0]["listing_id"]
    conn.execute("""UPDATE listings SET district = 'Тест-1', lat = 45.0196, lon = 38.92, geo_status = 'ok', geo_src = 'osm-house'
                    WHERE id = ?""", (lid,))
    conn.commit()
    assert placehints.border(conn) == 1
    row = conn.execute("SELECT extra_districts, border_districts FROM listings WHERE id = ?", (lid,)).fetchone()
    assert json.loads(row[0]) == ["Тест-2"] and json.loads(row[1]) == ["Тест-2"]
    conn.execute("UPDATE listings SET lat = 45.010 WHERE id = ?", (lid,))
    conn.commit()
    placehints.border(conn)
    row = conn.execute("SELECT extra_districts, border_districts FROM listings WHERE id = ?", (lid,)).fetchone()
    assert json.loads(row[0]) == [] and row[1] is None


def test_rules_vs_ai_dispute_goes_to_queue(conn):
    frag = "2-к квартира 45 м², 5/9 эт., ул. Тестовая 10. 6 млн. 89180000051"
    o = parser._build({"rooms": 3, "area_m2": 60, "floor": 5, "price_rub": 6_000_000}, frag, frag, None)
    assert set(o.disputes) == {"rooms", "area"} and o.disputes["rooms"] == {"rules": 2, "ai": 3}
    assert parser._build(None, frag, frag, None).disputes == {}
    lid, _ = ingest.save_object(conn, o, None, int(time.time()))
    conn.commit()
    assert json.loads(conn.execute("SELECT parse_dispute FROM listings WHERE id = ?", (lid,)).fetchone()[0])["area"]["ai"] == 60
    assert brain.counts(conn)["dispute"] == 1


def test_unparsed_report_and_explain_api(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from app import accounts, config, db, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    conn = db.get()
    u = accounts.upsert_email_user(conn, "admin@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    for i in range(2):
        mid = ingest.add_message(conn, source="wa", chat_id="c", msg_id=f"u{i}", ts=int(time.time()),
                                 text=f"{i + 1}-к квартира {40 + i * 10} м², {i + 2}/9 эт.\nрядом с Пушкинским парком\n{5 + i} млн. 8918000006{i}")
        lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
        conn.execute("UPDATE listings SET district = NULL WHERE id = ?", (lid,))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        items = c.get("/api/admin/unparsed?fresh=1").json()["items"]
        hit = [x for x in items if "пушкинским" in x["line"].lower()]
        assert hit and hit[0]["n"] == 2
        d = geo.canonical_district("ЦМР")
        res = c.post("/api/admin/place-phrase", json={"phrase": "рядом с Пушкинским парком", "district": d}).json()
        assert res["changed"] == 2
        items = c.get("/api/admin/unparsed").json()["items"]
        assert not [x for x in items if "пушкинским" in x["line"].lower()]
        assert c.post("/api/admin/place-phrase", json={"phrase": "ab"}).status_code == 400
    conn.close()
    db._local.conn = None


def test_queues_do_not_overlap(conn, add):
    """16.11: объект без района и без точки — только в «Без района»; примерная точка без дома/ЖК — не в очереди;
    примерные точки — не в выборке «Проверить решения сервиса»."""
    a = add("2-к квартира 50 м², 3/9 эт., ул. Тестовая. 6 млн. 89180000071")["results"][0]["listing_id"]
    b = add("1-к квартира 40 м², 2/9 эт., ул. Тестовая 7. 5 млн. 89180000072")["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET district = NULL, lat = NULL, geo_status = 'none' WHERE id = ?", (a,))
    conn.execute("""UPDATE listings SET district = 'ЦМР', lat = 45.0, lon = 38.9, geo_status = 'approx', geo_src = 'osm-street'
                    WHERE id = ?""", (b,))
    conn.commit()
    ids = lambda key: {r[0] for r in conn.execute(  # noqa: E731
        f"SELECT l.id FROM listings l WHERE {brain.VISIBLE} AND {brain.QUEUE[key]['where']}")}
    assert a in ids("nodistrict") and a not in ids("nogeo")
    assert b in ids("approx") and b not in ids("auto")
    conn.execute("UPDATE listings SET house = NULL WHERE id = ?", (b,))
    conn.commit()
    assert b not in ids("approx")   # дома нет — уточнять нечего


def test_fix_screen_survives_broken_hint(tmp_path, monkeypatch):
    """17.11: одна подсказка экрана разбора упала — объект всё равно открывается, админ видит, что именно сломалось."""
    from starlette.testclient import TestClient

    from app import accounts, config, db, server
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    conn = db.get()
    u = accounts.upsert_email_user(conn, "admin@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    mid = ingest.add_message(conn, source="wa", chat_id="c", msg_id="f1", ts=int(time.time()),
                             text="2-к квартира 50 м², 3/9 эт., ул. Тестовая 5. 6 млн. 89180000081")
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET district = NULL WHERE id = ?", (lid,))
    conn.commit()

    def boom(*a, **k):
        raise RuntimeError("тест")
    monkeypatch.setattr(server, "_fix_suggest", boom)
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        d = c.post("/api/admin/fix?kind=nodistrict", json={"exclude": []}).json()
        assert d["item"]["id"] == lid and d["item"]["suggest"] == {"districts": [], "complexes": []}
        assert any("подсказки районов" in w for w in d["warnings"])
        monkeypatch.setattr(server, "_fix_groups", boom)
        r = c.post("/api/admin/fix?kind=nodistrict", json={"exclude": []})
        assert r.status_code == 500 and "RuntimeError" in r.json()["detail"]
    conn.close()
    db._local.conn = None

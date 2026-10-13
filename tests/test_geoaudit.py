"""ТЗ «админка-гео» P0.0: правило против карты районов находится; отключение перерешает объекты по контуру под домом,
пишет журнал правок, каждую правку можно откатить по объекту. ЖК-мусор не принимается."""
import json
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, districtmap, geoaudit, ingest, server


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    districtmap._cache["t"] = 0
    conn = db.get()
    u = accounts.upsert_email_user(conn, "admin@example.ru")
    conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (u["id"],))
    conn.commit()
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None
    districtmap._cache["t"] = 0


def poly(conn, name, lat0, lat1, lon0, lon1):
    ring = [[lat0, lon0], [lat0, lon1], [lat1, lon1], [lat1, lon0]]
    conn.execute("""INSERT INTO district_polygons (name, kind, source, polys, area, lat_min, lat_max, lon_min, lon_max, ts)
                    VALUES (?, 'district', 'admin', ?, ?, ?, ?, ?, ?, 0)""",
                 (name, json.dumps([ring]), (lat1 - lat0) * (lon1 - lon0), lat0, lat1, lon0, lon1))


def add(conn, text, lat, lon, n=[0]):
    n[0] += 1
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=f"ga{n[0]}", ts=int(time.time()))
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = 'ok', geo_src = 'osm-house' WHERE id = ?", (lat, lon, lid))
    return lid


def test_bad_street_rule_found_and_disabled_with_journal(env):
    c, conn = env
    poly(conn, "ФМР", 45.00, 45.02, 38.90, 38.95)
    poly(conn, "ЦМР", 45.02, 45.04, 38.90, 38.95)
    conn.commit()
    ids = [add(conn, f"2-к квартира 5{i} м², {i + 1}/9 эт., ул. Тестовая {10 + i}. 6 млн. 8918000010{i}", 45.03, 38.92) for i in range(4)]
    # ошибочное правило: «Тестовая → ФМР», хотя все дома по карте в ЦМР
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('street', ?, ?, 'ул. Тестовая → ФМР', 3, 0)""",
                 ("street:|тестовая", json.dumps({"district": "ФМР"})))
    conn.execute("UPDATE listings SET district = 'ФМР'")
    conn.commit()
    rep = c.get("/api/admin/geo-audit").json()
    bad = [r for r in rep["rules"] if r["status"] == "bad"]
    assert len(bad) == 1 and bad[0]["dist"] == {"ЦМР": 4} and bad[0]["share"] == 0
    res = c.post("/api/admin/geo-audit", json={"rules": [bad[0]["id"]]}).json()
    assert res["rules"] == 1 and res["listings"] == 4
    assert {r[0] for r in conn.execute("SELECT district FROM listings")} == {"ЦМР"}
    hist = c.get(f"/api/admin/edits?listing={ids[0]}").json()["items"]
    assert hist[0]["field"] == "district" and hist[0]["old"] == "ФМР" and hist[0]["new"] == "ЦМР" and hist[0]["who"] == "чистка правил"
    assert c.post("/api/admin/edits", json={"id": hist[0]["id"]}).json()["ok"]
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (ids[0],)).fetchone()[0] == "ФМР"


def test_junk_complex_not_accepted():
    assert geoaudit.is_junk_complex("ЖК Краснодар") and geoaudit.is_junk_complex("жилой квартал")
    assert not geoaudit.is_junk_complex("Самолёт 2")


def test_broken_rule_label_and_district_fixed(env):
    """«ЖК Мечта → Губернский/Губернский/Любимово»: район в правиле — склеенная строка → один район + доп. район."""
    from app import geo, learning
    c, conn = env
    gub = geo.canonical_district("Губернский")
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('cx_district', 'cx:мечта', ?, ?, 1, 0)""",
                 (json.dumps({"district": "Губернский/Губернский/Любимово", "extra": ["Любимово"]}, ensure_ascii=False),
                  "ЖК Мечта → Губернский/Губернский/Любимово, Любимово"))
    conn.commit()
    assert learning.clean_districts("Губернский/Губернский/Любимово", ["Любимово", "Губернский"]) == (gub, ["Любимово"])
    assert learning.clean_districts("Мой/Район") == ("Мой/Район", [])   # свой район с «/» не ломаем
    rep = c.get("/api/admin/geo-audit").json()
    b = next(x for x in rep["broken"] if x["label"].startswith("ЖК Мечта"))
    assert b["new_district"] == gub and b["extra"] == ["Любимово"] and b["new_label"] == f"ЖК Мечта → {gub}, Любимово"
    assert c.post("/api/admin/geo-audit", json={"broken": [b["id"]]}).json()["broken"] == 1
    v = json.loads(conn.execute("SELECT value FROM learned_rules WHERE key = 'cx:мечта'").fetchone()[0])
    assert v == {"district": gub, "extra": ["Любимово"]}
    assert not c.get("/api/admin/geo-audit").json()["broken"]


def test_learned_points_checked_by_address_book(env):
    """Выученная точка дома без посёлка: дом в Краснодаре рядом — верна; далеко / улицы нет — забыть, объекты в «Спорные»."""
    from app import osmaddr
    c, conn = env
    osmaddr._index.clear()
    osmaddr.load(conn, "\n".join(["45.0520\t38.9790\tКрасная улица\t10\t", "45.0600\t38.9900\tЗеленая улица\t5\t"]
                                 + [f"45.01\t38.91\tТестовая улица\t{i}\t" for i in range(1000)]))
    for key, lat, lon in (("addr:|красная|10", 45.0521, 38.9791), ("addr:|зеленая|5", 45.10, 39.10),
                          ("addr:|первомайская|36к6", 45.20, 39.20)):
        conn.execute("INSERT INTO geo_learned (key, lat, lon, n, ts, label) VALUES (?,?,?,1,0,?)", (key, lat, lon, key))
    lid = add(conn, "2-к квартира 50 м², 3/9 эт., ул. Первомайская 36к6. 6 млн. 89180000201", 45.20, 39.20)
    conn.execute("UPDATE listings SET geo_status = 'learned', geo_src = 'learned' WHERE id = ?", (lid,))
    conn.commit()
    rep = c.get("/api/admin/geo-audit").json()
    st = {p["key"]: p["status"] for p in rep["points"]}
    assert st == {"addr:|зеленая|5": "far", "addr:|первомайская|36к6": "nostreet"} and rep["points_ok"] == 1
    res = c.post("/api/admin/geo-audit", json={"points": ["addr:|первомайская|36к6"]}).json()
    assert res["points"] == 1 and res["to_queue"] == 1
    assert conn.execute("SELECT place_conflict FROM listings WHERE id = ?", (lid,)).fetchone()[0].startswith("point:")
    assert not conn.execute("SELECT 1 FROM geo_learned WHERE key = 'addr:|первомайская|36к6'").fetchone()
    osmaddr._index.clear()


def test_complex_point_from_one_house_recomputed_and_reverted(env):
    """Точка ЖК = точка одного поправленного дома → середина известных домов; объект ЖК без дома переезжает, откат по объекту."""
    from app import geocode
    c, conn = env
    h1 = add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Облака, ул. Корнилова 10. 6 млн. 89180000211", 45.0500, 38.9800)
    h2 = add(conn, "1-к квартира 40 м², 4/9 эт., ЖК Облака, ул. Корнилова 12. 5 млн. 89180000212", 45.0520, 38.9820)
    flat = add(conn, "Студия 25 м², 2/9 эт., ЖК Облака. 3 млн. 89180000213", 45.0500, 38.9800)
    conn.execute("UPDATE listings SET geo_status = 'learned', geo_src = 'learned' WHERE id = ?", (flat,))
    conn.execute("INSERT INTO geo_learned (key, lat, lon, n, ts, label) VALUES ('addr:|корнилова|10', 45.05, 38.98, 1, 0, 'Корнилова, 10')")
    conn.execute("INSERT INTO geo_learned (key, lat, lon, n, ts, label) VALUES ('cx:облака', 45.05, 38.98, 1, 0, 'ЖК Облака')")
    conn.commit()
    cx = c.get("/api/admin/geo-audit").json()["cx_points"]
    assert len(cx) == 1 and cx[0]["one_house"] and cx[0]["new"] == [45.051, 38.981] and cx[0]["listings"] == 1
    c.post("/api/admin/geo-audit", json={"cx_points": ["cx:облака"]})
    r = conn.execute("SELECT lat, lon, geo_status FROM listings WHERE id = ?", (flat,)).fetchone()
    assert (r[0], r[1], r[2]) == (45.051, 38.981, "approx")
    assert conn.execute("SELECT lat FROM listings WHERE id = ?", (h1,)).fetchone()[0] == 45.05   # сам дом — на месте
    hist = c.get(f"/api/admin/edits?listing={flat}").json()["items"]
    assert hist[0]["field"] == "point"
    c.post("/api/admin/edits", json={"id": hist[0]["id"]})
    assert conn.execute("SELECT lat, geo_status FROM listings WHERE id = ?", (flat,)).fetchone()[:] == (45.05, "learned")
    # новая ручная точка дома больше не становится «центром ЖК»
    conn.execute("DELETE FROM geo_learned")
    geocode.learn(conn, conn.execute("SELECT * FROM listings WHERE id = ?", (h2,)).fetchone(), 45.052, 38.982)
    assert [k for (k,) in conn.execute("SELECT key FROM geo_learned")] == ["addr:|корнилова|12"]


def test_complex_key_normalizes_audit_variants():
    """ТЗ P1.1: варианты написания из аудита дают один ключ; разрядка и адрес в скобках убираются из названия."""
    from app import geo
    for group in (["Самолет 3", "Самолёт 3", "Самолёт -3", "Самолет-3", "Самолёт-3", "Самолёт 3 очередь", "ЖК «Самолет 3»"],
                  ["Лучший 2", "Лучший-2", "Лучший -2"], ["Акварели-2", "Акварели 2", "Акварели - 2"],
                  ["Клубный дом", "Клубный Дом"], ["О Т Р А Ж Е Н И Е", "Отражение"]):
        assert len({geo.complex_key(n) for n in group}) == 1, group
    assert geo.pretty_name("О Т Р А Ж Е Н И Е") == "Отражение"
    assert geo.pretty_name("Инсити (Российская улица, 267/3к1)") == "Инсити"
    assert geo.complex_key("Гарантия на Обрывной") != geo.complex_key("Гарантия на Дежнева")


def test_district_dupe_merged_with_journal(env):
    """«Молодёжный» при районе справочника «Молодежный/Витаминкомбинат» — слить (объекты в журнал, откат по объекту)."""
    from app import learning
    c, conn = env
    learning.rename_district(conn, "Молодёжный", "Молодежный/Витаминкомбинат")   # так на сервере назвал район админ
    lid = add(conn, "2-к квартира 50 м², 3/9 эт., ул. Тестовая 1. 6 млн. 89180000301", 45.0, 38.9)
    conn.execute("UPDATE listings SET district = 'Молодёжный' WHERE id = ?", (lid,))
    conn.commit()
    d = next(x for x in c.get("/api/admin/geo-audit").json()["dupes"] if x["name"] == "Молодёжный")
    assert d["to"] == "Молодежный/Витаминкомбинат" and d["listings"] == 1
    assert c.post("/api/admin/geo-audit", json={"dupes": ["Молодёжный"]}).json()["dupes"] == 1
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (lid,)).fetchone()[0] == "Молодежный/Витаминкомбинат"
    assert c.get(f"/api/admin/edits?listing={lid}").json()["items"][0]["old"] == "Молодёжный"


def test_map_groups_complex_level_and_hides_vague(env):
    """ТЗ P2.1: центр ЖК — одна метка ЖК с числом объектов; середина улицы/центр района — не метка; дом — метка."""
    from app import search
    c, conn = env
    a = add(conn, "2-к квартира 50 м², 3/9 эт., ЖК Губернский. 6 млн. 89180000401", 45.06, 39.02)
    b = add(conn, "1-к квартира 40 м², 5/9 эт., ЖК Губернский. 4 млн. 89180000402", 45.06, 39.02)
    v = add(conn, "Студия 25 м², 2/9 эт., ул. Тестовая. 3 млн. 89180000403", 45.07, 39.03)
    h = add(conn, "2-к квартира 55 м², 4/9 эт., ул. Тестовая 5. 6,5 млн. 89180000404", 45.08, 39.04)
    conn.execute("UPDATE listings SET geo_src = 'map-cx', geo_status = 'ok' WHERE id IN (?, ?)", (a, b))
    conn.execute("UPDATE listings SET geo_src = 'osm-street', geo_status = 'approx' WHERE id = ?", (v,))
    conn.commit()
    res = search.map_points(conn, search.Query(), 0)
    assert [p["id"] for p in res["points"]] == [h] and res["approx_hidden"] == 1
    assert len(res["complexes"]) == 1 and res["complexes"][0]["n"] == 2 and res["complexes"][0]["price_min"] == 4_000_000
    assert c.get(f"/api/listings/{v}").json()["geo_level"] == "street"
    assert c.get(f"/api/listings/{a}").json()["geo_approx"] is True


def test_quality_dashboard_snapshot_weekly_and_cli(env, capsys):
    from app import geoquality
    from tools import geo_audit
    c, conn = env
    add(conn, "2-к квартира 50 м², 3/9 эт., ул. Тестовая 1. 6 млн. 89180000501", 45.0, 38.9)
    q = c.get("/api/admin/geo-quality").json()
    assert q["now"]["total"] == 1 and "no_district" in q["labels"]
    assert geoquality.snapshot(conn) and not geoquality.snapshot(conn)   # раз в сутки
    text = geoquality.weekly(conn)
    assert text and "за неделю" in text and geoquality.weekly(conn) is None   # раз в неделю
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE title LIKE 'Отчёт за неделю%'").fetchone()[0] == 1
    assert geo_audit.main(["x", str(conn.execute("PRAGMA database_list").fetchone()[2])]) == 0
    assert "Без района" in capsys.readouterr().out


def test_post_with_two_flats_is_marked():
    """ТЗ P4.0.2: «этажи 2 и 6» — в посте две квартиры: пометка в заголовке, этаж одной не выдаём за общий."""
    from app import db, ingest
    conn = db.connect(":memory:")
    mid = ingest.add_message(conn, source="wa", text="Трешка 80 м², этажи 2 и 6, дом 9 эт. 9 млн. ул. Тестовая 3. 89180000601",
                             chat_id="c", msg_id="m1", ts=int(time.time()))
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    r = conn.execute("SELECT title, multi_units FROM listings WHERE id = ?", (lid,)).fetchone()
    assert r["multi_units"] == 2 and "2 кв. в посте" in r["title"] and "эт." not in r["title"]

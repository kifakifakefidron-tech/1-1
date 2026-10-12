"""Район по точке на карте районов (контуры загружает админ)."""
from app import districtmap, geocode

XML = """<?xml version="1.0" encoding="utf-8"?>
<ym:ymaps xmlns:ym="http://maps.yandex.ru/ymaps/1.x"><ym:GeoObjectCollection><ym:featureMembers>
<ym:GeoObject><name>ФМР (Фестивальный микрорайон)</name><Polygon><exterior><LinearRing>
<posList>38.99 45.04 39.02 45.04 39.02 45.06 38.99 45.06 38.99 45.04</posList></LinearRing></exterior></Polygon></ym:GeoObject>
<ym:GeoObject><name>Катюша</name><Polygon><exterior><LinearRing>
<posList>38.90 45.00 38.92 45.00 38.92 45.02 38.90 45.02 38.90 45.00</posList></LinearRing></exterior></Polygon></ym:GeoObject>
</ym:featureMembers></ym:GeoObjectCollection></ym:ymaps>"""


def test_district_by_point_and_auto_fill(conn, add):
    res = districtmap.import_map(conn, XML.encode())
    assert res["districts"] == 2
    assert districtmap.district_at(conn, 45.05, 39.00) == ("ФМР", "district")
    assert districtmap.district_at(conn, 45.01, 38.91)[0] == "Катюша"
    assert districtmap.district_at(conn, 44.0, 38.0) is None
    lid = add("2-к квартира 50 м², 3/9 эт., ул. Новаторов 7. 6 млн. 89180000061")["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET lat = 45.05, lon = 39.0, geo_status = 'ok' WHERE id = ?", (lid,))
    conn.commit()
    rc = districtmap.reconcile(conn)
    assert rc["empty_total"] == 1 and rc["empty"][0]["map"] == "ФМР"
    assert districtmap.fill_empty(conn) == 1
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (lid,)).fetchone()[0] == "ФМР"


def test_osm_import_and_admin_polygon(conn):
    osm = {"elements": [
        {"type": "way", "tags": {"name": "Жилой комплекс «Мозаика»", "place": "neighbourhood"},
         "geometry": [{"lat": 45.050, "lon": 39.000}, {"lat": 45.050, "lon": 39.004}, {"lat": 45.053, "lon": 39.004},
                      {"lat": 45.053, "lon": 39.000}, {"lat": 45.050, "lon": 39.000}]},
        {"type": "way", "tags": {"name": "микрорайон Горхутор", "place": "neighbourhood"},
         "geometry": [{"lat": 45.10, "lon": 38.90}, {"lat": 45.10, "lon": 38.95}, {"lat": 45.13, "lon": 38.95},
                      {"lat": 45.13, "lon": 38.90}, {"lat": 45.10, "lon": 38.90}]},
        {"type": "way", "tags": {"name": "СНТ «Луч»", "place": "neighbourhood"}, "geometry": []},
    ]}
    st = districtmap.import_osm(conn, osm)
    assert st["district"] == 1 and st["complex"] == 1 and st["skipped"] == 1
    assert districtmap.district_at(conn, 45.11, 38.92)[0] == "Горхутор"
    assert districtmap.complex_at(conn, 45.051, 39.002) == "Мозаика"
    # админ обвёл ФМР сам — его контур не трогает повторная загрузка из OSM
    districtmap.save_polygon(conn, "ФМР", "district", [[(45.04, 38.99), (45.04, 39.02), (45.06, 39.02), (45.06, 38.99)]])
    districtmap.import_osm(conn, osm)
    assert districtmap.district_at(conn, 45.045, 39.01)[0] == "ФМР"


def test_renamed_district_keeps_its_contour(conn):
    from app import learning
    districtmap.save_polygon(conn, "ФМР", "district", [[(45.04, 38.99), (45.04, 39.02), (45.06, 39.02), (45.06, 38.99)]])
    learning.rename_district(conn, "ФМР", "Фестивальный округ")
    assert districtmap.district_at(conn, 45.05, 39.0)[0] == "Фестивальный округ"


def test_map_move_is_learned(conn, add):
    from app import districtmap, ingest, learning
    lid = add("2-к квартира 58 м², 7/16 эт., ул. Ставропольская 10. 7,5 млн руб. 89181112233")["results"][0]["listing_id"]
    districtmap.set_districts(conn, [{"id": lid, "district": "ЦМР"}])
    assert conn.execute("SELECT admin_fixed FROM listings WHERE id = ?", (lid,)).fetchone()[0] == 1
    assert any(r["kind"] == "addr" for r in learning.list_rules(conn))
    # новое объявление о том же доме другого агента — сразу в ЦМР
    o = add("Двушка 60 м², 3/16 эт., ул. Ставропольская 10. 7,9 млн. 89184445566")["results"][0]["listing_id"]
    assert conn.execute("SELECT district FROM listings WHERE id = ?", (o,)).fetchone()[0] == "ЦМР"


def test_remember_street_rule(conn, add):
    from app import districtmap, learning
    lid = add("Дом 120 м², участок 4 сот., ул. Тихорецкая 5. 9 млн. 89181112233")["results"][0]["listing_id"]
    districtmap.set_districts(conn, [{"id": lid, "district": "Гидрострой"}],
                              remember_street={"name": "Тихорецкая", "district": "Гидрострой"})
    assert any(r["kind"] == "street" and "Тихорецкая" in (r["label"] or "") for r in learning.list_rules(conn))

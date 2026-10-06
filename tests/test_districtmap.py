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

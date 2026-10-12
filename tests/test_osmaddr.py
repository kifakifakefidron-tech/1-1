"""Свой справочник адресов из OpenStreetMap: точка по дому, соседний дом, середина улицы, опечатки в улицах."""
import pytest

from app import autolearn, osmaddr

TSV = "\n".join([
    "45.0520\t38.9790\tКрасная улица\t165/3\t",
    "45.0530\t38.9791\tКрасная улица\t167\tКраснодар",
    "45.0540\t38.9792\tКрасная улица\t171\t",
    "45.0600\t38.9900\tКомандорская улица\t5 корпус 3\t",
    "45.0610\t38.9910\tКомандорская улица\t9\t",
    "45.1000\t38.8000\tКрасная улица\t1\tЕлизаветинская",
    "45.0700\t38.9500\tулица 9-я Тихая\t12\t",
    "45.0800\t38.9600\tЖемчужная улица\t3\t",
] + [f"45.01\t38.91\tТестовая улица\t{i}\t" for i in range(1000)])   # справочник меньше 1000 домов не принимается


@pytest.fixture(autouse=True)
def _fresh():
    osmaddr._index.clear()
    yield
    osmaddr._index.clear()


def test_keys():
    assert osmaddr.house_key("62/1 корпус 3") == "62/1к3" == osmaddr.house_key("62/1к3")
    assert osmaddr.house_key("12 А") == "12а"
    assert osmaddr.street_key("ул. 9-я Тихая") == osmaddr.street_key("9 Тихая") == "9 тихая"
    assert osmaddr.street_key("им. Героя Пешкова") == "героя пешкова"


def test_point_house_near_street(conn):
    assert osmaddr.load(conn, TSV) > 1000
    assert osmaddr.point(conn, "Красная", "167", None)[2:] == ("ok", "osm-house")
    assert osmaddr.point(conn, "Командорская", "5к3", None)[:2] == (45.06, 38.99)
    assert osmaddr.point(conn, "Красная", "169", None)[3] == "osm-near"          # 169 нет — соседний 167/171
    assert osmaddr.point(conn, "Красная", "400", None)[2:] == ("approx", "osm-street")
    assert osmaddr.point(conn, "Красная", "1", "ст. Елизаветинская")[:2] == (45.1, 38.8)   # другая Красная — в станице
    assert osmaddr.point(conn, "Несуществующая", "1", None) is None
    assert osmaddr.point(conn, "9 Тихая", "12", None)[3] == "osm-house"


def test_street_typos(conn):
    osmaddr.load(conn, TSV)
    assert osmaddr.fix_street(conn, "Командоская") == "Командорская"
    assert osmaddr.fix_street(conn, "Комнадорская") == "Командорская"          # перестановка букв
    assert osmaddr.fix_street(conn, "Командорская") is None                    # и так правильно
    assert osmaddr.fix_street(conn, "Жемчужина") is None                       # 2 ошибки в коротком слове — скорее ЖК
    assert osmaddr.fix_street(conn, "8-я Тихая") is None                       # номер улицы не меняем


def test_ingest_fixes_street_and_autolearn_places_point(conn, add):
    osmaddr.load(conn, TSV)
    lid = add("2-к квартира 58 м², 7/16 эт., ул. Командоская 9. 7,5 млн руб. 89181112233")["results"][0]["listing_id"]
    r = conn.execute("SELECT street FROM listings WHERE id = ?", (lid,)).fetchone()
    assert r["street"] == "Командорская"
    autolearn.fill_points(conn)
    r = conn.execute("SELECT lat, lon, geo_status, geo_src FROM listings WHERE id = ?", (lid,)).fetchone()
    assert (r["lat"], r["lon"], r["geo_status"], r["geo_src"]) == (45.061, 38.991, "ok", "osm-house")

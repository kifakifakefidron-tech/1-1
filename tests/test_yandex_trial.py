"""Проба Яндекс Геокодера: разбор ответа и отчёт без записи в базу."""
import pytest

from app import config, db, ingest, yandex_trial

YA = {"response": {"GeoObjectCollection": {"featureMember": [{"GeoObject": {
    "Point": {"pos": "38.9800 45.0400"},
    "metaDataProperty": {"GeocoderMetaData": {"precision": "exact", "kind": "house",
        "text": "Россия, Краснодар, улица Красная, 100",
        "Address": {"Components": [{"kind": "locality", "name": "Краснодар"},
                                   {"kind": "district", "name": "Центральный округ"},
                                   {"kind": "district", "name": "микрорайон Центральный"}]}}}}}]}}}


def test_parse():
    y = yandex_trial.parse(YA)
    assert (y["lat"], y["lon"], y["precision"]) == (45.04, 38.98, "exact")
    assert y["district"] == "микрорайон Центральный"
    assert yandex_trial.parse({"response": {"GeoObjectCollection": {"featureMember": []}}}) is None


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    c = db.get()
    yield c
    c.close()
    db._local.conn = None


def test_run_writes_nothing(conn, monkeypatch):
    mid = ingest.add_message(conn, source="manual", text="Продаётся 1-к квартира 38 м², 5/9 эт., ул. Красная 100. 5 млн. 89181112233")
    ingest.process_message(conn, mid, use_llm=False)
    conn.execute("UPDATE listings SET lat = 45.0401, lon = 38.9801, geo_status = 'ok'")
    conn.commit()
    before = [tuple(r) for r in conn.execute("SELECT * FROM listings")]
    monkeypatch.setattr(yandex_trial, "yandex", lambda q, k: yandex_trial.parse(YA))
    monkeypatch.setattr(yandex_trial, "nominatim", lambda q: (45.05, 38.99))
    lines = []
    assert yandex_trial.run(conn, 10, "key", out=lines.append)["checked"] == 1
    text = "\n".join(lines)
    assert "Яндекс совпал с нашей точкой (до 150 м): 1 из 1" in text
    assert [tuple(r) for r in conn.execute("SELECT * FROM listings")] == before

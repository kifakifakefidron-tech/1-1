"""10.11: аренда ×1000, «50/50» как этаж, объекты других городов, выключение уведомлений, точка агента."""
import json

from app import db, notices, region, rules, settings


def test_rent_with_deposit_is_rent():
    assert rules.detect_deal("Студия 25 м2 ЖК Самолет 20000₽ + залог") == "rent"
    assert rules.detect_deal("1к 35 тыс + залог. Комиссия: арендатор 50/50") == "rent"
    assert rules.detect_deal("Продам 2к, обременение: залог Сбер 3 млн") == "sale"


def test_explicit_rub_is_not_thousands():
    assert rules.explicit_rub("20000₽ + залог", 20000)
    assert rules.explicit_rub("35 тыс + залог", 35000)
    assert not rules.explicit_rub("Цена 3200", 3200)


def test_commission_split_is_not_floor():
    assert rules.extract_floor("Комиссия 50/50") == (None, None)
    assert rules.extract_floor("Арендатор 70/30") == (None, None)
    assert rules.extract_floor("1к 40 м2, 7/16, комиссия 50/50") == (7, 16)
    assert rules.extract_floor("комиссия 60/40, 2к 5/9") == (5, 9)


def test_foreign_text():
    assert region.foreign_text("Продам 1к в Адлере, 8 млн")
    assert region.foreign_text("АК Моне, вид на море, 42 м2")
    assert region.foreign_text("2к, Таганский район")
    for ok in ("ул. Севастопольская 5, 2к", "Анапская 12, студия", "1к ул. Московская 5", "Переезжаю в Москву, срочно 1к ФМР"):
        assert region.foreign_text(ok) is None, ok


def test_notify_off_blocks(conn):
    conn.execute("INSERT INTO users (id, email, settings, created) VALUES (901, 'a@b.c', ?, 1)", (json.dumps({"notify_off": ["price"]}),))
    conn.commit()
    assert notices.is_off(conn, 901, "price")
    assert not notices.is_off(conn, 901, "search")
    assert notices.clean_off(["price", "bogus", "all"]) == ["all", "price"]
    assert settings.DEFAULTS["notify_off"] == []


def test_ingest_rent_deposit_and_foreign(conn, add):
    r = add("Студия 25 м2, ул. Красных Партизан 200, этаж 7/16, 20000₽ + залог. Тел 89181234567")
    lid = r["results"][0]["listing_id"]
    row = conn.execute("SELECT deal, price, floor FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["deal"] == "rent" and row["price"] == 20000 and row["floor"] == 7
    r2 = add("Продам 1к в Адлере, ул. Ленина 5, 42 м2, вид на море, 30 млн. Тел 89181234568")
    for x in r2["results"]:
        if x.get("listing_id"):
            st = conn.execute("SELECT is_active, hidden_reason FROM listings WHERE id = ?", (x["listing_id"],)).fetchone()
            assert st["is_active"] == 0 and st["hidden_reason"] == "foreign"


def test_datafix_fixes_old_rows(conn, add):
    from app import datafix
    lid = add("Студия 25 м2, ул. Красных Партизан 200, комиссия 50/50, 35 тыс + залог. Тел 89181234569")["results"][0]["listing_id"]
    conn.execute("UPDATE listings SET deal = 'sale', price = 35000000, floor = 50, floors = 50, is_active = 1 WHERE id = ?", (lid,))
    conn.commit()
    rep = datafix.run(conn)
    row = conn.execute("SELECT deal, price, floor, floors FROM listings WHERE id = ?", (lid,)).fetchone()
    assert rep["rent"] == 1 and rep["floor"] == 1
    assert row["deal"] == "rent" and row["price"] == 35000 and row["floor"] is None


def test_thumbs(tmp_path, monkeypatch):
    import pytest
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    from app import config, thumbs
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path))
    (tmp_path / "7").mkdir()
    Image.new("RGB", (1600, 1200), "teal").save(tmp_path / "7" / "0123456789abcdef.jpg")
    thumbs._next["t"] = 0
    assert thumbs.url("/photos/7/0123456789abcdef.jpg") == "/photos/7/0123456789abcdef.jpg"   # копии ещё нет
    assert thumbs.run() == 1
    thumbs._miss.clear()
    assert thumbs.url("/photos/7/0123456789abcdef.jpg") == "/photos/7/0123456789abcdef-s.jpg"
    with Image.open(tmp_path / "7" / "0123456789abcdef-s.jpg") as im:
        assert max(im.size) == thumbs.SIZE

"""Этап P0 (ТЗ v2): номера вырезаются в любом написании, клиенту — чистый текст, новая цена приходит с новым текстом."""
import re

import pytest
from starlette.testclient import TestClient

from app import config, db, ingest, p0fix, parser, rules, server

PHONES = [
    ("📩WA:8(918)33-260-33", "+79183326033"),
    ("тел 89181234567", "+79181234567"),
    ("+7 (918) 123-45-67", "+79181234567"),
    ("8 918 123 45 67", "+79181234567"),
    ("WA: 8-918-123-45-67", "+79181234567"),
    ("9181234567", "+79181234567"),
    ("📞8.918.123.45.67", "+79181234567"),
    ("TG 89181234567", "+79181234567"),
    ("т.89181234567", "+79181234567"),
    ("+79181234567", "+79181234567"),
    ("7 918 123-45-67", "+79181234567"),
    ("8–918–123–45–67", "+79181234567"),
    ("89 181 234 567", "+79181234567"),
    ("8 (861) 212-34-56", "+78612123456"),
    ("Телефон: +7 918 1234567", "+79181234567"),
    ("☎️ 8(918)1234567", "+79181234567"),
    ("Whatsapp 8 918 12 34 567", "+79181234567"),
]


@pytest.mark.parametrize("text,phone", PHONES)
def test_phone_formats_found_and_removed(text, phone):
    assert rules.extract_phones(text) == [phone]
    assert parser.strip_phones(text) == ""          # и номер, и подпись к нему


@pytest.mark.parametrize("text", ["Цена 5.900.000, 9/16 эт", "2-к 58 м2, 7/16, 7 500 000 руб", "Кадастр 23:43:0000000:12345",
                                  "8 950 000 руб", "Площадь 45 м2 Этаж 9 из 17 Цена 7 950 000", "год 2023, 13.10.2026",
                                  "Студия 25,5 м2, 9 этаж из 16, 3 750 000"])
def test_not_phones(text):
    assert rules.extract_phones(text) == [] and parser.strip_phones(text) == text


def test_phone_after_price_is_not_swallowed():
    t = "Цена 6 300 000 8 918 123 45 67 Анна"
    assert rules.extract_phones(t) == ["+79181234567"] and parser.strip_phones(t) == "Цена 6 300 000 Анна"


CLIENT = [
    ("Студия 23м2. Этаж 4/4 мансарда\nБез обрем. Вся сумма в дкп\nЦена 2250\nЗаклад обсуждаем\nЗаклад обсуждаем",
     "Студия 23м2. Этаж 4/4 мансарда"),
    ("♟СТУДИЯ♟\n➳ ЖК АКВАРЕЛИ 3\n➵ Состояние: ЧЕРНОВАЯ\n➵ В ДКП: 1100\n➵ КЛЮЧИ У ОБЪЕКТА🔑\nજ⁀➴ Цена - 2 600\n➳ Артём\n📞",
     "Студия\nЖК Акварели 3\nСостояние: черновая"),
    ("🏢 Продается Евро 2к кв\nв ЖК «Облака»\n💼 Ваша комиссия сверху\n💵 Цена 5 900 000 ₽\n‼️Писать и звонить только по этому номеру 👇\n📲 Контакты для просмотра:",
     "Продается Евро 2к кв\nВ ЖК «Облака»"),
    ("ЖК Дарград 💚\n✔️Обрем. Долгов нет/ Разбивка 1900\n✔️ремонт/укомплектованная кухня\n✔️Ключи в сейфе\n💸2.700 млн + Ваши",
     "ЖК Дарград\nРемонт/укомплектованная кухня"),
    ("ЖК САМОЛЕТ 5\nул. Западный обход 65/1\nСтудия 24м2. 15/16\nОбрем: сбер 3800\nЦена 3 800 000",
     "ЖК Самолет 5\nУл. Западный обход 65/1\nСтудия 24м2. 15/16"),
    ("Без обременения\nЦена 9800 🍋\nДом 100 м2", "Дом 100 м2"),
    ("ЖК Южане, 2к 54 м2\nКомиссию оплачиваю 2%\n📩WA:8(918)33-260-33 Ирина", "ЖК Южане, 2к 54 м2"),
    ("1-к квартира в ЖК Самолёт 3\nДля коллег: +50 000\nЗадаток 50 т.р.", "1-к квартира в ЖК Самолёт 3"),
    ("Продам дом в ст. Елизаветинской\nИЖС, газ, свет\nСобственник", "Продам дом в ст. Елизаветинской\nИЖС, газ, свет"),
    ("ЕВРО-2 В ФМР\nПЧО, 9/16", "ЕВРО-2 в ФМР\nПЧО, 9/16"),
]


@pytest.mark.parametrize("raw,clean", CLIENT)
def test_client_text(raw, clean):
    out = parser.client_text(raw)
    assert out == clean
    assert not re.search(r"дкп|обрем|комисс|ключ|заклад|задат", out, re.I) and not rules.find_phones(out)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    server._cache.clear()
    conn = db.get()
    with TestClient(server.app) as c:
        yield c, conn
    conn.close()
    db._local.conn = None


def _add(conn, text, ts, msg):
    mid = ingest.add_message(conn, source="wa", text=text, chat_id="c", msg_id=msg, ts=ts)
    return ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]


def test_guest_api_has_no_phones_and_new_price_brings_new_text(client):
    c, conn = client
    lid = _add(conn, "2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ФМР. Цена 6 300 000. 📩WA:8(918)33-260-33", 1_000_000, "a")
    item = c.get("/api/listings").json()["items"][0]
    assert not rules.find_phones(item["description"]) and "3326033" not in str(item)
    d = c.get(f"/api/listings/{lid}").json()
    assert not rules.find_phones(d["description"])
    # тот же объект позже с новой ценой — текст берётся новый, цена и текст не расходятся
    lid2 = _add(conn, "2-к квартира 58 м², 7/16 эт., ЖК Мозаика, ФМР. Цена 5 900 000. 📩WA:8(918)33-260-33", 1_000_500, "b")
    assert lid2 == lid
    row = conn.execute("SELECT price, description, description_ts FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["price"] == 5_900_000 and "5 900 000" in row["description"] and row["description_ts"] == 1_000_500
    assert p0fix.price_gap(row["price"], row["description"], "sale") is None


def test_p0fix_phones_report_and_apply(client):
    c, conn = client
    lid = _add(conn, "1-к квартира 40 м², 3/9 эт., ЦМР. 5 млн", 1_000_000, "x")
    conn.execute("UPDATE listings SET description = description || ' звоните 9181234567' WHERE id = ?", (lid,))
    conn.commit()
    rep = p0fix.phones_report(conn)
    assert rep["with_phone_in_text"] == 1 and not rep["applied"]
    assert "9181234567" in conn.execute("SELECT description FROM listings WHERE id = ?", (lid,)).fetchone()[0]   # отчёт ничего не меняет
    p0fix.phones_report(conn, apply=True)
    row = conn.execute("SELECT description, phones, phone_roles FROM listings WHERE id = ?", (lid,)).fetchone()
    assert "9181234567" not in row["description"] and "+79181234567" in row["phones"] and '"text"' in row["phone_roles"]

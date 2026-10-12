"""Ставки банков: разбор вставленного текста обзора и напоминание, когда устарели."""
from app import bankrates, config, notices, widgets

REVIEW = """Обзор ставок по ипотеке в банках
Самые низкие ставки на покупку квартиры: Банк Кубань Кредит 15,9%, Т-Банк — 16,9%.
Банк                 Новостройка   Вторичка   Семейная
Сбербанк             19,1%         19,4%      6%
ВТБ                  19,4 %        19,6 %     6 %
Банк ДОМ.РФ          18,3%         18,3%      6%
Рефинансирование: минимальная у Т-Банка 16,9%"""


def test_parse_table_and_phrases(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    res = bankrates.parse(REVIEW, "secondary")
    by = {r["bank"]: r for r in res["items"]}
    assert res["how"] == "rules"
    assert by["Сбербанк"] == {"bank": "Сбербанк", "new": 19.1, "secondary": 19.4, "family": 6.0}
    assert by["ВТБ"]["secondary"] == 19.6 and by["Банк ДОМ.РФ"]["new"] == 18.3
    assert by["Кубань Кредит"]["secondary"] == 15.9 and by["Т-Банк"]["secondary"] == 16.9


def test_merge_keeps_other_programs():
    old = [{"bank": "Сбербанк", "secondary": 20.0, "ihc": 21.0}]
    out = bankrates.merge(old, [{"bank": "сбербанк", "secondary": 19.4}])
    assert out == [{"bank": "Сбербанк", "secondary": 19.4, "ihc": 21.0}]


def test_remind_when_stale(conn, monkeypatch):
    conn.execute("INSERT INTO users (email, is_admin, created) VALUES ('a@x.ru', 1, 1)")
    widgets.set_banks(conn, [{"bank": "Сбербанк", "secondary": 19.4}])
    now = widgets.banks(conn)["ts"]
    assert bankrates.remind(conn, now + 86400) is False                       # свежие — молчим
    assert bankrates.remind(conn, now + 8 * 86400) is True                    # неделя прошла — напоминание
    assert bankrates.remind(conn, now + 9 * 86400) is False                   # и не каждый день
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE title LIKE 'Пора обновить ставки%'").fetchone()[0] == 1


DOMRF_COLUMN = """Ставки предложения по рыночным продуктам (на квартиру)
Изменение к предыдущей неделе, п.п.
Показать детальные данные
Банк
Первичный рынок
Вторичный рынок
Рефинансирование
Сбербанк
18,7
18,7
21,5
Промсвязьбанк
18,49
+0,44
18,49
+0,44
17,99
+0,2
Газпромбанк
Для з/п клиентов
Для з/п клиентов
Для з/п клиентов
Банк Кубань Кредит
15,9
15,9
15,9
Россельхозбанк
не предоставляет
не предоставляет
не предоставляет
Средневзвешенная ставка
18,74
+0,01
18,69
18,38"""

DOMRF_ROWS = """Банк\tПервичный рынок\tВторичный рынок\tРефинансирование
Сбербанк\t18,7\t18,7\t21,5
Т-Банк\t16,9\t16,9\t16,9
Газпромбанк\tДля з/п клиентов\tДля з/п клиентов\tДля з/п клиентов
Транскапиталбанк\t16,85\t16,85\t16,75
Средневзвешенная ставка\t18,74\t18,69\t18,38"""


def test_domrf_table_column_and_rows(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    for text in (DOMRF_COLUMN, DOMRF_ROWS):
        by = {r["bank"]: r for r in bankrates.parse(text)["items"]}
        assert by["Сбербанк"] == {"bank": "Сбербанк", "new": 18.7, "secondary": 18.7}   # рефинансирование не берём
        assert "Газпромбанк" not in by and "Россельхозбанк" not in by                    # «для з/п», «не предоставляет»
        assert not any("средневзвеш" in b.lower() for b in by)
    by = {r["bank"]: r for r in bankrates.parse(DOMRF_COLUMN)["items"]}
    assert by["ПСБ"]["secondary"] == 18.49 and by["Кубань Кредит"]["new"] == 15.9       # «+0,44» — не ставка
    assert {r["bank"]: r for r in bankrates.parse(DOMRF_ROWS)["items"]}["Транскапиталбанк"]["secondary"] == 16.85


def test_domrf_average_row_saved(conn, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    res = bankrates.parse(DOMRF_COLUMN)
    assert res["avg"] == {"new": 18.74, "secondary": 18.69}
    saved = widgets.set_banks(conn, res["items"], res["avg"])
    assert saved["avg"]["secondary"] == 18.69
    again = widgets.set_banks(conn, res["items"])          # сохранили без новой таблицы — средняя остаётся прежней
    assert again["avg"]["new"] == 18.74 and widgets.banks(conn)["avg"]["secondary"] == 18.69

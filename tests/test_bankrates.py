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

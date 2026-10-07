"""Аналитика рынка: медианы по районам/ЖК и динамика по ежедневным снимкам."""
import time

from app import market


def test_report_and_change(conn, add):
    for i, price in enumerate((5.0, 5.5, 6.0, 6.5, 7.0)):
        add(f"2-к квартира 50 м², {i + 2}/16 эт., ЖК Мозаика, ФМР. {price} млн руб. 8918111{i:04d}")
    now = int(time.time())
    r = market.report(conn, "sale", now)
    cx = {x["name"]: x for x in r["complexes"]}
    assert cx["Мозаика"]["median"] == 120_000 and cx["Мозаика"]["n"] == 5
    assert r["city"]["median"] == 120_000 and r["since7"] is None   # динамики ещё нет
    # снимок «неделю назад» с ценой ниже → рост
    old = time.strftime("%Y-%m-%d", time.gmtime(now - 8 * 86400))
    conn.execute("INSERT INTO market_daily VALUES (?, 'sale', 'city', '', 'Краснодар', 100000, 5000000, 5)", (old,))
    conn.commit()
    assert market.report(conn, "sale", now)["city"]["ch7"] == 20.0

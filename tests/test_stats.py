"""Статистика админки: добавлено/снято по дням и причинам."""
import time

from app import stats


def test_added_and_removed_counted(conn, add):
    r = add("Продаётся 2-к квартира 58 м², 7/16 эт., ЖК Мозаика. 7,5 млн руб. 89181112233", ts=int(time.time()) - 60)
    lid = r["results"][0]["listing_id"]
    s = stats.collect(conn)
    assert s["listings"]["added"]["today"] == 1 and s["listings"]["removed"]["today"] == 0
    conn.execute("UPDATE listings SET is_active = 0, sold_at = ? WHERE id = ?", (int(time.time()), lid))
    conn.commit()
    row = conn.execute("SELECT removed_at, removed_reason FROM listings WHERE id = ?", (lid,)).fetchone()
    assert row["removed_at"] and row["removed_reason"] == "sold"
    s = stats.collect(conn)
    assert s["listings"]["removed"]["today"] == 1 and s["days"][-1]["removed"] == 1
    assert s["listings"]["removed_reasons"][0]["reason"] == stats.REASONS["sold"]
    # вернули на сайт — отметка снятия убирается
    conn.execute("UPDATE listings SET is_active = 1 WHERE id = ?", (lid,))
    assert conn.execute("SELECT removed_at FROM listings WHERE id = ?", (lid,)).fetchone()[0] is None

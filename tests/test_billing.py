"""18.10: напоминания об оплате сервисов — админам в уведомления, «Оплатил» сдвигает срок."""
from app import billing, db

NOW = 1_792_000_000


def test_billing_reminders():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO users (email, created, is_admin) VALUES ('a@x.ru', 1, 1)")
    conn.commit()
    assert billing.remind(conn, NOW) == len(billing.SERVICES)          # ни разу не отмечены — просим отметить
    for key, *_ in billing.SERVICES:
        billing.mark_paid(conn, key, now=NOW)
    db.set_state(conn, "billing_remind_ts", "0")
    assert billing.remind(conn, NOW + 5 * 86400) == 0                  # до срока далеко
    db.set_state(conn, "billing_remind_ts", "0")
    n = billing.remind(conn, NOW + 19 * 86400)                         # за 1 день до 20-дневного срока
    assert n == len(billing.SERVICES) - 1                               # домен — раз в год, ему рано
    st = {s["key"]: s for s in billing.status(conn, NOW + 19 * 86400)}
    assert st["timeweb"]["left"] == 1 and st["regru"]["period"] == 365
    titles = [r[0] for r in conn.execute("SELECT title FROM notices WHERE kind = 'billing'")]
    assert any("Timeweb" in t for t in titles)

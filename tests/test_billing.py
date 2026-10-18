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


def test_skip_queue_old_or_all():
    import time
    from app import ingest
    conn = db.connect(":memory:")
    now = int(time.time())
    for i, age_h in enumerate((1, 3, 10, 30)):
        ingest.add_message(conn, source="wa", text=f"2к квартира {i}", chat_id="c", msg_id=f"q{i}", ts=now - age_h * 3600)
    conn.commit()
    assert ingest.skip_queue(conn, 6, now) == 2
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'new'").fetchone()[0] == 2
    assert ingest.skip_queue(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'new'").fetchone()[0] == 0


def test_yookassa_error_is_understandable(monkeypatch):
    import io
    import urllib.error
    import pytest
    from app import accounts, payments
    conn = db.connect(":memory:")
    uid = accounts.create_promo_user(conn)["id"]

    def boom(url, body=None):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"code":"invalid_credentials","description":"Wrong key"}'))
    monkeypatch.setattr(payments, "_request", boom)
    with pytest.raises(payments.PayError):
        payments.create(conn, uid, 7)
    assert conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 0

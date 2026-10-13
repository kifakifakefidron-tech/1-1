"""ЮKassa: доступ продлевается ровно один раз и только на сумму тарифа."""
from app import accounts, config, payments


def _setup(conn, monkeypatch, amount="49.00"):
    monkeypatch.setattr(config, "SUB_PLANS", [(7, 49)])
    calls = []

    def fake_request(url, data=None):
        calls.append(url)
        if data is not None:
            return {"id": "p1", "status": "pending", "confirmation": {"confirmation_url": "https://pay"}}
        return {"id": "p1", "status": "succeeded", "paid": True, "amount": {"value": amount, "currency": "RUB"}}

    monkeypatch.setattr(payments, "_request", fake_request)
    u = accounts.upsert_email_user(conn, "payer@example.ru")
    payments.create(conn, u["id"], 7)
    return u


def test_confirm_once(conn, monkeypatch):
    u = _setup(conn, monkeypatch)
    before = accounts.get_user(conn, u["id"])["paid_until"] or 0
    assert payments.confirm(conn, "p1") is True
    after = accounts.get_user(conn, u["id"])["paid_until"]
    assert after > before
    assert payments.confirm(conn, "p1") is False                 # повторное уведомление — без второго продления
    assert accounts.get_user(conn, u["id"])["paid_until"] == after


def test_confirm_rejects_smaller_amount(conn, monkeypatch):
    u = _setup(conn, monkeypatch, amount="1.00")
    assert payments.confirm(conn, "p1") is False
    assert conn.execute("SELECT status FROM payments").fetchone()[0] != "succeeded"


def test_first_week_49_then_99(conn, monkeypatch):
    monkeypatch.setattr(config, "SUB_PLANS", [(7, 99), (30, 399)])
    monkeypatch.setattr(config, "SUB_FIRST", (7, 49))
    u = accounts.upsert_email_user(conn, "new@example.ru")
    p = payments.plans(conn, u["id"])
    assert [(x["days"], x["price"], x["first"]) for x in p] == [(7, 49, True), (30, 399, False)]
    conn.execute("INSERT INTO payments (user_id, amount, days, provider_id, status, created) VALUES (?, 49, 7, 'x', 'succeeded', 1)", (u["id"],))
    assert [(x["days"], x["price"]) for x in payments.plans(conn, u["id"])] == [(7, 99), (30, 399)]

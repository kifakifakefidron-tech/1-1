"""Кабинет: один запрос со всем для дня риелтора; погода/ставки — из кэша, сбой источника не ломает кабинет."""
import json
import time

import pytest
from starlette.testclient import TestClient

from app import accounts, config, db, server, widgets


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)

    def offline(*a, **kw):   # внешние сайты в тестах недоступны
        raise OSError("нет сети")
    monkeypatch.setattr(widgets, "_http", offline)
    conn = db.get()
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    with TestClient(server.app) as c:
        c.cookies.set(server.SESSION, accounts.create_session(conn, u["id"]))
        yield c, conn
    conn.close()
    db._local.conn = None


def test_dashboard_works_offline_and_uses_cache(env):
    c, conn = env
    d = c.get("/api/dashboard").json()
    assert {"agenda", "crm", "notices", "market", "widgets"} <= set(d)
    assert d["widgets"]["weather"] is None and d["widgets"]["banks"]["items"] == []
    # прошлые данные в кэше — отдаются, даже если источник сейчас недоступен
    db.set_state(conn, "w:keyrate", json.dumps({"ts": int(time.time()) - 99999, "data": {"rate": 14.0, "since": "2026-07-27",
                                                                                    "prev": 14.25, "history": []}}))
    assert c.get("/api/widgets").json()["keyrate"]["data"]["rate"] == 14.0


def test_bank_rates_admin_only(env):
    c, conn = env
    assert c.post("/api/admin/banks", json={"items": [{"bank": "Сбер", "rate": "11,5"}]}).status_code == 403
    conn.execute("UPDATE users SET is_admin = 1")
    conn.commit()
    r = c.post("/api/admin/banks", json={"items": [{"bank": "Сбер", "program": "Вторичка", "rate": "11,5"}, {"bank": "", "rate": "1"}]}).json()
    assert r["items"] == [{"bank": "Сбер", "program": "Вторичка", "rate": 11.5}]
    assert c.get("/api/dashboard").json()["widgets"]["banks"]["items"][0]["bank"] == "Сбер"


def test_pay_page_plans(env, monkeypatch):
    c, _ = env
    monkeypatch.setattr(config, "SUB_PLANS", [(7, 49), (30, 179)])
    d = c.get("/api/pay").json()
    assert d["enabled"] is False and [p["days"] for p in d["plans"]] == [7, 30] and d["plans"][1]["save"] > 0
    assert c.post("/api/pay", json={"days": 30}).status_code == 503   # ключей ЮKassa нет — оплата выключена
    assert c.get("/pay").status_code == 200

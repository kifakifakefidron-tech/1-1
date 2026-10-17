"""17.10: клиент перешёл на «Сделку» — в планере напоминания (отзыв через месяц, поздравить через год), без повторов."""
from app import accounts, clients, config, db


def test_deal_stage_creates_followups(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    u = accounts.upsert_email_user(conn, "agent@example.ru")
    cid = clients.save(conn, u["id"], {"name": "Ирина", "kind": "buyer"})["client"]["id"]
    now = 1_760_000_000
    clients.set_stage(conn, u["id"], cid, "deal")
    ev = conn.execute("SELECT title, ts, client_id FROM planner_events WHERE user_id = ? ORDER BY ts", (u["id"],)).fetchall()
    assert [e["title"] for e in ev] == ["Попросить отзыв и рекомендацию — Ирина", "Год со сделки — поздравить — Ирина"]
    assert all(e["client_id"] == cid for e in ev)
    # вернули назад и снова «Сделка» — повторно не создаются
    clients.set_stage(conn, u["id"], cid, "talks")
    clients.set_stage(conn, u["id"], cid, "deal")
    assert conn.execute("SELECT COUNT(*) FROM planner_events WHERE user_id = ?", (u["id"],)).fetchone()[0] == 2
    assert clients.after_deal(conn, u["id"], cid, "Ирина", now) == 0
    conn.close()
    db._local.conn = None

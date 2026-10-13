"""Жизнь объекта: 20 дней на сайте без повтора → 20 дней в архиве → удалить; повтор в чатах возвращает."""
import time

from app import config, ingest


def _listing(conn, last_seen, source="chat", hidden=None):
    cur = conn.execute("""INSERT INTO listings (type, deal, price, title, phones, first_seen, last_seen, seen_count, is_active,
                          source, hidden_reason, search_text) VALUES ('flat','sale',5000000,'t','[]',?,?,1,1,?,?, '')""",
                       (last_seen, last_seen, source, hidden))
    conn.commit()
    return cur.lastrowid


def test_archive_then_delete(conn):
    now = int(time.time())
    old = _listing(conn, now - (config.STALE_DAYS + 1) * 86400)
    fresh = _listing(conn, now - 86400)
    feed = _listing(conn, now - 90 * 86400, source="feed")
    assert ingest.archive_stale(conn, now) == 1                         # 20 дней без повтора — в архив
    active = dict(conn.execute("SELECT id, is_active FROM listings").fetchall())
    assert active[old] == 0 and active[fresh] == 1 and active[feed] == 1   # фид сам не устаревает
    assert ingest.cleanup_old(conn, now + 5 * 86400)["listings"] == 0     # в архиве меньше 20 дней — лежит
    assert ingest.cleanup_old(conn, now + (config.ARCHIVE_DAYS + 1) * 86400)["listings"] == 1
    assert conn.execute("SELECT COUNT(*) FROM listings WHERE id = ?", (old,)).fetchone()[0] == 0


def test_banned_kept_for_recognition(conn):
    now = int(time.time())
    lid = _listing(conn, now - 200 * 86400, hidden="admin")
    conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (lid,))
    conn.commit()
    assert ingest.cleanup_old(conn, now + 365 * 86400)["listings"] == 0

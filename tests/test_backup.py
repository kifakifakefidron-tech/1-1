import calendar
import gzip
import sqlite3

from app import backup, config, db


def test_backup_nightly_and_rotation(tmp_path, monkeypatch):
    path = tmp_path / "strely.db"
    monkeypatch.setattr(config, "DB_PATH", str(path))
    conn = db.connect(str(path))
    conn.execute("INSERT INTO state(key, value) VALUES ('probe', 'ok')")
    conn.commit()
    night = calendar.timegm((2025, 10, 12, 1, 0, 0))   # 12.10.2025 04:00 МСК (01:00 UTC)
    assert backup.maybe_backup(conn, now=night - 3 * 3600) is None   # 01:00 МСК — ещё рано
    name = backup.maybe_backup(conn, now=night)
    assert name and name.endswith(".db.gz")
    assert backup.maybe_backup(conn, now=night + 60) is None
    raw = gzip.decompress((backup.folder() / name).read_bytes())
    copy = tmp_path / "copy.db"
    copy.write_bytes(raw)
    assert sqlite3.connect(copy).execute("SELECT value FROM state WHERE key='probe'").fetchone()[0] == "ok"
    for d in range(1, 10):   # хранятся только последние 7
        backup.make(conn, f"2025-11-{d:02d}")
    assert len(list(backup.folder().glob("*.db.gz"))) == backup.KEEP

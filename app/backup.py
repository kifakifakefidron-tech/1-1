"""Резервная копия базы раз в сутки (ночью): data/backups/1plus1-ГГГГ-ММ-ДД.db.gz, хранятся последние KEEP.

VACUUM INTO делает целостный снимок, не останавливая сайт (в режиме WAL запись идёт дальше),
потом снимок сжимается. Копия лежит на том же сервере — от поломки файла и ошибочной правки спасёт;
от потери всего сервера — нет: раз в неделю стоит скачивать свежую копию к себе (README «Резервные копии»).
"""
from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import time
from pathlib import Path

from . import config, db

log = logging.getLogger(__name__)
KEEP = 7
NIGHT_HOUR = 3   # по Москве: ночью сайтом почти не пользуются


def folder() -> Path:
    return Path(config.DB_PATH).parent / "backups"


def maybe_backup(conn: sqlite3.Connection, now: float | None = None) -> str | None:
    """Раз в сутки после 3:00 по Москве. Возвращает имя файла копии или None (ещё рано / уже сделана)."""
    now = now or time.time()
    msk = time.gmtime(now + 3 * 3600)
    day = time.strftime("%Y-%m-%d", msk)
    if msk.tm_hour < NIGHT_HOUR or db.get_state(conn, "backup_day") == day or config.DB_PATH == ":memory:":
        return None
    db.set_state(conn, "backup_day", day)   # даже если не выйдет — не пытаться каждый цикл (сообщим админу)
    db.flush(conn)
    return make(conn, day)


def make(conn: sqlite3.Connection, day: str) -> str:
    out = folder()
    out.mkdir(parents=True, exist_ok=True)
    src = Path(config.DB_PATH)
    free = shutil.disk_usage(out).free
    if src.exists() and free < src.stat().st_size * 2:
        raise RuntimeError(f"мало места на диске для копии базы: свободно {free // 2**20} МБ")
    raw = out / f"1plus1-{day}.db"
    raw.unlink(missing_ok=True)
    conn.execute("VACUUM INTO ?", (str(raw),))
    gz = raw.with_suffix(".db.gz")
    with raw.open("rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)
    raw.unlink()
    for old in sorted(out.glob("1plus1-*.db.gz"))[:-KEEP]:
        old.unlink()
    log.info("резервная копия базы: %s (%d МБ)", gz.name, gz.stat().st_size // 2**20)
    return gz.name


def restore_hint() -> str:
    return ("Восстановить: docker compose stop web worker bot && gunzip -c data/backups/<файл>.db.gz > data/strely.db "
            "&& rm -f data/strely.db-wal data/strely.db-shm && docker compose up -d")

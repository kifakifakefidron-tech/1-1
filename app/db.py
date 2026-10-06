"""SQLite-база: один файл, полнотекстовый поиск FTS5. Для тысяч–десятков тысяч
объектов этого с запасом хватает, а на сервере не нужен отдельный Postgres."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,          -- wa | tg | max | manual
    profile_id  TEXT,
    chat_id     TEXT,
    chat_name   TEXT,
    msg_id      TEXT,
    sender      TEXT,
    sender_name TEXT,
    text        TEXT NOT NULL,
    text_hash   TEXT NOT NULL,
    ts          INTEGER NOT NULL,       -- unix time
    status      TEXT NOT NULL DEFAULT 'new',  -- new | done | error | skipped
    kind        TEXT,                   -- listing | request | other
    objects     INTEGER DEFAULT 0,
    error       TEXT,
    UNIQUE (source, profile_id, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS ix_messages_hash ON messages(text_hash);

CREATE TABLE IF NOT EXISTS listings (
    id          INTEGER PRIMARY KEY,
    type        TEXT NOT NULL,
    deal        TEXT NOT NULL,
    rooms       INTEGER,
    area        REAL,
    land        REAL,
    floor       INTEGER,
    floors      INTEGER,
    price       INTEGER,
    price_m2    INTEGER,
    district    TEXT,
    complex     TEXT,
    settlement  TEXT,
    street      TEXT,
    house       TEXT,
    title       TEXT NOT NULL,
    description TEXT,
    fragment    TEXT,
    phones      TEXT NOT NULL DEFAULT '[]',
    first_seen  INTEGER NOT NULL,
    last_seen   INTEGER NOT NULL,
    seen_count  INTEGER NOT NULL DEFAULT 1,
    is_active   INTEGER NOT NULL DEFAULT 1,
    lat         REAL,
    lon         REAL,
    geo_status  TEXT DEFAULT 'pending', -- pending | ok | none | skip
    search_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_listings_main ON listings(is_active, type, deal, price);
CREATE INDEX IF NOT EXISTS ix_listings_last ON listings(last_seen);
CREATE INDEX IF NOT EXISTS ix_listings_district ON listings(district);
CREATE INDEX IF NOT EXISTS ix_listings_complex ON listings(complex);

CREATE TABLE IF NOT EXISTS listing_events (
    id          INTEGER PRIMARY KEY,
    listing_id  INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    message_id  INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    ts          INTEGER NOT NULL,
    price       INTEGER,
    phones      TEXT NOT NULL DEFAULT '[]',
    fragment_hash TEXT,
    match       TEXT                    -- new | repost | same-object
);
CREATE INDEX IF NOT EXISTS ix_events_listing ON listing_events(listing_id);
CREATE INDEX IF NOT EXISTS ix_events_hash ON listing_events(fragment_hash);

CREATE VIRTUAL TABLE IF NOT EXISTS listings_fts USING fts5(
    search_text, tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS geocache (
    query TEXT PRIMARY KEY, lat REAL, lon REAL, ts INTEGER
);

CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
"""

# Новые поля в уже существующих таблицах (база на сервере обновится сама при запуске)
MIGRATIONS = [
    ("listings", "source", "TEXT NOT NULL DEFAULT 'chat'"),   # chat | feed
    ("listings", "ext_id", "TEXT"),                           # id объекта в фиде
    ("listings", "photos", "TEXT NOT NULL DEFAULT '[]'"),
    ("listings", "url", "TEXT"),
]

_local = threading.local()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_listings_ext ON listings(ext_id) WHERE ext_id IS NOT NULL")
    conn.commit()


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or config.DB_PATH
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def get() -> sqlite3.Connection:
    """Соединение на поток (веб-сервер и воркер работают в разных потоках/процессах)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = connect()
        _local.conn = conn
    return conn


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()

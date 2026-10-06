"""SQLite-база: один файл, полнотекстовый поиск FTS5. Для тысяч–десятков тысяч
объектов этого с запасом хватает, а на сервере не нужен отдельный Postgres."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
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

-- ─── личный кабинет ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    tg_id       INTEGER UNIQUE,
    tg_username TEXT,
    email       TEXT UNIQUE,
    phone       TEXT,                   -- подтверждён через Telegram («Поделиться номером»)
    name        TEXT,
    created     INTEGER NOT NULL,
    last_seen   INTEGER,
    trial_until INTEGER NOT NULL DEFAULT 0,
    paid_until  INTEGER NOT NULL DEFAULT 0,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    blocked     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created INTEGER NOT NULL, last_seen INTEGER, ua TEXT
);
-- Вход/привязка/«убрать мой номер» через бота: сайт создаёт токен, бот его подтверждает
CREATE TABLE IF NOT EXISTS tg_tokens (
    token TEXT PRIMARY KEY, purpose TEXT NOT NULL, user_id INTEGER, chat_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending', result_user INTEGER, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS email_codes (
    email TEXT PRIMARY KEY, code_hash TEXT NOT NULL, created INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS trials (phone TEXT PRIMARY KEY, user_id INTEGER, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    created INTEGER NOT NULL, PRIMARY KEY (user_id, listing_id)
);
CREATE TABLE IF NOT EXISTS phone_views (
    id INTEGER PRIMARY KEY, user_id INTEGER, listing_id INTEGER NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_phone_views ON phone_views(user_id, ts);
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY, days INTEGER NOT NULL, max_uses INTEGER NOT NULL DEFAULT 1,
    used INTEGER NOT NULL DEFAULT 0, note TEXT, created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS promo_uses (code TEXT, user_id INTEGER, ts INTEGER, PRIMARY KEY (code, user_id));
-- Агент попросил убрать свой номер — не показываем нигде
CREATE TABLE IF NOT EXISTS optout_phones (phone TEXT PRIMARY KEY, ts INTEGER NOT NULL, source TEXT);
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY, listing_id INTEGER, user_id INTEGER, reason TEXT, text TEXT,
    ts INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, days INTEGER NOT NULL,
    provider_id TEXT UNIQUE, status TEXT NOT NULL DEFAULT 'pending', created INTEGER NOT NULL, paid INTEGER
);
-- Названия чатов (WhatsApp не присылает их в сообщениях); foreign_place — чат другого города
CREATE TABLE IF NOT EXISTS chats (
    source TEXT NOT NULL, chat_id TEXT NOT NULL, name TEXT, foreign_place INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, chat_id)
);
-- ─── кабинет агента ───────────────────────────────────────────────────────
-- Номер подтверждён: агент прислал код со своего номера на наш (MAX/WhatsApp через Wappi)
CREATE TABLE IF NOT EXISTS agent_phones (
    phone TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, verified_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS phone_codes (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, phone TEXT NOT NULL, code TEXT NOT NULL,
    created INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS ix_phone_codes ON phone_codes(phone, status);
-- Журнал правок агентов (админ может откатить)
CREATE TABLE IF NOT EXISTS listing_edits (
    id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL, user_id INTEGER NOT NULL, ts INTEGER NOT NULL,
    field TEXT NOT NULL, old TEXT, new TEXT, reverted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_listing_edits ON listing_edits(listing_id);
CREATE INDEX IF NOT EXISTS ix_listing_edits_user ON listing_edits(user_id, ts);
"""

# Новые поля в уже существующих таблицах (база на сервере обновится сама при запуске)
MIGRATIONS = [
    ("listings", "source", "TEXT NOT NULL DEFAULT 'chat'"),   # chat | feed
    ("listings", "ext_id", "TEXT"),                           # id объекта в фиде
    ("listings", "photos", "TEXT NOT NULL DEFAULT '[]'"),
    ("listings", "url", "TEXT"),
    ("listings", "article", "TEXT"),                          # артикул СТРЕЛ («Артикул: 337»)
    ("listings", "room_kind", "TEXT"),                        # studio | classic | euro | mini
    ("listings", "rooms_mask", "INTEGER NOT NULL DEFAULT 0"), # для фильтра «Комнаты» (rules.rooms_mask)
    # кабинет агента: source='own' — объект добавлен агентом вручную
    ("listings", "owner_user_id", "INTEGER"),                 # кто управляет карточкой (первый правящий)
    ("listings", "owner_edited_at", "INTEGER"),
    ("listings", "expires_at", "INTEGER"),                    # свой объект: снять после этого времени
    ("listings", "confirm_sent", "INTEGER"),                  # когда отправили письмо «ещё актуален?»
    ("listings", "sold_at", "INTEGER"),                       # агент отметил «продано/снято»
]

_local = threading.local()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError as e:  # другой процесс (сайт/worker/бот) успел добавить
                if "duplicate column" not in str(e):
                    raise
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_listings_ext ON listings(ext_id) WHERE ext_id IS NOT NULL")
    conn.commit()


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or config.DB_PATH
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from . import geo  # noqa: PLC0415 — ключ ЖК для фильтра (разные написания одного ЖК)
    conn.create_function("cxkey", 1, geo.complex_key, deterministic=True)
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

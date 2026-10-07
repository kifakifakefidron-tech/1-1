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
CREATE INDEX IF NOT EXISTS ix_messages_chat ON messages(source, chat_id);

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
    geo_status  TEXT DEFAULT 'pending', -- pending | ok | approx | none | skip | manual | learned | feed (точка из фида)
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
CREATE INDEX IF NOT EXISTS ix_events_message ON listing_events(message_id);

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
-- ─── фишки: подписки на поиск, заметки ───────────────────────────────────
CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL, params TEXT NOT NULL, created INTEGER NOT NULL, checked_at INTEGER NOT NULL,
    sent_at INTEGER, active INTEGER NOT NULL DEFAULT 1
);
-- Выученные точки: админ поправил объект на карте → эта точка для того же дома/ЖК во всех объявлениях
CREATE TABLE IF NOT EXISTS geo_learned (
    key TEXT PRIMARY KEY,            -- addr:<посёлок>|<улица>|<дом>  или  cx:<ЖК>
    lat REAL NOT NULL, lon REAL NOT NULL, n INTEGER NOT NULL DEFAULT 1, ts INTEGER NOT NULL, label TEXT
);
-- Контуры районов для сверки (карта районов neagent.info — с активной ссылкой на источник; не публикуем)
CREATE TABLE IF NOT EXISTS district_polygons (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_name TEXT, kind TEXT NOT NULL DEFAULT 'district',
    source TEXT NOT NULL, polys TEXT NOT NULL, area REAL, lat_min REAL, lat_max REAL, lon_min REAL, lon_max REAL, ts INTEGER
);
CREATE TABLE IF NOT EXISTS reconcile_ignore (key TEXT PRIMARY KEY, ts INTEGER);
-- Районы, добавленные админом (в дополнение к справочнику geo.DISTRICTS)
CREATE TABLE IF NOT EXISTS custom_districts (name TEXT PRIMARY KEY, aliases TEXT NOT NULL DEFAULT '[]', ts INTEGER NOT NULL);
-- Переименованные районы справочника: старое имя → новое (старое продолжает узнаваться в тексте)
CREATE TABLE IF NOT EXISTS district_renames (old TEXT PRIMARY KEY, new TEXT NOT NULL, ts INTEGER NOT NULL);
-- Выученные правила ЖК/района (app/learning.py)
CREATE TABLE IF NOT EXISTS learned_rules (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, label TEXT,
    n INTEGER NOT NULL DEFAULT 1, ts INTEGER NOT NULL, UNIQUE (kind, key)
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, ts INTEGER NOT NULL,
    kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT, url TEXT, listing_id INTEGER, actions TEXT,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_notices_user ON notices(user_id, read);
-- История цены объекта (для графика): пишут триггеры при появлении объекта и при каждой смене цены
CREATE TABLE IF NOT EXISTS price_history (
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE, ts INTEGER NOT NULL, price INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_price_history ON price_history(listing_id, ts);
-- Ежедневные медианы рынка (аналитика /market): для динамики за неделю и месяц
CREATE TABLE IF NOT EXISTS market_daily (
    day TEXT NOT NULL, deal TEXT NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, name TEXT,
    median INTEGER, price INTEGER, n INTEGER, PRIMARY KEY (day, deal, kind, key)
);
-- Планер агента: показы, созвоны, встречи, задачи (app/planner.py)
CREATE TABLE IF NOT EXISTS planner_events (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, title TEXT NOT NULL, ts INTEGER NOT NULL, dur_min INTEGER NOT NULL DEFAULT 60,
    listing_id INTEGER, contact_name TEXT, contact_phone TEXT, place TEXT, note TEXT,
    remind_min INTEGER NOT NULL DEFAULT 60, reminded INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL, updated INTEGER
);
CREATE INDEX IF NOT EXISTS ix_planner_user ON planner_events(user_id, ts);
CREATE INDEX IF NOT EXISTS ix_planner_remind ON planner_events(reminded, done, ts);
CREATE TABLE IF NOT EXISTS planner_notes (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, text TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0, color TEXT, created INTEGER NOT NULL, updated INTEGER NOT NULL
);
-- Подборки для клиента (app/picks.py): агент собирает объекты, клиент открывает /c/<token>
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, token TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, note TEXT, contact_name TEXT, contact_phone TEXT, created INTEGER NOT NULL, updated INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0, viewed_at INTEGER
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    pos INTEGER NOT NULL DEFAULT 0, note TEXT, added INTEGER NOT NULL, PRIMARY KEY (collection_id, listing_id)
);
-- Запросы покупателей из чатов («куплю/ищу/есть клиент»), app/buyers.py
CREATE TABLE IF NOT EXISTS buyer_requests (
    id INTEGER PRIMARY KEY, message_id INTEGER, text_hash TEXT, text TEXT NOT NULL, chat_name TEXT,
    deal TEXT NOT NULL, type TEXT, rooms_mask INTEGER NOT NULL DEFAULT 0, price_min INTEGER, price_max INTEGER,
    districts TEXT NOT NULL DEFAULT '[]', complexes TEXT NOT NULL DEFAULT '[]', phones TEXT NOT NULL DEFAULT '[]',
    first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, seen_count INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_buyer_active ON buyer_requests(is_active, deal, last_seen);
CREATE INDEX IF NOT EXISTS ix_buyer_hash ON buyer_requests(text_hash);
CREATE TABLE IF NOT EXISTS notes (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    text TEXT NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (user_id, listing_id)
);
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
    ("listings", "prev_price", "INTEGER"),                    # прошлая цена (для «цена снизилась»)
    ("listings", "price_changed_at", "INTEGER"),
    ("favorites", "price_at", "INTEGER"),                     # цена, когда добавили в избранное
    ("favorites", "notified_price", "INTEGER"),               # о какой цене уже написали
    ("chats", "blocked", "INTEGER NOT NULL DEFAULT 0"),       # админ отключил чат («не брать»)
    ("chats", "link", "TEXT"),                                # ссылка на чат, если мессенджер её отдаёт
    ("chats", "blocked_at", "INTEGER"),                       # когда отключили (через 10 мин — чистка сообщений)
    ("chats", "purged_at", "INTEGER"),                        # когда удалили сообщения этого чата
    ("saved_searches", "page_query", "TEXT"),                 # адрес страницы с этими фильтрами (для ссылки)
    ("favorites", "gone_notified", "INTEGER"),                # уже сообщили, что объект снят с сайта
    ("users", "sub_notified", "TEXT"),                        # о каком окончании доступа уже сообщили
    # Сайт и почта — отдельные отметки «уже сообщили»: письмо не должно «съедать» уведомление на сайте
    ("saved_searches", "notice_checked_at", "INTEGER"),       # до какого момента новые объекты уже показаны на сайте
    ("favorites", "site_price", "INTEGER"),                   # о какой цене уже сообщили на сайте
    ("listings", "admin_fixed", "INTEGER NOT NULL DEFAULT 0"),  # ЖК/район поправил админ — автоматика не трогает
    ("listings", "extra_districts", "TEXT NOT NULL DEFAULT '[]'"),  # ещё районы (объект на границе районов)
    ("listings", "hidden_reason", "TEXT"),                    # 'chat' — скрыт, т.к. приходил только из отключённых чатов
    ("listings", "removed_at", "INTEGER"),                    # когда сняли с сайта (для статистики; ставит триггер)
    ("listings", "removed_reason", "TEXT"),                   # stale | sold | feed | chat | expired | other
    ("listings", "market_diff", "INTEGER"),
    ("notes", "status", "TEXT"),
    ("collection_items", "text", "TEXT"),
    # «Приведи коллегу»: свой код, кто пригласил, начислили ли бонус пригласившему
    ("users", "ref_code", "TEXT"),
    ("users", "referred_by", "INTEGER"),
    ("users", "ref_rewarded", "INTEGER NOT NULL DEFAULT 0"),                     # свой текст объявления для клиента (вместо исходного)                              # статус агента по объекту: call | show | think | refuse | deal                   # % к медиане м² по ЖК/району (hooks.update_market_diff)
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
    # Любая смена цены (чаты, фид, правка агента) запоминает прошлую цену
    conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_price_change AFTER UPDATE OF price ON listings
                    WHEN OLD.price IS NOT NULL AND NEW.price IS NOT NULL AND NEW.price != OLD.price
                    BEGIN UPDATE listings SET prev_price = OLD.price, price_changed_at = CAST(strftime('%s','now') AS INTEGER)
                          WHERE id = NEW.id; END""")
    # История цены: первая цена объекта и каждая её смена (чаты, фид, правка агента)
    conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_price_hist_ins AFTER INSERT ON listings WHEN NEW.price IS NOT NULL
                    BEGIN INSERT INTO price_history (listing_id, ts, price) VALUES (NEW.id, NEW.first_seen, NEW.price); END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_price_hist_upd AFTER UPDATE OF price ON listings
                    WHEN NEW.price IS NOT NULL AND NEW.price IS NOT OLD.price
                    BEGIN INSERT INTO price_history (listing_id, ts, price)
                          VALUES (NEW.id, CAST(strftime('%s','now') AS INTEGER), NEW.price); END""")
    if not conn.execute("SELECT 1 FROM state WHERE key = 'price_hist_v1'").fetchone():
        _backfill_prices(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('price_hist_v1', '1')")
    # Сняли с сайта (любым путём) — запомнить когда и почему; вернули — забыть
    conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_removed AFTER UPDATE OF is_active ON listings
                    WHEN OLD.is_active != NEW.is_active
                    BEGIN UPDATE listings SET
                        removed_at = CASE WHEN NEW.is_active = 0 THEN CAST(strftime('%s','now') AS INTEGER) END,
                        removed_reason = CASE WHEN NEW.is_active = 1 THEN NULL
                            WHEN NEW.sold_at IS NOT NULL THEN 'sold'
                            WHEN NEW.hidden_reason = 'chat' THEN 'chat'
                            WHEN NEW.source = 'feed' THEN 'feed'
                            WHEN NEW.source = 'own' THEN 'expired'
                            WHEN NEW.last_seen < CAST(strftime('%s','now') AS INTEGER) - 15 * 86400 THEN 'stale'
                            ELSE 'other' END
                    WHERE id = NEW.id; END""")
    # Один раз: снятым раньше — примерная дата снятия (продано — дата продажи, устарело — последний повтор + срок)
    if not conn.execute("SELECT 1 FROM state WHERE key = 'removed_backfill'").fetchone():
        conn.execute("""UPDATE listings SET
                            removed_at = MIN(COALESCE(sold_at, CASE WHEN source = 'chat' THEN last_seen + ? END),
                                             CAST(strftime('%s','now') AS INTEGER)),
                            removed_reason = CASE WHEN sold_at IS NOT NULL THEN 'sold' WHEN hidden_reason = 'chat' THEN 'chat'
                                                  WHEN source = 'chat' THEN 'stale' ELSE 'other' END
                        WHERE is_active = 0 AND removed_at IS NULL""", (config.STALE_DAYS * 86400,))
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('removed_backfill', '1')")
    conn.commit()


def _backfill_prices(conn: sqlite3.Connection) -> None:
    """Один раз: история цен из уже присланных сообщений (цена в каждом повторе), иначе — текущая цена."""
    have = {r[0] for r in conn.execute("SELECT DISTINCT listing_id FROM price_history")}
    ev: dict[int, list] = {}
    for r in conn.execute("SELECT listing_id, ts, price FROM listing_events WHERE price IS NOT NULL ORDER BY listing_id, ts"):
        ev.setdefault(r[0], []).append((r[1], r[2]))
    rows = []
    for r in conn.execute("SELECT id, first_seen, price, prev_price, price_changed_at FROM listings WHERE price IS NOT NULL"):
        if r["id"] in have:
            continue
        pts = ev.get(r["id"]) or []
        if not pts and r["prev_price"] and r["price_changed_at"]:
            pts = [(r["first_seen"], r["prev_price"]), (r["price_changed_at"], r["price"])]
        if not pts or pts[-1][1] != r["price"]:
            pts.append((max(r["first_seen"], pts[-1][0]) if pts else r["first_seen"], r["price"]))
        last = None
        for ts, price in pts:
            if rows and rows[-1][0] == r["id"] and ts - rows[-1][1] < 600:   # одно сообщение разобрано повторно
                rows[-1] = (r["id"], rows[-1][1], price)
                last = price
            elif price != last:
                rows.append((r["id"], ts, price))
                last = price
    conn.executemany("INSERT INTO price_history (listing_id, ts, price) VALUES (?,?,?)", rows)


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


def flush(conn: sqlite3.Connection) -> None:
    """Закончить начатую запись. ОБЯЗАТЕЛЬНО перед любым ожиданием сети (Wappi, почта, геокодер):
    пока запись открыта, база занята для всех — сайт и админка получают «база занята»."""
    if conn is not None and conn.in_transaction:
        conn.commit()


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()

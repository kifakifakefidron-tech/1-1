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
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-32000;
PRAGMA mmap_size=268435456;

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
-- CRM агента (app/clients.py): клиенты, объекты клиента, история
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL, phone TEXT, kind TEXT NOT NULL DEFAULT 'buyer', stage TEXT NOT NULL DEFAULT 'new',
    deal TEXT NOT NULL DEFAULT 'sale', budget_min INTEGER, budget_max INTEGER, rooms TEXT NOT NULL DEFAULT '[]',
    districts TEXT NOT NULL DEFAULT '[]', note TEXT, next_ts INTEGER, next_text TEXT, source TEXT,
    archived INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL, updated INTEGER NOT NULL, stage_ts INTEGER
);
CREATE INDEX IF NOT EXISTS ix_clients_user ON clients (user_id, archived, stage);
CREATE TABLE IF NOT EXISTS client_items (
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'offered', note TEXT, ts INTEGER NOT NULL, PRIMARY KEY (client_id, listing_id)
);
CREATE TABLE IF NOT EXISTS client_log (
    id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_client_log ON client_log (client_id, ts);
-- Фото из чатов (app/media.py): картинки альбомов — к объявлению того же агента
CREATE TABLE IF NOT EXISTS chat_media (
    id INTEGER PRIMARY KEY, source TEXT NOT NULL, profile_id TEXT, chat_id TEXT NOT NULL, sender TEXT, msg_id TEXT NOT NULL,
    ts INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'new', listing_id INTEGER, url TEXT, UNIQUE (source, msg_id)
);
CREATE INDEX IF NOT EXISTS ix_chat_media_status ON chat_media (status, ts);
-- Справочник адресов из OpenStreetMap (app/osmaddr.py): дом → точка
CREATE TABLE IF NOT EXISTS osm_addr (
    street_key TEXT NOT NULL, street TEXT NOT NULL, house_key TEXT NOT NULL, city TEXT NOT NULL DEFAULT '',
    lat REAL NOT NULL, lon REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osm_addr ON osm_addr (street_key, house_key);
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
-- 17.10: согласия на cookie (web/cookies.js): когда, что выбрали (all | need), кто (если вошёл)
CREATE TABLE IF NOT EXISTS cookie_consents (ts INTEGER NOT NULL, choice TEXT NOT NULL, user_id INTEGER);
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
-- Удалено админом навсегда: тексты (хеш сообщения/куска), объекты фида — больше не добавляем
CREATE TABLE IF NOT EXISTS banned (
    kind TEXT NOT NULL, key TEXT NOT NULL, listing_id INTEGER, title TEXT, ts INTEGER NOT NULL, PRIMARY KEY (kind, key)
);
CREATE INDEX IF NOT EXISTS ix_banned_listing ON banned(listing_id);
CREATE TABLE IF NOT EXISTS presence (   -- кто сейчас на сайте (сигнал раз в минуту с открытой страницы)
    key TEXT PRIMARY KEY, user_id INTEGER, ts INTEGER NOT NULL, path TEXT, mobile INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_presence_ts ON presence(ts);
CREATE TABLE IF NOT EXISTS hidden_listings (   -- агент скрыл объект только для себя (продан, не сотрудничает…)
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    reason TEXT, ts INTEGER NOT NULL, PRIMARY KEY (user_id, listing_id)
);
-- P1 (ТЗ v2): «Не подходит» в подборе клиента — больше не предлагать этот объект этому клиенту
CREATE TABLE IF NOT EXISTS client_hidden (
    client_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, ts INTEGER NOT NULL, PRIMARY KEY (client_id, listing_id)
);
-- Реакции клиента со страницы подборки (без регистрации): нравится / не подходит / хочу посмотреть / вопрос
CREATE TABLE IF NOT EXISTS pick_reactions (
    id INTEGER PRIMARY KEY, collection_id INTEGER NOT NULL, listing_id INTEGER NOT NULL, kind TEXT NOT NULL,
    text TEXT, phone TEXT, device TEXT, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_pick_reactions ON pick_reactions (collection_id, listing_id);
-- Честные просмотры подборки: одно устройство — один раз в день (агент, вошедшие и боты-превью не считаются)
CREATE TABLE IF NOT EXISTS collection_views (
    collection_id INTEGER NOT NULL, day TEXT NOT NULL, device TEXT NOT NULL, ts INTEGER NOT NULL,
    PRIMARY KEY (collection_id, day, device)
);
-- Журнал работы агента по объекту (app/worklog.py): заметка/звонок/показ, к клиенту и/или подборке
CREATE TABLE IF NOT EXISTS work_notes (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, listing_id INTEGER NOT NULL,
    client_id INTEGER, collection_id INTEGER, kind TEXT NOT NULL DEFAULT 'note', text TEXT NOT NULL, ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_work_notes_listing ON work_notes (user_id, listing_id);
CREATE INDEX IF NOT EXISTS ix_work_notes_client ON work_notes (client_id);
CREATE INDEX IF NOT EXISTS ix_work_notes_pick ON work_notes (collection_id);
-- Выборочная проверка решений сервиса (ТЗ «админка-гео» P4.0.1): ответ админа по объекту из выборки — верно / поправил
-- Качество гео-данных по дням (app/geoquality.py): динамика на /learn
CREATE TABLE IF NOT EXISTS geo_quality_daily (day TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auto_reviews (
    listing_id INTEGER NOT NULL, src TEXT NOT NULL, ok INTEGER NOT NULL, ts INTEGER NOT NULL,
    PRIMARY KEY (listing_id, src)
);
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
    ("listings", "confirm_sent", "INTEGER"),
    ("listings", "confirm_stage", "INTEGER"),   # за сколько дней до конца уже напомнили (10 → 5 → 2 → 1)                  # когда отправили письмо «ещё актуален?»
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
    # 17.10 (Артём): своя цена для клиента в подборке (NULL — как в объявлении) и «скрыть цену»
    ("collection_items", "price", "INTEGER"),
    ("collection_items", "hide_price", "INTEGER NOT NULL DEFAULT 0"),
    ("collection_items", "photos", "TEXT"),
    ("collection_items", "photo_order", "TEXT"),   # 18.10: {"order": [url…], "hidden": [url…]} — фото для клиента по порядку
    ("listings", "repair", "TEXT"),
    ("listings", "added_at", "INTEGER"),
    ("listings", "admin_fields", "TEXT"),   # 18.10: характеристики, поправленные админом (JSON) — повторы их не перетирают
    ("phone_codes", "method", "TEXT"),       # 17.10: NULL/'max' — код в MAX, 'call' — звонок (SMS.ru callcheck)
    ("phone_codes", "check_id", "TEXT"),
    ("phone_codes", "call_phone", "TEXT"),
    ("phone_codes", "checked_at", "INTEGER"),   # 17.10: когда объект появился на 1+1 (срок «на сайте» не раньше этого дня)   # 17.10: состояние по тексту — 'yes' с ремонтом / 'no' без (ПЧО, черновая…) / NULL не сказано   # 17.10: свои фото агента к объекту в подборке (JSON, /photos/pk/…)
    # «Приведи коллегу»: свой код, кто пригласил, начислили ли бонус пригласившему
    ("users", "ref_code", "TEXT"),
    ("users", "referred_by", "INTEGER"),
    ("users", "ref_rewarded", "INTEGER NOT NULL DEFAULT 0"),
    ("listings", "deal_fixed", "INTEGER NOT NULL DEFAULT 0"),
    ("listings", "price_fixed", "INTEGER NOT NULL DEFAULT 0"),  # админ поправил цену — повторы с той же ошибкой не перебивают
    ("listings", "price_wrong", "INTEGER"),
    ("listings", "geo_src", "TEXT"),        # откуда точка, если её поставил сервис сам (app/autolearn.py)
    ("listings", "district_src", "TEXT"),
    ("listings", "place_conflict", "TEXT"),
    ("planner_events", "client_id", "INTEGER"),
    ("planner_events", "color", "TEXT"),
    ("users", "settings", "TEXT"),
    ("clients", "match_seen", "INTEGER"),   # до какого момента новые объекты под пожелания клиента уже показали агенту
    ("collections", "client_id", "INTEGER"),
    ("collections", "color", "TEXT"),
    ("collections", "views_unique", "INTEGER NOT NULL DEFAULT 0"),
    ("listing_edits", "src", "TEXT"),   # откуда правка: админ / агент / перепроверка / контур / чистка правил
    ("collections", "hide_address", "INTEGER NOT NULL DEFAULT 0"),   # клиенту — без номера дома, точка округлена ~300 м
    ("client_items", "pick_id", "INTEGER"),        # в какой подборке отправлен (статусы из подборки уходят вместе с ней)
    ("client_items", "sent_at", "INTEGER"),
    ("client_items", "opened_at", "INTEGER"),
    ("client_items", "reaction", "TEXT"),          # like | no | want_show | question — ответ клиента со страницы подборки
    ("client_items", "reaction_text", "TEXT"),
    ("client_items", "updated", "INTEGER"),
    ("clients", "wishes", "TEXT"),                 # JSON: типы, площадь, этаж, ЖК, состояние, обременение, оплата, срок, допуск
    ("clients", "stage_manual", "INTEGER NOT NULL DEFAULT 0"),   # этап двигали руками — сервис сам не двигает   # уникальные открытия клиентом (устройство в день)
    ("listings", "phone_roles", "TEXT"),       # {"+7…": "text" | "sender"} — номер в тексте / прислал в чат
    ("listings", "multi_units", "INTEGER"),
    ("listings", "price_doubt", "INTEGER"),
    ("users", "consent_at", "INTEGER"),       # когда дал согласие на обработку ПДн (галочка при входе, документ /consent)
    ("users", "consent_ver", "TEXT"),         # редакция документа согласия (POLICY_DATE)
    ("client_items", "question", "TEXT"),     # последний вопрос клиента из подборки (ответ «нравится» не трогает)    # цена «под вопросом» (pricefix): вне сортировок/фильтров по цене    # «этажи 2 и 6» — в посте несколько квартир (ТЗ «админка-гео» P4.0.2)
    ("listings", "description_ts", "INTEGER"), # когда последний раз обновлялся текст объявления          # цвет подборки в списке (палитра settings.COLORS)
    ("collections", "client_title", "TEXT"),   # заголовок, который видит клиент (название подборки — только агенту)   # подборка сделана для клиента CRM («Всё в подборку»)   # настройки аккаунта (app/settings.py): вид подборок, порядок блоков кабинета   # цвет события в планере (gold, sky, …; пусто — по виду события)   # событие по клиенту (CRM)  # источники спорят: «rule:…» (правило ≠ карта), «votes:…» (карта ≠ соседи)   # откуда район, если его поставил сервис сам: map | vote-house | vote-cx                     # какую ошибочную цену разбор давал (её и не принимаем)   # админ сам поставил продажу/аренду — автоматика не трогает                     # свой текст объявления для клиента (вместо исходного)                              # статус агента по объекту: call | show | think | refuse | deal                   # % к медиане м² по ЖК/району (hooks.update_market_diff)
]

_local = threading.local()


# То, что действует только на одно соединение (WAL хранится в самом файле базы)
PER_CONNECTION = """
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;
PRAGMA foreign_keys=ON;
PRAGMA temp_store=MEMORY;
PRAGMA cache_size=-32000;
PRAGMA mmap_size=268435456;
"""


def _migrate_pairs(conn: sqlite3.Connection) -> None:
    """ТЗ v2, P1 — один статус на пару клиент+объект; статусы «В работе» уходят к клиентам или в журнал объекта.
    offered → sent (если объект в подборке клиента) или matched; shown → show. notes.status: у объекта ровно один клиент —
    запись в журнал про этого клиента (показ → статус «Показ», отказ → «Не подходит»); иначе — запись без клиента."""
    pick_of = """(SELECT k.id FROM collections k JOIN collection_items ci ON ci.collection_id = k.id
                  WHERE k.client_id = client_items.client_id AND ci.listing_id = client_items.listing_id LIMIT 1)"""
    conn.execute(f"""UPDATE client_items SET status = 'sent', pick_id = {pick_of}, sent_at = ts
                     WHERE status = 'offered' AND {pick_of} IS NOT NULL""")
    conn.execute("UPDATE client_items SET status = 'matched' WHERE status = 'offered'")
    conn.execute("UPDATE client_items SET status = 'show' WHERE status = 'shown'")
    conn.execute("UPDATE client_items SET updated = ts WHERE updated IS NULL")
    one = """(CASE WHEN (SELECT COUNT(*) FROM client_items i JOIN clients c ON c.id = i.client_id
                        WHERE c.user_id = n.user_id AND i.listing_id = n.listing_id) = 1
              THEN (SELECT i.client_id FROM client_items i JOIN clients c ON c.id = i.client_id
                    WHERE c.user_id = n.user_id AND i.listing_id = n.listing_id) END)"""
    conn.execute(f"""INSERT INTO work_notes (user_id, listing_id, client_id, kind, text, ts)
                     SELECT n.user_id, n.listing_id, {one},
                            CASE n.status WHEN 'call' THEN 'call' WHEN 'show' THEN 'show' ELSE 'note' END,
                            CASE n.status WHEN 'call' THEN 'Звонил' WHEN 'show' THEN 'Показ' WHEN 'think' THEN 'Думает'
                                          WHEN 'refuse' THEN 'Отказ' ELSE n.status END || ' (статус из «В работе»)', n.ts
                     FROM notes n WHERE n.status IS NOT NULL AND n.status != ''""")
    for st, pair in (("show", "show"), ("refuse", "rejected")):
        conn.execute(f"""UPDATE client_items SET status = ?, updated = CAST(strftime('%s','now') AS INTEGER)
                         WHERE status IN ('matched', 'sent', 'opened', 'liked') AND EXISTS (
                             SELECT 1 FROM notes n JOIN clients c ON c.user_id = n.user_id
                             WHERE c.id = client_items.client_id AND n.listing_id = client_items.listing_id AND n.status = ?
                               AND (SELECT COUNT(*) FROM client_items i JOIN clients c2 ON c2.id = i.client_id
                                    WHERE c2.user_id = n.user_id AND i.listing_id = n.listing_id) = 1)""", (pair, st))
    conn.execute("UPDATE notes SET status = NULL WHERE status IS NOT NULL")


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
    if not conn.execute("SELECT 1 FROM state WHERE key = 'notes_no_deal'").fetchone():   # статус «Сделка» у объектов убран
        conn.execute("UPDATE notes SET status = NULL WHERE status = 'deal'")
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('notes_no_deal', '1')")
    if not conn.execute("SELECT 1 FROM state WHERE key = 'pairs_v1'").fetchone():
        _migrate_pairs(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('pairs_v1', '1')")
    if not conn.execute("SELECT 1 FROM state WHERE key = 'price_hist_v1'").fetchone():
        _backfill_prices(conn)
        conn.execute("INSERT OR REPLACE INTO state(key, value) VALUES ('price_hist_v1', '1')")
    # Сняли с сайта (любым путём) — запомнить когда и почему; вернули — забыть
    conn.execute("DROP TRIGGER IF EXISTS trg_removed")   # v2: причина «под задатком»
    conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_removed_v2 AFTER UPDATE OF is_active ON listings
                    WHEN OLD.is_active != NEW.is_active
                    BEGIN UPDATE listings SET
                        removed_at = CASE WHEN NEW.is_active = 0 THEN CAST(strftime('%s','now') AS INTEGER) END,
                        removed_reason = CASE WHEN NEW.is_active = 1 THEN NULL
                            WHEN NEW.sold_at IS NOT NULL THEN 'sold'
                            WHEN NEW.hidden_reason = 'chat' THEN 'chat'
                            WHEN NEW.hidden_reason = 'deposit' THEN 'deposit'
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


_ready: set[str] = set()   # базы, для которых в этом процессе уже проверены таблицы и обновления
_ready_lock = threading.Lock()


def connect(path: str | None = None) -> sqlite3.Connection:
    p = path or config.DB_PATH
    if p != ":memory:":
        Path(p).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    from . import geo  # noqa: PLC0415 — ключ ЖК для фильтра (разные написания одного ЖК)
    conn.create_function("cxkey", 1, geo.complex_key, deterministic=True)
    # Таблицы и обновления базы проверяем один раз на процесс, а не на каждое соединение
    # (у сайта десятки потоков — раньше каждый новый поток прогонял всю схему и брал базу на запись)
    with _ready_lock:
        if p == ":memory:" or p not in _ready:
            conn.executescript(SCHEMA)
            _migrate(conn)
            _ready.add(p)
        else:
            conn.executescript(PER_CONNECTION)
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

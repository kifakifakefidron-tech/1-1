"""Личный кабинет: пользователи, вход, пробный период, подписка, избранное, промокоды,
доступ к телефонам агентов (с лимитом и журналом), «убрать мой номер».

Правила (решения владельца, 07.10.2026):
  * вход через Telegram (с подтверждением номера кнопкой «Поделиться номером») или по почте;
  * пробный доступ TRIAL_DAYS дней — один раз на номер телефона, без карты;
  * дальше подписка SUB_PRICE ₽ за SUB_DAYS дней (оплата ЮKassa, пока выключена);
  * телефоны агентов — только с активным доступом, по одному объекту, не больше
    PHONE_VIEWS_PER_DAY новых объектов в сутки; каждое открытие записывается.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time

from . import config, rules

DAY = 86400
SESSION_DAYS = 180


def _hash(value: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()


# ─── пользователи и сессии ────────────────────────────────────────────────
def get_user(conn: sqlite3.Connection, user_id: int | None) -> sqlite3.Row | None:
    if not user_id:
        return None
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_session(conn: sqlite3.Connection, user_id: int, ua: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    conn.execute("INSERT INTO sessions (token_hash, user_id, created, last_seen, ua) VALUES (?,?,?,?,?)",
                 (_hash(token), user_id, now, now, ua[:200]))
    conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, user_id))
    conn.commit()
    return token


def user_by_session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = conn.execute("SELECT * FROM sessions WHERE token_hash = ?", (_hash(token),)).fetchone()
    if row is None or row["created"] < time.time() - SESSION_DAYS * DAY:
        return None
    user = get_user(conn, row["user_id"])
    if user is None or user["blocked"]:
        return None
    if not user["is_admin"] and _is_admin_identity(user["tg_username"], user["email"]):
        conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
        conn.commit()
        user = get_user(conn, user["id"])
    now = int(time.time())
    if (row["last_seen"] or 0) < now - 3600:  # не пишем в базу на каждый запрос
        try:
            conn.execute("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (now, row["token_hash"]))
            conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, user["id"]))
            conn.commit()
        except sqlite3.OperationalError:  # база занята — время визита обновим в следующий раз
            conn.rollback()
    return user


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))
        conn.commit()


def _is_admin_identity(tg_username: str | None = None, email: str | None = None) -> bool:
    if tg_username and config.TELEGRAM_ADMIN and tg_username.lower() == config.TELEGRAM_ADMIN.lower():
        return True
    return bool(email and email.lower() in config.ADMIN_EMAILS)


def upsert_tg_user(conn: sqlite3.Connection, tg_id: int, username: str | None, name: str | None,
                   phone: str | None = None) -> sqlite3.Row:
    now = int(time.time())
    row = conn.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
    admin = 1 if _is_admin_identity(tg_username=username) else 0
    if row is None:
        cur = conn.execute("INSERT INTO users (tg_id, tg_username, name, phone, created, last_seen, is_admin) "
                           "VALUES (?,?,?,?,?,?,?)", (tg_id, username, name, phone, now, now, admin))
        uid = cur.lastrowid
    else:
        uid = row["id"]
        conn.execute("UPDATE users SET tg_username = ?, name = COALESCE(?, name), phone = COALESCE(?, phone), "
                     "is_admin = MAX(is_admin, ?) WHERE id = ?", (username, name, phone, admin, uid))
    conn.commit()
    if phone:
        start_trial(conn, uid, phone)
    return get_user(conn, uid)


EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-zа-я]{2,}$", re.IGNORECASE)


def upsert_email_user(conn: sqlite3.Connection, email: str) -> sqlite3.Row:
    email = email.strip().lower()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is not None:
        return row
    now = int(time.time())
    cur = conn.execute("INSERT INTO users (email, created, last_seen, is_admin) VALUES (?,?,?,?)",
                       (email, now, now, 1 if _is_admin_identity(email=email) else 0))
    conn.commit()
    if config.TRIAL_BY_EMAIL:
        start_trial(conn, cur.lastrowid, f"email:{email}")
    return get_user(conn, cur.lastrowid)


def create_promo_user(conn: sqlite3.Connection) -> sqlite3.Row:
    """Вход по коду коллег (временно): обычный аккаунт с доступом на PROMO_LOGIN_DAYS дней."""
    now = int(time.time())
    cur = conn.execute("INSERT INTO users (name, created, last_seen, paid_until) VALUES (?,?,?,?)",
                       ("Коллега (по коду)", now, now, now + config.PROMO_LOGIN_DAYS * DAY))
    conn.commit()
    return get_user(conn, cur.lastrowid)


def link_telegram(conn: sqlite3.Connection, user_id: int, tg_id: int, username: str | None,
                  phone: str | None) -> tuple[bool, str]:
    """Привязать Telegram (и номер) к уже существующему аккаунту (вошёл по почте)."""
    other = conn.execute("SELECT id FROM users WHERE tg_id = ? AND id != ?", (tg_id, user_id)).fetchone()
    if other is not None:
        return False, "Этот Telegram уже привязан к другому аккаунту 1+1."
    admin = 1 if _is_admin_identity(tg_username=username) else 0
    conn.execute("UPDATE users SET tg_id = ?, tg_username = ?, phone = COALESCE(?, phone), "
                 "is_admin = MAX(is_admin, ?) WHERE id = ?", (tg_id, username, phone, admin, user_id))
    conn.commit()
    if phone:
        start_trial(conn, user_id, phone)
    return True, "ok"


# ─── доступ ────────────────────────────────────────────────────────────────
def start_trial(conn: sqlite3.Connection, user_id: int, phone: str) -> bool:
    """Пробный доступ — один раз на номер телефона."""
    phone = phone if phone.startswith("email:") else (rules.normalize_phone(phone) or phone)
    if conn.execute("SELECT 1 FROM trials WHERE phone = ?", (phone,)).fetchone():
        return False
    now = int(time.time())
    conn.execute("INSERT INTO trials (phone, user_id, ts) VALUES (?,?,?)", (phone, user_id, now))
    conn.execute("UPDATE users SET trial_until = MAX(trial_until, ?) WHERE id = ?",
                 (now + config.TRIAL_DAYS * DAY, user_id))
    conn.commit()
    return True


def access_until(user: sqlite3.Row | None) -> int:
    if user is None or user["blocked"]:
        return 0
    return max(user["trial_until"] or 0, user["paid_until"] or 0)


def has_access(user: sqlite3.Row | None, now: int | None = None) -> bool:
    if user is None or user["blocked"]:
        return False
    return bool(user["is_admin"]) or access_until(user) > (now or time.time())


def extend(conn: sqlite3.Connection, user_id: int, days: int) -> None:
    """Продлить оплаченный доступ на days дней (от сегодня или от конца текущего)."""
    now = int(time.time())
    conn.execute("UPDATE users SET paid_until = MAX(paid_until, ?, trial_until) + ? WHERE id = ?",
                 (now, days * DAY, user_id))
    conn.commit()


def me(conn: sqlite3.Connection, user: sqlite3.Row | None) -> dict | None:
    if user is None:
        return None
    now = int(time.time())
    fav = [r[0] for r in conn.execute("SELECT listing_id FROM favorites WHERE user_id = ?", (user["id"],))]
    used_trial = bool(conn.execute("SELECT 1 FROM trials WHERE user_id = ?", (user["id"],)).fetchone())
    return {
        "id": user["id"], "name": user["name"], "tg_username": user["tg_username"], "email": user["email"],
        "phone_confirmed": bool(user["phone"]), "is_admin": bool(user["is_admin"]),
        "access": has_access(user, now), "access_until": access_until(user),
        "trial_until": user["trial_until"], "paid_until": user["paid_until"], "trial_used": used_trial,
        "favorites": fav, "views_today": views_today(conn, user["id"]),
        "views_limit": None if user["is_admin"] or config.PHONE_VIEWS_PER_DAY <= 0 else config.PHONE_VIEWS_PER_DAY,
    }


# ─── телефоны агентов ──────────────────────────────────────────────────────
def optout_set(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT phone FROM optout_phones")}


def add_optout(conn: sqlite3.Connection, phone: str, source: str) -> str | None:
    p = rules.normalize_phone(phone)
    if not p:
        return None
    conn.execute("INSERT OR IGNORE INTO optout_phones (phone, ts, source) VALUES (?,?,?)", (p, int(time.time()), source))
    conn.commit()
    return p


def views_today(conn: sqlite3.Connection, user_id: int) -> int:
    since = int(time.time()) - DAY
    return conn.execute("SELECT COUNT(DISTINCT listing_id) FROM phone_views WHERE user_id = ? AND ts >= ?",
                        (user_id, since)).fetchone()[0]


def open_phones(conn: sqlite3.Connection, user: sqlite3.Row | None, listing_id: int,
                phones: list[str]) -> tuple[list[str] | None, str | None]:
    """Отдать телефоны объекта, если можно. (телефоны, причина отказа)."""
    phones = [p for p in phones if p not in optout_set(conn)]
    if not phones:
        return [], None
    uid = user["id"] if user is not None else None
    if user is not None and not user["is_admin"] and config.PHONE_VIEWS_PER_DAY > 0:
        since = int(time.time()) - DAY
        seen = conn.execute("SELECT 1 FROM phone_views WHERE user_id = ? AND listing_id = ? AND ts >= ?",
                            (uid, listing_id, since)).fetchone()
        if not seen and views_today(conn, uid) >= config.PHONE_VIEWS_PER_DAY:
            return None, "limit"
    try:
        conn.execute("INSERT INTO phone_views (user_id, listing_id, ts) VALUES (?,?,?)", (uid, listing_id, int(time.time())))
        conn.commit()
    except sqlite3.OperationalError:  # база занята — запись в журнал пропускаем, номер всё равно показываем
        conn.rollback()
    return phones, None


# ─── избранное ─────────────────────────────────────────────────────────────
def toggle_favorite(conn: sqlite3.Connection, user_id: int, listing_id: int) -> bool:
    """Добавить/убрать. Возвращает True, если теперь в избранном."""
    cur = conn.execute("DELETE FROM favorites WHERE user_id = ? AND listing_id = ?", (user_id, listing_id))
    if cur.rowcount:
        conn.commit()
        return False
    conn.execute("""INSERT INTO favorites (user_id, listing_id, created, price_at)
                    VALUES (?,?,?, (SELECT price FROM listings WHERE id = ?))""",
                 (user_id, listing_id, int(time.time()), listing_id))
    conn.commit()
    return True


# ─── промокоды ─────────────────────────────────────────────────────────────
def create_promo(conn: sqlite3.Connection, days: int, max_uses: int = 1, note: str = "", code: str | None = None) -> str:
    code = (code or secrets.token_hex(3)).strip().upper()
    conn.execute("INSERT INTO promo_codes (code, days, max_uses, note, created) VALUES (?,?,?,?,?)",
                 (code, days, max_uses, note, int(time.time())))
    conn.commit()
    return code


def redeem_promo(conn: sqlite3.Connection, user_id: int, code: str) -> tuple[bool, str]:
    code = (code or "").strip().upper()
    row = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        return False, "Такого промокода нет."
    if row["used"] >= row["max_uses"]:
        return False, "Промокод уже использован."
    if conn.execute("SELECT 1 FROM promo_uses WHERE code = ? AND user_id = ?", (code, user_id)).fetchone():
        return False, "Вы уже активировали этот промокод."
    conn.execute("INSERT INTO promo_uses (code, user_id, ts) VALUES (?,?,?)", (code, user_id, int(time.time())))
    conn.execute("UPDATE promo_codes SET used = used + 1 WHERE code = ?", (code,))
    extend(conn, user_id, row["days"])
    return True, f"Доступ продлён на {row['days']} дн."


# ─── вход через бота ───────────────────────────────────────────────────────
def new_tg_token(conn: sqlite3.Connection, purpose: str, user_id: int | None = None) -> str:
    token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    conn.execute("INSERT INTO tg_tokens (token, purpose, user_id, created) VALUES (?,?,?,?)",
                 (token, purpose, user_id, int(time.time())))
    conn.execute("DELETE FROM tg_tokens WHERE created < ?", (int(time.time()) - DAY,))
    conn.commit()
    return token


def tg_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM tg_tokens WHERE token = ?", (token,)).fetchone()
    if row is None or row["created"] < time.time() - 1800:  # 30 минут на вход
        return None
    return row


# ─── почта ─────────────────────────────────────────────────────────────────
def new_email_code(conn: sqlite3.Connection, email: str) -> tuple[str | None, str | None]:
    """(код, ошибка). Не чаще раза в минуту на адрес."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return None, "Проверьте адрес почты."
    row = conn.execute("SELECT created FROM email_codes WHERE email = ?", (email,)).fetchone()
    now = int(time.time())
    if row and row["created"] > now - 60:
        return None, "Код уже отправлен — подождите минуту."
    code = f"{secrets.randbelow(1_000_000):06d}"
    conn.execute("INSERT INTO email_codes (email, code_hash, created, attempts) VALUES (?,?,?,0) "
                 "ON CONFLICT(email) DO UPDATE SET code_hash=excluded.code_hash, created=excluded.created, attempts=0",
                 (email, _hash(code), now))
    conn.commit()
    return code, None


def check_email_code(conn: sqlite3.Connection, email: str, code: str) -> tuple[bool, str]:
    email = email.strip().lower()
    row = conn.execute("SELECT * FROM email_codes WHERE email = ?", (email,)).fetchone()
    if row is None or row["created"] < time.time() - 15 * 60:
        return False, "Код устарел — запросите новый."
    if row["attempts"] >= 5:
        return False, "Слишком много попыток — запросите новый код."
    conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE email = ?", (email,))
    conn.commit()
    if not hmac.compare_digest(row["code_hash"], _hash(re.sub(r"\D", "", code or ""))):
        return False, "Код не подошёл."
    conn.execute("DELETE FROM email_codes WHERE email = ?", (email,))
    conn.commit()
    return True, "ok"


def user_public_row(r: sqlite3.Row) -> dict:
    """Строка пользователя для админки."""
    return {k: r[k] for k in ("id", "tg_username", "email", "phone", "name", "created", "last_seen",
                              "trial_until", "paid_until", "is_admin", "blocked")}


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False)

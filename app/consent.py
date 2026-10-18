"""Согласие агента на размещение номера и объявлений (19.10, приёмка — раздел 7, этап 1; решения Артёма: делаем, бонус 30 дней,
неподтверждённые номера НЕ скрываем, а помечаем).

Агент подтверждает свой номер (звонок или код в MAX — app/agent.py) и отдельными галочками даёт:
  1) согласие на распространение ПДн — имя и номер в карточках его объявлений (ст. 10.1 152-ФЗ, документ /consent-publish);
  2) разрешение использовать тексты и фото объявлений (простая неисключительная лицензия, /license).
Условия показа выбирает сам: показывать ли имя, разрешить ли кнопку MAX. Каждое действие — строка журнала agent_consents
(номер, когда, как подтверждён номер, версия документов, выбранные условия, адрес). Отозвать — одной кнопкой: номер сразу
скрывается во всех объявлениях (optout_phones, source 'agent'). Без согласия на показ номер тоже скрыт — честный выбор.
"""
from __future__ import annotations

import json
import sqlite3
import time

from . import accounts, config, rules

BONUS_DAYS = 30
VERSION = "19.10.2026"   # редакция /consent-publish и /license — меняется вместе с текстами


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_consents (
        id INTEGER PRIMARY KEY, phone TEXT NOT NULL, user_id INTEGER NOT NULL, ts INTEGER NOT NULL, method TEXT,
        version TEXT, publish INTEGER NOT NULL DEFAULT 0, license INTEGER NOT NULL DEFAULT 0, show_name INTEGER NOT NULL DEFAULT 1,
        allow_max INTEGER NOT NULL DEFAULT 1, ip TEXT, revoked_at INTEGER, bonus INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_consents_phone ON agent_consents (phone, ts)")


def current(conn: sqlite3.Connection, phone: str) -> dict | None:
    _ensure(conn)
    r = conn.execute("SELECT * FROM agent_consents WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
    return dict(r) if r and not r["revoked_at"] else None


def _owned(conn: sqlite3.Connection, user_id: int, phone: str) -> str | None:
    p = rules.normalize_phone(phone)
    if p and conn.execute("SELECT 1 FROM agent_phones WHERE phone = ? AND user_id = ?", (p, user_id)).fetchone():
        return p
    return None


def give(conn: sqlite3.Connection, user_id: int, phone: str, publish: bool, license_: bool, show_name: bool,
         allow_max: bool, ip: str = "") -> dict:
    """Сохранить выбор агента. Номер должен быть подтверждён этим аккаунтом."""
    _ensure(conn)
    p = _owned(conn, user_id, phone)
    if not p:
        return {"error": "Сначала подтвердите номер — звонком или кодом в MAX."}
    # как подтверждён номер (для журнала): звонок / вход звонком / код в MAX
    row = conn.execute("SELECT method FROM phone_codes WHERE phone = ? AND status IN ('ok', 'used') ORDER BY id DESC LIMIT 1",
                       (p,)).fetchone()
    method = (row[0] if row else None) or "max"
    first_bonus = publish and not conn.execute("SELECT 1 FROM agent_consents WHERE phone = ? AND bonus = 1", (p,)).fetchone()
    conn.execute("""INSERT INTO agent_consents (phone, user_id, ts, method, version, publish, license, show_name, allow_max, ip, bonus)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                 (p, user_id, int(time.time()), method, VERSION, int(publish), int(license_), int(show_name), int(allow_max),
                  ip[:45], int(bool(first_bonus))))
    if publish:   # согласие есть — номер показываем (если раньше сам же агент его скрывал)
        conn.execute("DELETE FROM optout_phones WHERE phone = ? AND source = 'agent'", (p,))
    else:         # без согласия на показ — номер скрыт во всех объявлениях
        conn.execute("INSERT OR IGNORE INTO optout_phones (phone, ts, source) VALUES (?,?, 'agent')", (p, int(time.time())))
    conn.commit()
    if first_bonus:
        accounts.extend(conn, user_id, BONUS_DAYS)
    _bump()
    return {"ok": True, "bonus": BONUS_DAYS if first_bonus else 0}


def revoke(conn: sqlite3.Connection, user_id: int, phone: str) -> dict:
    """Отозвать согласие одной кнопкой — номер скрывается сразу во всех объявлениях."""
    _ensure(conn)
    p = _owned(conn, user_id, phone)
    if not p:
        return {"error": "Это не ваш подтверждённый номер."}
    now = int(time.time())
    conn.execute("UPDATE agent_consents SET revoked_at = ? WHERE phone = ? AND revoked_at IS NULL", (now, p))
    conn.execute("INSERT OR IGNORE INTO optout_phones (phone, ts, source) VALUES (?,?, 'agent')", (p, now))
    conn.commit()
    _bump()
    return {"ok": True}


def status(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Для страницы /agent: по каждому подтверждённому номеру — согласие, скрыт ли, сколько объявлений на сайте."""
    _ensure(conn)
    out = []
    hidden = accounts.optout_set(conn)
    for (p,) in conn.execute("SELECT phone FROM agent_phones WHERE user_id = ? ORDER BY verified_at", (user_id,)):
        c = current(conn, p)
        n = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1 AND phones LIKE ?", (f'%"{p}"%',)).fetchone()[0]
        out.append({"phone": p, "consent": c, "hidden": p in hidden, "listings": n})
    return out


_prefs: dict = {"t": 0.0, "no_name": set(), "no_max": set(), "published": set(), "no_license": set()}


def _bump() -> None:
    _prefs["t"] = 0.0


def prefs(conn: sqlite3.Connection) -> dict:
    """Номера с выбранными условиями показа (кэш на минуту): без имени, без кнопки MAX, подтвердившие согласие."""
    if time.time() - _prefs["t"] > 60:
        _ensure(conn)
        no_name, no_max, pub, no_lic = set(), set(), set(), set()
        for r in conn.execute("""SELECT a.* FROM agent_consents a JOIN (SELECT phone, MAX(id) AS id FROM agent_consents GROUP BY phone) m
                                 ON m.id = a.id"""):
            if r["revoked_at"] or not r["license"]:   # отозвал или не разрешил тексты и фото — их не отдаём клиентам в подборках
                no_lic.add(r["phone"])
            if r["revoked_at"]:
                continue
            if r["publish"]:
                pub.add(r["phone"])
            if not r["show_name"]:
                no_name.add(r["phone"])
            if not r["allow_max"]:
                no_max.add(r["phone"])
        _prefs.update(t=time.time(), no_name=no_name, no_max=no_max, published=pub, no_license=no_lic)
    return _prefs


def overview(conn: sqlite3.Connection) -> dict:
    """Для админки: доля агентов, подтвердивших номер и давших согласие (по номерам в объявлениях на сайте)."""
    _ensure(conn)
    phones: set[str] = set()
    for (ph,) in conn.execute("SELECT phones FROM listings WHERE is_active = 1 AND source = 'chat'"):
        phones.update(json.loads(ph or "[]"))
    verified = {r[0] for r in conn.execute("SELECT phone FROM agent_phones")} | set(config.ALWAYS_VERIFIED)
    pub = prefs(conn)["published"]
    return {"agents": len(phones), "verified": len(phones & verified), "published": len(phones & pub),
            "hidden": len(phones & accounts.optout_set(conn))}

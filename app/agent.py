"""Кабинет агента: подтверждение номера через мессенджер, «Мои объявления»,
правка своих объявлений, свой объект на 30 дней, журнал правок, защита от чужих правок.

Решения владельца (07.10.2026):
  * номер подтверждается сообщением с кодом СО СВОЕГО номера на наш номер в MAX/WhatsApp (через Wappi);
  * править можно только объявления со своим подтверждённым номером; первый правящий становится
    владельцем карточки — другие агенты с тем же номером в карточке её уже не меняют;
  * свои объекты добавляют только подписчики; объект живёт 20 дней: за 10, 5, 2 и 1 день — «актуален?»,
    «да» — продлили, «нет» — снят сразу, без ответа — снят по сроку;
  * все правки пишутся в журнал, админ может откатить.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

from . import accounts, config, geo, notices, rules

DAY = 86400
CODE_TTL = 30 * 60
EDIT_FIELDS = ("price", "description", "status")      # что агент может менять в объявлении из чата
OWN_FIELDS = ("type", "deal", "rooms", "room_kind", "area", "land", "floor", "floors", "price", "district",
              "complex", "street", "house", "description")


# ─── подтверждение номера ──────────────────────────────────────────────────
def verified_phones(conn: sqlite3.Connection, user_id: int) -> list[str]:
    return [r[0] for r in conn.execute("SELECT phone FROM agent_phones WHERE user_id = ? ORDER BY verified_at", (user_id,))]


def start_verification(conn: sqlite3.Connection, user_id: int, phone_raw: str) -> tuple[dict | None, str | None]:
    phone = rules.normalize_phone(phone_raw)
    if not phone:
        return None, "Проверьте номер — нужен российский номер, например +7 918 123-45-67."
    owner = conn.execute("SELECT user_id FROM agent_phones WHERE phone = ?", (phone,)).fetchone()
    # у другого аккаунта — подтвердив кодом с этого номера, человек докажет, что номер его: аккаунты объединятся
    if owner and owner[0] == user_id:
        return None, "Этот номер уже подтверждён."
    now = int(time.time())
    recent = conn.execute("SELECT COUNT(*) FROM phone_codes WHERE user_id = ? AND created > ?", (user_id, now - 3600)).fetchone()[0]
    if recent >= 5:
        return None, "Слишком много попыток — попробуйте через час."
    code = f"{secrets.randbelow(900000) + 100000}"
    conn.execute("INSERT INTO phone_codes (user_id, phone, code, created, status) VALUES (?,?,?,?, 'pending')",
                 (user_id, phone, code, now))
    conn.commit()
    return {"phone": phone, "code": code, "send_to": config.VERIFY_NUMBER}, None


CALL_TTL = 5 * 60   # SMS.ru держит проверку звонком несколько минут — потом начинаем заново


def _check_phone(conn: sqlite3.Connection, user_id: int, phone_raw: str) -> tuple[str | None, str | None]:
    phone = rules.normalize_phone(phone_raw)
    if not phone:
        return None, "Проверьте номер — нужен российский номер, например +7 918 123-45-67."
    owner = conn.execute("SELECT user_id FROM agent_phones WHERE phone = ?", (phone,)).fetchone()
    # номер у другого аккаунта — не ошибка: подтвердив звонком, человек докажет, что номер его, и аккаунты объединятся
    if owner and owner[0] == user_id:
        return None, "Этот номер уже подтверждён."
    if conn.execute("SELECT COUNT(*) FROM phone_codes WHERE user_id = ? AND created > ?",
                    (user_id, int(time.time()) - 3600)).fetchone()[0] >= 5:
        return None, "Слишком много попыток — попробуйте через час."
    return phone, None


def _confirm(conn: sqlite3.Connection, row_id: int, phone: str, user_id: int) -> None:
    conn.execute("UPDATE phone_codes SET status = 'ok' WHERE id = ?", (row_id,))
    conn.commit()
    user_id, _ = accounts.claim_phone(conn, user_id, phone)   # номер был у другого аккаунта — объединяем
    accounts.reward_referrer(conn, user_id)   # «Приведи коллегу»: пригласившему +неделя


def start_call(conn: sqlite3.Connection, user_id: int, phone_raw: str, ip: str = "") -> tuple[dict | None, str | None]:
    """17.10: подтверждение звонком — человек звонит со своего номера на номер SMS.ru, звонок сбрасывается сам."""
    from . import callcheck
    phone, why = _check_phone(conn, user_id, phone_raw)
    if not phone:
        return None, why
    from . import db
    db.flush(conn)   # перед ожиданием сети — закончить запись (иначе «база занята»)
    got, why = callcheck.start(phone, ip)
    if not got:
        return None, why
    now = int(time.time())
    conn.execute("""INSERT INTO phone_codes (user_id, phone, code, created, status, method, check_id, call_phone)
                    VALUES (?,?,?,?, 'pending', 'call', ?, ?)""", (user_id, phone, "", now, got["check_id"], got["call_phone"]))
    conn.commit()
    return {"phone": phone, "method": "call", "call_phone": got["call_phone"], "call_pretty": got["call_pretty"], "ttl": CALL_TTL}, None


def call_status(conn: sqlite3.Connection, user_id: int, phone_raw: str) -> str | None:
    """Последняя проверка звонком по этому номеру: ok / pending / expired; None — проверки звонком не было."""
    from . import callcheck
    phone = rules.normalize_phone(phone_raw) or phone_raw
    row = conn.execute("""SELECT * FROM phone_codes WHERE user_id = ? AND phone = ? AND method = 'call'
                          ORDER BY id DESC LIMIT 1""", (user_id, phone)).fetchone()
    if row is None:
        return None
    if row["status"] == "ok":
        return "ok"
    now = int(time.time())
    if row["created"] < now - CALL_TTL:
        return "expired"
    if row["checked_at"] and now - row["checked_at"] < 3:   # не дёргаем SMS.ru чаще раза в 3 секунды
        return "pending"
    conn.execute("UPDATE phone_codes SET checked_at = ? WHERE id = ?", (now, row["id"]))
    conn.commit()
    st = callcheck.status(row["check_id"])
    if st == "ok":
        _confirm(conn, row["id"], phone, user_id)
        return "ok"
    if st == "expired":
        conn.execute("UPDATE phone_codes SET status = 'expired' WHERE id = ?", (row["id"],))
        conn.commit()
        return "expired"
    return "pending"


def start_login_call(conn: sqlite3.Connection, phone_raw: str, ip: str = "") -> tuple[dict | None, str | None]:
    """18.10: вход по номеру — звонок со своего номера (SMS.ru callcheck). Возвращает tid — метку этой попытки для
    браузера (по ней он спрашивает статус; чужую попытку по номеру не угадать)."""
    from . import callcheck, db
    phone = rules.normalize_phone(phone_raw)
    if not phone:
        return None, "Проверьте номер — нужен российский номер, например +7 918 123-45-67."
    now = int(time.time())
    if conn.execute("SELECT COUNT(*) FROM phone_codes WHERE phone = ? AND method = 'login' AND created > ?",
                    (phone, now - 3600)).fetchone()[0] >= 6:
        return None, "Слишком много попыток входа с этим номером — попробуйте через час или войдите по почте."
    db.flush(conn)
    got, why = callcheck.start(phone, ip)
    if not got:
        return None, why
    tid = secrets.token_urlsafe(18)
    conn.execute("""INSERT INTO phone_codes (user_id, phone, code, created, status, method, check_id, call_phone)
                    VALUES (0, ?, ?, ?, 'pending', 'login', ?, ?)""", (phone, tid, now, got["check_id"], got["call_phone"]))
    conn.commit()
    return {"tid": tid, "phone": phone, "call_phone": got["call_phone"], "call_pretty": got["call_pretty"], "ttl": CALL_TTL}, None


def login_call_status(conn: sqlite3.Connection, tid: str) -> tuple[str, str | None]:
    """(статус, номер): ok — позвонил, можно входить (один раз: дальше 'used'), pending, expired."""
    from . import callcheck
    row = conn.execute("SELECT * FROM phone_codes WHERE code = ? AND method = 'login'", (tid,)).fetchone() if tid else None
    if row is None:
        return "expired", None
    if row["status"] in ("used", "expired"):
        return row["status"], row["phone"]
    now = int(time.time())
    if row["created"] < now - CALL_TTL:
        conn.execute("UPDATE phone_codes SET status = 'expired' WHERE id = ?", (row["id"],))
        conn.commit()
        return "expired", row["phone"]
    if row["checked_at"] and now - row["checked_at"] < 3:
        return "pending", row["phone"]
    conn.execute("UPDATE phone_codes SET checked_at = ? WHERE id = ?", (now, row["id"]))
    conn.commit()
    st = callcheck.status(row["check_id"])
    if st == "ok":
        conn.execute("UPDATE phone_codes SET status = 'used' WHERE id = ? AND status = 'pending'", (row["id"],))
        conn.commit()
        return "ok", row["phone"]
    if st == "expired":
        conn.execute("UPDATE phone_codes SET status = 'expired' WHERE id = ?", (row["id"],))
        conn.commit()
        return "expired", row["phone"]
    return "pending", row["phone"]


def has_pending(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM phone_codes WHERE status = 'pending' AND COALESCE(method, 'max') = 'max' AND created > ? LIMIT 1",
                             (int(time.time()) - CODE_TTL,)).fetchone())


def check_message(conn: sqlite3.Connection, sender_phone: str | None, text: str) -> bool:
    """Сообщение пришло на наш номер (в личку). Если в нём код от этого же номера — номер подтверждён."""
    phone = rules.normalize_phone(sender_phone or "")
    if not phone or not text:
        return False
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", text):
        row = conn.execute("""SELECT * FROM phone_codes WHERE phone = ? AND code = ? AND status = 'pending'
                              AND created > ? ORDER BY id DESC LIMIT 1""",
                           (phone, code, int(time.time()) - CODE_TTL)).fetchone()
        if row is None:
            continue
        _confirm(conn, row["id"], phone, row["user_id"])   # код с этого номера в MAX: номер его (другой аккаунт — объединяем)
        return True
    return False


def verification_status(conn: sqlite3.Connection, user_id: int, phone: str) -> str:
    phone = rules.normalize_phone(phone) or phone
    if conn.execute("SELECT 1 FROM agent_phones WHERE phone = ? AND user_id = ?", (phone, user_id)).fetchone():
        return "ok"
    row = conn.execute("SELECT created FROM phone_codes WHERE user_id = ? AND phone = ? ORDER BY id DESC LIMIT 1",
                       (user_id, phone)).fetchone()
    if row is None or row[0] < time.time() - CODE_TTL:
        return "expired"
    return "pending"


def _pretty(phone: str) -> str:
    d = "".join(ch for ch in phone if ch.isdigit())
    return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:11]}" if len(d) == 11 else phone


def check_result(conn: sqlite3.Connection, user_id: int, phone_raw: str, seen: list[dict], info: dict) -> dict:
    """Ответ кнопке «Я отправил — проверить»: подтверждено или понятное объяснение, что пошло не так."""
    phone = rules.normalize_phone(phone_raw) or phone_raw
    status = verification_status(conn, user_id, phone)
    if status != "pending":
        return {"status": status, "detail": "" if status == "ok" else "Код устарел — запросите новый."}
    row = conn.execute("SELECT code FROM phone_codes WHERE user_id = ? AND phone = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
                       (user_id, phone)).fetchone()
    code = row[0] if row else ""
    hits = [s for s in seen if code and code in s["codes"]]
    if hits and not hits[-1]["phone"]:
        why = ("Сообщение с кодом пришло, но мессенджер не показал номер отправителя. "
               "Мы получили сигнал и исправим — попробуйте чуть позже или напишите нам.")
    elif hits:
        why = (f"Код пришёл с номера {_pretty(rules.normalize_phone(hits[-1]['phone']) or hits[-1]['phone'])}, а подтверждаете "
               f"{_pretty(phone)}. Отправьте код с того номера, который подтверждаете.")
    elif not info.get("configured"):
        why = "Приём кодов сейчас не работает. Напишите нам — подтвердим номер вручную."
    elif info.get("errors"):
        why = "Не получилось связаться с мессенджером. Попробуйте ещё раз через минуту."
    else:
        to = _pretty(rules.normalize_phone(config.VERIFY_NUMBER) or config.VERIFY_NUMBER)
        why = (f"Сообщение пока не пришло. Проверьте: в MAX на номер {to}, с номера {_pretty(phone)}, "
               f"текст «1+1-{code}». Если только что отправили — нажмите ещё раз через 10–20 секунд.")
    return {"status": "pending", "detail": why, "seen_code": bool(hits)}


# ─── мои объявления ────────────────────────────────────────────────────────
def _phone_like(phones: list[str]) -> tuple[str, list]:
    return " OR ".join("l.phones LIKE ?" for _ in phones), [f'%"{p}"%' for p in phones]


def my_listings(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    phones = verified_phones(conn, user_id)
    if not phones:
        return conn.execute("SELECT * FROM listings l WHERE owner_user_id = ? ORDER BY last_seen DESC", (user_id,)).fetchall()
    cond, params = _phone_like(phones)
    # 18.10: и объекты с сайта (фид) с этим номером — чтобы было видно всё своё (править их — на сайте); без потолка 300;
    # скрытые копии своих объектов (приклеены к карточке с сайта — ownmatch) не показываем
    return conn.execute(f"""SELECT * FROM listings l WHERE (l.owner_user_id = ? OR (({cond})
                            AND (l.owner_user_id IS NULL OR l.owner_user_id = ?)))
                            AND COALESCE(l.hidden_reason, '') NOT IN ('strely', 'own_dup', 'strely_photo')
                            ORDER BY l.is_active DESC, l.last_seen DESC LIMIT 3000""",
                        [user_id, *params, user_id]).fetchall()


def can_edit(conn: sqlite3.Connection, user: sqlite3.Row, row: sqlite3.Row) -> tuple[bool, str]:
    """Править можно только своё: объявление с моим подтверждённым номером, у которого нет другого владельца."""
    if row is None:
        return False, "Объект не найден."
    if row["source"] == "feed":
        return False, "Объекты партнёра правятся на его сайте."
    if not accounts.has_access(user):
        return False, "Править объявления можно с активной подпиской."
    if row["owner_user_id"] and row["owner_user_id"] != user["id"]:
        return False, "Этой карточкой уже управляет другой агент. Если это ошибка — напишите нам."
    if row["owner_user_id"] == user["id"]:
        return True, ""
    phones = set(json.loads(row["phones"] or "[]"))
    if phones & set(verified_phones(conn, user["id"])):
        return True, ""
    return False, "В этом объявлении нет вашего подтверждённого номера."


def _log(conn, listing_id: int, user_id: int, field: str, old, new) -> None:
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                 (listing_id, user_id, int(time.time()), field, json.dumps(old, ensure_ascii=False),
                  json.dumps(new, ensure_ascii=False)))


def _too_many_edits(conn, user_id: int) -> bool:
    return conn.execute("SELECT COUNT(*) FROM listing_edits WHERE user_id = ? AND ts > ?",
                        (user_id, int(time.time()) - 3600)).fetchone()[0] >= 60


def _clean_price(v) -> int | None:
    """Цена в рублях; «6,5» — это миллионы."""
    try:
        x = float(re.sub(r"[\s ₽]", "", str(v)).replace(",", "."))
    except (TypeError, ValueError):
        return None
    n = int(round(x * 1_000_000 if x < 1000 else x))
    return n if 5_000 <= n <= 3_000_000_000 else None


def edit_listing(conn: sqlite3.Connection, user: sqlite3.Row, listing_id: int, data: dict) -> tuple[bool, str]:
    from .ingest import _price_m2, _reindex, make_search_text, make_title
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    ok, why = can_edit(conn, user, row)
    if not ok:
        return False, why
    if _too_many_edits(conn, user["id"]):
        return False, "Слишком много правок за час — попробуйте позже."
    fields = OWN_FIELDS if row["source"] == "own" else EDIT_FIELDS
    changes: dict = {}
    for k in fields:
        if k not in data or k == "status":
            continue
        v = data[k]
        if k == "price":
            v = _clean_price(v)
            if v is None:
                return False, "Проверьте цену."
        elif k in ("rooms", "floor", "floors"):
            v = int(v) if str(v).strip().isdigit() else None
        elif k in ("area", "land"):
            try:
                v = float(str(v).replace(",", ".")) if str(v).strip() else None
            except ValueError:
                return False, "Проверьте площадь."
        elif k == "description":
            v = (str(v).strip()[:3000]) or None
            if v and rules.extract_phones(v):
                v = rules.remove_phones(v)  # телефоны в описании не публикуем — они берутся из подтверждения
        elif k == "district":
            v = str(v).strip()[:80]
            v = (geo.canonical_district(v) or v) if v else None
        else:
            v = (str(v).strip()[:120]) or None
        if v != row[k]:
            changes[k] = v
    status = data.get("status")
    now = int(time.time())
    if changes:
        merged = {**dict(row), **changes}
        if "rooms" in changes:   # для кнопок «Комнаты»: агент указывает обычное число комнат (0 — студия)
            merged["room_kind"] = changes["room_kind"] = "studio" if merged["rooms"] == 0 else (
                merged["room_kind"] if merged["room_kind"] not in (None, "studio") else "classic")
            changes["rooms_mask"] = rules.rooms_mask(merged["room_kind"], merged["rooms"])
        merged["title"] = make_title(merged)
        merged["search_text"] = make_search_text(merged)
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"""UPDATE listings SET {sets}, title = ?, search_text = ?, price_m2 = ?, owner_user_id = ?,
                         owner_edited_at = ? WHERE id = ?""",
                     [*changes.values(), merged["title"], merged["search_text"],
                      _price_m2(merged["price"], merged["area"]), user["id"], now, listing_id])
        _reindex(conn, listing_id, merged["search_text"])
        if changes.keys() & {"street", "house", "complex", "district"}:   # адрес поменялся — точку на карте ищем заново
            conn.execute("UPDATE listings SET geo_status = 'pending' WHERE id = ? AND geo_status NOT IN ('manual', 'feed')", (listing_id,))
        for k, v in changes.items():
            _log(conn, listing_id, user["id"], k, row[k], v)
    if status in ("sold", "active"):
        if status == "sold" and row["is_active"]:
            conn.execute("UPDATE listings SET is_active = 0, sold_at = ?, owner_user_id = ? WHERE id = ?",
                         (now, user["id"], listing_id))
            _log(conn, listing_id, user["id"], "status", "active", "sold")
        elif status == "active" and not row["is_active"]:
            conn.execute("""UPDATE listings SET is_active = 1, sold_at = NULL, last_seen = ?, owner_user_id = ?,
                            expires_at = CASE WHEN source = 'own' THEN ? ELSE expires_at END, confirm_stage = NULL WHERE id = ?""",
                         (now, user["id"], now + config.OWN_LISTING_DAYS * DAY, listing_id))
            _log(conn, listing_id, user["id"], "status", "sold", "active")
    conn.commit()
    return True, "Сохранено."


def create_own(conn: sqlite3.Connection, user: sqlite3.Row, data: dict) -> tuple[int | None, str]:
    """Свой объект: только с подпиской и подтверждённым номером. Живёт OWN_LISTING_DAYS дней."""
    from .ingest import _price_m2, _reindex, make_search_text, make_title
    if not accounts.has_access(user):
        return None, "Добавлять объекты можно с активной подпиской."
    phones = verified_phones(conn, user["id"])
    if not phones:
        return None, "Сначала подтвердите свой номер — он будет указан в объявлении."
    if conn.execute("SELECT COUNT(*) FROM listings WHERE owner_user_id = ? AND source = 'own' AND first_seen > ?",
                    (user["id"], int(time.time()) - DAY)).fetchone()[0] >= 20:
        return None, "Не больше 20 новых объектов в сутки."
    t = data.get("type") if data.get("type") in ("flat", "room", "house", "land", "commercial") else None
    price = _clean_price(data.get("price"))
    if not t or not price:
        return None, "Укажите тип объекта и цену."
    if not any(str(data.get(k) or "").strip() for k in ("district", "complex", "street")):
        return None, "Укажите район, ЖК или улицу — без этого объект не найдут в поиске и на карте."
    now = int(time.time())
    d = {"type": t, "deal": "rent" if data.get("deal") == "rent" else "sale", "rooms": None, "room_kind": None,
         "area": None, "land": None, "floor": None, "floors": None, "price": price, "district": None, "complex": None,
         "settlement": None, "street": None, "house": None, "description": None}
    cur = conn.execute("""INSERT INTO listings (type, deal, price, title, phones, first_seen, last_seen, seen_count,
                          is_active, source, owner_user_id, expires_at, search_text)
                          VALUES (?,?,?,?,?,?,?,1,1,'own',?,?, '')""",
                       (t, d["deal"], price, "Объект", json.dumps([phones[0]]), now, now, user["id"],
                        now + config.OWN_LISTING_DAYS * DAY))
    lid = cur.lastrowid
    conn.commit()
    ok, why = edit_listing(conn, user, lid, {k: v for k, v in data.items() if k in OWN_FIELDS})
    if not ok:
        conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
        conn.commit()
        return None, why
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if not row["title"] or row["title"] == "Объект":
        title = make_title(dict(row))
        st = make_search_text({**dict(row), "title": title})
        conn.execute("UPDATE listings SET title = ?, search_text = ?, price_m2 = ? WHERE id = ?",
                     (title, st, _price_m2(row["price"], row["area"]), lid))
        _reindex(conn, lid, st)
        conn.commit()
    _log(conn, lid, user["id"], "create", None, "own")
    conn.commit()
    return lid, "Объект добавлен."


# ─── актуальность своих объектов ───────────────────────────────────────────
def confirm_token(listing_id: int, action: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"{listing_id}:{action}".encode(), hashlib.sha256).hexdigest()[:24]


def confirm(conn: sqlite3.Connection, listing_id: int, action: str, token: str) -> str:
    if not hmac.compare_digest(token, confirm_token(listing_id, action)):
        return "bad"
    row = conn.execute("SELECT * FROM listings WHERE id = ? AND source = 'own'", (listing_id,)).fetchone()
    if row is None:
        return "gone"
    now = int(time.time())
    if action == "yes":
        conn.execute("UPDATE listings SET expires_at = ?, last_seen = ?, is_active = 1, confirm_sent = NULL, confirm_stage = NULL WHERE id = ?",
                     (now + config.OWN_LISTING_DAYS * DAY, now, listing_id))
    else:
        conn.execute("UPDATE listings SET is_active = 0, sold_at = ? WHERE id = ?", (now, listing_id))
    _log(conn, listing_id, row["owner_user_id"] or 0, "confirm", None, action)
    conn.commit()
    return action


REMIND_DAYS = (10, 5, 2, 1)   # напоминания «ещё актуален?» за столько дней до конца срока (решение Артёма 13.10)


def _days_word(n: int) -> str:
    return "день" if n % 10 == 1 and n % 100 != 11 else "дня" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "дней"


def expire_own(conn: sqlite3.Connection, send_mail=None) -> dict:
    """Раз в цикл worker: свой объект живёт OWN_LISTING_DAYS; за 10, 5, 2 и 1 день до конца — «ещё актуален?»
    (на сайте и письмом, с кнопками «да, продлить» / «нет, снять»); по сроку — снять."""
    now = int(time.time())
    sent = 0
    for r in conn.execute("""SELECT l.*, u.email FROM listings l JOIN users u ON u.id = l.owner_user_id
                             WHERE l.source = 'own' AND l.is_active = 1 AND l.expires_at >= ? AND l.expires_at < ?""",
                          (now, now + max(REMIND_DAYS) * DAY + 60)).fetchall():
        left = max(1, -(-(r["expires_at"] - now) // DAY))   # дней до конца, с округлением вверх
        stage = min(d for d in REMIND_DAYS if left <= d)
        if r["confirm_stage"] is not None and r["confirm_stage"] <= stage:
            continue   # это напоминание уже было
        yes = f"{config.SITE_URL}/agent/confirm?l={r['id']}&a=yes&t={confirm_token(r['id'], 'yes')}"
        no = f"{config.SITE_URL}/agent/confirm?l={r['id']}&a=no&t={confirm_token(r['id'], 'no')}"
        when = f"через {left} {_days_word(left)}" if left > 1 else "завтра"
        notices.add(conn, r["owner_user_id"], "own", "Ваш объект ещё актуален?",
                    f"«{r['title']}» будет снят с сайта {when}, если не подтвердить.", listing_id=r["id"],
                    url=f"/my/edit?id={r['id']}", dedup_s=0,
                    actions=[{"label": f"Да, продлить на {config.OWN_LISTING_DAYS} дней", "url": yes},
                             {"label": "Нет, снять", "url": no}])
        if r["email"] and send_mail:
            send_mail(r["email"], f"1+1: объект ещё актуален? {r['title']}",
                      f"Ваш объект «{r['title']}» будет снят с сайта {when}.\n\n"
                      f"Ещё продаётся — продлить на {config.OWN_LISTING_DAYS} дней:\n{yes}\n\n"
                      f"Уже продан или неактуален — снять сейчас:\n{no}")
            sent += 1
        conn.execute("UPDATE listings SET confirm_sent = ?, confirm_stage = ? WHERE id = ?", (now, stage, r["id"]))
    gone = conn.execute("SELECT id, title, owner_user_id FROM listings WHERE source = 'own' AND is_active = 1 "
                        "AND expires_at < ?", (now,)).fetchall()
    for r in gone:
        notices.add(conn, r["owner_user_id"], "own", "Объект снят по сроку",
                    f"«{r['title']}» не подтвердили вовремя. Вернуть можно в «Мои объекты» → «Вернуть на сайт».",
                    listing_id=r["id"], url="/my")
    expired = conn.execute("UPDATE listings SET is_active = 0 WHERE source = 'own' AND is_active = 1 AND expires_at < ?",
                           (now,)).rowcount
    conn.commit()
    return {"asked": sent, "expired": expired}


def revert_edit(conn: sqlite3.Connection, edit_id: int) -> bool:
    """Админ откатывает правку агента."""
    from .ingest import _reindex, make_search_text, make_title
    e = conn.execute("SELECT * FROM listing_edits WHERE id = ? AND reverted = 0", (edit_id,)).fetchone()
    if e is None or e["field"] not in OWN_FIELDS + ("extra_districts", "point"):
        return False
    old = json.loads(e["old"])
    if e["field"] == "point":
        lat, lon, status, src = (list(old or []) + [None] * 4)[:4]
        conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ?, geo_src = ? WHERE id = ?",
                     (lat, lon, status or "pending", src, e["listing_id"]))
        conn.execute("UPDATE listing_edits SET reverted = 1 WHERE id = ?", (edit_id,))
        conn.commit()
        return True
    if e["field"] == "extra_districts":   # второй район хранится строкой JSON
        old = json.dumps(old or [], ensure_ascii=False)
    conn.execute(f"UPDATE listings SET {e['field']} = ? WHERE id = ?", (old, e["listing_id"]))
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (e["listing_id"],)).fetchone()
    title = make_title(dict(row))
    st = make_search_text({**dict(row), "title": title})
    from .ingest import _price_m2
    conn.execute("UPDATE listings SET title = ?, search_text = ?, price_m2 = ? WHERE id = ?",
                 (title, st, _price_m2(row["price"], row["area"]), e["listing_id"]))
    _reindex(conn, e["listing_id"], st)
    conn.execute("UPDATE listing_edits SET reverted = 1 WHERE id = ?", (edit_id,))
    conn.commit()
    return True


# ─── фото ──────────────────────────────────────────────────────────────────
_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp"}


def add_photo(conn: sqlite3.Connection, user: sqlite3.Row, listing_id: int, data_url: str) -> tuple[list | None, str]:
    """Фото приходит из браузера уже уменьшенным (до 1600 px, JPEG) в виде data:-строки."""
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    ok, why = can_edit(conn, user, row)
    if not ok:
        return None, why
    photos = json.loads(row["photos"] or "[]")
    if len(photos) >= config.MAX_PHOTOS:
        return None, f"Не больше {config.MAX_PHOTOS} фото."
    try:
        raw = base64.b64decode(str(data_url).split(",", 1)[-1], validate=False)
    except (ValueError, TypeError):
        return None, "Не получилось прочитать фото."
    ext = next((e for magic, e in _MAGIC.items() if raw.startswith(magic)), None)
    if ext is None or len(raw) > 3_000_000 or len(raw) < 1000:
        return None, "Нужна фотография JPEG/PNG до 3 МБ."
    name = hashlib.sha1(raw).hexdigest()[:16] + "." + ext
    folder = Path(config.PHOTOS_DIR) / str(listing_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(raw)
    url = f"/photos/{listing_id}/{name}"
    if url not in photos:
        photos.append(url)
    conn.execute("UPDATE listings SET photos = ?, owner_user_id = ?, owner_edited_at = ? WHERE id = ?",
                 (json.dumps(photos), user["id"], int(time.time()), listing_id))
    _log(conn, listing_id, user["id"], "photo+", None, url)
    conn.commit()
    return photos, "Фото добавлено."


def remove_photo(conn: sqlite3.Connection, user: sqlite3.Row, listing_id: int, url: str) -> tuple[list | None, str]:
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    ok, why = can_edit(conn, user, row)
    if not ok:
        return None, why
    photos = json.loads(row["photos"] or "[]")
    if url not in photos:
        return photos, "Фото уже удалено."
    photos.remove(url)
    conn.execute("UPDATE listings SET photos = ? WHERE id = ?", (json.dumps(photos), listing_id))
    _log(conn, listing_id, user["id"], "photo-", url, None)
    conn.commit()
    if url.startswith(f"/photos/{listing_id}/"):
        f = Path(config.PHOTOS_DIR) / str(listing_id) / url.rsplit("/", 1)[-1]
        f.unlink(missing_ok=True)
    return photos, "Фото удалено."


def edits_for_admin(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute("""SELECT e.*, l.title, u.email, u.name FROM listing_edits e
                           LEFT JOIN listings l ON l.id = e.listing_id LEFT JOIN users u ON u.id = e.user_id
                           ORDER BY e.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ─── «Написать красиво»: продающее описание объявления нейросетью ───────────
DESCRIBE_PROMPT = """Ты опытный риелтор в Краснодаре. Напиши продающее описание объекта для риелторских чатов и сайтов.
Правила:
- Только факты из данных ниже. Ничего не придумывай: ни ремонта, ни мебели, ни инфраструктуры, ни расстояний, если их нет в данных.
- Начни с короткого цепляющего заголовка в одну строку (тип, комнаты, ЖК/район, главная выгода).
- Дальше 3–6 коротких абзацев или списком через «—»: параметры, расположение, что важно покупателю, условия сделки, если они есть.
- Тон — деловой и тёплый, без восклицательных знаков подряд, без капслока, без хэштегов, смайликов не больше двух.
- НЕ пиши номера телефонов, ссылки и имена — контакт агента покажется отдельно.
- Цену пиши так: «6,5 млн ₽» (продажа) или «35 000 ₽ в месяц» (аренда).
- 600–900 символов. Только текст объявления, без пояснений."""
TYPE_NAMES = {"flat": "квартира", "room": "комната", "house": "дом", "land": "участок", "commercial": "коммерческое помещение"}


def describe(data: dict) -> tuple[str | None, str]:
    """Текст объявления по параметрам (и прежнему описанию — как источнику фактов)."""
    from . import llm, parser
    if not llm.available():
        return None, "Нейросеть пока не подключена — попросите администратора вписать ключ DeepSeek."
    def g(k):
        v = data.get(k)
        return str(v).strip() if v not in (None, "") else ""
    facts = [
        f"Сделка: {'аренда' if data.get('deal') == 'rent' else 'продажа'}",
        f"Тип: {TYPE_NAMES.get(data.get('type'), g('type') or 'квартира')}",
        g("rooms") and f"Комнат: {'студия' if g('rooms') == '0' else g('rooms')}",
        g("area") and f"Площадь: {g('area')} м²", g("land") and f"Участок: {g('land')} сот.",
        g("floor") and f"Этаж: {g('floor')}" + (f" из {g('floors')}" if g("floors") else ""),
        g("complex") and f"ЖК: {g('complex')}", g("district") and f"Район: {g('district')}",
        g("street") and f"Улица: {g('street')}" + (f", дом {g('house')}" if g("house") else ""),
        g("price") and f"Цена: {g('price')}" + (" ₽ в месяц" if data.get("deal") == "rent" else " (если число маленькое — это млн ₽)"),
        g("photos") and f"Фото в объявлении: {g('photos')} шт.",
    ]
    old = parser.strip_phones(g("description"))[:3000]
    user = "Данные объекта:\n" + "\n".join(f for f in facts if f) + (f"\n\nПрежний текст агента (источник фактов):\n{old}" if old else "")
    try:
        text = llm.ask_text(DESCRIBE_PROMPT, user)
    except llm.LLMError:
        return None, "Нейросеть сейчас не ответила — попробуйте через минуту."
    return parser.strip_phones(text)[:3000], "ok"

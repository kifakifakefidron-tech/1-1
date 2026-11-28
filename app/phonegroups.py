"""25.10 (Артём: «объекты от этих номеров одинаковые, но отправляются с разных номеров — совместим их»).

«Один агент — несколько номеров»: номера одной группы склейка повторов считает ОДНИМ номером — тот же текст с другим
номером внутри, тот же агент + место + комнаты + этаж (dedupe, dupreview). Группы ведёт админ на /dups
(state phone_groups: [[«+7…», …], …]); по умолчанию — PHONE_GROUPS из config. После правки всё перепроверяется заново."""
from __future__ import annotations

import json
import sqlite3
import time

from . import config, db
from .rules import contact_phone, extract_phones, normalize_phone

TTL_S = 60
_PRIMARY_CONTACT = "+79385151639"
_CONTACT_GROUP = (_PRIMARY_CONTACT, "+79528404528", "+79024064417", "+79186305681")
_CONTACT_BACKFILL = "primary_listing_contact_v1"
_map = dict.fromkeys(_CONTACT_GROUP, _PRIMARY_CONTACT)
_loaded = 0.0


def primary_contact(phones) -> str | None:
    """Единый контакт для объявлений этого агента, в том числе при рассылке с другого номера."""
    return _PRIMARY_CONTACT if any(p in _CONTACT_GROUP or contact_phone(p) in _CONTACT_GROUP for p in phones) else None


def contact_numbers(phone: str) -> tuple[str, ...]:
    """Номера источников известного основного контакта."""
    return _CONTACT_GROUP if phone == _PRIMARY_CONTACT else (phone,)


def _stored(raw, kind):
    try:
        value = json.loads(raw or ("[]" if kind is list else "{}"))
    except (TypeError, ValueError):
        return kind()
    return value if isinstance(value, kind) else kind()


def listing_phones(row) -> list[str]:
    """Контакты карточки. Исходные номера остаются в базе для истории и проверки владельца."""
    raw = row["phones"]
    phones = raw if isinstance(raw, list) else _stored(raw, list)
    roles = _stored(row["phone_roles"], dict) if "phone_roles" in row.keys() else {}
    primary = primary_contact(phones) or primary_contact(roles)
    return [primary] if primary else phones


def listing_phones_json(phones: str, roles: str) -> str:
    return json.dumps(listing_phones({"phones": phones, "phone_roles": roles}))


def remember_primary_contact(conn: sqlite3.Connection, listing_id: int, role: str) -> bool:
    row = conn.execute("SELECT phone_roles FROM listings WHERE id=?", (listing_id,)).fetchone()
    if row is None:
        return False
    roles = _stored(row["phone_roles"], dict)
    if roles.get(_PRIMARY_CONTACT) in (role, "text"):
        return False
    roles[_PRIMARY_CONTACT] = role
    conn.execute("UPDATE listings SET phone_roles=? WHERE id=?", (json.dumps(roles), listing_id))
    return True


def backfill_listing_contacts(conn: sqlite3.Connection) -> int:
    """Применяет выбранный номер к старым карточкам, в том числе по истории отправителей."""
    if db.get_state(conn, _CONTACT_BACKFILL):
        return 0
    senders = [sender for (sender,) in conn.execute("SELECT DISTINCT sender FROM messages WHERE sender IS NOT NULL")
               if primary_contact((sender,))]
    sender_ids = {lid for (lid,) in conn.execute("""SELECT DISTINCT e.listing_id FROM messages m
        JOIN listing_events e ON e.message_id=m.id WHERE m.sender IN (SELECT value FROM json_each(?))""",
        (json.dumps(senders),))}
    event_ids = {lid for (lid,) in conn.execute("""SELECT DISTINCT e.listing_id FROM listing_events e
        WHERE EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(e.phones) THEN e.phones ELSE '[]' END) ph
                      WHERE ph.value IN (SELECT value FROM json_each(?)))""", (json.dumps(_CONTACT_GROUP),))}
    matched = []
    for row in conn.execute("SELECT id,phones,phone_roles,fragment,description FROM listings"):
        lid = row["id"]
        roles = _stored(row["phone_roles"], dict)
        declared = (primary_contact(p for p, role in roles.items() if role == "text")
                    or primary_contact(extract_phones(row["fragment"]))
                    or primary_contact(extract_phones(row["description"])))
        sender = lid in sender_ids or primary_contact(p for p, role in roles.items() if role == "sender")
        if declared or sender or primary_contact(_stored(row["phones"], list)) or lid in event_ids:
            matched.append((lid, "text" if declared or not sender else "sender"))
    # Чтение истории закончено. В транзакции остаются только небольшие изменения карточек.
    with conn:
        if not conn.in_transaction:
            conn.execute("BEGIN IMMEDIATE")
        if db.get_state(conn, _CONTACT_BACKFILL):
            return 0
        for lid, role in matched:
            remember_primary_contact(conn, lid, role)
        if matched:
            conn.executemany("""INSERT INTO dup_changed(listing_id) SELECT id FROM listings WHERE id=? AND is_active=1
                ON CONFLICT(listing_id) DO UPDATE SET revision=revision+1""", ((lid,) for lid, _ in matched))
            conn.execute("""INSERT INTO state(key,value) VALUES ('cache_ver',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(time.time()),))
        conn.execute("INSERT INTO state(key,value) VALUES (?, '1')", (_CONTACT_BACKFILL,))
    return len(matched)


def parse(text: str) -> list[list[str]]:
    """Строка — одна группа, номера через запятую / точку с запятой / «и»."""
    import re
    out = []
    for line in str(text or "").splitlines():
        nums = []
        for part in re.split(r"[,;]|\s+и\s+", line):
            p = normalize_phone(part)
            if p and p not in nums:
                nums.append(p)
        if len(nums) >= 2:
            out.append(nums)
    return out


def groups(conn: sqlite3.Connection) -> list[list[str]]:
    raw = db.get_state(conn, "phone_groups")
    if raw is None:
        return [g[:] for g in config.PHONE_GROUPS]
    try:
        return [list(g) for g in json.loads(raw) if isinstance(g, list)]
    except ValueError:
        return []


def _build(gs: list[list[str]]) -> dict[str, str]:
    m = dict.fromkeys(_CONTACT_GROUP, _PRIMARY_CONTACT)
    for g in gs:   # номер в нескольких группах — группы сливаются
        roots = {m[p] for p in g if p in m}
        root = _PRIMARY_CONTACT if _PRIMARY_CONTACT in roots else min(roots) if roots else g[0]
        for p, r in list(m.items()):
            if r in roots:
                m[p] = root
        for p in g:
            m[p] = root
    return m


_conn_id = 0


def refresh(conn: sqlite3.Connection, force: bool = False) -> None:
    global _map, _loaded, _conn_id
    if force or id(conn) != _conn_id or time.time() - _loaded > TTL_S:
        _map = _build(groups(conn))
        _loaded, _conn_id = time.time(), id(conn)


def canon(phone: str) -> str:
    return _map.get(phone, phone)


def canon_set(phones) -> set[str]:
    return {canon(p) for p in phones or ()}


def save(conn: sqlite3.Connection, text: str) -> list[list[str]]:
    """Сохранить группы и перепроверить все карточки заново (worker — следующим циклом)."""
    gs = parse(text)
    db.set_state(conn, "phone_groups", json.dumps(gs, ensure_ascii=False))
    db.set_state(conn, "dup_scan_id", "0")
    conn.execute("UPDATE dup_pairs SET ai=NULL WHERE status='open' AND COALESCE(user_id,0)=0")
    conn.commit()
    refresh(conn, force=True)
    return gs


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Для окна на /dups: группы, у каждого номера — сколько карточек на сайте с ним."""
    from .dupreview import _pretty
    out = []
    for g in groups(conn):
        nums = [{"phone": ph, "pretty": _pretty(ph), "n": conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND instr(phones, ?) > 0", (f'"{ph}"',)).fetchone()[0]}
            for ph in g]
        out.append({"phones": nums})
    return out


def add(conn: sqlite3.Connection, phones: list[str]) -> list[list[str]]:
    """Добавить номера одним агентом (из кнопки «Это один агент» в сравнении): слить с группой, где уже есть такой номер."""
    nums = [p for p in (normalize_phone(x) for x in phones or ()) if p]
    gs = groups(conn)
    if len(set(nums)) < 2 and not any(set(nums) & set(g) for g in gs):
        return gs
    gs.append(list(dict.fromkeys(nums)))
    m = _build(gs)
    merged: dict[str, list[str]] = {}
    for g in gs:
        for ph in g:
            lst = merged.setdefault(m[ph], [])
            if ph not in lst:
                lst.append(ph)
    return save(conn, "\n".join(", ".join(v) for v in merged.values() if len(v) >= 2))

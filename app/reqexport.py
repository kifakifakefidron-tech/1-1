"""Выгрузка новых запросов покупателей в Google Таблицу рассылки (tools/google-sheet-outreach).

11.11 (Артём): таблица сама забирает новые запросы с сайта. Защита — ключ EXPORT_KEY из .env
(заголовок X-Export-Key); без ключа выгрузки нет вовсе.

Отдаём только запросы из чатов, у которых есть номер, и НЕ отдаём номера:
 - уже пользователей 1+1 (подтверждённые номера агентов, номер аккаунта) — им реклама не нужна;
 - тех, кто просил скрыть номер (optout_phones) и удалённых админом (banned phone);
 - свои (фид СТРЕЛ, поддержка, ALWAYS_VERIFIED).
Курсор — id запроса: таблица помнит последний и просит только новее.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import config, parser, rules

LIMIT_MAX = 500
_SRC = {"wa": "WA", "tg": "TG", "max": "MAX"}


def _blocked_phones(conn: sqlite3.Connection) -> set[str]:
    out = {rules.normalize_phone(p) for p in (config.FEED_PHONE, config.SUPPORT_PHONE, *config.ALWAYS_VERIFIED)}
    for sql in ("SELECT phone FROM agent_phones", "SELECT phone FROM users WHERE phone IS NOT NULL",
                "SELECT phone FROM optout_phones", "SELECT key FROM banned WHERE kind = 'phone'"):
        try:
            out.update(rules.normalize_phone(str(r[0])) for r in conn.execute(sql))
        except sqlite3.OperationalError:
            pass
    out.discard(None)
    return out


def _name(raw: str | None, phone: str) -> str:
    """Имя отправителя из чата. Номер вместо имени («+7 918…», «79181234567») — не имя."""
    s = " ".join(str(raw or "").split())
    if not s or re.sub(r"\D", "", s)[-10:] == phone[-10:] or not re.search(r"[A-Za-zА-Яа-яЁё]", s):
        return ""
    return s[:80]


def export(conn: sqlite3.Connection, after: int = 0, limit: int = 200) -> dict:
    limit = max(1, min(int(limit or 200), LIMIT_MAX))
    rows = conn.execute(
        """SELECT b.id, b.text, b.chat_name, b.deal, b.phones, b.first_seen, m.source AS msg_source,
                  m.sender_name, m.chat_name AS msg_chat
           FROM buyer_requests b LEFT JOIN messages m ON m.id = b.message_id
           WHERE b.id > ? AND b.removed = 0 AND COALESCE(b.source, 'chat') = 'chat'
           ORDER BY b.id LIMIT ?""", (int(after or 0), limit)).fetchall()
    blocked = _blocked_phones(conn)
    items, seen, skipped = [], set(), {"no_phone": 0, "user_or_hidden": 0, "same_phone": 0}
    for r in rows:
        try:
            phones = [p for p in json.loads(r["phones"] or "[]") if p]
        except ValueError:
            phones = []
        phones = [p for p in (rules.normalize_phone(p) for p in phones) if p]
        if not phones:
            skipped["no_phone"] += 1
            continue
        if any(p in blocked for p in phones):
            skipped["user_or_hidden"] += 1
            continue
        phone = phones[0]
        if phone in seen:
            skipped["same_phone"] += 1
            continue
        seen.add(phone)
        chat = (r["chat_name"] or r["msg_chat"] or "").strip()
        src = _SRC.get(r["msg_source"] or "", "")
        items.append({
            "id": r["id"],
            "name": _name(r["sender_name"], phone),
            "phone": phone,
            "source": " ".join(x for x in (src, chat) if x),
            "text": parser.strip_phones(r["text"] or "").strip()[:1500],   # номер — в своей колонке, не в цитате
            "deal": r["deal"],
            "ts": r["first_seen"],
        })
    last_id = rows[-1]["id"] if rows else int(after or 0)
    more = len(rows) == limit
    return {"items": items, "last_id": last_id, "more": more, "skipped": skipped}

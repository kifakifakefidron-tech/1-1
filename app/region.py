"""Какие чаты не берём.

1) Автоматически — чаты других городов (Сочи, Адлер, Сириус, Туапсе…): BLOCKED_PLACES в .env,
   по названию чата и по населённому пункту объекта.
2) Вручную — админка → «Чаты»: любой чат можно отключить («не брать») и включить обратно.

Сообщения из отключённых чатов не разбираются, а объекты, которые присылали ТОЛЬКО туда,
скрываются (hidden_reason='chat'). Если чат включить обратно — такие объекты возвращаются.
Название чата WhatsApp Wappi в сообщениях не присылает — берём из списка чатов (wappi.refresh_chat_names)."""
from __future__ import annotations

import re
import sqlite3
import time
from functools import lru_cache

from . import config
from .textnorm import words


@lru_cache(maxsize=1)
def _rx() -> re.Pattern | None:
    places = [words(p) for p in config.BLOCKED_PLACES if words(p)]
    if not places:
        return None
    return re.compile(r"(?:^|\s)(?:" + "|".join(re.escape(p) for p in places) + r")")


def is_foreign(name: str | None) -> bool:
    """Название чата или населённого пункта относится к другому городу/побережью."""
    rx = _rx()
    return bool(name and rx and rx.search(words(name)))


_blocked: dict = {"t": 0.0, "set": set()}


def blocked_set(conn: sqlite3.Connection, fresh: bool = False) -> set[tuple[str, str]]:
    """Чаты, отключённые вручную (кэш на минуту)."""
    if fresh or time.time() - _blocked["t"] > 60:
        _blocked.update(t=time.time(), set={(r[0], r[1]) for r in conn.execute(
            "SELECT source, chat_id FROM chats WHERE blocked = 1")})
    return _blocked["set"]


def is_excluded(conn: sqlite3.Connection, source: str, chat_id: str | None, name: str | None) -> bool:
    return is_foreign(name) or (source, chat_id or "") in blocked_set(conn)


def chat_name(conn: sqlite3.Connection, source: str, chat_id: str | None) -> str | None:
    if not chat_id:
        return None
    row = conn.execute("SELECT name FROM chats WHERE source = ? AND chat_id = ?", (source, chat_id)).fetchone()
    return row[0] if row else None


def save_chat(conn: sqlite3.Connection, source: str, chat_id: str, name: str | None) -> None:
    if not chat_id or not name:
        return
    conn.execute("""INSERT INTO chats (source, chat_id, name, foreign_place) VALUES (?,?,?,?)
                    ON CONFLICT(source, chat_id) DO UPDATE SET name = excluded.name,
                    foreign_place = excluded.foreign_place""", (source, chat_id, name, int(is_foreign(name))))


def hide_foreign(conn: sqlite3.Connection) -> dict:
    """Проставить названия чатов в старые сообщения, не разбирать сообщения из отключённых чатов,
    скрыть объекты, которые присылали только туда (или с населённым пунктом из списка)."""
    conn.execute("""INSERT OR IGNORE INTO chats (source, chat_id, name, foreign_place)
                    SELECT source, chat_id, MAX(chat_name), 0 FROM messages
                    WHERE chat_id IS NOT NULL AND chat_id != '' AND source IN ('wa', 'tg', 'max') GROUP BY 1, 2""")
    conn.execute("""UPDATE chats SET name = (SELECT MAX(m.chat_name) FROM messages m
                        WHERE m.source = chats.source AND m.chat_id = chats.chat_id)
                    WHERE name IS NULL OR name = ''""")
    conn.execute("""UPDATE messages SET chat_name = (SELECT c.name FROM chats c
                        WHERE c.source = messages.source AND c.chat_id = messages.chat_id)
                    WHERE (chat_name IS NULL OR chat_name = '') AND EXISTS (SELECT 1 FROM chats c
                        WHERE c.source = messages.source AND c.chat_id = messages.chat_id AND c.name IS NOT NULL)""")
    blocked = blocked_set(conn, fresh=True)
    conn.create_function("excluded_chat", 3, lambda s, cid, n: int(is_foreign(n) or (s, cid or "") in blocked),
                         deterministic=True)
    bad_msgs = "SELECT id FROM messages WHERE excluded_chat(source, chat_id, chat_name) = 1"
    skipped = conn.execute(f"UPDATE messages SET status = 'skipped', error = 'чат исключён' "
                           f"WHERE status = 'new' AND id IN ({bad_msgs})").rowcount
    hidden = conn.execute(f"""UPDATE listings SET is_active = 0, hidden_reason = 'chat'
        WHERE is_active = 1 AND source = 'chat'
        AND EXISTS (SELECT 1 FROM listing_events e WHERE e.listing_id = listings.id AND e.message_id IN ({bad_msgs}))
        AND NOT EXISTS (SELECT 1 FROM listing_events e JOIN messages m ON m.id = e.message_id
                        WHERE e.listing_id = listings.id AND excluded_chat(m.source, m.chat_id, m.chat_name) = 0)""").rowcount
    by_place = 0
    for r in conn.execute("SELECT id, settlement FROM listings WHERE is_active = 1 AND source = 'chat' "
                          "AND settlement IS NOT NULL").fetchall():
        if is_foreign(r["settlement"]):
            conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'chat' WHERE id = ?", (r["id"],))
            by_place += 1
    conn.commit()
    return {"skipped": skipped, "hidden": hidden + by_place}


def set_blocked(conn: sqlite3.Connection, source: str, chat_id: str, blocked: bool) -> dict:
    """Админ отключил/включил чат. Объекты пересчитываются сразу."""
    conn.execute("""INSERT INTO chats (source, chat_id, blocked) VALUES (?,?,?)
                    ON CONFLICT(source, chat_id) DO UPDATE SET blocked = excluded.blocked""",
                 (source, chat_id, int(blocked)))
    if not blocked:
        # Вернуть то, что скрывали из-за чатов, и снова поставить в очередь пропущенные сообщения этого чата
        conn.execute("UPDATE messages SET status = 'new', error = NULL WHERE source = ? AND chat_id = ? "
                     "AND status = 'skipped' AND error = 'чат исключён'", (source, chat_id))
        conn.execute("UPDATE listings SET is_active = 1, hidden_reason = NULL WHERE hidden_reason = 'chat' "
                     "AND last_seen >= ?", (int(time.time()) - config.STALE_DAYS * 86400,))
    conn.commit()
    return hide_foreign(conn)


def chats_for_admin(conn: sqlite3.Connection) -> list[dict]:
    hide_foreign(conn)   # подтянуть чаты из сообщений
    rows = conn.execute("""
        SELECT c.source, c.chat_id, c.name, c.blocked, c.foreign_place,
               (SELECT COUNT(*) FROM messages m WHERE m.source = c.source AND m.chat_id = c.chat_id) AS messages,
               (SELECT MAX(ts) FROM messages m WHERE m.source = c.source AND m.chat_id = c.chat_id) AS last_ts,
               (SELECT COUNT(DISTINCT e.listing_id) FROM messages m JOIN listing_events e ON e.message_id = m.id
                  JOIN listings l ON l.id = e.listing_id AND l.is_active = 1
                WHERE m.source = c.source AND m.chat_id = c.chat_id) AS listings
        FROM chats c ORDER BY messages DESC, c.name""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["foreign"] = is_foreign(r["name"])
        out.append(d)
    return out

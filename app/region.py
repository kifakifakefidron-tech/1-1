"""Пока работаем только по Краснодару: чаты Сочи, Адлера, Сириуса, Туапсе и т. п. не берём,
а объекты, пришедшие только из таких чатов, не показываем.

Список мест — BLOCKED_PLACES в .env (через запятую, начало слова). Название чата WhatsApp
Wappi в сообщениях не присылает — берём из списка чатов (wappi.refresh_chat_names)."""
from __future__ import annotations

import re
import sqlite3
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
    """Проставить названия чатов в старые сообщения, не разбирать сообщения из «чужих» чатов,
    скрыть объекты, которые присылали только туда (или с населённым пунктом из списка)."""
    conn.execute("""UPDATE messages SET chat_name = (SELECT c.name FROM chats c
                        WHERE c.source = messages.source AND c.chat_id = messages.chat_id)
                    WHERE (chat_name IS NULL OR chat_name = '') AND EXISTS (SELECT 1 FROM chats c
                        WHERE c.source = messages.source AND c.chat_id = messages.chat_id)""")
    conn.create_function("foreign_place", 1, lambda n: int(is_foreign(n)), deterministic=True)
    foreign_msgs = "SELECT id FROM messages WHERE foreign_place(chat_name) = 1"
    skipped = conn.execute(f"UPDATE messages SET status = 'skipped', error = 'другой город' "
                           f"WHERE status = 'new' AND id IN ({foreign_msgs})").rowcount
    hidden = conn.execute(f"""UPDATE listings SET is_active = 0 WHERE is_active = 1 AND source = 'chat'
        AND EXISTS (SELECT 1 FROM listing_events e WHERE e.listing_id = listings.id AND e.message_id IN ({foreign_msgs}))
        AND NOT EXISTS (SELECT 1 FROM listing_events e JOIN messages m ON m.id = e.message_id
                        WHERE e.listing_id = listings.id AND foreign_place(m.chat_name) = 0)""").rowcount
    by_place = 0
    for r in conn.execute("SELECT id, settlement FROM listings WHERE is_active = 1 AND source = 'chat' "
                          "AND settlement IS NOT NULL").fetchall():
        if is_foreign(r["settlement"]):
            conn.execute("UPDATE listings SET is_active = 0 WHERE id = ?", (r["id"],))
            by_place += 1
    conn.commit()
    return {"skipped": skipped, "hidden": hidden + by_place}

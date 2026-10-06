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


def save_chat(conn: sqlite3.Connection, source: str, chat_id: str, name: str | None, link: str | None = None) -> None:
    if not chat_id or not name:
        return
    conn.execute("""INSERT INTO chats (source, chat_id, name, foreign_place, link) VALUES (?,?,?,?,?)
                    ON CONFLICT(source, chat_id) DO UPDATE SET name = excluded.name,
                    foreign_place = excluded.foreign_place, link = COALESCE(excluded.link, chats.link)""",
                 (source, chat_id, name, int(is_foreign(name)), link))


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


def _retry(fn, tries: int = 4):
    """База может быть на секунду занята фоновой работой — пробуем ещё раз, а не падаем с ошибкой."""
    for i in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or i == tries - 1:
                raise
            time.sleep(1.5)


def set_blocked(conn: sqlite3.Connection, source: str, chat_id: str, blocked: bool) -> dict:
    """Админ отключил/включил чат. Пересчитываются только объекты этого чата — быстро."""
    def work() -> dict:
        try:
            now = int(time.time())
            conn.execute("""INSERT INTO chats (source, chat_id, blocked, blocked_at) VALUES (?,?,?,?)
                            ON CONFLICT(source, chat_id) DO UPDATE SET blocked = excluded.blocked,
                            blocked_at = excluded.blocked_at, purged_at = NULL""",
                         (source, chat_id, int(blocked), now if blocked else None))
            bl = blocked_set(conn, fresh=True)
            # Объекты, которые хоть раз приходили из этого чата
            ids = [r[0] for r in conn.execute(
                """SELECT DISTINCT e.listing_id FROM listing_events e JOIN messages m ON m.id = e.message_id
                   WHERE m.source = ? AND m.chat_id = ?""", (source, chat_id))]
            changed = 0
            for lid in ids:
                srcs = conn.execute("""SELECT m.source, m.chat_id, m.chat_name FROM listing_events e
                                       JOIN messages m ON m.id = e.message_id WHERE e.listing_id = ?""", (lid,)).fetchall()
                only_bad = all(is_foreign(r[2]) or (r[0], r[1] or "") in bl for r in srcs)
                if blocked and only_bad:
                    changed += conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'chat' "
                                            "WHERE id = ? AND is_active = 1 AND source = 'chat'", (lid,)).rowcount
                elif not blocked and not only_bad:
                    changed += conn.execute(
                        "UPDATE listings SET is_active = 1, hidden_reason = NULL WHERE id = ? AND hidden_reason = 'chat' "
                        "AND last_seen >= ?", (lid, int(time.time()) - config.STALE_DAYS * 86400)).rowcount
            if blocked:
                conn.execute("UPDATE messages SET status = 'skipped', error = 'чат исключён' "
                             "WHERE source = ? AND chat_id = ? AND status = 'new'", (source, chat_id))
            else:
                conn.execute("UPDATE messages SET status = 'new', error = NULL WHERE source = ? AND chat_id = ? "
                             "AND status = 'skipped' AND error = 'чат исключён'", (source, chat_id))
            conn.commit()
            return {"hidden" if blocked else "returned": changed}
        except Exception:
            conn.rollback()
            raise
    return _retry(work)


def _link(source: str, chat_id: str, link: str | None) -> str | None:
    if link:
        return link
    if source == "tg" and chat_id and not chat_id.lstrip("-").isdigit():
        return f"https://t.me/{chat_id.lstrip('@')}"
    return None


def chats_for_admin(conn: sqlite3.Connection) -> list[dict]:
    """Все чаты: сколько сообщений, сколько объектов на сайте, когда было последнее, ссылка (если есть)."""
    _retry(lambda: (conn.execute("""INSERT OR IGNORE INTO chats (source, chat_id, name, foreign_place)
                    SELECT source, chat_id, MAX(chat_name), 0 FROM messages
                    WHERE chat_id IS NOT NULL AND chat_id != '' AND source IN ('wa', 'tg', 'max') GROUP BY 1, 2"""),
                    conn.commit()))
    stats = {(r[0], r[1]): (r[2], r[3], r[4]) for r in conn.execute(
        "SELECT source, chat_id, COUNT(*), MAX(ts), MAX(chat_name) FROM messages GROUP BY 1, 2")}
    objs = {(r[0], r[1]): r[2] for r in conn.execute(
        """SELECT m.source, m.chat_id, COUNT(DISTINCT e.listing_id) FROM listing_events e
           JOIN messages m ON m.id = e.message_id JOIN listings l ON l.id = e.listing_id AND l.is_active = 1
           GROUP BY 1, 2""")}
    out = []
    for r in conn.execute("SELECT * FROM chats").fetchall():
        k = (r["source"], r["chat_id"])
        n, last, msg_name = stats.get(k, (0, None, None))
        name = r["name"] or msg_name
        out.append({"source": r["source"], "chat_id": r["chat_id"], "name": name, "blocked": r["blocked"],
                    "purged": bool(r["purged_at"]),
                    "foreign": is_foreign(name), "messages": n, "last_ts": last, "listings": objs.get(k, 0),
                    "link": _link(r["source"], r["chat_id"], r["link"] if "link" in r.keys() else None)})
    out.sort(key=lambda c: (-c["listings"], -c["messages"], c["name"] or "я"))
    return out


def chat_preview(conn: sqlite3.Connection, source: str, chat_id: str) -> dict:
    """Последние сообщения и объекты чата — чтобы решить, нужен ли он, не заходя в мессенджер."""
    msgs = [dict(r) for r in conn.execute(
        """SELECT ts, sender_name, substr(text, 1, 600) AS text, status FROM messages
           WHERE source = ? AND chat_id = ? ORDER BY ts DESC LIMIT 8""", (source, chat_id))]
    objs = [dict(r) for r in conn.execute(
        """SELECT DISTINCT l.id, l.title, l.price, l.deal, l.is_active, l.complex, l.district, l.street FROM listing_events e
           JOIN messages m ON m.id = e.message_id JOIN listings l ON l.id = e.listing_id
           WHERE m.source = ? AND m.chat_id = ? ORDER BY l.last_seen DESC LIMIT 20""", (source, chat_id))]
    return {"messages": msgs, "listings": objs}


PURGE_AFTER_S = 600   # 10 минут на «Отменить», потом сообщения отключённого чата удаляются


def purge_blocked(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Чистка отключённых чатов (из фонового цикла).

    Удаляем сообщения отключённого чата, которые НЕ повторялись в других (включённых) чатах.
    Объекты, которые присылали и в другие чаты, остаются — их сообщения из других чатов не трогаем.
    Объекты, которые были ТОЛЬКО в отключённых чатах, удаляются вместе с историей."""
    now = int(now or time.time())
    bl = blocked_set(conn, fresh=True)
    done = {"chats": 0, "messages": 0, "listings": 0}
    for ch in conn.execute("""SELECT source, chat_id FROM chats WHERE blocked = 1 AND purged_at IS NULL
                              AND COALESCE(blocked_at, 0) < ?""", (now - PURGE_AFTER_S,)).fetchall():
        src, cid = ch["source"], ch["chat_id"]
        # Объекты, которые были только в отключённых чатах — удаляем совсем
        for (lid,) in conn.execute("""SELECT DISTINCT e.listing_id FROM listing_events e JOIN messages m ON m.id = e.message_id
                                       WHERE m.source = ? AND m.chat_id = ?""", (src, cid)).fetchall():
            srcs = conn.execute("""SELECT m.source, m.chat_id, m.chat_name FROM listing_events e
                                   JOIN messages m ON m.id = e.message_id WHERE e.listing_id = ?""", (lid,)).fetchall()
            row = conn.execute("SELECT source, owner_user_id FROM listings WHERE id = ?", (lid,)).fetchone()
            if row is None or row["source"] != "chat" or row["owner_user_id"]:
                continue   # фид партнёра и объекты, которыми управляет агент, не трогаем
            if all(is_foreign(r[2]) or (r[0], r[1] or "") in bl for r in srcs):
                conn.execute("DELETE FROM listings_fts WHERE rowid = ?", (lid,))
                conn.execute("DELETE FROM listing_events WHERE listing_id = ?", (lid,))
                conn.execute("DELETE FROM favorites WHERE listing_id = ?", (lid,))
                conn.execute("DELETE FROM listings WHERE id = ?", (lid,))
                done["listings"] += 1
        # Сообщения этого чата, текст которых не встречался в других включённых чатах
        ids = [r[0] for r in conn.execute(
            """SELECT m.id FROM messages m WHERE m.source = ? AND m.chat_id = ?
               AND NOT EXISTS (SELECT 1 FROM messages o WHERE o.text_hash = m.text_hash AND o.id != m.id
                               AND NOT (o.source = m.source AND o.chat_id = m.chat_id))""", (src, cid))]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            q = ",".join("?" * len(part))
            conn.execute(f"UPDATE listing_events SET message_id = NULL WHERE message_id IN ({q})", part)
            conn.execute(f"DELETE FROM messages WHERE id IN ({q})", part)
        done["messages"] += len(ids)
        conn.execute("UPDATE chats SET purged_at = ? WHERE source = ? AND chat_id = ?", (now, src, cid))
        done["chats"] += 1
    conn.commit()
    return done

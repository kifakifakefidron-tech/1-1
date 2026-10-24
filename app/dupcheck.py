"""Проверка защиты от повторов на живой базе (только чтение): `docker compose exec web python -m app.dupcheck`.

Считает: сколько раз один и тот же текст объекта (отпечаток куска сообщения) оказался в РАЗНЫХ карточках, и сколько
повторов сообщений распознано как повтор (без нового разбора). Примеры — номера карточек, чтобы открыть /?open=ID."""
from __future__ import annotations

from . import db


def report(conn) -> dict:
    dup = conn.execute("""SELECT e.fragment_hash, GROUP_CONCAT(DISTINCT e.listing_id) AS ids, COUNT(DISTINCT e.listing_id) AS n
                          FROM listing_events e JOIN listings l ON l.id = e.listing_id
                          WHERE e.fragment_hash IS NOT NULL GROUP BY e.fragment_hash HAVING n > 1 ORDER BY n DESC LIMIT 20""").fetchall()
    reposts = conn.execute("SELECT COUNT(*) FROM listing_events WHERE match = 'repost'").fetchone()[0]
    msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'done'").fetchone()[0]
    uniq = conn.execute("SELECT COUNT(DISTINCT text_hash) FROM messages WHERE status = 'done'").fetchone()[0]
    return {"same_text_in_several_cards": len(dup), "examples": [dict(r) for r in dup],
            "messages_done": msgs, "unique_texts": uniq, "reposts_recognized": reposts}


if __name__ == "__main__":
    r = report(db.get())
    print(f"Разобрано сообщений: {r['messages_done']}, разных текстов среди них: {r['unique_texts']}")
    print(f"Повторов распознано (без нового объекта): {r['reposts_recognized']}")
    print(f"Один и тот же текст объекта в разных карточках: {r['same_text_in_several_cards']}")
    for e in r["examples"]:
        print(f"  карточки {e['ids']}")

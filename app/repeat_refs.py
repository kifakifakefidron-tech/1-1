"""Сохранённые ссылки при объединении версий одного объявления."""
from __future__ import annotations

import json
import sqlite3
import time

from . import clients, db, offers, ownmatch

_REPAIR_KEY = "dup_repeat_saved_refs_v1"


def _notes(kept, archived):
    return "\n\n".join(dict.fromkeys(text for text in (kept, archived) if text))


def _merge_collection(conn, kept, archived):
    priced = kept if kept["price"] or kept["hide_price"] else archived
    mapped = kept if kept["map_off"] or kept["map_lat"] is not None else archived
    photos = list(dict.fromkeys(offers._tokens(kept["photos"]) + offers._tokens(archived["photos"])))
    order = json.loads(kept["photo_order"] or "{}")
    other = json.loads(archived["photo_order"] or "{}")
    order = {field: list(dict.fromkeys((order.get(field) or []) + (other.get(field) or [])))
             for field in ("order", "hidden")}
    conn.execute("""UPDATE collection_items SET note=?,text=?,price=?,hide_price=?,photos=?,photo_order=?,
                    map_lat=?,map_lon=?,map_off=? WHERE collection_id=? AND listing_id=?""",
                 (_notes(kept["note"], archived["note"]), kept["text"] or archived["text"], priced["price"],
                  priced["hide_price"], json.dumps(photos), json.dumps(order), mapped["map_lat"], mapped["map_lon"],
                  mapped["map_off"], kept["collection_id"], kept["listing_id"]))


def _merge_client(conn, kept, archived):
    def updated(row):
        return row["updated"] or row["ts"]

    by_time = sorted((kept, archived), key=updated, reverse=True)
    stage = max(by_time, key=lambda r: clients.RANK.get(clients.OLD_STATUS.get(r["status"], r["status"]), -1))
    reaction = conn.execute("""SELECT r.kind,r.text FROM pick_reactions r
        JOIN collections c ON c.id=r.collection_id WHERE c.client_id=? AND r.listing_id IN (?,?)
        AND r.kind!='question' ORDER BY r.ts DESC,r.id DESC LIMIT 1""",
        (kept["client_id"], kept["listing_id"], archived["listing_id"])).fetchone()
    if reaction is None:
        reaction = next(((r["reaction"], r["reaction_text"]) for r in by_time if r["reaction"]), (None, None))
    status = stage["status"]
    reply_status = clients.REACTION_STATUS.get(reaction[0])
    if reply_status and clients.RANK.get(status, -1) <= clients.RANK[reply_status]:
        status = reply_status
    conn.execute("""UPDATE client_items SET status=?,note=?,ts=?,updated=?,pick_id=?,sent_at=?,opened_at=?,
                    reaction=?,reaction_text=?,question=? WHERE client_id=? AND listing_id=?""",
                 (status, _notes(kept["note"], archived["note"]), min(kept["ts"], archived["ts"]), updated(by_time[0]),
                  kept["pick_id"] or archived["pick_id"], kept["sent_at"] or archived["sent_at"],
                  kept["opened_at"] or archived["opened_at"], *reaction,
                  next((r["question"] for r in by_time if r["question"]), None), kept["client_id"], kept["listing_id"]))


def move(conn: sqlite3.Connection, keep: int, drop: int) -> None:
    """Оставляет одну запись на пользователя, клиента или подборку и переносит журнал ответов."""
    for table in ownmatch._REFS:
        conn.execute(f"UPDATE OR IGNORE {table} SET listing_id=? WHERE listing_id=?", (keep, drop))

    # После UPDATE OR IGNORE на старой карточке остались записи, где обе версии уже были сохранены.
    conn.execute("""UPDATE favorites AS kept SET created=MIN(kept.created,archived.created),
                    price_at=COALESCE(kept.price_at,archived.price_at)
                    FROM favorites archived WHERE kept.listing_id=? AND archived.listing_id=?
                    AND kept.user_id=archived.user_id""", (keep, drop))
    conn.execute("""UPDATE notes AS kept SET text=CASE
                      WHEN kept.text='' THEN archived.text
                      WHEN archived.text='' OR kept.text=archived.text THEN kept.text
                      ELSE kept.text || char(10,10) || archived.text END,
                    status=COALESCE(kept.status,archived.status),ts=MAX(kept.ts,archived.ts)
                    FROM notes archived WHERE kept.listing_id=? AND archived.listing_id=?
                    AND kept.user_id=archived.user_id""", (keep, drop))
    for table, key, combine in (("collection_items", "collection_id", _merge_collection),
                                ("client_items", "client_id", _merge_client)):
        archived = conn.execute(f"SELECT * FROM {table} WHERE listing_id=?", (drop,)).fetchall()
        for row in archived:
            kept = conn.execute(f"SELECT * FROM {table} WHERE {key}=? AND listing_id=?", (row[key], keep)).fetchone()
            combine(conn, kept, row)

    conn.execute("""UPDATE collection_items SET photo_order=replace(photo_order,?,?)
                    WHERE listing_id=? AND instr(photo_order,?)>0""",
                 (f'"/photos/{drop}/', f'"/photos/{keep}/', keep, f'"/photos/{drop}/'))
    conn.execute("UPDATE pick_reactions SET listing_id=? WHERE listing_id=?", (keep, drop))
    for table in ("favorites", "notes", "hidden_listings", "client_items", "client_hidden", "collection_items"):
        conn.execute(f"DELETE FROM {table} WHERE listing_id=?", (drop,))


def repair(conn: sqlite3.Connection) -> int:
    """Доводит перенос до конца для уже объединённых версий, включая цепочки."""
    if db.get_state(conn, _REPAIR_KEY):
        return 0
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if db.get_state(conn, _REPAIR_KEY):
            return 0
        ids = " UNION ".join(f"SELECT listing_id FROM {table}" for table in (*ownmatch._REFS, "pick_reactions"))
        archived = conn.execute(f"""SELECT l.id,l.hidden_reason,l.merged_into
            FROM ({ids}) refs JOIN listings l ON l.id=refs.listing_id
            WHERE l.hidden_reason=? AND l.merged_into IS NOT NULL""", (offers.MERGED_REPEAT,)).fetchall()
        for row in archived:
            kept = offers.merged_listing(conn, row)
            if kept["id"] != row["id"] and kept["hidden_reason"] != offers.MERGED_REPEAT:
                move(conn, kept["id"], row["id"])
        if archived:
            conn.execute("""INSERT INTO state(key,value) VALUES ('cache_ver',?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(time.time()),))
        conn.execute("INSERT INTO state(key,value) VALUES (?,?)", (_REPAIR_KEY, str(len(archived))))
    return len(archived)

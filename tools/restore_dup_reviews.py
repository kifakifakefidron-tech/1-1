"""Возвращает недавние автоматические решения DeepSeek в очередь ручного разбора."""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from contextlib import closing

from app import config, db, dupreview


def _restore_pair(conn, original):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT * FROM dup_pairs WHERE id=?", (original["id"],)).fetchone()
    if row is None or tuple(row) != tuple(original):
        return "changed", None
    restored = dupreview.decide(conn, row["id"], "reopen")
    if not restored["ok"]:
        return "blocked", restored.get("error") or "Пара уже изменилась."
    conn.execute("UPDATE dup_pairs SET ai=NULL,returned_decision_at=? WHERE id=?", (row["decided_at"], row["id"]))
    dupreview.record_review_action(conn, row["id"], "returned", row["status"], dict(row), keep_id=row["keep_id"])
    conn.execute("""INSERT INTO state(key,value) VALUES ('cache_ver',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(time.time()),))
    return "returned", None


def restore(conn, hours, *, until=None):
    until = int(time.time()) if until is None else until
    since = until - hours * 3600
    db.flush(conn)
    rows = dupreview._retry_database(lambda: conn.execute(f"""SELECT * FROM dup_pairs
        WHERE decided_at BETWEEN ? AND ? AND status IN {dupreview._RECENT_REVIEW_STATUSES}
          AND COALESCE(user_id,0)=0 AND kind NOT IN ('auto_price_revision','auto_exact_repeat')
          AND CASE WHEN json_valid(ai) THEN json_extract(ai,'$.verdict') IN {dupreview._AI_VERDICTS} END
        ORDER BY decided_at DESC,id DESC""", (since, until)).fetchall(), wait_for_db=True)
    print(f"Решений DeepSeek за {hours} часов к возврату {len(rows)}.", flush=True)
    pending = rows
    returned = Counter()
    changed = 0
    blocked = {}
    while pending:
        deferred = []
        for row in pending:
            pid = row["id"]
            outcome, reason = dupreview._write_review(conn, lambda: _restore_pair(conn, row), wait_for_db=True)
            if outcome == "returned":
                returned[row["status"]] += 1
                blocked.pop(pid, None)
                count = returned.total()
                if count % 100 == 0:
                    print(f"В очередь возвращено {count} из {len(rows)}.", flush=True)
            elif outcome == "changed":
                changed += 1
                blocked.pop(pid, None)
            else:
                deferred.append(row)
                blocked[pid] = reason
        if len(deferred) == len(pending):
            break
        pending = deferred
    return {"hours": hours, "since_ts": since, "until_ts": until, "selected": len(rows),
            "returned": dict(returned), "changed": changed, "blocked": blocked}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--hours", type=int, default=72)
    cli.add_argument("--until", type=int, help="конец окна в Unix-секундах для повторного запуска")
    cli.add_argument("--repair-exact", action="store_true", help="объединить точно подтверждённые версии объявления")
    args = cli.parse_args()
    if args.hours < 1:
        cli.error("--hours должен быть больше нуля")
    if config.DUP_AI_AUTO:
        cli.exit(1, "Для ручной очереди установите DUP_AI_AUTO=0 для web и worker.\n")
    with closing(dupreview._retry_database(db.connect, wait_for_db=True)) as conn:
        restored = restore(conn, args.hours, until=args.until)
        print(json.dumps(restored, ensure_ascii=False, indent=2), flush=True)
        if args.repair_exact:
            result = dupreview._retry_database(
                lambda: dupreview.scan_price_revisions(conn, apply=True, since=restored["since_ts"]), wait_for_db=True)
            print(f"Подтверждённых версий объединено {result['merged']}. В очередь добавлено или обновлено {result['marked']}.",
                  flush=True)


if __name__ == "__main__":
    main()

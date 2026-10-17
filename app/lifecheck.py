"""Жизнь объектов — отчёт (17.10, Артём: «проверь, как у нас удаляются объекты, нужны конкретные рамки»).

Рамки:
  * на сайте — пока объект присылают в чаты; без повтора STALE_DAYS (15) дней от последнего сообщения → в архив,
    но не раньше, чем через STALE_DAYS от появления на 1+1 (added_at);
  * в архиве — ARCHIVE_DAYS (25): повтор в чатах сразу возвращает на сайт;
  * удаляем только пролежавшее в архиве ARCHIVE_DAYS (и не моложе STALE_DAYS + ARCHIVE_DAYS с последнего сообщения);
  * удалённых админом и «всё от номера» не стираем никогда — по ним узнаём тот же объект в новых сообщениях.
Другие причины снятия — не срок: продано агентом, «под задатком», чат отключён/не Краснодар, «сдам» без коммуналки,
копия объекта СТРЕЛ, нет в фиде.

Запуск на сервере: docker compose exec web python -m app.lifecheck
"""
from __future__ import annotations

import json
import sqlite3
import time

from . import config, db

REASONS = {"stale": "не присылали в чаты", "sold": "агент отметил «продано»", "deposit": "под задатком",
           "chat": "чат отключён / не Краснодар", "feed": "нет в фиде СТРЕЛ", "expired": "свой объект: срок вышел",
           "other": "другое (копия объекта СТРЕЛ, «сдам» без коммуналки, правка админа)"}


def report(conn: sqlite3.Connection, now: int | None = None) -> dict:
    now = int(now or time.time())
    day = 86400
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    age = "(? - MAX(last_seen, COALESCE(added_at, 0))) / 86400"
    buckets = {k: one(f"SELECT COUNT(*) FROM listings WHERE is_active = 1 AND source = 'chat' AND {age} {cond}", now)
               for k, cond in (("0-3", "BETWEEN 0 AND 3"), ("4-7", "BETWEEN 4 AND 7"), ("8-14", "BETWEEN 8 AND 14"),
                               ("15+", ">= 15"))}
    reasons = {r[0] or "other": r[1] for r in conn.execute(
        "SELECT removed_reason, COUNT(*) FROM listings WHERE is_active = 0 GROUP BY removed_reason")}
    # «раньше срока»: сняли как устаревший, хотя с последнего сообщения / появления на 1+1 не прошло STALE_DAYS
    early = one(f"""SELECT COUNT(*) FROM listings WHERE is_active = 0 AND removed_reason = 'stale' AND removed_at IS NOT NULL
                    AND removed_at - MAX(last_seen, COALESCE(added_at, 0)) < ?""", config.STALE_DAYS * day - 3600)
    soon = one("""SELECT COUNT(*) FROM listings WHERE is_active = 0 AND removed_at IS NOT NULL AND removed_at < ?
                  AND COALESCE(hidden_reason, '') NOT IN ('admin', 'phone')""", now - (config.ARCHIVE_DAYS - 7) * day)
    life = json.loads(db.get_state(conn, "life_v1") or "{}")
    return {
        "rules": {"stale_days": config.STALE_DAYS, "archive_days": config.ARCHIVE_DAYS, "keep_days": config.KEEP_DAYS},
        "active": one("SELECT COUNT(*) FROM listings WHERE is_active = 1"),
        "active_by_age": buckets,
        "archive": one("SELECT COUNT(*) FROM listings WHERE is_active = 0"),
        "archive_by_reason": [{"reason": k, "label": REASONS.get(k, k), "n": v} for k, v in sorted(reasons.items(), key=lambda x: -x[1])],
        "archived_early": early,
        "delete_within_7d": soon,
        "deleted_total": int(db.get_state(conn, "deleted_listings") or 0),
        "restored_once": life.get("restored"),
    }


def main() -> None:
    conn = db.connect(config.DB_PATH)
    r = report(conn)
    print(f"Правила: на сайте {r['rules']['stale_days']} дн. без повтора → в архиве {r['rules']['archive_days']} дн. → удаление")
    print(f"На сайте: {r['active']}  (дней без повтора: {r['active_by_age']})")
    print(f"В архиве: {r['archive']}")
    for x in r["archive_by_reason"]:
        print(f"   {x['n']:>6}  {x['label']}")
    print(f"Снято «раньше срока» (должно быть 0): {r['archived_early']}")
    print(f"Удалится в ближайшие 7 дней: {r['delete_within_7d']}; удалено всего: {r['deleted_total']}")
    if r["restored_once"] is not None:
        print(f"После обновления вернули на сайт (снимали по дате старого сообщения): {r['restored_once']}")


if __name__ == "__main__":
    main()

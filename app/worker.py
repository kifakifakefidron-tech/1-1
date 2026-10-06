"""Фоновый цикл: Wappi → разбор → склейка → координаты → архив устаревших.

Запуск: python -m app.worker
"""
from __future__ import annotations

import logging
import time

from . import config, db, feed, geocode, ingest, llm, notify, wappi

log = logging.getLogger("worker")


def tick(conn) -> dict:
    stats = {"new_messages": wappi.poll_all(conn) if config.WAPPI_ENABLED else "пауза"}
    llm.failures = 0
    stats["processed"] = ingest.process_pending(conn, limit=300, budget_s=60)
    stats["queue"] = conn.execute("SELECT COUNT(*) FROM messages WHERE status='new'").fetchone()[0]
    stats["geocoded"] = geocode.run(conn, limit=40)
    stats["archived"] = ingest.archive_stale(conn)
    synced = feed.maybe_sync(conn)
    if synced:
        stats["feed"] = synced
    if db.get_state(conn, "cleanup_day") != time.strftime("%Y-%m-%d"):
        stats["cleanup"] = ingest.cleanup_old(conn)
        db.set_state(conn, "cleanup_day", time.strftime("%Y-%m-%d"))
    notify.check_health(conn, dict(wappi.errors), llm.failures)
    notify.daily_summary(conn)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    if not config.WAPPI_ENABLED:
        log.warning("Сбор из Wappi на паузе (WAPPI_ENABLED=false) — разбираю только уже скачанное")
    elif not config.WAPPI_PROFILES or not any(config.WAPPI_TOKENS.values()):
        log.warning("WAPPI_TOKEN / WAPPI_PROFILES не заданы — новые сообщения забираться не будут")
    if not llm.available():
        log.warning("DEEPSEEK_API_KEY не задан — разбор только правилами (хуже для сложных постов)")
    while True:
        started = time.time()
        stats: dict = {}
        try:
            stats = tick(conn)
            log.info("цикл: %s", stats)
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка цикла")
            try:
                notify.alert(conn, "worker", f"Ошибка в фоновой работе сайта: {e!r}"[:500], every_s=3600)
            except Exception:  # noqa: BLE001
                pass
        # Пока есть очередь неразобранных — почти без паузы; иначе ждём до следующего опроса
        pause = 5 if stats.get("queue") else config.WAPPI_POLL_SECONDS - (time.time() - started)
        time.sleep(max(5, pause))


if __name__ == "__main__":
    main()

"""Фоновый цикл: Wappi → разбор → склейка → координаты → архив устаревших.

Запуск: python -m app.worker
"""
from __future__ import annotations

import logging
import time

from . import config, db, geocode, ingest, llm, wappi

log = logging.getLogger("worker")


def tick(conn) -> dict:
    stats = {"new_messages": wappi.poll_all(conn)}
    stats["processed"] = ingest.process_pending(conn, limit=300, budget_s=60)
    stats["queue"] = conn.execute("SELECT COUNT(*) FROM messages WHERE status='new'").fetchone()[0]
    stats["geocoded"] = geocode.run(conn, limit=40)
    stats["archived"] = ingest.archive_stale(conn)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    conn = db.connect()
    if not config.WAPPI_PROFILES or not any(config.WAPPI_TOKENS.values()):
        log.warning("WAPPI_TOKEN / WAPPI_PROFILES не заданы — новые сообщения забираться не будут")
    if not llm.available():
        log.warning("DEEPSEEK_API_KEY не задан — разбор только правилами (хуже для сложных постов)")
    while True:
        started = time.time()
        stats: dict = {}
        try:
            stats = tick(conn)
            log.info("цикл: %s", stats)
        except Exception:  # noqa: BLE001
            log.exception("ошибка цикла")
        # Пока есть очередь неразобранных — почти без паузы; иначе ждём до следующего опроса
        pause = 5 if stats.get("queue") else config.WAPPI_POLL_SECONDS - (time.time() - started)
        time.sleep(max(5, pause))


if __name__ == "__main__":
    main()

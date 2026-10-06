"""Фоновый цикл: Wappi → разбор → склейка → координаты → архив устаревших.

Запуск: python -m app.worker
"""
from __future__ import annotations

import logging
import time

from . import agent, config, db, feed, geocode, hooks, ingest, llm, mailer, notify, region, wappi

log = logging.getLogger("worker")


def tick(conn) -> dict:
    if config.WAPPI_ENABLED and wappi.refresh_chat_names(conn) is not None:
        stats_r = region.hide_foreign(conn)   # чаты Сочи/Адлера/… — не показываем
        log.info("другие города: %s", stats_r)
    stats = {"new_messages": wappi.poll_all(conn) if config.WAPPI_ENABLED else "пауза"}
    llm.failures = 0
    stats["processed"] = ingest.process_pending(conn, limit=300, budget_s=60)
    stats["queue"] = conn.execute("SELECT COUNT(*) FROM messages WHERE status='new'").fetchone()[0]
    stats["geocoded"] = geocode.run(conn, limit=40)
    stats["archived"] = ingest.archive_stale(conn)
    own = agent.expire_own(conn, mailer.send_text if mailer.available() else None)
    if any(own.values()):
        stats["own"] = own
    if mailer.available():
        sent = hooks.run(conn, mailer.send_text)   # подписки на поиск, снижение цены в избранном
        if sent and any(sent.values()):
            stats["letters"] = sent
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
    # Один раз после обновления: исправить в базе то, что раньше терялось при фильтрах (см. app/audit.py)
    if db.get_state(conn, "audit_fix") != "3":
        try:
            from . import audit
            audit.run(fix=True, conn=conn)
            db.set_state(conn, "audit_fix", "3")
        except Exception:  # noqa: BLE001
            log.exception("исправление базы не удалось")
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

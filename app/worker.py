"""Фоновый цикл: Wappi → разбор → склейка → координаты → архив устаревших.

Запуск: python -m app.worker
"""
from __future__ import annotations

import logging
import time

from . import agent, config, db, feed, geocode, hooks, ingest, llm, mailer, notify, region, wappi

log = logging.getLogger("worker")


def _mail(conn):
    """Отправка письма: сначала закончить запись в базу (SMTP может думать секунды)."""
    if not mailer.available():
        return None

    def send(*a, **kw):
        db.flush(conn)
        return mailer.send_text(*a, **kw)
    return send


def tick(conn) -> dict:
    if config.WAPPI_ENABLED and wappi.refresh_chat_names(conn) is not None:
        stats_r = region.hide_foreign(conn)   # чаты Сочи/Адлера/… — не показываем
        log.info("другие города: %s", stats_r)
    stats = {"new_messages": wappi.poll_all(conn) if config.WAPPI_ENABLED else "пауза"}
    purged = region.purge_blocked(conn)   # отключённые чаты: удалить их сообщения и объекты только оттуда
    if purged["chats"]:
        stats["purged"] = purged
    llm.failures = 0
    stats["processed"] = ingest.process_pending(conn, limit=300, budget_s=60)
    stats["queue"] = conn.execute("SELECT COUNT(*) FROM messages WHERE status='new'").fetchone()[0]
    stats["geocoded"] = geocode.run(conn, limit=40)
    stats["archived"] = ingest.archive_stale(conn)
    own = agent.expire_own(conn, _mail(conn))
    if any(own.values()):
        stats["own"] = own
    from . import planner
    reminded = planner.remind(conn, _mail(conn))   # показы и созвоны: напомнить заранее
    if reminded:
        stats["reminders"] = reminded
    # подписки на поиск, избранное (цена, снят с сайта), окончание доступа — на сайте и письмом
    sent = hooks.run(conn, _mail(conn))
    if sent and any(sent.values()):
        stats["notices"] = sent
    synced = feed.maybe_sync(conn)
    if synced:
        stats["feed"] = synced
    if db.get_state(conn, "cleanup_day") != time.strftime("%Y-%m-%d"):
        stats["cleanup"] = ingest.cleanup_old(conn)
        db.set_state(conn, "cleanup_day", time.strftime("%Y-%m-%d"))
    notify.check_health(conn, dict(wappi.errors), llm.failures)
    notify.daily_summary(conn)
    try:
        from . import market
        if market.snapshot(conn):   # раз в сутки — медианы рынка для динамики
            stats["market_snapshot"] = True
    except Exception:  # noqa: BLE001
        log.exception("снимок рынка не записался")
    db.set_state(conn, "worker_tick", str(int(time.time())))   # для админки: фоновая работа жива
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
    if db.get_state(conn, "audit_fix") != "4":
        try:
            from . import audit
            audit.run(fix=True, conn=conn)
            db.set_state(conn, "audit_fix", "4")
        except Exception:  # noqa: BLE001
            log.exception("исправление базы не удалось")
    while True:
        started = time.time()
        stats: dict = {}
        try:
            stats = tick(conn)
            db.flush(conn)
            log.info("цикл: %s", stats)
        except Exception as e:  # noqa: BLE001
            log.exception("ошибка цикла")
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            try:
                notify.alert(conn, "worker", f"Ошибка в фоновой работе сайта: {e!r}"[:500], every_s=3600)
            except Exception:  # noqa: BLE001
                pass
        # Пока есть очередь неразобранных — почти без паузы; иначе ждём до следующего опроса
        pause = 5 if stats.get("queue") else config.WAPPI_POLL_SECONDS - (time.time() - started)
        time.sleep(max(5, pause))


if __name__ == "__main__":
    main()

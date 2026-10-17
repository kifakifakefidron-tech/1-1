"""Фоновый цикл: Wappi → разбор → склейка → координаты → архив устаревших.

Запуск: python -m app.worker
"""
from __future__ import annotations

import logging
import json
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
    if not config.WAPPI_ENABLED and wappi.poll_verify_only(conn):   # пауза, но агенты ждут подтверждения номера
        stats["verify_check"] = True
    purged = region.purge_blocked(conn)   # отключённые чаты: удалить их сообщения и объекты только оттуда
    if purged["chats"]:
        stats["purged"] = purged
    llm.failures = 0
    stats["processed"] = ingest.process_pending(conn, limit=300, budget_s=60)
    from . import media
    if media.trim_chat_photos(conn):   # один раз: у объявлений из чатов — по одному фото
        stats["photos_trimmed"] = True
    if config.WAPPI_ENABLED:
        got = media.attach(conn)   # фото из чатов — по одному в карточку объекта
        if got["photos"]:
            stats["photos"] = got["photos"]
    stats["queue"] = conn.execute("SELECT COUNT(*) FROM messages WHERE status='new'").fetchone()[0]
    from . import autolearn, osmaddr
    if osmaddr.maybe_refresh(conn):   # справочник адресов OSM — раз в месяц (≈20 с)
        stats["osm_addr"] = osmaddr.loaded(conn)
    learned = autolearn.run(conn)   # точки и районы из уже известного (фид, ЖК, контуры районов, соседние объявления)
    if learned.get("points") or learned.get("districts") or learned.get("recheck"):
        stats["autolearn"] = learned
    stats["geocoded"] = geocode.run(conn, limit=40)
    stats["archived"] = ingest.archive_stale(conn)
    own = agent.expire_own(conn, _mail(conn))
    if any(own.values()):
        stats["own"] = own
    from . import planner
    reminded = planner.remind(conn, _mail(conn))   # показы и созвоны: напомнить заранее
    if reminded:
        stats["reminders"] = reminded
    # «Новое под клиента» — раз в 15 минут
    if time.time() - float(db.get_state(conn, "client_match_at") or 0) > 900:
        db.set_state(conn, "client_match_at", str(int(time.time())))
        from . import clients
        n = clients.new_matches(conn)
        if n:
            stats["client_matches"] = n
    # подписки на поиск, избранное (цена, снят с сайта), окончание доступа — на сайте и письмом
    sent = hooks.run(conn, _mail(conn))
    if sent and any(sent.values()):
        stats["notices"] = sent
    try:
        from . import widgets
        widgets.refresh(conn)   # погода, ставка ЦБ, курсы, ипотека — заранее, чтобы кабинет открывался мгновенно
    except Exception:  # noqa: BLE001
        log.exception("виджеты кабинета не обновились")
    try:
        from . import bankrates
        if bankrates.remind(conn):   # ставки банков устарели — напомнить админу
            notify.alert(conn, "banks", "Ставки банков в кабинете устарели — вставьте свежий обзор ставок в «Ставки банков».",
                         every_s=6 * 86400)
    except Exception:  # noqa: BLE001
        log.exception("напоминание о ставках не отправилось")
    synced = feed.maybe_sync(conn)
    if synced:
        stats["feed"] = synced
    if not db.get_state(conn, "deposit_v1"):   # один раз (акт 14.10): «под задатком» — с сайта сразу
        db.set_state(conn, "deposit_v1", str(ingest.hide_deposits(conn)))
    from . import pricefix
    pf = pricefix.maybe_run(conn)   # один раз (акт 14.10, Б1–Б2): цены в тысячах и перебитые склейкой — отчёт в «Обзоре»
    if pf:
        stats["price_fix"] = pf["scale"] + pf["text"]
    from . import geoaudit
    try:
        gd = geoaudit.daily(conn)   # раз в сутки: явно ошибочные правила, ЖК-мусор, одно написание ЖК
        if gd:
            stats["geo_daily"] = gd.get("rules", 0)
        from . import autolearn as _al
        stats["unstacked"] = _al.maybe_unstack(conn) or None
    except Exception:  # noqa: BLE001
        log.exception("ежедневная уборка гео не прошла")
    gc = geoaudit.auto_cleanup(conn)   # один раз (акт 14.10): уборка правил и справочников, отчёт — в «Обзоре»
    if gc:
        stats["geo_cleanup"] = gc.get("listings", 0)
    from . import cxnorm
    cx = cxnorm.maybe_run(conn)   # один раз (ТЗ v2, P1.7): одно название на один ЖК, отчёт — в админке
    if cx:
        stats["cx_norm"] = cx["listings"]
    if not db.get_state(conn, "p0_phones_v1"):   # один раз (ТЗ v2, P0): вырезать номера из описаний, отчёт — в админке
        from . import p0fix
        ph = p0fix.phones_report(conn, apply=True, examples=10)
        pr = p0fix.prices_report(conn, examples=40)
        db.set_state(conn, "p0_report", json.dumps({"ts": int(time.time()), "phones": ph["with_phone_in_text"],
                                                    "phone_examples": ph["examples"], "prices": pr["mismatch"],
                                                    "price_examples": pr["examples"]}, ensure_ascii=False))
        db.set_state(conn, "p0_phones_v1", "1")
        stats["p0_phones"] = ph["with_phone_in_text"]
    if not db.get_state(conn, "rent_rule_v1"):   # один раз: новое правило аренды («+ком платежи»)
        stats["rent_rule"] = ingest.fix_rent_rule(conn)
        db.set_state(conn, "rent_rule_v1", "1")
    if not db.get_state(conn, "buyers_v2"):   # «запросить по номеру» — не запрос: удалить и разобрать заново
        from . import buyers
        stats["buyers_recheck"] = buyers.recheck(conn)
        db.set_state(conn, "buyers_v2", "1")
    if not db.get_state(conn, "buyers_v1"):   # один раз: запросы из уже разобранных сообщений
        from . import buyers
        stats["buyers_backfill"] = buyers.backfill(conn)
        db.set_state(conn, "buyers_v1", "1")
    if db.get_state(conn, "cleanup_day") != time.strftime("%Y-%m-%d"):
        from . import buyers
        stats["buyers_archived"] = buyers.archive(conn)
        stats["cleanup"] = ingest.cleanup_old(conn)
        db.set_state(conn, "cleanup_day", time.strftime("%Y-%m-%d"))
    notify.check_health(conn, dict(wappi.errors), llm.failures)
    notify.daily_summary(conn)
    try:
        from . import geoquality
        if geoquality.snapshot(conn):   # раз в сутки — качество гео-данных (динамика на /learn)
            stats["geo_quality"] = True
        if geoquality.weekly(conn):     # раз в неделю — отчёт админу: что сервис поменял сам, очереди, правила
            stats["geo_weekly"] = True
    except Exception:  # noqa: BLE001
        log.exception("качество гео-данных не посчитано")
    try:
        from . import market
        if market.snapshot(conn):   # раз в сутки — медианы рынка для динамики
            stats["market_snapshot"] = True
    except Exception:  # noqa: BLE001
        log.exception("снимок рынка не записался")
    try:
        from . import backup
        name = backup.maybe_backup(conn)   # раз в сутки ночью — копия базы в data/backups
        if name:
            stats["backup"] = name
    except Exception as e:  # noqa: BLE001
        log.exception("резервная копия не сделана")
        notify.alert(conn, "backup", f"Резервная копия базы не сделана: {e!r}"[:400], every_s=86400)
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

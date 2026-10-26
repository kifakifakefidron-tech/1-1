"""Уведомления в Telegram о сбоях и короткая сводка раз в день.

Бот создаётся в @BotFather, токен — в .env (TELEGRAM_BOT_TOKEN). Получатель
(TELEGRAM_ADMIN, ник без @) пишет боту /start — сервис бота (app/bot.py) запоминает
его чат, а отсюда туда шлются тревоги и сводка.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config, db, mailer, tg

log = logging.getLogger(__name__)



def _api(method: str, payload: dict) -> dict | None:
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    try:
        return tg.call(method, payload, timeout=20)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        log.warning("Telegram %s: %s", method, e)
        return None


def _chat_id(conn: sqlite3.Connection) -> str | None:
    return db.get_state(conn, "tg_admin_chat")


def send(conn: sqlite3.Connection, text: str) -> bool:
    """Сначала Telegram; если он недоступен или чат не известен — письмо на почту владельца."""
    db.flush(conn)
    chat = _chat_id(conn)
    if chat and _api("sendMessage", {"chat_id": chat, "text": text, "disable_web_page_preview": True}):
        return True
    to = config.ALERT_EMAIL or next(iter(sorted(config.ADMIN_EMAILS)), "")
    if to and mailer.available():
        return mailer.send_text(to, "1+1: " + text.splitlines()[0][:80], text, force=True)   # сбои — всегда
    return False


def alert(conn: sqlite3.Connection, key: str, text: str, every_s: int = 3 * 3600) -> None:
    """Тревога не чаще раза в every_s по одной причине (key)."""
    now = int(time.time())
    last = int(db.get_state(conn, f"alert:{key}") or 0)
    if now - last < every_s:
        return
    if send(conn, "⚠️ " + text):
        db.set_state(conn, f"alert:{key}", str(now))


def resolve(conn: sqlite3.Connection, key: str, text: str) -> None:
    """Сообщить, что проблема ушла (если о ней тревожили)."""
    if db.get_state(conn, f"alert:{key}") not in (None, "0"):
        db.set_state(conn, f"alert:{key}", "0")
        send(conn, "✅ " + text)


def daily_summary(conn: sqlite3.Connection) -> None:
    """Сводка раз в сутки, около 9:00 по Москве."""
    now = int(time.time())
    msk_hour = time.gmtime(now + 3 * 3600).tm_hour
    today = time.strftime("%Y-%m-%d", time.gmtime(now + 3 * 3600))
    if msk_hour < 9 or db.get_state(conn, "tg_summary_day") == today:
        return
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    day = now - 86400
    text = (f"☀️ Сводка 1+1 за сутки\n"
            f"Объектов на сайте: {one('SELECT COUNT(*) FROM listings WHERE is_active=1')}\n"
            f"Новых объектов: {one('SELECT COUNT(*) FROM listings WHERE first_seen >= ?', day)}\n"
            f"Сообщений из чатов: {one('SELECT COUNT(*) FROM messages WHERE ts >= ?', day)}\n"
            f"Ждут разбора: {one('SELECT COUNT(*) FROM messages WHERE status=?', 'new')}\n"
            f"Сбор из Wappi: {'включён' if config.WAPPI_ENABLED else 'на паузе'}")
    if send(conn, text):
        db.set_state(conn, "tg_summary_day", today)


WAPPI_GRACE_S = 15 * 60


def wappi_hint(err: str) -> str:
    """Что значит ошибка Wappi — простыми словами."""
    e = (err or "").lower()
    if "driver not ready" in e or "not authorized" in e or "qr" in e:
        return ("номер в Wappi отключён (телефон вышел из WhatsApp/мессенджера или разрядился). "
                "Откройте кабинет wappi.pro → профиль → если просит QR-код — отсканируйте его телефоном заново.")
    if "peer not found" in e:
        return ("Telegram не нашёл чат: номер вышел из группы или её удалили. Остальные чаты собираются; такой чат сервис "
                "пропускает сутки. Если группа нужна — добавьте номер в неё заново.")
    if "timed out" in e or "timeout" in e:
        return "Wappi долго не отвечает — у них перегрузка или сеть. Обычно проходит само; если держится часами — напишите в поддержку Wappi."
    if "чуть позже" in e or "429" in e or "too many" in e:
        return "Wappi просит повторить запрос позже — у них временная перегрузка. Обычно проходит само."
    if "401" in e or "403" in e or "token" in e:
        return "Wappi не принимает ключ — проверьте WAPPI_TOKEN в .env и оплату тарифа."
    if "402" in e or "оплат" in e or "balance" in e:
        return "похоже, закончилась оплата тарифа Wappi — продлите в кабинете wappi.pro."
    return "проверьте в кабинете wappi.pro, подключён ли номер и оплачен ли тариф."


def check_health(conn: sqlite3.Connection, wappi_errors: dict[str, str], llm_failures: int) -> None:
    """Проверки после каждого цикла worker."""
    if not _chat_id(conn) and not (mailer.available() and (config.ALERT_EMAIL or config.ADMIN_EMAILS)):
        return
    now = int(time.time())
    if config.WAPPI_ENABLED and config.WAPPI_PROFILES:
        last = conn.execute("SELECT MAX(ts) FROM messages WHERE source != 'manual'").fetchone()[0] or 0
        hours = (now - last) / 3600
        if hours >= config.ALERT_SILENCE_HOURS:
            alert(conn, "silence", f"Нет новых сообщений из чатов уже {hours:.0f} ч. Проверьте Wappi "
                                   f"(подключены ли номера, оплачен ли тариф).")
        else:
            resolve(conn, "silence", "Сообщения из чатов снова поступают.")
    # 24.10d: разовые сбои Wappi (таймаут, «повторите позже») проходят сами — пишем, только если ошибка держится 15+ минут
    for profile in set(config.WAPPI_PROFILES) | set(wappi_errors):
        key = f"wappi_err_since:{profile}"
        err = wappi_errors.get(profile)
        if not err:
            if db.get_state(conn, key) not in (None, "0"):
                db.set_state(conn, key, "0")
                resolve(conn, f"wappi:{profile}", f"Wappi {profile} снова отвечает — сообщения собираются.")
            continue
        since = int(db.get_state(conn, key, "0") or 0)
        if not since:
            db.set_state(conn, key, str(now))
            continue
        if now - since >= WAPPI_GRACE_S:
            alert(conn, f"wappi:{profile}", f"Wappi {profile} не отвечает уже {(now - since) // 60} мин: {wappi_hint(err)}\n"
                                            f"Ответ Wappi: {err[:300]}")
    if config.SITE_URL.startswith("https") and (config.SECRET_KEY in ("", "change-me") or len(config.SECRET_KEY) < 24):
        alert(conn, "secret_key", "В .env не задан надёжный SECRET_KEY — ссылки из писем и календаря можно подделать. "
                                  "Выполните на сервере: echo SECRET_KEY=$(openssl rand -hex 32) >> .env && docker compose up -d",
              every_s=86400)
    disk = shutil.disk_usage(Path(config.DB_PATH).parent) if config.DB_PATH != ":memory:" else None
    if disk and disk.free < 2 * 2**30:
        alert(conn, "disk", f"На сервере мало места: свободно {disk.free // 2**20} МБ. Сайт может остановиться.", every_s=6 * 3600)
    if llm_failures >= 3:
        alert(conn, "deepseek", f"DeepSeek не отвечает ({llm_failures} ошибок за цикл) — разбираю правилами. "
                                f"Проверьте баланс и ключ на platform.deepseek.com.")

"""Уведомления в Telegram о сбоях и короткая сводка раз в день.

Бот создаётся в @BotFather, токен — в .env (TELEGRAM_BOT_TOKEN). Получатель
(TELEGRAM_ADMIN, ник без @) пишет боту /start — сервис бота (app/bot.py) запоминает
его чат, а отсюда туда шлются тревоги и сводка.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request

from . import config, db

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


def _api(method: str, payload: dict) -> dict | None:
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    req = urllib.request.Request(
        API.format(token=config.TELEGRAM_BOT_TOKEN, method=method),
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.warning("Telegram %s: %s", method, e)
        return None


def _chat_id(conn: sqlite3.Connection) -> str | None:
    return db.get_state(conn, "tg_admin_chat")


def send(conn: sqlite3.Connection, text: str) -> bool:
    chat = _chat_id(conn)
    if not chat:
        return False
    return bool(_api("sendMessage", {"chat_id": chat, "text": text, "disable_web_page_preview": True}))


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


def check_health(conn: sqlite3.Connection, wappi_errors: dict[str, str], llm_failures: int) -> None:
    """Проверки после каждого цикла worker."""
    if not _chat_id(conn):
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
    for profile, err in wappi_errors.items():
        alert(conn, f"wappi:{profile}", f"Wappi {profile} отвечает ошибкой: {err[:300]}")
    if llm_failures >= 3:
        alert(conn, "deepseek", f"DeepSeek не отвечает ({llm_failures} ошибок за цикл) — разбираю правилами. "
                                f"Проверьте баланс и ключ на platform.deepseek.com.")

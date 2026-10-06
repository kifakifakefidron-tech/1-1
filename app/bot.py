"""Telegram-бот 1+1: вход на сайт, подтверждение номера, «убрать мой номер»,
уведомления владельцу. Отдельный сервис: python -m app.bot (долгий опрос Telegram).

Сценарии (ссылка с сайта t.me/<бот>?start=<payload>):
  login_<токен>  — вход на сайт; новый пользователь подтверждает номер кнопкой
                   «Поделиться номером» → неделя бесплатно (раз на номер);
  link_<токен>   — привязать Telegram и номер к аккаунту, вошедшему по почте;
  optout         — агент убирает свой номер с сайта (подтверждает, что номер его).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from . import accounts, config, db, rules, tg

log = logging.getLogger("bot")

PHONE_KB = {"keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}]],
            "resize_keyboard": True, "one_time_keyboard": True}
NO_KB = {"remove_keyboard": True}


def api(method: str, payload: dict, timeout: int = 20) -> dict | None:
    try:
        return tg.call(method, payload, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        log.warning("Telegram %s: %s%s", method, e, " — сервер не видит Telegram: нужен доступ у хостинга "
                    "или TELEGRAM_PROXY / TELEGRAM_API_BASE в .env" if "unreachable" in str(e) or "timed out" in str(e) else "")
        return None


def say(chat: int, text: str, kb: dict | None = None) -> None:
    payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if kb:
        payload["reply_markup"] = kb
    api("sendMessage", payload)


def _name(frm: dict) -> str:
    return " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or frm.get("username") or ""


def _confirm(conn, token_row, user_id: int) -> None:
    conn.execute("UPDATE tg_tokens SET status = 'ok', result_user = ? WHERE token = ?", (user_id, token_row["token"]))
    conn.commit()


def _welcome(conn, chat: int, user) -> None:
    if accounts.has_access(user):
        until = time.strftime("%d.%m.%Y", time.localtime(accounts.access_until(user) or time.time()))
        extra = "Доступ открыт" + ("" if user["is_admin"] else f" до {until}") + "."
    else:
        extra = "Пробная неделя на этот номер уже была — номера агентов откроются после оплаты подписки."
    say(chat, f"✅ Готово! Вы вошли на 1+1. {extra}\nВернитесь на вкладку с сайтом — она обновится сама.", NO_KB)


def handle_start(conn, msg: dict, payload: str) -> None:
    chat, frm = msg["chat"]["id"], msg.get("from") or {}
    if payload == "optout":
        conn.execute("INSERT INTO tg_tokens (token, purpose, chat_id, status, created) VALUES (?,?,?,?,?)",
                     (f"optout{chat}{int(time.time())}", "optout", chat, "need_phone", int(time.time())))
        conn.commit()
        say(chat, "Чтобы убрать ваш номер с сайта 1+1, подтвердите, что он ваш, — нажмите кнопку ниже. "
                  "После этого номер не будет показываться ни в одном объявлении.", PHONE_KB)
        return
    kind, _, token = payload.partition("_")
    row = accounts.tg_token(conn, token) if token else None
    if kind not in ("login", "link") or row is None or row["purpose"] != kind:
        say(chat, f"Это бот сайта 1+1 — поиск квартир и домов Краснодара из риелторских чатов.\n"
                  f"Войти можно на сайте: {config.SITE_URL}\n"
                  f"Убрать свой номер с сайта: отправьте /start optout", NO_KB)
        return
    conn.execute("UPDATE tg_tokens SET chat_id = ? WHERE token = ?", (chat, token))
    conn.commit()
    if kind == "login":
        user = conn.execute("SELECT * FROM users WHERE tg_id = ?", (frm["id"],)).fetchone()
        if user is not None and user["phone"]:
            user = accounts.upsert_tg_user(conn, frm["id"], frm.get("username"), _name(frm))
            _confirm(conn, row, user["id"])
            _welcome(conn, chat, user)
            return
    conn.execute("UPDATE tg_tokens SET status = 'need_phone' WHERE token = ?", (token,))
    conn.commit()
    say(chat, "Чтобы войти, подтвердите номер телефона — нажмите кнопку «📱 Поделиться номером» ниже.\n"
              f"Новым пользователям — {config.TRIAL_DAYS} дней бесплатного доступа (один раз на номер). "
              "Ваш номер никому не показывается.", PHONE_KB)


def handle_contact(conn, msg: dict) -> None:
    chat, frm, contact = msg["chat"]["id"], msg.get("from") or {}, msg["contact"]
    if contact.get("user_id") != frm.get("id"):
        say(chat, "Пожалуйста, отправьте СВОЙ номер — кнопкой «📱 Поделиться номером».", PHONE_KB)
        return
    phone = rules.normalize_phone(contact.get("phone_number", ""))
    row = conn.execute("SELECT * FROM tg_tokens WHERE chat_id = ? AND status = 'need_phone' "
                       "ORDER BY created DESC LIMIT 1", (chat,)).fetchone()
    if row is None or row["created"] < time.time() - 1800:
        say(chat, f"Номер получен, но вход устарел. Нажмите «Войти через Telegram» на сайте ещё раз: {config.SITE_URL}", NO_KB)
        return
    if not phone:
        say(chat, "Не получилось распознать номер. Сейчас вход доступен для российских номеров (+7).", NO_KB)
        return
    if row["purpose"] == "optout":
        accounts.add_optout(conn, phone, "agent")
        conn.execute("UPDATE tg_tokens SET status = 'ok' WHERE token = ?", (row["token"],))
        conn.commit()
        say(chat, f"✅ Номер {phone} скрыт: на сайте 1+1 его больше не увидят.", NO_KB)
        return
    if row["purpose"] == "link" and row["user_id"]:
        ok, err = accounts.link_telegram(conn, row["user_id"], frm["id"], frm.get("username"), phone)
        if not ok:
            say(chat, err, NO_KB)
            conn.execute("UPDATE tg_tokens SET status = 'error' WHERE token = ?", (row["token"],))
            conn.commit()
            return
        user = accounts.get_user(conn, row["user_id"])
    else:
        user = accounts.upsert_tg_user(conn, frm["id"], frm.get("username"), _name(frm), phone)
    _confirm(conn, row, user["id"])
    _welcome(conn, chat, accounts.get_user(conn, user["id"]))


def remember_admin(conn, msg: dict) -> None:
    """Владелец написал боту — запоминаем чат для уведомлений о сбоях."""
    frm = msg.get("from") or {}
    if (frm.get("username") or "").lower() == config.TELEGRAM_ADMIN.lower() and frm.get("id"):
        if db.get_state(conn, "tg_admin_chat") != str(msg["chat"]["id"]):
            db.set_state(conn, "tg_admin_chat", str(msg["chat"]["id"]))
            say(msg["chat"]["id"], "🔔 Сюда буду присылать уведомления о работе сайта 1+1 "
                                   "(сбои и короткая сводка раз в день).")


def handle(conn, upd: dict) -> None:
    msg = upd.get("message")
    if not msg or msg.get("chat", {}).get("type") != "private":
        return
    remember_admin(conn, msg)
    text = (msg.get("text") or "").strip()
    if msg.get("contact"):
        handle_contact(conn, msg)
    elif text.startswith("/start"):
        handle_start(conn, msg, text[6:].strip())
    elif text:
        say(msg["chat"]["id"], f"Сайт 1+1: {config.SITE_URL}\nУбрать свой номер с сайта: /start optout")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not config.TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN не задан — бот не запущен (вход через Telegram и уведомления выключены)")
        while True:
            time.sleep(3600)
    conn = db.connect()
    offset = int(db.get_state(conn, "tg_offset") or 0)
    log.info("бот запущен")
    while True:
        res = api("getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ["message"]}, timeout=40)
        if res is None:
            time.sleep(5)
            continue
        if time.time() - float(db.get_state(conn, "tg_ok") or 0) > 60:
            db.set_state(conn, "tg_ok", str(int(time.time())))  # связь с Telegram есть
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            db.set_state(conn, "tg_offset", str(offset))
            try:
                handle(conn, upd)
            except Exception:  # noqa: BLE001 — одно сообщение не должно ронять бота
                log.exception("ошибка обработки сообщения")


if __name__ == "__main__":
    main()

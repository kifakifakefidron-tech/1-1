"""Забираем новые сообщения из Wappi по API (без переключения webhook —
старая система продолжает получать сообщения как раньше).

Профили в .env: WAPPI_PROFILE_WA / _TG / _MAX (или WAPPI_PROFILES=wa:<id>,tg:<id>,max:<id>).

WhatsApp умеет отдавать сообщения сразу из всех чатов (/api/sync/messages/all/get).
У Telegram и MAX такого запроса нет: берём список чатов (chats/get), оставляем
группы, где были новые сообщения, и забираем сообщения каждой (messages/get).
Сообщения прочитанными НЕ отмечаем (mark_all=false) — в ваших чатах ничего не меняется.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import agent, config, db, ingest, region

log = logging.getLogger(__name__)

BASE = "https://wappi.pro"
PREFIX = {"wa": "/api/sync", "tg": "/tapi/sync", "max": "/maxapi/sync"}
PAGE = 400          # WhatsApp: сообщений за запрос
CHAT_PAGE = 100     # Telegram/MAX: сообщений чата за запрос (максимум API)
CHATS_PAGE = 200    # Telegram/MAX: чатов за запрос
MAX_PAGES = 50
_shown_fields: set[str] = set()
errors: dict[str, str] = {}  # профиль → последняя ошибка в этом цикле (для уведомлений)


class WappiError(Exception):
    pass


def _get(path: str, params: dict, token: str) -> dict | list:
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _call(profile: str, path: str, params: dict, token: str) -> dict | list | None:
    """Запрос к Wappi; при ошибке пишет понятную строку в журнал и возвращает None."""
    try:
        return _get(path, params, token)
    except urllib.error.HTTPError as e:
        body = e.read()[:300]
        hint = ""
        if b"token" in body.lower():
            hint = " — Wappi не принял токен: проверьте WAPPI_TOKEN_* в .env"
        elif e.code == 404:
            hint = " — такого адреса нет в Wappi"
        log.error("Wappi %s %s: HTTP %s %s%s", profile, path, e.code, body, hint)
        errors[profile] = f"HTTP {e.code} {body.decode(errors='replace')}{hint}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.error("Wappi %s %s: %s", profile, path, e)
        errors[profile] = str(e)
    return None


def _list(payload, keys: tuple[str, ...]) -> list[dict]:
    if isinstance(payload, list):
        return payload
    for key in keys:
        v = payload.get(key) if isinstance(payload, dict) else None
        if isinstance(v, list):
            return v
    return []


def _ts(v) -> int | None:
    """Время в секундах (MAX отдаёт миллисекунды)."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n // 1000 if n > 10**11 else n


def _is_group(source: str, m: dict) -> bool:
    chat = str(m.get("chatId") or m.get("chat_id") or m.get("id") or "")
    if source == "wa":
        return chat.endswith("@g.us")
    if m.get("isGroup") or m.get("is_group"):
        return True
    ctype = str(m.get("chat_type") or m.get("chatType") or m.get("type") or "").lower()
    if ctype in ("group", "supergroup", "channel", "chat"):
        return True
    return chat.startswith("-")


def _text(m: dict) -> str:
    for k in ("body", "text", "caption", "message"):
        v = m.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _sender_phone(m: dict) -> str | None:
    raw = str(m.get("from") or m.get("sender") or m.get("author") or "")
    digits = "".join(ch for ch in raw.split("@")[0] if ch.isdigit())
    return digits if 10 <= len(digits) <= 12 else None


def _since(cursor: int) -> str:
    return datetime.fromtimestamp(cursor - 300, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _store(conn: sqlite3.Connection, source: str, profile_id: str, m: dict,
           chat_id: str | None = None, chat_name: str | None = None, group: bool | None = None) -> tuple[bool, int | None]:
    """Сохранить одно сообщение. Возвращает (добавлено, время)."""
    ts = _ts(m.get("time") or m.get("timestamp"))
    if m.get("fromMe") and not config.WAPPI_INCLUDE_FROM_ME:
        return False, ts
    is_group = group if group is not None else _is_group(source, m)
    if not is_group and source in config.VERIFY_CHANNELS and _verify_pending(conn):
        _check_code(conn, profile_id, source, m, chat_id)
    if config.WAPPI_GROUPS_ONLY and not is_group:
        return False, ts
    if m.get("isDeleted"):
        return False, ts
    text = _text(m)
    if len(text) < 30:
        return False, ts
    cid = str(chat_id or m.get("chatId") or m.get("chat_id") or "")
    chat_name = (chat_name or m.get("chat_name") or m.get("chatName") or m.get("chat_title")
                 or region.chat_name(conn, source, cid))
    if region.is_excluded(conn, source, cid, chat_name):   # чат другого города или отключён в админке
        return False, ts
    mid = ingest.add_message(
        conn, source=source, text=text, ts=ts or int(time.time()), profile_id=profile_id,
        chat_id=cid, chat_name=chat_name,
        msg_id=str(m.get("id") or ""), sender=_sender_phone(m), sender_name=m.get("senderName"),
    )
    return bool(mid), ts


_pending_cache: dict = {"t": 0.0, "v": False}


def _verify_pending(conn) -> bool:
    """Есть ли агенты, ждущие подтверждения номера (проверяем не чаще раза в 20 с)."""
    if time.time() - _pending_cache["t"] > 20:
        _pending_cache.update(t=time.time(), v=agent.has_pending(conn))
    return _pending_cache["v"]


def _personal_phone(m: dict, chat_id: str | None) -> str | None:
    """Номер собеседника в личном чате: WhatsApp — «79181234567@c.us», у MAX/Telegram — поле с телефоном."""
    for k in ("phone", "sender_phone", "senderPhone", "from_phone"):
        if m.get(k):
            return str(m[k])
    for k in ("sender", "from", "author", "contact"):
        v = m.get(k)
        if isinstance(v, dict) and (v.get("phone") or v.get("phone_number")):
            return str(v.get("phone") or v.get("phone_number"))
    return _sender_phone(m) or _sender_phone({"from": chat_id or m.get("chatId") or m.get("chat_id") or ""})


def _check_code(conn, profile_id: str, source: str, m: dict, chat_id: str | None) -> None:
    text = _text(m)
    if not text or len(text) > 200 or not any(ch.isdigit() for ch in text):
        return
    _show_fields(f"{source}:{profile_id}", "личное сообщение", m)
    phone = _personal_phone(m, chat_id)
    if agent.check_message(conn, phone, text):
        log.info("Номер агента подтверждён через %s", source)
        _pending_cache["t"] = 0
    elif phone is None:
        log.warning("Код подтверждения пришёл в %s, но номер отправителя не найден в полях: %s", source, sorted(m))


def _show_fields(profile: str, what: str, obj: dict) -> None:
    key = f"{profile}:{what}"
    if key not in _shown_fields:  # один раз после запуска — для проверки формата
        _shown_fields.add(key)
        log.info("Wappi %s: пример полей (%s): %s", profile, what, sorted(obj.keys()))


def _fresh(m: dict, cursor: int) -> bool:
    ts = _ts(m.get("time") or m.get("timestamp"))
    return ts is None or ts >= cursor - 300


def _poll_all_messages(conn, profile, source, profile_id, token, cursor) -> tuple[int, int, int]:
    """WhatsApp: все сообщения всех чатов одним запросом (постранично).

    Wappi фильтр по дате не соблюдает и отдаёт всю историю, поэтому идём от новых
    к старым и останавливаемся, как только дошли до уже забранного (курсор)."""
    added = received = 0
    newest = cursor
    for page in range(MAX_PAGES):
        params = {"profile_id": profile_id, "limit": PAGE, "offset": page * PAGE,
                  "date": _since(cursor), "order": "desc"}
        payload = _call(profile, f"{PREFIX[source]}/messages/all/get", params, token)
        if payload is None:
            break
        batch = _list(payload, ("messages", "data", "result"))
        if batch:
            _show_fields(profile, "сообщение", batch[0])
        fresh = [m for m in batch if _fresh(m, cursor)]
        received += len(fresh)
        for m in fresh:
            ok, ts = _store(conn, source, profile_id, m)
            added += ok
            newest = max(newest, ts or 0)
        if len(batch) < PAGE or len(fresh) < len(batch):
            break
    return added, received, newest


def _poll_by_chats(conn, profile, source, profile_id, token, cursor) -> tuple[int, int, int]:
    """Telegram/MAX: список чатов → группы с новыми сообщениями → сообщения каждой."""
    chats: list[dict] = []
    for page in range(10):
        params = {"profile_id": profile_id, "limit": CHATS_PAGE, "offset": page * CHATS_PAGE,
                  "show_all": "true", "order": "desc"}
        payload = _call(profile, f"{PREFIX[source]}/chats/get", params, token)
        if payload is None:
            return 0, 0, cursor
        batch = _list(payload, ("dialogs", "chats", "data", "result"))
        if batch:
            _show_fields(profile, "чат", batch[0])
        old = False
        for c in batch:
            region.save_chat(conn, source, str(c.get("id") or c.get("chatId") or c.get("chat_id") or ""),
                             c.get("name") or c.get("title") or c.get("chat_name"))
            last = _ts(c.get("last_timestamp") or c.get("last_time") or c.get("timestamp"))
            if last is not None and last < cursor - 300:
                old = True
                continue
            if _is_group(source, c):
                chats.append(c)
            elif source in config.VERIFY_CHANNELS and _verify_pending(conn):
                c = {**c, "_personal": True}   # личка: ищем только коды подтверждения номера
                chats.append(c)
        if len(batch) < CHATS_PAGE or old:
            break

    added = received = 0
    newest = cursor
    for c in chats:
        chat_id = str(c.get("id") or c.get("chatId") or c.get("chat_id") or "")
        chat_name = c.get("name") or c.get("title") or c.get("chat_name")
        if not chat_id:
            continue
        for page in range(MAX_PAGES):
            params = {"profile_id": profile_id, "chat_id": chat_id, "limit": CHAT_PAGE,
                      "offset": page * CHAT_PAGE, "date": _since(cursor), "order": "desc", "mark_all": "false"}
            payload = _call(profile, f"{PREFIX[source]}/messages/get", params, token)
            if payload is None:
                break
            batch = _list(payload, ("messages", "data", "result"))
            if batch:
                _show_fields(profile, "сообщение", batch[0])
            fresh = [m for m in batch if _fresh(m, cursor)]
            received += len(fresh)
            for m in fresh:
                ok, ts = _store(conn, source, profile_id, m, chat_id=chat_id, chat_name=chat_name,
                                group=not c.get("_personal"))
                added += ok
                newest = max(newest, ts or 0)
            if len(batch) < CHAT_PAGE or len(fresh) < len(batch):
                break
        time.sleep(0.2)  # бережно к Wappi
    return added, received, newest


def _chat_title(c: dict) -> str | None:
    for k in ("name", "title", "subject", "chat_name", "chatName", "formattedTitle"):
        v = c.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    contact = c.get("contact")
    if isinstance(contact, dict):
        return contact.get("name") or contact.get("pushname")
    return None


def refresh_chat_names(conn: sqlite3.Connection, every_h: int = 6) -> int | None:
    """WhatsApp: названия групп из списка чатов (в сообщениях их нет). Раз в every_h часов; None — ещё рано."""
    if time.time() - float(db.get_state(conn, "chat_names_at") or 0) < every_h * 3600:
        return None
    db.set_state(conn, "chat_names_at", str(int(time.time())))
    saved = 0
    for profile in config.WAPPI_PROFILES:
        source, _, profile_id = profile.partition(":")
        token = config.WAPPI_TOKENS.get(source)
        if source != "wa" or not token:
            continue
        for page in range(20):
            payload = _call(profile, f"{PREFIX[source]}/chats/get",
                            {"profile_id": profile_id, "limit": CHATS_PAGE, "offset": page * CHATS_PAGE, "show_all": "true"},
                            token)
            if payload is None:
                break
            batch = _list(payload, ("dialogs", "chats", "data", "result"))
            if batch:
                _show_fields(profile, "чат", batch[0])
            for c in batch:
                cid = str(c.get("id") or c.get("chatId") or c.get("chat_id") or "")
                name = _chat_title(c)
                if cid and name:
                    region.save_chat(conn, source, cid, name)
                    saved += 1
            if len(batch) < CHATS_PAGE:
                break
    conn.commit()
    log.info("Wappi: названия чатов обновлены (%d)", saved)
    return saved


def poll_profile(conn: sqlite3.Connection, profile: str) -> int:
    """Забрать новые сообщения одного профиля. Возвращает число новых."""
    source, _, profile_id = profile.partition(":")
    if source not in PREFIX or not profile_id:
        log.error("Неверный профиль Wappi «%s», ожидается wa:<id>, tg:<id> или max:<id>", profile)
        return 0
    token = config.WAPPI_TOKENS.get(source)
    if not token:
        log.error("Нет токена Wappi для «%s»: задайте WAPPI_TOKEN_%s в .env", profile, source.upper())
        return 0
    state_key = f"wappi_cursor:{profile}"
    cursor = int(db.get_state(conn, state_key) or 0)
    if not cursor:
        cursor = int(time.time()) - config.WAPPI_BACKFILL_HOURS * 3600

    poll = _poll_all_messages if source == "wa" else _poll_by_chats
    added, received, newest = poll(conn, profile, source, profile_id, token, cursor)
    if newest > cursor:
        db.set_state(conn, state_key, str(newest))
    log.info("Wappi %s: получено %d, новых объявлений-кандидатов %d", profile, received, added)
    return added


def poll_all(conn: sqlite3.Connection) -> int:
    errors.clear()
    if not config.WAPPI_PROFILES or not any(config.WAPPI_TOKENS.values()):
        return 0
    return sum(poll_profile(conn, p) for p in config.WAPPI_PROFILES)

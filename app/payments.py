"""Оплата подписки через ЮKassa (самозанятый). ПОДГОТОВЛЕНО, но выключено: работает,
только если PAYMENTS_ENABLED=true и заданы YOOKASSA_SHOP_ID / YOOKASSA_SECRET.

Как включить (в конце, когда будет свой домен):
  1. В кабинете ЮKassa: «Интеграция» → HTTP-уведомления → адрес
     https://<домен>/api/payments/yookassa, событие payment.succeeded.
  2. Чеки самозанятого: в ЮKassa подключить «Мой налог» — чеки уйдут сами.
  3. В .env: PAYMENTS_ENABLED=true, YOOKASSA_SHOP_ID, YOOKASSA_SECRET; docker compose up -d.

Уведомлению ЮKassa не верим на слово: статус платежа всегда перепроверяем запросом к API.
"""
from __future__ import annotations

import base64
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

from . import accounts, config, db

log = logging.getLogger(__name__)
API = "https://api.yookassa.ru/v3/payments"


def available() -> bool:
    return config.PAYMENTS_ENABLED and bool(config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET)


def _request(url: str, body: dict | None = None) -> dict:
    auth = base64.b64encode(f"{config.YOOKASSA_SHOP_ID}:{config.YOOKASSA_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    if body is not None:
        headers["Idempotence-Key"] = str(uuid.uuid4())
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def first_time(conn: sqlite3.Connection | None, user_id: int | None) -> bool:
    """Человек ещё ни разу не платил — ему первая неделя по цене SUB_FIRST (49 ₽)."""
    if not config.SUB_FIRST or conn is None or not user_id:
        return bool(config.SUB_FIRST) and not user_id
    return not conn.execute("SELECT 1 FROM payments WHERE user_id = ? AND status = 'succeeded' LIMIT 1", (user_id,)).fetchone()


def _price_list(conn: sqlite3.Connection | None, user_id: int | None) -> list[tuple[int, int, str]]:
    """(дней, цена, ключ тарифа): у тех, кто ещё не платил, неделя — по первой цене."""
    out = [(d, p, f"{d}") for d, p in sorted(config.SUB_PLANS)]
    if first_time(conn, user_id):
        fd, fp = config.SUB_FIRST
        out = [(d, p, k) for d, p, k in out if d != fd]
        out.insert(0, (fd, fp, "first"))
    return out


def plans(conn: sqlite3.Connection | None = None, user_id: int | None = None) -> list[dict]:
    """Тарифы для страницы оплаты: дней, цена, цена за день, выгода к обычной неделе; first — первая неделя со скидкой."""
    regular = sorted(config.SUB_PLANS) or [(config.SUB_DAYS, config.SUB_PRICE)]
    base = min(regular, key=lambda x: x[0])
    per_base = base[1] / base[0]
    out = []
    for days, price, key in _price_list(conn, user_id):
        save = round((1 - (price / days) / per_base) * 100) if (days, price) != base else 0
        out.append({"days": days, "price": price, "per_day": round(price / days, 1), "save": max(0, save),
                    "key": key, "first": key == "first",
                    "old_price": base[1] if key == "first" else None})
    return out


class PayError(Exception):
    """Платёж не создан — текст для человека."""


def create(conn: sqlite3.Connection, user_id: int, days: int | None = None) -> str:
    """Создать платёж по тарифу (дней); возвращает ссылку на страницу оплаты ЮKassa."""
    # цену считает сервер (первая неделя по 49 ₽ — только тем, кто ещё не платил)
    options = _price_list(conn, user_id)
    plan = next(((d, p) for d, p, _ in options if d == days), None) or (options[0][:2] if options else (config.SUB_DAYS, config.SUB_PRICE))
    days, price = plan
    db.flush(conn)   # не держать базу, пока ждём ЮKassa
    try:
        pay = _request(API, {
        "amount": {"value": f"{price}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{config.SITE_URL}/pay?check=1"},
        "description": f"Доступ 1+1 на {days} дн.",
        "metadata": {"user_id": user_id, "days": days},
        })
    except urllib.error.HTTPError as e:   # 18.10: ЮKassa ответила ошибкой — причину админу, человеку понятный текст
        try:
            why = json.loads(e.read() or b"{}")
        except (ValueError, OSError):
            why = {}
        text = f"ЮKassa {e.code}: {why.get('code', '')} {why.get('description', '')} {why.get('parameter', '')}".strip()
        log.error("ЮKassa: не создан платёж: %s", text)
        from . import notify
        notify.alert(conn, "yookassa_create", f"Оплата не создаётся — {text}. Проверьте shopId/секретный ключ и чеки в ЮKassa.",
                     every_s=3600)
        raise PayError("Оплата сейчас не проходит — мы уже разбираемся. Попробуйте чуть позже.") from e
    except (urllib.error.URLError, TimeoutError) as e:
        log.error("ЮKassa недоступна: %s", e)
        raise PayError("Платёжный сервис не отвечает — попробуйте ещё раз через минуту.") from e
    conn.execute("INSERT INTO payments (user_id, amount, days, provider_id, status, created) VALUES (?,?,?,?,?,?)",
                 (user_id, price, days, pay["id"], pay.get("status", "pending"), int(time.time())))
    conn.commit()
    return pay["confirmation"]["confirmation_url"]


def last_status(conn: sqlite3.Connection, user_id: int) -> dict | None:
    """После возврата из ЮKassa: последний платёж человека — перепроверяем и говорим, чем закончился."""
    row = conn.execute("SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    if row is None:
        return None
    if row["status"] not in ("succeeded", "canceled") and available():
        confirm(conn, row["provider_id"])
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (row["id"],)).fetchone()
    return {"status": row["status"], "days": row["days"], "amount": row["amount"]}


def confirm(conn: sqlite3.Connection, provider_id: str) -> bool:
    """Перепроверить платёж в ЮKassa и, если оплачен, продлить доступ (один раз)."""
    row = conn.execute("SELECT * FROM payments WHERE provider_id = ?", (provider_id,)).fetchone()
    if row is None or row["status"] == "succeeded":
        return False
    db.flush(conn)   # не держать базу, пока ждём ЮKassa
    try:
        pay = _request(f"{API}/{provider_id}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.error("ЮKassa: не удалось проверить платёж %s: %s", provider_id, e)
        return False
    if pay.get("status") != "succeeded" or not pay.get("paid"):
        conn.execute("UPDATE payments SET status = ? WHERE id = ?", (pay.get("status", "unknown"), row["id"]))
        conn.commit()
        return False
    try:
        paid_sum = float(((pay.get("amount") or {}).get("value")) or 0)
    except (TypeError, ValueError):
        paid_sum = 0
    if paid_sum + 0.001 < float(row["amount"]):
        log.error("ЮKassa: платёж %s на %s ₽, а тариф %s ₽ — доступ не продлеваю", provider_id, paid_sum, row["amount"])
        return False
    # Уведомление ЮKassa и возврат человека на сайт могут прийти одновременно (в разные процессы):
    # продлеваем только тот, кто первым перевёл платёж в «оплачен» — иначе доступ продлился бы дважды
    cur = conn.execute("UPDATE payments SET status = 'succeeded', paid = ? WHERE id = ? AND status != 'succeeded'",
                       (int(time.time()), row["id"]))
    if cur.rowcount != 1:
        conn.commit()
        return False
    accounts.extend(conn, row["user_id"], row["days"])
    return True

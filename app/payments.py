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

from . import accounts, config

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


def create(conn: sqlite3.Connection, user_id: int) -> str:
    """Создать платёж; возвращает ссылку на страницу оплаты ЮKassa."""
    pay = _request(API, {
        "amount": {"value": f"{config.SUB_PRICE}.00", "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": f"{config.SITE_URL}/?paid=1"},
        "description": f"Подписка 1+1 на {config.SUB_DAYS} дн.",
        "metadata": {"user_id": user_id},
    })
    conn.execute("INSERT INTO payments (user_id, amount, days, provider_id, status, created) VALUES (?,?,?,?,?,?)",
                 (user_id, config.SUB_PRICE, config.SUB_DAYS, pay["id"], pay.get("status", "pending"), int(time.time())))
    conn.commit()
    return pay["confirmation"]["confirmation_url"]


def confirm(conn: sqlite3.Connection, provider_id: str) -> bool:
    """Перепроверить платёж в ЮKassa и, если оплачен, продлить доступ (один раз)."""
    row = conn.execute("SELECT * FROM payments WHERE provider_id = ?", (provider_id,)).fetchone()
    if row is None or row["status"] == "succeeded":
        return False
    try:
        pay = _request(f"{API}/{provider_id}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        log.error("ЮKassa: не удалось проверить платёж %s: %s", provider_id, e)
        return False
    if pay.get("status") != "succeeded" or not pay.get("paid"):
        conn.execute("UPDATE payments SET status = ? WHERE id = ?", (pay.get("status", "unknown"), row["id"]))
        conn.commit()
        return False
    conn.execute("UPDATE payments SET status = 'succeeded', paid = ? WHERE id = ?", (int(time.time()), row["id"]))
    accounts.extend(conn, row["user_id"], row["days"])
    return True

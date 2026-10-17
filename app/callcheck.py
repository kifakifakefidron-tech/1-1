"""Подтверждение номера звонком (17.10, решение Артёма — основной способ; код в MAX — запасной).

SMS.ru «Авторизация по звонку» (callcheck): человек звонит С СВОЕГО номера на номер, который выдал SMS.ru, звонок
сбрасывается сам — номер подтверждён. Кодов вводить не нужно, работает на любом телефоне.
  * POST https://sms.ru/callcheck/add?api_id=…&phone=79…&json=1 → check_id, call_phone, call_phone_pretty
  * GET  https://sms.ru/callcheck/status?api_id=…&check_id=…&json=1 → check_status: 400 ждём, 401 подтверждён, 402 истёк
Ключ — SMSRU_API_ID в .env (личный кабинет sms.ru → «Программистам» → api_id). Без ключа способ выключен.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

from . import config

log = logging.getLogger(__name__)
BASE = "https://sms.ru/callcheck"


def enabled() -> bool:
    return bool(config.SMSRU_API_ID)


def _get(path: str, **params) -> dict:
    q = urllib.parse.urlencode({"api_id": config.SMSRU_API_ID, "json": 1, **params})
    req = urllib.request.Request(f"{BASE}/{path}?{q}", headers={"User-Agent": "1plus1/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start(phone: str, ip: str = "") -> tuple[dict | None, str]:
    """phone — +79… → {'check_id', 'call_phone' (+7…), 'call_pretty'} или (None, причина).
    ip — адрес посетителя (не сервера): SMS.ru по нему понимает роуминг и выдаёт обычный номер вместо 8-800."""
    try:
        d = _get("add", phone=phone.lstrip("+"), **({"ip": ip} if ip else {}))
    except Exception as e:  # noqa: BLE001 — сеть / SMS.ru недоступен
        log.warning("callcheck add: %s", e)
        return None, "Сервис звонков сейчас не отвечает — попробуйте через минуту или подтвердите кодом в MAX."
    if d.get("status") != "OK" or not d.get("check_id") or not d.get("call_phone"):
        log.warning("callcheck add: %s", d)
        return None, "Не получилось начать проверку звонком — подтвердите кодом в MAX или напишите нам."
    num = "+" + str(d["call_phone"]).lstrip("+")
    return {"check_id": str(d["check_id"]), "call_phone": num, "call_pretty": d.get("call_phone_pretty") or num}, ""


def status(check_id: str) -> str:
    """'ok' — позвонили с нужного номера, 'pending' — ещё нет, 'expired' — время вышло, 'error' — сервис не ответил."""
    try:
        d = _get("status", check_id=check_id)
    except Exception as e:  # noqa: BLE001
        log.warning("callcheck status: %s", e)
        return "error"
    s = str(d.get("check_status") or "")
    return {"401": "ok", "400": "pending", "402": "expired"}.get(s, "error" if d.get("status") != "OK" else "pending")

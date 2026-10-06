"""Общий вызов Telegram Bot API (бот, уведомления, сайт).

Если хостинг не пускает сервер к api.telegram.org, можно ходить через посредника:
  TELEGRAM_API_BASE — свой адрес-«зеркало» API (например, прокси-скрипт на зарубежном сервере);
  TELEGRAM_PROXY    — HTTP(S)-прокси, например http://логин:пароль@1.2.3.4:3128.
"""
from __future__ import annotations

import json
import urllib.request

from . import config


def _opener() -> urllib.request.OpenerDirector:
    if config.TELEGRAM_PROXY:
        return urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": config.TELEGRAM_PROXY, "https": config.TELEGRAM_PROXY}))
    return urllib.request.build_opener()


def call(method: str, payload: dict | None = None, timeout: int = 20) -> dict:
    """Запрос к Bot API. Бросает исключение при ошибке сети — обработка у вызывающего."""
    url = f"{config.TELEGRAM_API_BASE.rstrip('/')}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with _opener().open(req, timeout=timeout) as resp:
        return json.loads(resp.read())

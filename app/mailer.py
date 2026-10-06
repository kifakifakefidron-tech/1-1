"""Письма с кодом входа (SMTP, по умолчанию — почта Яндекса с паролем приложения)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def available() -> bool:
    return bool(config.SMTP_USER and config.SMTP_PASSWORD)


def send_code(to: str, code: str) -> bool:
    msg = EmailMessage()
    msg["Subject"] = f"Код входа на 1+1: {code}"
    msg["From"] = f"1+1 · поиск объектов <{config.SMTP_FROM}>"
    msg["To"] = to
    msg.set_content(
        f"Ваш код для входа на сайт 1+1: {code}\n\n"
        f"Код действует 15 минут. Если вы не запрашивали вход — просто удалите это письмо.\n\n"
        f"{config.SITE_URL}\n")
    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(), timeout=10) as s:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error("Почта: Яндекс не принял логин/пароль приложения (SMTP_USER/SMTP_PASSWORD): %s", e)
        return False
    except (smtplib.SMTPException, OSError) as e:
        log.error("Письмо на %s не отправилось: %s — если это таймаут, хостинг закрыл почтовый порт %s "
                  "(в Timeweb: тикет в поддержку «открыть SMTP-порты»)", to, e, config.SMTP_PORT)
        return False

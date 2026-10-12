"""Письма с кодом входа (SMTP, по умолчанию — почта Яндекса с паролем приложения)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from . import config

log = logging.getLogger(__name__)


def _headers(msg: EmailMessage, to: str, subject: str) -> None:
    """Заголовки, без которых Gmail, Outlook и др. зарубежные почты режут или прячут письма:
    Date и Message-ID (российские почты пропускают и без них — отсюда «доходит только на .ru»)."""
    msg["Subject"] = subject
    msg["From"] = f"1+1 · поиск объектов <{config.SMTP_FROM}>"
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    domain = (config.SMTP_FROM.split("@")[-1] if "@" in config.SMTP_FROM else "") or "1plus1assistant.ru"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Reply-To"] = config.SMTP_FROM


def available() -> bool:
    return bool(config.SMTP_USER and config.SMTP_PASSWORD)


def send_text(to: str, subject: str, body: str) -> bool:
    """Обычное письмо (уведомления владельцу)."""
    msg = EmailMessage()
    _headers(msg, to, subject)
    msg.set_content(body + f"\n\n{config.SITE_URL}\n")
    try:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ssl.create_default_context(), timeout=10) as s:
            s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        log.error("Уведомление на %s не отправилось: %s", to, e)
        return False


def send_code(to: str, code: str) -> bool:
    msg = EmailMessage()
    _headers(msg, to, f"Код входа на 1+1: {code}")
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
    except UnicodeError as e:
        log.error("Почта: в SMTP_USER/SMTP_PASSWORD русские буквы или остались слова-подсказки — "
                  "впишите настоящий ящик и пароль приложения латиницей: %s", e)
        return False
    except Exception:  # noqa: BLE001 — любая другая ошибка не должна ронять вход
        log.exception("Почта: неожиданная ошибка при отправке на %s", to)
        return False

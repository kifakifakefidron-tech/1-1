"""Настройки из переменных окружения (файл .env)."""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on", "да")


DB_PATH = os.getenv("DB_PATH", str(Path(__file__).resolve().parent.parent / "data" / "strely.db"))

# DeepSeek
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")

# Wappi: токен и список профилей «тип:id» через запятую, например
#   WAPPI_PROFILES=wa:a1b2c3d4-...,tg:e5f6...,max:0a1b...
WAPPI_TOKEN = os.getenv("WAPPI_TOKEN", "").strip()
WAPPI_PROFILES = [p.strip() for p in os.getenv("WAPPI_PROFILES", "").split(",") if p.strip()]
# Проще: по строке на тип аккаунта — WAPPI_PROFILE_WA / _TG / _MAX = только ID профиля.
# Случайно вставленную приставку «wa:»/«tg:»/«max:» убираем.
for _s in ("wa", "tg", "max"):
    _v = os.getenv(f"WAPPI_PROFILE_{_s.upper()}", "").strip()
    while _v.lower().startswith(("wa:", "tg:", "max:")):
        _v = _v.split(":", 1)[1].strip()
    if _v and f"{_s}:{_v}" not in WAPPI_PROFILES:
        WAPPI_PROFILES.append(f"{_s}:{_v}")
# Свой токен на каждый тип аккаунта (WAPPI_TOKEN_WA / _TG / _MAX); если не задан — общий WAPPI_TOKEN
WAPPI_TOKENS = {s: os.getenv(f"WAPPI_TOKEN_{s.upper()}", "").strip() or WAPPI_TOKEN for s in ("wa", "tg", "max")}
WAPPI_POLL_SECONDS = int(os.getenv("WAPPI_POLL_SECONDS", "90"))
# При первом запуске забрать сообщения за столько часов назад
WAPPI_BACKFILL_HOURS = int(os.getenv("WAPPI_BACKFILL_HOURS", "48"))
# Берём только групповые чаты (личные переписки с клиентами не трогаем)
WAPPI_GROUPS_ONLY = _bool("WAPPI_GROUPS_ONLY", True)
# Свои исходящие (вашу рассылку) не разбираем, чтобы не плодить свои же объекты
WAPPI_INCLUDE_FROM_ME = _bool("WAPPI_INCLUDE_FROM_ME", False)
# Выключатель сбора: false — новые сообщения из Wappi не забираются (уже скачанные разбираются)
WAPPI_ENABLED = _bool("WAPPI_ENABLED", True)

# Объект пропадает из поиска, если его не присылали столько дней
STALE_DAYS = int(os.getenv("STALE_DAYS", "20"))
# Через столько дней удаляем старые сообщения и давно скрытые объекты
KEEP_DAYS = int(os.getenv("KEEP_DAYS", "90"))

# Доступ: если задан ACCESS_CODE, телефоны агентов и исходные сообщения видны
# только после ввода кода (для коллег). Остальные (покупатели) видят
# контакт из PUBLIC_CONTACT.
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
PUBLIC_CONTACT = os.getenv("PUBLIC_CONTACT", "")
PUBLIC_CONTACT_LABEL = os.getenv("PUBLIC_CONTACT_LABEL", "Узнать подробности")
SITE_TITLE = os.getenv("SITE_TITLE", "1+1 · поиск объектов")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Геокодер для карты: nominatim (бесплатно, 1 запрос/сек) или off
GEOCODER = os.getenv("GEOCODER", "nominatim")

# Фид объектов СТРЕЛ (YML с сайта на Тильде); пусто — не загружать
FEED_URL = os.getenv("FEED_URL", "https://arrowsrealty.ru/tstore/yml/a950e007bd575604e7517f13082cfeba.yml").strip()
FEED_HOURS = int(os.getenv("FEED_HOURS", "6"))

# Уведомления о сбоях в Telegram: бот от @BotFather и ник, кому писать (без @).
# Ник должен сам написать боту /start — после этого бот запомнит, куда слать.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN = os.getenv("TELEGRAM_ADMIN", "ArtemAndreevic1").strip().lstrip("@")
# Тревога, если новых сообщений нет столько часов (при включённом сборе)
ALERT_SILENCE_HOURS = int(os.getenv("ALERT_SILENCE_HOURS", "3"))

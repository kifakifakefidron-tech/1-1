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
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_URL = os.getenv("DEEPSEEK_URL", "https://api.deepseek.com/chat/completions")

# Wappi: токен и список профилей «тип:id» через запятую, например
#   WAPPI_PROFILES=wa:a1b2c3d4-...,tg:e5f6...,max:0a1b...
WAPPI_TOKEN = os.getenv("WAPPI_TOKEN", "")
WAPPI_PROFILES = [p.strip() for p in os.getenv("WAPPI_PROFILES", "").split(",") if p.strip()]
WAPPI_POLL_SECONDS = int(os.getenv("WAPPI_POLL_SECONDS", "90"))
# При первом запуске забрать сообщения за столько часов назад
WAPPI_BACKFILL_HOURS = int(os.getenv("WAPPI_BACKFILL_HOURS", "48"))
# Берём только групповые чаты (личные переписки с клиентами не трогаем)
WAPPI_GROUPS_ONLY = _bool("WAPPI_GROUPS_ONLY", True)
# Свои исходящие (вашу рассылку) не разбираем, чтобы не плодить свои же объекты
WAPPI_INCLUDE_FROM_ME = _bool("WAPPI_INCLUDE_FROM_ME", False)

# Объект пропадает из поиска, если его не присылали столько дней
STALE_DAYS = int(os.getenv("STALE_DAYS", "45"))

# Доступ: если задан ACCESS_CODE, телефоны агентов и исходные сообщения видны
# только после ввода кода (для коллег). Остальные (покупатели) видят
# контакт из PUBLIC_CONTACT.
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
PUBLIC_CONTACT = os.getenv("PUBLIC_CONTACT", "")
PUBLIC_CONTACT_LABEL = os.getenv("PUBLIC_CONTACT_LABEL", "Узнать подробности")
SITE_TITLE = os.getenv("SITE_TITLE", "СТРЕЛЫ · поиск объектов")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Геокодер для карты: nominatim (бесплатно, 1 запрос/сек) или off
GEOCODER = os.getenv("GEOCODER", "nominatim")

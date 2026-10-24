"""Настройки из переменных окружения (файл .env)."""
from __future__ import annotations

import os
import re
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

# Жизнь объекта (решение Артёма 17.10): STALE_DAYS (15) на сайте без повтора в чатах — считаем от ПОСЛЕДНЕГО сообщения,
# но не раньше, чем от дня появления на 1+1 (listings.added_at: старые сообщения из истории Wappi тоже живут 15 дней) →
# ARCHIVE_DAYS (25) в архиве (повтор в чатах — сразу снова на сайте) → удаляем. Сообщения храним KEEP_DAYS (40).
# Старые значения из прежних .env (20 / 20, 45 из README) не действуют — как с ценами (_LEGACY_PLANS).
def _days(name: str, default: int, legacy: tuple[str, ...]) -> int:
    v = os.getenv(name, "").strip()
    return int(v) if v.isdigit() and v not in legacy else default


STALE_DAYS = _days("STALE_DAYS", 15, ("20", "45"))
ARCHIVE_DAYS = _days("ARCHIVE_DAYS", 25, ("20",))
KEEP_DAYS = max(_days("KEEP_DAYS", 40, ()), STALE_DAYS + ARCHIVE_DAYS)

# Код коллег (временно, для теста): вход по коду даёт доступ к телефонам агентов
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
SITE_TITLE = os.getenv("SITE_TITLE", "1+1 · поиск объектов")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# «Приведи коллегу»: столько дней доступа пригласившему, когда коллега подтвердит свой номер агента
REF_DAYS = int(os.getenv("REF_DAYS", "7"))

# Геокодер для карты: nominatim (бесплатно, 1 запрос/сек) или off
GEOCODER = os.getenv("GEOCODER", "nominatim")

# Фид объектов СТРЕЛ (YML с сайта на Тильде); пусто — не загружать
FEED_URL = os.getenv("FEED_URL", "https://arrowsrealty.ru/tstore/yml/a950e007bd575604e7517f13082cfeba.yml").strip()
FEED_HOURS = int(os.getenv("FEED_HOURS", "6"))
# Телефон партнёра для объектов фида (виден только подписчикам, как телефоны агентов)
FEED_PHONE = os.getenv("FEED_PHONE", "+79618571772").strip()

# Уведомления о сбоях в Telegram: бот от @BotFather и ник, кому писать (без @).
# Ник должен сам написать боту /start — после этого бот запомнит, куда слать.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_ADMIN = os.getenv("TELEGRAM_ADMIN", "ArtemAndreevic1").strip().lstrip("@")
# Если хостинг не пускает к api.telegram.org — посредник (см. app/tg.py)
TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "").strip() or "https://api.telegram.org"
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip()
# Тревога, если новых сообщений нет столько часов (при включённом сборе)
ALERT_SILENCE_HOURS = int(os.getenv("ALERT_SILENCE_HOURS", "3"))

# ─── Личный кабинет и подписка ─────────────────────────────────────────────
SITE_DOMAIN = os.getenv("SITE_DOMAIN", "localhost:8090").strip()
SITE_URL = os.getenv("SITE_URL", "").strip() or (
    f"http://{SITE_DOMAIN}" if SITE_DOMAIN.startswith("localhost") else f"https://{SITE_DOMAIN}")
# Отдельный нейтральный адрес для подборок клиентам (PICK_DOMAIN в .env, например podborka-kvartir.ru): клиент не видит
# адрес 1+1 и не может перейти на сервис в обход агента. Пусто — подборки открываются на основном адресе.
PICK_DOMAIN = os.getenv("PICK_DOMAIN", "").strip().lower()
PICK_URL = f"https://{PICK_DOMAIN}" if PICK_DOMAIN else SITE_URL
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "7"))           # бесплатно после подтверждения номера (раз на номер)
# Цены (решение Артёма 13.10): неделя бесплатно (TRIAL_DAYS) → первая платная неделя 49 ₽ (SUB_FIRST, один раз) →
# дальше 99 ₽ за неделю или 399 ₽ за 30 дней (SUB_PLANS «дней:цена,дней:цена»).
# Старые цены из прежнего README («7:49,30:179,90:449») и SUB_PRICE больше не действуют — даже если остались в .env.
_LEGACY_PLANS = {"7:49,30:179", "7:49,30:179,90:449", "7:49", "7:49,30:179,90:449,"}
_plans_env = os.getenv("SUB_PLANS", "").replace(" ", "").strip()
SUB_PLANS = sorted((int(d), int(p)) for d, p in (x.split(":") for x in
                   ((_plans_env if _plans_env not in _LEGACY_PLANS else "") or "7:99,30:399").split(",") if ":" in x))
SUB_DAYS, SUB_PRICE = SUB_PLANS[0]                       # самый короткий тариф (для коротких надписей)
_first = os.getenv("SUB_FIRST", "7:49").strip()
SUB_FIRST = tuple(int(x) for x in _first.split(":")) if ":" in _first else None   # (дней, цена) — только первая оплата
# защита от выкачивания номеров: новых объектов с открытым номером в сутки (0 = без лимита). 18.10, перед выходом на
# рынок — 300 (номер открывается при каждом просмотре карточки; риелтору хватает с запасом, скрипту — нет);
# админу лимита нет, объекты партнёра (фид) в лимит не считаются
PHONE_VIEWS_PER_DAY = int(os.getenv("PHONE_VIEWS_PER_DAY", "300"))
PROMO_LOGIN_DAYS = int(os.getenv("PROMO_LOGIN_DAYS", "30"))  # доступ после входа по коду коллег
# Пробная неделя при первом входе по почте (раз на адрес) — пока Telegram в России недоступен
TRIAL_BY_EMAIL = _bool("TRIAL_BY_EMAIL", True)
# Куда слать уведомления о сбоях по почте (если пусто — первому из ADMIN_EMAILS)
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}
# Ник бота (без @) — для ссылки «Войти через Telegram»; если пусто, узнаём у Telegram сами
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")

# Почта для кодов входа (SMTP). Яндекс: smtp.yandex.ru, 465, пароль приложения
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.yandex.ru").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_FROM = os.getenv("SMTP_FROM", "").strip() or SMTP_USER

# Данные для политики, соглашения, оферты и «Контактов» (/privacy, /terms, /offer, /consent, /contacts)
def _operator_env() -> dict:
    """OPERATOR_NAME / OPERATOR_INN / CONTACT_EMAIL. Если в .env их вписали в одну строку
    («OPERATOR_NAME=Иванов OPERATOR_INN=2322… CONTACT_EMAIL=…»), разбираем, а не публикуем строку целиком."""
    vals = {k: os.getenv(k, "").strip() for k in ("OPERATOR_NAME", "OPERATOR_INN", "CONTACT_EMAIL")}
    for k in list(vals):
        parts = re.split(r"[\s,;]+(?=(?:OPERATOR_NAME|OPERATOR_INN|CONTACT_EMAIL)=)", vals[k])
        vals[k] = parts[0].strip().strip(",;")
        for extra in parts[1:]:
            key, _, val = extra.partition("=")
            if not vals.get(key):
                vals[key] = val.strip().strip(",;")
    vals["OPERATOR_INN"] = re.sub(r"\D", "", vals["OPERATOR_INN"])
    return vals


_OP = _operator_env()
OPERATOR_NAME = _OP["OPERATOR_NAME"]            # ФИО самозанятого; пусто — в документах «Администрация сервиса «1+1»»
OPERATOR_INN = _OP["OPERATOR_INN"]              # только цифры; пусто — ИНН в документах не пишем
CONTACT_EMAIL = _OP["CONTACT_EMAIL"] or ALERT_EMAIL or next(iter(sorted(ADMIN_EMAILS)), "")
OPERATOR_TEXT = (OPERATOR_NAME or "Администрация сервиса «1+1»") + (f", ИНН {OPERATOR_INN}" if OPERATOR_INN else "")


def dative_fio(fio: str) -> str:
    """«Никифоров Артём Андреевич» → «Никифорову Артёму Андреевичу» (кому? — для «даю согласие …»). Женские ФИО
    (отчество на -на) — «Ивановой Анне Сергеевне». Необычное ФИО — OPERATOR_NAME_DAT в .env."""
    parts = fio.split()
    if len(parts) != 3:
        return fio
    fam, name, pat = parts
    if pat.endswith("на"):   # женское
        fam = (fam[:-1] + "ой" if fam.endswith(("ова", "ева", "ёва", "ина", "ына")) else
               fam[:-2] + "ой" if fam.endswith(("ская", "цкая")) else fam)
        name = name[:-1] + "и" if name.endswith("ия") else name[:-1] + "е" if name.endswith(("а", "я")) else name
        pat = pat[:-1] + "е"
    else:
        fam = (fam[:-2] + "ому" if fam.endswith(("ий", "ой")) else fam + "у" if fam[-1:] not in "аяоеиыуюэ" else fam)
        name = (name[:-1] + "ю" if name.endswith(("й", "ь")) else name[:-1] + "е" if name.endswith(("а", "я")) else
                name + "у" if name[-1:] not in "аяоеиыуюэ" else name)
        pat = pat + "у" if pat.endswith("ич") else pat
    return f"{fam} {name} {pat}"


# 18.10 (приёмка п. 10): «даю … согласие» — кому? (дательный падеж)
OPERATOR_NAME_DAT = os.getenv("OPERATOR_NAME_DAT", "").strip() or (dative_fio(OPERATOR_NAME) if OPERATOR_NAME else "")
OPERATOR_TEXT_DAT = (OPERATOR_NAME_DAT or "Администрации сервиса «1+1»") + (f", ИНН {OPERATOR_INN}" if OPERATOR_INN else "")
POLICY_DATE = os.getenv("POLICY_DATE", "19.10.2026").strip()
LOADTEST_KEY = os.getenv("LOADTEST_KEY", "").strip()   # 19.10: пропуск нагрузочного теста мимо лимитов частоты (пусто — выключено)   # редакция документов (менять при правке текстов)

# Оплата ЮKassa — подготовлена, но выключена, пока PAYMENTS_ENABLED не true
PAYMENTS_ENABLED = _bool("PAYMENTS_ENABLED", False)
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET = os.getenv("YOOKASSA_SECRET", "").strip()

# ─── Кабинет агента ────────────────────────────────────────────────────────
# 24.10k (Артём): поддержка — вопросы и ошибки (звонок и MAX); подтверждение номера — свой номер VERIFY_NUMBER
SUPPORT_PHONE = os.getenv("SUPPORT_PHONE", "+79384222830").strip()
# Наш номер, на который агент пишет код из MAX или WhatsApp (номер профиля Wappi) — НЕ номер поддержки (решение Артёма 24.10)
VERIFY_NUMBER = os.getenv("VERIFY_NUMBER", "").strip()
# 17.10: подтверждение номера звонком (основной способ) — SMS.ru «Авторизация по звонку», ключ api_id из их кабинета
SMSRU_API_ID = os.getenv("SMSRU_API_ID", "").strip()
# 18.10 (Артём): номера, которые ВСЕГДА «Проверенный агент» (свои / агентства), без подтверждения; ещё — ALWAYS_VERIFIED в .env
ALWAYS_VERIFIED = sorted({"+79618571772", "+79183326033", "+79604811577"}
                         | {p.strip() for p in os.getenv("ALWAYS_VERIFIED", "").split(",") if p.strip().startswith("+7")})
# 25.10 (Артём): «объекты от этих номеров одинаковые, отправляются с разных номеров» — один агент (app/phonegroups.py).
# Правится админом на /dups («Один агент — несколько номеров»); это — только начальный список
PHONE_GROUPS = [["+79181477114", "+79604988303", "+79182651283", "+79282218147"]]
# Через какие мессенджеры принимаем код (профили Wappi): max, wa
VERIFY_CHANNELS = [c.strip() for c in os.getenv("VERIFY_CHANNELS", "max").split(",") if c.strip()]
OWN_LISTING_DAYS = int(os.getenv("OWN_LISTING_DAYS", "20"))  # сколько живёт свой объект без подтверждения
PHOTOS_DIR = os.getenv("PHOTOS_DIR", str(Path(DB_PATH).parent / "photos"))
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "12"))

# Пока только Краснодар: чаты и населённые пункты с этими словами не берём (начало слова, через запятую)
BLOCKED_PLACES = [x.strip() for x in os.getenv("BLOCKED_PLACES",
    "сочи,адлер,сириус,туапсе,анап,геленджик,новороссийск,лазаревск,хоста,дагомыс,красная поляна,"
    "абхаз,крым,ялт,севастопол,джубг,архипо,кабардинк,витязев,ейск,темрюк,тамань,ростов,москв,питер,"
    "санкт-петербург,ставропол").split(",") if x.strip()]

# Объекту с точной точкой на карте, но без района в объявлении — район по карте районов (app/districtmap.py)
DISTRICT_FROM_MAP = _bool("DISTRICT_FROM_MAP", True)

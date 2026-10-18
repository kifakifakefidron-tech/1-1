"""Веб-сервер: страница поиска, личный кабинет, админка + API.
Запуск: uvicorn app.server:app --port 8090"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import (accounts, agent, autolearn, callcheck, bankrates, brain, buyers, clients, dashboard, config, db, districtmap, geo, geocode, hooks, ingest, learning, mailer, notices, parser,
               market, notify, payments, picks, planner, region, search, settings, stats, tg, wappi, widgets, worklog)
from .rules import TYPE_LABELS

WEB = Path(__file__).resolve().parent.parent / "web"
log = logging.getLogger("server")
COOKIE = "strely_access"   # промокод для коллег (временно, потом уберём)
SESSION = "s"              # вход в личный кабинет


# ─── доступ ────────────────────────────────────────────────────────────────
def _sign(value: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def has_code(request: Request) -> bool:
    """Старый вход по промокоду (ACCESS_CODE) — для теста коллегами."""
    if not config.ACCESS_CODE:
        return False
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _sign(config.ACCESS_CODE))


def current_user(request: Request):
    return accounts.user_by_session(db.get(), request.cookies.get(SESSION))


def has_access(request: Request, user=None) -> bool:
    """Видны ли телефоны агентов: подписка/пробный период/админ — или промокод."""
    if not config.ACCESS_CODE and not config.TELEGRAM_BOT_TOKEN and not mailer.available():
        return True  # кабинет не настроен (локальная разработка) — всё открыто
    user = user if user is not None else current_user(request)
    return has_code(request) or accounts.has_access(user)


def tg_alive() -> bool:
    """Вход через Telegram показываем, только если бот недавно достучался до Telegram."""
    if not config.TELEGRAM_BOT_TOKEN:
        return False
    return time.time() - float(db.get_state(db.get(), "tg_ok") or 0) < 600


def _err(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"detail": text}, status_code=status)


def _session_response(request: Request, payload: dict, token: str) -> JSONResponse:
    resp = JSONResponse(payload)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(SESSION, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=secure)
    return resp


# Простой ограничитель частоты (на один IP) — для входа, промокодов, жалоб
_hits: dict[str, deque] = defaultdict(deque)


def _who(request: Request) -> str:
    """Кого считаем: вошедшего — по аккаунту (сессии). За Docker все посетители видны с одного внутреннего адреса,
    поэтому счёт по IP оставляем только для гостей и с мягкими лимитами — иначе один лимит на всех людей сразу."""
    tok = request.cookies.get(SESSION)
    if tok:
        return "s:" + hashlib.sha256(tok.encode()).hexdigest()[:16]
    return "ip:" + request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()


def _too_often(request: Request, key: str, limit: int, per_s: int, who: str | None = None) -> bool:
    now = time.time()
    if len(_hits) > 20000:   # память не растёт бесконечно: давно молчащих забываем (самый долгий лимит — сутки)
        for k in [k for k, q in list(_hits.items()) if not q or q[-1] < now - 86400]:
            _hits.pop(k, None)
    q = _hits[f"{key}:{who or _who(request)}"]
    while q and q[0] < now - per_s:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


_bot_name: str | None = None
_bot_tried = 0.0


def bot_username() -> str | None:
    """Ник бота. Спрашиваем у Telegram один раз (при неудаче — не чаще раза в 10 минут),
    чтобы недоступный Telegram не тормозил сайт."""
    global _bot_name, _bot_tried
    if config.TELEGRAM_BOT_USERNAME:
        return config.TELEGRAM_BOT_USERNAME
    if _bot_name is None and config.TELEGRAM_BOT_TOKEN and time.time() - _bot_tried > 600:
        _bot_tried = time.time()
        try:
            _bot_name = tg.call("getMe", timeout=5)["result"]["username"]
        except Exception:  # noqa: BLE001
            return None
    return _bot_name


MAX_BODY = 12_000_000   # самое большое — фото агента (до 3 МБ, в base64 ≈ 4 МБ) и контуры районов


async def _body(request: Request) -> dict:
    size = 0
    chunks = []
    async for chunk in request.stream():   # без Content-Length (chunked) тоже не больше MAX_BODY
        size += len(chunk)
        if size > MAX_BODY:
            return {}
        chunks.append(chunk)
    try:
        data = json.loads(b"".join(chunks) or b"null")
    except Exception:  # noqa: BLE001 — пустое или кривое тело
        return {}
    return data if isinstance(data, dict) else {}


def threaded(fn):
    """Обработчик выполняется в отдельном потоке: медленная операция с базой или почтой
    у одного посетителя не задерживает ответы всем остальным."""
    def guarded(request, body):
        try:
            return fn(request, body)
        finally:
            conn = getattr(db._local, "conn", None)
            if conn is not None and conn.in_transaction:
                try:
                    conn.rollback()   # недописанная запись (ошибка посередине) не должна держать базу
                except Exception:  # noqa: BLE001
                    pass

    async def endpoint(request: Request):
        t0 = time.perf_counter()
        if (_num(request.headers.get("content-length"), int) or 0) > MAX_BODY:   # огромный запрос — не читаем в память
            return _err("Слишком большой запрос.", 413)
        body = await _body(request) if request.method in ("POST", "PUT", "DELETE", "PATCH") else None
        resp = await run_in_threadpool(guarded, request, body)
        if request.method != "GET" and resp.status_code < 400 and request.url.path.startswith(("/api/agent", "/api/admin")):
            await run_in_threadpool(_bump_cache)
        resp.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - t0) * 1000:.0f}"
        return resp
    endpoint.__name__ = fn.__name__
    return endpoint


# ─── разбор параметров ─────────────────────────────────────────────────────
_num = search.num_param


def query_from(request: Request) -> search.Query:
    return search.query_from_params(request.query_params)


def _price_text(price, deal) -> str:
    if not price:
        return "цена не указана"
    if deal == "rent":
        return f"{price:,} ₽/мес".replace(",", " ")
    return f"{price / 1e6:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " млн ₽"


def index(request: Request, body: dict | None = None):
    # no-cache: после обновления сайта браузер сразу берёт новую страницу (и новые ?v= у стилей/скриптов)
    headers = {"Cache-Control": "no-cache"}
    oid = _num(request.query_params.get("open"), int)
    ref = re.sub(r"[^\w-]", "", request.query_params.get("ref", ""))[:20]
    html_text = (WEB / "index.html").read_text(encoding="utf-8").replace("{{SITE_URL}}", config.SITE_URL)
    if not oid:
        resp = Response(html_text, media_type="text/html; charset=utf-8", headers=headers)
        if ref and request.cookies.get("ck") == "all":   # «Приведи коллегу» — только с согласием на необязательные cookie
            resp.set_cookie("ref", ref, max_age=30 * 86400, httponly=True, samesite="lax")
        return resp
    # Ссылка на объект («Поделиться»): превью в WhatsApp/Telegram — название, цена, место, фото. Без телефонов.
    r = db.get().execute("SELECT * FROM listings WHERE id = ?", (oid,)).fetchone()
    if r is not None:
        place = " · ".join(x for x in (f"ЖК {r['complex']}" if r["complex"] else None, r["district"],
                                       f"ул. {r['street']}" if r["street"] else None) if x)
        title = f"{r['title']} — {_price_text(r['price'], r['deal'])}"
        photos = json.loads(r["photos"] or "[]")
        # фото из чатов — чужие произведения: в превью ссылки только фото партнёра (фид СТРЕЛ), иначе значок 1+1
        img = photos[0] if photos and r["source"] == "feed" else "/static/icon-192.png"
        if img.startswith("/"):
            img = config.SITE_URL + img
        esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
        url = f"{config.SITE_URL}/?open={oid}"
        desc = " ".join(parser.public_text(r["description"]).split())[:200] or place or "Объект на 1+1"
        meta = (f'<link rel="canonical" href="{esc(url)}"><meta name="robots" content="noindex, follow">'   # карточки — не в поиск
                f'<meta property="og:type" content="website">'
                f'<meta property="og:title" content="{esc(title)}">'
                f'<meta property="og:description" content="{esc(place or desc)}">'
                f'<meta property="og:image" content="{esc(img)}">'
                f'<meta property="og:url" content="{esc(url)}">'
                f'<meta property="og:site_name" content="1+1 · поиск объектов">')
        # Schema.org: предложение (цена, адрес) — для поиска Яндекса и Google; без телефонов
        ld = {"@context": "https://schema.org", "@type": "Offer", "name": r["title"], "url": url,
              "description": desc, "priceCurrency": "RUB", "image": img,
              "availability": "https://schema.org/InStock" if r["is_active"] else "https://schema.org/SoldOut",
              "itemOffered": {"@type": "Accommodation" if r["type"] in ("flat", "new", "room", "house") else "Place",
                              "name": r["title"],
                              **({"floorSize": {"@type": "QuantitativeValue", "value": r["area"], "unitCode": "MTK"}} if r["area"] else {}),
                              **({"numberOfRooms": r["rooms"]} if r["rooms"] is not None else {}),
                              "address": {"@type": "PostalAddress", "addressLocality": r["settlement"] or "Краснодар",
                                          "addressRegion": "Краснодарский край", "addressCountry": "RU",
                                          **({"streetAddress": r["street"]} if r["street"] else {})},
                              **({"geo": {"@type": "GeoCoordinates", "latitude": r["lat"], "longitude": r["lon"]}}
                                 if r["lat"] is not None else {})}}
        if r["price"] and not r["price_doubt"]:
            ld["price"] = r["price"]
        if r["source"] == "feed":   # разметка для поисковиков — только у объектов партнёра (у чатов карточки закрыты от индекса)
            meta += ('<script type="application/ld+json">' + json.dumps(ld, ensure_ascii=False).replace("</", "<\\/")
                     + "</script>")
        html_text = re.sub(r"<!--og-->.*?<!--/og-->", lambda _m: meta, html_text, count=1, flags=re.S)
        html_text = html_text.replace("<title>1+1 · поиск объектов</title>", f"<title>{esc(title)} · 1+1</title>", 1)
        html_text = re.sub(r'<meta name="description" content="[^"]*">',
                           lambda _m: f'<meta name="description" content="{esc(desc)}">', html_text, count=1)
    return Response(html_text, media_type="text/html; charset=utf-8", headers=headers)


def _prices_text() -> str:
    """«первая неделя — 49 ₽, дальше 99 ₽ за 7 дней или 399 ₽ за 30 дней» — для текстов на страницах."""
    plans = " или ".join(f"{p} ₽ за {d} дней" for d, p in sorted(config.SUB_PLANS))
    first = f"первая неделя — {config.SUB_FIRST[1]} ₽, дальше " if config.SUB_FIRST else ""
    return f"{first}{plans}"


def _price_cards() -> str:
    """Шаги цены для страницы «Как это работает»: бесплатно → первая неделя → обычные тарифы."""
    cards = [("Сначала", f"{config.TRIAL_DAYS} дней", "бесплатно", "без карты, при первом входе", "free")]
    if config.SUB_FIRST:
        cards.append(("Потом", f"{config.SUB_FIRST[1]} ₽", f"за первые {config.SUB_FIRST[0]} дней", "один раз, чтобы попробовать", "first"))
    for d, pr in sorted(config.SUB_PLANS):
        cards.append(("Дальше" if len(cards) < 3 else "Или", f"{pr} ₽", f"за {d} дней", f"≈ {pr / d:.0f} ₽ в день".replace(".", ","), "plan"))
    return "".join(f'<li class="hw-price {k}"><small>{a}</small><b>{b}</b><span>{c}</span><em>{e}</em></li>' for a, b, c, e, k in cards)


def _page(name: str) -> Response:
    """Текстовая страница с подстановкой данных оператора и условий подписки."""
    html = (WEB / name).read_text(encoding="utf-8")
    for key, val in {
        "OPERATOR_NAME": config.OPERATOR_NAME or "Администрация сервиса «1+1»", "OPERATOR_INN": config.OPERATOR_INN or "—",
        "OPERATOR_TEXT": config.OPERATOR_TEXT,
        "CONTACT_EMAIL": config.CONTACT_EMAIL or "почту из раздела «Контакты»", "POLICY_DATE": config.POLICY_DATE,
        "SITE_URL": config.SITE_URL, "TRIAL_DAYS": config.TRIAL_DAYS, "SUB_PRICE": config.SUB_PRICE, "PRICES": _prices_text(),
        "SUB_DAYS": config.SUB_DAYS, "STALE_DAYS": config.STALE_DAYS, "ARCHIVE_DAYS": config.ARCHIVE_DAYS, "KEEP_DAYS": config.KEEP_DAYS,
        "PRICE_CARDS": _price_cards(),
    }.items():
        html = html.replace("{{" + key + "}}", str(val))
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def how_page(request: Request, body: dict | None = None):
    return _page("how.html")


def privacy_page(request: Request, body: dict | None = None):
    return _page("privacy.html")


def offer_page(request: Request, body: dict | None = None):
    return _page("offer.html")


def consent_page(request: Request, body: dict | None = None):
    return _page("consent.html")


def contacts_page(request: Request, body: dict | None = None):
    return _page("contacts.html")


def terms_page(request: Request, body: dict | None = None):
    return _page("terms.html")


def admin_page(request: Request, body: dict | None = None):
    return FileResponse(WEB / "admin.html", headers={"Cache-Control": "no-cache"})


def api_meta(request: Request, body: dict | None = None):
    user = current_user(request)
    learning.rules(db.get())   # подтянуть районы, добавленные админом (раз в минуту)
    return JSONResponse({
        "title": config.SITE_TITLE,
        "types": TYPE_LABELS,
        "districts": geo.all_district_names(),
        "access": has_access(request, user),
        "access_required": bool(config.ACCESS_CODE),
        "promo_login": bool(config.ACCESS_CODE),
        "tg_login": tg_alive(),
        "email_login": mailer.available(),
        "phone_login": callcheck.enabled(),   # 18.10: вход по номеру звонком (SMS.ru)
        "payments": payments.available(),
        "price": config.SUB_PRICE, "period_days": config.SUB_DAYS, "trial_days": config.TRIAL_DAYS,
        "prices": _prices_text(), "price_first": config.SUB_FIRST[1] if config.SUB_FIRST else None,
        "bot": bot_username(),
        "me": accounts.me(db.get(), user),
    })


# Короткий кэш одинаковых запросов (поиск, фильтры, карта одинаковы для всех посетителей):
# при наплыве людей одни и те же популярные выдачи не считаются заново каждый раз
_cache: dict[str, tuple[float, bytes]] = {}
_cache_lock = threading.Lock()
CACHE_S = 15


_computing: dict[str, threading.Lock] = {}


def _json_or_none(v: str | None) -> dict | None:
    """Отчёт из state: только словарь (старые отчёты бывали просто числом)."""
    try:
        d = json.loads(v or "null")
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _cached(request: Request, compute, ttl: int = CACHE_S) -> Response:
    # cache_ver меняется при правках агентов/админа — второй процесс сайта тоже сразу видит новое
    key = f"{db.get_state(db.get(), 'cache_ver', '0')}:{request.url.path}?{request.url.query}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return Response(hit[1], media_type="application/json")
    # Один и тот же ответ считаем один раз: пришли сто человек разом — остальные ждут готового (не 100 расчётов)
    with _cache_lock:
        lock = _computing.setdefault(key, threading.Lock())
    with lock:
        hit = _cache.get(key)
        now = time.time()
        if hit and now - hit[0] < ttl:
            return Response(hit[1], media_type="application/json")
        body = json.dumps(compute(), ensure_ascii=False).encode()
        with _cache_lock:
            if len(_cache) > 3000:
                for k in [k for k, (t, _) in _cache.items() if now - t >= 120] or list(_cache)[:1500]:
                    _cache.pop(k, None)
                    _computing.pop(k, None)
            _cache[key] = (now, body)
    return Response(body, media_type="application/json")


def _bump_cache() -> None:
    try:
        db.set_state(db.get(), "cache_ver", str(time.time()))
    except Exception:  # noqa: BLE001 — база занята: кэш и так обновится через CACHE_S
        pass


def _hider(request: Request) -> int | None:
    """Кто смотрит, если он что-то скрыл для себя (тогда ответ свой, без общего кэша)."""
    tok = request.cookies.get(SESSION)
    if not tok:
        return None
    user = current_user(request)
    if user and db.get().execute("SELECT 1 FROM hidden_listings WHERE user_id = ? LIMIT 1", (user["id"],)).fetchone():
        return user["id"]
    return None


def api_listings(request: Request, body: dict | None = None):
    # В списке телефонов нет никогда — только в карточке, по одному объекту
    hider = _hider(request)

    def compute():
        now = int(time.time())
        qr = query_from(request)
        qr.hide_user = hider
        res = search.search(db.get(), qr, now, False)
        hooks.annotate(db.get(), res["items"])   # «ниже рынка на N %»
        if qr.q:   # как поняли фразу из строки поиска — чипами над выдачей
            ph = search.parse_phrase(qr.q, qr.deal)
            res["understood"] = {"chips": ph["chips"], "text": ph["text"], "deal": ph["deal"]}
        res["now"] = now  # от этого момента страница считает «новые объекты»
        return res
    if request.query_params.get("since") or hider:
        return JSONResponse(compute())
    return _cached(request, compute)


def api_hidden(request: Request, body: dict | None = None):
    """Скрыть объект только для себя: POST /api/hidden {id, hide, reason}; GET — список скрытых (чтобы вернуть)."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "POST":
        lid = _num(body.get("id"), int) or 0
        if body.get("hide", True):
            if not conn.execute("SELECT 1 FROM listings WHERE id = ?", (lid,)).fetchone():
                return _err("не найдено", 404)
            reason = body.get("reason") if body.get("reason") in ("sold", "no_coop", "not_fit", "other") else "other"
            conn.execute("INSERT OR REPLACE INTO hidden_listings (user_id, listing_id, reason, ts) VALUES (?,?,?,?)",
                         (user["id"], lid, reason, int(time.time())))
        else:
            conn.execute("DELETE FROM hidden_listings WHERE user_id = ? AND listing_id = ?", (user["id"], lid))
        conn.commit()
        return JSONResponse({"ok": True})
    rows = conn.execute(f"""SELECT l.*, h.reason AS h_reason, h.ts AS h_ts, {search.VERIFIED} AS verified
                            FROM hidden_listings h JOIN listings l ON l.id = h.listing_id
                            WHERE h.user_id = ? ORDER BY h.ts DESC LIMIT 500""", (user["id"],)).fetchall()
    return JSONResponse({"items": [{**search.row_to_item(r, False), "reason": r["h_reason"], "hidden_at": r["h_ts"]} for r in rows]})


def api_listing(request: Request, body: dict | None = None):
    conn = db.get()
    lid = int(request.path_params["id"])
    user = current_user(request)
    access = has_access(request, user)
    d = search.listing_detail(conn, lid, access, bool(user and user["is_admin"]))
    if not d:
        return _err("не найдено", 404)
    optout = accounts.optout_set(conn)
    if "phones_masked" in d:
        d["phones_masked"] = [m for m, p in zip(d["phones_masked"], _raw_phones(conn, lid)) if p not in optout]
    if access:
        phones, why = accounts.open_phones(conn, user, lid, d.get("phones") or [])
        d["phones"] = phones or []
        d["phones_limit"] = why == "limit"
        if d.get("fragment"):
            d["fragment"] = parser.strip_phones(d["fragment"])
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if access and phones:   # кто прислал объявление в чат, если это другой номер (не тот, что в тексте)
        roles = json.loads(row["phone_roles"] or "{}")
        d["sender_phone"] = next((p for p, role in roles.items() if role == "sender" and p not in d["phones"]), None)
    # текст объявления старее цены (цена обновилась позже текста) — честно говорим об этом в карточке
    # акт 14.10, Б2: в тексте другая цена — плашка всегда (не только когда цена обновилась позже текста)
    if not row["price_fixed"]:
        from .p0fix import price_gap
        tp = price_gap(row["price"], row["description"], row["deal"])
        if tp:
            d["text_price"] = {"price": tp, "ts": row["description_ts"] or row["last_seen"]}
    d["can_edit"] = bool(user) and agent.can_edit(conn, user, row)[0]
    d["market"] = hooks.market_for(conn, d) if not row["price_doubt"] else None
    d["same"] = hooks.same_elsewhere(conn, row)
    d["note"] = hooks.get_note(conn, user["id"], lid) if user else None
    d["status"] = hooks.get_status(conn, user["id"], lid) if user else None
    d["plans"] = planner.for_listing(conn, user["id"], lid) if user else []
    d["work"] = worklog.for_listing(conn, user["id"], lid) if user else []
    d["pairs"] = clients.for_listing(conn, user["id"], lid) if user else []
    d["work_ctx"] = worklog.context(conn, user["id"], lid) if user else None
    # «Покупатели под объект» — только админу (решение Артёма 11.10)
    # акт 14.10, Б4: «покупатели из чатов» подходили ко всему (реклама, чужие продажи) — теперь ваши клиенты из CRM
    d["fit_clients"] = clients.fits_listing(conn, user["id"], lid) if user else []
    d["favorite"] = bool(user and conn.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND listing_id = ?", (user["id"], lid)).fetchone())
    return JSONResponse(d)


def _raw_phones(conn, lid: int) -> list[str]:
    row = conn.execute("SELECT phones FROM listings WHERE id = ?", (lid,)).fetchone()
    return json.loads(row["phones"] or "[]") if row else []


def api_facets(request: Request, body: dict | None = None):
    return _cached(request, lambda: search.facets(db.get(), query_from(request), int(time.time())), ttl=60)


def api_map(request: Request, body: dict | None = None):
    hider = _hider(request)
    if hider:
        qr = query_from(request)
        qr.hide_user = hider
        return JSONResponse(search.map_points(db.get(), qr, int(time.time())))
    return _cached(request, lambda: search.map_points(db.get(), query_from(request), int(time.time())), ttl=45)


def api_districts_map(request: Request, body: dict | None = None):
    """Контуры районов для слоя на карте поиска (для всех; © OpenStreetMap и свои)."""
    r = JSONResponse({"districts": districtmap.public_polygons(db.get())})
    r.headers["Cache-Control"] = "public, max-age=600"
    return r


def api_stats(request: Request, body: dict | None = None):
    return _cached(request, _stats, ttl=120)   # цифры первого экрана — каждому посетителю, считаем раз в 2 минуты


def _stats() -> dict:
    conn = db.get()
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    day = int(time.time()) - 86400
    now = int(time.time())

    def visible(deal: str, **kw) -> int:   # считаем ровно так же, как список на главной (те же правила показа)
        where, params = search._where(search.Query(deal=deal, **kw), now)
        return conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {where}", params).fetchone()[0]

    sale, rent = visible("sale"), visible("rent")
    return ({
        "active": sale + rent, "sale": sale, "rent": rent,
        "new_24h": visible("sale", new_days=1) + visible("rent", new_days=1),
        "messages_24h": one("SELECT COUNT(*) FROM messages WHERE ts >= ?", day),
        "duplicates_merged": one("SELECT COUNT(*) FROM listing_events WHERE match != 'new'"),
        "districts": one("SELECT COUNT(DISTINCT district) FROM listings WHERE is_active=1 AND district IS NOT NULL"),
    })


# ─── промокод для коллег (временный) ───────────────────────────────────────
CONSENT_ERR = "Отметьте согласие на обработку персональных данных — без него вход невозможен."


def _no_consent(body: dict | None) -> bool:
    """152-ФЗ (с 01.09.2025): согласие — отдельный документ и отдельная галочка; без неё не входим."""
    return not (body or {}).get("consent")


def _save_consent(conn, user_id: int) -> None:
    conn.execute("UPDATE users SET consent_at = COALESCE(consent_at, ?), consent_ver = ? WHERE id = ?",
                 (int(time.time()), config.POLICY_DATE, user_id))
    conn.commit()


def api_login(request: Request, body: dict | None = None):
    if _too_often(request, "promo", 60, 600):
        return _err("Слишком много попыток — подождите 10 минут.", 429)
    body = body or {}
    if _no_consent(body):
        return _err(CONSENT_ERR)
    code = str(body.get("code", "")).strip().encode()  # bytes: код может быть по-русски
    if config.ACCESS_CODE and hmac.compare_digest(code, config.ACCESS_CODE.encode()):
        conn = db.get()
        user = current_user(request) or accounts.create_promo_user(conn)
        _save_consent(conn, user["id"])
        token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
        resp = _session_response(request, {"ok": True, "me": accounts.me(conn, accounts.get_user(conn, user["id"]))},
                                 token)
        resp.set_cookie(COOKIE, _sign(config.ACCESS_CODE), max_age=180 * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
        return resp
    return JSONResponse({"ok": False, "detail": "Код не подошёл. Проверьте и введите ещё раз."}, status_code=403)


# ─── вход через Telegram ───────────────────────────────────────────────────
def api_tg_start(request: Request, body: dict | None = None):
    if not tg_alive() or not bot_username():
        return _err("Вход через Telegram пока не настроен.", 503)
    if _too_often(request, "tg", 20, 600):
        return _err("Слишком много попыток — подождите немного.", 429)
    body = body or {}
    purpose = "link" if body.get("link") else "login"
    if purpose == "login" and _no_consent(body):
        return _err(CONSENT_ERR)
    user = current_user(request)
    if purpose == "link" and user is None:
        return _err("Сначала войдите.", 401)
    token = accounts.new_tg_token(db.get(), purpose, user["id"] if purpose == "link" else None)
    return JSONResponse({"token": token, "url": f"https://t.me/{_bot_name or config.TELEGRAM_BOT_USERNAME}?start={purpose}_{token}"})


def api_tg_status(request: Request, body: dict | None = None):
    conn = db.get()
    row = accounts.tg_token(conn, request.query_params.get("t", ""))
    if row is None:
        return JSONResponse({"status": "expired"})
    if row["status"] == "ok" and row["result_user"]:
        conn.execute("UPDATE tg_tokens SET status = 'used' WHERE token = ?", (row["token"],))
        conn.commit()
        user = accounts.get_user(conn, row["result_user"])
        _save_consent(conn, user["id"])   # вход через Telegram начинался с галочки согласия (api_tg_start)
        token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
        return _session_response(request, {"status": "ok", "me": accounts.me(conn, user)}, token)
    return JSONResponse({"status": row["status"]})


# ─── вход по почте ─────────────────────────────────────────────────────────
def api_email_start(request: Request, body: dict | None = None):
    if not mailer.available():
        return _err("Вход по почте пока не настроен.", 503)
    body = body or {}
    if _no_consent(body):
        return _err(CONSENT_ERR)
    email = str(body.get("email", ""))[:200]
    # лимит — на адрес почты (5 кодов в час), общий по сайту — только от явного перебора
    if _too_often(request, "email", 5, 3600, who=email.strip().lower()) or _too_often(request, "email_all", 300, 3600):
        return _err("Слишком много запросов кода — попробуйте через час.", 429)
    code, err = accounts.new_email_code(db.get(), email)
    if err:
        return _err(err)
    # Отправка письма — в фоне: пока почта отвечает, сайт для остальных не подвисает
    if not mailer.send_code(email.strip().lower(), code):
        return _err("Письмо не отправилось. Попробуйте ещё раз через минуту или войдите через Telegram.", 502)
    return JSONResponse({"ok": True})


def api_email_verify(request: Request, body: dict | None = None):
    body = body or {}
    conn = db.get()
    email = str(body.get("email", ""))[:200]
    if _too_often(request, "email_verify", 20, 600, who=email.strip().lower()):
        return _err("Слишком много попыток — подождите 10 минут.", 429)
    if _no_consent(body):
        return _err(CONSENT_ERR)
    ok, err = accounts.check_email_code(conn, email, str(body.get("code", ""))[:12])
    if not ok:
        return _err(err)
    user = accounts.upsert_email_user(conn, email)
    if user["blocked"]:
        return _err("Аккаунт заблокирован.", 403)
    _save_consent(conn, user["id"])
    if accounts.attach_referrer(conn, user["id"], request.cookies.get("ref") or str(body.get("ref") or "")):
        user = accounts.get_user(conn, user["id"])
    token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
    return _session_response(request, {"ok": True, "me": accounts.me(conn, user)}, token)


def api_phone_login(request: Request, body: dict | None = None):
    """18.10: вход по номеру. POST {phone, consent} — начать (звонок со своего номера); GET ?tid= — статус,
    позвонил → входим (кука сессии), номер сразу подтверждён («Проверенный агент»)."""
    conn = db.get()
    if not callcheck.enabled():
        return _err("Вход по номеру ещё не подключён — войдите по почте.", 503)
    if request.method == "POST":
        body = body or {}
        if _no_consent(body):
            return _err(CONSENT_ERR)
        if _too_often(request, "phone_login", 10, 3600, who=str(body.get("phone", ""))[:40]):
            return _err("Слишком много попыток — подождите час или войдите по почте.", 429)
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else ""))[:45]
        res, why = agent.start_login_call(conn, str(body.get("phone", ""))[:40], ip)
        return JSONResponse(res) if res else _err(why)
    db.flush(conn)
    st, phone = agent.login_call_status(conn, request.query_params.get("tid", "")[:60])
    if st != "ok":
        return JSONResponse({"status": st})
    user = accounts.upsert_phone_user(conn, phone)
    if user["blocked"]:
        return _err("Аккаунт заблокирован.", 403)
    _save_consent(conn, user["id"])
    if accounts.attach_referrer(conn, user["id"], request.cookies.get("ref") or ""):
        user = accounts.get_user(conn, user["id"])
    accounts.reward_referrer(conn, user["id"])   # номер подтверждён звонком — пригласившему бонус
    token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
    return _session_response(request, {"status": "ok", "me": accounts.me(conn, user)}, token)


def api_account_email(request: Request, body: dict | None = None):
    """18.10: привязать почту к аккаунту (вошли по номеру). {email} — прислать код; {email, code} — проверить и привязать.
    Почта уже у другого аккаунта (входили по почте раньше) — это тот же человек: аккаунты объединяются."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    email = str(body.get("email", "")).strip().lower()[:200]
    if body.get("code"):
        if _too_often(request, "email_verify", 20, 600, who=email):
            return _err("Слишком много попыток — подождите 10 минут.", 429)
        ok, why = accounts.check_email_code(conn, email, str(body.get("code"))[:12])
        if not ok:
            return _err(why)
        uid, merged = accounts.link_email(conn, user["id"], email)
        return JSONResponse({"ok": True, "merged": merged, "me": accounts.me(conn, accounts.get_user(conn, uid))})
    if not mailer.available():
        return _err("Почта пока не настроена.", 503)
    if _too_often(request, "email", 5, 3600, who=email):
        return _err("Слишком много запросов кода — попробуйте через час.", 429)
    code, why = accounts.new_email_code(conn, email)
    if why:
        return _err(why)
    db.flush(conn)
    if not mailer.send_code(email, code):
        return _err("Письмо не отправилось — попробуйте через минуту.", 502)
    return JSONResponse({"ok": True})


def api_logout(request: Request, body: dict | None = None):
    accounts.end_session(db.get(), request.cookies.get(SESSION))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION)
    resp.delete_cookie(COOKIE)  # и вход по коду коллег
    return resp


# ─── кабинет ───────────────────────────────────────────────────────────────
def _need_user(request: Request):
    user = current_user(request)
    return user, (None if user is not None else _err("Нужно войти.", 401))


def api_me(request: Request, body: dict | None = None):
    conn = db.get()
    user = current_user(request)
    me = accounts.me(conn, user)
    if me:
        me["counts"] = _counts(conn, user["id"])   # для меню слева и кабинета: что ждёт внимания
        me["photo"] = settings.get(conn, user["id"]).get("pick_photo") or ""   # 17.10: фото из настроек — и фото аккаунта
    resp = JSONResponse({"me": me})
    tok = request.cookies.get(SESSION)
    if user and tok:   # продлить куку входа: заходите хотя бы раз в полгода — входить заново не нужно
        resp.set_cookie(SESSION, tok, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return resp


def _counts(conn: sqlite3.Connection, uid: int) -> dict:
    """Счётчики-подсказки: дела планера на сегодня (не сделанные), просроченные, объекты в работе, клиенты «пора связаться»."""
    now = int(time.time())
    msk = time.gmtime(now + 3 * 3600)
    day0 = now - (msk.tm_hour * 3600 + msk.tm_min * 60 + msk.tm_sec)
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0] or 0  # noqa: E731
    return {
        "plan_today": one("SELECT COUNT(*) FROM planner_events WHERE user_id = ? AND done = 0 AND ts >= ? AND ts < ?", uid, day0, day0 + 86400),
        "plan_overdue": one("SELECT COUNT(*) FROM planner_events WHERE user_id = ? AND done = 0 AND ts < ?", uid, day0),
        "work": one("SELECT COUNT(*) FROM notes WHERE user_id = ? AND status IN ('call', 'show', 'think')", uid),
        "favorites": one("SELECT COUNT(*) FROM favorites WHERE user_id = ?", uid),
        "clients_due": one("""SELECT COUNT(*) FROM clients WHERE user_id = ? AND archived = 0 AND next_ts IS NOT NULL
                              AND next_ts < ? AND stage NOT IN ('deal', 'lost')""", uid, day0 + 86400),
        "clients": one("SELECT COUNT(*) FROM clients WHERE user_id = ? AND archived = 0 AND stage NOT IN ('deal', 'lost')", uid),
    }


def api_favorites(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    # Избранное и объекты «в работе» (со статусом) — вместе: это рабочий список агента
    rows = db.get().execute(f"""
        SELECT l.*, n.status AS crm_status, n.text AS crm_note, COALESCE(n.ts, f.created) AS crm_ts,
               f.created AS fav_at, f.price_at AS fav_price, {search.VERIFIED} AS verified
        FROM listings l
        LEFT JOIN favorites f ON f.listing_id = l.id AND f.user_id = ?
        LEFT JOIN notes n ON n.listing_id = l.id AND n.user_id = ?
        WHERE f.user_id IS NOT NULL OR n.status IS NOT NULL
        ORDER BY crm_ts DESC""", (user["id"], user["id"])).fetchall()
    items = []
    pairs: dict[int, list] = {}
    for p in db.get().execute("""SELECT i.listing_id, c.id, c.name, i.status, i.reaction FROM client_items i
                                 JOIN clients c ON c.id = i.client_id WHERE c.user_id = ?""", (user["id"],)):
        pairs.setdefault(p["listing_id"], []).append({"id": p["id"], "name": p["name"], "status": p["status"],
                                                       "label": clients.PAIR.get(p["status"], ""), "reaction": p["reaction"]})
    for r in rows:
        it = search.row_to_item(r, False)
        it.update(status=r["crm_status"], note=r["crm_note"] or "", favorite=r["fav_at"] is not None,
                  touched=r["crm_ts"], fav_price=r["fav_price"], clients=pairs.get(r["id"], []))
        items.append(it)
    return JSONResponse({"items": items})


def api_favorite_toggle(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    lid = int(request.path_params["id"])
    if not conn.execute("SELECT 1 FROM listings WHERE id = ?", (lid,)).fetchone():
        return _err("не найдено", 404)
    return JSONResponse({"favorite": accounts.toggle_favorite(conn, user["id"], lid)})


def api_promo(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    if _too_often(request, "promo_redeem", 10, 600):
        return _err("Слишком много попыток — подождите 10 минут.", 429)
    body = body or {}
    conn = db.get()
    ok, text = accounts.redeem_promo(conn, user["id"], str(body.get("code", ""))[:40])
    return JSONResponse({"ok": ok, "text": text, "me": accounts.me(conn, accounts.get_user(conn, user["id"]))},
                        status_code=200 if ok else 400)


def api_complaint(request: Request, body: dict | None = None):
    if _too_often(request, "complaint", 10, 3600):
        return _err("Слишком много жалоб — попробуйте позже.", 429)
    body = body or {}
    user = current_user(request)
    conn = db.get()
    lid = _num(body.get("listing_id"), int)
    text = str(body.get("text", ""))[:1000]
    if body.get("photos") and lid:   # «фото не от этого объекта» — только фото самого объекта
        row = conn.execute("SELECT photos FROM listings WHERE id = ?", (lid,)).fetchone()
        own = set(json.loads(row["photos"] or "[]")) if row else set()
        picked = [str(u) for u in body["photos"] if str(u) in own][:12]
        if picked:
            text = (text + "\n" if text else "") + "\n".join(f"Фото: {u}" for u in picked)
    conn.execute("INSERT INTO complaints (listing_id, user_id, reason, text, ts) VALUES (?,?,?,?,?)",
                 (lid, user["id"] if user is not None else None,
                  str(body.get("reason", ""))[:40], text[:3000], int(time.time())))
    conn.commit()
    return JSONResponse({"ok": True})


# ─── оплата (подготовлено, выключено) ──────────────────────────────────────
def api_pay(request: Request, body: dict | None = None):
    """GET — тарифы и последний платёж (страница /pay); POST {days} — создать платёж и вернуть ссылку ЮKassa."""
    user, err = _need_user(request)
    if err:
        return err
    if request.method == "GET":
        return JSONResponse({"plans": payments.plans(db.get(), user["id"] if user else None), "enabled": payments.available(),
                             "last": payments.last_status(db.get(), user["id"]) if request.query_params.get("check") else None})
    if not payments.available():
        return _err("Оплата скоро появится.", 503)
    return JSONResponse({"url": payments.create(db.get(), user["id"], _num((body or {}).get("days"), int))})


def pay_page(request: Request, body: dict | None = None):
    return _page("pay.html")


def api_yookassa(request: Request, body: dict | None = None):
    """Уведомление ЮKassa. Статус перепроверяется запросом к API ЮKassa."""
    if not payments.available():
        return Response(status_code=404)
    body = body or {}
    pid = str((body.get("object") or {}).get("id", ""))[:64]
    if pid:
        payments.confirm(db.get(), pid)
    return Response(status_code=200)


# ─── админка ───────────────────────────────────────────────────────────────
def _need_admin(request: Request):
    user = current_user(request)
    if user is None or not user["is_admin"]:
        return None, _err("Только для администратора.", 403)
    return user, None


ONLINE_S = 150   # «на сайте сейчас» — был сигнал за последние 2,5 минуты


def api_ping(request: Request, body: dict | None = None):
    """Сигнал «я на сайте» раз в минуту с открытой страницы (для админки: кто сейчас на сайте). Ничего лишнего не храним:
    ключ — хеш сессии или случайный номер браузера, страница и телефон/компьютер; старше суток — удаляется."""
    body = body or {}
    tok = request.cookies.get(SESSION)
    user = current_user(request) if tok else None
    raw = ("s:" + tok) if tok else ("g:" + str(body.get("g") or "")[:40])
    if len(raw) < 6:
        return JSONResponse({"ok": True})
    key = hashlib.sha256(raw.encode()).hexdigest()[:24]
    path = str(body.get("path") or "/")[:60].split("?")[0]
    mobile = 1 if re.search(r"Mobi|Android|iPhone", request.headers.get("user-agent", "")) else 0
    now = int(time.time())
    conn = db.get()
    try:
        conn.execute("INSERT OR REPLACE INTO presence (key, user_id, ts, path, mobile) VALUES (?,?,?,?,?)",
                     (key, user["id"] if user else None, now, path, mobile))
        if now % 50 == 0:
            conn.execute("DELETE FROM presence WHERE ts < ?", (now - 86400,))
        conn.commit()
    except sqlite3.OperationalError:   # база занята — не беда, отметимся через минуту
        conn.rollback()
    return JSONResponse({"ok": True})


def _online(conn: sqlite3.Connection) -> dict:
    now = int(time.time())
    rows = conn.execute("""SELECT p.user_id, p.ts, p.path, p.mobile, u.email, u.name, u.is_admin FROM presence p
                           LEFT JOIN users u ON u.id = p.user_id WHERE p.ts >= ? ORDER BY p.ts DESC""", (now - ONLINE_S,)).fetchall()
    users = [r for r in rows if r["user_id"]]
    seen, people = set(), []
    for r in users:
        if r["user_id"] in seen:
            continue
        seen.add(r["user_id"])
        people.append({"name": r["name"] or (r["email"] or "").split("@")[0] or f"#{r['user_id']}", "email": r["email"],
                       "path": r["path"], "mobile": bool(r["mobile"]), "ago": now - r["ts"], "admin": bool(r["is_admin"])})
    day = conn.execute("SELECT COUNT(DISTINCT COALESCE(user_id, key)) FROM presence WHERE ts >= ?", (now - 86400,)).fetchone()[0]
    hour = conn.execute("SELECT COUNT(DISTINCT COALESCE(user_id, key)) FROM presence WHERE ts >= ?", (now - 3600,)).fetchone()[0]
    return {"now": len(seen) + sum(1 for r in rows if not r["user_id"]), "users": len(seen),
            "guests": sum(1 for r in rows if not r["user_id"]), "mobile": sum(1 for r in rows if r["mobile"]),
            "hour": hour, "day": day, "people": people[:50],
            "pages": [dict(r) for r in conn.execute("""SELECT path, COUNT(*) AS n FROM presence WHERE ts >= ?
                                                       GROUP BY path ORDER BY n DESC LIMIT 8""", (now - ONLINE_S,))]}


def api_admin_online(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    return JSONResponse(_online(db.get()))


def api_admin_billing(request: Request, body: dict | None = None):
    """18.10: «Оплатил» — {key, period?} → следующее напоминание через период."""
    _, err = _need_admin(request)
    if err:
        return err
    from . import billing
    body = body or {}
    if not billing.mark_paid(db.get(), str(body.get("key", "")), _num(body.get("period"), int)):
        return _err("Нет такого сервиса.")
    return JSONResponse({"ok": True, "items": billing.status(db.get())})


def api_admin_mailtest(request: Request, body: dict | None = None):
    """Админ проверяет почту: письмо с тестовым кодом на любой адрес — и сразу видно, что ответил почтовый сервер."""
    _, err = _need_admin(request)
    if err:
        return err
    to = str((body or {}).get("to") or "").strip()[:120]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
        return _err("Впишите адрес почты.")
    if not mailer.available():
        return JSONResponse({"ok": False, "error": "Почта не настроена: в .env нет SMTP_USER / SMTP_PASSWORD."})
    import secrets
    ok = mailer.send_code(to, f"{secrets.randbelow(900000) + 100000}")   # «123456» фильтры считают тестовым спамом
    return JSONResponse({"ok": ok, "sender": mailer.sender(), "host": config.SMTP_HOST,
                         "error": None if ok else mailer.last_error.get("error")})


def api_admin_overview(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    now = int(time.time())
    return JSONResponse({
        "listings": one(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE}"),
        "life": __import__("app.lifecheck", fromlist=["report"]).report(conn, now),
        "own_match": json.loads(db.get_state(conn, "own_match_report") or "null"),
        "billing": __import__("app.billing", fromlist=["status"]).status(conn, now),
        "rules_fix": json.loads(db.get_state(conn, "rules_fix_report") or "null"),
        "rent_leak": json.loads(db.get_state(conn, "rent_leak_report") or "null"),
        "media_strict": _json_or_none(db.get_state(conn, "media_strict_report")),
        "llm_on": __import__("app.llm", fromlist=["available"]).available(),
        # 17.10: фильтр «Ремонт» — сколько объектов разобрано (квартиры и дома на сайте)
        "repair": {k: one(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND l.type IN ('flat','new','house','room') AND {cond}")
                   for k, cond in (("yes", "l.repair = 'yes'"), ("no", "l.repair = 'no'"), ("none", "l.repair IS NULL"))},
        "listings_no_place": one("""SELECT COUNT(*) FROM listings WHERE is_active=1 AND district IS NULL AND complex IS NULL
                                    AND street IS NULL AND settlement IS NULL"""),
        "listings_no_district": one(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND l.district IS NULL"),
        "listings_no_map": one(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND l.lat IS NULL"),
        "listings_feed": one("SELECT COUNT(*) FROM listings WHERE is_active=1 AND source='feed'"),
        "queue": one("SELECT COUNT(*) FROM messages WHERE status='new'"),
        "users": one("SELECT COUNT(*) FROM users"),
        "users_access": one("SELECT COUNT(*) FROM users WHERE MAX(trial_until, paid_until) > ? OR is_admin=1", now),
        "users_paid": one("SELECT COUNT(*) FROM users WHERE paid_until > ?", now),
        "views_24h": one("SELECT COUNT(*) FROM phone_views WHERE ts >= ?", now - 86400),
        "complaints_new": one("SELECT COUNT(*) FROM complaints WHERE status='new'"),
        "optouts": one("SELECT COUNT(*) FROM optout_phones"),
        "wappi_enabled": config.WAPPI_ENABLED, "payments": payments.available(),
        "price": config.SUB_PRICE, "period_days": config.SUB_DAYS,
        "p0_report": json.loads(db.get_state(conn, "p0_report") or "null"),
        "cx_report": json.loads(db.get_state(conn, "cx_norm_report") or "null"),
        "price_report": json.loads(db.get_state(conn, "price_fix_report") or "null"),
        "geo_cleanup": json.loads(db.get_state(conn, "geo_cleanup_report") or "null"),
        "geo_daily": json.loads(db.get_state(conn, "geo_daily_report") or "null"),
        "feed_synced": int(db.get_state(conn, "feed_synced") or 0),
    })


def api_admin_stats(request: Request, body: dict | None = None):
    """Подробная статистика: добавлено/снято по дням, причины, чаты, качество разбора, пользователи."""
    _, err = _need_admin(request)
    if err:
        return err
    return JSONResponse(stats.collect(db.get()))


def api_admin_users(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    q = f"%{request.query_params.get('q', '').strip().lower()}%"
    rows = db.get().execute(
        """SELECT * FROM users WHERE lower(COALESCE(tg_username,'') || ' ' || COALESCE(email,'') || ' ' ||
               COALESCE(phone,'') || ' ' || COALESCE(name,'')) LIKE ? ORDER BY id DESC LIMIT 200""", (q,)).fetchall()
    return JSONResponse({"items": [accounts.user_public_row(r) for r in rows]})


def api_admin_user_action(request: Request, body: dict | None = None):
    admin, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    uid = int(request.path_params["id"])
    if accounts.get_user(conn, uid) is None:
        return _err("не найдено", 404)
    body = body or {}
    action = body.get("action")
    if action == "extend":
        accounts.extend(conn, uid, max(1, min(_num(body.get("days"), int) or 7, 3650)))
    elif action == "reset":
        conn.execute("UPDATE users SET paid_until = 0, trial_until = 0 WHERE id = ?", (uid,))
    elif action in ("block", "unblock"):
        if uid == admin["id"]:
            return _err("Нельзя заблокировать себя.")
        conn.execute("UPDATE users SET blocked = ? WHERE id = ?", (1 if action == "block" else 0, uid))
        if action == "block":
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    elif action in ("admin", "unadmin"):
        if uid == admin["id"]:
            return _err("Нельзя снять права с себя.")
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if action == "admin" else 0, uid))
    else:
        return _err("Неизвестное действие.")
    conn.commit()
    return JSONResponse({"ok": True, "user": accounts.user_public_row(accounts.get_user(conn, uid))})


def api_admin_promos(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        body = body or {}
        try:
            code = accounts.create_promo(conn, max(1, min(_num(body.get("days"), int) or 7, 3650)),
                                         max(1, min(_num(body.get("max_uses"), int) or 1, 100000)),
                                         str(body.get("note", ""))[:200], (str(body.get("code") or "")[:40] or None))
        except Exception:  # noqa: BLE001 — например, такой код уже есть
            return _err("Не получилось создать промокод (возможно, такой уже есть).")
        return JSONResponse({"code": code})
    if request.method == "DELETE":
        conn.execute("DELETE FROM promo_codes WHERE code = ?", (request.query_params.get("code", "").upper(),))
        conn.commit()
        return JSONResponse({"ok": True})
    rows = conn.execute("SELECT * FROM promo_codes ORDER BY created DESC").fetchall()
    return JSONResponse({"items": [dict(r) for r in rows]})


def api_admin_optouts(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        body = body or {}
        if body.get("remove"):
            conn.execute("DELETE FROM optout_phones WHERE phone = ?", (str(body["remove"]),))
            conn.commit()
        elif not accounts.add_optout(conn, str(body.get("phone", "")), "admin"):
            return _err("Не похоже на номер телефона.")
        return JSONResponse({"ok": True})
    rows = conn.execute("SELECT * FROM optout_phones ORDER BY ts DESC").fetchall()
    return JSONResponse({"items": [dict(r) for r in rows]})


def api_admin_complaints(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        body = body or {}
        cid = _num(body.get("id"), int)
        conn.execute("UPDATE complaints SET status = ? WHERE id = ?", (str(body.get("status", "done"))[:20], cid))
        if body.get("hide_listing"):
            conn.execute("UPDATE listings SET is_active = 0 WHERE id = (SELECT listing_id FROM complaints WHERE id = ?)",
                         (cid,))
        conn.commit()
        return JSONResponse({"ok": True})
    rows = conn.execute("""SELECT c.*, l.title, l.photos FROM complaints c LEFT JOIN listings l ON l.id = c.listing_id
                           ORDER BY c.status = 'new' DESC, c.ts DESC LIMIT 300""").fetchall()
    return JSONResponse({"items": [dict(r) for r in rows]})


# ─── заметки и подписки на поиск ───────────────────────────────────────────
def api_note(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    body = body or {}
    hooks.set_note(db.get(), user["id"], int(request.path_params["id"]),
                   str(body["text"]) if "text" in body else None,
                   str(body.get("status") or "") if "status" in body else "keep")
    return JSONResponse({"ok": True})


def api_worklog(request: Request, body: dict | None = None):
    """Журнал работы по объекту: POST {listing_id, text, kind, client_id?, pick_id?}; DELETE {id}; GET ?listing=."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "DELETE":
        worklog.delete(conn, user["id"], _num(body.get("id"), int) or 0)
        return JSONResponse({"ok": True})
    if request.method == "POST":
        if _too_often(request, "worklog", 600, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        res = worklog.add(conn, user["id"], _num(body.get("listing_id"), int) or 0, str(body.get("text") or ""),
                          str(body.get("kind") or "note"), body.get("client_id"), body.get("pick_id"))
        if "error" in res:
            return _err(res["error"])
    lid = _num(request.query_params.get("listing") or body.get("listing_id"), int) or 0
    return JSONResponse({"items": worklog.for_listing(conn, user["id"], lid), "ctx": worklog.context(conn, user["id"], lid)})


def api_saved(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "POST":
        if _too_often(request, "saved", 30, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        res, why = hooks.save_search(conn, user, str(body.get("query", ""))[:2000], TYPE_LABELS,
                                     str(body.get("page", ""))[:2000])
        if res is None:
            return _err(why)
        return JSONResponse({"saved": res, "text": why, "items": hooks.list_saved(conn, user["id"])})
    if request.method == "DELETE":
        hooks.delete_saved(conn, user["id"], _num(body.get("id"), int) or 0)
    return JSONResponse({"items": hooks.list_saved(conn, user["id"])})


def api_notices(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        notices.mark_read(conn, user["id"], _num((body or {}).get("id"), int))
        return JSONResponse({"ok": True, "unread": notices.unread(conn, user["id"])})
    try:
        hooks.check_user(conn, user["id"])   # не ждём фоновой проверки
    except sqlite3.OperationalError:
        log.warning("уведомления: база занята, покажу то, что есть")
    favs = conn.execute("""SELECT COUNT(*), SUM(l.is_active) FROM favorites f JOIN listings l ON l.id = f.listing_id
                           WHERE f.user_id = ?""", (user["id"],)).fetchone()
    own = conn.execute("SELECT COUNT(*) FROM listings WHERE owner_user_id = ? AND is_active = 1", (user["id"],)).fetchone()[0]
    return JSONResponse({"items": notices.items(conn, user["id"]), "unread": notices.unread(conn, user["id"]),
                         "saved": hooks.list_saved(conn, user["id"]), "favorites": favs[0], "favorites_active": favs[1] or 0,
                         "own": own, "email": user["email"], "access_until": accounts.access_until(user),
                         "is_admin": bool(user["is_admin"])})


def api_notices_peek(request: Request, body: dict | None = None):
    """Для колокольчика в шапке: сколько непрочитанных и 3 самых свежих непрочитанных (быстро, без проверок)."""
    user = current_user(request)
    if user is None:   # гость: ответ «не вошёл» без ошибки — колокольчик перестаёт спрашивать
        return JSONResponse({"guest": True, "unread": 0, "latest": []})
    conn = db.get()
    rows = conn.execute("""SELECT id, ts, kind, title, body, url FROM notices WHERE user_id = ? AND read = 0
                           ORDER BY id DESC LIMIT 3""", (user["id"],)).fetchall()
    return JSONResponse({"unread": notices.unread(conn, user["id"]),
                         "latest": [{**dict(r), "icon": notices.ICONS.get(r["kind"], "🔔")} for r in rows]})


def notices_page(request: Request, body: dict | None = None):
    return _page("notify.html")


def _need_access(request: Request):
    """Вошёл и есть доступ (подписка / пробный период / админ)."""
    user, err = _need_user(request)
    if err:
        return None, err
    if not has_access(request, user):
        return None, _err("Подборки для клиентов — по подписке. Оформите доступ в кабинете.", 403)
    return user, None


def api_picks(request: Request, body: dict | None = None):
    """Мои подборки: GET — список; POST — создать/изменить {id?, title, note, contact_name, contact_phone, listing_id?};
    DELETE {id}."""
    user, err = _need_access(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "DELETE":
        picks.delete(conn, user["id"], int(body.get("id") or 0))
        return JSONResponse({"ok": True})
    if request.method == "POST":
        if _too_often(request, "picks", 300, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        res = picks.save(conn, user, body)
        return _err(res["error"]) if "error" in res else JSONResponse({"pick": res})
    lid = _num(request.query_params.get("listing"), int)
    return JSONResponse({"items": picks.mine(conn, user["id"]),
                         "with": picks.with_listing(conn, user["id"], lid) if lid else []})


def api_pick_items(request: Request, body: dict | None = None):
    """Объект в подборку / из подборки {listing_id, add, note?} или порядок {order: [...]}."""
    user, err = _need_access(request)
    if err:
        return err
    body = body or {}
    cid = int(request.path_params["id"])
    if "order" in body:
        res = picks.move(db.get(), user["id"], cid, [int(x) for x in body["order"] if str(x).isdigit()])
    elif "replace" in body:   # снятый объект → похожий на то же место
        res = picks.replace(db.get(), user["id"], cid, _num(body.get("replace"), int) or 0, _num(body.get("listing_id"), int) or 0)
    elif "photo" in body or "photo_del" in body:   # свои фото к объекту в подборке
        res = picks.item_photo(db.get(), user["id"], cid, int(body.get("listing_id") or 0),
                               data_url=body.get("photo"), remove=body.get("photo_del"))
    elif "status" in body:   # статус объекта в подборке клиента (понравился, не подходит, показ…)
        res = picks.set_status(db.get(), user["id"], cid, int(body.get("listing_id") or 0), str(body["status"]))
    else:
        price = None
        if "price" in body:   # своя цена для клиента: число в рублях, 0 / пусто — как в объявлении
            price = _num(body.get("price"), int) or 0
        res = picks.set_item(db.get(), user["id"], cid, int(body.get("listing_id") or 0), bool(body.get("add", True)),
                             body.get("note"), text=body["text"] if "text" in body else None,
                             price=price, hide_price=bool(body["hide_price"]) if "hide_price" in body else None)
    return _err(res["error"]) if "error" in res else JSONResponse(res)


# Роботы, которые открывают ссылку ради превью (WhatsApp, Telegram, VK…), и поисковики — это не клиент
_PREVIEW_BOT = re.compile(r"bot\b|bot/|crawl|spider|preview|whatsapp/|telegrambot|facebookexternalhit|vkshare|skypeuripreview|"
                          r"slack|discord|viber|yandex|google|bing|headless|python|curl|wget|okhttp|java/", re.I)
PV_COOKIE = "pv"


def api_pick_similar(request: Request, body: dict | None = None):
    """Снятый объект в подборке → 3 похожих на сайте (агенту, для замены в один клик)."""
    user, err = _need_access(request)
    if err:
        return err
    return JSONResponse({"items": picks.similar(db.get(), user["id"], int(request.path_params["id"]),
                                                _num(request.query_params.get("listing"), int) or 0)})


def api_pick_react(request: Request, body: dict | None = None):
    """Ответ клиента со страницы подборки: {listing_id, kind: like|no|want_show|question, text?, phone?}."""
    if _too_often(request, "react", 120, 3600):
        return _err("Слишком часто — попробуйте позже.", 429)
    body = body or {}
    device, _ = _device(request)
    res = picks.react(db.get(), request.path_params["token"], _num(body.get("listing_id"), int) or 0, str(body.get("kind") or ""),
                      str(body.get("text") or ""), str(body.get("phone") or ""), device)
    if "error" in res:
        return _err(res["error"])
    resp = JSONResponse(res)
    if request.cookies.get(PV_COOKIE) != device and request.cookies.get("ck") == "all":
        resp.set_cookie(PV_COOKIE, device, max_age=365 * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return resp


def _device(request: Request) -> tuple[str, bool]:
    """Метка устройства клиента подборки: с согласием на cookie — кука pv (надолго); без согласия — ничего не храним
    в браузере, метка считается из браузера и адреса (только для «уже ответил» и уникальных просмотров)."""
    d = request.cookies.get(PV_COOKIE) or ""
    if re.fullmatch(r"[A-Za-z0-9_-]{16,64}", d):
        return d, False
    if request.cookies.get("ck") == "all":
        return secrets.token_urlsafe(16), True
    import hashlib
    raw = "|".join((request.headers.get("user-agent", ""), request.headers.get("accept-language", ""),
                    request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "")))
    return "h" + hashlib.sha256((config.SECRET_KEY + raw).encode()).hexdigest()[:22], False


def api_cookie_consent(request: Request, body: dict | None = None):
    """17.10: согласие на cookie (web/cookies.js). all — можно необязательные (статистика, приглашение коллеги);
    need — только необходимые: необязательные удаляем. Выбор пишем в журнал (когда, что, кто) — подтверждение согласия."""
    body = body or {}
    choice = "all" if body.get("choice") == "all" else "need"
    u = current_user(request)
    conn = db.get()
    conn.execute("INSERT INTO cookie_consents (ts, choice, user_id) VALUES (?,?,?)", (int(time.time()), choice, u["id"] if u else None))
    conn.commit()
    resp = JSONResponse({"ok": True, "choice": choice})
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie("ck", choice, max_age=365 * 86400, samesite="lax", secure=secure)   # читает и cookies.js
    if choice == "all":
        ref = re.sub(r"[^\w-]", "", str(body.get("ref") or ""))[:20]
        if ref:
            resp.set_cookie("ref", ref, max_age=30 * 86400, httponly=True, samesite="lax", secure=secure)
    else:
        resp.delete_cookie("ref")
        resp.delete_cookie(PV_COOKIE)
    return resp


def api_pick_public(request: Request, body: dict | None = None):
    """Подборка для клиента. Открытие засчитываем честно: не агент («Как видит клиент» — ?as=agent), не вошедший
    пользователь сервиса, не робот-превью мессенджера; уникальные — по метке устройства (кука pv), раз в день."""
    ua = request.headers.get("user-agent", "")
    count = (current_user(request) is None and request.query_params.get("as") != "agent"
             and bool(ua) and not _PREVIEW_BOT.search(ua))
    device, new_device = _device(request)
    new_device = new_device and count
    d = picks.public(db.get(), request.path_params["token"], count_view=count, device=device)
    if not d:
        return _err("Подборка не найдена или удалена.", 404)
    resp = JSONResponse(d)
    if new_device:
        resp.set_cookie(PV_COOKIE, device, max_age=365 * 86400, httponly=True, samesite="lax",
                        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https")
    return resp


def pick_page(request: Request, body: dict | None = None):
    """Страница подборки для клиента + превью ссылки в мессенджерах (название, число объектов, фото)."""
    host = request.headers.get("host", "").split(":")[0].lower()
    if config.PICK_DOMAIN and host != config.PICK_DOMAIN:   # клиент не должен видеть адрес сервиса
        return RedirectResponse(f"{config.PICK_URL}/c/{request.path_params['token']}", status_code=301)
    html_text = (WEB / "pick.html").read_text(encoding="utf-8")
    d = picks.public(db.get(), request.path_params["token"], count_view=False)
    if d:
        esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
        n = len(d["items"])
        photo = d["style"]["cover"] or next((i["photos"][0] for i in d["items"] if i["photos"]), "/static/house.svg")
        if photo.startswith("/"):
            photo = config.PICK_URL + photo
        who = f" · {d['contact_name']}" if d["contact_name"] else ""
        desc = f"{n} {'объект' if n % 10 == 1 and n % 100 != 11 else 'объекта' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'объектов'}{who}"
        meta = (f'<meta property="og:type" content="website"><meta property="og:title" content="{esc(d["title"])}">'
                f'<meta property="og:description" content="{esc(desc)}"><meta property="og:image" content="{esc(photo)}">')
        html_text = html_text.replace("<title>Подборка</title>", f"<title>{esc(d['title'])}</title>{meta}", 1)
    return Response(html_text, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def cabinet_page(request: Request, body: dict | None = None):
    return _page("cabinet.html")


def picks_page(request: Request, body: dict | None = None):
    return _page("picks.html")


def work_page(request: Request, body: dict | None = None):
    return _page("work.html")


def compare_page(request: Request, body: dict | None = None):
    return _page("compare.html")


def settings_page(request: Request, body: dict | None = None):
    return _page("settings.html")


def service_worker(request: Request, body: dict | None = None):
    """Помощник приложения на телефоне (web/sw.js) — с корня сайта, всегда свежий."""
    return FileResponse(WEB / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


def my_page(request: Request, body: dict | None = None):
    return _page("my.html")   # «Мои объекты» и правка объявления (/my/edit) — одна страница, вид выбирает my.js


def api_settings(request: Request, body: dict | None = None):
    """Настройки аккаунта: GET — всё; POST {поля} — сохранить; {cover: dataURL} — обложка подборок;
    {action: "logout_all"} — выйти на всех устройствах (кроме этого)."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "POST":
        if body.get("action") == "logout_all":
            keep = accounts._hash(request.cookies.get(SESSION) or "")
            n = conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user["id"], keep)).rowcount
            conn.commit()
            return JSONResponse({"ok": True, "closed": n})
        if "cover" in body or "photo" in body:   # обложка подборок или ваше фото рядом с номером (видит клиент)
            if _too_often(request, "cover", 20, 3600):
                return _err("Слишком много фото за час.", 429)
            url, why = settings.save_cover(str(body.get("cover") or body.get("photo") or ""))
            if not url:
                return _err(why)
            body = {"pick_cover" if "cover" in body else "pick_photo": url}
        settings.save(conn, user["id"], body)
    st = settings.get(conn, user["id"])
    sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user["id"],)).fetchone()[0]
    return JSONResponse({"settings": st, "name": accounts.get_user(conn, user["id"])["name"] or "",
                         "email": user["email"], "colors": settings.COLORS, "pick_url": config.PICK_URL,
                         "pick_domain": bool(config.PICK_DOMAIN), "session_days": accounts.SESSION_DAYS, "sessions": sessions})


def requests_page(request: Request, body: dict | None = None):
    user = current_user(request)   # запросы покупателей — только админу (решение Артёма 13.10)
    if not (user and user["is_admin"]):
        return RedirectResponse("/", status_code=303)
    return _page("requests.html")


def api_requests(request: Request, body: dict | None = None):
    """Лента запросов покупателей — только админу (решение Артёма 13.10)."""
    user, err = _need_admin(request)
    if err:
        return err
    q = request.query_params
    return JSONResponse(buyers.feed(db.get(), has_access(request, user), q.get("deal", ""), q.get("district", ""),
                                    _num(q.get("rooms"), int), q.get("q", ""), _num(q.get("page"), int) or 1))


def planner_page(request: Request, body: dict | None = None):
    return _page("planner.html")


def api_planner(request: Request, body: dict | None = None):
    """Планер: GET — всё для страницы; POST — сохранить событие {…} / отметить {id, done}; DELETE {id}."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "DELETE":
        planner.delete_event(conn, user["id"], int(body.get("id") or 0))
        return JSONResponse({"ok": True})
    if request.method == "POST":
        if set(body) == {"id", "done"}:
            planner.set_done(conn, user["id"], int(body["id"]), bool(body["done"]))
            return JSONResponse({"ok": True})
        if set(body) == {"id", "ts"}:   # перетащили на другой день
            ok = planner.move(conn, user["id"], int(body["id"]), int(body["ts"]))
            return JSONResponse({"ok": True}) if ok else _err("Не получилось перенести.")
        if _too_often(request, "planner", 300, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        res = planner.save_event(conn, user["id"], body)
        return _err(res["error"]) if "error" in res else JSONResponse({"event": res})
    q = request.query_params
    if q.get("from") and q.get("to"):
        frm, to = int(q["from"]), int(q["to"])
        return JSONResponse({"events": planner.events(conn, user["id"], frm, to),
                             "stats": planner.month_stats(conn, user["id"], frm, to)})
    return JSONResponse(planner.overview(conn, user["id"]))


def api_planner_note(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    if _too_often(request, "pnote", 600, 3600):
        return _err("Слишком часто — попробуйте позже.", 429)
    res = planner.save_note(db.get(), user["id"], body or {})
    return _err(res["error"]) if "error" in res else JSONResponse({"note": res})


def api_planner_ics(request: Request, body: dict | None = None):
    """Одно событие .ics (кнопка «В календарь телефона»)."""
    user, err = _need_user(request)
    if err:
        return err
    text = planner.ics(db.get(), user["id"], int(request.path_params["id"]))
    return Response(text, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="1plus1-{request.path_params["id"]}.ics"'})


def planner_feed(request: Request, body: dict | None = None):
    """Подписка календаря телефона: /planner/<id>-<подпись>.ics — без входа, по секретной ссылке."""
    uid, token = int(request.path_params["uid"]), request.path_params["token"]
    if not planner.check_feed(uid, token):
        return Response("not found", status_code=404)
    return Response(planner.ics(db.get(), uid), media_type="text/calendar; charset=utf-8")


def market_page(request: Request, body: dict | None = None):
    return _page("market.html")


def api_market(request: Request, body: dict | None = None):
    """Аналитика рынка: медианы по районам и ЖК, динамика. Открыта всем."""
    return _cached(request, lambda: market.report(db.get(), request.query_params.get("deal", "sale"),
                                                  rooms=_num(request.query_params.get("rooms"), int)))


def saved_off_page(request: Request, body: dict | None = None):
    q = request.query_params
    ok = hooks.unsubscribe(db.get(), _num(q.get("s"), int) or 0, q.get("t", ""))
    text = "Готово — письма по этому поиску больше не придут." if ok else "Ссылка устарела или неверна."
    html = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>1+1</title><body style="font:18px system-ui;background:#035352;color:#F3E8BC;display:grid;'
            f'place-items:center;min-height:90vh;text-align:center;padding:16px"><div><h1>1+1</h1><p>{text}</p>'
            f'<p><a style="color:#F3E8BC" href="/">К поиску</a></p></div>')
    return Response(html, media_type="text/html; charset=utf-8")


# ─── кабинет агента ────────────────────────────────────────────────────────
def _agent_items(conn, user) -> list[dict]:
    out = []
    for r in agent.my_listings(conn, user["id"]):
        it = search.row_to_item(r, True)
        it["can_edit"] = agent.can_edit(conn, user, r)[0]
        out.append(it)
    return out


def api_agent(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    return JSONResponse({
        "phones": agent.verified_phones(conn, user["id"]), "verify_number": config.VERIFY_NUMBER,
        "channels": config.VERIFY_CHANNELS, "access": accounts.has_access(user),
        # способы подтверждения номера: звонок — основной (если подключён SMS.ru), код в MAX — запасной
        "verify_methods": [m for m, on in (("call", callcheck.enabled()), ("max", bool(config.VERIFY_NUMBER))) if on],
        "own_days": config.OWN_LISTING_DAYS, "max_photos": config.MAX_PHOTOS, "items": _agent_items(conn, user),
    })


def api_agent_verify(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    if request.method == "GET":
        ph = request.query_params.get("phone", "")
        if request.query_params.get("method") == "call":   # звонок: спрашиваем SMS.ru (не чаще раза в 3 с)
            db.flush(conn)
            return JSONResponse({"status": agent.call_status(conn, user["id"], ph) or "expired"})
        return JSONResponse({"status": agent.verification_status(conn, user["id"], ph)})
    if (body or {}).get("method") == "call":   # 17.10: подтвердить звонком (основной способ)
        if not callcheck.enabled():
            return _err("Подтверждение звонком ещё не подключено — подтвердите кодом в MAX.", 503)
        if _too_often(request, "agent_call", 6, 3600):
            return _err("Слишком много попыток — попробуйте через час.", 429)
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else ""))[:45]
        res, why = agent.start_call(conn, user["id"], str((body or {}).get("phone", ""))[:40], ip)
        return JSONResponse(res) if res else _err(why)
    if (body or {}).get("check"):   # «Я отправил — проверить»: читаем личку сразу, не ждём фонового цикла
        phone = str(body.get("phone", ""))[:40]
        if agent.verification_status(conn, user["id"], phone) != "pending":
            return JSONResponse(agent.check_result(conn, user["id"], phone, [], {}))
        if _too_often(request, "agent_check", 30, 900):
            return _err("Слишком часто — подождите пару минут.", 429)
        db.flush(conn)
        info = wappi.check_now(conn)
        res = agent.check_result(conn, user["id"], phone, wappi.seen_codes, info)
        if res.get("seen_code") and res["status"] == "pending":   # код пришёл, но не подошёл — сообщим админу
            db.flush(conn)
            notify.alert(conn, f"verify_phone:{user['id']}",
                         f"Агент (аккаунт {user['id']}) прислал код подтверждения, но номер не совпал или не виден: {res['detail']}",
                         every_s=3600)
        return JSONResponse(res)
    if not config.VERIFY_NUMBER:
        return _err("Подтверждение номера ещё не настроено. Напишите нам.", 503)
    if _too_often(request, "agent_verify", 10, 3600):
        return _err("Слишком много попыток — попробуйте через час.", 429)
    res, why = agent.start_verification(conn, user["id"], str((body or {}).get("phone", ""))[:40])
    return JSONResponse(res) if res else _err(why)


def api_agent_create(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    lid, why = agent.create_own(conn, user, body or {})
    if lid is None:
        return _err(why)
    return JSONResponse({"id": lid, "text": why, "items": _agent_items(conn, user)})


def api_agent_edit(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    ok, why = agent.edit_listing(conn, user, int(request.path_params["id"]), body or {})
    if not ok:
        return _err(why, 403 if "управляет" in why or "нет вашего" in why else 400)
    return JSONResponse({"ok": True, "text": why, "items": _agent_items(conn, user)})


def api_agent_describe(request: Request, body: dict | None = None):
    """«Написать красиво»: продающее описание объекта нейросетью (только с доступом; не чаще 30 раз в час)."""
    user, err = _need_access(request)
    if err:
        return err
    if _too_often(request, "describe", 30, 3600):
        return _err("Слишком часто — попробуйте через несколько минут.", 429)
    db.flush(db.get())   # ждём нейросеть — базу не держим
    text, why = agent.describe(body or {})
    return JSONResponse({"text": text}) if text else _err(why, 503)


def api_agent_photo(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    lid = int(request.path_params["id"])
    body = body or {}
    if request.method == "DELETE":
        photos, why = agent.remove_photo(conn, user, lid, str(body.get("url", "")))
    else:
        if _too_often(request, "agent_photo", 60, 3600):
            return _err("Слишком много фото за час.", 429)
        photos, why = agent.add_photo(conn, user, lid, str(body.get("data", "")))
    return JSONResponse({"photos": photos, "text": why}) if photos is not None else _err(why)


def agent_confirm_page(request: Request, body: dict | None = None):
    """Ссылка из письма «объект ещё актуален?»."""
    q = request.query_params
    res = agent.confirm(db.get(), _num(q.get("l"), int) or 0, q.get("a", ""), q.get("t", ""))
    text = {"yes": f"Готово! Объект продлён на {config.OWN_LISTING_DAYS} дней.",
            "no": "Готово! Объект снят с сайта. Спасибо, что сообщили.",
            "gone": "Объект уже удалён.", "bad": "Ссылка устарела или неверна."}.get(res, "Ссылка неверна.")
    html = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>1+1</title><body style="font:18px system-ui;background:#035352;color:#F3E8BC;display:grid;'
            f'place-items:center;min-height:90vh;text-align:center;padding:16px"><div><h1>1+1</h1><p>{text}</p>'
            f'<p><a style="color:#F3E8BC" href="/">К поиску</a></p></div>')
    return Response(html, media_type="text/html; charset=utf-8")


def photo_file(request: Request, body: dict | None = None):
    """Фото, загруженные агентами (data/photos/<объект>/<имя>; свои фото агента в подборках — data/photos/pk/<имя>).
    Имя — хеш содержимого, кэшируем надолго."""
    name, folder = request.path_params["name"], request.path_params["folder"]
    if not re.fullmatch(r"[0-9a-f]{16}\.(?:jpg|png|webp)", name) or not re.fullmatch(r"[0-9]{1,10}|pk", folder):
        return _err("не найдено", 404)
    f = Path(config.PHOTOS_DIR) / folder / name
    if not f.is_file():
        return _err("не найдено", 404)
    return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"})


def api_admin_chats(request: Request, body: dict | None = None):
    """Чаты: какие берём, какие нет. POST {source, chat_id, blocked} — отключить/включить."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    q = request.query_params
    try:
        if request.method == "POST":
            body = body or {}
            res = region.set_blocked(conn, str(body.get("source", ""))[:10], str(body.get("chat_id", ""))[:200],
                                     bool(body.get("blocked")))
            return JSONResponse({"ok": True, **res})
        if q.get("chat_id"):
            return JSONResponse(region.chat_preview(conn, q.get("source", ""), q.get("chat_id", "")))
        return JSONResponse({"items": region.chats_for_admin(conn)})
    except sqlite3.OperationalError:
        log.exception("чаты: база занята")
        return _err("Сайт сейчас обновляет базу — попробуйте ещё раз через минуту.", 503)


def api_admin_geo(request: Request, body: dict | None = None):
    """Админ поправил точку объекта на карте. {lat, lon} — поставить; {hide: true} — убрать с карты;
    {auto: true} — вернуть автоматический поиск по адресу."""
    user, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    lid = int(request.path_params["id"])
    body = body or {}
    row = conn.execute("SELECT lat, lon, geo_status FROM listings WHERE id = ?", (lid,)).fetchone()
    if row is None:
        return _err("не найдено", 404)
    from . import brain
    src = brain.auto_source(conn, lid)
    if src and src.startswith("point:"):   # выборочная проверка: точку сдвинули дальше 50 м — сервис ошибся
        lat0, lon0 = _num(body.get("lat")), _num(body.get("lon"))
        brain.record_review(conn, lid, src, lat0 is not None and lon0 is not None and row["lat"] is not None
                            and geocode._km((row["lat"], row["lon"]), (lat0, lon0)) <= 0.05)
    if body.get("auto"):
        lat, lon, status = None, None, "pending"
    elif body.get("hide"):
        lat, lon, status = None, None, "manual"
    else:
        lat, lon = _num(body.get("lat")), _num(body.get("lon"))
        if lat is None or lon is None or not (40 < lat < 50 and 35 < lon < 45):
            return _err("Точка должна быть в Краснодарском крае.")
        lat, lon, status = round(lat, 6), round(lon, 6), "manual"
    conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ?, geo_src = NULL WHERE id = ?", (lat, lon, status, lid))
    # Учимся: эта точка — для того же дома и ЖК во всех объявлениях (сейчас и в будущем)
    applied = 0
    if lat is not None:
        full = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        applied = geocode.learn(conn, full, lat, lon)
        if not full["district"] and not full["settlement"]:
            geocode._district_from_map(conn, full, (lat, lon))   # района нет — по вашим контурам районов
    conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                 (lid, user["id"], int(time.time()), "geo", json.dumps([row["lat"], row["lon"]]), json.dumps([lat, lon])))
    conn.commit()
    return JSONResponse({"ok": True, "lat": lat, "lon": lon, "geo_status": status, "applied": applied,
                         "learned": [label for _, label in geocode.learn_keys(full)] if lat is not None else []})


_GEO_QUEUE = {
    # нет на карте: адрес в объявлении есть (иначе объект и на сайте не показывается), но точку не нашли
    "none": "l.lat IS NULL AND l.geo_status IN ('none', 'skip')",
    # примерные точки (по улице без дома, по району)
    "approx": "l.geo_status = 'approx'",
    # ещё ищем (геокодер не успел)
    "pending": "l.lat IS NULL AND l.geo_status = 'pending'",
    # точку поставил сервис сам (фид, ЖК на карте, центр района) — проверить выборочно
    "auto": "l.geo_src IN ('feed-house', 'feed-cx', 'map-cx', 'map-district', 'osm-house', 'osm-near', 'osm-street') AND l.geo_status NOT IN ('manual', 'feed')",
}
_VISIBLE = "l.is_active = 1 AND (l.district IS NOT NULL OR l.complex IS NOT NULL OR l.street IS NOT NULL OR l.settlement IS NOT NULL)"


def _parse_admin_price(v, deal: str) -> int | None:
    """«3,2» → 3 200 000 (продажа, млн); «35» → 35 000 (аренда, тыс.); «3 200 000» — как есть."""
    s = re.sub(r"[^\d,.]", "", str(v or "")).replace(",", ".")
    try:
        x = float(s)
    except ValueError:
        return None
    if deal == "rent":
        x = x * 1000 if x < 1000 else x
    else:
        x = x * 1e6 if x < 1000 else x * 1000 if x < 100_000 else x
    return int(round(x)) if 1000 <= x <= 5_000_000_000 else None


def api_admin_place(request: Request, body: dict | None = None):
    """Админ поправил ЖК/район объекта и (по желанию) научил сервис.
    {complex, district, learn: {addr, alias, cx_district}}"""
    from .ingest import _reindex, make_search_text, make_title
    user, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    lid = int(request.path_params["id"])
    body = body or {}
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if row is None:
        return _err("не найдено", 404)
    cx = str(body.get("complex") or "").strip()[:120] or None
    if cx:
        rec = geo.resolve_complex(cx)
        cx = rec.name if rec else geo.pretty_name(re.sub(r"(?i)^\s*(?:жк|ж/к)\s+", "", cx))
    learning.sync_districts(conn)
    district = str(body.get("district") or "").strip() or None
    if district and not geo.canonical_district(district):
        return _err("Выберите район из списка.")
    district = geo.canonical_district(district) if district else None
    extra = []
    for x in body.get("extra") or []:
        x = geo.canonical_district(str(x))
        if x and x != district and x not in extra:
            extra.append(x)
    # ТЗ «админка-гео» P0.1: подтверждение без изменений правил не создаёт; правила района — только безопасные
    want = dict(body.get("learn") or {})
    unchanged = cx == row["complex"] and district == row["district"] and extra == json.loads(row["extra_districts"] or "[]")
    from . import brain
    src = brain.auto_source(conn, lid)
    if src and src.startswith("district:"):   # выборочная проверка: без правок района = «сервис решил верно»
        brain.record_review(conn, lid, src, district == row["district"])
    elif src:   # решение о точке: «верно», если следом экран разбора не сдвинет метку (тогда /geo запишет «поправил»)
        brain.record_review(conn, lid, src, True)
    skipped = []
    if unchanged:
        want = {}
    elif want.get("street") or want.get("cx_district"):
        ok = learning.allowed(learning.safety(conn, row), district)
        for k in ("street", "cx_district"):
            if want.get(k) and not ok[k][0]:
                want.pop(k)
                skipped.append(f"{'улица' if k == 'street' else 'ЖК'} → район не выучено: {ok[k][1]}")
    learned = learning.learn(conn, row, cx, district, want, extra)
    if body.get("price") not in (None, ""):
        price = _parse_admin_price(body.get("price"), row["deal"])
        if price is None:
            return _err("Цена не распознана. Пишите «3,2» (млн) или «3200000».")
        if price != row["price"]:
            ingest.set_price(conn, lid, price, user["id"])
            learned = learned + [f"цена → {_price_text(price, row['deal'])}"]
    d = {**dict(row), "complex": cx, "district": district}
    d["title"] = make_title(d)
    d["search_text"] = make_search_text(d)
    conn.execute("""UPDATE listings SET complex = ?, district = ?, extra_districts = ?, search_text = ?, admin_fixed = 1,
                    district_src = NULL, place_conflict = NULL, geo_status = CASE WHEN geo_status IN ('manual', 'learned', 'feed') THEN geo_status ELSE 'pending' END
                    WHERE id = ?""", (cx, district, json.dumps(extra, ensure_ascii=False), d["search_text"], lid))
    _reindex(conn, lid, d["search_text"])
    for field, old, new in (("complex", row["complex"], cx), ("district", row["district"], district)):
        if old != new:
            conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                         (lid, user["id"], int(time.time()), field, json.dumps(old, ensure_ascii=False),
                          json.dumps(new, ensure_ascii=False)))
    conn.commit()
    applied = learning.apply_to_existing(conn, skip_id=lid) if learned else 0
    # Группа (тот же ЖК / дом / улица) — та же правка сразу для всех её объектов.
    # ЖК меняем только в группе «тот же ЖК / тот же дом»; в группе «улица» — только район.
    same_place = body.get("group_same_place") is True
    for gid in [int(x) for x in (body.get("group_ids") or []) if str(x).isdigit()][:500]:
        g = conn.execute("SELECT * FROM listings WHERE id = ? AND is_active = 1", (gid,)).fetchone()
        if g is None or gid == lid:
            continue
        gd = {**dict(g), "district": district, "complex": cx if same_place else g["complex"]}
        gd["title"] = make_title(gd)
        gd["search_text"] = make_search_text(gd)
        conn.execute("""UPDATE listings SET complex = ?, district = ?, extra_districts = ?, title = ?, search_text = ?,
                        admin_fixed = 1, district_src = NULL,
                        geo_status = CASE WHEN geo_status IN ('manual', 'learned', 'feed') THEN geo_status ELSE 'pending' END
                        WHERE id = ?""", (gd["complex"], district, json.dumps(extra, ensure_ascii=False), gd["title"],
                                          gd["search_text"], gid))
        _reindex(conn, gid, gd["search_text"])
        applied += 1
    conn.commit()
    changed = {}
    if body.get("fields"):   # 18.10: все характеристики в одном окне (тип, комнаты, площадь, этаж, адрес, ремонт…)
        res = ingest.set_fields(conn, lid, body["fields"], user["id"])
        if "error" in res:
            return _err(res["error"])
        changed = res["changed"]
        if changed:
            learned = learned + ["характеристики: " + ", ".join(ingest.FIELD_SPEC[k][1] for k in changed)]
    price = conn.execute("SELECT price FROM listings WHERE id = ?", (lid,)).fetchone()[0]
    return JSONResponse({"ok": True, "complex": cx, "district": district, "extra": extra, "learned": learned, "skipped": skipped,
                         "applied": applied, "price": price, "changed": changed})


_PLACE_QUEUE = {
    "nodistrict": "l.district IS NULL AND l.settlement IS NULL AND l.admin_fixed = 0",
    "nocomplex": "l.complex IS NULL AND l.type IN ('flat', 'new') AND l.district IS NOT NULL AND l.admin_fixed = 0",
    "fixed": "l.admin_fixed = 1",
    # район поставил сервис сам (по карте районов или по соседним объявлениям) — проверить выборочно
    "auto": "l.district_src IS NOT NULL AND l.admin_fixed = 0",
}


def api_admin_place_queue(request: Request, body: dict | None = None):
    """Разбор ЖК/районов: объекты без района, квартиры без ЖК; выученные правила (GET); забыть правило (POST {forget})."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        learning.forget(conn, _num((body or {}).get("forget"), int) or 0)
        return JSONResponse({"ok": True})
    kind = request.query_params.get("kind", "nodistrict")
    where = _PLACE_QUEUE.get(kind, _PLACE_QUEUE["nodistrict"])
    counts = {k: conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND {w}").fetchone()[0]
              for k, w in _PLACE_QUEUE.items()}
    counts["rules"] = conn.execute("SELECT COUNT(*) FROM learned_rules").fetchone()[0]
    rows = conn.execute(
        f"""SELECT l.id, l.title, l.district, l.complex, l.street, l.house, l.settlement,
                   substr(COALESCE(l.fragment, l.description, ''), 1, 300) AS text
            FROM listings l WHERE {_VISIBLE} AND {where} ORDER BY l.last_seen DESC LIMIT 200""").fetchall()
    return JSONResponse({"items": [dict(r) for r in rows], "counts": counts, "rules": learning.list_rules(conn)})


def _fix_item(conn, lid: int) -> dict | None:
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if r is None:
        return None
    text = (r["fragment"] or r["description"] or "")[:2000]
    photos = json.loads(r["photos"] or "[]")
    return {k: r[k] for k in ("id", "title", "price", "deal", "type", "rooms", "area", "floor", "floors", "complex",
                              "district", "street", "house", "settlement", "lat", "lon", "geo_status", "admin_fixed",
                              "source", "room_kind", "repair")} | {
        "text": text, "photo": photos[0] if photos else None, "photos": photos[:12], "land": r["land"],
        "phones": json.loads(r["phones"] or "[]"),
        "extra_districts": json.loads(r["extra_districts"] or "[]"),
        "suggest": _fix_suggest(conn, r, text),
        "learned": [label for _, label in geocode.learn_keys(r)],
        "geo_src": autolearn.GEO_SRC.get(r["geo_src"] or "") if r["geo_src"] != "geocoder" else None,
        "district_src": autolearn.DISTRICT_SRC.get(r["district_src"] or ""),
        "was_district": _was_district(conn, r),
        "conflict": (r["place_conflict"] or "").split(":", 1)[-1] or None,
        "zone": _zone(conn, r["district"]),
        "learn_stats": learning.safety(conn, r),   # распределение улицы и ЖК по районам — какие правила безопасно учить
    }


def _was_district(conn, r) -> str | None:
    """Район, который был до перепроверки сервисом (если сервис его поменял и это не откатили)."""
    e = conn.execute("""SELECT old FROM listing_edits WHERE listing_id = ? AND user_id = ? AND field = 'district'
                        AND reverted = 0 ORDER BY id DESC LIMIT 1""", (r["id"], autolearn.SERVICE_UID)).fetchone()
    old = json.loads(e[0]) if e else None
    return old if old and old != r["district"] else None


def _fix_suggest(conn, r, text: str) -> dict:
    """Подсказки районов: сначала по вашей карте районов и соседним объявлениям, потом по тексту и справочнику."""
    sug = geo.suggestions(text, r["street"], r["complex"])
    seen: dict = {}
    was = _was_district(conn, r)
    for h in autolearn.hints(conn, r) + sug["districts"] + ([{"name": was, "why": "было до перепроверки"}] if was else []):
        if h["name"] in seen:
            seen[h["name"]]["why"] += " · " + h["why"]
        else:
            seen[h["name"]] = dict(h)
    sug["districts"] = list(seen.values())[:8]
    return sug


def _zone(conn, name: str | None) -> dict | None:
    """Контур района (для карты на экране разбора)."""
    if not name:
        return None
    for n, kind, polys, _a, _bb in districtmap._polys(conn):
        if kind == "district" and n == name:
            return {"name": n, "polys": polys}
    return None


def _group_key(mode: str, kind: str, r) -> tuple[str, str] | None:
    """Группа объектов, которые решаются одной правкой: (ключ, подпись)."""
    cx = learning.cx_key(r["complex"])
    house = learning.addr_key(r["street"], r["house"], r["settlement"])
    street = learning.street_key(r["street"], r["settlement"])
    if mode == "geo":   # точка ЖК переносит все его объекты (geocode.learn) — группа по ЖК, без ЖК — по дому
        if cx:
            return cx, f"ЖК {r['complex']}"
        return (house, f"дом ул. {r['street']}, {r['house']}") if house else None
    if kind == "nocomplex":
        return (house, f"дом ул. {r['street']}, {r['house']}") if house else None
    if cx:
        return cx, f"ЖК {r['complex']}"
    return (street, f"ул. {r['street']}") if street else None


def _fix_groups(conn, mode: str, kind: str, where: str, exclude: set) -> list[dict]:
    """Очередь группами: самая большая группа — первой (одна правка решает сразу много объектов)."""
    rows = conn.execute(f"""SELECT l.id, l.complex, l.street, l.house, l.settlement FROM listings l
                            WHERE {_VISIBLE} AND {where} ORDER BY l.last_seen DESC LIMIT 5000""").fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        if r["id"] in exclude:
            continue
        g = _group_key(mode, kind, r) or (f"id:{r['id']}", "")
        groups.setdefault(g[0], {"key": g[0], "label": g[1], "ids": []})["ids"].append(r["id"])
    return sorted(groups.values(), key=lambda g: -len(g["ids"]))


def _group_of(conn, mode: str, kind: str, where: str, r, exclude: set) -> dict | None:
    g = _group_key(mode, kind, r)
    if not g:
        return None
    for grp in _fix_groups(conn, mode, kind, where, exclude - {r["id"]}):
        if grp["key"] == g[0]:
            return grp if len(grp["ids"]) > 1 and r["id"] in grp["ids"] else None
    return None


def api_admin_districts(request: Request, body: dict | None = None):
    """Добавить район (POST {name, aliases}) / список (GET)."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    learning.sync_districts(conn)
    body = body or {}
    if request.method == "POST" and body.get("action") == "rename":
        res = learning.rename_district(conn, str(body.get("old", "")), str(body.get("new", "")))
        return JSONResponse({**res, "districts": geo.all_district_names()})
    if request.method == "POST" and body.get("action") == "move":
        res = learning.move(conn, "complex" if body.get("kind") == "complex" else "street", str(body.get("name", "")),
                            body.get("from") or None, str(body.get("to", "")),
                            [x for x in (body.get("extra") or []) if geo.canonical_district(x)])
        return JSONResponse(res)
    if request.method == "POST":
        aliases = [a.strip() for a in str(body.get("aliases", "")).split(",") if a.strip()]
        name = learning.add_district(conn, str(body.get("name", "")), aliases)
        if not name:
            return _err("Напишите название района.")
        return JSONResponse({"name": name, "districts": geo.all_district_names()})
    if request.query_params.get("name"):
        return JSONResponse(learning.district_detail(conn, request.query_params["name"]))
    if request.query_params.get("dir") in ("complex", "street"):
        return JSONResponse({"items": learning.directory(conn, request.query_params["dir"]),
                             "districts": geo.all_district_names()})
    counts = dict(conn.execute("""SELECT name, COUNT(*) FROM (
                                      SELECT district AS name FROM listings WHERE is_active = 1 AND district IS NOT NULL
                                      UNION ALL SELECT j.value FROM listings, json_each(listings.extra_districts) j
                                      WHERE is_active = 1) GROUP BY name""").fetchall())
    custom = {r[0] for r in conn.execute("SELECT name FROM custom_districts")}
    names = geo.all_district_names()
    return JSONResponse({"districts": names,
                         "items": [{"name": n, "count": counts.get(n, 0), "custom": n in custom} for n in names]})


def api_admin_dir(request: Request, body: dict | None = None):
    """Справочник: GET ?kind=district|complex|street — список; &name= — карточка; POST {action, ...} — изменения."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    learning.sync_districts(conn)
    q = request.query_params
    kind = q.get("kind") if q.get("kind") in ("district", "complex", "street") else "district"
    if request.method == "GET":
        if q.get("name"):
            return JSONResponse(learning.dir_item(conn, kind, q["name"]))
        return JSONResponse({"items": learning.dir_list(conn, kind), "districts": geo.all_district_names()})
    body = body or {}
    action = body.get("action")
    try:
        if action == "delete_district":
            res = learning.delete_district(conn, str(body.get("name", "")), body.get("to") or None)
        elif action == "rename_district":
            res = learning.rename_district(conn, str(body.get("name", "")), str(body.get("to", "")))
        elif action == "rename_complex":
            res = learning.rename_complex(conn, str(body.get("name", "")), (str(body.get("to") or "").strip() or None))
        elif action == "move":
            res = learning.move(conn, "complex" if body.get("kind") == "complex" else "street", str(body.get("name", "")),
                                body.get("from") or None, str(body.get("to", "")))
        else:
            return _err("Неизвестное действие.")
    except ValueError as e:
        return _err(str(e))
    return JSONResponse({**res, "districts": geo.all_district_names()})


def api_admin_reconcile(request: Request, body: dict | None = None):
    """Сверка с картой районов: GET — расхождения; POST {action: import|accept|ignore|fill_empty}."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    learning.sync_districts(conn)
    body = body or {}
    action = body.get("action")
    try:
        if action == "import":   # файл карты районов (YMapsML/KML-подобный), полученный с разрешения владельца
            if not body.get("xml"):
                return _err("Нужен файл карты районов.")
            res = districtmap.import_map(conn, str(body["xml"]).encode("utf-8"))
            return JSONResponse({**res, **districtmap.reconcile(conn)})
        if action == "import_osm":
            res = districtmap.import_osm(conn)
            return JSONResponse(res)
        if action == "save_polygon":
            name = str(body.get("name", "")).strip()
            kind = "complex" if body.get("kind") == "complex" else "district"
            if kind == "district":
                name = geo.canonical_district(name) or name
            pid = districtmap.save_polygon(conn, name, kind, body.get("rings") or [])
            return JSONResponse({"id": pid, "name": name})
        if action == "delete_polygon":
            districtmap.delete_polygon(conn, _num(body.get("id"), int) or 0)
            return JSONResponse({"ok": True})
        if action == "accept":
            res = districtmap.accept(conn, body.get("kind", ""), str(body.get("name", "")), body.get("district"))
            return JSONResponse(res)
        if action == "ignore":
            districtmap.ignore(conn, str(body.get("key", "")))
            return JSONResponse({"ok": True})
        if action == "fill_empty":
            return JSONResponse({"listings": districtmap.fill_empty(conn)})
        if action == "set_districts":   # выбранные галочками объекты → район по карте
            return JSONResponse(districtmap.set_districts(conn, body.get("items") or [], body.get("remember_complex"),
                                                          body.get("remember_street")))
    except (ValueError, OSError) as e:
        return _err(f"Не получилось: {e}")
    if request.query_params.get("polygons"):
        counts = dict(conn.execute("""SELECT district, COUNT(*) FROM listings WHERE is_active = 1 AND district IS NOT NULL
                                      GROUP BY district""").fetchall())
        return JSONResponse({"polygons": districtmap.all_polygons(conn), "districts": geo.all_district_names(),
                             "counts": counts})
    return JSONResponse(districtmap.reconcile(conn))


def districts_map_page(request: Request, body: dict | None = None):
    return _page("districts.html")


def api_admin_listing_action(request: Request, body: dict | None = None):
    """Админ: {action: delete | restore | deal | price}. delete — навсегда (тот же текст/объект не вернётся)."""
    from .ingest import _reindex, make_search_text, make_title
    admin_user, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    lid = int(request.path_params["id"])
    body = body or {}
    act = body.get("action")
    if act == "delete":
        res = ingest.ban_listing(conn, lid)
    elif act == "price":
        r = conn.execute("SELECT deal FROM listings WHERE id = ?", (lid,)).fetchone()
        price = _parse_admin_price(body.get("price"), r["deal"] if r else "sale")
        if price is None:
            return _err("Цена не распознана. Пишите «3,2» (млн) или «3200000».")
        res = ingest.set_price(conn, lid, price, admin_user["id"])
    elif act == "restore":
        ingest.unban_listing(conn, lid)
        res = {"ok": True}
    elif act == "photo":   # убрать одно фото у объекта (и не возвращать его из фида/чатов)
        res = ingest.remove_photo(conn, lid, str(body.get("url") or ""))
    elif act == "deal" and body.get("deal") in ("sale", "rent"):
        r = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        if r is None:
            return _err("не найдено", 404)
        d = {**dict(r), "deal": body["deal"]}
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        conn.execute("""UPDATE listings SET deal = ?, deal_fixed = 1, title = ?, search_text = ?,
                        is_active = CASE WHEN hidden_reason = 'rent_rule' THEN 1 ELSE is_active END,
                        hidden_reason = CASE WHEN hidden_reason = 'rent_rule' THEN NULL ELSE hidden_reason END
                        WHERE id = ?""", (body["deal"], d["title"], d["search_text"], lid))
        _reindex(conn, lid, d["search_text"])
        conn.commit()
        ingest.learn_listing_fix(conn, lid, "frag_deal", {"deal": body["deal"]},
                                 f"{d['title']}: {'аренда' if body['deal'] == 'rent' else 'продажа'}")
        res = {"ok": True, "deal": body["deal"], "title": d["title"]}
    else:
        return _err("Неизвестное действие.")
    if "error" in res:
        return _err(res["error"], 404)
    _bump_cache()
    return JSONResponse(res)


def api_admin_phone_ban(request: Request, body: dict | None = None):
    """Админ: {phone, action: ban | unban} — всё от номера скрыть/удалить и не принимать, или вернуть."""
    _, err = _need_admin(request)
    if err:
        return err
    body = body or {}
    conn = db.get()
    if body.get("action") == "unban":
        ingest.unban_phone(conn, str(body.get("phone") or ""))
        res = {"ok": True}
    else:
        res = ingest.ban_phone(conn, str(body.get("phone") or ""), block=body.get("action") != "delete")
        if "error" in res:
            return _err(res["error"])
    _bump_cache()
    return JSONResponse(res)


def api_admin_phones(request: Request, body: dict | None = None):
    """База номеров: ?phone= — всё по номеру (объекты, запросы, сообщения); иначе список номеров (?q= — поиск по цифрам)."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    q = request.query_params
    banned = ingest.banned_phones(conn)
    if q.get("phone"):
        from . import rules as _rules
        phone = _rules.normalize_phone(q["phone"]) or q["phone"]
        rows = conn.execute("""SELECT l.id, l.title, l.price, l.deal, l.district, l.complex, l.street, l.house, l.is_active,
                                      l.hidden_reason, l.first_seen, l.last_seen, l.seen_count, l.source, l.photos
                               FROM listings l, json_each(l.phones) j WHERE j.value = ? ORDER BY l.is_active DESC, l.last_seen DESC""",
                            (phone,)).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            ph = json.loads(d.pop("photos") or "[]")
            d["photo"] = ph[0] if ph else None
            items.append(d)
        reqs = conn.execute("""SELECT b.id, b.text, b.last_seen, b.is_active FROM buyer_requests b, json_each(b.phones) j
                               WHERE j.value = ? ORDER BY b.last_seen DESC LIMIT 50""", (phone,)).fetchall()
        msgs = conn.execute("SELECT COUNT(*) FROM messages WHERE sender LIKE ?", (f"%{phone[-10:]}%",)).fetchone()[0]
        return JSONResponse({"phone": phone, "banned": phone in banned, "listings": items,
                             "requests": [dict(r) for r in reqs], "messages": msgs})
    digits = re.sub(r"\D", "", q.get("q", ""))[-10:]
    rows = conn.execute(f"""SELECT j.value AS phone, COUNT(*) AS n, SUM(l.is_active) AS active, MAX(l.last_seen) AS last_seen
                            FROM listings l, json_each(l.phones) j {"WHERE j.value LIKE ?" if digits else ""}
                            GROUP BY j.value ORDER BY active DESC, n DESC LIMIT 300""",
                         ([f"%{digits}%"] if digits else [])).fetchall()
    return JSONResponse({"items": [{**dict(r), "banned": r["phone"] in banned} for r in rows]})


def api_admin_banned(request: Request, body: dict | None = None):
    """Удалённые админом объекты — список для «Восстановить»."""
    _, err = _need_admin(request)
    if err:
        return err
    rows = db.get().execute("""SELECT l.id, l.title, l.price, l.deal, l.district, l.complex, MAX(b.ts) AS ts, COUNT(b.key) AS keys
                               FROM banned b JOIN listings l ON l.id = b.listing_id GROUP BY l.id ORDER BY ts DESC LIMIT 500""").fetchall()
    phones = db.get().execute("""SELECT b.key AS phone, b.ts,
                                     (SELECT COUNT(*) FROM listings l, json_each(l.phones) j
                                      WHERE j.value = b.key AND l.hidden_reason = 'phone') AS listings
                                  FROM banned b WHERE b.kind = 'phone' ORDER BY b.ts DESC""").fetchall()
    return JSONResponse({"items": [dict(r) for r in rows], "phones": [dict(r) for r in phones]})


def api_admin_fix(request: Request, body: dict | None = None):
    """Экран разбора /fix: следующий объект очереди (без уже разобранных и пропущенных в этой сессии)
    или конкретный объект (?id=). mode: place (ЖК/район) | geo (карта)."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    learning.sync_districts(conn)
    q = request.query_params
    kind = brain.queue_key(q.get("mode"), q.get("kind"))   # одна очередь «Обучения» (старые ссылки тоже понимаем)
    mode, where = brain.QUEUE[kind]["mode"], brain.QUEUE[kind]["where"]
    if q.get("at"):   # район под точкой (метку двигают на карте разбора)
        try:
            lat, lon = (float(x) for x in q["at"].split(",")[:2])
        except ValueError:
            return _err("Неверная точка.")
        hit = districtmap.district_at(conn, lat, lon)
        return JSONResponse({"district": hit[0] if hit and hit[1] == "district" else None,
                             "complex": districtmap.complex_at(conn, lat, lon)})
    left = conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND {where}").fetchone()[0]
    exclude = {int(x) for x in q.get("exclude", "").split(",") if x.strip().isdigit()}
    if request.method == "POST":   # разобранные/пропущенные — в теле запроса, не в адресе (длина адреса ограничена)
        exclude |= {int(x) for x in ((body or {}).get("exclude") or []) if str(x).isdigit()}
    group = None
    if q.get("id"):
        item = _fix_item(conn, _num(q.get("id"), int) or 0)   # конкретный объект (из базы номера, из окна объекта)
        if item and kind not in ("auto", "fixed"):
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (item["id"],)).fetchone()
            group = _group_of(conn, mode, kind, where, row, exclude)
    elif kind in ("auto", "fixed"):   # проверка выборочно — по одному, свежие первыми
        ex = list(exclude)[:2000]
        ph = ",".join("?" * len(ex)) or "0"
        row = conn.execute(f"""SELECT l.id FROM listings l WHERE {_VISIBLE} AND {where} AND l.id NOT IN ({ph})
                               ORDER BY l.last_seen DESC LIMIT 1""", ex).fetchone()
        item = _fix_item(conn, row[0]) if row else None
    else:
        groups = _fix_groups(conn, mode, kind, where, exclude)
        item = _fix_item(conn, groups[0]["ids"][0]) if groups else None
        if groups and len(groups[0]["ids"]) > 1:
            group = groups[0]
    if group:
        gi = group["ids"][:500]
        ph = ",".join("?" * min(len(gi), 60))
        rows_g = conn.execute(f"SELECT id, title, street, house, complex, district FROM listings WHERE id IN ({ph})", gi[:60]).fetchall()
        group = {"label": group["label"], "ids": gi, "count": len(group["ids"]),
                 "same_place": not group["key"].startswith("street:"),
                 "items": [{"id": g["id"], "title": g["title"], "addr": ", ".join(x for x in (g["street"], g["house"]) if x),
                            "complex": g["complex"], "district": g["district"]} for g in rows_g]}
    complexes = sorted({c.name for _, c in geo._complexes()} | {r[0] for r in conn.execute(
        "SELECT DISTINCT complex FROM listings WHERE complex IS NOT NULL AND is_active = 1")},
        key=lambda x: x.lower().replace("ё", "е"))
    return JSONResponse({"item": item, "group": group, "left": left, "mode": mode, "kind": kind, "counts": brain.counts(conn),
                         "queues": [{"key": x["key"], "label": x["label"], "hint": x["hint"], "mode": x["mode"]}
                                    for x in brain.QUEUES],
                         "districts": geo.all_district_names(), "complexes": complexes})


def fix_page(request: Request, body: dict | None = None):
    return _page("fix.html")


def api_admin_geo_queue(request: Request, body: dict | None = None):
    """Объекты, которые не попали на карту (или стоят примерно), — разбирать по одному и учить сервис."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    kind = request.query_params.get("kind", "none")
    where = _GEO_QUEUE.get(kind, _GEO_QUEUE["none"])
    after = _num(request.query_params.get("after"), int)   # «следующий» после этого объекта
    counts = {k: conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND {w}").fetchone()[0]
              for k, w in _GEO_QUEUE.items()}
    counts["learned"] = conn.execute("SELECT COUNT(*) FROM geo_learned").fetchone()[0]
    rows = conn.execute(
        f"""SELECT l.id, l.title, l.price, l.deal, l.district, l.complex, l.street, l.house, l.settlement, l.geo_status,
                   substr(COALESCE(l.fragment, l.description, ''), 1, 300) AS text
            FROM listings l WHERE {_VISIBLE} AND {where} {"AND l.id < ?" if after else ""}
            ORDER BY l.id DESC LIMIT 200""", ([after] if after else [])).fetchall()
    learned = [dict(r) for r in conn.execute("SELECT key, label, lat, lon, n, ts FROM geo_learned ORDER BY ts DESC LIMIT 200")]
    return JSONResponse({"items": [dict(r) for r in rows], "counts": counts, "learned": learned})


def api_clients(request: Request, body: dict | None = None):
    """CRM: GET — доска клиентов (?q=), ?id= — карточка; POST — сохранить клиента / {id, stage}; DELETE {id}."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    body = body or {}
    if request.method == "DELETE":
        clients.delete(conn, user["id"], _num(body.get("id"), int) or 0)
        return JSONResponse({"ok": True})
    if request.method == "POST":
        if _too_often(request, "clients", 600, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        if set(body) == {"id", "stage"}:
            res = clients.set_stage(conn, user["id"], _num(body["id"], int) or 0, str(body["stage"]))
        else:
            # у нового клиента сразу своя подборка (подборки — с доступом)
            res = clients.save(conn, user["id"], body, user if has_access(request, user) else None)
        return _err(res["error"]) if "error" in res else JSONResponse(res)
    q = request.query_params
    if q.get("id"):
        res = clients.detail(conn, user["id"], _num(q["id"], int) or 0)
        return _err(res["error"], 404) if "error" in res else JSONResponse(res)
    if q.get("listing"):
        return JSONResponse({"items": clients.for_listing(conn, user["id"], _num(q["listing"], int) or 0),
                             "clients": [{"id": c["id"], "name": c["name"], "stage": c["stage"]}
                                         for c in clients.board(conn, user["id"])["items"] if not c["archived"]]})
    return JSONResponse(clients.board(conn, user["id"], q.get("q", "").strip()[:100]))


def api_client_action(request: Request, body: dict | None = None):
    """POST /api/clients/{id}/items {listing_id, status|null} — объект клиенту; /note {text} — заметка; GET /match — подбор."""
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    cid = int(request.path_params["id"])
    what = request.path_params["what"]
    body = body or {}
    if what == "match":
        d = clients.detail(conn, user["id"], cid)
        if "error" in d:
            return _err(d["error"], 404)
        qp = request.query_params
        page = max(1, _num(qp.get("page"), int) or 1)
        sort = qp.get("sort") if qp.get("sort") in ("new", "price_asc", "market", "budget") else "new"
        c = d["client"]
        target = c.get("budget_max") or c.get("budget_min")
        qr = clients.query_for(c, user["id"], size=60, page=page, sort=sort, since=_num(qp.get("since"), int),
                               with_photo=qp.get("photo") == "1", budget_near=target,
                               **({"not_first": True} if qp.get("not_first") == "1" else {}))
        res = search.search(conn, qr, int(time.time()), has_access(request, user))
        # уже связанные с клиентом и «не подходит» в подбор не попадают — счётчик честный
        return JSONResponse({"items": res["items"], "total": res.get("total"), "linked": len(d["items"]),
                             "hidden": d["hidden"], "more": page * 60 < (res.get("total") or 0), "page": page})
    if what == "hidden":   # «Не подходит» — список, чтобы вернуть
        rows = conn.execute("""SELECT l.* , 0 AS verified FROM client_hidden h JOIN listings l ON l.id = h.listing_id
                               JOIN clients c ON c.id = h.client_id WHERE h.client_id = ? AND c.user_id = ?
                               ORDER BY h.ts DESC LIMIT 200""", (cid, user["id"])).fetchall()
        return JSONResponse({"items": [search.row_to_item(r, False) for r in rows]})
    if what in ("hide", "unhide"):
        res = clients.hide(conn, user["id"], cid, [int(x) for x in (body.get("ids") or []) if str(x).isdigit()], what == "hide")
        return _err(res["error"]) if "error" in res else JSONResponse(res)
    if what == "add":   # «Клиенту ▾»: объект — в активную подборку клиента
        if not has_access(request, user):
            return _err("Подборки — по подписке.", 403)
        res = clients.add_to_client(conn, user, cid, _num(body.get("listing_id"), int) or 0)
        return _err(res["error"]) if "error" in res else JSONResponse(res)
    if what == "topick":   # «Всё в подборку» — новые под клиента одной кнопкой
        if not has_access(request, user):
            return _err("Подборки — по подписке.", 403)
        res = clients.to_pick(conn, user, cid, [int(x) for x in (body.get("ids") or []) if str(x).isdigit()])
    elif what == "items":
        res = clients.set_item(conn, user["id"], cid, _num(body.get("listing_id"), int) or 0, body.get("status"))
    elif what == "note":
        res = clients.add_note(conn, user["id"], cid, body.get("text", ""))
    else:
        return _err("Неизвестное действие.", 404)
    return _err(res["error"]) if "error" in res else JSONResponse(res)


def clients_page(request: Request, body: dict | None = None):
    return _page("clients.html")


def api_dashboard(request: Request, body: dict | None = None):
    """Кабинет одним запросом: день, CRM, уведомления, рынок, погода, ставки."""
    user, err = _need_user(request)
    if err:
        return err
    return JSONResponse(dashboard.build(db.get(), user))


def api_widgets(request: Request, body: dict | None = None):
    """Погода и ставки — и для гостей (главная, кабинет до входа)."""
    return JSONResponse(widgets.all_widgets(db.get()))


def api_admin_banks(request: Request, body: dict | None = None):
    """Ставки банков по ипотеке (ведёт админ): POST {items: [{bank, secondary, new, family, ihc}]} — сохранить;
    {text, program} — разобрать вставленный текст обзора (ничего не сохраняет: админ проверяет и жмёт «Сохранить»)."""
    _, err = _need_admin(request)
    if err:
        return err
    body = body or {}
    if "text" in body:
        db.flush(db.get())   # разбор может спросить нейросеть — базу не держим
        res = bankrates.parse(str(body.get("text") or ""), str(body.get("program") or "secondary"))
        return JSONResponse(res)
    return JSONResponse(widgets.set_banks(db.get(), body.get("items") or [], body.get("avg")))


def api_admin_learn(request: Request, body: dict | None = None):
    """«Обучение»: сводка, единая очередь с числами, цепочка решений, выученное, перепроверка."""
    _, err = _need_admin(request)
    if err:
        return err
    return JSONResponse(brain.overview(db.get()))


def api_admin_knowledge(request: Request, body: dict | None = None):
    """Всё выученное одним списком (GET ?kind=&q=); забыть (POST {type: rule|point, id})."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        b = body or {}
        return JSONResponse({"ok": brain.forget(conn, str(b.get("type", "")), b.get("id"))})
    q = request.query_params
    return JSONResponse({"items": brain.knowledge(conn, q.get("kind", ""), q.get("q", "").strip()),
                         "kinds": brain.KNOW_KINDS})


def learn_page(request: Request, body: dict | None = None):
    return _page("learn.html")


def api_admin_recheck(request: Request, body: dict | None = None):
    """Перепроверка всех объектов по справочнику адресов и нарисованным районам: {action: run | rollback}."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if (body or {}).get("action") == "rollback":
        n = autolearn.rollback_recheck(conn)
        _bump_cache()
        return JSONResponse({"ok": True, "reverted": n})
    rep = autolearn.recheck_all(conn)
    _bump_cache()
    return JSONResponse({"ok": True, **rep})


def api_admin_geo_audit(request: Request, body: dict | None = None):
    """ТЗ «админка-гео» P0.0: GET — отчёт (ничего не меняет); POST {rules: [id], junk: [имя], labels: bool} — применить
    выбранное админом (каждое изменение объекта — в журнал правок, откат по объекту)."""
    _, err = _need_admin(request)
    if err:
        return err
    from . import geoaudit
    conn = db.get()
    if request.method == "POST":
        body = body or {}
        res = geoaudit.apply(conn, [int(x) for x in body.get("rules") or [] if str(x).isdigit()],
                             [str(x) for x in body.get("junk") or []], bool(body.get("labels")),
                             broken=[int(x) for x in body.get("broken") or [] if str(x).isdigit()],
                             points=[str(x) for x in body.get("points") or []],
                             cx_points=[str(x) for x in body.get("cx_points") or []],
                             dupes=[str(x) for x in body.get("dupes") or []])
        _bump_cache()
        return JSONResponse({"ok": True, **res})
    return JSONResponse(geoaudit.report(conn))


def api_admin_geo_quality(request: Request, body: dict | None = None):
    """Панель качества на /learn: сейчас, неделю назад, по дням."""
    _, err = _need_admin(request)
    if err:
        return err
    from . import geoquality
    if request.query_params.get("outside"):   # карта районов: точки домов вне всех контуров
        return JSONResponse({"items": geoquality.outside_points(db.get())})
    return JSONResponse(geoquality.dashboard(db.get()))


def api_admin_edits(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        ok = agent.revert_edit(conn, _num((body or {}).get("id"), int) or 0)
        if ok:
            _bump_cache()
        return JSONResponse({"ok": ok})
    lid = _num(request.query_params.get("listing"), int)
    if lid:   # история одного объекта: что, кем/чем, когда (ТЗ «админка-гео» P0.3)
        rows = conn.execute("""SELECT e.*, u.name AS u_name, u.email AS u_email FROM listing_edits e
                               LEFT JOIN users u ON u.id = e.user_id WHERE e.listing_id = ? ORDER BY e.id DESC LIMIT 200""",
                            (lid,)).fetchall()
        return JSONResponse({"items": [{"id": r["id"], "ts": r["ts"], "field": r["field"], "old": json.loads(r["old"] or "null"),
                                        "new": json.loads(r["new"] or "null"), "reverted": r["reverted"],
                                        "who": r["src"] or ("сервис" if r["user_id"] == 0 else (r["u_name"] or r["u_email"] or "пользователь"))}
                                       for r in rows]})
    return JSONResponse({"items": agent.edits_for_admin(conn)})


# ─── отладка (только админ) ────────────────────────────────────────────────
def api_parse(request: Request, body: dict | None = None):
    """Проверить разбор текста, ничего не сохраняя (только админ: тратит запросы к нейросети)."""
    _, err = _need_admin(request)
    if err:
        return err
    body = body or {}
    kind, objs = parser.parse(str(body.get("text", ""))[:8000])
    return JSONResponse({"kind": kind, "objects": [o.to_dict() for o in objs]})


def api_add_message(request: Request, body: dict | None = None):
    """Добавить сообщение вручную (например, переслать объект, которого нет в чатах). Только админ."""
    _, err = _need_admin(request)
    if err:
        return err
    body = body or {}
    conn = db.get()
    mid = ingest.add_message(conn, source="manual", text=str(body.get("text", ""))[:8000], chat_name="вручную")
    if mid is None:
        return JSONResponse({"status": "duplicate"})
    return JSONResponse(ingest.process_message(conn, mid))



def robots_txt(request: Request, body: dict | None = None):
    """Поисковикам: главная, объекты и открытые страницы — можно; кабинеты, админка, API и подборки клиентов — нет."""
    lines = ["User-agent: *", "Allow: /", "Disallow: /api/", "Disallow: /admin", "Disallow: /fix", "Disallow: /learn",
             "Disallow: /c/", "Disallow: /cabinet", "Disallow: /clients", "Disallow: /picks", "Disallow: /planner",
             "Disallow: /work", "Disallow: /my", "Disallow: /settings", "Disallow: /notifications", "Disallow: /pay",
             "Disallow: /*?ref=", "Disallow: /*?open=", "Disallow: /*&open=", "", f"Sitemap: {config.SITE_URL}/sitemap.xml"]
    return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8")


def sitemap_xml(request: Request, body: dict | None = None):
    """Карта сайта: открытые страницы и все объекты на сайте (до 45 000)."""
    from xml.sax.saxutils import escape
    base = config.SITE_URL
    urls = [(f"{base}/", None), (f"{base}/how", None), (f"{base}/market", None), (f"{base}/privacy", None), (f"{base}/terms", None),
            (f"{base}/offer", None), (f"{base}/consent", None), (f"{base}/contacts", None)]
    # карточки объектов в карту сайта не входят: в них данные агентов из чатов — в поиск только открытые страницы
    body_xml = "".join(f"<url><loc>{escape(u)}</loc>"
                       + (f"<lastmod>{time.strftime('%Y-%m-%d', time.gmtime(ts))}</lastmod>" if ts else "") + "</url>"
                       for u, ts in urls)
    return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                    + body_xml + "</urlset>", media_type="application/xml; charset=utf-8",
                    headers={"Cache-Control": "public, max-age=3600"})


def favicon_ico(request: Request, body: dict | None = None):
    """Старые браузеры и Яндекс просят /favicon.ico — отдаём значок 1+1 (PNG подходит)."""
    return FileResponse(WEB / "icon-192.png", media_type="image/png", headers={"Cache-Control": "public, max-age=604800"})


_NOT_FOUND_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Страница не найдена · 1+1</title>
<meta name="robots" content="noindex"><link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#03302f;color:#f3e8bc;
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center;padding:16px}
a{color:#035352;background:#f3e8bc;border-radius:999px;padding:10px 20px;text-decoration:none;font-weight:600;display:inline-block;margin:6px}
a.l{background:transparent;color:#f3e8bc;border:1px solid rgba(243,232,188,.4)}b{font-size:56px;display:block;line-height:1}</style></head>
<body><main><b>404</b><h1>Такой страницы нет</h1><p>Возможно, ссылка устарела или объект уже снят с продажи.</p>
<a href="/">К поиску объектов</a><a class="l" href="/how">Как это работает</a></main></body></html>"""


async def _not_found(request: Request, exc: Exception):
    """404: для страниц — понятная страница с шапкой и ссылкой на поиск; для API — как раньше, JSON."""
    if request.url.path.startswith(("/api/", "/static/", "/photos/")):
        return JSONResponse({"detail": "не найдено"}, status_code=404)
    return Response(_NOT_FOUND_HTML, status_code=404, media_type="text/html; charset=utf-8")


routes = [
    Route("/", threaded(index)),
    Route("/admin", threaded(admin_page)),
    Route("/how", threaded(how_page)),
    Route("/privacy", threaded(privacy_page)),
    Route("/terms", threaded(terms_page)),
    Route("/offer", threaded(offer_page)),
    Route("/consent", threaded(consent_page)),
    Route("/contacts", threaded(contacts_page)),
    Route("/api/meta", threaded(api_meta)),
    Route("/api/listings", threaded(api_listings)),
    Route("/api/listings/{id:int}", threaded(api_listing)),
    Route("/api/facets", threaded(api_facets)),
    Route("/api/map", threaded(api_map)),
    Route("/api/districts/map", threaded(api_districts_map)),
    Route("/api/admin/recheck", threaded(api_admin_recheck), methods=["POST"]),
    Route("/api/admin/learn", threaded(api_admin_learn)),
    Route("/api/dashboard", threaded(api_dashboard)),
    Route("/api/clients", threaded(api_clients), methods=["GET", "POST", "DELETE"]),
    Route("/api/clients/{id:int}/{what}", threaded(api_client_action), methods=["GET", "POST"]),
    Route("/clients", threaded(clients_page)),
    Route("/api/widgets", threaded(api_widgets)),
    Route("/api/admin/banks", threaded(api_admin_banks), methods=["POST"]),
    Route("/api/admin/learn/knowledge", threaded(api_admin_knowledge), methods=["GET", "POST"]),
    Route("/learn", threaded(learn_page)),
    Route("/api/stats", threaded(api_stats)),
    Route("/api/login", threaded(api_login), methods=["POST"]),
    Route("/api/auth/tg/start", threaded(api_tg_start), methods=["POST"]),
    Route("/api/auth/tg/status", threaded(api_tg_status)),
    Route("/api/auth/email/start", threaded(api_email_start), methods=["POST"]),
    Route("/api/auth/email/verify", threaded(api_email_verify), methods=["POST"]),
    Route("/api/auth/logout", threaded(api_logout), methods=["POST"]),
    Route("/api/auth/phone", threaded(api_phone_login), methods=["GET", "POST"]),
    Route("/api/account/email", threaded(api_account_email), methods=["POST"]),
    Route("/api/me", threaded(api_me)),
    Route("/api/favorites", threaded(api_favorites)),
    Route("/api/hidden", threaded(api_hidden), methods=["GET", "POST"]),
    Route("/api/favorites/{id:int}", threaded(api_favorite_toggle), methods=["POST"]),
    Route("/api/promo", threaded(api_promo), methods=["POST"]),
    Route("/api/complaints", threaded(api_complaint), methods=["POST"]),
    Route("/api/pay", threaded(api_pay), methods=["GET", "POST"]),
    Route("/pay", threaded(pay_page)),
    Route("/api/payments/yookassa", threaded(api_yookassa), methods=["POST"]),
    Route("/api/admin/overview", threaded(api_admin_overview)),
    Route("/api/admin/mailtest", threaded(api_admin_mailtest), methods=["POST"]),
    Route("/api/admin/billing", threaded(api_admin_billing), methods=["POST"]),
    Route("/api/admin/online", threaded(api_admin_online)),
    Route("/api/ping", threaded(api_ping), methods=["POST"]),
    Route("/api/admin/stats", threaded(api_admin_stats)),
    Route("/api/admin/users", threaded(api_admin_users)),
    Route("/api/admin/users/{id:int}", threaded(api_admin_user_action), methods=["POST"]),
    Route("/api/admin/promos", threaded(api_admin_promos), methods=["GET", "POST", "DELETE"]),
    Route("/api/admin/optouts", threaded(api_admin_optouts), methods=["GET", "POST"]),
    Route("/api/admin/complaints", threaded(api_admin_complaints), methods=["GET", "POST"]),
    Route("/api/parse", threaded(api_parse), methods=["POST"]),
    Route("/api/messages", threaded(api_add_message), methods=["POST"]),
    Route("/api/notes/{id:int}", threaded(api_note), methods=["POST"]),
    Route("/api/worklog", threaded(api_worklog), methods=["GET", "POST", "DELETE"]),
    Route("/api/saved", threaded(api_saved), methods=["GET", "POST", "DELETE"]),
    Route("/api/notices", threaded(api_notices), methods=["GET", "POST"]),
    Route("/notifications", threaded(notices_page)),
    Route("/api/notices/peek", threaded(api_notices_peek)),
    Route("/market", threaded(market_page)),
    Route("/planner", threaded(planner_page)),
    Route("/requests", threaded(requests_page)),
    Route("/api/requests", threaded(api_requests)),
    Route("/picks", threaded(picks_page)),
    Route("/work", threaded(work_page)),
    Route("/compare", threaded(compare_page)),
    Route("/settings", threaded(settings_page)),
    Route("/sw.js", threaded(service_worker)),
    Route("/my", threaded(my_page)),
    Route("/my/edit", threaded(my_page)),
    Route("/api/settings", threaded(api_settings), methods=["GET", "POST"]),
    Route("/cabinet", threaded(cabinet_page)),
    Route("/c/{token}", threaded(pick_page)),
    Route("/api/picks", threaded(api_picks), methods=["GET", "POST", "DELETE"]),
    Route("/api/picks/{id:int}/items", threaded(api_pick_items), methods=["POST"]),
    Route("/api/picks/{id:int}/similar", threaded(api_pick_similar)),
    Route("/api/c/consent", threaded(api_cookie_consent), methods=["POST"]),
    Route("/api/c/{token}", threaded(api_pick_public)),
    Route("/api/c/{token}/react", threaded(api_pick_react), methods=["POST"]),
    Route("/planner/{uid:int}-{token}.ics", threaded(planner_feed)),
    Route("/api/planner", threaded(api_planner), methods=["GET", "POST", "DELETE"]),
    Route("/api/planner/notes", threaded(api_planner_note), methods=["POST"]),
    Route("/api/planner/{id:int}.ics", threaded(api_planner_ics)),
    Route("/api/market", threaded(api_market)),
    Route("/saved/off", threaded(saved_off_page)),
    Route("/api/agent", threaded(api_agent)),
    Route("/api/agent/verify", threaded(api_agent_verify), methods=["GET", "POST"]),
    Route("/api/agent/listings", threaded(api_agent_create), methods=["POST"]),
    Route("/api/agent/listings/{id:int}", threaded(api_agent_edit), methods=["POST"]),
    Route("/api/agent/listings/{id:int}/photos", threaded(api_agent_photo), methods=["POST", "DELETE"]),
    Route("/api/agent/describe", threaded(api_agent_describe), methods=["POST"]),
    Route("/agent/confirm", threaded(agent_confirm_page)),
    Route("/api/admin/edits", threaded(api_admin_edits), methods=["GET", "POST"]),
    Route("/api/admin/chats", threaded(api_admin_chats), methods=["GET", "POST"]),
    Route("/api/admin/listings/{id:int}/geo", threaded(api_admin_geo), methods=["POST"]),
    Route("/api/admin/geo-quality", threaded(api_admin_geo_quality)),
    Route("/api/admin/geo-queue", threaded(api_admin_geo_queue)),
    Route("/api/admin/fix", threaded(api_admin_fix), methods=["GET", "POST"]),
    Route("/api/admin/geo-audit", threaded(api_admin_geo_audit), methods=["GET", "POST"]),
    Route("/api/admin/dir", threaded(api_admin_dir), methods=["GET", "POST"]),
    Route("/api/admin/reconcile", threaded(api_admin_reconcile), methods=["GET", "POST"]),
    Route("/admin/map", threaded(districts_map_page)),
    Route("/api/admin/districts", threaded(api_admin_districts), methods=["GET", "POST"]),
    Route("/fix", threaded(fix_page)),
    Route("/api/admin/listings/{id:int}/action", threaded(api_admin_listing_action), methods=["POST"]),
    Route("/api/admin/banned", threaded(api_admin_banned)),
    Route("/api/admin/phones/ban", threaded(api_admin_phone_ban), methods=["POST"]),
    Route("/api/admin/phones", threaded(api_admin_phones)),
    Route("/api/admin/listings/{id:int}/place", threaded(api_admin_place), methods=["POST"]),
    Route("/api/admin/place-queue", threaded(api_admin_place_queue), methods=["GET", "POST"]),
    Route("/robots.txt", threaded(robots_txt)),
    Route("/sitemap.xml", threaded(sitemap_xml)),
    Route("/favicon.ico", threaded(favicon_ico)),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
    Route("/photos/{folder}/{name}", threaded(photo_file)),
]

async def _server_error(request: Request, exc: Exception):
    """Любая непредвиденная ошибка — понятный ответ (а не «Ошибка 500») и подробности в журнал."""
    log.error("ошибка %s %s", request.method, request.url.path, exc_info=exc)
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
        return _err("Сайт сейчас обновляет базу — попробуйте ещё раз через минуту.", 503)
    return _err(f"Что-то пошло не так на сервере ({type(exc).__name__}). Попробуйте ещё раз; если повторится — "
                f"пришлите скриншот.", 500)


# Защита браузера от чужих скриптов (CSP): код страниц — только с сайта и двух проверенных CDN; встроенных
# обработчиков onclick и eval нет. Стили inline разрешены (style="…" в разметке), картинки — с любых https (фото фида).
CSP = ("default-src 'self'; "
       "script-src 'self' https://cdnjs.cloudflare.com https://unpkg.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com https://unpkg.com; "
       "font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob: https:; "
       "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; worker-src 'self'; manifest-src 'self'; "
       "object-src 'none'; base-uri 'self'; form-action 'self'")


class _CSP:
    """Добавляет заголовок CSP ко всем ответам сайта."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                headers = [h for h in msg.get("headers", []) if h[0].lower() != b"content-security-policy"]
                headers.append((b"content-security-policy", CSP.encode()))
                msg = {**msg, "headers": headers}
            await send(msg)
        return await self.inner(scope, receive, send2)


app = _CSP(Starlette(routes=routes, exception_handlers={404: _not_found, Exception: _server_error}))

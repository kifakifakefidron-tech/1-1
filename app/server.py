"""Веб-сервер: страница поиска, личный кабинет, админка + API.
Запуск: uvicorn app.server:app --port 8090"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import (accounts, agent, config, db, districtmap, geo, geocode, hooks, ingest, learning, mailer, notices, parser,
               market, payments, picks, planner, region, search, stats, tg)
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


def _too_often(request: Request, key: str, limit: int, per_s: int) -> bool:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    q = _hits[f"{key}:{ip}"]
    now = time.time()
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


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
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
    if not oid:
        return FileResponse(WEB / "index.html", headers=headers)
    # Ссылка на объект («Поделиться»): превью в WhatsApp/Telegram — название, цена, место, фото. Без телефонов.
    html_text = (WEB / "index.html").read_text(encoding="utf-8")
    r = db.get().execute("SELECT * FROM listings WHERE id = ?", (oid,)).fetchone()
    if r is not None:
        place = " · ".join(x for x in (f"ЖК {r['complex']}" if r["complex"] else None, r["district"],
                                       f"ул. {r['street']}" if r["street"] else None) if x)
        title = f"{r['title']} — {_price_text(r['price'], r['deal'])}"
        photos = json.loads(r["photos"] or "[]")
        img = photos[0] if photos else "/static/favicon.svg"
        if img.startswith("/"):
            img = config.SITE_URL + img
        esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
        meta = (f'<meta property="og:type" content="website">'
                f'<meta property="og:title" content="{esc(title)}">'
                f'<meta property="og:description" content="{esc(place or "Объект на 1+1")}">'
                f'<meta property="og:image" content="{esc(img)}">'
                f'<meta property="og:url" content="{esc(config.SITE_URL)}/?open={oid}">'
                f'<meta property="og:site_name" content="1+1 · поиск объектов">')
        html_text = html_text.replace("<title>1+1 · поиск объектов</title>",
                                      f"<title>{esc(title)} · 1+1</title>{meta}", 1)
    return Response(html_text, media_type="text/html; charset=utf-8", headers=headers)


def _page(name: str) -> Response:
    """Текстовая страница с подстановкой данных оператора и условий подписки."""
    html = (WEB / name).read_text(encoding="utf-8")
    for key, val in {
        "OPERATOR_NAME": config.OPERATOR_NAME, "OPERATOR_INN": config.OPERATOR_INN,
        "CONTACT_EMAIL": config.CONTACT_EMAIL or "почту из раздела «Контакты»", "POLICY_DATE": config.POLICY_DATE,
        "SITE_URL": config.SITE_URL, "TRIAL_DAYS": config.TRIAL_DAYS, "SUB_PRICE": config.SUB_PRICE,
        "SUB_DAYS": config.SUB_DAYS, "STALE_DAYS": config.STALE_DAYS,
    }.items():
        html = html.replace("{{" + key + "}}", str(val))
    return Response(html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def how_page(request: Request, body: dict | None = None):
    return _page("how.html")


def privacy_page(request: Request, body: dict | None = None):
    return _page("privacy.html")


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
        "payments": payments.available(),
        "price": config.SUB_PRICE, "period_days": config.SUB_DAYS, "trial_days": config.TRIAL_DAYS,
        "bot": bot_username(),
        "me": accounts.me(db.get(), user),
    })


# Короткий кэш одинаковых запросов (поиск, фильтры, карта одинаковы для всех посетителей):
# при наплыве людей одни и те же популярные выдачи не считаются заново каждый раз
_cache: dict[str, tuple[float, bytes]] = {}
_cache_lock = threading.Lock()
CACHE_S = 15


def _cached(request: Request, compute) -> Response:
    # cache_ver меняется при правках агентов/админа — второй процесс сайта тоже сразу видит новое
    key = f"{db.get_state(db.get(), 'cache_ver', '0')}:{request.url.path}?{request.url.query}"
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_S:
        return Response(hit[1], media_type="application/json")
    body = json.dumps(compute(), ensure_ascii=False).encode()
    with _cache_lock:
        if len(_cache) > 2000:
            for k in [k for k, (t, _) in _cache.items() if now - t >= CACHE_S] or list(_cache)[:1000]:
                _cache.pop(k, None)
        _cache[key] = (now, body)
    return Response(body, media_type="application/json")


def _bump_cache() -> None:
    try:
        db.set_state(db.get(), "cache_ver", str(time.time()))
    except Exception:  # noqa: BLE001 — база занята: кэш и так обновится через CACHE_S
        pass


def api_listings(request: Request, body: dict | None = None):
    # В списке телефонов нет никогда — только в карточке, по одному объекту
    def compute():
        now = int(time.time())
        res = search.search(db.get(), query_from(request), now, False)
        hooks.annotate(db.get(), res["items"])   # «ниже рынка на N %»
        res["now"] = now  # от этого момента страница считает «новые объекты»
        return res
    if request.query_params.get("since"):
        return JSONResponse(compute())
    return _cached(request, compute)


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
    d["can_edit"] = bool(user) and agent.can_edit(conn, user, row)[0]
    d["market"] = hooks.market_for(conn, d)
    d["same"] = hooks.same_elsewhere(conn, row)
    d["note"] = hooks.get_note(conn, user["id"], lid) if user else None
    d["status"] = hooks.get_status(conn, user["id"], lid) if user else None
    d["plans"] = planner.for_listing(conn, user["id"], lid) if user else []
    d["favorite"] = bool(user and conn.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND listing_id = ?", (user["id"], lid)).fetchone())
    return JSONResponse(d)


def _raw_phones(conn, lid: int) -> list[str]:
    row = conn.execute("SELECT phones FROM listings WHERE id = ?", (lid,)).fetchone()
    return json.loads(row["phones"] or "[]") if row else []


def api_facets(request: Request, body: dict | None = None):
    return _cached(request, lambda: search.facets(db.get(), query_from(request), int(time.time())))


def api_map(request: Request, body: dict | None = None):
    return _cached(request, lambda: search.map_points(db.get(), query_from(request), int(time.time())))


def api_stats(request: Request, body: dict | None = None):
    conn = db.get()
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    day = int(time.time()) - 86400
    return JSONResponse({
        "active": one("SELECT COUNT(*) FROM listings WHERE is_active=1"),
        "new_24h": one("SELECT COUNT(*) FROM listings WHERE first_seen >= ?", day),
        "messages_24h": one("SELECT COUNT(*) FROM messages WHERE ts >= ?", day),
        "duplicates_merged": one("SELECT COUNT(*) FROM listing_events WHERE match != 'new'"),
    })


# ─── промокод для коллег (временный) ───────────────────────────────────────
def api_login(request: Request, body: dict | None = None):
    if _too_often(request, "promo", 10, 600):
        return _err("Слишком много попыток — подождите 10 минут.", 429)
    body = body or {}
    code = str(body.get("code", "")).strip().encode()  # bytes: код может быть по-русски
    if config.ACCESS_CODE and hmac.compare_digest(code, config.ACCESS_CODE.encode()):
        conn = db.get()
        user = current_user(request) or accounts.create_promo_user(conn)
        token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
        resp = _session_response(request, {"ok": True, "me": accounts.me(conn, accounts.get_user(conn, user["id"]))},
                                 token)
        resp.set_cookie(COOKIE, _sign(config.ACCESS_CODE), max_age=180 * 86400, httponly=True, samesite="lax")
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
        token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
        return _session_response(request, {"status": "ok", "me": accounts.me(conn, user)}, token)
    return JSONResponse({"status": row["status"]})


# ─── вход по почте ─────────────────────────────────────────────────────────
def api_email_start(request: Request, body: dict | None = None):
    if not mailer.available():
        return _err("Вход по почте пока не настроен.", 503)
    if _too_often(request, "email", 5, 3600):
        return _err("Слишком много запросов кода — попробуйте позже.", 429)
    body = body or {}
    email = str(body.get("email", ""))[:200]
    code, err = accounts.new_email_code(db.get(), email)
    if err:
        return _err(err)
    # Отправка письма — в фоне: пока почта отвечает, сайт для остальных не подвисает
    if not mailer.send_code(email.strip().lower(), code):
        return _err("Письмо не отправилось. Попробуйте ещё раз через минуту или войдите через Telegram.", 502)
    return JSONResponse({"ok": True})


def api_email_verify(request: Request, body: dict | None = None):
    if _too_often(request, "email_verify", 20, 600):
        return _err("Слишком много попыток — подождите 10 минут.", 429)
    body = body or {}
    conn = db.get()
    email = str(body.get("email", ""))[:200]
    ok, err = accounts.check_email_code(conn, email, str(body.get("code", ""))[:12])
    if not ok:
        return _err(err)
    user = accounts.upsert_email_user(conn, email)
    if user["blocked"]:
        return _err("Аккаунт заблокирован.", 403)
    token = accounts.create_session(conn, user["id"], request.headers.get("user-agent", ""))
    return _session_response(request, {"ok": True, "me": accounts.me(conn, user)}, token)


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
    return JSONResponse({"me": accounts.me(db.get(), current_user(request))})


def api_favorites(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    # Избранное и объекты «в работе» (со статусом) — вместе: это рабочий список агента
    rows = db.get().execute(f"""
        SELECT l.*, n.status AS crm_status, n.text AS crm_note, COALESCE(n.ts, f.created) AS crm_ts,
               {search.VERIFIED} AS verified
        FROM listings l
        LEFT JOIN favorites f ON f.listing_id = l.id AND f.user_id = ?
        LEFT JOIN notes n ON n.listing_id = l.id AND n.user_id = ?
        WHERE f.user_id IS NOT NULL OR n.status IS NOT NULL
        ORDER BY crm_ts DESC""", (user["id"], user["id"])).fetchall()
    items = []
    for r in rows:
        it = search.row_to_item(r, False)
        it.update(status=r["crm_status"], note=r["crm_note"] or "", favorite=True)
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
    conn.execute("INSERT INTO complaints (listing_id, user_id, reason, text, ts) VALUES (?,?,?,?,?)",
                 (_num(body.get("listing_id"), int), user["id"] if user is not None else None,
                  str(body.get("reason", ""))[:40], str(body.get("text", ""))[:1000], int(time.time())))
    conn.commit()
    return JSONResponse({"ok": True})


# ─── оплата (подготовлено, выключено) ──────────────────────────────────────
def api_pay(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    if not payments.available():
        return _err("Оплата скоро появится.", 503)
    return JSONResponse({"url": payments.create(db.get(), user["id"])})


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


def api_admin_overview(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    now = int(time.time())
    return JSONResponse({
        "listings": one(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE}"),
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
    rows = conn.execute("""SELECT c.*, l.title FROM complaints c LEFT JOIN listings l ON l.id = c.listing_id
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
    else:
        res = picks.set_item(db.get(), user["id"], cid, int(body.get("listing_id") or 0), bool(body.get("add", True)),
                             body.get("note"))
    return _err(res["error"]) if "error" in res else JSONResponse(res)


def api_pick_public(request: Request, body: dict | None = None):
    d = picks.public(db.get(), request.path_params["token"])
    return JSONResponse(d) if d else _err("Подборка не найдена или удалена.", 404)


def pick_page(request: Request, body: dict | None = None):
    """Страница подборки для клиента + превью ссылки в мессенджерах (название, число объектов, фото)."""
    html_text = (WEB / "pick.html").read_text(encoding="utf-8")
    d = picks.public(db.get(), request.path_params["token"], count_view=False)
    if d:
        esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
        n = len(d["items"])
        photo = next((i["photos"][0] for i in d["items"] if i["photos"]), "/static/favicon.svg")
        if photo.startswith("/"):
            photo = config.SITE_URL + photo
        who = f" · {d['contact_name']}" if d["contact_name"] else ""
        desc = f"{n} {'объект' if n % 10 == 1 and n % 100 != 11 else 'объекта' if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 'объектов'}{who}"
        meta = (f'<meta property="og:type" content="website"><meta property="og:title" content="{esc(d["title"])}">'
                f'<meta property="og:description" content="{esc(desc)}"><meta property="og:image" content="{esc(photo)}">')
        html_text = html_text.replace("<title>Подборка · 1+1</title>", f"<title>{esc(d['title'])} · 1+1</title>{meta}", 1)
    return Response(html_text, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})


def picks_page(request: Request, body: dict | None = None):
    return _page("picks.html")


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
        if _too_often(request, "planner", 300, 3600):
            return _err("Слишком часто — попробуйте позже.", 429)
        res = planner.save_event(conn, user["id"], body)
        return _err(res["error"]) if "error" in res else JSONResponse({"event": res})
    q = request.query_params
    if q.get("from") and q.get("to"):
        return JSONResponse({"events": planner.events(conn, user["id"], int(q["from"]), int(q["to"]))})
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
    return _cached(request, lambda: market.report(db.get(), request.query_params.get("deal", "sale")))


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
        "own_days": config.OWN_LISTING_DAYS, "max_photos": config.MAX_PHOTOS, "items": _agent_items(conn, user),
    })


def api_agent_verify(request: Request, body: dict | None = None):
    user, err = _need_user(request)
    if err:
        return err
    conn = db.get()
    if request.method == "GET":
        return JSONResponse({"status": agent.verification_status(conn, user["id"], request.query_params.get("phone", ""))})
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
    """Фото, загруженные агентами (data/photos/<объект>/<имя>). Имя — хеш содержимого, кэшируем надолго."""
    name = request.path_params["name"]
    if not re.fullmatch(r"[0-9a-f]{16}\.(?:jpg|png|webp)", name):
        return _err("не найдено", 404)
    f = Path(config.PHOTOS_DIR) / str(request.path_params["id"]) / name
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
    if body.get("auto"):
        lat, lon, status = None, None, "pending"
    elif body.get("hide"):
        lat, lon, status = None, None, "manual"
    else:
        lat, lon = _num(body.get("lat")), _num(body.get("lon"))
        if lat is None or lon is None or not (40 < lat < 50 and 35 < lon < 45):
            return _err("Точка должна быть в Краснодарском крае.")
        lat, lon, status = round(lat, 6), round(lon, 6), "manual"
    conn.execute("UPDATE listings SET lat = ?, lon = ?, geo_status = ? WHERE id = ?", (lat, lon, status, lid))
    # Учимся: эта точка — для того же дома и ЖК во всех объявлениях (сейчас и в будущем)
    applied = 0
    if lat is not None:
        full = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
        applied = geocode.learn(conn, full, lat, lon)
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
}
_VISIBLE = "l.is_active = 1 AND (l.district IS NOT NULL OR l.complex IS NOT NULL OR l.street IS NOT NULL OR l.settlement IS NOT NULL)"


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
    learned = learning.learn(conn, row, cx, district, body.get("learn") or {}, extra)
    d = {**dict(row), "complex": cx, "district": district}
    d["title"] = make_title(d)
    d["search_text"] = make_search_text(d)
    conn.execute("""UPDATE listings SET complex = ?, district = ?, extra_districts = ?, search_text = ?, admin_fixed = 1,
                    geo_status = CASE WHEN geo_status IN ('manual', 'learned', 'feed') THEN geo_status ELSE 'pending' END
                    WHERE id = ?""", (cx, district, json.dumps(extra, ensure_ascii=False), d["search_text"], lid))
    _reindex(conn, lid, d["search_text"])
    for field, old, new in (("complex", row["complex"], cx), ("district", row["district"], district)):
        if old != new:
            conn.execute("INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new) VALUES (?,?,?,?,?,?)",
                         (lid, user["id"], int(time.time()), field, json.dumps(old, ensure_ascii=False),
                          json.dumps(new, ensure_ascii=False)))
    conn.commit()
    applied = learning.apply_to_existing(conn, skip_id=lid) if learned else 0
    return JSONResponse({"ok": True, "complex": cx, "district": district, "extra": extra, "learned": learned,
                         "applied": applied})


_PLACE_QUEUE = {
    "nodistrict": "l.district IS NULL AND l.settlement IS NULL AND l.admin_fixed = 0",
    "nocomplex": "l.complex IS NULL AND l.type IN ('flat', 'new') AND l.district IS NOT NULL AND l.admin_fixed = 0",
    "fixed": "l.admin_fixed = 1",
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
                              "source")} | {
        "text": text, "photo": photos[0] if photos else None,
        "extra_districts": json.loads(r["extra_districts"] or "[]"),
        "suggest": geo.suggestions(text, r["street"], r["complex"]),
        "learned": [label for _, label in geocode.learn_keys(r)],
    }


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
            return JSONResponse(districtmap.set_districts(conn, body.get("items") or [], body.get("remember_complex")))
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


def api_admin_fix(request: Request, body: dict | None = None):
    """Экран разбора /fix: следующий объект очереди (без уже разобранных и пропущенных в этой сессии)
    или конкретный объект (?id=). mode: place (ЖК/район) | geo (карта)."""
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    learning.sync_districts(conn)
    q = request.query_params
    mode = "geo" if q.get("mode") == "geo" else "place"
    queues = _GEO_QUEUE if mode == "geo" else _PLACE_QUEUE
    kind = q.get("kind") if q.get("kind") in queues else next(iter(queues))
    where = queues[kind]
    left = conn.execute(f"SELECT COUNT(*) FROM listings l WHERE {_VISIBLE} AND {where}").fetchone()[0]
    if q.get("id"):
        item = _fix_item(conn, _num(q.get("id"), int) or 0)
    else:
        exclude = [int(x) for x in q.get("exclude", "").split(",") if x.strip().isdigit()][:2000]
        ph = ",".join("?" * len(exclude)) or "0"
        row = conn.execute(f"""SELECT l.id FROM listings l WHERE {_VISIBLE} AND {where} AND l.id NOT IN ({ph})
                               ORDER BY l.last_seen DESC LIMIT 1""", exclude).fetchone()
        item = _fix_item(conn, row[0]) if row else None
    complexes = sorted({c.name for _, c in geo._complexes()} | {r[0] for r in conn.execute(
        "SELECT DISTINCT complex FROM listings WHERE complex IS NOT NULL AND is_active = 1")},
        key=lambda x: x.lower().replace("ё", "е"))
    return JSONResponse({"item": item, "left": left, "mode": mode, "kind": kind,
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


def api_admin_edits(request: Request, body: dict | None = None):
    _, err = _need_admin(request)
    if err:
        return err
    conn = db.get()
    if request.method == "POST":
        return JSONResponse({"ok": agent.revert_edit(conn, _num((body or {}).get("id"), int) or 0)})
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


routes = [
    Route("/", threaded(index)),
    Route("/admin", threaded(admin_page)),
    Route("/how", threaded(how_page)),
    Route("/privacy", threaded(privacy_page)),
    Route("/terms", threaded(terms_page)),
    Route("/api/meta", threaded(api_meta)),
    Route("/api/listings", threaded(api_listings)),
    Route("/api/listings/{id:int}", threaded(api_listing)),
    Route("/api/facets", threaded(api_facets)),
    Route("/api/map", threaded(api_map)),
    Route("/api/stats", threaded(api_stats)),
    Route("/api/login", threaded(api_login), methods=["POST"]),
    Route("/api/auth/tg/start", threaded(api_tg_start), methods=["POST"]),
    Route("/api/auth/tg/status", threaded(api_tg_status)),
    Route("/api/auth/email/start", threaded(api_email_start), methods=["POST"]),
    Route("/api/auth/email/verify", threaded(api_email_verify), methods=["POST"]),
    Route("/api/auth/logout", threaded(api_logout), methods=["POST"]),
    Route("/api/me", threaded(api_me)),
    Route("/api/favorites", threaded(api_favorites)),
    Route("/api/favorites/{id:int}", threaded(api_favorite_toggle), methods=["POST"]),
    Route("/api/promo", threaded(api_promo), methods=["POST"]),
    Route("/api/complaints", threaded(api_complaint), methods=["POST"]),
    Route("/api/pay", threaded(api_pay), methods=["POST"]),
    Route("/api/payments/yookassa", threaded(api_yookassa), methods=["POST"]),
    Route("/api/admin/overview", threaded(api_admin_overview)),
    Route("/api/admin/stats", threaded(api_admin_stats)),
    Route("/api/admin/users", threaded(api_admin_users)),
    Route("/api/admin/users/{id:int}", threaded(api_admin_user_action), methods=["POST"]),
    Route("/api/admin/promos", threaded(api_admin_promos), methods=["GET", "POST", "DELETE"]),
    Route("/api/admin/optouts", threaded(api_admin_optouts), methods=["GET", "POST"]),
    Route("/api/admin/complaints", threaded(api_admin_complaints), methods=["GET", "POST"]),
    Route("/api/parse", threaded(api_parse), methods=["POST"]),
    Route("/api/messages", threaded(api_add_message), methods=["POST"]),
    Route("/api/notes/{id:int}", threaded(api_note), methods=["POST"]),
    Route("/api/saved", threaded(api_saved), methods=["GET", "POST", "DELETE"]),
    Route("/api/notices", threaded(api_notices), methods=["GET", "POST"]),
    Route("/notifications", threaded(notices_page)),
    Route("/market", threaded(market_page)),
    Route("/planner", threaded(planner_page)),
    Route("/picks", threaded(picks_page)),
    Route("/c/{token}", threaded(pick_page)),
    Route("/api/picks", threaded(api_picks), methods=["GET", "POST", "DELETE"]),
    Route("/api/picks/{id:int}/items", threaded(api_pick_items), methods=["POST"]),
    Route("/api/c/{token}", threaded(api_pick_public)),
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
    Route("/agent/confirm", threaded(agent_confirm_page)),
    Route("/api/admin/edits", threaded(api_admin_edits), methods=["GET", "POST"]),
    Route("/api/admin/chats", threaded(api_admin_chats), methods=["GET", "POST"]),
    Route("/api/admin/listings/{id:int}/geo", threaded(api_admin_geo), methods=["POST"]),
    Route("/api/admin/geo-queue", threaded(api_admin_geo_queue)),
    Route("/api/admin/fix", threaded(api_admin_fix)),
    Route("/api/admin/dir", threaded(api_admin_dir), methods=["GET", "POST"]),
    Route("/api/admin/reconcile", threaded(api_admin_reconcile), methods=["GET", "POST"]),
    Route("/admin/map", threaded(districts_map_page)),
    Route("/api/admin/districts", threaded(api_admin_districts), methods=["GET", "POST"]),
    Route("/fix", threaded(fix_page)),
    Route("/api/admin/listings/{id:int}/place", threaded(api_admin_place), methods=["POST"]),
    Route("/api/admin/place-queue", threaded(api_admin_place_queue), methods=["GET", "POST"]),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
    Route("/photos/{id:int}/{name}", threaded(photo_file)),
]

async def _server_error(request: Request, exc: Exception):
    """Любая непредвиденная ошибка — понятный ответ (а не «Ошибка 500») и подробности в журнал."""
    log.error("ошибка %s %s", request.method, request.url.path, exc_info=exc)
    if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc):
        return _err("Сайт сейчас обновляет базу — попробуйте ещё раз через минуту.", 503)
    return _err(f"Что-то пошло не так на сервере ({type(exc).__name__}). Попробуйте ещё раз; если повторится — "
                f"пришлите скриншот.", 500)


app = Starlette(routes=routes, exception_handlers={Exception: _server_error})

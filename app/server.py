"""Веб-сервер: страница поиска, личный кабинет, админка + API.
Запуск: uvicorn app.server:app --port 8090"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import accounts, config, db, geo, ingest, mailer, parser, payments, search, tg
from .rules import TYPE_LABELS

WEB = Path(__file__).resolve().parent.parent / "web"
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
    async def endpoint(request: Request):
        t0 = time.perf_counter()
        body = await _body(request) if request.method in ("POST", "PUT", "DELETE", "PATCH") else None
        resp = await run_in_threadpool(fn, request, body)
        resp.headers["Server-Timing"] = f"app;dur={(time.perf_counter() - t0) * 1000:.0f}"
        return resp
    endpoint.__name__ = fn.__name__
    return endpoint


# ─── разбор параметров ─────────────────────────────────────────────────────
def _num(v: str | None, cast=float):
    if v in (None, ""):
        return None
    try:
        return cast(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _list(request: Request, key: str) -> list[str]:
    vals: list[str] = []
    for v in request.query_params.getlist(key):
        vals += [x for x in v.split(",") if x]
    return vals


def query_from(request: Request) -> search.Query:
    qp = request.query_params
    rooms = [int(x) for x in _list(request, "rooms") if x.isdigit()]
    return search.Query(
        q=qp.get("q", "")[:200],
        types=[t for t in _list(request, "type") if t in TYPE_LABELS],
        deal="rent" if qp.get("deal") == "rent" else "sale",
        rooms=rooms,
        price_min=_num(qp.get("price_min"), int), price_max=_num(qp.get("price_max"), int),
        area_min=_num(qp.get("area_min")), area_max=_num(qp.get("area_max")),
        land_min=_num(qp.get("land_min")), land_max=_num(qp.get("land_max")),
        districts=_list(request, "district"), complexes=_list(request, "complex"),
        not_first=qp.get("not_first") == "1", not_last=qp.get("not_last") == "1",
        fresh_days=_num(qp.get("fresh_days"), int),
        since=_num(qp.get("since"), int),
        sort=qp.get("sort", "new"),
        page=_num(qp.get("page"), int) or 1,
        size=_num(qp.get("size"), int) or 30,
    )


# ─── страницы и поиск ──────────────────────────────────────────────────────
def index(request: Request, body: dict | None = None):
    # no-cache: после обновления сайта браузер сразу берёт новую страницу (и новые ?v= у стилей/скриптов)
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


def admin_page(request: Request, body: dict | None = None):
    return FileResponse(WEB / "admin.html", headers={"Cache-Control": "no-cache"})


def api_meta(request: Request, body: dict | None = None):
    user = current_user(request)
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
        "public_contact": config.PUBLIC_CONTACT,
        "public_contact_label": config.PUBLIC_CONTACT_LABEL,
    })


def api_listings(request: Request, body: dict | None = None):
    # В списке телефонов нет никогда — только в карточке, по одному объекту
    now = int(time.time())
    res = search.search(db.get(), query_from(request), now, False)
    res["now"] = now  # от этого момента страница считает «новые объекты»
    return JSONResponse(res)


def api_listing(request: Request, body: dict | None = None):
    conn = db.get()
    lid = int(request.path_params["id"])
    user = current_user(request)
    access = has_access(request, user)
    d = search.listing_detail(conn, lid, access)
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
    d["favorite"] = bool(user and conn.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND listing_id = ?", (user["id"], lid)).fetchone())
    return JSONResponse(d)


def _raw_phones(conn, lid: int) -> list[str]:
    row = conn.execute("SELECT phones FROM listings WHERE id = ?", (lid,)).fetchone()
    return json.loads(row["phones"] or "[]") if row else []


def api_facets(request: Request, body: dict | None = None):
    return JSONResponse(search.facets(db.get(), query_from(request), int(time.time())))


def api_map(request: Request, body: dict | None = None):
    return JSONResponse(search.map_points(db.get(), query_from(request), int(time.time())))


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
    rows = db.get().execute("""SELECT l.* FROM favorites f JOIN listings l ON l.id = f.listing_id
                               WHERE f.user_id = ? ORDER BY f.created DESC""", (user["id"],)).fetchall()
    return JSONResponse({"items": [search.row_to_item(r, False) for r in rows]})


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
        "listings": one("SELECT COUNT(*) FROM listings WHERE is_active=1"),
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


# ─── отладка (только админ) ────────────────────────────────────────────────
def api_parse(request: Request, body: dict | None = None):
    """Проверить разбор текста, ничего не сохраняя."""
    _, err = _need_admin(request)
    if err and not has_code(request):
        return err
    body = body or {}
    kind, objs = parser.parse(str(body.get("text", ""))[:8000])
    return JSONResponse({"kind": kind, "objects": [o.to_dict() for o in objs]})


def api_add_message(request: Request, body: dict | None = None):
    """Добавить сообщение вручную (например, переслать объект, которого нет в чатах)."""
    _, err = _need_admin(request)
    if err and not has_code(request):
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
    Route("/api/admin/users", threaded(api_admin_users)),
    Route("/api/admin/users/{id:int}", threaded(api_admin_user_action), methods=["POST"]),
    Route("/api/admin/promos", threaded(api_admin_promos), methods=["GET", "POST", "DELETE"]),
    Route("/api/admin/optouts", threaded(api_admin_optouts), methods=["GET", "POST"]),
    Route("/api/admin/complaints", threaded(api_admin_complaints), methods=["GET", "POST"]),
    Route("/api/parse", threaded(api_parse), methods=["POST"]),
    Route("/api/messages", threaded(api_add_message), methods=["POST"]),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
]

app = Starlette(routes=routes)

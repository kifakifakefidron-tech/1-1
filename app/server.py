"""Веб-сервер: страница поиска + API. Запуск: uvicorn app.server:app --port 8090"""
from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, db, geo, ingest, parser, search
from .rules import TYPE_LABELS

WEB = Path(__file__).resolve().parent.parent / "web"
COOKIE = "strely_access"


# ─── доступ ────────────────────────────────────────────────────────────────
def _sign(value: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), value.encode(), hashlib.sha256).hexdigest()[:32]


def has_access(request: Request) -> bool:
    if not config.ACCESS_CODE:
        return True
    return hmac.compare_digest(request.cookies.get(COOKIE, ""), _sign(config.ACCESS_CODE))


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
        sort=qp.get("sort", "new"),
        page=_num(qp.get("page"), int) or 1,
        size=_num(qp.get("size"), int) or 30,
    )


# ─── обработчики ───────────────────────────────────────────────────────────
async def index(request: Request):
    return FileResponse(WEB / "index.html")


async def api_meta(request: Request):
    return JSONResponse({
        "title": config.SITE_TITLE,
        "types": TYPE_LABELS,
        "districts": geo.all_district_names(),
        "access": has_access(request),
        "access_required": bool(config.ACCESS_CODE),
        "public_contact": config.PUBLIC_CONTACT,
        "public_contact_label": config.PUBLIC_CONTACT_LABEL,
    })


async def api_listings(request: Request):
    return JSONResponse(search.search(db.get(), query_from(request), int(time.time()), has_access(request)))


async def api_listing(request: Request):
    d = search.listing_detail(db.get(), int(request.path_params["id"]), has_access(request))
    return JSONResponse(d) if d else JSONResponse({"detail": "не найдено"}, status_code=404)


async def api_facets(request: Request):
    return JSONResponse(search.facets(db.get(), query_from(request), int(time.time())))


async def api_map(request: Request):
    return JSONResponse(search.map_points(db.get(), query_from(request), int(time.time())))


async def api_login(request: Request):
    body = await request.json()
    code = str(body.get("code", "")).strip().encode()  # bytes: код может быть по-русски
    if config.ACCESS_CODE and hmac.compare_digest(code, config.ACCESS_CODE.encode()):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, _sign(config.ACCESS_CODE), max_age=180 * 86400, httponly=True, samesite="lax")
        return resp
    return JSONResponse({"ok": False}, status_code=403)


async def api_parse(request: Request):
    """Проверить разбор текста, ничего не сохраняя (для отладки)."""
    if not has_access(request):
        return JSONResponse({"detail": "нужен код доступа"}, status_code=403)
    body = await request.json()
    kind, objs = parser.parse(str(body.get("text", ""))[:8000])
    return JSONResponse({"kind": kind, "objects": [o.to_dict() for o in objs]})


async def api_add_message(request: Request):
    """Добавить сообщение вручную (например, переслать объект, которого нет в чатах)."""
    if not has_access(request):
        return JSONResponse({"detail": "нужен код доступа"}, status_code=403)
    body = await request.json()
    conn = db.get()
    mid = ingest.add_message(conn, source="manual", text=str(body.get("text", ""))[:8000], chat_name="вручную")
    if mid is None:
        return JSONResponse({"status": "duplicate"})
    return JSONResponse(ingest.process_message(conn, mid))


async def api_stats(request: Request):
    conn = db.get()
    one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731
    day = int(time.time()) - 86400
    return JSONResponse({
        "active": one("SELECT COUNT(*) FROM listings WHERE is_active=1"),
        "new_24h": one("SELECT COUNT(*) FROM listings WHERE first_seen >= ?", day),
        "messages_24h": one("SELECT COUNT(*) FROM messages WHERE ts >= ?", day),
        "duplicates_merged": one("SELECT COUNT(*) FROM listing_events WHERE match != 'new'"),
    })


routes = [
    Route("/", index),
    Route("/api/meta", api_meta),
    Route("/api/listings", api_listings),
    Route("/api/listings/{id:int}", api_listing),
    Route("/api/facets", api_facets),
    Route("/api/map", api_map),
    Route("/api/stats", api_stats),
    Route("/api/login", api_login, methods=["POST"]),
    Route("/api/parse", api_parse, methods=["POST"]),
    Route("/api/messages", api_add_message, methods=["POST"]),
    Mount("/static", StaticFiles(directory=WEB), name="static"),
]

app = Starlette(routes=routes)

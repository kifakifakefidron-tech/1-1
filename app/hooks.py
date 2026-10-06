"""Фишки, ради которых возвращаются на сайт:
  * подписка на поиск — письмо, когда появились новые подходящие объекты;
  * снижение цены в избранном — письмо и отметка на карточке;
  * «этот объект продают ещё N агентов» — похожие карточки других агентов;
  * средняя цена за м² по ЖК/району и «дешевле/дороже рынка на N %»;
  * личные заметки к объекту.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sqlite3
import statistics
import threading
import time
import urllib.parse

from . import config, db, geo
from .textnorm import words

log = logging.getLogger(__name__)
DAY = 86400
MAX_SAVED = 10            # подписок на одного пользователя
SEND_EVERY_S = 3 * 3600   # не чаще одного письма по подписке за 3 часа


# ─── рыночная цена за м² ───────────────────────────────────────────────────
_market: dict = {"t": 0.0, "data": {}}
_market_lock = threading.Lock()
FLAT = ("flat", "new")


def _group(t: str | None) -> str:
    return "flat" if t in FLAT else (t or "")


def market(conn: sqlite3.Connection) -> dict:
    """Медиана цены за м² по ЖК и по району (для квартир), пересчёт раз в 10 минут."""
    if time.time() - _market["t"] < 600:
        return _market["data"]
    with _market_lock:
        if time.time() - _market["t"] < 600:
            return _market["data"]
        buckets: dict[tuple, list[int]] = {}
        for r in conn.execute("""SELECT deal, type, complex, district, price_m2 FROM listings
                                 WHERE is_active = 1 AND price_m2 IS NOT NULL AND type IN ('flat', 'new')"""):
            if r["complex"]:
                buckets.setdefault((r["deal"], "cx", geo.complex_key(r["complex"])), []).append(r["price_m2"])
            if r["district"]:
                buckets.setdefault((r["deal"], "d", r["district"]), []).append(r["price_m2"])
        data = {k: (int(statistics.median(v)), len(v)) for k, v in buckets.items()
                if len(v) >= (3 if k[1] == "cx" else 5)}
        _market.update(t=time.time(), data=data)
        return data


def market_for(conn: sqlite3.Connection, o: dict) -> dict | None:
    """{'base': 'ЖК Мозаика', 'median': 152000, 'n': 12, 'diff': -8} — или None, если сравнить не с чем."""
    if _group(o.get("type")) != "flat" or not o.get("price_m2"):
        return None
    m = market(conn)
    hit = None
    if o.get("complex"):
        k = (o["deal"], "cx", geo.complex_key(o["complex"]))
        if k in m:
            hit = (f"ЖК {o['complex']}", *m[k])
    if hit is None and o.get("district"):
        k = (o["deal"], "d", o["district"])
        if k in m:
            hit = (f"район {o['district']}", *m[k])
    if hit is None:
        return None
    base, median, n = hit
    diff = round((o["price_m2"] / median - 1) * 100)
    if abs(diff) > 60:   # скорее ошибка в площади/цене, чем реальная разница — не пугаем
        return None
    return {"base": base, "median": median, "n": n, "diff": diff}


def annotate(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Для карточек в списке: «ниже рынка на N %» (только заметная разница)."""
    for it in items:
        mk = market_for(conn, it)
        it["market_diff"] = mk["diff"] if mk and mk["diff"] <= -7 else None
    return items


# ─── «продают ещё N агентов» ───────────────────────────────────────────────
def _norm_street(v: str | None) -> str | None:
    w = words(v or "")
    w = re.sub(r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе)\b", " ", w)
    return " ".join(w.split()) or None


def same_elsewhere(conn: sqlite3.Connection, row: sqlite3.Row, limit: int = 10) -> list[dict]:
    """Тот же объект у других агентов. Строго: тот же тип, сделка, комнаты, этаж, площадь ±1 м²,
    и место совпадает — улица (если указана у обоих) и ЖК (если указан у обоих); хотя бы одно из них есть."""
    if row["area"] is None or row["floor"] is None or not (row["complex"] or row["street"]):
        return []
    phones = set(json.loads(row["phones"] or "[]"))
    rows = conn.execute(
        """SELECT id, price, title, last_seen, phones, complex, street, house FROM listings
           WHERE is_active = 1 AND id != ? AND deal = ? AND type IN (?, ?) AND area BETWEEN ? AND ?
             AND rooms IS ? AND floor = ?
             AND (district IS NOT NULL OR complex IS NOT NULL OR street IS NOT NULL OR settlement IS NOT NULL)
           LIMIT 100""",
        (row["id"], row["deal"], row["type"], "new" if row["type"] == "flat" else row["type"],
         row["area"] - 1, row["area"] + 1, row["rooms"], row["floor"])).fetchall()
    key = geo.complex_key(row["complex"]) if row["complex"] else None
    street = _norm_street(row["street"])
    out = []
    for r in rows:
        r_key = geo.complex_key(r["complex"]) if r["complex"] else None
        r_street = _norm_street(r["street"])
        if key and r_key and key != r_key:
            continue
        if street and r_street and street != r_street:
            continue
        if row["house"] and r["house"] and words(row["house"]) != words(r["house"]):
            continue
        if not ((key and r_key) or (street and r_street)):   # место должно совпасть хоть по чему-то
            continue
        if phones & set(json.loads(r["phones"] or "[]")):
            continue
        out.append({"id": r["id"], "price": r["price"], "title": r["title"], "last_seen": r["last_seen"]})
    out.sort(key=lambda x: (x["price"] is None, x["price"] or 0))
    return out[:limit]


# ─── заметки ───────────────────────────────────────────────────────────────
def get_note(conn: sqlite3.Connection, user_id: int, listing_id: int) -> str:
    r = conn.execute("SELECT text FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)).fetchone()
    return r[0] if r else ""


def set_note(conn: sqlite3.Connection, user_id: int, listing_id: int, text: str) -> None:
    text = (text or "").strip()[:2000]
    if text:
        conn.execute("""INSERT INTO notes (user_id, listing_id, text, ts) VALUES (?,?,?,?)
                        ON CONFLICT(user_id, listing_id) DO UPDATE SET text = excluded.text, ts = excluded.ts""",
                     (user_id, listing_id, text, int(time.time())))
    else:
        conn.execute("DELETE FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id))
    conn.commit()


def noted_ids(conn: sqlite3.Connection, user_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT listing_id FROM notes WHERE user_id = ?", (user_id,))]


# ─── подписки на поиск ─────────────────────────────────────────────────────
_KEEP = ("q", "deal", "type", "rooms", "price_min", "price_max", "area_min", "area_max", "land_min", "land_max",
         "district", "complex", "not_first", "not_last")


def clean_params(query: str) -> str:
    """Оставляем только фильтры (без страницы, сортировки, вида)."""
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(query) if k in _KEEP and v]
    return urllib.parse.urlencode(sorted(pairs))


def describe(params: str, types: dict) -> str:
    """Человеческое название подписки: «Продажа · 2 комн. · ФМР · до 7 млн»."""
    p = dict(urllib.parse.parse_qsl(params))
    parts = ["Аренда" if p.get("deal") == "rent" else "Продажа"]
    if p.get("type"):
        parts.append(", ".join(types.get(t, t) for t in p["type"].split(",")))
    if p.get("rooms"):
        parts.append(", ".join("студии" if r == "0" else ("4+" if r == "4" else r) for r in p["rooms"].split(",")) + " комн.")
    for k in ("district", "complex"):
        if p.get(k):
            parts.append(p[k].replace(",", ", "))
    rent = p.get("deal") == "rent"
    fmt = (lambda v: f"{int(v) // 1000} тыс.") if rent else (lambda v: f"{int(v) / 1e6:g} млн")
    if p.get("price_min"):
        parts.append(f"от {fmt(p['price_min'])}")
    if p.get("price_max"):
        parts.append(f"до {fmt(p['price_max'])}")
    if p.get("q"):
        parts.append(f"«{p['q']}»")
    return " · ".join(parts)[:200]


def save_search(conn: sqlite3.Connection, user: sqlite3.Row, query: str, types: dict) -> tuple[dict | None, str]:
    if not user["email"]:
        return None, "Письма о новых объектах приходят на почту — войдите по почте, чтобы подписаться."
    params = clean_params(query)
    if conn.execute("SELECT 1 FROM saved_searches WHERE user_id = ? AND params = ? AND active = 1",
                    (user["id"], params)).fetchone():
        return None, "Вы уже следите за этим поиском."
    if conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ? AND active = 1", (user["id"],)).fetchone()[0] >= MAX_SAVED:
        return None, f"Можно следить не больше чем за {MAX_SAVED} поисками — удалите ненужный в кабинете."
    now = int(time.time())
    title = describe(params, types)
    cur = conn.execute("INSERT INTO saved_searches (user_id, title, params, created, checked_at) VALUES (?,?,?,?,?)",
                       (user["id"], title, params, now, now))
    conn.commit()
    return {"id": cur.lastrowid, "title": title, "params": params}, "Готово! Пришлём письмо, когда появятся новые объекты."


def list_saved(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, title, params, created, sent_at FROM saved_searches WHERE user_id = ? AND active = 1 ORDER BY id DESC",
        (user_id,))]


def delete_saved(conn: sqlite3.Connection, user_id: int, sid: int) -> None:
    conn.execute("UPDATE saved_searches SET active = 0 WHERE id = ? AND user_id = ?", (sid, user_id))
    conn.commit()


def unsub_token(sid: int) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"unsub:{sid}".encode(), hashlib.sha256).hexdigest()[:20]


def unsubscribe(conn: sqlite3.Connection, sid: int, token: str) -> bool:
    if not hmac.compare_digest(token, unsub_token(sid)):
        return False
    conn.execute("UPDATE saved_searches SET active = 0 WHERE id = ?", (sid,))
    conn.commit()
    return True


def _price(p: int | None, deal: str) -> str:
    if not p:
        return "цена не указана"
    if deal == "rent":
        return f"{p:,} ₽/мес".replace(",", " ")
    return f"{p / 1e6:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " млн ₽"


def check_saved(conn: sqlite3.Connection, send) -> int:
    """Новые объекты по подпискам → письмо (не чаще раза в 3 часа на подписку)."""
    from starlette.datastructures import QueryParams

    from . import search
    now = int(time.time())
    sent = 0
    for s in conn.execute("""SELECT s.*, u.email FROM saved_searches s JOIN users u ON u.id = s.user_id
                             WHERE s.active = 1 AND u.blocked = 0 AND u.email IS NOT NULL
                             AND COALESCE(s.sent_at, 0) < ?""", (now - SEND_EVERY_S,)).fetchall():
        qr = search.query_from_params(QueryParams(s["params"]))
        qr.since, qr.sort, qr.size, qr.page = s["checked_at"], "new", 10, 1
        res = search.search(conn, qr, now, False)
        conn.execute("UPDATE saved_searches SET checked_at = ? WHERE id = ?", (now, s["id"]))
        if res["total"]:
            lines = []
            for it in res["items"]:
                place = f"ЖК {it['complex']}" if it["complex"] else (it["district"] or "")
                lines.append(f"• {it['title']} — {_price(it['price'], it['deal'])}{' · ' + place if place else ''}\n"
                             f"  {config.SITE_URL}/?open={it['id']}")
            more = f"\n…и ещё {res['total'] - len(lines)}" if res["total"] > len(lines) else ""
            body = (f"По вашему поиску «{s['title']}» появились новые объекты: {res['total']}.\n\n"
                    + "\n".join(lines) + more
                    + f"\n\nВсе новые: {config.SITE_URL}/?{s['params']}&new_days=1\n\n"
                    f"Больше не присылать письма по этому поиску: "
                    f"{config.SITE_URL}/saved/off?s={s['id']}&t={unsub_token(s['id'])}")
            if send(s["email"], f"1+1: новые объекты — {s['title']}"[:150], body):
                conn.execute("UPDATE saved_searches SET sent_at = ? WHERE id = ?", (now, s["id"]))
                sent += 1
        conn.commit()
    return sent


def check_price_drops(conn: sqlite3.Connection, send) -> int:
    """Цена в избранном снизилась → одно письмо пользователю со всеми такими объектами."""
    rows = conn.execute("""SELECT f.user_id, f.listing_id, f.price_at, f.notified_price, l.price, l.title, l.deal,
                                  u.email FROM favorites f JOIN listings l ON l.id = f.listing_id
                                  JOIN users u ON u.id = f.user_id
                           WHERE l.is_active = 1 AND l.price IS NOT NULL AND u.email IS NOT NULL AND u.blocked = 0
                             AND l.price < COALESCE(f.notified_price, f.price_at)""").fetchall()
    by_user: dict[int, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    sent = 0
    for uid, items in by_user.items():
        lines = [f"• {r['title']}: {_price(r['notified_price'] or r['price_at'], r['deal'])} → "
                 f"{_price(r['price'], r['deal'])}\n  {config.SITE_URL}/?open={r['listing_id']}" for r in items]
        if send(items[0]["email"], "1+1: цена снизилась в вашем избранном",
                "Цена снизилась на объекты из вашего избранного:\n\n" + "\n".join(lines)):
            sent += 1
        for r in items:
            conn.execute("UPDATE favorites SET notified_price = ? WHERE user_id = ? AND listing_id = ?",
                         (r["price"], uid, r["listing_id"]))
    conn.commit()
    return sent


def run(conn: sqlite3.Connection, send, every_s: int = 1800) -> dict | None:
    """Из фонового цикла: раз в полчаса проверить подписки и снижение цен."""
    if time.time() - float(db.get_state(conn, "hooks_at") or 0) < every_s:
        return None
    db.set_state(conn, "hooks_at", str(int(time.time())))
    return {"saved": check_saved(conn, send), "price_drops": check_price_drops(conn, send)}

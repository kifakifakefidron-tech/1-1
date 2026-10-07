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


def update_market_diff(conn: sqlite3.Connection) -> int:
    """Записать в listings.market_diff разницу с рынком (для фильтра «🔥 Горячее»). Меняем только изменившееся."""
    changed = []
    for r in conn.execute("SELECT id, type, deal, complex, district, price_m2, market_diff FROM listings WHERE is_active = 1"):
        mk = market_for(conn, dict(r))
        diff = mk["diff"] if mk else None
        if diff != r["market_diff"]:
            changed.append((diff, r["id"]))
    conn.executemany("UPDATE listings SET market_diff = ? WHERE id = ?", changed)
    conn.commit()
    return len(changed)


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
# Статусы по объекту — простая CRM агента (видит только он сам)
NOTE_STATUSES = ("call", "show", "think", "refuse", "deal")


def get_note(conn: sqlite3.Connection, user_id: int, listing_id: int) -> str:
    r = conn.execute("SELECT text FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)).fetchone()
    return r[0] if r else ""


def get_status(conn: sqlite3.Connection, user_id: int, listing_id: int) -> str | None:
    r = conn.execute("SELECT status FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)).fetchone()
    return r[0] if r else None


def set_note(conn: sqlite3.Connection, user_id: int, listing_id: int, text: str | None = None,
             status: str | None = "keep") -> None:
    """Заметка и/или статус. text=None — не трогать текст; status='keep' — не трогать статус, None/'' — снять."""
    old = conn.execute("SELECT text, status FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id)).fetchone()
    text = (old["text"] if old else "") if text is None else (text or "").strip()[:2000]
    if status == "keep":
        status = old["status"] if old else None
    status = status if status in NOTE_STATUSES else None
    if text or status:
        conn.execute("""INSERT INTO notes (user_id, listing_id, text, ts, status) VALUES (?,?,?,?,?)
                        ON CONFLICT(user_id, listing_id) DO UPDATE SET text = excluded.text, ts = excluded.ts,
                        status = excluded.status""", (user_id, listing_id, text, int(time.time()), status))
    else:
        conn.execute("DELETE FROM notes WHERE user_id = ? AND listing_id = ?", (user_id, listing_id))
    conn.commit()


def statuses(conn: sqlite3.Connection, user_id: int) -> dict:
    return {r[0]: r[1] for r in conn.execute(
        "SELECT listing_id, status FROM notes WHERE user_id = ? AND status IS NOT NULL", (user_id,))}


def noted_ids(conn: sqlite3.Connection, user_id: int) -> list[int]:
    return [r[0] for r in conn.execute("SELECT listing_id FROM notes WHERE user_id = ?", (user_id,))]


# ─── подписки на поиск ─────────────────────────────────────────────────────
_KEEP = ("q", "deal", "type", "rooms", "price_min", "price_max", "area_min", "area_max", "land_min", "land_max",
         "district", "complex", "not_first", "not_last", "hot")


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
    if p.get("hot"):
        parts.append("🔥 горячее")
    return " · ".join(parts)[:200]


def save_search(conn: sqlite3.Connection, user: sqlite3.Row, query: str, types: dict,
                page_query: str = "") -> tuple[dict | None, str]:
    params = clean_params(query)
    if conn.execute("SELECT 1 FROM saved_searches WHERE user_id = ? AND params = ? AND active = 1",
                    (user["id"], params)).fetchone():
        return None, "Вы уже следите за этим поиском — уведомления на странице «Уведомления»."
    if conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ? AND active = 1", (user["id"],)).fetchone()[0] >= MAX_SAVED:
        return None, f"Можно следить не больше чем за {MAX_SAVED} поисками — удалите ненужный в «Уведомлениях»."
    now = int(time.time())
    title = describe(params, types)
    page_query = _page_query(page_query)
    cur = conn.execute("""INSERT INTO saved_searches (user_id, title, params, created, checked_at, page_query)
                          VALUES (?,?,?,?,?,?)""", (user["id"], title, params, now, now, page_query))
    conn.commit()
    where = "в «Уведомлениях»" + (" и на почту" if user["email"] else "")
    return ({"id": cur.lastrowid, "title": title, "params": params, "page_query": page_query},
            f"Готово! Новые объекты по этому поиску придут {where}.")


def _page_query(q: str) -> str:
    """Адрес страницы с фильтрами — без вида, страницы, открытого объекта и служебных отметок."""
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl((q or "").lstrip("?"))
             if k not in ("open", "view", "page", "fresh", "saved", "since", "fav", "login", "cabinet", "agent")]
    return urllib.parse.urlencode(pairs)


def page_from_params(params: str) -> str:
    """Старые подписки (до page_query): фильтры API → адрес страницы. Цена на странице — в млн (аренда — в тыс.)."""
    p: dict[str, list[str]] = {}
    for k, v in urllib.parse.parse_qsl(params or ""):
        p.setdefault(k, []).append(v)
    one = lambda k: p.get(k, [""])[0]   # noqa: E731
    rent = one("deal") == "rent"
    out = []
    if one("q"):
        out.append(("q", one("q")))
    if rent:
        out.append(("deal", "rent"))
    for api, page in (("type", "type"), ("rooms", "rooms")):
        if one(api):
            out.append((page, one(api).replace(",", "|")))
    for api, page in (("price_min", "pmin"), ("price_max", "pmax")):
        if one(api).isdigit():
            out.append((page, f"{int(one(api)) / (1000 if rent else 1e6):g}"))
    for api, page in (("area_min", "amin"), ("area_max", "amax"), ("land_min", "lmin"), ("land_max", "lmax"),
                      ("not_first", "nf"), ("not_last", "nl"), ("hot", "hot")):
        if one(api):
            out.append((page, one(api)))
    for k in ("district", "complex"):
        vals = [x for v in p.get(k, []) for x in v.split(",") if x]
        if vals:
            out.append((k, "|".join(vals)))
    return urllib.parse.urlencode(out)


def search_url(s, since: int | None = None) -> str:
    """Ссылка на сохранённый поиск (те же фильтры). С since — страница выделит объекты, появившиеся после него."""
    pq = s["page_query"] if "page_query" in s.keys() and s["page_query"] else ""
    if not pq and "params" in s.keys():   # подписка сохранена до 07.10 — адрес страницы собираем из фильтров
        pq = page_from_params(s["params"])
    pq = _page_query(pq)
    extra = f"saved={s['id']}" + (f"&since={since}" if since else "")
    return "/?" + "&".join(x for x in (pq, extra) if x)


def list_saved(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT id, title, params, page_query, created, sent_at, notice_checked_at FROM saved_searches
                             WHERE user_id = ? AND active = 1 ORDER BY id DESC""", (user_id,)):
        d = dict(r)
        d["url"] = search_url(r)
        out.append(d)
    return out


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


def _new_since(conn, s, since: int, now: int, size: int = 10) -> dict:
    from starlette.datastructures import QueryParams

    from . import search
    qr = search.query_from_params(QueryParams(s["params"]))
    qr.since, qr.sort, qr.size, qr.page = since, "new", size, 1
    return search.search(conn, qr, now, False)


def check_saved(conn: sqlite3.Connection, send, user_id: int | None = None) -> int:
    """Новые объекты по подпискам.

    На сайте — при каждой проверке (своя отметка notice_checked_at), письмом — не чаще раза в 3 часа
    (свои отметки checked_at/sent_at). Отметки раздельные, поэтому письмо не «съедает» уведомление на сайте."""
    from . import notices
    now = int(time.time())
    made = 0
    rows = conn.execute("""SELECT s.*, u.email FROM saved_searches s JOIN users u ON u.id = s.user_id
                           WHERE s.active = 1 AND u.blocked = 0 AND (? IS NULL OR s.user_id = ?)""",
                        (user_id, user_id)).fetchall()
    for s in rows:
        # 1) уведомление на сайте
        since = s["notice_checked_at"] or s["created"]
        res = _new_since(conn, s, since, now, size=1)
        if res["total"]:
            n = res["total"]
            notices.add(conn, s["user_id"], "search", f"Новые объекты по поиску: {n}", s["title"], url=search_url(s, since),
                        listing_id=res["items"][0]["id"] if n == 1 else None, dedup_s=60)
            made += 1
        conn.execute("UPDATE saved_searches SET notice_checked_at = ? WHERE id = ?", (now, s["id"]))
        # 2) письмо (если есть почта и прошло 3 часа с прошлого)
        if send and s["email"] and (s["sent_at"] or 0) < now - SEND_EVERY_S:
            res = _new_since(conn, s, s["checked_at"], now)
            conn.execute("UPDATE saved_searches SET checked_at = ? WHERE id = ?", (now, s["id"]))
            if res["total"]:
                n = res["total"]
                lines = []
                for it in res["items"]:
                    place = f"ЖК {it['complex']}" if it["complex"] else (it["district"] or "")
                    lines.append(f"• {it['title']} — {_price(it['price'], it['deal'])}{' · ' + place if place else ''}\n"
                                 f"  {config.SITE_URL}/?open={it['id']}")
                more = f"\n…и ещё {n - len(lines)}" if n > len(lines) else ""
                send(s["email"], f"1+1: новые объекты — {s['title']}"[:150],
                     f"По вашему поиску «{s['title']}» появились новые объекты: {n}.\n\n" + "\n".join(lines) + more
                     + f"\n\nВсе новые: {config.SITE_URL}{search_url(s)}\n"
                     f"Все уведомления: {config.SITE_URL}/notifications\n\n"
                     f"Больше не присылать письма по этому поиску: "
                     f"{config.SITE_URL}/saved/off?s={s['id']}&t={unsub_token(s['id'])}")
                conn.execute("UPDATE saved_searches SET sent_at = ? WHERE id = ?", (now, s["id"]))
        conn.commit()
    return made


def check_favorites(conn: sqlite3.Connection, send, user_id: int | None = None) -> int:
    """Избранное: цена изменилась (вниз или вверх) или объект сняли с сайта → уведомление на сайте
    (своя отметка site_price). О снижении цены — ещё и письмо (своя отметка notified_price)."""
    from . import notices
    rows = conn.execute("""SELECT f.user_id, f.listing_id, f.price_at, f.notified_price, f.site_price, f.gone_notified,
                                  l.price, l.title, l.deal, l.is_active, u.email
                           FROM favorites f JOIN listings l ON l.id = f.listing_id JOIN users u ON u.id = f.user_id
                           WHERE u.blocked = 0 AND (? IS NULL OR f.user_id = ?)""", (user_id, user_id)).fetchall()
    drops: dict[int, list] = {}
    made = 0
    now = int(time.time())
    for r in rows:
        uid, lid = r["user_id"], r["listing_id"]
        if not r["is_active"]:
            if not r["gone_notified"]:
                notices.add(conn, uid, "gone", "Объект из избранного снят с сайта", r["title"], listing_id=lid,
                            url=f"/?open={lid}")
                conn.execute("UPDATE favorites SET gone_notified = ? WHERE user_id = ? AND listing_id = ?", (now, uid, lid))
                made += 1
            continue
        if r["gone_notified"]:   # объект вернулся на сайт
            conn.execute("UPDATE favorites SET gone_notified = NULL WHERE user_id = ? AND listing_id = ?", (uid, lid))
        if not r["price"]:
            continue
        # на сайте: любое изменение цены
        site_base = r["site_price"] or r["price_at"]
        if site_base and r["price"] != site_base:
            down = r["price"] < site_base
            notices.add(conn, uid, "price",
                        f"Цена {'снизилась' if down else 'выросла'} на {_price(abs(r['price'] - site_base), r['deal'])}",
                        f"{r['title']}: {_price(site_base, r['deal'])} → {_price(r['price'], r['deal'])}",
                        listing_id=lid, url=f"/?open={lid}", dedup_s=60)
            conn.execute("UPDATE favorites SET site_price = ? WHERE user_id = ? AND listing_id = ?", (r["price"], uid, lid))
            made += 1
        # письмом: только снижение
        mail_base = r["notified_price"] or r["price_at"]
        if mail_base and r["price"] != mail_base:
            if r["price"] < mail_base and r["email"] and send:
                drops.setdefault(uid, []).append((r, mail_base))
            conn.execute("UPDATE favorites SET notified_price = ? WHERE user_id = ? AND listing_id = ?", (r["price"], uid, lid))
    for uid, items in drops.items():
        lines = [f"• {r['title']}: {_price(base, r['deal'])} → {_price(r['price'], r['deal'])}\n"
                 f"  {config.SITE_URL}/?open={r['listing_id']}" for r, base in items]
        send(items[0][0]["email"], "1+1: цена снизилась в вашем избранном",
             "Цена снизилась на объекты из вашего избранного:\n\n" + "\n".join(lines)
             + f"\n\nВсе уведомления: {config.SITE_URL}/notifications")
    conn.commit()
    return made


def check_user(conn: sqlite3.Connection, user_id: int) -> None:
    """Когда человек открывает «Уведомления» — сразу проверить его подписки и избранное (без писем),
    чтобы не ждать фоновой проверки."""
    check_saved(conn, None, user_id)
    check_favorites(conn, None, user_id)


def check_price_drops(conn: sqlite3.Connection, send) -> int:
    """Совместимость: письма о снижении цены (теперь — часть check_favorites)."""
    sent = []
    check_favorites(conn, (lambda *a: sent.append(a) or (send(*a) if send else True)))
    return len(sent)


def run(conn: sqlite3.Connection, send, every_s: int = 300) -> dict | None:
    """Из фонового цикла: раз в 5 минут — подписки, избранное, окончание доступа."""
    from . import notices
    if time.time() - float(db.get_state(conn, "hooks_at") or 0) < every_s:
        return None
    db.set_state(conn, "hooks_at", str(int(time.time())))
    fmt_day = lambda ts: time.strftime("%d.%m", time.localtime(ts))  # noqa: E731
    update_market_diff(conn)
    out = {"saved": check_saved(conn, send), "favorites": check_favorites(conn, send),
           "subscriptions": notices.check_subscriptions(conn, fmt_day)}
    notices.cleanup(conn)
    return out

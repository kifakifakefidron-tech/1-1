"""Запросы покупателей из чатов («куплю», «ищу», «есть клиент») — раньше выбрасывались.

Сохраняем запрос с разобранными пожеланиями (тип, комнаты, бюджет, районы, ЖК) и сводим с объектами:
в окне объекта — «под этот объект есть N покупателей», на странице /requests — лента запросов
со ссылкой «подходящие объекты». Телефоны — как у объявлений: только с доступом.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import config, geo, parser, rules
from .textnorm import norm, words

STALE_DAYS = 10   # 19.10 (Артём): запрос живёт 10 дней, если его не повторяют

# 19.10 (Артём: «явную рекламу не добавляем»): выкуп квартир, ипотечные/юридические услуги, каналы, акции — не запрос покупателя
_AD = re.compile(r"(?:выкуп\w*|выкупим|купим (?:вашу|ваш|любую|любые|квартир\w* за)|срочный выкуп|деньги за (?:день|час)|"
                 r"(?:одобрим|одобряем|поможем одобрить|помощь в одобрении) ипотек|ипотек\w* (?:с|при) плох\w* (?:ки|кредитн)|"
                 r"помо(?:жем|щь) (?:с|в) (?:ипотек|одобрен)|юридическ\w* (?:услуг|сопровожд)|"
                 r"услуги (?:риелтор|юрист|оценк)|оценка недвижимост|ремонт (?:под ключ|квартир)|дизайн[- ]проект|"
                 r"подписывайтесь|наш (?:канал|сайт)|переходите|t\.me/|https?://|www\.|скидк\w*|акци[яи]\b|реклам\w*|"
                 r"обучени\w* (?:риелтор|агент)|курс\w* (?:риелтор|агент)|вакансия|требуются (?:агент|риелтор))", re.I)


def is_ad(text: str) -> bool:
    return bool(_AD.search(norm(text or "")))


# ─── разбор запроса ────────────────────────────────────────────────────────
def _districts(text: str) -> list[str]:
    """Все районы, названные в запросе («ФМР или ЮМР»)."""
    w = f" {words(text)} "
    out = []
    for alias, canon in geo._ALIASES:
        idx = w.find(f" {alias} ")
        if idx < 0:
            continue
        if alias in geo._AMBIGUOUS and not geo._DISTRICT_MARKER.search(w[:idx + 1].rstrip() + " "):
            continue
        if canon not in out:
            out.append(canon)
        w = w.replace(f" {alias} ", " ")
    return out[:8]


def _complexes(text: str) -> list[str]:
    """ЖК из запроса: первый — как в объявлениях, следующие — только из справочника («ЖК Мозаика или Свобода»)."""
    out, rest = [], text
    for _ in range(5):
        cx = geo.find_complex_in_text(rest)
        if not cx or cx.name in out or (out and geo.resolve_complex(cx.name) is None):
            break
        out.append(cx.name)
        rest = re.sub(re.escape(cx.name), " ", rest, flags=re.I)
    return out


_ROOM_RANGE = re.compile(r"\b([1-5])\s*(?:-|–|или|/|,|и)\s*([1-5])\s*-?\s*(?:х\s*)?(?:к\b|кк|ком|комн|комнатн)", re.I)


_ROOM_ONE = re.compile(r"(?<![\d/.,])([1-5])\s*-?\s*(?:х\s*)?(?:к\b|кк|ком\b|комн|комнатн)", re.I)
_ROOM_WORDS = [(r"\bоднушк|\bоднокомнатн", 1), (r"\bдвушк|\bдвухкомнатн", 2), (r"\bтр[её]шк|\bтр[её]хкомнатн", 3)]


def _rooms_mask(text: str) -> int:
    t = norm(text)
    mask = 0
    if re.search(r"\bстуди", t):
        mask |= 1
    for m in _ROOM_RANGE.finditer(t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        for n in range(a, b + 1):
            mask |= 1 << min(n, 4)
    for m in _ROOM_ONE.finditer(t):   # «студию или 1к», «2к или 3к»
        mask |= 1 << min(int(m.group(1)), 4)
    for rx, n in _ROOM_WORDS:
        if re.search(rx, t):
            mask |= 1 << n
    if not mask:
        rk = rules.extract_room_kind(text)
        if rk:
            mask = rules.rooms_mask(rk[0], rk[1])
        else:
            n = rules.extract_rooms(text)
            if n is not None:
                mask = 1 << min(n, 4)
    return mask


_BUDGET = re.compile(r"(?:\bдо|\bбюджет\w*|не\s+дороже|не\s+более|\bmax|\bмакс\w*|в\s+пределах|\bза)\s*[:\-–]?\s*"
                     r"(\d+(?:[.,]\d+)?)\s*(млн|млн\.|м\b|миллион\w*|т\.?\s?р|тыс\w*|к\b|000)?", re.I)
_FROM = re.compile(r"\bот\s*(\d+(?:[.,]\d+)?)\s*(млн|м\b|т\.?\s?р|тыс\w*|к\b)?", re.I)


def _money(num: str, unit: str | None, deal: str) -> int | None:
    try:
        v = float(num.replace(",", "."))
    except ValueError:
        return None
    unit = (unit or "").lower()
    if unit.startswith("м"):
        v *= 1e6
    elif unit.startswith("т") or unit == "к" or unit == "000":
        v *= 1000
    elif deal == "rent":
        v = v * 1000 if v < 1000 else v           # «до 35» — тысяч в месяц
    else:
        v = v * 1e6 if v < 100 else v * 1000 if v < 100_000 else v   # «до 6,5» — млн; «до 6500» — тысяч
    v = int(v)
    if deal == "rent":
        return v if 3_000 <= v <= 2_000_000 else None
    return v if 300_000 <= v <= 1_000_000_000 else None


def parse_request(text: str) -> dict:
    t = rules.normalize_digits(text)
    n = norm(t)
    deal = "rent" if re.search(r"\b(?:сниму|снимем|арендую|арендуем|аренд\w*|на длительн|посуточн|помесячн)", n) else "sale"
    typ = rules.detect_type(t)
    mask = _rooms_mask(t)
    if typ in (None, "new") and mask:
        typ = "flat"
    price_max = None
    for m in _BUDGET.finditer(t):
        tail = t[m.end(): m.end() + 6].lower()
        if re.match(r"\s*(?:м2|м²|кв|сот|эт|этаж|км|мин|лет|год)", tail):
            continue
        price_max = _money(m.group(1), m.group(2), deal)
        if price_max:
            break
    price_min = None
    fm = _FROM.search(t)
    if fm and not re.match(r"\s*(?:м2|м²|кв|сот|эт)", t[fm.end(): fm.end() + 5].lower()):
        price_min = _money(fm.group(1), fm.group(2), deal)
        if price_max and price_min and price_min >= price_max:
            price_min = None
    return {"deal": deal, "type": "flat" if typ == "new" else typ, "rooms_mask": mask, "price_max": price_max,
            "price_min": price_min, "districts": _districts(t), "complexes": _complexes(t),
            "phones": rules.extract_phones(t)}


# ─── сохранение ────────────────────────────────────────────────────────────
def save(conn: sqlite3.Connection, m: sqlite3.Row) -> int | None:
    """Сообщение-запрос → запись в buyer_requests (повтор того же текста — только «ещё присылали»)."""
    text = (m["text"] or "").strip()
    if len(text) < 15 or is_ad(text):
        return None
    if conn.execute("SELECT 1 FROM banned WHERE kind = 'reqhash' AND key = ?", (m["text_hash"],)).fetchone():
        return None   # этот запрос админ удалил — повтор не возвращаем
    hit = conn.execute("SELECT id FROM buyer_requests WHERE text_hash = ? AND removed = 0", (m["text_hash"],)).fetchone()
    if hit:
        conn.execute("""UPDATE buyer_requests SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, is_active = 1
                        WHERE id = ?""", (m["ts"], hit[0]))
        return hit[0]
    p = parse_request(text)
    if not p["phones"] and m["sender"]:
        ph = rules.normalize_phone(str(m["sender"]).split("@")[0])
        if ph:
            p["phones"] = [ph]
    if set(p["phones"]) & {r[0] for r in conn.execute("SELECT key FROM banned WHERE kind = 'phone'")}:
        return None   # номер удалён админом целиком
    # тот же человек с тем же запросом другими словами — тоже повтор
    for r in conn.execute("""SELECT id, phones, deal, type, rooms_mask, districts FROM buyer_requests
                             WHERE is_active = 1 AND deal = ? AND rooms_mask = ? AND COALESCE(type, '') = ?""",
                          (p["deal"], p["rooms_mask"], p["type"] or "")):
        if p["phones"] and set(json.loads(r["phones"])) & set(p["phones"]) and json.loads(r["districts"]) == p["districts"]:
            conn.execute("UPDATE buyer_requests SET last_seen = MAX(last_seen, ?), seen_count = seen_count + 1, text = ? WHERE id = ?",
                         (m["ts"], text[:3000], r["id"]))
            return r["id"]
    cur = conn.execute(
        """INSERT INTO buyer_requests (message_id, text_hash, text, chat_name, deal, type, rooms_mask, price_min, price_max,
                                       districts, complexes, phones, first_seen, last_seen, seen_count, is_active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
        (m["id"], m["text_hash"], text[:3000], m["chat_name"], p["deal"], p["type"], p["rooms_mask"], p["price_min"],
         p["price_max"], json.dumps(p["districts"], ensure_ascii=False), json.dumps(p["complexes"], ensure_ascii=False),
         json.dumps(p["phones"]), m["ts"], m["ts"]))
    return cur.lastrowid


def recheck(conn: sqlite3.Connection) -> dict:
    """После уточнения правила: «запросить по номеру», «цена по запросу» — это объявления. Такие запросы
    удаляем, а их сообщения разбираем заново (как объявления)."""
    removed = reparse = 0
    for r in conn.execute("SELECT id, message_id, text, text_hash FROM buyer_requests WHERE source != 'agent'").fetchall():   # запросы агентов сайта — не из чатов
        if rules.is_buyer_request(r["text"]):
            continue
        conn.execute("DELETE FROM buyer_requests WHERE id = ?", (r["id"],))
        removed += 1
        reparse += conn.execute("UPDATE messages SET status = 'new', kind = NULL WHERE text_hash = ? AND kind = 'request'",
                                (r["text_hash"],)).rowcount
    conn.commit()
    return {"removed": removed, "reparse": reparse}


def backfill(conn: sqlite3.Connection) -> int:
    """Один раз: запросы из уже разобранных сообщений (раньше их выбрасывали)."""
    n = 0
    for m in conn.execute("SELECT * FROM messages WHERE kind = 'request' AND status = 'done' ORDER BY ts").fetchall():
        if save(conn, m):
            n += 1
    conn.commit()
    return n


def remove(conn: sqlite3.Connection, rid: int) -> bool:
    """Админ удаляет запрос: с ленты сразу, а повтор того же текста больше не попадёт (banned 'reqhash')."""
    r = conn.execute("SELECT text_hash, text FROM buyer_requests WHERE id = ?", (rid,)).fetchone()
    if r is None:
        return False
    conn.execute("UPDATE buyer_requests SET is_active = 0, removed = 1 WHERE id = ?", (rid,))
    if r["text_hash"]:
        conn.execute("INSERT OR IGNORE INTO banned (kind, key, title, ts) VALUES ('reqhash', ?, ?, ?)",
                     (r["text_hash"], (r["text"] or "")[:80], int(time.time())))
    conn.commit()
    return True


def hide_ads(conn: sqlite3.Connection) -> int:
    """Один раз (state buyers_ads_v1): уже собранная явная реклама — с ленты запросов."""
    n = 0
    for r in conn.execute("SELECT id, text FROM buyer_requests WHERE removed = 0").fetchall():
        if is_ad(r["text"]):
            conn.execute("UPDATE buyer_requests SET is_active = 0, removed = 2 WHERE id = ?", (r["id"],))
            n += 1
    conn.commit()
    return n


def _req(r: sqlite3.Row) -> dict:
    return {"deal": r["deal"], "type": r["type"], "rooms_mask": r["rooms_mask"], "price_max": r["price_max"],
            "price_min": r["price_min"], "districts": json.loads(r["districts"] or "[]"), "complexes": json.loads(r["complexes"] or "[]")}


def agent_listings(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Объекты на сайте с подтверждёнными номерами этого агента (для «под мои объекты» и уведомлений)."""
    phones = [r[0] for r in conn.execute("SELECT phone FROM agent_phones WHERE user_id = ?", (user_id,))]
    if not phones:
        return []
    out = []
    for p in phones:
        for r in conn.execute("""SELECT id, deal, type, rooms_mask, price, district, extra_districts, complex, title FROM listings
                                 WHERE is_active = 1 AND phones LIKE ?""", (f'%"{p}"%',)):
            d = dict(r)
            d["extra_districts"] = json.loads(r["extra_districts"] or "[]")
            out.append(d)
    return list({o["id"]: o for o in out}.values())


def for_agent(conn: sqlite3.Connection, user_id: int, limit: int = 300) -> dict[int, list[int]]:
    """Какие активные запросы подходят под объекты агента: {id запроса: [id его объектов]}."""
    mine = agent_listings(conn, user_id)
    if not mine:
        return {}
    out: dict[int, list[int]] = {}
    for r in conn.execute("""SELECT * FROM buyer_requests WHERE is_active = 1 AND removed = 0 AND COALESCE(user_id, 0) != ?
                             ORDER BY last_seen DESC LIMIT 2000""", (user_id,)):   # свой запрос — не «под мои объекты»
        req = _req(r)
        hit = [o["id"] for o in mine if matches(req, o)]
        if hit:
            out[r["id"]] = hit
            if len(out) >= limit:
                break
    return out


def archive(conn: sqlite3.Connection, now: int | None = None) -> int:
    now = int(now or time.time())
    n = conn.execute("UPDATE buyer_requests SET is_active = 0 WHERE is_active = 1 AND last_seen < ?",
                     (now - STALE_DAYS * 86400,)).rowcount
    conn.commit()
    return n


# ─── сведение запросов и объектов ──────────────────────────────────────────
_GROUP = {"new": "flat"}


def matches(req: dict, o: dict) -> bool:
    """Подходит ли объект под запрос (мягко: бюджет +10 %, район или ЖК — если указаны)."""
    if req["deal"] != o.get("deal"):
        return False
    if req["type"] and _GROUP.get(o.get("type"), o.get("type")) != req["type"]:
        return False
    if req["rooms_mask"] and o.get("rooms_mask") and not (req["rooms_mask"] & o["rooms_mask"]):
        return False
    price = o.get("price")
    if req["price_max"] and (not price or price > req["price_max"] * 1.1):
        return False
    if req["price_min"] and price and price < req["price_min"] * 0.9:
        return False
    d, cx = req["districts"], req["complexes"]
    if d or cx:
        o_d = {o.get("district")} | set(o.get("extra_districts") or [])
        ok = bool(o_d & set(d))
        if cx and o.get("complex"):
            ok = ok or geo.complex_key(o["complex"]) in {geo.complex_key(c) for c in cx}
        if not ok:
            return False
    return True


def _row(r: sqlite3.Row, with_contacts: bool) -> dict:
    d = {k: r[k] for k in ("id", "deal", "type", "rooms_mask", "price_min", "price_max", "first_seen", "last_seen",
                           "seen_count")}
    d["districts"] = json.loads(r["districts"] or "[]")
    d["complexes"] = json.loads(r["complexes"] or "[]")
    phones = json.loads(r["phones"] or "[]")
    d["text"] = r["text"] if with_contacts else parser.strip_phones(r["text"])
    if with_contacts:
        d["phones"] = phones
    else:
        d["phones_count"] = len(phones)
    return d


def for_listing(conn: sqlite3.Connection, o: dict, with_contacts: bool, limit: int = 30) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM buyer_requests WHERE is_active = 1 AND removed = 0 AND deal = ? ORDER BY last_seen DESC LIMIT 2000",
                          (o.get("deal"),)):
        req = {"deal": r["deal"], "type": r["type"], "rooms_mask": r["rooms_mask"], "price_max": r["price_max"],
               "price_min": r["price_min"], "districts": json.loads(r["districts"] or "[]"),
               "complexes": json.loads(r["complexes"] or "[]")}
        if matches(req, o):
            out.append(_row(r, with_contacts))
            if len(out) >= limit:
                break
    return out


def search_url(r: dict) -> str:
    """Подходящие объекты на главной: те же пожелания фильтрами."""
    from urllib.parse import urlencode
    q = []
    if r["deal"] == "rent":
        q.append(("deal", "rent"))
    if r["type"]:
        q.append(("type", r["type"]))
    rooms = [str(i) for i in range(5) if r["rooms_mask"] & (1 << i)]
    if rooms:
        q.append(("rooms", "|".join(rooms)))
    if r["price_max"]:
        q.append(("pmax", f"{r['price_max'] * 1.1 / (1000 if r['deal'] == 'rent' else 1e6):g}"))
    if r["districts"]:
        q.append(("district", "|".join(r["districts"])))
    if r["complexes"]:
        q.append(("complex", "|".join(r["complexes"])))
    return "/?" + urlencode(q)


def feed(conn: sqlite3.Connection, with_contacts: bool, deal: str = "", district: str = "", rooms: int | None = None,
         q: str = "", page: int = 1, size: int = 32, user_id: int | None = None, mine: bool = False,
         first: int | None = None) -> dict:
    """Лента запросов — всем с подпиской. 19.10 (Артём): на странице без фильтров, без «под мои объекты» и «искать объекты» —
    просто лента от свежих. first — этот запрос первым (ссылка из уведомления «Под ваш объект есть покупатель»).
    deal/district/rooms/q/mine остались для совместимости API."""
    where, params = ["is_active = 1", "removed = 0"], []
    if deal in ("sale", "rent"):
        where.append("deal = ?"); params.append(deal)
    if district:
        where.append("EXISTS (SELECT 1 FROM json_each(districts) j WHERE j.value = ?)"); params.append(district)
    if rooms is not None and 0 <= rooms <= 4:
        where.append("(rooms_mask & ?) != 0"); params.append(1 << rooms)
    if q:
        where.append("text LIKE ?"); params.append(f"%{q.strip()[:60]}%")
    w = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM buyer_requests WHERE {w}", params).fetchone()[0]
    order = "ORDER BY last_seen DESC"
    if first:
        order = f"ORDER BY (id = {int(first)}) DESC, last_seen DESC"
    rows = conn.execute(f"SELECT * FROM buyer_requests WHERE {w} {order} LIMIT ? OFFSET ?",
                        params + [size, (max(1, page) - 1) * size]).fetchall()
    items = []
    for r in rows:
        d = _row(r, with_contacts)
        d["source"] = r["source"]
        d["pinned"] = bool(first and r["id"] == first)
        items.append(d)
    new_24h = conn.execute("SELECT COUNT(*) FROM buyer_requests WHERE is_active = 1 AND removed = 0 AND first_seen > ?",
                           (int(time.time()) - 86400,)).fetchone()[0]
    return {"total": total, "items": items, "page": page, "pages": (total + size - 1) // size, "stale_days": STALE_DAYS,
            "access": with_contacts, "new_24h": new_24h}


def summary(conn: sqlite3.Connection, user_id: int | None) -> dict:
    """Для кабинета: сколько запросов, новых за сутки и 3 свежих (19.10: «под ваши объекты» убрано вместе с вкладкой)."""
    rows = conn.execute("SELECT * FROM buyer_requests WHERE is_active = 1 AND removed = 0 ORDER BY last_seen DESC LIMIT 3").fetchall()
    one = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    return {"total": one("SELECT COUNT(*) FROM buyer_requests WHERE is_active = 1 AND removed = 0"),
            "new_24h": one("SELECT COUNT(*) FROM buyer_requests WHERE is_active = 1 AND removed = 0 AND first_seen > ?",
                           int(time.time()) - 86400),
            "latest": [_row(r, False) for r in rows]}


# ─── 19.10: запрос клиента от агента сайта и «под ваш объект есть покупатель» ─────────────────────────────────
def _rooms_from_list(rooms: list) -> int:
    m = 0
    for r in rooms or []:
        try:
            m |= 1 << max(0, min(int(r), 4))
        except (TypeError, ValueError):
            pass
    return m


def publish(conn: sqlite3.Connection, user_id: int, client: dict, comment: str = "") -> dict:
    """Агент публикует пожелания клиента (БЕЗ имени и телефона клиента) — запрос виден всем подписчикам с контактом агента.
    Повтор — продлевает запрос на 10 дней. Нужен подтверждённый номер агента (чтобы не было подставных запросов)."""
    phones = [r[0] for r in conn.execute("SELECT phone FROM agent_phones WHERE user_id = ? ORDER BY verified_at", (user_id,))]
    if not phones:
        return {"error": "Чтобы публиковать запросы, подтвердите свой номер — «Ваш номер на 1+1».", "need_verify": True}
    if client.get("kind") in ("seller", "landlord"):
        return {"error": "Запрос публикуют для покупателя или арендатора — у продавца объект, его добавляют в «Мои объекты»."}
    w = client.get("wishes") or {}
    deal = "rent" if client.get("deal") == "rent" or client.get("kind") == "tenant" else "sale"
    types = [t for t in (w.get("types") or []) if t in ("flat", "room", "house", "land", "commercial")]
    mask = _rooms_from_list(client.get("rooms"))
    districts = [d for d in (client.get("districts") or []) if d][:8]
    cxs = [c for c in (w.get("complexes") or []) if c][:6]
    pmin, pmax = client.get("budget_min") or None, client.get("budget_max") or None
    rooms_t = ", ".join("студия" if i == 0 else ("4+" if i == 4 else str(i)) for i in range(5) if mask & (1 << i))

    def money(v: int) -> str:
        return f"{v // 1000} тыс. ₽/мес" if deal == "rent" else f"{v / 1e6:g} млн ₽".replace(".", ",")
    names = {"flat": "квартиру", "room": "комнату", "house": "дом", "land": "участок", "commercial": "коммерцию"}
    parts = [("Сниму" if deal == "rent" else "Ищу") + " " + (", ".join(names[t] for t in types) or "квартиру")]
    if rooms_t:
        parts.append(f"{rooms_t} комн.")
    if districts:
        parts.append(", ".join(districts))
    if cxs:
        parts.append("ЖК " + ", ".join(cxs))
    if pmin and pmax:
        parts.append(f"{money(pmin)} – {money(pmax)}")
    elif pmax:
        parts.append(f"до {money(pmax)}")
    if "mortgage" in (w.get("pay") or []):
        parts.append("ипотека")
    if w.get("not_first"):
        parts.append("не первый этаж")
    extra = parser.strip_phones(comment.strip())[:500] if comment.strip() else ""
    text = " · ".join(parts) + (f"\n{extra}" if extra else "")
    now = int(time.time())
    old = conn.execute("SELECT id FROM buyer_requests WHERE source = 'agent' AND user_id = ? AND client_id = ? AND removed = 0",
                       (user_id, client["id"])).fetchone()
    vals = (text, deal, types[0] if len(types) == 1 else None, mask, pmin, pmax, json.dumps(districts, ensure_ascii=False),
            json.dumps(cxs, ensure_ascii=False), json.dumps(phones[:1]))
    if old:
        conn.execute("""UPDATE buyer_requests SET text = ?, deal = ?, type = ?, rooms_mask = ?, price_min = ?, price_max = ?,
                        districts = ?, complexes = ?, phones = ?, last_seen = ?, is_active = 1, notified = 0 WHERE id = ?""",
                     (*vals, now, old[0]))
        rid = old[0]
    else:
        rid = conn.execute("""INSERT INTO buyer_requests (text, text_hash, chat_name, deal, type, rooms_mask, price_min, price_max,
                              districts, complexes, phones, first_seen, last_seen, seen_count, is_active, source, user_id, client_id)
                              VALUES (?, NULL, 'Агент на сайте', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'agent', ?, ?)""",
                           (text, *vals[1:], now, now, user_id, client["id"])).lastrowid
    conn.commit()
    return {"ok": True, "id": rid, "text": text}


def unpublish(conn: sqlite3.Connection, user_id: int, client_id: int) -> bool:
    n = conn.execute("UPDATE buyer_requests SET is_active = 0 WHERE source = 'agent' AND user_id = ? AND client_id = ? AND is_active = 1",
                     (user_id, client_id)).rowcount
    conn.commit()
    return bool(n)


def published(conn: sqlite3.Connection, user_id: int, client_id: int) -> dict | None:
    r = conn.execute("""SELECT id, text, last_seen FROM buyer_requests WHERE source = 'agent' AND user_id = ? AND client_id = ?
                        AND is_active = 1 AND removed = 0""", (user_id, client_id)).fetchone()
    return dict(r) if r else None


NOTIFY_PER_DAY = 10   # не больше стольких «под ваш объект есть покупатель» одному агенту в сутки


def notify_matches(conn: sqlite3.Connection) -> int:
    """Раз в 15 мин (worker): новые запросы (из чатов и от агентов сайта) → подтверждённым агентам с подходящими объектами —
    уведомление «Под ваш объект есть покупатель». Свой же запрос себе не шлём; не больше NOTIFY_PER_DAY в сутки."""
    from . import notices
    reqs = conn.execute("SELECT * FROM buyer_requests WHERE is_active = 1 AND removed = 0 AND notified = 0 LIMIT 300").fetchall()
    if not reqs:
        return 0
    agents = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM agent_phones")]
    mine = {u: agent_listings(conn, u) for u in agents}
    day_ago = int(time.time()) - 86400
    sent = 0
    for r in reqs:
        req = _req(r)
        for uid, lst in mine.items():
            if uid == r["user_id"] or not lst:
                continue
            hit = [o for o in lst if matches(req, o)]
            if not hit:
                continue
            n_today = conn.execute("SELECT COUNT(*) FROM notices WHERE user_id = ? AND kind = 'buyer' AND ts > ?", (uid, day_ago)).fetchone()[0]
            if n_today >= NOTIFY_PER_DAY:
                continue
            o = hit[0]
            what = (o.get("complex") and f"ЖК {o['complex']}") or o.get("title") or "ваш объект"
            src = "запрос агента на 1+1" if r["source"] == "agent" else "запрос из чатов"
            if notices.add(conn, uid, "buyer", f"Под ваш объект есть покупатель — {what}", f"{(r['text'] or '')[:160]} ({src})",
                           url=f"/requests?id={r['id']}", listing_id=o["id"], dedup_s=7 * 86400):
                sent += 1
        conn.execute("UPDATE buyer_requests SET notified = 1 WHERE id = ?", (r["id"],))
    conn.commit()
    return sent

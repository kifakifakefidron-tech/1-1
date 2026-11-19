"""24.10 (Артём: «одна квартира — одна карточка; если сервис сомневается — в спорные, разберу сам и обучу»).

Фоновая проверка пар карточек из чатов, которые МОГУТ быть одним объектом (worker, каждый цикл, по новым карточкам):
  * сервис склеивает сам только наверняка:
      text  — тот же текст, поменялась только цена (dedupe.text_twin);
      agent — тот же агент (номер), то же место, те же комнаты И этаж (указаны у обоих), площадь не спорит;
  * «почти совпадает» — в очередь «Возможно, повтор» (/dups): админ смотрит две карточки рядом и решает
      similar  — текст похож на 80 %+ (числа не считаем), номера в тексте те же;
      nodetail — тот же агент и место, но этаж/площадь указаны не у обоих — различить нечем.
Решения запоминаются: «Разные» — эту пару больше не спрашиваем и сами не склеиваем; «Один объект — разные агенты»
(market) — карточки остаются отдельными (рынок), но связаны: в окне объекта «Этот объект у других агентов» (hooks.same_elsewhere); по каждому виду пары считается,
как часто вы говорите «один объект», — виду с надёжной статистикой можно доверить склейку (state dup_trust).
Склейка — ownmatch._move: избранное, клиенты, подборки, планер и история переезжают на оставшуюся карточку
(с фото, иначе самую раннюю), цена и текст — из самого свежего сообщения; «Разделить» возвращает копию."""
from __future__ import annotations

import difflib
import functools
import json
import sqlite3
import time
from collections import defaultdict
from itertools import combinations

from . import config, db, geo, offers
from .dedupe import _close, _house_eq, text_twin
from .ownmatch import _move
from .textnorm import words

KINDS = {
    "text": "Тот же текст, другая цена",
    "agent": "Тот же агент, место, комнаты и этаж",
    "similar": "Текст похож, номера те же",
    "nodetail": "Тот же агент и место, этаж или площадь не указаны",
    "offer_review": "Возможно, один объект у разных агентов",
    "offer_photo": "Один объект по фото и характеристикам",
}
AUTO = ("text", "agent")          # склеиваются сами всегда
DOUBT = ("similar", "nodetail")   # в очередь; сами — только если админ доверил (state dup_trust)
SIM_MIN = 0.8
PER_ROW = 3                       # не больше 3 сомнительных пар на карточку (агент с десятком однушек в одном ЖК)
TRUST_MIN_N, TRUST_MIN_SHARE = 20, 0.95
_FLATLIKE = ("flat", "new", "room", "commercial")
_COLS = ("id, type, deal, rooms, room_kind, floor, floors, area, land, complex, street, house, district, settlement, "
         "price, price_m2, phones, fragment, description, title, photos, first_seen, last_seen, seen_count, is_active, "
         "hidden_reason, source")


def _group(t: str | None) -> str:
    return "flat" if t in ("flat", "new") else (t or "")


@functools.lru_cache(maxsize=4096)
def _cxk(name: str | None) -> str:
    return (geo.complex_key(name) or words(name)) if name else ""


def _places(r) -> set[str]:
    out = set()
    if r["complex"]:
        out.add("cx:" + _cxk(r["complex"]))
    if r["street"]:
        out.add("st:" + words(r["street"]))
    if r["settlement"] and not out:
        out.add("pl:" + words(r["settlement"]))
    return out


@functools.lru_cache(maxsize=16384)
def _text_key(frag: str | None) -> str:
    return " ".join(t for t in words(frag or "").split() if not t.isdigit())


def _tokens(frag: str | None) -> list[str]:
    return [t for t in words(frag or "").split() if not t.isdigit()]


def _phones(raw) -> set[str]:
    """Номера карточки; номера одного агента (phonegroups) — одним номером."""
    from .phonegroups import canon_set
    try:
        return canon_set(json.loads(raw or "[]"))
    except (TypeError, ValueError):
        return set()


def _text_phones(frag: str | None) -> frozenset:
    from .phonegroups import canon_set
    from .rules import find_phones
    return frozenset(canon_set(p[2] for p in find_phones(frag or "")))


def similarity(a: str | None, b: str | None) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    if sm.real_quick_ratio() < SIM_MIN or sm.quick_ratio() < SIM_MIN:
        return sm.quick_ratio() * 0.5
    return sm.ratio()


def _contra(a, b) -> str | None:
    """Явное различие — разные объекты (две квартиры в одном доме)."""
    for k, lab in (("rooms", "комнаты"), ("floor", "этаж"), ("floors", "этажность")):
        if a[k] is not None and b[k] is not None and a[k] != b[k]:
            return lab
    if _close(a["area"], b["area"], 0.03, 1.5) is False:
        return "площадь"
    if _close(a["land"], b["land"], 0.04, 0.3) is False:
        return "участок"
    if _house_eq(a["house"], b["house"]) is False:
        return "дом"
    if a["complex"] and b["complex"] and _cxk(a["complex"]) != _cxk(b["complex"]):
        return "ЖК"
    if a["street"] and b["street"] and words(a["street"]) != words(b["street"]):
        return "улица"
    return None


def _mln(p) -> str:
    return f"{p / 1e6:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " млн" if p else "—"


def classify(a, b, info: dict | None = None) -> tuple[str, float, list[str]] | None:
    """Вид пары, сила (для порядка в очереди) и почему; None — разные или различить нечем."""
    info = info or {}
    fa, fb = a["fragment"], b["fragment"]
    same_place = bool(_places(a) & _places(b))
    price_close = _close(a["price"], b["price"], 0.10, 0)
    if text_twin(fa, fb) or (same_place and price_close and text_twin(fa, fb, short_ok=True)):
        return "text", 1.0, ["тот же текст, поменялась только цена", f"цена: {_mln(a['price'])} и {_mln(b['price'])}"]
    if not same_place:
        return None
    c = _contra(a, b)
    if c:
        return None
    agent = bool(_phones(a["phones"]) & _phones(b["phones"]))
    same_num = bool(set(json.loads(a["phones"] or "[]")) & set(json.loads(b["phones"] or "[]")))
    why = (["тот же агент (номер)"] if same_num else ["тот же агент (номера одного агента)"]) if agent else []
    eq = lambda k: a[k] is not None and b[k] is not None and a[k] == b[k]  # noqa: E731
    area_eq = _close(a["area"], b["area"], 0.03, 1.5) is True
    land_eq = _close(a["land"], b["land"], 0.04, 0.3) is True
    if eq("rooms"):
        why.append("комнаты совпали")
    if eq("floor"):
        why.append(f"этаж {a['floor']} у обоих")
    elif a["floor"] is None or b["floor"] is None:
        why.append("этаж указан не у обоих" if (a["floor"] or b["floor"]) else "этаж не указан")
    if area_eq:
        why.append(f"площадь {a['area']:g} и {b['area']:g}")
    elif a["area"] is None or b["area"] is None:
        why.append("площадь указана не у обоих" if (a["area"] or b["area"]) else "площадь не указана")
    if price_close:
        why.append(f"цена почти та же: {_mln(a['price'])} и {_mln(b['price'])}")
    flat = a["type"] in _FLATLIKE
    if flat and agent and eq("floor") and (eq("rooms") or a["type"] == "commercial") and (
            a["type"] != "commercial" or area_eq):
        return "agent", 0.95, why
    ta = info.get(a["id"]) if info else None
    tb = info.get(b["id"]) if info else None
    same_text_phones = (ta[0] if ta else _text_phones(fa)) == (tb[0] if tb else _text_phones(fb))
    sim = similarity(fa, fb) if (same_text_phones or agent) else 0.0
    if sim >= SIM_MIN and same_text_phones:
        return "similar", round(sim, 3), [f"текст похож на {round(sim * 100)} %", *why]
    if agent:
        known = eq("rooms") or eq("floor") or area_eq or land_eq
        if flat and known and (a["rooms"] is None or b["rooms"] is None or eq("rooms")):
            return "nodetail", round(0.4 + 0.1 * sum((eq("rooms"), eq("floor"), area_eq, bool(price_close))) + sim * 0.2, 3), \
                [*why, f"текст похож на {round(sim * 100)} %"] if sim > 0.3 else why
        if not flat and (area_eq or land_eq):
            return "nodetail", round(0.5 + 0.1 * sum((area_eq, land_eq, bool(price_close))) + sim * 0.2, 3), why
    return None


# ---------------------------------------------------------------- склейка и откат

def trusted(conn: sqlite3.Connection) -> set[str]:
    try:
        return set(json.loads(db.get_state(conn, "dup_trust") or "[]"))
    except ValueError:
        return set()


def choose_keep(conn: sqlite3.Connection, a: int, b: int) -> tuple[int, int]:
    """Какая карточка остаётся: с фото, потом с бóльшим числом избранного/клиентов, потом самая ранняя."""
    def rank(lid):
        r = conn.execute("SELECT photos, first_seen FROM listings WHERE id = ?", (lid,)).fetchone()
        n = len(json.loads(r["photos"] or "[]")) if r else 0
        used = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE listing_id = ?", (lid,)).fetchone()[0]
                   for t in ("favorites", "client_items", "collection_items"))
        return (-min(n, 1), -used, (r["first_seen"] if r else 0) or 0, lid)
    k, d = sorted((a, b), key=rank)
    return k, d


def _pair_key(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def merge(conn: sqlite3.Connection, keep: int, drop: int, kind: str, status: str = "auto", user_id: int = 0,
          why: list | None = None, score: float | None = None) -> int | None:
    """Склеить drop в keep (всё переезжает); вернуть id пары (для «Разделить»)."""
    k = conn.execute(f"SELECT {_COLS}, price_fixed FROM listings WHERE id = ?", (keep,)).fetchone()
    d = conn.execute(f"SELECT {_COLS} FROM listings WHERE id = ?", (drop,)).fetchone()
    if k is None or d is None or keep == drop:
        return None
    now = int(time.time())
    undo = {"events": [r[0] for r in conn.execute("SELECT id FROM listing_events WHERE listing_id = ?", (drop,))],
            "drop_active": d["is_active"], "drop_reason": d["hidden_reason"], "drop_seen": d["seen_count"] or 0,
            "keep": {f: k[f] for f in ("price", "price_m2", "fragment", "description", "phones", "last_seen")}}
    _move(conn, drop, keep)
    conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'dup_same' WHERE id = ?", (drop,))
    if (d["last_seen"] or 0) > (k["last_seen"] or 0) and not k["price_fixed"]:   # свежее сообщение — его цена и текст
        phones = sorted(set(json.loads(k["phones"] or "[]")) | set(json.loads(d["phones"] or "[]")))
        conn.execute("UPDATE listings SET price = ?, price_m2 = ?, fragment = ?, description = ?, phones = ? WHERE id = ?",
                     (d["price"], d["price_m2"], d["fragment"], d["description"], json.dumps(phones, ensure_ascii=False), keep))
    a, b = _pair_key(keep, drop)
    conn.execute("""INSERT INTO dup_pairs (a, b, kind, score, why, status, keep_id, drop_id, undo, created_at, decided_at, user_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (a, b) DO UPDATE SET status = excluded.status, keep_id = excluded.keep_id,
                      drop_id = excluded.drop_id, undo = excluded.undo, decided_at = excluded.decided_at,
                      user_id = excluded.user_id""",
                 (a, b, kind, score, json.dumps(why or [], ensure_ascii=False), status, keep, drop,
                  json.dumps(undo, ensure_ascii=False), now, now, user_id))
    # открытые пары со склеенной копией — теперь про оставшуюся карточку
    for p in conn.execute("SELECT id, a, b FROM dup_pairs WHERE status = 'open' AND (a = ? OR b = ?)", (drop, drop)).fetchall():
        other = p["b"] if p["a"] == drop else p["a"]
        na, nb = _pair_key(other, keep)
        if other == keep or conn.execute("SELECT 1 FROM dup_pairs WHERE a = ? AND b = ?", (na, nb)).fetchone():
            conn.execute("UPDATE dup_pairs SET status = 'gone' WHERE id = ?", (p["id"],))
        else:
            conn.execute("UPDATE dup_pairs SET a = ?, b = ? WHERE id = ?", (na, nb, p["id"]))
    return conn.execute("SELECT id FROM dup_pairs WHERE a = ? AND b = ?", (a, b)).fetchone()[0]


def split(conn: sqlite3.Connection, pid: int, user_id: int = 0) -> bool:
    """Разделить склейку: копия снова отдельной карточкой (история её сообщений — обратно), пара запоминается как «разные».
    Избранное и клиенты остаются на основной карточке."""
    p = conn.execute("SELECT * FROM dup_pairs WHERE id = ?", (pid,)).fetchone()
    if p is None or p["status"] not in ("same", "auto") or not p["drop_id"]:
        return False
    u = json.loads(p["undo"] or "{}")
    keep, drop = p["keep_id"], p["drop_id"]
    if conn.execute("SELECT 1 FROM listings WHERE id = ?", (drop,)).fetchone() is None:
        return False
    ev = u.get("events") or []
    for i in range(0, len(ev), 500):
        part = ev[i:i + 500]
        conn.execute(f"UPDATE listing_events SET listing_id = ? WHERE id IN ({','.join('?' * len(part))})", (drop, *part))
    fresh = conn.execute("SELECT last_seen FROM listings WHERE id = ?", (drop,)).fetchone()[0] or 0
    active = 1 if fresh >= time.time() - config.STALE_DAYS * 86400 and u.get("drop_active", 1) else 0
    conn.execute("UPDATE listings SET is_active = ?, hidden_reason = ?, merged_into = NULL WHERE id = ?",
                 (active, None if u.get("drop_reason") == "dup_same" else u.get("drop_reason"), drop))
    kv = u.get("keep") or {}
    if kv:
        conn.execute("""UPDATE listings SET price = ?, price_m2 = ?, fragment = ?, description = ?, phones = ?,
                        seen_count = MAX(1, seen_count - ?) WHERE id = ? AND COALESCE(price_fixed, 0) = 0""",
                     (kv.get("price"), kv.get("price_m2"), kv.get("fragment"), kv.get("description"), kv.get("phones"),
                      u.get("drop_seen", 0), keep))
    conn.execute("UPDATE dup_pairs SET status = 'split', decided_at = ?, user_id = ? WHERE id = ?",
                 (int(time.time()), user_id, pid))
    return True


def decide(conn: sqlite3.Connection, pid: int, verdict: str, user_id: int = 0, keep: int | None = None) -> dict:
    """Решение админа по паре из очереди: same — склеить (keep — какую оставить), diff — разные, запомнить."""
    p = conn.execute("SELECT * FROM dup_pairs WHERE id = ?", (pid,)).fetchone()
    if p is None:
        return {"ok": False, "error": "Пара не найдена"}
    if verdict == "same" and p["kind"] in ("offer_review", "offer_photo"):
        verdict = "market"
    now = int(time.time())
    if verdict == "diff":
        if p["status"] in ("same", "auto"):
            split(conn, pid, user_id)
        conn.execute("UPDATE dup_pairs SET status = 'diff', decided_at = ?, user_id = ? WHERE id = ?", (now, user_id, pid))
        return {"ok": True, "status": "diff"}
    if verdict == "market":   # 26.10 (Артём): одна квартира, продают разные агенты — карточки отдельно, но связаны
        if p["status"] in ("same", "auto"):
            split(conn, pid, user_id)
        conn.execute("UPDATE dup_pairs SET status = 'market', decided_at = ?, user_id = ? WHERE id = ?", (now, user_id, pid))
        return {"ok": True, "status": "market"}
    if verdict == "reopen":   # передумал «разные» / «разные агенты» — вернуть в очередь
        conn.execute("UPDATE dup_pairs SET status = 'open', decided_at = NULL WHERE id = ? AND status IN ('diff', 'market')", (pid,))
        return {"ok": True, "status": "open"}
    if verdict != "same":
        return {"ok": False, "error": "Не понял решение"}
    if p["status"] in ("same", "auto"):
        return {"ok": True, "status": p["status"], "keep": p["keep_id"]}
    act = {r[0] for r in conn.execute("SELECT id FROM listings WHERE id IN (?, ?) AND is_active = 1", (p["a"], p["b"]))}
    if len(act) < 2:
        conn.execute("UPDATE dup_pairs SET status = 'gone' WHERE id = ?", (pid,))
        return {"ok": False, "error": "Одной из карточек уже нет на сайте"}
    if keep in (p["a"], p["b"]):
        k, d = keep, (p["b"] if keep == p["a"] else p["a"])
    else:
        k, d = choose_keep(conn, p["a"], p["b"])
    merge(conn, k, d, p["kind"], status="same", user_id=user_id, why=json.loads(p["why"] or "[]"), score=p["score"])
    return {"ok": True, "status": "same", "keep": k, "drop": d}


# ---------------------------------------------------------------- фоновая проверка

def _candidates(conn: sqlite3.Connection):
    return conn.execute(f"""SELECT {_COLS} FROM listings WHERE source = 'chat' AND is_active = 1 AND hidden_reason IS NULL
                            AND COALESCE(owner_user_id, 0) = 0 AND fragment IS NOT NULL ORDER BY id""").fetchall()


def scan(conn: sqlite3.Connection, budget_s: float = 8.0, full: bool = False) -> dict:
    """Новые карточки (id больше курсора dup_scan_id) — против всех на сайте: наверняка — склеить, сомнительно — в очередь."""
    t0 = time.time()
    from . import phonegroups
    phonegroups.refresh(conn, force=True)
    cur = 0 if full else int(db.get_state(conn, "dup_scan_id") or 0)
    rows = _candidates(conn)
    if not rows or rows[-1]["id"] <= cur:
        return {}
    decided = {(r[0], r[1]) for r in conn.execute("SELECT a, b FROM dup_pairs")}
    trust = trusted(conn)
    groups: dict[tuple, list] = defaultdict(list)
    info: dict[int, tuple] = {}
    for r in rows:
        g = (_group(r["type"]), r["deal"])
        for p in _places(r):
            groups[(*g, p)].append(r)
        tk = _text_key(r["fragment"])
        if len(tk) >= 10:
            groups[(*g, "tx:" + tk)].append(r)
    gone: set[int] = set()
    res = {"auto": 0, "queued": 0, "rows": 0}
    last = cur
    for r in rows:
        if r["id"] <= cur:
            continue
        if time.time() - t0 > budget_s:
            break
        last = r["id"]
        res["rows"] += 1
        if r["id"] in gone:
            continue
        g = (_group(r["type"]), r["deal"])
        seen: set[int] = set()
        doubts: list = []
        keys = [(*g, p) for p in _places(r)]
        tk = _text_key(r["fragment"])
        if len(tk) >= 10:
            keys.append((*g, "tx:" + tk))
        for key in keys:
            for o in groups.get(key, ()):
                if o["id"] >= r["id"] or o["id"] in seen or o["id"] in gone:
                    continue
                seen.add(o["id"])
                if _pair_key(r["id"], o["id"]) in decided:
                    continue
                # 26.10 (нагрузка): разные комнаты / этаж у обоих — разные квартиры, дорогое сравнение текста не нужно
                if (r["rooms"] is not None and o["rooms"] is not None and r["rooms"] != o["rooms"]
                        or r["floor"] is not None and o["floor"] is not None and r["floor"] != o["floor"]) \
                        and _text_key(r["fragment"]) != _text_key(o["fragment"]):
                    continue
                if r["id"] not in info:
                    info[r["id"]] = (_text_phones(r["fragment"]),)
                if o["id"] not in info:
                    info[o["id"]] = (_text_phones(o["fragment"]),)
                c = classify(o, r, info)
                if not c:
                    continue
                kind, score, why = c
                pa, pb = _phones(o["phones"]), _phones(r["phones"])
                if pa and pb and not pa & pb:
                    doubts.append((score, o["id"], "offer_review", why))
                    continue
                if kind in AUTO or kind in trust:
                    k, d = choose_keep(conn, o["id"], r["id"])
                    merge(conn, k, d, kind, status="auto", why=why, score=score)
                    conn.commit()   # 26.10 («database is locked»): запись короткая — базу не держим весь проход
                    decided.add(_pair_key(k, d))
                    gone.add(d)
                    res["auto"] += 1
                    if d == r["id"]:
                        break
                else:
                    doubts.append((score, o["id"], kind, why))
            if r["id"] in gone:
                break
        if r["id"] in gone:
            continue
        now = int(time.time())
        for score, oid, kind, why in sorted(doubts, reverse=True)[:PER_ROW]:
            a, b = _pair_key(r["id"], oid)
            conn.execute("""INSERT OR IGNORE INTO dup_pairs (a, b, kind, score, why, status, created_at)
                            VALUES (?,?,?,?,?, 'open', ?)""", (a, b, kind, score, json.dumps(why, ensure_ascii=False), now))
            decided.add((a, b))
            res["queued"] += 1
        if doubts:
            conn.commit()
    db.set_state(conn, "dup_scan_id", str(last))
    conn.commit()
    return res


def recheck_offers(conn: sqlite3.Connection, batch: int = 250, budget_s: float = 3.0) -> dict:
    """Возвращаемся к старым карточкам с места и затем проверяем новые, не меняя их содержимое."""
    start = time.monotonic()
    cursor = int(db.get_state(conn, "offer_check_id", "0"))
    if not conn.execute("""SELECT 1 FROM listings WHERE id > ? AND is_active = 1
                           AND (street IS NOT NULL OR complex IS NOT NULL) LIMIT 1""", (cursor,)).fetchone():
        return {}

    from . import phonegroups
    phonegroups.refresh(conn)
    rows = conn.execute(f"""SELECT {offers.MATCH_FIELDS} FROM listings WHERE is_active = 1
                            AND (street IS NOT NULL OR complex IS NOT NULL) ORDER BY id""").fetchall()
    places, contacts = {}, {}
    buckets = defaultdict(list)

    def keys(row):
        prefix = (row["deal"], offers.property_type(row["type"]), row["rooms"])
        if prefix[1] == "flat" and row["rooms"] is None:
            return []
        return [(*prefix, i, value) for i, value in enumerate(places[row["id"]]) if value]

    for row in rows:
        lid = row["id"]
        places[lid] = offers.place_keys(row)
        contacts[lid] = offers.contact_keys(row)
        for key in keys(row):
            buckets[key].append(row)
    strong = defaultdict(list)
    for a, b in sorted(offers.photo_matches(conn)):
        if b > cursor:
            strong[b].append(a)
    decisions = {(a, b): (status, user_id) for a, b, status, user_id in conn.execute(
        "SELECT a, b, status, user_id FROM dup_pairs WHERE b > ?", (cursor,))}

    res = {"rows": 0, "linked": 0, "queued": 0}
    links, reviews = [], []
    for row in rows:
        lid = row["id"]
        if lid <= cursor:
            continue
        if res["rows"] and (res["rows"] >= batch or time.monotonic() - start >= budget_s):
            break
        res["rows"] += 1
        cursor = lid
        for oid in strong.get(lid, ()):
            status, user_id = decisions.get((oid, lid), (None, None))
            if status not in (None, "open") or user_id:
                continue
            links.append((oid, lid))
            decisions[oid, lid] = "market", None

        candidates = {}
        for key in keys(row):
            for other in buckets[key]:
                oid = other["id"]
                if oid >= lid:
                    break
                if oid in candidates or (oid, lid) in decisions or contacts[lid] & contacts[oid]:
                    continue
                candidates[oid] = offers.similar_signals(row, other, places[lid], places[oid])
        ranked = sorted(((oid, why) for oid, why in candidates.items() if why),
                        key=lambda pair: (len(pair[1]), pair[0]), reverse=True)
        for oid, why in ranked[:PER_ROW]:
            reviews.append((oid, lid, len(why) / 5, json.dumps(why, ensure_ascii=False)))

    now = int(time.time())
    if links:
        res["linked"] = conn.executemany("""INSERT INTO dup_pairs(a, b, kind, score, why, status, created_at)
            SELECT ?, ?, 'offer_photo', 1, '["Совпали фото и характеристики"]', 'market', ?
            WHERE EXISTS (SELECT 1 FROM listings WHERE id = ? AND is_active = 1)
              AND EXISTS (SELECT 1 FROM listings WHERE id = ? AND is_active = 1)
            ON CONFLICT(a,b) DO UPDATE SET kind=excluded.kind, score=1, why=excluded.why, status='market'
            WHERE dup_pairs.status='open' AND COALESCE(dup_pairs.user_id, 0)=0""",
            ((a, b, now, a, b) for a, b in links)).rowcount
    if reviews:
        res["queued"] = conn.executemany("""INSERT OR IGNORE INTO dup_pairs(a, b, kind, score, why, status, created_at)
            SELECT ?, ?, 'offer_review', ?, ?, 'open', ?
            WHERE EXISTS (SELECT 1 FROM listings WHERE id = ? AND is_active = 1)
              AND EXISTS (SELECT 1 FROM listings WHERE id = ? AND is_active = 1)""",
            ((a, b, score, why, now, a, b) for a, b, score, why in reviews)).rowcount
    if res["linked"]:
        conn.execute("""INSERT INTO state(key, value) VALUES ('cache_ver', ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (str(time.time()),))
    db.set_state(conn, "offer_check_id", str(cursor))
    return res


def tidy(conn: sqlite3.Connection) -> int:
    """Открытые пары, где одной карточки уже нет на сайте, — убрать из очереди."""
    cur = conn.execute("""UPDATE dup_pairs SET status = 'gone' WHERE status = 'open' AND (
                            (SELECT COALESCE(is_active, 0) FROM listings WHERE id = a) IS NOT 1
                         OR (SELECT COALESCE(is_active, 0) FROM listings WHERE id = b) IS NOT 1)""")
    return cur.rowcount


def run(conn: sqlite3.Connection) -> dict:
    if not db.get_state(conn, "phone_groups_v1"):   # 25.10: номера одного агента — перепроверить все карточки заново
        db.set_state(conn, "phone_groups_v1", "1")
        db.set_state(conn, "dup_scan_id", "0")
    res = scan(conn)
    n = tidy(conn)
    conn.commit()
    if n:
        res["gone"] = n
    return res


# ---------------------------------------------------------------- для страницы /dups

def open_count(conn: sqlite3.Connection) -> int:
    return conn.execute("""SELECT COUNT(*) FROM dup_pairs p WHERE status = 'open'
                           AND EXISTS (SELECT 1 FROM listings WHERE id = p.a AND is_active = 1)
                           AND EXISTS (SELECT 1 FROM listings WHERE id = p.b AND is_active = 1)""").fetchone()[0]


def stats(conn: sqlite3.Connection) -> list[dict]:
    """По видам пар: сколько раз вы сказали «один объект» / «разные» (и разделили склейку сервиса)."""
    rows = conn.execute("SELECT kind, status, COUNT(*) FROM dup_pairs GROUP BY kind, status").fetchall()
    by: dict[str, dict] = {k: {"same": 0, "diff": 0, "auto": 0, "open": 0, "market": 0} for k in KINDS}
    for kind, st, n in rows:
        d = by.setdefault(kind, {"same": 0, "diff": 0, "auto": 0, "open": 0, "market": 0})
        if st == "market":   # тот же объект, но разные агенты — не склейка, в долю «один объект» не входит
            d["market"] += n
        if st == "same":
            d["same"] += n
        elif st in ("diff", "split"):
            d["diff"] += n
        elif st == "auto":
            d["auto"] += n
        elif st == "open":
            d["open"] += n
    trust = trusted(conn)
    out = []
    for k, d in by.items():
        n = d["same"] + d["diff"]
        share = round(d["same"] / n, 3) if n else None
        out.append({"kind": k, "label": KINDS.get(k, k), **d, "decided": n, "share": share, "auto_kind": k in AUTO,
                    "trusted": k in trust,
                    "can_trust": k in DOUBT and n >= TRUST_MIN_N and (share or 0) >= TRUST_MIN_SHARE})
    return out


def set_trust(conn: sqlite3.Connection, kind: str, on: bool) -> list[str]:
    t = trusted(conn)
    if kind in DOUBT:
        (t.add if on else t.discard)(kind)
    db.set_state(conn, "dup_trust", json.dumps(sorted(t)))
    conn.commit()
    return sorted(t)


def _card(conn: sqlite3.Connection, lid: int) -> dict | None:
    r = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if r is None:
        return None
    used = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE listing_id = ?", (lid,)).fetchone()[0]
            for t in ("favorites", "client_items", "collection_items")}
    chat = conn.execute("""SELECT m.chat_name FROM listing_events e JOIN messages m ON m.id = e.message_id
                           WHERE e.listing_id = ? AND COALESCE(m.chat_name, '') != '' ORDER BY e.ts DESC LIMIT 1""",
                        (lid,)).fetchone() if _has_col(conn, "listing_events", "message_id") else None
    # 25.10 (Артём): внизу карточки — номер ИЗ ТЕКСТА объявления; нет в тексте — кто прислал в чат
    from .rules import find_phones
    text_ph = list(dict.fromkeys(p[2] for p in find_phones(r["fragment"] or r["description"] or "")))
    try:
        roles = json.loads(r["phone_roles"] or "{}") if "phone_roles" in r.keys() else {}
    except (TypeError, ValueError):
        roles = {}
    # 25.10 (Артём): «номер, который отправил, тоже нужен рядом» — кто прислал последнее сообщение об объекте (и раньше, если другие)
    from .rules import normalize_phone
    sender = []
    for (raw,) in conn.execute("""SELECT m.sender FROM listing_events e JOIN messages m ON m.id = e.message_id
                                  WHERE e.listing_id = ? AND COALESCE(m.sender, '') != '' ORDER BY e.ts DESC LIMIT 20""", (lid,)):
        ph = normalize_phone(str(raw).split("@")[0])
        if ph and ph not in sender:
            sender.append(ph)
    sender += [ph for ph, role in roles.items() if role == "sender" and ph not in sender]
    # 10.11 (Артём: «иногда номера вообще никакие не показываются»): номер мог быть не в куске текста объекта, а в общей
    # шапке/подвале «портянки» (разбор всё равно привязал его к объекту — колонка phones), а сообщение — уже удалено
    # по сроку KEEP_DAYS или пришло из Telegram/MAX без номера отправителя. Тогда берём номера карточки.
    try:
        card_ph = [p for p in json.loads(r["phones"] or "[]") if p]
    except (TypeError, ValueError):
        card_ph = []
    text_from = "text"
    if not text_ph:
        text_ph = [p for p in card_ph if roles.get(p) != "sender" and p not in sender]
        text_from = "card" if text_ph else None
    if not text_ph and not sender and card_ph:
        sender, text_from = list(card_ph), None
    who = None
    if not sender:   # номера отправителя нет — хотя бы имя, как в чате
        w = conn.execute("""SELECT m.sender_name, m.sender FROM listing_events e JOIN messages m ON m.id = e.message_id
                            WHERE e.listing_id = ? AND (COALESCE(m.sender_name, '') != '' OR COALESCE(m.sender, '') != '')
                            ORDER BY e.ts DESC LIMIT 1""", (lid,)).fetchone()
        if w:
            who = (w[0] or "").strip() or None
    if not text_ph and not sender and r["source"] == "feed":
        from . import config
        if config.FEED_PHONE:
            text_ph, text_from = [config.FEED_PHONE], "feed"
    return {"text_from": text_from, "sender_name": who,"id": r["id"], "title": r["title"], "text_phones": [_pretty(x) for x in text_ph], "raw_phones": text_ph + sender,
            "sender_phones": [_pretty(x) for x in sender], "type": r["type"], "deal": r["deal"], "price": r["price"],
            "price_m2": r["price_m2"], "rooms": r["rooms"], "room_kind": r["room_kind"], "area": r["area"],
            "land": r["land"], "floor": r["floor"], "floors": r["floors"], "complex": r["complex"],
            "district": r["district"], "street": r["street"], "house": r["house"], "settlement": r["settlement"],
            "phones": json.loads(r["phones"] or "[]"), "photos": json.loads(r["photos"] or "[]")[:8],
            "text": r["fragment"] or r["description"] or "", "first_seen": r["first_seen"], "last_seen": r["last_seen"],
            "seen_count": r["seen_count"], "is_active": r["is_active"], "hidden_reason": r["hidden_reason"],
            "favorites": used["favorites"], "clients": used["client_items"], "picks": used["collection_items"],
            "chat": chat[0] if chat else None}


def _pretty(p: str) -> str:
    """+79384222830 → +7 938 422-28-30."""
    d = "".join(ch for ch in p or "" if ch.isdigit())
    return f"+7 {d[1:4]} {d[4:7]}-{d[7:9]}-{d[9:11]}" if len(d) == 11 else (p or "")


_COLS_CACHE: dict = {}


def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    key = (table, col)
    if key not in _COLS_CACHE:
        _COLS_CACHE[key] = any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))
    return _COLS_CACHE[key]


_ACTIVE_PAIR = """AND EXISTS (SELECT 1 FROM listings WHERE id = p.a AND is_active = 1)
                  AND EXISTS (SELECT 1 FROM listings WHERE id = p.b AND is_active = 1)"""


def _queue_data(conn: sqlite3.Connection) -> dict:
    from . import phonegroups
    counts = dict(conn.execute("SELECT status, COUNT(*) FROM dup_pairs GROUP BY status"))
    return {"phone_groups": phonegroups.listing(conn), "counts": {"open": open_count(conn),
            "auto": counts.get("auto", 0), "done": sum(counts.get(k, 0) for k in ("same", "diff", "split", "market"))},
            "stats": stats(conn), "ai_enabled": bool(config.DEEPSEEK_API_KEY)}


def _pair_details(p, a, b):
    return {"id": p["id"], "kind": p["kind"], "kind_label": KINDS.get(p["kind"], p["kind"]),
            "why": json.loads(p["why"] or "[]"), "status": p["status"], "score": p["score"],
            "ai": json.loads(p["ai"]) if p["ai"] else None, "decided_at": p["decided_at"],
            "sim": round(similarity(a["text"], b["text"]), 3)}


def _separate_offers(kind, phones_a, phones_b) -> bool:
    if phones_a and phones_b:
        return not bool(phones_a & phones_b)
    return kind in ("offer_review", "offer_photo")


def comparison(conn: sqlite3.Connection, skip: list[int], anchor: int = 0) -> dict:
    """Одна карточка и все её прямые открытые пары, включая пары за пределами первой страницы."""
    from . import phonegroups
    phonegroups.refresh(conn)
    source = "dup_pairs p JOIN listings a ON a.id=p.a JOIN listings b ON b.id=p.b"
    scope = "p.status = 'open' AND a.is_active=1 AND b.is_active=1"
    if skip:
        marks = ",".join("?" for _ in skip)
        scope += f" AND p.a NOT IN ({marks}) AND p.b NOT IN ({marks})"
    params = (*skip, *skip)
    seed = conn.execute(f"""SELECT p.a, p.b FROM {source} WHERE {scope}
        ORDER BY MAX(a.first_seen, b.first_seen) DESC, p.score DESC, p.id LIMIT 1""", params).fetchone()
    if seed is None:
        return {**_queue_data(conn), "group": None}

    def related(ids):
        marks = ",".join("?" for _ in ids)
        return conn.execute(f"""SELECT p.*, a.first_seen AS first_a, b.first_seen AS first_b FROM {source}
            WHERE {scope} AND (p.a IN ({marks}) OR p.b IN ({marks}))""", (*params, *ids, *ids)).fetchall()

    rows = related((anchor,)) if anchor else []
    if not rows:
        rows = related(tuple(seed))
        anchor = max(seed, key=lambda lid: sum(lid in (p["a"], p["b"]) for p in rows))
        rows = [p for p in rows if anchor in (p["a"], p["b"])]
    rows.sort(key=lambda p: (-(p["first_b"] if p["a"] == anchor else p["first_a"]), -(p["score"] or 0), p["id"]))
    main = _card(conn, anchor)
    choices = []
    if main:
        phones = phonegroups.canon_set(main["phones"])
        for p in rows:
            card = _card(conn, p["b"] if p["a"] == anchor else p["a"])
            if card:
                choices.append({**_pair_details(p, main, card), "card": card,
                                "keep": choose_keep(conn, anchor, card["id"])[0],
                                "market_only": _separate_offers(p["kind"], phones, phonegroups.canon_set(card["phones"]))})
    return {**_queue_data(conn), "group": {"anchor": main, "candidates": choices} if choices else None}


def decide_comparison(conn: sqlite3.Connection, anchor: int, candidates: list[int], verdict: str,
                      user_id: int, keep: int = 0) -> dict:
    """Сохраняет решения только для выбранных активных пар в одной транзакции."""
    from . import phonegroups
    ids = [anchor, *candidates]
    if verdict not in ("same", "diff", "market") or len(set(ids)) != len(ids) or not candidates:
        return {"ok": False, "error": "Выберите карточки и решение."}
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        phonegroups.refresh(conn)
        marks = ",".join("?" for _ in ids)
        active = {r["id"]: _phones(r["phones"]) for r in conn.execute(
            f"SELECT id, phones FROM listings WHERE is_active = 1 AND id IN ({marks})", ids)}
        related = conn.execute(f"SELECT * FROM dup_pairs WHERE a IN ({marks}) AND b IN ({marks})", (*ids, *ids)).fetchall()
        direct = {p["b"] if p["a"] == anchor else p["a"]: p for p in related if anchor in (p["a"], p["b"])}
        if len(active) != len(ids) or any(lid not in direct or direct[lid]["status"] != "open" for lid in candidates):
            return {"ok": False, "stale": True, "error": "Очередь изменилась. Обновили карточки для сравнения."}
        if verdict != "diff" and any(p["status"] in ("diff", "split") for p in related):
            return {"ok": False, "error": "Среди выбранных карточек есть пара, уже отмеченная как разные объекты."}
        if verdict == "same":
            if any(p["status"] == "market" or _separate_offers(p["kind"], active[p["a"]], active[p["b"]])
                   for p in related) or any(a and b and not a & b for a, b in combinations(active.values(), 2)):
                return {"ok": False, "error": "Карточки разных агентов можно связать кнопкой 3."}
            keep = keep if keep in ids else anchor
            for lid in ids:
                if lid == keep:
                    continue
                p = direct[lid] if lid != anchor else direct[keep]
                merge(conn, keep, lid, p["kind"], status="same", user_id=user_id,
                      why=json.loads(p["why"] or "[]"), score=p["score"])
        else:
            conn.executemany("UPDATE dup_pairs SET status=?, decided_at=?, user_id=? WHERE id=?",
                             ((verdict, int(time.time()), user_id, direct[lid]["id"]) for lid in candidates))
    return {"ok": True, "status": verdict, "count": len(candidates)}


def pairs(conn: sqlite3.Connection, status: str = "open", limit: int = 30, offset: int = 0) -> dict:
    # 26.10 («database is locked»): только чтение — раньше здесь была запись (tidy), и база ждала, пока посчитаются карточки
    st = {"open": ("open",), "auto": ("auto",), "done": ("same", "diff", "split", "market")}.get(status, ("open",))
    order = "p.score DESC, p.id" if status == "open" else "p.decided_at DESC, p.id DESC"
    alive = _ACTIVE_PAIR if status == "open" else ""
    rows = conn.execute(f"""SELECT p.* FROM dup_pairs p WHERE p.status IN ({','.join('?' * len(st))}) {alive}
                            ORDER BY {order} LIMIT ? OFFSET ?""", (*st, limit, offset)).fetchall()
    out = []
    for p in rows:
        a, b = _card(conn, p["a"]), _card(conn, p["b"])
        if a is None or b is None:
            continue
        if (a["last_seen"] or 0, a["id"]) > (b["last_seen"] or 0, b["id"]):   # 25.10 (Артём): справа — присланное позже
            a, b = b, a
        keep = p["keep_id"] or choose_keep(conn, p["a"], p["b"])[0]
        out.append({**_pair_details(p, a, b), "a": a, "b": b, "keep": keep})
    return {**_queue_data(conn), "pairs": out}


_AI_SYSTEM = ("Ты помогаешь риелтору Краснодара понять, описывают ли два объявления ОДИН И ТОТ ЖЕ объект недвижимости "
              "(ту же квартиру/дом/участок), или это разные объекты — например, две похожие квартиры одной планировки в одном "
              "доме. Сравнивай факты: адрес, ЖК, этаж, этажность, площадь, комнаты, планировку, ремонт, вид, особенности. "
              "Цена могла измениться — это не различие. Ответь JSON: {\"same\": true|false|null, \"confidence\": число 0..1, "
              "\"why\": \"одно-два коротких предложения по-русски\"}. Если данных не хватает — same: null.")


def ask_ai(conn: sqlite3.Connection, pid: int) -> dict:
    """Подсказка нейросети по паре (только по запросу админа: одна пара — один запрос). Решает всё равно админ."""
    if not config.DEEPSEEK_API_KEY:
        return {"error": "Нейросеть не подключена (нет DEEPSEEK_API_KEY)."}
    p = conn.execute("SELECT * FROM dup_pairs WHERE id = ?", (pid,)).fetchone()
    if p is None:
        return {"error": "Пара не найдена"}
    from . import llm, parser
    cards = [_card(conn, p["a"]), _card(conn, p["b"])]
    if None in cards:
        return {"error": "Одной из карточек уже нет"}

    def block(n, c):
        params = ", ".join(f"{k}: {c[k]}" for k in ("type", "rooms", "area", "floor", "floors", "land", "complex", "street",
                                                       "house", "district") if c[k] not in (None, ""))
        text = parser.strip_phones(c["text"] or "")
        if hasattr(parser, "mask_names"):
            text = parser.mask_names(text)
        return f"Объявление {n} ({params}; цена {_mln(c['price'])}):\n{text[:3000]}"
    db.flush(conn)
    try:
        res = llm.ask_json(_AI_SYSTEM, block(1, cards[0]) + "\n\n" + block(2, cards[1]), max_tokens=300)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Нейросеть не ответила: {e}"}
    ai = {"same": res.get("same"), "confidence": res.get("confidence"), "why": str(res.get("why") or "")[:400],
          "ts": int(time.time())}
    conn.execute("UPDATE dup_pairs SET ai = ? WHERE id = ?", (json.dumps(ai, ensure_ascii=False), pid))
    conn.commit()
    return {"ok": True, "ai": ai}


if __name__ == "__main__":
    c = db.get()
    print(scan(c, budget_s=600))
    print("в очереди:", open_count(c))

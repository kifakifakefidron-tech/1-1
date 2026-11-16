"""16.11 (решения Артёма по точности разбора, пункты 1, 7, 8) — подсказки места, которые раньше терялись.

* Угол улиц — «угол Красной и Северной», «Ставропольская / Вишняковой»: точка там, где дома двух улиц из
  справочника OSM ближе всего друг к другу (не дальше CORNER_KM). Точка примерная (уровень «улица»).
* Ориентиры — «рядом с ТЦ Галерея», «у Северного рынка», «школа 101»: справочник мест OpenStreetMap
  (таблица osm_places, обновляется раз в 30 дней). Точка — ближайший к ориентиру дом своей улицы (если улица есть
  и дом рядом), иначе сам ориентир. Тоже примерная.
* Район по чату — чат, где ≥ 80 % объектов из одного района, или район прямо в названии чата. Слабая подсказка:
  ставится, только если других источников района нет.
* Объяснения админа (learned_rules, kind 'place_phrase'): «эти слова = район / ЖК / улица и дом / точка / не адрес».
  Подставляются новым объявлениям (ingest.save_object) и сразу всем уже собранным, где поля пустые.
* Дом на стыке районов — точный дом ближе BORDER_KM к соседнему нарисованному району ищется по обоим
  (extra_districts; что добавил сервис — listings.border_districts, чтобы пересчитывать).
Слабые источники района (WEAK) в окне объекта подписаны «определён примерно».
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

from . import db, districtmap, geo, osmaddr
from .textnorm import norm, words

log = logging.getLogger(__name__)

CORNER_KM = 0.25        # дома двух улиц дальше этого — улицы не пересекаются, это не угол
LANDMARK_STREET_KM = 1.0  # ближайший дом своей улицы к ориентиру — не дальше этого, иначе точка — сам ориентир
LANDMARK_SPREAD_KM = 1.5  # два одноимённых ориентира дальше этого друг от друга — неоднозначно, не используем
BORDER_KM = 0.10        # дом ближе этого к соседнему району — «на стыке», ищется по обоим районам
CHAT_MIN = 15           # объектов с районом в чате, чтобы по нему судить
CHAT_SHARE = 0.8        # и столько из них — в одном районе
REFRESH_DAYS = 30

HINT_SRC = {"osm-corner": "corner", "osm-landmark": "landmark", "phrase": "phrase"}   # точка → откуда район
WEAK = {"chat": "по чату, где прислали объявление", "landmark": "по ориентиру в тексте", "corner": "по углу улиц",
        "vote-cx": "по другим объявлениям этого ЖК"}
GEO_SRC = {"osm-corner": "угол улиц (OSM, примерно)", "osm-landmark": "рядом с ориентиром (OSM, примерно)",
           "phrase": "ваше объяснение адреса (примерно)"}
DISTRICT_SRC = {"chat": "по чату (там почти всё из этого района)", "landmark": "по ориентиру в тексте (примерно)",
                "corner": "по углу улиц (примерно)", "phrase": "ваше объяснение адреса", "border": "дом на стыке районов"}


def _km(a, b) -> float:
    dy = (a[0] - b[0]) * 111.2
    dx = (a[1] - b[1]) * 111.2 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


# ─── угол улиц ─────────────────────────────────────────────────────────────
_CORNER = re.compile(r"\b(?:на\s+)?(?:угол|углу|пересечени[еия]|перекр[её]ст\w*)\b\.?\s*(?:улиц\w*\s+|ул\.?\s*)?"
                     r"(?P<rest>[^\n.;:!?()]{3,70})", re.IGNORECASE)
_PAIR = re.compile(r"(?<![\w/.-])(?P<a>[А-ЯЁа-яё][а-яё]{3,}(?:\s+[А-ЯЁа-яё][а-яё]{3,})?)\s*(?:/|\\|\s[-–—]\s|[–—-])\s*"
                   r"(?:ул\.?\s*)?(?P<b>[А-ЯЁа-яё][а-яё]{3,}(?:\s+[А-ЯЁа-яё][а-яё]{3,})?)(?![\w/])")
_SPLIT = re.compile(r"\s+(?:и|с|со)\s+|\s*[/\\,–—-]\s*", re.IGNORECASE)
_LEAD = re.compile(r"^(?:ул|улица|улицы|улиц|пр|пр кт|проспект|проспекта|пер|переулок|переулка|проезд|проезда|бульвар|бульвара)\s+")
# «Красной» → «Красная», «Ставропольской» → «Ставропольская»: агенты пишут «угол чего»
_GEN = (("ской", "ская"), ("цкой", "цкая"), ("ной", "ная"), ("вой", "вая"), ("ой", "ая"), ("ей", "яя"))


def _nominative(w: str) -> str:
    out = []
    for p in w.split():
        for a, b in _GEN:
            if len(p) > 4 and p.endswith(a):
                p = p[: -len(a)] + b
                break
        out.append(p)
    return " ".join(out)


def resolve_street(conn: sqlite3.Connection, name: str | None) -> str | None:
    """Название улицы в любом падеже/с опечаткой → ключ улицы Краснодара в справочнике OSM (или None)."""
    w = _LEAD.sub("", words(name or ""))
    w = re.sub(r"\s+\d.*$", "", w).strip()   # «Красной 145» → «красной»
    if len(w) < 4:
        return None
    idx = osmaddr.index(conn)
    for v in dict.fromkeys((w, _nominative(w))):
        k = osmaddr.street_key(v)
        s = idx["streets"].get(k)
        if s and "" in s["pts"]:
            return k
    fixed = osmaddr.fix_street(conn, _nominative(w))
    if fixed:
        k = osmaddr.street_key(fixed)
        s = idx["streets"].get(k)
        if s and "" in s["pts"]:
            return k
    return None


_cross_cache: dict = {}


def crossing(conn: sqlite3.Connection, ka: str, kb: str) -> tuple[float, float, float] | None:
    """Где улицы встречаются: середина между ближайшими домами двух улиц (lat, lon, км между ними)."""
    if ka == kb:
        return None
    idx = osmaddr.index(conn)
    key = (idx.get("ver"), *sorted((ka, kb)))
    if key in _cross_cache:
        return _cross_cache[key]
    pa = idx["streets"][ka]["pts"].get("", [])
    pb = idx["streets"][kb]["pts"].get("", [])
    best = None
    if pa and pb:
        sa, sb = max(1, len(pa) // 300), max(1, len(pb) // 300)
        c = math.cos(math.radians(45.05))
        for a in pa[::sa]:
            for b in pb[::sb]:
                d = (a[0] - b[0]) ** 2 + ((a[1] - b[1]) * c) ** 2
                if best is None or d < best[0]:
                    best = (d, a, b)
    out = None
    if best:
        km = math.sqrt(best[0]) * 111.2
        if km <= CORNER_KM:
            a, b = best[1], best[2]
            out = (round((a[0] + b[0]) / 2, 6), round((a[1] + b[1]) / 2, 6), round(km, 3))
    if len(_cross_cache) > 20000:
        _cross_cache.clear()
    _cross_cache[key] = out
    return out


def corner(conn: sqlite3.Connection, text: str | None) -> dict | None:
    """«угол Красной и Северной», «Ставропольская / Вишняковой» → {a, b, lat, lon} (только если улицы правда пересекаются)."""
    if not text or not osmaddr.loaded(conn):
        return None
    pairs = []
    for m in _CORNER.finditer(text):
        parts = [p for p in _SPLIT.split(m.group("rest")) if p.strip()]
        if len(parts) >= 2:
            pairs.append((parts[0], parts[1]))
    for m in _PAIR.finditer(text):
        pairs.append((m.group("a"), m.group("b")))
    for a, b in pairs:
        ka, kb = resolve_street(conn, a), resolve_street(conn, b)
        if not ka or not kb:
            continue
        hit = crossing(conn, ka, kb)
        if hit:
            idx = osmaddr.index(conn)
            return {"a": idx["streets"][ka]["name"], "b": idx["streets"][kb]["name"], "lat": hit[0], "lon": hit[1]}
    return None


# ─── ориентиры ─────────────────────────────────────────────────────────────
PLACES_QUERY = f"""[out:csv(::lat,::lon,name,alt_name,shop,amenity,leisure,railway;false;"\\t")][timeout:180];
(nwr["shop"="mall"]["name"]({osmaddr.BBOX});
 nwr["amenity"~"^(marketplace|school|college|university|hospital)$"]["name"]({osmaddr.BBOX});
 nwr["leisure"~"^(park|stadium)$"]["name"]({osmaddr.BBOX});
 nwr["railway"="station"]["name"]({osmaddr.BBOX}););
out center;"""
_KIND = {"mall": "mall", "marketplace": "market", "school": "school", "college": "school", "university": "uni",
         "hospital": "hospital", "park": "park", "stadium": "stadium", "station": "station"}
# слова, которые не название: «ТЦ», «рынок», «парк»… (их ищем как метку рядом с названием)
_GENERIC = set("""тц трц трк тк торговый торгово развлекательный центр комплекс молл рынок рыночный парк сквер сад стадион
    вокзал железнодорожный жд станция ст больница больничный клиническая краевая городская мбоу маоу мау мбу гбу гбуз гбоу
    имени им и в на краснодар краснодарский краснодарская кк""".split())
_MARK = {}
for _w, _k in (("тц", "mall"), ("трц", "mall"), ("трк", "mall"), ("молл", "mall"), ("рынок", "market"), ("рынка", "market"),
               ("рынке", "market"), ("рынку", "market"), ("рынком", "market"), ("парк", "park"), ("парка", "park"),
               ("парке", "park"), ("парку", "park"), ("парком", "park"), ("сквер", "park"), ("сквера", "park"),
               ("скверу", "park"), ("стадион", "stadium"), ("стадиона", "stadium"), ("стадиону", "stadium"),
               ("стадионом", "stadium"), ("вокзал", "station"), ("вокзала", "station"), ("вокзалу", "station"),
               ("больница", "hospital"), ("больницы", "hospital"), ("больнице", "hospital"), ("больницей", "hospital"),
               ("университет", "uni"), ("университета", "uni"), ("университету", "uni")):
    _MARK[_w] = _k
_SCHOOL = re.compile(r"\b(?P<w>школ\w*|сош|гимнази\w*|лице(?:й|я|ю|ем|е))\s+(?:n\s+|номер\s+)?(?P<n>\d{1,3})\b"
                     r"|\b(?P<n2>\d{1,3})\s*(?:я|й|ой|ая)?\s+(?P<w2>школ\w*|гимнази\w*|лице(?:й|я|ю|ем|е))\b")
_ENDS = ("ого", "его", "ому", "ему", "ыми", "ими", "ой", "ей", "ая", "яя", "ый", "ий", "ое", "ее", "ые", "ие", "ым", "им",
         "ом", "ем", "ах", "ях", "ам", "ям", "ь", "а", "я", "у", "ю", "е", "ы", "и", "о")


def _stem(w: str) -> str:
    """Основа слова для сравнения: «Галерея» = «Галерее» = «галерею» → «галер» (первые 5 букв без окончания)."""
    for e in _ENDS:
        if w.endswith(e) and len(w) - len(e) >= 4:
            w = w[: -len(e)]
            break
    return w[:5]


def _school_key(word: str, n: str) -> str:
    return f"{'гимназия' if word.startswith('гимнази') else 'лицей' if word.startswith('лице') else 'школа'} {int(n)}"


def place_keys(kind: str, name: str) -> list[str]:
    """Ключи ориентира из названия OSM: «МБОУ СОШ № 101» → «школа 101»; «Северный рынок» → «северн»."""
    if kind == "school":
        n = re.search(r"№\s*(\d{1,3})", norm(name)) or re.search(r"\b(\d{1,3})\b", words(name))
        w = f" {words(name)}"
        word = "гимнази" if " гимнази" in w else "лице" if re.search(r" лице(?:й|я|ю|ем|е)(?!\w)", w) else "школ"
        return [_school_key(word, n.group(1))] if n else []
    ws = [x for x in words(name).split() if x not in _GENERIC and not x.isdigit()]
    k = " ".join(_stem(x) for x in ws)
    return [k] if len(k.replace(" ", "")) >= 4 else []


def load_places(conn: sqlite3.Connection, text: str | None = None) -> int:
    """Скачать ориентиры из OpenStreetMap (или взять готовый TSV) и заменить справочник."""
    if text is None:
        db.flush(conn)
        last = None
        for url in osmaddr.SERVERS:
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": PLACES_QUERY}).encode(),
                                             headers={"User-Agent": "1plus1-search/1.0 (Krasnodar real estate)"})
                with urllib.request.urlopen(req, timeout=200) as resp:
                    text = resp.read().decode("utf-8")
                if text.count("\n") > 50:
                    break
                last, text = ValueError("слишком мало мест в ответе"), None
            except Exception as e:  # noqa: BLE001 — следующий сервер
                last, text = e, None
        if text is None:
            raise ValueError(f"OpenStreetMap не ответил: {last}")
    rows = []
    for line in text.splitlines():
        p = line.split("\t")
        if len(p) < 8:
            continue
        try:
            lat, lon = float(p[0]), float(p[1])
        except ValueError:
            continue
        kind = _KIND.get(p[4]) or _KIND.get(p[5]) or _KIND.get(p[6]) or ("station" if p[7] == "station" else None)
        if not kind:
            continue
        for name in {p[2].strip(), *[x.strip() for x in p[3].split(";")]} - {""}:
            for k in place_keys(kind, name):
                rows.append((kind, k, name, round(lat, 6), round(lon, 6)))
    if len(rows) < 50:
        raise ValueError("В ответе OpenStreetMap слишком мало мест — справочник не меняю")
    conn.execute("DELETE FROM osm_places")
    conn.executemany("INSERT INTO osm_places (kind, key, name, lat, lon) VALUES (?,?,?,?,?)", rows)
    db.set_state(conn, "osm_places_ts", str(int(time.time())))
    conn.commit()
    _places.clear()
    return len(rows)


def maybe_refresh(conn: sqlite3.Connection) -> int | None:
    ts = int(db.get_state(conn, "osm_places_ts") or 0)
    tried = int(db.get_state(conn, "osm_places_try") or 0)
    now = int(time.time())
    if now - ts < REFRESH_DAYS * 86400 or now - tried < 6 * 3600:
        return None
    db.set_state(conn, "osm_places_try", str(now))
    try:
        n = load_places(conn)
        log.info("Ориентиры OSM: %s", n)
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("Ориентиры OSM не обновились: %s", e)
        return None


_places: dict = {}


def places(conn: sqlite3.Connection) -> dict:
    """{(вид, ключ): (lat, lon, название)} — одноимённые далеко друг от друга выброшены (неоднозначно)."""
    ver = db.get_state(conn, "osm_places_ts") or ""
    if _places.get("ver") != ver:
        groups: dict = defaultdict(list)
        for r in conn.execute("SELECT kind, key, name, lat, lon FROM osm_places"):
            groups[(r[0], r[1])].append((r[3], r[4], r[2]))
        out = {}
        for k, pts in groups.items():
            mid = (sorted(p[0] for p in pts)[len(pts) // 2], sorted(p[1] for p in pts)[len(pts) // 2])
            if max(_km(mid, p) for p in pts) <= LANDMARK_SPREAD_KM:
                out[k] = (round(mid[0], 6), round(mid[1], 6), pts[0][2])
        _places.clear()
        _places.update(ver=ver, map=out)
    return _places["map"]


def landmark(conn: sqlite3.Connection, text: str | None) -> dict | None:
    """Первый узнанный ориентир в тексте → {kind, name, lat, lon, said}."""
    if not text:
        return None
    pl = places(conn)
    if not pl:
        return None
    w = words(text)
    for m in _SCHOOL.finditer(w):
        word, n = (m.group("w"), m.group("n")) if m.group("w") else (m.group("w2"), m.group("n2"))
        hit = pl.get(("school", _school_key(word, n)))
        if hit:
            return {"kind": "school", "name": hit[2], "lat": hit[0], "lon": hit[1], "said": m.group(0)}
    tw = w.split()
    for i, t in enumerate(tw):
        kind = _MARK.get(t)
        if not kind:
            continue
        spans = [tw[i + 1:i + 1 + n] for n in (3, 2, 1)] + [tw[max(0, i - n):i] for n in (2, 1)]
        for span in spans:
            if not span or len(span) < 1:
                continue
            k = " ".join(_stem(x) for x in span if x not in _GENERIC)
            if len(k.replace(" ", "")) < 4:
                continue
            hit = pl.get((kind, k))
            if hit:
                return {"kind": kind, "name": hit[2], "lat": hit[0], "lon": hit[1], "said": " ".join([t, *span])}
    return None


def street_near(conn: sqlite3.Connection, street: str | None, lat: float, lon: float) -> tuple[float, float] | None:
    """Ближайший к ориентиру дом своей улицы (если он не дальше LANDMARK_STREET_KM)."""
    k = resolve_street(conn, street) if street else None
    if not k:
        return None
    pts = osmaddr.index(conn)["streets"][k]["pts"].get("", [])
    best = min(pts, key=lambda p: _km(p, (lat, lon)), default=None)
    return best if best and _km(best, (lat, lon)) <= LANDMARK_STREET_KM else None


# ─── объяснения админа: «эти слова = …» ───────────────────────────────────
_phr: dict = {}


def phrases(conn: sqlite3.Connection, fresh: bool = False) -> dict:
    """{фраза (words): значение} — перечитываются, как только в learned_rules что-то поменялось."""
    sig = tuple(conn.execute("SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM learned_rules WHERE kind = 'place_phrase'").fetchone())
    if fresh or _phr.get("sig") != sig:
        _phr.clear()
        _phr.update(sig=sig, map={r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT key, value FROM learned_rules WHERE kind = 'place_phrase'")})
    return _phr["map"]


def phrase_hits(conn: sqlite3.Connection, text: str | None) -> list[tuple[str, dict]]:
    w = f" {words(text or '')} "
    return [(k, v) for k, v in phrases(conn).items() if f" {k} " in w]


def apply_phrases(conn: sqlite3.Connection, o) -> bool:
    """Новое объявление: пустые район/ЖК/улица/дом — из ваших объяснений (ParsedObject или dict)."""
    get = (lambda k: o.get(k)) if isinstance(o, dict) else (lambda k: getattr(o, k))

    def put(k, v):
        if isinstance(o, dict):
            o[k] = v
        else:
            setattr(o, k, v)
    changed = False
    for _k, v in phrase_hits(conn, get("fragment")):
        if v.get("none"):
            continue
        for f in ("complex", "street", "house", "settlement"):
            if v.get(f) and not get(f):
                put(f, v[f])
                changed = True
        if v.get("district") and not get("district") and not get("settlement"):
            put("district", v["district"])
            changed = True
    return changed


def phrase_point(conn: sqlite3.Connection, text: str | None) -> tuple[float, float] | None:
    for _k, v in phrase_hits(conn, text):
        if v.get("lat") is not None:
            return v["lat"], v["lon"]
    return None


def learn_phrase(conn: sqlite3.Connection, phrase: str, v: dict, user_id: int = 0) -> dict:
    """Запомнить объяснение и сразу подставить его всем объявлениям с этими словами (где поле пустое)."""
    key = words(phrase)
    if not 3 <= len(key) <= 80:
        return {"error": "Фраза — от 3 до 80 знаков: именно те слова, что означают адрес."}
    val = {}
    if v.get("none"):
        val = {"none": True}
    else:
        for f in ("district", "complex", "street", "house", "settlement"):
            x = (v.get(f) or "").strip()
            if x:
                val[f] = x
        if val.get("district"):
            val["district"] = geo.canonical_district(val["district"]) or val["district"]
        try:
            if v.get("lat") not in (None, "") and v.get("lon") not in (None, ""):
                lat, lon = float(v["lat"]), float(v["lon"])
                if 44.5 < lat < 45.6 and 38.3 < lon < 39.8:
                    val["lat"], val["lon"] = round(lat, 6), round(lon, 6)
        except (TypeError, ValueError):
            pass
        if not val:
            return {"error": "Выберите, что значат эти слова: район, ЖК, улица, точка на карте — или «не адрес»."}
    parts = ["не адрес"] if val.get("none") else [x for x in (
        val.get("district") and f"район {val['district']}", val.get("complex") and f"ЖК {val['complex']}",
        val.get("street") and f"ул. {val['street']}{', ' + val['house'] if val.get('house') else ''}",
        val.get("settlement"), val.get("lat") is not None and "точка на карте") if x]
    label = f"«{key}» → {', '.join(parts)}"
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('place_phrase', ?, ?, ?, 1, ?)
                    ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label, n = n + 1, ts = excluded.ts""",
                 (key, json.dumps(val, ensure_ascii=False), label, int(time.time())))
    conn.commit()
    phrases(conn, fresh=True)
    return {"ok": True, "phrase": key, "label": label, "changed": apply_phrase_existing(conn, key, val, user_id)}


def apply_phrase_existing(conn: sqlite3.Connection, key: str, val: dict, user_id: int = 0) -> int:
    """Объяснение — уже собранным объявлениям с этими словами: только пустые поля, правленые админом не трогаем."""
    if val.get("none"):
        return 0
    from .ingest import _reindex, make_search_text, make_title
    n = 0
    now = int(time.time())
    # LIKE в SQLite не знает регистра кириллицы — слова сравниваем в Python (только у объектов с пустыми полями)
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND admin_fixed = 0 AND source = 'chat'
                             AND (district IS NULL OR complex IS NULL OR street IS NULL OR settlement IS NULL
                                  OR lat IS NULL OR geo_status = 'approx')""").fetchall():
        if f" {key} " not in f" {words(r['fragment'] or '')} ":
            continue
        d = dict(r)
        upd = {}
        for f in ("complex", "street", "house", "settlement"):
            if val.get(f) and not d[f]:
                upd[f] = val[f]
        if val.get("district") and not d["district"] and not d["settlement"] and not upd.get("settlement"):
            upd["district"] = val["district"]
        geo_new = val.get("lat") is not None and (d["lat"] is None or d["geo_status"] in ("approx", "none", "skip", "pending")) \
            and d["geo_status"] not in ("manual", "feed")
        if not upd and not geo_new:
            continue
        for f, x in upd.items():
            conn.execute("""INSERT INTO listing_edits (listing_id, user_id, ts, field, old, new, src)
                            VALUES (?, ?, ?, ?, ?, ?, 'объяснение адреса')""",
                         (r["id"], user_id, now, f, json.dumps(d[f], ensure_ascii=False), json.dumps(x, ensure_ascii=False)))
        d.update(upd)
        d["title"] = make_title(d)
        d["search_text"] = make_search_text(d)
        sets = {**upd, "title": d["title"], "search_text": d["search_text"]}
        if upd.get("district"):
            sets["district_src"] = "phrase"
        if geo_new:
            sets.update(lat=val["lat"], lon=val["lon"], geo_status="approx", geo_src="phrase")
        elif upd.get("street") or upd.get("house") or upd.get("complex"):
            if d["geo_status"] not in ("manual", "learned", "feed"):
                sets["geo_status"] = "pending"
        conn.execute(f"UPDATE listings SET {', '.join(k + ' = ?' for k in sets)} WHERE id = ?", (*sets.values(), r["id"]))
        _reindex(conn, r["id"], d["search_text"])
        n += 1
    conn.commit()
    return n


# ─── точка-подсказка для цепочки autolearn.Known.point ───────────────────
_hint_cache: dict = {}


def point_hint(conn: sqlite3.Connection, row) -> tuple[float, float, str, str] | None:
    """Ваше объяснение → угол улиц → ориентир. (lat, lon, 'approx', источник) или None. Только Краснодар."""
    text = row["fragment"] if "fragment" in row.keys() else None
    if not text:
        return None
    if row["settlement"] and not geo.canonical_district(row["settlement"]):
        return None   # станица / посёлок не в Краснодаре — справочник улиц и ориентиров городской
    key = (row["id"], len(text), db.get_state(conn, "osm_addr_ts"), db.get_state(conn, "osm_places_ts"),
           _phr.get("sig"), row["street"])
    if key in _hint_cache:
        return _hint_cache[key]
    out = None
    pp = phrase_point(conn, text)
    if pp:
        out = (*pp, "approx", "phrase")
    if not out:
        c = corner(conn, text)
        if c:
            out = (c["lat"], c["lon"], "approx", "osm-corner")
    if not out:
        lm = landmark(conn, text)
        if lm:
            near = street_near(conn, row["street"], lm["lat"], lm["lon"])
            out = (*(near or (lm["lat"], lm["lon"])), "approx", "osm-landmark")
    if len(_hint_cache) > 50000:
        _hint_cache.clear()
    _hint_cache[key] = out
    return out


def district_at_hint(conn: sqlite3.Connection, lat: float, lon: float) -> str | None:
    hit = districtmap.district_at(conn, lat, lon)
    return hit[0] if hit and hit[1] == "district" else None


# ─── район по чату ───────────────────────────────────────────────────────
_chat: dict = {"t": 0}


def _districts_in(name: str | None) -> set[str]:
    w = f" {words(name or '')} "
    out = set()
    for alias, canon in geo._ALIASES:
        if f" {alias} " in w and alias not in geo._AMBIGUOUS:
            out.add(canon)
    return out


def chat_priors(conn: sqlite3.Connection) -> dict:
    """{(source, chat_id): район} — для чатов, где почти всё из одного района (или район в названии чата)."""
    if time.time() - _chat["t"] < 600:
        return _chat["map"]
    since = int(time.time()) - 60 * 86400
    stats: dict = defaultdict(Counter)
    for r in conn.execute("""SELECT m.source, m.chat_id, l.district, COUNT(DISTINCT l.id) FROM listings l
                             JOIN listing_events e ON e.listing_id = l.id JOIN messages m ON m.id = e.message_id
                             WHERE l.district IS NOT NULL AND l.settlement IS NULL AND l.last_seen > ?
                             AND COALESCE(l.district_src, '') NOT IN ('chat', 'border') AND m.chat_id IS NOT NULL
                             GROUP BY m.source, m.chat_id, l.district""", (since,)):
        stats[(r[0], r[1])][r[2]] += r[3]
    names = {(r[0], r[1]): r[2] for r in conn.execute("SELECT source, chat_id, name FROM chats")}
    out = {}
    for ch in set(stats) | set(names):
        c = stats.get(ch, Counter())
        total = sum(c.values())
        top = c.most_common(1)[0] if c else None
        if top and total >= CHAT_MIN and top[1] / total >= CHAT_SHARE:
            out[ch] = top[0]
            continue
        named = _districts_in(names.get(ch))
        if len(named) == 1:
            d = next(iter(named))
            if total < 5 or c[d] / total >= 0.5:
                out[ch] = d
    _chat.update(t=time.time(), map=out, stats=stats)
    return out


def chat_district(conn: sqlite3.Connection, listing_id: int) -> str | None:
    """Район по чатам, где прислали объявление: все «районные» чаты согласны — этот район."""
    pri = chat_priors(conn)
    if not pri:
        return None
    got = {pri.get((r[0], r[1])) for r in conn.execute(
        """SELECT DISTINCT m.source, m.chat_id FROM listing_events e JOIN messages m ON m.id = e.message_id
           WHERE e.listing_id = ?""", (listing_id,))} - {None}
    return next(iter(got)) if len(got) == 1 else None


# ─── дом на стыке районов ────────────────────────────────────────────────
def _seg_km(p, a, b) -> float:
    c = math.cos(math.radians(p[0]))
    ax, ay, bx, by, px, py = a[1] * c, a[0], b[1] * c, b[0], p[1] * c, p[0]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy)) * 111.2


def near_districts(conn: sqlite3.Connection, lat: float, lon: float, km: float = BORDER_KM) -> list[str]:
    """Нарисованные районы, внутри которых точка или ближе km к их границе."""
    m_lat, m_lon = km / 111.2, km / (111.2 * math.cos(math.radians(lat)))
    out = []
    for name, kind, polys, _a, (a, b, c, d) in districtmap._polys(conn):
        if kind != "district" or not (a - m_lat <= lat <= b + m_lat and c - m_lon <= lon <= d + m_lon) or name in out:
            continue
        if any(districtmap._inside(lat, lon, ring) for ring in polys) or \
                min(_seg_km((lat, lon), p, q) for ring in polys for p, q in zip(ring, ring[1:] + ring[:1])) <= km:
            out.append(name)
    return out


def border(conn: sqlite3.Connection) -> int:
    """Точный дом у границы соседнего района — ищется по обоим. Пересчитывается целиком (раз в час)."""
    from .search import GEO_LEVEL
    if not any(k == "district" for _n, k, *_ in districtmap._polys(conn)):
        return 0
    n = 0
    for r in conn.execute(f"""SELECT l.id, l.lat, l.lon, l.district, l.extra_districts, l.border_districts FROM listings l
                              WHERE l.is_active = 1 AND l.settlement IS NULL AND l.district IS NOT NULL
                              AND (({GEO_LEVEL} = 'house' AND l.admin_fixed = 0) OR l.border_districts IS NOT NULL)""").fetchall():
        prev = json.loads(r["border_districts"] or "[]")
        extra = [x for x in json.loads(r["extra_districts"] or "[]") if x not in prev]
        add = [] if r["lat"] is None else [x for x in near_districts(conn, r["lat"], r["lon"])
                                           if x != r["district"] and x not in extra]
        new_extra = extra + add
        if add == prev:
            continue
        conn.execute("UPDATE listings SET extra_districts = ?, border_districts = ? WHERE id = ?",
                     (json.dumps(new_extra, ensure_ascii=False), json.dumps(add, ensure_ascii=False) if add else None, r["id"]))
        n += 1
    conn.commit()
    return n


def maybe_border(conn: sqlite3.Connection) -> int | None:
    if time.time() - float(db.get_state(conn, "border_ts") or 0) < 3600:
        return None
    db.set_state(conn, "border_ts", str(int(time.time())))
    return border(conn)

"""Свой справочник адресов Краснодара из OpenStreetMap (© участники OpenStreetMap, лицензия ODbL).

Раз в месяц worker скачивает все дома с адресом в границах Краснодара с пригородами (~190 тыс., ~12 МБ, ~20 с)
в таблицу osm_addr. Дальше точка ищется у себя — мгновенно, без лимита «1 запрос в секунду»:
  * точный дом (улица + номер, в т. ч. «62/1к3», «39 корпус 2») → 'osm-house';
  * такого номера нет — ближайший дом той же стороны улицы (±4 номера) → 'osm-near' (разница — десятки метров);
  * дома нет совсем — середина улицы → примерная точка 'osm-street'.
Опечатки в улицах («Ставрапольская», «Жигуленко» без имени) исправляются по списку улиц OSM и нашего справочника
(fix_street) — до поиска дублей и правил (ingest.save_object), чтобы одна улица не превращалась в две.
"""
from __future__ import annotations

import difflib
import logging
import re
import sqlite3
import statistics
import time
import urllib.parse
import urllib.request

from . import db, geo
from .textnorm import norm, words

log = logging.getLogger(__name__)

BBOX = "44.93,38.75,45.22,39.30"   # Краснодар с пригородами (как CITY_BOX геокодера)
SERVERS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
           "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
QUERY = f"""[out:csv(::lat,::lon,"addr:street","addr:housenumber","addr:city";false;"\\t")][timeout:300];
(node["addr:housenumber"]["addr:street"]({BBOX});
 way["addr:housenumber"]["addr:street"]({BBOX});
 relation["addr:housenumber"]["addr:street"]({BBOX}););
out center;"""
REFRESH_DAYS = 30
NEAR = 4            # «нет дома 45» — берём 43/47, но не дальше 4 номеров
_TYPE = re.compile(r"\b(?:улица|ул|переулок|пер|проезд|проспект|пр-кт|бульвар|б-р|шоссе|площадь|пл|им|имени)\b\.?",
                   re.IGNORECASE)
_STREET_WORDS = r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе|пл|площадь|им|имени)\b"


def street_key(v: str | None) -> str:
    """Как learning._street_key: без «улица/переулок/имени…», нижний регистр, ё→е; «9-я Тихая» = «9 Тихая»."""
    k = re.sub(_STREET_WORDS, " ", words(v or ""))
    return " ".join(re.sub(r"(\d+)\s+(?:я|й|ая|ой|ий|ый)\b", r"\1", k).split())


def house_key(v: str | None) -> str:
    """«62/1 корпус 3» → «62/1к3»; «12 А» → «12а»; «39, к.2» → «39к2»."""
    h = norm(v or "")
    h = re.sub(r"\s*(?:корпус|корп|кор|к)\.?\s*(?=\d)", "к", h)
    h = re.sub(r"\s*(?:литер|лит|л)\.?\s*(?=[а-я])", "", h)
    h = re.sub(r"\s*(?:строение|стр)\.?\s*(?=\d)", "с", h)
    return re.sub(r"[^0-9a-zа-я/]", "", h)


def _base(hk: str) -> int | None:
    m = re.match(r"\d+", hk)
    return int(m.group()) if m else None


def _display(name: str) -> str:
    """«Ставропольская улица» → «Ставропольская»; «улица Евгении Жигуленко» → «Евгении Жигуленко»."""
    return " ".join(_TYPE.sub(" ", name).split()) or name


def _city(v: str | None) -> str:
    c = re.sub(r"^(?:г|город|ст|ст ца|станица|п|пос|поселок|х|хутор|аул|снт|ст)\s+", "", words(v or ""))
    c = re.sub(r"\s+(?:ст|снт)$", "", c)   # «Родник СТ»
    return "" if c == "краснодар" else c


# ─── загрузка ──────────────────────────────────────────────────────────────
def _fetch() -> str:
    last: Exception | None = None
    for url in SERVERS:
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": QUERY}).encode(),
                                         headers={"User-Agent": "1plus1-search/1.0 (Krasnodar real estate)"})
            with urllib.request.urlopen(req, timeout=320) as resp:
                text = resp.read().decode("utf-8")
            if text.count("\n") > 1000:
                return text
            last = ValueError("слишком мало адресов в ответе")
        except Exception as e:  # noqa: BLE001 — пробуем следующий сервер
            last = e
    raise ValueError(f"OpenStreetMap не ответил: {last}")


def load(conn: sqlite3.Connection, text: str | None = None) -> int:
    """Скачать адреса (или взять готовый текст TSV) и заменить справочник. Возвращает число домов."""
    if text is None:
        db.flush(conn)   # не держать базу, пока ждём ответа
        text = _fetch()
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        sk, hk = street_key(parts[2]), house_key(parts[3])
        if sk and hk:
            rows.append((sk, _display(parts[2].strip()), hk, _city(parts[4] if len(parts) > 4 else ""),
                         round(lat, 6), round(lon, 6)))
    if len(rows) < 1000:
        raise ValueError("В ответе OpenStreetMap слишком мало адресов — справочник не меняю")
    conn.execute("DELETE FROM osm_addr")
    conn.executemany("INSERT INTO osm_addr (street_key, street, house_key, city, lat, lon) VALUES (?,?,?,?,?,?)", rows)
    db.set_state(conn, "osm_addr_ts", str(int(time.time())))
    conn.commit()
    _index.clear()
    return len(rows)


def maybe_refresh(conn: sqlite3.Connection) -> int | None:
    """Из worker: справочника нет или ему больше месяца — скачать (не чаще раза в 6 часов, если сеть подводит)."""
    ts = int(db.get_state(conn, "osm_addr_ts") or 0)
    tried = int(db.get_state(conn, "osm_addr_try") or 0)
    now = int(time.time())
    if now - ts < REFRESH_DAYS * 86400 or now - tried < 6 * 3600:
        return None
    db.set_state(conn, "osm_addr_try", str(now))
    try:
        n = load(conn)
        log.info("Справочник адресов OSM: %s домов", n)
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("Справочник адресов OSM не обновился: %s", e)
        return None


# ─── поиск ────────────────────────────────────────────────────────────────
_index: dict = {}


def index(conn: sqlite3.Connection) -> dict:
    """Справочник в памяти (перечитывается, когда его обновили)."""
    ver = db.get_state(conn, "osm_addr_ts") or ""
    if _index.get("ver") != ver:
        streets: dict[str, dict] = {}
        for r in conn.execute("SELECT street_key, street, house_key, city, lat, lon FROM osm_addr"):
            s = streets.setdefault(r[0], {"name": r[1], "houses": {}, "pts": {}})
            s["houses"].setdefault((r[3], r[2]), (r[4], r[5]))
            s["pts"].setdefault(r[3], []).append((r[4], r[5]))
        names = {k: v["name"] for k, v in streets.items()}
        for name in geo.street_names():   # улицы нашего справочника — тоже «правильные»
            names.setdefault(street_key(name), name)
        _index.clear()
        _index.update(ver=ver, streets=streets, names=names, keys=sorted(names), fixed={})
    return _index


def point(conn: sqlite3.Connection, street: str | None, house: str | None,
          settlement: str | None = None) -> tuple[float, float, str, str] | None:
    """(lat, lon, geo_status, geo_src) по справочнику или None."""
    idx = index(conn)
    s = idx["streets"].get(street_key(fix_street(conn, street) or street))
    if not s:
        return None
    city = _city(settlement)
    if city not in s["pts"]:
        return None   # улица есть, но в другом населённом пункте
    hk = house_key(house)
    if hk:
        hit = s["houses"].get((city, hk))
        if hit:
            return hit[0], hit[1], "ok", "osm-house"
        b = _base(hk)
        if b is not None:
            near = [(abs(_base(h) - b), (lat, lon)) for (c, h), (lat, lon) in s["houses"].items()
                    if c == city and _base(h) is not None and _base(h) % 2 == b % 2 and abs(_base(h) - b) <= NEAR]
            if near:
                return *min(near)[1], "ok", "osm-near"
    pts = s["pts"][city]
    return round(statistics.median(p[0] for p in pts), 6), round(statistics.median(p[1] for p in pts), 6), "approx", "osm-street"


def fix_street(conn: sqlite3.Connection, street: str | None) -> str | None:
    """Опечатка в названии улицы → правильное название (или None, если исправлять нечего / не уверены)."""
    if not street:
        return None
    idx = index(conn)
    k = street_key(street)
    if not k or k in idx["names"] or len(k) < 5:
        return None
    if k in idx["fixed"]:
        return idx["fixed"][k]
    digits = re.findall(r"\d+", k)
    # Опечатка = 1 ошибка (пропуск, лишняя, замена, перестановка букв); в длинных названиях — до 2.
    # «Жемчужина» ≠ «Жемчужная» (2 ошибки в коротком слове) — не трогаем: это скорее ЖК, а не улица.
    limit = 1 if len(k) < 10 else 2
    cands = sorted((_typos(k, c), c) for c in difflib.get_close_matches(k, idx["keys"], n=5, cutoff=0.8)
                   if re.findall(r"\d+", c) == digits)
    cands = [x for x in cands if x[0] <= limit]
    out = None
    if cands and (len(cands) == 1 or cands[1][0] > cands[0][0]):   # два одинаково близких варианта — не гадаем
        out = idx["names"][cands[0][1]]
    idx["fixed"][k] = out
    return out


def _typos(a: str, b: str) -> int:
    """Сколько опечаток между словами (вставка, пропуск, замена, перестановка соседних букв)."""
    prev2, prev = None, list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            if prev2 is not None and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1]


def apply(conn: sqlite3.Connection, o) -> bool:
    """Исправить улицу у объекта (ParsedObject или dict). True — исправили."""
    get = (lambda k: o.get(k)) if isinstance(o, dict) else (lambda k: getattr(o, k, None))
    new = fix_street(conn, get("street"))
    if not new or new == get("street"):
        return False
    if isinstance(o, dict):
        o["street"] = new
    else:
        o.street = new
    return True


def loaded(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM osm_addr").fetchone()[0]

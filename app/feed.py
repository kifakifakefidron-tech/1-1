"""Объекты СТРЕЛ из фида сайта arrowsrealty.ru (Тильда, YML): с фото, описанием и ссылкой.

Фид забирается раз в FEED_HOURS часов (worker). Объект, которого больше нет в фиде,
скрывается. Объекты фида не склеиваются с объявлениями из чатов и не устаревают
сами — их актуальность определяет сайт СТРЕЛ.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET

from . import config, db, geo, geocode, parser, rules
from .ingest import _price_m2, _reindex, make_search_text, make_title
from .rules import rooms_mask

log = logging.getLogger(__name__)

# «♟Е2 КВ♟» → (тип, комнаты, планировка)
_VENDOR = [
    (r"СТУДИ", ("flat", 0, "studio")), (r"ДОМ", ("house", None, None)), (r"УЧАСТ", ("land", None, None)),
    (r"КОММЕРЦ", ("commercial", None, None)), (r"МИНИ\s*(\d)", ("flat", "n", "mini")),
    (r"(?<![А-ЯЁ])Е\s*(\d)", ("flat", "n", "euro")), (r"(\d)\s*К?\s*КВ", ("flat", "n", "classic")),
]


def _text(desc: str | None) -> str:
    t = re.sub(r"(?i)<br\s*/?>", "\n", desc or "")
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    return "\n".join(ln.strip() for ln in t.splitlines() if ln.strip())


def _vendor(v: str | None) -> tuple[str | None, int | None, str | None]:
    v = (v or "").upper()
    for rx, (t, rooms, kind) in _VENDOR:
        m = re.search(rx, v)
        if m:
            return t, (int(m.group(1)) if rooms == "n" else rooms), kind
    return None, None, None


def placeholder_pictures(offers: list[ET.Element]) -> set[str]:
    """Картинки, которые стоят у многих объектов сразу, — заглушки вроде «Фото в пути»."""
    from collections import Counter
    c = Counter(p.text.strip() for o in offers for p in o.findall("picture") if p.text)
    return {url for url, n in c.items() if n >= 3}


def parse_offer(o: ET.Element, skip_pictures: set[str] | frozenset = frozenset()) -> dict | None:
    text = _text(o.findtext("description"))
    if not text:
        return None
    params = {p.get("name"): (p.text or "").strip() for p in o.findall("param")}
    po = parser._build(None, text, text, None)
    otype, rooms, kind = _vendor(o.findtext("vendor"))
    otype = otype or po.type or "flat"
    if otype in ("flat", "new", "room"):
        if rooms is None:
            rooms, kind = po.rooms, po.room_kind
    else:
        rooms = kind = None
    try:
        price = float(o.findtext("price") or 0)
    except ValueError:
        price = 0
    price = int(price * 1000) if 0 < price < 100_000 else int(price) or po.price  # в фиде — тысячи
    cx = params.get("Жилой комплекс") or ""
    cx = re.sub(r"(?i)^\s*(?:жк|ж/к)\s+", "", cx).strip()
    complex_name = (geo.resolve_complex(cx).name if geo.resolve_complex(cx) else geo.pretty_name(cx)) if cx else po.complex
    lat = lon = None
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", params.get("Координаты", ""))
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if 37 < lat < 41 and 43 < lon < 47:   # перепутаны местами долгота и широта
            lat, lon = lon, lat
        if geocode._km(geocode.CENTER, (lat, lon)) > geocode.MAX_KM:   # явно не Краснодар — ищем по адресу сами
            lat = lon = None
    d = po.to_dict()
    d.update(type=otype, rooms=rooms, room_kind=kind, price=price, complex=complex_name, deal="sale",
             article=(o.findtext("vendorCode") or "").strip() or rules.extract_article(text),
             description=parser.strip_phones(text), fragment=text, phones=[])
    if not d["district"] and complex_name:
        rec = geo.resolve_complex(complex_name)
        d["district"] = rec.district if rec else None
    d.update(ext_id=f"feed:{o.get('id')}", url=(o.findtext("url") or "").strip() or None,
             photos=[p.text.strip() for p in o.findall("picture")
                     if p.text and p.text.strip() not in skip_pictures][:12], lat=lat, lon=lon)
    d["title"] = make_title(d)
    return d if d["price"] else None


def sync(conn: sqlite3.Connection, xml_bytes: bytes | None = None, now: int | None = None) -> dict:
    """Загрузить фид и обновить объекты СТРЕЛ. Возвращает счётчики."""
    now = int(now or time.time())
    if xml_bytes is None:
        req = urllib.request.Request(config.FEED_URL, headers={"User-Agent": "1plus1-search/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml_bytes = resp.read()
    root = ET.fromstring(xml_bytes)
    offers = root.findall("./shop/offers/offer")
    seen: set[str] = set()
    added = updated = 0
    skip = placeholder_pictures(offers)
    for o in offers:
        d = parse_offer(o, skip)
        if not d:
            continue
        seen.add(d["ext_id"])
        d["search_text"] = make_search_text(d)
        row = conn.execute("SELECT id, price FROM listings WHERE ext_id = ?", (d["ext_id"],)).fetchone()
        vals = (d["type"], d["deal"], d["rooms"], d["area"], d["land"], d["floor"], d["floors"], d["price"],
                _price_m2(d["price"], d["area"]), d["district"], d["complex"], d["settlement"], d["street"],
                d["house"], d["title"], d["description"], d["fragment"], d["search_text"],
                json.dumps(d["photos"], ensure_ascii=False), d["url"], d["lat"], d["lon"],
                "ok" if d["lat"] else "pending", d["room_kind"], rooms_mask(d["room_kind"], d["rooms"]), d["article"])
        if row:
            lid = row["id"]
            conn.execute(
                """UPDATE listings SET type=?, deal=?, rooms=?, area=?, land=?, floor=?, floors=?, price=?, price_m2=?,
                       district=?, complex=?, settlement=?, street=?, house=?, title=?, description=?, fragment=?,
                       search_text=?, photos=?, url=?,
                       lat=CASE WHEN geo_status = 'manual' THEN lat ELSE ? END,
                       lon=CASE WHEN geo_status = 'manual' THEN lon ELSE ? END,
                       geo_status=CASE WHEN geo_status = 'manual' THEN 'manual' ELSE ? END, room_kind=?, rooms_mask=?,
                       article=?, is_active=1,
                       last_seen=CASE WHEN price_changed THEN ? ELSE last_seen END
                   WHERE id=?""".replace("price_changed", "1" if row["price"] != d["price"] else "0"),
                vals + (now, lid))
            updated += 1
        else:
            cur = conn.execute(
                """INSERT INTO listings (type, deal, rooms, area, land, floor, floors, price, price_m2, district,
                       complex, settlement, street, house, title, description, fragment, search_text, photos, url,
                       lat, lon, geo_status, room_kind, rooms_mask, article, phones, first_seen, last_seen,
                       seen_count, is_active, source, ext_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]',?,?,1,1,'feed',?)""",
                vals + (now, now, d["ext_id"]))
            lid = cur.lastrowid
            added += 1
        _reindex(conn, lid, d["search_text"])
    # Телефон партнёра — как у любого агента: виден только с подпиской
    if config.FEED_PHONE:
        conn.execute("UPDATE listings SET phones = ? WHERE source = 'feed'", (json.dumps([config.FEED_PHONE]),))
    # Нет в фиде — продано/снято: скрываем
    gone = 0
    for r in conn.execute("SELECT id, ext_id FROM listings WHERE source='feed' AND is_active=1").fetchall():
        if r["ext_id"] not in seen:
            conn.execute("UPDATE listings SET is_active=0 WHERE id=?", (r["id"],))
            gone += 1
    # Объявления из чатов с артикулом, который есть в фиде, — это объекты СТРЕЛ: копии скрываем
    dupes = conn.execute(
        """UPDATE listings SET is_active = 0 WHERE source = 'chat' AND is_active = 1 AND article IN
               (SELECT article FROM listings WHERE source = 'feed' AND is_active = 1 AND article IS NOT NULL)""").rowcount
    conn.commit()
    db.set_state(conn, "feed_synced", str(now))
    return {"offers": len(offers), "added": added, "updated": updated, "hidden": gone, "chat_copies_hidden": dupes}


def maybe_sync(conn: sqlite3.Connection) -> dict | None:
    """Раз в FEED_HOURS часов (вызывается из worker)."""
    if not config.FEED_URL:
        return None
    last = int(db.get_state(conn, "feed_synced") or 0)
    if time.time() - last < config.FEED_HOURS * 3600:
        return None
    try:
        return sync(conn)
    except Exception as e:  # noqa: BLE001 — сбой фида не должен останавливать разбор чатов
        log.exception("Фид СТРЕЛ не загрузился")
        db.set_state(conn, "feed_synced", str(int(time.time()) - config.FEED_HOURS * 3600 + 1800))
        return {"error": str(e)}

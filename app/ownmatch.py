"""Свои объекты СТРЕЛ — одна карточка (18.10, Артём: «мой номер 8 961 857-17-72: сайт (все объекты с фото) + рассылка
одиночного объекта с фото + рассылка портянки → появляются дубли»).

Главная карточка — объект с сайта (фид, все фото). Объявление из чатов с нашим номером (FEED_PHONE / ALWAYS_VERIFIED):
  * с «Артикул: N» — и раньше приклеивалось к объекту фида (ingest, match 'strely');
  * БЕЗ артикула (портянки, посты без артикула) — теперь сверяется с объектами фида по признакам и, если совпало
    однозначно, тоже приклеивается к карточке с сайта (история повторов — в ней, отдельной карточки нет).
  * Своих объектов, которых нет на сайте, несколько карточек (одиночный пост + строка из портянки) — объединяем в одну
    (остаётся та, где больше фото и данных).
Совпадение — строгое: тип и сделка, комнаты, площадь ±1,5 м² (обязательно у обоих), этаж и этажность (если указаны у обоих),
улица+дом или ЖК (если указаны у обоих — должны совпасть), цена ±15 %; два одинаково подходящих объекта — не трогаем.
Общая склейка объявлений других агентов (dedupe, P3) не меняется.

Решение Артёма (18.10): «всё, что отправляется с картинкой, уже есть в фиде». Значит, наш пост С ФОТО (одиночный объект)
отдельной карточкой не бывает никогда: строгое совпадение не нашлось — ищем мягче (комнаты, площадь ±3 м², цена ±20 %,
адрес — только если указан у обоих); не нашли и так — всё равно скрываем (hidden_reason 'strely_photo') и показываем админу
списком «без пары на сайте». Портянка (несколько объектов в сообщении, без фото) — только строгое совпадение.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import config, db, geo

TYPE_GROUP = {"new": "flat", "room": "flat"}
_REFS = ("favorites", "notes", "hidden_listings", "client_items", "client_hidden", "collection_items",
         "planner_events", "work_notes")


def own_phones() -> set[str]:
    return set(config.ALWAYS_VERIFIED) | ({config.FEED_PHONE} if config.FEED_PHONE else set())


def is_own(phones, roles=None) -> bool:
    """Объявление СТРЕЛ — номер СТРЕЛ в тексте объявления (phones; если в тексте номера нет, там номер приславшего).
    24.10l (Артём: «объекты сняты, а присылались сегодня»): раньше считался и номер ТОГО, КТО ПЕРЕСЛАЛ (phone_roles
    'sender') — сотрудник СТРЕЛ пересылал чужой объект с фото, его не находили на сайте агентства и скрывали ('strely_photo')."""
    ps = set(json.loads(phones) if isinstance(phones, str) else (phones or []))
    return bool(ps & own_phones())


def _street(s) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\b(ул|улица|пр|просп|проспект|пер|переулок|б-р|бульвар|ш|шоссе|проезд)\b\.?", " ", s)
    return re.sub(r"[^а-яa-z0-9]", "", s)


def _house(h) -> str:
    return re.sub(r"[\s\-]", "", (h or "").lower().replace("корпус", "к").replace("корп", "к").replace("литер", "").replace("лит", ""))


def score(a, b) -> int | None:
    """Насколько a (из чата) похож на b. None — точно разные объекты."""
    if TYPE_GROUP.get(a["type"], a["type"]) != TYPE_GROUP.get(b["type"], b["type"]) or (a["deal"] or "sale") != (b["deal"] or "sale"):
        return None
    s = 0
    if a["rooms"] is not None and b["rooms"] is not None:
        if a["rooms"] != b["rooms"]:
            return None
        s += 1
    if a["type"] in ("land",) or b["type"] in ("land",):
        if a["land"] and b["land"]:
            if abs(a["land"] - b["land"]) > 0.5:
                return None
            s += 3
        else:
            return None
    else:
        if not (a["area"] and b["area"]) or abs(a["area"] - b["area"]) > 1.5:
            return None
        s += 3
    for f in ("floor", "floors"):
        if a[f] and b[f]:
            if a[f] != b[f]:
                return None
            s += 1
    addr = False
    if a["street"] and b["street"]:
        if _street(a["street"]) != _street(b["street"]):
            return None
        s += 2
        addr = True
        if a["house"] and b["house"]:
            if _house(a["house"]) != _house(b["house"]):
                return None
            s += 2
    if a["complex"] and b["complex"]:
        if (geo.complex_key(a["complex"]) or a["complex"]) != (geo.complex_key(b["complex"]) or b["complex"]):
            return None
        s += 2
        addr = True
    if a["price"] and b["price"]:
        d = abs(a["price"] - b["price"]) / max(a["price"], b["price"])
        if d > 0.15:
            return None
        s += 2 if d <= 0.03 else 1
    # без адреса/ЖК у кого-то из двух — нужно больше совпадений (этаж и почти та же цена)
    if not addr and s < 7:
        return None
    return s


def loose(a, b) -> int | None:
    """Мягкое сравнение — только для наших постов с фото (они точно есть на сайте)."""
    if TYPE_GROUP.get(a["type"], a["type"]) != TYPE_GROUP.get(b["type"], b["type"]) or (a["deal"] or "sale") != (b["deal"] or "sale"):
        return None
    s = 0
    if a["rooms"] is not None and b["rooms"] is not None:
        if a["rooms"] != b["rooms"]:
            return None
        s += 1
    big = "land" if a["type"] == "land" else "area"
    if a[big] and b[big]:
        if abs(a[big] - b[big]) > (1 if big == "land" else 3):
            return None
        s += 3
    if a["price"] and b["price"]:
        if abs(a["price"] - b["price"]) / max(a["price"], b["price"]) > 0.2:
            return None
        s += 2
    if a["street"] and b["street"]:
        if _street(a["street"]) != _street(b["street"]):
            return None
        s += 2
        if a["house"] and b["house"] and _house(a["house"]) != _house(b["house"]):
            return None
    if a["complex"] and b["complex"] and (geo.complex_key(a["complex"]) or a["complex"]) != (geo.complex_key(b["complex"]) or b["complex"]):
        return None
    for f in ("floor", "floors"):
        if a[f] and b[f] and a[f] != b[f]:
            return None
    return s if s >= 4 else None


def photo_post(conn: sqlite3.Connection, row) -> bool:
    """Объявление пришло постом С ФОТО (одиночный объект): фото с подписью, картинка рядом с текстом того же агента в том
    же чате (±3 мин) или фото уже привязано. Строки портянки (в сообщении несколько объектов) — не считаем."""
    if row["source"] == "chat" and json.loads(row["photos"] or "[]"):
        return True
    # 18.10: «пост с фото» — только фото с подписью (то же сообщение), как и привязка фото (media._listing_for);
    # поиск по UNIQUE (source, msg_id) у chat_media — по индексу (раньше «±3 мин» с OR перебирало всю таблицу)
    for m in conn.execute("""SELECT m.id, m.source, m.msg_id FROM listing_events e
                             JOIN messages m ON m.id = e.message_id WHERE e.listing_id = ?""", (row["id"],)).fetchall():
        if not conn.execute("SELECT 1 FROM chat_media WHERE source = ? AND msg_id = ? LIMIT 1", (m["source"], m["msg_id"])).fetchone():
            continue
        if conn.execute("SELECT COUNT(DISTINCT listing_id) FROM listing_events WHERE message_id = ?", (m["id"],)).fetchone()[0] > 1:
            continue   # портянка
        return True
    return False


def best(cands: list, a, fn=None) -> int | None:
    """id однозначно лучшего кандидата (или None)."""
    scored = sorted((((fn or score)(a, c), c["id"]) for c in cands), key=lambda x: -(x[0] or -1))
    scored = [x for x in scored if x[0] is not None]
    if not scored or (len(scored) > 1 and scored[0][0] == scored[1][0]):
        return None
    return scored[0][1]


def feed_match(conn: sqlite3.Connection, d: dict, with_photo: bool = False) -> int | None:
    """Новое объявление из чата с нашим номером без артикула → id объекта фида или None (ingest).
    with_photo — сообщение само с картинкой (фото с подписью): строго не нашли — ищем мягче."""
    if not is_own(d.get("phones"), d.get("phone_roles")):
        return None
    cands = conn.execute("SELECT * FROM listings WHERE source = 'feed' AND is_active = 1").fetchall()
    return best(cands, d) or (best(cands, d, loose) if with_photo else None)


def _move(conn: sqlite3.Connection, src: int, dst: int) -> None:
    """Всё, что ссылалось на копию (избранное, клиенты, подборки, планер, журнал, история повторов), — на главную карточку."""
    conn.execute("UPDATE listings SET merged_into = ? WHERE id = ?", (dst, src))   # 24.10l: окно копии открывает главную
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for t in _REFS:
        if t in tables:
            conn.execute(f"UPDATE OR IGNORE {t} SET listing_id = ? WHERE listing_id = ?", (dst, src))
            conn.execute(f"DELETE FROM {t} WHERE listing_id = ?", (src,))
    conn.execute("UPDATE listing_events SET listing_id = ? WHERE listing_id = ?", (dst, src))
    conn.execute("UPDATE listings SET seen_count = seen_count + (SELECT seen_count FROM listings WHERE id = ?), "
                 "last_seen = MAX(last_seen, (SELECT last_seen FROM listings WHERE id = ?)) WHERE id = ?", (src, src, dst))


def unhide_forwarded(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """24.10l: один раз (state own_fix_v2) — вернуть объявления, скрытые как «свои» только из-за номера того, КТО ПЕРЕСЛАЛ.
    Свежие (присылали за STALE_DAYS) — снова на сайте; старые — в обычный архив (повтор вернёт)."""
    now = int(now or time.time())
    if db.get_state(conn, "own_fix_v2"):
        return {}
    from . import config
    back, arch, ex = 0, 0, []
    for r in conn.execute("""SELECT id, title, phones, last_seen, hidden_reason FROM listings WHERE source = 'chat'
                             AND hidden_reason IN ('strely', 'strely_photo', 'own_dup')""").fetchall():
        # «пост с фото без пары» больше не скрываем вовсе; копии и дубли — только если объект на самом деле не СТРЕЛ
        if r["hidden_reason"] != "strely_photo" and is_own(r["phones"]):
            continue
        fresh = (r["last_seen"] or 0) >= now - config.STALE_DAYS * 86400
        conn.execute("UPDATE listings SET hidden_reason = NULL, merged_into = NULL, is_active = ? WHERE id = ?", (1 if fresh else 0, r["id"]))
        back += fresh
        arch += not fresh
        if len(ex) < 30:
            ex.append({"id": r["id"], "title": r["title"], "back": bool(fresh)})
    rep = {"returned": back, "archived": arch, "examples": ex, "ts": now}
    db.set_state(conn, "own_fix_report", json.dumps(rep, ensure_ascii=False))
    db.set_state(conn, "own_fix_v2", "1")
    conn.commit()
    return rep


def run(conn: sqlite3.Connection, now: int | None = None) -> dict:
    """Свои копии из чатов → к карточке с сайта; свои дубли из чатов между собой → одна карточка."""
    now = int(now or time.time())
    own = [r for r in conn.execute("""SELECT * FROM listings WHERE source = 'chat' AND is_active = 1
                                      AND COALESCE(owner_user_id, 0) = 0""").fetchall() if is_own(r["phones"], r["phone_roles"])]
    feed = conn.execute("SELECT * FROM listings WHERE source = 'feed' AND is_active = 1").fetchall()
    to_feed, merged, nopair, ex = 0, 0, 0, []
    # 18.10: и старые копии, уже ушедшие в архив (были «Снятые» в «Мои объекты»), — привязать к карточке с сайта
    # (в архиве и останутся, но с пометкой 'strely': не показываются как отдельный объект и не вернутся дублем)
    # 18.10 (нагрузка): архив проверяем не каждую минуту, а раз в 6 часов — он меняется редко, а сравнение со всем фидом дорогое
    arch = [] if now - int(db.get_state(conn, "own_match_arch_ts") or 0) < 6 * 3600 else conn.execute(
        """SELECT * FROM listings WHERE source = 'chat' AND is_active = 0 AND hidden_reason IS NULL
           AND COALESCE(owner_user_id, 0) = 0""").fetchall()
    if arch or not db.get_state(conn, "own_match_arch_ts"):
        db.set_state(conn, "own_match_arch_ts", str(now))
    for a in arch:
        if not is_own(a["phones"], a["phone_roles"]):
            continue
        fid = best(feed, a) or (best(feed, a, loose) if photo_post(conn, a) else None)
        if fid:
            _move(conn, a["id"], fid)
            conn.execute("UPDATE listings SET hidden_reason = 'strely' WHERE id = ?", (a["id"],))
            to_feed += 1
    # 18.10 (нагрузка: «уборка/свои объекты» — 103 с процессора за цикл): каждую минуту — только свои объявления, пришедшие
    # с прошлой проверки; все сразу — раз в 6 часов (фид меняется редко) и при первом запуске
    since = int(db.get_state(conn, "own_match_since") or 0)
    full = not since or now - int(db.get_state(conn, "own_match_full_ts") or 0) >= 6 * 3600
    if full:
        db.set_state(conn, "own_match_full_ts", str(now))
    db.set_state(conn, "own_match_since", str(now))
    check = own if full else [r for r in own if max(r["last_seen"] or 0, r["first_seen"] or 0) >= since - 300]
    rest, done = [], set()
    for a in check:
        pp = photo_post(conn, a)
        fid = best(feed, a) or (best(feed, a, loose) if pp else None)
        if not fid and pp:   # 24.10l (Артём: «снимать строго — продано/задаток, 15 дней, сам или админ»): пару на сайте
            # не нашли — объект ОСТАЁТСЯ на сайте, админу — списком на проверку (раньше скрывали «strely_photo»)
            nopair += 1
            if len(ex) < 30:
                ex.append({"id": a["id"], "to": None, "title": a["title"], "why": "пост с фото — пары на сайте не нашли, оставлен на сайте"})
        if fid:
            _move(conn, a["id"], fid)
            conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'strely' WHERE id = ?", (a["id"],))
            done.add(a["id"])
            to_feed += 1
            if len(ex) < 30:
                ex.append({"id": a["id"], "to": fid, "title": a["title"], "why": "сайт"})
        else:
            rest.append(a)
    # свои объекты без пары на сайте: одиночный пост + строка из портянки → одна карточка (больше фото и данных).
    # Проверенные сейчас сравниваем со всеми своими (не только между собой); остаётся карточка «богаче» (фото, адрес, свежесть)
    rank = lambda r: (len(json.loads(r["photos"] or "[]")), sum(1 for f in ("street", "house", "complex", "floor", "area") if r[f]), r["last_seen"])  # noqa: E731
    pool = [r for r in own if r["id"] not in done]
    gone: set[int] = set()
    for a in sorted(rest, key=rank, reverse=True):
        if a["id"] in gone:
            continue
        for b in pool:
            if b["id"] == a["id"] or b["id"] in gone or a["id"] in gone:
                continue
            sc = score(b, a)
            if sc is not None and sc >= 6:
                keep, drop = (a, b) if rank(a) >= rank(b) else (b, a)
                _move(conn, drop["id"], keep["id"])
                conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'own_dup' WHERE id = ?", (drop["id"],))
                gone.add(drop["id"])
                merged += 1
                if len(ex) < 30:
                    ex.append({"id": drop["id"], "to": keep["id"], "title": drop["title"], "why": "дубль из чатов"})
    conn.commit()
    if to_feed or merged or nopair:
        prev = json.loads(db.get_state(conn, "own_match_report") or "{}")
        rep = {"to_feed": (prev.get("to_feed") or 0) + to_feed, "merged": (prev.get("merged") or 0) + merged,
               "nopair": (prev.get("nopair") or 0) + nopair, "ts": now, "examples": (ex + (prev.get("examples") or []))[:30]}
        db.set_state(conn, "own_match_report", json.dumps(rep, ensure_ascii=False))
    return {"to_feed": to_feed, "merged": merged, "nopair": nopair}


def maybe_run(conn: sqlite3.Connection) -> dict | None:
    """Раз в 10 минут (worker): новые копии и дубли своих объектов — убрать."""
    last = int(db.get_state(conn, "own_match_ts") or 0)
    if time.time() - last < 60:   # раз в минуту: фото приходит чуть позже текста — копия не висит на сайте
        return None
    db.set_state(conn, "own_match_ts", str(int(time.time())))
    unhide_forwarded(conn)   # один раз: вернуть скрытые только из-за номера переславшего
    return run(conn)

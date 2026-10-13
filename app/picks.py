"""Подборки для клиента: агент собирает объекты и отправляет клиенту одну красивую ссылку /c/<код>.

Клиент видит фото, цены, параметры, адрес, карту и ТОЛЬКО контакт агента, который сделал подборку:
ни телефонов других агентов, ни исходных сообщений, ни ссылок на чаты. Создавать подборки — с доступом (подписка).
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time

from . import config, parser, rules, settings, worklog

MAX_COLLECTIONS = 200
MAX_ITEMS = 60


def _phone(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10:
        return "+7" + d
    return (v or "").strip()[:30]


def _default_contact(conn: sqlite3.Connection, user) -> tuple[str, str]:
    """Контакт по умолчанию: из настроек аккаунта, иначе имя из кабинета и подтверждённый номер агента."""
    from . import settings
    st = settings.get(conn, user["id"])
    ph = conn.execute("SELECT phone FROM agent_phones WHERE user_id = ? ORDER BY verified_at DESC LIMIT 1",
                      (user["id"],)).fetchone()
    return (st["pick_name"] or user["name"] or "").strip(), (st["pick_phone"] or (ph[0] if ph else ""))


def mine(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = c.id) AS n,
                                    k.name AS client_name
                             FROM collections c LEFT JOIN clients k ON k.id = c.client_id AND k.user_id = c.user_id
                             WHERE c.user_id = ? ORDER BY c.updated DESC""", (user_id,)):
        d = dict(r)
        d["url"] = f"{config.PICK_URL}/c/{r['token']}"   # ссылка для клиента (на нейтральном адресе, если он задан)
        d["color"] = r["color"] if r["color"] in settings.COLORS else settings.COLORS[r["id"] % len(settings.COLORS)]
        d["notes"] = {str(k): v[:5] for k, v in worklog.for_pick(conn, user_id, r["id"], r["client_id"]).items()}
        d["items"] = [dict(x) for x in conn.execute(
            """SELECT i.listing_id, i.note, i.text, i.pos, l.title, l.price, l.deal, l.is_active, l.photos,
                      l.description AS orig, l.type, l.rooms, l.area, l.land, l.floor, l.floors, l.complex, l.district,
                      l.street, l.house
               FROM collection_items i JOIN listings l ON l.id = i.listing_id
               WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (r["id"],))]
        pairs = {x["listing_id"]: dict(x) for x in conn.execute(
            "SELECT listing_id, status, reaction, reaction_text FROM client_items WHERE client_id = ?", (r["client_id"],))} \
            if r["client_id"] else {}
        for it in d["items"]:
            photos = json.loads(it.pop("photos") or "[]")
            it["photo"] = photos[0] if photos else None
            it["orig"] = parser.client_text(it["orig"])   # стартовый текст для клиента — уже очищенный
            pr = pairs.get(it["listing_id"]) or {}
            it["status"], it["reaction"], it["reaction_text"] = pr.get("status"), pr.get("reaction"), pr.get("reaction_text")
        out.append(d)
    return out


def save(conn: sqlite3.Connection, user, body: dict) -> dict:
    title = str(body.get("title") or "").strip()[:120] or "Подборка для вас"
    note = str(body.get("note") or "").strip()[:2000]
    now = int(time.time())
    cid = body.get("id")
    if cid:
        if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (int(cid), user["id"])).fetchone():
            return {"error": "Подборка не найдена."}
        # меняем только присланные поля: смена клиента или цвета не должна стирать сообщение и контакт
        fields = {"updated": now}
        if "title" in body:
            fields["title"] = title
        if "note" in body:
            fields["note"] = note
        if "contact_name" in body:
            fields["contact_name"] = str(body.get("contact_name") or "").strip()[:100]
        if "contact_phone" in body:
            fields["contact_phone"] = _phone(str(body.get("contact_phone") or ""))
        if "client_title" in body:
            fields["client_title"] = str(body.get("client_title") or "").strip()[:120] or None
        conn.execute(f"UPDATE collections SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND user_id = ?",
                     (*fields.values(), int(cid), user["id"]))
    else:
        if conn.execute("SELECT COUNT(*) FROM collections WHERE user_id = ?", (user["id"],)).fetchone()[0] >= MAX_COLLECTIONS:
            return {"error": "Слишком много подборок — удалите старые."}
        name, phone = _default_contact(conn, user)
        cid = conn.execute("""INSERT INTO collections (user_id, token, title, note, contact_name, contact_phone, created, updated)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           (user["id"], secrets.token_urlsafe(9), title, note, body.get("contact_name") or name,
                            _phone(str(body.get("contact_phone") or phone)), now, now)).lastrowid
    if body.get("listing_id"):
        set_item(conn, user["id"], int(cid), int(body["listing_id"]), True, commit=False)
    if "color" in body:
        c = str(body.get("color") or "")
        conn.execute("UPDATE collections SET color = ? WHERE id = ? AND user_id = ?",
                     (c if c in settings.COLORS else None, int(cid), user["id"]))
    if "client_id" in body:   # подборка для клиента из CRM (только своего); объекты подборки сразу попадают к клиенту
        k = body.get("client_id")
        ok = str(k).isdigit() and conn.execute("SELECT 1 FROM clients WHERE id = ? AND user_id = ?", (int(k), user["id"])).fetchone()
        conn.execute("UPDATE collections SET client_id = ? WHERE id = ? AND user_id = ?", (int(k) if ok else None, int(cid), user["id"]))
        if ok:
            _sync_client(conn, int(cid))
    if body.get("copy_of"):   # «Дублировать»: те же объекты, комментарии и тексты, новая ссылка
        src = conn.execute("SELECT id FROM collections WHERE id = ? AND user_id = ?", (int(body["copy_of"]), user["id"])).fetchone()
        if src:
            conn.execute("""INSERT OR IGNORE INTO collection_items (collection_id, listing_id, pos, note, text, added)
                            SELECT ?, listing_id, pos, note, text, ? FROM collection_items WHERE collection_id = ?""",
                         (int(cid), now, src[0]))
    conn.commit()
    return next(c for c in mine(conn, user["id"]) if c["id"] == int(cid))


def _sync_client(conn: sqlite3.Connection, cid: int, listing_id: int | None = None) -> None:
    """Объекты подборки клиента — у клиента со статусом «Отправлен» (вперёд только: «показ» не откатывается)."""
    from . import clients
    row = conn.execute("SELECT user_id, client_id FROM collections WHERE id = ?", (cid,)).fetchone()
    if not row or not row["client_id"]:
        return
    ids = [listing_id] if listing_id else [r[0] for r in conn.execute(
        "SELECT listing_id FROM collection_items WHERE collection_id = ?", (cid,))]
    for lid in ids:
        clients.set_pair(conn, row["user_id"], row["client_id"], lid, "sent", pick_id=cid, auto=True, commit=False)


def delete(conn: sqlite3.Connection, user_id: int, cid: int) -> None:
    """Удалить подборку: её статусы у клиента, реакции и записи журнала уходят вместе с ней (решение Артёма 13.10)."""
    from . import clients
    if conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        clients.forget_pick(conn, cid)
        conn.execute("DELETE FROM collection_items WHERE collection_id = ?", (cid,))
        conn.execute("DELETE FROM collection_views WHERE collection_id = ?", (cid,))
        conn.execute("DELETE FROM collections WHERE id = ? AND user_id = ?", (cid, user_id))
    conn.commit()


def set_status(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, status: str) -> dict:
    """Статус объекта в подборке клиента (понравился, не подходит, показ…) — это статус пары клиент+объект.
    Запись «Вася: не понравилось» попадает в журнал объекта (и уходит вместе с подборкой)."""
    from . import clients, worklog
    row = conn.execute("SELECT client_id FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone()
    if row is None:
        return {"error": "Подборка не найдена."}
    if not row["client_id"]:
        return {"error": "Сначала выберите клиента подборки — статус ставится для него."}
    if status not in clients.PAIR:
        return {"error": "Неизвестный статус."}
    res = clients.set_pair(conn, user_id, row["client_id"], listing_id, status, pick_id=cid, commit=False)
    if "error" in res:
        return res
    name = conn.execute("SELECT name FROM clients WHERE id = ?", (row["client_id"],)).fetchone()["name"]
    conn.execute("""INSERT INTO work_notes (user_id, listing_id, client_id, collection_id, kind, text, ts)
                    VALUES (?,?,?,?,?,?,?)""", (user_id, listing_id, row["client_id"], cid,
                                                "show" if status == "show" else "note",
                                                f"{name}: {clients.PAIR[status].lower()}", int(time.time())))
    conn.commit()
    return {"ok": True, "status": res["status"]}


def set_item(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, add: bool,
             note: str | None = None, commit: bool = True, text: str | None = None) -> dict:
    """text — свой текст объявления для клиента ('' — вернуть исходный); телефоны из него убираем:
    клиент видит только контакт агента, сделавшего подборку."""
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return {"error": "Подборка не найдена."}
    if add:
        if not conn.execute("SELECT 1 FROM listings WHERE id = ?", (listing_id,)).fetchone():
            return {"error": "Объект не найден."}
        n = conn.execute("SELECT COUNT(*) FROM collection_items WHERE collection_id = ?", (cid,)).fetchone()[0]
        if n >= MAX_ITEMS and not conn.execute("SELECT 1 FROM collection_items WHERE collection_id = ? AND listing_id = ?",
                                               (cid, listing_id)).fetchone():
            return {"error": f"В подборке не больше {MAX_ITEMS} объектов."}
        conn.execute("""INSERT INTO collection_items (collection_id, listing_id, pos, note, added) VALUES (?,?,?,?,?)
                        ON CONFLICT(collection_id, listing_id) DO UPDATE SET note = COALESCE(excluded.note, note)""",
                     (cid, listing_id, n, (note or "").strip()[:1000] if note is not None else None, int(time.time())))
        _sync_client(conn, cid, listing_id)
        if text is not None:
            clean = parser.strip_phones(str(text)).strip()[:5000]
            conn.execute("UPDATE collection_items SET text = ? WHERE collection_id = ? AND listing_id = ?",
                         (clean or None, cid, listing_id))
    else:   # убрали объект из подборки — его статусы и записи из этой подборки тоже уходят
        from . import clients
        clients.forget_pick(conn, cid, listing_id)
        conn.execute("DELETE FROM collection_items WHERE collection_id = ? AND listing_id = ?", (cid, listing_id))
    conn.execute("UPDATE collections SET updated = ? WHERE id = ?", (int(time.time()), cid))
    if commit:
        conn.commit()
    return {"ok": True}


def move(conn: sqlite3.Connection, user_id: int, cid: int, order: list[int]) -> dict:
    """Новый порядок объектов (список id)."""
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return {"error": "Подборка не найдена."}
    for pos, lid in enumerate(order[:MAX_ITEMS]):
        conn.execute("UPDATE collection_items SET pos = ? WHERE collection_id = ? AND listing_id = ?", (pos, cid, int(lid)))
    conn.commit()
    return {"ok": True}


def with_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[int]:
    """В каких подборках уже есть объект (для окна объекта)."""
    return [r[0] for r in conn.execute("""SELECT c.id FROM collections c JOIN collection_items i ON i.collection_id = c.id
                                           WHERE c.user_id = ? AND i.listing_id = ?""", (user_id, listing_id))]


def count_open(conn: sqlite3.Connection, cid: int, device: str) -> None:
    """Клиент открыл подборку: всего +1; уникально — одно устройство раз в день (МСК)."""
    from datetime import datetime, timedelta, timezone
    now = int(time.time())
    day = datetime.fromtimestamp(now, timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
    try:
        conn.execute("UPDATE collections SET views = views + 1, viewed_at = ? WHERE id = ?", (now, cid))
        first_today = conn.execute("INSERT OR IGNORE INTO collection_views (collection_id, day, device, ts) VALUES (?,?,?,?)",
                                   (cid, day, device[:64], now)).rowcount
        if first_today:
            conn.execute("UPDATE collections SET views_unique = views_unique + 1 WHERE id = ?", (cid,))
            _opened(conn, cid)
        conn.commit()
    except sqlite3.OperationalError:   # база занята — просмотр не засчитаем, страницу покажем
        conn.rollback()


def _opened(conn: sqlite3.Connection, cid: int) -> None:
    """Клиент открыл подборку: отправленные объекты → «Открыл»; агенту — уведомление (раз в день на устройство)."""
    from . import clients, notices
    c = conn.execute("""SELECT k.id, k.user_id, k.title, k.client_id, cl.name FROM collections k
                        LEFT JOIN clients cl ON cl.id = k.client_id WHERE k.id = ?""", (cid,)).fetchone()
    if c is None:
        return
    if c["client_id"]:
        for (lid,) in conn.execute("SELECT listing_id FROM client_items WHERE client_id = ? AND pick_id = ? AND status = 'sent'",
                                   (c["client_id"], cid)).fetchall():
            clients.set_pair(conn, c["user_id"], c["client_id"], lid, "opened", auto=True, commit=False)
    who = c["name"] or "Клиент"
    notices.add(conn, c["user_id"], "client", f"{who} открыл(а) подборку «{c['title']}»", "",
                url=f"/picks?open={cid}", dedup_s=3 * 3600)


REACT_LIMIT = 60   # реакций с одного устройства на подборку в сутки


def react(conn: sqlite3.Connection, token: str, listing_id: int, kind: str, text: str = "", phone: str = "",
          device: str = "") -> dict:
    """Ответ клиента на объект со страницы подборки (без регистрации): нравится / не подходит / хочу посмотреть / вопрос.
    Меняет статус пары у клиента, пишет в историю и журнал объекта, присылает агенту уведомление."""
    from . import clients, notices
    if kind not in clients.REACTIONS:
        return {"error": "Неизвестный ответ."}
    c = conn.execute("""SELECT k.id, k.user_id, k.title, k.client_id, cl.name FROM collections k
                        LEFT JOIN clients cl ON cl.id = k.client_id WHERE k.token = ?""", (token,)).fetchone()
    if c is None:
        return {"error": "Подборка не найдена."}
    lst = conn.execute("""SELECT l.id, l.title, l.complex FROM collection_items i JOIN listings l ON l.id = i.listing_id
                          WHERE i.collection_id = ? AND i.listing_id = ?""", (c["id"], listing_id)).fetchone()
    if lst is None:
        return {"error": "Объекта нет в подборке."}
    now = int(time.time())
    if conn.execute("SELECT COUNT(*) FROM pick_reactions WHERE collection_id = ? AND device = ? AND ts > ?",
                    (c["id"], device, now - 86400)).fetchone()[0] >= REACT_LIMIT:
        return {"error": "Слишком много ответов — попробуйте завтра."}
    text = str(text or "").strip()[:1000]
    phone = (rules.normalize_phone(phone) or "") if phone else ""
    if kind == "question" and not text:
        return {"error": "Напишите вопрос."}
    conn.execute("""INSERT INTO pick_reactions (collection_id, listing_id, kind, text, phone, device, ts)
                    VALUES (?,?,?,?,?,?,?)""", (c["id"], listing_id, kind, text or None, phone or None, device[:64], now))
    name = f"ЖК {lst['complex']}, {lst['title']}" if lst["complex"] else lst["title"]
    who = c["name"] or "Клиент"
    label = clients.REACTIONS[kind]
    if c["client_id"]:
        if kind in clients.REACTION_STATUS:
            clients.set_pair(conn, c["user_id"], c["client_id"], listing_id, clients.REACTION_STATUS[kind], pick_id=c["id"],
                             auto=True, reaction=kind, reaction_text=text or None, commit=False)
        else:
            conn.execute("UPDATE client_items SET reaction = ?, reaction_text = ?, updated = ? WHERE client_id = ? AND listing_id = ?",
                         (kind, text, now, c["client_id"], listing_id))
        conn.execute("INSERT INTO client_log (client_id, ts, kind, text) VALUES (?,?,?,?)",
                     (c["client_id"], now, "reaction", f"Ответ клиента — {label}: {name}" + (f". «{text}»" if text else "")
                      + (f" · тел. {phone}" if phone else "")))
    conn.execute("""INSERT INTO work_notes (user_id, listing_id, client_id, collection_id, kind, text, ts)
                    VALUES (?,?,?,?,?,?,?)""", (c["user_id"], listing_id, c["client_id"], c["id"], "note",
                                                f"{who}: {label.lower()}" + (f" — «{text}»" if text else ""), now))
    notices.add(conn, c["user_id"], "client", f"{who}: «{label}» — {name}"[:200],
                (f"«{text}»" if text else "") + (f" · тел. {phone}" if phone else ""),
                url=f"/picks?open={c['id']}", listing_id=listing_id, dedup_s=60)
    conn.commit()
    _mail_agent(conn, c["user_id"], f"{who}: «{label}» — {name}", f"{text}\n{phone}".strip())
    return {"ok": True, "kind": kind}


def _mail_agent(conn: sqlite3.Connection, user_id: int, subject: str, body: str) -> None:
    """Дубль важного агенту на почту — в фоне (страница клиента не ждёт почтовый сервер)."""
    import threading
    from . import mailer
    row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row["email"] or not mailer.available():
        return
    msg = f"{subject}\n{body}\n\nОткройте подборку: {config.SITE_URL}/picks"
    threading.Thread(target=mailer.send_text, args=(row["email"], f"1+1 · {subject}"[:150], msg), daemon=True).start()


def public(conn: sqlite3.Connection, token: str, count_view: bool = True, device: str = "") -> dict | None:
    """Для клиента: без телефонов агентов из объявлений, без исходного текста и чатов.
    count_view — засчитывать ли открытие (сервер решает: не агент, не вошедший пользователь, не робот-превью)."""
    c = conn.execute("SELECT * FROM collections WHERE token = ?", (token,)).fetchone()
    if c is None:
        return None
    if count_view:
        count_open(conn, c["id"], device or "unknown")
    items = []
    # ответ этого устройства по каждому объекту (последний) — клиент видит, что уже ответил
    mine_r = {r[0]: r[1] for r in conn.execute(
        """SELECT listing_id, kind FROM pick_reactions WHERE collection_id = ? AND device = ? ORDER BY ts""",
        (c["id"], device or "-"))} if device else {}
    for r in conn.execute("""SELECT l.*, i.note AS item_note, i.text AS item_text FROM collection_items i JOIN listings l ON l.id = i.listing_id
                             WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (c["id"],)):
        items.append({
            "id": r["id"], "title": r["title"], "type": r["type"], "deal": r["deal"], "price": r["price"],
            "price_m2": r["price_m2"], "rooms": r["rooms"], "room_kind": r["room_kind"], "area": r["area"], "land": r["land"],
            "floor": r["floor"], "floors": r["floors"], "district": r["district"], "complex": r["complex"],
            "settlement": r["settlement"], "street": r["street"], "house": r["house"],
            "lat": r["lat"] if r["geo_status"] != "approx" else None, "lon": r["lon"] if r["geo_status"] != "approx" else None,
            "photos": json.loads(r["photos"] or "[]"),
            "description": parser.guard_phones(r["item_text"] or parser.client_text(r["description"]))[:5000],
            "note": r["item_note"] or "", "is_active": r["is_active"],
            "reaction": mine_r.get(r["id"])})
    from . import settings
    # название подборки — для агента («Ирине — 2к ФМР»); клиент видит только свой заголовок (или нейтральный)
    return {"title": c["client_title"] or "Подобрали для вас", "note": c["note"],
            "contact_name": c["contact_name"], "contact_phone": c["contact_phone"],
            "updated": c["updated"], "items": items, "style": settings.public_pick_style(conn, c["user_id"])}

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

from . import config, parser, rules, search, settings, worklog

MAX_COLLECTIONS = 200
PROMO_NAME = "Коллега (по коду)"   # служебное имя аккаунта по промокоду — клиенту его не показываем никогда
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
    name = st["pick_name"] or (user["name"] if user["name"] != PROMO_NAME else "") or ""
    return name.strip(), (st["pick_phone"] or (ph[0] if ph else ""))


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
            """SELECT i.listing_id, i.note, i.text, i.pos, i.price AS my_price, i.hide_price, i.photos AS my_photos, i.photo_order, l.title, l.price, l.deal, l.is_active, l.photos,
                      l.description AS orig, l.type, l.rooms, l.area, l.land, l.floor, l.floors, l.complex, l.district,
                      l.street, l.house
               FROM collection_items i JOIN listings l ON l.id = i.listing_id
               WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (r["id"],))]
        pairs = {x["listing_id"]: dict(x) for x in conn.execute(
            "SELECT listing_id, status, reaction, reaction_text FROM client_items WHERE client_id = ?", (r["client_id"],))} \
            if r["client_id"] else {}
        for it in d["items"]:
            photos = json.loads(it.pop("photos") or "[]")
            it["my_photos"] = json.loads(it["my_photos"] or "[]")
            it["photos_all"], it["photos_hidden"] = ordered_photos(it["my_photos"], photos, it.pop("photo_order"))
            it["photo"] = (it["photos_all"] or [None])[0]
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
        if "hide_address" in body:
            fields["hide_address"] = 1 if body.get("hide_address") else 0
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
    if "cover" in body:   # 24.10c: своё фото обложки (картинка из браузера) или "" — снова градиент
        raw = str(body.get("cover") or "")
        url = None
        if raw:
            url, why = settings.save_cover(raw, "pk")
            if not url:
                return {"error": why}
        conn.execute("UPDATE collections SET cover = ?, updated = ? WHERE id = ? AND user_id = ?", (url, now, int(cid), user["id"]))
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


def for_client_name(conn: sqlite3.Connection, user, name: str) -> dict:
    """24.10 (Артём: «подборка связана с клиентом — без двойной работы»): новая подборка = имя клиента. Такой клиент уже
    есть — открываем его подборку (дубля нет); нет — создаём карточку клиента и его подборку разом."""
    from . import clients
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
    if not name:
        return {"error": "Напишите имя клиента — подборка будет для него."}
    low = name.lower().replace("ё", "е")
    found = next((r["id"] for r in conn.execute("SELECT id, name FROM clients WHERE user_id = ? AND archived = 0", (user["id"],))
                  if (r["name"] or "").strip().lower().replace("ё", "е") == low), None)
    made_client = False
    if found is None:
        res = clients.save(conn, user["id"], {"name": name, "kind": "buyer"}, user)
        if "error" in res:
            return res
        found = res["client"]["id"]
        made_client = True
    pid = clients.ensure_pick(conn, user, found)
    if pid is None:
        return {"error": "Не получилось создать подборку."}
    pick = next(c for c in mine(conn, user["id"]) if c["id"] == pid)
    return {**pick, "made_client": made_client}


def _sync_client(conn: sqlite3.Connection, cid: int, listing_id: int | None = None) -> None:
    """Объекты подборки клиента — у клиента со статусом «Отправлен» (вперёд только: «показ» не откатывается)."""
    from . import clients
    row = conn.execute("SELECT user_id, client_id FROM collections WHERE id = ?", (cid,)).fetchone()
    if not row or not row["client_id"]:
        return
    ids = [listing_id] if listing_id else [r[0] for r in conn.execute(
        "SELECT listing_id FROM collection_items WHERE collection_id = ?", (cid,))]
    for lid in ids:
        clients.set_pair(conn, row["user_id"], row["client_id"], lid, "added", pick_id=cid, auto=True, commit=False)


def mark_sent(conn: sqlite3.Connection, user_id: int, token: str) -> int:
    """24.10b: агент скопировал / отправил ссылку подборки — объекты «Добавлен» → «Отправлен» (только вперёд)."""
    from . import clients
    k = conn.execute("SELECT id, client_id FROM collections WHERE token = ? AND user_id = ?", (token, user_id)).fetchone()
    if not k or not k["client_id"]:
        return 0
    n = 0
    for (lid,) in conn.execute("SELECT listing_id FROM client_items WHERE client_id = ? AND pick_id = ? AND status = 'added'",
                               (k["client_id"], k["id"])).fetchall():
        clients.set_pair(conn, user_id, k["client_id"], lid, "sent", pick_id=k["id"], auto=True, commit=False)
        n += 1
    conn.commit()
    return n


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
             note: str | None = None, commit: bool = True, text: str | None = None,
             price: int | None = None, hide_price: bool | None = None) -> dict:
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
        if price is not None:   # 0 — вернуть цену из объявления
            conn.execute("UPDATE collection_items SET price = ? WHERE collection_id = ? AND listing_id = ?",
                         (price if 0 < price < 10_000_000_000 else None, cid, listing_id))
        if hide_price is not None:
            conn.execute("UPDATE collection_items SET hide_price = ? WHERE collection_id = ? AND listing_id = ?",
                         (1 if hide_price else 0, cid, listing_id))
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


MAX_OWN_PHOTOS = 10


def _no_license(conn: sqlite3.Connection, phones_json: str | None) -> bool:
    from . import consent
    nl = consent.prefs(conn)["no_license"]
    return bool(nl) and any(p in nl for p in json.loads(phones_json or "[]"))


def ordered_photos(mine: list[str], listing: list[str], po: str | None) -> tuple[list[str], list[str]]:
    """18.10 (Артём: «фото в подборке можно перетаскивать»): фото для клиента в порядке агента. Новые фото (добавили позже
    или пришли в объявлении) — в конце, свои агента — перед фото объявления; скрытые агентом — не показываем."""
    d = json.loads(po or "{}") if po else {}
    pool = list(dict.fromkeys(mine + listing))
    hidden = [u for u in d.get("hidden") or [] if u in pool]
    order = [u for u in d.get("order") or [] if u in pool and u not in hidden]
    rest = [u for u in pool if u not in order and u not in hidden]
    return order + rest, hidden


def item_photo(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, data_url: str | None = None,
               remove: str | None = None, order: list | None = None, hide: str | None = None,
               unhide: str | None = None) -> dict:
    """17.10 (Артём): свои фото агента к объекту в подборке — клиент видит их первыми, перед фото из объявления."""
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return {"error": "Подборка не найдена."}
    row = conn.execute("""SELECT i.photos, i.photo_order, l.photos AS lphotos FROM collection_items i JOIN listings l ON l.id = i.listing_id
                          WHERE i.collection_id = ? AND i.listing_id = ?""", (cid, listing_id)).fetchone()
    if row is None:
        return {"error": "Объекта нет в подборке."}
    photos = json.loads(row["photos"] or "[]")
    if order is not None or hide or unhide:   # порядок / скрыть / вернуть фото для клиента
        visible, hidden = ordered_photos(photos, json.loads(row["lphotos"] or "[]"), row["photo_order"])
        pool = set(visible) | set(hidden)
        if order is not None:
            visible = [u for u in dict.fromkeys(str(x) for x in order) if u in pool and u not in hidden] +                       [u for u in visible if u not in order]
        if hide and hide in pool:
            if len([u for u in visible if u != hide]) < 1:
                return {"error": "Оставьте клиенту хотя бы одно фото."}
            visible, hidden = [u for u in visible if u != hide], hidden + [hide]
        if unhide and unhide in hidden:
            hidden, visible = [u for u in hidden if u != unhide], visible + [unhide]
        conn.execute("UPDATE collection_items SET photo_order = ? WHERE collection_id = ? AND listing_id = ?",
                     (json.dumps({"order": visible, "hidden": hidden}), cid, listing_id))
        conn.execute("UPDATE collections SET updated = ? WHERE id = ?", (int(time.time()), cid))
        conn.commit()
        return {"ok": True, "photos": photos, "visible": visible, "hidden": hidden}
    if remove:
        photos = [x for x in photos if x != remove]
    elif data_url:
        if len(photos) >= MAX_OWN_PHOTOS:
            return {"error": f"Не больше {MAX_OWN_PHOTOS} своих фото к объекту."}
        url, msg = settings.save_cover(data_url, "pk")
        if not url:
            return {"error": msg}
        if url not in photos:
            photos.append(url)
    conn.execute("UPDATE collection_items SET photos = ? WHERE collection_id = ? AND listing_id = ?",
                 (json.dumps(photos) if photos else None, cid, listing_id))
    conn.execute("UPDATE collections SET updated = ? WHERE id = ?", (int(time.time()), cid))
    conn.commit()
    return {"ok": True, "photos": photos}


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
        for (lid,) in conn.execute("SELECT listing_id FROM client_items WHERE client_id = ? AND pick_id = ? AND status IN ('added', 'sent')",
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
        else:   # вопрос не перезаписывает ответ клиента («нравится» остаётся) — он в истории и в уведомлении
            conn.execute("UPDATE client_items SET question = ?, updated = ? WHERE client_id = ? AND listing_id = ?",
                         (text, now, c["client_id"], listing_id))
        conn.execute("INSERT INTO client_log (client_id, ts, kind, text) VALUES (?,?,?,?)",
                     (c["client_id"], now, "reaction", f"Ответ клиента — {label}: {name}" + (f". «{text}»" if text else "")
                      + (f" · тел. {phone}" if phone else "")))
    conn.execute("""INSERT INTO work_notes (user_id, listing_id, client_id, collection_id, kind, text, ts)
                    VALUES (?,?,?,?,?,?,?)""", (c["user_id"], listing_id, c["client_id"], c["id"], "note",
                                                f"{who}: {label.lower()}" + (f" — «{text}»" if text else ""), now))
    # уведомление ведёт в карточку клиента, с кнопками (акт 14.10, С5)
    card = f"/clients?id={c['client_id']}" if c["client_id"] else f"/picks?open={c['id']}"
    acts = [{"label": "Открыть клиента" if c["client_id"] else "Открыть подборку", "url": card},
            {"label": "Объект", "url": f"/?open={listing_id}"}]
    if kind == "want_show":
        acts.insert(1, {"label": "Запланировать показ", "url": f"/planner?add=show&listing={listing_id}"
                        + (f"&client={c['client_id']}" if c["client_id"] else "")})
    notices.add(conn, c["user_id"], "client", f"{who}: «{label}» — {name}"[:200],
                (f"«{text}»" if text else "") + (f" · тел. {phone}" if phone else ""),
                url=card, listing_id=listing_id, dedup_s=60, actions=acts)
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


def _coarse(v, hide: bool):
    """Скрыть точный адрес: точка округляется примерно до 300 м (клиент видит квартал, а не дом)."""
    return None if v is None else (round(v / 0.003) * 0.003 if hide else v)


def similar(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int) -> list[dict]:
    """Снятый объект в подборке → 3 похожих на сайте: та же сделка и комнаты, тот же ЖК или район, цена ±15 %."""
    from . import search
    if not conn.execute("SELECT 1 FROM collections WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone():
        return []
    o = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if o is None:
        return []
    params = [o["deal"], o["id"], cid]
    w = ["l.is_active = 1", "l.deal = ?", "l.id != ?",
         "NOT EXISTS (SELECT 1 FROM collection_items i WHERE i.collection_id = ? AND i.listing_id = l.id)"]
    if o["rooms"] is not None:
        w.append("l.rooms = ?"); params.append(o["rooms"])
    if o["complex"]:
        w.append("(l.complex = ? OR l.district = ?)"); params += [o["complex"], o["district"]]
    elif o["district"]:
        w.append("l.district = ?"); params.append(o["district"])
    if o["price"]:
        w.append("l.price BETWEEN ? AND ?"); params += [int(o["price"] * .85), int(o["price"] * 1.15)]
    rows = conn.execute(f"""SELECT l.*, 0 AS verified FROM listings l WHERE {' AND '.join(w)}
                            ORDER BY (l.complex = ?) DESC, ABS(COALESCE(l.price, 0) - ?) LIMIT 3""",
                        params + [o["complex"] or "", o["price"] or 0]).fetchall()
    return [search.row_to_item(r, False) for r in rows]


def replace(conn: sqlite3.Connection, user_id: int, cid: int, old_id: int, new_id: int) -> dict:
    """Заменить снятый объект похожим — на то же место в подборке."""
    pos = conn.execute("SELECT pos FROM collection_items WHERE collection_id = ? AND listing_id = ?", (cid, old_id)).fetchone()
    if pos is None:
        return {"error": "Объекта нет в подборке."}
    res = set_item(conn, user_id, cid, new_id, True, commit=False)
    if "error" in res:
        return res
    set_item(conn, user_id, cid, old_id, False, commit=False)
    conn.execute("UPDATE collection_items SET pos = ? WHERE collection_id = ? AND listing_id = ?", (pos[0], cid, new_id))
    conn.commit()
    return {"ok": True}


def public(conn: sqlite3.Connection, token: str, count_view: bool = True, device: str = "") -> dict | None:
    """Для клиента: без телефонов агентов из объявлений, без исходного текста и чатов.
    count_view — засчитывать ли открытие (сервер решает: не агент, не вошедший пользователь, не робот-превью)."""
    c = conn.execute("SELECT * FROM collections WHERE token = ?", (token,)).fetchone()
    if c is None:
        return None
    if count_view:
        count_open(conn, c["id"], device or "unknown")
    items = []
    hide = bool(c["hide_address"])
    # ответ этого устройства по каждому объекту (последний) — клиент видит, что уже ответил
    mine_r = {r[0]: r[1] for r in conn.execute(
        """SELECT listing_id, kind FROM pick_reactions WHERE collection_id = ? AND device = ? AND kind != 'question'
           ORDER BY ts""",
        (c["id"], device or "-"))} if device else {}
    for r in conn.execute("""SELECT l.*, i.note AS item_note, i.text AS item_text, i.price AS my_price, i.hide_price, i.photos AS my_photos,
                                    i.photo_order FROM collection_items i JOIN listings l ON l.id = i.listing_id
                             WHERE i.collection_id = ? ORDER BY i.pos, i.added""", (c["id"],)):
        items.append({
            "id": r["id"], "title": r["title"], "type": r["type"], "deal": r["deal"],
            # цена: своя цена агента для клиента, иначе из объявления; «скрыть цену» — клиент видит «цена по запросу»
            "price": None if r["hide_price"] else (r["my_price"] or r["price"]),
            "price_m2": None if r["hide_price"] else (round(r["my_price"] / r["area"]) if r["my_price"] and r["area"] else
                                                     None if r["my_price"] else r["price_m2"]),
            "price_hidden": bool(r["hide_price"]), "rooms": r["rooms"], "room_kind": r["room_kind"], "area": r["area"], "land": r["land"],
            "floor": r["floor"], "floors": r["floors"], "district": r["district"], "complex": r["complex"],
            "settlement": r["settlement"], "street": r["street"], "house": None if hide else r["house"],
            "lat": _coarse(r["lat"], hide) if search.geo_level(r["lat"], r["geo_status"], r["geo_src"]) == "house" else None,
            "lon": _coarse(r["lon"], hide) if search.geo_level(r["lat"], r["geo_status"], r["geo_src"]) == "house" else None,
            "approx": hide,
            # фото в порядке агента (перетаскиванием в /picks), скрытые — не показываем; свои агента — первыми.
            # 19.10: автор объявления не разрешил тексты и фото (/agent) — клиенту только свои фото агента подборки, без текста
            "photos": ordered_photos(json.loads(r["my_photos"] or "[]"), [] if _no_license(conn, r["phones"]) else json.loads(r["photos"] or "[]"),
                                     r["photo_order"])[0],
            "description": "" if not r["is_active"] or (not r["item_text"] and _no_license(conn, r["phones"])) else
                           parser.guard_phones(r["item_text"] or parser.client_text(r["description"]))[:5000],
            "note": r["item_note"] or "", "is_active": r["is_active"],
            "reaction": mine_r.get(r["id"])})
    from . import settings
    # название подборки — для агента («Ирине — 2к ФМР»); клиент видит только свой заголовок (или нейтральный)
    return {"title": c["client_title"] or "Подобрали для вас", "note": c["note"],
            "contact_name": "" if (c["contact_name"] or "") == PROMO_NAME else c["contact_name"], "contact_phone": c["contact_phone"],
            "updated": c["updated"], "items": items,
            "style": {**(st := settings.public_pick_style(conn, c["user_id"])),
                      "cover": (c["cover"] if "cover" in c.keys() else None) or st.get("cover") or ""}}

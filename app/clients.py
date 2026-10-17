"""CRM агента: клиенты (покупатели, продавцы, арендаторы, арендодатели) и сделки по этапам.

Клиент — центр (ТЗ v2): контакт, пожелания, у каждого своя подборка (создаётся вместе с клиентом), объекты со статусом
пары клиент+объект: подобран → отправлен (в подборке) → открыл → понравился / не подходит → показ → переговоры →
сделка / отказ. Любая смена статуса — одной функцией set_pair: запись в историю клиента, этап клиента двигается сам
(если агент не двигал его руками). Статусы, поставленные в подборке, уходят вместе с подборкой или объектом из неё.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import geo, rules

KINDS = {"buyer": "Покупатель", "seller": "Продавец", "tenant": "Арендатор", "landlord": "Арендодатель"}
STAGES = {"new": "Новый", "search": "Подбор", "shows": "Показы", "talks": "Переговоры", "deposit": "Задаток",
          "deal": "Сделка", "lost": "Отказ"}
PAIR = {"matched": "Подобран", "sent": "Отправлен", "opened": "Открыл", "liked": "Понравился", "rejected": "Не подходит",
        "show": "Показ", "talks": "Переговоры", "deal": "Сделка", "lost": "Отказ"}
ITEM_STATUS = PAIR   # прежнее имя (страницы, тесты)
RANK = {"matched": 0, "sent": 1, "opened": 2, "liked": 3, "rejected": 3, "show": 4, "talks": 5, "deal": 6, "lost": 6}
PICK_STATUSES = ("sent", "opened", "liked", "rejected")   # ставятся подборкой — уходят вместе с ней
OLD_STATUS = {"offered": "matched", "shown": "show", "refused": "rejected"}
REACTIONS = {"like": "Нравится", "no": "Не подходит", "want_show": "Хочет посмотреть", "question": "Вопрос"}
REACTION_STATUS = {"like": "liked", "no": "rejected", "want_show": "show"}   # акт 14.10, С5: «хочу посмотреть» = показ
WISH_TYPES = ("flat", "house", "land", "commercial")
WISH_COND = {"pcho": "ПЧО / предчистовая", "repair": "С ремонтом", "furniture": "С мебелью"}
WISH_PAY = {"cash": "Наличные", "mortgage": "Ипотека", "matcap": "Маткапитал", "cert": "Сертификат"}
MAX_CLIENTS = 2000


def _row(r: sqlite3.Row) -> dict:
    d = dict(r)
    for k in ("rooms", "districts"):
        d[k] = json.loads(d.get(k) or "[]")
    try:
        d["wishes"] = json.loads(d.get("wishes") or "{}")
    except (TypeError, ValueError):
        d["wishes"] = {}
    return d


def _wishes(raw) -> dict:
    """Расширенные пожелания — только известные поля и значения (всё участвует в подборе)."""
    w = raw if isinstance(raw, dict) else {}

    def num(v, lo=0, hi=100000):
        try:
            x = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return x if lo <= x <= hi else None
    out = {
        "types": [t for t in (w.get("types") or []) if t in WISH_TYPES],
        "area_min": num(w.get("area_min"), 1, 10000), "area_max": num(w.get("area_max"), 1, 10000),
        "not_first": bool(w.get("not_first")), "not_last": bool(w.get("not_last")),
        "complexes": [str(x).strip()[:80] for x in (w.get("complexes") or []) if str(x).strip()][:30],
        "cond": [c for c in (w.get("cond") or []) if c in WISH_COND],
        "no_encumbrance": bool(w.get("no_encumbrance")),
        "pay": [c for c in (w.get("pay") or []) if c in WISH_PAY],
        "buy_by": str(w.get("buy_by") or "").strip()[:40],
        "budget_tol": int(num(w.get("budget_tol"), 0, 30) or 0),   # допуск бюджета, %; по умолчанию 0 — строго
    }
    return {k: v for k, v in out.items() if v not in (None, [], "", False, 0)}


def _log(conn: sqlite3.Connection, cid: int, kind: str, text: str, ts: int | None = None) -> None:
    conn.execute("INSERT INTO client_log (client_id, ts, kind, text) VALUES (?,?,?,?)", (cid, ts or int(time.time()), kind, text[:2000]))


def _own(conn: sqlite3.Connection, user_id: int, cid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM clients WHERE id = ? AND user_id = ?", (cid, user_id)).fetchone()


def board(conn: sqlite3.Connection, user_id: int, q: str = "") -> dict:
    """Все клиенты (для доски и списка) + сколько объектов у каждого."""
    rows = conn.execute("""SELECT c.*, (SELECT COUNT(*) FROM client_items i WHERE i.client_id = c.id) AS n_items,
                                  (SELECT COUNT(*) FROM client_items i WHERE i.client_id = c.id AND i.status = 'liked') AS n_liked
                           FROM clients c WHERE c.user_id = ? ORDER BY c.archived, COALESCE(c.next_ts, 9e9), c.updated DESC""",
                        (user_id,)).fetchall()
    items = [_row(r) for r in rows]
    if q:
        ql = q.lower().replace("ё", "е")
        digits = re.sub(r"\D", "", q)
        items = [c for c in items if ql in (c["name"] or "").lower().replace("ё", "е") or
                 (digits and digits in re.sub(r"\D", "", c["phone"] or "")) or ql in (c["note"] or "").lower()]
    return {"items": items, "stages": STAGES, "kinds": KINDS, "item_status": ITEM_STATUS, "now": int(time.time())}


def _clean(body: dict) -> dict:
    kind = body.get("kind") if body.get("kind") in KINDS else "buyer"
    stage = body.get("stage") if body.get("stage") in STAGES else "new"
    deal = "rent" if body.get("deal") == "rent" or kind in ("tenant", "landlord") else "sale"

    def money(v):
        s = re.sub(r"[^\d,.]", "", str(v or "")).replace(",", ".")
        try:
            x = float(s)
        except ValueError:
            return None
        if deal == "sale":
            x = x * 1e6 if x < 1000 else x
        else:
            x = x * 1000 if x < 1000 else x
        return int(x) if x > 0 else None
    rooms = sorted({int(x) for x in (body.get("rooms") or []) if str(x).isdigit() and 0 <= int(x) <= 5})
    districts = []
    for d in body.get("districts") or []:
        cd = geo.canonical_district(str(d))
        if cd and cd not in districts:
            districts.append(cd)
    try:
        next_ts = int(body.get("next_ts")) if body.get("next_ts") else None
    except (TypeError, ValueError):
        next_ts = None
    phone = rules.normalize_phone(str(body.get("phone") or "")) or str(body.get("phone") or "").strip()[:30] or None
    return {"name": str(body.get("name") or "").strip()[:120], "phone": phone, "kind": kind, "stage": stage, "deal": deal,
            "budget_min": money(body.get("budget_min")), "budget_max": money(body.get("budget_max")),
            "rooms": json.dumps(rooms), "districts": json.dumps(districts, ensure_ascii=False),
            "note": str(body.get("note") or "").strip()[:4000], "next_ts": next_ts,
            "next_text": str(body.get("next_text") or "").strip()[:200], "source": str(body.get("source") or "").strip()[:60],
            "archived": 1 if body.get("archived") else 0,
            "wishes": json.dumps(_wishes(body.get("wishes")), ensure_ascii=False)}


def save(conn: sqlite3.Connection, user_id: int, body: dict, user=None) -> dict:
    v = _clean(body)
    if not v["name"] and not v["phone"]:
        return {"error": "Укажите имя или телефон клиента."}
    v["name"] = v["name"] or v["phone"]
    now = int(time.time())
    cid = body.get("id")
    if cid:
        old = _own(conn, user_id, int(cid))
        if old is None:
            return {"error": "Клиент не найден."}
        conn.execute("""UPDATE clients SET name=:name, phone=:phone, kind=:kind, stage=:stage, deal=:deal, budget_min=:budget_min,
                        budget_max=:budget_max, rooms=:rooms, districts=:districts, note=:note, next_ts=:next_ts,
                        next_text=:next_text, source=:source, archived=:archived, wishes=:wishes, updated=:now,
                        stage_manual = CASE WHEN stage != :stage THEN 1 ELSE stage_manual END,
                        stage_ts = CASE WHEN stage != :stage THEN :now ELSE stage_ts END
                        WHERE id=:id AND user_id=:uid""", {**v, "now": now, "id": int(cid), "uid": user_id})
        if old["stage"] != v["stage"]:
            _log(conn, int(cid), "stage", f"Этап: {STAGES[old['stage']]} → {STAGES[v['stage']]}")
            if v["stage"] == "deal":
                after_deal(conn, user_id, int(cid), v["name"], now)
        if v["next_ts"] and (old["next_ts"] != v["next_ts"] or old["next_text"] != v["next_text"]):
            _log(conn, int(cid), "next", f"Следующий шаг: {v['next_text'] or 'связаться'}")
    else:
        if conn.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (user_id,)).fetchone()[0] >= MAX_CLIENTS:
            return {"error": "Слишком много клиентов — отправьте старых в архив и удалите."}
        cid = conn.execute("""INSERT INTO clients (user_id, name, phone, kind, stage, deal, budget_min, budget_max, rooms, districts,
                              note, next_ts, next_text, source, archived, wishes, created, updated, stage_ts)
                              VALUES (:uid, :name, :phone, :kind, :stage, :deal, :budget_min, :budget_max, :rooms, :districts,
                              :note, :next_ts, :next_text, :source, :archived, :wishes, :now, :now, :now)""",
                           {**v, "uid": user_id, "now": now}).lastrowid
        _log(conn, cid, "new", f"Новый клиент: {KINDS[v['kind']].lower()}")
        conn.commit()
        if user is not None:   # решение Артёма 13.10: у клиента сразу своя подборка
            ensure_pick(conn, user, int(cid))
    conn.commit()
    return detail(conn, user_id, int(cid))


AFTER_DEAL = ((30, "call", "Попросить отзыв и рекомендацию"), (365, "call", "Год со сделки — поздравить"))


def after_deal(conn: sqlite3.Connection, user_id: int, cid: int, name: str, now: int | None = None) -> int:
    """17.10: клиент на этапе «Сделка» — работа не кончилась: через месяц попросить отзыв и рекомендацию,
    через год — поздравить. События в планере (в 11:00 по Краснодару), повторно не создаются."""
    now = int(now or time.time())
    made = 0
    for days, kind, text in AFTER_DEAL:
        title = f"{text} — {name}"[:200]
        if conn.execute("SELECT 1 FROM planner_events WHERE user_id = ? AND client_id = ? AND title = ?",
                        (user_id, cid, title)).fetchone():
            continue
        day = (now + 3 * 3600) // 86400 + days       # сутки по UTC+3
        ts = day * 86400 + 8 * 3600                    # 11:00 по Москве/Краснодару
        conn.execute("""INSERT INTO planner_events (user_id, kind, title, ts, dur_min, client_id, remind_min, created, updated)
                        VALUES (?,?,?,?,?,?,?,?,?)""", (user_id, kind, title, ts, 15, cid, 60, now, now))
        made += 1
    if made:
        _log(conn, cid, "next", "В планере: через месяц — попросить отзыв, через год — поздравить")
    return made


def set_stage(conn: sqlite3.Connection, user_id: int, cid: int, stage: str) -> dict:
    old = _own(conn, user_id, cid)
    if old is None or stage not in STAGES:
        return {"error": "Клиент не найден."}
    if old["stage"] != stage:
        now = int(time.time())
        conn.execute("UPDATE clients SET stage = ?, stage_ts = ?, updated = ?, stage_manual = 1 WHERE id = ?", (stage, now, now, cid))
        _log(conn, cid, "stage", f"Этап: {STAGES[old['stage']]} → {STAGES[stage]}")
        if stage == "deal":
            after_deal(conn, user_id, cid, old["name"], now)
        conn.commit()
    return {"ok": True}


def delete(conn: sqlite3.Connection, user_id: int, cid: int) -> None:
    if _own(conn, user_id, cid):
        conn.execute("DELETE FROM client_items WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM client_hidden WHERE client_id = ?", (cid,))
        conn.execute("UPDATE collections SET client_id = NULL WHERE client_id = ?", (cid,))
        conn.execute("UPDATE work_notes SET client_id = NULL WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM client_log WHERE client_id = ?", (cid,))
        conn.execute("UPDATE planner_events SET client_id = NULL WHERE client_id = ?", (cid,))
        conn.execute("DELETE FROM clients WHERE id = ?", (cid,))
        conn.commit()


def detail(conn: sqlite3.Connection, user_id: int, cid: int) -> dict:
    c = _own(conn, user_id, cid)
    if c is None:
        return {"error": "Клиент не найден."}
    items = []
    for r in conn.execute("""SELECT i.status, i.note, i.ts, i.pick_id, i.sent_at, i.opened_at, i.reaction, i.reaction_text,
                                    COALESCE(i.updated, i.ts) AS updated,
                                    l.id, l.title, l.price, l.deal, l.complex, l.district, l.street, l.house, l.photos, l.is_active
                             FROM client_items i JOIN listings l ON l.id = i.listing_id WHERE i.client_id = ?
                             ORDER BY COALESCE(i.updated, i.ts) DESC""", (cid,)):
        photos = json.loads(r["photos"] or "[]")
        items.append({k: r[k] for k in ("id", "title", "price", "deal", "complex", "district", "street", "house", "is_active",
                                        "status", "note", "ts", "pick_id", "sent_at", "opened_at", "reaction", "reaction_text",
                                        "updated")} | {"photo": photos[0] if photos else None})
    log = [dict(r) for r in conn.execute("SELECT ts, kind, text FROM client_log WHERE client_id = ? ORDER BY ts DESC, id DESC LIMIT 200",
                                         (cid,))]
    events = [dict(r) for r in conn.execute("""SELECT id, kind, title, ts, done, listing_id FROM planner_events
                                               WHERE user_id = ? AND client_id = ? ORDER BY ts DESC LIMIT 50""", (user_id, cid))]
    from . import worklog
    picks = [dict(r) for r in conn.execute("""SELECT k.id, k.title, k.token, k.views, k.views_unique, k.viewed_at, k.updated, k.color,
                                                     (SELECT COUNT(*) FROM collection_items i WHERE i.collection_id = k.id) AS n
                                              FROM collections k WHERE k.user_id = ? AND k.client_id = ? ORDER BY k.updated DESC""",
                                           (user_id, cid))]
    from . import config
    for pk in picks:
        pk["url"] = f"{config.PICK_URL}/c/{pk.pop('token')}"
    hidden = conn.execute("SELECT COUNT(*) FROM client_hidden WHERE client_id = ?", (cid,)).fetchone()[0]
    return {"client": _row(c), "items": items, "log": log, "events": events, "hidden": hidden,
            "work": worklog.for_client(conn, user_id, cid), "picks": picks, "pair": PAIR, "reactions": REACTIONS}


def _name(lst) -> str:
    return f"ЖК {lst['complex']}, {lst['title']}" if lst["complex"] else lst["title"]


def set_pair(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, status: str, *, pick_id: int | None = None,
             auto: bool = False, reaction: str | None = None, reaction_text: str | None = None, commit: bool = True) -> dict:
    """Статус пары клиент+объект — ОДНА функция для всех мест (окно объекта, подбор, подборка, реакция клиента, журнал).
    auto=True — сервис двигает статус сам и только вперёд (клиент открыл подборку не «откатит» «показ»).
    Пишет в историю клиента и двигает этап клиента (если агент не двигал его руками)."""
    c = _own(conn, user_id, cid)
    if c is None:
        return {"error": "Клиент не найден."}
    lst = conn.execute("SELECT id, title, complex FROM listings WHERE id = ?", (listing_id,)).fetchone()
    if lst is None:
        return {"error": "Объект не найден."}
    status = OLD_STATUS.get(status, status)
    if status not in PAIR:
        return {"error": "Неизвестный статус."}
    now = int(time.time())
    old = conn.execute("SELECT * FROM client_items WHERE client_id = ? AND listing_id = ?", (cid, listing_id)).fetchone()
    new_status = status
    if old is not None and auto and RANK[old["status"]] >= RANK[status] and not (status == "rejected" and old["status"] == "liked") \
            and not (status == "liked" and old["status"] == "rejected"):
        new_status = old["status"]   # вперёд только (но «нравится» ↔ «не подходит» клиент может поменять)
    if old is None:
        conn.execute("""INSERT INTO client_items (client_id, listing_id, status, ts, updated, pick_id, sent_at, opened_at, reaction,
                                                  reaction_text) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                     (cid, listing_id, new_status, now, now, pick_id, now if status == "sent" else None,
                      now if status == "opened" else None, reaction, reaction_text))
    else:
        conn.execute("""UPDATE client_items SET status = ?, updated = ?, pick_id = COALESCE(?, pick_id),
                            sent_at = CASE WHEN ? = 'sent' THEN COALESCE(sent_at, ?) ELSE sent_at END,
                            opened_at = CASE WHEN ? = 'opened' THEN COALESCE(opened_at, ?) ELSE opened_at END,
                            reaction = COALESCE(?, reaction), reaction_text = CASE WHEN ? IS NOT NULL THEN ? ELSE reaction_text END
                         WHERE client_id = ? AND listing_id = ?""",
                     (new_status, now, pick_id, status, now, status, now, reaction, reaction, reaction_text, cid, listing_id))
    if old is None or old["status"] != new_status:
        pick = conn.execute("SELECT title FROM collections WHERE id = ?", (pick_id,)).fetchone() if pick_id else None
        _log(conn, cid, "object", f"{PAIR[new_status]}: {_name(lst)}" + (f" · подборка «{pick['title']}»" if pick else ""))
    # этап клиента — сам, если агент не двигал его руками
    if not c["stage_manual"]:
        stage = c["stage"]
        if new_status in ("sent", "opened", "liked", "rejected") and stage == "new":
            stage = "search"
        elif new_status in ("show", "talks") and stage in ("new", "search"):
            stage = "shows"
        if stage != c["stage"]:
            conn.execute("UPDATE clients SET stage = ?, stage_ts = ? WHERE id = ?", (stage, now, cid))
            _log(conn, cid, "stage", f"Этап: {STAGES[c['stage']]} → {STAGES[stage]} (сам)")
    conn.execute("UPDATE clients SET updated = ? WHERE id = ?", (now, cid))
    if commit:
        conn.commit()
    return {"ok": True, "status": new_status}


def set_item(conn: sqlite3.Connection, user_id: int, cid: int, listing_id: int, status: str | None) -> dict:
    """Агент вручную: статус пары (подобран, показ, переговоры…); None — убрать объект у клиента.
    В избранное больше не кладём (ТЗ v2: избранное — просто закладки)."""
    if status is None:
        if _own(conn, user_id, cid) is None:
            return {"error": "Клиент не найден."}
        lst = conn.execute("SELECT id, title, complex FROM listings WHERE id = ?", (listing_id,)).fetchone()
        conn.execute("DELETE FROM client_items WHERE client_id = ? AND listing_id = ?", (cid, listing_id))
        if lst:
            _log(conn, cid, "object", f"Убран объект: {_name(lst)}")
        conn.commit()
        return {"ok": True}
    return set_pair(conn, user_id, cid, listing_id, status if status in PAIR or status in OLD_STATUS else "matched")


def forget_pick(conn: sqlite3.Connection, pick_id: int, listing_id: int | None = None) -> None:
    """Подборку удалили или убрали из неё объект (решение Артёма 13.10): статусы, поставленные подборкой,
    реакции клиента и записи журнала, сделанные в подборке, уходят вместе с ней. Показ/переговоры, которые агент
    вёл сам, и записи из окна объекта (без подборки) остаются."""
    where, params = "pick_id = ?", [pick_id]
    if listing_id:
        where += " AND listing_id = ?"
        params.append(listing_id)
    rows = conn.execute(f"""SELECT i.client_id, i.listing_id, l.title, l.complex FROM client_items i
                            JOIN listings l ON l.id = i.listing_id
                            WHERE {where} AND status IN ({",".join("?" * len(PICK_STATUSES))})""",
                        params + list(PICK_STATUSES)).fetchall()
    for r in rows:
        conn.execute("DELETE FROM client_items WHERE client_id = ? AND listing_id = ?", (r["client_id"], r["listing_id"]))
        _log(conn, r["client_id"], "object", f"Убран из подборки: {_name(r)}")
    conn.execute(f"UPDATE client_items SET pick_id = NULL WHERE {where}", params)
    conn.execute(f"DELETE FROM pick_reactions WHERE collection_id = ?{' AND listing_id = ?' if listing_id else ''}", params)
    conn.execute(f"DELETE FROM work_notes WHERE collection_id = ?{' AND listing_id = ?' if listing_id else ''}", params)


def ensure_pick(conn: sqlite3.Connection, user, cid: int) -> int | None:
    """Активная подборка клиента: последняя его; нет — создаём «Для {имя}» с контактом агента из «Настроек»."""
    from . import picks
    row = conn.execute("SELECT id FROM collections WHERE user_id = ? AND client_id = ? ORDER BY updated DESC LIMIT 1",
                       (user["id"], cid)).fetchone()
    if row:
        return row[0]
    c = _own(conn, user["id"], cid)
    if c is None:
        return None
    made = picks.save(conn, user, {"title": f"Для {c['name']}"[:120], "client_id": cid})
    return None if "error" in made else made["id"]


def add_to_client(conn: sqlite3.Connection, user, cid: int, listing_id: int) -> dict:
    """«Клиенту ▾»: объект — в активную подборку клиента, статус «Отправлен». Возвращает подборку (ссылку)."""
    from . import picks
    pid = ensure_pick(conn, user, cid)
    if pid is None:
        return {"error": "Клиент не найден."}
    res = picks.set_item(conn, user["id"], pid, listing_id, True)
    if "error" in res:
        return res
    pick = next((x for x in picks.mine(conn, user["id"]) if x["id"] == pid), None)
    return {"ok": True, "pick": {"id": pid, "url": pick["url"], "title": pick["title"]} if pick else {"id": pid}}


def hide(conn: sqlite3.Connection, user_id: int, cid: int, ids: list[int], on: bool = True) -> dict:
    """«Не подходит» в подборе: этому клиенту объект больше не предлагать (раздел «Скрытые» — вернуть)."""
    if _own(conn, user_id, cid) is None:
        return {"error": "Клиент не найден."}
    now = int(time.time())
    for lid in ids[:200]:
        if on:
            conn.execute("INSERT OR IGNORE INTO client_hidden (client_id, listing_id, ts) VALUES (?,?,?)", (cid, int(lid), now))
        else:
            conn.execute("DELETE FROM client_hidden WHERE client_id = ? AND listing_id = ?", (cid, int(lid)))
    conn.commit()
    return {"ok": True}


def add_note(conn: sqlite3.Connection, user_id: int, cid: int, text: str) -> dict:
    if _own(conn, user_id, cid) is None:
        return {"error": "Клиент не найден."}
    text = str(text or "").strip()
    if not text:
        return {"error": "Пустая заметка."}
    _log(conn, cid, "note", text)
    conn.execute("UPDATE clients SET updated = ? WHERE id = ?", (int(time.time()), cid))
    conn.commit()
    return {"ok": True}


def match_query(c: dict) -> dict:
    """Пожелания клиента → параметры поиска сайта (/api/listings)."""
    q = {"deal": c.get("deal") or "sale"}
    rooms = c.get("rooms") if isinstance(c.get("rooms"), list) else json.loads(c.get("rooms") or "[]")
    districts = c.get("districts") if isinstance(c.get("districts"), list) else json.loads(c.get("districts") or "[]")
    if rooms:
        q["rooms"] = rooms
    if districts:
        q["district"] = districts
    if c.get("budget_min"):
        q["price_min"] = c["budget_min"]
    wishes = c.get("wishes") if isinstance(c.get("wishes"), dict) else json.loads(c.get("wishes") or "{}")
    tol = (wishes.get("budget_tol") or 0) / 100   # бюджет строгий; допуск — явно, в процентах
    if c.get("budget_min"):
        q["price_min"] = int(c["budget_min"] * (1 - tol))
    if c.get("budget_max"):
        q["price_max"] = int(c["budget_max"] * (1 + tol))
    for k in ("types", "area_min", "area_max", "not_first", "not_last", "complexes", "cond", "no_encumbrance"):
        if wishes.get(k):
            q[k] = wishes[k]
    if "mortgage" in (wishes.get("pay") or []):
        q["mortgage"] = True
    return q


def query_for(c: dict, user_id: int, **extra):
    """Пожелания клиента → поисковый запрос сайта (без уже связанных с клиентом и «не подходит»)."""
    from . import search
    p = match_query(c)
    kw = dict(deal=p["deal"], rooms=p.get("rooms", []), districts=p.get("district", []),
              price_min=p.get("price_min"), price_max=p.get("price_max"), types=p.get("types", []),
              area_min=p.get("area_min"), area_max=p.get("area_max"), complexes=p.get("complexes", []),
              not_first=bool(p.get("not_first")), not_last=bool(p.get("not_last")), cond=p.get("cond", []),
              no_encumbrance=bool(p.get("no_encumbrance")), mortgage=bool(p.get("mortgage")),
              exclude_client=c["id"], hide_user=user_id)
    kw.update(extra)   # фильтры экрана подбора (только с фото, не первый этаж, сортировка) поверх пожеланий
    return search.Query(**kw)


def fits_listing(conn: sqlite3.Connection, user_id: int, listing_id: int, now: int | None = None) -> list[dict]:
    """Ваши клиенты, которым подходит этот объект по пожеланиям (и он им ещё не предложен) — вместо «покупателей
    из чатов» (акт приёмки 14.10, Б4: там была реклама и чужие продажи)."""
    import time as _t

    from . import search
    now = now or int(_t.time())
    out = []
    for r in conn.execute("""SELECT * FROM clients WHERE user_id = ? AND archived = 0 AND stage NOT IN ('deal', 'lost')
                             ORDER BY updated DESC LIMIT 100""", (user_id,)).fetchall():
        c = dict(r)
        qr = query_for(c, user_id)
        where, params = search._where(qr, now)
        if conn.execute(f"SELECT 1 FROM listings l WHERE {where} AND l.id = ?", params + [listing_id]).fetchone():
            out.append({"id": c["id"], "name": c["name"], "stage": c["stage"], "stage_label": STAGES.get(c["stage"], "")})
    return out


def for_listing(conn: sqlite3.Connection, user_id: int, listing_id: int) -> list[dict]:
    """Кому из клиентов уже предложен объект (для окна объекта)."""
    return [dict(r) | {"label": PAIR.get(r["status"], r["status"])} for r in conn.execute(
        """SELECT c.id, c.name, i.status, i.reaction, i.pick_id FROM client_items i JOIN clients c ON c.id = i.client_id
           WHERE c.user_id = ? AND i.listing_id = ?""", (user_id, listing_id))]


def summary(conn: sqlite3.Connection, user_id: int) -> dict:
    """Для кабинета: клиенты по этапам и ближайшие следующие шаги."""
    st = {k: 0 for k in STAGES}
    for s, n in conn.execute("SELECT stage, COUNT(*) FROM clients WHERE user_id = ? AND archived = 0 GROUP BY stage", (user_id,)):
        st[s] = n
    nxt = [dict(r) for r in conn.execute("""SELECT id, name, next_ts, next_text, stage FROM clients
                                            WHERE user_id = ? AND archived = 0 AND next_ts IS NOT NULL
                                            ORDER BY next_ts LIMIT 5""", (user_id,))]
    return {"stages": [{"key": k, "label": STAGES[k], "n": st[k]} for k in STAGES], "next": nxt,
            "total": sum(st.values())}



# ─── «Новое под клиента»: объекты, появившиеся после прошлой проверки, — агенту уведомлением ───
def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def new_matches(conn: sqlite3.Connection, now: int | None = None) -> int:
    """Раз в 15 минут (worker): у активных клиентов с пожеланиями — новые подходящие объекты → уведомление
    «Под Анну: 3 новых объекта» с кнопками «Посмотреть» и «Всё в подборку». Первый раз — только запоминаем момент."""
    from . import notices, search
    now = int(now or time.time())
    sent = 0
    for c in conn.execute("""SELECT * FROM clients WHERE archived = 0 AND stage NOT IN ('deal', 'lost')""").fetchall():
        c = dict(c)
        rooms = json.loads(c.get("rooms") or "[]")
        districts = json.loads(c.get("districts") or "[]")
        if not (rooms or districts or c.get("budget_max") or c.get("budget_min")):
            continue
        since = c.get("match_seen")
        conn.execute("UPDATE clients SET match_seen = ? WHERE id = ?", (now, c["id"]))
        if not since:
            continue
        res = search.search(conn, query_for(c, c["user_id"], since=since, size=50), now, False)
        items = res["items"]
        if not items:
            continue
        n = len(items)
        url = f"/clients?id={c['id']}&fresh={since}"
        notices.add(conn, c["user_id"], "client", f"Под {c['name']}: {n} {_plural(n, 'новый объект', 'новых объекта', 'новых объектов')}",
                    " · ".join((x.get("complex") and f"ЖК {x['complex']}" or x["title"]) for x in items[:3]),
                    url=url, dedup_s=0,
                    actions=[{"label": "Посмотреть", "url": url}, {"label": "Отправить в подборку", "url": url + "&topick=1"}])
        sent += 1
    conn.commit()
    return sent


def to_pick(conn: sqlite3.Connection, user, cid: int, ids: list[int]) -> dict:
    """«В подборку» (подбор, «новое под клиента»): объекты — в активную подборку клиента, статус «Отправлен»."""
    from . import picks
    pid = ensure_pick(conn, user, cid)
    if pid is None:
        return {"error": "Клиент не найден."}
    added = 0
    for lid in ids[:100]:
        if picks.set_item(conn, user["id"], pid, int(lid), True, commit=False).get("ok"):
            added += 1
    conn.commit()
    pick = next((x for x in picks.mine(conn, user["id"]) if x["id"] == pid), None)
    return {"pick": {"id": pid, "url": pick["url"] if pick else "", "title": pick["title"] if pick else ""}, "added": added}

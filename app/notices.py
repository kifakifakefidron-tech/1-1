"""Уведомления на сайте (страница /notifications и колокольчик в шапке).

Что приходит:
  * search  — новые объекты по поиску, за которым человек следит;
  * price   — изменилась цена объекта из избранного (снизилась/выросла);
  * gone    — объект из избранного снят с сайта;
  * sub     — доступ к номерам скоро закончится / закончился;
  * own     — свой объект агента: «ещё актуален?» (с кнопками) и «снят по сроку»;
  * plan    — напоминание о показе/созвоне из планера.
Письма на почту дублируют самое важное (новые объекты по поиску, снижение цены, актуальность своего объекта).
"""
from __future__ import annotations

import json
import sqlite3
import time

DAY = 86400
KEEP_DAYS = 60
ICONS = {"search": "🔔", "price": "₽", "gone": "✕", "sub": "★", "own": "🏠", "plan": "📅", "client": "👥"}


# 10.11 (Артём: «в настройках и во вкладке уведомлений можно выключить уведомления»): виды, которые человек выключает сам
# (users.settings.notify_off — список видов; "all" — все сразу). Выключенный вид не приходит ни на сайт, ни письмом.
# Служебные (доступ заканчивается, оплаты сервисов, отчёты админу) не выключаются — без них можно потерять доступ.
USER_KINDS = (
    ("search", "Новые объекты по моим поискам", "Когда по сохранённому поиску появились объекты"),
    ("price", "Цена в избранном изменилась", "Снизилась или выросла цена объекта из избранного"),
    ("gone", "Объект из избранного снят", "Продан или больше не присылают"),
    ("client", "Клиенты", "Клиент открыл подборку, ответил, новые объекты под клиента"),
    ("buyer", "Покупатель под мой объект", "Появился запрос покупателя под ваш объект"),
    ("plan", "Напоминания планера", "Показы, звонки и встречи — заранее"),
    ("own", "Мои объекты: ещё актуален?", "Без ответа объект снимется по сроку"),
)
OFF_KINDS = {k for k, _, _ in USER_KINDS}


def clean_off(raw) -> list[str]:
    vals = raw if isinstance(raw, list) else []
    return sorted({str(x) for x in vals if str(x) in OFF_KINDS or str(x) == "all"})


def is_off(conn: sqlite3.Connection, user_id: int | None, kind: str) -> bool:
    """Человек выключил этот вид уведомлений (на сайте и на почте)."""
    if not user_id or kind not in OFF_KINDS:
        return False
    row = conn.execute("SELECT json_extract(settings, '$.notify_off') FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not row[0]:
        return False
    try:
        off = json.loads(row[0])
    except (TypeError, ValueError):
        return False
    return isinstance(off, list) and (kind in off or "all" in off)


def add(conn: sqlite3.Connection, user_id: int, kind: str, title: str, body: str = "", *,
        url: str | None = None, listing_id: int | None = None, actions: list[dict] | None = None,
        dedup_s: int = DAY) -> bool:
    """Добавить уведомление. Такое же (тот же вид и объект/ссылка) за последние сутки не дублируем."""
    if is_off(conn, user_id, kind):
        return False
    now = int(time.time())
    dup = conn.execute("""SELECT 1 FROM notices WHERE user_id = ? AND kind = ? AND COALESCE(listing_id, 0) = ?
                          AND COALESCE(url, '') = ? AND title = ? AND ts > ?""",
                       (user_id, kind, listing_id or 0, url or "", title, now - dedup_s)).fetchone()
    if dup:
        return False
    conn.execute("""INSERT INTO notices (user_id, ts, kind, title, body, url, listing_id, actions)
                    VALUES (?,?,?,?,?,?,?,?)""",
                 (user_id, now, kind, title[:200], body[:1000], url, listing_id,
                  json.dumps(actions, ensure_ascii=False) if actions else None))
    return True


def unread(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM notices WHERE user_id = ? AND read = 0", (user_id,)).fetchone()[0]


def items(conn: sqlite3.Connection, user_id: int, limit: int = 100) -> list[dict]:
    out = []
    for r in conn.execute("""SELECT n.*, l.photos, l.is_active FROM notices n LEFT JOIN listings l ON l.id = n.listing_id
                             WHERE n.user_id = ? ORDER BY n.id DESC LIMIT ?""", (user_id, limit)):
        d = {k: r[k] for k in ("id", "ts", "kind", "title", "body", "url", "listing_id", "read", "is_active")}
        d["actions"] = json.loads(r["actions"]) if r["actions"] else []
        photos = json.loads(r["photos"] or "[]") if r["photos"] else []
        d["photo"] = photos[0] if photos else None
        d["icon"] = ICONS.get(r["kind"], "•")
        out.append(d)
    return out


def mark_read(conn: sqlite3.Connection, user_id: int, notice_id: int | None = None) -> None:
    if notice_id:
        conn.execute("UPDATE notices SET read = 1 WHERE user_id = ? AND id = ?", (user_id, notice_id))
    else:
        conn.execute("UPDATE notices SET read = 1 WHERE user_id = ? AND read = 0", (user_id,))
    conn.commit()


def cleanup(conn: sqlite3.Connection) -> int:
    n = conn.execute("DELETE FROM notices WHERE ts < ?", (int(time.time()) - KEEP_DAYS * DAY,)).rowcount
    conn.commit()
    return n


def _price_hint(conn: sqlite3.Connection, user_id: int) -> str:
    """«Неделя — 49 ₽» по тарифам этого человека (первая неделя со скидкой, если ещё не платил)."""
    try:
        from . import payments
        p = payments.plans(conn, user_id)[0]
        return f" Неделя — {p['price']} ₽." if p["days"] == 7 else f" {p['days']} дн. — {p['price']} ₽."
    except Exception:  # noqa: BLE001
        return ""


def check_subscriptions(conn: sqlite3.Connection, fmt_day, send=None) -> int:
    """Доступ к номерам заканчивается через ≤2 дня или закончился — одно уведомление на каждое событие.
    14.11 (Артём: «приходят ли уведомления об окончании пробных 14 дней и оплаченной подписки?»): тексты для пробного
    периода и для оплаченной подписки разные, ссылка — на оплату (/pay), и дубль письмом (если почта есть и письма
    не выключены). Вид 'sub' — служебный, человек его не выключает."""
    from . import db
    now = int(time.time())
    made = 0
    for u in conn.execute("""SELECT id, email, trial_until, paid_until, MAX(trial_until, paid_until) AS until, sub_notified
                             FROM users WHERE is_admin = 0 AND blocked = 0 AND MAX(trial_until, paid_until) > ?""",
                          (now - 3 * DAY,)).fetchall():
        until = u["until"]
        trial = (u["paid_until"] or 0) < (u["trial_until"] or 0)      # доступ держится на пробном периоде
        what = "Бесплатный пробный период" if trial else "Подписка"
        price = _price_hint(conn, u["id"])
        if now < until <= now + 2 * DAY:
            tag = f"soon:{until}"
            title = f"{what} закончится {fmt_day(until)}"
            body = ("Чтобы и дальше видеть телефоны агентов и пользоваться клиентами и подборками, оплатите доступ."
                    if trial else "Продлите подписку, чтобы и дальше видеть телефоны агентов.") + price
        elif until <= now:
            tag = f"end:{until}"
            title = f"{what} закончился" if trial else "Подписка закончилась"
            body = "Телефоны агентов снова скрыты. Оплатите доступ — всё вернётся сразу." + price
        else:
            continue
        if u["sub_notified"] == tag:
            continue
        add(conn, u["id"], "sub", title, body, url="/pay", dedup_s=0)
        conn.execute("UPDATE users SET sub_notified = ? WHERE id = ?", (tag, u["id"]))
        made += 1
        if send and u["email"]:
            from . import config
            site = (config.SITE_URL or "").rstrip("/")
            db.flush(conn)
            try:
                send(u["email"], f"1+1: {title}"[:150],
                     f"{body}\n\nОплатить: {site}/pay\n\nЕсли уже оплатили — просто не обращайте внимания на это письмо.")
            except Exception:  # noqa: BLE001
                pass
    conn.commit()
    return made

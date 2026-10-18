"""Напоминания об оплате сервисов (18.10, Артём: «добавь в уведомления всё, что нужно оплачивать каждые 20 дней:
сервер, DeepSeek, Яндекс, SMS.ru и что там ещё у нас на оплате — приходит в админку»).

Каждый сервис — период (по умолчанию 20 дней) и дата последней оплаты (админка «Обзор» → «Оплаты сервисов» →
«Оплатил»). Подходит срок (за 2 дня и в день оплаты) — уведомление админам в колокольчик (kind 'billing', ссылка на
оплату) и письмо (notify.alert). Пока не нажали «Оплатил» — напоминаем раз в сутки. Данные — state billing (JSON).
"""
from __future__ import annotations

import json
import sqlite3
import time

from . import db

DAY = 86400
SERVICES = [   # ключ, название, где платить, период (дней)
    ("timeweb", "Сервер Timeweb Cloud", "https://timeweb.cloud/my/finances", 20),
    ("deepseek", "DeepSeek (нейросеть разбора)", "https://platform.deepseek.com/top_up", 20),
    ("yandex360", "Яндекс 360 (почта noreply@1plus1assistant.ru)", "https://admin.yandex.ru/", 20),
    ("smsru", "SMS.ru (подтверждение и вход по звонку)", "https://sms.ru/pay", 20),
    ("wappi", "Wappi (сбор сообщений из чатов)", "https://wappi.pro/", 20),
    ("regru", "Домен 1plus1assistant.ru (reg.ru)", "https://www.reg.ru/user/account/", 365),
]
WARN_DAYS = 2


def _state(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_state(conn, "billing") or "{}")


def status(conn: sqlite3.Connection, now: int | None = None) -> list[dict]:
    """Для админки: по каждому сервису — когда оплачен, когда следующая оплата, сколько дней осталось."""
    now = int(now or time.time())
    st = _state(conn)
    out = []
    for key, name, url, period in SERVICES:
        s = st.get(key) or {}
        per = int(s.get("period") or period)
        paid = int(s.get("paid") or 0)
        due = paid + per * DAY if paid else None
        out.append({"key": key, "name": name, "url": url, "period": per, "paid": paid or None, "due": due,
                    "left": None if due is None else int((due - now) // DAY)})
    return out


def mark_paid(conn: sqlite3.Connection, key: str, period: int | None = None, now: int | None = None) -> bool:
    if key not in {s[0] for s in SERVICES}:
        return False
    st = _state(conn)
    s = st.get(key) or {}
    s["paid"] = int(now or time.time())
    if period and 1 <= int(period) <= 400:
        s["period"] = int(period)
    st[key] = s
    db.set_state(conn, "billing", json.dumps(st))
    conn.commit()
    return True


def remind(conn: sqlite3.Connection, now: int | None = None) -> int:
    """Раз в сутки (worker): кому подходит срок — уведомление всем админам + письмо. Не отмечен ни разу — тоже напоминаем
    (раз в сутки), чтобы админ один раз нажал «Оплатил» и завёл даты."""
    from . import notices, notify
    now = int(now or time.time())
    if now - int(db.get_state(conn, "billing_remind_ts") or 0) < DAY - 600:
        return 0
    db.set_state(conn, "billing_remind_ts", str(now))
    admins = [r[0] for r in conn.execute("SELECT id FROM users WHERE is_admin = 1")]
    n = 0
    for s in status(conn, now):
        if s["left"] is not None and s["left"] > WARN_DAYS:
            continue
        if s["paid"] is None:
            title, body = f"Оплата: {s['name']}", "Отметьте дату последней оплаты в админке («Обзор» → «Оплаты сервисов»), и я буду напоминать вовремя."
        elif s["left"] < 0:
            title, body = f"Просрочена оплата: {s['name']}", f"Срок был {-s['left']} дн. назад. Оплатите и нажмите «Оплатил» в админке."
        else:
            when = "сегодня" if s["left"] == 0 else "через %d дн." % s["left"]
            title, body = (f"Оплатить {when}: {s['name']}",
                           "После оплаты нажмите «Оплатил» в админке («Обзор» → «Оплаты сервисов») — следующее напоминание через период.")
        for uid in admins:
            if notices.add(conn, uid, "billing", title, body, url=s["url"], dedup_s=DAY - 600):
                n += 1
        if s["paid"] is not None:
            notify.alert(conn, f"billing:{s['key']}", f"{title}. {body} {s['url']}", every_s=DAY - 600)
    conn.commit()
    return n

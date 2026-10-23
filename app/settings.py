"""Настройки аккаунта (users.settings, JSON): как выглядят подборки для клиента и порядок блоков в кабинете.

* подборки: цвет (из палитры или свой #rrggbb), обложка (своё фото), подпись (имя/агентство), имя и телефон агента;
* кабинет: порядок блоков (id виджетов), который агент собрал перетаскиванием;
* хранятся у аккаунта — одинаково на всех устройствах.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from pathlib import Path

from . import config

COLORS = ("gold", "teal", "sky", "violet", "coral", "rose", "lime", "slate")
DEFAULTS = {"pick_color": "gold", "pick_cover": "", "pick_photo": "", "pick_brand": "", "pick_name": "", "pick_phone": "", "cabinet_order": [],
            "blocks": [], "mail_off": False}
# Свои блоки кабинета — только безопасные виды и только данные (никакой разметки и скриптов от пользователя)
BLOCK_TYPES = ("note", "todo", "links", "search", "goal", "countdown", "stock")
MAX_BLOCKS = 12


def _blocks(raw) -> list[dict]:
    out = []
    for b in (raw if isinstance(raw, list) else [])[:MAX_BLOCKS]:
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            continue
        t = b["type"]
        bid = "u" + re.sub(r"[^a-z0-9]", "", str(b.get("id") or "").lower().removeprefix("u"))[:10]
        if len(bid) < 3:
            continue
        d = b.get("data") if isinstance(b.get("data"), dict) else {}
        title = str(b.get("title") or "").strip()[:60]
        color = b.get("color") if b.get("color") in COLORS else "gold"
        if t == "note":
            data = {"text": str(d.get("text") or "")[:2000]}
        elif t == "todo":
            data = {"items": [{"t": str(x.get("t") or "").strip()[:200], "done": bool(x.get("done"))}
                              for x in (d.get("items") or [])[:40] if isinstance(x, dict) and str(x.get("t") or "").strip()]}
        elif t == "links":
            data = {"items": [{"t": str(x.get("t") or "").strip()[:60] or str(x.get("u"))[:60], "u": str(x.get("u")).strip()[:300]}
                              for x in (d.get("items") or [])[:12]
                              if isinstance(x, dict) and re.match(r"^https?://[^\s<>\"']+$", str(x.get("u") or "").strip())]}
        elif t == "search":
            sid = d.get("saved_id")
            data = {"saved_id": int(sid) if str(sid).isdigit() else None}
        elif t == "goal":
            def num(v, lo, hi, dflt):
                try:
                    return max(lo, min(hi, int(v)))
                except (TypeError, ValueError):
                    return dflt
            data = {"n": num(d.get("n"), 0, 99999, 0), "of": num(d.get("of"), 1, 99999, 5)}
        elif t == "stock":   # бумага Мосбиржи: только код (SMLT, SBER, TMOS…), цены грузит worker
            tk = re.sub(r"[^A-Z0-9]", "", str(d.get("ticker") or "").upper())[:12]
            data = {"ticker": tk if len(tk) >= 2 else ""}
        else:   # countdown
            date = str(d.get("date") or "")
            data = {"date": date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else ""}
        out.append({"id": bid, "type": t, "title": title, "color": color, "data": data})
    return out
_MAGIC = {b"\xff\xd8\xff": "jpg", b"\x89PNG": "png", b"RIFF": "webp"}
COVER_DIR = "0"   # обложки подборок: data/photos/0/<хеш>.jpg (тот же путь /photos/…, что и у фото объектов)


def get(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute("SELECT settings FROM users WHERE id = ?", (user_id,)).fetchone()
    try:
        saved = json.loads(row[0]) if row and row[0] else {}
    except (TypeError, ValueError):
        saved = {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _phone(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d[0] in "78":
        return "+7" + d[1:]
    if len(d) == 10:
        return "+7" + d
    return ""


def save(conn: sqlite3.Connection, user_id: int, patch: dict) -> dict:
    cur = get(conn, user_id)
    if "pick_color" in patch:
        c = str(patch["pick_color"] or "")
        cur["pick_color"] = c if c in COLORS or re.fullmatch(r"#[0-9a-fA-F]{6}", c) else "gold"
    for key in ("pick_cover", "pick_photo"):   # только своё загруженное фото (или пусто — без обложки / без фото)
        if key in patch:
            u = str(patch[key] or "")
            cur[key] = u if re.fullmatch(rf"/photos/{COVER_DIR}/[0-9a-f]{{16}}\.(?:jpg|png|webp)", u) else ""
    for k, n in (("pick_brand", 60), ("pick_name", 80)):
        if k in patch:
            cur[k] = str(patch[k] or "").strip()[:n]
    if "pick_phone" in patch:
        cur["pick_phone"] = _phone(str(patch["pick_phone"] or ""))
    if "mail_off" in patch:   # 24.10: письма-уведомления на почту выключены (коды входа приходят всегда)
        cur["mail_off"] = bool(patch["mail_off"])
        from . import mailer
        mailer._off["t"] = 0   # список выключивших — перечитать сразу
    if "blocks" in patch:
        cur["blocks"] = _blocks(patch["blocks"])
    if "cabinet_order" in patch and isinstance(patch["cabinet_order"], list):
        cur["cabinet_order"] = [str(x)[:30] for x in patch["cabinet_order"] if re.fullmatch(r"[A-Za-z0-9_-]{1,30}", str(x))][:40]
    if "name" in patch:   # имя в профиле — колонка users.name
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (str(patch["name"] or "").strip()[:80] or None, user_id))
    conn.execute("UPDATE users SET settings = ? WHERE id = ?", (json.dumps(cur, ensure_ascii=False), user_id))
    conn.commit()
    return cur


def save_cover(data_url: str, folder_name: str = COVER_DIR) -> tuple[str | None, str]:
    """Обложка подборки (и свои фото к объекту в подборке — folder_name='pk'): картинка из браузера (уже уменьшенная)
    → data/photos/<папка>/<хеш>.<тип>."""
    try:
        raw = base64.b64decode(str(data_url).split(",", 1)[-1], validate=False)
    except (ValueError, TypeError):
        return None, "Не получилось прочитать фото."
    ext = next((e for magic, e in _MAGIC.items() if raw.startswith(magic)), None)
    if ext is None or len(raw) > 4_000_000 or len(raw) < 1000:
        return None, "Нужна фотография JPEG/PNG до 4 МБ."
    name = hashlib.sha1(raw).hexdigest()[:16] + "." + ext
    folder = Path(config.PHOTOS_DIR) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(raw)
    return f"/photos/{folder_name}/{name}", "Фото загружено."


def public_pick_style(conn: sqlite3.Connection, user_id: int) -> dict:
    """Что из настроек видит клиент в подборке."""
    s = get(conn, user_id)
    return {"color": s["pick_color"], "cover": s["pick_cover"], "brand": s["pick_brand"], "photo": s.get("pick_photo") or ""}

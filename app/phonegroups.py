"""25.10 (Артём: «объекты от этих номеров одинаковые, но отправляются с разных номеров — совместим их»).

«Один агент — несколько номеров»: номера одной группы склейка повторов считает ОДНИМ номером — тот же текст с другим
номером внутри, тот же агент + место + комнаты + этаж (dedupe, dupreview). Группы ведёт админ на /dups
(state phone_groups: [[«+7…», …], …]); по умолчанию — PHONE_GROUPS из config. После правки всё перепроверяется заново."""
from __future__ import annotations

import json
import sqlite3
import time

from . import config, db
from .rules import normalize_phone

TTL_S = 60
_map: dict[str, str] = {}
_loaded = 0.0


def parse(text: str) -> list[list[str]]:
    """Строка — одна группа, номера через запятую / точку с запятой / «и»."""
    import re
    out = []
    for line in str(text or "").splitlines():
        nums = []
        for part in re.split(r"[,;]|\s+и\s+", line):
            p = normalize_phone(part)
            if p and p not in nums:
                nums.append(p)
        if len(nums) >= 2:
            out.append(nums)
    return out


def groups(conn: sqlite3.Connection) -> list[list[str]]:
    raw = db.get_state(conn, "phone_groups")
    if raw is None:
        return [g[:] for g in config.PHONE_GROUPS]
    try:
        return [list(g) for g in json.loads(raw) if isinstance(g, list)]
    except ValueError:
        return []


def _build(gs: list[list[str]]) -> dict[str, str]:
    m: dict[str, str] = {}
    for g in gs:   # номер в нескольких группах — группы сливаются
        roots = {m[p] for p in g if p in m}
        root = min(roots) if roots else g[0]
        for p, r in list(m.items()):
            if r in roots:
                m[p] = root
        for p in g:
            m[p] = root
    return m


_conn_id = 0


def refresh(conn: sqlite3.Connection, force: bool = False) -> None:
    global _map, _loaded, _conn_id
    if force or id(conn) != _conn_id or time.time() - _loaded > TTL_S:
        _map = _build(groups(conn))
        _loaded, _conn_id = time.time(), id(conn)


def canon(phone: str) -> str:
    return _map.get(phone, phone)


def canon_set(phones) -> set[str]:
    return {canon(p) for p in phones or ()}


def save(conn: sqlite3.Connection, text: str) -> list[list[str]]:
    """Сохранить группы и перепроверить все карточки заново (worker — следующим циклом)."""
    gs = parse(text)
    db.set_state(conn, "phone_groups", json.dumps(gs, ensure_ascii=False))
    db.set_state(conn, "dup_scan_id", "0")
    conn.commit()
    refresh(conn, force=True)
    return gs


def listing(conn: sqlite3.Connection) -> list[dict]:
    """Для окна на /dups: группы, у каждого номера — сколько карточек на сайте с ним."""
    from .dupreview import _pretty
    out = []
    for g in groups(conn):
        nums = [{"phone": ph, "pretty": _pretty(ph), "n": conn.execute(
            "SELECT COUNT(*) FROM listings WHERE is_active = 1 AND instr(phones, ?) > 0", (f'"{ph}"',)).fetchone()[0]}
            for ph in g]
        out.append({"phones": nums})
    return out


def add(conn: sqlite3.Connection, phones: list[str]) -> list[list[str]]:
    """Добавить номера одним агентом (из кнопки «Это один агент» в сравнении): слить с группой, где уже есть такой номер."""
    nums = [p for p in (normalize_phone(x) for x in phones or ()) if p]
    gs = groups(conn)
    if len(set(nums)) < 2 and not any(set(nums) & set(g) for g in gs):
        return gs
    gs.append(list(dict.fromkeys(nums)))
    m = _build(gs)
    merged: dict[str, list[str]] = {}
    for g in gs:
        for ph in g:
            lst = merged.setdefault(m[ph], [])
            if ph not in lst:
                lst.append(ph)
    return save(conn, "\n".join(", ".join(v) for v in merged.values() if len(v) >= 2))

"""Обучение на правках администратора: ЖК и район.

Текст объявлений не трогаем — учимся только тому, как адрес превращается в ЖК и район.
Три вида правил (таблица learned_rules):

  * addr        — «ул. Тургенева, 10 — это ЖК Мозаика, район ФМР» (точный дом);
  * cx_alias    — «то, что агенты пишут как „Акварели 3“, — это ЖК „Акварели“», или «„Выше“ — это не ЖК»;
  * cx_district — «ЖК Мозаика — это район ФМР» (если справочник ошибается).

Правила применяются к каждому новому объявлению ДО проверки на дубли (ingest.save_object, feed.sync).
Поэтому следующее объявление о том же объекте сразу получает исправленные ЖК/район, совпадает с уже
исправленной карточкой и склеивается с ней, а не создаёт новую карточку с той же ошибкой.
Правку сразу применяем и к уже собранным объектам (apply_to_existing).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import geo
from .textnorm import words

_cache: dict = {"t": 0.0, "rules": {}}
_STREET_WORDS = r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе|пл|площадь)\b"


def _street_key(v: str | None) -> str:
    return " ".join(re.sub(_STREET_WORDS, " ", words(v or "")).split())


def addr_key(street: str | None, house: str | None, settlement: str | None = None) -> str | None:
    s, h = _street_key(street), words(house or "").replace(" ", "")
    return f"addr:{words(settlement or '')}|{s}|{h}" if s and h else None


def cx_key(name: str | None) -> str | None:
    k = geo.complex_key(name) if name else None
    return f"cx:{k}" if k else None


def rules(conn: sqlite3.Connection, fresh: bool = False) -> dict:
    """Все правила (кэш на минуту: сайт и фоновая работа — разные процессы)."""
    if fresh or time.time() - _cache["t"] > 60:
        r = {}
        for row in conn.execute("SELECT kind, key, value FROM learned_rules"):
            r[(row["kind"], row["key"])] = json.loads(row["value"])
        _cache.update(t=time.time(), rules=r)
    return _cache["rules"]


def apply(conn: sqlite3.Connection, o) -> bool:
    """Применить правила к объекту (ParsedObject или dict). Возвращает True, если что-то поменяли."""
    get = (lambda k: o.get(k)) if isinstance(o, dict) else (lambda k: getattr(o, k))

    def put(k, v):
        if isinstance(o, dict):
            o[k] = v
        else:
            setattr(o, k, v)
    rs = rules(conn)
    if not rs:
        return False
    before = (get("complex"), get("district"))
    # 1) как написали ЖК → правильный ЖК (или «это не ЖК»)
    k = cx_key(get("complex"))
    if k and ("cx_alias", k) in rs:
        old = geo.resolve_complex(get("complex"))
        new = rs[("cx_alias", k)].get("complex")
        put("complex", new)
        # район, выведенный из неправильного ЖК, тоже убираем — его определят заново
        if old and old.district and get("district") == old.district:
            put("district", None)
        if new and not get("district"):
            rec = geo.resolve_complex(new)
            put("district", rec.district if rec else None)
    # 2) ЖК → район
    k = cx_key(get("complex"))
    if k and ("cx_district", k) in rs:
        put("district", rs[("cx_district", k)]["district"])
    # 3) точный дом → ЖК и район (самое конкретное правило — последним, оно главнее)
    k = addr_key(get("street"), get("house"), get("settlement"))
    if k and ("addr", k) in rs:
        v = rs[("addr", k)]
        if "complex" in v:
            put("complex", v["complex"])
        if v.get("district"):
            put("district", v["district"])
    return (get("complex"), get("district")) != before


def learn(conn: sqlite3.Connection, row: sqlite3.Row, new_complex: str | None, new_district: str | None,
          want: dict) -> list[str]:
    """Запомнить правки админа. want: {"addr": bool, "alias": bool, "cx_district": bool}.
    Возвращает подписи выученных правил (для сообщения админу)."""
    now = int(time.time())
    made: list[str] = []

    def save(kind, key, value, label):
        conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES (?,?,?,?,1,?)
                        ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label,
                        n = n + 1, ts = excluded.ts""",
                     (kind, key, json.dumps(value, ensure_ascii=False), label, now))
        made.append(label)

    if want.get("addr"):
        k = addr_key(row["street"], row["house"], row["settlement"])
        if k:
            save("addr", k, {"complex": new_complex, "district": new_district},
                 f"ул. {row['street']}, {row['house']} → {('ЖК ' + new_complex) if new_complex else 'без ЖК'}"
                 f"{', ' + new_district if new_district else ''}")
    old_cx = row["complex"]
    if want.get("alias") and old_cx and cx_key(old_cx) != cx_key(new_complex):
        save("cx_alias", cx_key(old_cx), {"complex": new_complex},
             f"«{old_cx}» → {('ЖК ' + new_complex) if new_complex else 'это не ЖК'}")
    if want.get("cx_district") and new_complex and new_district:
        save("cx_district", cx_key(new_complex), {"district": new_district}, f"ЖК {new_complex} → район {new_district}")
    conn.commit()
    rules(conn, fresh=True)
    return made


def apply_to_existing(conn: sqlite3.Connection, skip_id: int | None = None) -> int:
    """Применить все правила к уже собранным объектам (кроме поправленных админом вручную)."""
    from .ingest import _reindex, make_search_text, make_title
    changed = 0
    for r in conn.execute("""SELECT * FROM listings WHERE is_active = 1 AND source != 'own' AND admin_fixed = 0
                             AND id != ?""", (skip_id or 0,)).fetchall():
        d = dict(r)
        if apply(conn, d):
            d["title"] = make_title(d)
            d["search_text"] = make_search_text(d)
            conn.execute("""UPDATE listings SET complex = ?, district = ?, search_text = ?,
                            geo_status = CASE WHEN geo_status IN ('manual', 'learned') THEN geo_status ELSE 'pending' END
                            WHERE id = ?""", (d["complex"], d["district"], d["search_text"], r["id"]))
            _reindex(conn, r["id"], d["search_text"])
            changed += 1
    conn.commit()
    return changed


def list_rules(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT id, kind, key, label, n, ts FROM learned_rules ORDER BY ts DESC")]


def forget(conn: sqlite3.Connection, rule_id: int) -> None:
    conn.execute("DELETE FROM learned_rules WHERE id = ?", (rule_id,))
    conn.commit()
    rules(conn, fresh=True)

"""Обучение на правках администратора: ЖК и район.

Текст объявлений не трогаем — учимся только тому, как адрес превращается в ЖК и район.
Три вида правил (таблица learned_rules):

  * addr        — «ул. Тургенева, 10 — это ЖК Мозаика, район ФМР» (точный дом);
  * cx_alias    — «то, что агенты пишут как „Акварели 3“, — это ЖК „Акварели“», или «„Выше“ — это не ЖК»;
  * cx_district — «ЖК Мозаика — это район ФМР» (если справочник ошибается);
  * street      — «ул. Тургенева — это район ФМР» (для объявлений этой улицы без своего района).

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
NONE = "__none__"   # «Без района» / «Без ЖК» в справочнике
_STREET_WORDS = r"\b(?:ул|улица|пр кт|проспект|пер|переулок|проезд|бульвар|б р|шоссе|пл|площадь)\b"


def _street_key(v: str | None) -> str:
    return " ".join(re.sub(_STREET_WORDS, " ", words(v or "")).split())


def addr_key(street: str | None, house: str | None, settlement: str | None = None) -> str | None:
    s, h = _street_key(street), words(house or "").replace(" ", "")
    return f"addr:{words(settlement or '')}|{s}|{h}" if s and h else None


def cx_key(name: str | None) -> str | None:
    k = geo.complex_key(name) if name else None
    return f"cx:{k}" if k else None


def sync_districts(conn: sqlite3.Connection) -> None:
    """Районы, добавленные админом, — в справочник этого процесса."""
    try:
        geo.set_custom_districts([(r["name"], json.loads(r["aliases"] or "[]"))
                                  for r in conn.execute("SELECT name, aliases FROM custom_districts")],
                                 {r["old"]: r["new"] for r in conn.execute("SELECT old, new FROM district_renames")})
    except sqlite3.OperationalError:
        pass


def add_district(conn: sqlite3.Connection, name: str, aliases: list[str] | None = None) -> str | None:
    name = " ".join(str(name or "").split())[:60]
    if len(name) < 2:
        return None
    existing = geo.canonical_district(name)
    if existing:
        return existing
    conn.execute("INSERT OR IGNORE INTO custom_districts (name, aliases, ts) VALUES (?,?,?)",
                 (name, json.dumps([a for a in (aliases or []) if a], ensure_ascii=False), int(time.time())))
    conn.commit()
    sync_districts(conn)
    return name


def street_key(street: str | None, settlement: str | None = None) -> str | None:
    s = _street_key(street)
    return f"street:{words(settlement or '')}|{s}" if s else None


def rules(conn: sqlite3.Connection, fresh: bool = False) -> dict:
    """Все правила (кэш на минуту: сайт и фоновая работа — разные процессы)."""
    if fresh or time.time() - _cache["t"] > 60:
        sync_districts(conn)
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
    # 2) улица → район: если района нет, он угадан справочником по улице или улицу перенесли из этого района
    k = street_key(get("street"), get("settlement"))
    if k and ("street", k) in rs:
        v = rs[("street", k)]
        cur = get("district")
        if not cur or cur == geo.district_by_street(get("street")) or cur == v.get("from"):
            put("district", v["district"])
            if "extra" in v:
                put("extra_districts", list(v["extra"]))
    # 3) ЖК → район (ЖК точнее улицы)
    k = cx_key(get("complex"))
    if k and ("cx_district", k) in rs:
        v = rs[("cx_district", k)]
        put("district", v["district"])
        if "extra" in v:
            put("extra_districts", list(v["extra"]))
    # 4) точный дом → ЖК и район (самое конкретное правило — последним, оно главнее)
    k = addr_key(get("street"), get("house"), get("settlement"))
    if k and ("addr", k) in rs:
        v = rs[("addr", k)]
        if "complex" in v:
            put("complex", v["complex"])
        if v.get("district"):
            put("district", v["district"])
        if "extra" in v:
            put("extra_districts", list(v["extra"]))
    return (get("complex"), get("district")) != before


def learn(conn: sqlite3.Connection, row: sqlite3.Row, new_complex: str | None, new_district: str | None,
          want: dict, extra: list[str] | None = None) -> list[str]:
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
            save("addr", k, {"complex": new_complex, "district": new_district, "extra": extra or []},
                 f"ул. {row['street']}, {row['house']} → {('ЖК ' + new_complex) if new_complex else 'без ЖК'}"
                 f"{', ' + new_district if new_district else ''}")
    old_cx = row["complex"]
    if want.get("alias") and old_cx and cx_key(old_cx) != cx_key(new_complex):
        save("cx_alias", cx_key(old_cx), {"complex": new_complex},
             f"«{old_cx}» → {('ЖК ' + new_complex) if new_complex else 'это не ЖК'}")
    if want.get("street") and new_district:
        k = street_key(row["street"], row["settlement"])
        if k:
            save("street", k, {"district": new_district, "extra": extra or []},
                 f"ул. {row['street']} → {', '.join([new_district, *(extra or [])])}")
    if want.get("cx_district") and new_complex and new_district:
        save("cx_district", cx_key(new_complex), {"district": new_district, "extra": extra or []},
             f"ЖК {new_complex} → {', '.join([new_district, *(extra or [])])}")
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
            extra = d["extra_districts"] if isinstance(d["extra_districts"], list) else json.loads(d["extra_districts"] or "[]")
            conn.execute("""UPDATE listings SET complex = ?, district = ?, extra_districts = ?, search_text = ?,
                            geo_status = CASE WHEN geo_status IN ('manual', 'learned') THEN geo_status ELSE 'pending' END
                            WHERE id = ?""", (d["complex"], d["district"], json.dumps(extra, ensure_ascii=False),
                                              d["search_text"], r["id"]))
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


# ─── управление районами (админка «Районы») ───────────────────────────────
def rename_district(conn: sqlite3.Connection, old: str, new: str) -> dict:
    """Переименовать район (или слить с существующим). Старое имя продолжает узнаваться в объявлениях."""
    sync_districts(conn)
    old = geo.canonical_district(old) or old
    new = " ".join(str(new or "").split())[:60]
    if not new or new == old:
        return {"listings": 0}
    same = geo.canonical_district(new)
    target = same if same and same != old else new        # такой район уже есть — сливаем; своё же прозвище — просто имя
    now = int(time.time())
    if conn.execute("SELECT 1 FROM custom_districts WHERE name = ?", (old,)).fetchone():
        aliases = json.loads(conn.execute("SELECT aliases FROM custom_districts WHERE name = ?", (old,)).fetchone()[0])
        conn.execute("DELETE FROM custom_districts WHERE name = ?", (old,))
        if not geo.canonical_district(target) or target == new:
            conn.execute("INSERT OR REPLACE INTO custom_districts (name, aliases, ts) VALUES (?,?,?)",
                         (target, json.dumps(aliases + [old], ensure_ascii=False), now))
    else:
        conn.execute("INSERT OR REPLACE INTO district_renames (old, new, ts) VALUES (?,?,?)", (old, target, now))
        conn.execute("UPDATE district_renames SET new = ? WHERE new = ?", (target, old))   # цепочки
    # Объекты, правила, подписки — на новое имя
    n = conn.execute("UPDATE listings SET district = ? WHERE district = ?", (target, old)).rowcount
    for r in conn.execute("SELECT id, extra_districts FROM listings WHERE extra_districts LIKE ?", (f'%"{old}"%',)).fetchall():
        ex = [target if x == old else x for x in json.loads(r["extra_districts"])]
        conn.execute("UPDATE listings SET extra_districts = ? WHERE id = ?", (json.dumps(list(dict.fromkeys(ex)), ensure_ascii=False), r["id"]))
    for r in conn.execute("SELECT id, value, label FROM learned_rules WHERE value LIKE ?", (f'%{old}%',)).fetchall():
        v = json.loads(r["value"])
        if v.get("district") == old:
            v["district"] = target
        if v.get("from") == old:
            v["from"] = target
        if "extra" in v:
            v["extra"] = [target if x == old else x for x in v["extra"]]
        conn.execute("UPDATE learned_rules SET value = ?, label = ? WHERE id = ?",
                     (json.dumps(v, ensure_ascii=False), (r["label"] or "").replace(old, target), r["id"]))
    import urllib.parse
    for r in conn.execute("SELECT id, params, page_query FROM saved_searches WHERE params LIKE '%district%'").fetchall():
        def fix(q):
            pairs = []
            for k, val in urllib.parse.parse_qsl(q or ""):
                if k == "district":
                    val = "|".join(target if x == old else x for x in val.split("|"))
                    val = ",".join(target if x == old else x for x in val.split(","))
                pairs.append((k, val))
            return urllib.parse.urlencode(pairs)
        conn.execute("UPDATE saved_searches SET params = ?, page_query = ? WHERE id = ?",
                     (fix(r["params"]), fix(r["page_query"]), r["id"]))
    conn.commit()
    sync_districts(conn)
    rules(conn, fresh=True)
    return {"listings": n, "name": target}


def move(conn: sqlite3.Connection, kind: str, name: str, frm: str | None, to: str, extra: list[str] | None = None) -> dict:
    """Перенести улицу или ЖК в другой район: правило на будущее + сразу все такие объекты."""
    from .ingest import _reindex, make_search_text
    now = int(time.time())
    to = geo.canonical_district(to) or to
    if kind == "complex":
        k = cx_key(name)
        if not k:
            return {"listings": 0}
        conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('cx_district', ?, ?, ?, 1, ?)
                        ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label, n = n + 1, ts = excluded.ts""",
                     (k, json.dumps({"district": to, "extra": extra or []}, ensure_ascii=False),
                      f"ЖК {name} → {', '.join([to, *(extra or [])])}", now))
        rows = conn.execute("SELECT * FROM listings WHERE is_active = 1 AND cxkey(complex) = ?", (k[3:],)).fetchall()
    else:
        k = street_key(name)
        if not k:
            return {"listings": 0}
        conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('street', ?, ?, ?, 1, ?)
                        ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label, n = n + 1, ts = excluded.ts""",
                     (k, json.dumps({"district": to, "from": None if frm == NONE else frm, "extra": extra or []},
                                    ensure_ascii=False),
                      f"ул. {name} → {', '.join([to, *(extra or [])])}", now))
        rows = [r for r in conn.execute("SELECT * FROM listings WHERE is_active = 1 AND street IS NOT NULL AND complex IS NULL")
                if street_key(r["street"], r["settlement"]) == street_key(name, r["settlement"])]
    n = 0
    for r in rows:
        if frm == NONE and r["district"] is not None:      # «Без района»: назначаем район только пустым
            continue
        if frm and frm != NONE and r["district"] != frm:
            continue
        d = {**dict(r), "district": to}
        d["search_text"] = make_search_text(d)
        conn.execute("UPDATE listings SET district = ?, extra_districts = ?, search_text = ? WHERE id = ?",
                     (to, json.dumps(extra or [], ensure_ascii=False), d["search_text"], r["id"]))
        _reindex(conn, r["id"], d["search_text"])
        n += 1
    conn.commit()
    rules(conn, fresh=True)
    return {"listings": n}


def district_detail(conn: sqlite3.Connection, name: str) -> dict:
    """Улицы и ЖК района (по объектам на сайте) — чтобы переносить их в другой район."""
    where = "is_active = 1 AND (district = ? OR extra_districts LIKE ?)"
    p = (name, f'%"{name}"%')
    streets = [dict(r) for r in conn.execute(
        f"""SELECT street AS name, COUNT(*) AS n FROM listings WHERE {where} AND street IS NOT NULL AND complex IS NULL
            GROUP BY street ORDER BY n DESC, street LIMIT 300""", p)]
    complexes = [dict(r) for r in conn.execute(
        f"""SELECT complex AS name, COUNT(*) AS n FROM listings WHERE {where} AND complex IS NOT NULL
            GROUP BY complex ORDER BY n DESC, complex LIMIT 300""", p)]
    return {"streets": streets, "complexes": complexes}


def directory(conn: sqlite3.Connection, kind: str) -> list[dict]:
    """Справочник для админки: все ЖК или все улицы — в каких районах их объекты и сколько.
    ЖК — из объявлений и из справочника (даже если объявлений пока нет), с районом из правил/справочника."""
    rs = rules(conn)
    out: dict[str, dict] = {}
    if kind == "complex":
        for r in conn.execute("""SELECT complex AS name, district, COUNT(*) AS n FROM listings
                                 WHERE is_active = 1 AND complex IS NOT NULL GROUP BY cxkey(complex), district"""):
            k = geo.complex_key(r["name"])
            e = out.setdefault(k, {"name": r["name"], "count": 0, "districts": {}})
            e["count"] += r["n"]
            if r["district"]:
                e["districts"][r["district"]] = e["districts"].get(r["district"], 0) + r["n"]
        for key, cx in geo._complexes():
            k = geo.complex_key(cx.name)
            e = out.setdefault(k, {"name": cx.name, "count": 0, "districts": {}})
            e.setdefault("kb_district", cx.district)
        for k, e in out.items():
            rule = rs.get(("cx_district", f"cx:{k}"))
            e["rule"] = rule["district"] if rule else None
    else:
        for r in conn.execute("""SELECT street AS name, settlement, district, COUNT(*) AS n FROM listings
                                 WHERE is_active = 1 AND street IS NOT NULL GROUP BY street, settlement, district"""):
            k = street_key(r["name"], r["settlement"])
            e = out.setdefault(k, {"name": r["name"] + (f" ({r['settlement']})" if r["settlement"] else ""),
                                   "street": r["name"], "count": 0, "districts": {}})
            e["count"] += r["n"]
            if r["district"]:
                e["districts"][r["district"]] = e["districts"].get(r["district"], 0) + r["n"]
        for k, e in out.items():
            rule = rs.get(("street", k))
            e["rule"] = rule["district"] if rule else None
            e["kb_district"] = ", ".join(geo.street_districts(e["street"])[:3]) or None
    items = sorted(out.values(), key=lambda e: (-e["count"], e["name"].lower()))
    for e in items:
        e["districts"] = sorted(e["districts"].items(), key=lambda x: -x[1])
    return items


# ─── Справочник: список и карточка района / ЖК / улицы ────────────────────
_LISTING_COLS = """id, title, price, deal, district, extra_districts, complex, street, house, settlement, admin_fixed, source,
                   last_seen"""


def _listings(conn, where: str, params: tuple, limit: int = 150) -> list[dict]:
    out = []
    for r in conn.execute(f"SELECT {_LISTING_COLS} FROM listings WHERE is_active = 1 AND {where} "
                          f"ORDER BY last_seen DESC LIMIT {limit}", params):
        d = dict(r)
        d["extra_districts"] = json.loads(d["extra_districts"] or "[]")
        out.append(d)
    return out


def dir_list(conn: sqlite3.Connection, kind: str) -> list[dict]:
    """Левая колонка справочника. Сверху — «пустые»: без района / без ЖК."""
    if kind == "district":
        counts = dict(conn.execute("""SELECT name, COUNT(*) FROM (
                                          SELECT district AS name FROM listings WHERE is_active = 1 AND district IS NOT NULL
                                          UNION ALL SELECT j.value FROM listings, json_each(listings.extra_districts) j
                                          WHERE is_active = 1) GROUP BY name""").fetchall())
        custom = {r[0] for r in conn.execute("SELECT name FROM custom_districts")}
        empty = conn.execute("SELECT COUNT(*) FROM listings WHERE is_active = 1 AND district IS NULL").fetchone()[0]
        return [{"name": NONE, "title": "Без района", "count": empty, "special": True}] + [
            {"name": n, "title": n, "count": counts.get(n, 0), "custom": n in custom} for n in geo.all_district_names()]
    items = directory(conn, kind)
    out = [{"name": e["street"] if kind == "street" else e["name"], "title": e["name"], "count": e["count"],
            "districts": [d for d, _ in e["districts"]][:3], "rule": e["rule"]} for e in items]
    if kind == "complex":
        empty = conn.execute("""SELECT COUNT(*) FROM listings WHERE is_active = 1 AND complex IS NULL
                                AND type IN ('flat', 'new')""").fetchone()[0]
        out.insert(0, {"name": NONE, "title": "Квартиры без ЖК", "count": empty, "special": True})
    return out


def dir_item(conn: sqlite3.Connection, kind: str, name: str) -> dict:
    """Правая карточка: что сейчас, откуда (справочник, правило) и собранные объекты."""
    rs = rules(conn)
    if kind == "district":
        if name == NONE:
            where, p = "district IS NULL", ()
            det = {"streets": [dict(r) for r in conn.execute(
                       """SELECT street AS name, COUNT(*) AS n FROM listings WHERE is_active = 1 AND district IS NULL
                          AND street IS NOT NULL AND complex IS NULL GROUP BY street ORDER BY n DESC LIMIT 300""")],
                   "complexes": [dict(r) for r in conn.execute(
                       """SELECT complex AS name, COUNT(*) AS n FROM listings WHERE is_active = 1 AND district IS NULL
                          AND complex IS NOT NULL GROUP BY complex ORDER BY n DESC LIMIT 300""")]}
        else:
            where, p = "(district = ? OR extra_districts LIKE ?)", (name, f'%"{name}"%')
            det = district_detail(conn, name)
        return {"kind": kind, "name": name, **det, "listings": _listings(conn, where, p),
                "total": conn.execute(f"SELECT COUNT(*) FROM listings WHERE is_active = 1 AND {where}", p).fetchone()[0]}
    if kind == "complex":
        if name == NONE:
            where, p = "complex IS NULL AND type IN ('flat', 'new')", ()
            info = {}
        else:
            k = geo.complex_key(name)
            where, p = "cxkey(complex) = ?", (k,)
            rec = geo.resolve_complex(name)
            rule = rs.get(("cx_district", f"cx:{k}"))
            alias = rs.get(("cx_alias", f"cx:{k}"))
            info = {"kb_district": rec.district if rec else None, "in_kb": bool(rec),
                    "rule": rule["district"] if rule else None, "alias": alias}
    else:
        k = street_key(name)
        rows = [r["id"] for r in conn.execute("SELECT id, street, settlement FROM listings WHERE is_active = 1 AND street IS NOT NULL")
                if street_key(r["street"]) == k]
        where = f"id IN ({','.join(map(str, rows)) or '0'})"
        p = ()
        rule = rs.get(("street", f"street:|{k[8:]}" if k else ""))
        info = {"kb_district": ", ".join(geo.street_districts(name)) or None, "rule": rule["district"] if rule else None}
    dist = [dict(r) for r in conn.execute(
        f"""SELECT COALESCE(district, '{NONE}') AS name, COUNT(*) AS n FROM listings WHERE is_active = 1 AND {where}
            GROUP BY 1 ORDER BY n DESC""", p)]
    return {"kind": kind, "name": name, **info, "districts": dist, "listings": _listings(conn, where, p),
            "total": sum(d["n"] for d in dist)}


def rename_complex(conn: sqlite3.Connection, old: str, new: str | None) -> dict:
    """«Это ЖК …» (переименовать / объединить с другим ЖК) или «это не ЖК» (new=None) — везде и на будущее."""
    k = cx_key(old)
    if not k:
        return {"listings": 0}
    if new:
        rec = geo.resolve_complex(new)
        new = rec.name if rec else " ".join(new.split())
    conn.execute("""INSERT INTO learned_rules (kind, key, value, label, n, ts) VALUES ('cx_alias', ?, ?, ?, 1, ?)
                    ON CONFLICT(kind, key) DO UPDATE SET value = excluded.value, label = excluded.label, n = n + 1, ts = excluded.ts""",
                 (k, json.dumps({"complex": new}, ensure_ascii=False),
                  f"«{old}» → {('ЖК ' + new) if new else 'это не ЖК'}", int(time.time())))
    conn.commit()
    rules(conn, fresh=True)
    # уже собранные — включая поправленные вручную: админ прямо сказал, что это за ЖК
    from .ingest import _reindex, make_search_text
    n = 0
    for r in conn.execute("SELECT * FROM listings WHERE is_active = 1 AND cxkey(complex) = ?", (k[3:],)).fetchall():
        d = dict(r)
        apply(conn, d)
        d["search_text"] = make_search_text(d)
        conn.execute("UPDATE listings SET complex = ?, district = ?, search_text = ? WHERE id = ?",
                     (d["complex"], d["district"], d["search_text"], r["id"]))
        _reindex(conn, r["id"], d["search_text"])
        n += 1
    conn.commit()
    return {"listings": n, "name": new}


def delete_district(conn: sqlite3.Connection, name: str, to: str | None) -> dict:
    """Удалить район: его объекты, правила и подписки — в другой район (старое имя узнаётся как тот район).
    Без «куда» можно удалить только пустой свой район."""
    if to:
        return rename_district(conn, name, to)
    if conn.execute("SELECT COUNT(*) FROM listings WHERE district = ? OR extra_districts LIKE ?",
                    (name, f'%"{name}"%')).fetchone()[0]:
        raise ValueError("В районе есть объекты — выберите, куда их перенести.")
    if not conn.execute("DELETE FROM custom_districts WHERE name = ?", (name,)).rowcount:
        raise ValueError("Встроенный район без объектов удалить нельзя — его можно слить с другим.")
    conn.commit()
    sync_districts(conn)
    return {"listings": 0}

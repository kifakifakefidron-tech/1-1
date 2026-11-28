"""Группы объявлений одной квартиры для поиска. Исходные объявления не меняем."""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from itertools import combinations
import json
import re
import sqlite3

from . import dedupe, geo, phonegroups, rules
from .branding import PARTNER_NAME
from .textnorm import words


CHAT_PHOTO = re.compile(r"/photos/\d+/([a-f0-9]{16})\.[a-z0-9]+$")
LINK_STATUSES = ("market", "repeat", "linked")
LINK_TYPES = str(LINK_STATUSES)
MERGED_REPEAT = "dup_repeat"
REVIEW_AREA_TOLERANCE_M2 = 2
MATCH_FIELDS = ("id, type, deal, rooms, room_kind, area, land, floor, floors, complex, street, house, "
                "settlement, price, price_doubt, phones, title, last_seen")


def _tokens(raw: str) -> list[str]:
    try:
        values = raw if isinstance(raw, list) else json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _photo_keys(raw: str) -> set[str]:
    return {match[1] for url in _tokens(raw) if (match := CHAT_PHOTO.fullmatch(url))}


def merged_listing(conn, row):
    seen = set()
    while row["hidden_reason"] == MERGED_REPEAT and row["merged_into"] and row["id"] not in seen:
        seen.add(row["id"])
        target = conn.execute("SELECT * FROM listings WHERE id=?", (row["merged_into"],)).fetchone()
        if target is None:
            break
        row = target
    return row


def transferred_photos(conn) -> dict[int, set[str]]:
    """Фото ручного объединения остаются в карточке при обновлении её источником."""
    by_id = defaultdict(set)
    for lid, raw in conn.execute("""SELECT keep_id, undo FROM dup_pairs
                                  WHERE status='repeat' AND drop_id IS NOT NULL AND undo IS NOT NULL"""):
        by_id[lid].update(json.loads(raw).get("photos", []))
    return by_id


def contact_keys(row) -> set[str]:
    return phonegroups.canon_set(_tokens(row["phones"]))


def property_type(value):
    return "flat" if value in ("flat", "new") else value


@lru_cache(maxsize=4096)
def street_key(value):
    from .osmaddr import street_key as address_street_key

    # В карточках встречаются обе формы «40 лет Победы» и «Имени 40-летия Победы».
    return re.sub(r"\b(\d+) летия\b", r"\1 лет", address_street_key(value))


@lru_cache(maxsize=4096)
def house_key(value):
    house = (value or "").casefold()
    house = re.sub(r"(?<=\d)\s*,?\s*(?:корпус|корп\.?|к\.?)\s*(?=\d)", "к", house)
    house = re.sub(r"(?<=\d)\s*(?:строение|стр\.?)\s*(?=\d)", "стр", house)
    return re.sub(r"\s+", "", house)


_ADDRESS_LABEL = re.compile(r"^адрес\s*:\s*", re.IGNORECASE)
_STREET_PREFIX = re.compile(r"^(?:ул(?:ица)?|проспект|пр-кт|пер(?:еулок)?|проезд|бульвар|б-р|шоссе)\b", re.IGNORECASE)
_CORPUS_SUFFIX = re.compile(r"(?<=\d)к\d{1,3}$")
_UNIT_NUMBER = re.compile(r"\b(?:квартира|кв\.?|лот)\s*№\s*(\d{1,5})\b(?![.,/]\d)", re.IGNORECASE)
_BARE_UNIT = re.compile(
    r"(?:^[^\w\n]*|[,;][^\S\r\n]*)(?:кв\.[^\S\r\n]*|(?:кв|квартира)[^\S\r\n]+)(\d{1,5})"
    r"(?=[^\S\r\n]*(?:$|[,;.!](?![^\S\r\n]*\d)))", re.IGNORECASE
)
_DETAIL_LINE = re.compile(
    r"[^\w\n]*(?P<kind>квартира|подъезд|кв\.?)\s*(?:№\s*|\s+)(?P<number>\d{1,5})[^\w\n]*", re.IGNORECASE
)


@lru_cache(maxsize=4096)
def _review_address(street, house, text):
    street, house = street_key(street), house_key(house)
    observed = set()
    for line in text.splitlines():
        line = re.sub(r"^[^\w]+", "", line)
        label = _ADDRESS_LABEL.match(line)
        address = line[label.end():] if label else line
        prefixed = _STREET_PREFIX.match(address)
        known_street = street and words(address).startswith(f"{street} ")
        if not (label or prefixed or known_street):
            continue
        name, number = rules.extract_street(address)
        if not number and not prefixed:
            name, number = rules.extract_street(f"ул. {address}")
        if name and number:
            observed.add((street_key(name), house_key(number)))
    if len(observed) != 1:
        return street, house, len(observed) > 1
    stated_street, stated_house = observed.pop()
    if _different(street, stated_street):
        return street, house, True
    if not house or house == _CORPUS_SUFFIX.sub("", stated_house):
        house = stated_house
    conflict = house != stated_house and stated_house != _CORPUS_SUFFIX.sub("", house)
    return street or stated_street, house, conflict


def _listing_text(row):
    keys = row.keys()
    if "text" in keys:
        return row["text"] or ""
    return (
        (row["fragment"] if "fragment" in keys else None)
        or (row["description"] if "description" in keys else None)
        or ""
    )


def review_address(row):
    """Адрес и признак противоречия между карточкой и исходным текстом."""
    return _review_address(row["street"], row["house"], _listing_text(row))


@lru_cache(maxsize=4096)
def review_text_details(text):
    body, units, entrances = [], set(), set()
    for line in text.splitlines():
        if match := _DETAIL_LINE.fullmatch(line):
            (entrances if match["kind"].casefold() == "подъезд" else units).add(int(match["number"]))
        else:
            units.update(map(int, _UNIT_NUMBER.findall(line)))
            units.update(map(int, _BARE_UNIT.findall(line)))
            body.append(line)
    return "\n".join(body), frozenset(units), frozenset(entrances)


def unit_number(row):
    _, units, _ = review_text_details(_listing_text(row))
    return next(iter(units)) if len(units) == 1 else None


def _unit_details_conflict(a, b):
    _, units_a, entrances_a = review_text_details(_listing_text(a))
    _, units_b, entrances_b = review_text_details(_listing_text(b))
    return len(units_a | units_b) > 1 or len(entrances_a | entrances_b) > 1


def place_keys(row):
    return geo.complex_key(row["complex"]), street_key(row["street"])


def known_price(row):
    return None if row["price_doubt"] else row["price"]


def _different(a, b):
    return bool(a and b and a != b)


def review_area_delta(a, b) -> float | None:
    return round(abs(a - b), 6) if a is not None and b is not None else None


def review_area_close(a, b) -> bool | None:
    delta = review_area_delta(a, b)
    return delta <= REVIEW_AREA_TOLERANCE_M2 if delta is not None else None


def _conflicts(a, b, place_a, place_b, *, for_review: bool = False, houses=None):
    close_area = (review_area_close(a["area"], b["area"]) if for_review
                  else dedupe._close(a["area"], b["area"], 0.03, 2))
    house_a, house_b = houses if houses is not None else (house_key(a["house"]), house_key(b["house"]))
    return (any(_different(x, y) for x, y in zip(place_a, place_b))
             or _different(house_a, house_b)
            or _different(words(a["settlement"]), words(b["settlement"]))
            or _different(a["room_kind"], b["room_kind"])
            or any(a[k] is not None and b[k] is not None and a[k] != b[k]
                   for k in ("rooms", "floor", "floors"))
            or close_area is False
            or dedupe._close(a["land"], b["land"], 0.04, 0.3) is False)


def identity_conflict(a, b) -> bool:
    if a["deal"] != b["deal"] or property_type(a["type"]) != property_type(b["type"]):
        return True
    street_a, house_a, conflict_a = review_address(a)
    street_b, house_b, conflict_b = review_address(b)
    return conflict_a or conflict_b or _unit_details_conflict(a, b) or _conflicts(
        a, b, (geo.complex_key(a["complex"]), street_a), (geo.complex_key(b["complex"]), street_b),
        for_review=True, houses=(house_a, house_b))


def similar_signals(a, b, place_a, place_b):
    """Совпадения для подсказки и ручной проверки. Цена без характеристик не считается."""
    if not any(x and x == y for x, y in zip(place_a, place_b)) or _conflicts(a, b, place_a, place_b, for_review=True):
        return []
    labels = []
    area = review_area_close(a["area"], b["area"]) is True
    floor = a["floor"] is not None and a["floor"] == b["floor"]
    if area:
        labels.append("Близкая площадь")
    if floor:
        labels.append("Тот же этаж")
    if a["floors"] is not None and a["floors"] == b["floors"]:
        labels.append("Та же этажность")
    if len(labels) < 2:
        return []
    if place_a[1] and place_a[1] == place_b[1] and house_key(a["house"]) and house_key(a["house"]) == house_key(b["house"]):
        labels.insert(0, "Тот же адрес")
    elif place_a[0] and place_a[0] == place_b[0]:
        labels.insert(0, "Тот же ЖК")
    if dedupe._close(known_price(a), known_price(b), 0.07, 0):
        labels.append("Близкая цена")
    return labels


def _address_key(row):
    if row["type"] not in ("flat", "new"):
        return None
    house = house_key(row["house"])
    street = street_key(row["street"])
    if not (street and house and row["area"] and row["rooms"] is not None and row["floor"] is not None):
        return None
    return row["deal"], words(row["settlement"]), street, house, row["rooms"], row["floor"], row["area"]


def _photo_address_key(row):
    house, street = house_key(row["house"]), street_key(row["street"])
    if row["type"] in ("flat", "new") and street and house and row["area"] and row["floor"] is not None:
        return row["deal"], words(row["settlement"]), street, house, row["floor"], row["area"]
    return None


def same_unit_details(a, b):
    key = _address_key(a)
    return key is not None and key == _address_key(b) and not _conflicts(a, b, place_keys(a), place_keys(b))


@lru_cache(maxsize=2048)
def _property_text(raw):
    return rules.remove_phones(raw or "")


def _matching_photos(a, b, shared, single_photo, *, exact=False):
    trusted = shared & single_photo
    if not trusted:
        return False
    if exact or a["rooms"] is None or b["rooms"] is None:
        ta, tb = (dedupe.strip_price_notices(_property_text(row["fragment"] or row["description"])) for row in (a, b))
        if min(len(words(ta).split()), len(words(tb).split())) < 12:
            return False
        if words(ta) == words(tb):
            return True
        price_key = dedupe.price_revision_key(ta, a["deal"], known_price(a))
        return price_key is not None and price_key == dedupe.price_revision_key(tb, b["deal"], known_price(b))
    if len(trusted) >= 2:
        return True
    ta = _property_text(a["fragment"] or a["description"])
    tb = _property_text(b["fragment"] or b["description"])
    return min(len(words(ta).split()), len(words(tb).split())) >= 20 and dedupe.text_twin(ta, tb)


def is_site(row) -> bool:
    return row["source"] == "feed" and (row["ext_id"] or "").startswith("feed:")


def agent_key(row):
    from . import applet
    slava, artem = applet.numbers()
    ext = row["ext_id"] or ""
    phones = {rules.normalize_phone(p) or p for p in _tokens(row["phones"])}
    if ext.startswith(applet.STAYL) or (artem and phones == {artem}):
        return ("stayl",)
    if ext.startswith(applet.PREFIX) or (phones and slava and phones <= set(slava)):
        return ("slava",)
    if row["owner_user_id"]:
        return ("account", row["owner_user_id"])
    if phones:
        return ("phones", frozenset(phonegroups.canon_set(phones)))
    return ("listing", row["id"])


def representatives(group, hidden: set[int]) -> dict:
    """Выбираем отдельное предложение каждого агента и свежую версию повтора."""
    versions = {}
    family_keys = defaultdict(set)
    by_agent = {}
    shown = [row for row in group if row["id"] not in hidden]
    keys = {row["id"]: agent_key(row) for row in shown}
    for row in sorted(shown, key=lambda r: (not r.get("kept"), not is_site(r), -r["last_seen"], -r["first_seen"], -r["id"])):
        if row.get("repeat_id") is not None:
            versions.setdefault(row["repeat_id"], row)
            family_keys[row["repeat_id"]].add(keys[row["id"]])
    aliases = {}
    for row in sorted(shown, key=lambda r: not r.get("kept")):
        repeat_id = row.get("repeat_id")
        version = versions.get(repeat_id, row)
        aliases[row["id"]] = ("repeat", repeat_id) if len(family_keys.get(repeat_id, ())) > 1 else keys[version["id"]]
        by_agent.setdefault(aliases[row["id"]], version)
    return {lid: by_agent[key] for lid, key in aliases.items()}


def _connected_ids(edges):
    parents = {}
    members = set()

    def root(lid):
        while lid in parents:
            lid = parents[lid]
        return lid

    for a, b in edges:
        members.update((a, b))
        left, right = sorted((root(a), root(b)))
        if left != right:
            parents[right] = left
    return {lid: root(lid) for lid in members}


def _repeat_ids(decisions):
    return _connected_ids(pair for pair, status in decisions.items() if status == "repeat")


def hidden_ids(conn: sqlite3.Connection, user_id: int | None) -> set[int]:
    if not user_id:
        return set()
    return {row[0] for row in conn.execute("SELECT listing_id FROM hidden_listings WHERE user_id = ?", (user_id,))}


def publication(row) -> tuple[int, int, int]:
    return row["first_seen"], row["catalog_migrated"], row["id"]


def repeat_publications(conn: sqlite3.Connection) -> dict[int, tuple[int, int, int]]:
    """Первые публикации версий одного предложения, в том числе уже снятых."""
    decisions, published = {}, {}
    for a, first_a, moved_a, b, first_b, moved_b in conn.execute("""
        SELECT a.id, a.first_seen, a.catalog_migrated, b.id, b.first_seen, b.catalog_migrated
        FROM dup_pairs p JOIN listings a ON a.id = p.a JOIN listings b ON b.id = p.b
        WHERE p.status = 'repeat' AND a.deal = b.deal
    """):
        decisions[a, b] = "repeat"
        published[a] = first_a, moved_a, a
        published[b] = first_b, moved_b, b
    families = _repeat_ids(decisions)
    first = {}
    for lid, family in families.items():
        first[family] = min(first.get(family, published[lid]), published[lid])
    return {lid: first[family] for lid, family in families.items()}


def agent_publications(group, repeated: dict) -> dict[int, tuple[int, int, int]]:
    """Первая дата каждого предложения внутри общей карточки."""
    reps = representatives(group, set())
    first = {}
    for row in group:
        lid = row["id"]
        rep = reps[lid]["id"]
        date = repeated.get(lid, publication(row))
        first[rep] = min(first.get(rep, date), date)
    return {lid: first[rep["id"]] for lid, rep in reps.items()}


def archived_publications(conn: sqlite3.Connection, lid: int | None = None) -> dict[int, tuple[int, int, int]]:
    """Первая публикация подтверждённого дубля учитывается и после снятия предложения."""
    by_id = {}
    endpoint = " AND (a.id = ? OR b.id = ?)" if lid is not None else ""
    for active_id, first_seen, migrated, old_id in conn.execute(f"""SELECT
        CASE WHEN a.is_active THEN a.id ELSE b.id END,
        CASE WHEN a.is_active THEN b.first_seen ELSE a.first_seen END,
        CASE WHEN a.is_active THEN b.catalog_migrated ELSE a.catalog_migrated END,
        CASE WHEN a.is_active THEN b.id ELSE a.id END
        FROM dup_pairs p JOIN listings a ON a.id = p.a JOIN listings b ON b.id = p.b
        WHERE p.status IN {LINK_TYPES} AND a.deal = b.deal AND a.is_active != b.is_active{endpoint}""",
        (lid, lid) if lid is not None else ()):
        published = first_seen, migrated, old_id
        by_id[active_id] = min(published, by_id.get(active_id, published))
    scope = "AND k.id=?" if lid is not None else ""
    for keep, first_seen, migrated, original in conn.execute(f"""WITH RECURSIVE merged(keep, source, first_seen, migrated) AS (
        SELECT k.id, s.id, s.first_seen, s.catalog_migrated
        FROM listings s JOIN listings k ON k.id=s.merged_into
        WHERE s.hidden_reason=? AND k.is_active=1 {scope}
        UNION ALL
        SELECT m.keep, s.id, s.first_seen, s.catalog_migrated FROM listings s JOIN merged m ON s.merged_into=m.source
        WHERE s.hidden_reason=?
    ) SELECT keep, first_seen, migrated, source FROM merged""",
        (MERGED_REPEAT, lid, MERGED_REPEAT) if lid is not None else (MERGED_REPEAT, MERGED_REPEAT)):
        published = first_seen, migrated, original
        by_id[keep] = min(published, by_id.get(keep, published))
    return by_id


def first_publication(rows, archived: dict) -> tuple[int, int, int]:
    published = min(map(publication, rows))
    return min((published, *(archived[row["id"]] for row in rows if row["id"] in archived)))


def _build_groups(conn: sqlite3.Connection):
    """Автоматические группы требуют попарного совпадения. Для подтверждённых пар допустима связь через общее предложение."""
    stored = conn.execute(f"""
        SELECT a, b, status, user_id, CASE WHEN user_id IS NULL OR user_id=0 THEN
            CASE WHEN json_valid(ai) THEN json_extract(ai,'$.applied') END END AS ai_applied
        FROM dup_pairs WHERE (status IN {LINK_TYPES} OR status IN ('diff','split'))
        AND NOT (status = 'market' AND kind = 'offer_photo' AND COALESCE(user_id,0)=0
                 AND COALESCE(CASE WHEN json_valid(ai) THEN json_extract(ai,'$.applied') END,0)=0)""").fetchall()
    decisions = {(a, b): status for a, b, status, _, _ in stored}
    manual_links = {(a, b) for a, b, status, user_id, _ in stored if status == "market" and (user_id or 0) > 0}
    aliases = dict(conn.execute("SELECT id, merged_into FROM listings WHERE hidden_reason=? AND merged_into IS NOT NULL",
                                (MERGED_REPEAT,)))

    def active_id(lid):
        while lid in aliases:
            lid = aliases[lid]
        return lid

    rows = {r["id"]: r for r in conn.execute(f"""SELECT l.id, l.source, l.type, l.deal, l.settlement, l.street, l.house,
        l.complex, l.rooms, l.room_kind, l.floor, l.floors, l.area, l.land, l.photos, l.phones, l.price, l.price_doubt, l.last_seen,
        l.ext_id, l.owner_user_id, l.district, l.repair, l.price_m2, l.first_seen, l.catalog_migrated, l.fragment, l.description,
        l.id IN (SELECT keep_id FROM dup_pairs WHERE status='repeat' AND drop_id IS NOT NULL) AS kept,
        CASE WHEN l.source = 'own' THEN (SELECT MAX(e.ts) FROM listing_edits e
            WHERE e.listing_id = l.id AND e.field = 'confirm' AND e.new = '"yes"' AND e.reverted = 0) END AS confirmed_at
        FROM listings l WHERE l.is_active = 1 AND (
            (l.type IN ('flat', 'new') AND l.street IS NOT NULL AND l.house IS NOT NULL
             AND CASE WHEN json_valid(l.photos) THEN json_array_length(l.photos) >= 1 ELSE 0 END)
            OR l.id IN (SELECT a FROM dup_pairs WHERE status IN {LINK_TYPES}
                        UNION SELECT b FROM dup_pairs WHERE status IN {LINK_TYPES}))""")}
    for a, b, status, _, ai_applied in stored:
        if status in ("market", "linked") and ai_applied and a in rows and b in rows and identity_conflict(rows[a], rows[b]):
            decisions[a, b] = "diff"
    repeats = _repeat_ids(decisions)
    if aliases:
        # Прямой вердикт по двум оставшимся карточкам важнее любой связи их прежних версий.
        for (a, b), status in sorted(decisions.items(), key=lambda item: item[1] not in ("diff", "split")):
            pair = tuple(sorted((active_id(a), active_id(b))))
            if pair[0] != pair[1] and pair not in decisions:
                decisions[pair] = status
                if (a, b) in manual_links:
                    manual_links.add(pair)
    approved = sorted(((a, b) for (a, b), status in decisions.items() if status in LINK_STATUSES
                       and a in rows and b in rows and rows[a]["deal"] == rows[b]["deal"]),
                      key=lambda pair: (decisions[pair] == "linked", decisions[pair] != "repeat", pair not in manual_links, pair))
    confirmed_roots = _connected_ids(pair for pair in approved if pair in manual_links)
    pairs = set(approved)
    buckets = defaultdict(list)
    photo_addresses = defaultdict(set)
    places = {lid: place_keys(row) for lid, row in rows.items()}
    phonegroups.refresh(conn)
    for lid, row in rows.items():
        key = _photo_address_key(row)
        if key is None:
            continue
        photos = _photo_keys(row["photos"])
        phones = contact_keys(row)
        for photo in photos:
            photo_addresses[photo].add(key)
        if photos and phones:
            buckets[key].append((lid, photos, phones))
    single_photo = {photo for photo, addresses in photo_addresses.items() if len(addresses) == 1}
    for members in buckets.values():
        for (a, photos_a, phones_a), (b, photos_b, phones_b) in combinations(members, 2):
            pair = (min(a, b), max(a, b))
            same_contact = bool(phones_a & phones_b)
            if same_contact and (phones_a != phones_b or agent_key(rows[a]) != agent_key(rows[b])):
                continue
            if pair not in decisions and not identity_conflict(rows[a], rows[b]) \
                    and _matching_photos(rows[a], rows[b], photos_a & photos_b, single_photo, exact=same_contact):
                pairs.add(pair)

    def family(lid):
        return repeats.get(lid, lid)

    market_families = {tuple(sorted((family(a), family(b)))) for a, b in approved if decisions[a, b] == "market"}

    def consistent(a, b, confirmed):
        pair = min(a, b), max(a, b)
        if pair in pairs:
            return True
        if not confirmed or pair in decisions or identity_conflict(rows[a], rows[b]):
            return False
        if a in confirmed_roots and confirmed_roots[a] == confirmed_roots.get(b):
            return True
        if same_unit_details(rows[a], rows[b]):
            return True
        families = tuple(sorted((family(a), family(b))))
        return (a in repeats or b in repeats) and (families[0] == families[1] or families in market_families) \
            and property_type(rows[a]["type"]) == property_type(rows[b]["type"]) \
            and not _conflicts(rows[a], rows[b], places[a], places[b])

    linked = {}
    manual_groups = set()
    for a, b in approved + sorted(pairs.difference(approved)):
        left, right = linked.get(a, (a,)), linked.get(b, (b,))
        status = decisions.get((a, b))
        confirmed = status != "linked" and (status in ("repeat", "market") or left in manual_groups or right in manual_groups)
        if left != right and all(consistent(i, j, confirmed) for i in left for j in right):
            group = tuple(sorted(left + right))
            linked.update(dict.fromkeys(group, group))
            if confirmed:
                manual_groups.add(group)
    auto = {(a, b) for a, b in pairs.difference(approved) if a in linked and linked[a] == linked.get(b)
            and contact_keys(rows[a]).isdisjoint(contact_keys(rows[b]))}
    return rows, set(linked.values()), auto, repeats, confirmed_roots


def photo_matches(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """Связи, которые можно сохранить после снятия одного из предложений."""
    return _build_groups(conn)[2]


def groups(conn: sqlite3.Connection, hide_user: int | None = None) -> dict:
    rows, groups, _, repeats, _ = _build_groups(conn)
    hidden = hidden_ids(conn, hide_user)
    result = {}
    for ids in groups:
        active = [dict(rows[lid], repeat_id=repeats.get(lid)) for lid in ids if lid not in hidden]
        if len(active) < 2:
            continue
        group = tuple(sorted(active, key=lambda r: (not is_site(r), not r["confirmed_at"], -r["last_seen"], -r["id"])))
        result.update(dict.fromkeys((row["id"] for row in group), group))
    return result


def confirmed_group_ids(conn: sqlite3.Connection) -> dict[int, tuple[int, int]]:
    """Общая группа и цепочка ручных связей для уже подтверждённых предложений."""
    _, groups, _, _, confirmed_roots = _build_groups(conn)
    return {lid: (members[0], confirmed_roots[lid]) for members in groups for lid in members if lid in confirmed_roots}


def for_listing(conn: sqlite3.Connection, row: sqlite3.Row, hide_user: int | None = None):
    lid = row["id"]
    if conn.execute(f"SELECT 1 FROM dup_pairs WHERE status IN {LINK_TYPES} AND (a = ? OR b = ?)", (lid, lid)).fetchone():
        return groups(conn, hide_user).get(lid)
    photos = _photo_keys(row["photos"])
    if photos and _photo_address_key(row) is not None:
        shared = " OR ".join("instr(photos, ?) > 0" for _ in photos)
        if conn.execute(f"""SELECT 1 FROM listings WHERE is_active = 1 AND id != ?
            AND type IN ('flat', 'new') AND deal = ? AND floor = ? AND area = ?
            AND street IS NOT NULL AND house IS NOT NULL AND ({shared}) LIMIT 1""",
            (lid, row["deal"], row["floor"], row["area"], *photos)).fetchone():
            return groups(conn, hide_user).get(lid)
    return None


def summary(group, matching: set[int] | None = None) -> dict:
    offers = [{"id": r["id"], "price": known_price(r), "last_seen": r["last_seen"],
               "source_label": PARTNER_NAME if is_site(r) else None,
               "site": is_site(r),
               "confirmed_at": r["confirmed_at"], "matches": matching is None or r["id"] in matching,
               **({"contact_unknown": True} if agent_key(r)[0] == "listing" else {})} for r in group]
    offers.sort(key=lambda offer: (not offer["site"], not offer["matches"], offer.get("contact_unknown", False)))
    prices = [offer["price"] for offer in offers if offer["matches"] and offer["price"] is not None]
    return {"offers": offers, "matching": sum(offer["matches"] for offer in offers),
            "min_price": min(prices, default=None), "price_range": bool(prices and min(prices) != max(prices))}

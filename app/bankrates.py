"""Ставки банков по ипотеке: «почти автоматически» и всегда свежие.

Сайты-агрегаторы (banki.ru, Домклик) и сайт ДОМ.РФ ботов не пускают (у ДОМ.РФ — защита от роботов), обходить это
нельзя. Поэтому так:
  * админ открывает в браузере любой обзор ставок (например, еженедельный «Обзор ставок по ипотеке в банках» ДОМ.РФ
    или страницы банков), выделяет текст или таблицу, вставляет в окно «Ставки банков» — parse() раскладывает
    по банкам и программам (вторичка / новостройка / семейная / ИЖС); DeepSeek помогает с таблицами, если подключён;
  * средние ставки по краю (вторичка, ДДУ) по-прежнему сами приходят из статистики Банка России (widgets.mortgage);
  * ставки старше STALE_DAYS — напоминание админу (уведомление на сайте + письмо), в кабинете видно «обновлено N дн. назад».
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time

from . import db, llm, notices, widgets

log = logging.getLogger(__name__)
STALE_DAYS = 7
REVIEW_URL = "https://xn--h1alcedd.xn--d1aqf.xn--p1ai/news/"   # дом.рф/news — там еженедельный «Обзор ставок по ипотеке в банках»

# как пишут банки в обзорах → короткое имя в таблице
BANKS = [
    (r"сбер", "Сбербанк"), (r"\bвтб\b", "ВТБ"), (r"альфа", "Альфа-Банк"), (r"газпромбанк|\bгпб\b", "Газпромбанк"),
    (r"т-банк|т банк|тинькофф", "Т-Банк"), (r"дом\.?\s?рф", "Банк ДОМ.РФ"), (r"россельхоз|\bрсхб\b", "Россельхозбанк"),
    (r"совкомбанк", "Совкомбанк"), (r"\bпсб\b|промсвязь", "ПСБ"), (r"санкт-петербург|\bбспб\b", "Банк Санкт-Петербург"),
    (r"уралсиб", "Уралсиб"), (r"мтс", "МТС Банк"), (r"абсолют", "Абсолют Банк"), (r"росбанк", "Росбанк"),
    (r"райффайзен", "Райффайзенбанк"), (r"кубань\s*кредит", "Кубань Кредит"), (r"центр-инвест", "Центр-инвест"),
    (r"транскапитал|\bткб\b", "Транскапиталбанк"), (r"металлинвест", "Металлинвестбанк"), (r"\bрнкб\b", "РНКБ"),
    (r"ак\s*барс", "Ак Барс"), (r"зенит", "Банк Зенит"), (r"новиком", "Новикомбанк"), (r"\bмкб\b|московский кредитный", "МКБ"),
    (r"примсоц", "Примсоцбанк"), (r"почта\s*банк", "Почта Банк"), (r"русский стандарт", "Русский Стандарт"),
    (r"open|открытие", "Открытие"), (r"ренессанс", "Ренессанс Банк"), (r"\bбжф\b|жилищного финансирования", "БЖФ"),
]
PROG_WORDS = [("secondary", r"вторич|готов\w* жиль|готов\w* квартир"), ("new", r"новострой|первич|дду|строящ"),
              ("family", r"семейн"), ("ihc", r"ижс|частн\w* дом|индивидуальн\w* дом|строительств\w* дом")]
RATE_RE = re.compile(r"(?<![\d.,])(\d{1,2}[.,]\d{1,2}|\d{1,2})\s*%")
NUM_RE = re.compile(r"(?<![\d.,])(\d{1,2}[.,]\d{1,2})(?![\d])")


def _bank(line: str) -> str | None:
    low = line.lower().replace("ё", "е")
    for pat, name in BANKS:
        if re.search(pat, low):
            return name
    m = re.search(r"(?:банк[а-я]*\s+[«\"]?([А-ЯЁA-Z][\w\-]+(?:\s[А-ЯЁA-Z][\w\-]+)?)|([А-ЯЁA-Z][\w\-]+)\s*[-–]?\s*банк)", line)
    if m:
        return (m.group(1) or m.group(2) or "").strip().strip("«»\"") or None
    return None


def _prog(text: str) -> str | None:
    low = text.lower()
    for key, pat in PROG_WORDS:
        if re.search(pat, low):
            return key
    return None


def _num(s: str) -> float | None:
    try:
        v = float(s.replace(",", "."))
    except ValueError:
        return None
    return v if 1 <= v < 40 else None


# Таблица ДОМ.РФ «Ставки предложения по рыночным продуктам»: Банк | Первичный рынок | Вторичный рынок | Рефинансирование.
# Скопированная со страницы, она бывает строками («Сбербанк  18,7  18,7  21,5») или столбиком (банк, затем по значению
# в строке), с изменениями к прошлой неделе («+0,44») и пометками «не предоставляет», «Для з/п клиентов».
COL_WORDS = [("new", r"первичн|новострой"), ("secondary", r"вторичн|готов\w* жиль"), ("refi", r"рефинанс"),
             ("family", r"семейн"), ("ihc", r"ижс|индивидуальн\w* дом")]
GAP_RE = re.compile(r"не предоставля|для з/?п|зарплатн|нет программ|^[—–-]$", re.I)
CHANGE_RE = re.compile(r"^[\^+−-]\s*\+?\d+[.,]?\d*$|^[↑↓▲▼^]")
SKIP_RE = re.compile(r"средневзвешен|показать детальн|изменение к предыдущ|ставки предложения|^банк$|^п\.п\.?$", re.I)


def parse_table(text: str) -> list[dict]:
    cols: list[str] = []
    rows: list[dict] = []
    cur = None
    for raw in text.replace("\u00a0", " ").splitlines():
        cells = [c.strip() for c in re.split(r"\t|\s{2,}|\|", raw) if c.strip()]
        for cell in cells:
            low = cell.lower()
            if SKIP_RE.search(low):
                if "средневзвешен" in low:   # итоговая строка — конец таблицы, её цифры не банк
                    cur = None
                continue
            hits = [k for k, pat in COL_WORDS if re.search(pat, low)]
            if hits and not re.search(r"\d", cell) and not _bank(cell):   # заголовок колонки
                if not rows:
                    for k in hits:
                        if k not in cols:
                            cols.append(k)
                continue
            if CHANGE_RE.search(cell.replace(" ", "")):   # изменение к прошлой неделе — не ставка
                continue
            if GAP_RE.search(low):
                if cur is not None:
                    cur["vals"].append(None)
                continue
            m = re.fullmatch(r"(\d{1,2}[.,]\d{1,2}|\d{1,2})\s*%?", cell)
            if m:
                if cur is not None:
                    cur["vals"].append(_num(m.group(1)))
                continue
            # текст — название банка (начинает новую строку таблицы)
            if re.search(r"[а-яёa-z]", low) and len(cell) <= 60:
                cur = {"bank": _bank(cell) or cell.strip("«»\"*"), "vals": []}
                rows.append(cur)
    if len(cols) < 2:
        return []
    out = []
    for r in rows:
        row = {"bank": r["bank"]}
        for k, v in zip(cols, r["vals"]):
            if k in widgets.PROGRAMS and v is not None:
                row[k] = v
        if len(row) > 1:
            out.append(row)
    return out


def parse_rules(text: str, hint: str = "secondary") -> list[dict]:
    """Без нейросети: таблица (шапка с программами → колонки по порядку) или фразы «Банк … 15,9%»."""
    rows: dict[str, dict] = {}
    columns: list[str] = []
    context = hint
    for raw in text.replace("\t", "  ").splitlines():
        line = raw.strip()
        if not line or re.search(r"рефинанс", line.lower()):   # рефинансирование в таблицу не берём
            continue
        progs_in_line = [k for k, pat in PROG_WORDS for _ in [0] if re.search(pat, line.lower())]
        bank = _bank(line)
        nums = [_num(x) for x in RATE_RE.findall(line)] or ([_num(x) for x in NUM_RE.findall(line)] if bank else [])
        nums = [n for n in nums if n is not None]
        if not bank and len(progs_in_line) >= 2 and not nums:   # шапка таблицы: «Банк | Новостройка | Вторичка | …»
            order = sorted(((re.search(pat, line.lower()).start(), k) for k, pat in PROG_WORDS if re.search(pat, line.lower())))
            columns = [k for _, k in order]
            continue
        if not bank and progs_in_line and not nums:   # подзаголовок «Вторичное жильё:» — дальше ставки для него
            context = progs_in_line[0]
            continue
        if not bank or not nums:
            continue
        if not (columns and len(nums) >= 2):   # «Банк А 15,9%, Банк Б — 16,9%»: каждая ставка — к банку перед ней
            pieces, start = [], 0
            for m in RATE_RE.finditer(line):
                pieces.append((line[start:m.end()], _num(m.group(1))))
                start = m.end()
            banks_in = [(_bank(seg), v) for seg, v in pieces]
            if len(pieces) >= 2 and len({b for b, _ in banks_in if b}) >= 2:
                prog = progs_in_line[0] if len(progs_in_line) == 1 else context
                for b, v in banks_in:
                    if b and v is not None:
                        r = rows.setdefault(b, {"bank": b})
                        r[prog] = min(v, r.get(prog, 99))
                continue
        row = rows.setdefault(bank, {"bank": bank})
        if columns and len(nums) >= 2:
            for k, v in zip(columns, nums):
                row[k] = v
        elif len(progs_in_line) == 1:
            row[progs_in_line[0]] = min(nums)
        else:
            row[context] = min(nums)   # «от 15,9%» — берём меньшую
    return [r for r in rows.values() if len(r) > 1]


LLM_PROMPT = """Тебе дают текст про ипотечные ставки банков России (обзор, таблица, страница банка).
Верни ТОЛЬКО JSON: {"items": [{"bank": "короткое название банка",
  "secondary": ставка % на вторичное/готовое жильё или null,
  "new": ставка % на новостройку/первичку или null,
  "family": ставка % по семейной ипотеке или null,
  "ihc": ставка % на ИЖС/частный дом или null}]}
Правила: только числа, которые прямо есть в тексте; «от 15,9%» = 15.9; если ставка указана просто «на покупку квартиры»
без уточнения — ставь её и в secondary, и в new. Рефинансирование не нужно. Не выдумывай банки и ставки."""


def parse(text: str, hint: str = "secondary") -> dict:
    """Разобрать вставленный текст. Возвращает {items, how}: how = 'rules' | 'ai' | 'ai+rules'."""
    text = (text or "")[:20000]
    table = parse_table(text)   # таблица ДОМ.РФ и похожие — надёжнее всего
    rules = bankrates_merge_rules(table, parse_rules(text, hint)) if table else parse_rules(text, hint)
    if not llm.available():
        return {"items": rules, "how": "rules"}
    try:
        res = llm.ask_json(LLM_PROMPT, text)
        ai = []
        for x in res.get("items") or []:
            row = {"bank": str(x.get("bank") or "").strip()[:40]}
            for k in widgets.PROGRAMS:
                v = widgets._rate(x.get(k)) if x.get(k) is not None else None
                if v is not None:
                    row[k] = v
            if row["bank"] and len(row) > 1:
                ai.append(row)
    except llm.LLMError as e:
        log.warning("ставки банков: нейросеть не помогла: %s", e)
        return {"items": rules, "how": "rules"}
    if not ai:
        return {"items": rules, "how": "rules"}
    # нейросеть + правила: что правила нашли, а нейросеть пропустила — добавить
    names = {r["bank"].lower() for r in ai}
    extra = [r for r in rules if r["bank"].lower() not in names]
    return {"items": ai + extra, "how": "ai+rules" if extra else "ai"}


def bankrates_merge_rules(table: list[dict], phrases: list[dict]) -> list[dict]:
    """Таблица главнее; из фраз — только банки, которых в таблице нет."""
    have = {r["bank"].lower() for r in table}
    return table + [r for r in phrases if r["bank"].lower() not in have]


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """Новые ставки поверх прежних: обновлённые банки и программы заменяются, остальное остаётся как было."""
    by = {r["bank"].lower(): dict(r) for r in old}
    for r in new:
        cur = by.setdefault(r["bank"].lower(), {"bank": r["bank"]})
        cur.update({k: v for k, v in r.items() if k != "bank"})
    return list(by.values())


def remind(conn: sqlite3.Connection, now: float | None = None) -> bool:
    """Ставки устарели (старше STALE_DAYS) — раз в неделю напомнить админам на сайте (и письмом через уведомления)."""
    now = now or time.time()
    ts = widgets.banks(conn).get("ts") or 0
    if ts and now - ts < STALE_DAYS * 86400:
        return False
    if now - float(db.get_state(conn, "banks_remind_at") or 0) < 6 * 86400:
        return False
    db.set_state(conn, "banks_remind_at", str(int(now)))
    days = int((now - ts) / 86400) if ts else None
    text = (f"Ставкам банков в кабинете уже {days} дн." if days else "Ставки банков в кабинете ещё не заполнены") + \
        " — откройте свежий обзор ставок (например, ДОМ.РФ), скопируйте текст и вставьте в «Ставки банков» в кабинете."
    for (uid,) in conn.execute("SELECT id FROM users WHERE is_admin = 1"):
        notices.add(conn, uid, "sub", "Пора обновить ставки банков", text, url="/cabinet?banks=1", dedup_s=5 * 86400)
    conn.commit()
    return True

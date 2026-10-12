"""Данные для кабинета риелтора: погода, ключевая ставка и курсы ЦБ, ставки по ипотеке.

Источники бесплатные и официальные, все — с кэшем в таблице state (сайт не ждёт чужие серверы на каждом заходе,
а при сбое показывает последние данные с пометкой «обновлено …»):
  * погода — MET Norway Locationforecast (бесплатно, в т. ч. для коммерции, подпись «MET Norway», CC BY 4.0), 30 мин;
  * ключевая ставка — ЦБ РФ, веб-сервис DailyInfo (KeyRateXML), 3 ч;
  * курсы USD / EUR / CNY — ЦБ РФ, XML_daily, 3 ч;
  * средние ставки по ипотеке в Краснодарском крае — статистика ЦБ (xlsx, раз в месяц, кэш сутки):
    все ипотечные кредиты и кредиты по ДДУ (новостройки); вторичка — расчёт: (ставка·объём всех − ставка·объём ДДУ) /
    (объём всех − объём ДДУ). Это средние по всем выданным кредитам, включая льготные программы;
  * ставки банков — таблицу ведёт админ (готового бесплатного источника ставок банков нет).
"""
from __future__ import annotations

import io
import json
import logging
import re
import sqlite3
import time
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

from . import db

log = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))
UA = "1plus1assistant.ru/1.0 (+https://1plus1assistant.ru)"
LAT, LON = 45.04, 38.98   # Краснодар
REGION = "Краснодарский край"
CBR_STAT = "https://www.cbr.ru/vfs/statistics/BankSector/Mortgage/"


def _http(url: str, data: bytes | None = None, headers: dict | None = None, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


_FAIL_PAUSE = 600   # источник не ответил — следующая попытка не раньше чем через 10 минут


def _cached(conn: sqlite3.Connection, key: str, ttl: int, fetch, force: bool = False) -> dict | None:
    """Свежие данные из кэша; устарели — загрузить заново; сеть подвела — отдать старые.

    Сайт (кабинет) в сеть НЕ ходит — только читает сохранённое: загружает worker (refresh).
    Иначе при недоступном источнике каждый посетитель ждал бы его по 15 секунд."""
    raw = db.get_state(conn, f"w:{key}")
    old = json.loads(raw) if raw else None
    if not _online.get("on") and not force:
        return old
    if old and not force and time.time() - old["ts"] < ttl:
        return old
    if not force and time.time() - float(db.get_state(conn, f"w:{key}:fail") or 0) < _FAIL_PAUSE:
        return old
    try:
        db.flush(conn)   # не держать базу, пока ждём чужой сервер
        data = fetch()
    except Exception as e:  # noqa: BLE001 — источник недоступен: показываем прошлое
        log.warning("виджет %s: %s", key, e)
        db.set_state(conn, f"w:{key}:fail", str(int(time.time())))
        return old
    if data is None:
        return old
    new = {"ts": int(time.time()), "data": data}
    db.set_state(conn, f"w:{key}", json.dumps(new, ensure_ascii=False))
    return new


# ─── погода ───────────────────────────────────────────────────────────────
_SYMBOL = {   # код MET Norway (без _day/_night) → (подпись, иконка)
    "clearsky": ("Ясно", "sun"), "fair": ("Малооблачно", "cloud-sun"), "partlycloudy": ("Переменная облачность", "cloud-sun"),
    "cloudy": ("Облачно", "cloud"), "fog": ("Туман", "fog"),
    "lightrain": ("Небольшой дождь", "rain"), "rain": ("Дождь", "rain"), "heavyrain": ("Ливень", "rain"),
    "lightrainshowers": ("Кратковременный дождь", "rain"), "rainshowers": ("Ливни", "rain"), "heavyrainshowers": ("Сильные ливни", "rain"),
    "lightsleet": ("Мокрый снег", "snow"), "sleet": ("Мокрый снег", "snow"), "heavysleet": ("Мокрый снег", "snow"),
    "lightsnow": ("Небольшой снег", "snow"), "snow": ("Снег", "snow"), "heavysnow": ("Снегопад", "snow"),
    "lightsnowshowers": ("Снег", "snow"), "snowshowers": ("Снег", "snow"), "sleetshowers": ("Мокрый снег", "snow"),
}


def _sym(code: str | None, night: bool = False) -> dict:
    base = re.sub(r"_(day|night|polartwilight)$", "", code or "")
    if "thunder" in base:
        label, icon = "Гроза", "storm"
    else:
        label, icon = _SYMBOL.get(base, ("", "cloud"))
    if night and icon in ("sun", "cloud-sun"):
        icon = "moon"
    return {"label": label, "icon": icon}


def _weather_fetch() -> dict:
    j = json.loads(_http(f"https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}"))
    ts = j["properties"]["timeseries"]
    now = ts[0]
    det = now["data"]["instant"]["details"]
    code = (now["data"].get("next_1_hours") or now["data"].get("next_6_hours") or {}).get("summary", {}).get("symbol_code")
    current = {"temp": round(det["air_temperature"]), "wind": round(det.get("wind_speed", 0)),
               "humidity": round(det.get("relative_humidity", 0)), **_sym(code, (code or "").endswith("_night"))}
    days: dict[str, dict] = {}
    for t in ts:
        local = datetime.fromisoformat(t["time"].replace("Z", "+00:00")).astimezone(MSK)
        key = local.strftime("%Y-%m-%d")
        d = days.setdefault(key, {"date": key, "min": 99, "max": -99, "code": None, "dist": 99, "rain": 0.0})
        temp = t["data"]["instant"]["details"]["air_temperature"]
        d["min"], d["max"] = min(d["min"], temp), max(d["max"], temp)
        six = t["data"].get("next_6_hours") or {}
        # «погода дня» — ближе всего к 13:00 (дальние дни идут шагом 6 часов)
        if six.get("summary") and abs(local.hour - 13) < d["dist"]:
            d["code"], d["dist"] = six["summary"]["symbol_code"], abs(local.hour - 13)
        if six and local.hour in (3, 9, 15, 21):   # осадки — по шестичасовым отрезкам, без двойного счёта
            d["rain"] += float((six.get("details") or {}).get("precipitation_amount", 0) or 0)
    out = []
    for d in list(days.values())[:6]:
        if d["code"] is None:
            continue
        out.append({"date": d["date"], "min": round(d["min"]), "max": round(d["max"]), "rain": round(d["rain"], 1),
                    **_sym(d["code"])})
    return {"current": current, "days": out[:5], "source": "MET Norway"}


def weather(conn: sqlite3.Connection, force: bool = False) -> dict | None:
    return _cached(conn, "weather", 1800, _weather_fetch, force)


# ─── ЦБ: ключевая ставка и курсы ─────────────────────────────────────────
def _keyrate_fetch() -> dict:
    today = datetime.now(MSK).date()
    body = f"""<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
<soap:Body><KeyRateXML xmlns="http://web.cbr.ru/"><fromDate>{today - timedelta(days=400)}</fromDate><ToDate>{today}</ToDate>
</KeyRateXML></soap:Body></soap:Envelope>"""
    x = _http("https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx", body.encode(),
              {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "http://web.cbr.ru/KeyRateXML"}).decode()
    pts = [(d[:10], float(r)) for d, r in re.findall(r"<DT>([^<]+)</DT><Rate>([\d.]+)</Rate>", x)]
    if not pts:
        return None
    pts.sort()
    rate = pts[-1][1]
    since, prev = pts[-1][0], None
    for d, r in reversed(pts):   # когда ставка стала такой и какой была до этого
        if r != rate:
            prev = r
            break
        since = d
    return {"rate": rate, "since": since, "prev": prev,
            "history": [{"date": d, "rate": r} for d, r in pts[::7]][-52:] + [{"date": pts[-1][0], "rate": rate}]}


def _rates_day(date: str | None = None) -> dict:
    url = "https://www.cbr.ru/scripts/XML_daily.asp" + (f"?date_req={date}" if date else "")
    x = _http(url).decode("cp1251")
    out = {}
    for code, nominal, value in re.findall(r"<CharCode>(\w+)</CharCode><Nominal>(\d+)</Nominal><Name>[^<]*</Name>"
                                           r"<Value>([\d,]+)</Value>", x):
        out[code] = float(value.replace(",", ".")) / int(nominal)
    m = re.search(r'Date="([\d.]+)"', x)
    return {"date": m.group(1) if m else "", "rates": out}


def _currency_fetch() -> dict:
    now = _rates_day()
    d = datetime.strptime(now["date"], "%d.%m.%Y") - timedelta(days=1) if now["date"] else datetime.now() - timedelta(days=1)
    prev = _rates_day(d.strftime("%d/%m/%Y"))
    out = []
    for c in ("USD", "EUR", "CNY"):
        v, p = now["rates"].get(c), prev["rates"].get(c)
        if v:
            out.append({"code": c, "value": round(v, 2), "change": round(v - p, 2) if p else None})
    return {"date": now["date"], "items": out}


def keyrate(conn: sqlite3.Connection, force: bool = False) -> dict | None:
    return _cached(conn, "keyrate", 3 * 3600, _keyrate_fetch, force)


def currency(conn: sqlite3.Connection, force: bool = False) -> dict | None:
    return _cached(conn, "currency", 3 * 3600, _currency_fetch, force)


# ─── ЦБ: средние ставки по ипотеке в регионе ─────────────────────────────
def _xlsx_sheet(raw: bytes, index: int) -> list[list[str]]:
    """Лист xlsx без сторонних библиотек: строки как списки текстов."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        x = z.read("xl/sharedStrings.xml").decode()
        ss = [re.sub(r"<[^>]+>", "", si) for si in re.findall(r"<si>(.*?)</si>", x, re.S)]
    x = z.read(f"xl/worksheets/sheet{index}.xml").decode()
    rows = []
    for r in re.findall(r"<row[^>]*>(.*?)</row>", x, re.S):
        cells = []
        for attrs, inner in re.findall(r"<c ([^>]*?)(?:/>|>(.*?)</c>)", r, re.S):
            v = re.search(r"<v>(.*?)</v>", inner or "")
            val = v.group(1) if v else ""
            if 't="s"' in attrs and val:
                val = ss[int(val)]
            cells.append(val)
        rows.append(cells)
    return rows


def _region_last(rows: list[list[str]]) -> tuple[str, float] | None:
    head = rows[1] if len(rows) > 1 else []
    for r in rows:
        if r and REGION.lower() in r[0].lower():
            for i in range(len(r) - 1, 0, -1):
                try:
                    v = float(r[i].replace(",", "."))
                except ValueError:
                    continue
                if v:
                    return (head[i] if i < len(head) else ""), v
    return None


def _mortgage_fetch() -> dict:
    rate_all = _region_last(_xlsx_sheet(_http(CBR_STAT + "02_13_Rates_mortgage.xlsx", timeout=40), 3))
    rate_ddu = _region_last(_xlsx_sheet(_http(CBR_STAT + "02_17_Rates_scpa_mortgage.xlsx", timeout=40), 3))
    vol_all = _region_last(_xlsx_sheet(_http(CBR_STAT + "02_11_New_loans_mortgage.xlsx", timeout=40), 1))
    vol_ddu = _region_last(_xlsx_sheet(_http(CBR_STAT + "02_16_New_loans_scpa_mortgage.xlsx", timeout=40), 1))
    if not (rate_all and rate_ddu):
        return None
    out = {"month": rate_all[0], "all": round(rate_all[1], 2), "new": round(rate_ddu[1], 2), "secondary": None,
           "region": REGION, "source": "ЦБ РФ"}
    if vol_all and vol_ddu and vol_all[0] == vol_ddu[0] == rate_all[0] and vol_all[1] > vol_ddu[1] > 0:
        sec = (rate_all[1] * vol_all[1] - rate_ddu[1] * vol_ddu[1]) / (vol_all[1] - vol_ddu[1])
        out["secondary"] = round(sec, 2)
    return out


def mortgage(conn: sqlite3.Connection, force: bool = False) -> dict | None:
    return _cached(conn, "mortgage", 86400, _mortgage_fetch, force)


# ─── ставки банков (ведёт админ): строка = банк, колонки = программы ────────
PROGRAMS = {"secondary": "Вторичка", "new": "Новостройка", "family": "Семейная", "ihc": "ИЖС / дом"}


def _rate(v) -> float | None:
    try:
        x = round(float(str(v).replace(",", ".").replace("%", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    return x if 0 < x < 50 else None


def banks(conn: sqlite3.Connection) -> dict:
    raw = db.get_state(conn, "bank_rates")
    data = json.loads(raw) if raw else {"ts": None, "items": []}
    items = []
    for x in data.get("items", []):   # старый формат {bank, program, rate} → колонки
        if "rate" in x and not any(k in x for k in PROGRAMS):
            prog = (x.get("program") or "").lower()
            key = "new" if "нов" in prog else "family" if "сем" in prog else "ihc" if ("ижс" in prog or "дом" in prog) else "secondary"
            x = {"bank": x["bank"], key: x["rate"]}
        items.append(x)
    return {"ts": data.get("ts"), "items": items, "programs": PROGRAMS, "avg": data.get("avg")}


def set_banks(conn: sqlite3.Connection, items: list, avg: dict | None = None) -> dict:
    clean = []
    for x in items[:40]:
        bank = str(x.get("bank") or "").strip()[:40]
        row = {"bank": bank}
        for k in PROGRAMS:
            r = _rate(x.get(k))
            if r is not None:
                row[k] = r
        if bank and len(row) > 1:
            clean.append(row)
    clean.sort(key=lambda r: (r.get("secondary") is None, r.get("secondary") or 99))   # дешёвая вторичка — выше
    prev = json.loads(db.get_state(conn, "bank_rates") or "{}")
    # средневзвешенная по рынку (из таблицы ДОМ.РФ): новая — если прислали, иначе прежняя
    clean_avg = {k: r for k in PROGRAMS if (r := _rate((avg or {}).get(k))) is not None}
    data = {"ts": int(time.time()), "items": clean,
            "avg": {**clean_avg, "ts": int(time.time())} if clean_avg else prev.get("avg")}
    db.set_state(conn, "bank_rates", json.dumps(data, ensure_ascii=False))
    return {**data, "programs": PROGRAMS}


def all_widgets(conn: sqlite3.Connection) -> dict:
    """Всё для кабинета; каждое — {ts, data} или None (источник ещё ни разу не ответил)."""
    return {"weather": weather(conn), "keyrate": keyrate(conn), "currency": currency(conn),
            "mortgage": mortgage(conn), "banks": banks(conn)}


_online: dict = {"on": False}


def refresh(conn: sqlite3.Connection) -> None:
    """Из worker: обновить устаревшее заранее, чтобы кабинет открывался мгновенно."""
    _online["on"] = True
    try:
        weather(conn)
        keyrate(conn)
        currency(conn)
        mortgage(conn)
    finally:
        _online["on"] = False

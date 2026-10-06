"""Нагрузочная проверка: N «пользователей» одновременно ищут, листают, открывают объекты и карту.

Запуск (с сервера или с любого компьютера):
    python scripts/loadtest.py https://31-130-132-118.sslip.io 50 60
        адрес сайта, сколько одновременных пользователей, сколько секунд
Каждый «пользователь» делает запрос, ждёт 1–3 с (как живой человек) и делает следующий.
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import threading
import time
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8090"
USERS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
SECONDS = int(sys.argv[3]) if len(sys.argv) > 3 else 30
THINK = (1.0, 3.0) if "--fast" not in sys.argv else (0.0, 0.05)

QUERIES = ["", "2к", "фмр", "студия", "мозаика", "евро2", "дом", "участок", "1к юмр", "трешка центр"]
lock = threading.Lock()
times: dict[str, list[float]] = {}
errors: list[str] = []
ids: list[int] = []


def get(kind: str, path: str):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"User-Agent": "loadtest"}), timeout=30) as r:
            body = r.read()
        ok = True
    except Exception as e:  # noqa: BLE001
        body, ok = b"", False
        with lock:
            errors.append(f"{kind}: {e}")
    with lock:
        times.setdefault(kind, []).append(time.perf_counter() - t0)
    return json.loads(body) if ok and body[:1] in (b"{", b"[") else None


def user(stop: float) -> None:
    while time.time() < stop:
        r = random.random()
        params = {"q": random.choice(QUERIES), "deal": random.choice(["sale", "sale", "rent"]),
                  "page": random.choice([1, 1, 1, 2, 3])}
        if random.random() < 0.4:
            params["rooms"] = random.choice(["1", "2", "0,1", "3"])
        qs = urllib.parse.urlencode(params)
        if r < 0.45:
            res = get("список", f"/api/listings?{qs}")
            if res and res.get("items"):
                with lock:
                    ids.extend(i["id"] for i in res["items"][:5])
        elif r < 0.60:
            get("фильтры", f"/api/facets?{qs}")
        elif r < 0.90 and ids:
            get("объект", f"/api/listings/{random.choice(ids)}")
        else:
            get("карта", f"/api/map?{qs}")
        time.sleep(random.uniform(*THINK))


def main() -> None:
    print(f"{BASE}: {USERS} пользователей одновременно, {SECONDS} с…")
    stop = time.time() + SECONDS
    th = [threading.Thread(target=user, args=(stop,), daemon=True) for _ in range(USERS)]
    for t in th:
        t.start()
        time.sleep(0.02)
    for t in th:
        t.join()
    total = sum(len(v) for v in times.values())
    print(f"\nЗапросов: {total} ({total / SECONDS:.1f} в секунду), ошибок: {len(errors)}")
    print(f"{'что':<10}{'кол-во':>8}{'обычно':>10}{'95% быстрее':>14}{'худший':>9}")
    for k, v in sorted(times.items()):
        v.sort()
        p95 = v[int(len(v) * 0.95) - 1] if len(v) > 1 else v[0]
        print(f"{k:<10}{len(v):>8}{statistics.median(v):>9.2f}с{p95:>13.2f}с{v[-1]:>8.2f}с")
    for e in errors[:10]:
        print("  ошибка:", e)


if __name__ == "__main__":
    main()

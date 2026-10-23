"""Цена из поля ввода — одно правило для всего сайта (24.10, Артём: «многие будут писать 5000 или 5000000»).

Продажа: до 1000 — миллионы («5», «6,5»), от 1000 до 100 000 — тысячи («5000» = 5 млн), больше — рубли («5000000»).
Аренда: до 1000 — тысячи в месяц («40»), больше — рубли («40000»). То же правило — в web/pickers.js (window.moneyRub)."""
from __future__ import annotations

import re


def to_rub(v, deal: str | None = "sale") -> int | None:
    s = re.sub(r"[^\d,.]", "", str(v if v is not None else "")).replace(",", ".")
    if s.count(".") > 1:   # «5.000.000» — точки как разделители тысяч
        s = s.replace(".", "")
    try:
        x = float(s)
    except ValueError:
        return None
    if x <= 0:
        return None
    if deal == "rent":
        x = x * 1000 if x < 1000 else x
    else:
        x = x * 1e6 if x < 1000 else x * 1000 if x < 100_000 else x
    return int(round(x))

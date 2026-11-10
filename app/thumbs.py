"""10.11 (Артём: «долго грузит, ускорить, ничего не удаляя»): маленькие копии фото для карточек.

Фото из чатов приходят как есть — 200–450 КБ каждое, а в ленте карточка шириной 300 px. Здесь — копия 640 px
(`<хеш>-s.jpg` рядом с оригиналом, ~40–70 КБ). Оригинал остаётся: окно объекта и просмотр фото берут его.
Делает worker (`run`, понемногу за цикл); сайт отдаёт ссылку на копию, только если файл уже есть (`url`).
Нет Pillow — ничего не делаем, карточки показывают оригинал, как раньше."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from . import config

SIZE = 640
QUALITY = 78
_NAME = re.compile(r"^[0-9a-f]{16}\.(?:jpg|png|webp)$")
_URL = re.compile(r"^/photos/([0-9]{1,10}|pk)/([0-9a-f]{16})\.(?:jpg|png|webp)$")


def thumb_name(name: str) -> str:
    return name.rsplit(".", 1)[0] + "-s.jpg"


def _exists(folder: str, name: str) -> bool:
    return os.path.isfile(os.path.join(config.PHOTOS_DIR, folder, name))


_have: set[tuple[str, str]] = set()   # «копия есть» запоминаем (пока жив оригинал, копия не пропадает)
_miss: dict[tuple[str, str], float] = {}   # «копии нет» — проверяем снова через минуту (worker мог уже сделать)


def _known(folder: str, name: str) -> bool:
    k = (folder, name)
    if k in _have:
        return True
    now = time.monotonic()
    if now - _miss.get(k, -1e9) < 60:
        return False
    if _exists(folder, name):
        _have.add(k)
        _miss.pop(k, None)
        return True
    if len(_miss) > 50000:
        _miss.clear()
    _miss[k] = now
    return False


def url(u: str) -> str:
    """Ссылка на маленькую копию, если она уже сделана; иначе — исходная ссылка."""
    m = _URL.match(u or "")
    if not m:
        return u
    name = m.group(2) + "-s.jpg"
    if _known(m.group(1), name):
        return f"/photos/{m.group(1)}/{name}"
    return u


def make(src: Path) -> bool:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return False
    dst = src.with_name(thumb_name(src.name))
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.thumbnail((SIZE, SIZE))
            tmp = dst.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        os.replace(tmp, dst)
        return True
    except Exception:  # noqa: BLE001 — битый файл: пусть карточка показывает оригинал
        return False


def drop(folder: str, name: str) -> None:
    """Оригинал удалили — удалить и копию."""
    try:
        os.remove(os.path.join(config.PHOTOS_DIR, folder, thumb_name(name)))
    except OSError:
        pass
    _have.discard((folder, thumb_name(name)))


_bad: set[str] = set()       # файлы, из которых копия не получилась (битые) — не мучаем каждый цикл
_next = {"t": 0.0}           # полный обход папок — не чаще раза в 10 минут, если в прошлый раз всё успели


def run(budget_s: float = 3.0) -> int:
    """Сделать недостающие копии (не дольше budget_s за раз). Возвращает, сколько сделано."""
    if time.monotonic() < _next["t"]:
        return 0
    try:
        import PIL  # noqa: F401
    except ImportError:
        return 0
    root = Path(config.PHOTOS_DIR)
    if not root.is_dir():
        return 0
    t0, done = time.monotonic(), 0
    for d in sorted(root.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else 0, reverse=True):   # новые объекты — первыми
        if not d.is_dir() or not (d.name.isdigit() or d.name == "pk"):
            continue
        try:
            names = set(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if _NAME.match(n) and thumb_name(n) not in names and str(d / n) not in _bad:
                if make(d / n):
                    done += 1
                else:
                    _bad.add(str(d / n))
                if time.monotonic() - t0 > budget_s:
                    return done   # не успели — продолжим в следующем цикле
    _next["t"] = time.monotonic() + 600
    return done

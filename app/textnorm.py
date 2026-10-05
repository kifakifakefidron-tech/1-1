"""Нормализация текста: нижний регистр, ё→е, единые пробелы и тире."""
from __future__ import annotations

import re
import unicodedata

_SPACES = re.compile(r"\s+")
_DASHES = re.compile(r"[‐-―−]")
_NON_WORD = re.compile(r"[^0-9a-zа-я]+")


def norm(text: str | None) -> str:
    """«ЖК  Мозаика — Ёлка» → «жк мозаика - елка»."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).casefold().replace("ё", "е")
    t = _DASHES.sub("-", t)
    return _SPACES.sub(" ", t).strip()


def words(text: str | None) -> str:
    """Только буквы/цифры через одиночный пробел: удобно для поиска по границам слов."""
    return _SPACES.sub(" ", _NON_WORD.sub(" ", norm(text))).strip()


def contains_phrase(haystack_words: str, phrase: str) -> bool:
    """Есть ли фраза в тексте целыми словами (оба аргумента — результат words())."""
    if not phrase:
        return False
    return f" {phrase} " in f" {haystack_words} "

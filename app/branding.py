"""Название агентства в интерфейсе и текстах объявлений."""
from __future__ import annotations

import re

PARTNER_NAME = "СТРЕЛЫ"
_LEGACY_NAME = re.compile(r"(?<![\w./@-])stayl(?![\w/@-]|\.\w)", re.IGNORECASE)


def partner_text(text: str | None) -> str | None:
    """Заменяет прежнее название агентства в тексте для отображения."""
    return _LEGACY_NAME.sub(PARTNER_NAME, text) if text else text

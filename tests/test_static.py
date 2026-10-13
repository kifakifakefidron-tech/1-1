"""Скрипты страниц: перенос строки внутри "…" или '…' ломает весь файл (так 13.10 перестала открываться /clients)."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def test_no_raw_newline_inside_quotes():
    bad = []
    for f in WEB.glob("*.js"):
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r'''[(,=+:]\s*(["'])$''', line.rstrip()):
                bad.append(f"{f.name}:{i}: {line.strip()[:80]}")
    assert not bad, bad

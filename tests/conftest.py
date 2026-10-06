import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Тесты не должны ходить в DeepSeek, Wappi и Nominatim, даже если на машине есть .env
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["WAPPI_TOKEN"] = ""
os.environ["GEOCODER"] = "off"

from app import db, ingest  # noqa: E402

NOW = int(time.time())


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def add(conn):
    """Добавить сообщение и разобрать его правилами. Возвращает результат process_message."""
    counter = iter(range(10_000))

    def _add(text: str, ts: int | None = None, chat: str = "Чат риелторов", sender: str = "Агент"):
        n = next(counter)
        mid = ingest.add_message(conn, source="wa", text=text, ts=ts or NOW - 1000 + n,
                                 chat_id=f"chat{n}", chat_name=chat, msg_id=f"m{n}", sender_name=sender)
        assert mid is not None
        return ingest.process_message(conn, mid, use_llm=False)

    return _add


@pytest.fixture(autouse=True)
def _fresh_learning_cache():
    """Кэш выученных правил — на процесс; в тестах у каждой проверки своя база."""
    from app import geo, learning
    learning._cache.update(t=0.0, rules={})
    geo.set_custom_districts([])
    yield
    learning._cache.update(t=0.0, rules={})
    geo.set_custom_districts([])

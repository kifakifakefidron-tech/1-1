"""Пересобрать все объекты заново новым разбором (после улучшения правил/нейросети).

Сообщения остаются — удаляются только объекты из чатов (фид и объекты агентов
остаются), и все сообщения снова ставятся
в очередь. Фоновый цикл (worker) разберёт их сам: сначала свежие, повторы одного
текста — без нейросети.

Запуск на сервере (worker на время останавливаем):
    docker compose stop worker
    docker compose run --rm worker python -m app.reprocess
    docker compose start worker
"""
from __future__ import annotations

from . import db


def main() -> None:
    conn = db.connect()
    # Пересобираем только объекты из чатов: фид СТРЕЛ и объекты, которыми управляют агенты, не трогаем
    chat = "SELECT id FROM listings WHERE source = 'chat' AND owner_user_id IS NULL"
    before = conn.execute(f"SELECT COUNT(*) FROM ({chat})").fetchone()[0]
    conn.execute(f"DELETE FROM listings_fts WHERE rowid IN ({chat})")
    conn.execute(f"DELETE FROM listing_events WHERE listing_id IN ({chat})")
    conn.execute(f"DELETE FROM listings WHERE id IN ({chat})")
    cur = conn.execute("UPDATE messages SET status='new', kind=NULL, objects=0, error=NULL "
                       "WHERE status IN ('done', 'error')")
    conn.commit()
    print(f"Удалено объектов: {before}. Сообщений снова в очереди: {cur.rowcount}.")
    print("Запустите worker — он разберёт очередь заново (сначала свежие).")


if __name__ == "__main__":
    main()

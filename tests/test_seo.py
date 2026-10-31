"""Акт приёмки 14.10: SEO-минимум — robots, sitemap, favicon, описание и превью, разметка объекта, страница 404."""
import time

import pytest
from starlette.testclient import TestClient

from app import config, db, ingest, server


@pytest.fixture
def c(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(db._local, "conn", None, raising=False)
    conn = db.get()
    mid = ingest.add_message(conn, source="wa", text="2-к квартира 50 м², 3/9 эт., ФМР, ул. Тургенева 10. 6 млн. 89180000901",
                             chat_id="c", msg_id="seo1", ts=int(time.time()))
    lid = ingest.process_message(conn, mid, use_llm=False)["results"][0]["listing_id"]
    with TestClient(server.app) as client:
        yield client, lid
    conn.close()
    db._local.conn = None


def test_seo_basics(c):
    client, lid = c
    r = client.get("/robots.txt")
    assert r.status_code == 200 and "Sitemap:" in r.text and "Disallow: /api/" in r.text
    r = client.get("/sitemap.xml")
    # карточки объектов (данные агентов из чатов) — не в карту сайта и закрыты от индекса (17.10, 152-ФЗ)
    assert r.status_code == 200 and f"/?open={lid}" not in r.text and "/offer" in r.text and "<urlset" in r.text
    assert client.get("/favicon.ico").status_code == 200
    home = client.get("/").text
    assert '<meta name="description"' in home and 'property="og:image"' in home and "{{SITE_URL}}" not in home
    obj = client.get(f"/?open={lid}").text
    assert 'noindex' in obj and '"@type": "Offer"' not in obj and f'rel="canonical" href="{config.SITE_URL}/?open={lid}"' in obj
    assert "Disallow: /*?open=" in client.get("/robots.txt").text
    assert obj.count('property="og:title"') == 1 and "89180000901" not in obj and "+79180000901" not in obj
    nf = client.get("/no-such-page")
    assert nf.status_code == 404 and "К поиску объектов" in nf.text
    assert client.get("/api/no-such").status_code == 404


def test_public_text_hides_names_commission_and_broken_phones():
    from app import parser
    t = parser.public_text("2к 58 м2\nРемонт\nПлачу 3%\nЗвоните 8918384538 Наталия\nЕлена 892825773125\nВячеслав Цветков")
    assert t == "2к 58 м2\nРемонт"
    assert parser.mask_names("Звоните Вячеслав Цветков", "[имя]") == "Звоните [имя]"


def test_photo_filter_and_hero_always(c):
    """26.10 (Артём): фильтр «с фото / без фото»; первый экран с числом объектов — всегда, без крестика."""
    client, lid = c
    conn = db.get()
    assert client.get("/api/listings?photo=no").json()["total"] == 1
    assert client.get("/api/listings?photo=yes").json()["total"] == 0
    conn.execute("UPDATE listings SET photos = ? WHERE id = ?", ('["/photos/1/a.jpg"]', lid))
    conn.commit()
    assert client.get("/api/listings?photo=yes&size=31").json()["total"] == 1   # другой размер страницы — мимо 15-секундного кэша
    client.cookies.set("hh", "1")   # раньше кука «скрыть приветствие» прятала блок
    html = client.get("/").text
    assert '<section class="hero" id="hero" aria-label' in html and 'id="heroX"' not in html


def test_live_counters(c):
    """30.10 (Артём): «онлайн сейчас · заходили сегодня · агентов» — настоящие цифры, сразу в HTML первого экрана."""
    client, _ = c
    conn = db.get()
    now = int(time.time())
    day = time.strftime("%Y-%m-%d", time.gmtime(now + 3 * 3600))
    conn.executemany("INSERT INTO presence (key, user_id, ts, path, mobile) VALUES (?,?,?,?,0)",
                     [("a", None, now, "/"), ("b", None, now - 30, "/"), ("old", None, now - 7200, "/")])
    conn.executemany("INSERT INTO visits_daily (day, key, user_id) VALUES (?,?,?)",
                     [(day, "a", None), (day, "b", None), (day, "old", None), (day, "u", 1)])
    conn.execute("INSERT INTO users (email, created) VALUES ('a@b.ru', ?)", (now,))
    conn.commit()
    server._live_mem.update(t=0.0, v=None)
    v = client.get("/api/live").json()
    assert {k: v[k] for k in ("online", "today", "guests", "agents")} == {"online": 2, "today": 4, "guests": 3, "agents": 1}
    assert len(v["pulse"]) == 12 and v["pulse"][-1] >= 1   # «пульс сайта»: 12 столбиков по 5 минут, последний — хотя бы смотрящий
    html = client.get("/").text
    assert 'id="heroLive" aria-expanded="false">' in html and 'data-l="online">2<' in html and 'data-l="agents">1<' in html
    client.post("/api/ping", json={"path": "/", "load": 1}, headers={"user-agent": "Mozilla/5.0"})
    server._live_mem.update(t=0.0, v=None)
    assert conn.execute("SELECT COUNT(*) FROM live_buckets").fetchone()[0] == 1


def test_hero_removed_counter(c):
    """31.10 (Артём): на первом экране вместо «районов на карте» — «убрано неактуальных» (проданные, по сроку, удалённые
    + уже стёртые из базы); склеенные повторы не считаются."""
    client, lid = c
    conn = db.get()
    conn.execute("UPDATE listings SET is_active = 0, hidden_reason = 'sold_text' WHERE id = ?", (lid,))
    db.set_state(conn, "deleted_listings", "4")
    conn.commit()
    server._stats_mem.update(t=0.0, v=None)
    assert server._stats()["removed"] == 5
    html = client.get("/").text
    assert 'data-k="removed" data-ssr="1">5<' in html and "районов на карте" not in html


def test_hero_requests_counter(c):
    """01.11 (Артём): запросы покупателей на первом экране — цифра-ссылка, кнопка и пункт рядом с «Продажа / Аренда»."""
    client, _ = c
    conn = db.get()
    conn.execute("DELETE FROM buyer_requests")
    conn.commit()
    server._stats_mem.update(t=0.0, v=None)
    html = client.get("/").text
    assert '<a class="hs-req" href="/requests" title="Что ищут покупатели прямо сейчас" hidden><b data-k="requests">0<' in html
    assert 'id="heroReq"' in html and 'class="seg seg-req" href="/requests"' in html
    from app import buyers, ingest
    m = ingest.add_message(conn, source="wa", text="Ищу 2к в ФМР до 7 млн, ипотека одобрена. 89180002233",
                           chat_id="c", msg_id="rq1", ts=int(__import__("time").time()))
    ingest.process_message(conn, m, use_llm=False)
    server._stats_mem.update(t=0.0, v=None)
    assert server._stats()["requests"] == 1
    assert 'data-k="requests" data-ssr="1">1<' in client.get("/").text

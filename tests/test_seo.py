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

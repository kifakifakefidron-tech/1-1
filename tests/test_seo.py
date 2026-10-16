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
    assert r.status_code == 200 and f"/?open={lid}" in r.text and "<urlset" in r.text
    assert client.get("/favicon.ico").status_code == 200
    home = client.get("/").text
    assert '<meta name="description"' in home and 'property="og:image"' in home and "{{SITE_URL}}" not in home
    obj = client.get(f"/?open={lid}").text
    assert '"@type": "Offer"' in obj and f'rel="canonical" href="{config.SITE_URL}/?open={lid}"' in obj
    assert obj.count('property="og:title"') == 1 and "89180000901" not in obj and "+79180000901" not in obj
    nf = client.get("/no-such-page")
    assert nf.status_code == 404 and "К поиску объектов" in nf.text
    assert client.get("/api/no-such").status_code == 404

"""Подборка для клиента не выдаёт сервис: в файлах, которые отдаёт нейтральный адрес подборок (Caddyfile, @pickStatic),
нет названия сервиса, домена и агентства — даже в комментариях («Посмотреть код страницы»)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAD = re.compile(r"1\+1|1plus1|стрел[ыа]|arrowsrealty", re.I)


def test_pick_domain_files_are_neutral():
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    files = re.search(r"@pickStatic path ([^\n]+)", caddy).group(1).split()
    assert "/static/cookies.js" in files
    paths = []
    for f in files:
        rel = f.replace("/static/", "")
        paths += sorted((ROOT / "web").glob(rel.replace("*", "**/*"))) if "*" in rel else [ROOT / "web" / rel]
    for path in paths:
        if path.is_dir() or path.suffix in (".woff2", ".png"):
            continue
        text = path.read_text(encoding="utf-8")
        assert not BAD.search(text), (path.name, BAD.search(text).group(0))
    page = (ROOT / "web" / "pick.html").read_text(encoding="utf-8")
    assert not BAD.search(page)


def test_pick_photos_served_on_both_domains():
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert caddy.count(r"^/photos/([0-9]+|pk)/") == 2


def test_no_third_party_scripts_or_fonts():
    """18.10: шрифты и карта — свои копии (web/vendor); браузер посетителя не ходит к Google Fonts / cdnjs / unpkg."""
    for f in (ROOT / "web").glob("*.html"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"fonts\.googleapis|fonts\.gstatic|cdnjs\.cloudflare|unpkg\.com", text), f.name
    for f in ("vendor/fonts/fonts.css", "vendor/leaflet/leaflet.min.js", "vendor/leaflet/leaflet.markercluster.min.js",
              "vendor/leaflet/leaflet-geoman.min.js", "vendor/leaflet/images/marker-icon.png"):
        assert (ROOT / "web" / f).is_file(), f

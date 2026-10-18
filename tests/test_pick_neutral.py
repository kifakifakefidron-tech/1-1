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
    for f in files:
        text = (ROOT / "web" / f.replace("/static/", "")).read_text(encoding="utf-8")
        assert not BAD.search(text), (f, BAD.search(text).group(0))
    page = (ROOT / "web" / "pick.html").read_text(encoding="utf-8")
    assert not BAD.search(page)


def test_pick_photos_served_on_both_domains():
    caddy = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert caddy.count(r"^/photos/([0-9]+|pk)/") == 2

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


def test_map_screen_layout():
    """16.11: карта — отдельный экран. Слои Leaflet заперты внутри .map-wrap (иначе шторка фильтров и списки
    открывались под картой); нажатие на метку/район — карточка, а не смена фильтра; заметная кнопка вида."""
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    css = (web / "glass.css").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    for id_ in ('id="mapSide"', 'id="mapWrap"', 'id="mapCard"', 'id="viewFab"'):
        assert id_ in html
    assert ".map-wrap { position: relative; isolation: isolate;" in css
    # район на карте: карточка, фильтр меняет только кнопка в ней
    dz = js[js.index('shape.on("click"'):js.index("shape.zoneName = z.name")]
    assert "refresh()" not in dz and "showMapCard" in dz
    # служебные перезагрузки карты не двигают её
    assert 'loadMap("keep")' in js and "flyToBounds" not in js

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


def test_place_edit_window_like_fix():
    """16.11: окно правки объекта админом — как экран разбора: текст объявления, подсказки района/ЖК «по точке на карте»,
    ваши нарисованные районы на карте; карточка на карте — в цветах сайта, заглушка фото — как в списке."""
    from pathlib import Path
    web = Path(__file__).resolve().parent.parent / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "glass.css").read_text(encoding="utf-8")
    dlg = html[html.index('<dialog id="placeDlg"'):]
    dlg = dlg[:dlg.index("</dialog>")]
    for id_ in ('id="placeText"', 'id="placeDChips"', 'id="placeCChips"', 'id="placeMap"', 'class="dlg-actions place-actions"'):
        assert id_ in dlg
    place = js[js.index("function placeLoadZones"):js.index("function placeCoordsGo")]
    assert "/api/districts/map" in place and "/api/admin/fix?at=" in place
    assert "interactive: false" in place   # контуры не мешают ставить метку нажатием
    assert '<div class="mc2-ph none" aria-hidden="true">${LOGO}<span>Фото у агента</span></div>' in js
    card = css[css.index(".mcard { position: absolute"):css.index("/* ─── окно правки объекта админом")]
    assert "var(--gold-100); color: var(--teal-950)" not in card   # больше не светлая
    assert ".mc2-open {" in card and "background: var(--gold-300)" in card

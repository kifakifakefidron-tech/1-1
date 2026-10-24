"""24.10c: своя обложка подборки — сохраняется, клиент видит её вместо общей; "" — снова градиент."""
import base64

from app import accounts, config, db, picks


def _png() -> str:
    # настоящий PNG побольше 1000 байт (save_cover отбрасывает мелочь)
    import struct
    import zlib
    w = h = 40
    raw = b"".join(b"\x00" + bytes([(x * 7 + y * 3) % 256 for x in range(w * 3)]) for y in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 0)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def test_cover_saved_and_public(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PHOTOS_DIR", str(tmp_path))
    conn = db.connect(":memory:")
    user = accounts.create_promo_user(conn)
    cid = conn.execute("INSERT INTO collections (user_id, title, token, created, updated) VALUES (?, 'П', 'tokc', 1, 1)", (user["id"],)).lastrowid
    conn.commit()
    res = picks.save(conn, user, {"id": cid, "cover": _png()})
    assert "error" not in res
    url = conn.execute("SELECT cover FROM collections WHERE id = ?", (cid,)).fetchone()[0]
    assert url.startswith("/photos/pk/") and (tmp_path / "pk" / url.rsplit("/", 1)[1]).exists()
    assert picks.public(conn, "tokc", count_view=False)["style"]["cover"] == url
    picks.save(conn, user, {"id": cid, "cover": ""})
    assert conn.execute("SELECT cover FROM collections WHERE id = ?", (cid,)).fetchone()[0] is None
    assert "error" in picks.save(conn, user, {"id": cid, "cover": "data:image/png;base64,AAAA"})


def test_hide_map_removes_points():
    """24.10e: автор подборки убрал карту — клиенту не отдаём координаты и флаг hide_map."""
    conn = db.connect(":memory:")
    user = accounts.create_promo_user(conn)
    lid = conn.execute("""INSERT INTO listings (type, deal, title, photos, first_seen, last_seen, seen_count, is_active, source, search_text,
                          lat, lon, geo_status, geo_src) VALUES ('flat','sale','2-к','[]',1,1,1,1,'feed','',45.03,38.97,'ok','osm-house')""").lastrowid
    cid = conn.execute("INSERT INTO collections (user_id, title, token, created, updated) VALUES (?, 'П', 'tokm', 1, 1)", (user["id"],)).lastrowid
    conn.execute("INSERT INTO collection_items (collection_id, listing_id, pos, added) VALUES (?, ?, 0, 1)", (cid, lid))
    conn.commit()
    picks.save(conn, user, {"id": cid, "hide_map": True})
    pub = picks.public(conn, "tokm", count_view=False)
    assert pub["hide_map"] is True and pub["items"][0]["lat"] is None

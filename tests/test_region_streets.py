"""24.10: улицы и шоссе Краснодара в названии чата («Ставропольская», «Ростовское шоссе») — не другой город."""
from app import region


def test_street_adjectives_are_not_foreign():
    for name in ("Аренда Сочи", "Недвижимость Адлер", "Ростов-на-Дону квартиры", "Анапа аренда", "Новороссийск"):
        assert region.is_foreign(name), name
    for name in ("ЮМР Ставропольская аренда", "Ростовское шоссе продажа", "ул. Новороссийская", "Анапская",
                 "Аренда Прикубанский округ", "Ейское шоссе", "Краснодар аренда"):
        assert not region.is_foreign(name), name


def test_mail_off_setting_saved():
    """24.10: «Присылать уведомления на почту» выключено — адрес попадает в список тех, кому письма не шлём."""
    from app import db, settings
    conn = db.connect(":memory:")
    uid = conn.execute("INSERT INTO users (email, created) VALUES ('a@x.ru', 0)").lastrowid
    assert settings.get(conn, uid)["mail_off"] is False
    settings.save(conn, uid, {"mail_off": True})
    rows = conn.execute("SELECT email FROM users WHERE json_extract(settings, '$.mail_off') = 1").fetchall()
    assert [r[0] for r in rows] == ["a@x.ru"]

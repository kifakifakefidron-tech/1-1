"""19.10: согласие агента на /agent — журнал, скрытие номера без согласия и при отзыве, бонус один раз на номер."""
import time

from app import accounts, consent, db


def _agent(conn, phone="+79181112233"):
    uid = accounts.create_promo_user(conn)["id"]
    conn.execute("INSERT INTO agent_phones (phone, user_id, verified_at) VALUES (?,?,?)", (phone, uid, int(time.time())))
    conn.commit()
    return uid


def test_consent_flow():
    conn = db.connect(":memory:")
    uid = _agent(conn)
    before = conn.execute("SELECT paid_until FROM users WHERE id = ?", (uid,)).fetchone()[0]
    r = consent.give(conn, uid, "8 918 111-22-33", True, True, False, True, "1.2.3.4")
    assert r["ok"] and r["bonus"] == 0                     # 25.10: подарка за подтверждение больше нет
    assert conn.execute("SELECT paid_until FROM users WHERE id = ?", (uid,)).fetchone()[0] == before
    assert "+79181112233" not in accounts.optout_set(conn)
    assert "+79181112233" not in consent.prefs(conn)["no_name"]   # 24.10i: имя и MAX — всегда, даже если прислали «нет»
    assert consent.give(conn, uid, "+79181112233", True, False, True, True)["bonus"] == 0   # бонус — один раз
    assert "+79181112233" in (consent._bump() or consent.prefs(conn))["no_license"]
    consent.revoke(conn, uid, "+79181112233")
    assert "+79181112233" in accounts.optout_set(conn)
    assert consent.current(conn, "+79181112233") is None
    assert conn.execute("SELECT COUNT(*) FROM agent_consents").fetchone()[0] == 2   # журнал хранит все решения


def test_no_publish_hides_and_foreign_phone_refused():
    conn = db.connect(":memory:")
    uid = _agent(conn)
    consent.give(conn, uid, "+79181112233", False, False, True, True)
    assert "+79181112233" in accounts.optout_set(conn)
    assert "error" in consent.give(conn, uid, "+79990000000", True, True, True, True)



def test_signup_choice_applies_to_phones_now_and_later():
    """25.10: галочки при регистрации — сразу на подтверждённые номера и сами на номер, подтверждённый позже."""
    conn = db.connect(":memory:")
    uid = _agent(conn)
    consent.at_signup(conn, uid, True, True, "1.2.3.4")
    c = consent.current(conn, "+79181112233")
    assert c and c["publish"] == 1 and c["license"] == 1
    conn.execute("INSERT INTO agent_phones (phone, user_id, verified_at) VALUES (?,?,?)", ("+79185556677", uid, int(time.time())))
    assert consent.apply_signup(conn, uid) == 1
    assert consent.current(conn, "+79185556677")["publish"] == 1
    consent.revoke(conn, uid, "+79185556677")
    assert consent.apply_signup(conn, uid) == 0          # отозванное не возвращаем
    assert consent.current(conn, "+79185556677") is None


def test_no_choice_at_signup_changes_nothing():
    conn = db.connect(":memory:")
    uid = _agent(conn)
    consent.at_signup(conn, uid, False, False)
    assert consent.signup_choice(conn, uid) is None and consent.current(conn, "+79181112233") is None


def test_trial_is_14_days():
    from app import config
    assert config.TRIAL_DAYS == 14 and config.REF_DAYS == 0 and consent.BONUS_DAYS == 0

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import accounts, buyers, db, notices
from app.stats import day_start


MSK = timezone(timedelta(hours=3))
MORNING = int(datetime(2026, 11, 21, 9, tzinfo=MSK).timestamp())
TODAY = day_start(MORNING)
YESTERDAY = TODAY - notices.DAY


class BuyerDigestTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.addCleanup(self.conn.close)

    def owner(self, name, *, access=True, source="own", active=True, off=None):
        uid = accounts.upsert_email_user(self.conn, f"{name}@example.test")["id"]
        self.conn.execute("UPDATE users SET trial_until=0, paid_until=?, settings=? WHERE id=?",
                          (MORNING + 3 * notices.DAY if access else 0, json.dumps({"notify_off": off or []}), uid))
        self.listing(uid, source=source, active=active)
        self.conn.commit()
        return uid

    def listing(self, uid, *, source="own", active=True):
        self.conn.execute("""INSERT INTO listings(type, deal, title, first_seen, last_seen, source, owner_user_id, is_active)
                             VALUES ('flat','sale','Двухкомнатная',?,?,?,?,?)""",
                          (YESTERDAY, YESTERDAY, source, uid, active))

    def request(self, ts, *, author=None, active=True, removed=0, last_seen=None):
        self.conn.execute("""INSERT INTO buyer_requests (text, deal, type, first_seen, last_seen, user_id, is_active, removed)
                             VALUES ('Ищу помещение в аренду','rent','commercial',?,?,?,?,?)""",
                          (ts, last_seen or ts, author, active, removed))
        self.conn.commit()

    def run_digest(self, now=MORNING):
        with patch("app.buyers.time.time", return_value=now):
            return buyers.notify_daily_digest(self.conn)

    def test_one_notice_for_an_owner_at_nine_moscow_time(self):
        uid = self.owner("first")
        self.listing(uid)
        self.request(YESTERDAY, author=uid)
        self.request(YESTERDAY + 1)
        self.request(TODAY - 1)
        self.request(TODAY)
        self.request(YESTERDAY - 1, last_seen=YESTERDAY + 1)
        self.request(YESTERDAY, active=False)
        self.request(YESTERDAY, removed=1)

        self.assertEqual(self.run_digest(MORNING - 1), 0)
        self.assertEqual(self.run_digest(), 1)
        self.assertEqual(self.run_digest(MORNING + 60), 0)
        notice, = notices.items(self.conn, uid)
        self.assertEqual((notice["kind"], notice["title"], notice["url"], notice["listing_id"]),
                         ("buyer", "Новые запросы за вчера · 2", "/requests", None))
        self.assertIn("вашу недвижимость", notice["body"])

        tomorrow = TODAY + notices.DAY
        self.request(tomorrow - 1)
        self.assertEqual(self.run_digest(MORNING + notices.DAY), 1)
        self.assertEqual([n["title"] for n in notices.items(self.conn, uid)], [notice["title"], notice["title"]])

    def test_requires_active_own_listing_and_respects_notification_preferences(self):
        enabled = self.owner("enabled")
        old_notice = self.owner("legacy")
        with patch("app.buyers.time.time", return_value=MORNING - 1800):
            notices.add(self.conn, old_notice, "buyer", "Старое уведомление", url="/requests?id=1")
        ineligible = ({"access": False}, {"source": "feed"}, {"active": False}, {"off": ["buyer"]}, {"off": ["all"]})
        for index, options in enumerate(ineligible):
            self.owner(f"other{index}", **options)
        self.request(YESTERDAY)
        self.assertEqual(self.run_digest(), 1)
        self.assertEqual([row[0] for row in self.conn.execute("SELECT user_id FROM notices WHERE url='/requests'")], [enabled])

    def test_only_own_request_and_empty_days_do_not_send_notices(self):
        uid = self.owner("author")
        self.request(YESTERDAY + 1, author=uid)
        self.assertEqual(self.run_digest(), 0)
        self.assertEqual(self.run_digest(MORNING + notices.DAY), 0)
        self.assertEqual(notices.items(self.conn, uid), [])

    def test_failure_rolls_back_both_notices_and_the_daily_marker(self):
        uid = self.owner("retry")
        self.request(YESTERDAY)
        add = notices.add

        def fail_after_insert(*args, **kwargs):
            add(*args, **kwargs)
            raise RuntimeError("temporary failure")

        with patch.object(notices, "add", side_effect=fail_after_insert), self.assertRaises(RuntimeError):
            self.run_digest()
        self.assertIsNone(db.get_state(self.conn, "buyer_digest_day"))
        self.assertEqual(notices.items(self.conn, uid), [])
        self.assertEqual(self.run_digest(), 1)

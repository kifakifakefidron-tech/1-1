import io
import json
import os
import runpy
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from app import config, db, ingest, llm


class DeepSeekTests(unittest.TestCase):
    def test_default_and_retired_aliases_use_current_model(self):
        official = "https://api.deepseek.com/chat/completions"
        cases = (
            (official, "", "deepseek-flash", "disabled"),
            (official, "deepseek-chat", "deepseek-flash", "disabled"),
            (official, "deepseek-reasoner", "deepseek-flash", "enabled"),
            (official, "deepseek-v4-pro", "deepseek-v4-pro", None),
            ("https://example.test/chat/completions", "deepseek-chat", "deepseek-chat", None),
        )
        for url, configured, model, thinking in cases:
            with self.subTest(configured=configured, url=url), patch.dict(
                os.environ, DEEPSEEK_URL=url, DEEPSEEK_MODEL=configured
            ):
                result = runpy.run_path(config.__file__)
                self.assertEqual((result["DEEPSEEK_MODEL"], result["DEEPSEEK_THINKING"]), (model, thinking))

    def test_permanent_request_errors_do_not_retry(self):
        for code in (401, 402, 404, 422):
            failure = HTTPError(config.DEEPSEEK_URL, code, "request rejected", {}, None)
            with self.subTest(code=code), patch.object(llm, "failures", 0), patch(
                "app.llm.urllib.request.urlopen", side_effect=failure
            ) as request, patch("app.llm.time.sleep") as wait:
                with self.assertRaisesRegex(llm.LLMError, f"HTTP Error {code}"):
                    llm.parse_message("Тест")
                request.assert_called_once()
                wait.assert_not_called()
                self.assertEqual(llm.failures, 1)

    def test_transient_error_is_retried(self):
        failure = HTTPError(config.DEEPSEEK_URL, 429, "rate limited", {}, None)
        result = {"kind": "other", "objects": []}
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode())
        with patch("app.llm.urllib.request.urlopen", side_effect=[failure, response]) as request, patch(
            "app.llm.time.sleep"
        ) as wait:
            self.assertEqual(llm.parse_message("Тест"), result)
            self.assertEqual(request.call_count, 2)
            wait.assert_called_once()

    def test_worker_queue_saves_listing_using_flash(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        text = ("Продам 2-комнатную квартиру в Краснодаре\nЖК Губернский, ул. Домбайская 59\n"
                "54 м², этаж 16/24\nЦена 6,8 млн рублей\nТел. +7 900 000-00-01")
        mid = ingest.add_message(conn, source="wa", text=text, ts=int(time.time()), msg_id="flash-test")
        result = {"kind": "listing", "objects": [{"type": "flat", "rooms": 2, "price": 6800000}]}
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(result)}}]}).encode())
        with patch.multiple(config, DEEPSEEK_API_KEY="test", DEEPSEEK_MODEL="deepseek-flash", DEEPSEEK_THINKING="disabled"), patch(
            "app.llm.urllib.request.urlopen", return_value=response
        ) as request:
            self.assertEqual(ingest.process_pending(conn), 1)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIn("[телефон]", payload["messages"][1]["content"])
        self.assertEqual(conn.execute("SELECT status FROM messages WHERE id=?", (mid,)).fetchone()[0], "done")
        self.assertEqual(conn.execute("SELECT price FROM listings WHERE is_active=1").fetchone()[0], 6800000)

    def test_recovery_only_returns_current_messages_with_the_import_error(self):
        conn = db.connect(":memory:")
        self.addCleanup(conn.close)
        now = int(time.time())
        missing_import = "name 'llm' is not defined"
        cases = (
            ("fresh", now - 300, missing_import),
            ("old", now - (config.STALE_DAYS + 1) * 86400, missing_import),
            ("other", now, "unrelated error"),
        )
        for mid, ts, error in cases:
            lid = ingest.add_message(conn, source="wa", text=f"Тестовое сообщение об объекте {mid}", ts=ts, msg_id=mid)
            conn.execute("UPDATE messages SET status='error', error=? WHERE id=?", (error, lid))
        self.assertEqual(ingest.requeue_failed_llm_import(conn, now), 1)
        self.assertEqual(ingest.requeue_failed_llm_import(conn, now), 0)
        rows = [tuple(row) for row in conn.execute("SELECT ts, status, error FROM messages ORDER BY id")]
        self.assertEqual(rows, [(cases[0][1], "new", None), (cases[1][1], "error", missing_import),
                                (cases[2][1], "error", "unrelated error")])

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from stoa import verda


class Limited(Exception):
    status_code = 429
    response = SimpleNamespace(headers={"retry-after": "20"})


class RetryTests(unittest.TestCase):
    def call(self, effects, endpoint="deepseek"):
        connection = Mock()
        connection.chat.completions.create.side_effect = effects
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(verda, "LOG_PATH", Path(folder)/"log.jsonl"), \
             patch.object(verda, "client", return_value=connection), \
             patch.object(verda, "_wait_for_rate_limit") as wait, \
             patch.object(verda, "_backoff") as backoff:
            caught = None
            try:
                verda.chat("test", [{"role": "user", "content": "question"}], endpoint=endpoint)
            except Exception as exc:
                caught = exc
            record = json.loads(verda.LOG_PATH.read_text(encoding="utf-8"))
        return connection, backoff, record, caught

    def test_rate_limit_retries_log_success_usage_once(self):
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20))
        connection, backoff, record, error = self.call([Limited(), response])
        self.assertIsNone(error)
        self.assertEqual(connection.chat.completions.create.call_count, 2)
        self.assertEqual(backoff.call_count, 1)
        self.assertEqual(record["rate_limit_retries"], 1)
        self.assertEqual(record["prompt_tokens"], 100)
        self.assertIsNone(record["error"])

    def test_retries_are_bounded(self):
        connection, _, record, error = self.call([Limited(), Limited(), Limited()])
        self.assertIsInstance(error, Limited)
        self.assertEqual(connection.chat.completions.create.call_count, 3)
        self.assertEqual(record["rate_limit_retries"], 2)

    def test_other_errors_and_google_are_not_retried_by_verda_policy(self):
        for error, endpoint in ((ValueError("bad request"), "deepseek"), (Limited(), "gemini")):
            with self.subTest(endpoint=endpoint):
                connection, backoff, _, caught = self.call([error], endpoint)
                self.assertIs(caught, error)
                self.assertEqual(connection.chat.completions.create.call_count, 1)
                backoff.assert_not_called()

    def test_shared_backoff_honors_retry_after_and_never_shortens_existing_wait(self):
        with patch.object(verda, "_RATE_UNTIL", 0), patch.object(verda.time, "monotonic", return_value=10):
            verda._backoff(Limited(), 0)
            self.assertEqual(verda._RATE_UNTIL, 30)
            verda._backoff(Limited(), 1)
            self.assertEqual(verda._RATE_UNTIL, 40)
            verda._backoff(Limited(), 0)
            self.assertEqual(verda._RATE_UNTIL, 40)


if __name__ == "__main__":
    unittest.main()

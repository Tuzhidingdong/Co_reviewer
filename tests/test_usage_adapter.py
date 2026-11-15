from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from co_reviewer.usage_adapter import UsageAdapterError, invoke_with_usage
from co_reviewer.usage_audit import read_usage_events


class UsageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = self.root / "usage.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def _invoke(self, call, **overrides):
        values = {
            "ledger_path": self.ledger,
            "invoke": call,
            "stage": "decision_synthesis",
            "actor": "supervisor",
            "provider": "provider-x",
            "model": "model-y",
            "context_scope": "excerpt",
            "reasoning_relation": "included_in_output",
            "job_id": "job-01",
            "session_id": "session-01",
        }
        values.update(overrides)
        return invoke_with_usage(**values)

    def test_records_measured_usage_and_returns_original_result(self):
        response = {"answer": "not copied into ledger"}
        result = self._invoke(
            lambda: response,
            usage_extractor=lambda value: {
                "input_tokens": 100,
                "cache_read_tokens": 20,
                "cache_write_tokens": 5,
                "output_tokens": 40,
                "reasoning_tokens": 12,
            },
        )
        self.assertIs(result, response)
        events, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["event_type"], "model_usage")
        self.assertEqual(event["context_scope"], "excerpt")
        self.assertEqual(event["call_status"], "succeeded")
        self.assertEqual(event["token_usage"]["source"], "measured")
        self.assertEqual(event["token_usage"]["input_tokens"], 100)
        self.assertEqual(event["job_id"], "job-01")
        self.assertEqual(event["session_id"], "session-01")
        ledger_text = self.ledger.read_text(encoding="utf-8")
        self.assertNotIn("not copied into ledger", ledger_text)
        self.assertNotIn('"answer"', ledger_text)
        self.assertGreaterEqual(event["duration_ms"], 0)

    def test_missing_usage_metadata_is_unavailable_not_estimated_from_text(self):
        result = self._invoke(lambda: {"output": "a large response"})
        self.assertEqual(result, {"output": "a large response"})
        events, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        usage = events[0]["token_usage"]
        self.assertEqual(usage["source"], "unavailable")
        self.assertIsNone(usage["output_tokens"])
        self.assertEqual(events[0]["context_scope"], "excerpt")

    def test_extractor_error_does_not_discard_successful_call_result(self):
        def broken_extractor(value):
            raise ValueError("do not persist this message")
        result = self._invoke(lambda: "model result", usage_extractor=broken_extractor)
        self.assertEqual(result, "model result")
        events, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        self.assertEqual(events[0]["token_usage"]["source"], "unavailable")
        self.assertEqual(events[0]["usage_extraction_error_type"], "ValueError")
        self.assertNotIn("do not persist this message", self.ledger.read_text(encoding="utf-8"))

    def test_invalid_extracted_counts_become_unavailable(self):
        result = self._invoke(lambda: "result", usage_extractor=lambda value: {"input_tokens": True})
        self.assertEqual(result, "result")
        events, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        self.assertEqual(events[0]["token_usage"]["source"], "unavailable")
        self.assertEqual(events[0]["usage_extraction_error_type"], "UsageAuditError")

    def test_failed_model_call_logs_unavailable_usage_and_reraises_original_error(self):
        def fail():
            raise RuntimeError("sensitive request/response details")
        with self.assertRaisesRegex(RuntimeError, "sensitive request/response details"):
            self._invoke(fail)
        events, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        self.assertEqual(events[0]["call_status"], "failed")
        self.assertEqual(events[0]["error_type"], "RuntimeError")
        self.assertEqual(events[0]["token_usage"]["source"], "unavailable")
        self.assertNotIn("sensitive request/response details", self.ledger.read_text(encoding="utf-8"))

    def test_successful_call_with_unwritable_ledger_exposes_result_without_blind_retry(self):
        ledger_is_directory = self.root / "unwritable.jsonl"
        ledger_is_directory.mkdir()
        response = {"completed": True}
        with self.assertRaises(UsageAdapterError) as caught:
            self._invoke(lambda: response, ledger_path=ledger_is_directory)
        self.assertTrue(caught.exception.call_completed)
        self.assertIs(caught.exception.result, response)
        self.assertIn("Do not retry", str(caught.exception))

    def test_invalid_context_scope_is_rejected_before_model_call(self):
        called = []
        with self.assertRaisesRegex(ValueError, "context_scope"):
            self._invoke(lambda: called.append(True), context_scope="guessed")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()

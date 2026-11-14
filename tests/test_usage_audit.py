# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import co_reviewer_cli as cli

from co_reviewer.usage_audit import (
    UsageAuditError, build_usage_report, estimate_usage_cost,
    read_usage_events, record_usage_event, validate_usage_event,
)


class UsageAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = self.root / "usage.jsonl"

    def tearDown(self):
        self.temp.cleanup()

    def test_record_and_read_metadata_only_event(self):
        event = record_usage_event(self.ledger, {
            "event_type": "document_read",
            "stage": "parse_inventory",
            "actor": "system",
            "document_read": {
                "role": "Manuscript",
                "source_sha256": "a" * 64,
                "cache_status": "miss",
                "parsed": True,
                "full_text_extracted": True,
                "paragraphs": 18,
                "characters": 2400,
                "context_scope": "none",
            },
        })
        rows, errors = read_usage_events(self.ledger)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_id"], event["event_id"])
        self.assertEqual(rows[0]["document_read"]["characters"], 2400)

    def test_unavailable_tokens_remain_null_not_zero(self):
        record_usage_event(self.ledger, {
            "event_type": "model_usage",
            "stage": "decision_synthesis",
            "actor": "supervisor",
            "provider": "provider-x",
            "model": "model-y",
            "token_usage": {"source": "unavailable"},
        })
        rows, errors = read_usage_events(self.ledger)
        report = build_usage_report(rows, errors)
        bucket = report["by_stage"]["decision_synthesis"]["measured"]["input_tokens"]
        self.assertIsNone(bucket["sum"])
        self.assertEqual(report["by_stage"]["decision_synthesis"]["unavailable_events"], 1)

    def test_repeated_full_document_extractions_are_grouped_by_sha(self):
        source_sha = "b" * 64
        for cache_status in ("miss", "miss", "hit"):
            record_usage_event(self.ledger, {
                "event_type": "document_read",
                "stage": "parse_inventory",
                "actor": "system",
                "document_read": {
                    "source_sha256": source_sha,
                    "cache_status": cache_status,
                    "parsed": cache_status == "miss",
                    "full_text_extracted": cache_status == "miss",
                    "paragraphs": 3,
                    "characters": 42,
                    "context_scope": "none",
                },
            })
        events, errors = read_usage_events(self.ledger)
        report = build_usage_report(events, errors)
        self.assertEqual(report["documents"]["full_text_extractions"], 2)
        self.assertEqual(report["documents"]["cache_hits"], 1)
        self.assertEqual(report["documents"]["repeated_full_reads_by_sha256"][0]["extraction_count"], 2)
        self.assertEqual(report["documents"]["model_context_scopes"]["none"], 3)

    def test_model_usage_buckets_are_aggregated_separately(self):
        record_usage_event(self.ledger, {
            "event_type": "model_usage",
            "stage": "patch_authoring",
            "actor": "worker",
            "provider": "provider-x",
            "model": "model-y",
            "token_usage": {
                "source": "measured",
                "reasoning_relation": "included_in_output",
                "input_tokens": 100,
                "cache_read_tokens": 20,
                "cache_write_tokens": 5,
                "output_tokens": 40,
                "reasoning_tokens": 10,
            },
        })
        events, errors = read_usage_events(self.ledger)
        report = build_usage_report(events, errors)
        totals = report["by_actor"]["worker"]["measured"]
        self.assertEqual(totals["input_tokens"]["sum"], 100)
        self.assertEqual(totals["cache_read_tokens"]["sum"], 20)
        self.assertEqual(totals["reasoning_tokens"]["sum"], 10)
        self.assertIn("Do not sum", report["token_accounting_note"])

    def test_rejects_negative_counts_and_unknown_reasoning_semantics(self):
        with self.assertRaises(UsageAuditError):
            validate_usage_event({
                "event_type": "model_usage",
                "token_usage": {"source": "measured", "input_tokens": -1},
            })
        with self.assertRaises(UsageAuditError):
            validate_usage_event({
                "event_type": "model_usage",
                "token_usage": {
                    "source": "unavailable", "input_tokens": 1,
                },
            })

    def test_rejects_raw_prompt_or_document_content(self):
        with self.assertRaisesRegex(UsageAuditError, "metadata only"):
            validate_usage_event({"event_type": "note", "prompt_text": "secret prompt"})

    def test_cost_estimation_requires_explicit_disjoint_pricing_and_complete_data(self):
        event = validate_usage_event({
            "event_type": "model_usage",
            "stage": "patch_authoring",
            "actor": "supervisor",
            "provider": "provider-x",
            "model": "model-y",
            "token_usage": {
                "source": "measured",
                "reasoning_relation": "included_in_output",
                "input_tokens": 1_000_000,
                "cache_read_tokens": 1_000_000,
                "cache_write_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "reasoning_tokens": 500_000,
            },
        })
        pricing = {
            "currency": "USD",
            "rates": {"provider-x/model-y": {
                "accounting_model": "disjoint_buckets",
                "input_per_million": 1,
                "cache_read_per_million": 0.1,
                "cache_write_per_million": 0.5,
                "output_per_million": 2,
            }},
        }
        estimate = estimate_usage_cost([event], pricing)
        self.assertEqual(estimate["total_estimated_cost"], 3.6)
        pricing["rates"]["provider-x/model-y"]["accounting_model"] = "unknown"
        blocked = estimate_usage_cost([event], pricing)
        self.assertEqual(blocked["estimated_events"], 0)
        self.assertEqual(blocked["unpriced_event_ids"], [event["event_id"]])

    def test_cli_refuses_to_overwrite_usage_ledger_and_pricing_inputs(self):
        parser = cli.build_parser()
        audit_args = parser.parse_args([
            "audit-usage", "--ledger", str(self.ledger), "--out", str(self.ledger),
        ])
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            cli.cmd_audit_usage(audit_args)

        pricing = self.root / "pricing.json"
        estimate_args = parser.parse_args([
            "estimate-usage", "--ledger", str(self.ledger),
            "--pricing", str(pricing), "--out", str(pricing),
        ])
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            cli.cmd_estimate_usage(estimate_args)

        record_args = parser.parse_args([
            "record-usage-event", "--ledger", str(self.ledger), "--event", str(self.ledger),
        ])
        with self.assertRaisesRegex(ValueError, "different files"):
            cli.cmd_record_usage_event(record_args)

    def test_usage_cli_commands_and_parse_instrumentation_flag_are_registered(self):
        parser = cli.build_parser()
        parsed = parser.parse_args(["audit-usage", "--ledger", str(self.ledger)])
        self.assertEqual(parsed.func, cli.cmd_audit_usage)
        parsed = parser.parse_args(["record-usage-event", "--event", str(self.root / "event.json")])
        self.assertEqual(parsed.func, cli.cmd_record_usage_event)
        parsed = parser.parse_args(["inspect", "--man", "sample.docx", "--usage-ledger", str(self.ledger)])
        self.assertEqual(parsed.usage_ledger, str(self.ledger))

    def test_cost_estimation_does_not_price_partial_token_records(self):
        event = validate_usage_event({
            "event_type": "model_usage",
            "provider": "provider-x",
            "model": "model-y",
            "token_usage": {"source": "measured", "input_tokens": 10},
        })
        estimate = estimate_usage_cost([event], {
            "currency": "USD",
            "rates": {"provider-x/model-y": {
                "accounting_model": "disjoint_buckets",
                "input_per_million": 1,
                "cache_read_per_million": 1,
                "cache_write_per_million": 1,
                "output_per_million": 1,
            }},
        })
        self.assertEqual(estimate["estimated_events"], 0)
        self.assertEqual(estimate["unpriced_event_ids"], [event["event_id"]])


if __name__ == "__main__":
    unittest.main()

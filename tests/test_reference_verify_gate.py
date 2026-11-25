from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli


class ReferenceVerifyGateTests(unittest.TestCase):
    def test_reference_graph_failure_blocks_standard_verify_gate(self):
        with patch(
            "co_reviewer.reference_transaction.verify_reference_transaction_outputs",
            return_value={
                "passed": False,
                "checked": True,
                "errors": ["Expected linked citation is missing from the output graph."],
            },
        ):
            report = cli.verify_apply_report(
                {
                    "revision_items": [],
                    "reference_transaction": {"transaction_id": "REF-TXN-FAIL"},
                },
                [],
            )
        self.assertFalse(report["reference_transaction"]["passed"])
        self.assertFalse(report["passed"])
        self.assertIn(
            "Expected linked citation",
            report["reference_transaction"]["errors"][0],
        )


if __name__ == "__main__":
    unittest.main()

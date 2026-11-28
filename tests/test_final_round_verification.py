# -*- coding: utf-8 -*-
"""Generic tests for original-target + formal-Patch final verification and status semantics."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.final_gate import evaluate_final_gate  # noqa: E402
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402
from test_final_gate import FinalGateTests  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


class FinalRoundVerificationTests(unittest.TestCase):
    """No paper-specific vocabulary: synthetic neutral tokens only."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.marked = self.root / "marked.docx"
        self.final = self.root / "final.docx"
        _write_docx(self.source, "alpha beta gamma")
        _write_docx(self.marked, "ALPHA beta GAMMA")
        _write_docx(self.final, "ALPHA2 beta GAMMA")

        self.plan = compile_revision_plan({
            "documents": {"Manuscript": {
                "input_file": str(self.source),
                "output_file": str(self.marked),
            }},
            "intents": [
                {"id": "NEU-1", "doc_role": "Manuscript", "find": "alpha", "replace": "ALPHA"},
                {"id": "NEU-2", "doc_role": "Manuscript", "find": "gamma", "replace": "GAMMA"},
            ],
        })
        self.t1 = self.plan["revision_items"][0]["targets"][0]["target_id"]
        self.t2 = self.plan["revision_items"][1]["targets"][0]["target_id"]
        self.verify = {
            "passed": True, "targets_total": 2, "targets_verified": 2, "targets_failed": 0,
            "untracked_text_audit": {"passed": True, "violations": []},
            "results": [
                {"revision_item_id": "NEU-1", "target_id": self.t1,
                 "status": "verified", "output_file": str(self.marked)},
                {"revision_item_id": "NEU-2", "target_id": self.t2,
                 "status": "verified", "output_file": str(self.marked)},
            ],
        }
        self.hashes = {
            str(self.marked.resolve()): _sha(self.marked),
            str(self.final.resolve()): _sha(self.final),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _patch(self, *, target: str | None = None, sequence: int = 1,
               passed: bool = True, final_text: str | None = None) -> dict:
        supersedes = [target or self.t1]
        patch_plan = {"revision_items": [{
            "id": "PAT-1", "status": "applied",
            "targets": [{
                "target_id": "PATCH-T1", "supersedes_target_ids": supersedes,
                "repair_class": "engine_defect", "document": "Manuscript",
                "input_file": str(self.marked), "output_file": str(self.final),
                "find": "ALPHA", "replace": "ALPHA2", "p_idx": 0, "status": "applied",
            }],
        }]}
        patch_apply = {
            "input_file": str(self.marked), "output_file": str(self.final),
            "results": [{
                "revision_item_id": "PAT-1", "target_id": "PATCH-T1",
                "supersedes_target_ids": supersedes, "p_idx": 0, "status": "applied",
                "find": "ALPHA", "replace": "ALPHA2",
            }],
        }
        if final_text is not None:
            _write_docx(self.final, final_text)
            self.hashes[str(self.final.resolve())] = _sha(self.final)
        return {
            "patch_id": f"PATCH-{sequence}", "sequence": sequence,
            "plan": patch_plan, "apply_report": patch_apply,
            "verify_report": {"passed": passed, "targets_total": 1,
                              "targets_verified": 1 if passed else 0,
                              "targets_failed": 0 if passed else 1},
            "supersedes_target_ids": supersedes,
        }

    def _run(self, patches: list[dict], hashes: dict | None = None) -> dict:
        return cli.verify_final_round(
            self.plan, self.verify, patches, self.hashes if hashes is None else hashes
        )

    def test_all_original_targets_pass_without_patch(self):
        result = self._run([])
        self.assertEqual(result["round_status"], "ROUND_COMPLETE")
        self.assertTrue(result["passed"])
        self.assertEqual([row["source"] for row in result["target_results"]],
                         ["original_verify", "original_verify"])

    def test_one_target_failed_then_fixed_by_formal_patch(self):
        result = self._run([self._patch()])
        self.assertEqual(result["round_status"], "ROUND_COMPLETE")
        row = next(row for row in result["target_results"] if row["target_id"] == self.t1)
        self.assertEqual(row["source"], "formal_patch")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["evidence"][0]["patch_ids"], ["PATCH-1"])

    def test_patch_success_does_not_hide_other_unmet_requirement(self):
        # The patch fixes T1; T2's approved text is absent from the document
        # that is actually bound as its final state.
        verify = copy.deepcopy(self.verify)
        verify["results"][1]["output_file"] = str(self.final)
        self.verify = verify
        result = self._run([self._patch(final_text="ALPHA2 beta unchanged")])
        self.assertEqual(result["round_status"], "NOT_VERIFIED")
        t2 = next(row for row in result["target_results"] if row["target_id"] == self.t2)
        self.assertEqual(t2["status"], "NOT_VERIFIED")
        self.assertIn("Expected final text", t2["reason"])

    def test_patch_that_changes_another_target_is_detected(self):
        verify = copy.deepcopy(self.verify)
        verify["results"][1]["output_file"] = str(self.final)
        self.verify = verify
        result = self._run([self._patch(final_text="ALPHA2 beta GAMMA-X")])
        self.assertEqual(result["round_status"], "NOT_VERIFIED")
        t2 = next(row for row in result["target_results"] if row["target_id"] == self.t2)
        self.assertEqual(t2["status"], "NOT_VERIFIED")

    def test_missing_original_evidence_is_blocked(self):
        verify = copy.deepcopy(self.verify)
        verify["results"] = verify["results"][:1]
        self.verify = verify
        result = self._run([])
        self.assertEqual(result["round_status"], "BLOCKED")
        self.assertTrue(result["blockers"])

    def test_missing_patch_verify_evidence_is_blocked(self):
        result = self._run([self._patch(passed=False)])
        self.assertEqual(result["round_status"], "BLOCKED")
        self.assertTrue(result["blockers"])

    def test_ambiguous_patch_target_mapping_is_not_verified(self):
        patch = self._patch()
        patch["plan"]["revision_items"][0]["targets"].append(
            copy.deepcopy(patch["plan"]["revision_items"][0]["targets"][0])
        )
        result = self._run([patch])
        self.assertEqual(result["round_status"], "NOT_VERIFIED")
        row = next(row for row in result["target_results"] if row["target_id"] == self.t1)
        self.assertIn("mapped uniquely", row["reason"])

    def test_multiple_patches_on_same_target_use_unique_order(self):
        second = self._patch(sequence=2)
        result = self._run([self._patch(sequence=1), second])
        self.assertEqual(result["round_status"], "ROUND_COMPLETE")

    def test_ambiguous_patch_order_is_not_verified(self):
        first = self._patch(sequence=1)
        second = self._patch(sequence=1)
        result = self._run([first, second])
        self.assertEqual(result["round_status"], "NOT_VERIFIED")

    def test_final_document_hash_mismatch_fails_closed(self):
        bad_hashes = dict(self.hashes)
        bad_hashes[str(self.marked.resolve())] = "0" * 64
        result = self._run([], hashes=bad_hashes)
        self.assertEqual(result["round_status"], "NOT_VERIFIED")
        self.assertTrue(all(row["status"] == "NOT_VERIFIED" for row in result["target_results"]))
        self.assertIn("hash", result["target_results"][0]["reason"])

    def test_patch_output_hash_mismatch_blocks_round(self):
        bad_hashes = dict(self.hashes)
        bad_hashes[str(self.final.resolve())] = "0" * 64
        result = self._run([self._patch()], hashes=bad_hashes)
        self.assertEqual(result["round_status"], "BLOCKED")
        self.assertTrue(any("output hash" in item for item in result["blockers"]))


class FinalGateStatusSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = FinalGateTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture

    def _gate(self, round_verification=None, scope=None):
        return evaluate_final_gate(
            plan=self.fixture.plan,
            verify_report=self.fixture.verify_report,
            apply_reports=self.fixture.apply_reports,
            docs=[self.fixture.clean_docx],
            round_root=self.fixture.root,
            skip_word_com=True,
            instruction_coverage=self.fixture.instruction_coverage,
            instruction_coverage_approval=self.fixture.instruction_coverage_approval,
            final_round_verification=round_verification,
            verification_scope=scope,
        )

    def test_patch_ready_is_not_round_complete(self):
        report = self._gate()
        self.assertEqual(report["verdict"], "READY")
        self.assertEqual(report["status"], "PATCH_READY")
        self.assertEqual(report["verification_scope"], "patch")
        self.assertFalse(report["round_complete"])

    def test_not_verified_round_cannot_become_round_complete(self):
        report = self._gate({"round_status": "NOT_VERIFIED"}, scope="round")
        self.assertEqual(report["verdict"], "READY")
        self.assertEqual(report["status"], "NOT_VERIFIED")
        self.assertEqual(report["round_status"], "NOT_VERIFIED")
        self.assertFalse(report["round_complete"])

    def test_round_complete_requires_full_verification_and_gate(self):
        report = self._gate({"round_status": "ROUND_COMPLETE"}, scope="round")
        self.assertEqual(report["status"], "ROUND_COMPLETE")
        self.assertTrue(report["round_complete"])
        self.assertTrue(report["document_ready"])
        self.assertTrue(report["patch_ready"])

    def test_document_only_scope_reports_document_ready(self):
        report = self._gate(scope="document")
        self.assertEqual(report["status"], "DOCUMENT_READY")
        self.assertEqual(report["verification_scope"], "document")


if __name__ == "__main__":
    unittest.main()

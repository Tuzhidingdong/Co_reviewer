import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli
from co_reviewer.instruction_coverage import (
    InstructionCoverageError,
    instruction_coverage_sha256,
    validate_instruction_based_authorization,
    validate_instruction_coverage,
    validate_instruction_coverage_scope,
)
from co_reviewer.identity import revision_targets_fingerprint


class InstructionBasedAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.source.write_bytes(b"immutable original DOCX bytes")
        self.instruction = self.root / "instructions.txt"
        self.instruction.write_text("T-01: replace exact old phrase with exact new phrase.\n", encoding="utf-8")
        self.output = self.root / "source.marked.docx"
        self.plan_path = self.root / "revision_plan.preflighted.json"
        self.coverage_path = self.root / "instruction_coverage.json"
        self.assessment_path = self.root / "instruction_authorization_assessment.json"
        self.approved_plan_path = self.root / "revision_plan.instruction_authorized.json"
        self.authorization_path = self.root / "instruction_based_authorization.json"
        source_sha = self.sha(self.source)
        instruction_sha = self.sha(self.instruction)
        self.target = {
            "document": "Manuscript",
            "input_file": str(self.source),
            "output_file": str(self.output),
            "part": "word/document.xml",
            "p_idx": 0,
            "find": "old phrase",
            "replace": "new phrase",
            "source_sha256": source_sha,
            "source_comment_ids": [],
            "instruction_requirement_ids": ["REQ-T-01-EDIT"],
            "authorized_change": {
                "intent": "T-01 explicitly requests this exact replacement.",
                "explicitly_authorized_tokens": [],
                "explicitly_authorized_insertions": [],
                "explicitly_authorized_deletions": [],
                "explicitly_authorized_spans": [],
            },
        }
        self.plan = {
            "schema_version": "1.0",
            "approved": False,
            "revision_items": [{
                "id": "T-01",
                "reviewer": "Author Instruction",
                "comment": "T-01: replace exact old phrase with exact new phrase.",
                "proposed_response": "",
                "status": "planned",
                "targets": [copy.deepcopy(self.target)],
            }],
        }
        self.plan["revision_items"][0]["preflight_fingerprint"] = revision_targets_fingerprint(
            self.plan["revision_items"][0]
        )
        self.coverage = {
            "schema_version": "1.1",
            "source_instruction": {"file": str(self.instruction), "sha256": instruction_sha},
            "requirements": [{
                "requirement_id": "REQ-T-01-EDIT",
                "task_id": "T-01",
                "source_line_start": 1,
                "source_line_end": 1,
                "source_locator": "L1–L1",
                "instruction_excerpt": "T-01: replace exact old phrase with exact new phrase.",
                "status": "planned",
                "revision_targets": [{"revision_item_id": "T-01", "target_index": 0}],
            }],
        }
        self.coverage["coverage_sha256"] = instruction_coverage_sha256(self.coverage)
        self.plan_path.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")
        self.coverage_path.write_text(json.dumps(self.coverage, ensure_ascii=False), encoding="utf-8")
        self.assessment = {
            "schema_version": "1.0",
            "status": "eligible",
            "preflight_plan_sha256": self.sha(self.plan_path),
            "source_instruction_sha256": instruction_sha,
            "coverage_sha256": self.coverage["coverage_sha256"],
            "reviewed_requirement_ids": ["REQ-T-01-EDIT"],
            "reviewed_target_refs": [{"revision_item_id": "T-01", "target_index": 0}],
            "unresolved_conflicts": [],
            "uncovered_requirements": [],
            "out_of_scope_targets": [],
            "unresolved_questions": [],
        }
        self.assessment_path.write_text(json.dumps(self.assessment, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def authorize(self):
        args = SimpleNamespace(
            plan=str(self.plan_path),
            instruction_coverage=str(self.coverage_path),
            assessment=str(self.assessment_path),
            out_plan=str(self.approved_plan_path),
            out_authorization=str(self.authorization_path),
        )
        self.assertEqual(cli.cmd_authorize_instruction_plan(args), 0)
        plan = json.loads(self.approved_plan_path.read_text(encoding="utf-8"))
        authorization = json.loads(self.authorization_path.read_text(encoding="utf-8"))
        return plan, authorization

    def test_explicit_instruction_task_is_authorized_without_human_receipt(self):
        plan, authorization = self.authorize()
        self.assertEqual(plan["authorization_mode"], "instruction_based")
        self.assertNotIn("author_approval", plan)
        self.assertEqual(authorization["authorization_type"], "instruction_based_authorization")
        report = validate_instruction_coverage(
            self.coverage,
            None,
            plan,
            instruction_based_authorization=authorization,
            plan_file=self.approved_plan_path,
            coverage_file=self.coverage_path,
        )
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["authorization_mode"], "instruction_based")

    def test_substantive_conflict_blocks_machine_authorization(self):
        self.assessment["unresolved_conflicts"] = ["Conflicting explicit replacement spans."]
        self.assessment_path.write_text(json.dumps(self.assessment), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.authorize()
        self.assertFalse(self.approved_plan_path.exists())

    def test_uncovered_extra_target_is_blocked(self):
        second = copy.deepcopy(self.target)
        second["find"] = "unrequested phrase"
        second["replace"] = "unrequested edit"
        second["instruction_requirement_ids"] = ["REQ-T-01-EDIT"]
        self.plan["revision_items"][0]["targets"].append(second)
        self.plan["revision_items"][0]["preflight_fingerprint"] = revision_targets_fingerprint(
            self.plan["revision_items"][0]
        )
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        self.assessment["preflight_plan_sha256"] = self.sha(self.plan_path)
        self.assessment["reviewed_target_refs"].append({"revision_item_id": "T-01", "target_index": 1})
        self.assessment_path.write_text(json.dumps(self.assessment), encoding="utf-8")
        errors = validate_instruction_coverage_scope(self.coverage, self.plan)
        self.assertTrue(any("not linked" in error or "exactly its linked" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "Coverage/plan scope"):
            self.authorize()

    def test_plan_or_coverage_hash_change_invalidates_authorization(self):
        self.authorize()
        tampered_plan = json.loads(self.approved_plan_path.read_text(encoding="utf-8"))
        tampered_plan["revision_items"][0]["targets"][0]["replace"] = "expanded unauthorized text"
        self.approved_plan_path.write_text(json.dumps(tampered_plan), encoding="utf-8")
        errors = validate_instruction_based_authorization(
            authorization=json.loads(self.authorization_path.read_text(encoding="utf-8")),
            coverage=self.coverage,
            plan=tampered_plan,
            plan_file=self.approved_plan_path,
            coverage_file=self.coverage_path,
        )
        self.assertTrue(any("plan SHA-256 changed" in error for error in errors))

        # Restore a new independently authorized pair and then mutate Coverage.
        self.approved_plan_path.unlink()
        self.authorization_path.unlink()
        plan, authorization = self.authorize()
        changed_coverage = copy.deepcopy(self.coverage)
        changed_coverage["requirements"][0]["instruction_excerpt"] += " Extra."
        changed_coverage["coverage_sha256"] = instruction_coverage_sha256(changed_coverage)
        self.coverage_path.write_text(json.dumps(changed_coverage), encoding="utf-8")
        errors = validate_instruction_based_authorization(
            authorization=authorization,
            coverage=changed_coverage,
            plan=plan,
            plan_file=self.approved_plan_path,
            coverage_file=self.coverage_path,
        )
        self.assertTrue(any("Coverage file SHA-256 changed" in error for error in errors))

    def test_changed_input_docx_invalidates_authorization(self):
        plan, authorization = self.authorize()
        self.source.write_bytes(b"changed after authorization")
        errors = validate_instruction_based_authorization(
            authorization=authorization,
            coverage=self.coverage,
            plan=plan,
            plan_file=self.approved_plan_path,
            coverage_file=self.coverage_path,
        )
        self.assertTrue(any("Input DOCX changed" in error for error in errors))

    def test_special_task_requires_human_approval_record(self):
        self.plan["revision_items"][0]["requires_author_approval"] = True
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        self.assessment["preflight_plan_sha256"] = self.sha(self.plan_path)
        self.assessment_path.write_text(json.dumps(self.assessment), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "human author approval"):
            self.authorize()

    def test_human_approval_path_creates_distinct_valid_record(self):
        args = SimpleNamespace(plan=str(self.plan_path), ids=["T-01"], actor="Human Author")
        self.assertEqual(cli.cmd_approve_plan(args), 0)
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["authorization_mode"], "author_approval")
        self.assertEqual(plan["author_approval"]["authorization_type"], "author_approval")
        self.assertEqual(cli._validate_author_approval_record(plan), [])
        self.assertNotIn("instruction_based_authorization", plan)

    def test_auto_authorization_does_not_bypass_verify_or_final_gate(self):
        plan, authorization = self.authorize()
        from co_reviewer.final_gate import evaluate_final_gate

        gate = evaluate_final_gate(
            plan=plan,
            verify_report={
                "passed": False,
                "targets_failed": 1,
                "untracked_text_audit": {"passed": False, "violations": [{"issue": "bad"}]},
            },
            docs=[],
            instruction_coverage=self.coverage,
            instruction_based_authorization=authorization,
            plan_file=self.approved_plan_path,
            coverage_file=self.coverage_path,
            skip_word_com=True,
        )
        self.assertEqual(gate["gates"]["instruction_coverage"], "PASS")
        self.assertEqual(gate["gates"]["verify"], "FAIL")
        self.assertEqual(gate["verdict"], "BLOCKED")

    def test_verify_cli_requires_instruction_receipt_for_machine_authorized_plan(self):
        self.authorize()
        apply_report = self.root / "apply_report.json"
        apply_report.write_text("[]", encoding="utf-8")
        verify_out = self.root / "verify.json"
        rc = cli.main([
            "verify",
            "--plan", str(self.approved_plan_path),
            "--apply-report", str(apply_report),
            "--out", str(verify_out),
        ])
        self.assertNotEqual(rc, 0)
        self.assertFalse(verify_out.exists())

    def test_apply_rejects_machine_plan_without_its_receipt(self):
        plan, _ = self.authorize()
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "Instruction-based Apply requires"):
            cli.apply_plan(plan, "Co_Reviewer")
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(self.output.exists())

    def test_apply_plan_rejects_input_output_identity_without_touching_original(self):
        before = self.source.read_bytes()
        bad_plan = copy.deepcopy(self.plan)
        bad_plan["revision_items"][0]["status"] = "approved"
        bad_plan["revision_items"][0]["targets"][0]["output_file"] = str(self.source)
        bad_plan["approved"] = True
        bad_plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(
            bad_plan["revision_items"][0]
        )
        with self.assertRaises(ValueError):
            cli.apply_plan(bad_plan, "Co_Reviewer")
        self.assertEqual(self.source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

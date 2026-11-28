"""Test-first regression suite for the instruction-based Plan lifecycle.

These tests reproduce the real Round failure: Apply rewriting the hash-bound
authorized Plan, Verify seeing 0/0 targets, and Final Gate blocking on the
resulting authorization mismatch. They are created before any production
source change.
"""
import copy
import re
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.final_gate import evaluate_final_gate
from co_reviewer.instruction_coverage import instruction_coverage_sha256
from co_reviewer.identity import revision_targets_fingerprint


def q(local):
    return f"{{{cli.W}}}{local}"


def make_docx(path, paragraphs):
    body = ET.Element(q("body"))
    for chunks in paragraphs:
        p = ET.SubElement(body, q("p"))
        for value in chunks:
            r = ET.SubElement(p, q("r"))
            ET.SubElement(r, q("t")).text = value
    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
        "word/media/image1.png": b"fixture-image-bytes",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def fake_pdf_exporter_ok(docx_path, pdf_path):
    pdf_path.write_bytes(b"%PDF-1.4\n%fake-pdf\n%%EOF\n")
    return {"status": "PASS", "pdf_file": str(pdf_path), "bytes": pdf_path.stat().st_size}


class InstructionPlanLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        make_docx(self.source, [["The old phrase remains."], ["alpha beta"]])
        self.output = self.root / "source.marked.docx"
        self.instruction = self.root / "instructions.txt"
        self.instruction.write_text("T-01: replace old phrase.\nT-02: replace alpha.\n", encoding="utf-8")
        self.plan_path = self.root / "revision_plan.preflighted.json"
        self.coverage_path = self.root / "instruction_coverage.json"
        self.assessment_path = self.root / "assessment.json"
        self.authorized_plan_path = self.root / "revision_plan.instruction_authorized.json"
        self.authorization_path = self.root / "instruction_based_authorization.json"
        self.apply_report_path = self.root / "revision_apply_report.json"
        self.verify_report_path = self.root / "verify_report.json"

        source_sha = self.sha(self.source)
        self.targets = [
            {
                "document": "Manuscript",
                "input_file": str(self.source),
                "output_file": str(self.output),
                "part": "word/document.xml",
                "p_idx": 0,
                "find": "old phrase",
                "replace": "new wording",
                "source_sha256": source_sha,
                "source_comment_ids": [],
                "instruction_requirement_ids": ["REQ-T-01-EDIT"],
                "authorized_change": {"intent": "T-01 exact replacement", "explicitly_authorized_tokens": []},
            },
            {
                "document": "Manuscript",
                "input_file": str(self.source),
                "output_file": str(self.output),
                "part": "word/document.xml",
                "p_idx": 1,
                "find": "alpha",
                "replace": "beta",
                "source_sha256": source_sha,
                "source_comment_ids": [],
                "instruction_requirement_ids": ["REQ-T-02-EDIT"],
                "authorized_change": {"intent": "T-02 exact replacement", "explicitly_authorized_tokens": []},
            },
        ]
        self.plan = {
            "schema_version": "1.0",
            "revision_round_id": "round-lifecycle-test",
            "approved": False,
            "revision_items": [],
        }
        for task_id, target, req in (
            ("T-01", self.targets[0], "REQ-T-01-EDIT"),
            ("T-02", self.targets[1], "REQ-T-02-EDIT"),
        ):
            item = {
                "id": task_id,
                "reviewer": "Author Instruction",
                "comment": f"{task_id} exact replacement.",
                "proposed_response": "",
                "status": "planned",
                "targets": [copy.deepcopy(target)],
            }
            item["preflight_fingerprint"] = revision_targets_fingerprint(item)
            self.plan["revision_items"].append(item)
        self.coverage = {
            "schema_version": "1.1",
            "source_instruction": {"file": str(self.instruction), "sha256": self.sha(self.instruction)},
            "requirements": [
                {
                    "requirement_id": "REQ-T-01-EDIT", "task_id": "T-01",
                    "source_line_start": 1, "source_line_end": 1,
                    "source_locator": "L1–L1",
                    "instruction_excerpt": "T-01: replace old phrase.",
                    "status": "planned",
                    "revision_targets": [{"revision_item_id": "T-01", "target_index": 0}],
                },
                {
                    "requirement_id": "REQ-T-02-EDIT", "task_id": "T-02",
                    "source_line_start": 2, "source_line_end": 2,
                    "source_locator": "L2–L2",
                    "instruction_excerpt": "T-02: replace alpha.",
                    "status": "planned",
                    "revision_targets": [{"revision_item_id": "T-02", "target_index": 0}],
                },
            ],
        }
        self.coverage["coverage_sha256"] = instruction_coverage_sha256(self.coverage)
        self.assessment = {
            "schema_version": "1.0",
            "status": "eligible",
            "preflight_plan_sha256": None,
            "source_instruction_sha256": self.coverage["source_instruction"]["sha256"],
            "coverage_sha256": self.coverage["coverage_sha256"],
            "reviewed_requirement_ids": ["REQ-T-01-EDIT", "REQ-T-02-EDIT"],
            "reviewed_target_refs": [
                {"revision_item_id": "T-01", "target_index": 0},
                {"revision_item_id": "T-02", "target_index": 0},
            ],
            "unresolved_conflicts": [], "uncovered_requirements": [],
            "out_of_scope_targets": [], "unresolved_questions": [],
        }
        self._write_json(self.plan_path, self.plan)
        self._write_json(self.coverage_path, self.coverage)
        self.assessment["preflight_plan_sha256"] = self.sha(self.plan_path)
        self._write_json(self.assessment_path, self.assessment)
        self.authorize()
        self.authorized_plan_sha = self.sha(self.authorized_plan_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path, payload):
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _read_json(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def authorize(self):
        rc = cli.cmd_authorize_instruction_plan(SimpleNamespace(
            plan=str(self.plan_path),
            instruction_coverage=str(self.coverage_path),
            assessment=str(self.assessment_path),
            out_plan=str(self.authorized_plan_path),
            out_authorization=str(self.authorization_path),
        ))
        self.assertEqual(rc, 0)

    def apply(self):
        return cli.cmd_apply(SimpleNamespace(
            plan=str(self.authorized_plan_path),
            report=str(self.apply_report_path),
            author="Lifecycle Test",
            instruction_coverage=str(self.coverage_path),
            instruction_authorization=str(self.authorization_path),
            revision_ledger=None,
        ))

    def verify(self):
        return cli.cmd_verify(SimpleNamespace(
            plan=str(self.authorized_plan_path),
            apply_report=str(self.apply_report_path),
            out=str(self.verify_report_path),
            instruction_coverage=str(self.coverage_path),
            instruction_authorization=str(self.authorization_path),
        ))

    def lifecycle_kwargs(self, *, plan=None, apply_reports=None, verify_report=None):
        plan = plan if plan is not None else self._read_json(self.authorized_plan_path)
        apply_reports = apply_reports if apply_reports is not None else self._read_json(self.apply_report_path)
        verify_report = verify_report if verify_report is not None else self._read_json(self.verify_report_path)
        return dict(
            plan=plan,
            verify_report=verify_report,
            apply_reports=apply_reports,
            docs=[str(self.output)],
            round_root=str(self.root),
            quality_profile="marked-manuscript",
            skip_word_com=True,
            pdf_exporter=fake_pdf_exporter_ok,
            instruction_coverage=self._read_json(self.coverage_path),
            instruction_based_authorization=self._read_json(self.authorization_path),
            plan_file=self.authorized_plan_path,
            coverage_file=self.coverage_path,
            document_roles={str(self.output): "marked-manuscript"},
        )

    # --- A: authorized Plan immutability ---------------------------------
    def test_apply_leaves_authorized_plan_byte_identical(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.sha(self.authorized_plan_path), self.authorized_plan_sha,
                         "Apply must not rewrite the hash-bound authorized Plan")
        plan = self._read_json(self.authorized_plan_path)
        self.assertTrue(all(item.get("status") != "applied" for item in plan["revision_items"]),
                        "Apply must not write execution status into the authorized Plan")

    # --- B: independent execution record ---------------------------------
    def test_apply_report_binds_round_plan_and_targets(self):
        self.assertEqual(self.apply(), 0)
        reports = self._read_json(self.apply_report_path)
        self.assertIsInstance(reports, list)
        for entry in reports:
            self.assertEqual(entry.get("authorized_plan_sha256"), self.authorized_plan_sha)
            self.assertEqual(entry.get("plan_file"), str(self.authorized_plan_path))
            self.assertEqual(entry.get("authorization_sha256"),
                             self._read_json(self.authorization_path)["authorization_sha256"])
            self.assertEqual(entry.get("round_id"), "round-lifecycle-test")
        results = [r for entry in reports for r in entry.get("results", [])]
        self.assertEqual({(r["revision_item_id"], r.get("target_index")) for r in results},
                         {("T-01", 0), ("T-02", 0)})

    def test_apply_record_missing_target_blocks_verify(self):
        self.assertEqual(self.apply(), 0)
        reports = self._read_json(self.apply_report_path)
        reports[0]["results"] = [r for r in reports[0]["results"] if r["revision_item_id"] != "T-02"]
        self._write_json(self.apply_report_path, reports)
        with self.assertRaisesRegex(ValueError, re.compile("target|targets|coverage|incomplete|missing", re.IGNORECASE)):
            self.verify()

    def test_apply_record_duplicate_target_blocks_verify(self):
        self.assertEqual(self.apply(), 0)
        reports = self._read_json(self.apply_report_path)
        first = next(r for r in reports[0]["results"] if r["revision_item_id"] == "T-01")
        reports[0]["results"].append(copy.deepcopy(first))
        self._write_json(self.apply_report_path, reports)
        with self.assertRaisesRegex(ValueError, re.compile("duplicate|target", re.IGNORECASE)):
            self.verify()

    def test_apply_record_unauthorized_target_blocks_verify(self):
        self.assertEqual(self.apply(), 0)
        reports = self._read_json(self.apply_report_path)
        reports[0]["results"].append({
            "revision_item_id": "T-99", "target_index": 0, "status": "applied",
            "find": "unrequested", "replace": "edit",
        })
        self._write_json(self.apply_report_path, reports)
        with self.assertRaisesRegex(ValueError, re.compile("unauthorized|not authorized|target", re.IGNORECASE)):
            self.verify()

    def test_apply_record_plan_hash_mismatch_blocks_verify(self):
        self.assertEqual(self.apply(), 0)
        reports = self._read_json(self.apply_report_path)
        for entry in reports:
            entry["authorized_plan_sha256"] = "0" * 64
        self._write_json(self.apply_report_path, reports)
        with self.assertRaisesRegex(ValueError, re.compile("plan SHA|plan_sha256|hash", re.IGNORECASE)):
            self.verify()

    # --- C: Verify reads the full authorized target set ------------------
    def test_verify_covers_all_authorized_targets_not_zero(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.verify(), 0)
        report = self._read_json(self.verify_report_path)
        self.assertEqual(report.get("targets_total"), 2, report)
        self.assertEqual(report.get("targets_verified"), 2, report)
        self.assertEqual(report.get("targets_failed"), 0, report)
        self.assertEqual(report.get("authorized_plan_sha256"), self.authorized_plan_sha)
        self.assertEqual(report.get("round_id"), "round-lifecycle-test")

    def test_verify_zero_targets_is_failure(self):
        self.assertEqual(self.apply(), 0)
        empty = copy.deepcopy(self._read_json(self.authorized_plan_path))
        for item in empty["revision_items"]:
            item["targets"] = []
        empty_path = self.root / "empty_authorized_plan.json"
        self._write_json(empty_path, empty)
        # The receipt no longer binds the empty plan, so Verify must fail closed.
        with self.assertRaises(ValueError):
            cli.cmd_verify(SimpleNamespace(
                plan=str(empty_path),
                apply_report=str(self.apply_report_path),
                out=str(self.root / "verify_empty.json"),
                instruction_coverage=str(self.coverage_path),
                instruction_authorization=str(self.authorization_path),
            ))

    # --- D: Final Gate lifecycle consistency -----------------------------
    def test_final_gate_accepts_consistent_lifecycle(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.verify(), 0)
        report = evaluate_final_gate(**self.lifecycle_kwargs())
        self.assertTrue(report["verdict"] == "READY", report.get("blockers"))
        self.assertIn(report["status"], {"PATCH_READY", "READY", "ROUND_COMPLETE"})

    def test_final_gate_blocks_verify_report_with_wrong_plan_hash(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.verify(), 0)
        verify_report = self._read_json(self.verify_report_path)
        verify_report["authorized_plan_sha256"] = "f" * 64
        report = evaluate_final_gate(**self.lifecycle_kwargs(verify_report=verify_report))
        self.assertNotEqual(report["verdict"], "READY")
        self.assertTrue(any("Verify" in b or "plan" in b.lower() for b in report["blockers"]), report["blockers"])

    def test_final_gate_blocks_when_verify_report_has_zero_targets(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.verify(), 0)
        verify_report = self._read_json(self.verify_report_path)
        verify_report.update(targets_total=0, targets_verified=0, targets_failed=0, passed=False)
        report = evaluate_final_gate(**self.lifecycle_kwargs(verify_report=verify_report))
        self.assertNotEqual(report["verdict"], "READY")

    def test_final_gate_blocks_apply_record_from_another_round(self):
        self.assertEqual(self.apply(), 0)
        self.assertEqual(self.verify(), 0)
        apply_reports = self._read_json(self.apply_report_path)
        for entry in apply_reports:
            entry["round_id"] = "round-someone-else"
        report = evaluate_final_gate(**self.lifecycle_kwargs(apply_reports=apply_reports))
        self.assertNotEqual(report["verdict"], "READY")


def re_ignore_case(pattern):
    """Minimal helper so assertRaisesRegex stays case-insensitive without a dependency."""
    import re
    return re.compile(pattern, re.IGNORECASE)


if __name__ == "__main__":
    unittest.main()



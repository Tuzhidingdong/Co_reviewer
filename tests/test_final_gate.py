import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.final_gate import evaluate_final_gate
from co_reviewer.instruction_coverage import (
    authorize_instruction_coverage,
    instruction_coverage_sha256,
)


def q(local: str) -> str:
    return f"{{{cli.W}}}{local}"


def make_valid_docx(path: Path, *, with_tracked_ins: bool = False) -> None:
    body = ET.Element(q("body"))
    p = ET.SubElement(body, q("p"))
    if with_tracked_ins:
        ins = ET.SubElement(p, q("ins"))
        ins.set(q("id"), "1")
        ins.set(q("author"), "Reviewer")
        r = ET.SubElement(ins, q("r"))
    else:
        r = ET.SubElement(p, q("r"))
    ET.SubElement(r, q("t")).text = "This is a clean verified paragraph."
    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def fake_pdf_exporter_ok(docx_path: Path, pdf_path: Path) -> dict:
    pdf_path.write_bytes(b"%PDF-1.4\n%fake-pdf\n%%EOF\n")
    return {"status": "PASS", "pdf_file": str(pdf_path), "bytes": pdf_path.stat().st_size}


def fake_pdf_exporter_fail(docx_path: Path, pdf_path: Path) -> dict:
    return {"status": "FAIL", "pdf_file": str(pdf_path), "error": "Word COM unavailable"}


def fake_pdf_exporter_invalid_bytes(docx_path: Path, pdf_path: Path) -> dict:
    pdf_path.write_bytes(b"NOTPDF")
    return {"status": "PASS", "pdf_file": str(pdf_path), "bytes": 6}


class FinalGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.clean_docx = self.root / "manuscript_clean.docx"
        make_valid_docx(self.clean_docx)

        self.plan = {
            "revision_items": [
                {
                    "id": "REV-1",
                    "status": "verified",
                    "targets": [
                        {
                            "document": "Manuscript",
                            "output_file": str(self.clean_docx),
                            "find": "old text",
                            "replace": "new text",
                            "status": "verified",
                        }
                    ],
                },
                {
                    "id": "REV-2",
                    "status": "verified",
                    "targets": [
                        {
                            "document": "Manuscript",
                            "output_file": str(self.clean_docx),
                            "find": "alpha",
                            "replace": "alpha",
                            "format_operations": [{"find": "alpha", "properties": {"i": True}}],
                            "status": "verified",
                        }
                    ],
                },
            ]
        }
        self.apply_reports = [
            {
                "output_file": str(self.clean_docx),
                "results": [
                    {
                        "revision_item_id": "REV-1",
                        "status": "applied",
                        "find": "old text",
                        "replace": "new text",
                    },
                    {
                        "revision_item_id": "REV-2",
                        "status": "applied",
                        "find": "alpha",
                        "replace": "alpha",
                        "format_operations": [{"find": "alpha", "properties": {"i": True}}],
                    },
                ],
            }
        ]
        self.verify_report = {
            "passed": True,
            "targets_total": 2,
            "targets_verified": 2,
            "targets_failed": 0,
            "untracked_text_audit": {"passed": True, "violations": []},
            "results": [],
        }

        self.instruction_file = self.root / "source_instructions.txt"
        self.instruction_file.write_text(
            "Synthetic requirement one.\nSynthetic requirement two.\n",
            encoding="utf-8",
        )
        self.instruction_coverage = {
            "schema_version": "1.0",
            "source_instruction": {
                "file": str(self.instruction_file),
                "sha256": __import__("hashlib").sha256(
                    self.instruction_file.read_bytes()
                ).hexdigest(),
            },
            "requirements": [
                {
                    "requirement_id": "REQ-1",
                    "source_locator": "Synthetic instruction line 1",
                    "instruction_excerpt": "Synthetic requirement one.",
                    "status": "planned",
                    "revision_item_ids": ["REV-1"],
                },
                {
                    "requirement_id": "REQ-2",
                    "source_locator": "Synthetic instruction line 2",
                    "instruction_excerpt": "Synthetic requirement two.",
                    "status": "planned",
                    "revision_item_ids": ["REV-2"],
                },
            ],
        }
        self.instruction_coverage["coverage_sha256"] = instruction_coverage_sha256(
            self.instruction_coverage
        )
        self.instruction_coverage_approval = authorize_instruction_coverage(
            self.instruction_coverage,
            self.instruction_coverage["coverage_sha256"],
            "Human Author",
            approved_at_utc="2026-11-23T10:00:00+00:00",
        )

    def write_instruction_coverage_files(self):
        coverage_path = self.root / "instruction_coverage.json"
        approval_path = self.root / "instruction_coverage_approval.json"
        coverage_path.write_text(
            json.dumps(self.instruction_coverage, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        approval_path.write_text(
            json.dumps(self.instruction_coverage_approval, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return coverage_path, approval_path

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_final_gate_ready_and_reports_written(self):
        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            round_root=self.root,
            skip_word_com=True,
            instruction_coverage=self.instruction_coverage,
            instruction_coverage_approval=self.instruction_coverage_approval,
        )
        self.assertEqual(report["verdict"], "READY")
        self.assertEqual(report["reconciliation"]["input_items"], 2)
        self.assertEqual(report["reconciliation"]["applied_text_edits"], 1)
        self.assertEqual(report["reconciliation"]["applied_format_changes"], 1)
        self.assertEqual(report["reconciliation"]["failed"], 0)
        self.assertEqual(report["reconciliation"]["manual_fallback"], 0)
        self.assertEqual(report["reconciliation"]["quality_status"], "PASS")
        self.assertEqual(report["reconciliation"]["pdf_render_status"], "SKIPPED_TEST_ONLY")

        plan_path = self.root / "revision_plan.json"
        verify_path = self.root / "verify_report.json"
        apply_path = self.root / "apply_report.json"
        out_json = self.root / "final_gate_report.json"
        out_md = self.root / "final_gate_report.md"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        verify_path.write_text(json.dumps(self.verify_report), encoding="utf-8")
        apply_path.write_text(json.dumps(self.apply_reports), encoding="utf-8")
        coverage_path, coverage_approval_path = self.write_instruction_coverage_files()

        with patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok):
            rc = cli.main([
                "final-gate",
                "--plan", str(plan_path),
                "--verify-report", str(verify_path),
                "--apply-report", str(apply_path),
                "--docs", str(self.clean_docx),
                "--round-root", str(self.root),
                "--instruction-coverage", str(coverage_path),
                "--instruction-coverage-approval", str(coverage_approval_path),
                "--out-json", str(out_json),
                "--out-md", str(out_md),
            ])

        self.assertEqual(rc, 0)
        self.assertTrue(out_json.is_file())
        self.assertTrue(out_md.is_file())
        written_json = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(written_json["verdict"], "READY")
        self.assertEqual(written_json["reconciliation"]["pdf_render_status"], "PASS")
        md_text = out_md.read_text(encoding="utf-8")
        self.assertIn("# Final Delivery Hard Gate Report", md_text)
        self.assertIn("**Verdict:** READY", md_text)
        self.assertIn("| input_items | 2 |", md_text)
        self.assertIn("| applied_text_edits | 1 |", md_text)
        self.assertIn("| applied_format_changes | 1 |", md_text)

    def test_final_gate_blocks_missing_instruction_coverage(self):
        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            skip_word_com=True,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["instruction_coverage"], "FAIL")
        self.assertTrue(any("author approval receipt" in item for item in report["blockers"]))

    def test_final_gate_blocks_requirement_omitted_from_compiled_plan(self):
        from copy import deepcopy

        coverage = deepcopy(self.instruction_coverage)
        coverage["requirements"][1]["status"] = "blocked"
        coverage["requirements"][1].pop("revision_item_ids", None)
        coverage["requirements"][1]["reason"] = "Reference paragraph insertion is not supported."
        coverage["coverage_sha256"] = instruction_coverage_sha256(coverage)
        approval = authorize_instruction_coverage(
            coverage,
            coverage["coverage_sha256"],
            "Human Author",
            approved_at_utc="2026-11-23T10:00:00+00:00",
        )
        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            skip_word_com=True,
            instruction_coverage=coverage,
            instruction_coverage_approval=approval,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["instruction_coverage"], "FAIL")
        self.assertTrue(any("remains blocked" in item for item in report["blockers"]))

    def test_final_gate_blocks_unlinked_compiled_plan_item(self):
        from copy import deepcopy

        coverage = deepcopy(self.instruction_coverage)
        coverage["requirements"][1]["revision_item_ids"] = ["REV-UNKNOWN"]
        coverage["coverage_sha256"] = instruction_coverage_sha256(coverage)
        approval = authorize_instruction_coverage(
            coverage,
            coverage["coverage_sha256"],
            "Human Author",
            approved_at_utc="2026-11-23T10:00:00+00:00",
        )
        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            skip_word_com=True,
            instruction_coverage=coverage,
            instruction_coverage_approval=approval,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["instruction_coverage"], "FAIL")
        self.assertTrue(any("missing revision item REV-UNKNOWN" in item for item in report["blockers"]))

    def test_final_gate_blocked_on_verify_failure(self):
        bad_verify = {
            "passed": False,
            "targets_total": 2,
            "targets_verified": 1,
            "targets_failed": 1,
            "untracked_text_audit": {"passed": False, "violations": [{"detail": "untracked"}]},
        }
        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=bad_verify,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            pdf_exporter=fake_pdf_exporter_ok,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["verify"], "FAIL")

        verify_path = self.root / "bad_verify.json"
        out_json = self.root / "fg.json"
        out_md = self.root / "fg.md"
        verify_path.write_text(json.dumps(bad_verify), encoding="utf-8")
        with patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok):
            rc = cli.main([
                "final-gate",
                "--verify-report", str(verify_path),
                "--docs", str(self.clean_docx),
                "--instruction-coverage", str(coverage_path),
                "--instruction-coverage-approval", str(coverage_approval_path),
                "--out-json", str(out_json),
                "--out-md", str(out_md),
            ])
        self.assertEqual(rc, 1)

    def test_final_gate_blocked_on_docx_quality_failure(self):
        bad_clean = self.root / "bad_clean.docx"
        make_valid_docx(bad_clean, with_tracked_ins=True)

        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[bad_clean],
            pdf_exporter=fake_pdf_exporter_ok,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["docx_quality"], "FAIL")

        verify_path = self.root / "verify.json"
        verify_path.write_text(json.dumps(self.verify_report), encoding="utf-8")
        with patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok):
            rc = cli.main([
                "final-gate",
                "--verify-report", str(verify_path),
                "--docs", str(bad_clean),
                "--out-json", str(self.root / "fg_qa.json"),
                "--out-md", str(self.root / "fg_qa.md"),
            ])
        self.assertEqual(rc, 1)

    def test_final_gate_blocked_on_manual_fallback_zero_silent_fallback(self):
        manual_dir = self.root / "manual_suggestions"
        manual_dir.mkdir(parents=True, exist_ok=True)
        (manual_dir / "item_1.json").write_text('{"reason": "table"}', encoding="utf-8")
        (self.root / "manual_fallback_checklist.md").write_text(
            "# Manual Fallback\n- [ ] Apply manual table edit\n",
            encoding="utf-8",
        )

        report = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            round_root=self.root,
            pdf_exporter=fake_pdf_exporter_ok,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["zero_silent_fallback"], "FAIL")
        self.assertEqual(report["reconciliation"]["manual_fallback"], 2)

        verify_path = self.root / "verify.json"
        verify_path.write_text(json.dumps(self.verify_report), encoding="utf-8")
        with patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok):
            rc = cli.main([
                "final-gate",
                "--verify-report", str(verify_path),
                "--docs", str(self.clean_docx),
                "--round-root", str(self.root),
                "--out-json", str(self.root / "fg_fb.json"),
                "--out-md", str(self.root / "fg_fb.md"),
            ])
        self.assertEqual(rc, 1)

    def test_final_gate_ready_with_explicit_waiver(self):
        (self.root / "manual_fallback_checklist.md").write_text(
            "# Manual Fallback\n- [ ] Apply manual table edit\n",
            encoding="utf-8",
        )
        verify_path = self.root / "verify.json"
        out_json = self.root / "fg_waived.json"
        out_md = self.root / "fg_waived.md"
        verify_path.write_text(json.dumps(self.verify_report), encoding="utf-8")
        coverage_path, coverage_approval_path = self.write_instruction_coverage_files()

        reason = "Approved by author for manual table edit"
        with patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok):
            rc = cli.main([
                "final-gate",
                "--verify-report", str(verify_path),
                "--docs", str(self.clean_docx),
                "--round-root", str(self.root),
                "--instruction-coverage", str(coverage_path),
                "--instruction-coverage-approval", str(coverage_approval_path),
                "--waive-fallback-reason", reason,
                "--out-json", str(out_json),
                "--out-md", str(out_md),
            ])
        self.assertEqual(rc, 0)
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(data["verdict"], "READY")
        self.assertTrue(data["waived"])
        self.assertEqual(data["waive_fallback_reason"], reason)
        self.assertEqual(data["gates"]["zero_silent_fallback"], "WAIVED")
        self.assertIn(reason, out_md.read_text(encoding="utf-8"))

    def test_final_gate_blocked_on_pdf_render_failure(self):
        report_fail = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            skip_word_com=False,
            pdf_exporter=fake_pdf_exporter_fail,
        )
        self.assertEqual(report_fail["verdict"], "BLOCKED")
        self.assertEqual(report_fail["gates"]["word_com_pdf"], "FAIL")

        report_invalid = evaluate_final_gate(
            plan=self.plan,
            verify_report=self.verify_report,
            apply_reports=self.apply_reports,
            docs=[self.clean_docx],
            skip_word_com=False,
            pdf_exporter=fake_pdf_exporter_invalid_bytes,
        )
        self.assertEqual(report_invalid["verdict"], "BLOCKED")
        self.assertEqual(report_invalid["gates"]["word_com_pdf"], "FAIL")

    def test_final_gate_cli_recomputes_verify_when_omitted(self):
        plan_path = self.root / "revision_plan.json"
        apply_path = self.root / "apply_report.json"
        out_json = self.root / "fg_recompute.json"
        out_md = self.root / "fg_recompute.md"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        apply_path.write_text(json.dumps(self.apply_reports), encoding="utf-8")
        coverage_path, coverage_approval_path = self.write_instruction_coverage_files()

        with (
            patch("co_reviewer_cli.verify_apply_report", return_value=self.verify_report) as mock_verify,
            patch("co_reviewer.final_gate.export_docx_to_pdf_word_com", side_effect=fake_pdf_exporter_ok),
        ):
            rc = cli.main([
                "final-gate",
                "--plan", str(plan_path),
                "--apply-report", str(apply_path),
                "--docs", str(self.clean_docx),
                "--out-json", str(out_json),
                "--out-md", str(out_md),
            ])
        self.assertEqual(rc, 0)
        mock_verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()

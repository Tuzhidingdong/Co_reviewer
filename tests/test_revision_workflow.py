import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import co_reviewer_cli as cli
from response_letter_docx import render_response_docx
from test_revision_engine import make_docx


class RevisionWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "input.docx"
        self.output = self.root / "output.docx"
        self.plan_path = self.root / "edits_plan.json"
        self.report_path = self.root / "apply_report.json"

    def tearDown(self):
        self.temp.cleanup()

    def _plan(self):
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [
                {
                    "id": "R1-C1",
                    "reviewer": "Reviewer 1",
                    "comment": "Please clarify the first statement.",
                    "proposed_response": "We have clarified the first statement.",
                    "status": "approved",
                    "targets": [{
                        "document": "Manuscript", "input_file": str(self.input),
                        "output_file": str(self.output), "p_idx": 0,
                        "location": "Section 1", "find": "old phrase",
                        "replace": "revised phrase"
                    }]
                },
                {
                    "id": "R2-C1",
                    "reviewer": "Reviewer 2",
                    "comment": "Please provide further detail.",
                    "proposed_response": "We will address this in a later iteration.",
                    "status": "planned",
                    "targets": [{
                        "document": "SI", "input_file": str(self.input),
                        "output_file": str(self.root / "si_revised.docx"), "p_idx": 0,
                        "location": "Section S1", "find": "another old phrase",
                        "replace": "another revised phrase"
                    }]
                }
            ]
        }
        for item in plan["revision_items"]:
            if item.get("status") == "approved":
                item["approval_fingerprint"] = cli.revision_item_fingerprint(item)
        return plan

    def test_approve_plan_parser_is_registered(self):
        args = cli.build_parser().parse_args([
            "approve-plan", "--plan", str(self.plan_path), "--ids", "R1-C1", "R2-C1"
        ])
        self.assertIs(args.func, cli.cmd_approve_plan)
        self.assertEqual(args.ids, ["R1-C1", "R2-C1"])

    def test_approve_plan_cli_seals_only_selected_items(self):
        plan = self._plan()
        for item in plan["revision_items"]:
            item["status"] = "planned"
            item.pop("approval_fingerprint", None)
        plan["approved"] = False
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        args = Namespace(plan=str(self.plan_path), ids=["R1-C1"])
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.cmd_approve_plan(args)
        self.assertEqual(code, 0)
        saved = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["approved"])
        first, second = saved["revision_items"]
        self.assertEqual(first["status"], "approved")
        self.assertEqual(first["approval_fingerprint"], cli.revision_item_fingerprint(first))
        self.assertEqual(second["status"], "planned")
        self.assertNotIn("approval_fingerprint", second)
        self.assertEqual(cli.validate_plan(saved), [])

    def test_approved_item_fails_closed_after_its_scope_is_changed(self):
        plan = self._plan()
        plan["revision_items"][0]["targets"][0]["replace"] = "unapproved replacement"
        errors = cli.validate_plan(plan)
        self.assertTrue(any("approval_fingerprint" in error for error in errors))
        with self.assertRaisesRegex(ValueError, "approval_fingerprint"):
            cli.apply_plan(plan, "Test Author")
        self.assertFalse(self.output.exists())

    def test_apply_only_approved_items_and_preserve_pending_items(self):
        make_docx(self.input, [[("old phrase and another old phrase", False)]])
        plan = self._plan()
        reports = cli.apply_plan(plan, "Test Author")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["edits_applied"], 1)
        self.assertEqual(
            [edit["revision_item_id"] for edit in reports[0]["results"]],
            ["R1-C1"]
        )
        # Only the applied item is mechanically checked; the planned item is not
        # treated as a missing/failed change in this iteration.
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_total"], 1)
        self.assertEqual(plan["revision_items"][1]["status"], "planned")

    def test_apply_refuses_when_no_item_is_approved(self):
        make_docx(self.input, [[("old phrase", False)]])
        plan = self._plan()
        plan["revision_items"][0]["status"] = "planned"
        with self.assertRaisesRegex(ValueError, "No revision items"):
            cli.apply_plan(plan, "Test Author")
        self.assertFalse(self.output.exists())

    def test_cli_apply_persists_item_state_and_leaves_other_items_pending(self):
        make_docx(self.input, [[("old phrase and another old phrase", False)]])
        plan = self._plan()
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        args = Namespace(
            plan=str(self.plan_path), report=str(self.report_path), author="Test Author"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.cmd_apply(args)
        self.assertEqual(code, 0)
        saved = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["revision_items"][0]["status"], "applied")
        self.assertEqual(saved["revision_items"][1]["status"], "planned")
        self.assertTrue(self.report_path.is_file())

    def test_incremental_iterations_preserve_docx_changes_and_apply_report_history(self):
        make_docx(self.input, [[("old phrase and another old phrase", False)]])
        plan = self._plan()
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")

        first_args = Namespace(
            plan=str(self.plan_path), report=str(self.report_path), author="Test Author"
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_apply(first_args), 0)

        after_first = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual(after_first["revision_items"][0]["status"], "applied")
        self.assertEqual(after_first["revision_items"][1]["status"], "planned")

        second_output = self.root / "output_round_02.docx"
        second_item = after_first["revision_items"][1]
        second_item["status"] = "approved"
        second_item["targets"][0]["input_file"] = str(self.output)
        second_item["targets"][0]["output_file"] = str(second_output)
        second_item["approval_fingerprint"] = cli.revision_item_fingerprint(second_item)
        self.plan_path.write_text(json.dumps(after_first), encoding="utf-8")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_apply(first_args), 0)

        final_plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [item["status"] for item in final_plan["revision_items"]],
            ["applied", "applied"]
        )
        history = json.loads(self.report_path.read_text(encoding="utf-8"))
        self.assertEqual(len(history), 2)
        self.assertTrue(self.output.is_file())
        self.assertTrue(second_output.is_file())

        verification = cli.verify_apply_report(final_plan, history)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_total"], 2)
        self.assertEqual(verification["targets_verified"], 2)

        with zipfile.ZipFile(second_output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            deleted = "".join(
                node.text or "" for node in paragraph.findall(".//w:del//w:delText", cli.NS)
            )
            inserted = "".join(
                node.text or "" for node in paragraph.findall(".//w:ins//w:t", cli.NS)
            )
            self.assertIn("old phrase", deleted)
            self.assertIn("another old phrase", deleted)
            self.assertIn("revised phrase", inserted)
            self.assertIn("another revised phrase", inserted)

    def test_response_docx_contains_verified_and_draft_sections(self):
        plan = self._plan()
        plan["revision_items"][0]["status"] = "verified"
        output = self.root / "Response_to_Reviewers.docx"
        rendered = render_response_docx(
            plan, output, manuscript_title="A sample manuscript", author="Corresponding Author"
        )
        self.assertTrue(Path(rendered).is_file())
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn("word/document.xml", archive.namelist())
        from docx import Document
        paragraphs = [p.text for p in Document(output).paragraphs]
        joined = "\n".join(paragraphs)
        self.assertIn("Response to Reviewers", joined)
        self.assertIn("Reviewer 1 — R1-C1", joined)
        self.assertIn("Response", joined)
        self.assertIn("Reviewer 2 — R2-C1", joined)
        self.assertIn("DRAFT (planned; not yet verified)", joined)
        self.assertIn("We will address this in a later iteration.", joined)

    def test_response_docx_does_not_overwrite_existing_file(self):
        plan = self._plan()
        output = self.root / "existing.docx"
        output.write_bytes(b"do not overwrite")
        with self.assertRaises(FileExistsError):
            render_response_docx(plan, output)
        self.assertEqual(output.read_bytes(), b"do not overwrite")

    def test_response_docx_rejects_non_docx_output(self):
        with self.assertRaisesRegex(ValueError, "end with .docx"):
            render_response_docx(self._plan(), self.root / "response.md")


if __name__ == "__main__":
    unittest.main()

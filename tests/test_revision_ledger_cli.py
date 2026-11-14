import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import co_reviewer_cli as cli
from docx import Document
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseUnit,
    RevisionLedger,
    revision_ledger_from_mapping,
)
from test_revision_engine import make_docx


class RevisionLedgerCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "Manuscript.docx"
        self.output = self.root / "Manuscript_revised.docx"
        self.plan_path = self.root / "revision_plan.json"
        self.report_path = self.root / "apply_report.json"
        self.ledger_path = self.root / "revision_ledger.json"

    def tearDown(self):
        self.temp.cleanup()

    def _ledger(self):
        make_docx(self.input, [[("The old phrase remains.", False)]])
        operation = PatchOperation.from_mapping({
            "operation_id": "OP-001",
            "document": "Manuscript",
            "input_file": str(self.input),
            "output_file": str(self.output),
            "part": "word/document.xml",
            "p_idx": 0,
            "find": "old phrase",
            "replace": "revised phrase",
            "location": "Results paragraph 1",
            "evidence_ids": ["E1"],
        })
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify the statement.",
            proposed_response="We have clarified the statement.",
            operations=[operation],
        )
        unit = ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify the statement.",
            response_text="We have clarified the statement.",
            linked_task_ids=["T1"],
            linked_patch_ids=["T1"],
            linked_targets=[{
                "document": "Manuscript",
                "location": "Results paragraph 1",
                "output_file": str(self.output),
            }],
            status="draft",
        )
        return RevisionLedger(
            revision_round_id="round-cli-integration",
            patches=[patch],
            response_units=[unit],
        )

    def test_patch_ledger_is_connected_to_preflight_apply_and_verify(self):
        ledger = self._ledger()
        cli.write_json_atomic(self.ledger_path, ledger.to_dict())

        plan = ledger.to_legacy_revision_plan()
        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"])
        cli.write_json_atomic(self.plan_path, plan)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_approve_plan(Namespace(
                plan=str(self.plan_path), ids=["T1"]
            )), 0)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_apply(Namespace(
                plan=str(self.plan_path),
                report=str(self.report_path),
                author="RevisionLedger integration",
                revision_ledger=str(self.ledger_path),
            )), 0)

        applied_ledger = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(applied_ledger.patches[0].status, "applied")
        self.assertEqual(len(applied_ledger.events), 1)
        self.assertTrue(self.output.is_file())

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_confirm_verified(Namespace(
                plan=str(self.plan_path),
                apply_report=str(self.report_path),
                ids=["T1"],
                semantic_review_confirmed=True,
                revision_ledger=str(self.ledger_path),
            )), 0)

        verified_ledger = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(verified_ledger.patches[0].status, "verified")
        self.assertEqual(len(verified_ledger.patches[0].verified_output_hashes), 1)
        self.assertEqual(len(verified_ledger.events), 2)
        # Patch verification does not silently promote the separate ResponseUnit.
        self.assertEqual(verified_ledger.response_units[0].status, "draft")

    def test_response_unit_renderer_uses_revision_ledger_and_keeps_draft_label(self):
        ledger = self._ledger()
        cli.write_json_atomic(self.ledger_path, ledger.to_dict())
        output = self.root / "Response_to_Reviewers.docx"
        args = Namespace(
            plan=None,
            revision_ledger=str(self.ledger_path),
            out=str(output),
            manuscript_title="Test manuscript",
            author="Corresponding Author",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_response_docx(args), 0)
        self.assertTrue(output.is_file())
        paragraphs = [item.text for item in Document(output).paragraphs]
        joined = "\n".join(paragraphs)
        self.assertIn("Response to Reviewers", joined)
        self.assertIn("Reviewer 1 — RU-T1-C1", joined)
        self.assertIn("DRAFT", joined)
        self.assertIn("Results paragraph 1", joined)

    def test_response_unit_is_not_rendered_final_before_final_pdf_line_mapping(self):
        ledger = self._ledger()
        unit = ledger.response_units[0]
        unit.status = "verified"
        unit.semantic_review_confirmed = True
        unit.verification_report_id = "semantic-review-001"
        plan = __import__(
            "co_reviewer.revision_models", fromlist=["response_units_to_legacy_plan"]
        ).response_units_to_legacy_plan(ledger.response_units, ledger.patches)
        self.assertEqual(plan["revision_items"][0]["status"], "location_pending")

    def test_build_revision_ledger_cli_persists_exact_patch_operations(self):
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-build-cli",
            "tasks": [{
                "task_id": "T2",
                "task_type": "local_revision",
                "source_comment_ids": ["C2"],
                "evidence": [{
                    "evidence_id": "E2",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_comment_id": "C2",
                    "source_excerpt": "Please clarify this statement.",
                    "evidence_type": "reviewer_comment",
                }],
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "replace",
                    "evidence_ids": ["E2"],
                }],
                "patch_operations": [{
                    "operation_id": "OP-2",
                    "document": "Manuscript",
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "find": "old phrase",
                    "replace": "new phrase",
                    "evidence_ids": ["E2"],
                }],
            }],
        }
        make_docx(self.input, [[("old phrase", False)]])
        workflow_path = self.root / "workflow_ledger.json"
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_build_revision_ledger(Namespace(
                workflow_ledger=str(workflow_path),
                document_paths=None,
                reviewer_comments=None,
                reviewer_map=None,
                out=str(self.ledger_path),
            )), 0)
        saved = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(len(saved.patches), 1)
        self.assertEqual(saved.patches[0].operations[0].find, "old phrase")
        self.assertEqual(len(saved.response_units), 1)

    def test_parser_requires_exactly_one_response_input(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["render-response"])
        parsed = parser.parse_args([
            "render-response", "--revision-ledger", str(self.ledger_path)
        ])
        self.assertEqual(parsed.revision_ledger, str(self.ledger_path))


if __name__ == "__main__":
    unittest.main()

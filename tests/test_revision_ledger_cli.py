import contextlib
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
    response_units_to_legacy_plan,
)
from test_revision_engine import make_docx


class RevisionLedgerCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "Manuscript.docx"
        self.output = self.root / "Manuscript_revised.docx"
        self.plan_path = self.root / "revision_plan.json"
        self.preflight_path = self.root / "preflight_report.json"
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
        cli.write_json_atomic(self.plan_path, plan)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_preflight(Namespace(
                plan=str(self.plan_path),
                out=str(self.preflight_path),
                author="RevisionLedger preflight",
                revision_ledger=str(self.ledger_path),
            )), 0)
        preflight = json.loads(self.preflight_path.read_text(encoding="utf-8"))
        self.assertTrue(preflight["passed"])
        preflighted = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(preflighted.patches[0].status, "preflighted")
        self.assertEqual(len(preflighted.events), 1)

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_approve_plan(Namespace(
                plan=str(self.plan_path),
                ids=["T1"],
                revision_ledger=str(self.ledger_path),
                actor="Human Author",
            )), 0)
        approved = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(approved.patches[0].status, "approved")
        self.assertEqual(len(approved.events), 2)

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
        self.assertEqual(len(applied_ledger.events), 3)
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
        self.assertEqual(len(verified_ledger.events), 4)
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

    def test_semantic_review_without_pdf_mapping_is_not_final_response(self):
        ledger = self._ledger()
        unit = ledger.response_units[0]
        unit.status = "verified"
        unit.semantic_review_confirmed = True
        unit.verification_report_id = "semantic-review-001"
        plan = response_units_to_legacy_plan(ledger.response_units, ledger.patches)
        self.assertEqual(plan["revision_items"][0]["status"], "location_pending")

    def test_build_revision_ledger_cli_persists_exact_patch_operations(self):
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-build-cli",
            "source_files": [],
            "documents": [],
            "object_registry": [],
            "events": [],
            "tasks": [{
                "task_id": "T2",
                "sequence": 1,
                "stage": "revision",
                "task_type": "local_revision",
                "title": "Clarify the statement",
                "source_comment_ids": ["C2"],
                "target_documents": ["Manuscript"],
                "evidence": [{
                    "evidence_id": "E2",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_comment_id": "C2",
                    "source_excerpt": "Please clarify this statement.",
                    "evidence_type": "reviewer_comment",
                    "match_status": "confirmed",
                }],
                "proposed_action": "Clarify the statement at the evidenced location.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "replace",
                    "evidence_ids": ["E2"],
                }],
                "dependencies": [],
                "target_groups": ["Manuscript|word/document.xml|p_idx=0"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "requires_user_approval": True,
                "status": "planned",
                "patch_operations": [{
                    "operation_id": "OP-2",
                    "document": "Manuscript",
                    "input_file": "Manuscript.docx",
                    "output_file": "Manuscript_revised.docx",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "find": "old phrase",
                    "replace": "new phrase",
                    "evidence_ids": ["E2"],
                }],
            }],
        }
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

    def test_preflight_and_approval_parsers_accept_revision_ledger(self):
        parser = cli.build_parser()
        parsed_preflight = parser.parse_args([
            "preflight-plan",
            "--plan", str(self.plan_path),
            "--revision-ledger", str(self.ledger_path),
        ])
        self.assertEqual(parsed_preflight.revision_ledger, str(self.ledger_path))
        parsed_approval = parser.parse_args([
            "approve-plan",
            "--plan", str(self.plan_path),
            "--ids", "T1",
            "--revision-ledger", str(self.ledger_path),
        ])
        self.assertEqual(parsed_approval.revision_ledger, str(self.ledger_path))

    def test_response_parser_requires_exactly_one_input(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["render-response"])
        parsed = parser.parse_args([
            "render-response", "--revision-ledger", str(self.ledger_path)
        ])
        self.assertEqual(parsed.revision_ledger, str(self.ledger_path))


if __name__ == "__main__":
    unittest.main()

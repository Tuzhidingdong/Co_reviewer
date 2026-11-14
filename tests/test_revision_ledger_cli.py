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
from co_reviewer.identity import sha256_file
from docx import Document
from co_reviewer.identity import canonical_fingerprint, sha256_file
from co_reviewer.workflow_engine import select_ready_tasks, validate_workflow_ledger, workflow_scope_fingerprint
from co_reviewer.revision_models import (
    RevisionEvent,
    Patch,
    PatchOperation,
    ResponseUnit,
    RevisionDocument,
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
            self.assertEqual(cli.cmd_authorize_workflow_tasks(Namespace(
                workflow_ledger=str(workflow_path),
                ids=["T2"],
                actor="Human Author",
                plan_approved=True,
            )), 0)
        authorized_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        self.assertEqual(authorized_workflow["tasks"][0]["status"], "ready")
        self.assertEqual(len(authorized_workflow["events"]), 1)
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

    def test_working_clean_view_cli_persists_immutable_projection_in_ledger(self):
        ledger = self._ledger()
        cli.apply_document(
            str(self.input),
            str(self.output),
            [{
                "p_idx": 0,
                "find": "old phrase",
                "replace": "revised phrase",
                "part": "word/document.xml",
            }],
            "Working View CLI Test",
        )
        marked_bytes = self.output.read_bytes()
        ledger.documents = [RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-manuscript-1",
            role="Manuscript",
            input_file=str(self.input),
            output_file=str(self.output),
            source_sha256=sha256_file(self.input),
            output_sha256=sha256_file(self.output),
        )]
        cli.write_json_atomic(self.ledger_path, ledger.to_dict())

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_create_working_clean_views(Namespace(
                ledger=str(self.ledger_path),
                suffix=".working_clean",
                actor="Working View Test",
            )), 0)

        restored = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        document = restored.documents[0]
        view_path = Path(document.working_clean_file)
        self.assertTrue(view_path.is_file())
        self.assertEqual(self.output.read_bytes(), marked_bytes)
        self.assertEqual(document.output_sha256, sha256_file(self.output))
        self.assertEqual(document.working_clean_sha256, sha256_file(view_path))
        with __import__("zipfile").ZipFile(view_path) as archive:
            root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(
                archive.read("word/document.xml")
            )
            self.assertIsNone(root.find(".//w:ins", cli.NS))
            self.assertIsNone(root.find(".//w:del", cli.NS))
            paragraph = root.find(".//w:p", cli.NS)
            self.assertEqual(cli.visible_text(paragraph), "The revised phrase remains.")

    def test_supersede_patch_cli_reopens_response_and_writes_new_pending_patch(self):
        ledger = self._ledger()
        cli.write_json_atomic(self.ledger_path, ledger.to_dict())
        replacement_path = self.root / "replacement_patch.json"
        replacement_output = self.root / "Manuscript_r2.docx"
        replacement_path.write_text(json.dumps({
            "patch_id": "T1-R2",
            "task_id": "T1",
            "reviewer": "Reviewer 1",
            "comment": "Please clarify the statement.",
            "proposed_response": "We revised the clarification again.",
            "supersedes_patch_id": "T1",
            "operations": [{
                "operation_id": "OP-R2",
                "document": "Manuscript",
                "input_file": str(self.input),
                "output_file": str(replacement_output),
                "part": "word/document.xml",
                "p_idx": 0,
                "find": "old phrase",
                "replace": "more precise phrase",
                "evidence_ids": ["E1"],
            }],
        }), encoding="utf-8")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_supersede_patch(Namespace(
                ledger=str(self.ledger_path),
                previous_patch_id="T1",
                replacement_patch=str(replacement_path),
                actor="Human Author",
                reason="The original wording needs greater precision.",
            )), 0)

        updated = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            {patch.patch_id: patch.status for patch in updated.patches},
            {"T1": "superseded", "T1-R2": "planned"},
        )
        self.assertEqual(updated.response_units[0].linked_patch_ids, ["T1-R2"])
        self.assertEqual(updated.response_units[0].status, "draft")
        self.assertEqual(
            [item["id"] for item in updated.to_legacy_revision_plan()["revision_items"]],
            ["T1-R2"],
        )

    def test_response_unit_cli_requires_semantic_review_pdf_map_and_completion_confirmation(self):
        unit = ResponseUnit(
            response_unit_id="RU-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain the mechanism.",
            response_text="We have explained the mechanism.",
        )
        ledger = RevisionLedger(
            revision_round_id="round-response-cli",
            response_units=[unit],
        )
        cli.write_json_atomic(self.ledger_path, ledger.to_dict())

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_confirm_response_units(Namespace(
                ledger=str(self.ledger_path),
                ids=["RU-C1"],
                verification_report_id="semantic-review-C1",
                actor="Human Author",
                semantic_review_confirmed=True,
            )), 0)
        semantically_reviewed = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(semantically_reviewed.response_units[0].status, "verified")

        pdf_path = self.root / "Manuscript_final.pdf"
        pdf_bytes = b"%PDF-1.4\nsynthetic final pagination output\n%%EOF"
        pdf_path.write_bytes(pdf_bytes)
        location_map = {
            "location_map_id": "map-response-C1",
            "response_units": [{
                "response_unit_id": "RU-C1",
                "location_map_fingerprint": "a" * 64,
                "final_locations": [{
                    "document": "Manuscript",
                    "output_file": str(self.output),
                    "pdf_file": str(pdf_path),
                    "page": 4,
                    "line_start": 18,
                    "line_end": 22,
                    "pdf_sha256": __import__("hashlib").sha256(pdf_bytes).hexdigest(),
                    "location_map_id": "map-response-C1",
                    "mapping_fingerprint": "a" * 64,
                }],
            }],
        }
        location_map_path = self.root / "location-map.json"
        location_map_path.write_text(json.dumps(location_map), encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_record_response_locations(Namespace(
                ledger=str(self.ledger_path),
                location_map=str(location_map_path),
                actor="Word/PDF Location Mapper",
            )), 0)
        mapped = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(mapped.response_units[0].status, "location_mapped")
        self.assertEqual(mapped.response_units[0].final_locations[0].line_start, 18)
        self.assertEqual(
            mapped.response_units[0].location_map_file_sha256,
            __import__("hashlib").sha256(location_map_path.read_bytes()).hexdigest(),
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_complete_response_units(Namespace(
                ledger=str(self.ledger_path),
                ids=["RU-C1"],
                actor="Human Author",
                ready_for_submission_confirmed=True,
            )), 0)
        completed = revision_ledger_from_mapping(
            json.loads(self.ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(completed.response_units[0].status, "completed")
        self.assertEqual(len(completed.events), 3)

    def test_verified_patch_sync_unlocks_dependent_task_and_incremental_batch_merges(self):
        source = self.root / "Manuscript.docx"
        marked = self.root / "Manuscript_r1.docx"
        final_docx = self.root / "Manuscript_r2.docx"
        source.write_bytes(b"source docx bytes")
        marked.write_bytes(b"marked revision one")
        final_docx.write_bytes(b"marked revision two")
        source_hash = sha256_file(source)
        marked_hash = sha256_file(marked)

        def make_task(task_id, status, dependency, input_path, output_path, p_idx):
            return {
                "task_id": task_id,
                "sequence": 1 if task_id == "T1" else 2,
                "stage": "revision",
                "task_type": "local_revision",
                "title": f"Revise {task_id}",
                "source_comment_ids": [f"C-{task_id}"],
                "target_documents": ["Manuscript"],
                "evidence": [{
                    "evidence_id": f"E-{task_id}",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_comment_id": f"C-{task_id}",
                    "source_excerpt": f"Please revise {task_id}.",
                    "evidence_type": "reviewer_comment",
                    "match_status": "confirmed",
                }],
                "proposed_action": f"Revise the text for {task_id}.",
                "proposed_response": f"We revised {task_id}.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": p_idx,
                    "action": "replace",
                    "evidence_ids": [f"E-{task_id}"],
                }],
                "dependencies": [dependency] if dependency else [],
                "target_groups": [f"Manuscript|word/document.xml|p_idx={p_idx}"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "requires_user_approval": True,
                "status": status,
                "patch_id": task_id,
                "patch_operations": [{
                    "operation_id": f"{task_id}-OP1",
                    "document": "Manuscript",
                    "input_file": str(input_path),
                    "output_file": str(output_path),
                    "part": "word/document.xml",
                    "p_idx": p_idx,
                    "find": f"old text {task_id}",
                    "replace": f"new text {task_id}",
                    "evidence_ids": [f"E-{task_id}"],
                }],
            }

        task1 = make_task("T1", "ready", None, source, marked, 1)
        task2 = make_task("T2", "planned", "T1", marked, final_docx, 2)
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-dag-sync",
            "source_files": [{
                "role": "Manuscript",
                "file": str(source),
                "sha256": source_hash,
            }],
            "documents": [],
            "object_registry": [],
            "tasks": [task1, task2],
            "events": [{
                "event_id": "EV-T1-READY",
                "task_id": "T1",
                "timestamp_utc": "2026-11-14T12:00:00+08:00",
                "event_type": "status_transition",
                "from_status": "planned",
                "to_status": "ready",
                "reason": "Author approved T1.",
                "actor": "Human Author",
                "fingerprint": workflow_scope_fingerprint(task1),
            }],
        }
        self.assertEqual(validate_workflow_ledger(workflow), [])
        workflow_path = self.root / "workflow_ledger.json"
        workflow_path.write_text(json.dumps(workflow), encoding="utf-8")

        patch1 = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please revise T1.",
            proposed_response="We revised T1.",
            operations=[PatchOperation.from_mapping({
                **task1["patch_operations"][0],
                "source_sha256": source_hash,
            })],
            status="verified",
            preflight_fingerprint="a" * 64,
            preflight_report_id="preflight-T1",
            approval_fingerprint="b" * 64,
            apply_report_ids=["apply-T1"],
            verification_report_id="verify-T1",
            verified_output_hashes=[{
                "path": str(marked),
                "sha256": marked_hash,
            }],
        )
        response1 = ResponseUnit(
            response_unit_id="RU-T1-C-T1",
            reviewer="Reviewer 1",
            comment_id="C-T1",
            comment_text="Please revise T1.",
            response_text="We revised T1.",
            linked_task_ids=["T1"],
            linked_patch_ids=["T1"],
        )
        timestamp = "2026-11-14T12:00:00+08:00"
        revision = RevisionLedger(
            revision_round_id="round-dag-sync",
            patches=[patch1],
            response_units=[response1],
            events=[
                RevisionEvent("EV-PF", "T1", "patch", "preflight", timestamp, "Preflight"),
                RevisionEvent("EV-AP", "T1", "patch", "approval", timestamp, "Human Author"),
                RevisionEvent("EV-DO", "T1", "patch", "patch_applied", timestamp, "DOCX Writer"),
                RevisionEvent("EV-VE", "T1", "patch", "verification", timestamp, "Human Author"),
            ],
            workflow_ledger_fingerprint=canonical_fingerprint(workflow),
            workflow_task_fingerprints={
                "T1": workflow_scope_fingerprint(task1),
                "T2": workflow_scope_fingerprint(task2),
            },
        )
        revision_path = self.root / "revision_ledger.json"
        cli.write_json_atomic(revision_path, revision.to_dict())

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_sync_workflow_ledger(Namespace(
                workflow_ledger=str(workflow_path),
                revision_ledger=str(revision_path),
            )), 0)
        synced = json.loads(workflow_path.read_text(encoding="utf-8"))
        self.assertEqual(synced["tasks"][0]["status"], "verified")
        self.assertEqual(synced["tasks"][1]["status"], "planned")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_authorize_workflow_tasks(Namespace(
                workflow_ledger=str(workflow_path),
                ids=["T2"],
                actor="Human Author",
                plan_approved=True,
            )), 0)
        authorized = json.loads(workflow_path.read_text(encoding="utf-8"))
        self.assertEqual(authorized["tasks"][1]["status"], "ready")
        self.assertEqual(select_ready_tasks(authorized["tasks"]), ["T2"])

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_build_revision_ledger(Namespace(
                workflow_ledger=str(workflow_path),
                document_paths=None,
                reviewer_comments=None,
                reviewer_map=None,
                task_ids=["T2"],
                out=str(revision_path),
            )), 0)
        merged = revision_ledger_from_mapping(
            json.loads(revision_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            {patch.patch_id: patch.status for patch in merged.patches},
            {"T1": "verified", "T2": "planned"},
        )
        self.assertEqual(merged.revision_round_id, "round-dag-sync")
        self.assertEqual(merged.workflow_task_fingerprints["T1"], workflow_scope_fingerprint(task1))

    def test_workflow_task_authorization_parser_requires_explicit_plan_approval(self):
        parser = cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                "approve-workflow-tasks",
                "--workflow-ledger", str(self.root / "workflow.json"),
                "--ids", "T1",
            ])
        parsed = parser.parse_args([
            "approve-workflow-tasks",
            "--workflow-ledger", str(self.root / "workflow.json"),
            "--ids", "T1",
            "--plan-approved",
        ])
        self.assertTrue(parsed.plan_approved)

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

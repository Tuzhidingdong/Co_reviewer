from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
TESTS = Path(__file__).resolve().parent
for candidate in (SCRIPTS, TESTS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import co_reviewer_cli as cli
from co_reviewer.identity import sha256_file
from co_reviewer.revision_models import (
    revision_ledger_from_mapping,
    response_units_to_legacy_plan,
)
from co_reviewer.workflow_engine import validate_workflow_ledger
from test_revision_engine import make_docx
from validate_decision_plan import main as validate_decision_plan_main


def add_native_comment(
    path: Path,
    *,
    comment_id: str,
    reviewer: str,
    comment_text: str,
) -> None:
    """Add a minimal native Word comment and range to a synthetic DOCX."""
    with zipfile.ZipFile(path, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}

    document_root = cli.ET.fromstring(members["word/document.xml"])
    paragraph = document_root.find(".//w:p", cli.NS)
    if paragraph is None:
        raise AssertionError("Synthetic reviewer DOCX has no paragraph.")
    first_run = paragraph.find("w:r", cli.NS)
    if first_run is None:
        raise AssertionError("Synthetic reviewer paragraph has no text run.")

    marker_index = paragraph.index(first_run)
    range_start = cli.ET.Element(
        cli.qn("commentRangeStart"), {cli.qn("id"): comment_id}
    )
    range_end = cli.ET.Element(
        cli.qn("commentRangeEnd"), {cli.qn("id"): comment_id}
    )
    paragraph.insert(marker_index, range_start)
    paragraph.insert(marker_index + 2, range_end)
    reference_run = cli.ET.Element(cli.qn("r"))
    cli.ET.SubElement(
        reference_run,
        cli.qn("commentReference"),
        {cli.qn("id"): comment_id},
    )
    paragraph.insert(marker_index + 3, reference_run)

    comments_root = cli.ET.Element(cli.qn("comments"))
    comment = cli.ET.SubElement(
        comments_root,
        cli.qn("comment"),
        {
            cli.qn("id"): comment_id,
            cli.qn("author"): reviewer,
            cli.qn("initials"): "R1",
            cli.qn("date"): "2026-11-14T00:00:00Z",
        },
    )
    comment_paragraph = cli.ET.SubElement(comment, cli.qn("p"))
    comment_run = cli.ET.SubElement(comment_paragraph, cli.qn("r"))
    comment_value = cli.ET.SubElement(comment_run, cli.qn("t"))
    comment_value.text = comment_text

    members["word/document.xml"] = cli.ET.tostring(
        document_root, encoding="utf-8", xml_declaration=True
    )
    members["word/comments.xml"] = cli.ET.tostring(
        comments_root, encoding="utf-8", xml_declaration=True
    )

    temporary = path.with_name(path.stem + ".commented.docx")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    temporary.replace(path)


class FullWorkflowIntegrationTests(unittest.TestCase):
    """One synthetic round connecting the existing P1 workflow adapters."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call_silently(self, function, args) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return function(args)

    def test_one_review_round_connects_inventory_through_response_draft(self) -> None:
        reviewer_file = self.root / "Reviewers.docx"
        manuscript_input = self.root / "Manuscript.docx"
        si_input = self.root / "SI.docx"
        manuscript_output = self.root / "Manuscript_marked.docx"
        si_output = self.root / "SI_marked.docx"

        selected_text = "The selected sample is ZB-01."
        reviewer_comment = (
            "Please keep this sample identifier consistent in the Manuscript and SI."
        )
        make_docx(reviewer_file, [[(selected_text, False)]])
        add_native_comment(
            reviewer_file,
            comment_id="1",
            reviewer="Reviewer 1",
            comment_text=reviewer_comment,
        )
        make_docx(manuscript_input, [[(selected_text, False)]])
        make_docx(si_input, [[("Supporting Information specimen code: ZB-01.", False)]])

        original_bytes = {
            path: path.read_bytes()
            for path in (reviewer_file, manuscript_input, si_input)
        }

        inventory_path = self.root / "workspace_inventory.json"
        audit_path = self.root / "review_audit_report.json"
        queue_path = self.root / "review_decision_queue.json"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                cli.cmd_audit(
                    type("Args", (), {
                        "rev": str(reviewer_file),
                        "man": str(manuscript_input),
                        "si": str(si_input),
                        "out": str(self.root / "workspace_digest.md"),
                        "json_out": str(inventory_path),
                        "audit_md_out": str(self.root / "review_audit_report.md"),
                        "audit_out": str(audit_path),
                        "decision_queue_out": str(queue_path),
                        "comment_ids": None,
                        "comment_author": None,
                        "cache_dir": str(self.root / ".cache"),
                        "usage_ledger": None,
                        "refresh_cache": False,
                        "max_items_per_section": 50,
                    })(),
                ),
                0,
            )

        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(audit["comment_count"], 1)
        match = audit["comment_matches"][0]
        self.assertEqual(match["comment_id"], "1")
        self.assertEqual(match["target_status"], "exact_unique")
        self.assertEqual(
            {row["document"] for row in match["confirmed_matches"]},
            {"Manuscript"},
        )

        reports_by_role = {row["role"]: row for row in inventory}
        reviewer_record = reports_by_role["Reviewers"]["comments"][0]
        manuscript_paragraph = reports_by_role["Manuscript"]["paragraphs"][0]
        si_paragraph = reports_by_role["SI"]["paragraphs"][0]
        source_files = audit["source_files"]

        decision_plan = {
            "schema_version": "1.0",
            "comment_scope": "all_comments",
            "source_files": source_files,
            "tasks": [{
                "task_id": "T1",
                "sequence": 1,
                "title": "Keep the sample identifier consistent",
                "task_type": "cross_document_consistency",
                "source_comment_ids": ["1"],
                "target_documents": ["Manuscript", "SI"],
                "evidence": [
                    {
                        "evidence_id": "E-COMMENT-1",
                        "document": "Reviewers",
                        "part": "word/comments.xml",
                        "p_idx": reviewer_record["comment_xml_p_idx"],
                        "source_comment_id": "1",
                        "source_excerpt": reviewer_comment,
                        "evidence_type": "reviewer_comment",
                        "match_status": "confirmed",
                    },
                    {
                        "evidence_id": "E-MANUSCRIPT-1",
                        "document": "Manuscript",
                        "part": manuscript_paragraph["part"],
                        "p_idx": manuscript_paragraph["p_idx"],
                        "para_id": manuscript_paragraph.get("para_id"),
                        "source_excerpt": selected_text,
                        "evidence_type": "paragraph",
                        "match_status": "confirmed",
                    },
                    {
                        "evidence_id": "E-SI-1",
                        "document": "SI",
                        "part": si_paragraph["part"],
                        "p_idx": si_paragraph["p_idx"],
                        "para_id": si_paragraph.get("para_id"),
                        "source_excerpt": si_paragraph["text"],
                        "evidence_type": "paragraph",
                        "match_status": "confirmed",
                    },
                ],
                "proposed_action": (
                    "Replace the sample identifier in both documents with the approved "
                    "identifier while preserving the reviewer-linked evidence."
                ),
                "targets": [
                    {
                        "document": "Manuscript",
                        "part": manuscript_paragraph["part"],
                        "p_idx": manuscript_paragraph["p_idx"],
                        "para_id": manuscript_paragraph.get("para_id"),
                        "action": "replace",
                        "evidence_ids": ["E-MANUSCRIPT-1"],
                    },
                    {
                        "document": "SI",
                        "part": si_paragraph["part"],
                        "p_idx": si_paragraph["p_idx"],
                        "para_id": si_paragraph.get("para_id"),
                        "action": "replace",
                        "evidence_ids": ["E-SI-1"],
                    },
                ],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.99,
                "status": "pending",
                "requires_user_approval": True,
            }],
        }

        decision_plan_path = self.root / "revision_decision_plan.json"
        audit_workflow_path = self.root / "workflow_ledger.json"
        decision_plan_path.write_text(
            json.dumps(decision_plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.assertEqual(
            validate_decision_plan_main([
                "--plan", str(decision_plan_path),
                "--audit", str(audit_path),
                "--inventory", str(inventory_path),
                "--workflow-ledger-out", str(audit_workflow_path),
                "--revision-round-id", "round-e2e-synthetic",
            ]),
            0,
        )

        workflow = json.loads(audit_workflow_path.read_text(encoding="utf-8"))
        task = workflow["tasks"][0]
        task["patch_id"] = "T1"
        task["proposed_response"] = (
            "We have aligned the sample identifier in the Manuscript and Supporting Information."
        )
        task["patch_operations"] = [
            {
                "operation_id": "T1-OP-MANUSCRIPT",
                "document": "Manuscript",
                "input_file": str(manuscript_input),
                "output_file": str(manuscript_output),
                "part": "word/document.xml",
                "p_idx": manuscript_paragraph["p_idx"],
                "find": "ZB-01",
                "replace": "ZB-02",
                "location": "Manuscript body paragraph 1",
                "evidence_ids": ["E-MANUSCRIPT-1"],
            },
            {
                "operation_id": "T1-OP-SI",
                "document": "SI",
                "input_file": str(si_input),
                "output_file": str(si_output),
                "part": "word/document.xml",
                "p_idx": si_paragraph["p_idx"],
                "find": "ZB-01",
                "replace": "ZB-02",
                "location": "SI body paragraph 1",
                "evidence_ids": ["E-SI-1"],
            },
        ]
        errors = validate_workflow_ledger(workflow)
        self.assertEqual(errors, [])
        audit_workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.assertEqual(
            self.call_silently(
                cli.cmd_authorize_workflow_tasks,
                type("Args", (), {
                    "workflow_ledger": str(audit_workflow_path),
                    "ids": ["T1"],
                    "actor": "Human Author",
                    "plan_approved": True,
                })(),
            ),
            0,
        )

        revision_ledger_path = self.root / "revision_ledger.json"
        self.assertEqual(
            self.call_silently(
                cli.cmd_build_revision_ledger,
                type("Args", (), {
                    "workflow_ledger": str(audit_workflow_path),
                    "document_paths": None,
                    "reviewer_comments": None,
                    "reviewer_map": None,
                    "task_ids": ["T1"],
                    "out": str(revision_ledger_path),
                })(),
            ),
            0,
        )
        ledger = revision_ledger_from_mapping(
            json.loads(revision_ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(len(ledger.patches), 1)
        self.assertEqual(len(ledger.patches[0].operations), 2)
        self.assertEqual(len(ledger.response_units), 1)
        self.assertEqual(
            set(ledger.response_units[0].linked_patch_ids),
            {"T1"},
        )

        revision_plan_path = self.root / "revision_plan.json"
        self.assertEqual(
            self.call_silently(
                cli.cmd_export_revision_plan,
                type("Args", (), {
                    "ledger": str(revision_ledger_path),
                    "out": str(revision_plan_path),
                })(),
            ),
            0,
        )
        preflight_path = self.root / "preflight_report.json"
        self.assertEqual(
            self.call_silently(
                cli.cmd_preflight,
                type("Args", (), {
                    "plan": str(revision_plan_path),
                    "out": str(preflight_path),
                    "author": "Synthetic Integration",
                    "revision_ledger": str(revision_ledger_path),
                })(),
            ),
            0,
        )
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        self.assertTrue(preflight["passed"])
        self.assertEqual(preflight["targets_preflighted"], 2)

        self.assertEqual(
            self.call_silently(
                cli.cmd_approve_plan,
                type("Args", (), {
                    "plan": str(revision_plan_path),
                    "ids": ["T1"],
                    "revision_ledger": str(revision_ledger_path),
                    "actor": "Human Author",
                })(),
            ),
            0,
        )
        apply_report_path = self.root / "apply_report.json"
        self.assertEqual(
            self.call_silently(
                cli.cmd_apply,
                type("Args", (), {
                    "plan": str(revision_plan_path),
                    "report": str(apply_report_path),
                    "author": "Synthetic Integration",
                    "revision_ledger": str(revision_ledger_path),
                })(),
            ),
            0,
        )
        self.assertTrue(manuscript_output.is_file())
        self.assertTrue(si_output.is_file())
        self.assertEqual(
            {path: path.read_bytes() for path in original_bytes},
            original_bytes,
        )

        for output in (manuscript_output, si_output):
            with zipfile.ZipFile(output, "r") as archive:
                document = cli.ET.fromstring(archive.read("word/document.xml"))
                self.assertTrue(document.findall(".//w:del", cli.NS))
                self.assertTrue(document.findall(".//w:ins", cli.NS))

        verification_path = self.root / "verification_report.json"
        self.assertEqual(
            self.call_silently(
                cli.cmd_verify,
                type("Args", (), {
                    "plan": str(revision_plan_path),
                    "apply_report": str(apply_report_path),
                    "out": str(verification_path),
                })(),
            ),
            0,
        )
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_verified"], 2)

        self.assertEqual(
            self.call_silently(
                cli.cmd_confirm_verified,
                type("Args", (), {
                    "plan": str(revision_plan_path),
                    "apply_report": str(apply_report_path),
                    "ids": ["T1"],
                    "semantic_review_confirmed": True,
                    "revision_ledger": str(revision_ledger_path),
                })(),
            ),
            0,
        )
        self.assertEqual(
            self.call_silently(
                cli.cmd_sync_workflow_ledger,
                type("Args", (), {
                    "workflow_ledger": str(audit_workflow_path),
                    "revision_ledger": str(revision_ledger_path),
                })(),
            ),
            0,
        )
        workflow_after_sync = json.loads(
            audit_workflow_path.read_text(encoding="utf-8")
        )
        self.assertEqual(workflow_after_sync["tasks"][0]["status"], "verified")

        working_clean_dir = self.root / "working_clean"
        self.assertEqual(
            self.call_silently(
                cli.cmd_create_working_clean_views,
                type("Args", (), {
                    "ledger": str(revision_ledger_path),
                    "suffix": ".working_clean",
                    "actor": "Synthetic Integration",
                })(),
            ),
            0,
        )
        verified_ledger = revision_ledger_from_mapping(
            json.loads(revision_ledger_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(len(verified_ledger.documents), 2)
        for document in verified_ledger.documents:
            clean_path = Path(document.working_clean_file)
            self.assertTrue(clean_path.is_file())
            with zipfile.ZipFile(clean_path, "r") as archive:
                clean_xml = cli.ET.fromstring(archive.read("word/document.xml"))
                self.assertFalse(clean_xml.findall(".//w:ins", cli.NS))
                self.assertFalse(clean_xml.findall(".//w:del", cli.NS))
                self.assertIn("ZB-02", cli.visible_text(clean_xml))

        self.assertEqual(
            self.call_silently(
                cli.cmd_confirm_response_units,
                type("Args", (), {
                    "ledger": str(revision_ledger_path),
                    "ids": [verified_ledger.response_units[0].response_unit_id],
                    "verification_report_id": "synthetic-response-review",
                    "actor": "Human Author",
                    "semantic_review_confirmed": True,
                })(),
            ),
            0,
        )
        response_ledger = revision_ledger_from_mapping(
            json.loads(revision_ledger_path.read_text(encoding="utf-8"))
        )
        response_unit = response_ledger.response_units[0]
        self.assertEqual(response_unit.status, "verified")
        self.assertEqual(response_unit.final_locations, [])
        response_plan = response_units_to_legacy_plan(
            response_ledger.response_units,
            response_ledger.patches,
        )
        self.assertEqual(
            response_plan["revision_items"][0]["status"],
            "location_pending",
        )

        response_path = self.root / "Response_to_Reviewers.md"
        self.assertEqual(
            self.call_silently(
                cli.cmd_response,
                type("Args", (), {
                    "plan": None,
                    "revision_ledger": str(revision_ledger_path),
                    "out": str(response_path),
                })(),
            ),
            0,
        )
        response_text = response_path.read_text(encoding="utf-8")
        self.assertIn("SEMANTIC REVIEWED; FINAL PDF PAGE/LINE MAPPING PENDING", response_text)
        self.assertNotIn("**Response**", response_text)
        self.assertEqual(
            {path: path.read_bytes() for path in original_bytes},
            original_bytes,
        )

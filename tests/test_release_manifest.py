from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.docx_quality import audit_docx_quality
from co_reviewer.release_manifest import (
    ReleaseGateError,
    _require_release_quality_status,
    _require_successful_sequence_audit,
    _require_consistent_sequence_audits,
    _require_bound_final_quality_report,
    _require_word_page_count,
    _validate_release_batch_scope,
    audit_patch_operation_deltas,
    build_final_release_manifest,
)
from co_reviewer.response_renderer import render_response_letter
from co_reviewer.revision_models import (
    ResponseUnit,
    RevisionLedger,
    response_units_to_legacy_plan,
)
from co_reviewer.workflow_engine import build_workflow_ledger, transition_task
from response_letter_docx import render_response_docx


STAMP = "2026-11-15T08:00:00+08:00"


class ReleaseManifestGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Manuscript.docx"
        self.source.write_bytes(b"synthetic source document")
        self.workflow = build_workflow_ledger(
            {
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                }],
                "tasks": [{
                    "task_id": "T1",
                    "sequence": 1,
                    "task_type": "response_only",
                    "title": "Respond to reviewer comment",
                    "source_comment_ids": ["C1"],
                    "target_documents": [],
                    "evidence": [{
                        "evidence_id": "E-C1",
                        "document": "Reviewers",
                        "part": "word/comments.xml",
                        "p_idx": 0,
                        "source_excerpt": "Please clarify this statement.",
                        "evidence_type": "reviewer_comment",
                        "source_comment_id": "C1",
                        "match_status": "confirmed",
                    }],
                    "proposed_action": "Provide an explanatory response.",
                    "targets": [],
                    "dependencies": [],
                    "readiness": "ready_for_author_approval",
                    "unresolved_questions": [],
                    "confidence": 1.0,
                    "status": "pending",
                    "requires_user_approval": True,
                }],
            },
            "round-release-tests",
            STAMP,
        )
        task = self.workflow["tasks"][0]
        ready_task = transition_task(task, "ready")
        completed_task = transition_task(
            ready_task,
            "completed",
            evidence={
                "completion_record_id": "synthetic-response-only-completion",
                "completion_record_fingerprint": hashlib.sha256(
                    b"synthetic-response-only-completion"
                ).hexdigest(),
                "completed_at_utc": STAMP,
            },
        )
        self.workflow["tasks"][0] = completed_task
        self.revision_path = self.root / "revision_ledger.json"
        self.workflow_path = self.root / "workflow_ledger.json"
        self.revision_path.write_text("{}", encoding="utf-8")
        self.workflow_path.write_text(
            json.dumps(self.workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def _unit(self, *, status="verified", comment_id="C1"):
        return ResponseUnit(
            response_unit_id=f"RU-{comment_id}",
            reviewer="Reviewer 1",
            comment_id=comment_id,
            comment_text="Please clarify this statement.",
            response_text="We have clarified the statement.",
            linked_task_ids=["T1"],
            disposition="reply_only",
            location_requirement="not_required",
            resolution_reason="The response is conceptual explanation and makes no manuscript-change claim.",
            status=status,
            semantic_review_confirmed=True,
            verification_report_id="semantic-review-01",
            completed_at_utc=STAMP if status == "completed" else None,
        )

    def _call(self, unit):
        ledger = RevisionLedger(
            revision_round_id="round-release-tests",
            response_units=[unit],
        )
        self.revision_path.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return build_final_release_manifest(
            ledger,
            self.workflow,
            revision_ledger_path=self.revision_path,
            workflow_ledger_path=self.workflow_path,
            response_letter_md=self.root / "missing.md",
            response_letter_docx=self.root / "missing.docx",
            response_letter_quality=self.root / "missing-quality.json",
        )

    def test_release_gate_accepts_only_schema_defined_quality_statuses(self):
        self.assertEqual(
            _require_release_quality_status({"status": "PASS"}, "Synthetic DOCX"),
            "PASS",
        )
        self.assertEqual(
            _require_release_quality_status({"status": "PASS_WITH_WARNINGS"}, "Synthetic DOCX"),
            "PASS_WITH_WARNINGS",
        )
        for status in ("FAIL", "BLOCKED", "PENDING", None):
            with self.subTest(status=status):
                with self.assertRaisesRegex(ReleaseGateError, "expected PASS or PASS_WITH_WARNINGS"):
                    _require_release_quality_status({"status": status}, "Synthetic DOCX")

    def test_release_gate_rejects_unknown_or_failed_sequence_audit_statuses(self):
        manual_review = {
            "status": "manual_review",
            "detected": {"Figure": [], "Table": []},
            "issues": [],
            "manual_review": [{"reason": "No native SEQ fields were found."}],
        }
        self.assertEqual(
            _require_successful_sequence_audit(manual_review, "Manuscript")["status"],
            "manual_review",
        )
        passing = {
            "status": "pass",
            "detected": {"Figure": [{"value": 1}], "Table": [{"value": 1}]},
            "issues": [],
            "manual_review": [],
        }
        self.assertEqual(_require_successful_sequence_audit(passing, "Manuscript")["status"], "pass")
        for payload in (
            {"status": "fail", "detected": {"Figure": [], "Table": []}, "issues": [{}], "manual_review": []},
            {"status": "FAIL", "detected": {"Figure": [], "Table": []}, "issues": [], "manual_review": []},
            {"status": "BLOCKED", "detected": {"Figure": [], "Table": []}, "issues": [], "manual_review": []},
            {"status": None, "detected": {"Figure": [], "Table": []}, "issues": [], "manual_review": []},
            {"status": "manual_review", "detected": {}, "issues": [], "manual_review": []},
            {"status": "manual_review", "detected": {"Figure": [], "Table": []}, "issues": [], "manual_review": []},
            {"status": "manual_review", "detected": {"Figure": [], "Table": []}, "issues": [{}], "manual_review": [{}]},
            {"status": "pass", "detected": {"Figure": [], "Table": []}, "issues": [], "manual_review": [{}]},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ReleaseGateError):
                    _require_successful_sequence_audit(payload, "Manuscript")

    def test_word_page_count_requires_positive_non_boolean_integer(self):
        self.assertEqual(_require_word_page_count(1, "Manuscript"), 1)
        for value in (True, False, 0, -1, 1.0, "1", None):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ReleaseGateError, "positive integer"):
                    _require_word_page_count(value, "Manuscript")

    def test_final_sequence_audits_from_all_maps_must_agree(self):
        passing = {
            "status": "pass",
            "detected": {"Figure": [{"value": 1}], "Table": []},
            "issues": [],
            "manual_review": [],
        }
        self.assertEqual(
            _require_consistent_sequence_audits([passing, dict(passing)], "Manuscript"),
            passing,
        )
        conflicting = {
            "status": "manual_review",
            "detected": {"Figure": [{"value": 1}], "Table": []},
            "issues": [],
            "manual_review": [{"reason": "Different audit outcome."}],
        }
        with self.assertRaisesRegex(ReleaseGateError, "inconsistent Figure/Table SEQ audit"):
            _require_consistent_sequence_audits([passing, conflicting], "Manuscript")

    def test_release_batch_scope_must_match_ledger_patch_task_and_document_ownership(self):
        parent = self.root / "parent.docx"
        output = self.root / "batch" / "documents" / "Manuscript.marked.docx"
        intent = {
            "batch_id": "batch-01",
            "revision_round_id": "round-release-tests",
            "selected_task_ids": ["T1"],
            "all_selected_task_ids": ["T1", "T2"],
            "documents": {
                "Manuscript": {
                    "parent_file": str(parent),
                    "parent_sha256": "a" * 64,
                    "output_file": str(output),
                }
            },
        }
        scope = {
            "batch_id": "batch-01",
            "revision_round_id": "round-release-tests",
            "revision_ledger_file": str(self.revision_path),
            "selected_task_ids": ["T1", "T2"],
            "batch_task_ids": ["T1"],
            "patch_ids": ["P1"],
            "documents": {
                "Manuscript": {
                    "input_file": str(parent),
                    "input_sha256": "a" * 64,
                    "output_file": str(output),
                }
            },
        }
        operation = SimpleNamespace(document="Manuscript", output_file=str(output))
        patch = SimpleNamespace(patch_id="P1", task_id="T1", status="verified", operations=[operation])
        ledger = SimpleNamespace(revision_round_id="round-release-tests", patches=[patch])
        self.assertEqual(
            _validate_release_batch_scope(
                intent, scope, revision_ledger=ledger, revision_ledger_path=self.revision_path
            ),
            ["P1"],
        )

        bad_patch_scope = dict(scope, patch_ids=[])
        with self.assertRaisesRegex(ReleaseGateError, "Patch ownership"):
            _validate_release_batch_scope(
                intent, bad_patch_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path
            )
        bad_task_scope = dict(scope, batch_task_ids=["T2"])
        with self.assertRaisesRegex(ReleaseGateError, "task ownership"):
            _validate_release_batch_scope(
                intent, bad_task_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path
            )
        bad_document_scope = dict(
            scope,
            documents={
                "Manuscript": {
                    "input_file": str(parent),
                    "input_sha256": "b" * 64,
                    "output_file": str(output),
                }
            },
        )
        with self.assertRaisesRegex(ReleaseGateError, "document hash scope"):
            _validate_release_batch_scope(
                intent, bad_document_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path
            )

    def test_final_docx_quality_report_must_bind_docx_pdf_and_page_count(self):
        payload = {
            "status": "PASS",
            "docx": {"docx_file": "/tmp/Manuscript.docx", "docx_sha256": "a" * 64},
            "pdf": {
                "file": "/tmp/Manuscript.pdf",
                "sha256": "b" * 64,
                "word_page_count": 4,
            },
        }
        self.assertEqual(
            _require_bound_final_quality_report(
                payload,
                role="Manuscript",
                docx_path="/tmp/Manuscript.docx",
                docx_sha256="a" * 64,
                pdf_path="/tmp/Manuscript.pdf",
                pdf_sha256="b" * 64,
                word_page_count=4,
            ),
            "PASS",
        )
        for mutation in (
            {"docx": {"docx_file": "/tmp/other.docx", "docx_sha256": "a" * 64}},
            {"pdf": {"file": "/tmp/other.pdf", "sha256": "b" * 64, "word_page_count": 4}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "c" * 64, "word_page_count": 4}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "b" * 64, "word_page_count": 5}},
        ):
            broken = dict(payload)
            broken.update(mutation)
            with self.subTest(mutation=mutation):
                with self.assertRaises(ReleaseGateError):
                    _require_bound_final_quality_report(
                        broken,
                        role="Manuscript",
                        docx_path="/tmp/Manuscript.docx",
                        docx_sha256="a" * 64,
                        pdf_path="/tmp/Manuscript.pdf",
                        pdf_sha256="b" * 64,
                        word_page_count=4,
                    )

    def test_deterministic_patch_delta_audit_surfaces_numbers_units_refs_and_sample_ids(self):
        operation = SimpleNamespace(
            operation_id="OP-DELTA-01",
            document="Manuscript",
            source_comment_ids=["C1"],
            find="The conductivity was 1.2 mS cm-1 in Figure 2 [1] for sample A1.",
            replace="The conductivity was 1.4 S m-1 in Figure 3 [2] for sample A2.",
        )
        patch = SimpleNamespace(
            patch_id="P-DELTA-01",
            status="verified",
            operations=[operation],
        )
        unit = SimpleNamespace(
            response_unit_id="RU-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            linked_operation_ids=["OP-DELTA-01"],
            status="completed",
            semantic_review_confirmed=True,
        )
        report = audit_patch_operation_deltas(
            SimpleNamespace(patches=[patch], response_units=[unit])
        )
        self.assertEqual(report[0]["review_status"], "human_semantic_review_completed")
        self.assertIn("numeric_values", report[0]["changed_categories"])
        self.assertIn("units", report[0]["changed_categories"])
        self.assertIn("figure_table_equation_references", report[0]["changed_categories"])
        self.assertIn("citation_markers", report[0]["changed_categories"])
        self.assertIn("sample_or_component_identifiers", report[0]["changed_categories"])
        self.assertEqual(report[0]["deltas"]["numeric_values"]["removed"], ["1.2"])
        self.assertEqual(report[0]["deltas"]["numeric_values"]["added"], ["1.4"])
        self.assertIn("do not assert scientific correctness", report[0]["interpretation"])

    def test_release_is_blocked_until_every_response_unit_is_completed(self):
        with self.assertRaisesRegex(ReleaseGateError, "explicitly completed"):
            self._call(self._unit(status="verified"))

    def test_release_manifest_binds_reply_only_package_and_rejects_tampered_docx_content(self):
        unit = self._unit(status="completed", comment_id="C1")
        ledger = RevisionLedger(
            revision_round_id="round-release-tests",
            response_units=[unit],
        )
        self.revision_path.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        response_plan = response_units_to_legacy_plan(ledger.response_units, ledger.patches)
        response_md_path = self.root / "Response_to_Reviewers.md"
        response_md_path.write_text(
            render_response_letter(response_plan),
            encoding="utf-8",
        )
        response_docx_path = self.root / "Response_to_Reviewers.docx"
        render_response_docx(response_plan, response_docx_path)
        quality = audit_docx_quality(response_docx_path, profile="response-letter")
        quality_path = self.root / "Response_to_Reviewers.quality.json"
        quality_path.write_text(
            json.dumps(quality, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        manifest = build_final_release_manifest(
            ledger,
            self.workflow,
            revision_ledger_path=self.revision_path,
            workflow_ledger_path=self.workflow_path,
            response_letter_md=response_md_path,
            response_letter_docx=response_docx_path,
            response_letter_quality=quality_path,
        )
        self.assertEqual(manifest["release_status"], "READY")
        self.assertEqual(manifest["final_documents"], {})
        self.assertEqual(manifest["location_maps"], [])
        self.assertEqual(manifest["deterministic_consistency_audit"]["status"], "PASS")
        self.assertEqual(manifest["response_units"][0]["location_methods"], [])
        self.assertTrue(manifest["checks"]["response_letter_contains_verified_patch_excerpts"])

        # A valid hash/quality report must not let a stale or hand-edited reply body pass release.
        response_document = Document(str(response_docx_path))
        changed = False
        for paragraph in response_document.paragraphs:
            if paragraph.text == "We have clarified the statement.":
                paragraph.text = "We have not clarified the statement."
                changed = True
                break
        self.assertTrue(changed, "Synthetic Response Letter fixture did not contain its expected response text.")
        response_document.save(response_docx_path)
        tampered_quality = audit_docx_quality(response_docx_path, profile="response-letter")
        quality_path.write_text(
            json.dumps(tampered_quality, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseGateError, "DOCX content does not match the current ResponseUnits/PatchOperations"):
            build_final_release_manifest(
                ledger,
                self.workflow,
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                response_letter_md=response_md_path,
                response_letter_docx=response_docx_path,
                response_letter_quality=quality_path,
            )

        # Non-canonical structures must not be used to hide additional response text.
        response_document = Document(str(response_docx_path))
        response_document.add_table(rows=1, cols=1).cell(0, 0).text = "Additional untracked reviewer reply"
        response_document.save(response_docx_path)
        tampered_quality = audit_docx_quality(response_docx_path, profile="response-letter")
        quality_path.write_text(
            json.dumps(tampered_quality, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ReleaseGateError, "tables or inline shapes"):
            build_final_release_manifest(
                ledger,
                self.workflow,
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                response_letter_md=response_md_path,
                response_letter_docx=response_docx_path,
                response_letter_quality=quality_path,
            )

    def test_final_consistency_audit_blocks_unresolved_figure_reference(self):
        source = self.root / "synthetic_manuscript.docx"
        document = Document()
        document.add_paragraph("Figure 1: An example caption.")
        document.add_paragraph("The results are presented in Figure 2.")
        document.save(source)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        revision_ledger = SimpleNamespace(documents=[
            SimpleNamespace(
                role="Manuscript",
                working_clean_file=str(source),
                working_clean_sha256=digest,
            )
        ])
        report = build_final_docx_consistency_audit(revision_ledger)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any(
            row.get("category") == "unresolved_figure_table_reference"
            for row in report["blocking_findings"]
        ))

    def test_release_is_blocked_when_a_workflow_comment_has_no_matching_response_unit(self):
        with self.assertRaisesRegex(ReleaseGateError, "comment C1 must map to exactly one completed ResponseUnit"):
            self._call(self._unit(status="completed", comment_id="C2"))


if __name__ == "__main__":
    unittest.main()

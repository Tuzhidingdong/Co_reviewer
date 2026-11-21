from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document
from jsonschema import Draft202012Validator, FormatChecker
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli
from unittest import mock

from co_reviewer.docx_quality import audit_docx_quality
from co_reviewer.release_manifest import (
    ReleaseGateError,
    _require_release_quality_status,
    _require_successful_sequence_audit,
    _require_consistent_sequence_audits,
    _require_bound_final_quality_report,
    _require_bound_response_quality_report,
    _require_word_page_count,
    _validate_release_batch_scope,
    _validate_response_unit_task_attribution,
    _collect_release_batch_history,
    _collect_recovered_batch_history,
    audit_patch_operation_deltas,
    _validate_qa_remediation_events,
    build_final_docx_consistency_audit,
    build_final_release_manifest,
)
from co_reviewer.batch_store import (
    _batch_artifact_inventory,
    _json_payload_sha256,
    _prepare_recovery_workflow,
)
from co_reviewer.response_renderer import render_response_letter
from co_reviewer.revision_models import (
    ResponseUnit,
    RevisionEvent,
    RevisionLedger,
    response_units_to_legacy_plan,
)
from co_reviewer.workflow_engine import (
    build_workflow_ledger,
    transition_task,
    validate_workflow_ledger,
    workflow_tasks_from_decision_plan,
)
from co_reviewer.v232_contract import ContractError, validate_qa_remediation_eligibility
from co_reviewer.release_package import verify_release_package
from response_letter_docx import render_response_docx


STAMP = "2026-11-15T08:00:00+08:00"


class ReleaseManifestGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Manuscript.docx"
        source_document = Document()
        source_document.add_paragraph("Synthetic immutable manuscript source.")
        source_document.save(self.source)
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
            response_units=[] if unit is None else [unit],
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

    def test_release_package_resumes_after_partial_manifest_generation_and_verifies_receipt(self):
        ledger = RevisionLedger(
            revision_round_id="round-release-tests",
            response_units=[self._unit(status="completed")],
        )
        self.revision_path.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        release_dir = self.root / "runs" / "release-resume-001"
        args = SimpleNamespace(
            workflow_ledger=str(self.workflow_path),
            revision_ledger=str(self.revision_path),
            out_dir=str(release_dir),
            manuscript_title="Synthetic manuscript",
            author="Synthetic Author",
        )

        # Simulate a process failure after the response letter and quality report
        # have been generated, but before the final Release Manifest is committed.
        with mock.patch(
            "co_reviewer.release_manifest.build_final_release_manifest",
            side_effect=RuntimeError("synthetic interruption before manifest commit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                cli.cmd_create_release_package(args)

        self.assertTrue((release_dir / "release_package_intent.json").is_file())
        self.assertTrue((release_dir / "Response_to_Reviewers.md").is_file())
        self.assertTrue((release_dir / "Response_to_Reviewers.docx").is_file())
        self.assertTrue((release_dir / "Response_to_Reviewers.quality.json").is_file())
        self.assertFalse((release_dir / "final_release_manifest.json").exists())
        self.assertFalse((release_dir / "release_package_receipt.json").exists())

        with mock.patch("co_reviewer.release_manifest.build_final_release_manifest", wraps=build_final_release_manifest):
            self.assertEqual(cli.cmd_create_release_package(args), 0)

        required = (
            "Response_to_Reviewers.md",
            "Response_to_Reviewers.docx",
            "Response_to_Reviewers.quality.json",
            "final_release_manifest.json",
            "release_package_intent.json",
            "release_package_receipt.json",
        )
        hashes = {
            name: hashlib.sha256((release_dir / name).read_bytes()).hexdigest()
            for name in required
        }
        receipt = json.loads((release_dir / "release_package_receipt.json").read_text(encoding="utf-8"))
        manifest = json.loads((release_dir / "final_release_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["schema_version"], "2.0")
        receipt_schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "release-package-receipt.schema.json")
            .read_text(encoding="utf-8")
        )
        Draft202012Validator(receipt_schema, format_checker=FormatChecker()).validate(receipt)
        self.assertEqual(receipt["status"], "complete")
        self.assertEqual(receipt["artifacts"]["final_release_manifest.json"], hashes["final_release_manifest.json"])
        self.assertEqual(manifest["release_status"], "READY")
        self.assertTrue(Path(receipt["package_manifest_file"]).is_file())
        self.assertTrue(Path(receipt["commit_marker_file"]).is_file())
        committed = verify_release_package(release_dir, expected_release_id=receipt["release_id"])
        self.assertEqual(committed["package_id"], receipt["package_id"])
        self.assertEqual(committed["artifact_count"], receipt["package_artifact_count"])
        self.assertGreaterEqual(receipt["package_artifact_count"], 6)

        # A lost success response is idempotent: a rerun verifies the receipt and
        # all package hashes without changing the committed artifacts.
        self.assertEqual(cli.cmd_create_release_package(args), 0)
        self.assertEqual(
            hashes,
            {
                name: hashlib.sha256((release_dir / name).read_bytes()).hexdigest()
                for name in required
            },
        )

    def test_release_package_does_not_commit_receipt_if_source_ledger_changes_mid_build(self):
        ledger = RevisionLedger(
            revision_round_id="round-release-tests",
            response_units=[self._unit(status="completed")],
        )
        self.revision_path.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        release_dir = self.root / "runs" / "release-race-001"
        args = SimpleNamespace(
            workflow_ledger=str(self.workflow_path),
            revision_ledger=str(self.revision_path),
            out_dir=str(release_dir),
            manuscript_title="Synthetic manuscript",
            author="Synthetic Author",
        )

        def build_then_change_workflow(*builder_args, **builder_kwargs):
            manifest = build_final_release_manifest(*builder_args, **builder_kwargs)
            with self.workflow_path.open("ab") as stream:
                stream.write(b" ")
            return manifest

        with mock.patch(
            "co_reviewer.release_manifest.build_final_release_manifest",
            side_effect=build_then_change_workflow,
        ):
            with self.assertRaisesRegex(ValueError, "Workflow Ledger changed during Release Package generation"):
                cli.cmd_create_release_package(args)

        self.assertTrue((release_dir / "release_package_intent.json").is_file())
        self.assertTrue((release_dir / "final_release_manifest.json").is_file())
        self.assertFalse((release_dir / "release_package_receipt.json").exists())
        with self.assertRaisesRegex(ValueError, "different source snapshot"):
            cli.cmd_create_release_package(args)

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

    def test_response_quality_report_must_use_response_letter_profile_and_exact_docx(self):
        payload = {
            "status": "PASS_WITH_WARNINGS",
            "docx": {
                "profile": "response-letter",
                "docx_file": "/tmp/Response_to_Reviewers.docx",
                "docx_sha256": "a" * 64,
            },
        }
        self.assertEqual(
            _require_bound_response_quality_report(
                payload,
                docx_path="/tmp/Response_to_Reviewers.docx",
                docx_sha256="a" * 64,
            ),
            "PASS_WITH_WARNINGS",
        )
        for changed in (
            {"docx": {"profile": "generic", "docx_file": "/tmp/Response_to_Reviewers.docx", "docx_sha256": "a" * 64}},
            {"docx": {"profile": "response-letter", "docx_file": "/tmp/other.docx", "docx_sha256": "a" * 64}},
            {"docx": {"profile": "response-letter", "docx_file": "/tmp/Response_to_Reviewers.docx", "docx_sha256": "b" * 64}},
        ):
            broken = dict(payload, docx=changed["docx"])
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ReleaseGateError, "response-letter profile"):
                    _require_bound_response_quality_report(
                        broken,
                        docx_path="/tmp/Response_to_Reviewers.docx",
                        docx_sha256="a" * 64,
                    )

    def test_response_units_must_reference_a_current_task_and_its_source_comment(self):
        unit = SimpleNamespace(
            response_unit_id="RU-ATTRIBUTION",
            comment_id="C1",
            linked_task_ids=["T1"],
        )
        task_map = {
            "T1": {"task_id": "T1", "source_comment_ids": ["C1"]},
        }
        _validate_response_unit_task_attribution([unit], task_map)

        with self.assertRaisesRegex(ReleaseGateError, "at least one current Workflow task"):
            _validate_response_unit_task_attribution(
                [SimpleNamespace(response_unit_id="RU-NO-TASK", comment_id="C1", linked_task_ids=[])],
                task_map,
            )
        with self.assertRaisesRegex(ReleaseGateError, "unknown or removed Workflow task"):
            _validate_response_unit_task_attribution(
                [SimpleNamespace(response_unit_id="RU-UNKNOWN", comment_id="C1", linked_task_ids=["T2"])],
                task_map,
            )
        with self.assertRaisesRegex(ReleaseGateError, "not declared by linked Workflow task"):
            _validate_response_unit_task_attribution(
                [SimpleNamespace(response_unit_id="RU-EXTRA", comment_id="C2", linked_task_ids=["T1"])],
                task_map,
            )

    def test_release_manifest_captures_unpublished_failed_batch_recovery_evidence(self):
        batch_id = "recovery-003"
        batch_dir = self.root / "runs" / f"batch-{batch_id}"
        documents_dir = batch_dir / "documents"
        documents_dir.mkdir(parents=True)
        output_path = documents_dir / "Manuscript.marked.docx"
        output_path.write_bytes(b"partial failed Apply artifact")
        parent_pointer = self.workflow["active_documents"]["Manuscript"]
        source_sha = parent_pointer["sha256"]
        output_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()

        intent = {
            "schema_version": "1.0",
            "batch_id": batch_id,
            "revision_round_id": "round-release-tests",
            "workflow_ledger_file": str(self.workflow_path),
            "selected_task_ids": ["T1"],
            "all_selected_task_ids": ["T1"],
            "documents": {
                "Manuscript": {
                    "parent_file": parent_pointer["file"],
                    "parent_sha256": parent_pointer["sha256"],
                    "parent_batch_id": parent_pointer["batch_id"],
                    "output_file": str(output_path),
                }
            },
        }
        intent_path = batch_dir / "batch_intent.json"
        intent_path.write_text(json.dumps(intent, ensure_ascii=False), encoding="utf-8")
        intent_sha = hashlib.sha256(intent_path.read_bytes()).hexdigest()
        scope = {
            "schema_version": "1.0",
            "batch_id": batch_id,
            "revision_round_id": "round-release-tests",
            "revision_ledger_file": str(self.revision_path),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "batch_intent_sha256": intent_sha,
            "patch_ids": ["P-RECOVER"],
            "documents": {
                "Manuscript": {
                    "input_file": parent_pointer["file"],
                    "input_sha256": parent_pointer["sha256"],
                    "output_file": str(output_path),
                }
            },
        }
        scope_path = batch_dir / "batch_scope.json"
        scope_path.write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")
        scope_sha = hashlib.sha256(scope_path.read_bytes()).hexdigest()
        parent_documents = {
            "Manuscript": {
                "file": str(Path(parent_pointer["file"]).expanduser().resolve()),
                "sha256": parent_pointer["sha256"],
                "batch_id": parent_pointer["batch_id"],
            },
        }
        failed_output_files = {"Manuscript": [str(output_path.resolve())]}
        workflow_for_recovery = build_workflow_ledger(
            {
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(self.source),
                    "sha256": source_sha,
                }],
                "tasks": [
                    {
                        "task_id": "T1",
                        "sequence": 1,
                        "task_type": "response_only",
                        "title": "Synthetic failed-batch task",
                        "source_comment_ids": ["C1"],
                        "target_documents": [],
                        "evidence": [],
                        "proposed_action": "A synthetic upstream task.",
                        "targets": [],
                        "dependencies": [],
                        "readiness": "ready_for_author_approval",
                        "unresolved_questions": [],
                        "confidence": 1.0,
                        "status": "pending",
                        "requires_user_approval": True,
                    },
                    {
                        "task_id": "T2",
                        "sequence": 2,
                        "task_type": "response_only",
                        "title": "Synthetic downstream task",
                        "source_comment_ids": ["C2"],
                        "target_documents": [],
                        "evidence": [],
                        "proposed_action": "A synthetic downstream dependent task.",
                        "targets": [],
                        "dependencies": ["T1"],
                        "readiness": "ready_for_author_approval",
                        "unresolved_questions": [],
                        "confidence": 1.0,
                        "status": "pending",
                        "requires_user_approval": True,
                    },
                ],
            },
            "round-release-tests",
            STAMP,
        )
        upstream_ready = transition_task(workflow_for_recovery["tasks"][0], "ready")
        workflow_for_recovery["tasks"][0] = transition_task(
            upstream_ready,
            "completed",
            evidence={
                "completion_record_id": "synthetic-recovery-upstream",
                "completion_record_fingerprint": hashlib.sha256(
                    b"synthetic-recovery-upstream"
                ).hexdigest(),
                "completed_at_utc": STAMP,
            },
        )
        self.workflow = workflow_for_recovery
        self.workflow_path.write_text(
            json.dumps(self.workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        recovery_reason = "Synthetic recovery test."
        workflow_after = _prepare_recovery_workflow(
            self.workflow,
            ["T1"],
            batch_id=batch_id,
            actor="Recovery Agent",
            reason=recovery_reason,
            timestamp_utc=STAMP,
        )
        invalidated_task_ids = ["T1", "T2"]
        workflow_after_sha = hashlib.sha256(
            json.dumps(workflow_after, ensure_ascii=False, indent=2).encode("utf-8")
        ).hexdigest()
        recovery_id = "recovery-synthetic-003"
        event = RevisionEvent(
            event_id="event-recovery-003",
            subject_id="P-RECOVER",
            subject_type="patch",
            event_type="patch_abandoned_after_batch_recovery",
            timestamp_utc=STAMP,
            actor="Recovery Agent",
            details={
                "recovery_id": recovery_id,
                "batch_id": batch_id,
                "reason": "Synthetic recovery test.",
                "parent_documents": parent_documents,
                "failed_output_files": failed_output_files,
                "artifact_inventory": [],
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
            },
        )
        artifact_inventory = _batch_artifact_inventory(batch_dir)
        event.details["artifact_inventory"] = artifact_inventory
        recovery_intent = {
            "schema_version": "1.0",
            "status": "prepared",
            "recovery_id": recovery_id,
            "batch_id": batch_id,
            "workflow_ledger_file": str(self.workflow_path),
            "revision_ledger_file": str(self.revision_path),
            "created_at_utc": STAMP,
            "actor": "Recovery Agent",
            "reason": recovery_reason,
            "workflow_ledger_sha256_before": hashlib.sha256(
                self.workflow_path.read_bytes()
            ).hexdigest(),
            "workflow_ledger_payload_sha256_before": _json_payload_sha256(self.workflow),
            "workflow_after": workflow_after,
            "workflow_ledger_sha256_after": workflow_after_sha,
            "revision_ledger_sha256_before": hashlib.sha256(
                self.revision_path.read_bytes()
            ).hexdigest(),
            "revision_ledger_sha256_after": "4" * 64,
            "revision_ledger_payload_sha256_before": _json_payload_sha256(
                json.loads(self.revision_path.read_text(encoding="utf-8"))
            ),
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "invalidated_task_ids": invalidated_task_ids,
            "patch_ids": ["P-RECOVER"],
            "patch_statuses_before": {"P-RECOVER": "approved"},
            "parent_documents": parent_documents,
            "failed_output_files": failed_output_files,
            "artifact_inventory": artifact_inventory,
            "revision_events": [event.to_dict()],
        }
        recovery_intent_path = batch_dir / "batch_recovery_intent.json"
        recovery_intent_path.write_text(json.dumps(recovery_intent, ensure_ascii=False), encoding="utf-8")
        recovery_intent_schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "batch-recovery-intent.schema.json").read_text(encoding="utf-8")
        )
        recovery_intent_validator = Draft202012Validator(
            recovery_intent_schema,
            format_checker=FormatChecker(),
        )
        recovery_intent_validator.validate(recovery_intent)
        for required_payload_hash in (
            "workflow_ledger_payload_sha256_before",
            "revision_ledger_payload_sha256_before",
        ):
            incomplete_intent = dict(recovery_intent)
            incomplete_intent.pop(required_payload_hash)
            self.assertTrue(
                any(
                    required_payload_hash in error.message
                    for error in recovery_intent_validator.iter_errors(incomplete_intent)
                ),
                f"Recovery intent schema must require {required_payload_hash}.",
            )
        recovery_intent_sha = hashlib.sha256(recovery_intent_path.read_bytes()).hexdigest()
        recovery_receipt = {
            "schema_version": "1.0",
            "status": "recovered",
            "recovery_id": recovery_id,
            "batch_id": batch_id,
            "workflow_ledger_file": str(self.workflow_path),
            "workflow_ledger_sha256": workflow_after_sha,
            "revision_ledger_file": str(self.revision_path),
            "revision_ledger_sha256": "4" * 64,
            "recovered_at_utc": STAMP,
            "actor": "Recovery Agent",
            "reason": recovery_reason,
            "batch_recovery_intent_sha256": recovery_intent_sha,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
            "selected_task_ids": ["T1"],
            "invalidated_task_ids": invalidated_task_ids,
            "patch_ids": ["P-RECOVER"],
            "parent_documents": parent_documents,
            "failed_output_files": failed_output_files,
            "retained_artifacts": artifact_inventory,
            "retained_batch_files": True,
        }
        (batch_dir / "batch_recovered.json").write_text(
            json.dumps(recovery_receipt, ensure_ascii=False), encoding="utf-8"
        )
        recovery_receipt_schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "batch-recovery-receipt.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(recovery_receipt_schema, format_checker=FormatChecker()).validate(recovery_receipt)
        revision = SimpleNamespace(
            revision_round_id="round-release-tests",
            patches=[SimpleNamespace(
                patch_id="P-RECOVER",
                task_id="T1",
                status="abandoned",
                operations=[SimpleNamespace(
                    document="Manuscript",
                    output_file=str(output_path),
                )],
            )],
            events=[event],
        )
        records = _collect_recovered_batch_history(
            revision,
            published_batch_ids=set(),
            revision_ledger_path=self.revision_path,
            workflow_ledger_path=self.workflow_path,
            workflow_task_ids={"T1", "T2"},
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["batch_id"], "recovery-003")
        self.assertEqual(records[0]["patch_ids"], ["P-RECOVER"])
        self.assertTrue(records[0]["retained_batch_files"])
        self.assertEqual(records[0]["retained_artifacts"], artifact_inventory)
        self.assertEqual(records[0]["invalidated_task_ids"], ["T1", "T2"])
        with self.assertRaisesRegex(ReleaseGateError, "both recovered-unpublished and published"):
            _collect_recovered_batch_history(
                revision,
                published_batch_ids={"recovery-003"},
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                workflow_task_ids={"T1"},
            )

        with self.assertRaisesRegex(ReleaseGateError, "current Workflow Ledger"):
            _collect_recovered_batch_history(
                revision,
                published_batch_ids=set(),
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                workflow_task_ids={"T1"},
            )

        recovery_intent_path = self.root / "runs" / "batch-recovery-003" / "batch_recovery_intent.json"
        receipt_path = self.root / "runs" / "batch-recovery-003" / "batch_recovered.json"
        original_recovery_intent_bytes = recovery_intent_path.read_bytes()
        original_recovery_receipt_bytes = receipt_path.read_bytes()
        recovery_intent_payload = json.loads(recovery_intent_path.read_text(encoding="utf-8"))
        recovery_intent_payload["selected_task_ids"] = [{"malformed": "not hashable"}]
        recovery_intent_path.write_text(
            json.dumps(recovery_intent_payload, ensure_ascii=False), encoding="utf-8"
        )
        recovery_receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
        recovery_receipt_payload["selected_task_ids"] = recovery_intent_payload["selected_task_ids"]
        recovery_receipt_payload["batch_recovery_intent_sha256"] = hashlib.sha256(
            recovery_intent_path.read_bytes()
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(recovery_receipt_payload, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaisesRegex(ReleaseGateError, "task sets differ"):
            _collect_recovered_batch_history(
                revision,
                published_batch_ids=set(),
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                workflow_task_ids={"T1"},
            )

        # Restore the valid recovery pair before exercising the independent
        # missing-parent check; each tamper case must start from a clean fixture.
        recovery_intent_path.write_bytes(original_recovery_intent_bytes)
        receipt_path.write_bytes(original_recovery_receipt_bytes)
        restored_batch_intent = json.loads(intent_path.read_text(encoding="utf-8"))
        restored_recovery_intent = json.loads(recovery_intent_path.read_text(encoding="utf-8"))
        restored_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(
            restored_recovery_intent["selected_task_ids"],
            restored_batch_intent["all_selected_task_ids"],
        )
        self.assertEqual(
            restored_recovery_intent["batch_task_ids"],
            restored_batch_intent["selected_task_ids"],
        )
        self.assertEqual(
            restored_receipt["selected_task_ids"],
            restored_recovery_intent["selected_task_ids"],
        )
        self.assertEqual(
            restored_receipt["batch_recovery_intent_sha256"],
            hashlib.sha256(original_recovery_intent_bytes).hexdigest(),
        )
        self.source.unlink()
        with self.assertRaisesRegex(ReleaseGateError, "active parent is missing or stale"):
            _collect_recovered_batch_history(
                revision,
                published_batch_ids=set(),
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
                # Restore the complete original task inventory after the
                # preceding negative check intentionally omitted T2.
                workflow_task_ids={"T1", "T2"},
            )

    def test_create_release_package_consumes_deduplicated_initial_source_inventory(self):
        reviewer_source = self.root / "Reviewers.docx"
        reviewer_document = Document()
        reviewer_document.add_paragraph("Synthetic reviewer source.")
        reviewer_document.save(reviewer_source)

        manuscript_record = {
            "role": "Manuscript",
            "file": str(self.source),
            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
        }
        reviewers_record = {
            "role": "Reviewers",
            "file": str(reviewer_source),
            "sha256": hashlib.sha256(reviewer_source.read_bytes()).hexdigest(),
        }
        decision_plan = {
            "source_files": [
                dict(manuscript_record),
                dict(manuscript_record),
                {**reviewers_record, "role": "Reply"},
                dict(reviewers_record),
            ],
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
        }
        workflow = build_workflow_ledger(
            decision_plan, "round-deduplicated-release", STAMP
        )
        self.assertEqual(
            [row["role"] for row in workflow["source_files"]],
            ["Manuscript", "Reviewers"],
        )
        self.assertEqual(
            {row["role"] for row in workflow["tasks"][0]["source_versions"]},
            {"Reviewers"},
        )
        workflow["tasks"][0] = transition_task(
            workflow["tasks"][0], "ready"
        )
        workflow["tasks"][0] = transition_task(
            workflow["tasks"][0],
            "completed",
            evidence={
                "completion_record_id": "source-dedup-release-completion",
                "completion_record_fingerprint": hashlib.sha256(
                    b"source-dedup-release-completion"
                ).hexdigest(),
                "completed_at_utc": STAMP,
            },
        )

        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        revision = RevisionLedger(
            revision_round_id="round-deduplicated-release",
            response_units=[self._unit(status="completed")],
        )
        self.revision_path.write_text(
            json.dumps(revision.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        release_dir = self.root / "runs" / "release-deduplicated-sources"
        args = SimpleNamespace(
            workflow_ledger=str(self.workflow_path),
            revision_ledger=str(self.revision_path),
            out_dir=str(release_dir),
            manuscript_title="Synthetic source-inventory manuscript",
            author="Synthetic Author",
        )

        self.assertEqual(cli.cmd_create_release_package(args), 0)
        package_manifest = verify_release_package(release_dir)
        self.assertGreater(package_manifest["artifact_count"], 0)
        self.assertTrue((release_dir / "final_release_manifest.json").is_file())
        self.assertTrue((release_dir / "release_package_receipt.json").is_file())
        self.assertEqual(validate_workflow_ledger(workflow), [])
        self.assertEqual(
            [row["role"] for row in workflow["source_files"]],
            ["Manuscript", "Reviewers"],
            "Release generation must not re-flatten history or recreate Reply aliases as initial sources.",
        )

    def test_release_manifest_retains_historical_published_and_rolled_back_batches(self):
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        revision = SimpleNamespace(revision_round_id="round-release-tests", patches=[])
        workflow_events = []
        published_versions = {}
        active_pointer = None

        def add_batch(batch_id, task_id, patch_id, rolled_back):
            nonlocal active_pointer
            batch_dir = self.root / "runs" / f"batch-{batch_id}"
            documents_dir = batch_dir / "documents"
            documents_dir.mkdir(parents=True)
            output_path = documents_dir / "Manuscript.marked.docx"
            output_path.write_bytes(f"synthetic output {batch_id}".encode("utf-8"))
            output_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
            parent_pointer = active_pointer or {
                "file": str(self.source.resolve()),
                "sha256": source_sha,
                "batch_id": None,
            }
            operation = SimpleNamespace(document="Manuscript", output_file=str(output_path))
            revision.patches.append(SimpleNamespace(
                patch_id=patch_id,
                task_id=task_id,
                status="superseded" if rolled_back else "verified",
                operations=[operation],
            ))
            intent = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "revision_round_id": "round-release-tests",
                "workflow_ledger_file": str(self.workflow_path),
                "selected_task_ids": [task_id],
                "all_selected_task_ids": [task_id],
                "documents": {
                    "Manuscript": {
                        "parent_file": parent_pointer["file"],
                        "parent_sha256": parent_pointer["sha256"],
                        "parent_batch_id": parent_pointer["batch_id"],
                        "output_file": str(output_path),
                    }
                },
            }
            intent_path = batch_dir / "batch_intent.json"
            intent_path.write_text(json.dumps(intent, ensure_ascii=False), encoding="utf-8")
            intent_sha = hashlib.sha256(intent_path.read_bytes()).hexdigest()
            scope = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "revision_round_id": "round-release-tests",
                "revision_ledger_file": str(self.revision_path),
                "selected_task_ids": [task_id],
                "batch_task_ids": [task_id],
                "batch_intent_sha256": intent_sha,
                "patch_ids": [patch_id],
                "documents": {
                    "Manuscript": {
                        "input_file": parent_pointer["file"],
                        "input_sha256": parent_pointer["sha256"],
                        "output_file": str(output_path),
                    }
                },
            }
            scope_path = batch_dir / "batch_scope.json"
            scope_path.write_text(json.dumps(scope, ensure_ascii=False), encoding="utf-8")
            scope_sha = hashlib.sha256(scope_path.read_bytes()).hexdigest()
            published_pointer = {
                "file": str(output_path.resolve()),
                "sha256": output_sha,
                "batch_id": batch_id,
                "updated_at_utc": STAMP,
            }
            pointer_map = {"Manuscript": published_pointer}
            revision_sha = "a" * 64
            publish_intent = {
                "batch_id": batch_id,
                "revision_ledger_sha256": revision_sha,
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
                "active_documents_to_publish": pointer_map,
            }
            (batch_dir / "batch_publish_intent.json").write_text(
                json.dumps(publish_intent, ensure_ascii=False), encoding="utf-8"
            )
            publish_receipt = {
                "batch_id": batch_id,
                "status": "published",
                "workflow_ledger_file": str(self.workflow_path),
                "revision_ledger_file": str(self.revision_path),
                "revision_ledger_sha256": revision_sha,
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
                "selected_task_ids": [task_id],
                "active_documents": pointer_map,
            }
            (batch_dir / "batch_published.json").write_text(
                json.dumps(publish_receipt, ensure_ascii=False), encoding="utf-8"
            )
            published_versions[batch_id] = {
                "role": "Manuscript",
                "file": str(output_path.resolve()),
                "sha256": output_sha,
            }
            workflow_events.append({
                "event_id": f"EV-{batch_id}-PUBLISH",
                "task_id": task_id,
                "event_type": "batch_published",
                "timestamp_utc": STAMP,
                "actor": "Co_Reviewer Batch Publisher",
                "batch_id": batch_id,
                "documents": pointer_map,
            })
            if rolled_back:
                restored = {
                    "Manuscript": {
                        "file": parent_pointer["file"],
                        "sha256": parent_pointer["sha256"],
                        "batch_id": parent_pointer["batch_id"],
                        "updated_at_utc": STAMP,
                    }
                }
                rollback_intent = {
                    "batch_id": batch_id,
                    "batch_intent_sha256": intent_sha,
                    "batch_scope_sha256": scope_sha,
                    "restored_active_documents": restored,
                }
                rollback_intent_path = batch_dir / "batch_rollback_intent.json"
                rollback_intent_path.write_text(
                    json.dumps(rollback_intent, ensure_ascii=False), encoding="utf-8"
                )
                rollback_receipt = {
                    "batch_id": batch_id,
                    "status": "rolled_back",
                    "retained_batch_files": True,
                    "workflow_ledger_file": str(self.workflow_path),
                    "selected_task_ids": [task_id],
                    "batch_intent_sha256": intent_sha,
                    "batch_scope_sha256": scope_sha,
                    "rollback_intent_sha256": hashlib.sha256(rollback_intent_path.read_bytes()).hexdigest(),
                    "restored_active_documents": restored,
                }
                (batch_dir / "batch_rollback.json").write_text(
                    json.dumps(rollback_receipt, ensure_ascii=False), encoding="utf-8"
                )
                workflow_events.append({
                    "event_id": f"EV-{batch_id}-ROLLBACK",
                    "task_id": task_id,
                    "event_type": "batch_rolled_back",
                    "timestamp_utc": STAMP,
                    "actor": "Co_Reviewer Batch Rollback",
                    "batch_id": batch_id,
                    "documents": restored,
                })
            else:
                active_pointer = published_pointer

        add_batch("batch-old-001", "T1", "P1", rolled_back=True)
        add_batch("batch-current-002", "T2", "P2", rolled_back=False)
        add_batch("batch-next-003", "T3", "P3", rolled_back=False)
        initial_source = {
            "role": "Manuscript",
            "file": str(self.source.resolve()),
            "sha256": source_sha,
        }
        task_specs = [
            {
                "task_id": "T1",
                "sequence": 1,
                "task_type": "local_revision",
                "title": "Publish first revision",
                "source_comment_ids": ["C1"],
                "target_documents": ["Manuscript"],
                "evidence": [],
                "proposed_action": "Apply the first approved document revision.",
                "targets": [],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
                "source_versions": [
                    initial_source,
                    published_versions["batch-old-001"],
                ],
            },
            {
                "task_id": "T2",
                "sequence": 2,
                "task_type": "local_revision",
                "title": "Publish second revision",
                "source_comment_ids": ["C2"],
                "target_documents": ["Manuscript"],
                "evidence": [],
                "proposed_action": "Apply the second approved document revision.",
                "targets": [],
                "dependencies": ["T1"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
                "source_versions": [
                    initial_source,
                    published_versions["batch-current-002"],
                ],
            },
            {
                "task_id": "T3",
                "sequence": 3,
                "task_type": "local_revision",
                "title": "Publish third revision",
                "source_comment_ids": ["C3"],
                "target_documents": ["Manuscript"],
                "evidence": [],
                "proposed_action": "Apply the third approved document revision.",
                "targets": [],
                "dependencies": ["T2"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
                "source_versions": [
                    published_versions["batch-current-002"],
                    published_versions["batch-next-003"],
                ],
            },
        ]
        workflow_ledger = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-release-tests",
            "created_at_utc": STAMP,
            "source_files": [initial_source],
            "active_documents": {"Manuscript": active_pointer},
            "documents": [],
            "object_registry": [],
            "tasks": workflow_tasks_from_decision_plan(task_specs),
            "events": workflow_events,
        }
        self.assertEqual(validate_workflow_ledger(workflow_ledger), [])
        self.assertEqual(len(workflow_ledger["source_files"]), 1)
        self.assertEqual(
            workflow_ledger["source_files"][0],
            initial_source,
            "Published and rolled-back versions must remain in append-only batch events, not initial source_files.",
        )
        records = _collect_release_batch_history(
            workflow_ledger,
            {"Manuscript": active_pointer},
            revision_ledger=revision,
            revision_ledger_path=self.revision_path,
            workflow_ledger_path=self.workflow_path,
        )
        # Legacy batches remain schema-compatible; protocol-v1 batches carry
        # a hash-bound creation receipt in this nullable manifest field.
        self.assertTrue(all("creation_receipt" in row for row in records))
        self.assertTrue(all(row["creation_receipt"] is None for row in records))
        self.assertEqual(
            [row["batch_id"] for row in records],
            ["batch-old-001", "batch-current-002", "batch-next-003"],
        )
        status_by_batch = {row["batch_id"]: row["publication_status"] for row in records}
        self.assertEqual(status_by_batch["batch-old-001"], "rolled_back")
        self.assertEqual(status_by_batch["batch-current-002"], "published")
        self.assertEqual(status_by_batch["batch-next-003"], "published")
        self.assertEqual(
            next(row for row in records if row["batch_id"] == "batch-next-003")["parent_documents"]["Manuscript"]["batch_id"],
            "batch-current-002",
        )
        self.assertEqual(
            [row["publication_sequence"] for row in records],
            [1, 2, 3],
        )

        next_batch_dir = self.root / "runs" / "batch-batch-next-003"
        next_intent_path = next_batch_dir / "batch_intent.json"
        next_scope_path = next_batch_dir / "batch_scope.json"
        next_publish_intent_path = next_batch_dir / "batch_publish_intent.json"
        next_receipt_path = next_batch_dir / "batch_published.json"
        originals = {
            path: path.read_bytes()
            for path in (next_intent_path, next_scope_path, next_publish_intent_path, next_receipt_path)
        }
        tampered_next_intent = json.loads(originals[next_intent_path])
        tampered_next_intent["documents"]["Manuscript"].update({
            "parent_file": str(self.source.resolve()),
            "parent_sha256": source_sha,
            "parent_batch_id": None,
        })
        next_intent_path.write_text(json.dumps(tampered_next_intent, ensure_ascii=False), encoding="utf-8")
        next_intent_sha = hashlib.sha256(next_intent_path.read_bytes()).hexdigest()
        tampered_next_scope = json.loads(originals[next_scope_path])
        tampered_next_scope["batch_intent_sha256"] = next_intent_sha
        tampered_next_scope["documents"]["Manuscript"].update({
            "input_file": str(self.source.resolve()),
            "input_sha256": source_sha,
        })
        next_scope_path.write_text(json.dumps(tampered_next_scope, ensure_ascii=False), encoding="utf-8")
        next_scope_sha = hashlib.sha256(next_scope_path.read_bytes()).hexdigest()
        tampered_publish_intent = json.loads(originals[next_publish_intent_path])
        tampered_publish_intent.update({
            "batch_intent_sha256": next_intent_sha,
            "batch_scope_sha256": next_scope_sha,
        })
        next_publish_intent_path.write_text(json.dumps(tampered_publish_intent, ensure_ascii=False), encoding="utf-8")
        tampered_receipt = json.loads(originals[next_receipt_path])
        tampered_receipt.update({
            "batch_intent_sha256": next_intent_sha,
            "batch_scope_sha256": next_scope_sha,
        })
        next_receipt_path.write_text(json.dumps(tampered_receipt, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ReleaseGateError, "parent pointer does not match the preceding published/rollback state"):
            _collect_release_batch_history(
                workflow_ledger,
                {"Manuscript": active_pointer},
                revision_ledger=revision,
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
            )
        for path, payload in originals.items():
            path.write_bytes(payload)

        rolled_back_record = next(row for row in records if row["batch_id"] == "batch-old-001")
        self.assertIn("rollback_intent", rolled_back_record)
        self.assertIn("rollback_receipt", rolled_back_record)
        self.assertEqual(rolled_back_record["all_selected_task_ids"], ["T1"])
        rollback_intent_path = self.root / "runs" / "batch-batch-old-001" / "batch_rollback_intent.json"
        tampered_intent = json.loads(rollback_intent_path.read_text(encoding="utf-8"))
        tampered_intent["reason"] = "Changed after receipt creation."
        rollback_intent_path.write_text(json.dumps(tampered_intent, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ReleaseGateError, "rollback receipt/intent"):
            _collect_release_batch_history(
                workflow_ledger,
                {"Manuscript": active_pointer},
                revision_ledger=revision,
                revision_ledger_path=self.revision_path,
                workflow_ledger_path=self.workflow_path,
            )

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
        patch = SimpleNamespace(patch_id="P1", task_id="T1", status="superseded", operations=[operation])
        ledger = SimpleNamespace(revision_round_id="round-release-tests", patches=[patch])
        self.assertEqual(
            _validate_release_batch_scope(
                intent, scope, revision_ledger=ledger, revision_ledger_path=self.revision_path,
                workflow_task_ids={"T1", "T2"},
            ),
            ["P1"],
        )

        bad_patch_scope = dict(scope, patch_ids=[])
        with self.assertRaisesRegex(ReleaseGateError, "Patch ownership"):
            _validate_release_batch_scope(
                intent, bad_patch_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path,
                workflow_task_ids={"T1", "T2"},
            )
        bad_task_scope = dict(scope, batch_task_ids=["T2"])
        with self.assertRaisesRegex(ReleaseGateError, "task ownership"):
            _validate_release_batch_scope(
                intent, bad_task_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path,
                workflow_task_ids={"T1", "T2"},
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
                intent, bad_document_scope, revision_ledger=ledger, revision_ledger_path=self.revision_path,
                workflow_task_ids={"T1", "T2"},
            )

    def test_final_docx_quality_report_must_bind_docx_pdf_and_page_count(self):
        payload = {
            "status": "PASS",
            "docx": {"profile": "word-final-clean", "docx_file": "/tmp/Manuscript.docx", "docx_sha256": "a" * 64},
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
            {"docx": {"profile": "generic", "docx_file": "/tmp/Manuscript.docx", "docx_sha256": "a" * 64}},
            {"docx": {"profile": "word-final-clean", "docx_file": "/tmp/other.docx", "docx_sha256": "a" * 64}},
            {"pdf": {"file": "/tmp/other.pdf", "sha256": "b" * 64, "word_page_count": 4}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "c" * 64, "word_page_count": 4}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "b" * 64, "word_page_count": 5}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "b" * 64, "word_page_count": True}},
            {"pdf": {"file": "/tmp/Manuscript.pdf", "sha256": "b" * 64, "word_page_count": 4.0}},
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
        self.assertTrue(manifest["checks"]["all_protocol_v1_batch_creation_receipts_valid"])

        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "final-release-manifest.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).validate(manifest)

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
            self._call(None)




class BoundedQARemediationEvidenceTests(unittest.TestCase):
    def _fixture(self):
        root = Path(tempfile.mkdtemp(prefix="qa-remediation-release-"))
        qa_before = root / "qa-before.json"
        qa_after = root / "qa-after.json"
        clean_before = root / "working-before.docx"
        clean_after = root / "working-after.docx"
        marked_before = root / "marked-before.docx"
        marked_after = root / "marked-after.docx"
        for path, payload in (
            (clean_before, b"clean-before"),
            (clean_after, b"clean-after"),
            (marked_before, b"marked-before"),
            (marked_after, b"marked-after"),
        ):
            path.write_bytes(payload)
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        before_payload = {
            "status": "FAIL",
            "docx": {
                "docx_file": str(clean_before),
                "docx_sha256": digest(clean_before),
                "profile": "word-final-clean",
            },
            "checks": [{
                "id": "placeholders", "status": "FAIL", "severity": "error",
                "evidence": ["TODO"],
            }],
        }
        after_payload = {
            "status": "PASS",
            "docx": {
                "docx_file": str(clean_after),
                "docx_sha256": digest(clean_after),
                "profile": "word-final-clean",
            },
            "checks": [],
        }
        qa_before.write_text(json.dumps(before_payload), encoding="utf-8")
        qa_after.write_text(json.dumps(after_payload), encoding="utf-8")
        operation = SimpleNamespace(
            operation_id="OP-REMEDIATION",
            document="Manuscript",
            input_file=str(marked_before),
            output_file=str(marked_after),
            source_sha256=digest(marked_before),
            find="Replace this TODO placeholder.",
            replace="Replace this draft placeholder.",
        )
        patch = SimpleNamespace(
            patch_id="PATCH-REMEDIATION",
            status="verified",
            operations=[operation],
            apply_report_ids=["APPLY-1"],
            verification_report_id="VERIFY-1",
            verified_output_hashes=[{"path": str(marked_after), "sha256": digest(marked_after)}],
        )
        start_details = {
            "remediation_round": 1, "phase": "PATCH_COMMIT",
            "qa_report_file": str(qa_before), "qa_report_sha256": digest(qa_before),
            "failing_clean_file": str(clean_before), "failing_clean_sha256": digest(clean_before),
            "failed_check_ids": ["placeholders"], "placeholder_tokens": ["TODO"],
            "document_role": "Manuscript",
            "marked_input_file": str(marked_before), "marked_input_sha256": digest(marked_before),
            "marked_output_file": str(marked_after),
            "batch_id": "BATCH-1", "batch_dir": str(root / "batch-1"),
            "batch_intent_sha256": "a" * 64, "batch_scope_sha256": "b" * 64,
            "operation_id": operation.operation_id,
        }
        completion_details = {
            "remediation_round": 1, "phase": "COMPLETE", "qa_status": "PASS",
            "qa_report_file": str(qa_after), "qa_report_sha256": digest(qa_after),
            "clean_file": str(clean_after), "clean_sha256": digest(clean_after),
            "marked_file": str(marked_after), "marked_sha256": digest(marked_after),
            "document_role": "Manuscript", "operation_id": operation.operation_id,
            "marked_input_file": str(marked_before), "marked_input_sha256": digest(marked_before),
            "batch_id": "BATCH-1", "batch_dir": str(root / "batch-1"),
            "batch_intent_sha256": "a" * 64, "batch_scope_sha256": "b" * 64,
            "compiled_revision_ids": ["101", "102"],
        }
        from co_reviewer.revision_models import RevisionEvent
        ledger = SimpleNamespace(
            events=[
                RevisionEvent(
                    event_id="START", subject_id=patch.patch_id, subject_type="patch",
                    event_type="qa_remediation_started", timestamp_utc=STAMP,
                    actor="test", details=start_details,
                ),
                RevisionEvent(
                    event_id="COMPLETE", subject_id=patch.patch_id, subject_type="patch",
                    event_type="qa_remediation_completed", timestamp_utc=STAMP,
                    actor="test", details=completion_details,
                ),
            ],
            patches=[patch],
            documents=[SimpleNamespace(
                role="Manuscript",
                output_file=str(marked_after),
                output_sha256=digest(marked_after),
                working_clean_file=str(clean_after),
                working_clean_sha256=digest(clean_after),
            )],
        )
        batch_record = {
            "batch_id": "BATCH-1", "directory": str(root / "batch-1"),
            "intent": {"sha256": "a" * 64},
            "scope": {"sha256": "b" * 64},
            "publication_status": "published",
            "patch_ids": [patch.patch_id],
            "parent_documents": {"Manuscript": {
                "file": str(marked_before), "sha256": digest(marked_before),
            }},
            "documents": {"Manuscript": {
                "file": str(marked_after), "sha256": digest(marked_after),
            }},
        }
        return ledger, batch_record, start_details, operation

    def test_release_gate_accepts_only_complete_hash_bound_patch_and_batch_lineage(self):
        ledger, batch, _, _ = self._fixture()
        result = _validate_qa_remediation_events(ledger, batch_records=[batch])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["batch_id"], "BATCH-1")

    def test_release_gate_rejects_wrong_published_batch_patch_scope(self):
        ledger, batch, _, _ = self._fixture()
        batch["patch_ids"] = []
        with self.assertRaisesRegex(ReleaseGateError, "exact published immutable batch lineage"):
            _validate_qa_remediation_events(ledger, batch_records=[batch])


    def test_release_gate_rejects_patch_verified_hash_not_matching_published_marked(self):
        ledger, batch, _, _ = self._fixture()
        ledger.patches[0].verified_output_hashes[0]["sha256"] = "e" * 64
        with self.assertRaisesRegex(ReleaseGateError, "Patch verification output hash"):
            _validate_qa_remediation_events(ledger, batch_records=[batch])

    def test_release_gate_rejects_patch_output_that_differs_from_published_marked(self):
        ledger, batch, _, operation = self._fixture()
        operation.output_file = str(Path(batch["directory"]) / "other.docx")
        with self.assertRaisesRegex(ReleaseGateError, "exact PatchOperation and Marked input/output chain"):
            _validate_qa_remediation_events(ledger, batch_records=[batch])

    def test_remediation_eligibility_rejects_structural_or_unmapped_qa_failures(self):
        ledger, _, _, operation = self._fixture()
        before = json.loads(Path(ledger.events[0].details["qa_report_file"]).read_text(encoding="utf-8"))
        wrong = json.loads(json.dumps(before))
        wrong["checks"][0]["id"] = "comments"
        with self.assertRaisesRegex(ContractError, "Only a proven local placeholder"):
            validate_qa_remediation_eligibility(wrong, {"find": operation.find, "replace": operation.replace})
        wrong = json.loads(json.dumps(before))
        wrong["checks"][0]["evidence"] = ["TBD"]
        with self.assertRaisesRegex(ContractError, "does not anchor"):
            validate_qa_remediation_eligibility(wrong, {"find": operation.find, "replace": operation.replace})
        wrong = json.loads(json.dumps(before))
        wrong["checks"][0]["evidence"] = ["TODO", "TBD"]
        with self.assertRaisesRegex(ContractError, "does not anchor"):
            validate_qa_remediation_eligibility(wrong, {"find": operation.find, "replace": operation.replace})



    def test_release_manifest_schema_models_not_used_and_completed_qa_remediation(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "schemas" / "final-release-manifest.schema.json")
            .read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema)

        def qa_branch_is_valid(value):
            errors = list(validator.iter_errors({"qa_remediation": value}))
            return not any(
                error.absolute_path and error.absolute_path[0] == "qa_remediation"
                for error in errors
            )

        self.assertTrue(qa_branch_is_valid({"status": "NOT_USED", "remediation_round": 0}))
        self.assertTrue(qa_branch_is_valid({
            "status": "PASS",
            "remediation_round": 1,
            "patch_id": "PATCH-1",
            "batch_id": "BATCH-1",
            "pre_remediation_qa_report": {"file": "before.json", "sha256": "a" * 64},
            "post_remediation_qa_report": {
                "file": "after.json", "sha256": "b" * 64, "status": "PASS_WITH_WARNINGS",
            },
            "marked": {"file": "marked.docx", "sha256": "c" * 64},
            "clean": {"file": "clean.docx", "sha256": "d" * 64},
        }))
        self.assertFalse(qa_branch_is_valid({"status": "BLOCKED", "remediation_round": 1}))
        self.assertFalse(qa_branch_is_valid({
            "status": "NOT_USED", "remediation_round": 0,
            "patch_id": "unexpected",
        }))

if __name__ == "__main__":
    unittest.main()

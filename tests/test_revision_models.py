import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.identity import revision_item_fingerprint, revision_targets_fingerprint
from co_reviewer.workflow_engine import workflow_scope_fingerprint
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseLocation,
    ResponseUnit,
    RevisionLedger,
    RevisionDocument,
    RevisionEvent,
    RevisionModelError,
    build_revision_ledger,
    response_units_to_legacy_plan,
    revision_ledger_from_mapping,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
STAMP = "2026-11-14T12:00:00+08:00"


def _operation(**overrides):
    data = {
        "operation_id": "OP-001",
        "document": "Manuscript",
        "input_file": "Manuscript.docx",
        "output_file": "Manuscript_revised.docx",
        "part": "word/document.xml",
        "p_idx": 4,
        "find": "old phrase",
        "replace": "revised phrase",
        "location": "Results paragraph 4",
        "source_sha256": HASH_A,
        "evidence_ids": ["E1"],
        "source_comment_ids": ["C1"],
    }
    data.update(overrides)
    return PatchOperation.from_mapping(data)


class RevisionModelsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf_path = self.root / "Manuscript_final.pdf"
        self.pdf_bytes = b"%PDF-1.4\nsynthetic final-layout artifact\n%%EOF"
        self.pdf_path.write_bytes(self.pdf_bytes)
        self.pdf_sha256 = hashlib.sha256(self.pdf_bytes).hexdigest()
        self.map_path = self.root / "location-map-source.json"
        self.map_bytes = b'{"location_map_id":"fixture-map","source":"synthetic"}'
        self.map_path.write_bytes(self.map_bytes)
        self.map_sha256 = hashlib.sha256(self.map_bytes).hexdigest()
        self.input_docx = self.root / "Manuscript_source.docx"
        self.marked_docx = self.root / "Manuscript_marked.docx"
        self.clean_docx = self.root / "Manuscript_working_clean.docx"
        self.input_docx.write_bytes(b"synthetic source DOCX")
        self.marked_docx.write_bytes(b"synthetic marked DOCX")
        self.clean_docx.write_bytes(b"synthetic accepted Clean DOCX")
        self.clean_sha256 = hashlib.sha256(self.clean_docx.read_bytes()).hexdigest()
        self.working_view_fingerprint = hashlib.sha256(json.dumps({
            "source_sha256": hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
            "working_clean_sha256": self.clean_sha256,
            "policy": "accept-insertions-reject-deletions-v1",
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.document = RevisionDocument(
            document_id="doc-manuscript", revision_id="rev-synthetic", role="Manuscript",
            input_file=str(self.input_docx), output_file=str(self.marked_docx),
            source_sha256=hashlib.sha256(self.input_docx.read_bytes()).hexdigest(),
            output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
            working_clean_file=str(self.clean_docx), working_clean_sha256=self.clean_sha256,
            working_view_fingerprint=self.working_view_fingerprint,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_revision_ledger_schema_keeps_patch_and_response_unit_separate(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "revision-ledger.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertIn("patch", schema["$defs"])
        self.assertIn("responseUnit", schema["$defs"])
        self.assertIn("patchOperation", schema["$defs"])
        response = schema["$defs"]["responseUnit"]
        self.assertIn("location_mapped", response["properties"]["status"]["enum"])
        self.assertTrue(any(
            isinstance(condition.get("then"), dict)
            and "location_map_fingerprint" in condition["then"].get("required", [])
            and condition["then"].get("properties", {}).get("final_locations", {}).get("minItems") == 1
            for condition in response["allOf"]
        ))

    def test_patch_requires_explicit_exact_text_operation(self):
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify the statement.",
            proposed_response="We clarified it.",
            operations=[_operation()],
        )
        self.assertEqual(patch.status, "planned")
        self.assertEqual(patch.operations[0].find, "old phrase")
        self.assertEqual(patch.operations[0].replace, "revised phrase")

    def test_patch_operation_requires_explicit_source_comment_attribution(self):
        payload = _operation().to_dict()
        payload["source_comment_ids"] = []
        with self.assertRaisesRegex(RevisionModelError, "requires at least one exact source_comment_id"):
            PatchOperation.from_mapping(payload)

    def test_patch_refuses_inferred_or_missing_find_replace(self):
        with self.assertRaises(RevisionModelError):
            PatchOperation.from_mapping({
                "document": "Manuscript",
                "input_file": "Manuscript.docx",
                "output_file": "Revised.docx",
                "part": "word/document.xml",
                "replace": "guess",
            })

    def test_patch_output_cannot_overwrite_source_docx(self):
        with self.assertRaisesRegex(RevisionModelError, "must be different"):
            _operation(output_file="Manuscript.docx")

    def test_legacy_plan_adapter_emits_pending_unapproved_items(self):
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="We will clarify.",
            operations=[_operation()],
        )
        ledger = RevisionLedger("round-001", patches=[patch])
        plan = ledger.to_legacy_revision_plan()
        self.assertFalse(plan["approved"])
        self.assertEqual(plan["revision_items"][0]["status"], "planned")
        self.assertEqual(plan["revision_items"][0]["targets"][0]["find"], "old phrase")
        self.assertNotIn("approval_fingerprint", plan["revision_items"][0])

    def test_no_change_anchor_can_bind_to_current_active_document_without_patch(self):
        active_docx = self.root / "Manuscript_active.docx"
        active_docx.write_bytes(b"synthetic active source DOCX")
        source_sha = hashlib.sha256(active_docx.read_bytes()).hexdigest()
        document = RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-active",
            role="Manuscript",
            input_file=str(active_docx),
            output_file=str(active_docx),
            source_sha256=source_sha,
        )
        unit = ResponseUnit(
            response_unit_id="RU-NOCHANGE-01",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify the existing explanation.",
            response_text="The existing explanation already addresses this point.",
            disposition="no_change",
            location_requirement="existing_anchor_required",
            resolution_reason="The current Manuscript already contains the requested explanation.",
            anchor_evidence_id="E-ANCHOR-01",
            anchor_document="Manuscript",
            anchor_part="word/document.xml",
            anchor_p_idx=0,
            anchor_source_excerpt="The existing explanation is already present.",
        )
        ledger = RevisionLedger(
            "round-nochange-anchor",
            documents=[document],
            response_units=[unit],
        )
        ledger.validate_or_raise()
        self.assertEqual(ledger.documents[0].input_file, ledger.documents[0].output_file)
        self.assertEqual(ledger.documents[0].source_sha256, source_sha)

    def test_no_change_passthrough_rejects_patch_roles_and_changed_source_hash(self):
        active_docx = self.root / "Manuscript_nochange.docx"
        active_docx.write_bytes(b"synthetic active source DOCX")
        source_sha = hashlib.sha256(active_docx.read_bytes()).hexdigest()
        document = RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-active",
            role="Manuscript",
            input_file=str(active_docx),
            output_file=str(active_docx),
            source_sha256=source_sha,
        )
        patch = Patch(
            patch_id="P-NOCHANGE-CONFLICT",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Revise this text.",
            proposed_response="We revised the text.",
            operations=[_operation(
                input_file=str(active_docx),
                output_file=str(self.root / "Manuscript_marked_conflict.docx"),
                source_sha256=source_sha,
            )],
        )
        ledger_with_patch = RevisionLedger(
            "round-nochange-patch-conflict",
            patches=[patch],
            documents=[document],
        )
        with self.assertRaisesRegex(RevisionModelError, "role also has PatchOperations"):
            ledger_with_patch.validate_or_raise()

        ledger = RevisionLedger("round-nochange-hash", documents=[document])
        active_docx.write_bytes(b"changed active source DOCX")
        with self.assertRaisesRegex(RevisionModelError, "no longer matches its registered source SHA-256"):
            ledger.validate_or_raise()

    def test_incremental_merge_accepts_repeated_no_change_anchor_on_unchanged_source(self):
        active_docx = self.root / "Manuscript_repeated_anchor.docx"
        active_docx.write_bytes(b"synthetic active source DOCX")
        source_sha = hashlib.sha256(active_docx.read_bytes()).hexdigest()
        original = RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-active",
            role="Manuscript",
            input_file=str(active_docx),
            output_file=str(active_docx),
            source_sha256=source_sha,
        )
        repeated = RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-active",
            role="Manuscript",
            input_file=str(active_docx),
            output_file=str(active_docx),
            source_sha256=source_sha,
        )
        ledger = RevisionLedger("round-nochange-repeated", documents=[original])
        ledger.merge_batch(RevisionLedger("round-nochange-repeated", documents=[repeated]))
        ledger.validate_or_raise()
        self.assertEqual(len(ledger.documents), 1)
        self.assertEqual(ledger.documents[0].source_sha256, source_sha)

    def test_response_only_task_does_not_generate_fake_patch(self):
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-response",
            "source_files": [],
            "documents": [],
            "object_registry": [],
            "events": [],
            "tasks": [{
                "task_id": "R1",
                "sequence": 1,
                "stage": "response",
                "task_type": "response_only",
                "title": "Explain the mechanism",
                "source_comment_ids": ["C1"],
                "target_documents": [],
                "evidence": [{
                    "evidence_id": "E1",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_comment_id": "C1",
                    "source_excerpt": "Please explain the mechanism.",
                    "evidence_type": "reviewer_comment",
                    "match_status": "confirmed",
                }],
                "targets": [],
                "dependencies": [],
                "target_groups": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "requires_user_approval": True,
                "status": "planned",
                "patch_operations": [],
                "proposed_response": "The mechanism is described in the revised discussion.",
            }],
        }
        ledger = build_revision_ledger(workflow, document_paths={})
        self.assertEqual(ledger.patches, [])
        self.assertEqual(len(ledger.response_units), 1)
        self.assertEqual(ledger.response_units[0].status, "draft")
        self.assertEqual(ledger.response_units[0].linked_patch_ids, [])
        self.assertEqual(ledger.response_units[0].disposition, "reply_only")
        self.assertEqual(ledger.response_units[0].location_requirement, "not_required")
        ledger.confirm_response_units(
            [ledger.response_units[0].response_unit_id], verification_report_id="semantic-reply-only", actor="Human Author"
        )
        ledger.complete_response_units(
            [ledger.response_units[0].response_unit_id], ready_for_submission_confirmed=True, actor="Human Author"
        )
        self.assertEqual(ledger.response_units[0].status, "completed")
        self.assertEqual(ledger.response_units[0].final_locations, [])

    def test_patch_builder_only_materializes_the_current_conflict_free_ready_batch(self):
        def task(task_id, comment_id, status, output_file):
            return {
                "task_id": task_id,
                "sequence": 1 if task_id == "T1" else 2,
                "stage": "revision",
                "task_type": "local_revision",
                "title": f"Revise {task_id}",
                "source_comment_ids": [comment_id],
                "target_documents": ["Manuscript"],
                "evidence": [{
                    "evidence_id": f"E-{task_id}",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_comment_id": comment_id,
                    "source_excerpt": f"Please revise {task_id}.",
                    "evidence_type": "reviewer_comment",
                    "match_status": "confirmed",
                }],
                "proposed_action": f"Revise the text for {task_id}.",
                "proposed_response": f"We revised {task_id}.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 1,
                    "action": "replace",
                    "evidence_ids": [f"E-{task_id}"],
                }],
                "dependencies": [],
                "target_groups": ["Manuscript|word/document.xml|p_idx=1"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "requires_user_approval": True,
                "status": status,
                "patch_operations": [{
                    "operation_id": f"{task_id}-OP1",
                    "document": "Manuscript",
                    "input_file": "Manuscript.docx",
                    "output_file": output_file,
                    "part": "word/document.xml",
                    "p_idx": 1,
                    "find": f"old text {task_id}",
                    "replace": f"new text {task_id}",
                    "evidence_ids": [f"E-{task_id}"],
                    "source_comment_ids": [comment_id],
                }],
            }

        ready_task = task("T1", "C1", "ready", "Manuscript_T1.docx")
        planned_task = task("T2", "C2", "planned", "Manuscript_T2.docx")
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-ready-batch",
            "source_files": [],
            "documents": [],
            "object_registry": [],
            "tasks": [ready_task, planned_task],
            "events": [{
                "event_id": "EV-T1-READY",
                "task_id": "T1",
                "timestamp_utc": STAMP,
                "event_type": "status_transition",
                "from_status": "planned",
                "to_status": "ready",
                "reason": "Author approved T1.",
                "actor": "Human Author",
                "fingerprint": workflow_scope_fingerprint(ready_task),
            }],
        }
        ledger = build_revision_ledger(workflow, document_paths={})
        self.assertEqual([patch.task_id for patch in ledger.patches], ["T1"])
        self.assertEqual(
            {unit.comment_id for unit in ledger.response_units},
            {"C1", "C2"},
        )

    def test_build_revision_ledger_requires_traceable_reviewer_comment(self):
        task = {
            "task_id": "T1",
            "sequence": 1,
            "stage": "revision",
            "task_type": "local_revision",
            "title": "Clarify a statement",
            "source_comment_ids": ["C1"],
            "target_documents": ["Manuscript"],
            "evidence": [],
            "proposed_action": "Clarify the text.",
            "targets": [{
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
                "action": "replace",
                "evidence_ids": [],
            }],
            "dependencies": [],
            "target_groups": ["Manuscript|word/document.xml|p_idx=0"],
            "readiness": "ready_for_author_approval",
            "unresolved_questions": [],
            "confidence": 0.9,
            "requires_user_approval": True,
            "status": "ready",
            "patch_operations": [{
                "operation_id": "T1-OP1",
                "document": "Manuscript",
                "input_file": "Manuscript.docx",
                "output_file": "Revised.docx",
                "part": "word/document.xml",
                "p_idx": 0,
                "find": "old",
                "replace": "new",
                "evidence_ids": [],
                "source_comment_ids": ["C1"],
            }],
        }
        workflow = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-no-comment",
            "source_files": [],
            "documents": [],
            "object_registry": [],
            "tasks": [task],
            "events": [{
                "event_id": "EV-T1-READY",
                "task_id": "T1",
                "timestamp_utc": STAMP,
                "event_type": "status_transition",
                "from_status": "planned",
                "to_status": "ready",
                "reason": "Author approved the task plan.",
                "actor": "Human Author",
                "fingerprint": workflow_scope_fingerprint(task),
            }],
        }
        with self.assertRaisesRegex(RevisionModelError, "no traceable reviewer comment"):
            build_revision_ledger(workflow, document_paths={})

    def test_manual_only_operation_can_pass_preflight_without_body_locator_phrase(self):
        operation = replace(
            _operation(),
            part="word/header1.xml",
            location_phrase=None,
        )
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please revise the header.",
            proposed_response="We revised the header.",
            operations=[operation],
        )
        unit = ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise the header.",
            response_text="We revised the header.",
            linked_task_ids=["T1"],
            linked_patch_ids=["T1"],
            linked_operation_ids=[operation.operation_id],
            disposition="revised",
            location_requirement="manual_required",
        )
        ledger = RevisionLedger("round-manual-preflight", patches=[patch], response_units=[unit])
        legacy_item = {
            "id": "T1",
            "reviewer": patch.reviewer,
            "comment": patch.comment,
            "proposed_response": patch.proposed_response,
            "status": "planned",
            "targets": [operation.to_legacy_target()],
        }
        legacy_item["targets"][0]["source_sha256"] = HASH_A
        legacy_item["targets"][0]["location_phrase"] = "revised phrase"
        legacy_item["preflight_fingerprint"] = revision_targets_fingerprint(legacy_item)
        ledger.record_legacy_preflight(
            {
                "preflight_items": [{
                    "revision_item_id": "T1",
                    "preflight_fingerprint": legacy_item["preflight_fingerprint"],
                }]
            },
            {"revision_items": [legacy_item]},
            report_id="preflight-manual",
        )
        self.assertEqual(patch.status, "preflighted")
        self.assertIsNone(patch.operations[0].location_phrase)
        self.assertEqual(patch.operations[0].part, "word/header1.xml")

    def test_apply_and_verification_reports_update_revision_ledger(self):
        operation = _operation()
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="We clarified it.",
            operations=[operation],
        )
        ledger = RevisionLedger("round-apply", patches=[patch])
        legacy_item = {
            "id": "T1",
            "reviewer": patch.reviewer,
            "comment": patch.comment,
            "proposed_response": patch.proposed_response,
            "status": "planned",
            "targets": [operation.to_legacy_target()],
        }
        legacy_item["targets"][0]["source_sha256"] = HASH_A
        legacy_item["targets"][0]["location_phrase"] = "revised phrase"
        legacy_item["preflight_fingerprint"] = revision_targets_fingerprint(legacy_item)
        preflight_report = {
            "preflight_items": [{
                "revision_item_id": "T1",
                "preflight_fingerprint": legacy_item["preflight_fingerprint"],
            }]
        }
        preflight_plan = {"revision_items": [legacy_item]}
        ledger.record_legacy_preflight(
            preflight_report,
            preflight_plan,
            report_id="preflight-001",
        )
        self.assertEqual(patch.status, "preflighted")
        self.assertEqual(patch.operations[0].source_sha256, HASH_A)

        approved_item = dict(legacy_item)
        approved_item["status"] = "approved"
        approved_item["approval_fingerprint"] = revision_item_fingerprint(approved_item)
        approval_plan = {"approved": True, "revision_items": [approved_item]}
        ledger.record_legacy_approval(
            approval_plan, ["T1"], actor="Human Author"
        )
        self.assertEqual(patch.status, "approved")

        applied_item = dict(approved_item, status="applied")
        report = {
            "input_file": "Manuscript.docx",
            "output_file": "Manuscript_revised.docx",
            "edits_applied": 1,
            "results": [{
                "revision_item_id": "T1",
                "operation_id": operation.operation_id,
                "status": "applied",
            }],
        }
        ledger.record_legacy_apply_reports(
            [report], {"revision_items": [applied_item]}
        )
        self.assertEqual(patch.status, "applied")
        self.assertEqual(patch.approval_fingerprint, approved_item["approval_fingerprint"])

        verified_item = {
            **applied_item,
            "status": "verified",
            "semantic_review_confirmed": True,
            "verified_output_hashes": [{
                "path": "Manuscript_revised.docx",
                "sha256": HASH_B,
            }],
        }
        ledger.record_legacy_verified_items(
            {"revision_items": [verified_item]},
            verification_report_id="verify-001",
        )
        self.assertEqual(patch.status, "verified")
        self.assertEqual(patch.verification_report_id, "verify-001")
        self.assertEqual(len(patch.verified_output_hashes), 1)
        self.assertEqual(
            [event.event_type for event in ledger.events],
            ["preflight", "approval", "patch_applied", "verification"],
        )
        ledger.to_dict()

    def test_response_is_not_final_until_final_pdf_line_mapping_exists(self):
        unit = ResponseUnit(
            response_unit_id="RU-1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain.",
            response_text="We have explained it.",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="semantic-001",
        )
        mapped = ResponseUnit(
            response_unit_id="RU-1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain.",
            response_text="We have explained it.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-001"],
            status="location_mapped",
            semantic_review_confirmed=True,
            verification_report_id="semantic-001",
            location_map_fingerprint=HASH_A,
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
            final_locations=[ResponseLocation(
                document="Manuscript",
                output_file=str(self.marked_docx),
                pdf_file=str(self.pdf_path),
                page=3,
                line_start=12,
                line_end=15,
                pdf_sha256=self.pdf_sha256,
                location_map_id="map-001",
                mapping_fingerprint=HASH_A,
                output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
                locator_id="OP-001",
                source_clean_sha256=self.clean_sha256,
            )],
        )
        patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please explain.",
            proposed_response="We have explained it.",
            operations=[_operation(
                input_file=str(self.input_docx),
                output_file=str(self.marked_docx),
            )],
            status="verified",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-001",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-001"],
            verification_report_id="verify-001",
            verified_output_hashes=[{
                "path": str(self.marked_docx),
                "sha256": hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
            }],
        )
        draft_plan = response_units_to_legacy_plan([unit])
        final_plan = response_units_to_legacy_plan([mapped], [patch])
        self.assertEqual(draft_plan["revision_items"][0]["status"], "location_pending")
        self.assertEqual(final_plan["revision_items"][0]["status"], "verified")
        self.assertIn("Page 3, lines 12-15", final_plan["revision_items"][0]["targets"][0]["location"])

    def test_location_mapped_response_requires_semantic_review_and_pdf_map(self):
        with self.assertRaisesRegex(RevisionModelError, "requires semantic review"):
            ResponseUnit(
                response_unit_id="RU-1",
                reviewer="Reviewer 1",
                comment_id="C1",
                comment_text="Please explain.",
                response_text="",
                status="location_mapped",
                location_map_fingerprint=HASH_A,
                final_locations=[ResponseLocation(
                    document="Manuscript",
                    output_file=str(self.clean_docx),
                    pdf_file=str(self.pdf_path),
                    page=1,
                    line_start=1,
                    line_end=1,
                    pdf_sha256=self.pdf_sha256,
                    location_map_id="map-001",
                    mapping_fingerprint=HASH_A,
                    output_sha256=self.clean_sha256,
                    locator_id="OP-002",
                    source_clean_sha256=self.clean_sha256,
                )],
            )

    def test_automatic_location_records_must_cover_every_linked_operation_once(self):
        unit = ResponseUnit(
            response_unit_id="RU-MULTI-OP",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify both locations.",
            response_text="We clarified both locations.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-001", "OP-002"],
            disposition="revised",
            location_requirement="required",
            resolution_reason="The reviewer comment requires two exact manuscript changes.",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="semantic-multi-op",
        )

        def location(locator_id):
            return ResponseLocation(
                document="Manuscript",
                output_file=str(self.marked_docx),
                pdf_file=str(self.pdf_path),
                page=1,
                line_start=1,
                line_end=2,
                pdf_sha256=self.pdf_sha256,
                location_map_id="map-multi-op",
                mapping_fingerprint=HASH_A,
                output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
                locator_id=locator_id,
                source_clean_sha256=self.clean_sha256,
            )

        with self.assertRaisesRegex(RevisionModelError, "every linked PatchOperation"):
            unit.record_final_locations(
                HASH_A,
                [location("OP-001")],
                location_map_file=str(self.map_path),
                location_map_file_sha256=self.map_sha256,
            )

        with self.assertRaisesRegex(RevisionModelError, "every linked PatchOperation"):
            unit.record_final_locations(
                HASH_A,
                [location("OP-001"), location("OP-001")],
                location_map_file=str(self.map_path),
                location_map_file_sha256=self.map_sha256,
            )

        unit.record_final_locations(
            HASH_A,
            [location("OP-001"), location("OP-002")],
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
        )
        self.assertEqual(unit.status, "location_mapped")
        self.assertEqual(
            {item.locator_id for item in unit.final_locations},
            {"OP-001", "OP-002"},
        )

    def test_persisted_mapped_response_rejects_partial_or_duplicate_locator_coverage(self):
        def location(locator_id):
            return ResponseLocation(
                document="Manuscript",
                output_file=str(self.marked_docx),
                pdf_file=str(self.pdf_path),
                page=1,
                line_start=1,
                line_end=2,
                pdf_sha256=self.pdf_sha256,
                location_map_id="map-persisted-multi",
                mapping_fingerprint=HASH_A,
                output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
                locator_id=locator_id,
                source_clean_sha256=self.clean_sha256,
            )

        unit = ResponseUnit(
            response_unit_id="RU-PERSISTED-MULTI",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify both locations.",
            response_text="We clarified both locations.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-001", "OP-002"],
            disposition="revised",
            location_requirement="required",
            resolution_reason="The reviewer comment requires two exact manuscript changes.",
            status="location_mapped",
            semantic_review_confirmed=True,
            verification_report_id="semantic-persisted-multi",
            location_map_fingerprint=HASH_A,
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
            final_locations=[location("OP-001")],
        )
        errors = unit.validate_location_evidence()
        self.assertTrue(any("exactly one final locator" in error for error in errors))

        unit.final_locations = [location("OP-001"), location("OP-001")]
        errors = unit.validate_location_evidence()
        self.assertTrue(any("exactly one final locator" in error for error in errors))

    def test_manual_locator_records_table_without_fabricating_word_line_numbers(self):
        unit = ResponseUnit(
            response_unit_id="RU-MANUAL-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify Table S2.",
            response_text="We clarified the table in the Supplementary Information.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-001"],
            disposition="revised",
            location_requirement="manual_required",
            resolution_reason="Table-cell location is not suitable for automatic body-line mapping.",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="semantic-manual-001",
        )
        location = ResponseLocation(
            document="SI",
            output_file=str(self.marked_docx),
            pdf_file=str(self.pdf_path),
            page=None,
            line_start=None,
            line_end=None,
            pdf_sha256=self.pdf_sha256,
            location_map_id="manual-map-001",
            mapping_fingerprint=HASH_A,
            output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
            locator_id="manual:OP-001",
            source_clean_sha256=self.clean_sha256,
            mapping_method="manual",
            locator_text="Table S2, Supplementary Information",
        )
        unit.record_final_locations(
            HASH_A,
            [location],
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
        )
        self.assertEqual(location.display_text, "Table S2, Supplementary Information")
        unit.mark_completed(STAMP)
        self.assertEqual(unit.status, "completed")
        self.assertEqual(unit.validate_location_evidence(), [])


    def test_abandoned_patch_cannot_be_replayed_from_an_old_legacy_plan(self):
        patch = Patch(
            patch_id="P-ABANDONED",
            task_id="T-ABANDONED",
            reviewer="Reviewer 1",
            comment="Please clarify the statement.",
            proposed_response="We clarified the statement.",
            operations=[_operation()],
            status="planned",
        )
        ledger = RevisionLedger("round-abandoned-plan", patches=[patch])
        stale_plan = ledger.to_legacy_revision_plan()
        patch.status = "abandoned"
        with self.assertRaisesRegex(RevisionModelError, "abandoned in the RevisionLedger"):
            ledger.assert_legacy_plan_matches(stale_plan)

    def test_revision_ledger_rejects_reviewer_facing_plan_drift(self):
        patch = Patch(
            patch_id="T1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="We clarified the statement.",
            operations=[_operation()],
        )
        ledger = RevisionLedger("round-drift", patches=[patch])
        plan = ledger.to_legacy_revision_plan()
        plan["revision_items"][0]["proposed_response"] = "A response that was never approved."
        with self.assertRaisesRegex(RevisionModelError, "proposed_response differs"):
            ledger.assert_legacy_plan_matches(plan)

    def test_ledger_refuses_pdf_changed_after_final_line_mapping(self):
        unit = ResponseUnit(
            response_unit_id="RU-STALE",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain.",
            response_text="We explained it.",
            disposition="no_change",
            location_requirement="existing_anchor_required",
            resolution_reason="The original paragraph already contains the requested explanation.",
            anchor_evidence_id="E-STALE-ANCHOR",
            anchor_document="Manuscript",
            anchor_part="word/document.xml",
            anchor_p_idx=0,
            anchor_source_excerpt="The original paragraph already contains this explanation.",
            status="location_mapped",
            semantic_review_confirmed=True,
            verification_report_id="semantic-001",
            location_map_fingerprint=HASH_A,
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
            final_locations=[ResponseLocation(
                document="Manuscript",
                output_file=str(self.marked_docx),
                pdf_file=str(self.pdf_path),
                page=1,
                line_start=1,
                line_end=2,
                pdf_sha256=self.pdf_sha256,
                location_map_id="fixture-map",
                mapping_fingerprint=HASH_A,
                output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
                locator_id="anchor:E-STALE-ANCHOR",
                source_clean_sha256=self.clean_sha256,
            )],
        )
        ledger = RevisionLedger("round-stale-pdf", documents=[self.document], response_units=[unit])
        self.pdf_path.write_bytes(self.pdf_bytes + b"tampered")
        with self.assertRaisesRegex(RevisionModelError, "Final mapped PDF changed"):
            ledger.to_dict()

    def test_response_unit_semantic_review_location_mapping_and_completion_are_separate(self):
        ledger = RevisionLedger(
            "round-response-lifecycle",
            response_units=[ResponseUnit(
                response_unit_id="RU-1",
                reviewer="Reviewer 1",
                comment_id="C1",
                comment_text="Please explain.",
                response_text="We have explained the process.",
                disposition="no_change",
                location_requirement="existing_anchor_required",
                resolution_reason="The confirmed original paragraph already describes the method.",
                anchor_evidence_id="E-ANCHOR",
                anchor_document="Manuscript",
                anchor_part="word/document.xml",
                anchor_p_idx=0,
                anchor_source_excerpt="The existing paragraph explains the process.",
            )],
            documents=[self.document],
        )
        ledger.confirm_response_units(
            ["RU-1"],
            verification_report_id="response-semantic-001",
            actor="Human Author",
        )
        self.assertEqual(ledger.response_units[0].status, "verified")
        self.assertFalse(ledger.response_units[0].final_locations)

        location_map = {
            "location_map_id": "map-001",
            "response_units": [{
                "response_unit_id": "RU-1",
                "location_map_fingerprint": HASH_A,
                "final_locations": [{
                    "document": "Manuscript",
                    "output_file": str(self.marked_docx),
                    "output_sha256": hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
                    "pdf_file": str(self.pdf_path),
                    "page": 3,
                    "line_start": 12,
                    "line_end": 14,
                    "pdf_sha256": self.pdf_sha256,
                    "location_map_id": "map-001",
                    "mapping_fingerprint": HASH_A,
                    "locator_id": "anchor:E-ANCHOR",
                    "source_clean_sha256": self.clean_sha256,
                    "mapping_method": "word_com",
                    "locator_text": None,
                }],
            }],
        }
        map_path = self.root / "location_map.json"
        map_path.write_text(json.dumps(location_map), encoding="utf-8")
        location_map["location_map_file"] = str(map_path)
        location_map["location_map_file_sha256"] = hashlib.sha256(
            map_path.read_bytes()
        ).hexdigest()
        ledger.record_response_locations(location_map, actor="Location Mapper")
        self.assertEqual(ledger.response_units[0].status, "location_mapped")
        self.assertEqual(ledger.response_units[0].final_locations[0].page, 3)

        ledger.complete_response_units(
            ["RU-1"],
            ready_for_submission_confirmed=True,
            actor="Human Author",
        )
        self.assertEqual(ledger.response_units[0].status, "completed")
        self.assertEqual(len(ledger.events), 3)
        self.assertEqual(
            [event.subject_type for event in ledger.events],
            ["response_unit", "response_unit", "response_unit"],
        )
        ledger.to_dict()

    def test_response_completion_waits_for_every_linked_patch_to_be_verified(self):
        patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please explain.",
            proposed_response="We explained it.",
            operations=[_operation()],
            status="verified",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-001",
            approval_fingerprint=HASH_A,
            apply_report_ids=["apply-P1"],
            verification_report_id="verify-001",
            verified_output_hashes=[{
                "path": "Manuscript_revised.docx",
                "sha256": HASH_B,
            }],
        )
        unit = ResponseUnit(
            response_unit_id="RU-1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain.",
            response_text="We explained it.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-001"],
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="response-verify-001",
        )
        ledger = RevisionLedger("round-response-patch", documents=[self.document], patches=[patch], response_units=[unit])
        location = ResponseLocation(
            document="Manuscript",
            output_file=str(self.marked_docx),
            pdf_file=str(self.pdf_path),
            page=2,
            line_start=4,
            line_end=5,
            pdf_sha256=self.pdf_sha256,
            location_map_id="map-002",
            mapping_fingerprint=HASH_A,
            output_sha256=hashlib.sha256(self.marked_docx.read_bytes()).hexdigest(),
            locator_id="OP-001",
            source_clean_sha256=self.clean_sha256,
        )
        unit.record_final_locations(
            HASH_A,
            [location],
            location_map_file=str(self.map_path),
            location_map_file_sha256=self.map_sha256,
        )
        ledger.complete_response_units(
            ["RU-1"],
            ready_for_submission_confirmed=True,
            actor="Human Author",
        )
        self.assertEqual(unit.status, "completed")

    def test_patch_supersession_reopens_response_and_rebases_on_prior_output(self):
        previous = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify the statement.",
            proposed_response="We clarified the statement.",
            operations=[_operation(
                operation_id="P1-OP1",
                input_file="Manuscript.docx",
                output_file="Manuscript_r1.docx",
            )],
            status="applied",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-P1",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-P1"],
        )
        response = ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify the statement.",
            response_text="We clarified the statement.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["P1-OP1"],
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="response-semantic-P1",
        )
        ledger = RevisionLedger(
            "round-supersession",
            patches=[previous],
            response_units=[response],
        )
        replacement = Patch(
            patch_id="P2",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify the statement.",
            proposed_response="We revised the clarification again.",
            operations=[_operation(
                operation_id="P2-OP1",
                input_file="Manuscript_r1.docx",
                output_file="Manuscript_r2.docx",
                find="revised phrase",
                replace="more precise phrase",
            )],
            supersedes_patch_id="P1",
        )
        ledger.supersede_patch(
            "P1",
            replacement,
            actor="Human Author",
            reason="The first revision needs a more precise description.",
        )
        self.assertEqual(previous.status, "superseded")
        self.assertEqual(replacement.status, "planned")
        self.assertEqual(response.linked_patch_ids, ["P2"])
        self.assertEqual(response.status, "draft")
        self.assertFalse(response.semantic_review_confirmed)
        self.assertIsNone(response.verification_report_id)
        self.assertEqual(
            [item["id"] for item in ledger.to_legacy_revision_plan()["revision_items"]],
            ["P2"],
        )
        self.assertEqual(
            [event.event_type for event in ledger.events],
            [
                "patch_superseded",
                "patch_created_as_replacement",
                "response_reopened_after_patch_supersession",
            ],
        )
        ledger.events[1].details["supersedes_patch_id"] = "P404"
        with self.assertRaisesRegex(RevisionModelError, "supersession event lineage"):
            ledger.validate_or_raise()

    def test_patch_supersession_refuses_to_rebase_from_wrong_source(self):
        previous = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="Clarified.",
            operations=[_operation(
                input_file="Manuscript.docx",
                output_file="Manuscript_r1.docx",
            )],
            status="verified",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-P1",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-P1"],
            verification_report_id="verify-P1",
            verified_output_hashes=[{"path": "Manuscript_r1.docx", "sha256": HASH_B}],
        )
        ledger = RevisionLedger("round-wrong-rebase", patches=[previous])
        replacement = Patch(
            patch_id="P2",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="Updated clarification.",
            operations=[_operation(
                input_file="Manuscript.docx",
                output_file="Manuscript_r2.docx",
            )],
            supersedes_patch_id="P1",
        )
        with self.assertRaisesRegex(RevisionModelError, "must use the prior Patch output"):
            ledger.supersede_patch(
                "P1",
                replacement,
                actor="Human Author",
                reason="Replace earlier approved wording.",
            )
        self.assertEqual(previous.status, "verified")
        self.assertEqual(len(ledger.patches), 1)

    def test_executed_patch_cannot_be_reexported_as_a_fresh_pending_edit(self):
        patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="Clarified.",
            operations=[_operation()],
            status="applied",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-P1",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-P1"],
        )
        ledger = RevisionLedger("round-no-replay", patches=[patch])
        with self.assertRaisesRegex(RevisionModelError, "no executable patches"):
            ledger.to_legacy_revision_plan()
        with self.assertRaisesRegex(RevisionModelError, "only a new planned Patch"):
            patch.to_legacy_revision_item()

    def test_verified_patch_must_seal_every_operation_output(self):
        patch = Patch(
            patch_id="P-MULTI",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Update the two affected locations.",
            proposed_response="We updated both locations.",
            operations=[
                _operation(
                    operation_id="OP-1",
                    output_file="Manuscript_revised.docx",
                ),
                _operation(
                    operation_id="OP-2",
                    output_file="SI_revised.docx",
                    document="SI",
                    input_file="SI.docx",
                ),
            ],
            status="verified",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-multi",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-multi"],
            verification_report_id="verify-multi",
            verified_output_hashes=[{
                "path": "Manuscript_revised.docx",
                "sha256": HASH_A,
            }],
        )
        with self.assertRaisesRegex(RevisionModelError, "cover exactly its DOCX outputs"):
            RevisionLedger("round-partial-output-hash", patches=[patch])

    def test_incremental_batch_merge_preserves_history_and_reopens_changed_response(self):
        first_patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="We clarified the statement.",
            operations=[_operation(operation_id="P1-OP")],
        )
        response = ResponseUnit(
            response_unit_id="RU-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify.",
            response_text="We clarified the statement.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["P1-OP"],
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="response-review-1",
        )
        round_ledger = RevisionLedger(
            "round-incremental",
            patches=[first_patch],
            response_units=[response],
            workflow_ledger_fingerprint=HASH_A,
            workflow_task_fingerprints={"T1": HASH_B},
        )
        second_patch = Patch(
            patch_id="P2",
            task_id="T2",
            reviewer="Reviewer 1",
            comment="Please clarify.",
            proposed_response="We clarified the statement with the dependent result.",
            operations=[_operation(
                operation_id="P2-OP",
                input_file="Manuscript_r1.docx",
                output_file="Manuscript_r2.docx",
            )],
        )
        incoming_response = ResponseUnit(
            response_unit_id="RU-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please clarify.",
            response_text="We clarified the statement with the dependent result.",
            linked_task_ids=["T1", "T2"],
            linked_patch_ids=["P2"],
            linked_operation_ids=["P2-OP"],
            linked_targets=[{
                "document": "Manuscript",
                "location": "Updated Results",
                "output_file": "Manuscript_r2.docx",
            }],
            status="draft",
        )
        addition = RevisionLedger(
            "round-incremental",
            patches=[second_patch],
            response_units=[incoming_response],
            workflow_ledger_fingerprint=HASH_C,
            workflow_task_fingerprints={"T1": HASH_B, "T2": HASH_A},
        )
        round_ledger.merge_batch(addition)
        self.assertEqual(
            [patch.patch_id for patch in round_ledger.patches],
            ["P1", "P2"],
        )
        self.assertEqual(round_ledger.patches[0].status, "planned")
        self.assertEqual(response.linked_patch_ids, ["P1", "P2"])
        self.assertEqual(response.status, "draft")
        self.assertFalse(response.semantic_review_confirmed)
        self.assertIsNone(response.verification_report_id)
        self.assertEqual(
            round_ledger.workflow_task_fingerprints,
            {"T1": HASH_B, "T2": HASH_A},
        )
        self.assertEqual(round_ledger.workflow_ledger_fingerprint, HASH_C)

    def test_incremental_batch_merge_rejects_conflicting_duplicate_patch_ids(self):
        existing_patch = Patch(
            patch_id="P-DUP",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Original comment.",
            proposed_response="Original response.",
            operations=[_operation(operation_id="P-DUP-OP")],
        )
        conflicting_patch = Patch(
            patch_id="P-DUP",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Changed comment.",
            proposed_response="Conflicting response.",
            operations=[_operation(operation_id="P-DUP-OP")],
        )
        existing = RevisionLedger("round-duplicate-id", patches=[existing_patch])
        addition = RevisionLedger("round-duplicate-id", patches=[conflicting_patch])

        with self.assertRaisesRegex(RevisionModelError, "already exist with different contents"):
            existing.merge_batch(addition)

        self.assertEqual(existing.patches, [existing_patch])
        self.assertEqual(existing.patches[0].comment, "Original comment.")

    def test_incremental_batch_merge_rejects_changed_prior_task_scope(self):
        existing = RevisionLedger(
            "round-no-drift",
            patches=[],
            workflow_ledger_fingerprint=HASH_A,
            workflow_task_fingerprints={"T1": HASH_B},
        )
        incoming = RevisionLedger(
            "round-no-drift",
            patches=[],
            workflow_ledger_fingerprint=HASH_C,
            workflow_task_fingerprints={"T1": HASH_C, "T2": HASH_A},
        )
        with self.assertRaisesRegex(RevisionModelError, "Workflow task scope changed"):
            existing.merge_batch(incoming)
        self.assertEqual(existing.workflow_task_fingerprints, {"T1": HASH_B})

    def test_revision_ledger_round_trips_without_losing_patch_lineage(self):
        patch = Patch(
            patch_id="P2",
            task_id="T1",
            reviewer="Reviewer 2",
            comment="Update the wording.",
            proposed_response="Updated.",
            operations=[_operation(operation_id="OP-2")],
            supersedes_patch_id="P1",
        )
        ledger = RevisionLedger(
            "round-lineage",
            patches=[
                Patch(
                    patch_id="P1",
                    task_id="T1",
                    reviewer="Reviewer 1",
                    comment="Old patch.",
                    proposed_response="",
                    operations=[_operation(operation_id="OP-1")],
                    status="superseded",
                ),
                patch,
            ],
            events=[
                RevisionEvent(
                    event_id="event-patch-superseded-P1",
                    subject_id="P1",
                    subject_type="patch",
                    event_type="patch_superseded",
                    timestamp_utc="2026-11-15T08:00:00+08:00",
                    actor="Human Author",
                    details={
                        "replacement_patch_id": "P2",
                        "reason": "Round-trip supersession fixture.",
                        "previous_status": "planned",
                    },
                ),
                RevisionEvent(
                    event_id="event-patch-replacement-P2",
                    subject_id="P2",
                    subject_type="patch",
                    event_type="patch_created_as_replacement",
                    timestamp_utc="2026-11-15T08:00:00+08:00",
                    actor="Human Author",
                    details={
                        "supersedes_patch_id": "P1",
                        "reason": "Round-trip supersession fixture.",
                    },
                ),
            ],
        )
        restored = revision_ledger_from_mapping(ledger.to_dict())
        self.assertEqual(restored.patches[1].supersedes_patch_id, "P1")
        self.assertEqual(restored.patches[0].status, "superseded")


if __name__ == "__main__":
    unittest.main()

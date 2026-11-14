import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.identity import revision_item_fingerprint, revision_targets_fingerprint
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseLocation,
    ResponseUnit,
    RevisionLedger,
    RevisionModelError,
    build_revision_ledger,
    response_units_to_legacy_plan,
    revision_ledger_from_mapping,
)

HASH_A = "a" * 64
HASH_B = "b" * 64
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
            "final_locations" in condition.get("then", {}).get("required", [])
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

    def test_response_only_task_does_not_generate_fake_patch(self):
        workflow = {
            "revision_round_id": "round-response",
            "tasks": [{
                "task_id": "R1",
                "task_type": "response_only",
                "source_comment_ids": ["C1"],
                "evidence": [{
                    "evidence_type": "reviewer_comment",
                    "source_comment_id": "C1",
                    "source_excerpt": "Please explain the mechanism.",
                }],
                "patch_operations": [],
                "proposed_response": "The mechanism is described in the revised discussion.",
            }],
        }
        ledger = build_revision_ledger(workflow, document_paths={})
        self.assertEqual(ledger.patches, [])
        self.assertEqual(len(ledger.response_units), 1)
        self.assertEqual(ledger.response_units[0].status, "draft")
        self.assertEqual(ledger.response_units[0].linked_patch_ids, [])

    def test_build_revision_ledger_requires_traceable_reviewer_comment(self):
        workflow = {
            "revision_round_id": "round-no-comment",
            "tasks": [{
                "task_id": "T1",
                "task_type": "local_revision",
                "source_comment_ids": ["C1"],
                "evidence": [],
                "patch_operations": [{
                    "document": "Manuscript",
                    "input_file": "Manuscript.docx",
                    "output_file": "Revised.docx",
                    "part": "word/document.xml",
                    "find": "old",
                    "replace": "new",
                }],
            }],
        }
        with self.assertRaisesRegex(RevisionModelError, "no traceable reviewer comment"):
            build_revision_ledger(workflow, document_paths={})

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
            status="location_mapped",
            semantic_review_confirmed=True,
            verification_report_id="semantic-001",
            location_map_fingerprint=HASH_A,
            final_locations=[ResponseLocation(
                document="Manuscript",
                output_file="Manuscript_revised.docx",
                pdf_file=str(self.pdf_path),
                page=3,
                line_start=12,
                line_end=15,
                pdf_sha256=self.pdf_sha256,
                location_map_id="map-001",
                mapping_fingerprint=HASH_A,
            )],
        )
        draft_plan = response_units_to_legacy_plan([unit])
        final_plan = response_units_to_legacy_plan([mapped])
        self.assertEqual(draft_plan["revision_items"][0]["status"], "planned")
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
                    output_file="Revised.docx",
                    pdf_file=str(self.pdf_path),
                    page=1,
                    line_start=1,
                    line_end=1,
                    pdf_sha256=self.pdf_sha256,
                    location_map_id="map-001",
                    mapping_fingerprint=HASH_A,
                )],
            )

    def test_response_unit_semantic_review_location_mapping_and_completion_are_separate(self):
        ledger = RevisionLedger(
            "round-response-lifecycle",
            response_units=[ResponseUnit(
                response_unit_id="RU-1",
                reviewer="Reviewer 1",
                comment_id="C1",
                comment_text="Please explain.",
                response_text="We have explained the process.",
            )],
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
                    "output_file": "Manuscript_revised.docx",
                    "pdf_file": str(self.pdf_path),
                    "page": 3,
                    "line_start": 12,
                    "line_end": 14,
                    "pdf_sha256": self.pdf_sha256,
                    "location_map_id": "map-001",
                    "mapping_fingerprint": HASH_A,
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
            verification_report_id="verify-001",
            verified_output_hashes=[{"path": "revised.docx", "sha256": HASH_B}],
        )
        unit = ResponseUnit(
            response_unit_id="RU-1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please explain.",
            response_text="We explained it.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="response-verify-001",
        )
        ledger = RevisionLedger("round-response-patch", patches=[patch], response_units=[unit])
        location = ResponseLocation(
            document="Manuscript",
            output_file="Manuscript_revised.docx",
            pdf_file=str(self.pdf_path),
            page=2,
            line_start=4,
            line_end=5,
            pdf_sha256=self.pdf_sha256,
            location_map_id="map-002",
            mapping_fingerprint=HASH_A,
        )
        unit.record_final_locations(HASH_A, [location])
        ledger.complete_response_units(
            ["RU-1"],
            ready_for_submission_confirmed=True,
            actor="Human Author",
        )
        self.assertEqual(unit.status, "completed")

    def test_revision_ledger_round_trips_without_losing_patch_lineage(self):
        patch = Patch(
            patch_id="P2",
            task_id="T2",
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
        )
        restored = revision_ledger_from_mapping(ledger.to_dict())
        self.assertEqual(restored.patches[1].supersedes_patch_id, "P1")
        self.assertEqual(restored.patches[0].status, "superseded")


if __name__ == "__main__":
    unittest.main()

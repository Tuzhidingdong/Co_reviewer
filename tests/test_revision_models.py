import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
        approval = {
            "id": "T1",
            "status": "applied",
            "approval_fingerprint": HASH_A,
        }
        report = {
            "input_file": "Manuscript.docx",
            "output_file": "Manuscript_revised.docx",
            "edits_applied": 1,
            "results": [{
                "revision_item_id": "T1",
                "status": "applied",
            }],
        }
        ledger.record_legacy_apply_reports([report], {"revision_items": [approval]})
        self.assertEqual(patch.status, "applied")
        self.assertEqual(patch.approval_fingerprint, HASH_A)
        self.assertEqual(len(ledger.events), 1)

        verified_item = {
            "id": "T1",
            "status": "verified",
            "approval_fingerprint": HASH_A,
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
        self.assertEqual(len(ledger.events), 2)
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
                page=3,
                line_start=12,
                line_end=15,
                pdf_sha256=HASH_B,
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
                    page=1,
                    line_start=1,
                    line_end=1,
                    pdf_sha256=HASH_B,
                    location_map_id="map-001",
                    mapping_fingerprint=HASH_A,
                )],
            )

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

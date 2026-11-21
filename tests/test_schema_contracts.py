from __future__ import annotations

import json
import unittest
from pathlib import Path

from jsonschema.validators import validator_for


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


class JsonSchemaContractTests(unittest.TestCase):
    def test_batch_creation_protocol_intent_and_receipt_contracts(self):
        intent_schema = json.loads((SCHEMA_DIR / "batch-intent.schema.json").read_text(encoding="utf-8"))
        receipt_schema = json.loads((SCHEMA_DIR / "batch-creation-receipt.schema.json").read_text(encoding="utf-8"))
        intent = {
            "schema_version": "1.0",
            "batch_id": "batch-test-001",
            "revision_round_id": "round-schema-tests",
            "workflow_ledger_file": "/round/workflow_ledger.json",
            "workflow_ledger_sha256_at_prepare": "a" * 64,
            "batch_creation_protocol_version": "1.0",
            "revision_ledger_file": "/round/revision_ledger.json",
            "revision_ledger_sha256_before": None,
            "revision_ledger_sha256_after": "b" * 64,
            "created_at_utc": "2026-11-16T00:00:00+00:00",
            "selected_task_ids": ["T1"],
            "all_selected_task_ids": ["T1"],
            "status": "prepared",
            "documents": {
                "Manuscript": {
                    "parent_file": "/round/source.docx",
                    "parent_sha256": "c" * 64,
                    "parent_batch_id": None,
                    "output_file": "/round/runs/batch-test-001/documents/Manuscript.marked.docx"
                }
            },
            "policy": {
                "append_only": True,
                "overwrite_committed_outputs": False,
                "rollback_deletes_files": False,
                "publish_mode": "single_workflow_ledger_active_documents_replace"
            }
        }
        intent_validator = validator_for(intent_schema)(intent_schema)
        intent_validator.validate(intent)
        legacy_intent = dict(intent)
        for field in (
            "batch_creation_protocol_version",
            "revision_ledger_file",
            "revision_ledger_sha256_before",
            "revision_ledger_sha256_after"
        ):
            legacy_intent.pop(field)
        intent_validator.validate(legacy_intent)

        common = {
            "schema_version": "1.0",
            "batch_id": "batch-test-001",
            "created_at_utc": "2026-11-16T00:00:00+00:00",
            "revision_ledger_file": "/round/revision_ledger.json",
            "revision_ledger_sha256_before": None,
            "revision_ledger_sha256_after": "b" * 64,
            "batch_intent_sha256": "d" * 64,
            "active_parent_pointers_unchanged": True,
            "execution_artifacts_present": False,
            "retained_batch_files": True
        }
        committed = {
            **common,
            "status": "committed",
            "batch_scope_sha256": "e" * 64,
            "revision_ledger_committed": True,
            "no_patch_records_committed": False
        }
        abandoned = {
            **common,
            "status": "abandoned_before_ledger_commit",
            "batch_scope_sha256": None,
            "revision_ledger_committed": False,
            "no_patch_records_committed": True
        }
        receipt_validator = validator_for(receipt_schema)(receipt_schema)
        receipt_validator.validate(committed)
        receipt_validator.validate(abandoned)

    def test_revision_plan_and_ledger_schema_include_strict_formatting_contracts(self):
        plan_schema = json.loads((SCHEMA_DIR / "revision-plan.schema.json").read_text(encoding="utf-8"))
        ledger_schema = json.loads((SCHEMA_DIR / "revision-ledger.schema.json").read_text(encoding="utf-8"))
        plan_target = plan_schema["properties"]["revision_items"]["items"]["properties"]["targets"]["items"]
        ledger_operation = ledger_schema["$defs"]["patchOperation"]
        for schema in (plan_target, ledger_operation):
            self.assertIn("replacement_format_map", schema["properties"])
            self.assertIn("format_authorization", schema["properties"])
            self.assertIn("format_operations", schema["properties"])
            self.assertFalse(schema["additionalProperties"])
        operation = {
            "operation_id": "OP-FORMAT",
            "document": "Manuscript",
            "input_file": "source.docx",
            "output_file": "marked.docx",
            "part": "word/document.xml",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "find": "Journal Name",
            "replace": "Journal Name",
            "evidence_ids": ["E1"],
            "source_comment_ids": ["C1"],
            "format_authorization": {"intent": "Restore journal typography"},
            "format_operations": [{"find": "Journal", "properties": {"i": True}}],
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 12, "source_offset": 0}
            ],
        }
        validator_for(ledger_schema)(ledger_schema).validate({
            "schema_version": "0.5.1",
            "revision_round_id": "round-schema-format",
            "documents": [],
            "patches": [{
                "patch_id": "T-FORMAT",
                "task_id": "T-FORMAT",
                "reviewer": "Reviewer 1",
                "comment": "Restore typography",
                "proposed_response": "Formatting restored",
                "operations": [operation],
                "status": "planned",
                "apply_report_ids": [],
                "verified_output_hashes": [],
            }],
            "response_units": [],
            "events": [],
        })

        ledger_validator = validator_for(ledger_schema)(ledger_schema)
        valid_ledger = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-schema-format",
            "documents": [],
            "patches": [{
                "patch_id": "T-FORMAT",
                "task_id": "T-FORMAT",
                "reviewer": "Reviewer 1",
                "comment": "Restore typography",
                "proposed_response": "Formatting restored",
                "operations": [operation],
                "status": "planned",
                "apply_report_ids": [],
                "verified_output_hashes": [],
            }],
            "response_units": [],
            "events": [],
        }
        ledger_validator.validate(valid_ledger)

        implicit_legacy = json.loads(json.dumps(valid_ledger))
        implicit_legacy["patches"][0]["operations"][0].pop("target_identity_mode")
        with self.assertRaises(Exception):
            ledger_validator.validate(implicit_legacy)

        null_target_without_mode = json.loads(json.dumps(valid_ledger))
        null_operation = null_target_without_mode["patches"][0]["operations"][0]
        null_operation["target_ref"] = None
        null_operation.pop("target_identity_mode")
        with self.assertRaises(Exception):
            ledger_validator.validate(null_target_without_mode)

        stable_target = json.loads(json.dumps(valid_ledger))
        stable_operation = stable_target["patches"][0]["operations"][0]
        stable_operation["target_ref"] = {
            "document_id": "doc-1",
            "revision_id": "rev-1",
            "object_id": "obj-1",
            "object_type": "paragraph",
            "part": "word/document.xml",
            "object_fingerprint": "a" * 64,
            "start_offset": None,
            "end_offset": None,
            "location_hint": {"p_idx": 0},
        }
        stable_operation["target_identity_mode"] = "stable_target_ref"
        ledger_validator.validate(stable_target)


    def test_bounded_qa_remediation_state_schema_accepts_pending_and_complete_only(self):
        schema = json.loads((SCHEMA_DIR / "qa-remediation.schema.json").read_text(encoding="utf-8"))
        validator = validator_for(schema)(schema)
        base = {
            "schema_version": "1.0",
            "state_id": "state-qa-1",
            "revision_round_id": "round-1",
            "phase": "PATCH_COMMIT_PENDING",
            "remediation_round": 1,
            "created_at_utc": "2026-11-18T12:00:00+00:00",
            "actor": "Author",
            "reason": "Mechanical formatting correction supported by QA evidence",
            "event_id": "event-start-1",
            "revision_ledger_file": "revision_ledger.json",
            "patch_id": "PATCH-1",
            "operation_id": "OP-1",
            "document_role": "Manuscript",
            "marked_input_file": "source.marked.docx",
            "marked_input_sha256": "a" * 64,
            "marked_output_file": "remediation.marked.docx",
            "batch_id": "batch-qa-1",
            "batch_dir": "runs/batch-qa-1",
            "batch_intent_sha256": "b" * 64,
            "batch_scope_sha256": "c" * 64,
            "pre_remediation_qa_report_file": "before.quality.json",
            "pre_remediation_qa_report_sha256": "d" * 64,
            "pre_remediation_clean_file": "before.clean.docx",
            "pre_remediation_clean_sha256": "e" * 64,
            "clean_must_be_recompiled_from_marked": True,
            "second_qa_failure_action": "BLOCKED",
        }
        validator.validate(base)
        completed = {
            **base,
            "phase": "COMPLETE",
            "completed_at_utc": "2026-11-18T12:05:00+00:00",
            "completion_event_id": "event-complete-1",
            "qa_status": "PASS",
            "qa_report_file": "after.quality.json",
            "qa_report_sha256": "f" * 64,
            "clean_file": "after.clean.docx",
            "clean_sha256": "1" * 64,
            "marked_file": "remediation.marked.docx",
            "marked_sha256": "2" * 64,
            "compiled_revision_ids": ["101", "102"],
            "block_reason": None,
        }
        validator.validate(completed)
        malformed = dict(completed)
        malformed["remediation_round"] = 2
        with self.assertRaises(Exception):
            validator.validate(malformed)

    def test_every_repository_schema_passes_its_declared_meta_schema(self):
        schema_paths = sorted(SCHEMA_DIR.glob("*.schema.json"))
        self.assertTrue(schema_paths, "No JSON Schema files were found.")

        for schema_path in schema_paths:
            with self.subTest(schema=schema_path.name):
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                validator_type = validator_for(schema)
                validator_type.check_schema(schema)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from validate_decision_plan import validate_decision_plan
from co_reviewer.workflow_engine import workflow_tasks_from_decision_plan


HASH_M = "a" * 64
HASH_R = "b" * 64


def fixture():
    inventory = [
        {
            "role": "Manuscript",
            "file": "Manuscript.docx",
            "source_sha256": HASH_M,
            "paragraphs": [
                {
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "para_id": "P0001",
                    "text": "The selected sample is ZB-01 and must remain consistent.",
                }
            ],
            "comments": [],
        },
        {
            "role": "Reviewers",
            "file": "Reply.docx",
            "source_sha256": HASH_R,
            "paragraphs": [],
            "comments": [{
                "id": "1",
                "comment": "Please keep the sample identifier consistent.",
                "comment_xml_p_idx": 0,
            }],
        },
    ]
    audit = {
        "source_files": [
            {"role": "Manuscript", "file": "Manuscript.docx", "sha256": HASH_M},
            {"role": "Reviewers", "file": "Reply.docx", "sha256": HASH_R},
        ],
        "comment_matches": [{"comment_id": "1"}],
    }
    plan = {
        "schema_version": "1.0",
        "comment_scope": "all_comments",
        "source_files": [
            {"role": "Manuscript", "file": "Manuscript.docx", "sha256": HASH_M},
            {"role": "Reviewers", "file": "Reply.docx", "sha256": HASH_R},
        ],
        "tasks": [
            {
                "task_id": "T1",
                "sequence": 1,
                "title": "Align sample identifier",
                "task_type": "cross_document_consistency",
                "source_comment_ids": ["1"],
                "target_documents": ["Manuscript"],
                "evidence": [
                    {
                        "evidence_id": "E1",
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "para_id": "P0001",
                        "source_excerpt": "The selected sample is ZB-01",
                        "evidence_type": "paragraph",
                        "match_status": "confirmed",
                    }
                ],
                "proposed_action": "Check and align the sample identifier across all affected documents.",
                "targets": [
                    {
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "para_id": "P0001",
                        "action": "replace",
                        "evidence_ids": ["E1"],
                    }
                ],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
            }
        ],
    }
    return plan, audit, inventory


class DecisionPlanValidationTests(unittest.TestCase):
    def test_valid_pending_plan_passes_provenance_and_approval_checks(self):
        plan, audit, inventory = fixture()
        self.assertEqual(validate_decision_plan(plan, audit, inventory), [])

    def test_response_only_task_remains_in_workflow_without_docx_targets(self):
        plan, audit, inventory = fixture()
        task = plan["tasks"][0]
        task["task_type"] = "response_only"
        task["target_documents"] = []
        task["targets"] = []
        task["proposed_action"] = (
            "Prepare the author response using the cited evidence without editing a DOCX."
        )

        self.assertEqual(validate_decision_plan(plan, audit, inventory), [])
        workflow_tasks = workflow_tasks_from_decision_plan(plan["tasks"])
        self.assertEqual(workflow_tasks[0]["task_type"], "response_only")
        self.assertEqual(workflow_tasks[0]["target_groups"], [])

    def test_unknown_source_comment_id_is_rejected(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["source_comment_ids"] = ["999"]
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("unknown reviewer comment ID" in error for error in errors))

    def test_excerpt_must_be_grounded_in_cited_inventory_location(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["evidence"][0]["source_excerpt"] = "sample is ZB-99"
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("source_excerpt is not present" in error for error in errors))

    def test_target_must_resolve_to_an_inventory_paragraph(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["targets"][0]["p_idx"] = 10
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("target paragraph absent" in error for error in errors))

    def test_dependency_cycle_is_rejected(self):
        plan, audit, inventory = fixture()
        second = dict(plan["tasks"][0])
        second["task_id"] = "T2"
        second["dependencies"] = ["T1"]
        plan["tasks"][0]["dependencies"] = ["T2"]
        plan["tasks"].append(second)
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("Dependency cycle detected" in error for error in errors))

    def test_decision_plan_cannot_claim_approval_or_applied_state(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["status"] = "approved"
        plan["tasks"][0]["requires_user_approval"] = False
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("status must remain 'pending'" in error for error in errors))
        self.assertTrue(any("requires_user_approval must be true" in error for error in errors))

    def test_full_round_plan_must_cover_every_comment(self):
        plan, audit, inventory = fixture()
        second = dict(plan["tasks"][0])
        second["task_id"] = "T2"
        second["sequence"] = 2
        second["source_comment_ids"] = []
        second["dependencies"] = ["T1"]
        plan["tasks"].append(second)
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertEqual(errors, [])

    def test_dependency_must_appear_before_dependent_task(self):
        plan, audit, inventory = fixture()
        second = dict(plan["tasks"][0])
        second["task_id"] = "T2"
        second["sequence"] = 1
        second["source_comment_ids"] = []
        second["dependencies"] = ["T1"]
        plan["tasks"][0]["sequence"] = 2
        plan["tasks"].append(second)
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("sequenced after dependency" in error for error in errors))

    def test_full_round_plan_cannot_silently_omit_a_comment(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["source_comment_ids"] = []
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("omitted reviewer comment ID" in error for error in errors))

    def test_plan_must_preserve_audit_source_hashes(self):
        plan, audit, inventory = fixture()
        plan["source_files"][0]["sha256"] = "c" * 64
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("does not match the audit/inventory source identity" in error for error in errors))

    def test_native_comment_body_can_be_cited_without_a_resolved_selection(self):
        plan, audit, inventory = fixture()
        evidence = plan["tasks"][0]["evidence"][0]
        evidence.update({
            "document": "Reviewers",
            "part": "word/comments.xml",
            "p_idx": 0,
            "para_id": None,
            "source_comment_id": "1",
            "source_excerpt": "Please keep the sample identifier consistent.",
            "evidence_type": "reviewer_comment",
        })
        plan["tasks"][0]["evidence"].append({
            "evidence_id": "E2",
            "document": "Manuscript",
            "part": "word/document.xml",
            "p_idx": 0,
            "para_id": "P0001",
            "source_excerpt": "The selected sample is ZB-01",
            "evidence_type": "paragraph",
            "match_status": "confirmed",
        })
        target = plan["tasks"][0]["targets"][0]
        target.update({
            "document": "Manuscript",
            "part": "word/document.xml",
            "p_idx": 0,
            "para_id": "P0001",
            "evidence_ids": ["E1", "E2"],
        })
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertEqual(errors, [])

    def test_schema_file_is_valid_json_and_enforces_pending_approval(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "revision-decision-plan.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertIn("comment_scope", schema["required"])
        self.assertIn("status", schema["properties"]["tasks"]["items"]["required"])
        self.assertEqual(
            schema["properties"]["tasks"]["items"]["properties"]["status"]["const"],
            "pending",
        )
        self.assertIs(
            schema["properties"]["tasks"]["items"]["properties"]["requires_user_approval"]["const"],
            True,
        )

    def test_malformed_ai_fields_are_reported_without_crashing(self):
        plan, audit, inventory = fixture()
        plan["tasks"][0]["target_documents"] = [{"not": "a role"}]
        plan["tasks"][0]["targets"][0]["document"] = {"not": "a role"}
        plan["tasks"][0]["targets"][0]["action"] = ["replace"]
        plan["tasks"][0]["targets"][0]["evidence_ids"] = [{"not": "an id"}]
        errors = validate_decision_plan(plan, audit, inventory)
        self.assertTrue(any("target_documents" in error for error in errors))
        self.assertTrue(any("known document role" in error for error in errors))
        self.assertTrue(any("action has an unsupported" in error for error in errors))
        self.assertTrue(any("evidence_ids" in error for error in errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)

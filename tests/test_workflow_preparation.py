from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.workflow_engine import build_workflow_ledger
from co_reviewer.workflow_preparation import (
    WorkflowPreparationError,
    prepare_workflow_execution,
)


STAMP = "2026-11-16T00:00:00+00:00"


class WorkflowPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.reviewers = self.root / "Reviewers.docx"
        self.manuscript = self.root / "Manuscript.docx"
        self.reviewers.write_bytes(b"synthetic reviewers source")
        self.manuscript.write_bytes(b"synthetic manuscript source")
        self.workflow_path = self.root / "workflow_ledger.json"
        self.scope_path = self.root / "workflow_execution_scope.json"

        reviewer_sha = hashlib.sha256(self.reviewers.read_bytes()).hexdigest()
        manuscript_sha = hashlib.sha256(self.manuscript.read_bytes()).hexdigest()
        plan = {
            "source_files": [
                {"role": "Reviewers", "file": str(self.reviewers), "sha256": reviewer_sha},
                {"role": "Manuscript", "file": str(self.manuscript), "sha256": manuscript_sha},
            ],
            "tasks": [{
                "task_id": "T1",
                "sequence": 1,
                "task_type": "local_revision",
                "title": "Update the sample identifier",
                "source_comment_ids": ["1"],
                "target_documents": ["Manuscript"],
                "evidence": [
                    {
                        "evidence_id": "E-COMMENT-1",
                        "document": "Reviewers",
                        "part": "word/comments.xml",
                        "p_idx": 0,
                        "source_excerpt": "Please use ZB-02 consistently.",
                        "evidence_type": "reviewer_comment",
                        "match_status": "confirmed",
                        "source_comment_id": "1",
                    },
                    {
                        "evidence_id": "E-MAN-1",
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "source_excerpt": "The selected sample is ZB-01.",
                        "evidence_type": "paragraph",
                        "match_status": "confirmed",
                    },
                ],
                "proposed_action": "Replace the sample identifier at the evidenced manuscript target.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "replace",
                    "evidence_ids": ["E-MAN-1"],
                }],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
            }],
        }
        self.workflow = build_workflow_ledger(plan, "round-workflow-preparation", STAMP)
        self.workflow_path.write_text(
            json.dumps(self.workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.inventory = [
            {
                "role": "Reviewers",
                "file": str(self.reviewers),
                "source_sha256": reviewer_sha,
                "comments": [{"id": "1", "author": "Reviewer 1"}],
            },
            {
                "role": "Manuscript",
                "file": str(self.manuscript),
                "source_sha256": manuscript_sha,
            },
        ]
        self.scope = {
            "schema_version": "1.0",
            "revision_round_id": "round-workflow-preparation",
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "tasks": [{
                "task_id": "T1",
                "patch_id": "P-T1",
                "proposed_response": "We have updated the sample identifier to ZB-02.",
                "patch_operations": [{
                    "operation_id": "T1-OP-MAN",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "find": "ZB-01",
                    "replace": "ZB-02",
                    "location": "The selected sample is ZB-01.",
                    "evidence_ids": ["E-MAN-1"],
                    "source_comment_ids": ["1"],
                }],
            }],
        }
        self.scope_path.write_text(
            json.dumps(self.scope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepares_exact_operation_without_authorizing_or_editing_documents(self) -> None:
        prepared, reviewer_map, report = prepare_workflow_execution(
            self.workflow,
            self.scope,
            self.inventory,
            workflow_path=self.workflow_path,
            execution_scope_path=self.scope_path,
        )
        task = prepared["tasks"][0]
        operation = task["patch_operations"][0]

        self.assertEqual(task["status"], "planned")
        self.assertTrue(task["requires_user_approval"])
        self.assertNotIn("human_approval_confirmed", task)
        self.assertEqual(task["patch_id"], "P-T1")
        self.assertEqual(operation["input_file"], str(self.manuscript.resolve()))
        self.assertEqual(operation["source_sha256"], hashlib.sha256(self.manuscript.read_bytes()).hexdigest())
        self.assertIn(str(Path(self.root / "runs" / ".planned").resolve()), operation["output_file"])
        self.assertEqual(reviewer_map["T1"]["comment_ids"]["1"], "Reviewer 1")
        self.assertEqual(report["status"], "prepared_pending_author_approval")
        self.assertEqual(self.manuscript.read_bytes(), b"synthetic manuscript source")
        self.assertEqual(self.reviewers.read_bytes(), b"synthetic reviewers source")

    def test_rejects_stale_workflow_hash(self) -> None:
        stale_scope = json.loads(json.dumps(self.scope))
        stale_scope["workflow_ledger_sha256"] = "0" * 64
        with self.assertRaisesRegex(WorkflowPreparationError, "is stale"):
            prepare_workflow_execution(
                self.workflow,
                stale_scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_rejects_find_text_outside_target_evidence_excerpt(self) -> None:
        invalid_scope = json.loads(json.dumps(self.scope))
        invalid_scope["tasks"][0]["patch_operations"][0]["find"] = "NOT IN THE EVIDENCE"
        with self.assertRaisesRegex(WorkflowPreparationError, "find text is not present"):
            prepare_workflow_execution(
                self.workflow,
                invalid_scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_rejects_operation_without_same_target_evidence(self) -> None:
        invalid_scope = json.loads(json.dumps(self.scope))
        invalid_scope["tasks"][0]["patch_operations"][0]["evidence_ids"] = ["E-COMMENT-1"]
        with self.assertRaisesRegex(WorkflowPreparationError, "matches 0 evidence-linked targets"):
            prepare_workflow_execution(
                self.workflow,
                invalid_scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_rejects_revised_response_unit_without_linked_patch(self) -> None:
        invalid_scope = json.loads(json.dumps(self.scope))
        invalid_scope["tasks"][0]["response_unit_specs"] = [{
            "comment_id": "1",
            "disposition": "reply_only",
            "location_requirement": "not_required",
        }]
        with self.assertRaisesRegex(WorkflowPreparationError, "links a PatchOperation"):
            prepare_workflow_execution(
                self.workflow,
                invalid_scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )


if __name__ == "__main__":
    unittest.main()

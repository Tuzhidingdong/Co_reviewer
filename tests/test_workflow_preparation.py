from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli
from co_reviewer.workflow_engine import build_workflow_ledger
from co_reviewer.workflow_preparation import (
    WorkflowPreparationError,
    _validate_vertical_alignment_source_range,
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
            "identity_mode": "legacy_p_idx_explicit",
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

    def test_vertical_alignment_source_must_be_exact_local_occurrence(self):
        source = {
            "text": "HB",
            "format_properties": {"vertAlign": "subscript"},
        }
        _validate_vertical_alignment_source_range(
            "OP-1", 0, {"find": "HB"}, source, source["format_properties"]
        )
        with self.assertRaisesRegex(WorkflowPreparationError, "exact text of its source occurrence"):
            _validate_vertical_alignment_source_range(
                "OP-1", 0, {"find": "entire sentence"}, source, source["format_properties"]
            )
        long_source = {
            "text": "The entire sentence is subscript.",
            "format_properties": {"vertAlign": "subscript"},
        }
        with self.assertRaisesRegex(WorkflowPreparationError, "multiword"):
            _validate_vertical_alignment_source_range(
                "OP-1", 1, {"find": long_source["text"]}, long_source, long_source["format_properties"]
            )

    def test_stable_identity_is_the_default_and_never_silently_falls_back_to_p_idx(self):
        scope = json.loads(json.dumps(self.scope))
        scope.pop("identity_mode", None)
        with self.assertRaisesRegex(
            WorkflowPreparationError,
            "has no stable TargetRef; set identity_mode=legacy_p_idx_explicit",
        ):
            prepare_workflow_execution(
                self.workflow,
                scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_explicit_legacy_p_idx_migration_is_blocked_when_identity_registry_exists(self):
        from docx import Document
        from co_reviewer.docx_identity import build_document_identity_manifest

        stable_source = self.root / "registered-Manuscript.docx"
        document = Document()
        document.add_paragraph("The selected sample is ZB-01.")
        document.save(stable_source)
        source_sha = hashlib.sha256(stable_source.read_bytes()).hexdigest()
        identity_manifest, _ = build_document_identity_manifest([{
            "role": "Manuscript",
            "file": str(stable_source),
            "source_sha256": source_sha,
            "paragraphs": [],
        }])

        workflow = json.loads(json.dumps(self.workflow))
        manuscript_source = {
            "role": "Manuscript",
            "file": str(stable_source.resolve()),
            "sha256": source_sha,
        }
        workflow["source_files"] = [
            row for row in workflow["source_files"]
            if row["role"] != "Manuscript"
        ] + [manuscript_source]
        workflow["active_documents"]["Manuscript"] = {
            "file": str(stable_source.resolve()),
            "sha256": source_sha,
            "batch_id": None,
            "updated_at_utc": STAMP,
        }
        for task in workflow["tasks"]:
            task["source_versions"] = [
                row for row in task["source_versions"]
                if row["role"] != "Manuscript"
            ] + [manuscript_source]
        workflow["documents"] = identity_manifest["documents"]
        workflow["object_registry"] = identity_manifest["objects"]
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        inventory = json.loads(json.dumps(self.inventory))
        manuscript_row = next(row for row in inventory if row["role"] == "Manuscript")
        manuscript_row.update({
            "file": str(stable_source.resolve()),
            "source_sha256": source_sha,
        })

        # Even an explicitly marked migration cannot fall back to paragraph
        # indices once a document identity / object registry is available.
        legacy_scope = json.loads(json.dumps(self.scope))
        legacy_scope["identity_mode"] = "legacy_p_idx_explicit"
        legacy_scope["workflow_ledger_sha256"] = hashlib.sha256(
            self.workflow_path.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(
            WorkflowPreparationError,
            "missing its registered TargetRef; rebind the task identity manifest",
        ):
            prepare_workflow_execution(
                workflow,
                legacy_scope,
                inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_prepare_cli_rejects_inventory_bytes_changed_after_manifest_binding(self) -> None:
        from argparse import Namespace

        inventory_path = self.root / "workspace_inventory.json"
        manifest_path = self.root / "round_manifest.json"
        prepared_path = self.root / "prepared.json"
        reviewer_map_path = self.root / "reviewer_map.json"
        report_path = self.root / "preparation_report.json"
        inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        expected_inventory_sha = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps({
            "review_round_id": self.workflow["revision_round_id"],
            "workspace": str(self.root),
            "directories": {"plan": str(self.root / "plan")},
            "workflow_ledger": str(self.workflow_path),
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "artifacts": {"workspace_inventory": str(inventory_path)},
            "artifact_sha256": {"workspace_inventory": expected_inventory_sha},
        }), encoding="utf-8")
        inventory_path.write_text(json.dumps(self.inventory) + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Workspace Inventory bytes"):
            cli.cmd_prepare_workflow_execution(Namespace(
                workflow_ledger=str(self.workflow_path),
                execution_scope=str(self.scope_path),
                inventory=str(inventory_path),
                out=str(prepared_path),
                reviewer_map_out=str(reviewer_map_path),
                report=str(report_path),
                round_manifest=str(manifest_path),
            ))
        self.assertFalse(prepared_path.exists())
        self.assertFalse(reviewer_map_path.exists())
        self.assertFalse(report_path.exists())

    def test_prepare_cli_accepts_dedicated_refresh_manifest(self) -> None:
        from argparse import Namespace

        inventory_path = self.root / "refreshed_inventory.json"
        manifest_path = self.root / "refresh_manifest.json"
        root_manifest_path = self.root / "round_manifest.json"
        prepared_path = self.root / "prepared_refresh.json"
        reviewer_map_path = self.root / "reviewer_map_refresh.json"
        report_path = self.root / "preparation_refresh.json"
        inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        manifest_path.write_text(json.dumps({
            "review_round_id": self.workflow["revision_round_id"],
            "workflow_ledger": str(self.workflow_path),
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "audit_artifacts": {"workspace_inventory": str(inventory_path)},
            "artifact_sha256": {
                "workspace_inventory": hashlib.sha256(inventory_path.read_bytes()).hexdigest()
            },
        }), encoding="utf-8")
        root_manifest_path.write_text(json.dumps({
            "review_round_id": self.workflow["revision_round_id"],
            "workspace": str(self.root),
            "directories": {"plan": str(self.root / "plan")},
            "workflow_ledger": str(self.workflow_path),
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "latest_refresh_inventory": str(inventory_path),
            "latest_refresh_inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
        }), encoding="utf-8")
        self.assertEqual(cli.cmd_prepare_workflow_execution(Namespace(
            workflow_ledger=str(self.workflow_path),
            execution_scope=str(self.scope_path),
            inventory=str(inventory_path),
            out=str(prepared_path),
            reviewer_map_out=str(reviewer_map_path),
            report=str(report_path),
            round_manifest=str(manifest_path),
            round_root_manifest=str(self.root / "round_manifest.json"),
        )), 0)
        root_manifest = json.loads((self.root / "round_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(root_manifest["current_workflow_ledger"], str(prepared_path.resolve()))
        self.assertEqual(root_manifest["workflow_ledger_sha256"], hashlib.sha256(prepared_path.read_bytes()).hexdigest())
        self.assertTrue(prepared_path.is_file())
        self.assertTrue(reviewer_map_path.is_file())
        self.assertTrue(report_path.is_file())

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
        self.assertEqual(task["risk_tier"], "TIER_1")
        self.assertEqual(task["risk_policy_version"], "1.0")
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

    def test_global_numbering_scope_uses_author_approval_without_fabricating_reviewer_comments(self) -> None:
        approval = {
            "schema_version": "1.0",
            "status": "authorized",
            "decision_type": "package_numbering",
            "impact_plan_sha256": "d" * 64,
            "approved_by": "Human Author",
            "approved_at_utc": "2026-11-20T12:00:00+00:00",
        }
        approval["approval_sha256"] = hashlib.sha256(
            json.dumps(approval, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        plan = {
            "source_files": [
                {"role": "Reviewers", "file": str(self.reviewers), "sha256": hashlib.sha256(self.reviewers.read_bytes()).hexdigest()},
                {"role": "Manuscript", "file": str(self.manuscript), "sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest()},
            ],
            "tasks": [{
                "task_id": "GN1",
                "sequence": 1,
                "task_type": "global_numbering",
                "title": "Renumber a package-wide figure reference",
                "source_comment_ids": [],
                "target_documents": ["Manuscript"],
                "evidence": [{
                    "evidence_id": "E-GN1",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "source_excerpt": "The selected sample is ZB-01.",
                    "evidence_type": "audit_finding",
                    "match_status": "confirmed",
                }],
                "proposed_action": "Renumber the caption and all cross-document mentions through one approved package batch.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "renumber",
                    "evidence_ids": ["E-GN1"],
                }],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 1.0,
                "status": "pending",
                "requires_user_approval": True,
            }],
        }
        workflow = build_workflow_ledger(plan, "round-author-numbering", STAMP)
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        scope = {
            "schema_version": "1.0",
            "identity_mode": "legacy_p_idx_explicit",
            "revision_round_id": "round-author-numbering",
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "tasks": [{
                "task_id": "GN1",
                "patch_id": "P-GN1",
                "proposed_response": "We have updated the package-wide figure numbering under the approved numbering plan.",
                "author_decision_ref": approval,
                "patch_operations": [{
                    "operation_id": "GN1-OP1",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "find": "ZB-01",
                    "replace": "ZB-02",
                    "location": "The selected sample is ZB-01.",
                    "evidence_ids": ["E-GN1"],
                    "source_comment_ids": [],
                }],
            }],
        }
        prepared, _, report = prepare_workflow_execution(
            workflow, scope, self.inventory,
            workflow_path=self.workflow_path, execution_scope_path=self.scope_path,
        )
        task = prepared["tasks"][0]
        operation = task["patch_operations"][0]
        self.assertEqual(task["author_decision_ref"], approval)
        self.assertEqual(operation["author_decision_ref"], approval)
        self.assertEqual(operation["source_comment_ids"], [])
        self.assertEqual(report["status"], "prepared_pending_author_approval")
        self.assertEqual(self.manuscript.read_bytes(), b"synthetic manuscript source")

    def test_recheck_requires_a_fresh_numbering_author_approval(self) -> None:
        from co_reviewer.safety_policy import assess_task_risk
        from co_reviewer.workflow_engine import transition_task

        def make_approval(plan_sha: str) -> dict[str, str]:
            value = {
                "schema_version": "1.0",
                "status": "authorized",
                "decision_type": "package_numbering",
                "impact_plan_sha256": plan_sha,
                "approved_by": "Human Author",
                "approved_at_utc": "2026-11-20T12:00:00+00:00",
            }
            value["approval_sha256"] = hashlib.sha256(
                json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            return value

        old_approval = make_approval("d" * 64)
        workflow = json.loads(json.dumps(self.workflow))
        task = workflow["tasks"][0]
        task["task_type"] = "global_numbering"
        task["targets"][0]["action"] = "renumber"
        task["status"] = "ready"
        task["author_decision_ref"] = old_approval
        task["patch_operations"] = [{
            "operation_id": "GN1-OLD-OP",
            "author_decision_ref": old_approval,
        }]
        task = transition_task(
            task,
            "recheck_required",
            reason="An upstream batch changed the package inputs.",
        )
        task.update(assess_task_risk(task))
        workflow["tasks"][0] = task
        self.assertEqual(
            task["invalidated_package_numbering_approval_hashes"],
            [old_approval["approval_sha256"]],
        )
        self.assertNotIn("author_decision_ref", task)
        self.assertEqual(task["patch_operations"], [])
        workflow["active_documents"] = {
            "Reviewers": {
                "file": str(self.reviewers),
                "sha256": hashlib.sha256(self.reviewers.read_bytes()).hexdigest(),
                "batch_id": None,
            },
            "Manuscript": {
                "file": str(self.manuscript),
                "sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
                "batch_id": None,
            },
        }
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        scope = json.loads(json.dumps(self.scope))
        scope_task = scope["tasks"][0]
        scope_task["evidence"] = [{
            "evidence_id": "E-MAN-REFRESH",
            "document": "Manuscript",
            "part": "word/document.xml",
            "p_idx": 0,
            "source_excerpt": "The selected sample is ZB-01.",
            "evidence_type": "audit_finding",
            "match_status": "confirmed",
        }]
        scope_task["targets"] = [{
            "document": "Manuscript",
            "part": "word/document.xml",
            "p_idx": 0,
            "action": "renumber",
            "evidence_ids": ["E-MAN-REFRESH"],
        }]
        scope_task["patch_operations"][0]["evidence_ids"] = ["E-MAN-REFRESH"]

        inventory = json.loads(json.dumps(self.inventory))
        manuscript_inventory = next(
            row for row in inventory if row["role"] == "Manuscript"
        )
        manuscript_inventory["paragraphs"] = [{
            "part": "word/document.xml",
            "p_idx": 0,
            "text": "The selected sample is ZB-01.",
        }]

        def prepare_current_scope():
            scope["workflow_ledger_sha256"] = hashlib.sha256(
                self.workflow_path.read_bytes()
            ).hexdigest()
            self.scope_path.write_text(
                json.dumps(scope, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return prepare_workflow_execution(
                json.loads(json.dumps(workflow)),
                json.loads(json.dumps(scope)),
                json.loads(json.dumps(inventory)),
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

        with self.assertRaisesRegex(
            WorkflowPreparationError,
            "requires a fresh package-numbering author approval after recheck",
        ):
            prepare_current_scope()

        # A stale author approval left behind in an old Execution Scope must
        # be rejected even though the Workflow task's live approval is cleared.
        scope_task["author_decision_ref"] = old_approval
        with self.assertRaisesRegex(
            WorkflowPreparationError,
            "previously invalidated package-numbering approval",
        ):
            prepare_current_scope()

        # A newly approved impact plan produces a different hash-bound approval
        # and can proceed through the same refreshed preparation path.
        fresh_approval = make_approval("e" * 64)
        scope_task["author_decision_ref"] = fresh_approval
        prepared, _, report = prepare_current_scope()
        prepared_task = prepared["tasks"][0]
        self.assertEqual(prepared_task["author_decision_ref"], fresh_approval)
        self.assertEqual(
            prepared_task["patch_operations"][0]["author_decision_ref"],
            fresh_approval,
        )
        self.assertEqual(report["status"], "prepared_pending_author_approval")

    def test_workflow_execution_scope_schema_accepts_author_numbering_provenance(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "workflow-execution-scope.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        task_schema = schema["properties"]["tasks"]["items"]
        operation_schema = task_schema["properties"]["patch_operations"]["items"]
        self.assertIn("author_decision_ref", task_schema["properties"])
        self.assertIn("author_decision_ref", operation_schema["properties"])
        self.assertNotIn("source_comment_ids", operation_schema["required"])
        self.assertTrue(task_schema.get("allOf"))

    def test_recheck_task_refreshes_evidence_and_source_version_from_current_active_docx(self) -> None:
        current_manuscript = self.root / "Manuscript.after-T1.docx"
        current_manuscript.write_bytes(b"synthetic manuscript after T1")
        current_sha = hashlib.sha256(current_manuscript.read_bytes()).hexdigest()
        current_workflow = json.loads(json.dumps(self.workflow))
        current_workflow["active_documents"] = {
            "Reviewers": {
                "file": str(self.reviewers),
                "sha256": hashlib.sha256(self.reviewers.read_bytes()).hexdigest(),
                "batch_id": None,
            },
            "Manuscript": {
                "file": str(current_manuscript),
                "sha256": current_sha,
                "batch_id": "batch-t1-published",
            },
        }
        task = current_workflow["tasks"][0]
        task["status"] = "recheck_required"
        task["recheck_from"] = "ready"
        task["invalidation_reason"] = "An upstream batch changed the active Manuscript."
        self.workflow_path.write_text(
            json.dumps(current_workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        refreshed_scope = {
            "schema_version": "1.0",
            "identity_mode": "legacy_p_idx_explicit",
            "revision_round_id": "round-workflow-preparation",
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "tasks": [{
                "task_id": "T1",
                "patch_id": "P-T1-REFRESH",
                "proposed_response": "We have updated the sample identifier in the current Manuscript version.",
                "evidence": [{
                    "evidence_id": "E-MAN-02",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "source_excerpt": "The selected sample is ZB-02.",
                    "evidence_type": "paragraph",
                    "match_status": "confirmed",
                }],
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "replace",
                    "evidence_ids": ["E-MAN-02"],
                }],
                "patch_operations": [{
                    "operation_id": "T1-OP-MAN-REFRESH",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "find": "ZB-02",
                    "replace": "ZB-03",
                    "location": "The selected sample is ZB-02.",
                    "evidence_ids": ["E-MAN-02"],
                    "source_comment_ids": ["1"],
                }],
                "response_unit_specs": [{
                    "comment_id": "1",
                    "disposition": "revised",
                    "location_requirement": "required",
                    "resolution_reason": "The current active version is revised at the refreshed target.",
                }],
            }],
        }
        refreshed_inventory = [
            self.inventory[0],
            {
                "role": "Manuscript",
                "file": str(current_manuscript),
                "source_sha256": current_sha,
                "paragraphs": [{
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "text": "The selected sample is ZB-02.",
                }],
            },
        ]
        prepared, _, report = prepare_workflow_execution(
            current_workflow,
            refreshed_scope,
            refreshed_inventory,
            workflow_path=self.workflow_path,
            execution_scope_path=self.scope_path,
        )
        updated = prepared["tasks"][0]
        self.assertEqual(updated["status"], "recheck_required")
        self.assertIn({
            "role": "Manuscript",
            "file": str(current_manuscript.resolve()),
            "sha256": current_sha,
        }, updated["source_versions"])
        self.assertEqual(updated["targets"][0]["evidence_ids"], ["E-MAN-02"])
        self.assertEqual(
            [row["evidence_id"] for row in updated["evidence"]],
            ["E-COMMENT-1", "E-MAN-1", "E-MAN-02"],
        )
        self.assertEqual(updated["patch_operations"][0]["input_file"], str(current_manuscript.resolve()))
        self.assertEqual(updated["patch_operations"][0]["source_sha256"], current_sha)
        self.assertEqual(report["refreshed_task_ids"], ["T1"])
        self.assertEqual(report["active_document_versions"]["Manuscript"]["batch_id"], "batch-t1-published")

    def test_recheck_task_with_document_targets_requires_fresh_evidence(self) -> None:
        current_workflow = json.loads(json.dumps(self.workflow))
        current_workflow["active_documents"] = {
            "Reviewers": {
                "file": str(self.reviewers),
                "sha256": hashlib.sha256(self.reviewers.read_bytes()).hexdigest(),
                "batch_id": None,
            },
            "Manuscript": {
                "file": str(self.manuscript),
                "sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
                "batch_id": None,
            },
        }
        task = current_workflow["tasks"][0]
        task["status"] = "recheck_required"
        task["recheck_from"] = "ready"
        task["invalidation_reason"] = "An upstream batch changed the active Manuscript."
        self.workflow_path.write_text(
            json.dumps(current_workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stale_scope = {
            "schema_version": "1.0",
            "identity_mode": "legacy_p_idx_explicit",
            "revision_round_id": "round-workflow-preparation",
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "tasks": [{
                "task_id": "T1",
                "patch_id": "P-T1-STALE",
                "proposed_response": "A response from an outdated target.",
                "patch_operations": [],
            }],
        }
        with self.assertRaisesRegex(WorkflowPreparationError, "requires refreshed evidence/targets"):
            prepare_workflow_execution(
                current_workflow,
                stale_scope,
                self.inventory,
                workflow_path=self.workflow_path,
                execution_scope_path=self.scope_path,
            )

    def test_active_audit_requires_exact_published_workflow_paths_and_hashes(self) -> None:
        from argparse import Namespace

        args = Namespace(
            rev=str(self.reviewers),
            man=str(self.manuscript),
            si=None,
        )
        expected = cli._validate_active_workflow_audit_sources(args, self.workflow_path)
        self.assertEqual(
            expected,
            {
                "Reviewers": hashlib.sha256(self.reviewers.read_bytes()).hexdigest(),
                "Manuscript": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
            },
        )

        wrong_manuscript = self.root / "Manuscript.copy.docx"
        wrong_manuscript.write_bytes(self.manuscript.read_bytes())
        wrong_args = Namespace(
            rev=str(self.reviewers),
            man=str(wrong_manuscript),
            si=None,
        )
        with self.assertRaisesRegex(ValueError, "path for Manuscript does not match"):
            cli._validate_active_workflow_audit_sources(wrong_args, self.workflow_path)

        self.manuscript.write_bytes(b"changed after publication")
        with self.assertRaisesRegex(ValueError, "Active document for Manuscript changed outside the Workflow Ledger pointer"):
            cli._validate_active_workflow_audit_sources(args, self.workflow_path)

    def _prepare_rechecked_no_change_task(self, anchor_evidence_id: str):
        current_manuscript = self.root / "Manuscript.no-change-current.docx"
        current_manuscript.write_bytes(b"synthetic current manuscript")
        current_sha = hashlib.sha256(current_manuscript.read_bytes()).hexdigest()
        current_workflow = json.loads(json.dumps(self.workflow))
        current_workflow["active_documents"]["Manuscript"] = {
            "file": str(current_manuscript),
            "sha256": current_sha,
            "batch_id": "batch-upstream-published",
            "updated_at_utc": STAMP,
        }
        task = current_workflow["tasks"][0]
        task["status"] = "recheck_required"
        task["recheck_from"] = "ready"
        task["invalidation_reason"] = "An upstream batch changed the active Manuscript."
        task["targets"][0]["action"] = "keep"
        task["patch_operations"] = []
        task["response_unit_specs"] = [{
            "comment_id": "1",
            "disposition": "no_change",
            "location_requirement": "existing_anchor_required",
            "anchor_evidence_id": "E-MAN-1",
        }]
        self.workflow_path.write_text(
            json.dumps(current_workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        scope = {
            "schema_version": "1.0",
            "identity_mode": "legacy_p_idx_explicit",
            "revision_round_id": "round-workflow-preparation",
            "workflow_ledger_sha256": hashlib.sha256(self.workflow_path.read_bytes()).hexdigest(),
            "tasks": [{
                "task_id": "T1",
                "evidence": [{
                    "evidence_id": "E-MAN-02",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "source_excerpt": "The selected sample is ZB-02.",
                    "evidence_type": "paragraph",
                    "match_status": "confirmed",
                }],
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "keep",
                    "evidence_ids": ["E-MAN-02"],
                }],
                "patch_operations": [],
                "response_unit_specs": [{
                    "comment_id": "1",
                    "disposition": "no_change",
                    "location_requirement": "existing_anchor_required",
                    "anchor_evidence_id": anchor_evidence_id,
                }],
            }],
        }
        refreshed_inventory = [
            self.inventory[0],
            {
                "role": "Manuscript",
                "file": str(current_manuscript),
                "source_sha256": current_sha,
                "paragraphs": [{
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "text": "The selected sample is ZB-02.",
                }],
            },
        ]
        return prepare_workflow_execution(
            current_workflow,
            scope,
            refreshed_inventory,
            workflow_path=self.workflow_path,
            execution_scope_path=self.scope_path,
        )

    def test_recheck_no_change_accepts_only_current_version_anchor_evidence(self) -> None:
        prepared, _, report = self._prepare_rechecked_no_change_task("E-MAN-02")
        unit_spec = prepared["tasks"][0]["response_unit_specs"][0]
        self.assertEqual(prepared["tasks"][0]["status"], "recheck_required")
        self.assertEqual(unit_spec["anchor_evidence_id"], "E-MAN-02")
        self.assertEqual(report["refreshed_task_ids"], ["T1"])

    def test_recheck_no_change_rejects_stale_anchor_evidence(self) -> None:
        with self.assertRaisesRegex(WorkflowPreparationError, "newly refreshed anchor_evidence_id"):
            self._prepare_rechecked_no_change_task("E-MAN-1")

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
        with self.assertRaisesRegex(WorkflowPreparationError, "find text must be present"):
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

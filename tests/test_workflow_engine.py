import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.identity import (
    TargetRef,
    build_document_identity,
    build_object_identity,
)
from co_reviewer.workflow_engine import (
    WorkflowValidationError,
    build_workflow_ledger,
    build_workflow_schedule,
    invalidate_downstream,
    select_ready_tasks,
    transition_task,
    validate_workflow_ledger,
    validate_workflow_tasks,
    workflow_scope_fingerprint,
    workflow_tasks_from_decision_plan,
)

INPUT_HASH = "a" * 64
OUTPUT_HASH = "b" * 64
COMPLETION_HASH = "c" * 64
STAMP = "2026-11-14T12:00:00+08:00"


def _task(task_id, dependencies=None, status="ready", groups=None, sequence=1):
    return {
        "task_id": task_id,
        "sequence": sequence,
        "stage": "revision",
        "task_type": "local_revision",
        "dependencies": dependencies or [],
        "target_groups": groups or [],
        "status": status,
        "title": f"Task {task_id}",
        "proposed_action": f"Revise {task_id}",
        "requires_user_approval": True,
    }


def _preflighted(task):
    return transition_task(
        task,
        "preflighted",
        evidence={
            "input_fingerprint": INPUT_HASH,
            "preflight_report_id": "preflight-001",
            "preflighted_at_utc": STAMP,
        },
    )


def _approved(task):
    return transition_task(
        _preflighted(task),
        "approved",
        evidence={
            "human_approval_confirmed": True,
            "approved_by": "Human Author",
            "approved_at_utc": STAMP,
        },
    )


def _applied(task):
    return transition_task(
        _approved(task),
        "applied",
        evidence={
            "output_fingerprint": OUTPUT_HASH,
            "execution_report_id": "apply-001",
            "applied_at_utc": STAMP,
        },
    )


def _verified(task):
    return transition_task(
        _applied(task),
        "verified",
        evidence={
            "semantic_review_confirmed": True,
            "verified_by": "Human Author",
            "verified_at_utc": STAMP,
            "verification_report_id": "verify-001",
        },
    )


def _completed(task):
    return transition_task(
        _verified(task),
        "completed",
        evidence={
            "completion_record_id": "completion-001",
            "completion_record_fingerprint": COMPLETION_HASH,
            "completed_at_utc": STAMP,
        },
    )


class WorkflowEngineTests(unittest.TestCase):
    def test_task_dag_returns_stable_dependency_order(self):
        tasks = [
            _task("C", ["A"], "planned", ["doc-2"], 3),
            _task("B", [], "planned", ["doc-1"], 2),
            _task("A", [], "planned", ["doc-1"], 1),
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.task_order, ("A", "B", "C"))
        self.assertEqual(schedule.target_group_edges, (("doc-1", "doc-2"),))

    def test_unknown_dependency_and_cycle_are_rejected(self):
        unknown = [_task("A", ["MISSING"], "planned")]
        self.assertTrue(any(
            "unknown workflow task" in error
            for error in validate_workflow_tasks(unknown)
        ))

        cyclic = [
            _task("A", ["B"], "planned"),
            _task("B", ["A"], "planned"),
        ]
        self.assertTrue(any(
            "Dependency cycle detected" in error
            for error in validate_workflow_tasks(cyclic)
        ))
        with self.assertRaises(WorkflowValidationError):
            build_workflow_schedule(cyclic)

    def test_target_group_quotient_cycle_is_condensed_not_misreported_as_task_cycle(self):
        tasks = [
            _task("A1", ["B1"], "planned", ["A"]),
            _task("A2", [], "planned", ["A"]),
            _task("B1", ["A2"], "planned", ["B"]),
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.task_order, ("A2", "B1", "A1"))
        self.assertEqual(schedule.target_group_components, (("A", "B"),))
        self.assertEqual(schedule.target_group_edges, (("A", "B"), ("B", "A")))

    def test_response_only_decision_task_has_no_document_target_group(self):
        adapted = workflow_tasks_from_decision_plan([{
            "task_id": "R1-RESPONSE",
            "sequence": 1,
            "task_type": "response_only",
            "title": "Draft response",
            "status": "pending",
            "dependencies": [],
            "targets": [{
                "action": "reply_only",
                "document": "Reviewers",
                "part": "word/document.xml",
                "p_idx": 0,
            }],
        }])
        self.assertEqual(adapted[0]["status"], "planned")
        self.assertEqual(adapted[0]["task_type"], "response_only")
        self.assertEqual(adapted[0]["target_groups"], [])
        self.assertEqual(
            build_workflow_schedule(adapted).target_group_components, ()
        )

    def test_ready_tasks_wait_for_verified_dependencies(self):
        task_a = _applied(_task("A"))
        task_b = _task("B", ["A"], "ready")
        task_c = _task("C", [], "ready")
        tasks = [task_a, task_b, task_c]

        self.assertEqual(select_ready_tasks(tasks), ["C"])
        tasks[0] = _verified(_task("A"))
        self.assertEqual(select_ready_tasks(tasks), ["B", "C"])

    def test_ready_batch_never_selects_two_tasks_on_the_same_target_group(self):
        tasks = [
            _task("A", [], "ready", ["shared"], 1),
            _task("B", [], "ready", ["shared"], 2),
            _task("C", [], "ready", ["independent"], 3),
            _task("D", [], "ready", [], 4),
        ]
        self.assertEqual(select_ready_tasks(tasks), ["A", "C", "D"])

    def test_lifecycle_requires_preflight_human_approval_and_semantic_review(self):
        task = _task("T1")
        with self.assertRaisesRegex(WorkflowValidationError, "ready -> applied"):
            transition_task(task, "applied")
        with self.assertRaisesRegex(WorkflowValidationError, "input_fingerprint"):
            transition_task(task, "preflighted")

        preflighted = _preflighted(task)
        with self.assertRaisesRegex(WorkflowValidationError, "human_approval_confirmed"):
            transition_task(
                preflighted,
                "approved",
                evidence={
                    "human_approval_confirmed": False,
                    "approved_by": "Human Author",
                    "approved_at_utc": STAMP,
                },
            )

        completed = _completed(task)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(validate_workflow_tasks([completed]), [])
        self.assertEqual(task["status"], "ready")

    def test_scope_change_invalidates_preflight_and_approval_fingerprints(self):
        approved = _approved(_task("T1"))
        tampered = dict(approved)
        tampered["proposed_action"] = "A changed action that was not re-approved."
        errors = validate_workflow_tasks([tampered])
        self.assertTrue(any("preflight_fingerprint is stale" in error for error in errors))
        self.assertTrue(any("approval_fingerprint is stale" in error for error in errors))

    def test_verification_requires_explicit_semantic_confirmation(self):
        applied = _applied(_task("T1"))
        with self.assertRaisesRegex(WorkflowValidationError, "semantic_review_confirmed"):
            transition_task(
                applied,
                "verified",
                evidence={
                    "semantic_review_confirmed": False,
                    "verified_by": "Human Author",
                    "verified_at_utc": STAMP,
                    "verification_report_id": "verify-001",
                },
            )

    def test_block_and_invalidation_transitions_require_reasons(self):
        with self.assertRaisesRegex(WorkflowValidationError, "reason is required"):
            transition_task(_task("T1", status="planned"), "blocked")
        with self.assertRaisesRegex(WorkflowValidationError, "reason is required"):
            transition_task(_verified(_task("T2")), "recheck_required")

    def test_upstream_change_invalidates_all_transitive_dependents(self):
        tasks = [
            _completed(_task("A")),
            _verified(_task("B", ["A"])),
            _approved(_task("C", ["B"])),
            _task("D", [], "ready"),
        ]
        updated = invalidate_downstream(tasks, "A", "Upstream target changed.")
        states = {task["task_id"]: task["status"] for task in updated}
        self.assertEqual(states, {
            "A": "completed",
            "B": "recheck_required",
            "C": "recheck_required",
            "D": "ready",
        })
        self.assertEqual(
            {
                task["task_id"]: task.get("recheck_from")
                for task in updated if task["task_id"] in {"B", "C"}
            },
            {"B": "verified", "C": "approved"},
        )
        self.assertEqual(tasks[1]["status"], "verified")

    def test_malformed_status_and_action_values_do_not_crash_adapter(self):
        malformed = [{
            "task_id": "T1",
            "dependencies": [],
            "target_groups": [],
            "status": {"unexpected": "mapping"},
        }]
        self.assertTrue(any(
            "unsupported workflow status" in error
            for error in validate_workflow_tasks(malformed)
        ))
        adapted = workflow_tasks_from_decision_plan([{
            "task_id": "T2",
            "status": "pending",
            "dependencies": [],
            "targets": [{
                "action": {"bad": "type"},
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
            }],
        }])
        self.assertEqual(adapted[0]["target_groups"], [])

    def test_workflow_ledger_schema_requires_state_evidence(self):
        schema_path = Path(__file__).resolve().parents[1] / "schemas" / "workflow-ledger.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        task_schema = schema["$defs"]["workflowTask"]
        self.assertIn("response_only", task_schema["properties"]["task_type"]["enum"])
        self.assertTrue(any(
            condition.get("if", {}).get("properties", {}).get("status", {}).get("const") == "approved"
            for condition in task_schema["allOf"]
        ))
        self.assertTrue(any(
            "verification_fingerprint" in condition.get("then", {}).get("required", [])
            for condition in task_schema["allOf"]
        ))

    def test_persistent_workflow_ledger_builds_and_validates(self):
        decision_plan = {
            "source_files": [{
                "role": "Manuscript",
                "file": "Manuscript.docx",
                "sha256": INPUT_HASH,
            }],
            "tasks": [{
                "task_id": "T1",
                "sequence": 1,
                "task_type": "response_only",
                "title": "Prepare response",
                "source_comment_ids": ["C1"],
                "target_documents": [],
                "evidence": [{
                    "evidence_id": "E1",
                    "document": "Reviewers",
                    "part": "word/comments.xml",
                    "p_idx": 0,
                    "source_excerpt": "Please clarify.",
                    "evidence_type": "reviewer_comment",
                    "match_status": "confirmed",
                    "source_comment_id": "C1",
                }],
                "proposed_action": "Draft a response using the cited comment.",
                "targets": [],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.9,
                "status": "pending",
                "requires_user_approval": True,
            }],
        }
        ledger = build_workflow_ledger(
            decision_plan, "round-2026-11-14-001", STAMP
        )
        self.assertEqual(ledger["schema_version"], "0.5.1")
        self.assertEqual(ledger["tasks"][0]["status"], "planned")
        self.assertEqual(ledger["tasks"][0]["task_type"], "response_only")
        self.assertEqual(ledger["tasks"][0]["target_groups"], [])
        self.assertEqual(validate_workflow_ledger(ledger), [])

    def test_ledger_persists_stable_targetrefs_and_schedules_by_object_id(self):
        document = build_document_identity(
            role="Manuscript",
            source_sha256=INPUT_HASH,
            visible_text="The sample was heated.",
            structure={"paragraphs": 1},
            formatting={"style": "Normal"},
            citation_state={},
            document_id="doc-stable",
        )
        object_record = build_object_identity(
            document=document,
            object_id="para-stable-01",
            object_type="body_paragraph",
            part="word/document.xml",
            object_payload={"text": "The sample was heated."},
            location_hint={"p_idx": 4},
        )
        target_ref = TargetRef.from_object(object_record)
        plan = {
            "source_files": [{
                "role": "Manuscript",
                "file": "Manuscript.docx",
                "sha256": INPUT_HASH,
            }],
            "tasks": [{
                "task_id": "T1",
                "sequence": 1,
                "title": "Revise the sample condition",
                "task_type": "local_revision",
                "source_comment_ids": ["C1"],
                "target_documents": ["Manuscript"],
                "evidence": [{
                    "evidence_id": "E1",
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 4,
                    "source_excerpt": "The sample was heated.",
                    "evidence_type": "paragraph",
                    "match_status": "confirmed",
                }],
                "proposed_action": "Clarify the sample heating condition.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 4,
                    "action": "replace",
                    "evidence_ids": ["E1"],
                }],
                "dependencies": [],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 0.95,
                "status": "pending",
                "requires_user_approval": True,
            }],
        }
        ledger = build_workflow_ledger(
            plan,
            "round-targetref-001",
            STAMP,
            document_identities=[document],
            object_registry=[object_record],
            target_refs_by_task={"T1": [target_ref]},
        )
        task = ledger["tasks"][0]
        self.assertEqual(task["target_refs"][0]["object_id"], "para-stable-01")
        self.assertEqual(
            task["target_groups"],
            ["doc-stable|object=para-stable-01"],
        )
        self.assertEqual(validate_workflow_ledger(ledger), [])

    def test_ledger_rejects_targetref_not_registered_to_document_revision(self):
        document = build_document_identity(
            role="Manuscript",
            source_sha256=INPUT_HASH,
            visible_text="Text.",
            structure={},
            formatting={},
            citation_state={},
            document_id="doc-stable",
        )
        object_record = build_object_identity(
            document=document,
            object_id="para-01",
            object_type="body_paragraph",
            part="word/document.xml",
            object_payload={"text": "Text."},
        )
        wrong_ref = dict(TargetRef.from_object(object_record).to_dict())
        wrong_ref["object_fingerprint"] = OUTPUT_HASH
        ledger = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-bad-targetref",
            "created_at_utc": STAMP,
            "source_files": [],
            "documents": [document.to_dict()],
            "object_registry": [object_record.to_dict()],
            "tasks": [{
                **_task("T1", status="planned", groups=["doc-stable|object=para-01"]),
                "task_type": "local_revision",
                "target_refs": [wrong_ref],
            }],
            "events": [],
        }
        errors = validate_workflow_ledger(ledger)
        self.assertTrue(any("does not match the registered object fingerprint" in error for error in errors))

    def test_workflow_ledger_rejects_ready_without_author_event_or_valid_scope_fingerprint(self):
        task = {
            "task_id": "T1",
            "sequence": 1,
            "stage": "revision",
            "task_type": "local_revision",
            "title": "Revise section",
            "dependencies": [],
            "target_groups": ["Manuscript|word/document.xml|p_idx=0"],
            "targets": [],
            "patch_operations": [],
            "status": "ready",
        }
        ledger = {
            "schema_version": "0.5.1",
            "revision_round_id": "round-ready-audit",
            "documents": [],
            "object_registry": [],
            "source_files": [],
            "tasks": [task],
            "events": [],
        }
        self.assertTrue(any(
            "without an explicit author-authorization event" in error
            for error in validate_workflow_ledger(ledger)
        ))
        ledger["events"] = [{
            "event_id": "EV-READY-1",
            "task_id": "T1",
            "timestamp_utc": STAMP,
            "event_type": "status_transition",
            "from_status": "planned",
            "to_status": "ready",
            "actor": "Human Author",
            "fingerprint": "0" * 64,
        }]
        self.assertTrue(any(
            "fingerprint does not match" in error
            for error in validate_workflow_ledger(ledger)
        ))
        ledger["events"][0]["fingerprint"] = workflow_scope_fingerprint(task)
        self.assertEqual(validate_workflow_ledger(ledger), [])

    def test_same_target_group_does_not_create_a_self_edge(self):
        tasks = [
            _task("A", [], "planned", ["shared"]),
            _task("B", ["A"], "planned", ["shared"]),
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.target_group_edges, ())
        self.assertEqual(schedule.target_group_components, (("shared",),))


if __name__ == "__main__":
    unittest.main()

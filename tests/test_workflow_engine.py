import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.workflow_engine import (
    WorkflowValidationError,
    build_workflow_schedule,
    invalidate_downstream,
    select_ready_tasks,
    transition_task,
    validate_workflow_tasks,
    workflow_tasks_from_decision_plan,
)


class WorkflowEngineTests(unittest.TestCase):
    def test_task_dag_returns_stable_dependency_order(self):
        tasks = [
            {"task_id": "C", "sequence": 3, "dependencies": ["A"], "target_groups": ["doc-2"], "status": "planned"},
            {"task_id": "B", "sequence": 2, "dependencies": [], "target_groups": ["doc-1"], "status": "planned"},
            {"task_id": "A", "sequence": 1, "dependencies": [], "target_groups": ["doc-1"], "status": "planned"},
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.task_order, ("A", "B", "C"))
        self.assertEqual(schedule.target_group_edges, (("doc-1", "doc-2"),))

    def test_unknown_dependency_and_cycle_are_rejected(self):
        unknown = [{"task_id": "A", "dependencies": ["MISSING"], "target_groups": [], "status": "planned"}]
        self.assertTrue(any("unknown workflow task" in error for error in validate_workflow_tasks(unknown)))

        cyclic = [
            {"task_id": "A", "dependencies": ["B"], "target_groups": [], "status": "planned"},
            {"task_id": "B", "dependencies": ["A"], "target_groups": [], "status": "planned"},
        ]
        self.assertTrue(any("Dependency cycle detected" in error for error in validate_workflow_tasks(cyclic)))
        with self.assertRaises(WorkflowValidationError):
            build_workflow_schedule(cyclic)

    def test_target_group_quotient_cycle_is_condensed_not_misreported_as_task_cycle(self):
        tasks = [
            {"task_id": "A1", "dependencies": ["B1"], "target_groups": ["A"], "status": "planned"},
            {"task_id": "A2", "dependencies": [], "target_groups": ["A"], "status": "planned"},
            {"task_id": "B1", "dependencies": ["A2"], "target_groups": ["B"], "status": "planned"},
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.task_order, ("A2", "B1", "A1"))
        self.assertEqual(schedule.target_group_components, (("A", "B"),))
        self.assertEqual(
            schedule.target_group_edges,
            (("A", "B"), ("B", "A")),
        )

    def test_response_only_decision_task_has_no_document_target_group(self):
        adapted = workflow_tasks_from_decision_plan([{
            "task_id": "R1-RESPONSE",
            "sequence": 1,
            "task_type": "response_only",
            "status": "pending",
            "dependencies": [],
            "targets": [{"action": "reply_only", "document": "Reviewers", "part": "word/document.xml", "p_idx": 0}],
        }])
        self.assertEqual(adapted[0]["status"], "planned")
        self.assertEqual(adapted[0]["target_groups"], [])
        self.assertEqual(build_workflow_schedule(adapted).target_group_components, ())

    def test_ready_tasks_wait_for_verified_dependencies(self):
        tasks = [
            {"task_id": "A", "dependencies": [], "target_groups": [], "status": "applied"},
            {"task_id": "B", "dependencies": ["A"], "target_groups": [], "status": "ready"},
            {"task_id": "C", "dependencies": [], "target_groups": [], "status": "ready"},
        ]
        self.assertEqual(select_ready_tasks(tasks), ["C"])
        tasks[0]["status"] = "verified"
        self.assertEqual(select_ready_tasks(tasks), ["B", "C"])

    def test_lifecycle_rejects_skipping_approval_and_verification_gates(self):
        task = {"task_id": "T1", "status": "ready"}
        with self.assertRaisesRegex(WorkflowValidationError, "ready -> applied"):
            transition_task(task, "applied")
        preflighted = transition_task(task, "preflighted")
        approved = transition_task(preflighted, "approved")
        applied = transition_task(approved, "applied")
        verified = transition_task(applied, "verified")
        completed = transition_task(verified, "completed")
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(task["status"], "ready")

    def test_block_and_invalidation_transitions_require_reasons(self):
        with self.assertRaisesRegex(WorkflowValidationError, "reason is required"):
            transition_task({"status": "planned"}, "blocked")
        with self.assertRaisesRegex(WorkflowValidationError, "reason is required"):
            transition_task({"status": "verified"}, "recheck_required")

    def test_upstream_change_invalidates_all_transitive_dependents(self):
        tasks = [
            {"task_id": "A", "dependencies": [], "target_groups": [], "status": "completed"},
            {"task_id": "B", "dependencies": ["A"], "target_groups": [], "status": "verified"},
            {"task_id": "C", "dependencies": ["B"], "target_groups": [], "status": "approved"},
            {"task_id": "D", "dependencies": [], "target_groups": [], "status": "ready"},
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
            {task["task_id"]: task.get("recheck_from") for task in updated if task["task_id"] in {"B", "C"}},
            {"B": "verified", "C": "approved"},
        )
        self.assertEqual(tasks[1]["status"], "verified")

    def test_same_target_group_does_not_create_a_self_edge(self):
        tasks = [
            {"task_id": "A", "dependencies": [], "target_groups": ["shared"], "status": "planned"},
            {"task_id": "B", "dependencies": ["A"], "target_groups": ["shared"], "status": "planned"},
        ]
        schedule = build_workflow_schedule(tasks)
        self.assertEqual(schedule.target_group_edges, ())
        self.assertEqual(schedule.target_group_components, (("shared",),))


if __name__ == "__main__":
    unittest.main()

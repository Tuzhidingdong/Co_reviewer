# -*- coding: utf-8 -*-
"""Deterministic workflow state transitions and dependency-aware scheduling.

This is the v0.5.1 workflow core. It is intentionally independent from the
legacy DOCX CLI: decision-plan adapters can feed it without making document
editing, human approval, or Word pagination implicit side effects.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

WORKFLOW_STATUSES = frozenset({
    "identified",
    "planned",
    "blocked",
    "ready",
    "preflighted",
    "approved",
    "applied",
    "verified",
    "completed",
    "recheck_required",
})

ALLOWED_TRANSITIONS = {
    "identified": frozenset({"planned", "blocked"}),
    "planned": frozenset({"ready", "blocked", "recheck_required"}),
    "blocked": frozenset({"planned", "ready", "recheck_required"}),
    "ready": frozenset({"preflighted", "blocked", "recheck_required"}),
    "preflighted": frozenset({"approved", "ready", "blocked", "recheck_required"}),
    "approved": frozenset({"applied", "recheck_required"}),
    "applied": frozenset({"verified", "recheck_required"}),
    "verified": frozenset({"completed", "recheck_required"}),
    "completed": frozenset({"recheck_required"}),
    "recheck_required": frozenset({"planned", "ready", "blocked"}),
}

_MUTATING_TARGET_ACTIONS = frozenset({
    "replace", "add", "remove", "renumber", "reference_update",
})


class WorkflowValidationError(ValueError):
    """Raised when a workflow cannot be scheduled without guessing."""


@dataclass(frozen=True)
class WorkflowSchedule:
    """Stable task order plus the condensed target-group quotient DAG."""

    task_order: tuple[str, ...]
    target_group_components: tuple[tuple[str, ...], ...]
    target_group_edges: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_order": list(self.task_order),
            "target_group_components": [list(row) for row in self.target_group_components],
            "target_group_edges": [list(edge) for edge in self.target_group_edges],
        }



_SCOPE_FINGERPRINT_FIELDS = (
    "task_id", "sequence", "stage", "task_type", "title",
    "source_comment_ids", "target_documents", "evidence", "proposed_action",
    "targets", "dependencies", "target_groups", "readiness",
    "unresolved_questions", "confidence", "requires_user_approval",
    "response_unit_ids",
)
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _digest(payload: Mapping[str, Any]) -> str:
    try:
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise WorkflowValidationError(
            f"Workflow fingerprint input is not JSON-serializable: {exc}"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _is_aware_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def workflow_scope_fingerprint(task: Mapping[str, Any]) -> str:
    """Fingerprint every author-facing and target-defining part of one task."""
    payload = {
        field: task[field]
        for field in _SCOPE_FINGERPRINT_FIELDS
        if field in task
    }
    return _digest(payload)


def workflow_preflight_fingerprint(
    task: Mapping[str, Any], input_fingerprint: str
) -> str:
    if not _is_sha256(input_fingerprint):
        raise WorkflowValidationError("input_fingerprint must be a lowercase SHA-256 digest.")
    return _digest({
        "scope_fingerprint": workflow_scope_fingerprint(task),
        "input_fingerprint": input_fingerprint,
    })


def workflow_approval_fingerprint(
    task: Mapping[str, Any], preflight_fingerprint: str
) -> str:
    if not _is_sha256(preflight_fingerprint):
        raise WorkflowValidationError("preflight_fingerprint must be a lowercase SHA-256 digest.")
    return _digest({
        "scope_fingerprint": workflow_scope_fingerprint(task),
        "preflight_fingerprint": preflight_fingerprint,
    })


def workflow_verification_fingerprint(task: Mapping[str, Any]) -> str:
    return _digest({
        "approval_fingerprint": task.get("approval_fingerprint"),
        "output_fingerprint": task.get("output_fingerprint"),
        "verification_report_id": task.get("verification_report_id"),
        "semantic_review_confirmed": task.get("semantic_review_confirmed"),
    })


def workflow_completion_fingerprint(task: Mapping[str, Any]) -> str:
    return _digest({
        "verification_fingerprint": task.get("verification_fingerprint"),
        "completion_record_id": task.get("completion_record_id"),
        "completion_record_fingerprint": task.get("completion_record_fingerprint"),
    })


def _state_evidence_errors(
    task_id: str, task: Mapping[str, Any], status: str
) -> list[str]:
    errors: list[str] = []

    if status in {"blocked"} and not (
        isinstance(task.get("block_reason"), str) and task["block_reason"].strip()
    ):
        errors.append(f"Task {task_id} in blocked state requires block_reason.")
    if status == "recheck_required":
        recheck_from = task.get("recheck_from")
        if (
            not isinstance(recheck_from, str)
            or recheck_from not in WORKFLOW_STATUSES - {"recheck_required"}
        ):
            errors.append(f"Task {task_id} in recheck_required state needs a valid recheck_from.")
        if not (
            isinstance(task.get("invalidation_reason"), str)
            and task["invalidation_reason"].strip()
        ):
            errors.append(f"Task {task_id} in recheck_required state requires invalidation_reason.")

    preflight_states = {"preflighted", "approved", "applied", "verified", "completed"}
    if status in preflight_states:
        if not _is_sha256(task.get("input_fingerprint")):
            errors.append(f"Task {task_id} requires input_fingerprint before preflighted state.")
        if not _is_sha256(task.get("preflight_fingerprint")):
            errors.append(f"Task {task_id} requires preflight_fingerprint.")
        elif _is_sha256(task.get("input_fingerprint")):
            try:
                expected = workflow_preflight_fingerprint(
                    task, task["input_fingerprint"]
                )
                if task["preflight_fingerprint"] != expected:
                    errors.append(f"Task {task_id} preflight_fingerprint is stale for its current scope/input.")
            except WorkflowValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")
        if not isinstance(task.get("preflight_report_id"), str) or not task["preflight_report_id"].strip():
            errors.append(f"Task {task_id} requires preflight_report_id.")
        if not _is_aware_timestamp(task.get("preflighted_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware preflighted_at_utc.")

    approval_states = {"approved", "applied", "verified", "completed"}
    if status in approval_states:
        if task.get("human_approval_confirmed") is not True:
            errors.append(f"Task {task_id} requires explicit human_approval_confirmed=true.")
        if not isinstance(task.get("approved_by"), str) or not task["approved_by"].strip():
            errors.append(f"Task {task_id} requires approved_by.")
        if not _is_aware_timestamp(task.get("approved_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware approved_at_utc.")
        if not _is_sha256(task.get("approval_fingerprint")):
            errors.append(f"Task {task_id} requires approval_fingerprint.")
        elif _is_sha256(task.get("preflight_fingerprint")):
            try:
                expected = workflow_approval_fingerprint(
                    task, task["preflight_fingerprint"]
                )
                if task["approval_fingerprint"] != expected:
                    errors.append(f"Task {task_id} approval_fingerprint is stale for its current scope/preflight.")
            except WorkflowValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")

    applied_states = {"applied", "verified", "completed"}
    if status in applied_states:
        if not _is_sha256(task.get("output_fingerprint")):
            errors.append(f"Task {task_id} requires output_fingerprint.")
        if not isinstance(task.get("execution_report_id"), str) or not task["execution_report_id"].strip():
            errors.append(f"Task {task_id} requires execution_report_id.")
        if not _is_aware_timestamp(task.get("applied_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware applied_at_utc.")

    verified_states = {"verified", "completed"}
    if status in verified_states:
        if task.get("semantic_review_confirmed") is not True:
            errors.append(f"Task {task_id} requires semantic_review_confirmed=true.")
        if not isinstance(task.get("verified_by"), str) or not task["verified_by"].strip():
            errors.append(f"Task {task_id} requires verified_by.")
        if not _is_aware_timestamp(task.get("verified_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware verified_at_utc.")
        if not isinstance(task.get("verification_report_id"), str) or not task["verification_report_id"].strip():
            errors.append(f"Task {task_id} requires verification_report_id.")
        if not _is_sha256(task.get("verification_fingerprint")):
            errors.append(f"Task {task_id} requires verification_fingerprint.")
        else:
            try:
                expected = workflow_verification_fingerprint(task)
                if task["verification_fingerprint"] != expected:
                    errors.append(f"Task {task_id} verification_fingerprint is stale.")
            except WorkflowValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")

    if status == "completed":
        if not isinstance(task.get("completion_record_id"), str) or not task["completion_record_id"].strip():
            errors.append(f"Task {task_id} requires completion_record_id.")
        if not _is_sha256(task.get("completion_record_fingerprint")):
            errors.append(f"Task {task_id} requires completion_record_fingerprint.")
        if not _is_aware_timestamp(task.get("completed_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware completed_at_utc.")
        if not _is_sha256(task.get("completion_fingerprint")):
            errors.append(f"Task {task_id} requires completion_fingerprint.")
        else:
            try:
                expected = workflow_completion_fingerprint(task)
                if task["completion_fingerprint"] != expected:
                    errors.append(f"Task {task_id} completion_fingerprint is stale.")
            except WorkflowValidationError as exc:
                errors.append(f"Task {task_id}: {exc}")

    return errors


def _clear_execution_evidence(task: dict[str, Any]) -> None:
    for field in (
        "input_fingerprint", "preflight_fingerprint", "preflight_report_id",
        "preflighted_at_utc", "human_approval_confirmed", "approved_by",
        "approved_at_utc", "approval_fingerprint", "output_fingerprint",
        "execution_report_id", "applied_at_utc", "semantic_review_confirmed",
        "verified_by", "verified_at_utc", "verification_report_id",
        "verification_fingerprint", "completion_record_id",
        "completion_record_fingerprint", "completed_at_utc",
        "completion_fingerprint",
    ):
        task.pop(field, None)


def _string_list(value: Any, field: str, task_id: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    if value is None:
        return [], errors
    if not isinstance(value, (list, tuple)):
        return [], [f"Task {task_id} {field} must be an array."]
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"Task {task_id} {field}[{index}] must be a non-empty string.")
            continue
        values.append(item.strip())
    if len(values) != len(set(values)):
        errors.append(f"Task {task_id} {field} contains duplicates.")
    return values, errors


def validate_workflow_tasks(tasks: Any) -> list[str]:
    """Validate task identity, lifecycle status and the task-level dependency DAG."""
    if not isinstance(tasks, (list, tuple)):
        return ["Workflow tasks must be an array."]

    errors: list[str] = []
    rows: dict[str, Mapping[str, Any]] = {}
    dependencies: dict[str, list[str]] = {}

    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            errors.append(f"Workflow tasks[{index}] must be an object.")
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            errors.append(f"Workflow tasks[{index}].task_id must be a non-empty string.")
            continue
        task_id = task_id.strip()
        if task_id in rows:
            errors.append(f"Duplicate workflow task_id: {task_id}.")
            continue
        rows[task_id] = task

        deps, dep_errors = _string_list(task.get("dependencies", []), "dependencies", task_id)
        groups, group_errors = _string_list(task.get("target_groups", []), "target_groups", task_id)
        errors.extend(dep_errors)
        errors.extend(group_errors)
        dependencies[task_id] = deps

        status = task.get("status", "identified")
        if not isinstance(status, str) or status not in WORKFLOW_STATUSES:
            errors.append(
                f"Task {task_id} has unsupported workflow status {status!r}."
            )
        sequence = task.get("sequence")
        if sequence is not None and (
            not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
        ):
            errors.append(f"Task {task_id} sequence must be a non-negative integer.")
        if not isinstance(task.get("stage", "revision"), str):
            errors.append(f"Task {task_id} stage must be a string.")
        if isinstance(status, str) and status in WORKFLOW_STATUSES:
            errors.extend(_state_evidence_errors(task_id, task, status))

    for task_id, deps in dependencies.items():
        for dependency in deps:
            if dependency == task_id:
                errors.append(f"Task {task_id} cannot depend on itself.")
            elif dependency not in rows:
                errors.append(
                    f"Task {task_id} depends on unknown workflow task {dependency}."
                )

    # A colour-marked DFS reports an actionable cycle path, not just a boolean.
    colour: dict[str, int] = {}
    stack: list[str] = []
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(task_id: str) -> None:
        colour[task_id] = 1
        stack.append(task_id)
        for dependency in dependencies.get(task_id, []):
            if dependency not in rows:
                continue
            state = colour.get(dependency, 0)
            if state == 0:
                visit(dependency)
            elif state == 1:
                start = stack.index(dependency)
                cycle = tuple(stack[start:] + [dependency])
                canonical = min(
                    tuple(cycle[index:-1] + cycle[:index] + (cycle[index],))
                    for index in range(len(cycle) - 1)
                )
                if canonical not in reported_cycles:
                    reported_cycles.add(canonical)
                    errors.append(
                        "Dependency cycle detected: " + " -> ".join(cycle) + "."
                    )
        stack.pop()
        colour[task_id] = 2

    for task_id in rows:
        if colour.get(task_id, 0) == 0:
            visit(task_id)

    return list(dict.fromkeys(errors))


def _task_topological_order(tasks: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    rows = {str(task["task_id"]).strip(): task for task in tasks}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {task_id: 0 for task_id in rows}
    priority: dict[str, tuple[int, str]] = {}

    for task_id, task in rows.items():
        sequence = task.get("sequence")
        priority[task_id] = (
            sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0,
            task_id,
        )
        for dependency in task.get("dependencies", []):
            outgoing[dependency].append(task_id)
            indegree[task_id] += 1

    ready = [
        (priority[task_id][0], priority[task_id][1], task_id)
        for task_id, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(ready)
    order: list[str] = []

    while ready:
        _, _, task_id = heapq.heappop(ready)
        order.append(task_id)
        for dependent in sorted(outgoing.get(task_id, [])):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(
                    ready,
                    (priority[dependent][0], priority[dependent][1], dependent),
                )

    return tuple(order)


def _strongly_connected_components(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str]],
) -> list[tuple[str, ...]]:
    """Tarjan SCCs; cycles in the quotient are collapsed into one target lock group."""
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency[source].append(target)
    for targets in adjacency.values():
        targets.sort()

    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def connect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlink[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in adjacency[node]:
            if neighbor not in indices:
                connect(neighbor)
                lowlink[node] = min(lowlink[node], lowlink[neighbor])
            elif neighbor in on_stack:
                lowlink[node] = min(lowlink[node], indices[neighbor])

        if lowlink[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(tuple(sorted(component)))

    for node in sorted(nodes):
        if node not in indices:
            connect(node)
    return components


def _condensed_group_order(
    components: Sequence[tuple[str, ...]],
    edges: Sequence[tuple[str, str]],
) -> tuple[tuple[str, ...], ...]:
    component_of = {
        group_id: index
        for index, component in enumerate(components)
        for group_id in component
    }
    outgoing: dict[int, set[int]] = defaultdict(set)
    indegree = {index: 0 for index in range(len(components))}
    for source, target in edges:
        left, right = component_of[source], component_of[target]
        if left != right and right not in outgoing[left]:
            outgoing[left].add(right)
            indegree[right] += 1

    ready: list[tuple[tuple[str, ...], int]] = [
        (components[index], index)
        for index, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(ready)
    ordered: list[tuple[str, ...]] = []
    while ready:
        component, index = heapq.heappop(ready)
        ordered.append(component)
        for neighbor in sorted(outgoing.get(index, set()), key=lambda value: components[value]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                heapq.heappush(ready, (components[neighbor], neighbor))
    return tuple(ordered)


def build_workflow_schedule(tasks: Any) -> WorkflowSchedule:
    """Build both the task DAG order and the condensed Target Group Quotient DAG.

    A cycle in the task DAG is invalid. A cycle created only after mapping tasks
    onto shared target groups is condensed into one target lock/review component;
    the original task-level order remains authoritative inside that component.
    """
    errors = validate_workflow_tasks(tasks)
    if errors:
        raise WorkflowValidationError(
            "Workflow validation failed:\n- " + "\n- ".join(errors)
        )

    rows = {str(task["task_id"]).strip(): task for task in tasks}
    task_order = _task_topological_order(tasks)
    groups_by_task: dict[str, list[str]] = {}
    all_groups: set[str] = set()
    for task_id, task in rows.items():
        groups, _ = _string_list(task.get("target_groups", []), "target_groups", task_id)
        groups_by_task[task_id] = groups
        all_groups.update(groups)

    quotient_edges: set[tuple[str, str]] = set()
    for task_id, task in rows.items():
        current_groups = groups_by_task[task_id]
        for dependency in task.get("dependencies", []):
            predecessor_groups = groups_by_task[dependency]
            for source in predecessor_groups:
                for target in current_groups:
                    if source != target:
                        quotient_edges.add((source, target))

    ordered_edges = tuple(sorted(quotient_edges))
    components = _strongly_connected_components(sorted(all_groups), ordered_edges)
    group_order = _condensed_group_order(components, ordered_edges)
    return WorkflowSchedule(
        task_order=task_order,
        target_group_components=group_order,
        target_group_edges=ordered_edges,
    )


def select_ready_tasks(tasks: Any) -> list[str]:
    """Return explicitly-ready tasks whose dependencies are verified or completed."""
    errors = validate_workflow_tasks(tasks)
    if errors:
        raise WorkflowValidationError(
            "Workflow validation failed:\n- " + "\n- ".join(errors)
        )
    rows = {str(task["task_id"]).strip(): task for task in tasks}
    candidates = []
    for task_id, task in rows.items():
        if task.get("status", "identified") != "ready":
            continue
        if all(
            rows[dependency].get("status") in {"verified", "completed"}
            for dependency in task.get("dependencies", [])
        ):
            sequence = task.get("sequence")
            sequence = sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else 0
            candidates.append((sequence, task_id))
    return [task_id for _, task_id in sorted(candidates)]


def transition_task(
    task: Mapping[str, Any],
    new_status: str,
    reason: str | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a legal transition only when its state-specific evidence is supplied."""
    if not isinstance(task, Mapping):
        raise WorkflowValidationError("Workflow task must be an object.")
    current = task.get("status", "identified")
    if not isinstance(current, str) or current not in WORKFLOW_STATUSES:
        raise WorkflowValidationError(f"Unsupported current workflow status: {current!r}.")
    if not isinstance(new_status, str) or new_status not in WORKFLOW_STATUSES:
        raise WorkflowValidationError(f"Unsupported target workflow status: {new_status!r}.")
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise WorkflowValidationError(
            f"Illegal workflow transition for {task.get('task_id', '<unknown>')}: "
            f"{current} -> {new_status}."
        )
    if new_status in {"blocked", "recheck_required"} and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise WorkflowValidationError(
            f"A non-empty reason is required when transitioning to {new_status}."
        )
    if evidence is not None and not isinstance(evidence, Mapping):
        raise WorkflowValidationError("Transition evidence must be an object.")

    supplied = dict(evidence or {})
    updated = copy.deepcopy(dict(task))

    if current == "recheck_required" or (
        current == "preflighted" and new_status in {"ready", "blocked"}
    ):
        _clear_execution_evidence(updated)
    if current == "recheck_required":
        updated.pop("recheck_from", None)
        updated.pop("invalidation_reason", None)
    if current == "blocked":
        updated.pop("block_reason", None)

    updated["status"] = new_status
    if new_status == "blocked":
        updated["block_reason"] = reason.strip()
        return updated
    if new_status == "recheck_required":
        updated["recheck_from"] = current
        updated["invalidation_reason"] = reason.strip()
        return updated

    if new_status == "preflighted":
        input_fingerprint = supplied.get("input_fingerprint")
        report_id = supplied.get("preflight_report_id")
        timestamp = supplied.get("preflighted_at_utc")
        if not _is_sha256(input_fingerprint):
            raise WorkflowValidationError(
                "Preflight requires a lowercase SHA-256 input_fingerprint."
            )
        if not isinstance(report_id, str) or not report_id.strip():
            raise WorkflowValidationError("Preflight requires preflight_report_id.")
        if not _is_aware_timestamp(timestamp):
            raise WorkflowValidationError("Preflight requires timezone-aware preflighted_at_utc.")
        updated["input_fingerprint"] = input_fingerprint
        updated["preflight_report_id"] = report_id.strip()
        updated["preflighted_at_utc"] = timestamp
        updated["preflight_fingerprint"] = workflow_preflight_fingerprint(
            updated, input_fingerprint
        )

    elif new_status == "approved":
        if supplied.get("human_approval_confirmed") is not True:
            raise WorkflowValidationError(
                "Approval requires explicit human_approval_confirmed=true."
            )
        approved_by = supplied.get("approved_by")
        approved_at = supplied.get("approved_at_utc")
        if not isinstance(approved_by, str) or not approved_by.strip():
            raise WorkflowValidationError("Approval requires approved_by.")
        if not _is_aware_timestamp(approved_at):
            raise WorkflowValidationError("Approval requires timezone-aware approved_at_utc.")
        updated["human_approval_confirmed"] = True
        updated["approved_by"] = approved_by.strip()
        updated["approved_at_utc"] = approved_at
        updated["approval_fingerprint"] = workflow_approval_fingerprint(
            updated, updated.get("preflight_fingerprint", "")
        )

    elif new_status == "applied":
        output_fingerprint = supplied.get("output_fingerprint")
        report_id = supplied.get("execution_report_id")
        timestamp = supplied.get("applied_at_utc")
        if not _is_sha256(output_fingerprint):
            raise WorkflowValidationError("Apply requires a lowercase SHA-256 output_fingerprint.")
        if not isinstance(report_id, str) or not report_id.strip():
            raise WorkflowValidationError("Apply requires execution_report_id.")
        if not _is_aware_timestamp(timestamp):
            raise WorkflowValidationError("Apply requires timezone-aware applied_at_utc.")
        updated["output_fingerprint"] = output_fingerprint
        updated["execution_report_id"] = report_id.strip()
        updated["applied_at_utc"] = timestamp

    elif new_status == "verified":
        if supplied.get("semantic_review_confirmed") is not True:
            raise WorkflowValidationError(
                "Verification requires explicit semantic_review_confirmed=true."
            )
        verified_by = supplied.get("verified_by")
        verified_at = supplied.get("verified_at_utc")
        report_id = supplied.get("verification_report_id")
        if not isinstance(verified_by, str) or not verified_by.strip():
            raise WorkflowValidationError("Verification requires verified_by.")
        if not _is_aware_timestamp(verified_at):
            raise WorkflowValidationError("Verification requires timezone-aware verified_at_utc.")
        if not isinstance(report_id, str) or not report_id.strip():
            raise WorkflowValidationError("Verification requires verification_report_id.")
        updated["semantic_review_confirmed"] = True
        updated["verified_by"] = verified_by.strip()
        updated["verified_at_utc"] = verified_at
        updated["verification_report_id"] = report_id.strip()
        updated["verification_fingerprint"] = workflow_verification_fingerprint(updated)

    elif new_status == "completed":
        record_id = supplied.get("completion_record_id")
        record_fingerprint = supplied.get("completion_record_fingerprint")
        completed_at = supplied.get("completed_at_utc")
        if not isinstance(record_id, str) or not record_id.strip():
            raise WorkflowValidationError("Completion requires completion_record_id.")
        if not _is_sha256(record_fingerprint):
            raise WorkflowValidationError(
                "Completion requires a lowercase SHA-256 completion_record_fingerprint."
            )
        if not _is_aware_timestamp(completed_at):
            raise WorkflowValidationError("Completion requires timezone-aware completed_at_utc.")
        updated["completion_record_id"] = record_id.strip()
        updated["completion_record_fingerprint"] = record_fingerprint
        updated["completed_at_utc"] = completed_at
        updated["completion_fingerprint"] = workflow_completion_fingerprint(updated)

    local_errors = _state_evidence_errors(
        str(updated.get("task_id", "<unknown>")), updated, new_status
    )
    if local_errors:
        raise WorkflowValidationError(
            "Transition evidence failed validation:\n- " + "\n- ".join(local_errors)
        )
    return updated

def invalidate_downstream(
    tasks: Any,
    changed_task_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    """Invalidate every transitive dependent when an upstream task changes."""
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowValidationError("A non-empty invalidation reason is required.")
    errors = validate_workflow_tasks(tasks)
    if errors:
        raise WorkflowValidationError(
            "Workflow validation failed:\n- " + "\n- ".join(errors)
        )

    rows = copy.deepcopy([dict(task) for task in tasks])
    by_id = {task["task_id"]: task for task in rows}
    if changed_task_id not in by_id:
        raise WorkflowValidationError(f"Unknown changed workflow task: {changed_task_id}.")

    dependents: dict[str, list[str]] = defaultdict(list)
    for task in rows:
        for dependency in task.get("dependencies", []):
            dependents[dependency].append(task["task_id"])

    impacted: set[str] = set()
    queue = deque(dependents.get(changed_task_id, []))
    while queue:
        task_id = queue.popleft()
        if task_id in impacted:
            continue
        impacted.add(task_id)
        queue.extend(dependents.get(task_id, []))

    for task_id in impacted:
        task = by_id[task_id]
        if task.get("status") != "recheck_required":
            task["recheck_from"] = task.get("status", "identified")
        task["status"] = "recheck_required"
        task["invalidation_reason"] = reason.strip()
    return rows


def workflow_tasks_from_decision_plan(tasks: Any) -> list[dict[str, Any]]:
    """Adapt pending decision-plan tasks to the workflow core.

    The target-group key is deliberately provisional: it uses the legacy
    document/Word-part/paragraph-index anchor until persistent object IDs land.
    Response-only and non-mutating tasks correctly have no document target group.
    """
    if not isinstance(tasks, (list, tuple)):
        return []
    adapted: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue

        groups: set[str] = set()
        targets = task.get("targets", [])
        if isinstance(targets, (list, tuple)):
            for target in targets:
                if not isinstance(target, Mapping):
                    continue
                action = target.get("action")
                if not isinstance(action, str) or action not in _MUTATING_TARGET_ACTIONS:
                    continue
                document = target.get("document")
                part = target.get("part")
                p_idx = target.get("p_idx")
                if (
                    isinstance(document, str) and document.strip()
                    and isinstance(part, str) and part.strip()
                    and isinstance(p_idx, int) and not isinstance(p_idx, bool) and p_idx >= 0
                ):
                    groups.add(f"{document.strip()}|{part.strip()}|p_idx={p_idx}")

        status = task.get("status", "pending")
        if status == "pending":
            status = "planned"
        if not isinstance(status, str) or status not in WORKFLOW_STATUSES:
            status = "planned"
        # Preserve the decision-plan evidence and author-facing task payload.
        # The workflow ledger is the common layer; DOCX-only revision_items are
        # generated later from mutating tasks, not from response-only tasks.
        record = copy.deepcopy(dict(task))
        record.update({
            "task_id": task_id.strip(),
            "sequence": task.get("sequence", 0),
            "stage": task.get("task_type", "revision"),
            "dependencies": task.get("dependencies", []),
            "target_groups": sorted(groups),
            "status": status,
        })
        adapted.append(record)
    return adapted


_WORKFLOW_TASK_TYPES = frozenset({
    "local_revision",
    "cross_document_consistency",
    "global_numbering",
    "content_placement_decision",
    "response_only",
    "author_decision",
    "evidence_request",
    "other",
})
_WORKFLOW_EVENT_TYPES = frozenset({
    "status_transition",
    "preflight",
    "approval",
    "patch_applied",
    "verification",
    "invalidation",
    "location_mapping",
})


def validate_workflow_ledger(ledger: Any) -> list[str]:
    """Validate the persisted review-round ledger and all of its task records."""
    if not isinstance(ledger, Mapping):
        return ["Workflow ledger must be an object."]
    errors: list[str] = []
    if ledger.get("schema_version") != "0.5.1":
        errors.append("Workflow ledger schema_version must be '0.5.1'.")
    round_id = ledger.get("revision_round_id")
    if not isinstance(round_id, str) or not round_id.strip():
        errors.append("Workflow ledger revision_round_id must be a non-empty string.")
    if "created_at_utc" in ledger and not _is_aware_timestamp(ledger.get("created_at_utc")):
        errors.append("Workflow ledger created_at_utc must be timezone-aware.")

    sources = ledger.get("source_files", [])
    if not isinstance(sources, list):
        errors.append("Workflow ledger source_files must be an array.")
        sources = []
    seen_sources: set[tuple[str, str]] = set()
    for index, source in enumerate(sources):
        prefix = f"Workflow ledger source_files[{index}]"
        if not isinstance(source, Mapping):
            errors.append(f"{prefix} must be an object.")
            continue
        role, file_path, digest = (
            source.get("role"), source.get("file"), source.get("sha256")
        )
        if role not in {"Reviewers", "Manuscript", "SI"}:
            errors.append(f"{prefix}.role is unsupported.")
        if not isinstance(file_path, str) or not file_path.strip():
            errors.append(f"{prefix}.file must be non-empty.")
        if not _is_sha256(digest):
            errors.append(f"{prefix}.sha256 must be a lowercase SHA-256 digest.")
        if isinstance(role, str) and isinstance(file_path, str):
            source_key = (role, file_path)
            if source_key in seen_sources:
                errors.append(f"{prefix} duplicates source {role}/{file_path}.")
            seen_sources.add(source_key)

    tasks = ledger.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("Workflow ledger tasks must be a non-empty array.")
        tasks = []
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id", f"index {index}")
        task_type = task.get("task_type")
        if not isinstance(task_type, str) or task_type not in _WORKFLOW_TASK_TYPES:
            errors.append(f"Task {task_id} has unsupported task_type {task_type!r}.")
        if not isinstance(task.get("target_groups"), (list, tuple)):
            errors.append(f"Task {task_id} target_groups must be an array.")
        if task_type == "response_only" and task.get("target_groups") != []:
            errors.append(f"Response-only task {task_id} cannot have document target groups.")

    errors.extend(validate_workflow_tasks(tasks))

    events = ledger.get("events", [])
    if not isinstance(events, list):
        errors.append("Workflow ledger events must be an array.")
        events = []
    known_task_ids = {
        task.get("task_id")
        for task in tasks if isinstance(task, Mapping)
    }
    seen_event_ids: set[str] = set()
    for index, event in enumerate(events):
        prefix = f"Workflow ledger events[{index}]"
        if not isinstance(event, Mapping):
            errors.append(f"{prefix} must be an object.")
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            errors.append(f"{prefix}.event_id must be non-empty.")
        elif event_id in seen_event_ids:
            errors.append(f"{prefix} duplicates event_id {event_id}.")
        else:
            seen_event_ids.add(event_id)
        if event.get("task_id") not in known_task_ids:
            errors.append(f"{prefix}.task_id refers to an unknown task.")
        if not _is_aware_timestamp(event.get("timestamp_utc")):
            errors.append(f"{prefix}.timestamp_utc must be timezone-aware.")
        if event.get("event_type") not in _WORKFLOW_EVENT_TYPES:
            errors.append(f"{prefix}.event_type is unsupported.")
        if "fingerprint" in event and not _is_sha256(event.get("fingerprint")):
            errors.append(f"{prefix}.fingerprint must be a lowercase SHA-256 digest.")

    return list(dict.fromkeys(errors))


def build_workflow_ledger(
    decision_plan: Mapping[str, Any],
    revision_round_id: str,
    created_at_utc: str,
) -> dict[str, Any]:
    """Create a durable Workflow Ledger from a validated decision-plan payload."""
    if not isinstance(decision_plan, Mapping):
        raise WorkflowValidationError("Decision plan must be an object.")
    if not isinstance(revision_round_id, str) or not revision_round_id.strip():
        raise WorkflowValidationError("revision_round_id must be non-empty.")
    if not _is_aware_timestamp(created_at_utc):
        raise WorkflowValidationError("created_at_utc must be timezone-aware.")
    tasks = workflow_tasks_from_decision_plan(decision_plan.get("tasks"))
    ledger = {
        "schema_version": "0.5.1",
        "revision_round_id": revision_round_id.strip(),
        "created_at_utc": created_at_utc,
        "source_files": copy.deepcopy(decision_plan.get("source_files", [])),
        "tasks": tasks,
        "events": [],
    }
    errors = validate_workflow_ledger(ledger)
    if errors:
        raise WorkflowValidationError(
            "Workflow ledger validation failed:\n- " + "\n- ".join(errors)
        )
    build_workflow_schedule(tasks)
    return ledger


class WorkflowEngine:
    """Stable namespace facade for the workflow engine public API."""

    validate_tasks = staticmethod(validate_workflow_tasks)
    build_schedule = staticmethod(build_workflow_schedule)
    select_ready_tasks = staticmethod(select_ready_tasks)
    transition_task = staticmethod(transition_task)
    invalidate_downstream = staticmethod(invalidate_downstream)
    from_decision_plan = staticmethod(workflow_tasks_from_decision_plan)
    build_ledger = staticmethod(build_workflow_ledger)
    validate_ledger = staticmethod(validate_workflow_ledger)

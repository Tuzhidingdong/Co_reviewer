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
from pathlib import Path
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .identity import CitationProfile, DocumentIdentity, DocumentObjectIdentity, TargetRef
from .safety_policy import assess_task_risk

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
    "ready": frozenset({"preflighted", "completed", "blocked", "recheck_required"}),
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
    "targets", "target_refs", "source_versions", "dependencies", "target_groups", "readiness",
    "unresolved_questions", "confidence", "requires_user_approval",
    "patch_id", "patch_operations", "supersedes_patch_id", "proposed_response",
    "response_unit_ids", "response_unit_specs",
    "risk_tier", "risk_policy_version", "risk_reasons",
    "high_risk_review_confirmed", "high_risk_reviewed_by",
    "high_risk_reviewed_at_utc", "high_risk_review_note",
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


def _canonical_workflow_file_path(value: str) -> str:
    """Compare filesystem identities consistently across Windows short/long path aliases."""
    try:
        return str(Path(value).expanduser().resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return str(Path(value).expanduser())



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

    response_only = task.get("task_type") == "response_only"
    if response_only and status in {"preflighted", "approved", "applied", "verified"}:
        errors.append(
            f"Response-only Task {task_id} cannot enter document-execution state {status}."
        )

    preflight_states = {"preflighted", "approved", "applied", "verified"}
    if not response_only:
        preflight_states.add("completed")
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

    approval_states = {"approved", "applied", "verified"}
    if not response_only:
        approval_states.add("completed")
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

    applied_states = {"applied", "verified"}
    if not response_only:
        applied_states.add("completed")
    if status in applied_states:
        if not _is_sha256(task.get("output_fingerprint")):
            errors.append(f"Task {task_id} requires output_fingerprint.")
        if not isinstance(task.get("execution_report_id"), str) or not task["execution_report_id"].strip():
            errors.append(f"Task {task_id} requires execution_report_id.")
        if not _is_aware_timestamp(task.get("applied_at_utc")):
            errors.append(f"Task {task_id} requires timezone-aware applied_at_utc.")

    verified_states = {"verified"}
    if not response_only:
        verified_states.add("completed")
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

    known_task_ids_for_sources = {
        task.get("task_id")
        for task in tasks
        if isinstance(task, Mapping) and isinstance(task.get("task_id"), str)
    }
    source_records.update(
        _published_source_records_from_events(
            ledger.get("events", []),
            known_task_ids_for_sources,
        )
    )
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
        if "risk_tier" in task:
            expected_risk = assess_task_risk(task)
            if task.get("risk_tier") != expected_risk["risk_tier"]:
                errors.append(
                    f"Task {task_id} risk_tier does not match deterministic policy "
                    f"({expected_risk['risk_tier']})."
                )
            if task.get("risk_policy_version") != expected_risk["risk_policy_version"]:
                errors.append(f"Task {task_id} risk_policy_version is stale or unsupported.")
            if task.get("risk_reasons") != expected_risk["risk_reasons"]:
                errors.append(f"Task {task_id} risk_reasons do not match deterministic policy.")
            if expected_risk["risk_tier"] == "TIER_1" and status in {
                "ready", "preflighted", "approved", "applied", "verified", "completed"
            }:
                if task.get("high_risk_review_confirmed") is not True:
                    errors.append(f"Tier 1 Task {task_id} requires explicit high_risk_review_confirmed=true.")
                if not isinstance(task.get("high_risk_reviewed_by"), str) or not task["high_risk_reviewed_by"].strip():
                    errors.append(f"Tier 1 Task {task_id} requires high_risk_reviewed_by.")
                if not _is_aware_timestamp(task.get("high_risk_reviewed_at_utc")):
                    errors.append(f"Tier 1 Task {task_id} requires timezone-aware high_risk_reviewed_at_utc.")
                if not isinstance(task.get("high_risk_review_note"), str) or not task["high_risk_review_note"].strip():
                    errors.append(f"Tier 1 Task {task_id} requires a non-empty high_risk_review_note.")

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
    """Return one deterministic, dependency-safe and target-conflict-free batch."""
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
    candidates.sort()

    schedule = build_workflow_schedule(tasks)
    component_of = {
        group_id: component_index
        for component_index, component in enumerate(schedule.target_group_components)
        for group_id in component
    }
    reserved_components: set[int] = set()
    selected: list[str] = []

    for _, task_id in candidates:
        task_groups, _ = _string_list(
            rows[task_id].get("target_groups", []), "target_groups", task_id
        )
        locks = {component_of[group] for group in task_groups if group in component_of}
        if locks & reserved_components:
            continue
        selected.append(task_id)
        reserved_components.update(locks)

    return selected

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
        for field in (
            "high_risk_review_confirmed", "high_risk_reviewed_by",
            "high_risk_reviewed_at_utc", "high_risk_review_note",
        ):
            updated.pop(field, None)
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

def invalidate_affected_tasks(
    tasks: Any,
    changed_task_ids: Sequence[str],
    reason: str,
    *,
    preserve_verified_conflict_task_ids: Sequence[str] = (),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Invalidate same-target conflict peers and all transitive dependents."""
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowValidationError("A non-empty invalidation reason is required.")
    errors = validate_workflow_tasks(tasks)
    if errors:
        raise WorkflowValidationError("Workflow validation failed:\n- " + "\n- ".join(errors))
    if not isinstance(changed_task_ids, (list, tuple, set)) or not changed_task_ids:
        raise WorkflowValidationError("changed_task_ids must contain at least one task ID.")

    rows = copy.deepcopy([dict(task) for task in tasks])
    by_id = {task["task_id"]: task for task in rows}
    changed = set(changed_task_ids)
    unknown = changed - set(by_id)
    if unknown:
        raise WorkflowValidationError("Unknown changed task(s): " + ", ".join(sorted(unknown)))
    if not isinstance(preserve_verified_conflict_task_ids, (list, tuple, set)):
        raise WorkflowValidationError(
            "preserve_verified_conflict_task_ids must be a sequence of verified task IDs."
        )
    preserved = set(preserve_verified_conflict_task_ids)
    invalid_preserved = sorted(
        task_id for task_id in preserved
        if task_id not in by_id
        or by_id[task_id].get("status") not in {"verified", "completed"}
    )
    if invalid_preserved:
        raise WorkflowValidationError(
            "Only existing verified/completed task IDs may be preserved as sequential predecessors: "
            + ", ".join(invalid_preserved)
        )

    schedule = build_workflow_schedule(rows)
    component_of = {
        group_id: index
        for index, component in enumerate(schedule.target_group_components)
        for group_id in component
    }
    changed_components: set[int] = set()
    for task_id in changed:
        groups, _ = _string_list(by_id[task_id].get("target_groups", []), "target_groups", task_id)
        changed_components.update(
            component_of[group] for group in groups if group in component_of
        )

    conflicting: set[str] = set()
    for task_id, task in by_id.items():
        if task_id in changed:
            continue
        groups, _ = _string_list(task.get("target_groups", []), "target_groups", task_id)
        if (
            task_id not in preserved
            and any(component_of.get(group) in changed_components for group in groups)
        ):
            conflicting.add(task_id)

    dependents: dict[str, list[str]] = defaultdict(list)
    for task in rows:
        for dependency in task.get("dependencies", []):
            dependents[dependency].append(task["task_id"])

    impacted = set(conflicting)
    queue = deque([*changed, *conflicting])
    visited = set(changed)
    while queue:
        source_id = queue.popleft()
        for dependent_id in dependents.get(source_id, []):
            if dependent_id in visited:
                continue
            visited.add(dependent_id)
            impacted.add(dependent_id)
            queue.append(dependent_id)

    for task_id in impacted:
        task = by_id[task_id]
        if task.get("status") != "recheck_required":
            task["recheck_from"] = task.get("status", "identified")
        task["status"] = "recheck_required"
        task["invalidation_reason"] = reason.strip()
    return rows, sorted(impacted)


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
    if not isinstance(changed_task_id, str) or changed_task_id not in by_id:
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


def _identity_record_payload(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        record = to_dict()
        if isinstance(record, Mapping):
            return copy.deepcopy(dict(record))
    raise WorkflowValidationError(
        f"{field} entries must be identity objects or dictionaries."
    )


def workflow_tasks_from_decision_plan(tasks: Any, target_refs_by_task: Mapping[str, Sequence[Any]] | None = None) -> list[dict[str, Any]]:
    """Adapt pending decision-plan tasks to the workflow core.

    The target-group key is deliberately provisional: it uses the legacy
    document/Word-part/paragraph-index anchor until persistent object IDs land.
    Response-only and non-mutating tasks correctly have no document target group.
    """
    if not isinstance(tasks, (list, tuple)):
        return []
    if target_refs_by_task is not None and not isinstance(target_refs_by_task, Mapping):
        raise WorkflowValidationError("target_refs_by_task must be a mapping keyed by task_id.")
    adapted: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue

        groups: set[str] = set()
        stable_refs: list[dict[str, Any]] = []
        supplied_refs = (
            target_refs_by_task.get(task_id.strip(), [])
            if target_refs_by_task is not None else []
        )
        if not isinstance(supplied_refs, (list, tuple)):
            raise WorkflowValidationError(
                f"Target refs for task {task_id} must be an array."
            )
        for target_ref in supplied_refs:
            record = _identity_record_payload(target_ref, f"Target refs for task {task_id}")
            stable_refs.append(record)
            document_id = record.get("document_id")
            object_id = record.get("object_id")
            if (
                isinstance(document_id, str) and document_id.strip()
                and isinstance(object_id, str) and object_id.strip()
            ):
                groups.add(f"{document_id.strip()}|object={object_id.strip()}")

        # Prefer persistent object IDs. The old p_idx key is only a migration
        # fallback for tasks that have not yet been mapped into the object registry.
        if not stable_refs:
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
        if stable_refs:
            record["target_refs"] = stable_refs
        record.update(assess_task_risk(record))
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
    "batch_published",
    "batch_rolled_back",
})


def _published_source_records_from_events(
    events: Any,
    known_task_ids: set[str],
) -> set[tuple[str, str, str]]:
    """Return source versions grounded in structurally valid append-only publish events.

    source_files is the immutable initial-input inventory. Published versions are
    recorded by batch_published event documents and must not be copied into that
    initial inventory merely to satisfy task provenance validation.
    """
    records: set[tuple[str, str, str]] = set()
    if not isinstance(events, list):
        return records
    for event in events:
        if not isinstance(event, Mapping) or event.get("event_type") != "batch_published":
            continue
        if event.get("task_id") not in known_task_ids:
            continue
        batch_id = event.get("batch_id")
        documents = event.get("documents")
        if not isinstance(batch_id, str) or not batch_id.strip() or not isinstance(documents, Mapping) or not documents:
            continue
        for role, pointer in documents.items():
            if not isinstance(role, str) or role not in {"Reviewers", "Manuscript", "SI"}:
                continue
            if not isinstance(pointer, Mapping):
                continue
            file_path = pointer.get("file")
            digest = pointer.get("sha256")
            if (
                isinstance(file_path, str) and file_path.strip()
                and _is_sha256(digest)
                and pointer.get("batch_id") == batch_id
                and _is_aware_timestamp(pointer.get("updated_at_utc"))
            ):
                records.add((role, _canonical_workflow_file_path(file_path), digest))
    return records


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
    seen_source_roles: set[str] = set()
    source_records: set[tuple[str, str, str]] = set()
    source_hashes_by_role: dict[str, set[str]] = defaultdict(set)
    for index, source in enumerate(sources):
        prefix = f"Workflow ledger source_files[{index}]"
        if not isinstance(source, Mapping):
            errors.append(f"{prefix} must be an object.")
            continue
        role, file_path, digest = (
            source.get("role"), source.get("file"), source.get("sha256")
        )
        if not isinstance(role, str) or role not in {"Reviewers", "Manuscript", "SI"}:
            errors.append(f"{prefix}.role is unsupported.")
        elif role in seen_source_roles:
            errors.append(
                f"{prefix} duplicates initial source role {role}; source_files is an initial-input inventory, not version history."
            )
        else:
            seen_source_roles.add(role)
        if not isinstance(file_path, str) or not file_path.strip():
            errors.append(f"{prefix}.file must be non-empty.")
        if not _is_sha256(digest):
            errors.append(f"{prefix}.sha256 must be a lowercase SHA-256 digest.")
        if isinstance(role, str) and isinstance(file_path, str):
            canonical_file_path = _canonical_workflow_file_path(file_path)
            source_key = (role, canonical_file_path)
            if source_key in seen_sources:
                errors.append(f"{prefix} duplicates source {role}/{file_path}.")
            seen_sources.add(source_key)
        if (
            isinstance(role, str) and isinstance(file_path, str)
            and isinstance(digest, str) and _is_sha256(digest)
        ):
            source_hashes_by_role[role].add(digest)
            source_records.add((role, _canonical_workflow_file_path(file_path), digest))

    active_documents = ledger.get("active_documents")
    if active_documents is not None:
        if not isinstance(active_documents, Mapping):
            errors.append("Workflow Ledger active_documents must be an object.")
        else:
            known_roles = {row.get("role") for row in sources if isinstance(row, Mapping)}
            for role, pointer in active_documents.items():
                prefix = f"Workflow Ledger active_documents[{role}]"
                if not isinstance(role, str) or role not in known_roles:
                    errors.append(f"{prefix} uses a role absent from source_files.")
                    continue
                if not isinstance(pointer, Mapping):
                    errors.append(f"{prefix} must be an object.")
                    continue
                file_path, digest = pointer.get("file"), pointer.get("sha256")
                if not isinstance(file_path, str) or not file_path.strip():
                    errors.append(f"{prefix}.file must be non-empty.")
                if not _is_sha256(digest):
                    errors.append(f"{prefix}.sha256 must be a lowercase SHA-256 digest.")
                if (
                    isinstance(role, str) and isinstance(file_path, str)
                    and isinstance(digest, str) and _is_sha256(digest)
                ):
                    source_records.add((role, str(Path(file_path).expanduser().resolve()), digest))
                batch_id = pointer.get("batch_id")
                if batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip()):
                    errors.append(f"{prefix}.batch_id must be a non-empty string or null.")
                updated_at = pointer.get("updated_at_utc")
                if updated_at is not None and not _is_aware_timestamp(updated_at):
                    errors.append(f"{prefix}.updated_at_utc must be timezone-aware.")

    documents = ledger.get("documents", [])
    if not isinstance(documents, list):
        errors.append("Workflow ledger documents must be an array.")
        documents = []
    document_versions: dict[tuple[str, str], DocumentIdentity] = {}
    for index, row in enumerate(documents):
        prefix = f"Workflow ledger documents[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{prefix} must be an object.")
            continue
        dimensions = row.get("dimensions")
        profile_data = row.get("citation_profile")
        try:
            if not isinstance(dimensions, Mapping) or not isinstance(profile_data, Mapping):
                raise IdentityValidationError("dimensions and citation_profile must be objects.")
            profile = CitationProfile(
                mode=profile_data.get("mode"),
                style_name=profile_data.get("style_name"),
                numbering_scope=profile_data.get("numbering_scope"),
                marker_examples=tuple(profile_data.get("marker_examples", [])),
                reference_labels=tuple(profile_data.get("reference_labels", [])),
                detection_method=profile_data.get("detection_method"),
                evidence_fingerprint=profile_data.get("evidence_fingerprint"),
                profile_version=profile_data.get("profile_version", "1"),
            )
            identity = DocumentIdentity(
                document_id=row.get("document_id"),
                revision_id=row.get("revision_id"),
                role=row.get("role"),
                source_sha256=row.get("source_sha256"),
                content_sha256=dimensions.get("content_sha256"),
                structure_sha256=dimensions.get("structure_sha256"),
                formatting_sha256=dimensions.get("formatting_sha256"),
                citation_sha256=dimensions.get("citation_sha256"),
                citation_profile=profile,
                identity_version=row.get("identity_version", "0.5.1"),
            )
            key = (identity.document_id, identity.revision_id)
            if key in document_versions:
                errors.append(f"{prefix} duplicates document revision {key}.")
            document_versions[key] = identity
            source_digests = source_hashes_by_role.get(identity.role, set())
            if len(source_digests) == 1 and identity.source_sha256 not in source_digests:
                errors.append(
                    f"{prefix} source_sha256 does not match the Workflow Ledger source_files "
                    f"entry for role {identity.role}."
                )
            elif len(source_digests) > 1:
                errors.append(
                    f"{prefix} role {identity.role} has multiple source-file hashes; "
                    "the identity manifest cannot be bound unambiguously to one logical document."
                )
        except (IdentityValidationError, TypeError, ValueError) as exc:
            errors.append(f"{prefix} is invalid: {exc}")

    objects = ledger.get("object_registry", [])
    if not isinstance(objects, list):
        errors.append("Workflow ledger object_registry must be an array.")
        objects = []
    object_records: dict[tuple[str, str, str], DocumentObjectIdentity] = {}
    for index, row in enumerate(objects):
        prefix = f"Workflow ledger object_registry[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{prefix} must be an object.")
            continue
        try:
            item = DocumentObjectIdentity(
                object_id=row.get("object_id"),
                document_id=row.get("document_id"),
                revision_id=row.get("revision_id"),
                object_type=row.get("object_type"),
                part=row.get("part"),
                object_fingerprint=row.get("object_fingerprint"),
                native_id=row.get("native_id"),
                parent_object_id=row.get("parent_object_id"),
                location_hint=row.get("location_hint"),
            )
            document_key = (item.document_id, item.revision_id)
            if document_key not in document_versions:
                errors.append(f"{prefix} refers to an unknown document revision.")
            object_key = (item.document_id, item.revision_id, item.object_id)
            if object_key in object_records:
                errors.append(f"{prefix} duplicates object identity {object_key}.")
            object_records[object_key] = item
        except (IdentityValidationError, TypeError, ValueError) as exc:
            errors.append(f"{prefix} is invalid: {exc}")

    tasks = ledger.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("Workflow ledger tasks must be a non-empty array.")
        tasks = []
    for index, task in enumerate(tasks):
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id", f"index {index}")
        task_source_versions = task.get("source_versions", [])
        if not isinstance(task_source_versions, list):
            errors.append(f"Task {task_id} source_versions must be an array.")
            task_source_versions = []
        seen_task_sources: set[tuple[str, str, str]] = set()
        for source_index, source_version in enumerate(task_source_versions):
            prefix = f"Task {task_id} source_versions[{source_index}]"
            if not isinstance(source_version, Mapping):
                errors.append(f"{prefix} must be an object.")
                continue
            role = source_version.get("role")
            file_path = source_version.get("file")
            digest = source_version.get("sha256")
            if (
                not isinstance(role, str) or role not in {"Reviewers", "Manuscript", "SI"}
                or not isinstance(file_path, str) or not file_path.strip()
                or not _is_sha256(digest)
            ):
                errors.append(f"{prefix} requires a supported role, file and SHA-256.")
                continue
            key = (role, _canonical_workflow_file_path(file_path), digest)
            if key in seen_task_sources:
                errors.append(f"{prefix} duplicates a source version.")
            seen_task_sources.add(key)
            if key not in source_records:
                errors.append(
                    f"{prefix} is not present in the initial source inventory, current active pointers, or validated published-batch history."
                )
        task_type = task.get("task_type")
        if not isinstance(task_type, str) or task_type not in _WORKFLOW_TASK_TYPES:
            errors.append(f"Task {task_id} has unsupported task_type {task_type!r}.")
        if not isinstance(task.get("target_groups"), (list, tuple)):
            errors.append(f"Task {task_id} target_groups must be an array.")
        target_refs = task.get("target_refs", [])
        if not isinstance(target_refs, list):
            errors.append(f"Task {task_id} target_refs must be an array.")
            target_refs = []
        expected_groups: set[str] = set()
        for ref_index, row in enumerate(target_refs):
            prefix = f"Task {task_id} target_refs[{ref_index}]"
            if not isinstance(row, Mapping):
                errors.append(f"{prefix} must be an object.")
                continue
            try:
                target_ref = TargetRef(
                    document_id=row.get("document_id"),
                    revision_id=row.get("revision_id"),
                    object_id=row.get("object_id"),
                    object_type=row.get("object_type"),
                    part=row.get("part"),
                    object_fingerprint=row.get("object_fingerprint"),
                    start_offset=row.get("start_offset"),
                    end_offset=row.get("end_offset"),
                    location_hint=row.get("location_hint"),
                )
                key = (
                    target_ref.document_id,
                    target_ref.revision_id,
                    target_ref.object_id,
                )
                registered = object_records.get(key)
                if registered is None:
                    errors.append(f"{prefix} is not present in the object registry.")
                elif (
                    registered.object_type != target_ref.object_type
                    or registered.part != target_ref.part
                    or registered.object_fingerprint != target_ref.object_fingerprint
                ):
                    errors.append(f"{prefix} does not match the registered object fingerprint/type/part.")
                expected_groups.add(
                    f"{target_ref.document_id}|object={target_ref.object_id}"
                )
            except (IdentityValidationError, TypeError, ValueError) as exc:
                errors.append(f"{prefix} is invalid: {exc}")
        raw_groups = task.get("target_groups", [])
        group_set = {
            value for value in raw_groups
            if isinstance(value, str)
        } if isinstance(raw_groups, (list, tuple)) else set()
        if target_refs and not expected_groups.issubset(group_set):
            errors.append(f"Task {task_id} target_groups omit one or more stable TargetRefs.")
        raw_patch_operations = task.get("patch_operations", [])
        if not isinstance(raw_patch_operations, list):
            errors.append(f"Task {task_id} patch_operations must be an array.")
            raw_patch_operations = []
        if task_type == "response_only" and raw_patch_operations:
            errors.append(f"Response-only task {task_id} cannot contain DOCX patch operations.")
        if raw_patch_operations:
            from .revision_models import PatchOperation, RevisionModelError
            operation_ids: set[str] = set()
            for operation_index, raw_operation in enumerate(raw_patch_operations):
                prefix = f"Task {task_id} patch_operations[{operation_index}]"
                try:
                    operation = PatchOperation.from_mapping(
                        raw_operation,
                        fallback_id=f"{task_id}-OP{operation_index + 1:03d}",
                    )
                    if operation.operation_id in operation_ids:
                        errors.append(f"{prefix} duplicates operation_id.")
                    operation_ids.add(operation.operation_id)
                    if operation.document not in {
                        target.get("document")
                        for target in task.get("targets", [])
                        if isinstance(target, Mapping)
                    }:
                        errors.append(
                            f"{prefix} document role is not represented in the task's evidence-linked targets."
                        )
                    if operation.evidence_ids:
                        known_evidence_ids = {
                            row.get("evidence_id")
                            for row in task.get("evidence", [])
                            if isinstance(row, Mapping)
                        }
                        if not set(operation.evidence_ids).issubset(known_evidence_ids):
                            errors.append(f"{prefix} refers to unknown task evidence IDs.")
                except (RevisionModelError, TypeError, ValueError) as exc:
                    errors.append(f"{prefix} is invalid: {exc}")
        if task_type == "response_only" and (
            task.get("target_groups") != [] or target_refs
        ):
            errors.append(f"Response-only task {task_id} cannot have document targets.")

    errors.extend(validate_workflow_tasks(tasks))

    events = ledger.get("events", [])
    if not isinstance(events, list):
        errors.append("Workflow ledger events must be an array.")
        events = []
    known_task_ids = {
        task.get("task_id")
        for task in tasks
        if isinstance(task, Mapping) and isinstance(task.get("task_id"), str)
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
        event_task_id = event.get("task_id")
        if not isinstance(event_task_id, str) or event_task_id not in known_task_ids:
            errors.append(f"{prefix}.task_id refers to an unknown task.")
        if not _is_aware_timestamp(event.get("timestamp_utc")):
            errors.append(f"{prefix}.timestamp_utc must be timezone-aware.")
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or event_type not in _WORKFLOW_EVENT_TYPES:
            errors.append(f"{prefix}.event_type is unsupported.")
        if "fingerprint" in event and not _is_sha256(event.get("fingerprint")):
            errors.append(f"{prefix}.fingerprint must be a lowercase SHA-256 digest.")
        if event_type in {"batch_published", "batch_rolled_back"}:
            batch_id = event.get("batch_id")
            if not isinstance(batch_id, str) or not batch_id.strip():
                errors.append(f"{prefix}.batch_id must be a non-empty string for batch events.")
            documents = event.get("documents")
            if not isinstance(documents, Mapping) or not documents:
                errors.append(f"{prefix}.documents must be a non-empty role-to-pointer object for batch events.")
                continue
            for role, pointer in documents.items():
                pointer_prefix = f"{prefix}.documents[{role}]"
                if not isinstance(role, str) or role not in {"Reviewers", "Manuscript", "SI"}:
                    errors.append(f"{pointer_prefix} has an unsupported document role.")
                    continue
                if not isinstance(pointer, Mapping):
                    errors.append(f"{pointer_prefix} must be an object.")
                    continue
                file_path = pointer.get("file")
                digest = pointer.get("sha256")
                pointer_batch_id = pointer.get("batch_id")
                if not isinstance(file_path, str) or not file_path.strip():
                    errors.append(f"{pointer_prefix}.file must be non-empty.")
                if not _is_sha256(digest):
                    errors.append(f"{pointer_prefix}.sha256 must be a lowercase SHA-256 digest.")
                if event_type == "batch_published" and pointer_batch_id != batch_id:
                    errors.append(f"{pointer_prefix}.batch_id must match its batch_published event.")
                elif event_type == "batch_rolled_back" and pointer_batch_id is not None and (
                    not isinstance(pointer_batch_id, str) or not pointer_batch_id.strip()
                ):
                    errors.append(f"{pointer_prefix}.batch_id must be a non-empty string or null.")
                if not _is_aware_timestamp(pointer.get("updated_at_utc")):
                    errors.append(f"{pointer_prefix}.updated_at_utc must be timezone-aware.")

    for task in tasks:
        if not isinstance(task, Mapping) or task.get("status") != "ready":
            continue
        task_id = task.get("task_id")
        authorization_events = [
            event for event in events
            if isinstance(event, Mapping)
            and event.get("task_id") == task_id
            and event.get("event_type") == "status_transition"
            and event.get("to_status") == "ready"
            and event.get("from_status") in {"planned", "blocked", "recheck_required"}
            and isinstance(event.get("actor"), str)
            and event.get("actor", "").strip()
        ]
        if not authorization_events:
            errors.append(
                f"Task {task_id} is ready without an explicit author-authorization event."
            )
            continue
        expected_fingerprint = workflow_scope_fingerprint(task)
        if not any(
            event.get("fingerprint") == expected_fingerprint
            for event in authorization_events
        ):
            errors.append(
                f"Task {task_id} ready authorization fingerprint does not match its current task scope."
            )

    return list(dict.fromkeys(errors))


def build_workflow_ledger(
    decision_plan: Mapping[str, Any],
    revision_round_id: str,
    created_at_utc: str,
    *,
    document_identities: Sequence[Any] | None = None,
    object_registry: Sequence[Any] | None = None,
    target_refs_by_task: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Create a durable Workflow Ledger from a validated decision-plan payload."""
    if not isinstance(decision_plan, Mapping):
        raise WorkflowValidationError("Decision plan must be an object.")
    if not isinstance(revision_round_id, str) or not revision_round_id.strip():
        raise WorkflowValidationError("revision_round_id must be non-empty.")
    if not _is_aware_timestamp(created_at_utc):
        raise WorkflowValidationError("created_at_utc must be timezone-aware.")
    documents = [
        _identity_record_payload(item, "document_identities")
        for item in (document_identities or [])
    ]
    objects = [
        _identity_record_payload(item, "object_registry")
        for item in (object_registry or [])
    ]
    tasks = workflow_tasks_from_decision_plan(
        decision_plan.get("tasks"),
        target_refs_by_task=target_refs_by_task,
    )
    source_records_by_role: dict[str, list[dict[str, str]]] = defaultdict(list)
    for source in decision_plan.get("source_files", []):
        if not isinstance(source, Mapping):
            continue
        role = source.get("role")
        if role == "Reply":
            role = "Reviewers"
        file_path = source.get("file")
        digest = source.get("sha256")
        if (
            isinstance(role, str) and role
            and isinstance(file_path, str) and file_path
            and isinstance(digest, str) and _is_sha256(digest)
        ):
            source_records_by_role[role].append({
                "role": role,
                "file": file_path,
                "sha256": digest,
            })
    for task in tasks:
        roles: set[str] = set()
        if isinstance(task.get("target_documents"), (list, tuple)):
            roles.update(
                "Reviewers" if role == "Reply" else role
                for role in task["target_documents"]
                if isinstance(role, str) and role
            )
        if task.get("source_comment_ids"):
            roles.add("Reviewers")
        for evidence in task.get("evidence", []):
            if isinstance(evidence, Mapping):
                role = evidence.get("document")
                if isinstance(role, str) and role:
                    roles.add("Reviewers" if role == "Reply" else role)
        for target in task.get("targets", []):
            if isinstance(target, Mapping):
                role = target.get("document")
                if isinstance(role, str) and role:
                    roles.add("Reviewers" if role == "Reply" else role)
        task["source_versions"] = [
            copy.deepcopy(source)
            for role in sorted(roles)
            for source in sorted(
                source_records_by_role.get(role, []),
                key=lambda item: (item["file"], item["sha256"]),
            )
        ]
    source_files = copy.deepcopy(decision_plan.get("source_files", []))
    active_documents: dict[str, dict[str, Any]] = {}
    for source in source_files:
        if not isinstance(source, Mapping):
            continue
        role, file_path, digest = source.get("role"), source.get("file"), source.get("sha256")
        if isinstance(role, str) and isinstance(file_path, str) and _is_sha256(digest):
            if role in active_documents:
                raise WorkflowValidationError(
                    f"Multiple initial source files exist for role {role}; resolve document identity before creating active pointers."
                )
            active_documents[role] = {
                "file": file_path,
                "sha256": digest,
                "batch_id": None,
                "updated_at_utc": created_at_utc,
            }
    ledger = {
        "schema_version": "0.5.1",
        "revision_round_id": revision_round_id.strip(),
        "created_at_utc": created_at_utc,
        "source_files": source_files,
        "active_documents": active_documents,
        "documents": documents,
        "object_registry": objects,
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

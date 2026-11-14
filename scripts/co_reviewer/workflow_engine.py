# -*- coding: utf-8 -*-
"""Deterministic workflow state transitions and dependency-aware scheduling.

This is the v0.5.1 workflow core. It is intentionally independent from the
legacy DOCX CLI: decision-plan adapters can feed it without making document
editing, human approval, or Word pagination implicit side effects.
"""
from __future__ import annotations

import copy
import heapq
from collections import defaultdict, deque
from dataclasses import dataclass
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
        if status not in WORKFLOW_STATUSES:
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
) -> dict[str, Any]:
    """Apply one legal lifecycle transition without mutating the input mapping."""
    if not isinstance(task, Mapping):
        raise WorkflowValidationError("Workflow task must be an object.")
    current = task.get("status", "identified")
    if current not in WORKFLOW_STATUSES:
        raise WorkflowValidationError(f"Unsupported current workflow status: {current!r}.")
    if new_status not in WORKFLOW_STATUSES:
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

    updated = copy.deepcopy(dict(task))
    updated["status"] = new_status
    if new_status == "blocked":
        updated["block_reason"] = reason.strip()
    elif current == "blocked":
        updated.pop("block_reason", None)

    if new_status == "recheck_required":
        updated["recheck_from"] = current
        updated["invalidation_reason"] = reason.strip()
    elif current == "recheck_required":
        updated.pop("recheck_from", None)
        updated.pop("invalidation_reason", None)
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
                if target.get("action") not in _MUTATING_TARGET_ACTIONS:
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
        if status not in WORKFLOW_STATUSES:
            status = "planned"
        adapted.append({
            "task_id": task_id.strip(),
            "sequence": task.get("sequence", 0),
            "stage": task.get("task_type", "revision"),
            "dependencies": task.get("dependencies", []),
            "target_groups": sorted(groups),
            "status": status,
        })
    return adapted


class WorkflowEngine:
    """Stable namespace facade for the workflow engine public API."""

    validate_tasks = staticmethod(validate_workflow_tasks)
    build_schedule = staticmethod(build_workflow_schedule)
    select_ready_tasks = staticmethod(select_ready_tasks)
    transition_task = staticmethod(transition_task)
    invalidate_downstream = staticmethod(invalidate_downstream)
    from_decision_plan = staticmethod(workflow_tasks_from_decision_plan)

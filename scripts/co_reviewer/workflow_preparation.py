from __future__ import annotations

import copy
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from .batch_store import load_active_document_pointers
from .identity import sha256_file
from .revision_models import PatchOperation, RevisionModelError, validate_author_decision_ref
from .workflow_engine import validate_workflow_ledger


class WorkflowPreparationError(ValueError):
    """An author-reviewed execution scope cannot be safely merged."""


def _validate_vertical_alignment_source_range(
    operation_id: str,
    format_index: int,
    raw_format: Mapping[str, Any],
    source_occurrence: Mapping[str, Any],
    source_properties: Mapping[str, Any],
) -> None:
    """Require script formatting to cite the exact same local token as its source occurrence."""
    if source_properties.get("vertAlign") not in {"subscript", "superscript"}:
        return
    target_text = raw_format.get("find")
    source_text = source_occurrence.get("text")
    label = f"PatchOperation {operation_id} format_operations[{format_index}]"
    if not isinstance(target_text, str) or not target_text or target_text != source_text:
        raise WorkflowPreparationError(
            f"{label} vertical alignment must target the exact text of its source occurrence."
        )
    if any(character.isspace() for character in target_text):
        raise WorkflowPreparationError(
            f"{label} vertical alignment source occurrence is multiword; sentence-level subscript/superscript is refused."
        )


_MUTATING = {"replace", "add", "remove", "renumber", "reference_update"}
_OPERATION_FIELDS = {
    "operation_id", "document", "part", "p_idx", "find", "replace",
    "location", "evidence_ids", "source_comment_ids", "author_decision_ref", "location_phrase", "target_ref",
    "two_pass", "format_authorization", "format_operations", "replacement_format_map",
}
_TASK_FIELDS = {
    "task_id", "patch_id", "proposed_response", "patch_operations",
    "response_unit_specs", "supersedes_patch_id", "evidence", "targets", "target_refs",
    "author_decision_ref",
}
_DISPOSITIONS = {"revised", "reply_only", "no_change", "clarification_needed"}
_LOCATION_REQUIREMENTS = {"required", "existing_anchor_required", "not_required", "manual_required"}


def _role(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return "Reviewers" if value.casefold() in {"reviewer", "reviewers", "reply"} else value


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-") or "item"


def _read_inventory_sources(
    inventory: Any,
    workflow: Mapping[str, Any],
    active_pointers: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, str], dict[str, str], dict[str, Mapping[str, Any]]]:
    if isinstance(inventory, list):
        docs = inventory
    elif isinstance(inventory, Mapping):
        docs = inventory.get("documents")
    else:
        docs = None
    if not isinstance(docs, list):
        raise WorkflowPreparationError("Workspace inventory must be the document-record array emitted by audit.")
    workflow_sources = {
        _role(row.get("role")): row
        for row in workflow.get("source_files", [])
        if isinstance(row, Mapping)
    }
    hashes: dict[str, str] = {}
    authors: dict[str, str] = {}
    reports_by_role: dict[str, Mapping[str, Any]] = {}
    reviewer_docs = 0
    for doc in docs:
        if not isinstance(doc, Mapping):
            continue
        role = _role(doc.get("role"))
        if role not in {"Reviewers", "Manuscript", "SI"}:
            continue
        pointer = active_pointers.get(role)
        source = workflow_sources.get(role)
        expected_file = pointer.get("file") if isinstance(pointer, Mapping) else (
            source.get("file") if isinstance(source, Mapping) else None
        )
        expected_sha = pointer.get("sha256") if isinstance(pointer, Mapping) else (
            source.get("sha256") if isinstance(source, Mapping) else None
        )
        digest = doc.get("source_sha256") or doc.get("sha256")
        path_value = doc.get("file")
        if (
            not isinstance(expected_file, str)
            or not isinstance(path_value, str)
            or not isinstance(digest, str)
            or Path(path_value).expanduser().resolve() != Path(expected_file).expanduser().resolve()
            or digest != expected_sha
            or not Path(path_value).is_file()
            or sha256_file(path_value) != digest
        ):
            raise WorkflowPreparationError(
                f"Inventory source identity for {role} does not match the current active document path/hash."
            )
        if role in hashes:
            raise WorkflowPreparationError(f"Inventory contains multiple source records for role {role}.")
        hashes[role] = digest
        reports_by_role[role] = doc
        if role == "Reviewers":
            reviewer_docs += 1
            comments = doc.get("comments", [])
            if not isinstance(comments, list):
                raise WorkflowPreparationError("Reviewer comment inventory must be an array.")
            for comment in comments:
                if not isinstance(comment, Mapping) or comment.get("id") is None:
                    continue
                author = comment.get("author")
                if isinstance(author, str) and author.strip():
                    authors[str(comment["id"])] = author.strip()
    if reviewer_docs > 1:
        raise WorkflowPreparationError("Multiple Reviewers documents are not supported in one review round.")
    missing = set(active_pointers) - set(hashes)
    if missing:
        raise WorkflowPreparationError("Workspace inventory is missing active role(s): " + ", ".join(sorted(missing)))
    return hashes, authors, reports_by_role


def _reviewer_spec(
    task_id: str,
    comment_ids: list[str],
    supplied: Any,
    authors: Mapping[str, str],
) -> dict[str, Any]:
    if isinstance(supplied, str):
        if not supplied.strip():
            raise WorkflowPreparationError(f"Reviewer label for {task_id} cannot be empty.")
        return {"default": supplied.strip()}
    if supplied is not None and not isinstance(supplied, Mapping):
        raise WorkflowPreparationError(f"Reviewer map for {task_id} must be a string or object.")
    supplied = dict(supplied or {})
    if set(supplied) - {"default", "patch_label", "comment_ids"}:
        raise WorkflowPreparationError(f"Reviewer map for {task_id} contains unsupported fields.")
    result: dict[str, Any] = {}
    for name in ("default", "patch_label"):
        value = supplied.get(name)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise WorkflowPreparationError(f"Reviewer map {task_id}.{name} must be non-empty.")
            result[name] = value.strip()
    labels = supplied.get("comment_ids", {})
    if not isinstance(labels, Mapping):
        raise WorkflowPreparationError(f"Reviewer map {task_id}.comment_ids must be an object.")
    merged: dict[str, str] = {}
    for raw_id, label in labels.items():
        comment_id = str(raw_id)
        if comment_id not in comment_ids:
            raise WorkflowPreparationError(f"Reviewer map {task_id} refers to unknown comment {comment_id}.")
        if not isinstance(label, str) or not label.strip():
            raise WorkflowPreparationError(f"Reviewer map label for comment {comment_id} is empty.")
        merged[comment_id] = label.strip()
    for comment_id in comment_ids:
        if comment_id not in merged and "default" not in result:
            merged[comment_id] = authors.get(comment_id, "Reviewer")
    if merged:
        result["comment_ids"] = merged
    return result




def _normalised_excerpt(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _refresh_task_evidence(
    task: dict[str, Any],
    spec: Mapping[str, Any],
    inventory_by_role: Mapping[str, Mapping[str, Any]],
    active_pointers: Mapping[str, Mapping[str, Any]],
) -> bool:
    has_evidence = "evidence" in spec
    has_targets = "targets" in spec
    if has_evidence != has_targets:
        raise WorkflowPreparationError(f"Task {task.get('task_id')} must supply both refreshed evidence and targets.")
    if not has_evidence:
        has_mutating = any(
            isinstance(row, Mapping) and row.get("action") in _MUTATING
            for row in task.get("targets", [])
        )
        existing_unit_specs = spec.get(
            "response_unit_specs", task.get("response_unit_specs", [])
        )
        has_no_change_units = isinstance(existing_unit_specs, list) and any(
            isinstance(row, Mapping) and row.get("disposition") == "no_change"
            for row in existing_unit_specs
        )
        if task.get("status") == "recheck_required" and (has_mutating or has_no_change_units):
            raise WorkflowPreparationError(
                f"Task {task.get('task_id')} requires refreshed evidence/targets and no-change anchors from current active documents."
            )
        return False

    raw_evidence, raw_targets = spec.get("evidence"), spec.get("targets")
    if not isinstance(raw_evidence, list) or not raw_evidence or any(not isinstance(row, Mapping) for row in raw_evidence) or not isinstance(raw_targets, list):
        raise WorkflowPreparationError("Refreshed evidence must be a non-empty array and targets must be an array.")
    old_rows = task.get("evidence", [])
    if not isinstance(old_rows, list):
        raise WorkflowPreparationError(f"Task {task.get('task_id')} evidence must be an array.")
    known_ids = {str(row.get("evidence_id")) for row in old_rows if isinstance(row, Mapping) and row.get("evidence_id") is not None}
    fresh: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_evidence):
        row = dict(raw)
        allowed = {"evidence_id","document","part","p_idx","para_id","source_comment_id","source_excerpt","evidence_type","match_status"}
        if set(row) - allowed:
            raise WorkflowPreparationError(f"Refreshed evidence[{idx}] contains unsupported fields.")
        evidence_id = row.get("evidence_id")
        role = _role(row.get("document"))
        part, p_idx = row.get("part"), row.get("p_idx")
        excerpt = row.get("source_excerpt")
        if not isinstance(evidence_id, str) or not evidence_id.strip() or evidence_id in known_ids:
            raise WorkflowPreparationError("Refreshed evidence IDs must be new, unique and non-empty.")
        known_ids.add(evidence_id)
        if (
            role not in {"Reviewers","Manuscript","SI"}
            or not isinstance(part,str) or not part.startswith("word/")
            or not isinstance(p_idx,int) or isinstance(p_idx,bool) or p_idx < 0
            or not isinstance(excerpt,str) or not excerpt.strip()
            or row.get("evidence_type") not in {"reviewer_comment","native_anchor","paragraph","caption","reference","table","figure","audit_finding"}
            or row.get("match_status") not in {"confirmed","candidate","ambiguous","unresolved","not_applicable"}
        ):
            raise WorkflowPreparationError(f"Refreshed evidence[{idx}] has invalid fields.")
        if row.get("evidence_type") == "reviewer_comment":
            if part != "word/comments.xml" or row.get("source_comment_id") is None:
                raise WorkflowPreparationError(f"Reviewer comment evidence {evidence_id} requires comments.xml and source_comment_id.")
            task_comment_ids = {str(value) for value in task.get("source_comment_ids", [])}
            if str(row.get("source_comment_id")) not in task_comment_ids:
                raise WorkflowPreparationError(
                    f"Reviewer evidence {evidence_id} refers to a comment outside task {task.get('task_id')}."
                )
            comments = inventory_by_role.get("Reviewers", {}).get("comments", [])
            if not any(
                isinstance(comment, Mapping)
                and str(comment.get("id")) == str(row.get("source_comment_id"))
                and _normalised_excerpt(comment.get("comment")) == _normalised_excerpt(excerpt)
                for comment in comments
            ):
                raise WorkflowPreparationError(f"Reviewer evidence {evidence_id} is absent from the current Reviewers inventory.")
        else:
            report = inventory_by_role.get(role)
            paragraphs = report.get("paragraphs", []) if isinstance(report, Mapping) else []
            matched = any(
                isinstance(paragraph, Mapping)
                and paragraph.get("part") == part
                and paragraph.get("p_idx") == p_idx
                and (
                    _normalised_excerpt(excerpt) in _normalised_excerpt(paragraph.get("text"))
                    or _normalised_excerpt(paragraph.get("text")) in _normalised_excerpt(excerpt)
                )
                for paragraph in paragraphs
            ) if isinstance(paragraphs, list) else False
            if not matched or row.get("match_status") != "confirmed":
                raise WorkflowPreparationError(f"Evidence {evidence_id} does not match current {role} inventory or is not confirmed.")
        fresh.append(row)

    evidence_by_id = {row["evidence_id"]: row for row in fresh}
    targets: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_targets):
        if not isinstance(raw, Mapping):
            raise WorkflowPreparationError(f"Refreshed targets[{idx}] must be an object.")
        target = dict(raw)
        if set(target) - {"document","part","p_idx","para_id","action","evidence_ids"}:
            raise WorkflowPreparationError(f"Refreshed targets[{idx}] contains unsupported fields.")
        role, part, p_idx = _role(target.get("document")), target.get("part"), target.get("p_idx")
        evidence_ids = target.get("evidence_ids")
        action = target.get("action")
        if (
            role not in {"Manuscript","SI"}
            or not isinstance(part,str) or not part.startswith("word/")
            or not isinstance(p_idx,int) or isinstance(p_idx,bool) or p_idx < 0
            or not isinstance(action,str) or action not in (_MUTATING | {"keep","inspect_only","reply_only"})
            or not isinstance(evidence_ids,list) or not evidence_ids
            or any(not isinstance(item,str) or item not in evidence_by_id for item in evidence_ids)
        ):
            raise WorkflowPreparationError(f"Refreshed target[{idx}] has invalid fields/evidence.")
        for evidence_id in evidence_ids:
            row = evidence_by_id[evidence_id]
            if _role(row.get("document")) != role or row.get("part") != part or row.get("p_idx") != p_idx or row.get("match_status") != "confirmed":
                raise WorkflowPreparationError(f"Refreshed target[{idx}] is not bound to confirmed evidence at the same document location.")
        targets.append(target)

    if not targets and task.get("task_type") != "response_only":
        raise WorkflowPreparationError("Refreshed document task must retain at least one target.")
    task["evidence"] = [*old_rows, *fresh]
    task["targets"] = targets
    task["target_documents"] = list(dict.fromkeys(_role(row.get("document")) for row in targets))

    versions: dict[tuple[str,str,str], dict[str,str]] = {}
    for row in task.get("source_versions", []):
        if isinstance(row, Mapping):
            key=(str(row.get("role","")),str(Path(str(row.get("file",""))).expanduser().resolve()),str(row.get("sha256","")))
            versions[key]={"role":key[0],"file":key[1],"sha256":key[2]}
    for role in task["target_documents"]:
        pointer=active_pointers.get(role)
        if pointer is None:
            raise WorkflowPreparationError(f"No active pointer for refreshed role {role}.")
        key=(role,str(Path(pointer["file"]).expanduser().resolve()),pointer["sha256"])
        versions[key]={"role":role,"file":key[1],"sha256":pointer["sha256"]}
    task["source_versions"]=[versions[key] for key in sorted(versions)]

    if task.get("target_refs"):
        refs=spec.get("target_refs")
        if not isinstance(refs,list) or not refs or any(not isinstance(row,Mapping) for row in refs):
            raise WorkflowPreparationError(f"Task {task.get('task_id')} requires refreshed target_refs.")
        task["target_refs"]=[dict(row) for row in refs]
    elif "target_refs" in spec:
        refs=spec.get("target_refs")
        if not isinstance(refs,list) or any(not isinstance(row,Mapping) for row in refs):
            raise WorkflowPreparationError("target_refs must be an array of objects.")
        task["target_refs"]=[dict(row) for row in refs]

    from .workflow_engine import workflow_tasks_from_decision_plan
    adapted=workflow_tasks_from_decision_plan([task],target_refs_by_task={task["task_id"]:task.get("target_refs",[])})
    if not adapted:
        raise WorkflowPreparationError(f"Task {task.get('task_id')} could not rebuild target groups.")
    task["target_groups"]=adapted[0]["target_groups"]
    for field in ("risk_tier", "risk_policy_version", "risk_reasons"):
        task[field] = copy.deepcopy(adapted[0][field])
    return True


def prepare_workflow_execution(
    workflow: Mapping[str, Any],
    execution_scope: Mapping[str, Any],
    inventory: Any,
    *,
    workflow_path: str | Path,
    execution_scope_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Merge exact Patch operands without authorizing tasks or editing documents."""
    if not isinstance(execution_scope, Mapping):
        raise WorkflowPreparationError("Execution scope root must be a JSON object.")
    allowed_root_fields = {"schema_version", "revision_round_id", "workflow_ledger_sha256", "identity_mode", "tasks", "reviewer_map"}
    if set(execution_scope) - allowed_root_fields:
        raise WorkflowPreparationError("Execution scope contains unsupported root fields.")
    if not isinstance(inventory, (Mapping, list)):
        raise WorkflowPreparationError("Workspace inventory must be a JSON document-record array.")
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise WorkflowPreparationError("Workflow Ledger is invalid:\n- " + "\n- ".join(errors))
    if execution_scope.get("schema_version") != "1.0":
        raise WorkflowPreparationError("Execution scope schema_version must be '1.0'.")
    if execution_scope.get("revision_round_id") != workflow.get("revision_round_id"):
        raise WorkflowPreparationError("Execution scope belongs to a different review round.")

    identity_mode = execution_scope.get("identity_mode", "stable_target_ref")
    if identity_mode not in {"stable_target_ref", "legacy_p_idx_explicit"}:
        raise WorkflowPreparationError("Execution Scope identity_mode must be stable_target_ref or legacy_p_idx_explicit.")

    workflow_file = Path(workflow_path).expanduser().resolve()
    scope_file = Path(execution_scope_path).expanduser().resolve()
    if workflow_file == scope_file:
        raise WorkflowPreparationError("Workflow Ledger and execution scope must be different files.")
    workflow_disk = json.loads(workflow_file.read_text(encoding="utf-8"))
    if workflow_disk != json.loads(json.dumps(workflow, ensure_ascii=False)):
        raise WorkflowPreparationError("In-memory Workflow Ledger differs from the on-disk file.")
    workflow_sha = sha256_file(workflow_file)
    if execution_scope.get("workflow_ledger_sha256") != workflow_sha:
        raise WorkflowPreparationError("Execution scope is stale: workflow_ledger_sha256 does not match the current Workflow Ledger.")

    pointers = load_active_document_pointers(workflow)
    _inventory_hashes, authors, inventory_by_role = _read_inventory_sources(inventory, workflow, pointers)
    base_tasks = workflow.get("tasks", [])
    by_id = {
        row.get("task_id"): row
        for row in base_tasks
        if isinstance(row, Mapping) and isinstance(row.get("task_id"), str)
    }
    rows = execution_scope.get("tasks")
    if not isinstance(rows, list) or not rows:
        raise WorkflowPreparationError("Execution scope tasks must be a non-empty array.")
    explicit_reviewers = execution_scope.get("reviewer_map", {})
    if not isinstance(explicit_reviewers, Mapping):
        raise WorkflowPreparationError("Execution scope reviewer_map must be an object.")
    if set(explicit_reviewers) - {row.get("task_id") for row in rows if isinstance(row, Mapping)}:
        raise WorkflowPreparationError("Reviewer map contains task IDs absent from the execution scope.")

    prepared = copy.deepcopy(dict(workflow))
    prepared_by_id = {
        row.get("task_id"): row
        for row in prepared.get("tasks", [])
        if isinstance(row, dict)
    }
    reviewer_map: dict[str, Any] = {}
    scope_ids: set[str] = set()
    operation_ids: set[str] = set()
    patch_ids: set[str] = set()
    planned_root = workflow_file.parent / "runs" / ".planned"

    for row_index, spec in enumerate(rows):
        if not isinstance(spec, Mapping):
            raise WorkflowPreparationError(f"Execution scope tasks[{row_index}] must be an object.")
        if set(spec) - _TASK_FIELDS:
            raise WorkflowPreparationError(f"Execution scope tasks[{row_index}] contains unsupported fields.")
        task_id = spec.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or task_id in scope_ids:
            raise WorkflowPreparationError(f"Execution scope tasks[{row_index}].task_id must be unique and non-empty.")
        scope_ids.add(task_id)
        if task_id not in by_id:
            raise WorkflowPreparationError(f"Execution scope references unknown task {task_id}.")
        source_task = by_id[task_id]
        task = prepared_by_id[task_id]
        if task.get("status") not in {"planned", "recheck_required"}:
            raise WorkflowPreparationError(
                f"Task {task_id} is {task.get('status')!r}; only planned/recheck_required tasks can be prepared."
            )
        if task.get("requires_user_approval") is not True:
            raise WorkflowPreparationError(f"Task {task_id} does not require explicit user approval.")
        refreshed = _refresh_task_evidence(task, spec, inventory_by_role, pointers)
        has_mutating = any(
            isinstance(target, Mapping) and target.get("action") in _MUTATING
            for target in task.get("targets", [])
        )
        current_unit_specs = spec.get(
            "response_unit_specs", task.get("response_unit_specs", [])
        )
        has_no_change_units = isinstance(current_unit_specs, list) and any(
            isinstance(row, Mapping) and row.get("disposition") == "no_change"
            for row in current_unit_specs
        )
        if (
            task.get("status") == "recheck_required"
            and (has_mutating or has_no_change_units)
            and not refreshed
        ):
            raise WorkflowPreparationError(
                f"Task {task_id} must be rebound to current active evidence before renewed author approval."
            )
        comment_ids = [str(value) for value in task.get("source_comment_ids", [])]
        raw_scope_author_ref = spec.get("author_decision_ref")
        workflow_author_ref = task.get("author_decision_ref")
        reauthorizing_numbering_task = (
            task.get("status") == "recheck_required"
            and task.get("task_type") == "global_numbering"
        )
        if reauthorizing_numbering_task:
            if raw_scope_author_ref is None:
                raise WorkflowPreparationError(
                    f"Task {task_id} requires a fresh package-numbering author approval after recheck."
                )
            if workflow_author_ref is not None and raw_scope_author_ref == workflow_author_ref:
                raise WorkflowPreparationError(
                    f"Task {task_id} cannot reuse its pre-recheck package-numbering author approval."
                )
        elif (
            raw_scope_author_ref is not None
            and workflow_author_ref is not None
            and raw_scope_author_ref != workflow_author_ref
        ):
            raise WorkflowPreparationError(
                f"Task {task_id} execution-scope author_decision_ref differs from the Workflow Ledger."
            )
        raw_author_ref = raw_scope_author_ref if raw_scope_author_ref is not None else workflow_author_ref
        task_author_ref = None
        if raw_author_ref is not None:
            try:
                task_author_ref = validate_author_decision_ref(raw_author_ref)
            except (RevisionModelError, TypeError, ValueError) as exc:
                raise WorkflowPreparationError(
                    f"Task {task_id} author_decision_ref is invalid: {exc}"
                ) from exc
            if task.get("task_type") != "global_numbering":
                raise WorkflowPreparationError(
                    f"Task {task_id} may carry package-numbering author provenance only as global_numbering."
                )
            invalidated_approvals = task.get(
                "invalidated_package_numbering_approval_hashes", []
            )
            if task_author_ref["approval_sha256"] in invalidated_approvals:
                raise WorkflowPreparationError(
                    f"Task {task_id} author_decision_ref is a previously invalidated package-numbering approval and cannot be reused."
                )
            task["author_decision_ref"] = copy.deepcopy(task_author_ref)
        targets = task.get("targets", [])
        if not isinstance(targets, list):
            raise WorkflowPreparationError(f"Task {task_id} targets must be an array.")
        mutating_targets = [
            target for target in targets
            if isinstance(target, Mapping) and target.get("action") in _MUTATING
        ]
        raw_ops = spec.get("patch_operations", [])
        if not isinstance(raw_ops, list) or len(raw_ops) != len(mutating_targets):
            raise WorkflowPreparationError(
                f"Task {task_id} requires exactly one PatchOperation for each of its {len(mutating_targets)} mutating target(s)."
            )
        if task.get("task_type") == "response_only" and raw_ops:
            raise WorkflowPreparationError(f"Response-only task {task_id} cannot contain PatchOperations.")
        if task.get("task_type") == "global_numbering" and raw_ops and task_author_ref is None:
            raise WorkflowPreparationError(
                f"Global-numbering task {task_id} requires a hash-bound author_decision_ref before PatchOperations can be prepared."
            )

        operations: list[dict[str, Any]] = []
        matched_targets: set[int] = set()
        linked_comments: set[str] = set()
        for op_index, raw in enumerate(raw_ops):
            if not isinstance(raw, Mapping) or set(raw) - _OPERATION_FIELDS:
                raise WorkflowPreparationError(f"Task {task_id} PatchOperation {op_index} has invalid fields.")
            operation = dict(raw)
            raw_operation_author_ref = operation.get("author_decision_ref")
            if task_author_ref is not None:
                if raw_operation_author_ref is not None and raw_operation_author_ref != task_author_ref:
                    raise WorkflowPreparationError(
                        f"PatchOperation {operation.get('operation_id')} author_decision_ref differs from Task {task_id}."
                    )
                operation["author_decision_ref"] = copy.deepcopy(task_author_ref)
            elif raw_operation_author_ref is not None:
                raise WorkflowPreparationError(
                    f"PatchOperation {operation.get('operation_id')} has author_decision_ref but Task {task_id} has no matching task-level approval."
                )
            op_id = operation.get("operation_id")
            if not isinstance(op_id, str) or not op_id.strip() or op_id in operation_ids:
                raise WorkflowPreparationError(f"PatchOperation {task_id}/{op_index} needs a globally unique operation_id.")
            operation_ids.add(op_id)
            role = _role(operation.get("document"))
            part = operation.get("part")
            p_idx = operation.get("p_idx")
            if role not in {"Manuscript", "SI"} or not isinstance(part, str) or not part.startswith("word/"):
                raise WorkflowPreparationError(f"PatchOperation {op_id} must target Manuscript/SI and a Word package part.")
            if not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0:
                raise WorkflowPreparationError(f"PatchOperation {op_id} requires a non-negative p_idx.")
            evidence_ids = operation.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise WorkflowPreparationError(f"PatchOperation {op_id} requires target evidence_ids.")
            known_evidence = {
                str(item.get("evidence_id"))
                for item in task.get("evidence", [])
                if isinstance(item, Mapping) and item.get("evidence_id") is not None
            }
            if not set(evidence_ids).issubset(known_evidence):
                raise WorkflowPreparationError(f"PatchOperation {op_id} refers to unknown task evidence.")
            candidates = [
                (target_index, target)
                for target_index, target in enumerate(mutating_targets)
                if _role(target.get("document")) == role
                and target.get("part") == part
                and target.get("p_idx") == p_idx
                and set(target.get("evidence_ids", [])).issubset(set(evidence_ids))
            ]
            if len(candidates) != 1:
                raise WorkflowPreparationError(f"PatchOperation {op_id} matches {len(candidates)} evidence-linked targets; expected exactly one.")
            target_index, target = candidates[0]
            target_evidence_ids = [str(value) for value in target.get("evidence_ids", [])]
            evidence_by_id = {
                str(item.get("evidence_id")): item
                for item in task.get("evidence", [])
                if isinstance(item, Mapping) and item.get("evidence_id") is not None
            }
            target_evidence_rows = [
                evidence_by_id[evidence_id]
                for evidence_id in target_evidence_ids
                if evidence_id in evidence_by_id
            ]
            if (
                len(target_evidence_rows) != len(target_evidence_ids)
                or any(
                    _role(row.get("document")) != role
                    or row.get("part") != part
                    or row.get("p_idx") != p_idx
                    or row.get("match_status") != "confirmed"
                    for row in target_evidence_rows
                )
            ):
                raise WorkflowPreparationError(
                    f"PatchOperation {op_id} target evidence must be confirmed at the exact role/part/paragraph."
                )
            find_excerpt = _normalised_excerpt(operation.get("find"))
            if not find_excerpt or not any(
                find_excerpt in _normalised_excerpt(row.get("source_excerpt"))
                for row in target_evidence_rows
            ):
                raise WorkflowPreparationError(
                    f"PatchOperation {op_id} find text must be present in one exact target evidence excerpt."
                )
            if target_index in matched_targets:
                raise WorkflowPreparationError(f"Task {task_id} maps multiple operations to the same Decision target.")
            matched_targets.add(target_index)

            # FormatChange sources are resolved from the one-time source profile, never guessed from
            # the replaced text or copied from a neighbouring Run.
            raw_format_operations = operation.get("format_operations", [])
            if raw_format_operations:
                if not isinstance(raw_format_operations, list) or not raw_format_operations:
                    raise WorkflowPreparationError(f"PatchOperation {op_id} format_operations must be a non-empty array.")
                profile = inventory_by_role.get(role, {}).get("semantic_format_profile")
                if not isinstance(profile, Mapping):
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} needs the initial semantic/format profile for {role}; rerun the round inventory."
                    )
                if profile.get("source_sha256") != pointers[role].get("sha256"):
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} semantic/format profile is stale for {role}."
                    )
                authorization = operation.get("format_authorization")
                if not isinstance(authorization, Mapping):
                    raise WorkflowPreparationError(f"PatchOperation {op_id} requires format_authorization.")
                if authorization.get("semantic_profile_sha256") != profile.get("profile_fingerprint"):
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} semantic_profile_sha256 does not match the current {role} profile."
                    )
                profile_zone = authorization.get("format_zone")
                paragraph_regions = profile.get("paragraph_regions", [])
                target_region = next((
                    row for row in paragraph_regions
                    if isinstance(row, Mapping)
                    and row.get("part") == part and row.get("p_idx") == p_idx
                ), None)
                if not isinstance(target_region, Mapping):
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} target is missing from the semantic profile."
                    )
                target_zone = target_region.get("zone")
                if target_zone not in {"body", "references"} or profile_zone != target_zone:
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} format source zone must match the target zone ({target_zone})."
                    )
                occurrences = [
                    item for key in ("body_occurrences", "reference_occurrences")
                    for item in profile.get(key, [])
                    if isinstance(item, Mapping)
                ]
                occurrence_by_id = {
                    str(item.get("occurrence_id")): item
                    for item in occurrences
                    if isinstance(item.get("occurrence_id"), str)
                }
                resolved_format_operations = []
                for format_index, raw_format in enumerate(raw_format_operations):
                    if not isinstance(raw_format, Mapping):
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} format_operations[{format_index}] must be an object."
                        )
                    source_id = raw_format.get("source_occurrence_id")
                    source_occurrence = occurrence_by_id.get(str(source_id))
                    if source_occurrence is None:
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} format_operations[{format_index}] references an unknown source occurrence."
                        )
                    if source_occurrence.get("zone") != target_zone:
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} cannot borrow formatting across body/reference zones."
                        )
                    if source_occurrence.get("paragraph_style", "") != target_region.get("paragraph_style", ""):
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} format source must use the target paragraph style; "
                            "a different paragraph-style inheritance chain is not silently copied."
                        )
                    unsupported_properties = source_occurrence.get("unsupported_format_properties", [])
                    if unsupported_properties:
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} source occurrence contains character properties "
                            f"the current deterministic FormatChange adapter cannot reproduce: {unsupported_properties}."
                        )
                    source_properties = source_occurrence.get("format_properties")
                    if not isinstance(source_properties, Mapping) or not source_properties:
                        raise WorkflowPreparationError(
                            f"PatchOperation {op_id} format source occurrence has no directly-applied character properties."
                        )
                    _validate_vertical_alignment_source_range(
                        op_id, format_index, raw_format, source_occurrence, source_properties
                    )
                    resolved = dict(raw_format)
                    # Ignore model-supplied properties; the exact source occurrence is authoritative.
                    resolved["properties"] = copy.deepcopy(dict(source_properties))
                    resolved_format_operations.append(resolved)
                operation["format_operations"] = resolved_format_operations

            task_target_refs = task.get("target_refs", [])
            if not isinstance(task_target_refs, list):
                raise WorkflowPreparationError(f"Task {task_id} target_refs must be an array.")
            document_identities = [
                row for row in workflow.get("documents", [])
                if isinstance(row, Mapping) and _role(row.get("role")) == role
            ]
            if task_target_refs:
                if len(document_identities) != 1:
                    raise WorkflowPreparationError(
                        f"Task {task_id} has TargetRefs but no unique registered document identity for {role}."
                    )
                identity = document_identities[0]
                stable_matches = []
                for ref in task_target_refs:
                    if not isinstance(ref, Mapping):
                        continue
                    hint = ref.get("location_hint")
                    if (
                        ref.get("document_id") == identity.get("document_id")
                        and ref.get("revision_id") == identity.get("revision_id")
                        and ref.get("part") == part
                        and isinstance(hint, Mapping)
                        and hint.get("p_idx") == p_idx
                    ):
                        stable_matches.append(ref)
                supplied_ref = operation.get("target_ref")
                if supplied_ref is not None:
                    if not isinstance(supplied_ref, Mapping):
                        raise WorkflowPreparationError(f"PatchOperation {op_id} target_ref must be an object.")
                    stable_matches = [row for row in stable_matches if dict(row) == dict(supplied_ref)]
                if len(stable_matches) != 1:
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} must bind exactly one registered TargetRef at {role}/{part}/p_idx={p_idx}; found {len(stable_matches)}."
                    )
                operation["target_ref"] = copy.deepcopy(dict(stable_matches[0]))
                operation["target_identity_mode"] = "stable_target_ref"
            else:
                if document_identities or workflow.get("object_registry"):
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} is missing its registered TargetRef; rebind the task identity manifest instead of falling back to p_idx."
                    )
                if identity_mode != "legacy_p_idx_explicit":
                    raise WorkflowPreparationError(
                        f"PatchOperation {op_id} has no stable TargetRef; set identity_mode=legacy_p_idx_explicit only for an intentional migration."
                    )
                operation["target_identity_mode"] = "legacy_p_idx_explicit"

            sources = operation.get("source_comment_ids", [])
            if not isinstance(sources, list):
                raise WorkflowPreparationError(f"PatchOperation {op_id} source_comment_ids must be an array.")
            source_ids = [str(value) for value in sources]
            if not source_ids and task_author_ref is None:
                raise WorkflowPreparationError(
                    f"PatchOperation {op_id} requires reviewer comment IDs or a hash-bound global-numbering author decision."
                )
            if len(source_ids) != len(set(source_ids)) or not set(source_ids).issubset(set(comment_ids)):
                raise WorkflowPreparationError(f"PatchOperation {op_id} has duplicate/out-of-scope source_comment_ids.")
            linked_comments.update(source_ids)

            pointer = pointers.get(role)
            if pointer is None:
                raise WorkflowPreparationError(f"No active document pointer is configured for {role}.")
            operation["document"] = role
            operation["source_comment_ids"] = source_ids
            operation["input_file"] = pointer["file"]
            operation["output_file"] = str(planned_root / _slug(task_id) / f"{_slug(role)}.marked.docx")
            operation["source_sha256"] = pointer["sha256"]
            try:
                PatchOperation.from_mapping(operation, fallback_id=op_id)
            except (RevisionModelError, TypeError, ValueError) as exc:
                raise WorkflowPreparationError(f"PatchOperation {op_id} is invalid: {exc}") from exc
            operations.append(operation)
        if matched_targets != set(range(len(mutating_targets))):
            raise WorkflowPreparationError(f"Task {task_id} does not cover every mutating Decision target exactly once.")

        patch_id = spec.get("patch_id")
        response = spec.get("proposed_response", "")
        if operations:
            if not isinstance(patch_id, str) or not patch_id.strip() or patch_id in patch_ids:
                raise WorkflowPreparationError(f"Executable task {task_id} requires a unique patch_id.")
            patch_ids.add(patch_id)
            if not isinstance(response, str) or not response.strip():
                raise WorkflowPreparationError(f"Executable task {task_id} requires reviewed proposed_response wording.")
        elif patch_id is not None:
            raise WorkflowPreparationError(
                f"Task {task_id} has no document operations; omit patch_id rather than creating an empty Patch."
            )
        if not isinstance(response, str):
            raise WorkflowPreparationError(f"Task {task_id} proposed_response must be a string.")
        if spec.get("supersedes_patch_id") is not None and not operations:
            raise WorkflowPreparationError(
                f"Task {task_id} cannot supersede a Patch without new document operations."
            )

        raw_specs = spec.get("response_unit_specs", [])
        if not isinstance(raw_specs, list) or any(not isinstance(item, Mapping) for item in raw_specs):
            raise WorkflowPreparationError(f"Task {task_id} response_unit_specs must be an array of objects.")
        response_specs = [dict(item) for item in raw_specs]
        spec_comments = [str(item.get("comment_id", "")) for item in response_specs]
        if len(spec_comments) != len(set(spec_comments)) or not set(spec_comments).issubset(set(comment_ids)):
            raise WorkflowPreparationError(f"Task {task_id} response_unit_specs contains duplicate/unknown comment IDs.")
        evidence_by_id = {
            str(item.get("evidence_id")): item
            for item in task.get("evidence", [])
            if isinstance(item, Mapping) and item.get("evidence_id") is not None
        }
        for item in response_specs:
            allowed_spec_fields = {"comment_id", "disposition", "location_requirement", "resolution_reason", "anchor_evidence_id"}
            if set(item) - allowed_spec_fields:
                raise WorkflowPreparationError(f"Task {task_id} ResponseUnit spec contains unsupported fields.")
            comment_id = str(item.get("comment_id", ""))
            disposition = item.get("disposition")
            requirement = item.get("location_requirement")
            if disposition not in _DISPOSITIONS or requirement not in _LOCATION_REQUIREMENTS:
                raise WorkflowPreparationError(f"Task {task_id} ResponseUnit spec for {comment_id} has unsupported states.")
            linked = comment_id in linked_comments
            if disposition == "revised" and not linked:
                raise WorkflowPreparationError(f"Task {task_id} marks {comment_id} revised without a linked PatchOperation.")
            if linked and disposition != "revised":
                raise WorkflowPreparationError(f"Task {task_id} links a PatchOperation to {comment_id} but classifies it as {disposition}.")
            if "resolution_reason" in item and not isinstance(item.get("resolution_reason"), str):
                raise WorkflowPreparationError(f"Task {task_id} ResponseUnit resolution_reason must be a string.")
            if disposition == "reply_only" and requirement != "not_required":
                raise WorkflowPreparationError(f"Task {task_id} reply_only {comment_id} must use not_required location.")
            if disposition == "no_change":
                anchor_id = item.get("anchor_evidence_id")
                if anchor_id not in evidence_by_id or requirement not in {"existing_anchor_required", "manual_required"}:
                    raise WorkflowPreparationError(f"Task {task_id} no_change {comment_id} needs a valid anchor_evidence_id.")
                if (
                    task.get("status") == "recheck_required"
                    and refreshed
                    and anchor_id not in {
                        row.get("evidence_id") for row in spec.get("evidence", [])
                        if isinstance(row, Mapping)
                    }
                ):
                    raise WorkflowPreparationError(
                        f"no_change {comment_id} must use a newly refreshed anchor_evidence_id from the current active document."
                    )
            if disposition == "clarification_needed" and requirement == "not_required":
                raise WorkflowPreparationError(f"Task {task_id} clarification_needed {comment_id} cannot use not_required location.")
        uncovered = set(comment_ids) - linked_comments - set(spec_comments)
        if uncovered and task.get("task_type") != "response_only":
            raise WorkflowPreparationError(
                f"Task {task_id} has comments without a PatchOperation or explicit ResponseUnit classification: "
                + ", ".join(sorted(uncovered))
            )
        if any(str(item.get("disposition")) == "reply_only" and str(item.get("comment_id")) in linked_comments for item in response_specs):
            raise WorkflowPreparationError(f"Task {task_id} marks a comment reply_only while a PatchOperation is linked to it.")

        task["patch_operations"] = operations
        task["proposed_response"] = response
        task["response_unit_specs"] = response_specs
        supersedes_patch_id = spec.get("supersedes_patch_id")
        if supersedes_patch_id is not None:
            if not isinstance(supersedes_patch_id, str) or not supersedes_patch_id.strip():
                raise WorkflowPreparationError(f"Task {task_id} supersedes_patch_id must be non-empty when supplied.")
            task["supersedes_patch_id"] = supersedes_patch_id.strip()
        if patch_id:
            task["patch_id"] = patch_id
        else:
            task.pop("patch_id", None)

        # Recompute risk from the exact author-reviewed operations. This may
        # raise a task from Tier 0 to Tier 1 when numbers, units, formulas,
        # citations, references or formatting enter the executable operands.
        from .safety_policy import assess_task_risk
        task.update(assess_task_risk(task))
        for field in (
            "high_risk_review_confirmed", "high_risk_reviewed_by",
            "high_risk_reviewed_at_utc", "high_risk_review_note",
        ):
            task.pop(field, None)

        reviewer_map[task_id] = _reviewer_spec(
            task_id,
            comment_ids,
            explicit_reviewers.get(task_id),
            authors,
        )

    prepared_errors = validate_workflow_ledger(prepared)
    if prepared_errors:
        raise WorkflowPreparationError("Prepared Workflow Ledger is invalid:\n- " + "\n- ".join(prepared_errors))
    report = {
        "schema_version": "1.0",
        "status": "prepared_pending_author_approval",
        "revision_round_id": workflow.get("revision_round_id"),
        "workflow_ledger_source": str(workflow_file),
        "workflow_ledger_source_sha256": workflow_sha,
        "execution_scope_source": str(scope_file),
        "execution_scope_sha256": sha256_file(scope_file),
        "prepared_task_ids": list(scope_ids),
        "refreshed_task_ids": [
            str(row["task_id"]) for row in rows
            if isinstance(row, Mapping)
            and "evidence" in row and "targets" in row
            and by_id.get(row.get("task_id"), {}).get("status") == "recheck_required"
        ],
        "active_document_versions": {
            role: {"file": row["file"], "sha256": row["sha256"], "batch_id": row.get("batch_id")}
            for role, row in pointers.items()
        },
        "reviewer_map": reviewer_map,
        "note": "This command does not authorize Workflow tasks or edit DOCX files. build-revision-ledger replaces planned output placeholders with immutable batch output paths.",
    }
    return prepared, reviewer_map, report

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .batch_store import load_active_document_pointers
from .identity import sha256_file
from .revision_models import PatchOperation, RevisionModelError
from .workflow_engine import validate_workflow_ledger


class WorkflowPreparationError(ValueError):
    """An author-reviewed execution scope cannot be safely merged."""


_MUTATING = {"replace", "add", "remove", "renumber", "reference_update"}
_OPERATION_FIELDS = {
    "operation_id", "document", "part", "p_idx", "find", "replace",
    "location", "evidence_ids", "source_comment_ids", "location_phrase", "target_ref",
}
_TASK_FIELDS = {
    "task_id", "patch_id", "proposed_response", "patch_operations",
    "response_unit_specs", "supersedes_patch_id",
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


def _read_inventory_sources(inventory: Any, workflow: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
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
    reviewer_docs = 0
    for doc in docs:
        if not isinstance(doc, Mapping):
            continue
        role = _role(doc.get("role"))
        if role not in {"Reviewers", "Manuscript", "SI"}:
            continue
        source = workflow_sources.get(role)
        digest = doc.get("source_sha256") or doc.get("sha256")
        path_value = doc.get("file")
        if (
            not isinstance(source, Mapping)
            or not isinstance(path_value, str)
            or not isinstance(digest, str)
            or Path(path_value).expanduser().resolve() != Path(str(source.get("file", ""))).expanduser().resolve()
            or digest != source.get("sha256")
        ):
            raise WorkflowPreparationError(
                f"Inventory source identity for {role} does not match the Workflow Ledger."
            )
        hashes[role] = digest
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
    return hashes, authors


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


def prepare_workflow_execution(
    workflow: Mapping[str, Any],
    execution_scope: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    workflow_path: str | Path,
    execution_scope_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Merge exact Patch operands without authorizing tasks or editing documents."""
    if not isinstance(execution_scope, Mapping):
        raise WorkflowPreparationError("Execution scope root must be a JSON object.")
    allowed_root_fields = {"schema_version", "revision_round_id", "workflow_ledger_sha256", "tasks", "reviewer_map"}
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

    _inventory_hashes, authors = _read_inventory_sources(inventory, workflow)
    pointers = load_active_document_pointers(workflow)
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
        comment_ids = [str(value) for value in task.get("source_comment_ids", [])]
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

        operations: list[dict[str, Any]] = []
        matched_targets: set[int] = set()
        linked_comments: set[str] = set()
        for op_index, raw in enumerate(raw_ops):
            if not isinstance(raw, Mapping) or set(raw) - _OPERATION_FIELDS:
                raise WorkflowPreparationError(f"Task {task_id} PatchOperation {op_index} has invalid fields.")
            operation = dict(raw)
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
            target_index, _target = candidates[0]
            if target_index in matched_targets:
                raise WorkflowPreparationError(f"Task {task_id} maps multiple operations to the same Decision target.")
            matched_targets.add(target_index)

            sources = operation.get("source_comment_ids")
            if not isinstance(sources, list) or not sources:
                raise WorkflowPreparationError(f"PatchOperation {op_id} requires source_comment_ids.")
            source_ids = [str(value) for value in sources]
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
        "reviewer_map": reviewer_map,
        "note": "This command does not authorize Workflow tasks or edit DOCX files. build-revision-ledger replaces planned output placeholders with immutable batch output paths.",
    }
    return prepared, reviewer_map, report

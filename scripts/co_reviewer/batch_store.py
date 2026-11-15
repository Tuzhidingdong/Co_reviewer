# -*- coding: utf-8 -*-
"""Append-only batch storage and atomic active-document pointer publication."""
from __future__ import annotations

import json
import copy
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import sha256_file
from .workflow_engine import validate_workflow_ledger


class BatchStoreError(ValueError):
    """Raised when a revision batch cannot be safely prepared or published."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_aware_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _safe_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return result or "document"


def write_json_new(path: str | Path, payload: Mapping[str, Any]) -> str:
    """Atomically create a new JSON artifact without ever replacing an existing file."""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        temporary = Path(temporary_name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise BatchStoreError(
                f"Append-only batch artifact already exists; refusing to overwrite: {target}"
            ) from exc
        return sha256_file(target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def load_active_document_pointers(workflow: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the current active file/hash per role, bootstrapping from source_files."""
    active = workflow.get("active_documents")
    if active is None:
        sources = workflow.get("source_files", [])
        if not isinstance(sources, list):
            raise BatchStoreError("Workflow Ledger source_files must be an array.")
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for row in sources:
            if not isinstance(row, Mapping):
                continue
            role, file_path, digest = row.get("role"), row.get("file"), row.get("sha256")
            if isinstance(role, str) and isinstance(file_path, str) and isinstance(digest, str):
                grouped.setdefault(role, []).append(row)
        active = {}
        for role, rows in grouped.items():
            if len(rows) != 1:
                raise BatchStoreError(
                    f"Cannot initialize active pointer for {role}: expected exactly one source file, found {len(rows)}."
                )
            row = rows[0]
            active[role] = {
                "file": row["file"],
                "sha256": row["sha256"],
                "batch_id": None,
                "updated_at_utc": workflow.get("created_at_utc") or _timestamp(),
            }
    if not isinstance(active, Mapping):
        raise BatchStoreError("Workflow Ledger active_documents must be an object.")
    result: dict[str, dict[str, Any]] = {}
    for role, row in active.items():
        if not isinstance(role, str) or not role.strip() or not isinstance(row, Mapping):
            raise BatchStoreError("Every active_documents entry must map a document role to an object.")
        file_path, digest = row.get("file"), row.get("sha256")
        if not isinstance(file_path, str) or not file_path.strip():
            raise BatchStoreError(f"active_documents[{role}].file must be non-empty.")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise BatchStoreError(f"active_documents[{role}].sha256 must be a lowercase SHA-256 digest.")
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise BatchStoreError(f"Active document for {role} does not exist: {path}")
        actual = sha256_file(path)
        if actual != digest:
            raise BatchStoreError(
                f"Active document for {role} changed outside the Workflow Ledger pointer: {path}"
            )
        result[role] = {
            "file": str(path),
            "sha256": digest,
            "batch_id": row.get("batch_id"),
            "updated_at_utc": row.get("updated_at_utc"),
        }
    return result


def _validate_recovery_revision_event_lineage(
    recovery: Mapping[str, Any],
    revision_events: Sequence[Any],
) -> None:
    """Require the persisted RevisionLedger to contain the exact staged recovery event set."""
    patch_ids = recovery.get("patch_ids")
    expected_rows = recovery.get("revision_events")
    recovery_id = recovery.get("recovery_id")
    if (
        not isinstance(patch_ids, list)
        or not patch_ids
        or any(not isinstance(value, str) or not value.strip() for value in patch_ids)
        or len(patch_ids) != len(set(patch_ids))
        or not isinstance(expected_rows, list)
        or len(expected_rows) != len(patch_ids)
        or not isinstance(recovery_id, str)
        or not recovery_id.strip()
    ):
        raise BatchStoreError(
            "Recovery intent has an invalid RevisionLedger recovery event inventory."
        )

    expected_by_patch: dict[str, dict[str, Any]] = {}
    expected_event_ids: set[str] = set()
    for row in expected_rows:
        if not isinstance(row, Mapping):
            raise BatchStoreError(
                "Recovery intent contains a malformed staged RevisionLedger recovery event."
            )
        subject_id = row.get("subject_id")
        event_id = row.get("event_id")
        if (
            not isinstance(subject_id, str)
            or subject_id not in patch_ids
            or subject_id in expected_by_patch
            or not isinstance(event_id, str)
            or not event_id.strip()
            or event_id in expected_event_ids
        ):
            raise BatchStoreError(
                "Recovery intent staged RevisionLedger events do not cover unique Patch/event IDs."
            )
        expected_by_patch[subject_id] = dict(row)
        expected_event_ids.add(event_id)
    if set(expected_by_patch) != set(patch_ids):
        raise BatchStoreError(
            "Recovery intent staged RevisionLedger events do not cover the exact Patch set."
        )

    actual_rows: list[dict[str, Any]] = []
    for event in revision_events:
        if hasattr(event, "to_dict") and callable(event.to_dict):
            row = event.to_dict()
        elif isinstance(event, Mapping):
            row = dict(event)
        else:
            continue
        details = row.get("details")
        if (
            row.get("subject_type") == "patch"
            and row.get("event_type") == "patch_abandoned_after_batch_recovery"
            and isinstance(details, Mapping)
            and details.get("recovery_id") == recovery_id
        ):
            actual_rows.append(row)

    actual_by_patch: dict[str, dict[str, Any]] = {}
    actual_event_ids: set[str] = set()
    for row in actual_rows:
        subject_id = row.get("subject_id")
        event_id = row.get("event_id")
        if (
            not isinstance(subject_id, str)
            or subject_id in actual_by_patch
            or not isinstance(event_id, str)
            or not event_id.strip()
            or event_id in actual_event_ids
        ):
            raise BatchStoreError(
                "RevisionLedger contains duplicate or malformed recovery events for this recovery ID."
            )
        actual_by_patch[subject_id] = row
        actual_event_ids.add(event_id)

    if (
        len(actual_rows) != len(patch_ids)
        or set(actual_by_patch) != set(patch_ids)
        or actual_by_patch != expected_by_patch
    ):
        raise BatchStoreError(
            "RevisionLedger recovery event lineage does not exactly match the immutable staged recovery intent."
        )


def _validate_recovered_batch_receipt(
    batch_dir: str | Path,
    intent: Mapping[str, Any],
    patch_status_by_id: Mapping[str, str],
    *,
    revision_events: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Fail closed unless the recovery receipt matches its staged evidence."""
    root = Path(batch_dir).expanduser().resolve()
    receipt_path = root / "batch_recovered.json"
    intent_path = root / "batch_recovery_intent.json"
    scope_path = root / "batch_scope.json"
    if not receipt_path.is_file() or not intent_path.is_file() or not scope_path.is_file():
        raise BatchStoreError(
            f"Recovered batch {intent.get('batch_id')} is missing its receipt, recovery intent or scope."
        )
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        recovery = json.loads(intent_path.read_text(encoding="utf-8"))
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStoreError(f"Cannot read recovered batch receipt/intent/scope: {exc}") from exc
    if not all(isinstance(value, Mapping) for value in (receipt, recovery, scope)):
        raise BatchStoreError("Recovered batch receipt, recovery intent and scope must be JSON objects.")

    intent_sha = sha256_file(root / "batch_intent.json")
    scope_sha = sha256_file(scope_path)
    recovery_sha = sha256_file(intent_path)
    if (
        receipt.get("schema_version") != "1.0"
        or recovery.get("schema_version") != "1.0"
        or receipt.get("status") != "recovered"
        or recovery.get("status") != "prepared"
        or receipt.get("batch_id") != intent.get("batch_id")
        or recovery.get("batch_id") != intent.get("batch_id")
        or receipt.get("recovery_id") != recovery.get("recovery_id")
        or not isinstance(recovery.get("recovery_id"), str)
        or not recovery.get("recovery_id", "").strip()
        or not isinstance(receipt.get("recovered_at_utc"), str)
        or not _is_aware_iso_timestamp(receipt.get("recovered_at_utc"))
        or receipt.get("actor") != recovery.get("actor")
        or receipt.get("reason") != recovery.get("reason")
        or scope.get("batch_id") != intent.get("batch_id")
        or scope.get("batch_intent_sha256") != intent_sha
        or receipt.get("batch_intent_sha256") != intent_sha
        or recovery.get("batch_intent_sha256") != intent_sha
        or receipt.get("batch_scope_sha256") != scope_sha
        or recovery.get("batch_scope_sha256") != scope_sha
        or receipt.get("batch_recovery_intent_sha256") != recovery_sha
    ):
        raise BatchStoreError(
            f"Recovered batch {intent.get('batch_id')} receipt does not match its immutable intent/scope hashes."
        )

    # Verify the receipt binds the recovery intent before using that intent as
    # the expected record for comparison with the current RevisionLedger.
    if revision_events is not None:
        _validate_recovery_revision_event_lineage(recovery, revision_events)

    expected_ledger_paths = {
        "workflow_ledger_file": intent.get("workflow_ledger_file"),
        "revision_ledger_file": scope.get("revision_ledger_file"),
    }
    for field, expected_value in expected_ledger_paths.items():
        receipt_value = receipt.get(field)
        recovery_value = recovery.get(field)
        if (
            not isinstance(expected_value, str)
            or not expected_value.strip()
            or not isinstance(receipt_value, str)
            or not receipt_value.strip()
            or not isinstance(recovery_value, str)
            or not recovery_value.strip()
            or Path(receipt_value).expanduser().resolve() != Path(expected_value).expanduser().resolve()
            or Path(recovery_value).expanduser().resolve() != Path(expected_value).expanduser().resolve()
        ):
            raise BatchStoreError(
                "Recovered batch ledger file paths differ between the immutable batch records, recovery intent and receipt."
            )

    ledger_hash_fields = (
        "workflow_ledger_sha256_before",
        "workflow_ledger_sha256_after",
        "workflow_ledger_payload_sha256_before",
        "revision_ledger_sha256_before",
        "revision_ledger_sha256_after",
        "revision_ledger_payload_sha256_before",
    )
    if any(
        not isinstance(recovery.get(field), str)
        or re.fullmatch(r"[a-f0-9]{64}", recovery[field]) is None
        for field in ledger_hash_fields
    ):
        raise BatchStoreError("Recovered batch recovery intent contains an invalid staged Ledger SHA-256.")

    intent_documents = intent.get("documents")
    if not isinstance(intent_documents, Mapping) or not intent_documents:
        raise BatchStoreError("Recovered batch intent has no document-role records.")
    expected_parent_documents: dict[str, dict[str, Any]] = {}
    expected_failed_output_files: dict[str, list[str]] = {}
    for role, row in intent_documents.items():
        if not isinstance(role, str) or not isinstance(row, Mapping):
            raise BatchStoreError("Recovered batch intent has malformed document-role records.")
        parent_file = row.get("parent_file")
        parent_sha = row.get("parent_sha256")
        output_file = row.get("output_file")
        if (
            not isinstance(parent_file, str)
            or not parent_file.strip()
            or not isinstance(parent_sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", parent_sha) is None
            or not isinstance(output_file, str)
            or not output_file.strip()
        ):
            raise BatchStoreError(f"Recovered batch intent has invalid parent/output evidence for {role}.")
        expected_parent_documents[role] = {
            "file": str(Path(parent_file).expanduser().resolve()),
            "sha256": parent_sha,
            "batch_id": row.get("parent_batch_id"),
        }
        expected_failed_output_files[role] = [
            str(Path(output_file).expanduser().resolve())
        ]

    selected_task_ids = recovery.get("selected_task_ids")
    batch_task_ids = recovery.get("batch_task_ids")
    expected_selected_task_ids = intent.get("all_selected_task_ids", intent.get("selected_task_ids"))
    expected_batch_task_ids = intent.get("selected_task_ids")

    def valid_task_ids(values: Any) -> bool:
        return (
            isinstance(values, list)
            and bool(values)
            and all(isinstance(value, str) and value.strip() for value in values)
            and len(values) == len(set(values))
        )

    selected_task_ids_valid = valid_task_ids(selected_task_ids)
    batch_task_ids_valid = valid_task_ids(batch_task_ids)
    if (
        not selected_task_ids_valid
        or not batch_task_ids_valid
        or selected_task_ids != expected_selected_task_ids
        or batch_task_ids != expected_batch_task_ids
    ):
        raise BatchStoreError(
            "Recovered batch task sets differ from its immutable batch intent "
            f"(recovery.selected_task_ids={selected_task_ids!r}, "
            f"intent.all_selected_task_ids={expected_selected_task_ids!r}, "
            f"recovery.batch_task_ids={batch_task_ids!r}, "
            f"intent.selected_task_ids={expected_batch_task_ids!r}, "
            f"selected_valid={selected_task_ids_valid}, batch_valid={batch_task_ids_valid})."
        )

    invalidated_task_ids = recovery.get("invalidated_task_ids")
    if (
        not valid_task_ids(invalidated_task_ids)
        or receipt.get("invalidated_task_ids") != invalidated_task_ids
    ):
        raise BatchStoreError(
            "Recovered batch invalidated task IDs differ between recovery intent and receipt."
        )

    workflow_after = recovery.get("workflow_after")
    if (
        not isinstance(workflow_after, Mapping)
        or workflow_after.get("revision_round_id") != intent.get("revision_round_id")
        or _json_payload_sha256(workflow_after) != recovery.get("workflow_ledger_sha256_after")
    ):
        raise BatchStoreError("Recovered batch staged Workflow Ledger snapshot hash or revision round is invalid.")

    snapshot_active_documents = workflow_after.get("active_documents")
    if not isinstance(snapshot_active_documents, Mapping):
        raise BatchStoreError("Recovered batch staged Workflow Ledger snapshot has no active document pointers.")
    for role, parent in expected_parent_documents.items():
        pointer = snapshot_active_documents.get(role)
        if (
            not isinstance(pointer, Mapping)
            or not isinstance(pointer.get("file"), str)
            or Path(pointer["file"]).expanduser().resolve() != Path(parent["file"]).expanduser().resolve()
            or pointer.get("sha256") != parent["sha256"]
            or pointer.get("batch_id") != parent["batch_id"]
        ):
            raise BatchStoreError(
                f"Recovered batch staged Workflow Ledger snapshot changed the active parent pointer for {role}."
            )

    snapshot_tasks = {
        row.get("task_id"): row
        for row in workflow_after.get("tasks", [])
        if isinstance(row, Mapping) and isinstance(row.get("task_id"), str)
    } if isinstance(workflow_after.get("tasks"), list) else {}
    if any(
        task_id not in snapshot_tasks
        or snapshot_tasks[task_id].get("status") != "recheck_required"
        for task_id in selected_task_ids
    ):
        raise BatchStoreError(
            "Recovered batch staged Workflow Ledger snapshot does not require recheck for every selected task."
        )

    _validate_recovery_workflow_invalidation_evidence(
        recovery,
        intent,
        workflow_after,
    )

    patch_ids = recovery.get("patch_ids")
    if (
        not isinstance(patch_ids, list)
        or not patch_ids
        or any(not isinstance(value, str) or not value.strip() for value in patch_ids)
        or len(patch_ids) != len(set(patch_ids))
        or receipt.get("patch_ids") != patch_ids
        or scope.get("patch_ids") != patch_ids
    ):
        raise BatchStoreError("Recovered batch Patch ownership differs across receipt, scope and recovery intent.")
    patch_statuses_before = recovery.get("patch_statuses_before")
    allowed_before_statuses = {"planned", "preflighted", "approved", "applied", "abandoned"}
    if (
        not isinstance(patch_statuses_before, Mapping)
        or set(patch_statuses_before) != set(patch_ids)
        or any(status not in allowed_before_statuses for status in patch_statuses_before.values())
    ):
        raise BatchStoreError(
            "Recovered batch original Patch status inventory does not cover its exact Patch set."
        )
    for patch_id in patch_ids:
        if patch_status_by_id.get(patch_id) not in {"abandoned", "superseded"}:
            raise BatchStoreError(
                f"Recovered batch {intent.get('batch_id')} Patch {patch_id} is missing or not abandoned/superseded in the RevisionLedger."
            )

    revision_events = recovery.get("revision_events")
    if not isinstance(revision_events, list) or len(revision_events) != len(patch_ids):
        raise BatchStoreError("Recovered batch intent has an incomplete RevisionLedger recovery event set.")
    event_subject_ids = [
        row.get("subject_id") for row in revision_events if isinstance(row, Mapping)
    ]
    event_ids = [
        row.get("event_id") for row in revision_events if isinstance(row, Mapping)
    ]
    if (
        len(event_subject_ids) != len(patch_ids)
        or set(event_subject_ids) != set(patch_ids)
        or any(not isinstance(event_id, str) or not event_id.strip() for event_id in event_ids)
        or len(event_ids) != len(set(event_ids))
        or any(
            row.get("subject_type") != "patch"
            or row.get("event_type") != "patch_abandoned_after_batch_recovery"
            or row.get("actor") != recovery.get("actor")
            or row.get("timestamp_utc") != recovery.get("created_at_utc")
            or not isinstance(row.get("details"), Mapping)
            or row["details"].get("recovery_id") != recovery.get("recovery_id")
            or row["details"].get("batch_id") != intent.get("batch_id")
            or row["details"].get("reason") != recovery.get("reason")
            or row["details"].get("batch_intent_sha256") != intent_sha
            or row["details"].get("batch_scope_sha256") != scope_sha
            for row in revision_events
            if isinstance(row, Mapping)
        )
    ):
        raise BatchStoreError(
            "Recovered batch RevisionLedger recovery events do not exactly match its staged Patch lineage."
        )

    artifact_inventory = _batch_artifact_inventory(root)
    if (
        recovery.get("artifact_inventory") != artifact_inventory
        or receipt.get("retained_artifacts") != artifact_inventory
        or recovery.get("parent_documents") != expected_parent_documents
        or receipt.get("parent_documents") != expected_parent_documents
        or recovery.get("failed_output_files") != expected_failed_output_files
        or receipt.get("failed_output_files") != expected_failed_output_files
        or receipt.get("selected_task_ids") != selected_task_ids
        or receipt.get("workflow_ledger_sha256") != recovery.get("workflow_ledger_sha256_after")
        or receipt.get("revision_ledger_sha256") != recovery.get("revision_ledger_sha256_after")
        or receipt.get("retained_batch_files") is not True
    ):
        raise BatchStoreError(
            f"Recovered batch {intent.get('batch_id')} receipt does not attest the immutable parent/output scope, retained artifacts and staged Ledger hashes."
        )
    return dict(receipt)


def _read_batch_transaction_record(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStoreError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BatchStoreError(f"{label} must be a JSON object: {path}")
    return payload


def _batch_pointer_projection(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise BatchStoreError(f"{label} must contain at least one document pointer.")
    projected: dict[str, dict[str, Any]] = {}
    for role, pointer in value.items():
        if not isinstance(role, str) or not role.strip() or not isinstance(pointer, Mapping):
            raise BatchStoreError(f"{label} contains a malformed document pointer.")
        file_value = pointer.get("file")
        digest = pointer.get("sha256")
        batch_id = pointer.get("batch_id")
        if (
            not isinstance(file_value, str)
            or not file_value.strip()
            or not isinstance(digest, str)
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or (batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip()))
        ):
            raise BatchStoreError(f"{label} contains invalid path, SHA-256 or batch ID evidence for {role}.")
        projected[role] = {
            "file": str(Path(file_value).expanduser().resolve()),
            "sha256": digest,
            "batch_id": batch_id,
        }
    return projected


def _validate_batch_transaction_events(
    workflow: Mapping[str, Any],
    *,
    batch_id: str,
    event_type: str,
    task_ids: Sequence[str],
    expected_documents: Mapping[str, Mapping[str, Any]],
) -> None:
    events = workflow.get("events")
    if not isinstance(events, list):
        raise BatchStoreError(f"Workflow Ledger has no event history for {event_type} batch {batch_id}.")
    matching = [
        row for row in events
        if isinstance(row, Mapping)
        and row.get("batch_id") == batch_id
        and row.get("event_type") == event_type
    ]
    event_task_ids = [row.get("task_id") for row in matching]
    if (
        len(event_task_ids) != len(task_ids)
        or any(not isinstance(value, str) or not value.strip() for value in event_task_ids)
        or len(event_task_ids) != len(set(event_task_ids))
        or set(event_task_ids) != set(task_ids)
    ):
        raise BatchStoreError(
            f"Workflow Ledger {event_type} events do not cover the exact selected task set for batch {batch_id}."
        )
    expected = _batch_pointer_projection(expected_documents, f"Batch {batch_id} {event_type} evidence")
    for row in matching:
        actual = _batch_pointer_projection(
            row.get("documents"),
            f"Workflow Ledger {event_type} event for batch {batch_id}",
        )
        if actual != expected:
            raise BatchStoreError(
                f"Workflow Ledger {event_type} event pointers differ from the committed batch record for {batch_id}."
            )


def _validate_committed_batch_publication(
    batch_dir: Path,
    intent: Mapping[str, Any],
    workflow: Mapping[str, Any],
    patch_status_by_id: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Only a complete, hash-bound publication chain may release a role reservation."""
    publish_intent_path = batch_dir / "batch_publish_intent.json"
    receipt_path = batch_dir / "batch_published.json"
    scope_path = batch_dir / "batch_scope.json"
    if not publish_intent_path.is_file() or not receipt_path.is_file() or not scope_path.is_file():
        raise BatchStoreError(
            f"Batch {intent.get('batch_id')} has an incomplete publication transaction; "
            "both publication intent and receipt, plus its immutable scope, are required."
        )
    publish_intent = _read_batch_transaction_record(publish_intent_path, "Batch publication intent")
    receipt = _read_batch_transaction_record(receipt_path, "Batch publication receipt")
    scope = _read_batch_transaction_record(scope_path, "Batch scope")
    batch_id = intent.get("batch_id")
    intent_sha = sha256_file(batch_dir / "batch_intent.json")
    scope_sha = sha256_file(scope_path)
    task_ids = intent.get("all_selected_task_ids", intent.get("selected_task_ids"))
    intent_batch_task_ids = intent.get("selected_task_ids")
    scope_batch_task_ids = scope.get("batch_task_ids")
    scope_task_ids = scope.get("selected_task_ids")
    patch_ids = scope.get("patch_ids")
    revision_file = scope.get("revision_ledger_file")
    if (
        not isinstance(batch_id, str)
        or scope.get("batch_id") != batch_id
        or scope.get("batch_intent_sha256") != intent_sha
        or not isinstance(task_ids, list)
        or not task_ids
        or any(not isinstance(value, str) or not value.strip() for value in task_ids)
        or len(task_ids) != len(set(task_ids))
        or not isinstance(intent_batch_task_ids, list)
        or not intent_batch_task_ids
        or any(not isinstance(value, str) or not value.strip() for value in intent_batch_task_ids)
        or len(intent_batch_task_ids) != len(set(intent_batch_task_ids))
        or not isinstance(scope_batch_task_ids, list)
        or not scope_batch_task_ids
        or any(not isinstance(value, str) or not value.strip() for value in scope_batch_task_ids)
        or len(scope_batch_task_ids) != len(set(scope_batch_task_ids))
        or set(scope_batch_task_ids) != set(intent_batch_task_ids)
        or not isinstance(scope_task_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in scope_task_ids)
        or len(scope_task_ids) != len(set(scope_task_ids))
        or set(scope_task_ids) != set(task_ids)
        or not isinstance(patch_ids, list)
        or not patch_ids
        or any(not isinstance(value, str) or not value.strip() for value in patch_ids)
        or len(patch_ids) != len(set(patch_ids))
        or not isinstance(revision_file, str)
        or not revision_file.strip()
    ):
        raise BatchStoreError(f"Batch {batch_id} publication scope does not match its immutable intent.")
    scope_documents = scope.get("documents")
    intent_documents = intent.get("documents")
    if (
        not isinstance(scope_documents, Mapping)
        or not isinstance(intent_documents, Mapping)
        or set(scope_documents) != set(intent_documents)
    ):
        raise BatchStoreError(f"Batch {batch_id} publication scope has incomplete document-role coverage.")
    for role, record in intent_documents.items():
        scoped = scope_documents.get(role)
        if (
            not isinstance(record, Mapping)
            or not isinstance(scoped, Mapping)
            or not isinstance(scoped.get("input_file"), str)
            or Path(scoped["input_file"]).expanduser().resolve()
               != Path(str(record.get("parent_file", ""))).expanduser().resolve()
            or scoped.get("input_sha256") != record.get("parent_sha256")
            or not isinstance(scoped.get("output_file"), str)
            or Path(scoped["output_file"]).expanduser().resolve()
               != Path(str(record.get("output_file", ""))).expanduser().resolve()
        ):
            raise BatchStoreError(f"Batch {batch_id} publication scope differs from the immutable parent/output record for {role}.")
    for patch_id in patch_ids:
        if patch_status_by_id.get(patch_id) not in {"verified", "superseded"}:
            raise BatchStoreError(
                f"Batch {batch_id} published Patch {patch_id} is missing or no longer has verified lineage."
            )

    revision_sha = receipt.get("revision_ledger_sha256")
    expected_workflow_file = intent.get("workflow_ledger_file")
    expected_revision_file = str(Path(revision_file).expanduser().resolve())
    if (
        publish_intent.get("batch_id") != batch_id
        or receipt.get("batch_id") != batch_id
        or receipt.get("status") != "published"
        or not isinstance(expected_workflow_file, str)
        or Path(str(receipt.get("workflow_ledger_file", ""))).expanduser().resolve()
           != Path(expected_workflow_file).expanduser().resolve()
        or Path(str(receipt.get("revision_ledger_file", ""))).expanduser().resolve()
           != Path(expected_revision_file).expanduser().resolve()
        or not isinstance(revision_sha, str)
        or re.fullmatch(r"[a-f0-9]{64}", revision_sha) is None
        or publish_intent.get("revision_ledger_sha256") != revision_sha
        or publish_intent.get("batch_intent_sha256") != intent_sha
        or receipt.get("batch_intent_sha256") != intent_sha
        or publish_intent.get("batch_scope_sha256") != scope_sha
        or receipt.get("batch_scope_sha256") != scope_sha
        or receipt.get("selected_task_ids") != task_ids
    ):
        raise BatchStoreError(f"Batch {batch_id} publication intent/receipt hash or path binding is invalid.")

    published = _batch_pointer_projection(
        receipt.get("active_documents"),
        f"Batch {batch_id} publication receipt",
    )
    staged = _batch_pointer_projection(
        publish_intent.get("active_documents_to_publish"),
        f"Batch {batch_id} publication intent",
    )
    if published != staged or set(published) != set(intent_documents):
        raise BatchStoreError(f"Batch {batch_id} publication intent and receipt disagree on document pointers.")
    for role, pointer in published.items():
        expected_output = Path(str(intent_documents[role].get("output_file", ""))).expanduser().resolve()
        output_path = Path(pointer["file"])
        if (
            output_path != expected_output
            or pointer["batch_id"] != batch_id
            or not output_path.is_file()
            or sha256_file(output_path) != pointer["sha256"]
        ):
            raise BatchStoreError(
                f"Batch {batch_id} published output path/hash does not match its retained document for {role}."
            )
    _validate_batch_transaction_events(
        workflow,
        batch_id=batch_id,
        event_type="batch_published",
        task_ids=task_ids,
        expected_documents=published,
    )
    return published


def _validate_committed_batch_rollback(
    batch_dir: Path,
    intent: Mapping[str, Any],
    workflow: Mapping[str, Any],
) -> None:
    """Require a complete rollback intent/receipt and matching Workflow invalidation events."""
    rollback_intent_path = batch_dir / "batch_rollback_intent.json"
    receipt_path = batch_dir / "batch_rollback.json"
    if not rollback_intent_path.is_file() or not receipt_path.is_file():
        raise BatchStoreError(
            f"Batch {intent.get('batch_id')} has an incomplete rollback transaction; "
            "both rollback intent and receipt are required."
        )
    rollback_intent = _read_batch_transaction_record(rollback_intent_path, "Batch rollback intent")
    receipt = _read_batch_transaction_record(receipt_path, "Batch rollback receipt")
    batch_id = intent.get("batch_id")
    intent_sha = sha256_file(batch_dir / "batch_intent.json")
    scope_sha = sha256_file(batch_dir / "batch_scope.json")
    task_ids = intent.get("all_selected_task_ids", intent.get("selected_task_ids"))
    if (
        rollback_intent.get("batch_id") != batch_id
        or receipt.get("batch_id") != batch_id
        or receipt.get("status") != "rolled_back"
        or rollback_intent.get("batch_intent_sha256") != intent_sha
        or receipt.get("batch_intent_sha256") != intent_sha
        or rollback_intent.get("batch_scope_sha256") != scope_sha
        or receipt.get("batch_scope_sha256") != scope_sha
        or receipt.get("rollback_intent_sha256") != sha256_file(rollback_intent_path)
        or receipt.get("workflow_ledger_file") is None
        or Path(str(receipt.get("workflow_ledger_file"))).expanduser().resolve()
           != Path(str(intent.get("workflow_ledger_file", ""))).expanduser().resolve()
        or receipt.get("selected_task_ids") != task_ids
        or receipt.get("retained_batch_files") is not True
        or not isinstance(rollback_intent.get("reason"), str)
        or not rollback_intent.get("reason", "").strip()
        or receipt.get("reason") != rollback_intent.get("reason")
    ):
        raise BatchStoreError(f"Batch {batch_id} rollback intent/receipt hash or path binding is invalid.")
    expected_parent_documents = {
        role: {
            "file": str(Path(str(row.get("parent_file", ""))).expanduser().resolve()),
            "sha256": row.get("parent_sha256"),
            "batch_id": row.get("parent_batch_id"),
        }
        for role, row in intent.get("documents", {}).items()
        if isinstance(row, Mapping)
    }
    intent_parents = _batch_pointer_projection(
        rollback_intent.get("restored_active_documents"),
        f"Batch {batch_id} rollback intent",
    )
    receipt_parents = _batch_pointer_projection(
        receipt.get("restored_active_documents"),
        f"Batch {batch_id} rollback receipt",
    )
    expected_parents = _batch_pointer_projection(
        expected_parent_documents,
        f"Batch {batch_id} immutable parents",
    )
    if intent_parents != expected_parents or receipt_parents != expected_parents:
        raise BatchStoreError(f"Batch {batch_id} rollback parent pointers differ from its immutable intent.")
    for role, pointer in expected_parents.items():
        parent_path = Path(pointer["file"])
        if not parent_path.is_file() or sha256_file(parent_path) != pointer["sha256"]:
            raise BatchStoreError(f"Batch {batch_id} rollback parent document changed or is missing for {role}.")
    _validate_batch_transaction_events(
        workflow,
        batch_id=batch_id,
        event_type="batch_rolled_back",
        task_ids=task_ids,
        expected_documents=expected_parents,
    )


def assert_no_uncommitted_role_batches(
    runs_dir: str | Path,
    roles: Sequence[str],
    selected_task_ids: Sequence[str],
    patch_status_by_id: Mapping[str, str],
    workflow: Mapping[str, Any],
    *,
    revision_events: Sequence[Any] | None = None,
) -> None:
    """Prevent competing batches from branching from the same active document-role version."""
    root = Path(runs_dir).expanduser().resolve()
    if not root.exists():
        return
    requested_roles = set(roles)
    active_pointers = load_active_document_pointers(workflow)
    new_task_ids = set(selected_task_ids)
    for batch_dir in sorted(root.glob("batch-*")):
        if batch_dir.is_symlink():
            raise BatchStoreError(
                f"Batch directory must not be a symbolic link: {batch_dir}"
            )
        if not batch_dir.is_dir():
            continue
        intent_path = batch_dir / "batch_intent.json"
        control_names = (
            "batch_recovery_intent.json",
            "batch_recovered.json",
            "batch_publish_intent.json",
            "batch_published.json",
            "batch_rollback_intent.json",
            "batch_rollback.json",
        )
        present_controls = [name for name in control_names if (batch_dir / name).exists()]
        if not intent_path.is_file():
            if present_controls:
                raise BatchStoreError(
                    f"Batch directory {batch_dir} has transaction control records but no immutable batch intent."
                )
            continue
        intent = read_batch_intent(batch_dir)
        recovery_receipt = batch_dir / "batch_recovered.json"
        publication_markers = (
            batch_dir / "batch_publish_intent.json",
            batch_dir / "batch_published.json",
        )
        rollback_markers = (
            batch_dir / "batch_rollback_intent.json",
            batch_dir / "batch_rollback.json",
        )
        if recovery_receipt.is_file():
            if any(path.exists() for path in (*publication_markers, *rollback_markers)):
                raise BatchStoreError(
                    f"Batch {intent['batch_id']} has conflicting recovery and publish/rollback transaction records."
                )
            if revision_events is None:
                raise BatchStoreError(
                    f"Recovered batch {intent['batch_id']} cannot release its role reservation without the current RevisionLedger events."
                )
            _validate_recovered_batch_receipt(
                batch_dir,
                intent,
                patch_status_by_id,
                revision_events=revision_events,
            )
            continue
        if any(path.exists() for path in (*publication_markers, *rollback_markers)):
            if any(path.exists() for path in rollback_markers) and not any(
                path.exists() for path in publication_markers
            ):
                raise BatchStoreError(
                    f"Batch {intent['batch_id']} has rollback records without a committed publication chain."
                )
            if any(path.exists() for path in publication_markers):
                _validate_committed_batch_publication(
                    batch_dir, intent, workflow, patch_status_by_id
                )
            if any(path.exists() for path in rollback_markers):
                _validate_committed_batch_rollback(batch_dir, intent, workflow)
            continue
        overlap = requested_roles.intersection(intent.get("documents", {}))
        if not overlap:
            continue

        # An uncommitted intent must still be based on the currently active parent.
        # If not, it may have had its pointer update committed before the receipt
        # was written; do not branch another batch until that receipt is recovered.
        for role in overlap:
            parent = intent["documents"][role]
            current = active_pointers.get(role)
            if (
                current is None
                or current["file"] != str(Path(parent["parent_file"]).expanduser().resolve())
                or current["sha256"] != parent["parent_sha256"]
                or current.get("batch_id") != parent.get("parent_batch_id")
            ):
                raise BatchStoreError(
                    f"Uncommitted batch {intent['batch_id']} no longer has its parent active pointer for {role}; "
                    "recover its publish/rollback receipt before creating another batch."
                )

        old_task_ids = set(intent.get("all_selected_task_ids", intent["selected_task_ids"]))
        if old_task_ids and old_task_ids.issubset(new_task_ids):
            # A staged recovery is a two-Ledger transaction. Do not let a new
            # batch race it before its final recovery receipt has been written.
            if (batch_dir / "batch_recovery_intent.json").is_file():
                raise BatchStoreError(
                    f"Batch {intent['batch_id']} has a staged recovery transaction; "
                    "rerun recover-failed-batch until batch_recovered.json exists."
                )
            scope_probe = batch_dir / "batch_scope.json"
            if scope_probe.is_file():
                try:
                    probe = json.loads(scope_probe.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise BatchStoreError(
                        f"Cannot inspect uncommitted batch scope {scope_probe}: {exc}"
                    ) from exc
                abandoned = [
                    patch_id for patch_id in probe.get("patch_ids", [])
                    if patch_status_by_id.get(patch_id) == "abandoned"
                ] if isinstance(probe, Mapping) else []
                if abandoned:
                    raise BatchStoreError(
                        f"Batch {intent['batch_id']} contains abandoned Patch(es) without a recovery receipt: "
                        + ", ".join(sorted(abandoned))
                    )
            # The scheduler only yields tasks with current ready authorization.
            # Rebuilding the full set uses RevisionLedger Patch supersession lineage.
            continue

        scope_path = batch_dir / "batch_scope.json"
        if not scope_path.is_file():
            raise BatchStoreError(
                f"Uncommitted batch {intent['batch_id']} has no scope record and reserves "
                + ", ".join(sorted(overlap))
                + "; reselect/re-authorize the entire original task set before retrying."
            )
        try:
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchStoreError(f"Cannot inspect uncommitted batch scope {scope_path}: {exc}") from exc
        if (
            not isinstance(scope, Mapping)
            or scope.get("batch_id") != intent["batch_id"]
            or scope.get("batch_intent_sha256") != sha256_file(intent_path)
        ):
            raise BatchStoreError(
                f"Uncommitted batch {intent['batch_id']} has an invalid or stale scope record; "
                "resolve it before creating another batch for " + ", ".join(sorted(overlap))
            )
        patch_ids = scope.get("patch_ids")
        if not isinstance(patch_ids, list) or not patch_ids:
            raise BatchStoreError(
                f"Uncommitted batch {intent['batch_id']} has no resolvable Patch IDs; "
                "reselect the entire original task set or resolve the existing batch."
            )
        unknown = [patch_id for patch_id in patch_ids if patch_id not in patch_status_by_id]
        active = [
            patch_id for patch_id in patch_ids
            if patch_status_by_id.get(patch_id) != "superseded"
        ]
        if unknown or active:
            reasons = []
            if unknown:
                reasons.append("missing ledger Patch(es): " + ", ".join(sorted(unknown)))
            if active:
                reasons.append("non-superseded Patch(es): " + ", ".join(sorted(active)))
            raise BatchStoreError(
                f"Document role(s) {', '.join(sorted(overlap))} are reserved by uncommitted batch "
                f"{intent['batch_id']} ({'; '.join(reasons)}). Reauthorize/reselect its entire task set "
                "or supersede every Patch before creating a different batch."
            )


def create_batch_intent(
    workflow: Mapping[str, Any],
    workflow_path: str | Path,
    selected_task_ids: Sequence[str],
    roles: Sequence[str],
    *,
    all_selected_task_ids: Sequence[str] | None = None,
    runs_dir: str | Path | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Create a unique batch directory and immutable parent/output intent manifest."""
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise BatchStoreError("Cannot prepare a batch from an invalid Workflow Ledger:\n- " + "\n- ".join(errors))
    if not isinstance(selected_task_ids, (list, tuple)) or not selected_task_ids:
        raise BatchStoreError("A batch must contain at least one selected task.")
    selected_ids = list(selected_task_ids)
    all_task_ids = list(all_selected_task_ids) if all_selected_task_ids is not None else list(selected_ids)
    if (
        not isinstance(all_task_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in selected_ids)
        or any(not isinstance(item, str) or not item.strip() for item in all_task_ids)
        or len(selected_ids) != len(set(selected_ids))
        or len(all_task_ids) != len(set(all_task_ids))
        or not all_task_ids
        or not set(selected_ids).issubset(set(all_task_ids))
    ):
        raise BatchStoreError(
            "Selected task ID arrays must contain unique non-empty strings and include every document-changing task."
        )
    unique_roles = list(dict.fromkeys(roles))
    if not unique_roles:
        raise BatchStoreError("A document-changing batch must contain at least one target document role.")
    pointers = load_active_document_pointers(workflow)
    unknown_roles = sorted(set(unique_roles) - set(pointers))
    if unknown_roles:
        raise BatchStoreError("No active document pointer exists for: " + ", ".join(unknown_roles))
    workflow_file = Path(workflow_path).expanduser().resolve()
    if not workflow_file.is_file():
        raise BatchStoreError(f"Workflow Ledger file does not exist: {workflow_file}")
    workflow_sha_at_prepare = sha256_file(workflow_file)
    try:
        workflow_on_disk = json.loads(workflow_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStoreError(f"Cannot read Workflow Ledger {workflow_file}: {exc}") from exc
    if workflow_on_disk != json.loads(json.dumps(workflow, ensure_ascii=False)):
        raise BatchStoreError(
            "The in-memory Workflow Ledger differs from the current file on disk; reload it before preparing a batch."
        )
    base = Path(runs_dir).expanduser() if runs_dir else workflow_file.parent / "runs"
    if not base.is_absolute():
        base = workflow_file.parent / base
    base = base.resolve()
    if batch_id is None:
        batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,95}", batch_id):
        raise BatchStoreError("batch_id must be 3-96 safe filename characters.")
    batch_dir = (base / f"batch-{batch_id}").resolve()
    if batch_dir.exists():
        raise BatchStoreError(f"Batch directory already exists; refusing to reuse it: {batch_dir}")
    batch_dir.mkdir(parents=True, exist_ok=False)
    documents_dir = batch_dir / "documents"
    documents_dir.mkdir()
    role_records: dict[str, dict[str, Any]] = {}
    for role in unique_roles:
        parent = pointers[role]
        slug = _safe_component(role)
        role_output = documents_dir / f"{slug}.marked.docx"
        role_records[role] = {
            "parent_file": parent["file"],
            "parent_sha256": parent["sha256"],
            "parent_batch_id": parent.get("batch_id"),
            "output_file": str(role_output),
        }
    intent = {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "revision_round_id": workflow.get("revision_round_id"),
        "workflow_ledger_file": str(workflow_file),
        "workflow_ledger_sha256_at_prepare": workflow_sha_at_prepare,
        "created_at_utc": _timestamp(),
        "selected_task_ids": list(dict.fromkeys(selected_task_ids)),
        "all_selected_task_ids": all_task_ids,
        "status": "prepared",
        "documents": role_records,
        "policy": {
            "append_only": True,
            "overwrite_committed_outputs": False,
            "rollback_deletes_files": False,
            "publish_mode": "single_workflow_ledger_active_documents_replace",
        },
    }
    write_json_new(batch_dir / "batch_intent.json", intent)
    if sha256_file(workflow_file) != workflow_sha_at_prepare:
        raise BatchStoreError(
            "Workflow Ledger changed while the batch intent was being prepared; this batch remains uncommitted and must not be used."
        )
    return {**intent, "batch_dir": str(batch_dir), "batch_intent_file": str(batch_dir / "batch_intent.json")}


def read_batch_intent(batch_dir: str | Path) -> dict[str, Any]:
    requested_root = Path(batch_dir).expanduser()
    if requested_root.is_symlink():
        raise BatchStoreError(
            f"Batch directory must not be a symbolic link: {requested_root}"
        )
    root = requested_root.resolve()
    if not root.is_dir():
        raise BatchStoreError(f"Batch directory does not exist: {root}")
    intent_path = root / "batch_intent.json"
    if not intent_path.is_file():
        raise BatchStoreError(f"Batch intent does not exist: {intent_path}")
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStoreError(f"Cannot read batch intent {intent_path}: {exc}") from exc
    if not isinstance(intent, dict) or intent.get("schema_version") != "1.0":
        raise BatchStoreError("Batch intent has an unsupported schema.")
    if intent.get("batch_id") != root.name.removeprefix("batch-"):
        raise BatchStoreError("Batch intent ID does not match its directory name.")
    if not isinstance(intent.get("documents"), dict) or not intent["documents"]:
        raise BatchStoreError("Batch intent has no document role records.")
    selected_task_ids = intent.get("selected_task_ids")
    all_selected_task_ids = intent.get("all_selected_task_ids", selected_task_ids)
    if (
        not isinstance(selected_task_ids, list)
        or not selected_task_ids
        or any(not isinstance(item, str) or not item.strip() for item in selected_task_ids)
        or not isinstance(all_selected_task_ids, list)
        or any(not isinstance(item, str) or not item.strip() for item in selected_task_ids)
        or any(not isinstance(item, str) or not item.strip() for item in all_selected_task_ids)
        or len(all_selected_task_ids) != len(set(all_selected_task_ids))
        or len(selected_task_ids) != len(set(selected_task_ids))
        or not set(selected_task_ids).issubset(set(all_selected_task_ids))
    ):
        raise BatchStoreError("Batch intent task ID sets are invalid or inconsistent.")
    for role, row in intent["documents"].items():
        if not isinstance(role, str) or not isinstance(row, Mapping):
            raise BatchStoreError("Batch document records must be role-keyed objects.")
        for key in ("parent_file", "parent_sha256", "output_file"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise BatchStoreError(f"Batch document {role} requires {key}.")
        if not re.fullmatch(r"[a-f0-9]{64}", row["parent_sha256"]):
            raise BatchStoreError(f"Batch parent SHA-256 is invalid for {role}.")
        expected_output = (root / "documents" / f"{_safe_component(role)}.marked.docx").resolve()
        if Path(row["output_file"]).expanduser().resolve() != expected_output:
            raise BatchStoreError(f"Batch output path for {role} escapes its immutable batch directory.")
    return intent


def publish_active_pointers(
    workflow: Mapping[str, Any],
    workflow_path: str | Path,
    batch_dir: str | Path,
    revision_ledger: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the whole batch, then return a new Workflow Ledger with all pointers switched."""
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise BatchStoreError("Cannot publish into an invalid Workflow Ledger:\n- " + "\n- ".join(errors))
    intent = read_batch_intent(batch_dir)
    if Path(str(intent.get("workflow_ledger_file"))).expanduser().resolve() != Path(workflow_path).expanduser().resolve():
        raise BatchStoreError("Batch intent belongs to a different Workflow Ledger.")
    if intent.get("revision_round_id") != workflow.get("revision_round_id"):
        raise BatchStoreError("Batch intent belongs to a different revision round.")
    pointers = load_active_document_pointers(workflow)
    batch_id = intent["batch_id"]
    role_updates: dict[str, dict[str, Any]] = {}
    patch_by_id = {patch.patch_id: patch for patch in revision_ledger.patches}
    for role, record in intent["documents"].items():
        current = pointers.get(role)
        if current is None:
            raise BatchStoreError(f"No active parent pointer for document role {role}.")
        output_path = Path(record["output_file"]).expanduser().resolve()
        if not output_path.is_file():
            raise BatchStoreError(f"Batch output for {role} is missing: {output_path}")
        output_sha = sha256_file(output_path)
        already_published = (
            current.get("batch_id") == batch_id
            and current["file"] == str(output_path)
            and current["sha256"] == output_sha
        )
        if not already_published and (
            current["file"] != str(Path(record["parent_file"]).expanduser().resolve())
            or current["sha256"] != record["parent_sha256"]
        ):
            raise BatchStoreError(
                f"Active pointer for {role} changed after batch preparation; refusing stale publish."
            )
        linked_patches = [
            patch for patch in revision_ledger.patches
            if patch.status != "superseded"
            and any(
                operation.document == role
                and Path(operation.output_file).expanduser().resolve() == output_path
                for operation in patch.operations
            )
        ]
        if not linked_patches:
            raise BatchStoreError(f"No Patch in the RevisionLedger owns the batch output for {role}.")
        unverified = [patch.patch_id for patch in linked_patches if patch.status != "verified"]
        if unverified:
            raise BatchStoreError(
                f"Cannot publish {role}; Patch(es) are not verified: " + ", ".join(sorted(unverified))
            )
        verified_hashes = {
            row.get("sha256")
            for patch in linked_patches
            for row in patch.verified_output_hashes
            if isinstance(row, Mapping)
            and isinstance(row.get("path"), str)
            and Path(row["path"]).expanduser().resolve() == output_path
        }
        if verified_hashes != {output_sha}:
            raise BatchStoreError(
                f"Verified Patch output hashes do not uniquely match the batch output for {role}."
            )
        role_updates[role] = (
            dict(current) if already_published else {
                "file": str(output_path),
                "sha256": output_sha,
                "batch_id": batch_id,
                "updated_at_utc": _timestamp(),
            }
        )
    updated = json.loads(json.dumps(workflow, ensure_ascii=False))
    active = dict(updated.get("active_documents") or {})
    active.update(role_updates)
    updated["active_documents"] = active
    task_ids = list(intent.get("all_selected_task_ids", intent.get("selected_task_ids", [])))
    events = list(updated.get("events", []))
    now = _timestamp()
    already_recorded = {
        event.get("task_id") for event in events
        if isinstance(event, Mapping)
        and event.get("event_type") == "batch_published"
        and event.get("batch_id") == batch_id
    }
    for task_id in task_ids:
        if task_id in already_recorded:
            continue
        events.append({
            "event_id": str(uuid.uuid4()),
            "task_id": task_id,
            "event_type": "batch_published",
            "timestamp_utc": now,
            "actor": "Co_Reviewer Batch Publisher",
            "batch_id": batch_id,
            "documents": role_updates,
        })
    updated["events"] = events
    new_errors = validate_workflow_ledger(updated)
    if new_errors:
        raise BatchStoreError("New active pointers failed Workflow Ledger validation:\n- " + "\n- ".join(new_errors))
    receipt = {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "published_at_utc": now,
        "workflow_ledger_file": str(Path(workflow_path).expanduser().resolve()),
        "active_documents": role_updates,
        "selected_task_ids": task_ids,
        "status": "published",
    }
    return updated, receipt


def rollback_active_pointers(
    workflow: Mapping[str, Any],
    workflow_path: str | Path,
    batch_dir: str | Path,
    *,
    reason: str = "Published batch rolled back; revalidate all affected document targets.",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Restore parent pointers for a published batch without deleting any batch file."""
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise BatchStoreError("Cannot roll back an invalid Workflow Ledger:\n- " + "\n- ".join(errors))
    intent = read_batch_intent(batch_dir)
    batch_id = intent["batch_id"]
    pointers = load_active_document_pointers(workflow)
    restored: dict[str, dict[str, Any]] = {}
    for role, record in intent["documents"].items():
        current = pointers.get(role)
        output_path = str(Path(record["output_file"]).expanduser().resolve())
        parent_path = Path(record["parent_file"]).expanduser().resolve()
        already_rolled_back = (
            current is not None
            and current["file"] == str(parent_path)
            and current["sha256"] == record["parent_sha256"]
            and current.get("batch_id") == record.get("parent_batch_id")
        )
        if not already_rolled_back and (
            current is None or current.get("batch_id") != batch_id or current["file"] != output_path
        ):
            raise BatchStoreError(
                f"Active pointer for {role} no longer points to batch {batch_id}; refusing rollback."
            )
        if not parent_path.is_file() or sha256_file(parent_path) != record["parent_sha256"]:
            raise BatchStoreError(
                f"Parent output for {role} is unavailable or changed; cannot restore its pointer."
            )
        restored[role] = {
            "file": str(parent_path),
            "sha256": record["parent_sha256"],
            "batch_id": record.get("parent_batch_id"),
            "updated_at_utc": _timestamp(),
        }
    updated = json.loads(json.dumps(workflow, ensure_ascii=False))
    from .workflow_engine import invalidate_affected_tasks
    invalidated_tasks, _ = invalidate_affected_tasks(
        updated.get("tasks", []),
        list(intent.get("all_selected_task_ids", intent.get("selected_task_ids", []))),
        reason,
    )
    task_rows = {row.get("task_id"): row for row in invalidated_tasks}
    for task_id in intent.get("all_selected_task_ids", intent.get("selected_task_ids", [])):
        task = task_rows.get(task_id)
        if task is None:
            raise BatchStoreError(f"Rollback refers to unknown Workflow task {task_id}.")
        if task.get("status") != "recheck_required":
            task["recheck_from"] = task.get("status", "identified")
        task["status"] = "recheck_required"
        task["invalidation_reason"] = reason
    updated["tasks"] = invalidated_tasks
    active = dict(updated.get("active_documents") or {})
    active.update(restored)
    updated["active_documents"] = active
    now = _timestamp()
    existing_rollback_events = {
        event.get("task_id") for event in updated.get("events", [])
        if isinstance(event, Mapping)
        and event.get("event_type") == "batch_rolled_back"
        and event.get("batch_id") == batch_id
    }
    for task_id in intent.get("all_selected_task_ids", intent.get("selected_task_ids", [])):
        if task_id in existing_rollback_events:
            continue
        updated.setdefault("events", []).append({
            "event_id": str(uuid.uuid4()),
            "task_id": task_id,
            "event_type": "batch_rolled_back",
            "timestamp_utc": now,
            "actor": "Co_Reviewer Batch Rollback",
            "batch_id": batch_id,
            "documents": restored,
        })
    new_errors = validate_workflow_ledger(updated)
    if new_errors:
        raise BatchStoreError("Rollback pointers failed Workflow Ledger validation:\n- " + "\n- ".join(new_errors))
    receipt = {
        "schema_version": "1.0",
        "batch_id": batch_id,
        "rolled_back_at_utc": now,
        "workflow_ledger_file": str(Path(workflow_path).expanduser().resolve()),
        "restored_active_documents": restored,
        "selected_task_ids": list(intent.get("all_selected_task_ids", intent.get("selected_task_ids", []))),
        "status": "rolled_back",
        "retained_batch_files": True,
    }
    return updated, receipt


def _batch_artifact_inventory(batch_dir: Path) -> list[dict[str, Any]]:
    """Hash every retained batch artifact, excluding only root recovery controls.

    Symlinks are rejected rather than hashing their targets: the inventory must
    describe files retained inside this immutable batch directory, not mutable
    files that happen to be reachable through a link.
    """
    requested_root = batch_dir.expanduser()
    if requested_root.is_symlink():
        raise BatchStoreError(
            f"Batch directory must not be a symbolic link: {requested_root}"
        )
    root = requested_root.resolve()
    rows: list[dict[str, Any]] = []
    excluded_root_controls = {"batch_recovery_intent.json", "batch_recovered.json"}
    for path in sorted(root.rglob("*")):
        try:
            if path.is_symlink():
                raise BatchStoreError(
                    f"Retained batch artifact must not be a symbolic link: {path}"
                )
            if not path.is_file():
                continue
            if path.parent == root and path.name in excluded_root_controls:
                continue
            rows.append({
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
        except BatchStoreError:
            raise
        except OSError as exc:
            raise BatchStoreError(
                f"Cannot inventory retained batch artifact {path}: {exc}"
            ) from exc
    return rows


def _json_payload_sha256(payload: Mapping[str, Any]) -> str:
    import hashlib
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_recovery_workflow_invalidation_evidence(
    recovery: Mapping[str, Any],
    intent: Mapping[str, Any],
    workflow_after: Mapping[str, Any],
) -> list[str]:
    """Require one invalidation event for every selected/conflicting/downstream task."""
    batch_id = intent.get("batch_id")
    selected_task_ids = recovery.get("selected_task_ids")
    reason = recovery.get("reason")
    actor = recovery.get("actor")
    timestamp = recovery.get("created_at_utc")
    if (
        not isinstance(batch_id, str)
        or not isinstance(reason, str)
        or not reason.strip()
        or not isinstance(actor, str)
        or not actor.strip()
        or not isinstance(timestamp, str)
        or not timestamp.strip()
        or not isinstance(selected_task_ids, list)
        or not selected_task_ids
        or any(not isinstance(value, str) or not value.strip() for value in selected_task_ids)
        or len(selected_task_ids) != len(set(selected_task_ids))
    ):
        raise BatchStoreError("Recovery intent has invalid task, actor, reason or timestamp evidence.")

    expected_reason = (
        f"Unpublished batch {batch_id} abandoned after Apply failure: {reason}"
    )
    staged_errors = validate_workflow_ledger(workflow_after)
    if staged_errors:
        raise BatchStoreError(
            "Recovered batch Workflow after-image is invalid: " + "; ".join(staged_errors)
        )
    task_rows = workflow_after.get("tasks")
    if not isinstance(task_rows, list):
        raise BatchStoreError("Recovered batch Workflow after-image has no task array.")
    from .workflow_engine import invalidate_affected_tasks, WorkflowValidationError
    try:
        _, expected_impacted_ids = invalidate_affected_tasks(
            task_rows,
            selected_task_ids,
            expected_reason,
        )
    except WorkflowValidationError as exc:
        raise BatchStoreError(
            f"Cannot reconstruct failed-batch downstream invalidation scope: {exc}"
        ) from exc
    expected_affected_task_ids = sorted(set(selected_task_ids) | set(expected_impacted_ids))
    affected_task_ids = sorted({
        row.get("task_id")
        for row in task_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("task_id"), str)
        and row.get("status") == "recheck_required"
        and row.get("invalidation_reason") == expected_reason
    })
    if affected_task_ids != expected_affected_task_ids:
        missing = sorted(set(expected_affected_task_ids) - set(affected_task_ids))
        extra = sorted(set(affected_task_ids) - set(expected_affected_task_ids))
        details = []
        if missing:
            details.append("missing=" + ", ".join(missing))
        if extra:
            details.append("unexpected=" + ", ".join(extra))
        raise BatchStoreError(
            "Recovered batch Workflow after-image does not exactly reflect the selected, conflicting and downstream DAG scope ("
            + "; ".join(details) + ")."
        )
    declared_affected = recovery.get("invalidated_task_ids")
    if not isinstance(declared_affected, list) or declared_affected != affected_task_ids:
        raise BatchStoreError(
            "Recovered batch invalidated_task_ids is missing or differs from the actual Workflow after-image."
        )

    events = workflow_after.get("events")
    if not isinstance(events, list):
        raise BatchStoreError("Recovered batch staged Workflow Ledger snapshot has no event history.")
    matching_events = [
        row for row in events
        if isinstance(row, Mapping)
        and row.get("event_type") == "invalidation"
        and row.get("batch_id") == batch_id
    ]
    event_task_ids = [row.get("task_id") for row in matching_events]
    if (
        len(event_task_ids) != len(affected_task_ids)
        or any(not isinstance(value, str) or not value.strip() for value in event_task_ids)
        or len(event_task_ids) != len(set(event_task_ids))
        or set(event_task_ids) != set(affected_task_ids)
    ):
        raise BatchStoreError(
            "Recovered batch Workflow after-image must contain exactly one invalidation event "
            "for every selected, conflicting and downstream task."
        )
    if any(
        row.get("reason") != expected_reason
        or row.get("actor") != actor
        or row.get("timestamp_utc") != timestamp
        for row in matching_events
    ):
        raise BatchStoreError(
            "Recovered batch Workflow invalidation event reason/actor/timestamp differs from its recovery intent."
        )
    return affected_task_ids


def _prepare_recovery_workflow(
    workflow: Mapping[str, Any],
    selected_task_ids: Sequence[str],
    *,
    batch_id: str,
    actor: str,
    reason: str,
    timestamp_utc: str,
) -> dict[str, Any]:
    """Create the exact Workflow Ledger after-image for a failed-batch recovery."""
    from .workflow_engine import invalidate_affected_tasks

    updated = json.loads(json.dumps(workflow, ensure_ascii=False))
    changed_ids = list(dict.fromkeys(selected_task_ids))
    task_rows = {
        row.get("task_id"): row
        for row in updated.get("tasks", [])
        if isinstance(row, Mapping)
    }
    unknown = sorted(set(changed_ids) - set(task_rows))
    if unknown:
        raise BatchStoreError(
            "Recovery refers to unknown Workflow task(s): " + ", ".join(unknown)
        )

    invalidated_tasks, impacted_ids = invalidate_affected_tasks(
        updated.get("tasks", []),
        changed_ids,
        f"Unpublished batch {batch_id} abandoned after Apply failure: {reason}",
    )
    invalidated_by_id = {row.get("task_id"): row for row in invalidated_tasks}
    recovery_task_ids = sorted(set(changed_ids) | set(impacted_ids))
    for task_id in changed_ids:
        task = invalidated_by_id[task_id]
        if task.get("status") != "recheck_required":
            task["recheck_from"] = task.get("status", "identified")
        task["status"] = "recheck_required"
        task["invalidation_reason"] = (
            f"Unpublished batch {batch_id} abandoned after Apply failure: {reason}"
        )
    updated["tasks"] = invalidated_tasks

    for task_id in recovery_task_ids:
        updated.setdefault("events", []).append({
            "event_id": str(uuid.uuid4()),
            "task_id": task_id,
            "timestamp_utc": timestamp_utc,
            "event_type": "invalidation",
            "reason": f"Unpublished batch {batch_id} abandoned after Apply failure: {reason}",
            "actor": actor,
            "batch_id": batch_id,
        })

    errors = validate_workflow_ledger(updated)
    if errors:
        raise BatchStoreError(
            "Recovery Workflow Ledger failed validation: " + "; ".join(errors)
        )
    return updated


def recover_unpublished_batch(
    workflow: Mapping[str, Any],
    workflow_path: str | Path,
    revision_ledger: Any,
    revision_ledger_path: str | Path,
    batch_dir: str | Path,
    *,
    actor: str,
    reason: str,
) -> tuple[dict[str, Any], Any, dict[str, Any], bool, bool, bool]:
    """Abandon an unpublished failed Apply and require renewed Workflow authorization.

    Returns (workflow, revision_ledger, recovery_intent, workflow_changed,
    ledger_changed, already_recovered). Active document pointers are never changed.
    """
    import copy
    from .revision_models import RevisionEvent

    requested_root = Path(batch_dir).expanduser()
    if requested_root.is_symlink():
        raise BatchStoreError(
            f"Batch directory must not be a symbolic link: {requested_root}"
        )
    root = requested_root.resolve()
    workflow_file = Path(workflow_path).expanduser().resolve()
    ledger_file = Path(revision_ledger_path).expanduser().resolve()
    if workflow_file == ledger_file:
        raise BatchStoreError("Workflow Ledger and RevisionLedger must be separate files.")
    if not workflow_file.is_file() or not ledger_file.is_file():
        raise BatchStoreError("Workflow Ledger and RevisionLedger files must both exist.")

    receipt_path = root / "batch_recovered.json"
    if receipt_path.is_file():
        intent = read_batch_intent(root)
        patch_status_by_id = {
            patch.patch_id: patch.status for patch in revision_ledger.patches
        }
        receipt = _validate_recovered_batch_receipt(
            root,
            intent,
            patch_status_by_id,
            revision_events=revision_ledger.events,
        )
        return dict(workflow), revision_ledger, dict(receipt), False, False, True

    if any((root / name).exists() for name in (
        "batch_published.json",
        "batch_rollback.json",
        "batch_publish_intent.json",
        "batch_rollback_intent.json",
    )):
        raise BatchStoreError(
            "This batch has a publication or rollback receipt/intent. Resolve that transaction first; "
            "failed-Apply recovery is only for unpublished batches with no pointer transaction in progress."
        )

    errors = validate_workflow_ledger(workflow)
    if errors:
        raise BatchStoreError(
            "Cannot recover against an invalid Workflow Ledger: " + "; ".join(errors)
        )
    intent = read_batch_intent(root)
    if Path(str(intent.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_file:
        raise BatchStoreError("Batch intent belongs to a different Workflow Ledger.")
    if intent.get("revision_round_id") != workflow.get("revision_round_id"):
        raise BatchStoreError("Batch intent belongs to a different revision round.")

    scope_path = root / "batch_scope.json"
    if not scope_path.is_file():
        raise BatchStoreError(
            "Failed batch has no batch_scope.json; cannot prove its Patch ownership."
        )
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStoreError(f"Cannot read batch scope: {exc}") from exc

    intent_path = root / "batch_intent.json"
    intent_sha = sha256_file(intent_path)
    scope_sha = sha256_file(scope_path)
    if (
        not isinstance(scope, Mapping)
        or scope.get("batch_id") != intent["batch_id"]
        or scope.get("batch_intent_sha256") != intent_sha
    ):
        raise BatchStoreError("Batch intent/scope integrity check failed.")
    if Path(str(scope.get("revision_ledger_file", ""))).expanduser().resolve() != ledger_file:
        raise BatchStoreError("Batch scope points to a different RevisionLedger.")
    if scope.get("revision_round_id") != revision_ledger.revision_round_id:
        raise BatchStoreError("Batch scope revision round differs from the RevisionLedger.")
    if set(scope.get("batch_task_ids", [])) != set(intent["selected_task_ids"]):
        raise BatchStoreError("Batch scope task IDs differ from the immutable intent.")
    if set(scope.get("selected_task_ids", [])) != set(
        intent.get("all_selected_task_ids", intent["selected_task_ids"])
    ):
        raise BatchStoreError("Batch scope full task IDs differ from the immutable intent.")
    scope_documents = scope.get("documents")
    if not isinstance(scope_documents, Mapping) or set(scope_documents) != set(intent["documents"]):
        raise BatchStoreError("Batch scope document roles differ from the immutable intent.")

    pointers = load_active_document_pointers(workflow)
    parent_documents: dict[str, dict[str, Any]] = {}
    failed_output_files: dict[str, list[str]] = {}
    for role, record in intent["documents"].items():
        pointer = pointers.get(role)
        parent_file = str(Path(record["parent_file"]).expanduser().resolve())
        if (
            pointer is None
            or pointer["file"] != parent_file
            or pointer["sha256"] != record["parent_sha256"]
            or pointer.get("batch_id") != record.get("parent_batch_id")
        ):
            raise BatchStoreError(
                f"Active parent for {role} no longer matches failed batch {intent['batch_id']}; "
                "recovery is refused and no pointer will be changed."
            )
        scope_row = scope_documents.get(role)
        if (
            not isinstance(scope_row, Mapping)
            or str(Path(str(scope_row.get("input_file", ""))).expanduser().resolve()) != parent_file
            or scope_row.get("input_sha256") != record["parent_sha256"]
            or str(Path(str(scope_row.get("output_file", ""))).expanduser().resolve())
            != str(Path(record["output_file"]).expanduser().resolve())
        ):
            raise BatchStoreError(
                f"Batch scope parent/output contract differs for document role {role}."
            )
        parent_documents[role] = {
            "file": parent_file,
            "sha256": record["parent_sha256"],
            "batch_id": record.get("parent_batch_id"),
        }
        failed_output_files[role] = [
            str(Path(record["output_file"]).expanduser().resolve())
        ]

    patch_ids = scope.get("patch_ids")
    if (
        not isinstance(patch_ids, list)
        or not patch_ids
        or any(not isinstance(item, str) or not item.strip() for item in patch_ids)
        or len(patch_ids) != len(set(patch_ids))
    ):
        raise BatchStoreError("Batch scope must contain unique, non-empty Patch IDs.")
    patch_by_id = {patch.patch_id: patch for patch in revision_ledger.patches}
    missing = sorted(set(patch_ids) - set(patch_by_id))
    if missing:
        raise BatchStoreError(
            "RevisionLedger is missing batch Patch(es): " + ", ".join(missing)
        )

    allowed_states = {"planned", "preflighted", "approved", "applied", "abandoned"}
    patch_statuses_now: dict[str, str] = {}
    for patch_id in patch_ids:
        patch = patch_by_id[patch_id]
        if patch.task_id not in set(intent["selected_task_ids"]):
            raise BatchStoreError(
                f"Patch {patch_id} is outside the selected document-changing task scope."
            )
        if patch.status not in allowed_states:
            raise BatchStoreError(
                f"Patch {patch_id} is {patch.status}; recovery never abandons verified or superseded output."
            )
        patch_statuses_now[patch_id] = patch.status
        for operation in patch.operations:
            role = operation.document
            if role not in intent["documents"]:
                raise BatchStoreError(
                    f"Patch {patch_id} refers to document role outside the batch: {role}."
                )
            role_record = intent["documents"][role]
            if (
                str(Path(operation.input_file).expanduser().resolve())
                != str(Path(role_record["parent_file"]).expanduser().resolve())
                or str(Path(operation.output_file).expanduser().resolve())
                != str(Path(role_record["output_file"]).expanduser().resolve())
                or operation.source_sha256 not in {None, role_record["parent_sha256"]}
            ):
                raise BatchStoreError(
                    f"Patch {patch_id} operation {operation.operation_id} does not match "
                    f"the immutable parent/output contract for {role}."
                )

    artifact_inventory = _batch_artifact_inventory(root)
    workflow_sha_now = sha256_file(workflow_file)
    ledger_sha_now = sha256_file(ledger_file)
    workflow_payload_sha_now = _json_payload_sha256(workflow)
    ledger_payload_sha_now = _json_payload_sha256(revision_ledger.to_dict())
    recovery_intent_path = root / "batch_recovery_intent.json"

    if recovery_intent_path.exists():
        try:
            recovery_intent = json.loads(recovery_intent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BatchStoreError(f"Cannot read immutable batch recovery intent: {exc}") from exc
        workflow_after = (
            recovery_intent.get("workflow_after")
            if isinstance(recovery_intent, Mapping) else None
        )
        if (
            not isinstance(recovery_intent, Mapping)
            or recovery_intent.get("batch_id") != intent["batch_id"]
            or recovery_intent.get("batch_intent_sha256") != intent_sha
            or recovery_intent.get("batch_scope_sha256") != scope_sha
            or recovery_intent.get("parent_documents") != parent_documents
            or recovery_intent.get("failed_output_files") != failed_output_files
            or recovery_intent.get("artifact_inventory") != artifact_inventory
            or recovery_intent.get("patch_ids") != patch_ids
            or not isinstance(workflow_after, Mapping)
            or _json_payload_sha256(workflow_after)
            != recovery_intent.get("workflow_ledger_sha256_after")
        ):
            raise BatchStoreError(
                "Existing recovery intent no longer matches the batch, active parent, "
                "retained artifacts or staged Workflow Ledger."
            )
        _validate_recovery_workflow_invalidation_evidence(
            recovery_intent,
            intent,
            workflow_after,
        )
        workflow_payload_sha_now = _json_payload_sha256(workflow)
        workflow_payload_hashes = {
            recovery_intent.get("workflow_ledger_payload_sha256_before"),
            recovery_intent.get("workflow_ledger_sha256_after"),
        }
        if (
            not all(isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) for value in workflow_payload_hashes)
            or workflow_payload_sha_now not in workflow_payload_hashes
        ):
            raise BatchStoreError(
                "Workflow Ledger changed outside the staged batch recovery transaction."
            )
        workflow_changed = (
            workflow_payload_sha_now
            == recovery_intent.get("workflow_ledger_payload_sha256_before")
        )
        if not workflow_changed and dict(workflow) != dict(workflow_after):
            raise BatchStoreError(
                "Workflow Ledger hash matches recovery intent but its parsed content differs."
            )

        revision_events = recovery_intent.get("revision_events")
        if not isinstance(revision_events, list) or len(revision_events) != len(patch_ids):
            raise BatchStoreError("Recovery intent has an incomplete RevisionLedger event set.")
        event_subject_ids = {
            row.get("subject_id") for row in revision_events if isinstance(row, Mapping)
        }
        if event_subject_ids != set(patch_ids):
            raise BatchStoreError("Recovery intent Patch event set differs from batch scope.")

        existing_events = {
            event.subject_id: event
            for event in revision_ledger.events
            if event.subject_type == "patch"
            and event.event_type == "patch_abandoned_after_batch_recovery"
            and event.details.get("recovery_id") == recovery_intent.get("recovery_id")
        }
        if existing_events:
            if (
                set(existing_events) != set(patch_ids)
                or any(patch_by_id[pid].status not in {"abandoned", "superseded"} for pid in patch_ids)
            ):
                raise BatchStoreError(
                    "RevisionLedger contains only a partial or inconsistent recovery update."
                )
            if ledger_payload_sha_now != recovery_intent.get("revision_ledger_sha256_after"):
                raise BatchStoreError(
                    "RevisionLedger changed after recovery was staged; refusing to seal a different state."
                )
            candidate_ledger = revision_ledger
            ledger_changed = False
        else:
            if ledger_payload_sha_now != recovery_intent.get("revision_ledger_payload_sha256_before"):
                raise BatchStoreError(
                    "RevisionLedger changed after recovery intent was staged but before its atomic update."
                )
            if any(patch_statuses_now[pid] == "abandoned" for pid in patch_ids):
                raise BatchStoreError(
                    "A Patch is abandoned without the matching staged recovery events."
                )
            candidate_ledger = copy.deepcopy(revision_ledger)
            candidate_by_id = {patch.patch_id: patch for patch in candidate_ledger.patches}
            for patch_id in patch_ids:
                candidate_by_id[patch_id].status = "abandoned"
            candidate_ledger.events.extend(
                RevisionEvent(
                    event_id=row["event_id"],
                    subject_id=row["subject_id"],
                    subject_type=row["subject_type"],
                    event_type=row["event_type"],
                    timestamp_utc=row["timestamp_utc"],
                    actor=row["actor"],
                    details=row["details"],
                )
                for row in revision_events
            )
            candidate_ledger.validate_or_raise()
            if (
                _json_payload_sha256(candidate_ledger.to_dict())
                != recovery_intent.get("revision_ledger_sha256_after")
            ):
                raise BatchStoreError(
                    "Reconstructed RevisionLedger does not match the immutable recovery intent."
                )
            ledger_changed = True

        return (
            dict(workflow_after),
            candidate_ledger,
            dict(recovery_intent),
            workflow_changed,
            ledger_changed,
            False,
        )

    if any(status == "abandoned" for status in patch_statuses_now.values()):
        raise BatchStoreError(
            "A Patch is already abandoned without a recovery intent; inspect its history before proceeding."
        )
    now = _timestamp()
    recovery_id = "recovery-" + uuid.uuid4().hex
    recovery_actor = actor.strip() if isinstance(actor, str) and actor.strip() else ""
    recovery_reason = reason.strip() if isinstance(reason, str) and reason.strip() else ""
    if not recovery_actor or not recovery_reason:
        raise BatchStoreError("Recovery actor and reason must be non-empty.")

    workflow_after = _prepare_recovery_workflow(
        workflow,
        intent.get("all_selected_task_ids", intent["selected_task_ids"]),
        batch_id=intent["batch_id"],
        actor=recovery_actor,
        reason=recovery_reason,
        timestamp_utc=now,
    )
    candidate_ledger = copy.deepcopy(revision_ledger)
    candidate_by_id = {patch.patch_id: patch for patch in candidate_ledger.patches}
    revision_events = []
    for patch_id in patch_ids:
        event = RevisionEvent(
            event_id=str(uuid.uuid4()),
            subject_id=patch_id,
            subject_type="patch",
            event_type="patch_abandoned_after_batch_recovery",
            timestamp_utc=now,
            actor=recovery_actor,
            details={
                "recovery_id": recovery_id,
                "batch_id": intent["batch_id"],
                "reason": recovery_reason,
                "parent_documents": parent_documents,
                "failed_output_files": failed_output_files,
                "artifact_inventory": artifact_inventory,
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
            },
        )
        revision_events.append(event.to_dict())
        candidate_by_id[patch_id].status = "abandoned"
    candidate_ledger.events.extend(
        RevisionEvent(
            event_id=row["event_id"],
            subject_id=row["subject_id"],
            subject_type=row["subject_type"],
            event_type=row["event_type"],
            timestamp_utc=row["timestamp_utc"],
            actor=row["actor"],
            details=row["details"],
        )
        for row in revision_events
    )
    candidate_ledger.validate_or_raise()

    recovery_intent = {
        "schema_version": "1.0",
        "recovery_id": recovery_id,
        "batch_id": intent["batch_id"],
        "created_at_utc": now,
        "actor": recovery_actor,
        "reason": recovery_reason,
        "workflow_ledger_file": str(workflow_file),
        "workflow_ledger_sha256_before": workflow_sha_now,
        "workflow_ledger_sha256_after": _json_payload_sha256(workflow_after),
        "workflow_ledger_payload_sha256_before": workflow_payload_sha_now,
        "workflow_after": workflow_after,
        "revision_ledger_file": str(ledger_file),
        "revision_ledger_sha256_before": ledger_sha_now,
        "revision_ledger_sha256_after": _json_payload_sha256(candidate_ledger.to_dict()),
        "revision_ledger_payload_sha256_before": ledger_payload_sha_now,
        "batch_intent_sha256": intent_sha,
        "batch_scope_sha256": scope_sha,
        "selected_task_ids": list(intent.get("all_selected_task_ids", intent["selected_task_ids"])),
        "batch_task_ids": list(intent["selected_task_ids"]),
        "invalidated_task_ids": sorted({
            row.get("task_id")
            for row in workflow_after.get("tasks", [])
            if isinstance(row, Mapping)
            and isinstance(row.get("task_id"), str)
            and row.get("status") == "recheck_required"
            and row.get("invalidation_reason") == (
                f"Unpublished batch {intent['batch_id']} abandoned after Apply failure: {recovery_reason}"
            )
        }),
        "patch_ids": list(patch_ids),
        "patch_statuses_before": patch_statuses_now,
        "parent_documents": parent_documents,
        "failed_output_files": failed_output_files,
        "artifact_inventory": artifact_inventory,
        "revision_events": revision_events,
        "status": "prepared",
    }
    write_json_new(recovery_intent_path, recovery_intent)
    return workflow_after, candidate_ledger, recovery_intent, True, True, False

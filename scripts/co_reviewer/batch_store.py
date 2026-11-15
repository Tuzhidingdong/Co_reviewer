# -*- coding: utf-8 -*-
"""Append-only batch storage and atomic active-document pointer publication."""
from __future__ import annotations

import json
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


def create_batch_intent(
    workflow: Mapping[str, Any],
    workflow_path: str | Path,
    selected_task_ids: Sequence[str],
    roles: Sequence[str],
    *,
    runs_dir: str | Path | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Create a unique batch directory and immutable parent/output intent manifest."""
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise BatchStoreError("Cannot prepare a batch from an invalid Workflow Ledger:\n- " + "\n- ".join(errors))
    if not selected_task_ids:
        raise BatchStoreError("A batch must contain at least one selected task.")
    unique_roles = list(dict.fromkeys(roles))
    if not unique_roles:
        raise BatchStoreError("A document-changing batch must contain at least one target document role.")
    pointers = load_active_document_pointers(workflow)
    unknown_roles = sorted(set(unique_roles) - set(pointers))
    if unknown_roles:
        raise BatchStoreError("No active document pointer exists for: " + ", ".join(unknown_roles))
    workflow_file = Path(workflow_path).expanduser().resolve()
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
        "workflow_ledger_sha256_at_prepare": sha256_file(workflow_file),
        "created_at_utc": _timestamp(),
        "selected_task_ids": list(dict.fromkeys(selected_task_ids)),
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
    return {**intent, "batch_dir": str(batch_dir), "batch_intent_file": str(batch_dir / "batch_intent.json")}


def read_batch_intent(batch_dir: str | Path) -> dict[str, Any]:
    root = Path(batch_dir).expanduser().resolve()
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
        if current["file"] != str(Path(record["parent_file"]).expanduser().resolve()) or current["sha256"] != record["parent_sha256"]:
            raise BatchStoreError(
                f"Active pointer for {role} changed after batch preparation; refusing stale publish."
            )
        output_path = Path(record["output_file"]).expanduser().resolve()
        if not output_path.is_file():
            raise BatchStoreError(f"Batch output for {role} is missing: {output_path}")
        output_sha = sha256_file(output_path)
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
        role_updates[role] = {
            "file": str(output_path),
            "sha256": output_sha,
            "batch_id": batch_id,
            "updated_at_utc": _timestamp(),
        }
    updated = json.loads(json.dumps(workflow, ensure_ascii=False))
    active = dict(updated.get("active_documents") or {})
    active.update(role_updates)
    updated["active_documents"] = active
    task_ids = list(intent.get("selected_task_ids", []))
    events = list(updated.get("events", []))
    now = _timestamp()
    for task_id in task_ids:
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
        if current is None or current.get("batch_id") != batch_id or current["file"] != output_path:
            raise BatchStoreError(
                f"Active pointer for {role} no longer points to batch {batch_id}; refusing rollback."
            )
        parent_path = Path(record["parent_file"]).expanduser().resolve()
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
    active = dict(updated.get("active_documents") or {})
    active.update(restored)
    updated["active_documents"] = active
    now = _timestamp()
    for task_id in intent.get("selected_task_ids", []):
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
        "selected_task_ids": list(intent.get("selected_task_ids", [])),
        "status": "rolled_back",
        "retained_batch_files": True,
    }
    return updated, receipt

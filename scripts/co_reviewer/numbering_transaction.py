# -*- coding: utf-8 -*-
"""Hash-bound package-wide numbering plans and verified numbering transactions.

This module does not edit DOCX files. All document changes must already have
passed the ordinary Patch / Apply / Verify / Publish path before the package
transaction can be committed here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .identity import sha256_file
from .v232_contract import (
    ContractError,
    build_package_reference_graph,
    commit_document_package,
    extract_docx_reference_segments,
    renumber_reference_graph,
    validate_committed_document_package,
)


class NumberingTransactionError(ValueError):
    """Raised when a package-wide numbering plan or commit is not safe."""


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _without_key(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in payload.items() if name != key}


def _assert_no_symlink_path(path_value: str | Path, label: str) -> Path:
    requested = Path(path_value).expanduser()
    candidate = Path(os.path.abspath(str(requested)))
    probe = candidate
    while True:
        if probe.is_symlink():
            raise NumberingTransactionError(
                f"{label} path must not traverse a symbolic link: {probe}"
            )
        if probe.parent == probe:
            break
        probe = probe.parent
    return candidate.resolve()


def _require_aware_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise NumberingTransactionError(f"{label} must be a timezone-aware ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NumberingTransactionError(f"{label} is not a valid ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NumberingTransactionError(f"{label} must include a timezone offset.")
    return value


def _validate_impact_plan(plan: Any) -> dict[str, Any]:
    if not isinstance(plan, Mapping) or plan.get("schema_version") != "1.0":
        raise NumberingTransactionError("Numbering impact plan must be a schema_version 1.0 object.")
    claimed = plan.get("impact_plan_sha256")
    if (
        not isinstance(claimed, str)
        or not re.fullmatch(r"[a-f0-9]{64}", claimed)
        or _canonical_sha256(_without_key(plan, "impact_plan_sha256")) != claimed
    ):
        raise NumberingTransactionError("Numbering impact plan hash is missing or does not match its exact contents.")
    if plan.get("status") != "awaiting_authorization":
        raise NumberingTransactionError("Numbering impact plan is not awaiting author authorization.")
    if plan.get("numbering_mutation_authorized") is not False:
        raise NumberingTransactionError("Impact-plan generation must not authorize or apply numbering changes.")
    source_documents = plan.get("source_documents")
    affected_roles = plan.get("affected_document_roles")
    proposed_graph = plan.get("proposed_graph")
    source_graph_sha = plan.get("source_graph_sha256")
    allowed_roles = {"Reviewers", "Manuscript", "SI"}
    if (
        not isinstance(source_documents, Mapping) or not source_documents
        or not isinstance(affected_roles, list) or not affected_roles
        or not isinstance(proposed_graph, Mapping)
        or not isinstance(source_graph_sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", source_graph_sha)
    ):
        raise NumberingTransactionError("Numbering impact plan is missing source documents, affected roles, source graph hash or proposed graph.")
    proposed_roles = proposed_graph.get("documents")
    if (
        not isinstance(proposed_roles, list)
        or set(proposed_roles) != set(source_documents)
        or len(proposed_roles) != len(set(proposed_roles))
        or set(source_documents) - allowed_roles
    ):
        raise NumberingTransactionError("Numbering impact plan document roles must exactly match supported Workflow roles.")
    for role, source in source_documents.items():
        if (
            not isinstance(role, str) or not role
            or not isinstance(source, Mapping)
            or not isinstance(source.get("file"), str) or not source.get("file")
            or not isinstance(source.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", source["sha256"])
        ):
            raise NumberingTransactionError(f"Numbering impact plan source record is invalid for role {role!r}.")
    definition_updates = plan.get("definition_updates")
    mention_updates = plan.get("mention_updates")
    change_counts = plan.get("change_counts")
    if not isinstance(definition_updates, list) or not isinstance(mention_updates, list) or not isinstance(change_counts, Mapping):
        raise NumberingTransactionError("Numbering impact plan change lists or counts are malformed.")
    actual_affected_roles = sorted({
        row.get("document")
        for row in [*definition_updates, *mention_updates]
        if isinstance(row, Mapping) and isinstance(row.get("document"), str)
    })
    if (
        affected_roles != actual_affected_roles
        or set(affected_roles) - set(source_documents)
        or change_counts.get("definitions") != len(definition_updates)
        or change_counts.get("mentions") != len(mention_updates)
        or change_counts.get("documents") != len(affected_roles)
    ):
        raise NumberingTransactionError("Numbering impact plan affected-role list or change counts do not match its exact update records.")
    return dict(plan)


def validate_numbering_impact_approval(
    plan: Mapping[str, Any],
    approval: Any,
    *,
    expected_plan_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a separate author approval bound to the exact impact-plan hash."""
    actual_plan = _validate_impact_plan(plan)
    plan_sha = actual_plan["impact_plan_sha256"]
    if expected_plan_sha256 is not None and expected_plan_sha256 != plan_sha:
        raise NumberingTransactionError("The approved-plan SHA-256 does not match the supplied impact plan.")
    if not isinstance(approval, Mapping) or approval.get("schema_version") != "1.0":
        raise NumberingTransactionError("Numbering approval must be a schema_version 1.0 object.")
    claimed_approval_sha = approval.get("approval_sha256")
    if (
        not isinstance(claimed_approval_sha, str)
        or not re.fullmatch(r"[a-f0-9]{64}", claimed_approval_sha)
        or _canonical_sha256(_without_key(approval, "approval_sha256")) != claimed_approval_sha
    ):
        raise NumberingTransactionError("Numbering approval hash is missing or invalid.")
    if (
        approval.get("status") != "authorized"
        or approval.get("decision_type") != "package_numbering"
        or approval.get("impact_plan_sha256") != plan_sha
        or not isinstance(approval.get("approved_by"), str)
        or not approval.get("approved_by", "").strip()
    ):
        raise NumberingTransactionError("Numbering approval is not bound to this exact impact plan and author.")
    _require_aware_timestamp(approval.get("approved_at_utc"), "approved_at_utc")
    return dict(approval)


def build_numbering_impact_plan(
    graph: Mapping[str, Any],
    number_map: Mapping[str, str],
    source_documents: Mapping[str, str | Path],
    *,
    confirmed_ambiguous_mappings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create a complete, non-authorizing package-wide impact plan from exact DOCX snapshots."""
    if not isinstance(graph, Mapping) or not isinstance(number_map, Mapping):
        raise NumberingTransactionError("Reference graph and typed number map must be JSON objects.")
    graph_roles = graph.get("documents")
    allowed_roles = {"Reviewers", "Manuscript", "SI"}
    if (
        not isinstance(graph_roles, list) or not graph_roles
        or any(not isinstance(role, str) or not role.strip() for role in graph_roles)
        or len(graph_roles) != len(set(graph_roles))
        or set(graph_roles) - allowed_roles
    ):
        raise NumberingTransactionError("Reference graph document roles must be unique supported Workflow roles.")
    if not isinstance(source_documents, Mapping) or set(source_documents) != set(graph_roles):
        raise NumberingTransactionError(
            "Every graph document role must have exactly one source DOCX; omissions and extra documents are blocked."
        )

    source_records: dict[str, dict[str, str]] = {}
    extracted: dict[str, list[dict[str, Any]]] = {}
    canonical_paths: set[str] = set()
    for role in sorted(graph_roles):
        if not isinstance(role, str) or not role.strip():
            raise NumberingTransactionError("Reference graph contains an invalid document role.")
        path = _assert_no_symlink_path(source_documents[role], f"Source DOCX for {role}")
        if not path.is_file() or path.suffix.casefold() != ".docx":
            raise NumberingTransactionError(f"Source DOCX for {role} is missing or is not a DOCX: {path}")
        canonical = os.path.normcase(str(path))
        if canonical in canonical_paths:
            raise NumberingTransactionError("Two graph roles resolve to the same source DOCX path.")
        canonical_paths.add(canonical)
        source_records[role] = {"file": str(path), "sha256": sha256_file(path)}
        extracted[role] = extract_docx_reference_segments(path)

    rebuilt_source_graph = build_package_reference_graph(extracted)
    if _canonical_sha256(dict(graph)) != _canonical_sha256(rebuilt_source_graph):
        raise NumberingTransactionError(
            "Reference graph does not match the exact source DOCX snapshots; rebuild the graph before planning."
        )

    confirmed = dict(confirmed_ambiguous_mappings or {})
    try:
        proposed = renumber_reference_graph(
            graph,
            number_map,
            explicit_authorization=True,
            confirmed_ambiguous_mappings=confirmed,
        )
    except ContractError as exc:
        raise NumberingTransactionError(str(exc)) from exc
    proposed["numbering_mutation_authorized"] = False

    definition_updates = proposed.get("definition_updates", [])
    mention_updates = proposed.get("mention_updates", [])
    affected_roles = sorted({
        row.get("document")
        for row in [*definition_updates, *mention_updates]
        if isinstance(row, Mapping) and isinstance(row.get("document"), str)
    })
    if not affected_roles:
        raise NumberingTransactionError("Number map produced no concrete definition or mention updates.")
    if set(affected_roles) - set(source_records):
        raise NumberingTransactionError("Numbering impact includes a role outside the exact source graph.")

    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "awaiting_authorization",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_graph_sha256": _canonical_sha256(dict(graph)),
        "source_documents": source_records,
        "number_map": dict(number_map),
        "confirmed_ambiguous_mappings": confirmed,
        "affected_document_roles": affected_roles,
        "definition_updates": copy.deepcopy(definition_updates),
        "mention_updates": copy.deepcopy(mention_updates),
        "change_counts": {
            "definitions": len(definition_updates),
            "mentions": len(mention_updates),
            "documents": len(affected_roles),
        },
        "proposed_graph": proposed,
        "numbering_mutation_authorized": False,
        "authorization_required": True,
    }
    plan["impact_plan_sha256"] = _canonical_sha256(plan)
    return plan


def authorize_numbering_impact_plan(
    plan: Mapping[str, Any],
    approved_plan_sha256: str,
    approved_by: str,
    *,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create a separate approval artifact only when the author confirms the exact plan SHA."""
    actual_plan = _validate_impact_plan(plan)
    if approved_plan_sha256 != actual_plan["impact_plan_sha256"]:
        raise NumberingTransactionError(
            "Author approval rejected: approved-plan SHA-256 must exactly match the displayed impact plan."
        )
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise NumberingTransactionError("Author identity is required for numbering authorization.")
    timestamp = approved_at_utc or datetime.now(timezone.utc).isoformat()
    _require_aware_timestamp(timestamp, "approved_at_utc")
    approval: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "authorized",
        "decision_type": "package_numbering",
        "impact_plan_sha256": approved_plan_sha256,
        "approved_by": approved_by.strip(),
        "approved_at_utc": timestamp,
    }
    approval["approval_sha256"] = _canonical_sha256(approval)
    return approval


def _definition_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("document"), row.get("part"), row.get("paragraph_index"),
        row.get("kind"), str(row.get("number")), row.get("text"),
    )


def _mention_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    resolution = row.get("resolution")
    if resolution == "confirmed_by_user":
        resolution = "resolved"
    return (
        row.get("document"), row.get("part"), row.get("paragraph_index"),
        row.get("kind"), str(row.get("number")), row.get("text"),
        row.get("surface_text"), resolution,
    )


def _graph_projection(graph: Mapping[str, Any]) -> dict[str, Any]:
    definitions = graph.get("definitions", [])
    mentions = graph.get("mentions", [])
    fields = graph.get("field_references", [])
    edges = graph.get("edges", [])
    if not all(isinstance(rows, list) for rows in (definitions, mentions, fields, edges)):
        raise NumberingTransactionError("Reference graph has malformed definitions, mentions, fields or edges.")

    definitions_by_id = {
        row.get("id"): _definition_signature(row)
        for row in definitions if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    mentions_by_id = {
        row.get("id"): _mention_signature(row)
        for row in mentions if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }
    edge_projection: list[tuple[Any, ...]] = []
    for edge in edges:
        if not isinstance(edge, Mapping):
            raise NumberingTransactionError("Reference graph contains a malformed edge.")
        mention_sig = mentions_by_id.get(edge.get("mention_id"))
        definition_sig = definitions_by_id.get(edge.get("target_definition_id"))
        if mention_sig is None or definition_sig is None:
            raise NumberingTransactionError("Reference graph edge points to an unknown mention or definition.")
        edge_projection.append((mention_sig, definition_sig))

    field_projection = []
    for row in fields:
        if not isinstance(row, Mapping):
            raise NumberingTransactionError("Reference graph contains a malformed native-field record.")
        field_projection.append((
            row.get("document"), row.get("part"), row.get("paragraph_index"),
            row.get("field_type"), row.get("instruction"), row.get("display_text"),
        ))

    return {
        "definitions": Counter(_definition_signature(row) for row in definitions if isinstance(row, Mapping)),
        "mentions": Counter(_mention_signature(row) for row in mentions if isinstance(row, Mapping)),
        "edges": Counter(edge_projection),
        "fields": Counter(field_projection),
        "unresolved_count": graph.get("unresolved_count"),
        "ambiguous_count": graph.get("ambiguous_count"),
        "manual_field_review_count": graph.get("manual_field_review_count"),
    }


def _rebuild_approved_numbering_proposal(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Re-derive the proposal from exact source DOCX snapshots; never trust a self-hashed proposed graph."""
    number_map = plan.get("number_map")
    confirmed = plan.get("confirmed_ambiguous_mappings")
    if not isinstance(number_map, Mapping) or not isinstance(confirmed, Mapping):
        raise NumberingTransactionError(
            "Approved numbering plan must retain its typed number map and ambiguity confirmations."
        )

    source_documents = plan["source_documents"]
    extracted: dict[str, list[dict[str, Any]]] = {}
    for role in sorted(source_documents):
        source = source_documents[role]
        path = _assert_no_symlink_path(source["file"], f"Approved source DOCX for {role}")
        if (
            not path.is_file()
            or path.suffix.casefold() != ".docx"
            or sha256_file(path) != source["sha256"]
        ):
            raise NumberingTransactionError(
                f"Approved source DOCX for {role} is missing, not a DOCX, or no longer matches its approved SHA-256."
            )
        extracted[role] = extract_docx_reference_segments(path)

    source_graph = build_package_reference_graph(extracted)
    rebuilt_source_sha = _canonical_sha256(source_graph)
    if rebuilt_source_sha != plan.get("source_graph_sha256"):
        raise NumberingTransactionError(
            "Approved source DOCX snapshots no longer rebuild the exact source Reference Graph."
        )
    try:
        recomputed = renumber_reference_graph(
            source_graph,
            number_map,
            explicit_authorization=True,
            confirmed_ambiguous_mappings=confirmed,
        )
    except ContractError as exc:
        raise NumberingTransactionError(
            f"Approved numbering proposal cannot be reproduced from its source graph: {exc}"
        ) from exc
    recomputed["numbering_mutation_authorized"] = False
    if _canonical_sha256(recomputed) != _canonical_sha256(plan["proposed_graph"]):
        raise NumberingTransactionError(
            "Numbering impact plan proposed_graph is not the deterministic result of its exact source snapshots and mappings."
        )
    return recomputed


def verify_numbering_package_outputs(
    plan: Mapping[str, Any],
    output_documents: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Rebuild the complete graph from final DOCX files and compare it with the approved proposal."""
    actual_plan = _validate_impact_plan(plan)
    _rebuild_approved_numbering_proposal(actual_plan)
    expected_roles = set(actual_plan["source_documents"])
    if not isinstance(output_documents, Mapping) or set(output_documents) != expected_roles:
        raise NumberingTransactionError(
            "Final package output roles must exactly match every document role in the approved source graph."
        )
    output_records: dict[str, dict[str, str]] = {}
    extracted: dict[str, list[dict[str, Any]]] = {}
    for role in sorted(expected_roles):
        path = _assert_no_symlink_path(output_documents[role], f"Final DOCX for {role}")
        if not path.is_file() or path.suffix.casefold() != ".docx":
            raise NumberingTransactionError(f"Final package DOCX for {role} is missing: {path}")
        output_records[role] = {"file": str(path), "sha256": sha256_file(path)}
        extracted[role] = extract_docx_reference_segments(path)

    final_graph = build_package_reference_graph(extracted)
    expected_graph = actual_plan["proposed_graph"]
    expected_projection = _graph_projection(expected_graph)
    actual_projection = _graph_projection(final_graph)
    for key in ("definitions", "mentions", "edges", "fields"):
        if expected_projection[key] != actual_projection[key]:
            missing = expected_projection[key] - actual_projection[key]
            extra = actual_projection[key] - expected_projection[key]
            raise NumberingTransactionError(
                f"Post-publication Reference Graph mismatch for {key}: "
                f"{sum(missing.values())} expected item(s) missing; {sum(extra.values())} unexpected item(s)."
            )
    for key in ("unresolved_count", "ambiguous_count", "manual_field_review_count"):
        if expected_projection[key] != actual_projection[key]:
            raise NumberingTransactionError(
                f"Post-publication Reference Graph {key} differs from the author-approved impact plan."
            )
    return {
        "status": "PASS",
        "output_documents": output_records,
        "final_graph_sha256": _canonical_sha256(final_graph),
        "final_graph": final_graph,
        "graph_counts": {
            "definitions": len(final_graph.get("definitions", [])),
            "mentions": len(final_graph.get("mentions", [])),
            "edges": len(final_graph.get("edges", [])),
            "unresolved": final_graph.get("unresolved_count"),
            "ambiguous": final_graph.get("ambiguous_count"),
            "native_fields_for_manual_review": final_graph.get("manual_field_review_count"),
        },
    }


def validate_numbering_patch_operation_coverage(
    plan: Mapping[str, Any],
    workflow_tasks: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Require normal PatchOperations to cover exactly every changed reference paragraph."""
    expected: set[tuple[str, str, int]] = set()
    for field in ("definition_updates", "mention_updates"):
        rows = plan.get(field)
        if not isinstance(rows, list):
            raise NumberingTransactionError(f"Numbering impact plan {field} must be an array.")
        for row in rows:
            if not isinstance(row, Mapping):
                raise NumberingTransactionError(f"Numbering impact plan {field} contains a malformed update.")
            role, part, p_idx = (
                row.get("document"), row.get("part"), row.get("paragraph_index")
            )
            if (
                not isinstance(role, str) or not role
                or not isinstance(part, str) or not part.startswith("word/")
                or not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0
            ):
                raise NumberingTransactionError(
                    f"Numbering impact plan {field} contains an update without an exact role/part/paragraph target."
                )
            expected.add((role, part, p_idx))
    if not expected:
        raise NumberingTransactionError("Numbering impact plan contains no target paragraph updates.")

    actual: set[tuple[str, str, int]] = set()
    for task in workflow_tasks:
        operations = task.get("patch_operations")
        if not isinstance(operations, list) or not operations:
            raise NumberingTransactionError(
                f"Global-numbering task {task.get('task_id')} must contain one or more PatchOperations."
            )
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise NumberingTransactionError("Global-numbering task contains a malformed PatchOperation.")
            role, part = operation.get("document"), operation.get("part")
            p_idx = operation.get("p_idx")
            target_ref = operation.get("target_ref")
            if isinstance(target_ref, Mapping):
                if target_ref.get("part") != part:
                    raise NumberingTransactionError(
                        f"PatchOperation {operation.get('operation_id')} TargetRef part differs from its operation part."
                    )
                hint = target_ref.get("location_hint")
                if isinstance(hint, Mapping):
                    ref_p_idx = hint.get("p_idx")
                    if p_idx is not None and p_idx != ref_p_idx:
                        raise NumberingTransactionError(
                            f"PatchOperation {operation.get('operation_id')} p_idx differs from its stable TargetRef hint."
                        )
                    p_idx = ref_p_idx
            if (
                not isinstance(role, str) or not role
                or not isinstance(part, str) or not part.startswith("word/")
                or not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0
            ):
                raise NumberingTransactionError(
                    f"PatchOperation {operation.get('operation_id')} has no exact role/part/paragraph target."
                )
            actual.add((role, part, p_idx))

    if expected != actual:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise NumberingTransactionError(
            "Global-numbering PatchOperations do not cover exactly the approved Reference Graph update paragraphs. "
            f"Missing={missing!r}; unexpected={unexpected!r}."
        )
    return {
        "status": "PASS",
        "target_paragraphs": [
            {"document": role, "part": part, "paragraph_index": p_idx}
            for role, part, p_idx in sorted(expected)
        ],
        "target_paragraph_count": len(expected),
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NumberingTransactionError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise NumberingTransactionError(f"{label} must be a JSON object.")
    return payload


def _write_receipt_new(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError:
        existing = _load_json_object(path, "Numbering transaction receipt")
        if existing != dict(payload):
            raise NumberingTransactionError("An existing numbering transaction receipt conflicts with this transaction.")


def _existing_numbering_package_receipt(
    package_root: Path,
    *,
    plan_sha: str,
    approval_sha: str,
    batch_id: str,
) -> dict[str, Any] | None:
    markers = sorted(package_root.glob("COMMITTED-*.json")) if package_root.exists() else []
    matched: list[dict[str, Any]] = []
    for marker_path in markers:
        marker = _load_json_object(marker_path, "Package commit marker")
        package_id = marker.get("package_id")
        if not isinstance(package_id, str) or marker_path.name != f"COMMITTED-{package_id}.json":
            raise NumberingTransactionError("Committed numbering package has a malformed commit marker.")
        version_dir = package_root / f"package-{package_id}"
        manifest_path = version_dir / "package_manifest.json"
        try:
            manifest = validate_committed_document_package(version_dir, marker_path)
        except ContractError as exc:
            raise NumberingTransactionError(
                f"Committed numbering package failed integrity validation: {exc}"
            ) from exc
        metadata = manifest.get("metadata")
        tx = metadata.get("numbering_transaction") if isinstance(metadata, Mapping) else None
        if (
            isinstance(tx, Mapping)
            and tx.get("impact_plan_sha256") == plan_sha
            and tx.get("approval_sha256") == approval_sha
            and tx.get("batch_id") == batch_id
        ):
            matched.append({
                "package_id": package_id,
                "package_directory": str(version_dir),
                "package_manifest_sha256": marker["manifest_sha256"],
                "commit_marker": str(marker_path),
                "commit_marker_sha256": sha256_file(marker_path),
                "package_manifest": manifest,
            })
    if len(matched) > 1:
        raise NumberingTransactionError("More than one committed package exists for the same numbering transaction.")
    if matched:
        return matched[0]
    if markers:
        raise NumberingTransactionError(
            "The numbering transaction directory already contains a different committed package; refusing a duplicate commit."
        )
    return None


def commit_numbering_package_transaction(
    plan: Mapping[str, Any],
    approval: Mapping[str, Any],
    *,
    batch_dir: str | Path,
    workflow_ledger_path: str | Path,
    revision_ledger_path: str | Path,
    revision_ledger: Any,
    release_root: str | Path,
) -> dict[str, Any]:
    """Commit only a fully verified, author-approved, all-role numbering batch."""
    actual_plan = _validate_impact_plan(plan)
    actual_approval = validate_numbering_impact_approval(
        actual_plan, approval, expected_plan_sha256=actual_plan["impact_plan_sha256"]
    )

    batch_root = _assert_no_symlink_path(batch_dir, "Numbering batch")
    workflow_path = _assert_no_symlink_path(workflow_ledger_path, "Workflow Ledger")
    revision_path = _assert_no_symlink_path(revision_ledger_path, "RevisionLedger")
    output_root = _assert_no_symlink_path(release_root, "Numbering package output root")
    if not batch_root.is_dir() or not workflow_path.is_file() or not revision_path.is_file():
        raise NumberingTransactionError("Numbering commit requires an existing batch and both persisted ledgers.")
    if workflow_path == revision_path:
        raise NumberingTransactionError("Workflow Ledger and RevisionLedger must be separate files.")

    workflow_sha_before = sha256_file(workflow_path)
    workflow = _load_json_object(workflow_path, "Workflow Ledger")
    revision_sha_before = sha256_file(revision_path)
    if sha256_file(workflow_path) != workflow_sha_before or sha256_file(revision_path) != revision_sha_before:
        raise NumberingTransactionError("Workflow or RevisionLedger changed while loading the transaction snapshot.")

    from .workflow_engine import validate_workflow_ledger
    from .batch_store import (
        _validate_committed_batch_publication,
        read_batch_intent,
        validate_batch_creation_receipt,
    )

    workflow_errors = validate_workflow_ledger(workflow)
    if workflow_errors:
        raise NumberingTransactionError("Workflow Ledger is invalid:\n- " + "\n- ".join(workflow_errors))
    intent = read_batch_intent(batch_root)
    batch_id = intent.get("batch_id")
    if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", batch_id):
        raise NumberingTransactionError("Numbering batch ID is invalid.")
    if Path(str(intent.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path:
        raise NumberingTransactionError("Numbering batch belongs to another Workflow Ledger.")
    if intent.get("revision_round_id") != workflow.get("revision_round_id"):
        raise NumberingTransactionError("Numbering batch belongs to another review round.")
    if intent.get("batch_creation_protocol_version") == "1.0":
        creation = validate_batch_creation_receipt(batch_root, intent)
        if creation.get("status") != "committed":
            raise NumberingTransactionError("Numbering batch creation transaction was not committed.")

    scope_path = batch_root / "batch_scope.json"
    publication_path = batch_root / "batch_published.json"
    if not scope_path.is_file() or not publication_path.is_file():
        raise NumberingTransactionError(
            "Numbering Package Transaction requires a fully published batch with immutable scope and receipt."
        )
    scope = _load_json_object(scope_path, "Batch scope")
    publication = _load_json_object(publication_path, "Batch publication receipt")
    if Path(str(scope.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path:
        raise NumberingTransactionError("Numbering batch scope belongs to another RevisionLedger.")
    patch_statuses = {
        patch.patch_id: patch.status
        for patch in getattr(revision_ledger, "patches", [])
    }
    try:
        published = _validate_committed_batch_publication(
            batch_root, intent, workflow, patch_statuses
        )
    except Exception as exc:
        raise NumberingTransactionError(f"Published numbering batch failed its immutable receipt checks: {exc}") from exc

    affected_roles = set(actual_plan["affected_document_roles"])
    intent_documents = intent.get("documents")
    scope_documents = scope.get("documents")
    if (
        not isinstance(intent_documents, Mapping)
        or not isinstance(scope_documents, Mapping)
        or set(intent_documents) != affected_roles
        or set(scope_documents) != affected_roles
    ):
        raise NumberingTransactionError(
            "The published numbering batch must contain every and only document role affected by the approved impact plan."
        )
    task_ids = intent.get("selected_task_ids")
    tasks = {
        task.get("task_id"): task
        for task in workflow.get("tasks", [])
        if isinstance(task, Mapping)
    }
    if (
        not isinstance(task_ids, list) or not task_ids
        or any(task_id not in tasks or tasks[task_id].get("task_type") != "global_numbering" for task_id in task_ids)
    ):
        raise NumberingTransactionError(
            "Every task in the numbering publication batch must be an explicit global_numbering Workflow task."
        )
    from .revision_models import validate_author_decision_ref
    operation_roles: set[str] = set()
    for task_id in task_ids:
        task = tasks[task_id]
        try:
            task_approval = validate_author_decision_ref(task.get("author_decision_ref"))
        except Exception as exc:
            raise NumberingTransactionError(
                f"Global-numbering Workflow task {task_id} has no valid hash-bound author approval: {exc}"
            ) from exc
        if task_approval != actual_approval:
            raise NumberingTransactionError(
                f"Global-numbering Workflow task {task_id} is not bound to this exact approved impact plan and approval artifact."
            )
        operations = task.get("patch_operations", [])
        if not isinstance(operations, list) or not operations:
            raise NumberingTransactionError(
                f"Global-numbering Workflow task {task_id} has no executable PatchOperations."
            )
        for operation in operations:
            if not isinstance(operation, Mapping):
                raise NumberingTransactionError(
                    f"Global-numbering Workflow task {task_id} contains a malformed PatchOperation."
                )
            try:
                operation_approval = validate_author_decision_ref(operation.get("author_decision_ref"))
            except Exception as exc:
                raise NumberingTransactionError(
                    f"Global-numbering PatchOperation {operation.get('operation_id')} has no valid author decision: {exc}"
                ) from exc
            if operation_approval != actual_approval:
                raise NumberingTransactionError(
                    f"Global-numbering PatchOperation {operation.get('operation_id')} is bound to another author decision."
                )
            role = operation.get("document")
            if not isinstance(role, str) or not role:
                raise NumberingTransactionError("Global-numbering PatchOperation has no document role.")
            operation_roles.add(role)
    if operation_roles != set(actual_plan["affected_document_roles"]):
        raise NumberingTransactionError(
            "Global-numbering PatchOperations must cover every and only document role in the approved impact plan."
        )
    coverage = validate_numbering_patch_operation_coverage(
        actual_plan, [tasks[task_id] for task_id in task_ids]
    )

    source_documents = actual_plan["source_documents"]
    active_documents = workflow.get("active_documents")
    if not isinstance(active_documents, Mapping) or set(active_documents) != set(source_documents):
        raise NumberingTransactionError(
            "Workflow active document roles must exactly match the complete graph source inventory."
        )
    for role, source in source_documents.items():
        if not isinstance(source, Mapping):
            raise NumberingTransactionError(f"Impact plan source record for {role} is malformed.")
        active = active_documents.get(role)
        if not isinstance(active, Mapping):
            raise NumberingTransactionError(f"Workflow has no active document pointer for graph role {role}.")
        source_file = Path(source["file"]).expanduser().resolve()
        source_sha = source["sha256"]
        if not source_file.is_file() or sha256_file(source_file) != source_sha:
            raise NumberingTransactionError(
                f"Author-reviewed source DOCX for {role} is missing or no longer matches its approved SHA-256."
            )
        if role in affected_roles:
            batch_record = intent_documents[role]
            if (
                Path(str(batch_record.get("parent_file", ""))).expanduser().resolve() != source_file
                or batch_record.get("parent_sha256") != source_sha
            ):
                raise NumberingTransactionError(
                    f"Numbering batch parent for {role} is not the exact author-reviewed source snapshot."
                )
            pointer = published.get(role)
            if (
                not isinstance(pointer, Mapping)
                or active.get("file") != pointer.get("file")
                or active.get("sha256") != pointer.get("sha256")
                or active.get("batch_id") != batch_id
                or pointer.get("batch_id") != batch_id
            ):
                raise NumberingTransactionError(f"Workflow active pointer for changed role {role} is not this published batch.")
        else:
            if (
                Path(str(active.get("file", ""))).expanduser().resolve() != source_file
                or active.get("sha256") != source_sha
            ):
                raise NumberingTransactionError(
                    f"Unchanged graph role {role} changed after the impact plan was approved; rebuild and re-authorize the plan."
                )
            active_path = source_file
            if not active_path.is_file() or sha256_file(active_path) != source_sha:
                raise NumberingTransactionError(f"Unchanged graph source for {role} is missing or stale.")

    output_documents = {
        role: Path(str(active_documents[role]["file"])).expanduser().resolve()
        for role in source_documents
    }
    verification = verify_numbering_package_outputs(actual_plan, output_documents)

    if sha256_file(workflow_path) != workflow_sha_before or sha256_file(revision_path) != revision_sha_before:
        raise NumberingTransactionError(
            "Workflow Ledger or RevisionLedger changed during numbering verification; package commit was not started."
        )

    transaction_root = output_root / f"numbering-{batch_id}-{actual_plan['impact_plan_sha256'][:16]}"
    transaction_root.mkdir(parents=True, exist_ok=True)
    package_root = transaction_root / "package"
    receipt_path = transaction_root / "numbering_transaction_receipt.json"
    if receipt_path.is_file():
        receipt = _load_json_object(receipt_path, "Numbering transaction receipt")
        if (
            receipt.get("status") != "COMMITTED"
            or receipt.get("impact_plan_sha256") != actual_plan["impact_plan_sha256"]
            or receipt.get("approval_sha256") != actual_approval["approval_sha256"]
            or receipt.get("batch_id") != batch_id
        ):
            raise NumberingTransactionError("Existing numbering transaction receipt conflicts with this approved transaction.")
        marker = Path(receipt.get("commit_marker", "")).expanduser()
        manifest = Path(receipt.get("package_manifest", "")).expanduser()
        package_directory = Path(receipt.get("package_directory", "")).expanduser()
        try:
            validated_manifest = validate_committed_document_package(package_directory, marker)
        except ContractError as exc:
            raise NumberingTransactionError(
                f"Existing numbering transaction package failed integrity validation: {exc}"
            ) from exc
        metadata = validated_manifest.get("metadata")
        transaction = metadata.get("numbering_transaction") if isinstance(metadata, Mapping) else None
        expected_manifest = package_directory.resolve() / "package_manifest.json"
        if (
            manifest.resolve() != expected_manifest
            or marker.resolve().parent != package_directory.resolve().parent
            or receipt.get("package_id") != validated_manifest.get("package_id")
            or receipt.get("package_manifest_sha256") != sha256_file(expected_manifest)
            or receipt.get("commit_marker_sha256") != sha256_file(marker)
            or not isinstance(transaction, Mapping)
            or transaction.get("impact_plan_sha256") != actual_plan["impact_plan_sha256"]
            or transaction.get("approval_sha256") != actual_approval["approval_sha256"]
            or transaction.get("batch_id") != batch_id
            or transaction.get("output_documents") != receipt.get("output_documents")
            or transaction.get("final_graph_sha256") != receipt.get("final_graph_sha256")
        ):
            raise NumberingTransactionError(
                "Existing numbering transaction receipt does not match its validated package manifest and approval."
            )
        return receipt

    prior = _existing_numbering_package_receipt(
        package_root,
        plan_sha=actual_plan["impact_plan_sha256"],
        approval_sha=actual_approval["approval_sha256"],
        batch_id=batch_id,
    )
    if prior is None:
        package_outputs: dict[str, bytes] = {}
        for role, path in output_documents.items():
            package_path = f"{role}/{path.name}"
            if package_path in package_outputs:
                raise NumberingTransactionError("Numbering package contains a duplicate role/path output.")
            payload = path.read_bytes()
            expected_output_sha = verification["output_documents"][role]["sha256"]
            if hashlib.sha256(payload).hexdigest() != expected_output_sha:
                raise NumberingTransactionError(
                    f"Final DOCX for {role} changed after graph verification; refusing a time-of-check/time-of-use package commit."
                )
            package_outputs[package_path] = payload
        transaction_metadata = {
            "transaction_type": "package_wide_numbering",
            "impact_plan_sha256": actual_plan["impact_plan_sha256"],
            "approval_sha256": actual_approval["approval_sha256"],
            "approved_by": actual_approval["approved_by"],
            "approved_at_utc": actual_approval["approved_at_utc"],
            "batch_id": batch_id,
            "batch_intent_sha256": sha256_file(batch_root / "batch_intent.json"),
            "batch_scope_sha256": sha256_file(scope_path),
            "batch_publication_receipt_sha256": sha256_file(publication_path),
            "workflow_ledger_sha256": workflow_sha_before,
            "revision_ledger_sha256": revision_sha_before,
            "source_graph_sha256": actual_plan["source_graph_sha256"],
            "final_graph_sha256": verification["final_graph_sha256"],
            "final_graph": verification["final_graph"],
            "change_counts": actual_plan["change_counts"],
            "patch_operation_coverage": coverage,
            "output_documents": verification["output_documents"],
        }
        try:
            package_result = commit_document_package(
                package_outputs, package_root, metadata={"numbering_transaction": transaction_metadata}
            )
        except ContractError as exc:
            raise NumberingTransactionError(f"Package Transaction rejected numbering outputs: {exc}") from exc
        package_id = package_result["package_id"]
        package_directory = Path(package_result["directory"])
        marker_path = Path(package_result["commit_marker"])
        manifest_path = package_directory / "package_manifest.json"
        prior = {
            "package_id": package_id,
            "package_directory": str(package_directory),
            "package_manifest_sha256": sha256_file(manifest_path),
            "commit_marker": str(marker_path),
            "commit_marker_sha256": sha256_file(marker_path),
            "package_manifest": _load_json_object(manifest_path, "Committed numbering package manifest"),
        }

    receipt = {
        "schema_version": "1.0",
        "status": "COMMITTED",
        "transaction_type": "package_wide_numbering",
        "impact_plan_sha256": actual_plan["impact_plan_sha256"],
        "approval_sha256": actual_approval["approval_sha256"],
        "approved_by": actual_approval["approved_by"],
        "approved_at_utc": actual_approval["approved_at_utc"],
        "batch_id": batch_id,
        "package_id": prior["package_id"],
        "package_directory": prior["package_directory"],
        "package_manifest": str(Path(prior["package_directory"]) / "package_manifest.json"),
        "package_manifest_sha256": prior["package_manifest_sha256"],
        "commit_marker": prior["commit_marker"],
        "commit_marker_sha256": prior["commit_marker_sha256"],
        "source_graph_sha256": actual_plan["source_graph_sha256"],
        "final_graph_sha256": verification["final_graph_sha256"],
        "output_documents": verification["output_documents"],
        "graph_counts": verification["graph_counts"],
        "patch_operation_coverage": coverage,
    }
    _write_receipt_new(receipt_path, receipt)
    return receipt

# -*- coding: utf-8 -*-
"""Deterministic final-release gate and hash-bound manifest generation."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from docx import Document

from .batch_store import load_active_document_pointers
from .docx_quality import audit_docx_quality
from .identity import sha256_file
from .revision_models import (
    RevisionLedger,
    response_units_to_legacy_plan,
)
from .workflow_engine import validate_workflow_ledger


class ReleaseGateError(ValueError):
    """Raised when the revision round is not safe to seal as a final release."""


def _sha_record(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ReleaseGateError(f"Release artifact does not exist: {path}")
    return {
        "file": str(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
    }


def _normalise_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _docx_text(path: Path) -> str:
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ReleaseGateError(f"Cannot reopen generated Response Letter DOCX {path}: {exc}") from exc
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                paragraphs.extend(paragraph.text for paragraph in cell.paragraphs)
    return "\n".join(paragraphs)


def build_final_release_manifest(
    revision_ledger: RevisionLedger,
    workflow_ledger: Mapping[str, Any],
    *,
    revision_ledger_path: str | Path,
    workflow_ledger_path: str | Path,
    response_letter_md: str | Path,
    response_letter_docx: str | Path,
    response_letter_quality: str | Path,
) -> dict[str, Any]:
    """Validate every release dependency and return a self-contained hash manifest."""
    if not isinstance(revision_ledger, RevisionLedger):
        raise ReleaseGateError("A validated RevisionLedger object is required.")
    revision_ledger.validate_or_raise()
    workflow_errors = validate_workflow_ledger(workflow_ledger)
    if workflow_errors:
        raise ReleaseGateError("Workflow Ledger is invalid:\n- " + "\n- ".join(workflow_errors))
    if workflow_ledger.get("revision_round_id") != revision_ledger.revision_round_id:
        raise ReleaseGateError("Workflow Ledger and RevisionLedger belong to different review rounds.")

    if not workflow_ledger.get("active_documents"):
        raise ReleaseGateError(
            "Workflow Ledger has no committed active_documents pointers. Publish the verified batches first."
        )
    active_documents = load_active_document_pointers(workflow_ledger)
    workflow_tasks = {
        row.get("task_id"): row
        for row in workflow_ledger.get("tasks", [])
        if isinstance(row, Mapping)
    }
    patch_by_id = {patch.patch_id: patch for patch in revision_ledger.patches}
    operation_by_id = {
        operation.operation_id: (patch, operation)
        for patch in revision_ledger.patches
        for operation in patch.operations
    }

    unresolved_patches = [
        patch.patch_id for patch in revision_ledger.patches
        if patch.status not in {"verified", "superseded"}
    ]
    if unresolved_patches:
        raise ReleaseGateError(
            "Every active Patch must be verified before final release; unresolved Patch(es): "
            + ", ".join(sorted(unresolved_patches))
        )

    for patch in revision_ledger.patches:
        if patch.status == "superseded":
            continue
        task = workflow_tasks.get(patch.task_id)
        if task is None or task.get("status") not in {"verified", "completed"}:
            raise ReleaseGateError(
                f"Workflow task {patch.task_id} must be synchronized to verified/completed before release."
            )
        for entry in patch.verified_output_hashes:
            path = Path(entry["path"]).expanduser().resolve()
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                raise ReleaseGateError(
                    f"Verified Patch output changed after verification: {path}"
                )

    incomplete_units = [
        unit.response_unit_id for unit in revision_ledger.response_units
        if unit.status != "completed"
    ]
    if incomplete_units:
        raise ReleaseGateError(
            "Every ResponseUnit must be explicitly completed before final release; pending unit(s): "
            + ", ".join(sorted(incomplete_units))
        )

    response_units_by_task_comment: dict[tuple[str, str], list[Any]] = {}
    for unit in revision_ledger.response_units:
        for task_id in unit.linked_task_ids:
            response_units_by_task_comment.setdefault((task_id, unit.comment_id), []).append(unit)
    for task_id, task in workflow_tasks.items():
        if task.get("status") in {"blocked", "recheck_required"}:
            raise ReleaseGateError(
                f"Workflow task {task_id} is {task.get('status')}; resolve or recheck it before final release."
            )
        comment_ids = task.get("source_comment_ids", [])
        if not isinstance(comment_ids, list):
            raise ReleaseGateError(f"Workflow task {task_id} source_comment_ids must be an array.")
        if not comment_ids:
            if task.get("status") not in {"verified", "completed"}:
                raise ReleaseGateError(
                    f"Workflow task {task_id} has no ResponseUnit comments but is not verified/completed."
                )
            continue
        for raw_comment_id in comment_ids:
            comment_id = str(raw_comment_id)
            candidates = response_units_by_task_comment.get((task_id, comment_id), [])
            if len(candidates) != 1 or candidates[0].status != "completed":
                raise ReleaseGateError(
                    f"Workflow task {task_id} comment {comment_id} must map to exactly one completed ResponseUnit."
                )

    for unit in revision_ledger.response_units:
        if unit.disposition == "clarification_needed":
            raise ReleaseGateError(
                f"clarification_needed ResponseUnit {unit.response_unit_id} cannot be released."
            )
        if unit.disposition == "revised" and not unit.linked_operation_ids:
            raise ReleaseGateError(
                f"Revised ResponseUnit {unit.response_unit_id} has no exact linked PatchOperation."
            )
        for patch_id in unit.linked_patch_ids:
            patch = patch_by_id.get(patch_id)
            if patch is None or patch.status != "verified":
                raise ReleaseGateError(
                    f"ResponseUnit {unit.response_unit_id} links a Patch that is not verified: {patch_id}."
                )
        for operation_id in unit.linked_operation_ids:
            owner = operation_by_id.get(operation_id)
            if owner is None or patch_by_id.get(owner[0].patch_id) is None:
                raise ReleaseGateError(
                    f"ResponseUnit {unit.response_unit_id} has an unresolved exact operation link: {operation_id}."
                )
            if unit.comment_id not in owner[1].source_comment_ids:
                raise ReleaseGateError(
                    f"ResponseUnit {unit.response_unit_id} is not explicitly attributed to PatchOperation {operation_id}."
                )
        evidence_errors = unit.validate_location_evidence()
        if evidence_errors:
            raise ReleaseGateError(
                f"ResponseUnit {unit.response_unit_id} has invalid final-location evidence:\n- "
                + "\n- ".join(evidence_errors)
            )

    document_by_role = {document.role: document for document in revision_ledger.documents}
    if len(document_by_role) != len(revision_ledger.documents):
        raise ReleaseGateError("RevisionLedger contains duplicate active document roles.")
    marked_documents: dict[str, dict[str, Any]] = {}
    for role, document in document_by_role.items():
        active = active_documents.get(role)
        if active is None:
            raise ReleaseGateError(f"Workflow Ledger has no active pointer for RevisionDocument role {role}.")
        marked_path = Path(document.output_file).expanduser().resolve()
        if Path(active["file"]).expanduser().resolve() != marked_path:
            raise ReleaseGateError(
                f"RevisionLedger output path for {role} differs from Workflow Ledger active pointer."
            )
        if not marked_path.is_file() or sha256_file(marked_path) != active["sha256"]:
            raise ReleaseGateError(f"Active Marked DOCX for {role} is missing or stale: {marked_path}.")
        if document.output_sha256 and document.output_sha256 != active["sha256"]:
            raise ReleaseGateError(f"RevisionDocument output SHA-256 is stale for {role}.")
        if not document.working_clean_file or not document.working_clean_sha256:
            raise ReleaseGateError(f"Working Clean View is missing for {role}.")
        working_clean_path = Path(document.working_clean_file).expanduser().resolve()
        if not working_clean_path.is_file() or sha256_file(working_clean_path) != document.working_clean_sha256:
            raise ReleaseGateError(f"Working Clean View is missing or stale for {role}: {working_clean_path}.")
        marked_documents[role] = {
            "active_file": str(marked_path),
            "active_sha256": active["sha256"],
            "active_batch_id": active.get("batch_id"),
            "working_clean_file": str(working_clean_path),
            "working_clean_sha256": document.working_clean_sha256,
        }

    final_documents: dict[str, dict[str, Any]] = {}
    location_maps: dict[str, dict[str, Any]] = {}
    for unit in revision_ledger.response_units:
        for location in unit.final_locations:
            role = location.document
            path_key = (
                str(Path(location.output_file).expanduser().resolve()),
                location.output_sha256,
                str(Path(location.pdf_file).expanduser().resolve()),
                location.pdf_sha256,
            )
            existing = final_documents.get(role)
            if existing is not None and (
                existing["final_clean_file"],
                existing["final_clean_sha256"],
                existing["final_pdf_file"],
                existing["final_pdf_sha256"],
            ) != path_key:
                raise ReleaseGateError(
                    f"ResponseUnits for {role} do not share one final Word Clean DOCX/PDF pair."
                )
            final_documents[role] = {
                "document": role,
                "final_clean_file": path_key[0],
                "final_clean_sha256": path_key[1],
                "final_pdf_file": path_key[2],
                "final_pdf_sha256": path_key[3],
                "source_working_clean_sha256": location.source_clean_sha256,
            }
            map_path = Path(unit.location_map_file).expanduser().resolve()
            map_digest = unit.location_map_file_sha256
            map_key = str(map_path)
            if map_key not in location_maps:
                if not map_path.is_file() or sha256_file(map_path) != map_digest:
                    raise ReleaseGateError(f"Response location map is missing or stale: {map_path}.")
                try:
                    map_payload = json.loads(map_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ReleaseGateError(f"Cannot read response location map {map_path}: {exc}") from exc
                location_maps[map_key] = {
                    "file": map_key,
                    "sha256": map_digest,
                    "payload": map_payload,
                }

    for role, final_record in final_documents.items():
        if role not in marked_documents:
            raise ReleaseGateError(f"Final location exists for unregistered document role {role}.")
        role_maps = [
            entry["payload"]
            for entry in location_maps.values()
            if isinstance(entry.get("payload"), Mapping)
            and any(
                isinstance(row, Mapping) and row.get("document") == role
                for row in entry["payload"].get("documents", [])
            )
        ]
        if not role_maps:
            raise ReleaseGateError(f"No hash-bound Word location map includes final document role {role}.")
        matching_rows = [
            row for payload in role_maps
            for row in payload.get("documents", [])
            if isinstance(row, Mapping)
            and row.get("document") == role
            and row.get("output_sha256") == final_record["final_clean_sha256"]
            and row.get("pdf_sha256") == final_record["final_pdf_sha256"]
        ]
        if not matching_rows:
            raise ReleaseGateError(f"Location map does not match final DOCX/PDF hashes for {role}.")
        page_counts = {row.get("word_page_count") for row in matching_rows}
        if len(page_counts) != 1 or not isinstance(next(iter(page_counts)), int):
            raise ReleaseGateError(f"Word COM page count is missing or inconsistent for {role}.")
        final_record["word_page_count"] = next(iter(page_counts))
        final_record["sequence_audit"] = matching_rows[0].get("sequence_audit")
        if not isinstance(final_record["sequence_audit"], Mapping) or final_record["sequence_audit"].get("status") == "fail":
            raise ReleaseGateError(f"Figure/Table SEQ numbering audit failed or is missing for {role}.")
        quality_rows = [
            row for row in matching_rows
            if isinstance(row.get("quality_report_file"), str)
        ]
        if not quality_rows:
            raise ReleaseGateError(f"Final Word Clean DOCX quality report is missing for {role}.")
        quality_row = quality_rows[0]
        quality_path = Path(quality_row["quality_report_file"]).expanduser().resolve()
        if not quality_path.is_file() or sha256_file(quality_path) != quality_row.get("quality_report_sha256"):
            raise ReleaseGateError(f"Final Word Clean DOCX quality report is missing or stale: {quality_path}.")
        quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))
        if quality_payload.get("status") == "FAIL":
            raise ReleaseGateError(f"Final Word Clean DOCX failed deterministic quality checks for {role}.")
        if quality_payload.get("docx", {}).get("docx_sha256") != final_record["final_clean_sha256"]:
            raise ReleaseGateError(f"Final DOCX quality report is bound to another file for {role}.")
        final_quality = audit_docx_quality(
            final_record["final_clean_file"],
            profile="word-final-clean",
            pdf_path=final_record["final_pdf_file"],
            word_page_count=final_record["word_page_count"],
        )
        if final_quality["status"] == "FAIL":
            raise ReleaseGateError(
                f"Current final Word/PDF quality re-audit failed for {role}: {final_quality['summary']}."
            )
        final_record["quality_report"] = {
            "file": str(quality_path),
            "sha256": quality_row["quality_report_sha256"],
            "status": quality_payload["status"],
            "release_reaudit_status": final_quality["status"],
        }

    revision_path = Path(revision_ledger_path).expanduser().resolve()
    workflow_path = Path(workflow_ledger_path).expanduser().resolve()
    response_md_path = Path(response_letter_md).expanduser().resolve()
    response_docx_path = Path(response_letter_docx).expanduser().resolve()
    response_quality_path = Path(response_letter_quality).expanduser().resolve()
    for path in (revision_path, workflow_path, response_md_path, response_docx_path, response_quality_path):
        if not path.is_file():
            raise ReleaseGateError(f"Required release artifact is missing: {path}.")

    response_quality_payload = json.loads(response_quality_path.read_text(encoding="utf-8"))
    if response_quality_payload.get("status") == "FAIL":
        raise ReleaseGateError("Response Letter DOCX quality report has status FAIL.")
    response_docx_sha = sha256_file(response_docx_path)
    if response_quality_payload.get("docx", {}).get("docx_sha256") != response_docx_sha:
        raise ReleaseGateError("Response Letter DOCX quality report is not bound to the current DOCX.")
    current_response_quality = audit_docx_quality(response_docx_path, profile="response-letter")
    if current_response_quality["status"] == "FAIL":
        raise ReleaseGateError(
            f"Response Letter DOCX failed deterministic quality re-audit: {current_response_quality['summary']}."
        )

    response_plan = response_units_to_legacy_plan(revision_ledger.response_units, revision_ledger.patches)
    from .response_renderer import render_response_letter
    expected_response_md = render_response_letter(response_plan)
    stored_response_md = response_md_path.read_text(encoding="utf-8")
    if stored_response_md != expected_response_md:
        raise ReleaseGateError(
            "Response Letter Markdown does not match the current ResponseUnits/PatchOperations; regenerate it."
        )
    expected_quotes = [
        _normalise_whitespace(str(change.get("quote_text") or ""))
        for row in response_plan["revision_items"]
        for change in row.get("quoted_changes", [])
        if change.get("verified") and change.get("quote_text")
    ]
    combined_docx_text = _normalise_whitespace(_docx_text(response_docx_path))
    combined_md_text = _normalise_whitespace(stored_response_md)
    missing_md_quotes = [quote for quote in expected_quotes if quote not in combined_md_text]
    missing_docx_quotes = [quote for quote in expected_quotes if quote not in combined_docx_text]
    if missing_md_quotes or missing_docx_quotes:
        raise ReleaseGateError(
            "Response Letter omitted exact verified PatchOperation excerpt(s): "
            f"Markdown missing {len(missing_md_quotes)}, DOCX missing {len(missing_docx_quotes)}."
        )

    active_records = {
        role: {
            "file": pointer["file"],
            "sha256": pointer["sha256"],
            "batch_id": pointer.get("batch_id"),
        }
        for role, pointer in active_documents.items()
    }
    patch_records = [
        {
            "patch_id": patch.patch_id,
            "task_id": patch.task_id,
            "status": patch.status,
            "operation_ids": [operation.operation_id for operation in patch.operations],
            "source_comment_ids": sorted({
                comment_id
                for operation in patch.operations
                for comment_id in operation.source_comment_ids
            }),
            "verified_output_hashes": copy.deepcopy(patch.verified_output_hashes),
        }
        for patch in revision_ledger.patches
    ]
    response_records = [
        {
            "response_unit_id": unit.response_unit_id,
            "comment_id": unit.comment_id,
            "reviewer": unit.reviewer,
            "disposition": unit.disposition,
            "location_requirement": unit.location_requirement,
            "status": unit.status,
            "linked_patch_ids": list(unit.linked_patch_ids),
            "linked_operation_ids": list(unit.linked_operation_ids),
            "location_methods": sorted({location.mapping_method for location in unit.final_locations}),
        }
        for unit in revision_ledger.response_units
    ]
    return {
        "schema_version": "1.0",
        "release_status": "READY",
        "revision_round_id": revision_ledger.revision_round_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_ledgers": {
            "revision_ledger": _sha_record(revision_path),
            "workflow_ledger": _sha_record(workflow_path),
        },
        "active_documents": active_records,
        "marked_documents": marked_documents,
        "final_documents": final_documents,
        "patches": patch_records,
        "response_units": response_records,
        "location_maps": [
            {"file": row["file"], "sha256": row["sha256"]}
            for row in location_maps.values()
        ],
        "artifacts": {
            "response_letter_markdown": _sha_record(response_md_path),
            "response_letter_docx": _sha_record(response_docx_path),
            "response_letter_quality": {
                **_sha_record(response_quality_path),
                "status": response_quality_payload["status"],
                "release_reaudit_status": current_response_quality["status"],
            },
        },
        "checks": {
            "all_active_patches_verified": True,
            "all_response_units_completed": True,
            "active_pointers_match_marked_docx": True,
            "all_final_clean_docx_pdf_hashes_valid": True,
            "all_word_quality_reports_pass": True,
            "response_letter_contains_verified_patch_excerpts": True,
        },
    }

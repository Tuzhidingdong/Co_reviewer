# -*- coding: utf-8 -*-
"""Deterministic final-release gate and hash-bound manifest generation."""
from __future__ import annotations

import copy
import json
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from docx import Document

from .batch_store import (
    _validate_recovered_batch_receipt,
    load_active_document_pointers,
    read_batch_intent,
)
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


def _require_release_quality_status(payload: Any, label: str) -> str:
    """Accept only the quality states represented by the release-manifest schema."""
    if not isinstance(payload, Mapping):
        raise ReleaseGateError(f"{label} quality report must be an object.")
    status = payload.get("status")
    if status not in {"PASS", "PASS_WITH_WARNINGS"}:
        raise ReleaseGateError(
            f"{label} quality report has unsupported or blocking status {status!r}; "
            "expected PASS or PASS_WITH_WARNINGS."
        )
    return status


def _require_successful_sequence_audit(payload: Any, role: str) -> dict[str, Any]:
    """Require a structurally complete and internally consistent Word-native SEQ result."""
    if not isinstance(payload, Mapping) or payload.get("status") not in {"pass", "manual_review"}:
        status = payload.get("status") if isinstance(payload, Mapping) else None
        raise ReleaseGateError(
            f"Figure/Table SEQ audit for {role} has unsupported or blocking status {status!r}; "
            "expected pass or manual_review."
        )
    detected = payload.get("detected")
    issues = payload.get("issues")
    manual_review = payload.get("manual_review")
    if (
        not isinstance(detected, Mapping)
        or set(detected) != {"Figure", "Table"}
        or any(not isinstance(detected.get(name), list) for name in ("Figure", "Table"))
        or not isinstance(issues, list)
        or not isinstance(manual_review, list)
    ):
        raise ReleaseGateError(
            f"Figure/Table SEQ audit for {role} is structurally incomplete; "
            "detected Figure/Table arrays, issues and manual_review arrays are required."
        )
    status = payload["status"]
    if status == "pass" and (issues or manual_review):
        raise ReleaseGateError(
            f"Figure/Table SEQ audit for {role} claims pass but still contains issues or manual-review findings."
        )
    if status == "manual_review" and (issues or not manual_review):
        raise ReleaseGateError(
            f"Figure/Table SEQ audit for {role} has inconsistent manual_review status or findings."
        )
    return dict(payload)


def _require_word_page_count(value: Any, role: str) -> int:
    """Require a positive integer Word COM page count; bool is not an integer here."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ReleaseGateError(
            f"Word COM page count for {role} must be a positive integer, got {value!r}."
        )
    return value


def _validate_response_unit_task_attribution(
    response_units: Sequence[Any],
    workflow_tasks: Mapping[str, Mapping[str, Any]],
) -> None:
    """Require every final ResponseUnit to map to a current task and its real reviewer comment."""
    for unit in response_units:
        task_ids = list(unit.linked_task_ids)
        if not task_ids:
            raise ReleaseGateError(
                f"ResponseUnit {unit.response_unit_id} must link to at least one current Workflow task."
            )
        for task_id in task_ids:
            task = workflow_tasks.get(task_id)
            if task is None:
                raise ReleaseGateError(
                    f"ResponseUnit {unit.response_unit_id} links unknown or removed Workflow task {task_id}."
                )
            comment_ids = task.get("source_comment_ids", [])
            if not isinstance(comment_ids, list):
                raise ReleaseGateError(f"Workflow task {task_id} source_comment_ids must be an array.")
            if unit.comment_id not in {str(value) for value in comment_ids}:
                raise ReleaseGateError(
                    f"ResponseUnit {unit.response_unit_id} comment {unit.comment_id} is not declared by linked Workflow task {task_id}."
                )


def _validate_release_batch_scope(
    intent: Mapping[str, Any],
    scope: Mapping[str, Any],
    *,
    revision_ledger: RevisionLedger,
    revision_ledger_path: str | Path,
    workflow_task_ids: set[str],
) -> list[str]:
    """Cross-check immutable batch task/document/Patch ownership against the current ledger."""
    batch_id = intent.get("batch_id")
    if scope.get("batch_id") != batch_id:
        raise ReleaseGateError(f"Batch {batch_id} scope/intent IDs differ.")
    if (
        intent.get("revision_round_id") != revision_ledger.revision_round_id
        or scope.get("revision_round_id") != revision_ledger.revision_round_id
    ):
        raise ReleaseGateError(f"Batch {batch_id} belongs to another revision round.")
    batch_task_ids = intent.get("selected_task_ids")
    all_task_ids = intent.get("all_selected_task_ids", batch_task_ids)
    scope_batch_task_ids = scope.get("batch_task_ids")
    scope_all_task_ids = scope.get("selected_task_ids")
    for label, values in (
        ("intent selected_task_ids", batch_task_ids),
        ("intent all_selected_task_ids", all_task_ids),
        ("scope batch_task_ids", scope_batch_task_ids),
        ("scope selected_task_ids", scope_all_task_ids),
    ):
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
            or len(values) != len(set(values))
        ):
            raise ReleaseGateError(f"Batch {batch_id} has invalid {label}.")
    if scope_batch_task_ids != batch_task_ids or scope_all_task_ids != all_task_ids:
        raise ReleaseGateError(f"Batch {batch_id} task ownership differs between intent and scope.")
    if not set(batch_task_ids).issubset(set(all_task_ids)):
        raise ReleaseGateError(f"Batch {batch_id} document-changing tasks are outside its full task selection.")
    if not set(all_task_ids).issubset(workflow_task_ids):
        missing_tasks = sorted(set(all_task_ids) - workflow_task_ids)
        raise ReleaseGateError(
            f"Batch {batch_id} references task(s) missing from the current Workflow Ledger: "
            + ", ".join(missing_tasks)
        )
    ledger_path = Path(revision_ledger_path).expanduser().resolve()
    if (
        not isinstance(scope.get("revision_ledger_file"), str)
        or Path(scope["revision_ledger_file"]).expanduser().resolve() != ledger_path
    ):
        raise ReleaseGateError(f"Batch {batch_id} scope points to another RevisionLedger file.")
    intent_documents = intent.get("documents")
    scope_documents = scope.get("documents")
    if (
        not isinstance(intent_documents, Mapping)
        or not intent_documents
        or not isinstance(scope_documents, Mapping)
        or set(scope_documents) != set(intent_documents)
    ):
        raise ReleaseGateError(f"Batch {batch_id} document-role scope differs from its immutable intent.")
    for role, intent_row in intent_documents.items():
        scope_row = scope_documents.get(role)
        if not isinstance(intent_row, Mapping) or not isinstance(scope_row, Mapping):
            raise ReleaseGateError(f"Batch {batch_id} has malformed document scope for {role}.")
        expected_pairs = (
            ("parent_file", "input_file"),
            ("parent_sha256", "input_sha256"),
            ("output_file", "output_file"),
        )
        for intent_key, scope_key in expected_pairs:
            left, right = intent_row.get(intent_key), scope_row.get(scope_key)
            if intent_key.endswith("file"):
                if (
                    not isinstance(left, str)
                    or not left.strip()
                    or not isinstance(right, str)
                    or not right.strip()
                    or Path(left).expanduser().resolve() != Path(right).expanduser().resolve()
                ):
                    raise ReleaseGateError(f"Batch {batch_id} document path scope differs for {role}.")
            elif left != right:
                raise ReleaseGateError(f"Batch {batch_id} document hash scope differs for {role}.")
    expected_patch_ids = [
        patch.patch_id
        for patch in revision_ledger.patches
        if patch.task_id in set(batch_task_ids)
        and any(
            operation.document in intent_documents
            and Path(operation.output_file).expanduser().resolve()
            == Path(intent_documents[operation.document]["output_file"]).expanduser().resolve()
            for operation in patch.operations
        )
    ]
    actual_patch_ids = scope.get("patch_ids")
    if (
        not isinstance(actual_patch_ids, list)
        or any(not isinstance(value, str) or not value.strip() for value in actual_patch_ids)
        or len(actual_patch_ids) != len(set(actual_patch_ids))
        or actual_patch_ids != expected_patch_ids
    ):
        raise ReleaseGateError(
            f"Batch {batch_id} Patch ownership does not match current RevisionLedger operations; "
            f"expected {expected_patch_ids}, received {actual_patch_ids!r}."
        )
    return expected_patch_ids


def _require_consistent_sequence_audits(payloads: list[Any], role: str) -> dict[str, Any]:
    """Require every location map for the same final document to attest the same valid SEQ audit."""
    if not payloads:
        raise ReleaseGateError(f"Figure/Table SEQ audit records are missing for {role}.")
    audits = [_require_successful_sequence_audit(payload, role) for payload in payloads]
    fingerprints = {
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for payload in audits
    }
    if len(fingerprints) != 1:
        raise ReleaseGateError(
            f"Location maps contain inconsistent Figure/Table SEQ audit records for the same final document {role}."
        )
    return audits[0]


def _require_bound_final_quality_report(
    payload: Any,
    *,
    role: str,
    docx_path: str,
    docx_sha256: str,
    pdf_path: str,
    pdf_sha256: str,
    word_page_count: int,
) -> str:
    """Bind a Word final quality report to the exact final DOCX/PDF pair and Word pagination."""
    status = _require_release_quality_status(payload, f"Final Word Clean DOCX for {role}")
    docx_metrics = payload.get("docx")
    pdf_metrics = payload.get("pdf")
    if not isinstance(docx_metrics, Mapping) or not isinstance(pdf_metrics, Mapping):
        raise ReleaseGateError(
            f"Final Word Clean DOCX quality report for {role} must include DOCX and PDF metric objects."
        )
    if (
        docx_metrics.get("profile") != "word-final-clean"
        or docx_metrics.get("docx_sha256") != docx_sha256
        or not isinstance(docx_metrics.get("docx_file"), str)
        or Path(docx_metrics["docx_file"]).expanduser().resolve() != Path(docx_path).expanduser().resolve()
    ):
        raise ReleaseGateError(
            f"Final DOCX quality report is not bound to the current Word Clean file for {role}."
        )
    quality_page_count = _require_word_page_count(
        pdf_metrics.get("word_page_count"), role
    )
    if (
        pdf_metrics.get("sha256") != pdf_sha256
        or not isinstance(pdf_metrics.get("file"), str)
        or Path(pdf_metrics["file"]).expanduser().resolve() != Path(pdf_path).expanduser().resolve()
        or quality_page_count != word_page_count
    ):
        raise ReleaseGateError(
            f"Final DOCX quality report is not bound to the current PDF and Word page count for {role}."
        )
    return status


def _require_bound_response_quality_report(
    payload: Any,
    *,
    docx_path: str,
    docx_sha256: str,
) -> str:
    """Require the stored Response Letter quality report to use its dedicated profile and file hash."""
    status = _require_release_quality_status(payload, "Response Letter DOCX")
    docx_metrics = payload.get("docx")
    if (
        not isinstance(docx_metrics, Mapping)
        or docx_metrics.get("profile") != "response-letter"
        or docx_metrics.get("docx_sha256") != docx_sha256
        or not isinstance(docx_metrics.get("docx_file"), str)
        or Path(docx_metrics["docx_file"]).expanduser().resolve()
        != Path(docx_path).expanduser().resolve()
    ):
        raise ReleaseGateError(
            "Response Letter quality report is not bound to the current DOCX using the response-letter profile."
        )
    return status


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


def _response_docx_body_text(path: Path) -> str:
    """Return a structurally constrained Response Letter body, excluding optional title/author metadata."""
    try:
        document = Document(str(path))
    except Exception as exc:
        raise ReleaseGateError(f"Cannot reopen Response Letter DOCX {path}: {exc}") from exc
    if len(document.sections) != 1:
        raise ReleaseGateError("Response Letter DOCX must use the single section generated by the canonical renderer.")
    if document.tables or document.inline_shapes:
        raise ReleaseGateError("Response Letter DOCX contains tables or inline shapes not emitted by the canonical renderer.")
    try:
        with zipfile.ZipFile(path, "r") as package:
            document_xml = package.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ReleaseGateError(f"Cannot inspect Response Letter DOCX body XML {path}: {exc}") from exc
    if b"txbxContent" in document_xml:
        raise ReleaseGateError("Response Letter DOCX contains a text box not emitted by the canonical renderer.")
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    if not paragraphs or paragraphs[0] != "Response to Reviewers":
        raise ReleaseGateError("Response Letter DOCX is missing the canonical title paragraph.")
    summary_index = next(
        (index for index, value in enumerate(paragraphs)
         if value.startswith("Revision items: ")),
        None,
    )
    if summary_index is None or not 1 <= summary_index <= 3:
        raise ReleaseGateError(
            f"Response Letter DOCX has a missing or structurally displaced revision summary paragraph: {path}"
        )
    return "\n".join(paragraphs[summary_index:])


_NUMERIC_TOKEN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?%?")
_FIGURE_TABLE_TOKEN = re.compile(
    r"\b(?:Fig(?:ure)?\.?|Table|Eq(?:uation)?\.?|Section|Scheme|Supplementary\s+Fig(?:ure)?\.?)\s*"
    r"(?:S\s*)?\d+(?:[A-Za-z])?(?:\s*[-–]\s*\d+)?",
    re.IGNORECASE,
)
_CITATION_TOKEN = re.compile(r"\[(?:\d{1,4}(?:\s*[,;–-]\s*\d{1,4})*)\]|\bDOI\s*:\s*10\.\d{4,9}/[^\s,;]+", re.IGNORECASE)
_SAMPLE_TOKEN = re.compile(
    r"\b(?:sample|specimen|electrode|cell|batch|group|cathode|anode)\s*[-_:#]?\s*[A-Za-z0-9][A-Za-z0-9._/-]*",
    re.IGNORECASE,
)
_UNIT_TOKEN = re.compile(
    r"(?<![A-Za-z])(?:"
    r"m?S\s*cm(?:\^?[-−]?1)?|"
    r"S\s*m(?:\^?[-−]?1)?|"
    r"mAh\s*g(?:\^?[-−]?1)?|"
    r"Ah\s*kg(?:\^?[-−]?1)?|"
    r"mg\s*L(?:\^?[-−]?1)?|"
    r"g\s*L(?:\^?[-−]?1)?|"
    r"mol\s*L(?:\^?[-−]?1)?|"
    r"mmol\s*L(?:\^?[-−]?1)?|"
    r"eV|meV|kJ\s*mol(?:\^?[-−]?1)?|"
    r"mV|V|mA|A|mW|W|nm|µm|um|Å|K|°C|wt%|at%|vol%|MPa|GPa|kPa|"
    r"m\^?2\s*g(?:\^?[-−]?1)?|cm\^?2\s*g(?:\^?[-−]?1)?"
    r")(?![A-Za-z])"
)


def _token_delta(
    before: str,
    after: str,
    pattern: re.Pattern[str],
    *,
    exclude_patterns: tuple[re.Pattern[str], ...] = (),
) -> dict[str, list[str]]:
    def extract(value: str) -> list[str]:
        excluded = [
            match.span()
            for excluded_pattern in exclude_patterns
            for match in excluded_pattern.finditer(value or "")
        ]
        return [
            match.group(0)
            for match in pattern.finditer(value or "")
            if not any(start < match.end() and match.start() < end for start, end in excluded)
        ]

    old_tokens = extract(before or "")
    new_tokens = extract(after or "")
    old_counts: dict[str, int] = {}
    new_counts: dict[str, int] = {}
    for token in old_tokens:
        normalized = _normalise_whitespace(token).casefold()
        old_counts[normalized] = old_counts.get(normalized, 0) + 1
    for token in new_tokens:
        normalized = _normalise_whitespace(token).casefold()
        new_counts[normalized] = new_counts.get(normalized, 0) + 1
    removed: list[str] = []
    added: list[str] = []
    for key, count in old_counts.items():
        removed.extend([key] * max(0, count - new_counts.get(key, 0)))
    for key, count in new_counts.items():
        added.extend([key] * max(0, count - old_counts.get(key, 0)))
    return {"removed": sorted(removed), "added": sorted(added)}


def audit_patch_operation_deltas(
    revision_ledger: RevisionLedger,
) -> list[dict[str, Any]]:
    """Surface deterministic operand deltas; never claim that changed science is correct."""
    units_by_operation: dict[str, list[Any]] = {}
    for unit in revision_ledger.response_units:
        for operation_id in unit.linked_operation_ids:
            units_by_operation.setdefault(operation_id, []).append(unit)

    rows: list[dict[str, Any]] = []
    for patch in revision_ledger.patches:
        if patch.status == "superseded":
            continue
        for operation in patch.operations:
            linked_units = units_by_operation.get(operation.operation_id, [])
            completed_reviewers = sorted({
                unit.reviewer for unit in linked_units
                if unit.status == "completed" and unit.semantic_review_confirmed
                and unit.comment_id in operation.source_comment_ids
            })
            if not linked_units or not all(
                unit.status == "completed" and unit.semantic_review_confirmed
                and unit.comment_id in operation.source_comment_ids
                for unit in linked_units
            ):
                raise ReleaseGateError(
                    f"PatchOperation {operation.operation_id} has no complete human-reviewed ResponseUnit link."
                )
            categories = {
                "numeric_values": _token_delta(
                    operation.find,
                    operation.replace,
                    _NUMERIC_TOKEN,
                    exclude_patterns=(
                        _UNIT_TOKEN,
                        _FIGURE_TABLE_TOKEN,
                        _CITATION_TOKEN,
                        _SAMPLE_TOKEN,
                    ),
                ),
                "units": _token_delta(operation.find, operation.replace, _UNIT_TOKEN),
                "figure_table_equation_references": _token_delta(operation.find, operation.replace, _FIGURE_TABLE_TOKEN),
                "citation_markers": _token_delta(operation.find, operation.replace, _CITATION_TOKEN),
                "sample_or_component_identifiers": _token_delta(operation.find, operation.replace, _SAMPLE_TOKEN),
            }
            changed_categories = [
                name for name, delta in categories.items()
                if delta["removed"] or delta["added"]
            ]
            rows.append({
                "patch_id": patch.patch_id,
                "operation_id": operation.operation_id,
                "document": operation.document,
                "source_comment_ids": list(operation.source_comment_ids),
                "linked_response_unit_ids": [unit.response_unit_id for unit in linked_units],
                "human_reviewers": completed_reviewers,
                "review_status": "human_semantic_review_completed",
                "changed_categories": changed_categories,
                "deltas": categories,
                "interpretation": (
                    "Deterministic token differences are surfaced for author review; "
                    "they do not assert scientific correctness or incorrectness."
                ),
            })
    return rows


def build_final_docx_consistency_audit(revision_ledger: RevisionLedger) -> dict[str, Any]:
    """Run the existing deterministic Figure/Table reference audit on current Working Clean files."""
    from co_reviewer_cli import inspect_docx
    from review_audit import audit_figure_table_numbering

    reports: list[dict[str, Any]] = []
    package_errors: list[dict[str, Any]] = []
    for document in revision_ledger.documents:
        if document.role not in {"Manuscript", "SI"}:
            continue
        if not document.working_clean_file or not document.working_clean_sha256:
            package_errors.append({
                "document": document.role,
                "reason": "No current hash-bound Working Clean View is registered.",
            })
            continue
        source = Path(document.working_clean_file).expanduser().resolve()
        if not source.is_file() or sha256_file(source) != document.working_clean_sha256:
            package_errors.append({
                "document": document.role,
                "file": str(source),
                "reason": "Working Clean View is missing or has changed since registration.",
            })
            continue
        report = inspect_docx(str(source))
        report["role"] = document.role
        reports.append(report)
        if report.get("errors"):
            package_errors.append({
                "document": document.role,
                "file": str(source),
                "reason": "DOCX inspection reported errors.",
                "errors": report["errors"],
            })

    numbering = audit_figure_table_numbering(reports)
    blocking: list[dict[str, Any]] = list(package_errors)
    for row in numbering.get("unresolved_references", []):
        blocking.append({"category": "unresolved_figure_table_reference", **row})
    for row in numbering.get("ambiguous_references", []):
        blocking.append({"category": "ambiguous_figure_table_reference", **row})
    for row in numbering.get("duplicate_captions_within_documents", []):
        blocking.append({"category": "duplicate_caption_within_document", **row})
    for role, row in numbering.get("by_document", {}).items():
        for gap in row.get("numbering_gaps", []):
            blocking.append({"category": "figure_table_numbering_gap", "document": role, **gap})
        for mention in row.get("out_of_order_first_mentions", []):
            blocking.append({"category": "out_of_order_figure_table_first_mention", "document": role, **mention})
    for row in numbering.get("duplicate_labels_across_documents", []):
        if row.get("classification") == "cross_document_same_label_different_captions":
            blocking.append({"category": "cross_document_caption_label_collision", **row})
    return {
        "schema_version": "1.0",
        "status": "BLOCKED" if blocking else "PASS",
        "scope": "Deterministic textual Figure/Table caption and reference consistency in current Manuscript/SI Working Clean Views.",
        "documents_checked": [row.get("role") for row in reports],
        "figure_table_audit": numbering,
        "blocking_findings": blocking,
        "review_flags": {
            "unreferenced_captions": numbering.get("unreferenced_captions", []),
            "cross_document_caption_reproductions": [
                row for row in numbering.get("duplicate_labels_across_documents", [])
                if row.get("classification") == "cross_document_caption_reproduction"
            ],
            "caption_text_identity_groups": numbering.get("caption_text_identity_groups", []),
        },
        "limitations": [
            "Textual caption/reference analysis cannot verify embedded figure-image contents or floating text boxes.",
            "Repeated captions are not proof that underlying visual objects are identical.",
            "Numeric values, units, sample identities and citation semantics remain covered by the explicit Patch/ResponseUnit semantic-review gate; this audit does not infer scientific equivalence.",
        ],
    }


def _pointer_projection(value: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping) or not value:
        raise ReleaseGateError(f"{label} must be a non-empty document pointer object.")
    projected: dict[str, dict[str, Any]] = {}
    for role, row in value.items():
        if not isinstance(role, str) or not role.strip() or not isinstance(row, Mapping):
            raise ReleaseGateError(f"{label} contains an invalid document-role pointer.")
        file_path = row.get("file")
        digest = row.get("sha256")
        batch_id = row.get("batch_id")
        if not isinstance(file_path, str) or not file_path.strip():
            raise ReleaseGateError(f"{label}[{role}] requires a file path.")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ReleaseGateError(f"{label}[{role}] requires a valid SHA-256.")
        if batch_id is not None and (not isinstance(batch_id, str) or not batch_id.strip()):
            raise ReleaseGateError(f"{label}[{role}] has an invalid batch_id.")
        projected[role] = {
            "file": str(Path(file_path).expanduser().resolve()),
            "sha256": digest,
            "batch_id": batch_id,
        }
    return projected


def _collect_release_batch_history(
    workflow_ledger: Mapping[str, Any],
    active_documents: Mapping[str, Any],
    *,
    revision_ledger: RevisionLedger,
    revision_ledger_path: str | Path,
    workflow_ledger_path: str | Path,
) -> list[dict[str, Any]]:
    """Seal every published batch in the round, including batches later rolled back."""
    events = workflow_ledger.get("events", [])
    if not isinstance(events, list):
        raise ReleaseGateError("Workflow Ledger events must be an array.")
    published: dict[str, list[Mapping[str, Any]]] = {}
    rolled_back: dict[str, list[Mapping[str, Any]]] = {}
    for row in events:
        if not isinstance(row, Mapping):
            continue
        event_type = row.get("event_type")
        if event_type not in {"batch_published", "batch_rolled_back"}:
            continue
        batch_id = row.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ReleaseGateError(f"{event_type} Workflow event is missing batch_id.")
        target = published if event_type == "batch_published" else rolled_back
        target.setdefault(batch_id, []).append(row)
    orphan_rollbacks = sorted(set(rolled_back) - set(published))
    if orphan_rollbacks:
        raise ReleaseGateError(
            "Workflow Ledger contains rollback events without a published batch: "
            + ", ".join(orphan_rollbacks)
        )
    workflow_task_ids = {
        row.get("task_id")
        for row in workflow_ledger.get("tasks", [])
        if isinstance(row, Mapping) and isinstance(row.get("task_id"), str)
    }
    records: list[dict[str, Any]] = []
    by_batch_id: dict[str, dict[str, Any]] = {}
    workflow_path = Path(workflow_ledger_path).expanduser().resolve()
    for batch_id in sorted(published):
        publish_events = published[batch_id]
        event_task_ids = [row.get("task_id") for row in publish_events]
        if (
            any(not isinstance(value, str) or not value.strip() for value in event_task_ids)
            or len(event_task_ids) != len(set(event_task_ids))
        ):
            raise ReleaseGateError(f"Batch {batch_id} has duplicate or invalid publication task events.")
        event_documents = [
            _pointer_projection(row.get("documents"), f"batch {batch_id} publication event documents")
            for row in publish_events
        ]
        if any(value != event_documents[0] for value in event_documents[1:]):
            raise ReleaseGateError(f"Batch {batch_id} publication events disagree on document outputs.")
        published_documents = event_documents[0]
        batch_directories: set[Path] = set()
        for role, pointer in published_documents.items():
            if pointer["batch_id"] != batch_id:
                raise ReleaseGateError(f"Batch {batch_id} event pointer for {role} declares another batch.")
            output_path = Path(pointer["file"])
            if output_path.parent.name != "documents":
                raise ReleaseGateError(f"Batch {batch_id} output for {role} is outside its documents directory.")
            batch_directories.add(output_path.parent.parent)
        if len(batch_directories) != 1:
            raise ReleaseGateError(f"Batch {batch_id} output documents do not share one batch directory.")
        batch_dir = next(iter(batch_directories))
        if batch_dir.name != f"batch-{batch_id}":
            raise ReleaseGateError(f"Batch {batch_id} event paths do not resolve to their declared batch directory.")

        intent_path = batch_dir / "batch_intent.json"
        scope_path = batch_dir / "batch_scope.json"
        publish_intent_path = batch_dir / "batch_publish_intent.json"
        publish_receipt_path = batch_dir / "batch_published.json"
        required = {
            "intent": intent_path,
            "scope": scope_path,
            "publish_intent": publish_intent_path,
            "publish_receipt": publish_receipt_path,
        }
        for label, path in required.items():
            if not path.is_file():
                raise ReleaseGateError(f"Published batch {batch_id} is missing {label}: {path}.")
        try:
            intent_payload = json.loads(intent_path.read_text(encoding="utf-8"))
            scope_payload = json.loads(scope_path.read_text(encoding="utf-8"))
            publish_intent_payload = json.loads(publish_intent_path.read_text(encoding="utf-8"))
            publish_receipt = json.loads(publish_receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseGateError(f"Cannot read published batch {batch_id} records: {exc}") from exc

        intent_sha = sha256_file(intent_path)
        scope_sha = sha256_file(scope_path)
        expected_round = revision_ledger.revision_round_id
        all_task_ids = intent_payload.get("all_selected_task_ids", intent_payload.get("selected_task_ids"))
        if (
            intent_payload.get("batch_id") != batch_id
            or scope_payload.get("batch_id") != batch_id
            or publish_intent_payload.get("batch_id") != batch_id
            or publish_receipt.get("batch_id") != batch_id
            or publish_receipt.get("status") != "published"
            or intent_payload.get("revision_round_id") != expected_round
            or not isinstance(intent_payload.get("workflow_ledger_file"), str)
            or Path(intent_payload["workflow_ledger_file"]).expanduser().resolve() != workflow_path
            or publish_receipt.get("workflow_ledger_file") is None
            or Path(publish_receipt["workflow_ledger_file"]).expanduser().resolve() != workflow_path
            or publish_receipt.get("revision_ledger_file") is None
            or Path(publish_receipt["revision_ledger_file"]).expanduser().resolve()
               != Path(revision_ledger_path).expanduser().resolve()
            or not isinstance(publish_receipt.get("revision_ledger_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", publish_receipt["revision_ledger_sha256"])
            or scope_payload.get("batch_intent_sha256") != intent_sha
            or publish_intent_payload.get("batch_intent_sha256") != intent_sha
            or publish_receipt.get("batch_intent_sha256") != intent_sha
            or publish_intent_payload.get("batch_scope_sha256") != scope_sha
            or publish_receipt.get("batch_scope_sha256") != scope_sha
            or publish_intent_payload.get("revision_ledger_sha256") != publish_receipt.get("revision_ledger_sha256")
        ):
            raise ReleaseGateError(f"Batch {batch_id} has inconsistent or stale publication intent/receipt records.")
        if (
            not isinstance(all_task_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in all_task_ids)
            or len(all_task_ids) != len(set(all_task_ids))
            or set(event_task_ids) != set(all_task_ids)
            or publish_receipt.get("selected_task_ids") != all_task_ids
        ):
            raise ReleaseGateError(f"Batch {batch_id} publication events do not cover its exact selected task set.")
        if _pointer_projection(publish_receipt.get("active_documents"), f"batch {batch_id} publish receipt") != published_documents:
            raise ReleaseGateError(f"Batch {batch_id} published receipt does not attest all Workflow publication events.")
        if _pointer_projection(publish_intent_payload.get("active_documents_to_publish"), f"batch {batch_id} publish intent") != published_documents:
            raise ReleaseGateError(f"Batch {batch_id} publish intent does not attest all Workflow publication events.")
        _validate_release_batch_scope(
            intent_payload,
            scope_payload,
            revision_ledger=revision_ledger,
            revision_ledger_path=revision_ledger_path,
            workflow_task_ids=workflow_task_ids,
        )
        record: dict[str, Any] = {
            "batch_id": batch_id,
            "directory": str(batch_dir),
            "intent": _sha_record(intent_path),
            "scope": _sha_record(scope_path),
            "publish_intent": _sha_record(publish_intent_path),
            "publish_receipt": _sha_record(publish_receipt_path),
            "selected_task_ids": list(intent_payload.get("selected_task_ids", [])),
            "all_selected_task_ids": list(all_task_ids),
            "patch_ids": list(scope_payload.get("patch_ids", [])),
            "documents": {
                role: {"file": pointer["file"], "sha256": pointer["sha256"]}
                for role, pointer in published_documents.items()
            },
            "publication_status": "published",
        }

        rollback_events = rolled_back.get(batch_id, [])
        rollback_intent_path = batch_dir / "batch_rollback_intent.json"
        rollback_receipt_path = batch_dir / "batch_rollback.json"
        if rollback_events or rollback_intent_path.exists() or rollback_receipt_path.exists():
            if not rollback_events or not rollback_intent_path.is_file() or not rollback_receipt_path.is_file():
                raise ReleaseGateError(f"Batch {batch_id} has an incomplete rollback event/intent/receipt chain.")
            rollback_task_ids = [row.get("task_id") for row in rollback_events]
            if (
                any(not isinstance(value, str) or not value.strip() for value in rollback_task_ids)
                or len(rollback_task_ids) != len(set(rollback_task_ids))
                or set(rollback_task_ids) != set(all_task_ids)
            ):
                raise ReleaseGateError(f"Batch {batch_id} rollback events do not cover its exact selected task set.")
            rollback_documents = [
                _pointer_projection(row.get("documents"), f"batch {batch_id} rollback event documents")
                for row in rollback_events
            ]
            if any(value != rollback_documents[0] for value in rollback_documents[1:]):
                raise ReleaseGateError(f"Batch {batch_id} rollback events disagree on restored parent pointers.")
            try:
                rollback_intent = json.loads(rollback_intent_path.read_text(encoding="utf-8"))
                rollback_receipt = json.loads(rollback_receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseGateError(f"Cannot read batch {batch_id} rollback records: {exc}") from exc
            restored = _pointer_projection(
                rollback_receipt.get("restored_active_documents"),
                f"batch {batch_id} rollback receipt",
            )
            if (
                rollback_intent.get("batch_id") != batch_id
                or rollback_receipt.get("batch_id") != batch_id
                or rollback_receipt.get("status") != "rolled_back"
                or rollback_receipt.get("retained_batch_files") is not True
                or rollback_receipt.get("workflow_ledger_file") is None
                or Path(rollback_receipt["workflow_ledger_file"]).expanduser().resolve() != workflow_path
                or rollback_receipt.get("selected_task_ids") != all_task_ids
                or rollback_receipt.get("batch_intent_sha256") != intent_sha
                or rollback_receipt.get("batch_scope_sha256") != scope_sha
                or rollback_intent.get("batch_intent_sha256") != intent_sha
                or rollback_intent.get("batch_scope_sha256") != scope_sha
                or _pointer_projection(rollback_intent.get("restored_active_documents"), f"batch {batch_id} rollback intent") != restored
                or rollback_documents[0] != restored
            ):
                raise ReleaseGateError(f"Batch {batch_id} rollback receipt/intent does not attest Workflow rollback events.")
            for role, pointer in restored.items():
                parent_path = Path(pointer["file"])
                if not parent_path.is_file() or sha256_file(parent_path) != pointer["sha256"]:
                    raise ReleaseGateError(f"Batch {batch_id} rollback parent for {role} is missing or stale.")
            record["rollback_intent"] = _sha_record(rollback_intent_path)
            record["rollback_receipt"] = _sha_record(rollback_receipt_path)
            record["publication_status"] = "rolled_back"

        records.append(record)
        by_batch_id[batch_id] = record

    for role, pointer in active_documents.items():
        active_batch_id = pointer.get("batch_id")
        if active_batch_id is None:
            continue
        record = by_batch_id.get(active_batch_id)
        if record is None:
            raise ReleaseGateError(f"Active document {role} refers to unpublished or unrecorded batch {active_batch_id}.")
        if record["publication_status"] != "published":
            raise ReleaseGateError(f"Active document {role} still points to batch {active_batch_id}, which was rolled back.")
        batch_pointer = record["documents"].get(role)
        if batch_pointer is None or (
            Path(batch_pointer["file"]).expanduser().resolve()
            != Path(pointer["file"]).expanduser().resolve()
            or batch_pointer["sha256"] != pointer["sha256"]
        ):
            raise ReleaseGateError(f"Active document {role} pointer differs from its publication history.")
    return records


def _collect_recovered_batch_history(
    revision_ledger: RevisionLedger,
    *,
    published_batch_ids: set[str],
    revision_ledger_path: str | Path,
    workflow_ledger_path: str | Path,
    workflow_task_ids: set[str],
) -> list[dict[str, Any]]:
    """Seal every abandoned unpublished batch from its RevisionLedger recovery events."""
    patch_status_by_id = {
        patch.patch_id: patch.status for patch in revision_ledger.patches
    }
    groups: dict[str, list[Any]] = {}
    for event in revision_ledger.events:
        if (
            event.subject_type != "patch"
            or event.event_type != "patch_abandoned_after_batch_recovery"
        ):
            continue
        details = event.details
        batch_id = details.get("batch_id")
        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ReleaseGateError(
                f"Recovery event for Patch {event.subject_id} has no batch_id."
            )
        if batch_id in published_batch_ids:
            raise ReleaseGateError(
                f"Batch {batch_id} is marked both recovered-unpublished and published."
            )
        groups.setdefault(batch_id, []).append(event)

    records: list[dict[str, Any]] = []
    revision_path = Path(revision_ledger_path).expanduser().resolve()
    workflow_path = Path(workflow_ledger_path).expanduser().resolve()
    for batch_id in sorted(groups):
        events = groups[batch_id]
        patch_ids = [event.subject_id for event in events]
        details_by_event = [
            json.dumps(event.details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for event in events
        ]
        if (
            any(not isinstance(value, str) or not value.strip() for value in patch_ids)
            or len(patch_ids) != len(set(patch_ids))
            or any(value != details_by_event[0] for value in details_by_event[1:])
        ):
            raise ReleaseGateError(f"Recovered batch {batch_id} has duplicate or inconsistent Patch recovery events.")
        details = events[0].details
        failed_outputs = details.get("failed_output_files")
        if not isinstance(failed_outputs, Mapping) or not failed_outputs:
            raise ReleaseGateError(f"Recovered batch {batch_id} has no failed-output path inventory.")
        candidate_dirs: set[Path] = set()
        for role, raw_paths in failed_outputs.items():
            if not isinstance(role, str) or not isinstance(raw_paths, list) or not raw_paths:
                raise ReleaseGateError(f"Recovered batch {batch_id} has invalid failed-output records for {role}.")
            for raw_path in raw_paths:
                if not isinstance(raw_path, str) or not raw_path.strip():
                    raise ReleaseGateError(f"Recovered batch {batch_id} has an invalid failed output path.")
                output_path = Path(raw_path).expanduser().resolve()
                if output_path.parent.name != "documents":
                    raise ReleaseGateError(f"Recovered batch {batch_id} failed output is outside its documents directory.")
                candidate_dirs.add(output_path.parent.parent)
        if len(candidate_dirs) != 1:
            raise ReleaseGateError(f"Recovered batch {batch_id} failed outputs do not share one batch directory.")
        batch_dir = next(iter(candidate_dirs))
        if batch_dir.name != f"batch-{batch_id}":
            raise ReleaseGateError(f"Recovered batch {batch_id} event paths do not resolve to its declared directory.")

        try:
            intent = read_batch_intent(batch_dir)
        except Exception as exc:
            raise ReleaseGateError(f"Cannot validate recovered batch {batch_id} intent: {exc}") from exc
        recovery_intent_path = batch_dir / "batch_recovery_intent.json"
        recovery_receipt_path = batch_dir / "batch_recovered.json"
        scope_path = batch_dir / "batch_scope.json"
        for label, path in (
            ("recovery intent", recovery_intent_path),
            ("recovery receipt", recovery_receipt_path),
            ("batch scope", scope_path),
        ):
            if not path.is_file():
                raise ReleaseGateError(f"Recovered batch {batch_id} is missing its {label}: {path}.")
        try:
            recovery_intent = json.loads(recovery_intent_path.read_text(encoding="utf-8"))
            receipt = _validate_recovered_batch_receipt(batch_dir, intent, patch_status_by_id)
            scope_payload = json.loads(scope_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ReleaseGateError(f"Recovered batch {batch_id} failed receipt validation: {exc}") from exc
        selected_task_ids = recovery_intent.get("selected_task_ids")
        batch_task_ids = recovery_intent.get("batch_task_ids")
        intent_all_task_ids = intent.get("all_selected_task_ids", intent.get("selected_task_ids"))
        if (
            not isinstance(selected_task_ids, list)
            or not isinstance(batch_task_ids, list)
            or selected_task_ids != intent_all_task_ids
            or batch_task_ids != intent.get("selected_task_ids")
            or not set(selected_task_ids).issubset(workflow_task_ids)
        ):
            raise ReleaseGateError(
                f"Recovered batch {batch_id} task sets differ from its original batch intent or current Workflow Ledger."
            )
        _validate_release_batch_scope(
            intent,
            scope_payload,
            revision_ledger=revision_ledger,
            revision_ledger_path=revision_path,
            workflow_task_ids=workflow_task_ids,
        )
        parent_documents = recovery_intent.get("parent_documents")
        if not isinstance(parent_documents, Mapping) or set(parent_documents) != set(intent.get("documents", {})):
            raise ReleaseGateError(f"Recovered batch {batch_id} active-parent roles differ from its original intent.")
        for role, parent in parent_documents.items():
            intent_parent = intent["documents"].get(role)
            if (
                not isinstance(parent, Mapping)
                or not isinstance(intent_parent, Mapping)
                or not isinstance(parent.get("file"), str)
                or not isinstance(parent.get("sha256"), str)
                or Path(parent["file"]).expanduser().resolve()
                    != Path(intent_parent["parent_file"]).expanduser().resolve()
                or parent["sha256"] != intent_parent["parent_sha256"]
            ):
                raise ReleaseGateError(f"Recovered batch {batch_id} parent evidence differs from intent for {role}.")
            parent_path = Path(parent["file"]).expanduser().resolve()
            if not parent_path.is_file() or sha256_file(parent_path) != parent["sha256"]:
                raise ReleaseGateError(f"Recovered batch {batch_id} active parent is missing or stale for {role}.")

        event_patch_ids = set(patch_ids)
        recovery_patch_ids = recovery_intent.get("patch_ids")
        recovery_events = recovery_intent.get("revision_events")
        actual_event_records = [event.to_dict() for event in events]
        expected_events_by_subject = {
            row.get("subject_id"): row
            for row in recovery_events
            if isinstance(row, Mapping)
        } if isinstance(recovery_events, list) else {}
        if (
            intent.get("batch_id") != batch_id
            or recovery_intent.get("batch_id") != batch_id
            or receipt.get("batch_id") != batch_id
            or receipt.get("status") != "recovered"
            or not isinstance(recovery_patch_ids, list)
            or set(recovery_patch_ids) != event_patch_ids
            or receipt.get("patch_ids") != recovery_patch_ids
            or not isinstance(recovery_events, list)
            or len(recovery_events) != len(events)
            or set(expected_events_by_subject) != event_patch_ids
            or any(expected_events_by_subject.get(event.subject_id) != event_record for event, event_record in zip(events, actual_event_records))
            or any(
                event.details.get(key) != recovery_intent.get(intent_key)
                for event in events
                for key, intent_key in (
                    ("recovery_id", "recovery_id"),
                    ("parent_documents", "parent_documents"),
                    ("failed_output_files", "failed_output_files"),
                    ("artifact_inventory", "artifact_inventory"),
                    ("batch_intent_sha256", "batch_intent_sha256"),
                    ("batch_scope_sha256", "batch_scope_sha256"),
                )
            )
            or not isinstance(recovery_intent.get("workflow_ledger_file"), str)
            or Path(recovery_intent["workflow_ledger_file"]).expanduser().resolve() != workflow_path
            or not isinstance(recovery_intent.get("revision_ledger_file"), str)
            or Path(recovery_intent["revision_ledger_file"]).expanduser().resolve() != revision_path
        ):
            raise ReleaseGateError(f"Recovered batch {batch_id} event history does not match its recovery intent/receipt.")
        records.append({
            "batch_id": batch_id,
            "directory": str(batch_dir),
            "intent": _sha_record(batch_dir / "batch_intent.json"),
            "scope": _sha_record(scope_path),
            "recovery_intent": _sha_record(recovery_intent_path),
            "recovery_receipt": _sha_record(recovery_receipt_path),
            "recovery_id": receipt["recovery_id"],
            "selected_task_ids": list(receipt.get("selected_task_ids", [])),
            "batch_task_ids": list(recovery_intent.get("batch_task_ids", [])),
            "patch_ids": list(receipt["patch_ids"]),
            "parent_documents": receipt["parent_documents"],
            "failed_output_files": receipt["failed_output_files"],
            "retained_artifacts": receipt["retained_artifacts"],
            "retained_batch_files": True,
        })
    return records


def build_final_release_manifest(

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
    revision_path = Path(revision_ledger_path).expanduser().resolve()
    workflow_path = Path(workflow_ledger_path).expanduser().resolve()
    for path in (revision_path, workflow_path):
        if not path.is_file():
            raise ReleaseGateError(f"Required source ledger does not exist: {path}.")
    revision_sha_at_start = sha256_file(revision_path)
    workflow_sha_at_start = sha256_file(workflow_path)
    try:
        stored_revision = json.loads(revision_path.read_text(encoding="utf-8"))
        stored_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError(f"Cannot reload source ledgers for release binding: {exc}") from exc
    if stored_revision != revision_ledger.to_dict():
        raise ReleaseGateError(
            "In-memory RevisionLedger does not match its current on-disk version; reload before release."
        )
    if stored_workflow != dict(workflow_ledger):
        raise ReleaseGateError(
            "In-memory Workflow Ledger does not match its current on-disk version; reload before release."
        )
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
    batch_records = _collect_release_batch_history(
        workflow_ledger,
        active_documents,
        revision_ledger=revision_ledger,
        revision_ledger_path=revision_path,
        workflow_ledger_path=workflow_path,
    )
    recovered_batch_records = _collect_recovered_batch_history(
        revision_ledger,
        published_batch_ids={row["batch_id"] for row in batch_records},
        revision_ledger_path=revision_path,
        workflow_ledger_path=workflow_path,
        workflow_task_ids={
            row.get("task_id")
            for row in workflow_ledger.get("tasks", [])
            if isinstance(row, Mapping) and isinstance(row.get("task_id"), str)
        },
    )
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

    _validate_response_unit_task_attribution(revision_ledger.response_units, workflow_tasks)

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
        if task.get("status") != "completed":
            raise ReleaseGateError(
                f"Workflow task {task_id} has reviewer comments but is not completed in the Workflow Ledger."
            )
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

    consistency_audit = build_final_docx_consistency_audit(revision_ledger)
    if consistency_audit["status"] != "PASS":
        raise ReleaseGateError(
            "Deterministic Figure/Table cross-document audit found blocking issue(s): "
            + str(len(consistency_audit["blocking_findings"]))
            + ". Inspect the Working Clean View audit details before final release."
        )

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
        page_counts = {
            _require_word_page_count(row.get("word_page_count"), role)
            for row in matching_rows
        }
        if len(page_counts) != 1:
            raise ReleaseGateError(f"Word COM page count is inconsistent across location maps for {role}.")
        final_record["word_page_count"] = next(iter(page_counts))
        final_record["sequence_audit"] = _require_consistent_sequence_audits(
            [row.get("sequence_audit") for row in matching_rows], role
        )
        quality_records: list[tuple[dict[str, Any], Path, dict[str, Any], str]] = []
        if any(
            not isinstance(row.get("quality_report_file"), str)
            or not isinstance(row.get("quality_report_sha256"), str)
            for row in matching_rows
        ):
            raise ReleaseGateError(
                f"Every final Word location map must carry a hash-bound DOCX quality report for {role}."
            )
        for row in matching_rows:
            quality_path = Path(row["quality_report_file"]).expanduser().resolve()
            if not quality_path.is_file() or sha256_file(quality_path) != row["quality_report_sha256"]:
                raise ReleaseGateError(f"Final Word Clean DOCX quality report is missing or stale: {quality_path}.")
            try:
                quality_payload = json.loads(quality_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ReleaseGateError(f"Cannot read final DOCX quality report {quality_path}: {exc}") from exc
            quality_status = _require_bound_final_quality_report(
                quality_payload,
                role=role,
                docx_path=final_record["final_clean_file"],
                docx_sha256=final_record["final_clean_sha256"],
                pdf_path=final_record["final_pdf_file"],
                pdf_sha256=final_record["final_pdf_sha256"],
                word_page_count=final_record["word_page_count"],
            )
            quality_records.append((row, quality_path, quality_payload, quality_status))
        quality_row, quality_path, quality_payload, quality_status = quality_records[0]
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
            "status": quality_status,
            "release_reaudit_status": final_quality["status"],
        }

    response_md_path = Path(response_letter_md).expanduser().resolve()
    response_docx_path = Path(response_letter_docx).expanduser().resolve()
    response_quality_path = Path(response_letter_quality).expanduser().resolve()
    for path in (revision_path, workflow_path, response_md_path, response_docx_path, response_quality_path):
        if not path.is_file():
            raise ReleaseGateError(f"Required release artifact is missing: {path}.")

    response_quality_payload = json.loads(response_quality_path.read_text(encoding="utf-8"))
    response_docx_sha = sha256_file(response_docx_path)
    response_quality_status = _require_bound_response_quality_report(
        response_quality_payload,
        docx_path=str(response_docx_path),
        docx_sha256=response_docx_sha,
    )
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

    # The quality report proves that the existing DOCX is structurally acceptable;
    # it does not prove that its content still corresponds to the current ledger.
    # Re-render the deterministic document from the current ResponseUnits and compare
    # all semantic paragraphs, ignoring only optional manuscript title/author metadata.
    try:
        from response_letter_docx import render_response_docx
        with tempfile.TemporaryDirectory(prefix="co-reviewer-release-response-") as temp_dir:
            expected_docx_path = Path(temp_dir) / "expected_response_letter.docx"
            render_response_docx(response_plan, expected_docx_path)
            expected_body = _normalise_whitespace(_response_docx_body_text(expected_docx_path))
        actual_body = _normalise_whitespace(_response_docx_body_text(response_docx_path))
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError(
            f"Cannot regenerate the canonical Response Letter DOCX for content comparison: {exc}"
        ) from exc
    if actual_body != expected_body:
        raise ReleaseGateError(
            "Response Letter DOCX content does not match the current ResponseUnits/PatchOperations; regenerate it."
        )

    if sha256_file(revision_path) != revision_sha_at_start:
        raise ReleaseGateError(
            "RevisionLedger changed while the final release checks were running; discard this draft package and regenerate it."
        )
    if sha256_file(workflow_path) != workflow_sha_at_start:
        raise ReleaseGateError(
            "Workflow Ledger changed while the final release checks were running; discard this draft package and regenerate it."
        )

    deterministic_change_audit = audit_patch_operation_deltas(revision_ledger)
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
        "batches": batch_records,
        "recovered_batches": recovered_batch_records,
        "marked_documents": marked_documents,
        "deterministic_consistency_audit": consistency_audit,
        "final_documents": final_documents,
        "patches": patch_records,
        "response_units": response_records,
        "deterministic_change_audit": deterministic_change_audit,
        "location_maps": [
            {"file": row["file"], "sha256": row["sha256"]}
            for row in location_maps.values()
        ],
        "artifacts": {
            "response_letter_markdown": _sha_record(response_md_path),
            "response_letter_docx": _sha_record(response_docx_path),
            "response_letter_quality": {
                **_sha_record(response_quality_path),
                "status": response_quality_status,
                "release_reaudit_status": current_response_quality["status"],
            },
        },
        "checks": {
            "all_published_batch_history_valid": True,
            "all_recovered_batch_history_valid": True,
            "all_active_patches_verified": True,
            "all_response_units_completed": True,
            "active_pointers_match_marked_docx": True,
            "all_final_clean_docx_pdf_hashes_valid": True,
            "all_word_quality_reports_pass": True,
            "response_letter_contains_verified_patch_excerpts": True,
            "numeric_unit_reference_deltas_surfaced_for_human_review": True,
        },
    }

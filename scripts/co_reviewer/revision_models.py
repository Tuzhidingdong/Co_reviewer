# -*- coding: utf-8 -*-
"""Typed Patch, RevisionLedger and ResponseUnit contracts.

The legacy DOCX writer remains the only document-writing implementation. This
module converts explicitly authored, evidence-linked patches into its existing
revision-plan format and records the resulting execution history.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import TargetRef

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PATCH_ACTIONS = frozenset({"replace"})
_PATCH_STATES = frozenset({
    "planned", "preflighted", "approved", "applied",
    "verified", "superseded", "blocked",
})
_RESPONSE_STATES = frozenset({
    "draft", "blocked", "verified", "location_mapped", "completed",
})


class RevisionModelError(ValueError):
    """Raised when an execution or response record is incomplete or unsafe."""


def _text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise RevisionModelError(f"{field_name} must be a non-empty string.")
    return value


def _sha256(value: Any, field_name: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise RevisionModelError(f"{field_name} must be a lowercase SHA-256 digest.")
    return value


def _aware_timestamp(value: Any, field_name: str) -> str:
    _text(value, field_name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RevisionModelError(f"{field_name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RevisionModelError(f"{field_name} must include a timezone.")
    return value


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_docx_path(value: Any, field_name: str) -> str:
    path = _text(value, field_name)
    if Path(path).suffix.casefold() != ".docx":
        raise RevisionModelError(f"{field_name} must point to a .docx file.")
    return path


@dataclass(frozen=True)
class PatchOperation:
    """One atomic exact-text operation supported by the existing DOCX writer."""

    operation_id: str
    document: str
    input_file: str
    output_file: str
    part: str
    find: str
    replace: str
    p_idx: int | None = None
    location: str = ""
    source_sha256: str | None = None
    evidence_ids: tuple[str, ...] = ()
    target_ref: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _text(self.operation_id, "operation_id")
        _text(self.document, "document")
        _safe_docx_path(self.input_file, "input_file")
        _safe_docx_path(self.output_file, "output_file")
        if Path(self.input_file).expanduser().resolve() == Path(self.output_file).expanduser().resolve():
            raise RevisionModelError("Patch input_file and output_file must be different.")
        _text(self.part, "part")
        if not self.part.startswith("word/") or ".." in Path(self.part).parts:
            raise RevisionModelError("Patch part must be a safe path inside the Word package.")
        _text(self.find, "find")
        _text(self.replace, "replace", allow_empty=True)
        if self.p_idx is not None and (
            not isinstance(self.p_idx, int) or isinstance(self.p_idx, bool) or self.p_idx < 0
        ):
            raise RevisionModelError("p_idx must be a non-negative integer or null.")
        _sha256(self.source_sha256, "source_sha256", optional=True)
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_ids):
            raise RevisionModelError("evidence_ids must contain non-empty strings.")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise RevisionModelError("evidence_ids must be unique.")
        if self.target_ref is not None:
            if not isinstance(self.target_ref, Mapping):
                raise RevisionModelError("target_ref must be an object or null.")
            TargetRef(
                document_id=self.target_ref.get("document_id"),
                revision_id=self.target_ref.get("revision_id"),
                object_id=self.target_ref.get("object_id"),
                object_type=self.target_ref.get("object_type"),
                part=self.target_ref.get("part"),
                object_fingerprint=self.target_ref.get("object_fingerprint"),
                start_offset=self.target_ref.get("start_offset"),
                end_offset=self.target_ref.get("end_offset"),
                location_hint=self.target_ref.get("location_hint"),
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, fallback_id: str = "") -> "PatchOperation":
        if not isinstance(data, Mapping):
            raise RevisionModelError("Patch operation must be an object.")
        return cls(
            operation_id=data.get("operation_id") or fallback_id or str(uuid.uuid4()),
            document=data.get("document"),
            input_file=data.get("input_file"),
            output_file=data.get("output_file"),
            part=data.get("part", "word/document.xml"),
            find=data.get("find"),
            replace=data.get("replace"),
            p_idx=data.get("p_idx"),
            location=data.get("location", ""),
            source_sha256=data.get("source_sha256"),
            evidence_ids=tuple(data.get("evidence_ids", [])),
            target_ref=data.get("target_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "document": self.document,
            "input_file": self.input_file,
            "output_file": self.output_file,
            "part": self.part,
            "find": self.find,
            "replace": self.replace,
            "p_idx": self.p_idx,
            "location": self.location,
            "source_sha256": self.source_sha256,
            "evidence_ids": list(self.evidence_ids),
            "target_ref": dict(self.target_ref) if self.target_ref is not None else None,
        }

    def to_legacy_target(self) -> dict[str, Any]:
        """Return the exact target object consumed by co_reviewer_cli.apply_plan()."""
        target = {
            "document": self.document,
            "input_file": self.input_file,
            "output_file": self.output_file,
            "part": self.part,
            "find": self.find,
            "replace": self.replace,
            "location": self.location,
        }
        if self.p_idx is not None:
            target["p_idx"] = self.p_idx
        if self.source_sha256:
            target["source_sha256"] = self.source_sha256
        return target


@dataclass
class Patch:
    """A task-scoped group of atomic operations with explicit supersession lineage."""

    patch_id: str
    task_id: str
    reviewer: str
    comment: str
    proposed_response: str
    operations: list[PatchOperation]
    status: str = "planned"
    supersedes_patch_id: str | None = None
    approval_fingerprint: str | None = None
    apply_report_ids: list[str] = field(default_factory=list)
    verification_report_id: str | None = None
    verified_output_hashes: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        for name in ("patch_id", "task_id", "reviewer", "comment"):
            _text(getattr(self, name), name)
        _text(self.proposed_response, "proposed_response", allow_empty=True)
        if not self.operations:
            raise RevisionModelError("A Patch must contain at least one explicit operation.")
        if any(not isinstance(item, PatchOperation) for item in self.operations):
            raise RevisionModelError("Patch operations must be PatchOperation records.")
        op_ids = [item.operation_id for item in self.operations]
        if len(op_ids) != len(set(op_ids)):
            raise RevisionModelError("Patch operation IDs must be unique.")
        if self.status not in _PATCH_STATES:
            raise RevisionModelError(f"Unsupported Patch status: {self.status!r}.")
        if self.supersedes_patch_id is not None:
            _text(self.supersedes_patch_id, "supersedes_patch_id")
        _sha256(self.approval_fingerprint, "approval_fingerprint", optional=True)
        if self.status in {"approved", "applied", "verified"} and not self.approval_fingerprint:
            raise RevisionModelError(f"Patch in {self.status} state requires approval_fingerprint.")

    @property
    def scope_fingerprint(self) -> str:
        return _fingerprint({
            "patch_id": self.patch_id,
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "comment": self.comment,
            "proposed_response": self.proposed_response,
            "operations": [item.to_dict() for item in self.operations],
            "supersedes_patch_id": self.supersedes_patch_id,
        })

    def to_legacy_revision_item(self) -> dict[str, Any]:
        """Create a pending legacy revision item; approval is deliberately not copied."""
        if self.status in {"superseded", "blocked"}:
            raise RevisionModelError(
                f"Patch {self.patch_id} in {self.status} state cannot be sent to the DOCX writer."
            )
        return {
            "id": self.patch_id,
            "reviewer": self.reviewer,
            "comment": self.comment,
            "proposed_response": self.proposed_response,
            "status": "planned",
            "targets": [item.to_legacy_target() for item in self.operations],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "task_id": self.task_id,
            "reviewer": self.reviewer,
            "comment": self.comment,
            "proposed_response": self.proposed_response,
            "operations": [item.to_dict() for item in self.operations],
            "status": self.status,
            "supersedes_patch_id": self.supersedes_patch_id,
            "approval_fingerprint": self.approval_fingerprint,
            "apply_report_ids": list(self.apply_report_ids),
            "verification_report_id": self.verification_report_id,
            "verified_output_hashes": copy.deepcopy(self.verified_output_hashes),
        }


@dataclass(frozen=True)
class RevisionDocument:
    """One source/revised DOCX pair in a revision round."""

    document_id: str
    revision_id: str
    role: str
    input_file: str
    output_file: str
    source_sha256: str | None = None
    output_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in ("document_id", "revision_id", "role"):
            _text(getattr(self, name), name)
        _safe_docx_path(self.input_file, "input_file")
        _safe_docx_path(self.output_file, "output_file")
        if Path(self.input_file).expanduser().resolve() == Path(self.output_file).expanduser().resolve():
            raise RevisionModelError("RevisionDocument input and output must be different.")
        _sha256(self.source_sha256, "source_sha256", optional=True)
        _sha256(self.output_sha256, "output_sha256", optional=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "role": self.role,
            "input_file": self.input_file,
            "output_file": self.output_file,
            "source_sha256": self.source_sha256,
            "output_sha256": self.output_sha256,
        }


@dataclass(frozen=True)
class RevisionEvent:
    event_id: str
    patch_id: str
    event_type: str
    timestamp_utc: str
    actor: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("event_id", "patch_id", "event_type", "actor"):
            _text(getattr(self, name), name)
        _aware_timestamp(self.timestamp_utc, "timestamp_utc")
        if not isinstance(self.details, Mapping):
            raise RevisionModelError("RevisionEvent details must be an object.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "patch_id": self.patch_id,
            "event_type": self.event_type,
            "timestamp_utc": self.timestamp_utc,
            "actor": self.actor,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ResponseLocation:
    """A final output location backed by a frozen PDF/render mapping report."""

    document: str
    output_file: str
    page: int
    line_start: int
    line_end: int
    pdf_sha256: str
    location_map_id: str
    mapping_fingerprint: str

    def __post_init__(self) -> None:
        _text(self.document, "document")
        _safe_docx_path(self.output_file, "output_file")
        for name in ("page", "line_start", "line_end"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise RevisionModelError(f"{name} must be a positive integer.")
        if self.line_end < self.line_start:
            raise RevisionModelError("line_end cannot be earlier than line_start.")
        _sha256(self.pdf_sha256, "pdf_sha256")
        _text(self.location_map_id, "location_map_id")
        _sha256(self.mapping_fingerprint, "mapping_fingerprint")

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "output_file": self.output_file,
            "page": self.page,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "pdf_sha256": self.pdf_sha256,
            "location_map_id": self.location_map_id,
            "mapping_fingerprint": self.mapping_fingerprint,
        }

    @property
    def display_text(self) -> str:
        line_text = (
            f"line {self.line_start}"
            if self.line_start == self.line_end
            else f"lines {self.line_start}-{self.line_end}"
        )
        return f"Page {self.page}, {line_text}"


@dataclass
class ResponseUnit:
    """One reviewer comment and its response, linked to tasks, patches and final lines."""

    response_unit_id: str
    reviewer: str
    comment_id: str
    comment_text: str
    response_text: str
    linked_task_ids: list[str] = field(default_factory=list)
    linked_patch_ids: list[str] = field(default_factory=list)
    linked_targets: list[dict[str, str]] = field(default_factory=list)
    status: str = "draft"
    semantic_review_confirmed: bool = False
    verification_report_id: str | None = None
    location_map_fingerprint: str | None = None
    final_locations: list[ResponseLocation] = field(default_factory=list)
    completed_at_utc: str | None = None

    def __post_init__(self) -> None:
        for name in ("response_unit_id", "reviewer", "comment_id", "comment_text"):
            _text(getattr(self, name), name)
        _text(self.response_text, "response_text", allow_empty=True)
        if self.status not in _RESPONSE_STATES:
            raise RevisionModelError(f"Unsupported ResponseUnit status: {self.status!r}.")
        for field_name in ("linked_task_ids", "linked_patch_ids"):
            values = getattr(self, field_name)
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise RevisionModelError(f"{field_name} must contain non-empty strings.")
            if len(values) != len(set(values)):
                raise RevisionModelError(f"{field_name} must be unique.")
        if self.semantic_review_confirmed and not (
            isinstance(self.verification_report_id, str) and self.verification_report_id.strip()
        ):
            raise RevisionModelError("Semantic confirmation requires verification_report_id.")
        if self.location_map_fingerprint is not None:
            _sha256(self.location_map_fingerprint, "location_map_fingerprint")
        if self.status in {"location_mapped", "completed"}:
            if not self.semantic_review_confirmed:
                raise RevisionModelError("Location-mapped ResponseUnit requires semantic review.")
            if not self.final_locations:
                raise RevisionModelError("Location-mapped ResponseUnit requires final PDF line locations.")
            if not self.location_map_fingerprint:
                raise RevisionModelError("Location-mapped ResponseUnit requires location_map_fingerprint.")
        if self.status == "completed":
            if not self.completed_at_utc:
                raise RevisionModelError("Completed ResponseUnit requires completed_at_utc.")
            _aware_timestamp(self.completed_at_utc, "completed_at_utc")

    @property
    def final_location_fingerprint(self) -> str:
        return _fingerprint([item.to_dict() for item in self.final_locations])

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_unit_id": self.response_unit_id,
            "reviewer": self.reviewer,
            "comment_id": self.comment_id,
            "comment_text": self.comment_text,
            "response_text": self.response_text,
            "linked_task_ids": list(self.linked_task_ids),
            "linked_patch_ids": list(self.linked_patch_ids),
            "linked_targets": copy.deepcopy(self.linked_targets),
            "status": self.status,
            "semantic_review_confirmed": self.semantic_review_confirmed,
            "verification_report_id": self.verification_report_id,
            "location_map_fingerprint": self.location_map_fingerprint,
            "final_locations": [item.to_dict() for item in self.final_locations],
            "completed_at_utc": self.completed_at_utc,
        }


@dataclass
class RevisionLedger:
    """Durable patch and response execution ledger for one review round."""

    revision_round_id: str
    documents: list[RevisionDocument] = field(default_factory=list)
    patches: list[Patch] = field(default_factory=list)
    response_units: list[ResponseUnit] = field(default_factory=list)
    events: list[RevisionEvent] = field(default_factory=list)
    schema_version: str = "0.5.1"
    workflow_ledger_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _text(self.revision_round_id, "revision_round_id")
        if self.schema_version != "0.5.1":
            raise RevisionModelError("RevisionLedger schema_version must be '0.5.1'.")
        _sha256(self.workflow_ledger_fingerprint, "workflow_ledger_fingerprint", optional=True)
        self.validate()

    def validate(self) -> list[str]:
        errors: list[str] = []
        patch_ids = [item.patch_id for item in self.patches]
        if len(patch_ids) != len(set(patch_ids)):
            errors.append("RevisionLedger contains duplicate patch_id values.")
        response_ids = [item.response_unit_id for item in self.response_units]
        if len(response_ids) != len(set(response_ids)):
            errors.append("RevisionLedger contains duplicate response_unit_id values.")
        known_patches = set(patch_ids)
        for patch in self.patches:
            if patch.supersedes_patch_id and patch.supersedes_patch_id not in known_patches:
                errors.append(
                    f"Patch {patch.patch_id} supersedes unknown patch {patch.supersedes_patch_id}."
                )
            if patch.supersedes_patch_id == patch.patch_id:
                errors.append(f"Patch {patch.patch_id} cannot supersede itself.")
        for unit in self.response_units:
            unknown = set(unit.linked_patch_ids) - known_patches
            if unknown:
                errors.append(
                    f"ResponseUnit {unit.response_unit_id} links unknown patch(es): "
                    + ", ".join(sorted(unknown))
                )
        for event in self.events:
            if event.patch_id not in known_patches:
                errors.append(f"RevisionEvent {event.event_id} refers to unknown patch.")
        return errors

    def to_legacy_revision_plan(self) -> dict[str, Any]:
        """Bridge exact patches into the existing preflight/approve/apply CLI.

        This always emits pending items. The legacy CLI must re-preflight and
        record fresh human approval; a Workflow Ledger approval is not silently
        imported into the older execution gate.
        """
        items = [
            patch.to_legacy_revision_item()
            for patch in self.patches
            if patch.status not in {"superseded", "blocked"}
        ]
        if not items:
            raise RevisionModelError("RevisionLedger has no executable patches.")
        return {
            "schema_version": "1.0",
            "approved": False,
            "revision_items": items,
        }

    def record_legacy_apply_reports(
        self, reports: Sequence[Mapping[str, Any]], *, actor: str = "Co_Reviewer"
    ) -> None:
        by_id = {patch.patch_id: patch for patch in self.patches}
        now = datetime.now(timezone.utc).isoformat()
        for report in reports:
            if not isinstance(report, Mapping):
                raise RevisionModelError("Apply report entries must be objects.")
            results = report.get("results", [])
            applied_ids = {
                item.get("revision_item_id")
                for item in results if isinstance(item, Mapping) and item.get("status") == "applied"
            }
            for patch_id in applied_ids:
                patch = by_id.get(patch_id)
                if patch is None:
                    continue
                patch.status = "applied"
                if report.get("output_file"):
                    report_id = str(report.get("report_id") or report.get("output_file"))
                    patch.apply_report_ids.append(report_id)
                    self.events.append(RevisionEvent(
                        event_id=str(uuid.uuid4()),
                        patch_id=patch_id,
                        event_type="patch_applied",
                        timestamp_utc=now,
                        actor=actor,
                        details={
                            "input_file": report.get("input_file", ""),
                            "output_file": report.get("output_file", ""),
                            "edits_applied": report.get("edits_applied", 0),
                        },
                    ))

    def record_legacy_verified_items(
        self,
        revision_plan: Mapping[str, Any],
        *,
        actor: str = "Human Author",
        verification_report_id: str,
    ) -> None:
        _text(verification_report_id, "verification_report_id")
        rows = revision_plan.get("revision_items", [])
        if not isinstance(rows, list):
            raise RevisionModelError("Legacy revision plan must contain revision_items.")
        by_id = {patch.patch_id: patch for patch in self.patches}
        now = datetime.now(timezone.utc).isoformat()
        for item in rows:
            if not isinstance(item, Mapping) or item.get("status") != "verified":
                continue
            patch = by_id.get(item.get("id"))
            if patch is None:
                continue
            if item.get("semantic_review_confirmed") is not True:
                raise RevisionModelError(
                    f"Legacy item {patch.patch_id} lacks semantic-review confirmation."
                )
            patch.status = "verified"
            patch.verification_report_id = verification_report_id
            patch.verified_output_hashes = copy.deepcopy(
                item.get("verified_output_hashes", [])
            )
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                patch_id=patch.patch_id,
                event_type="verification",
                timestamp_utc=now,
                actor=actor,
                details={
                    "verification_report_id": verification_report_id,
                    "verified_output_hashes": patch.verified_output_hashes,
                },
            ))

    def to_dict(self) -> dict[str, Any]:
        self.validate_or_raise()
        return {
            "schema_version": self.schema_version,
            "revision_round_id": self.revision_round_id,
            "workflow_ledger_fingerprint": self.workflow_ledger_fingerprint,
            "documents": [item.to_dict() for item in self.documents],
            "patches": [item.to_dict() for item in self.patches],
            "response_units": [item.to_dict() for item in self.response_units],
            "events": [item.to_dict() for item in self.events],
        }

    def validate_or_raise(self) -> None:
        errors = self.validate()
        if errors:
            raise RevisionModelError("RevisionLedger validation failed:\n- " + "\n- ".join(errors))


def build_revision_ledger(
    workflow_ledger: Mapping[str, Any],
    *,
    document_paths: Mapping[str, Mapping[str, str]],
    reviewer_comments_by_id: Mapping[str, str] | None = None,
    reviewer_by_task: Mapping[str, str] | None = None,
) -> RevisionLedger:
    """Build explicit Patch and ResponseUnit records from a Workflow Ledger.

    No edit text is inferred. Every executable patch operation must supply
    find/replace and both paths. Reviewer comment text must come from explicit
    reviewer evidence or reviewer_comments_by_id.
    """
    if not isinstance(workflow_ledger, Mapping):
        raise RevisionModelError("workflow_ledger must be an object.")
    round_id = _text(workflow_ledger.get("revision_round_id"), "revision_round_id")
    task_rows = workflow_ledger.get("tasks")
    if not isinstance(task_rows, list):
        raise RevisionModelError("Workflow Ledger tasks must be an array.")
    reviewer_comments_by_id = reviewer_comments_by_id or {}
    reviewer_by_task = reviewer_by_task or {}

    patches: list[Patch] = []
    response_units: list[ResponseUnit] = []

    for task in task_rows:
        if not isinstance(task, Mapping):
            raise RevisionModelError("Workflow task entries must be objects.")
        task_id = _text(task.get("task_id"), "task_id")
        comment_ids = [str(value) for value in task.get("source_comment_ids", [])]
        reviewer = reviewer_by_task.get(task_id, "Reviewer")
        evidence = task.get("evidence", [])
        comment_texts = []
        for comment_id in comment_ids:
            if comment_id in reviewer_comments_by_id:
                comment_texts.append(reviewer_comments_by_id[comment_id])
        if not comment_texts:
            comment_texts = [
                row.get("source_excerpt", "")
                for row in evidence
                if isinstance(row, Mapping)
                and row.get("evidence_type") == "reviewer_comment"
                and row.get("source_excerpt")
            ]
        comment_text = "\n".join(dict.fromkeys(comment_texts)).strip()

        raw_operations = task.get("patch_operations", [])
        if not isinstance(raw_operations, list):
            raise RevisionModelError(f"Task {task_id} patch_operations must be an array.")
        operation_records: list[PatchOperation] = []
        for index, raw in enumerate(raw_operations):
            if not isinstance(raw, Mapping):
                raise RevisionModelError(f"Task {task_id} patch_operations[{index}] must be an object.")
            document = raw.get("document")
            paths = document_paths.get(document, {}) if isinstance(document, str) else {}
            data = dict(raw)
            data.setdefault("input_file", paths.get("input_file"))
            data.setdefault("output_file", paths.get("output_file"))
            data.setdefault("document", document)
            operation_records.append(
                PatchOperation.from_mapping(
                    data, fallback_id=f"{task_id}-OP{index + 1:03d}"
                )
            )

        if operation_records:
            if not comment_text:
                raise RevisionModelError(
                    f"Task {task_id} has executable patch operations but no traceable reviewer comment text."
                )
            patch_id = str(task.get("patch_id") or task_id)
            patches.append(Patch(
                patch_id=patch_id,
                task_id=task_id,
                reviewer=reviewer,
                comment=comment_text,
                proposed_response=str(task.get("proposed_response") or ""),
                operations=operation_records,
                status="planned",
                supersedes_patch_id=task.get("supersedes_patch_id"),
            ))

        # One ResponseUnit per source reviewer comment. Response text is copied
        # only when the workflow task explicitly contains it; it is never invented.
        if comment_ids and comment_text:
            for index, comment_id in enumerate(comment_ids):
                specific_comment = reviewer_comments_by_id.get(comment_id)
                if not specific_comment:
                    matching = next((
                        row.get("source_excerpt", "")
                        for row in evidence
                        if isinstance(row, Mapping)
                        and row.get("evidence_type") == "reviewer_comment"
                        and str(row.get("source_comment_id", "")) == comment_id
                    ), "")
                    specific_comment = matching or comment_text
                linked_targets = []
                for op in operation_records:
                    linked_targets.append({
                        "document": op.document,
                        "location": op.location,
                        "output_file": op.output_file,
                    })
                response_units.append(ResponseUnit(
                    response_unit_id=f"RU-{task_id}-{comment_id}",
                    reviewer=reviewer,
                    comment_id=comment_id,
                    comment_text=specific_comment,
                    response_text=str(task.get("proposed_response") or ""),
                    linked_task_ids=[task_id],
                    linked_patch_ids=[task_id] if operation_records else [],
                    linked_targets=linked_targets,
                    status="draft",
                ))

    ledger = RevisionLedger(
        revision_round_id=round_id,
        documents=[],
        patches=patches,
        response_units=response_units,
        workflow_ledger_fingerprint=_fingerprint(workflow_ledger),
    )
    ledger.validate_or_raise()
    return ledger


def response_units_to_legacy_plan(
    response_units: Sequence[ResponseUnit],
    patches: Sequence[Patch] = (),
) -> dict[str, Any]:
    """Adapt ResponseUnits to the current renderer without inventing DOCX targets."""
    patch_by_id = {item.patch_id: item for item in patches}
    revision_items = []
    for unit in response_units:
        linked_patches = [patch_by_id[item] for item in unit.linked_patch_ids if item in patch_by_id]
        targets = copy.deepcopy(unit.linked_targets)
        if unit.final_locations:
            for location in unit.final_locations:
                targets.append({
                    "document": location.document,
                    "location": location.display_text,
                    "output_file": location.output_file,
                })
        final_is_mapped = (
            unit.status in {"location_mapped", "completed"}
            and unit.semantic_review_confirmed
            and bool(unit.final_locations)
            and bool(unit.location_map_fingerprint)
        )
        patch_statuses = {item.status for item in linked_patches}
        status = (
            "verified" if final_is_mapped
            else "applied" if patch_statuses & {"applied", "verified"}
            else "planned"
        )
        revision_items.append({
            "id": unit.response_unit_id,
            "reviewer": unit.reviewer,
            "comment": unit.comment_text,
            "proposed_response": unit.response_text,
            "status": status,
            "targets": targets,
        })
    return {"revision_items": revision_items}

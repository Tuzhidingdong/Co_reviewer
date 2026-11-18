# -*- coding: utf-8 -*-
"""Typed Patch, RevisionLedger and ResponseUnit contracts.

The legacy DOCX writer remains the only document-writing implementation. This
module converts explicitly authored, evidence-linked patches into its existing
revision-plan format and records the resulting execution history.
"""
from __future__ import annotations

import copy
from collections import defaultdict
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import TargetRef, revision_item_fingerprint, sha256_file

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_PATCH_ACTIONS = frozenset({"replace"})
_PATCH_STATES = frozenset({
    "planned", "preflighted", "approved", "applied",
    "verified", "superseded", "blocked", "abandoned",
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


def _canonical_path(value: str) -> str:
    return str(Path(value).expanduser().resolve()).casefold()


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
    source_comment_ids: tuple[str, ...] = ()
    location_phrase: str | None = None
    target_ref: Mapping[str, Any] | None = None
    authorized_change: Mapping[str, Any] | None = None
    protected_spans: tuple[Mapping[str, Any], ...] = ()

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
        if not self.source_comment_ids:
            raise RevisionModelError("PatchOperation requires at least one exact source_comment_id.")
        if any(not isinstance(item, str) or not item.strip() for item in self.source_comment_ids):
            raise RevisionModelError("source_comment_ids must contain non-empty comment IDs.")
        if len(self.source_comment_ids) != len(set(self.source_comment_ids)):
            raise RevisionModelError("source_comment_ids must be unique.")
        if self.location_phrase is not None:
            _text(self.location_phrase, "location_phrase")
            if len(self.location_phrase) > 255 or any(ch in self.location_phrase for ch in "\r\n\t"):
                raise RevisionModelError("location_phrase must be a single-line Word Find phrase of at most 255 characters.")
        if self.authorized_change is not None:
            if not isinstance(self.authorized_change, Mapping) or not isinstance(self.authorized_change.get("intent"), str) or not self.authorized_change.get("intent", "").strip():
                raise RevisionModelError("authorized_change must contain a non-empty intent.")
            tokens = self.authorized_change.get("explicitly_authorized_tokens", [])
            if not isinstance(tokens, list):
                raise RevisionModelError("authorized_change.explicitly_authorized_tokens must be an array.")
            for token in tokens:
                if not isinstance(token, Mapping) or not isinstance(token.get("text"), str) or not isinstance(token.get("replacement"), str):
                    raise RevisionModelError("Each authorized token requires exact text and replacement strings.")
            spans = self.authorized_change.get("explicitly_authorized_spans", [])
            if not isinstance(spans, list) or any(not isinstance(span, Mapping) for span in spans):
                raise RevisionModelError("explicitly_authorized_spans must be an array of objects.")
        if any(not isinstance(span, Mapping) for span in self.protected_spans):
            raise RevisionModelError("protected_spans must contain objects.")
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
        raw_source_comment_ids = data.get("source_comment_ids", [])
        if not isinstance(raw_source_comment_ids, (list, tuple)):
            raise RevisionModelError("source_comment_ids must be an array of exact reviewer comment IDs.")
        if any(not isinstance(value, str) or not value.strip() for value in raw_source_comment_ids):
            raise RevisionModelError("source_comment_ids must contain non-empty strings.")
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
            source_comment_ids=tuple(raw_source_comment_ids),
            location_phrase=data.get("location_phrase"),
            target_ref=data.get("target_ref"),
            authorized_change=data.get("authorized_change"),
            protected_spans=tuple(data.get("protected_spans", [])),
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
            "source_comment_ids": list(self.source_comment_ids),
            "location_phrase": self.location_phrase,
            "target_ref": dict(self.target_ref) if self.target_ref is not None else None,
            "authorized_change": dict(self.authorized_change) if self.authorized_change is not None else None,
            "protected_spans": [dict(x) for x in self.protected_spans],
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
            "operation_id": self.operation_id,
            "source_comment_ids": list(self.source_comment_ids),
            "location_phrase": self.location_phrase,
            "authorized_change": dict(self.authorized_change) if self.authorized_change is not None else None,
            "protected_spans": [dict(x) for x in self.protected_spans],
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
    preflight_fingerprint: str | None = None
    preflight_report_id: str | None = None
    approval_fingerprint: str | None = None
    apply_report_ids: list[str] = field(default_factory=list)
    applied_revision_ids: list[dict[str, Any]] = field(default_factory=list)
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
        if not isinstance(self.applied_revision_ids, list):
            raise RevisionModelError("applied_revision_ids must be an array.")
        for record in self.applied_revision_ids:
            if not isinstance(record, Mapping):
                raise RevisionModelError("applied_revision_ids entries must be objects.")
            _safe_docx_path(record.get("input_file"), "applied_revision_ids.input_file")
            _safe_docx_path(record.get("output_file"), "applied_revision_ids.output_file")
            _sha256(record.get("input_sha256"), "applied_revision_ids.input_sha256")
            _sha256(record.get("output_sha256"), "applied_revision_ids.output_sha256")
            revision_ids = record.get("revision_ids")
            if (not isinstance(revision_ids, list) or not revision_ids
                    or any(not isinstance(value, str) or not value.strip() for value in revision_ids)
                    or len(revision_ids) != len(set(revision_ids))):
                raise RevisionModelError("applied_revision_ids.revision_ids must be a non-empty unique string array.")
        if self.supersedes_patch_id is not None:
            _text(self.supersedes_patch_id, "supersedes_patch_id")
        _sha256(self.preflight_fingerprint, "preflight_fingerprint", optional=True)
        _sha256(self.approval_fingerprint, "approval_fingerprint", optional=True)
        if self.status in {"preflighted", "approved", "applied", "verified"} and not self.preflight_fingerprint:
            raise RevisionModelError(f"Patch in {self.status} state requires preflight_fingerprint.")
        if self.status in {"approved", "applied", "verified"} and not self.approval_fingerprint:
            raise RevisionModelError(f"Patch in {self.status} state requires approval_fingerprint.")
        if self.status in {"preflighted", "approved", "applied", "verified"} and not self.preflight_report_id:
            raise RevisionModelError(f"Patch in {self.status} state requires preflight_report_id.")

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
        if self.status != "planned":
            raise RevisionModelError(
                f"Patch {self.patch_id} is {self.status}; only a new planned Patch can be exported. "
                "Use its existing preflight/approval/apply history rather than resetting it."
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
            "preflight_fingerprint": self.preflight_fingerprint,
            "preflight_report_id": self.preflight_report_id,
            "approval_fingerprint": self.approval_fingerprint,
            "apply_report_ids": list(self.apply_report_ids),
            "applied_revision_ids": copy.deepcopy(self.applied_revision_ids),
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
    working_clean_file: str | None = None
    working_clean_sha256: str | None = None
    working_view_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in ("document_id", "revision_id", "role"):
            _text(getattr(self, name), name)
        _safe_docx_path(self.input_file, "input_file")
        _safe_docx_path(self.output_file, "output_file")
        # Equal input/output paths are permitted only for an unchanged source
        # document used as a no-change ResponseUnit anchor. RevisionLedger.validate
        # rejects this passthrough form whenever the role has any PatchOperation.
        _sha256(self.source_sha256, "source_sha256", optional=True)
        _sha256(self.output_sha256, "output_sha256", optional=True)
        _sha256(self.working_clean_sha256, "working_clean_sha256", optional=True)
        _sha256(self.working_view_fingerprint, "working_view_fingerprint", optional=True)
        working_values = (
            self.working_clean_file,
            self.working_clean_sha256,
            self.working_view_fingerprint,
        )
        if any(value is not None for value in working_values):
            if not all(isinstance(value, str) and value.strip() for value in working_values):
                raise RevisionModelError(
                    "Working Clean View path, SHA-256 and fingerprint must be recorded together."
                )
            working_path = _safe_docx_path(
                self.working_clean_file, "working_clean_file"
            )
            resolved_working = Path(working_path).expanduser().resolve()
            if resolved_working in {
                Path(self.input_file).expanduser().resolve(),
                Path(self.output_file).expanduser().resolve(),
            }:
                raise RevisionModelError(
                    "Working Clean View must be separate from both source and marked DOCX."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "role": self.role,
            "input_file": self.input_file,
            "output_file": self.output_file,
            "source_sha256": self.source_sha256,
            "output_sha256": self.output_sha256,
            "working_clean_file": self.working_clean_file,
            "working_clean_sha256": self.working_clean_sha256,
            "working_view_fingerprint": self.working_view_fingerprint,
        }


@dataclass(frozen=True)
class RevisionEvent:
    event_id: str
    subject_id: str
    subject_type: str
    event_type: str
    timestamp_utc: str
    actor: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("event_id", "subject_id", "subject_type", "event_type", "actor"):
            _text(getattr(self, name), name)
        if self.subject_type not in {"patch", "response_unit"}:
            raise RevisionModelError("RevisionEvent subject_type must be patch or response_unit.")
        _aware_timestamp(self.timestamp_utc, "timestamp_utc")
        if not isinstance(self.details, Mapping):
            raise RevisionModelError("RevisionEvent details must be an object.")

    @property
    def patch_id(self) -> str | None:
        """Compatibility accessor for patch-scoped events."""
        return self.subject_id if self.subject_type == "patch" else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
            "event_type": self.event_type,
            "timestamp_utc": self.timestamp_utc,
            "actor": self.actor,
            "details": dict(self.details),
        }

@dataclass(frozen=True)
class ResponseLocation:
    """A final output location backed by exact DOCX, PDF and mapping-file versions."""

    document: str
    output_file: str
    pdf_file: str
    page: int | None
    line_start: int | None
    line_end: int | None
    pdf_sha256: str
    location_map_id: str
    mapping_fingerprint: str
    output_sha256: str
    locator_id: str
    source_clean_sha256: str
    mapping_method: str = "word_com"
    locator_text: str | None = None

    def __post_init__(self) -> None:
        _text(self.document, "document")
        output_path = Path(_safe_docx_path(self.output_file, "output_file")).expanduser().resolve()
        expected_output_sha256 = _sha256(self.output_sha256, "output_sha256")
        if not output_path.is_file():
            raise RevisionModelError(f"Final mapped DOCX does not exist: {output_path}")
        try:
            actual_output_sha256 = sha256_file(output_path)
        except OSError as exc:
            raise RevisionModelError(f"Cannot hash final mapped DOCX {output_path}: {exc}") from exc
        if actual_output_sha256 != expected_output_sha256:
            raise RevisionModelError(f"Final mapped DOCX changed after line mapping: {output_path}")
        pdf_path = Path(_text(self.pdf_file, "pdf_file")).expanduser().resolve()
        if pdf_path.suffix.casefold() != ".pdf":
            raise RevisionModelError("pdf_file must point to a .pdf file.")
        if not pdf_path.is_file():
            raise RevisionModelError(f"Final mapped PDF does not exist: {pdf_path}")
        if self.mapping_method not in {"word_com", "manual"}:
            raise RevisionModelError("mapping_method must be word_com or manual.")
        if self.mapping_method == "word_com":
            for name in ("page", "line_start", "line_end"):
                value = getattr(self, name)
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    raise RevisionModelError(f"{name} must be a positive integer for Word COM locations.")
            if self.line_end < self.line_start:
                raise RevisionModelError("line_end cannot be earlier than line_start.")
            if self.locator_text is not None:
                _text(self.locator_text, "locator_text")
        else:
            _text(self.locator_text, "locator_text")
            if self.page is not None and (
                not isinstance(self.page, int) or isinstance(self.page, bool) or self.page < 1
            ):
                raise RevisionModelError("Manual location page must be a positive integer or null.")
            if self.line_start is not None or self.line_end is not None:
                if self.page is None:
                    raise RevisionModelError("Manual line numbers require a positive page number.")
                for name in ("line_start", "line_end"):
                    value = getattr(self, name)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                        raise RevisionModelError(f"Manual {name} must be positive when supplied.")
                if self.line_end < self.line_start:
                    raise RevisionModelError("Manual line_end cannot be earlier than line_start.")
        expected_pdf_sha256 = _sha256(self.pdf_sha256, "pdf_sha256")
        try:
            actual_pdf_sha256 = sha256_file(pdf_path)
        except OSError as exc:
            raise RevisionModelError(f"Cannot hash final mapped PDF {pdf_path}: {exc}") from exc
        if actual_pdf_sha256 != expected_pdf_sha256:
            raise RevisionModelError(f"Final mapped PDF changed after line mapping: {pdf_path}")
        _text(self.location_map_id, "location_map_id")
        _sha256(self.mapping_fingerprint, "mapping_fingerprint")
        _text(self.locator_id, "locator_id")
        _sha256(self.source_clean_sha256, "source_clean_sha256")

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "output_file": self.output_file,
            "pdf_file": self.pdf_file,
            "page": self.page,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "pdf_sha256": self.pdf_sha256,
            "location_map_id": self.location_map_id,
            "mapping_fingerprint": self.mapping_fingerprint,
            "output_sha256": self.output_sha256,
            "locator_id": self.locator_id,
            "source_clean_sha256": self.source_clean_sha256,
            "mapping_method": self.mapping_method,
            "locator_text": self.locator_text,
        }

    @property
    def display_text(self) -> str:
        if self.mapping_method == "manual":
            return (
                f"Page {self.page}, {self.locator_text}"
                if self.page is not None else str(self.locator_text)
            )
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
    linked_operation_ids: list[str] = field(default_factory=list)
    disposition: str = "revised"
    location_requirement: str = "required"
    resolution_reason: str = ""
    anchor_evidence_id: str | None = None
    anchor_document: str | None = None
    anchor_part: str | None = None
    anchor_p_idx: int | None = None
    anchor_para_id: str | None = None
    anchor_source_excerpt: str | None = None
    location_phrase: str | None = None
    status: str = "draft"
    semantic_review_confirmed: bool = False
    verification_report_id: str | None = None
    location_map_fingerprint: str | None = None
    location_map_file: str | None = None
    location_map_file_sha256: str | None = None
    final_locations: list[ResponseLocation] = field(default_factory=list)
    completed_at_utc: str | None = None

    def __post_init__(self) -> None:
        for name in ("response_unit_id", "reviewer", "comment_id", "comment_text"):
            _text(getattr(self, name), name)
        _text(self.response_text, "response_text", allow_empty=True)
        if self.disposition not in {"revised", "reply_only", "no_change", "clarification_needed"}:
            raise RevisionModelError(f"Unsupported ResponseUnit disposition: {self.disposition!r}.")
        if self.location_requirement not in {"required", "existing_anchor_required", "not_required", "manual_required"}:
            raise RevisionModelError(f"Unsupported location_requirement: {self.location_requirement!r}.")
        _text(self.resolution_reason, "resolution_reason", allow_empty=True)
        if self.location_phrase is not None:
            _text(self.location_phrase, "location_phrase")
            if len(self.location_phrase) > 255 or any(ch in self.location_phrase for ch in "\r\n\t"):
                raise RevisionModelError("ResponseUnit location_phrase must be a single-line phrase of at most 255 characters.")
        if self.disposition == "reply_only" and self.location_requirement != "not_required":
            raise RevisionModelError("reply_only ResponseUnits must use location_requirement=not_required.")
        if self.disposition != "no_change" and self.location_requirement == "existing_anchor_required":
            raise RevisionModelError("existing_anchor_required is reserved for no_change ResponseUnits.")
        if self.disposition not in {"revised", "no_change"} and self.location_requirement == "manual_required":
            raise RevisionModelError("manual_required is reserved for revised or anchored no_change ResponseUnits.")
        if self.disposition in {"reply_only", "no_change", "clarification_needed"} and (self.linked_patch_ids or self.linked_operation_ids):
            raise RevisionModelError(
                f"{self.disposition} ResponseUnits cannot claim Patch or PatchOperation links; "
                "only disposition=revised may carry executable operations."
            )
        if self.disposition == "no_change":
            if self.location_requirement not in {"existing_anchor_required", "manual_required"}:
                raise RevisionModelError("no_change ResponseUnits must use existing_anchor_required or manual_required.")
            if not all((self.anchor_evidence_id, self.anchor_document, self.anchor_part, self.anchor_source_excerpt)) or self.anchor_p_idx is None:
                raise RevisionModelError("no_change ResponseUnits require a confirmed evidence ID, document, Word part, paragraph index and source excerpt.")
            _text(self.anchor_source_excerpt, "anchor_source_excerpt")
        if self.disposition == "clarification_needed" and self.status != "blocked":
            raise RevisionModelError("clarification_needed ResponseUnits must remain blocked until resolved.")
        if self.disposition == "revised" and self.location_requirement == "not_required":
            raise RevisionModelError("revised ResponseUnits cannot bypass final location mapping.")
        if self.anchor_p_idx is not None and (not isinstance(self.anchor_p_idx, int) or isinstance(self.anchor_p_idx, bool) or self.anchor_p_idx < 0):
            raise RevisionModelError("anchor_p_idx must be a non-negative integer or null.")
        if self.status not in _RESPONSE_STATES:
            raise RevisionModelError(f"Unsupported ResponseUnit status: {self.status!r}.")
        for field_name in ("linked_task_ids", "linked_patch_ids", "linked_operation_ids"):
            values = getattr(self, field_name)
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise RevisionModelError(f"{field_name} must contain non-empty strings.")
            if len(values) != len(set(values)):
                raise RevisionModelError(f"{field_name} must be unique.")
        if self.semantic_review_confirmed and not (
            isinstance(self.verification_report_id, str) and self.verification_report_id.strip()
        ):
            raise RevisionModelError("Semantic confirmation requires verification_report_id.")
        if self.status in {"verified", "location_mapped", "completed"} and not self.semantic_review_confirmed:
            raise RevisionModelError(f"ResponseUnit in {self.status} state requires semantic review.")
        if self.status in {"verified", "location_mapped", "completed"} and not self.response_text.strip():
            raise RevisionModelError(f"ResponseUnit in {self.status} state requires response_text.")
        if self.location_map_fingerprint is not None:
            _sha256(self.location_map_fingerprint, "location_map_fingerprint")
        if self.location_map_file_sha256 is not None:
            _sha256(self.location_map_file_sha256, "location_map_file_sha256")
        if self.status == "location_mapped" and self.location_requirement == "not_required":
            raise RevisionModelError("A not_required ResponseUnit cannot carry a Word/PDF location state.")
        if self.status in {"location_mapped", "completed"} and self.location_requirement == "manual_required":
            if not self.final_locations or any(location.mapping_method != "manual" for location in self.final_locations):
                raise RevisionModelError("manual_required ResponseUnits need explicit manual locator records.")
        if self.status in {"location_mapped", "completed"} and self.location_requirement != "not_required":
            if not self.semantic_review_confirmed:
                raise RevisionModelError("Location-mapped ResponseUnit requires semantic review.")
            if not self.final_locations:
                raise RevisionModelError("Location-mapped ResponseUnit requires final PDF line locations.")
            if not self.location_map_fingerprint:
                raise RevisionModelError("Location-mapped ResponseUnit requires location_map_fingerprint.")
            if not self.location_map_file or not self.location_map_file_sha256:
                raise RevisionModelError(
                    "Location-mapped ResponseUnit requires the source location-map file and SHA-256."
                )
            map_path = Path(self.location_map_file).expanduser().resolve()
            if not map_path.is_file():
                raise RevisionModelError(f"Location map file does not exist: {map_path}")
            try:
                actual_map_hash = sha256_file(map_path)
            except OSError as exc:
                raise RevisionModelError(f"Cannot hash location map file {map_path}: {exc}") from exc
            if actual_map_hash != self.location_map_file_sha256:
                raise RevisionModelError(f"Location map file changed after mapping: {map_path}")
        if self.status == "completed" and self.location_requirement == "not_required":
            if self.disposition != "reply_only" or not self.semantic_review_confirmed:
                raise RevisionModelError("Only a semantically reviewed reply_only ResponseUnit can complete without location evidence.")
            if self.final_locations or self.location_map_file or self.location_map_fingerprint or self.location_map_file_sha256:
                raise RevisionModelError("not_required completed ResponseUnits cannot carry fabricated location evidence.")
        if self.status == "completed":
            if not self.completed_at_utc:
                raise RevisionModelError("Completed ResponseUnit requires completed_at_utc.")
            _aware_timestamp(self.completed_at_utc, "completed_at_utc")

    def _expected_location_locator_ids(self) -> set[str]:
        """Return the exact locator IDs required by this ResponseUnit's disposition."""
        if self.disposition == "revised":
            prefix = "manual:" if self.location_requirement == "manual_required" else ""
            return {f"{prefix}{operation_id}" for operation_id in self.linked_operation_ids}
        if self.disposition == "no_change" and self.anchor_evidence_id:
            prefix = "manual:anchor:" if self.location_requirement == "manual_required" else "anchor:"
            return {f"{prefix}{self.anchor_evidence_id}"}
        return set()

    def validate_location_evidence(self) -> list[str]:
        if self.location_requirement == "not_required":
            return []
        if self.status not in {"location_mapped", "completed"}:
            return []
        errors: list[str] = []
        if not self.location_map_file or not self.location_map_file_sha256:
            return ["Mapped ResponseUnit is missing its source location-map artifact."]
        map_path = Path(self.location_map_file).expanduser().resolve()
        if not map_path.is_file():
            errors.append(f"Location map file no longer exists: {map_path}")
        else:
            try:
                if sha256_file(map_path) != self.location_map_file_sha256:
                    errors.append(f"Location map file changed after mapping: {map_path}")
            except OSError as exc:
                errors.append(f"Cannot recheck location map file {map_path}: {exc}")
        if not self.final_locations:
            errors.append("Mapped ResponseUnit has no final Word/PDF location records.")
        expected_locator_ids = self._expected_location_locator_ids()
        actual_locator_ids = [location.locator_id for location in self.final_locations]
        if not expected_locator_ids:
            errors.append("Mapped ResponseUnit has no exact linked PatchOperation or existing-anchor locator scope.")
        elif (
            len(actual_locator_ids) != len(expected_locator_ids)
            or set(actual_locator_ids) != expected_locator_ids
        ):
            errors.append(
                "Mapped ResponseUnit must contain exactly one final locator for every linked PatchOperation or existing anchor."
            )
        for location in self.final_locations:
            expected_method = "manual" if self.location_requirement == "manual_required" else "word_com"
            if location.mapping_method != expected_method:
                errors.append(
                    f"ResponseUnit {self.response_unit_id} requires {expected_method} locator records."
                )
            pdf_path = Path(location.pdf_file).expanduser().resolve()
            if not pdf_path.is_file():
                errors.append(f"Final mapped PDF no longer exists: {pdf_path}")
                continue
            try:
                if sha256_file(pdf_path) != location.pdf_sha256:
                    errors.append(f"Final mapped PDF changed after line mapping: {pdf_path}")
            except OSError as exc:
                errors.append(f"Cannot recheck final mapped PDF {pdf_path}: {exc}")
            output_path = Path(location.output_file).expanduser().resolve()
            if not output_path.is_file():
                errors.append(f"Final mapped DOCX no longer exists: {output_path}")
            else:
                try:
                    if sha256_file(output_path) != location.output_sha256:
                        errors.append(f"Final mapped DOCX changed after line mapping: {output_path}")
                except OSError as exc:
                    errors.append(f"Cannot recheck final mapped DOCX {output_path}: {exc}")
            if location.mapping_fingerprint != self.location_map_fingerprint:
                errors.append(
                    f"ResponseUnit {self.response_unit_id} contains a location from another map."
                )
        return errors

    @property
    def final_location_fingerprint(self) -> str:
        return _fingerprint([item.to_dict() for item in self.final_locations])

    def confirm_semantic_review(self, verification_report_id: str) -> None:
        if self.disposition == "clarification_needed":
            raise RevisionModelError("clarification_needed ResponseUnits cannot be confirmed before clarification is resolved.")
        if self.status not in {"draft", "verified"}:
            raise RevisionModelError(
                f"ResponseUnit {self.response_unit_id} cannot be semantically confirmed from {self.status}."
            )
        _text(self.response_text, "response_text")
        self.verification_report_id = _text(
            verification_report_id, "verification_report_id"
        )
        self.semantic_review_confirmed = True
        self.status = "verified"

    def record_final_locations(
        self,
        location_map_fingerprint: str,
        final_locations: Sequence[ResponseLocation],
        *,
        location_map_file: str,
        location_map_file_sha256: str,
    ) -> None:
        if self.location_requirement == "not_required":
            raise RevisionModelError("This ResponseUnit is explicitly not required to have a location.")
        if self.status not in {"verified", "location_mapped", "completed"} or not self.semantic_review_confirmed:
            raise RevisionModelError(
                "ResponseUnit must pass semantic review before final location mapping."
            )
        _sha256(location_map_fingerprint, "location_map_fingerprint")
        if not final_locations:
            raise RevisionModelError("At least one final Word/PDF location record is required.")
        if any(not isinstance(item, ResponseLocation) for item in final_locations):
            raise RevisionModelError("final_locations must contain ResponseLocation records.")
        expected_method = "manual" if self.location_requirement == "manual_required" else "word_com"
        if any(item.mapping_method != expected_method for item in final_locations):
            raise RevisionModelError(
                f"{self.location_requirement} ResponseUnit requires mapping_method={expected_method}."
            )
        expected_locator_ids = self._expected_location_locator_ids()
        if not expected_locator_ids:
            raise RevisionModelError(
                f"ResponseUnit {self.response_unit_id} has no exact linked PatchOperation or existing-anchor locator scope."
            )
        actual_locator_ids = [item.locator_id for item in final_locations]
        if (
            len(actual_locator_ids) != len(expected_locator_ids)
            or set(actual_locator_ids) != expected_locator_ids
        ):
            raise RevisionModelError(
                "Final location records must cover every linked PatchOperation or existing anchor exactly once; "
                "expected locator IDs: " + ", ".join(sorted(expected_locator_ids))
            )
        if any(
            item.mapping_fingerprint != location_map_fingerprint
            for item in final_locations
        ):
            raise RevisionModelError(
                "Every final location must belong to the supplied location-map fingerprint."
            )
        map_path = Path(_text(location_map_file, "location_map_file")).expanduser().resolve()
        expected_map_hash = _sha256(
            location_map_file_sha256, "location_map_file_sha256"
        )
        if not map_path.is_file():
            raise RevisionModelError(f"Location map file does not exist: {map_path}")
        try:
            actual_map_hash = sha256_file(map_path)
        except OSError as exc:
            raise RevisionModelError(f"Cannot hash location map file {map_path}: {exc}") from exc
        if actual_map_hash != expected_map_hash:
            raise RevisionModelError(f"Location map file changed after mapping: {map_path}")
        self.location_map_fingerprint = location_map_fingerprint
        self.location_map_file = str(map_path)
        self.location_map_file_sha256 = expected_map_hash
        self.final_locations = list(final_locations)
        self.completed_at_utc = None
        self.status = "location_mapped"

    def mark_completed(self, completed_at_utc: str) -> None:
        if self.disposition == "clarification_needed":
            raise RevisionModelError("clarification_needed ResponseUnits cannot be completed.")
        if self.location_requirement == "not_required":
            if self.disposition != "reply_only" or self.status != "verified" or not self.semantic_review_confirmed:
                raise RevisionModelError("Only a semantically verified reply_only ResponseUnit may complete without location evidence.")
            if self.final_locations or self.location_map_file or self.location_map_fingerprint:
                raise RevisionModelError("not_required ResponseUnits cannot include page/line locations.")
        elif self.status != "location_mapped":
            raise RevisionModelError("Location-required ResponseUnits can only complete after final PDF locations are mapped.")
        _aware_timestamp(completed_at_utc, "completed_at_utc")
        self.completed_at_utc = completed_at_utc
        self.status = "completed"

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
            "linked_operation_ids": list(self.linked_operation_ids),
            "disposition": self.disposition,
            "location_requirement": self.location_requirement,
            "resolution_reason": self.resolution_reason,
            "anchor_evidence_id": self.anchor_evidence_id,
            "anchor_document": self.anchor_document,
            "anchor_part": self.anchor_part,
            "anchor_p_idx": self.anchor_p_idx,
            "anchor_para_id": self.anchor_para_id,
            "anchor_source_excerpt": self.anchor_source_excerpt,
            "location_phrase": self.location_phrase,
            "status": self.status,
            "semantic_review_confirmed": self.semantic_review_confirmed,
            "verification_report_id": self.verification_report_id,
            "location_map_fingerprint": self.location_map_fingerprint,
            "location_map_file": self.location_map_file,
            "location_map_file_sha256": self.location_map_file_sha256,
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
    workflow_task_fingerprints: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _text(self.revision_round_id, "revision_round_id")
        if self.schema_version != "0.5.1":
            raise RevisionModelError("RevisionLedger schema_version must be '0.5.1'.")
        _sha256(self.workflow_ledger_fingerprint, "workflow_ledger_fingerprint", optional=True)
        if not isinstance(self.workflow_task_fingerprints, dict):
            raise RevisionModelError("workflow_task_fingerprints must be an object.")
        for task_id, fingerprint in self.workflow_task_fingerprints.items():
            _text(task_id, "workflow_task_fingerprints task ID")
            _sha256(fingerprint, f"workflow_task_fingerprints[{task_id}]")
        self.validate_or_raise()

    def validate(self) -> list[str]:
        errors: list[str] = []
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            errors.append("RevisionLedger contains duplicate document_id values.")
        for document in self.documents:
            if not isinstance(document, RevisionDocument):
                errors.append("RevisionLedger documents must be RevisionDocument records.")
                continue
            if document.working_clean_file:
                working_path = Path(document.working_clean_file).expanduser().resolve()
                marked_path = Path(document.output_file).expanduser().resolve()
                if not marked_path.is_file():
                    errors.append(
                        f"Marked DOCX no longer exists for Working Clean View {document.role}: {marked_path}"
                    )
                if not working_path.is_file():
                    errors.append(
                        f"Working Clean View no longer exists for {document.role}: {working_path}"
                    )
                else:
                    try:
                        if marked_path.is_file():
                            actual_marked_hash = sha256_file(marked_path)
                            if actual_marked_hash != document.output_sha256:
                                errors.append(
                                    f"Marked DOCX changed after Working Clean View generation: {marked_path}"
                                )
                        actual_hash = sha256_file(working_path)
                        if actual_hash != document.working_clean_sha256:
                            errors.append(
                                f"Working Clean View changed after generation: {working_path}"
                            )
                        expected_view_fingerprint = _fingerprint({
                            "source_sha256": document.output_sha256,
                            "working_clean_sha256": document.working_clean_sha256,
                            "policy": "accept-insertions-reject-deletions-v1",
                        })
                        if document.working_view_fingerprint != expected_view_fingerprint:
                            errors.append(
                                f"Working Clean View fingerprint is stale for {working_path}."
                            )
                    except OSError as exc:
                        errors.append(
                            f"Cannot recheck Working Clean View {working_path}: {exc}"
                        )

        patch_ids = [item.patch_id for item in self.patches]
        if len(patch_ids) != len(set(patch_ids)):
            errors.append("RevisionLedger contains duplicate patch_id values.")
        all_operation_ids = [operation.operation_id for patch in self.patches for operation in patch.operations]
        if len(all_operation_ids) != len(set(all_operation_ids)):
            errors.append("RevisionLedger contains duplicate PatchOperation operation_id values.")
        response_ids = [item.response_unit_id for item in self.response_units]
        if len(response_ids) != len(set(response_ids)):
            errors.append("RevisionLedger contains duplicate response_unit_id values.")
        known_patches = set(patch_ids)
        patch_by_id = {patch.patch_id: patch for patch in self.patches}
        successor_by_patch: dict[str, str] = {}
        for patch in self.patches:
            if patch.status in {"applied", "verified"} and not patch.apply_report_ids:
                errors.append(f"Patch {patch.patch_id} in {patch.status} state lacks apply_report_ids.")
            if patch.status in {"preflighted", "approved", "applied", "verified"}:
                if not _SHA256_RE.fullmatch(patch.preflight_fingerprint or ""):
                    errors.append(f"Patch {patch.patch_id} in {patch.status} state lacks a valid preflight fingerprint.")
                if not patch.preflight_report_id:
                    errors.append(f"Patch {patch.patch_id} in {patch.status} state lacks preflight_report_id.")
            if patch.status in {"approved", "applied", "verified"} and not _SHA256_RE.fullmatch(
                patch.approval_fingerprint or ""
            ):
                errors.append(f"Patch {patch.patch_id} in {patch.status} state lacks a valid approval fingerprint.")
            if patch.status == "verified":
                if not patch.verification_report_id:
                    errors.append(f"Verified patch {patch.patch_id} lacks verification_report_id.")
                if not patch.verified_output_hashes:
                    errors.append(f"Verified patch {patch.patch_id} lacks verified_output_hashes.")
                expected_outputs = {
                    _canonical_path(operation.output_file)
                    for operation in patch.operations
                }
                actual_outputs: set[str] = set()
                for entry in patch.verified_output_hashes:
                    if not isinstance(entry, Mapping):
                        errors.append(f"Verified Patch {patch.patch_id} contains a malformed output hash record.")
                        continue
                    path_value = entry.get("path")
                    digest = entry.get("sha256")
                    if not isinstance(path_value, str) or not path_value.strip():
                        errors.append(f"Verified Patch {patch.patch_id} contains an empty output path.")
                        continue
                    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                        errors.append(f"Verified Patch {patch.patch_id} contains an invalid output SHA-256.")
                        continue
                    actual_outputs.add(_canonical_path(path_value))
                if actual_outputs != expected_outputs or len(actual_outputs) != len(patch.verified_output_hashes):
                    errors.append(
                        f"Verified output hashes for Patch {patch.patch_id} must cover exactly its DOCX outputs."
                    )
            if patch.supersedes_patch_id and patch.supersedes_patch_id not in known_patches:
                errors.append(
                    f"Patch {patch.patch_id} supersedes unknown patch {patch.supersedes_patch_id}."
                )
            if patch.supersedes_patch_id == patch.patch_id:
                errors.append(f"Patch {patch.patch_id} cannot supersede itself.")
            if patch.supersedes_patch_id in patch_by_id:
                previous = patch_by_id[patch.supersedes_patch_id]
                if previous.task_id != patch.task_id:
                    errors.append(
                        f"Patch {patch.patch_id} cannot supersede a Patch from another task."
                    )
                if previous.status not in {"superseded", "applied", "verified", "abandoned"}:
                    errors.append(
                        f"Patch {patch.patch_id} has supersession lineage but predecessor "
                        f"{previous.patch_id} is still {previous.status}."
                    )
                if previous.patch_id in successor_by_patch:
                    errors.append(
                        f"Patch {previous.patch_id} has more than one superseding Patch."
                    )
                successor_by_patch[previous.patch_id] = patch.patch_id

        for patch in self.patches:
            if patch.status == "superseded" and patch.patch_id not in successor_by_patch:
                errors.append(
                    f"Superseded Patch {patch.patch_id} has no recorded replacement."
                )

        # Supersession is a lineage graph; reject cycles before it can be used
        # to select the current Patch for a task.
        for patch in self.patches:
            seen_lineage: set[str] = set()
            current = patch
            while current.supersedes_patch_id:
                if current.patch_id in seen_lineage:
                    errors.append(
                        f"Patch supersession cycle detected at {current.patch_id}."
                    )
                    break
                seen_lineage.add(current.patch_id)
                predecessor = patch_by_id.get(current.supersedes_patch_id)
                if predecessor is None:
                    break
                current = predecessor
        operation_owners = {
            operation.operation_id: (patch.patch_id, operation)
            for patch in self.patches
            for operation in patch.operations
        }
        documents_by_role = {document.role: document for document in self.documents}
        roles_with_patch_operations = {
            operation.document
            for patch in self.patches
            for operation in patch.operations
        }
        for document in self.documents:
            input_path = Path(document.input_file).expanduser().resolve()
            output_path = Path(document.output_file).expanduser().resolve()
            if input_path == output_path:
                if document.role in roles_with_patch_operations:
                    errors.append(
                        f"RevisionDocument {document.role} uses an unchanged-source passthrough "
                        "path but the role also has PatchOperations."
                    )
                    continue
                if not document.source_sha256:
                    errors.append(
                        f"Unchanged-source RevisionDocument {document.role} requires source_sha256."
                    )
                    continue
                if not output_path.is_file():
                    errors.append(
                        f"Unchanged-source RevisionDocument {document.role} is missing: {output_path}."
                    )
                    continue
                try:
                    actual_source_hash = sha256_file(output_path)
                    if actual_source_hash != document.source_sha256:
                        errors.append(
                            f"Unchanged-source RevisionDocument {document.role} no longer matches "
                            "its registered source SHA-256."
                        )
                    if document.output_sha256 and actual_source_hash != document.output_sha256:
                        errors.append(
                            f"Unchanged-source RevisionDocument {document.role} has a stale output SHA-256."
                        )
                except OSError as exc:
                    errors.append(
                        f"Cannot hash unchanged-source RevisionDocument {document.role}: {exc}"
                    )
        role_location_versions: dict[str, set[tuple[str, str, str, str]]] = {}
        for unit in self.response_units:
            for operation_id in unit.linked_operation_ids:
                owner = operation_owners.get(operation_id)
                if owner is None:
                    errors.append(f"ResponseUnit {unit.response_unit_id} links unknown PatchOperation {operation_id}.")
                    continue
                patch_id, operation = owner
                if unit.comment_id not in operation.source_comment_ids:
                    errors.append(f"ResponseUnit {unit.response_unit_id} is not declared by PatchOperation {operation_id}.")
                if patch_id not in unit.linked_patch_ids:
                    errors.append(f"ResponseUnit {unit.response_unit_id} links operation {operation_id} without its parent Patch {patch_id}.")
            if unit.disposition == "revised" and (unit.linked_patch_ids or unit.status in {"verified", "location_mapped", "completed"}) and not unit.linked_operation_ids:
                errors.append(f"ResponseUnit {unit.response_unit_id} is marked revised but has no exact linked PatchOperation IDs.")
            if unit.disposition in {"reply_only", "no_change", "clarification_needed"} and (unit.linked_patch_ids or unit.linked_operation_ids):
                errors.append(
                    f"ResponseUnit {unit.response_unit_id} has executable Patch links but its disposition is {unit.disposition}; "
                    "reclassify explicitly instead of silently skipping the operations."
                )
            errors.extend(
                f"ResponseUnit {unit.response_unit_id}: {error}"
                for error in unit.validate_location_evidence()
            )
            if unit.status in {"location_mapped", "completed"}:
                expected_locator_ids = set(unit.linked_operation_ids)
                if unit.disposition == "no_change" and unit.anchor_evidence_id:
                    expected_locator_ids = (
                        {f"manual:anchor:{unit.anchor_evidence_id}"}
                        if unit.location_requirement == "manual_required"
                        else {f"anchor:{unit.anchor_evidence_id}"}
                    )
                elif unit.location_requirement == "manual_required":
                    expected_locator_ids = {
                        f"manual:{operation_id}" for operation_id in unit.linked_operation_ids
                    }
                actual_locator_ids = {location.locator_id for location in unit.final_locations if location.locator_id}
                if expected_locator_ids and expected_locator_ids - actual_locator_ids:
                    errors.append(
                        f"ResponseUnit {unit.response_unit_id} is missing mapped locator(s): "
                        + ", ".join(sorted(expected_locator_ids - actual_locator_ids))
                    )
                for location in unit.final_locations:
                    current_document = documents_by_role.get(location.document)
                    if current_document is None:
                        errors.append(f"ResponseUnit {unit.response_unit_id} maps unknown document role {location.document}.")
                    elif location.source_clean_sha256 != current_document.working_clean_sha256:
                        errors.append(
                            f"ResponseUnit {unit.response_unit_id} has stale page/line positions for {location.document}; "
                            "the current Working Clean hash changed and that whole document role must be remapped."
                        )
                    role_location_versions.setdefault(location.document, set()).add((
                        _canonical_path(location.output_file),
                        location.output_sha256,
                        _canonical_path(location.pdf_file),
                        location.pdf_sha256,
                    ))
                    if unit.disposition == "no_change":
                        if location.document != unit.anchor_document:
                            errors.append(
                                f"ResponseUnit {unit.response_unit_id} existing-anchor location is not in "
                                f"its confirmed document role {unit.anchor_document}."
                            )
                    else:
                        operation_id = (
                            location.locator_id[len("manual:"):]
                            if unit.location_requirement == "manual_required"
                            and location.locator_id.startswith("manual:")
                            else location.locator_id
                        )
                        owner = operation_owners.get(operation_id)
                        if owner is not None and location.document != owner[1].document:
                            errors.append(
                                f"ResponseUnit {unit.response_unit_id} location for PatchOperation "
                                f"{operation_id} is assigned to {location.document}, not {owner[1].document}."
                            )
            unknown = set(unit.linked_patch_ids) - known_patches
            if unknown:
                errors.append(
                    f"ResponseUnit {unit.response_unit_id} links unknown patch(es): "
                    + ", ".join(sorted(unknown))
                )
        for role, versions in role_location_versions.items():
            if len(versions) > 1:
                errors.append(
                    f"Final page/line locations for document role {role} do not share one final DOCX/PDF hash pair; "
                    "all ResponseUnits in a changed role must be remapped together."
                )
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            errors.append("RevisionLedger contains duplicate event_id values.")
        superseded_events_by_patch: dict[str, list[RevisionEvent]] = {}
        replacement_events_by_patch: dict[str, list[RevisionEvent]] = {}
        for event in self.events:
            if event.subject_type != "patch":
                continue
            if event.event_type == "patch_superseded":
                superseded_events_by_patch.setdefault(event.subject_id, []).append(event)
            elif event.event_type == "patch_created_as_replacement":
                replacement_events_by_patch.setdefault(event.subject_id, []).append(event)

        allowed_superseded_predecessor_states = {
            "planned", "preflighted", "approved", "applied", "verified", "abandoned"
        }
        for predecessor_id, replacement_id in successor_by_patch.items():
            predecessor = patch_by_id[predecessor_id]
            predecessor_events = superseded_events_by_patch.get(predecessor_id, [])
            replacement_events = replacement_events_by_patch.get(replacement_id, [])
            if predecessor.status != "superseded":
                errors.append(
                    f"Patch supersession event lineage requires predecessor {predecessor_id} "
                    "to remain in superseded state."
                )
            if len(predecessor_events) != 1 or len(replacement_events) != 1:
                errors.append(
                    f"Patch supersession event lineage for {predecessor_id} -> {replacement_id} "
                    "must contain exactly one predecessor and one replacement event."
                )
                continue
            predecessor_event = predecessor_events[0]
            replacement_event = replacement_events[0]
            previous_status = predecessor_event.details.get("previous_status")
            reason = predecessor_event.details.get("reason")
            if (
                predecessor_event.details.get("replacement_patch_id") != replacement_id
                or replacement_event.details.get("supersedes_patch_id") != predecessor_id
                or previous_status not in allowed_superseded_predecessor_states
                or not isinstance(reason, str)
                or not reason.strip()
                or replacement_event.details.get("reason") != reason
                or predecessor_event.timestamp_utc != replacement_event.timestamp_utc
                or predecessor_event.actor != replacement_event.actor
                or predecessor_event.subject_type != "patch"
                or replacement_event.subject_type != "patch"
            ):
                errors.append(
                    f"Patch supersession event lineage for {predecessor_id} -> {replacement_id} "
                    "does not match the immutable Patch relationship."
                )

        for predecessor_id, event_rows in superseded_events_by_patch.items():
            expected_replacement_id = successor_by_patch.get(predecessor_id)
            if (
                len(event_rows) != 1
                or expected_replacement_id is None
                or event_rows[0].details.get("replacement_patch_id") != expected_replacement_id
            ):
                errors.append(
                    f"Patch supersession event lineage contains an orphan or duplicate predecessor event for {predecessor_id}."
                )
        for replacement_id, event_rows in replacement_events_by_patch.items():
            replacement = patch_by_id.get(replacement_id)
            if (
                len(event_rows) != 1
                or replacement is None
                or not replacement.supersedes_patch_id
                or event_rows[0].details.get("supersedes_patch_id") != replacement.supersedes_patch_id
            ):
                errors.append(
                    f"Patch supersession event lineage contains an orphan or duplicate replacement event for {replacement_id}."
                )

        recovery_events_by_patch: dict[str, list[RevisionEvent]] = {}
        for event in self.events:
            if (
                event.subject_type == "patch"
                and event.event_type == "patch_abandoned_after_batch_recovery"
            ):
                recovery_events_by_patch.setdefault(event.subject_id, []).append(event)
        for patch in self.patches:
            if patch.status == "abandoned":
                recovery_events = recovery_events_by_patch.get(patch.patch_id, [])
                if len(recovery_events) != 1:
                    errors.append(
                        f"Abandoned Patch {patch.patch_id} must have exactly one failed-batch recovery event."
                    )
                elif (
                    not recovery_events[0].details.get("recovery_id")
                    or not recovery_events[0].details.get("batch_id")
                ):
                    errors.append(
                        f"Abandoned Patch {patch.patch_id} has an incomplete failed-batch recovery event."
                    )
        known_response_units = set(response_ids)
        for event in self.events:
            if event.subject_type == "patch" and event.subject_id not in known_patches:
                errors.append(f"RevisionEvent {event.event_id} refers to unknown patch.")
            elif event.subject_type == "response_unit" and event.subject_id not in known_response_units:
                errors.append(f"RevisionEvent {event.event_id} refers to unknown ResponseUnit.")
        return errors

    def confirm_response_units(
        self,
        response_unit_ids: Sequence[str],
        *,
        verification_report_id: str,
        actor: str,
    ) -> None:
        requested = list(dict.fromkeys(response_unit_ids))
        if not requested:
            raise RevisionModelError("At least one ResponseUnit ID must be supplied.")
        by_id = {unit.response_unit_id: unit for unit in self.response_units}
        unknown = [item for item in requested if item not in by_id]
        if unknown:
            raise RevisionModelError("Unknown ResponseUnit ID(s): " + ", ".join(unknown))
        _text(actor, "actor")
        _text(verification_report_id, "verification_report_id")
        now = datetime.now(timezone.utc).isoformat()
        for unit_id in requested:
            unit = by_id[unit_id]
            unit.confirm_semantic_review(verification_report_id)
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=unit_id,
                subject_type="response_unit",
                event_type="response_semantic_review",
                timestamp_utc=now,
                actor=actor,
                details={"verification_report_id": verification_report_id},
            ))
        self.validate_or_raise()

    def record_response_locations(
        self,
        location_map: Mapping[str, Any],
        *,
        actor: str = "Co_Reviewer Location Mapper",
    ) -> None:
        if not isinstance(location_map, Mapping):
            raise RevisionModelError("Location map must be an object.")
        location_map_file = _text(
            location_map.get("location_map_file"), "location_map_file"
        )
        location_map_file_sha256 = _sha256(
            location_map.get("location_map_file_sha256"),
            "location_map_file_sha256",
        )
        map_path = Path(location_map_file).expanduser().resolve()
        if not map_path.is_file():
            raise RevisionModelError(f"Location map file does not exist: {map_path}")
        try:
            actual_map_sha256 = sha256_file(map_path)
        except OSError as exc:
            raise RevisionModelError(f"Cannot hash location map file {map_path}: {exc}") from exc
        if actual_map_sha256 != location_map_file_sha256:
            raise RevisionModelError(
                f"Location map file changed after it was read: {map_path}"
            )
        mappings = location_map.get("response_units")
        if not isinstance(mappings, list):
            raise RevisionModelError("Location map must contain a response_units array.")
        by_id = {unit.response_unit_id: unit for unit in self.response_units}
        now = datetime.now(timezone.utc).isoformat()
        for entry in mappings:
            if not isinstance(entry, Mapping):
                raise RevisionModelError("Location map entries must be objects.")
            unit_id = _text(entry.get("response_unit_id"), "response_unit_id")
            unit = by_id.get(unit_id)
            if unit is None:
                raise RevisionModelError(f"Unknown ResponseUnit in location map: {unit_id}.")
            location_fingerprint = _sha256(
                entry.get("location_map_fingerprint"), "location_map_fingerprint"
            )
            raw_locations = entry.get("final_locations")
            if not isinstance(raw_locations, list):
                raise RevisionModelError(f"{unit_id} final_locations must be an array.")
            final_locations = [
                ResponseLocation(
                    document=row.get("document"),
                    output_file=row.get("output_file"),
                    pdf_file=row.get("pdf_file"),
                    page=row.get("page"),
                    line_start=row.get("line_start"),
                    line_end=row.get("line_end"),
                    pdf_sha256=row.get("pdf_sha256"),
                    location_map_id=row.get("location_map_id"),
                    mapping_fingerprint=row.get("mapping_fingerprint"),
                    output_sha256=row.get("output_sha256"),
                    locator_id=row.get("locator_id"),
                    source_clean_sha256=row.get("source_clean_sha256"),
                    mapping_method=row.get("mapping_method", "word_com"),
                    locator_text=row.get("locator_text"),
                )
                for row in raw_locations if isinstance(row, Mapping)
            ]
            if len(final_locations) != len(raw_locations):
                raise RevisionModelError(f"{unit_id} contains malformed final location entries.")
            unit.record_final_locations(
                location_fingerprint,
                final_locations,
                location_map_file=str(map_path),
                location_map_file_sha256=location_map_file_sha256,
            )
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=unit_id,
                subject_type="response_unit",
                event_type="final_location_mapped",
                timestamp_utc=now,
                actor=actor,
                details={
                    "location_map_id": location_map.get("location_map_id", ""),
                    "location_map_file": str(map_path),
                    "location_map_file_sha256": location_map_file_sha256,
                    "location_map_fingerprint": location_fingerprint,
                    "final_locations": [item.to_dict() for item in final_locations],
                },
            ))
        self.validate_or_raise()

    def invalidate_response_units_for_tasks(
        self,
        task_ids: Sequence[str],
        *,
        actor: str,
        reason: str,
    ) -> list[str]:
        """Reopen linked ResponseUnits and clear stale semantic/location evidence."""
        selected = set(task_ids)
        if not selected:
            return []
        _text(actor, "actor")
        _text(reason, "reason")
        now = datetime.now(timezone.utc).isoformat()
        reopened: list[str] = []
        for unit in self.response_units:
            affected = selected.intersection(unit.linked_task_ids)
            if not affected:
                continue
            previous_status = unit.status
            unit.status = "draft"
            unit.semantic_review_confirmed = False
            unit.verification_report_id = None
            unit.location_map_fingerprint = None
            unit.location_map_file = None
            unit.location_map_file_sha256 = None
            unit.final_locations = []
            unit.completed_at_utc = None
            unit.linked_targets = []
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=unit.response_unit_id,
                subject_type="response_unit",
                event_type="response_reopened_after_workflow_invalidation",
                timestamp_utc=now,
                actor=actor,
                details={
                    "invalidated_task_ids": sorted(affected),
                    "previous_status": previous_status,
                    "reason": reason,
                },
            ))
            reopened.append(unit.response_unit_id)
        self.validate_or_raise()
        return reopened


    def complete_response_units(
        self,
        response_unit_ids: Sequence[str],
        *,
        ready_for_submission_confirmed: bool,
        actor: str,
    ) -> None:
        if ready_for_submission_confirmed is not True:
            raise RevisionModelError(
                "Completion requires explicit ready_for_submission_confirmed=true."
            )
        requested = list(dict.fromkeys(response_unit_ids))
        if not requested:
            raise RevisionModelError("At least one ResponseUnit ID must be supplied.")
        unit_by_id = {unit.response_unit_id: unit for unit in self.response_units}
        patch_by_id = {patch.patch_id: patch for patch in self.patches}
        unknown = [item for item in requested if item not in unit_by_id]
        if unknown:
            raise RevisionModelError("Unknown ResponseUnit ID(s): " + ", ".join(unknown))
        _text(actor, "actor")
        now = datetime.now(timezone.utc).isoformat()
        for unit_id in requested:
            unit = unit_by_id[unit_id]
            unverified = [
                patch_id for patch_id in unit.linked_patch_ids
                if patch_by_id.get(patch_id) is None
                or patch_by_id[patch_id].status != "verified"
            ]
            if unverified:
                raise RevisionModelError(
                    f"ResponseUnit {unit_id} still links unverified Patch(es): "
                    + ", ".join(unverified)
                )
            unit.mark_completed(now)
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=unit_id,
                subject_type="response_unit",
                event_type="response_completed",
                timestamp_utc=now,
                actor=actor,
                details={"ready_for_submission_confirmed": True},
            ))
        self.validate_or_raise()

    def assert_legacy_plan_matches(self, revision_plan: Mapping[str, Any]) -> None:
        """Reject plan/ledger drift before handing the plan to the DOCX writer."""
        if not isinstance(revision_plan, Mapping):
            raise RevisionModelError("Legacy revision plan must be an object.")
        rows = revision_plan.get("revision_items")
        if not isinstance(rows, list):
            raise RevisionModelError("Legacy revision plan must contain revision_items.")
        expected = {
            patch.patch_id: {
                "reviewer": patch.reviewer,
                "comment": patch.comment,
                "proposed_response": patch.proposed_response,
                "targets": [
                    operation.to_legacy_target()
                    for operation in patch.operations
                ],
            }
            for patch in self.patches
            if patch.status not in {"superseded", "blocked", "abandoned"}
        }
        actual = {
            item.get("id"): item
            for item in rows if isinstance(item, Mapping)
        }
        if len(actual) != len(rows):
            raise RevisionModelError(
                "Legacy revision plan contains duplicate or invalid Patch IDs."
            )
        unexpected = set(actual) - set(expected)
        if unexpected:
            raise RevisionModelError(
                "Legacy revision plan contains Patch IDs that are unknown, superseded, blocked "
                "or abandoned in the RevisionLedger: " + ", ".join(sorted(unexpected))
            )
        # A legacy plan is a single executable batch, not a serialization of
        # every active Patch in the round. Validate every item it contains while
        # allowing already-applied/verified Patches to remain only in the ledger.
        for patch_id, actual_item in actual.items():
            expected_item = expected.get(patch_id)
            if not isinstance(expected_item, Mapping) or not isinstance(actual_item, Mapping):
                raise RevisionModelError(
                    f"Legacy revision plan item {patch_id} is unknown, blocked, superseded, abandoned or invalid."
                )
            for field_name in ("reviewer", "comment", "proposed_response"):
                if actual_item.get(field_name, "") != expected_item[field_name]:
                    raise RevisionModelError(
                        f"Legacy revision plan {field_name} differs from Patch {patch_id}; "
                        "refusing to apply a drifted reviewer-facing scope."
                    )
            expected_targets = expected_item["targets"]
            actual_targets = actual_item.get("targets")
            if not isinstance(actual_targets, list) or len(actual_targets) != len(expected_targets):
                raise RevisionModelError(
                    f"Legacy revision plan target count differs for patch {patch_id}."
                )
            fields = (
                "document", "input_file", "output_file", "part", "p_idx", "find",
                "replace", "location", "operation_id", "source_comment_ids",
                "location_phrase", "authorized_change", "protected_spans",
            )
            for index, (actual_target, expected_target) in enumerate(zip(actual_targets, expected_targets)):
                if not isinstance(actual_target, Mapping) or any(
                    actual_target.get(field) != expected_target.get(field)
                    for field in fields
                ):
                    raise RevisionModelError(
                        f"Legacy revision plan target {index + 1} differs from Patch {patch_id}."
                    )

    def to_legacy_revision_plan(self) -> dict[str, Any]:
        """Bridge exact patches into the existing preflight/approve/apply CLI.

        This always emits pending items. The legacy CLI must re-preflight and
        record fresh human approval; a Workflow Ledger approval is not silently
        imported into the older execution gate.
        """
        items = [
            patch.to_legacy_revision_item()
            for patch in self.patches
            if patch.status == "planned"
        ]
        if not items:
            raise RevisionModelError("RevisionLedger has no executable patches.")
        return {
            "schema_version": "1.0",
            "approved": False,
            "revision_items": items,
        }

    def supersede_patch(
        self,
        previous_patch_id: str,
        replacement: Patch,
        *,
        actor: str,
        reason: str,
    ) -> None:
        """Supersede one Patch without erasing its execution history.

        For a Patch that has already been applied, the replacement must start
        from the previous Patch's output DOCX for every affected document.
        Linked ResponseUnits are reopened for semantic review and remapping.
        """
        previous_patch_id = _text(previous_patch_id, "previous_patch_id")
        _text(actor, "actor")
        reason = _text(reason, "reason")
        if not isinstance(replacement, Patch):
            raise RevisionModelError("replacement must be a Patch.")
        by_id = {patch.patch_id: patch for patch in self.patches}
        previous = by_id.get(previous_patch_id)
        if previous is None:
            raise RevisionModelError(f"Unknown Patch to supersede: {previous_patch_id}.")
        if replacement.patch_id in by_id:
            raise RevisionModelError(f"Replacement Patch ID already exists: {replacement.patch_id}.")
        if replacement.status != "planned":
            raise RevisionModelError("A replacement Patch must start in planned state.")
        if replacement.supersedes_patch_id != previous_patch_id:
            raise RevisionModelError(
                "Replacement Patch supersedes_patch_id must identify the selected predecessor."
            )
        if replacement.task_id != previous.task_id:
            raise RevisionModelError("Replacement Patch must remain within the same workflow task.")
        if previous.status in {"superseded", "blocked"}:
            raise RevisionModelError(
                f"Patch {previous_patch_id} is already {previous.status} and cannot be superseded."
            )
        if any(
            patch.supersedes_patch_id == previous_patch_id
            for patch in self.patches
        ):
            raise RevisionModelError(
                f"Patch {previous_patch_id} already has a replacement; branching supersession is not allowed."
            )

        if previous.status in {"applied", "verified"}:
            previous_outputs: dict[str, set[str]] = {}
            for operation in previous.operations:
                previous_outputs.setdefault(operation.document, set()).add(
                    _canonical_path(operation.output_file)
                )
            for operation in replacement.operations:
                allowed_inputs = previous_outputs.get(operation.document, set())
                if _canonical_path(operation.input_file) not in allowed_inputs:
                    raise RevisionModelError(
                        f"Replacement operation {operation.operation_id} must use the prior Patch output "
                        f"as its input for {operation.document}."
                    )
        elif previous.status == "abandoned":
            recovery_events = [
                event for event in self.events
                if event.subject_type == "patch"
                and event.subject_id == previous_patch_id
                and event.event_type == "patch_abandoned_after_batch_recovery"
            ]
            if len(recovery_events) != 1:
                raise RevisionModelError(
                    f"Abandoned Patch {previous_patch_id} lacks one unambiguous recovery record."
                )
            details = recovery_events[0].details
            parent_documents = details.get("parent_documents")
            failed_outputs = details.get("failed_output_files")
            if not isinstance(parent_documents, Mapping) or not isinstance(failed_outputs, Mapping):
                raise RevisionModelError(
                    f"Abandoned Patch {previous_patch_id} has no recorded active-parent lineage."
                )
            for operation in replacement.operations:
                parent = parent_documents.get(operation.document)
                old_outputs = failed_outputs.get(operation.document)
                if not isinstance(parent, Mapping) or not isinstance(old_outputs, list):
                    raise RevisionModelError(
                        f"Recovery record for Patch {previous_patch_id} does not cover "
                        f"document role {operation.document}."
                    )
                if _canonical_path(operation.output_file) in {
                    _canonical_path(value) for value in old_outputs if isinstance(value, str)
                }:
                    raise RevisionModelError(
                        f"Replacement operation {operation.operation_id} reuses a failed batch output."
                    )
                if _canonical_path(operation.input_file) != _canonical_path(parent.get("file", "")):
                    raise RevisionModelError(
                        f"Recovered replacement operation {operation.operation_id} must restart from "
                        f"the recorded active parent for {operation.document}."
                    )
                parent_path = Path(operation.input_file).expanduser().resolve()
                if not parent_path.is_file() or sha256_file(parent_path) != parent.get("sha256"):
                    raise RevisionModelError(
                        f"Recorded active parent for {operation.document} is missing or changed; "
                        "the recovered Patch cannot be rebased."
                    )
                if operation.source_sha256 not in {None, parent.get("sha256")}:
                    raise RevisionModelError(
                        f"Replacement operation {operation.operation_id} source hash differs from "
                        f"the recovered active parent for {operation.document}."
                    )

        replacement_operations_by_unit: dict[str, list[PatchOperation]] = {}
        for unit in self.response_units:
            if previous_patch_id not in unit.linked_patch_ids:
                continue
            matched_operations = [
                operation for operation in replacement.operations
                if unit.comment_id in operation.source_comment_ids
            ]
            if unit.disposition == "revised" and not matched_operations:
                raise RevisionModelError(
                    f"Replacement Patch {replacement.patch_id} contains no operation explicitly attributed "
                    f"to ResponseUnit {unit.response_unit_id} / comment {unit.comment_id}; "
                    "refusing to guess operation ownership."
                )
            replacement_operations_by_unit[unit.response_unit_id] = matched_operations

        existing_operation_ids = {
            operation.operation_id
            for existing_patch in self.patches
            for operation in existing_patch.operations
        }
        replacement_operation_ids = [
            operation.operation_id for operation in replacement.operations
        ]
        seen_replacement_operation_ids: set[str] = set()
        duplicate_operation_ids: set[str] = set()
        for operation_id in replacement_operation_ids:
            if operation_id in seen_replacement_operation_ids:
                duplicate_operation_ids.add(operation_id)
            seen_replacement_operation_ids.add(operation_id)
        duplicate_operation_ids.update(
            existing_operation_ids.intersection(seen_replacement_operation_ids)
        )
        if duplicate_operation_ids:
            raise RevisionModelError(
                "Replacement PatchOperation operation_id values must be globally unique: "
                + ", ".join(sorted(duplicate_operation_ids))
            )

        now = datetime.now(timezone.utc).isoformat()
        previous_status = previous.status
        previous.status = "superseded"
        self.patches.append(replacement)
        self.events.append(RevisionEvent(
            event_id=str(uuid.uuid4()),
            subject_id=previous_patch_id,
            subject_type="patch",
            event_type="patch_superseded",
            timestamp_utc=now,
            actor=actor,
            details={
                "replacement_patch_id": replacement.patch_id,
                "reason": reason,
                "previous_status": previous_status,
            },
        ))
        self.events.append(RevisionEvent(
            event_id=str(uuid.uuid4()),
            subject_id=replacement.patch_id,
            subject_type="patch",
            event_type="patch_created_as_replacement",
            timestamp_utc=now,
            actor=actor,
            details={
                "supersedes_patch_id": previous_patch_id,
                "reason": reason,
            },
        ))
        for unit in self.response_units:
            if previous_patch_id not in unit.linked_patch_ids:
                continue
            unit.linked_patch_ids = [
                replacement.patch_id if patch_id == previous_patch_id else patch_id
                for patch_id in unit.linked_patch_ids
            ]
            unit.linked_patch_ids = list(dict.fromkeys(unit.linked_patch_ids))
            replacement_operations = replacement_operations_by_unit[unit.response_unit_id]
            unit.linked_operation_ids = [operation.operation_id for operation in replacement_operations]
            unit.linked_targets = [{
                "document": operation.document,
                "location": operation.location,
                "output_file": operation.output_file,
            } for operation in replacement_operations]
            unit.status = "draft"
            unit.semantic_review_confirmed = False
            unit.verification_report_id = None
            unit.location_map_fingerprint = None
            unit.location_map_file = None
            unit.location_map_file_sha256 = None
            unit.final_locations = []
            unit.completed_at_utc = None
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=unit.response_unit_id,
                subject_type="response_unit",
                event_type="response_reopened_after_patch_supersession",
                timestamp_utc=now,
                actor=actor,
                details={
                    "previous_patch_id": previous_patch_id,
                    "replacement_patch_id": replacement.patch_id,
                    "reason": reason,
                },
            ))
        self.validate_or_raise()

    def record_legacy_preflight(
        self,
        preflight_report: Mapping[str, Any],
        revision_plan: Mapping[str, Any],
        *,
        report_id: str,
        actor: str = "Co_Reviewer Preflight",
    ) -> None:
        if not isinstance(preflight_report, Mapping):
            raise RevisionModelError("Preflight report must be an object.")
        preflight_items = preflight_report.get("preflight_items", [])
        if not isinstance(preflight_items, list):
            raise RevisionModelError("Preflight report must contain preflight_items.")
        plan_items = revision_plan.get("revision_items", [])
        if not isinstance(plan_items, list):
            raise RevisionModelError("Legacy revision plan must contain revision_items.")
        plan_by_id = {
            item.get("id"): item for item in plan_items if isinstance(item, Mapping)
        }
        patch_by_id = {patch.patch_id: patch for patch in self.patches}
        now = datetime.now(timezone.utc).isoformat()
        _text(report_id, "report_id")
        _text(actor, "actor")
        for record in preflight_items:
            if not isinstance(record, Mapping):
                raise RevisionModelError("Preflight item entries must be objects.")
            patch_id = _text(record.get("revision_item_id"), "revision_item_id")
            patch = patch_by_id.get(patch_id)
            plan_item = plan_by_id.get(patch_id)
            if patch is None or not isinstance(plan_item, Mapping):
                raise RevisionModelError(f"Preflight result refers to unknown Patch {patch_id}.")
            fingerprint = _sha256(
                record.get("preflight_fingerprint"), "preflight_fingerprint"
            )
            if fingerprint != plan_item.get("preflight_fingerprint"):
                raise RevisionModelError(
                    f"Preflight fingerprint mismatch for Patch {patch_id}."
                )
            previous_fingerprint = patch.preflight_fingerprint
            previous_approval = patch.approval_fingerprint
            approval_is_still_valid = (
                patch.status == "approved"
                and previous_fingerprint == fingerprint
                and previous_approval == plan_item.get("approval_fingerprint")
                and previous_approval == revision_item_fingerprint(dict(plan_item))
                and plan_item.get("status") == "approved"
            )
            targets = plan_item.get("targets", [])
            if len(targets) != len(patch.operations):
                raise RevisionModelError(f"Preflight target count mismatch for Patch {patch_id}.")
            updated_operations = []
            for operation, target in zip(patch.operations, targets):
                source_hash = _sha256(
                    target.get("source_sha256"), "source_sha256"
                )
                if target.get("operation_id") != operation.operation_id:
                    raise RevisionModelError(f"Preflight operation ID mismatch for Patch {patch_id}.")
                if list(target.get("source_comment_ids", [])) != list(operation.source_comment_ids):
                    raise RevisionModelError(f"Preflight source_comment_ids changed for PatchOperation {operation.operation_id}.")
                phrase = target.get("location_phrase")
                operation_units = [
                    unit for unit in self.response_units
                    if operation.operation_id in unit.linked_operation_ids
                ]
                requires_automatic_locator = (
                    not operation_units
                    or any(unit.location_requirement != "manual_required" for unit in operation_units)
                )
                if requires_automatic_locator:
                    if not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 255:
                        raise RevisionModelError(
                            f"Preflight did not generate a valid unique location_phrase for "
                            f"automatically mapped PatchOperation {operation.operation_id}."
                        )
                elif phrase is not None and (
                    not isinstance(phrase, str) or not phrase.strip() or len(phrase) > 255
                ):
                    raise RevisionModelError(
                        f"Manual-only PatchOperation {operation.operation_id} has an invalid optional location_phrase."
                    )
                updated_operations.append(replace(
                    operation,
                    source_sha256=source_hash,
                    location_phrase=phrase if requires_automatic_locator else None,
                    p_idx=target.get("p_idx"),
                ))
            patch.operations = updated_operations
            patch.preflight_fingerprint = fingerprint
            patch.preflight_report_id = report_id
            if approval_is_still_valid:
                patch.status = "approved"
            else:
                patch.approval_fingerprint = None
                patch.status = "preflighted"
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=patch_id,
                subject_type="patch",
                event_type="preflight",
                timestamp_utc=now,
                actor=actor,
                details={
                    "preflight_report_id": report_id,
                    "preflight_fingerprint": fingerprint,
                },
            ))
        self.validate_or_raise()

    def record_legacy_approval(
        self,
        revision_plan: Mapping[str, Any],
        patch_ids: Sequence[str],
        *,
        actor: str,
    ) -> None:
        plan_items = revision_plan.get("revision_items", [])
        if not isinstance(plan_items, list):
            raise RevisionModelError("Legacy revision plan must contain revision_items.")
        plan_by_id = {
            item.get("id"): item for item in plan_items if isinstance(item, Mapping)
        }
        patch_by_id = {patch.patch_id: patch for patch in self.patches}
        now = datetime.now(timezone.utc).isoformat()
        _text(actor, "actor")
        requested = list(dict.fromkeys(patch_ids))
        if not requested:
            raise RevisionModelError("At least one Patch ID must be approved.")
        for patch_id in requested:
            patch = patch_by_id.get(patch_id)
            item = plan_by_id.get(patch_id)
            if patch is None or not isinstance(item, Mapping):
                raise RevisionModelError(f"Approval refers to unknown Patch {patch_id}.")
            if patch.status not in {"preflighted", "approved"}:
                raise RevisionModelError(
                    f"Patch {patch_id} must be preflighted before approval; current state is {patch.status}."
                )
            if item.get("status") != "approved":
                raise RevisionModelError(f"Legacy revision item {patch_id} is not approved.")
            preflight_fp = _sha256(item.get("preflight_fingerprint"), "preflight_fingerprint")
            approval_fp = _sha256(item.get("approval_fingerprint"), "approval_fingerprint")
            if preflight_fp != patch.preflight_fingerprint:
                raise RevisionModelError(f"Patch {patch_id} preflight fingerprint changed before approval.")
            if approval_fp != revision_item_fingerprint(dict(item)):
                raise RevisionModelError(f"Patch {patch_id} approval fingerprint is stale or invalid.")
            # Persist the human-approved authorization metadata in the typed
            # Patch model so a later Ledger export cannot silently lose it.
            approved_targets = item.get("targets", [])
            if len(approved_targets) != len(patch.operations):
                raise RevisionModelError(f"Approved target count changed for Patch {patch_id}.")
            patch.operations = [
                replace(
                    operation,
                    authorized_change=target.get("authorized_change"),
                    protected_spans=tuple(target.get("protected_spans", [])),
                )
                for operation, target in zip(patch.operations, approved_targets)
            ]
            patch.approval_fingerprint = approval_fp
            patch.status = "approved"
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=patch_id,
                subject_type="patch",
                event_type="approval",
                timestamp_utc=now,
                actor=actor,
                details={"approval_fingerprint": approval_fp},
            ))
        self.validate_or_raise()

    def record_legacy_apply_reports(
        self,
        reports: Sequence[Mapping[str, Any]],
        revision_plan: Mapping[str, Any],
        *,
        actor: str = "Co_Reviewer",
    ) -> None:
        by_id = {patch.patch_id: patch for patch in self.patches}
        plan_items = revision_plan.get("revision_items", [])
        if not isinstance(plan_items, list):
            raise RevisionModelError("Legacy revision plan must contain revision_items.")
        plan_by_id = {
            item.get("id"): item
            for item in plan_items if isinstance(item, Mapping)
        }

        # One Patch may span several document roles. Apply emits one report per
        # input/output DOCX pair, so validate the complete operation set before
        # moving the Patch out of approved state. Mutating it after the first
        # document report made the second report look like a replay.
        applied_by_patch: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(list)
        for report in reports:
            if not isinstance(report, Mapping):
                raise RevisionModelError("Apply report entries must be objects.")
            results = report.get("results", [])
            if not isinstance(results, list):
                raise RevisionModelError("Apply report results must be an array.")
            for result in results:
                if not isinstance(result, Mapping) or result.get("status") != "applied":
                    continue
                patch_id = result.get("revision_item_id")
                if not isinstance(patch_id, str) or not patch_id.strip():
                    raise RevisionModelError("Applied result is missing its exact Patch ID.")
                if patch_id in by_id:
                    applied_by_patch[patch_id].append((report, result))

        now = datetime.now(timezone.utc).isoformat()
        for patch_id, evidence_rows in applied_by_patch.items():
            patch = by_id[patch_id]
            legacy_item = plan_by_id.get(patch_id)
            if not isinstance(legacy_item, Mapping):
                raise RevisionModelError(
                    f"Applied patch {patch_id} is missing from the legacy revision plan."
                )
            approval_fingerprint = legacy_item.get("approval_fingerprint")
            _sha256(approval_fingerprint, "approval_fingerprint")
            preflight_fingerprint = _sha256(
                legacy_item.get("preflight_fingerprint"), "preflight_fingerprint"
            )
            if (
                patch.status != "approved"
                or patch.approval_fingerprint != approval_fingerprint
                or patch.preflight_fingerprint != preflight_fingerprint
            ):
                raise RevisionModelError(
                    f"Apply evidence for Patch {patch_id} does not match its preflight/approval ledger state."
                )
            if not patch.preflight_report_id:
                raise RevisionModelError(
                    f"Patch {patch_id} has no recorded preflight report; refusing to record apply."
                )

            expected_by_output: dict[str, dict[str, Any]] = {}
            for operation in patch.operations:
                output_key = _canonical_path(operation.output_file)
                row = expected_by_output.setdefault(output_key, {
                    "input_file": _canonical_path(operation.input_file),
                    "output_file": str(Path(operation.output_file).expanduser().resolve()),
                    "operation_ids": set(),
                })
                if row["input_file"] != _canonical_path(operation.input_file):
                    raise RevisionModelError(
                        f"Patch {patch_id} maps one output DOCX to multiple source DOCX files."
                    )
                row["operation_ids"].add(operation.operation_id)

            actual_by_output: dict[str, set[str]] = defaultdict(set)
            reports_by_output: dict[str, Mapping[str, Any]] = {}
            for report, result in evidence_rows:
                output_value = report.get("output_file")
                input_value = report.get("input_file")
                operation_id = result.get("operation_id")
                if not isinstance(output_value, str) or not output_value.strip():
                    raise RevisionModelError(
                        f"Apply evidence for Patch {patch_id} has no output DOCX path."
                    )
                if not isinstance(input_value, str) or not input_value.strip():
                    raise RevisionModelError(
                        f"Apply evidence for Patch {patch_id} has no input DOCX path."
                    )
                output_key = _canonical_path(output_value)
                expected = expected_by_output.get(output_key)
                if expected is None:
                    raise RevisionModelError(
                        f"Apply evidence for Patch {patch_id} refers to an unowned output DOCX: {output_value}."
                    )
                if _canonical_path(input_value) != expected["input_file"]:
                    raise RevisionModelError(
                        f"Apply evidence for Patch {patch_id} does not match its exact source DOCX."
                    )
                if not isinstance(operation_id, str) or operation_id not in expected["operation_ids"]:
                    raise RevisionModelError(
                        f"Apply evidence for Patch {patch_id} is missing a valid exact PatchOperation ID."
                    )
                if operation_id in actual_by_output[output_key]:
                    raise RevisionModelError(
                        f"Apply evidence duplicates PatchOperation {operation_id} for Patch {patch_id}."
                    )
                actual_by_output[output_key].add(operation_id)
                reports_by_output[output_key] = report

            expected_operation_sets = {
                key: row["operation_ids"] for key, row in expected_by_output.items()
            }
            if dict(actual_by_output) != expected_operation_sets:
                missing = sorted({
                    operation_id
                    for key, operation_ids in expected_operation_sets.items()
                    for operation_id in operation_ids - actual_by_output.get(key, set())
                })
                extra = sorted({
                    operation_id
                    for key, operation_ids in actual_by_output.items()
                    for operation_id in operation_ids - expected_operation_sets.get(key, set())
                })
                raise RevisionModelError(
                    f"Apply evidence for Patch {patch_id} does not cover its exact operation/output set; "
                    f"missing={missing}, unexpected={extra}."
                )

            patch.status = "applied"
            revision_ids_by_output: dict[str, set[str]] = defaultdict(set)
            for report, result in evidence_rows:
                output_key = _canonical_path(str(report.get("output_file", "")))
                for field_name in ("delete_revision_id", "insert_revision_id"):
                    revision_id = result.get(field_name)
                    if revision_id is not None:
                        revision_ids_by_output[output_key].add(str(revision_id))
            for output_key, report in reports_by_output.items():
                report_id = str(report.get("report_id") or report.get("output_file"))
                if report_id not in patch.apply_report_ids:
                    patch.apply_report_ids.append(report_id)
                input_sha = report.get("input_sha256")
                output_sha = report.get("output_sha256")
                _sha256(input_sha, "apply_report.input_sha256")
                _sha256(output_sha, "apply_report.output_sha256")
                revision_ids = sorted(revision_ids_by_output.get(output_key, set()))
                if not revision_ids:
                    raise RevisionModelError(
                        f"Apply report for Patch {patch_id} has no Track Changes revision IDs."
                    )
                revision_record = {
                    "input_file": str(report.get("input_file", "")),
                    "input_sha256": input_sha,
                    "output_file": str(report.get("output_file", "")),
                    "output_sha256": output_sha,
                    "revision_ids": revision_ids,
                }
                if revision_record not in patch.applied_revision_ids:
                    patch.applied_revision_ids.append(revision_record)
                self.events.append(RevisionEvent(
                    event_id=str(uuid.uuid4()),
                    subject_id=patch_id,
                    subject_type="patch",
                    event_type="patch_applied",
                    timestamp_utc=now,
                    actor=actor,
                    details={
                        "input_file": report.get("input_file", ""),
                        "output_file": report.get("output_file", ""),
                        "edits_applied": report.get("edits_applied", 0),
                        "input_sha256": report.get("input_sha256"),
                        "output_sha256": report.get("output_sha256"),
                        "revision_ids": sorted(revision_ids_by_output.get(output_key, set())),
                        "operation_ids": sorted(actual_by_output[output_key]),
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
            item_approval_fingerprint = _sha256(
                item.get("approval_fingerprint"), "approval_fingerprint"
            )
            item_preflight_fingerprint = _sha256(
                item.get("preflight_fingerprint"), "preflight_fingerprint"
            )
            if (
                item_approval_fingerprint != patch.approval_fingerprint
                or item_preflight_fingerprint != patch.preflight_fingerprint
            ):
                raise RevisionModelError(
                    f"Verification seals for Patch {patch.patch_id} do not match the RevisionLedger."
                )
            item_output_hashes = copy.deepcopy(item.get("verified_output_hashes", []))
            if patch.status == "verified":
                if (
                    patch.verification_report_id != verification_report_id
                    or patch.verified_output_hashes != item_output_hashes
                ):
                    raise RevisionModelError(
                        f"Patch {patch.patch_id} is already verified with different report/hash evidence."
                    )
                # Idempotent retry after a process interruption between writing
                # the verified plan snapshot and committing the RevisionLedger.
                continue
            if patch.status != "applied":
                raise RevisionModelError(
                    f"Patch {patch.patch_id} must be applied before verification; current state is {patch.status}."
                )
            patch.status = "verified"
            patch.verification_report_id = verification_report_id
            patch.verified_output_hashes = item_output_hashes
            if not patch.verified_output_hashes:
                raise RevisionModelError(
                    f"Verified patch {patch.patch_id} has no sealed output hashes."
                )
            expected_outputs = {
                _canonical_path(operation.output_file)
                for operation in patch.operations
            }
            actual_outputs: set[str] = set()
            for hash_entry in patch.verified_output_hashes:
                if not isinstance(hash_entry, Mapping):
                    raise RevisionModelError(
                        f"Verified Patch {patch.patch_id} contains a malformed output hash record."
                    )
                path_value = _text(hash_entry.get("path"), "verified_output_hashes.path")
                _sha256(hash_entry.get("sha256"), "verified_output_hashes.sha256")
                actual_outputs.add(_canonical_path(path_value))
            if actual_outputs != expected_outputs or len(actual_outputs) != len(patch.verified_output_hashes):
                raise RevisionModelError(
                    f"Verified output hashes for Patch {patch.patch_id} must cover exactly its DOCX outputs."
                )
            self.events.append(RevisionEvent(
                event_id=str(uuid.uuid4()),
                subject_id=patch.patch_id,
                subject_type="patch",
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
            "workflow_task_fingerprints": dict(self.workflow_task_fingerprints),
            "documents": [item.to_dict() for item in self.documents],
            "patches": [item.to_dict() for item in self.patches],
            "response_units": [item.to_dict() for item in self.response_units],
            "events": [item.to_dict() for item in self.events],
        }

    def merge_batch(
        self,
        addition: "RevisionLedger",
        *,
        allow_scope_refresh_for_task_ids: Sequence[str] = (),
    ) -> None:
        """Append a new ready-batch result to the same review-round ledger."""
        if not isinstance(addition, RevisionLedger):
            raise RevisionModelError("addition must be a RevisionLedger.")
        if self.revision_round_id != addition.revision_round_id:
            raise RevisionModelError(
                "Cannot merge Patches from different revision rounds."
            )
        previous_task_fingerprints = self.workflow_task_fingerprints
        incoming_task_fingerprints = addition.workflow_task_fingerprints
        missing_tasks = set(previous_task_fingerprints) - set(incoming_task_fingerprints)
        if missing_tasks:
            raise RevisionModelError(
                "New Workflow Ledger removed task(s) from this revision round: "
                + ", ".join(sorted(missing_tasks))
            )
        changed_tasks = {
            task_id for task_id, previous_fingerprint in previous_task_fingerprints.items()
            if incoming_task_fingerprints.get(task_id) != previous_fingerprint
        }
        allowed_scope_refreshes = set(allow_scope_refresh_for_task_ids)
        if changed_tasks - allowed_scope_refreshes:
            raise RevisionModelError(
                "Workflow task scope changed without an explicitly authorized rebase: "
                + ", ".join(sorted(changed_tasks - allowed_scope_refreshes))
            )
        for task_id in changed_tasks:
            existing_task_patches = [
                patch for patch in self.patches if patch.task_id == task_id
            ]
            active_task_patches = [
                patch for patch in existing_task_patches
                if patch.status not in {"superseded", "blocked"}
            ]
            incoming_task_patches = [
                patch for patch in addition.patches if patch.task_id == task_id
            ]
            existing_replacements = [
                patch for patch in existing_task_patches
                if patch.supersedes_patch_id
                and patch.status not in {"superseded", "blocked", "abandoned"}
            ]
            incoming_units = [
                unit for unit in addition.response_units
                if task_id in unit.linked_task_ids
            ]
            if active_task_patches and not existing_replacements and not any(
                patch.supersedes_patch_id for patch in incoming_task_patches
            ):
                raise RevisionModelError(
                    f"Task {task_id} scope cannot be refreshed while an earlier Patch is active; "
                    "create an explicit superseding Patch first."
                )
            if not incoming_task_patches and not existing_replacements and not incoming_units:
                raise RevisionModelError(
                    f"Task {task_id} scope refresh has no replacement Patch or ResponseUnit evidence."
                )
        existing_patch_by_id = {patch.patch_id: patch for patch in self.patches}
        incoming_patch_by_id = {patch.patch_id: patch for patch in addition.patches}
        duplicate_patches = set(existing_patch_by_id) & set(incoming_patch_by_id)
        conflicting_duplicates = [
            patch_id for patch_id in duplicate_patches
            if existing_patch_by_id[patch_id].to_dict()
            != incoming_patch_by_id[patch_id].to_dict()
        ]
        if conflicting_duplicates:
            raise RevisionModelError(
                "Patch IDs already exist with different contents in the RevisionLedger: "
                + ", ".join(sorted(conflicting_duplicates))
            )

        document_index = {
            document.document_id: index
            for index, document in enumerate(self.documents)
        }
        for incoming in addition.documents:
            index = document_index.get(incoming.document_id)
            if index is None:
                document_index[incoming.document_id] = len(self.documents)
                self.documents.append(incoming)
                continue
            current = self.documents[index]
            current_input = _canonical_path(current.input_file)
            incoming_input = _canonical_path(incoming.input_file)
            current_output = _canonical_path(current.output_file)
            incoming_output = _canonical_path(incoming.output_file)
            incoming_has_patch_operations = any(
                operation.document == incoming.role
                for patch in addition.patches
                for operation in patch.operations
            )
            # A newly selected no-change ResponseUnit may anchor the document
            # version already published by an earlier batch. In that case the
            # incoming record deliberately uses the current active file as both
            # input and output. Preserve the established RevisionDocument chain
            # rather than rebasing a previously edited role onto itself.
            if (
                not incoming_has_patch_operations
                and incoming_input == incoming_output == current_output
            ):
                expected_active_hash = current.output_sha256
                if (
                    expected_active_hash is None
                    and current_input == current_output
                ):
                    # Before the first Working Clean projection, an unchanged
                    # source/active document has a source hash but no derived
                    # output hash yet. That source hash is sufficient to bind
                    # another no-change ResponseUnit to the same active file.
                    expected_active_hash = current.source_sha256
                if not expected_active_hash:
                    raise RevisionModelError(
                        f"Document {incoming.document_id} cannot attach a no-change anchor "
                        "to an active version without a verified output or unchanged-source hash."
                    )
                if incoming.source_sha256 and incoming.source_sha256 != expected_active_hash:
                    raise RevisionModelError(
                        f"Document {incoming.document_id} no-change anchor source hash "
                        "does not match the current active document."
                    )
                active_path = Path(incoming.output_file).expanduser().resolve()
                if not active_path.is_file() or sha256_file(active_path) != expected_active_hash:
                    raise RevisionModelError(
                        f"Document {incoming.document_id} no-change anchor is not bound to "
                        "the current active document hash."
                    )
                continue

            revision_changed = current.revision_id != incoming.revision_id
            input_changed = current_input != incoming_input
            output_changed = current_output != incoming_output

            advanced_source_hash: str | None = None
            if revision_changed or input_changed:
                recovery_parent = None
                if incoming_input != current_output:
                    for recovery_event in self.events:
                        if (
                            recovery_event.subject_type != "patch"
                            or recovery_event.event_type != "patch_abandoned_after_batch_recovery"
                        ):
                            continue
                        parent_rows = recovery_event.details.get("parent_documents")
                        failed_rows = recovery_event.details.get("failed_output_files")
                        parent_row = parent_rows.get(current.role) if isinstance(parent_rows, Mapping) else None
                        failed_paths = failed_rows.get(current.role) if isinstance(failed_rows, Mapping) else None
                        if (
                            isinstance(parent_row, Mapping)
                            and isinstance(failed_paths, list)
                            and current_output in {
                                _canonical_path(value) for value in failed_paths if isinstance(value, str)
                            }
                        ):
                            recovery_parent = parent_row
                            break
                    if recovery_parent is None:
                        raise RevisionModelError(
                            f"Document {incoming.document_id} rebase input must be the previous "
                            f"RevisionDocument output: {current.output_file}, unless a matching "
                            "failed-batch recovery event explicitly restores the active parent."
                        )
                    if incoming_input != _canonical_path(str(recovery_parent.get("file", ""))):
                        raise RevisionModelError(
                            f"Document {incoming.document_id} recovered rebase must use its recorded active parent."
                        )
                    expected_input_hash = recovery_parent.get("sha256")
                    if not isinstance(expected_input_hash, str) or not _SHA256_RE.fullmatch(expected_input_hash):
                        raise RevisionModelError(
                            f"Document {incoming.document_id} recovery record has no valid active-parent SHA-256."
                        )
                else:
                    predecessor_hashes: list[str] = []
                    for previous_patch in self.patches:
                        if previous_patch.status not in {"verified", "superseded"}:
                            continue
                        if not any(
                            operation.document == current.role
                            and _canonical_path(operation.output_file) == current_output
                            for operation in previous_patch.operations
                        ):
                            continue
                        predecessor_hashes.extend(
                            entry["sha256"]
                            for entry in previous_patch.verified_output_hashes
                            if isinstance(entry, Mapping)
                            and isinstance(entry.get("path"), str)
                            and _canonical_path(entry["path"]) == current_output
                            and isinstance(entry.get("sha256"), str)
                        )
                    if current.output_sha256:
                        predecessor_hashes.append(current.output_sha256)
                    if not predecessor_hashes:
                        raise RevisionModelError(
                            f"Document {incoming.document_id} cannot advance revisions without "
                            "a verified SHA-256 for the previous DOCX output."
                        )
                    if len(set(predecessor_hashes)) != 1:
                        raise RevisionModelError(
                            f"Document {incoming.document_id} has conflicting verified hashes "
                            "for its previous output."
                        )
                    expected_input_hash = predecessor_hashes[0]
                if incoming.source_sha256 and incoming.source_sha256 != expected_input_hash:
                    raise RevisionModelError(
                        f"Document {incoming.document_id} new source identity does not match "
                        "the previous verified output or explicitly recovered active parent."
                    )
                advanced_source_hash = expected_input_hash
                input_path = Path(incoming.input_file).expanduser().resolve()
                if not input_path.is_file():
                    raise RevisionModelError(
                        f"Document {incoming.document_id} rebased input DOCX does not exist: {input_path}."
                    )
                try:
                    actual_input_hash = sha256_file(input_path)
                except OSError as exc:
                    raise RevisionModelError(
                        f"Cannot hash rebased input DOCX {input_path}: {exc}"
                    ) from exc
                if actual_input_hash != expected_input_hash:
                    raise RevisionModelError(
                        f"Document {incoming.document_id} rebased input file changed "
                        "after the predecessor Patch was verified or recovered."
                    )
            elif (
                current.source_sha256
                and incoming.source_sha256
                and current.source_sha256 != incoming.source_sha256
            ):
                raise RevisionModelError(
                    f"Document {incoming.document_id} source SHA-256 changed without an "
                    "advancing verified output/rebase."
                )

            self.documents[index] = replace(
                current,
                revision_id=incoming.revision_id,
                input_file=incoming.input_file,
                source_sha256=(
                    incoming.source_sha256
                    if incoming.source_sha256 is not None
                    else (advanced_source_hash or current.source_sha256)
                ),
                output_file=incoming.output_file,
                output_sha256=(
                    incoming.output_sha256
                    if incoming.output_sha256 is not None
                    else (None if output_changed else current.output_sha256)
                ),
                working_clean_file=None if output_changed or input_changed else current.working_clean_file,
                working_clean_sha256=None if output_changed or input_changed else current.working_clean_sha256,
                working_view_fingerprint=None if output_changed or input_changed else current.working_view_fingerprint,
            )

        unit_index = {
            unit.response_unit_id: index
            for index, unit in enumerate(self.response_units)
        }
        for incoming in addition.response_units:
            index = unit_index.get(incoming.response_unit_id)
            if index is None:
                unit_index[incoming.response_unit_id] = len(self.response_units)
                self.response_units.append(incoming)
                continue
            current = self.response_units[index]
            if (
                current.comment_id != incoming.comment_id
                or current.comment_text != incoming.comment_text
                or (
                    current.reviewer != incoming.reviewer
                    and current.reviewer != "Reviewer"
                    and incoming.reviewer != "Reviewer"
                )
            ):
                raise RevisionModelError(
                    f"ResponseUnit {incoming.response_unit_id} no longer matches its original reviewer comment."
                )
            # "Reviewer" is the deliberate no-map fallback, not a real identity.
            # Upgrade it when an explicit label arrives, and never let a later
            # fallback overwrite a reviewer label already recorded in this round.
            if current.reviewer == "Reviewer" and incoming.reviewer != "Reviewer":
                current.reviewer = incoming.reviewer
            linked_patches = list(dict.fromkeys(
                current.linked_patch_ids + incoming.linked_patch_ids
            ))
            linked_tasks = list(dict.fromkeys(
                current.linked_task_ids + incoming.linked_task_ids
            ))
            linked_targets = list(current.linked_targets)
            for target in incoming.linked_targets:
                if target not in linked_targets:
                    linked_targets.append(target)
            linked_operations = list(dict.fromkeys(current.linked_operation_ids + incoming.linked_operation_ids))
            classification_changed = (
                current.disposition != incoming.disposition
                or current.location_requirement != incoming.location_requirement
                or current.resolution_reason != incoming.resolution_reason
                or current.anchor_evidence_id != incoming.anchor_evidence_id
                or current.anchor_document != incoming.anchor_document
                or current.anchor_part != incoming.anchor_part
                or current.anchor_p_idx != incoming.anchor_p_idx
                or current.anchor_para_id != incoming.anchor_para_id
                or current.anchor_source_excerpt != incoming.anchor_source_excerpt
                or current.location_phrase != incoming.location_phrase
            )
            if classification_changed:
                current.disposition = incoming.disposition
                current.location_requirement = incoming.location_requirement
                current.resolution_reason = incoming.resolution_reason
                current.anchor_evidence_id = incoming.anchor_evidence_id
                current.anchor_document = incoming.anchor_document
                current.anchor_part = incoming.anchor_part
                current.anchor_p_idx = incoming.anchor_p_idx
                current.anchor_para_id = incoming.anchor_para_id
                current.anchor_source_excerpt = incoming.anchor_source_excerpt
                current.location_phrase = incoming.location_phrase
            response_text_changed = (
                bool(incoming.response_text)
                and incoming.response_text != current.response_text
            )
            relationship_changed = (
                linked_patches != current.linked_patch_ids
                or linked_tasks != current.linked_task_ids
                or linked_targets != current.linked_targets
                or linked_operations != current.linked_operation_ids
                or classification_changed
            )
            current.linked_patch_ids = linked_patches
            current.linked_task_ids = linked_tasks
            current.linked_targets = linked_targets
            current.linked_operation_ids = linked_operations
            if response_text_changed:
                current.response_text = incoming.response_text
            if response_text_changed or relationship_changed:
                current.status = "blocked" if current.disposition == "clarification_needed" else "draft"
                current.semantic_review_confirmed = False
                current.verification_report_id = None
                current.location_map_fingerprint = None
                current.location_map_file = None
                current.location_map_file_sha256 = None
                current.final_locations = []
                current.completed_at_utc = None

        self.patches.extend(
            patch for patch in addition.patches
            if patch.patch_id not in existing_patch_by_id
        )
        known_event_ids = {event.event_id for event in self.events}
        for event in addition.events:
            if event.event_id in known_event_ids:
                raise RevisionModelError(
                    f"RevisionEvent ID already exists: {event.event_id}."
                )
            known_event_ids.add(event.event_id)
            self.events.append(event)
        self.workflow_ledger_fingerprint = addition.workflow_ledger_fingerprint
        self.workflow_task_fingerprints = dict(incoming_task_fingerprints)
        self.validate_or_raise()

    def validate_or_raise(self) -> None:
        errors = self.validate()
        if errors:
            raise RevisionModelError("RevisionLedger validation failed:\n- " + "\n- ".join(errors))


def build_revision_ledger(
    workflow_ledger: Mapping[str, Any],
    *,
    document_paths: Mapping[str, Mapping[str, str]] | None = None,
    reviewer_comments_by_id: Mapping[str, str] | None = None,
    reviewer_by_task: Mapping[str, Any] | None = None,
    selected_task_ids: Sequence[str] | None = None,
    force_document_paths: bool = False,
) -> RevisionLedger:
    """Build explicit Patch and ResponseUnit records from a Workflow Ledger.

    No edit text is inferred. Every executable patch operation must supply
    find/replace and both paths. Reviewer comment text must come from explicit
    reviewer evidence or reviewer_comments_by_id. reviewer_by_task accepts a
    legacy task-level label or a per-comment mapping so ResponseUnits preserve
    the reviewer attached to each original comment ID.
    """
    if not isinstance(workflow_ledger, Mapping):
        raise RevisionModelError("workflow_ledger must be an object.")
    from .workflow_engine import (
        select_ready_tasks,
        validate_workflow_ledger,
        workflow_scope_fingerprint,
    )
    workflow_errors = validate_workflow_ledger(workflow_ledger)
    if workflow_errors:
        raise RevisionModelError(
            "Cannot build Patches from an invalid Workflow Ledger:\n- "
            + "\n- ".join(workflow_errors)
        )
    round_id = _text(workflow_ledger.get("revision_round_id"), "revision_round_id")
    task_rows = workflow_ledger.get("tasks")
    if not isinstance(task_rows, list):
        raise RevisionModelError("Workflow Ledger tasks must be an array.")
    reviewer_comments_by_id = reviewer_comments_by_id or {}
    reviewer_by_task = reviewer_by_task or {}
    document_paths = document_paths or {}

    scheduler_ready_batch = set(select_ready_tasks(task_rows))
    if selected_task_ids is None:
        selected_ids = scheduler_ready_batch
    else:
        if not isinstance(selected_task_ids, (list, tuple)):
            raise RevisionModelError("selected_task_ids must be an array.")
        if any(not isinstance(item, str) or not item.strip() for item in selected_task_ids):
            raise RevisionModelError("selected_task_ids must contain non-empty task IDs.")
        if len(selected_task_ids) != len(set(selected_task_ids)):
            raise RevisionModelError("selected_task_ids contains duplicates.")
        selected_ids = set(selected_task_ids)
    unavailable = selected_ids - scheduler_ready_batch
    if unavailable:
        raise RevisionModelError(
            "Patch batch includes tasks outside the current conflict-free ready batch: "
            + ", ".join(sorted(unavailable))
        )

    patches: list[Patch] = []
    response_units: list[ResponseUnit] = []

    for task in task_rows:
        if not isinstance(task, Mapping):
            raise RevisionModelError("Workflow task entries must be objects.")
        task_id = _text(task.get("task_id"), "task_id")
        comment_ids = [str(value) for value in task.get("source_comment_ids", [])]
        reviewer_spec = reviewer_by_task.get(task_id, "Reviewer")
        comment_reviewers: dict[str, str] = {}
        default_reviewer: str | None = None
        if isinstance(reviewer_spec, str):
            reviewer = _text(reviewer_spec, f"reviewer_map[{task_id}]")
            default_reviewer = reviewer
        elif isinstance(reviewer_spec, Mapping):
            allowed_reviewer_keys = {"default", "patch_label", "comment_ids"}
            unknown_reviewer_keys = set(reviewer_spec) - allowed_reviewer_keys
            if unknown_reviewer_keys:
                raise RevisionModelError(
                    f"reviewer_map[{task_id}] contains unsupported key(s): "
                    + ", ".join(sorted(str(key) for key in unknown_reviewer_keys))
                )
            raw_default = reviewer_spec.get("default")
            if raw_default is not None:
                default_reviewer = _text(
                    raw_default, f"reviewer_map[{task_id}].default"
                )
            raw_comment_reviewers = reviewer_spec.get("comment_ids", {})
            if not isinstance(raw_comment_reviewers, Mapping):
                raise RevisionModelError(
                    f"reviewer_map[{task_id}].comment_ids must be an object."
                )
            for raw_comment_id, raw_label in raw_comment_reviewers.items():
                comment_id = str(raw_comment_id)
                if comment_id not in comment_ids:
                    raise RevisionModelError(
                        f"reviewer_map[{task_id}] refers to unknown task comment ID {comment_id!r}."
                    )
                comment_reviewers[comment_id] = _text(
                    raw_label,
                    f"reviewer_map[{task_id}].comment_ids[{comment_id}]",
                )
            missing_reviewers = [
                comment_id for comment_id in comment_ids
                if comment_id not in comment_reviewers and default_reviewer is None
            ]
            if missing_reviewers:
                raise RevisionModelError(
                    f"reviewer_map[{task_id}] is missing reviewer labels for comment ID(s): "
                    + ", ".join(missing_reviewers)
                )
            assigned_reviewers = [
                comment_reviewers.get(comment_id, default_reviewer)
                for comment_id in comment_ids
            ]
            unique_reviewers = list(dict.fromkeys(
                value for value in assigned_reviewers if value
            ))
            raw_patch_label = reviewer_spec.get("patch_label")
            reviewer = (
                _text(raw_patch_label, f"reviewer_map[{task_id}].patch_label")
                if raw_patch_label is not None
                else " / ".join(unique_reviewers) or "Reviewer"
            )
            if default_reviewer is None and unique_reviewers:
                default_reviewer = unique_reviewers[0]
        else:
            raise RevisionModelError(
                f"reviewer_map[{task_id}] must be a reviewer label or an object."
            )
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
        has_authored_patch_operations = bool(raw_operations)
        # Keep ResponseUnits for all tasks, but create executable Patches only
        # for the selected, author-approved, conflict-free ready batch.
        if task_id not in selected_ids:
            raw_operations = []
        elif raw_operations and task.get("status") != "ready":
            raise RevisionModelError(
                f"Task {task_id} has Patch operations but is not in ready state."
            )
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
            if force_document_paths and isinstance(document, str) and document in document_paths:
                managed = document_paths[document]
                data["input_file"] = managed.get("input_file")
                data["output_file"] = managed.get("output_file")
                data["source_sha256"] = managed.get("source_sha256")
            operation = PatchOperation.from_mapping(
                data, fallback_id=f"{task_id}-OP{index + 1:03d}"
            )
            if not operation.source_comment_ids:
                raise RevisionModelError(
                    f"Task {task_id} PatchOperation {operation.operation_id} must declare source_comment_ids at authoring time."
                )
            unknown_sources = set(operation.source_comment_ids) - set(comment_ids)
            if unknown_sources:
                raise RevisionModelError(
                    f"PatchOperation {operation.operation_id} refers to comment ID(s) outside Task {task_id}: "
                    + ", ".join(sorted(unknown_sources))
                )
            operation_records.append(operation)

        patch_id = str(task.get("patch_id") or task_id)
        if operation_records:
            if not comment_text:
                raise RevisionModelError(
                    f"Task {task_id} has executable patch operations but no traceable reviewer comment text."
                )
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
            raw_specs = task.get("response_unit_specs")
            if raw_specs is not None and not isinstance(raw_specs, list):
                raise RevisionModelError(f"Task {task_id} response_unit_specs must be an array.")
            specs_by_comment: dict[str, Mapping[str, Any]] = {}
            for spec in (raw_specs or []):
                if not isinstance(spec, Mapping):
                    raise RevisionModelError(f"Task {task_id} response_unit_specs entries must be objects.")
                spec_comment_id = str(spec.get("comment_id", ""))
                if spec_comment_id not in comment_ids:
                    raise RevisionModelError(f"Task {task_id} response_unit_specs references unknown comment ID {spec_comment_id!r}.")
                if spec_comment_id in specs_by_comment:
                    raise RevisionModelError(f"Task {task_id} has duplicate response_unit_specs for {spec_comment_id}.")
                specs_by_comment[spec_comment_id] = spec
            evidence_by_id = {
                str(row.get("evidence_id")): row for row in evidence
                if isinstance(row, Mapping) and row.get("evidence_id") is not None
            }
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
                linked_ops = [op for op in operation_records if comment_id in op.source_comment_ids]
                spec = specs_by_comment.get(comment_id)
                if spec is None:
                    if linked_ops:
                        disposition, location_requirement, resolution_reason = "revised", "required", ""
                    elif task_id not in selected_ids and has_authored_patch_operations:
                        disposition, location_requirement = "revised", "required"
                        resolution_reason = "Exact PatchOperations are recorded in the pending task but are not materialized until author authorization."
                    elif task.get("task_type") == "response_only":
                        disposition, location_requirement = "reply_only", "not_required"
                        resolution_reason = "Response-only task; no manuscript change or existing-text location is claimed."
                    elif task.get("readiness") == "needs_clarification" or task.get("status") == "blocked":
                        disposition, location_requirement = "clarification_needed", "required"
                        resolution_reason = "; ".join(str(value) for value in task.get("unresolved_questions", [])) or "Author clarification remains unresolved."
                    else:
                        raise RevisionModelError(
                            f"Task {task_id} comment {comment_id} has no PatchOperation and no explicit response_unit_specs classification; declare reply_only, no_change or clarification_needed."
                        )
                    anchor_evidence_id = None
                    anchor_document = anchor_part = None
                    anchor_p_idx = None
                    anchor_para_id = anchor_source_excerpt = None
                else:
                    disposition = _text(spec.get("disposition"), f"Task {task_id} response_unit_specs[{comment_id}].disposition")
                    location_requirement = _text(spec.get("location_requirement"), f"Task {task_id} response_unit_specs[{comment_id}].location_requirement")
                    resolution_reason = str(spec.get("resolution_reason") or "")
                    anchor_evidence_id = spec.get("anchor_evidence_id")
                    anchor_document = anchor_part = None
                    anchor_p_idx = None
                    anchor_para_id = anchor_source_excerpt = None
                    if disposition == "no_change":
                        anchor = evidence_by_id.get(str(anchor_evidence_id))
                        anchor_part_value = anchor.get("part") if isinstance(anchor, Mapping) else None
                        supported_anchor_part = (
                            anchor_part_value == "word/document.xml"
                            or (
                                isinstance(anchor_part_value, str)
                                and re.fullmatch(r"word/(?:header|footer)\d+\.xml", anchor_part_value) is not None
                            )
                        )
                        if (
                            not isinstance(anchor, Mapping)
                            or anchor.get("match_status") != "confirmed"
                            or anchor.get("document") not in {"Manuscript", "SI"}
                            or not supported_anchor_part
                            or not isinstance(anchor.get("p_idx"), int)
                        ):
                            raise RevisionModelError(
                                f"no_change ResponseUnit {comment_id} requires confirmed Manuscript/SI paragraph anchor evidence in document.xml/header/footer."
                            )
                        if anchor_part_value != "word/document.xml" and location_requirement != "manual_required":
                            raise RevisionModelError(
                                f"no_change ResponseUnit {comment_id} anchor is in {anchor_part_value}; use manual_required for non-body anchors."
                            )
                        anchor_document, anchor_part, anchor_p_idx = anchor.get("document"), anchor.get("part"), anchor.get("p_idx")
                        anchor_para_id = anchor.get("para_id")
                        anchor_source_excerpt = anchor.get("source_excerpt")
                        if not isinstance(anchor_source_excerpt, str) or not anchor_source_excerpt.strip():
                            raise RevisionModelError(f"no_change ResponseUnit {comment_id} anchor evidence must include source_excerpt text.")
                if disposition == "revised" and task_id in selected_ids and not linked_ops:
                    raise RevisionModelError(f"revised ResponseUnit {comment_id} has no PatchOperation with source_comment_ids including that comment.")
                if disposition == "reply_only" and location_requirement != "not_required":
                    raise RevisionModelError(f"reply_only ResponseUnit {comment_id} must use not_required location policy.")
                if disposition == "no_change" and location_requirement not in {"existing_anchor_required", "manual_required"}:
                    raise RevisionModelError(f"no_change ResponseUnit {comment_id} must use existing_anchor_required or manual_required location policy.")
                if disposition == "revised" and location_requirement not in {"required", "manual_required"}:
                    raise RevisionModelError(f"revised ResponseUnit {comment_id} must use required or manual_required location policy.")
                if disposition == "clarification_needed" and location_requirement == "not_required":
                    raise RevisionModelError(f"clarification_needed ResponseUnit {comment_id} cannot bypass resolution with not_required.")
                linked_targets = [{
                    "document": op.document, "location": op.location, "output_file": op.output_file,
                } for op in linked_ops]
                response_units.append(ResponseUnit(
                    response_unit_id=f"RU-{task_id}-{comment_id}",
                    reviewer=comment_reviewers.get(
                        comment_id, default_reviewer or reviewer
                    ),
                    comment_id=comment_id,
                    comment_text=specific_comment,
                    response_text=str(task.get("proposed_response") or ""),
                    linked_task_ids=[task_id],
                    linked_patch_ids=[patch_id] if linked_ops else [],
                    linked_targets=linked_targets,
                    linked_operation_ids=[op.operation_id for op in linked_ops],
                    disposition=disposition,
                    location_requirement=location_requirement,
                    resolution_reason=resolution_reason,
                    anchor_evidence_id=str(anchor_evidence_id) if anchor_evidence_id is not None else None,
                    anchor_document=anchor_document,
                    anchor_part=anchor_part,
                    anchor_p_idx=anchor_p_idx,
                    anchor_para_id=anchor_para_id,
                    anchor_source_excerpt=anchor_source_excerpt,
                    status="blocked" if disposition == "clarification_needed" else "draft",
                ))

    identities_by_role: dict[str, list[Mapping[str, Any]]] = {}
    for identity in workflow_ledger.get("documents", []):
        if isinstance(identity, Mapping) and isinstance(identity.get("role"), str):
            identities_by_role.setdefault(identity["role"], []).append(identity)

    operations_by_role: dict[str, PatchOperation] = {}
    for patch in patches:
        for operation in patch.operations:
            operations_by_role.setdefault(operation.document, operation)
    anchor_roles = {
        unit.anchor_document for unit in response_units
        if unit.disposition == "no_change"
        and unit.location_requirement in {"existing_anchor_required", "manual_required"}
        and unit.anchor_document
    }
    registered_roles = set(operations_by_role) | anchor_roles

    document_records: list[RevisionDocument] = []
    for role in sorted(registered_roles):
        first_operation = operations_by_role.get(role)
        if first_operation is None:
            paths = document_paths.get(role, {})
            if not paths.get("input_file") or not paths.get("output_file"):
                raise RevisionModelError(f"Existing-anchor document role {role} needs explicit input_file and output_file in --document-paths.")
        identity_rows = identities_by_role.get(role, [])
        identity = identity_rows[0] if identity_rows else {}
        paths = document_paths.get(role, {})
        document_records.append(RevisionDocument(
            document_id=str(identity.get("document_id") or role),
            revision_id=str(identity.get("revision_id") or _fingerprint({
                "role": role,
                "revision_round_id": round_id,
                "identity_mode": "round-scoped-legacy-fallback",
            })),
            role=role,
            input_file=paths.get("input_file", first_operation.input_file if first_operation else None),
            output_file=paths.get("output_file", first_operation.output_file if first_operation else None),
            source_sha256=paths.get("source_sha256", identity.get("source_sha256")),
        ))

    ledger = RevisionLedger(
        revision_round_id=round_id,
        documents=document_records,
        patches=patches,
        response_units=response_units,
        workflow_ledger_fingerprint=_fingerprint(workflow_ledger),
        workflow_task_fingerprints={
            task["task_id"]: workflow_scope_fingerprint(task)
            for task in task_rows
            if isinstance(task, Mapping) and isinstance(task.get("task_id"), str)
        },
    )
    ledger.validate_or_raise()
    return ledger


def response_units_to_legacy_plan(
    response_units: Sequence[ResponseUnit],
    patches: Sequence[Patch] = (),
) -> dict[str, Any]:
    """Adapt ResponseUnits and exact PatchOperation excerpts for deterministic rendering."""
    patch_by_id = {item.patch_id: item for item in patches}
    operation_owners = {
        operation.operation_id: (patch, operation)
        for patch in patches
        for operation in patch.operations
    }
    revision_items = []
    for unit in response_units:
        linked_patches = [patch_by_id[item] for item in unit.linked_patch_ids if item in patch_by_id]
        targets = copy.deepcopy(unit.linked_targets)
        location_by_id = {
            location.locator_id: location
            for location in unit.final_locations
        }
        if unit.final_locations:
            for location in unit.final_locations:
                targets.append({
                    "document": location.document,
                    "location": location.display_text,
                    "output_file": location.output_file,
                })

        quoted_changes: list[dict[str, Any]] = []
        for operation_id in unit.linked_operation_ids:
            owner = operation_owners.get(operation_id)
            if owner is None:
                raise RevisionModelError(
                    f"ResponseUnit {unit.response_unit_id} links unknown PatchOperation {operation_id}; "
                    "the Response Letter renderer will not infer operation ownership."
                )
            patch, operation = owner
            if patch.patch_id not in unit.linked_patch_ids or unit.comment_id not in operation.source_comment_ids:
                raise RevisionModelError(
                    f"PatchOperation {operation_id} is not explicitly attributed to "
                    f"ResponseUnit {unit.response_unit_id}/{unit.comment_id}."
                )
            locator_id = (
                f"manual:{operation_id}"
                if unit.location_requirement == "manual_required"
                else operation_id
            )
            location = location_by_id.get(locator_id)
            if location is not None and location.document != operation.document:
                raise RevisionModelError(
                    f"ResponseUnit {unit.response_unit_id} location role does not match PatchOperation {operation_id}."
                )
            is_deletion = not bool(operation.replace.strip())
            quote_text = operation.find if is_deletion else operation.replace
            quoted_changes.append({
                "patch_id": patch.patch_id,
                "operation_id": operation_id,
                "document": operation.document,
                "action": "removed_text" if is_deletion else "revised_text",
                "quote_text": quote_text,
                "location": location.display_text if location is not None else None,
                "location_status": (
                    "manual_confirmed" if location is not None and location.mapping_method == "manual"
                    else "word_com_confirmed" if location is not None
                    else "location_pending"
                ),
                "output_file": location.output_file if location is not None else operation.output_file,
                "verified": patch.status == "verified",
            })

        if unit.disposition == "no_change":
            locator_id = (
                f"manual:anchor:{unit.anchor_evidence_id}"
                if unit.location_requirement == "manual_required"
                else f"anchor:{unit.anchor_evidence_id}"
            )
            location = location_by_id.get(locator_id)
            if unit.anchor_source_excerpt:
                quoted_changes.append({
                    "patch_id": None,
                    "operation_id": None,
                    "document": unit.anchor_document,
                    "action": "existing_text",
                    "quote_text": unit.anchor_source_excerpt,
                    "location": location.display_text if location is not None else None,
                    "location_status": (
                        "manual_confirmed" if location is not None and location.mapping_method == "manual"
                        else "word_com_confirmed" if location is not None
                        else "location_pending"
                    ),
                    "output_file": location.output_file if location is not None else None,
                    "verified": unit.semantic_review_confirmed,
                })

        final_is_mapped = (
            unit.status in {"location_mapped", "completed"}
            and unit.semantic_review_confirmed
            and bool(unit.final_locations)
            and bool(unit.location_map_fingerprint)
        )
        patch_statuses = {item.status for item in linked_patches}
        status = (
            "verified" if final_is_mapped or (
                unit.location_requirement == "not_required"
                and unit.semantic_review_confirmed
                and unit.status in {"verified", "completed"}
            )
            else "location_pending" if unit.status == "verified"
            else "blocked" if unit.status == "blocked"
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
            "quoted_changes": quoted_changes,
            "disposition": unit.disposition,
            "resolution_reason": unit.resolution_reason,
        })
    return {"revision_items": revision_items}



def revision_ledger_from_mapping(data: Mapping[str, Any]) -> RevisionLedger:
    """Rehydrate and validate a RevisionLedger JSON object."""
    if not isinstance(data, Mapping):
        raise RevisionModelError("RevisionLedger root must be an object.")

    documents = [
        RevisionDocument(
            document_id=row.get("document_id"),
            revision_id=row.get("revision_id"),
            role=row.get("role"),
            input_file=row.get("input_file"),
            output_file=row.get("output_file"),
            source_sha256=row.get("source_sha256"),
            output_sha256=row.get("output_sha256"),
            working_clean_file=row.get("working_clean_file"),
            working_clean_sha256=row.get("working_clean_sha256"),
            working_view_fingerprint=row.get("working_view_fingerprint"),
        )
        for row in data.get("documents", [])
    ]
    patches = []
    for row in data.get("patches", []):
        operations = [
            PatchOperation.from_mapping(item)
            for item in row.get("operations", [])
        ]
        patches.append(Patch(
            patch_id=row.get("patch_id"),
            task_id=row.get("task_id"),
            reviewer=row.get("reviewer"),
            comment=row.get("comment"),
            proposed_response=row.get("proposed_response", ""),
            operations=operations,
            status=row.get("status", "planned"),
            supersedes_patch_id=row.get("supersedes_patch_id"),
            preflight_fingerprint=row.get("preflight_fingerprint"),
            preflight_report_id=row.get("preflight_report_id"),
            approval_fingerprint=row.get("approval_fingerprint"),
            apply_report_ids=list(row.get("apply_report_ids", [])),
            applied_revision_ids=copy.deepcopy(row.get("applied_revision_ids", [])),
            verification_report_id=row.get("verification_report_id"),
            verified_output_hashes=copy.deepcopy(row.get("verified_output_hashes", [])),
        ))
    response_units = []
    for row in data.get("response_units", []):
        locations = [
            ResponseLocation(
                document=item.get("document"),
                output_file=item.get("output_file"),
                pdf_file=item.get("pdf_file"),
                page=item.get("page"),
                line_start=item.get("line_start"),
                line_end=item.get("line_end"),
                pdf_sha256=item.get("pdf_sha256"),
                location_map_id=item.get("location_map_id"),
                mapping_fingerprint=item.get("mapping_fingerprint"),
                output_sha256=item.get("output_sha256"),
                locator_id=item.get("locator_id"),
                source_clean_sha256=item.get("source_clean_sha256"),
                mapping_method=item.get("mapping_method", "word_com"),
                locator_text=item.get("locator_text"),
            )
            for item in row.get("final_locations", [])
        ]
        legacy_unattributed = (
            "disposition" not in row
            or ("linked_patch_ids" in row and row.get("linked_patch_ids") and not row.get("linked_operation_ids"))
        )
        response_units.append(ResponseUnit(
            response_unit_id=row.get("response_unit_id"),
            reviewer=row.get("reviewer"),
            comment_id=str(row.get("comment_id")),
            comment_text=row.get("comment_text"),
            response_text=row.get("response_text", ""),
            linked_task_ids=list(row.get("linked_task_ids", [])),
            linked_patch_ids=list(row.get("linked_patch_ids", [])),
            linked_targets=copy.deepcopy(row.get("linked_targets", [])),
            linked_operation_ids=list(row.get("linked_operation_ids", [])),
            disposition=row.get("disposition", "revised" if row.get("linked_patch_ids") else "clarification_needed"),
            location_requirement=row.get("location_requirement", "required"),
            resolution_reason=row.get("resolution_reason", ""),
            anchor_evidence_id=row.get("anchor_evidence_id"),
            anchor_document=row.get("anchor_document"),
            anchor_part=row.get("anchor_part"),
            anchor_p_idx=row.get("anchor_p_idx"),
            anchor_para_id=row.get("anchor_para_id"),
            anchor_source_excerpt=row.get("anchor_source_excerpt"),
            location_phrase=row.get("location_phrase"),
            status=("blocked" if legacy_unattributed else row.get("status", "draft")),
            semantic_review_confirmed=(False if legacy_unattributed else row.get("semantic_review_confirmed", False)),
            verification_report_id=row.get("verification_report_id"),
            location_map_fingerprint=row.get("location_map_fingerprint"),
            location_map_file=row.get("location_map_file"),
            location_map_file_sha256=row.get("location_map_file_sha256"),
            final_locations=locations,
            completed_at_utc=row.get("completed_at_utc"),
        ))
    events = [
        RevisionEvent(
            event_id=row.get("event_id"),
            subject_id=row.get("subject_id", row.get("patch_id")),
            subject_type=row.get("subject_type", "patch"),
            event_type=row.get("event_type"),
            timestamp_utc=row.get("timestamp_utc"),
            actor=row.get("actor"),
            details=copy.deepcopy(row.get("details", {})),
        )
        for row in data.get("events", [])
    ]
    return RevisionLedger(
        revision_round_id=data.get("revision_round_id"),
        documents=documents,
        patches=patches,
        response_units=response_units,
        events=events,
        schema_version=data.get("schema_version", "0.5.1"),
        workflow_ledger_fingerprint=data.get("workflow_ledger_fingerprint"),
        workflow_task_fingerprints=dict(data.get("workflow_task_fingerprints", {})),
    )

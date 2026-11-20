# -*- coding: utf-8 -*-
"""Unapplied, source-bound manual suggestions for safely blocked revision operations."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any


class ManualFallbackRequired(ValueError):
    """A deterministic safety gate blocked an edit and supplied a manual suggestion."""

    def __init__(self, suggestion: Mapping[str, Any], message: str):
        super().__init__(message)
        self.suggestion = copy.deepcopy(dict(suggestion))


def build_manual_suggestion(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build an immutable diagnostic artifact; this function never edits a DOCX."""
    if not isinstance(payload, Mapping):
        raise TypeError("Manual suggestion input must be an object.")
    required = (
        "document_role", "source_document_path", "source_sha256", "word_part",
        "target", "source_excerpt", "blocking_reason",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError("Manual suggestion is missing required fields: " + ", ".join(missing))

    role = payload.get("document_role")
    if role not in {"Manuscript", "SI", "Reviewers", "Reply"}:
        raise ValueError("document_role must be Manuscript, SI, Reviewers or Reply.")
    source_hash = payload.get("source_sha256")
    if (
        not isinstance(source_hash, str)
        or len(source_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in source_hash)
    ):
        raise ValueError("source_sha256 must be a lowercase SHA-256 digest.")
    if not isinstance(payload.get("source_document_path"), str) or not payload["source_document_path"].strip():
        raise ValueError("source_document_path must be a non-empty path.")
    if not isinstance(payload.get("word_part"), str) or not payload["word_part"].strip():
        raise ValueError("word_part must be a non-empty Word package part.")
    if not isinstance(payload.get("source_excerpt"), str) or not payload["source_excerpt"].strip():
        raise ValueError("source_excerpt must contain the exact source text.")
    if not isinstance(payload.get("blocking_reason"), str) or not payload["blocking_reason"].strip():
        raise ValueError("blocking_reason must explain the deterministic safety block.")
    if not isinstance(payload.get("target"), Mapping):
        raise ValueError("target must contain a stable object identity or a location hint.")

    target = copy.deepcopy(dict(payload["target"]))
    if not any(
        isinstance(target.get(key), (str, int)) and str(target.get(key)).strip()
        for key in ("object_id", "para_id", "paragraph_index", "target_ref")
    ):
        raise ValueError("target requires object_id, para_id, paragraph_index or target_ref.")

    comment_ids = payload.get("reviewer_comment_ids", [])
    structures = payload.get("blocking_structures", [])
    missing_evidence = payload.get("missing_evidence", [])
    for name, value in (
        ("reviewer_comment_ids", comment_ids),
        ("blocking_structures", structures),
        ("missing_evidence", missing_evidence),
    ):
        if not isinstance(value, list):
            raise ValueError(f"{name} must be an array.")
    normalized_comment_ids = [str(value) for value in comment_ids]
    if any(not value.strip() for value in normalized_comment_ids):
        raise ValueError("reviewer_comment_ids cannot contain empty identifiers.")
    if len(normalized_comment_ids) != len(set(normalized_comment_ids)):
        raise ValueError("reviewer_comment_ids cannot contain duplicates.")
    if any(not isinstance(value, Mapping) for value in structures):
        raise ValueError("blocking_structures entries must be structured objects.")
    operation_id = payload.get("operation_id")
    if operation_id is not None and (
        not isinstance(operation_id, str) or not operation_id.strip()
    ):
        raise ValueError("operation_id must be a non-empty string or null.")

    proposed = payload.get("proposed_edit")
    if proposed is not None and not isinstance(proposed, str):
        raise ValueError("proposed_edit must be a string or null.")
    if missing_evidence:
        proposed = None

    record = {
        "schema_version": "1.0",
        "artifact_kind": "manual_suggestion",
        "status": "BLOCKED_MANUAL_REVIEW",
        "document_role": role,
        "source_document_path": str(payload["source_document_path"]),
        "source_sha256": source_hash,
        "word_part": str(payload["word_part"]),
        "target": target,
        "reviewer_comment_ids": normalized_comment_ids,
        "operation_id": operation_id,
        "source_excerpt": payload["source_excerpt"],
        "before_context": str(payload.get("before_context") or ""),
        "after_context": str(payload.get("after_context") or ""),
        "blocking_reason": payload["blocking_reason"],
        "blocking_structures": copy.deepcopy(structures),
        "missing_evidence": copy.deepcopy(missing_evidence),
        "proposed_edit": proposed,
        "not_applied": True,
        "docx_write_performed": False,
        "human_action_required": (
            "Clarify or supply missing evidence before proposing a change."
            if missing_evidence else
            "Inspect the exact target in Microsoft Word and make any approved edit manually."
        ),
    }
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record["suggestion_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return record

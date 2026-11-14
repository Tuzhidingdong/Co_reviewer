# -*- coding: utf-8 -*-
"""Stable document/version fingerprints used by caches and approval gates.

This module initially centralises the v0.3 SHA-256 and workflow fingerprints.
The v0.5.1 multi-dimensional object/citation identity implementation is added
through versioned, separately tested normalisers rather than changing these
legacy integrity fingerprints in place.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def revision_targets_fingerprint(item: dict[str, Any]) -> str:
    """Fingerprint the exact target anchors and source DOCX versions checked by preflight."""
    payload = {"targets": item.get("targets", [])}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def revision_item_fingerprint(item: dict[str, Any]) -> str:
    """Fingerprint the exact reviewer-facing approval scope, excluding lifecycle metadata."""
    payload = {
        "id": item.get("id"),
        "reviewer": item.get("reviewer"),
        "comment": item.get("comment"),
        "proposed_response": item.get("proposed_response", ""),
        "targets": item.get("targets", []),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def revision_verification_fingerprint(item: dict[str, Any]) -> str:
    """Fingerprint the approved scope and exact output versions acknowledged as verified."""
    payload = {
        "approval_fingerprint": item.get("approval_fingerprint"),
        "verified_output_hashes": item.get("verified_output_hashes", []),
        "semantic_review_confirmed": item.get("semantic_review_confirmed"),
        "verified_at_utc": item.get("verified_at_utc"),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()




_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CITATION_MODES = frozenset({
    "numeric_superscript",
    "numeric_bracketed",
    "numeric_parenthetical",
    "author_year",
    "footnote",
    "mixed",
    "unknown",
})
_CITATION_NUMBERING_SCOPES = frozenset({
    "document", "section", "chapter", "none", "unknown",
})


class IdentityValidationError(ValueError):
    """Raised when a document or object identity is incomplete or inconsistent."""


def canonical_fingerprint(value: Any) -> str:
    """Hash a deterministic JSON representation; reject non-serializable payloads."""
    try:
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as exc:
        raise IdentityValidationError(
            f"Identity payload must be JSON-serializable: {exc}"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityValidationError(f"{field} must be a non-empty string.")
    return value.strip()


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IdentityValidationError(
            f"{field} must be a lowercase SHA-256 hexadecimal digest."
        )
    return value


def normalize_visible_text(value: str) -> str:
    """Normalise text for identity hashing, without treating formatting as content."""
    if not isinstance(value, str):
        raise IdentityValidationError("Visible text must be a string.")
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip()


def hash_content(value: str) -> str:
    return canonical_fingerprint({"normalization": "NFKC-whitespace-v1", "text": normalize_visible_text(value)})


def hash_structure(value: Any) -> str:
    return canonical_fingerprint({"dimension": "structure-v1", "value": value})


def hash_formatting(value: Any) -> str:
    return canonical_fingerprint({"dimension": "formatting-v1", "value": value})


def hash_citation_state(value: Any) -> str:
    return canonical_fingerprint({"dimension": "citation-state-v1", "value": value})


@dataclass(frozen=True)
class CitationProfile:
    """Explicit citation-system profile; detection evidence remains inspectable."""

    mode: str = "unknown"
    style_name: str | None = None
    numbering_scope: str = "unknown"
    marker_examples: tuple[str, ...] = ()
    reference_labels: tuple[str, ...] = ()
    detection_method: str = "unknown"
    evidence_fingerprint: str | None = None
    profile_version: str = "1"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in _CITATION_MODES:
            raise IdentityValidationError(f"Unsupported citation mode: {self.mode!r}.")
        if (
            not isinstance(self.numbering_scope, str)
            or self.numbering_scope not in _CITATION_NUMBERING_SCOPES
        ):
            raise IdentityValidationError(
                f"Unsupported citation numbering scope: {self.numbering_scope!r}."
            )
        if self.style_name is not None and not isinstance(self.style_name, str):
            raise IdentityValidationError("Citation style_name must be a string or null.")
        for field_name, values in (
            ("marker_examples", self.marker_examples),
            ("reference_labels", self.reference_labels),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, str) or not value.strip() for value in values
            ):
                raise IdentityValidationError(
                    f"CitationProfile.{field_name} must be a tuple of non-empty strings."
                )
        _require_nonempty(self.detection_method, "detection_method")
        if self.evidence_fingerprint is not None:
            _require_sha256(self.evidence_fingerprint, "evidence_fingerprint")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "style_name": self.style_name,
            "numbering_scope": self.numbering_scope,
            "marker_examples": list(self.marker_examples),
            "reference_labels": list(self.reference_labels),
            "detection_method": self.detection_method,
            "evidence_fingerprint": self.evidence_fingerprint,
            "profile_version": self.profile_version,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self.to_dict())


@dataclass(frozen=True)
class DocumentIdentity:
    """Five independent identity dimensions for one exact document revision."""

    document_id: str
    revision_id: str
    role: str
    source_sha256: str
    content_sha256: str
    structure_sha256: str
    formatting_sha256: str
    citation_sha256: str
    citation_profile: CitationProfile
    identity_version: str = "0.5.1"

    def __post_init__(self) -> None:
        for field_name in ("document_id", "revision_id", "role"):
            _require_nonempty(getattr(self, field_name), field_name)
        for field_name in (
            "source_sha256", "content_sha256", "structure_sha256",
            "formatting_sha256", "citation_sha256",
        ):
            _require_sha256(getattr(self, field_name), field_name)
        if not isinstance(self.citation_profile, CitationProfile):
            raise IdentityValidationError("citation_profile must be a CitationProfile.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_version": self.identity_version,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "role": self.role,
            "source_sha256": self.source_sha256,
            "dimensions": {
                "content_sha256": self.content_sha256,
                "structure_sha256": self.structure_sha256,
                "formatting_sha256": self.formatting_sha256,
                "citation_sha256": self.citation_sha256,
            },
            "citation_profile": self.citation_profile.to_dict(),
        }

    @property
    def identity_fingerprint(self) -> str:
        return canonical_fingerprint(self.to_dict())


def build_document_identity(
    *,
    role: str,
    source_sha256: str,
    visible_text: str,
    structure: Any,
    formatting: Any,
    citation_state: Any,
    citation_profile: CitationProfile | None = None,
    document_id: str | None = None,
) -> DocumentIdentity:
    """Build document identity from parser-produced dimensions, not a filename.

    Reuse document_id across revisions of the same logical document. revision_id
    changes with exact source bytes, while content/structure/formatting/citation
    dimensions allow controlled comparison between revisions.
    """
    role = _require_nonempty(role, "role")
    source_sha256 = _require_sha256(source_sha256, "source_sha256")
    stable_document_id = (
        _require_nonempty(document_id, "document_id")
        if document_id is not None else str(uuid.uuid4())
    )
    revision_id = canonical_fingerprint({
        "document_id": stable_document_id,
        "source_sha256": source_sha256,
    })
    profile = citation_profile or CitationProfile()
    if not isinstance(profile, CitationProfile):
        raise IdentityValidationError("citation_profile must be a CitationProfile.")
    citation_payload = {
        "state": citation_state,
        "profile": profile.to_dict(),
    }
    return DocumentIdentity(
        document_id=stable_document_id,
        revision_id=revision_id,
        role=role,
        source_sha256=source_sha256,
        content_sha256=hash_content(visible_text),
        structure_sha256=hash_structure(structure),
        formatting_sha256=hash_formatting(formatting),
        citation_sha256=hash_citation_state(citation_payload),
        citation_profile=profile,
    )


@dataclass(frozen=True)
class DocumentObjectIdentity:
    """Persisted internal identity for one paragraph/table/caption/reference object."""

    object_id: str
    document_id: str
    revision_id: str
    object_type: str
    part: str
    object_fingerprint: str
    native_id: str | None = None
    parent_object_id: str | None = None
    location_hint: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "object_id", "document_id", "revision_id",
            "object_type", "part",
        ):
            _require_nonempty(getattr(self, field_name), field_name)
        _require_sha256(self.object_fingerprint, "object_fingerprint")
        if self.native_id is not None:
            _require_nonempty(self.native_id, "native_id")
        if self.parent_object_id is not None:
            _require_nonempty(self.parent_object_id, "parent_object_id")
        if self.location_hint is not None and not isinstance(self.location_hint, Mapping):
            raise IdentityValidationError("location_hint must be an object or null.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "object_type": self.object_type,
            "part": self.part,
            "object_fingerprint": self.object_fingerprint,
            "native_id": self.native_id,
            "parent_object_id": self.parent_object_id,
            "location_hint": dict(self.location_hint or {}),
        }


def build_object_identity(
    *,
    document: DocumentIdentity,
    object_type: str,
    part: str,
    object_payload: Any,
    object_id: str | None = None,
    native_id: str | None = None,
    parent_object_id: str | None = None,
    location_hint: Mapping[str, Any] | None = None,
) -> DocumentObjectIdentity:
    """Register an object with a persisted ID independent of its paragraph index."""
    if not isinstance(document, DocumentIdentity):
        raise IdentityValidationError("document must be a DocumentIdentity.")
    return DocumentObjectIdentity(
        object_id=(
            _require_nonempty(object_id, "object_id")
            if object_id is not None else str(uuid.uuid4())
        ),
        document_id=document.document_id,
        revision_id=document.revision_id,
        object_type=_require_nonempty(object_type, "object_type"),
        part=_require_nonempty(part, "part"),
        object_fingerprint=canonical_fingerprint({
            "object_type": object_type,
            "part": part,
            "payload": object_payload,
        }),
        native_id=native_id,
        parent_object_id=parent_object_id,
        location_hint=location_hint,
    )


@dataclass(frozen=True)
class TargetRef:
    """Stable target reference; location hints are advisory, never identity keys."""

    document_id: str
    revision_id: str
    object_id: str
    object_type: str
    part: str
    object_fingerprint: str
    start_offset: int | None = None
    end_offset: int | None = None
    location_hint: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "document_id", "revision_id", "object_id", "object_type", "part",
        ):
            _require_nonempty(getattr(self, field_name), field_name)
        _require_sha256(self.object_fingerprint, "object_fingerprint")
        if (self.start_offset is None) != (self.end_offset is None):
            raise IdentityValidationError("TargetRef offsets must both be set or both be null.")
        if self.start_offset is not None and (
            not isinstance(self.start_offset, int)
            or isinstance(self.start_offset, bool)
            or not isinstance(self.end_offset, int)
            or isinstance(self.end_offset, bool)
            or self.start_offset < 0
            or self.end_offset <= self.start_offset
        ):
            raise IdentityValidationError("TargetRef offsets must define a positive half-open range.")
        if self.location_hint is not None and not isinstance(self.location_hint, Mapping):
            raise IdentityValidationError("TargetRef location_hint must be an object or null.")

    @classmethod
    def from_object(
        cls,
        item: DocumentObjectIdentity,
        *,
        start_offset: int | None = None,
        end_offset: int | None = None,
    ) -> "TargetRef":
        if not isinstance(item, DocumentObjectIdentity):
            raise IdentityValidationError("TargetRef.from_object requires a DocumentObjectIdentity.")
        return cls(
            document_id=item.document_id,
            revision_id=item.revision_id,
            object_id=item.object_id,
            object_type=item.object_type,
            part=item.part,
            object_fingerprint=item.object_fingerprint,
            start_offset=start_offset,
            end_offset=end_offset,
            location_hint=item.location_hint,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "revision_id": self.revision_id,
            "object_id": self.object_id,
            "object_type": self.object_type,
            "part": self.part,
            "object_fingerprint": self.object_fingerprint,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "location_hint": dict(self.location_hint or {}),
        }


class DocumentIdentityAndHash:
    """Compatibility facade for current document and revision integrity hashes."""

    sha256_file = staticmethod(sha256_file)
    revision_targets_fingerprint = staticmethod(revision_targets_fingerprint)
    revision_item_fingerprint = staticmethod(revision_item_fingerprint)
    revision_verification_fingerprint = staticmethod(revision_verification_fingerprint)
    canonical_fingerprint = staticmethod(canonical_fingerprint)
    hash_content = staticmethod(hash_content)
    hash_structure = staticmethod(hash_structure)
    hash_formatting = staticmethod(hash_formatting)
    hash_citation_state = staticmethod(hash_citation_state)
    build_document_identity = staticmethod(build_document_identity)
    build_object_identity = staticmethod(build_object_identity)

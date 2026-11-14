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
from pathlib import Path
from typing import Any


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


class DocumentIdentityAndHash:
    """Compatibility facade for current document and revision integrity hashes."""

    sha256_file = staticmethod(sha256_file)
    revision_targets_fingerprint = staticmethod(revision_targets_fingerprint)
    revision_item_fingerprint = staticmethod(revision_item_fingerprint)
    revision_verification_fingerprint = staticmethod(revision_verification_fingerprint)

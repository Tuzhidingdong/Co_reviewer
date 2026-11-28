# -*- coding: utf-8 -*-
"""Format Requirements: authoritative semantic records, version chains and integrity.

The Agent authors the format semantics; Python NEVER infers them from keywords.
Coverage and Plan only reference FR ids.  This module does not grant authority:
authorization remains the existing instruction_based_authorization mechanism.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

FR_SCHEMA_VERSION = "1.0"
SEMANTIC_FIELDS = (
    "format_requirement_id",
    "instruction_id",
    "source_line_start",
    "source_line_end",
    "source_locator",
    "source_excerpt",
    "document_role",
    "target_ref",
    "token",
    "properties",
    "scope",
    "semantic_role",
)
_REQUIRED_SEMANTIC = ("format_requirement_id", "source_excerpt", "token", "properties", "scope")
_FORBIDDEN_COVERAGE_SEMANTIC_KEYS = frozenset({
    "format_token", "format_properties", "format_scope",
    "fr_token", "fr_properties", "fr_scope", "fr_semantic",
})
_ID_RE = re.compile(r"^FR-.+-v[1-9][0-9]*$")


def canonical_json(value: Any) -> str:
    """Deterministic serialization. No NFC normalization; arrays keep their order."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def semantic_fingerprint(version: Mapping[str, Any]) -> str:
    """SHA-256 over semantic fields only. Status/history/authorization are excluded."""
    data = {key: version[key] for key in SEMANTIC_FIELDS if key in version}
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def version_sha256(version: Mapping[str, Any]) -> str:
    """SHA-256 over the full version object, excluding its own digest field."""
    data = {k: v for k, v in version.items() if k != "version_sha256"}
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def _fr_rows(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = analysis.get("format_requirements", []) if isinstance(analysis, Mapping) else []
    return [row for row in raw if isinstance(row, Mapping)] if isinstance(raw, list) else []


def _versions(fr: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = fr.get("versions", [])
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def validate_format_requirements(analysis: Mapping[str, Any]) -> list[str]:
    """Validate FR data model and immutable, traceable version chains."""
    rows = _fr_rows(analysis)
    if not rows:
        return []
    errors: list[str] = []
    seen_fr_ids: set[str] = set()
    versions_by_id: dict[str, dict[str, Any]] = {}
    fr_versions: dict[str, set[str]] = {}

    for fi, fr in enumerate(rows):
        label = f"format_requirements[{fi}]"
        fr_id = str(fr.get("format_requirement_id", ""))
        if not fr_id or fr_id in seen_fr_ids:
            errors.append(f"{label}.format_requirement_id missing or duplicated")
        seen_fr_ids.add(fr_id)
        if fr.get("schema_version") != FR_SCHEMA_VERSION:
            errors.append(f"{label}.schema_version must be {FR_SCHEMA_VERSION}")
        req_id = str(fr.get("requirement_id", ""))
        if not req_id:
            errors.append(f"{label}.requirement_id required")
        versions = _versions(fr)
        if not versions:
            errors.append(f"{label}.versions must be a non-empty array")
            continue
        ids = {str(v.get("version_id", "")) for v in versions}
        if "" in ids:
            errors.append(f"{label} has a version without version_id")
        if len(ids) != len(versions):
            errors.append(f"{label} has duplicate version_id")
        # T11-15: two children of the same parent are a concurrent semantic
        # fork.  It must be resolved through the Reviewer/authorization flow,
        # never selected automatically by validation or compilation.
        children_by_parent: dict[str, set[str]] = {}
        for version in versions:
            parent = version.get("parent_version_id")
            if parent is None:
                continue
            children_by_parent.setdefault(str(parent), set()).add(
                str(version.get("version_id", ""))
            )
        for parent, children in sorted(children_by_parent.items()):
            if len(children) > 1:
                errors.append(
                    f"{label} has a concurrent version fork from {parent}: "
                    + ", ".join(sorted(children))
                )
        current = str(fr.get("current_version_id", ""))
        if current not in ids:
            errors.append(f"{label}.current_version_id does not point to a listed version")
        fr_versions[fr_id] = ids
        for vi, version in enumerate(versions):
            vlabel = f"{label}.versions[{vi}]"
            vid = str(version.get("version_id", ""))
            if not _ID_RE.match(vid):
                errors.append(f"{vlabel}.version_id invalid: {vid!r}")
            if str(version.get("format_requirement_id", "")) != fr_id:
                errors.append(f"{vlabel}.format_requirement_id mismatch")
            for field in _REQUIRED_SEMANTIC:
                value = version.get(field)
                if field == "properties":
                    if not isinstance(value, dict) or not value:
                        errors.append(f"{vlabel}.properties must be a non-empty object")
                elif field == "scope":
                    if not isinstance(value, Mapping) or not {"replacement", "start", "end"} <= set(value):
                        errors.append(f"{vlabel}.scope invalid")
                elif not isinstance(value, str) or not value.strip():
                    errors.append(f"{vlabel}.{field} required")
            if not isinstance(version.get("status"), str):
                errors.append(f"{vlabel}.status required")
            if semantic_fingerprint(version) != version.get("fingerprint"):
                errors.append(f"{vlabel}.fingerprint mismatch")
            if version_sha256(version) != version.get("version_sha256"):
                errors.append(f"{vlabel}.version_sha256 mismatch")
            parent = version.get("parent_version_id")
            parent_fp = version.get("parent_fingerprint")
            if parent is None:
                if parent_fp is not None:
                    errors.append(f"{vlabel}.parent_fingerprint must be null for a root version")
            else:
                if parent not in ids:
                    errors.append(f"{vlabel}.parent_version_id not found in this FR")
                else:
                    parent_version = next((v for v in versions if str(v.get("version_id")) == parent), None)
                    if parent_version is None or semantic_fingerprint(parent_version) != parent_fp:
                        errors.append(f"{vlabel}.parent_fingerprint inconsistent with parent")
            if vid in versions_by_id and version_sha256(versions_by_id[vid]) != version_sha256(version):
                errors.append(f"{vlabel}.version_id reused with different content")
            versions_by_id[vid] = version

    # Cycle detection on the current chain of each FR.
    for fr in rows:
        current = str(fr.get("current_version_id", ""))
        versions = {str(v.get("version_id")): v for v in _versions(fr)}
        visited: set[str] = set()
        cursor = current
        while cursor:
            if cursor in visited:
                errors.append(f"format requirement {fr.get('format_requirement_id')} has a cyclic version chain")
                break
            visited.add(cursor)
            version = versions.get(cursor)
            if version is None:
                errors.append(f"format requirement {fr.get('format_requirement_id')} current version chain is broken")
                break
            cursor = version.get("parent_version_id")
    return errors


def validate_format_requirements_schema(analysis: Mapping[str, Any]) -> list[str]:
    """Validate FR rows against the JSON Schema when jsonschema is available."""
    rows = _fr_rows(analysis)
    if not rows:
        return []
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return []
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "format-requirements.schema.json"
    if not schema_path.is_file():
        return [f"Format Requirements schema is missing: {schema_path}"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        "format_requirements schema " + "/".join(str(part) for part in error.absolute_path) + ": " + error.message
        for error in sorted(validator.iter_errors(rows), key=lambda item: list(item.absolute_path))
    ]


def current_versions(analysis: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Map atomic requirement id -> current FR version object."""
    out: dict[str, dict[str, Any]] = {}
    for fr in _fr_rows(analysis):
        req_id = str(fr.get("requirement_id", ""))
        current = str(fr.get("current_version_id", ""))
        for version in _versions(fr):
            if str(version.get("version_id")) == current:
                out[req_id] = version
                break
    return out


def crosscheck_coverage_fr(coverage: Mapping[str, Any], analysis: Mapping[str, Any]) -> list[str]:
    """Coverage may only reference FR ids; it must not redefine format semantics."""
    rows = _fr_rows(analysis)
    if not rows:
        return []
    errors: list[str] = []
    valid_ids = {str(fr.get("format_requirement_id")) for fr in rows}
    current = current_versions(analysis)
    valid_current = {str(v.get("version_id")) for v in current.values()}
    referenced: set[str] = set()
    cov_rows = coverage.get("requirements", []) if isinstance(coverage, Mapping) else []
    for index, row in enumerate(cov_rows if isinstance(cov_rows, list) else []):
        if not isinstance(row, Mapping):
            continue
        label = f"coverage.requirements[{index}]"
        bad = _FORBIDDEN_COVERAGE_SEMANTIC_KEYS & set(row)
        if bad:
            errors.append(f"{label} redefines FR semantics: {sorted(bad)}")
        for fr_id in row.get("format_requirement_ids", []) or []:
            if not isinstance(fr_id, str) or fr_id not in valid_ids:
                errors.append(f"{label} references unknown format requirement {fr_id!r}")
                continue
            referenced.add(fr_id)
        for ref in row.get("format_coverage", []) or []:
            if not isinstance(ref, Mapping):
                errors.append(f"{label}.format_coverage entry malformed")
                continue
            fr_id = str(ref.get("format_requirement_id", ""))
            if fr_id not in valid_ids:
                errors.append(f"{label}.format_coverage references unknown FR {fr_id!r}")
                continue
            vid = str(ref.get("version_id", ""))
            if vid not in valid_current:
                errors.append(f"{label}.format_coverage references non-current version {vid!r}")
    for fr_id in valid_ids - referenced:
        errors.append(f"format requirement {fr_id} is not referenced by Coverage")
    return errors


def fr_ids_for_requirement(analysis: Mapping[str, Any], requirement_id: str) -> list[str]:
    """Return FR ids belonging to one atomic instruction requirement."""
    return [
        str(fr.get("format_requirement_id"))
        for fr in _fr_rows(analysis)
        if str(fr.get("requirement_id")) == requirement_id
    ]

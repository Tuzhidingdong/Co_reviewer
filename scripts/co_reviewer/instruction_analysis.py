# -*- coding: utf-8 -*-
"""Instruction-understanding record: validation and deterministic transfer only.

The semantic judgement (action/polarity) is made by the instruction-understanding
Agent from the full original instruction.  Python NEVER guesses polarity from
keywords; it validates the record against the immutable source and transfers
the validated values into Coverage / Plan.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "1.0"
ACTIONS = frozenset({"insert", "replace", "delete", "format", "insert_paragraph"})
POLARITIES = frozenset({
    "affirmative", "negative", "conditional", "unadopted", "ambiguous",
    # legacy V2 values retained for backward compatibility
    "positive", "accepted", "prohibited", "rejected", "not_adopted", "unknown",
})
INSERT_ACTIONS = frozenset({"insert", "insert_paragraph"})
AFFIRMATIVE = frozenset({"affirmative", "positive", "accepted"})
_LOCATOR_RE = re.compile(r"^L(\d+)[–\-]L(\d+)$")


def analysis_sha256(analysis: Mapping[str, Any]) -> str:
    data = {k: v for k, v in analysis.items() if k != "analysis_sha256"}
    return hashlib.sha256(
        __import__("json").dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _rows(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = analysis.get("analyses", []) if isinstance(analysis, Mapping) else []
    return [row for row in rows if isinstance(row, Mapping)]


def validate_instruction_analysis(analysis: Mapping[str, Any]) -> list[str]:
    """Validate structure, provenance and verbatim source alignment."""
    errors: list[str] = []
    if not isinstance(analysis, Mapping):
        return ["instruction_analysis must be an object"]
    if analysis.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if analysis.get("analysis_sha256") != analysis_sha256(analysis):
        errors.append("analysis_sha256 mismatch")
    src = analysis.get("source_instruction")
    if not isinstance(src, Mapping) or not isinstance(src.get("file"), str) or not isinstance(src.get("sha256"), str):
        return errors + ["source_instruction.file/sha256 required"]
    path = Path(src["file"]).expanduser()
    if not path.is_absolute() or not path.is_file():
        return errors + [f"source_instruction.file not readable: {path}"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != src["sha256"]:
        errors.append("source_instruction.sha256 mismatch")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    seen: set[str] = set()
    for index, row in enumerate(_rows(analysis)):
        label = f"analyses[{index}]"
        req_id = str(row.get("requirement_id", ""))
        if not req_id or req_id in seen:
            errors.append(f"{label}.requirement_id missing or duplicated")
        seen.add(req_id)
        action = str(row.get("instruction_action", "")).strip().lower()
        polarity = str(row.get("instruction_polarity", "")).strip().lower()
        if action not in ACTIONS:
            errors.append(f"{label}.instruction_action invalid: {action!r}")
        if polarity not in POLARITIES:
            errors.append(f"{label}.instruction_polarity invalid: {polarity!r}")
        if not str(row.get("instruction_object", "")).strip():
            errors.append(f"{label}.instruction_object required")
        start, end = row.get("source_line_start"), row.get("source_line_end")
        locator = str(row.get("source_locator", ""))
        match = _LOCATOR_RE.match(locator)
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            errors.append(f"{label}.source lines invalid")
            continue
        if end > len(lines) or not match or (int(match.group(1)), int(match.group(2))) != (start, end):
            errors.append(f"{label}.source_locator inconsistent with lines")
            continue
        excerpt = row.get("instruction_excerpt")
        expected = "\n".join(lines[start - 1:end])
        if not isinstance(excerpt, str) or excerpt.rstrip("\n") != expected.rstrip("\n"):
            errors.append(f"{label}.instruction_excerpt differs from source lines L{start}-L{end}")
    return errors


def crosscheck_coverage(coverage: Mapping[str, Any], analysis: Mapping[str, Any]) -> list[str]:
    """Coverage must carry exactly the Agent's validated judgement."""
    errors: list[str] = []
    by_id = {str(r.get("requirement_id")): r for r in _rows(analysis)}
    cov_rows = coverage.get("requirements", []) if isinstance(coverage, Mapping) else []
    for row in cov_rows if isinstance(cov_rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        req_id = str(row.get("requirement_id", ""))
        source = by_id.get(req_id)
        if source is None:
            errors.append(f"coverage requirement {req_id} has no instruction_analysis row")
            continue
        for field in ("instruction_action", "instruction_polarity", "instruction_object",
                      "source_locator", "instruction_excerpt", "source_line_start", "source_line_end"):
            if row.get(field) != source.get(field):
                errors.append(f"coverage requirement {req_id} field {field} differs from instruction_analysis")
    for req_id in by_id:
        if not any(isinstance(r, Mapping) and r.get("requirement_id") == req_id for r in cov_rows if isinstance(cov_rows, list)):
            errors.append(f"instruction_analysis row {req_id} missing from Coverage")
    return errors


def crosscheck_plan(plan: Mapping[str, Any], analysis: Mapping[str, Any]) -> list[str]:
    """Plan authorization_source must equal the Agent's validated judgement."""
    errors: list[str] = []
    by_id = {str(r.get("requirement_id")): r for r in _rows(analysis)}
    items = plan.get("revision_items", []) if isinstance(plan, Mapping) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        for index, target in enumerate(item.get("targets", []) if isinstance(item.get("targets"), list) else []):
            if not isinstance(target, Mapping) or target.get("action") != "insert_paragraph":
                continue
            auth = target.get("authorized_change")
            source = auth.get("authorization_source") if isinstance(auth, Mapping) else None
            if not isinstance(source, Mapping):
                errors.append(f"{item.get('id')}[{index}] missing authorization_source")
                continue
            req_id = str(source.get("requirement_id", ""))
            expected = by_id.get(req_id)
            if expected is None:
                errors.append(f"{item.get('id')}[{index}] requirement {req_id} absent from instruction_analysis")
                continue
            for field in ("instruction_action", "instruction_polarity", "instruction_object",
                          "source_locator", "instruction_excerpt"):
                if source.get(field) != expected.get(field):
                    errors.append(f"{item.get('id')}[{index}] {field} differs from instruction_analysis")
    return errors


def analysis_to_coverage(coverage: Mapping[str, Any], analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Deterministically copy validated Agent judgement into Coverage requirements."""
    errors = validate_instruction_analysis(analysis)
    if errors:
        raise ValueError("instruction_analysis invalid:\n- " + "\n- ".join(errors))
    by_id = {str(r.get("requirement_id")): r for r in _rows(analysis)}
    result = dict(coverage)
    rows: list[dict[str, Any]] = []
    for row in coverage.get("requirements", []) if isinstance(coverage, Mapping) else []:
        item = dict(row) if isinstance(row, Mapping) else {}
        source = by_id.get(str(item.get("requirement_id", "")))
        if source is not None:
            for field in ("instruction_action", "instruction_polarity", "instruction_object",
                          "source_locator", "instruction_excerpt", "source_line_start", "source_line_end"):
                item[field] = source.get(field)
        rows.append(item)
    result["requirements"] = rows
    return result

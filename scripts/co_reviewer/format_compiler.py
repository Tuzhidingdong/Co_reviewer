# -*- coding: utf-8 -*-
"""Deterministic format compiler (Batch 3).

Consumes authoritative Format Requirements (Batch 1), Run safety analysis
(Batch 2) and the existing Plan, then produces a safe replacement_format_map,
explicit Pass-2 format_operations and an auditable trace.  This module never
writes a DOCX and never grants authorization.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from .document_engine import NS, parse_xml, run_plain_text, visible_text
from .format_requirements import semantic_fingerprint, validate_format_requirements
from .run_format_analysis import ANALYSIS_VERSION, SAFE_BASE_CLASSES, canonical_json, validate_run_format_analysis

COMPILER_VERSION = "format-compiler/1.0"
TRACE_SCHEMA_VERSION = "1.0"


def has_format_requirements(analysis: Mapping[str, Any], coverage: Mapping[str, Any]) -> bool:
    """True when either side carries FR data; normal Plans stay untouched otherwise."""
    raw_fr = analysis.get("format_requirements") if isinstance(analysis, Mapping) else None
    fr_rows = any(isinstance(row, Mapping) and row.get("format_requirement_id")
                  for row in raw_fr if isinstance(raw_fr, list)) if isinstance(raw_fr, list) else False
    rows = coverage.get("requirements") if isinstance(coverage, Mapping) else None
    referenced = any(
        isinstance(row, Mapping) and bool(row.get("format_requirement_ids"))
        for row in rows if isinstance(rows, list)
    ) if isinstance(rows, list) else False
    return bool(fr_rows or referenced)


def trace_sha256(trace: Mapping[str, Any]) -> str:
    data = {k: v for k, v in trace.items() if k not in {"compile_fingerprint", "sha256"}}
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def validate_compile_trace(trace: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(trace, Mapping) or trace.get("schema_version") != TRACE_SCHEMA_VERSION:
        return ["compile trace schema_version invalid"]
    if trace.get("status") not in {"COMPILED", "BLOCKED"}:
        errors.append("compile trace status invalid")
    if trace.get("compile_fingerprint") != trace_sha256(trace):
        errors.append("compile trace fingerprint mismatch")
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return errors
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "format-compile-trace.schema.json"
    if schema_path.is_file():
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors.extend(
            "compile trace schema " + "/".join(str(p) for p in e.absolute_path) + ": " + e.message
            for e in sorted(validator.iter_errors(trace), key=lambda item: list(item.absolute_path))
        )
    return errors


def _blocked(blockers: list[str], partial: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "status": "BLOCKED",
        "blockers": blockers,
        "results": [],
        "unresolved_requirement_ids": [],
    }
    if partial:
        result.update(partial)
    result["compile_fingerprint"] = trace_sha256(result)
    return result


def _run_spans(paragraph):
    """Map paragraph characters to Run indices using Batch 2's numbering."""
    spans, cursor = [], 0
    for index, run in enumerate(paragraph.findall(".//w:r", NS)):
        text = run_plain_text(run)
        if not text:
            continue
        spans.append({"start": cursor, "end": cursor + len(text), "run": run,
                      "run_index": index, "text": text})
        cursor += len(text)
    return spans


def _covering_spans(spans, start: int, end: int) -> list[dict[str, Any]]:
    return [span for span in spans if span["start"] < end and span["end"] > start]


def _safe_candidate(rows: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    safe = [r for r in rows if r.get("safety_classification") in SAFE_BASE_CLASSES and r.get("context_ready") is True]
    if not safe:
        return None
    neutral = [r for r in safe if r.get("safety_classification") == "SAFE_NEUTRAL"]
    pool = neutral if neutral else safe
    return sorted(pool, key=lambda r: (int(r.get("run", {}).get("run_index", 0)), str(r.get("run", {}).get("run_id", ""))))[0]


def _strip_properties(row: Mapping[str, Any]) -> set[str]:
    return {
        d.get("property", "") for d in row.get("property_decisions", [])
        if isinstance(d, Mapping) and d.get("decision") == "STRIP" and d.get("effective") is not None
    }


def _blocked_properties(row: Mapping[str, Any]) -> set[str]:
    return {
        d.get("property", "") for d in row.get("property_decisions", [])
        if isinstance(d, Mapping) and d.get("decision") == "BLOCKED_IF_UNKNOWN"
    }


def _choose_source_offset(spans, rows_by_index, p_idx: int, start: int, end: int, find: str) -> int | None:
    """Choose one deterministic safe source offset inside [start,end) of `find`."""
    candidates: list[int] = []
    for span in _covering_spans(spans, start, end):
        row = rows_by_index.get((p_idx, span["run_index"]))
        if row is None or row.get("safety_classification") not in SAFE_BASE_CLASSES:
            continue
        if _strip_properties(row) or _blocked_properties(row):
            continue
        lo = max(start, span["start"])
        hi = min(end, span["end"])
        if lo < hi:
            candidates.append(lo - 0)
    if not candidates:
        return None
    return min(candidates)


def _build_map(find: str, replace: str, spans, rows_by_index, p_idx: int, paragraph_offset: int) -> tuple[list[dict[str, int]], list[str]]:
    if not find or not replace:
        return [], ["empty find or replacement text"]
    offsets: list[int] = []
    blockers: list[str] = []
    matcher = difflib.SequenceMatcher(None, find, replace, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            continue
        if tag == "equal":
            for k in range(i2 - i1):
                src = paragraph_offset + i1 + k
                span = next((s for s in spans if s["start"] <= src < s["end"]), None)
                row = rows_by_index.get((p_idx, span["run_index"])) if span else None
                if row is None or row.get("safety_classification") not in SAFE_BASE_CLASSES or _strip_properties(row) or _blocked_properties(row):
                    blockers.append(f"unchanged source offset {src} is not a safe base run")
                    continue
                offsets.append(src - paragraph_offset)
        else:
            # Insertions/replacements inherit from the last safe character of
            # the source anchor (never from a paragraph offset outside `find`).
            if i1 == i2:
                lo, hi = paragraph_offset + i1 - 1, paragraph_offset + i1
            else:
                lo, hi = paragraph_offset + i1, paragraph_offset + i2
            chosen = _choose_source_offset(spans, rows_by_index, p_idx, lo, hi, find)
            if chosen is None:
                blockers.append(f"no safe base run for replacement span [{i1},{i2})")
                continue
            chosen = min(chosen, paragraph_offset + len(find) - 1)
            offsets.extend([chosen - paragraph_offset] * (j2 - j1))
    if blockers or len(offsets) != len(replace):
        return [], blockers or ["replacement map length mismatch"]
    segments, seg_start, current = [], 0, offsets[0]
    for index in range(1, len(offsets) + 1):
        if index == len(offsets) or offsets[index] != current:
            segments.append({
                "replacement_start": seg_start, "replacement_end": index,
                "source_offset": current,
            })
            if index < len(offsets):
                seg_start, current = index, offsets[index]
    return segments, []


def _operations(fr: Mapping[str, Any], version: Mapping[str, Any], token_offset: int, replacement: str) -> list[dict[str, Any]]:
    token = str(version.get("token", ""))
    props = version.get("properties") if isinstance(version.get("properties"), Mapping) else {}
    ops = []
    for name in sorted(props):
        value = props[name]
        if value is None:
            continue
        operation_id = hashlib.sha256("|".join([
            str(fr.get("format_requirement_id", "")), str(version.get("version_id", "")),
            name, json.dumps(value, sort_keys=True, ensure_ascii=False),
            str(token_offset),
        ]).encode("utf-8")).hexdigest()[:24]
        ops.append({
            "operation_id": operation_id,
            "find": token,
            "properties": {name: value},
            "scope_find": replacement,
            "scope_offset": token_offset,
            "format_requirement_id": fr.get("format_requirement_id"),
            "format_requirement_version_id": version.get("version_id"),
        })
    return sorted(ops, key=lambda op: (op["scope_offset"], op["operation_id"]))


def compile_format_requirements(
    plan: Mapping[str, Any],
    analysis: Mapping[str, Any],
    coverage: Mapping[str, Any],
    run_analysis: Mapping[str, Any],
    *,
    source_docx: str | Path | None = None,
) -> dict[str, Any]:
    """Compile all Coverage-referenced FRs into map + Pass-2 + trace."""
    blockers: list[str] = []
    if not isinstance(plan, Mapping) or not isinstance(analysis, Mapping) or not isinstance(coverage, Mapping) or not isinstance(run_analysis, Mapping):
        return _blocked(["one or more compile inputs are not objects"])
    if run_analysis.get("schema_version") != "1.0" or run_analysis.get("analysis_version") != ANALYSIS_VERSION:
        blockers.append("Run Analysis schema/analysis version is incompatible")
    schema_errors = validate_run_format_analysis(run_analysis)
    if schema_errors:
        blockers.extend(schema_errors[:10])
    # Batch 1: verify FR integrity and resolve only the current version per FR.
    # current_versions() is keyed by atomic requirement id; the compiler consumes
    # Coverage's format_requirement_id references, so build that index explicitly.
    fr_integrity = validate_format_requirements(analysis)
    if fr_integrity:
        blockers.extend("FR integrity: " + message for message in fr_integrity[:10])
    current: dict[str, dict[str, Any]] = {}
    raw_fr = analysis.get("format_requirements")
    for fr in raw_fr if isinstance(raw_fr, list) else []:
        if not isinstance(fr, Mapping):
            continue
        fr_id = str(fr.get("format_requirement_id", ""))
        current_id = str(fr.get("current_version_id", ""))
        versions = fr.get("versions")
        for version in versions if isinstance(versions, list) else []:
            if isinstance(version, Mapping) and str(version.get("version_id")) == current_id:
                if semantic_fingerprint(version) != version.get("fingerprint"):
                    blockers.append(f"FR fingerprint mismatch for {fr_id}")
                current[fr_id] = version
                break
    referenced: list[str] = []
    for row in coverage.get("requirements", []) if isinstance(coverage.get("requirements"), list) else []:
        if isinstance(row, Mapping):
            referenced.extend(row.get("format_requirement_ids") or [])
    referenced = sorted(set(referenced))
    if not referenced:
        return _blocked(["no Format Requirements referenced by Coverage"])
    missing = [fr_id for fr_id in referenced if fr_id not in {v.get("format_requirement_id") for v in current.values()}]
    if missing:
        blockers.append("Coverage references missing or non-current FR ids: " + ", ".join(missing))

    input_files = {
        str(Path(str(t.get("input_file", ""))).expanduser().resolve()): t.get("source_sha256")
        for item in plan.get("revision_items", [])
        if isinstance(item, Mapping)
        for t in item.get("targets", [])
        if isinstance(t, Mapping)
    }
    if len(input_files) != 1:
        blockers.append("format compilation requires exactly one source DOCX in the plan")
        docx_path = Path(str(source_docx)).expanduser().resolve() if source_docx else None
    else:
        docx_path = Path(next(iter(input_files)))
    analysis_doc = run_analysis.get("source_docx")
    if docx_path is not None and analysis_doc and Path(str(analysis_doc)).expanduser().resolve() != docx_path:
        blockers.append("Run Analysis source DOCX differs from the Plan source DOCX")
    if docx_path is None or not docx_path.is_file():
        blockers.append("source DOCX is missing")
        return _blocked(blockers)
    actual_sha = hashlib.sha256(docx_path.read_bytes()).hexdigest()
    expected_sha = next(iter(input_files.values())) if input_files else None
    if expected_sha and actual_sha != expected_sha:
        blockers.append("Plan source DOCX SHA-256 mismatch")
    if run_analysis.get("source_docx_sha256") != actual_sha:
        blockers.append("Run Analysis source DOCX SHA-256 mismatch")
    if blockers:
        return _blocked(blockers)

    with zipfile.ZipFile(docx_path) as archive:
        root = parse_xml(archive.read("word/document.xml"))
    paragraphs = root.findall(".//w:body//w:p", NS)
    rows_by_index = {
        (int(r.get("run", {}).get("p_idx", -1)), int(r.get("run", {}).get("run_index", -1))): r
        for r in run_analysis.get("runs", [])
        if isinstance(r, Mapping) and isinstance(r.get("run"), Mapping)
    }
    results: list[dict[str, Any]] = []
    pending: list[str] = []
    for fr_id in referenced:
        version = current[fr_id]
        fr_row = next((f for f in analysis.get("format_requirements", []) if isinstance(f, Mapping) and f.get("format_requirement_id") == fr_id), {})
        target_ref = version.get("target_ref") if isinstance(version.get("target_ref"), Mapping) else {}
        part = str(target_ref.get("part", "word/document.xml"))
        if part != "word/document.xml":
            pending.append(fr_id)
            continue
        p_idx = target_ref.get("p_idx")
        if not isinstance(p_idx, int) or not 0 <= p_idx < len(paragraphs):
            pending.append(fr_id)
            continue
        revision_item_id = target_ref.get("revision_item_id")
        target_index = target_ref.get("target_index")
        target = None
        for item in plan.get("revision_items", []):
            if isinstance(item, Mapping) and item.get("id") == revision_item_id:
                targets = item.get("targets", [])
                if isinstance(target_index, int) and 0 <= target_index < len(targets):
                    target = targets[target_index]
                break
        if not isinstance(target, Mapping):
            pending.append(fr_id)
            continue
        find = str(target.get("find", ""))
        replacement = str(target.get("replace", ""))
        scope = version.get("scope") if isinstance(version.get("scope"), Mapping) else {}
        token = str(version.get("token", ""))
        if not token or token not in replacement:
            pending.append(fr_id)
            continue
        token_offset = int(scope.get("start", replacement.find(token)))
        if replacement[token_offset:token_offset + len(token)] != token:
            pending.append(fr_id)
            continue
        if find.count(token) != 1:
            pending.append(fr_id)
            continue
        paragraph = paragraphs[p_idx]
        if visible_text(paragraph).count(find) != 1:
            pending.append(fr_id)
            continue
        spans = _run_spans(paragraph)
        para_text = visible_text(paragraph)
        find_at = para_text.find(find)
        if find_at < 0:
            pending.append(fr_id)
            continue
        source_offset = find_at + find.find(token)
        covering = _covering_spans(spans, source_offset, source_offset + len(token))
        analysis_rows = [rows_by_index.get((p_idx, s["run_index"])) for s in covering]
        analysis_rows = [r for r in analysis_rows if isinstance(r, Mapping)]
        if len(analysis_rows) != len(covering):
            pending.append(fr_id)
            continue
        structures = {json.dumps(r.get("structure_context") or {}, sort_keys=True) for r in analysis_rows}
        doc_structures = set()
        for span in covering:
            structure = {
                "in_table": any(a.tag == "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tbl" for a in paragraph.iterancestors()),
                "in_numbering": paragraph.find("w:pPr/w:numPr", NS) is not None,
            }
            doc_structures.add(json.dumps(structure, sort_keys=True))
        if structures != doc_structures:
            pending.append(fr_id)
            continue
        base_row = _safe_candidate(analysis_rows)
        if base_row is None:
            pending.append(fr_id)
            continue
        segments, map_blockers = _build_map(find, replacement, spans, rows_by_index, p_idx, find_at)
        if map_blockers or not segments:
            pending.append(fr_id)
            continue
        ops = _operations(fr_row, version, token_offset, replacement)
        if not ops:
            pending.append(fr_id)
            continue
        targets_of_item = item.get("targets", []) if isinstance(item, Mapping) else []
        fmt_index = target_index + 1
        fmt_target = targets_of_item[fmt_index] if fmt_index < len(targets_of_item) and isinstance(targets_of_item[fmt_index], Mapping) else None
        expected_props = {
            name: value
            for op in ops if isinstance(op.get("properties"), Mapping)
            for name, value in op["properties"].items()
        }
        for existing_op in (fmt_target or {}).get("format_operations", []) or []:
            if not isinstance(existing_op, Mapping) or existing_op.get("find") != token:
                continue
            if existing_op.get("scope_offset") not in (None, token_offset):
                continue
            existing_props = existing_op.get("properties")
            if not isinstance(existing_props, Mapping) or dict(existing_props) != expected_props:
                # A legacy op without FR reference, or with different properties,
                # must not be silently kept or silently overwritten.
                pending.append(fr_id)
                break
        if fr_id in pending:
            continue
        results.append({
            "format_requirement_id": fr_id,
            "version_id": version.get("version_id"),
            "fingerprint": version.get("fingerprint"),
            "source_excerpt": version.get("source_excerpt"),
            "part": part,
            "p_idx": p_idx,
            "revision_item_id": revision_item_id,
            "target_index": target_index,
            "find": find,
            "replace": replacement,
            "token": token,
            "source_offset": source_offset,
            "run_ids": [r.get("run", {}).get("run_id") for r in analysis_rows],
            "base_run_id": base_row.get("run", {}).get("run_id"),
            "base_selection_reason": "SAFE_NEUTRAL preferred; deterministic by run_index" if base_row.get("safety_classification") == "SAFE_NEUTRAL" else "compatible context; deterministic by run_index",
            "safety_classifications": [r.get("safety_classification") for r in analysis_rows],
            "property_decisions": {d.get("property"): d.get("decision") for r in analysis_rows for d in r.get("property_decisions", []) if isinstance(d, Mapping)},
            "replacement_format_map": segments,
            "format_operations": ops,
            "status": "COMPILED",
        })
    if pending:
        blockers.append("Format Requirements could not be compiled: " + ", ".join(sorted(set(pending))))
    trace = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "status": "COMPILED" if not blockers else "BLOCKED",
        "source_docx": str(docx_path),
        "source_docx_sha256": actual_sha,
        "input_validation": {
            "fr_integrity_errors": fr_integrity,
            "run_analysis_schema_errors": schema_errors,
            "coverage_fr_ids": referenced,
            "plan_docx_sha256": expected_sha,
            "actual_docx_sha256": actual_sha,
            "run_analysis_docx_sha256": run_analysis.get("source_docx_sha256"),
        },
        "results": sorted(results, key=lambda row: row["format_requirement_id"]),
        "blockers": blockers,
        "unresolved_requirement_ids": sorted(set(pending)),
    }
    trace["compile_fingerprint"] = trace_sha256(trace)
    return trace


def apply_compiled_formats(plan: dict[str, Any], trace: Mapping[str, Any]) -> dict[str, Any]:
    """Merge compiled maps/ops into a pending Plan without dropping existing fields."""
    if trace.get("status") != "COMPILED":
        return plan
    for entry in trace.get("results", []):
        item = next((i for i in plan.get("revision_items", []) if i.get("id") == entry.get("revision_item_id")), None)
        if not isinstance(item, Mapping):
            continue
        targets = item.get("targets", [])
        index = entry.get("target_index")
        if not isinstance(index, int) or index >= len(targets):
            continue
        target = targets[index]
        target["replacement_format_map"] = entry["replacement_format_map"]
        fr_id = entry["format_requirement_id"]
        ids = list(target.get("format_requirement_ids") or [])
        if fr_id not in ids:
            ids.append(fr_id)
        target["format_requirement_ids"] = ids
        fmt_index = index + 1
        if fmt_index >= len(targets):
            targets.append({
                "target_id": f"{entry.get('revision_item_id')}-{fr_id}-format",
                "document": target.get("document"),
                "input_file": target.get("input_file"),
                "output_file": target.get("output_file"),
                "part": target.get("part", "word/document.xml"),
                "p_idx": target.get("p_idx"),
                "find": entry["replace"],
                "replace": entry["replace"],
                "authorized_change": {"intent": "Explicit Pass-2 operations compiled from Format Requirements"},
                "format_authorization": {"intent": "Explicit Pass-2 operations compiled from Format Requirements"},
                "instruction_requirement_ids": list(target.get("instruction_requirement_ids") or []),
                # Link the format-only target to its text target so the existing
                # Apply/Verify scope machinery authorizes format overlays on the
                # inserted characters instead of treating them as format leakage.
                "applies_to_target_index": index,
                "format_operations": [],
            })
        if fmt_index < len(targets) and isinstance(targets[fmt_index], Mapping) and targets[fmt_index].get("action") in {None, "replace"}:
            fmt_target = targets[fmt_index]
            existing = {str(op.get("find")) + "|" + json.dumps(op.get("properties"), sort_keys=True): op
                        for op in fmt_target.get("format_operations", []) if isinstance(op, Mapping)}
            merged: list[dict[str, Any]] = []
            for op in entry["format_operations"]:
                key = str(op.get("find")) + "|" + json.dumps(op.get("properties"), sort_keys=True)
                existing_op = existing.pop(key, None)
                if existing_op is not None:
                    # Verified existing operation: preserve every original field.
                    merged.append(dict(existing_op))
                    continue
                # Plan format_operations only accept the V3 contract fields; the
                # full FR/version/trace linkage lives in format_compilation.
                merged.append({
                    "find": op["find"],
                    "properties": dict(op["properties"]),
                    "scope_find": op.get("scope_find"),
                    "scope_offset": op.get("scope_offset"),
                    "source_occurrence_id": f"{fr_id}:{entry.get('version_id')}",
                })
            merged.extend(existing.values())  # legacy ops preserved, never silently deleted
            merged.sort(key=lambda op: (int(op.get("scope_offset", -1)), str(op.get("source_occurrence_id", ""))))
            fmt_target["format_operations"] = merged
            fr_ids = list(fmt_target.get("format_requirement_ids") or [])
            if fr_id not in fr_ids:
                fr_ids.append(fr_id)
            fmt_target["format_requirement_ids"] = sorted(fr_ids)
    plan["format_compilation"] = dict(trace)
    return plan

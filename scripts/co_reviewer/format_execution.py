# -*- coding: utf-8 -*-
"""Format execution integrity (Batch 4).

Completes the FR -> Compile -> Preflight -> Apply -> Verify -> Final Gate chain.
This module reuses the existing V3 Apply/Verify/Gate implementations; it never
builds a parallel authorization or style engine and never touches real documents.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from lxml import etree as ET

from .document_engine import NS, parse_xml, run_plain_text, visible_text
from .format_requirements import validate_format_requirements
from .run_format_analysis import (
    ANALYSIS_VERSION, SAFE_BASE_CLASSES, canonical_json,
    validate_run_format_analysis,
)
from .v232_contract import effective_run_properties, qn

EXECUTION_RULE_VERSION = "format-execution/1.0"
RECOMPUTE_RULE_VERSION = "recompute/1.0"
TARGET_ID_RULE_VERSION = "target-occurrence/1.0"
GATE_RULE_VERSION = "format-gate/1.0"

_OFF_VALUES = frozenset({"0", "false", "off"})
_BOOL_PROPERTIES = frozenset({
    "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "vanish",
})
_RECOMPUTE_PROPERTIES = frozenset({"rFonts", "sz", "szCs", "color"})
_VERIFY_PROPERTIES = (
    "vertAlign", "b", "bCs", "i", "iCs", "rFonts", "sz", "szCs", "color", "u",
)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).expanduser().read_bytes()).hexdigest()


def _norm(name: str, raw: Any) -> Any:
    if raw is None:
        return None
    if name in _BOOL_PROPERTIES:
        if isinstance(raw, Mapping):
            value = raw.get(qn("val"))
            return True if value is None else str(value).casefold() not in _OFF_VALUES
        return bool(raw)
    if isinstance(raw, Mapping):
        value = raw.get(qn("val"))
        if name == "vertAlign":
            return str(value) if value is not None else None
        if name in {"sz", "szCs"}:
            try:
                return int(str(value))
            except (TypeError, ValueError):
                return None
        if name == "color":
            return str(value) if value is not None else None
        if name == "rFonts":
            return {ET.QName(k).localname: str(v) for k, v in raw.items() if v is not None}
        return {ET.QName(k).localname: str(v) for k, v in raw.items() if v is not None}
    return raw


def _source(label: str | None) -> str:
    if not label:
        return "UNKNOWN"
    if label == "directRun" or label.startswith("paragraphDirect"):
        return "DIRECT_RUN"
    if label.startswith("characterStyle"):
        return "CHARACTER_STYLE"
    if label.startswith("paragraphStyle"):
        return "PARAGRAPH_STYLE"
    if label in {"tableStyle", "numberingLevel"}:
        return "TABLE_OR_NUMBERING_CONTEXT"
    if label == "docDefaults":
        return "DOC_DEFAULTS"
    if label.startswith("theme:"):
        return "THEME"
    return "UNKNOWN"


def _docx_parts(docx_path: str | Path) -> dict[str, bytes]:
    with zipfile.ZipFile(Path(docx_path).expanduser()) as archive:
        names = set(archive.namelist())
        return {
            "document_xml": archive.read("word/document.xml"),
            "styles_xml": archive.read("word/styles.xml") if "word/styles.xml" in names else None,
            "theme_xml": archive.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in names else None,
        }


def _paragraph_at(document_xml: bytes, p_idx: int):
    root = parse_xml(document_xml)
    paragraphs = root.findall(".//w:body//w:p", NS)
    if not 0 <= p_idx < len(paragraphs):
        raise IndexError(f"paragraph {p_idx} not present")
    return paragraphs[p_idx]


def _paragraph_context(paragraph) -> dict[str, Any]:
    p_pr = paragraph.find("w:pPr", NS)
    p_style = p_pr.find("w:pStyle", NS) if p_pr is not None else None
    p_rpr = p_pr.find("w:rPr", NS) if p_pr is not None else None
    return {
        "paragraph_style_id": p_style.get(qn("val")) if p_style is not None else None,
        "paragraph_rpr": p_rpr,
        "structure_context": {
            "in_table": any(a.tag == qn("tbl") for a in paragraph.iterancestors()),
            "in_numbering": p_pr is not None and p_pr.find("w:numPr", NS) is not None,
        },
    }


# ---------------------------------------------------------------------------
# 4.1 RECOMPUTE — deterministic final values before Apply
# ---------------------------------------------------------------------------
def recompute_properties(trace: dict[str, Any], *, docx_path: str | Path) -> dict[str, Any]:
    """Resolve every RECOMPUTE decision to a concrete value in the trace.

    Input is the target context (paragraph/table/numbering style chain, theme,
    docDefaults) with the unsafe base Run's direct rPr and character style
    excluded.  Unresolvable values BLOCK the compilation before Apply.
    """
    result = json.loads(json.dumps(trace, ensure_ascii=False))
    blockers: list[str] = []
    parts = _docx_parts(docx_path)
    for entry in result.get("results", []):
        decisions = entry.get("property_decisions") or {}
        needed = [name for name, decision in decisions.items() if decision == "RECOMPUTE"]
        record: dict[str, Any] = {
            "rule_version": RECOMPUTE_RULE_VERSION,
            "status": "NOT_REQUIRED" if not needed else "RESOLVED",
            "context": {},
            "properties": {},
        }
        if not needed:
            entry["recompute"] = record
            continue
        try:
            paragraph = _paragraph_at(parts["document_xml"], int(entry.get("p_idx", -1)))
            context = _paragraph_context(paragraph)
        except (IndexError, TypeError, ValueError) as exc:
            record["status"] = "BLOCKED"
            record["reason"] = f"target paragraph unavailable: {exc}"
            entry["recompute"] = record
            blockers.append(f"{entry.get('format_requirement_id')}: recompute context unavailable")
            continue
        record["context"] = context["structure_context"]
        record["paragraph_style_id"] = context["paragraph_style_id"]
        try:
            resolved = effective_run_properties(
                parts["document_xml"], parts["styles_xml"],
                context["paragraph_style_id"], None, None,
                paragraph_rpr=context["paragraph_rpr"], theme_xml=parts["theme_xml"],
            )
        except Exception as exc:  # fail closed
            record["status"] = "BLOCKED"
            record["reason"] = f"target context resolution failed: {type(exc).__name__}: {exc}"
            entry["recompute"] = record
            blockers.append(f"{entry.get('format_requirement_id')}: recompute context unresolved")
            continue
        properties = resolved.get("properties") or {}
        provenance = resolved.get("provenance") or {}
        for name in sorted(needed):
            if name not in _RECOMPUTE_PROPERTIES:
                record["status"] = "BLOCKED"
                record["properties"][name] = {"value": None, "source": "UNKNOWN",
                                               "reason": "no deterministic recompute rule"}
                blockers.append(
                    f"{entry.get('format_requirement_id')}: no recompute rule for {name}"
                )
                continue
            value = _norm(name, properties.get(name))
            if value is None:
                record["status"] = "BLOCKED"
                record["properties"][name] = {"value": None, "source": "UNKNOWN",
                                               "reason": "target context value missing"}
                blockers.append(
                    f"{entry.get('format_requirement_id')}: recompute value missing for {name}"
                )
                continue
            record["properties"][name] = {
                "value": value, "source": _source(provenance.get(name)),
            }
        entry["recompute"] = record
        if record["status"] == "BLOCKED":
            continue
        # Materialise recomputed values as explicit operations inside the same
        # FR scope; no value is left undecided for Apply.
        existing = {
            next(iter(op.get("properties") or {})): op
            for op in entry.get("format_operations", [])
            if isinstance(op, Mapping) and isinstance(op.get("properties"), Mapping)
            and len(op["properties"]) == 1
        }
        for name, info in record["properties"].items():
            if name in existing:
                record["properties"][name]["operation_id"] = existing[name].get("operation_id")
                continue
            digest = hashlib.sha256("|".join([
                str(entry.get("format_requirement_id", "")), str(entry.get("version_id", "")),
                name, json.dumps(info["value"], sort_keys=True, ensure_ascii=False),
                RECOMPUTE_RULE_VERSION,
            ]).encode("utf-8")).hexdigest()[:24]
            replacement = str(entry.get("replace", ""))
            token = str(entry.get("token", ""))
            scope_offset = replacement.find(token)
            if scope_offset < 0:
                scope_offset = 0
            op = {
                "operation_id": digest,
                "find": token,
                "properties": {name: info["value"]},
                "scope_find": replacement,
                "scope_offset": scope_offset,
                "format_requirement_id": entry.get("format_requirement_id"),
                "format_requirement_version_id": entry.get("version_id"),
                "recomputed": True,
            }
            entry.setdefault("format_operations", []).append(op)
            record["properties"][name]["operation_id"] = digest
        entry["format_operations"] = sorted(
            entry["format_operations"],
            key=lambda op: (int(op.get("scope_offset", 0)), str(op.get("operation_id", ""))),
        )
    result["recompute_rule_version"] = RECOMPUTE_RULE_VERSION
    result["recompute_blockers"] = blockers
    if blockers:
        result["status"] = "BLOCKED"
        result.setdefault("blockers", []).extend(blockers)
    # Fingerprint must cover the new deterministic content.
    from .format_compiler import trace_sha256
    result["compile_fingerprint"] = trace_sha256(result)
    return result


# ---------------------------------------------------------------------------
# 4.2 Target occurrence identity and multi-FR conflict detection
# ---------------------------------------------------------------------------
def target_occurrence_id(*, docx_sha256: str, part: str, p_idx: int,
                         source_start: int, source_end: int,
                         run_start: int, run_end: int,
                         find_text: str, structure: Mapping[str, Any]) -> str:
    payload = {
        "rule_version": TARGET_ID_RULE_VERSION,
        "docx_sha256": docx_sha256,
        "part": part, "p_idx": p_idx,
        "source_start": source_start, "source_end": source_end,
        "run_start": run_start, "run_end": run_end,
        "find": find_text,
        "structure": dict(structure),
    }
    return "to-" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def _run_spans(paragraph):
    spans, cursor = [], 0
    for index, run in enumerate(paragraph.findall(".//w:r", NS)):
        text = run_plain_text(run)
        if not text:
            continue
        spans.append({"run_index": index, "start": cursor, "end": cursor + len(text)})
        cursor += len(text)
    return spans


def assign_target_occurrences(trace: dict[str, Any], *, docx_path: str | Path) -> dict[str, Any]:
    result = json.loads(json.dumps(trace, ensure_ascii=False))
    docx_sha = sha256_file(docx_path)
    parts = _docx_parts(docx_path)
    for entry in result.get("results", []):
        try:
            paragraph = _paragraph_at(parts["document_xml"], int(entry.get("p_idx", -1)))
        except (IndexError, TypeError, ValueError):
            entry["target_occurrence_id"] = None
            continue
        find = str(entry.get("find", ""))
        text = visible_text(paragraph)
        if text.count(find) != 1:
            entry["target_occurrence_id"] = None
            continue
        find_at = text.find(find)
        token = str(entry.get("token", ""))
        token_at = find_at + find.find(token)
        spans = _run_spans(paragraph)
        covering = [s for s in spans if s["start"] < token_at + len(token) and s["end"] > token_at]
        if not covering:
            entry["target_occurrence_id"] = None
            continue
        context = _paragraph_context(paragraph)["structure_context"]
        entry["target_occurrence_id"] = target_occurrence_id(
            docx_sha256=docx_sha,
            part=str(entry.get("part", "word/document.xml")),
            p_idx=int(entry["p_idx"]),
            source_start=find_at, source_end=find_at + len(find),
            run_start=min(s["run_index"] for s in covering),
            run_end=max(s["run_index"] for s in covering),
            find_text=find, structure=context,
        )
        entry["target_occurrence"] = {
            "paragraph_offset": find_at, "token_offset": token_at,
            "run_indices": [s["run_index"] for s in covering],
            "structure_context": context,
        }
    from .format_compiler import trace_sha256
    result["target_id_rule_version"] = TARGET_ID_RULE_VERSION
    result["compile_fingerprint"] = trace_sha256(result)
    return result


def validate_target_occurrences(trace: Mapping[str, Any], *, docx_path: str | Path) -> list[str]:
    errors: list[str] = []
    expected = assign_target_occurrences(dict(trace), docx_path=docx_path)
    by_id = {entry.get("format_requirement_id"): entry for entry in expected.get("results", [])}
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in trace.get("results", []):
        fr_id = str(entry.get("format_requirement_id", ""))
        occurrence = entry.get("target_occurrence_id")
        if not occurrence:
            errors.append(f"{fr_id}: target_occurrence_id missing or ambiguous")
            continue
        recomputed = by_id.get(fr_id, {}).get("target_occurrence_id")
        if occurrence != recomputed:
            errors.append(f"{fr_id}: target_occurrence_id does not match current DOCX")
            continue
        groups.setdefault(occurrence, []).append(entry)
    for occurrence, entries in sorted(groups.items()):
        replacements = {str(e.get("replace", "")) for e in entries}
        if len(replacements) > 1:
            errors.append(
                f"target {occurrence}: multiple FRs declare different replacement text: "
                + ", ".join(sorted(replacements))
            )
        scopes = {canonical_json(e.get("scope") or {}) for e in entries}
        if len(scopes) > 1:
            errors.append(f"target {occurrence}: conflicting FR scopes")
        values: dict[str, set[str]] = {}
        for entry in entries:
            for op in entry.get("format_operations", []):
                if not isinstance(op, Mapping) or not isinstance(op.get("properties"), Mapping):
                    continue
                for name, value in op["properties"].items():
                    values.setdefault(str(name), set()).add(canonical_json(value))
        for name, alternatives in sorted(values.items()):
            if len(alternatives) > 1:
                errors.append(
                    f"target {occurrence}: conflicting format value for {name}: "
                    + ", ".join(sorted(alternatives))
                )
    return errors


# ---------------------------------------------------------------------------
# 5. Apply Preflight
# ---------------------------------------------------------------------------
def _current_fr_versions(analysis: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Map format_requirement_id -> current version (Coverage uses FR ids)."""
    out: dict[str, dict[str, Any]] = {}
    raw = analysis.get("format_requirements")
    for fr in raw if isinstance(raw, list) else []:
        if not isinstance(fr, Mapping):
            continue
        current_id = str(fr.get("current_version_id", ""))
        versions = fr.get("versions")
        for version in versions if isinstance(versions, list) else []:
            if isinstance(version, Mapping) and str(version.get("version_id")) == current_id:
                out[str(fr.get("format_requirement_id", ""))] = version
                break
    return out


def fr_completion_status(analysis: Mapping[str, Any], coverage: Mapping[str, Any],
                         trace: Mapping[str, Any]) -> dict[str, str]:
    current = _current_fr_versions(analysis)
    referenced: set[str] = set()
    for row in coverage.get("requirements", []) if isinstance(coverage.get("requirements"), list) else []:
        if isinstance(row, Mapping):
            referenced.update(row.get("format_requirement_ids") or [])
    compiled = {str(e.get("format_requirement_id")) for e in trace.get("results", [])}
    status: dict[str, str] = {}
    for fr_id in sorted(set(current) | referenced):
        if fr_id not in current:
            status[fr_id] = "MISSING_OR_STALE_FR"
        elif fr_id not in referenced:
            status[fr_id] = "UNCOVERED"
        elif fr_id not in compiled:
            status[fr_id] = "UNCOMPILED"
        elif trace.get("status") != "COMPILED":
            status[fr_id] = "COMPILE_BLOCKED"
        else:
            status[fr_id] = "COMPILED"
    return status


def format_apply_preflight(*, plan: Mapping[str, Any], analysis: Mapping[str, Any],
                           coverage: Mapping[str, Any], run_analysis: Mapping[str, Any],
                           trace: Mapping[str, Any], docx_path: str | Path,
                           authorization: Mapping[str, Any] | None = None,
                           analysis_file: str | Path | None = None,
                           coverage_file: str | Path | None = None,
                           plan_file: str | Path | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    docx = Path(docx_path).expanduser().resolve()
    if not docx.is_file():
        blockers.append(f"source DOCX missing: {docx}")
        return _preflight_result(blockers)
    actual_sha = sha256_file(docx)
    inputs = [t for item in plan.get("revision_items", []) if isinstance(item, Mapping)
              for t in item.get("targets", []) if isinstance(t, Mapping)]
    for target in inputs:
        expected = target.get("source_sha256")
        if expected and expected != actual_sha:
            blockers.append(f"Plan source SHA mismatch for {target.get('target_id')}")
        if str(Path(str(target.get('input_file', ''))).expanduser().resolve()) != str(docx):
            blockers.append(f"Plan input_file differs from selected DOCX: {target.get('input_file')}")
    if run_analysis.get("source_docx_sha256") != actual_sha:
        blockers.append("Run Analysis source DOCX SHA-256 mismatch")
    if trace.get("source_docx_sha256") != actual_sha:
        blockers.append("Format Compilation source DOCX SHA-256 mismatch")
    if run_analysis.get("analysis_version") != ANALYSIS_VERSION:
        blockers.append("Run Analysis rule version incompatible")
    schema_errors = validate_run_format_analysis(run_analysis)
    blockers.extend("Run Analysis schema: " + error for error in schema_errors[:10])
    blockers.extend("FR integrity: " + error for error in validate_format_requirements(analysis)[:10])
    from .format_compiler import trace_sha256, validate_compile_trace
    blockers.extend("Compile Trace: " + error for error in validate_compile_trace(trace))
    if trace.get("status") != "COMPILED":
        blockers.append("Format Compilation status is not COMPILED")
    status = fr_completion_status(analysis, coverage, trace)
    for fr_id, state in status.items():
        if state != "COMPILED":
            blockers.append(f"Format Requirement {fr_id}: {state}")
    if not status:
        blockers.append("no current Format Requirement was checked")

    rows_by_id = {
        str(row.get("run", {}).get("run_id")): row
        for row in run_analysis.get("runs", [])
        if isinstance(row, Mapping) and isinstance(row.get("run"), Mapping)
    }
    for entry in trace.get("results", []):
        fr_id = str(entry.get("format_requirement_id", ""))
        base = rows_by_id.get(str(entry.get("base_run_id")))
        if base is None:
            blockers.append(f"{fr_id}: base Run missing from Run Analysis")
        elif base.get("safety_classification") not in SAFE_BASE_CLASSES:
            blockers.append(
                f"{fr_id}: unsafe base Run {entry.get('base_run_id')} "
                f"({base.get('safety_classification')})"
            )
        recompute = entry.get("recompute") or {}
        if recompute.get("status") not in {"NOT_REQUIRED", "RESOLVED"}:
            blockers.append(f"{fr_id}: unresolved RECOMPUTE before Apply")
        for decision in base.get("property_decisions", []) if isinstance(base, Mapping) else []:
            if isinstance(decision, Mapping) and decision.get("decision") == "BLOCKED_IF_UNKNOWN":
                blockers.append(
                    f"{fr_id}: unresolved BLOCKED_IF_UNKNOWN property {decision.get('property')}"
                )
        ops = entry.get("format_operations") or []
        if not ops:
            blockers.append(f"{fr_id}: no Pass-2 format operation")
        for op in ops:
            if not isinstance(op, Mapping) or not op.get("format_requirement_id") or not op.get("format_requirement_version_id"):
                blockers.append(f"{fr_id}: format operation missing FR identity")
            if not isinstance(op.get("properties"), Mapping) or not op["properties"]:
                blockers.append(f"{fr_id}: format operation has no properties")
        map_rows = entry.get("replacement_format_map") or []
        if not isinstance(map_rows, list) or not map_rows:
            blockers.append(f"{fr_id}: replacement_format_map missing")
        cursor = 0
        for index, segment in enumerate(map_rows):
            if not isinstance(segment, Mapping):
                blockers.append(f"{fr_id}: map segment {index} invalid")
                continue
            if segment.get("replacement_start") != cursor or not isinstance(segment.get("replacement_end"), int):
                blockers.append(f"{fr_id}: map segment {index} not contiguous")
            cursor = segment.get("replacement_end", cursor)
        blocklist = {"superscript", "subscript"}
        for decision in base.get("property_decisions", []) if isinstance(base, Mapping) else []:
            if (isinstance(decision, Mapping) and decision.get("property") == "vertAlign"
                    and decision.get("decision") == "STRIP"
                    and decision.get("effective") in blocklist):
                blockers.append(f"{fr_id}: forbidden script format in base mapping")
    blockers.extend(validate_target_occurrences(trace, docx_path=docx))

    # Authorization is still owned by the existing mechanism.
    if plan.get("authorization_mode") == "instruction_based":
        if authorization is None:
            blockers.append("instruction_based plan requires an authorization receipt")
        elif coverage_file is None or plan_file is None:
            blockers.append("authorization check requires plan and Coverage file paths")
        else:
            try:
                from .instruction_coverage import validate_instruction_based_authorization
                blockers.extend(
                    "Authorization: " + message
                    for message in validate_instruction_based_authorization(
                        authorization=authorization, coverage=coverage, plan=plan,
                        plan_file=plan_file, coverage_file=coverage_file,
                        analysis_file=analysis_file,
                    )
                )
            except Exception as exc:  # fail closed
                blockers.append(f"Authorization validation error: {type(exc).__name__}: {exc}")
    elif plan.get("authorization_mode") == "author_approval":
        try:
            import co_reviewer_cli as cli
            blockers.extend(
                "Author approval: " + message
                for message in cli._validate_author_approval_record(plan)
            )
        except Exception as exc:
            blockers.append(f"Author approval validation error: {type(exc).__name__}: {exc}")
    else:
        blockers.append("Plan authorization mode is missing")
    return _preflight_result(blockers, actual_sha=actual_sha, status=status)


def _preflight_result(blockers: list[str], *, actual_sha: str | None = None,
                      status: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "rule_version": EXECUTION_RULE_VERSION,
        "status": "BLOCKED" if blockers else "PASSED",
        "passed": not blockers,
        "blockers": blockers,
        "docx_sha256": actual_sha,
        "fr_status": status or {},
    }


# ---------------------------------------------------------------------------
# 6. Apply execution record
# ---------------------------------------------------------------------------
def build_execution_record(*, preflight: Mapping[str, Any], trace: Mapping[str, Any],
                           apply_reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for result in trace.get("results", []):
        fr_id = str(result.get("format_requirement_id", ""))
        ops = []
        for op in result.get("format_operations", []):
            if not isinstance(op, Mapping):
                continue
            ops.append({
                "operation_id": op.get("operation_id"),
                "properties": dict(op.get("properties") or {}),
                "recomputed": bool(op.get("recomputed")),
            })
        entries.append({
            "format_requirement_id": fr_id,
            "version_id": result.get("version_id"),
            "fingerprint": result.get("fingerprint"),
            "target_occurrence_id": result.get("target_occurrence_id"),
            "find": result.get("find"), "replace": result.get("replace"),
            "token": result.get("token"),
            "replacement_format_map": result.get("replacement_format_map"),
            "format_operations": ops,
            "compile_fingerprint": trace.get("compile_fingerprint"),
            "execution_status": "APPLIED" if preflight.get("passed") else "BLOCKED",
        })
    return {
        "schema_version": "1.0",
        "rule_version": EXECUTION_RULE_VERSION,
        "preflight_status": preflight.get("status"),
        "preflight_blockers": preflight.get("blockers", []),
        "docx_sha256_input": preflight.get("docx_sha256"),
        "compile_fingerprint": trace.get("compile_fingerprint"),
        "apply_outputs": [
            {"output_file": report.get("output_file"), "output_sha256": report.get("output_sha256")}
            for report in apply_reports if isinstance(report, Mapping)
        ],
        "requirements": entries,
    }


# ---------------------------------------------------------------------------
# 7. Independent Verify against the actual applied DOCX
# ---------------------------------------------------------------------------
def _effective_value(parts: Mapping[str, bytes], paragraph, run) -> dict[str, Any]:
    context = _paragraph_context(paragraph)
    try:
        resolved = effective_run_properties(
            parts["document_xml"], parts["styles_xml"], context["paragraph_style_id"],
            None, run.find("w:rPr", NS), paragraph_rpr=context["paragraph_rpr"],
            theme_xml=parts["theme_xml"],
        )
    except Exception:
        return {}
    properties = resolved.get("properties") or {}
    provenance = resolved.get("provenance") or {}
    direct_rpr = run.find("w:rPr", NS)
    direct_map: dict[str, Any] = {}
    if direct_rpr is not None:
        for child in direct_rpr:
            local = ET.QName(child).localname
            if local not in {"rStyle", "rPrChange"}:
                direct_map[local] = dict(child.attrib)
    return {
        name: {
            "direct": _norm(name, direct_map.get(name)),
            "effective": _norm(name, properties.get(name)),
            "provenance": _source(provenance.get(name)),
        }
        for name in _VERIFY_PROPERTIES
    }


def _expected_properties(entry: Mapping[str, Any]) -> dict[str, Any]:
    expected: dict[str, Any] = {}
    for op in entry.get("format_operations", []):
        if not isinstance(op, Mapping) or not isinstance(op.get("properties"), Mapping):
            continue
        expected.update(op["properties"])
    return expected


def verify_format_execution(*, docx_path: str | Path, analysis: Mapping[str, Any],
                            trace: Mapping[str, Any], original_docx: str | Path | None = None,
                            execution_record: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Re-open the APPLIED DOCX and verify each FR's effective format."""
    docx = Path(docx_path).expanduser().resolve()
    results: list[dict[str, Any]] = []
    if not docx.is_file():
        return {"schema_version": "1.0", "rule_version": EXECUTION_RULE_VERSION,
                "passed": False, "results": [], "blockers": [f"applied DOCX missing: {docx}"]}
    parts = _docx_parts(docx)
    root = parse_xml(parts["document_xml"])
    paragraphs = root.findall(".//w:body//w:p", NS)
    entries = {str(e.get("format_requirement_id")): e for e in trace.get("results", [])}
    current = _current_fr_versions(analysis)
    for fr_id in sorted(current):
        version = current[fr_id]
        entry = entries.get(fr_id)
        if entry is None:
            results.append({"format_requirement_id": fr_id, "status": "UNRESOLVED",
                            "errors": ["FR was not compiled"]})
            continue
        expected = _expected_properties(entry)
        if not expected:
            results.append({"format_requirement_id": fr_id, "status": "BLOCKED",
                            "errors": ["no explicit format operations to verify"]})
            continue
        try:
            paragraph = paragraphs[int(entry.get("p_idx", -1))]
        except (IndexError, TypeError, ValueError):
            results.append({"format_requirement_id": fr_id, "status": "BLOCKED",
                            "errors": ["target paragraph missing in applied DOCX"]})
            continue
        text = visible_text(paragraph)
        token = str(entry.get("token", ""))
        positions = []
        cursor = 0
        while True:
            at = text.find(token, cursor)
            if at < 0:
                break
            positions.append(at)
            cursor = at + 1
        if len(positions) != 1:
            results.append({"format_requirement_id": fr_id, "status": "UNRESOLVED",
                            "errors": [f"token occurs {len(positions)} times in applied DOCX"]})
            continue
        spans, offset = [], 0
        for index, run in enumerate(paragraph.findall(".//w:r", NS)):
            value = run_plain_text(run)
            if not value:
                continue
            spans.append({"run_index": index, "start": offset, "end": offset + len(value), "run": run})
            offset += len(value)
        checks: list[dict[str, Any]] = []
        errors: list[str] = []
        start, end = positions[0], positions[0] + len(token)
        for position in range(start, end):
            span = next((s for s in spans if s["start"] <= position < s["end"]), None)
            if span is None:
                errors.append(f"character {position} has no source Run")
                continue
            effective = _effective_value(parts, paragraph, span["run"])
            for name, wanted in expected.items():
                actual = effective.get(name, {}).get("effective")
                matches = actual == wanted if wanted is not None else actual is None
                checks.append({
                    "position": position, "run_index": span["run_index"],
                    "property": name, "expected": wanted, "actual": actual,
                    "direct": effective.get(name, {}).get("direct"),
                    "provenance": effective.get(name, {}).get("provenance"),
                    "passed": bool(matches),
                })
                if not matches:
                    errors.append(
                        f"char {position}: {name} expected {wanted!r}, effective {actual!r}"
                    )
        non_target_ok = True
        if original_docx is not None and Path(original_docx).expanduser().is_file():
            original_parts = _docx_parts(original_docx)
            original_root = parse_xml(original_parts["document_xml"])
            original_paragraphs = original_root.findall(".//w:body//w:p", NS)
            if int(entry.get("p_idx", -1)) < len(original_paragraphs):
                before = visible_text(original_paragraphs[int(entry["p_idx"])])
                after = text
                find = str(entry.get("find", ""))
                replacement = str(entry.get("replace", ""))
                non_target_ok = after == before.replace(find, replacement, 1)
                if not non_target_ok:
                    errors.append("non-target text changed unexpectedly")
        status = "VERIFIED" if not errors else "FAILED"
        results.append({
            "format_requirement_id": fr_id,
            "version_id": version.get("version_id"),
            "fingerprint": version.get("fingerprint"),
            "target_occurrence_id": entry.get("target_occurrence_id"),
            "status": status,
            "expected": expected,
            "checks": checks,
            "errors": errors,
            "non_target_text_preserved": non_target_ok,
        })
    missing = sorted(set(current) - {r["format_requirement_id"] for r in results})
    blockers = [f"unverified Format Requirement {fr_id}" for fr_id in missing]
    if execution_record is not None:
        for row in execution_record.get("requirements", []):
            if row.get("execution_status") != "APPLIED":
                blockers.append(f"{row.get('format_requirement_id')}: not executed")
    return {
        "schema_version": "1.0",
        "rule_version": EXECUTION_RULE_VERSION,
        "verified_docx": str(docx),
        "verified_docx_sha256": sha256_file(docx),
        "passed": not blockers and all(r["status"] == "VERIFIED" for r in results),
        "results": results,
        "blockers": blockers,
    }


# ---------------------------------------------------------------------------
# 8. Final Gate guard: FR semantics cannot be removed by repairs
# ---------------------------------------------------------------------------
def format_final_gate_guard(*, plan: Mapping[str, Any], analysis: Mapping[str, Any],
                            trace: Mapping[str, Any], format_verification: Mapping[str, Any] | None,
                            authorization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    blockers: list[str] = []
    current = _current_fr_versions(analysis)
    results = {str(r.get("format_requirement_id")): r
               for r in (format_verification or {}).get("results", [])}
    compiled = {str(e.get("format_requirement_id")): e for e in trace.get("results", [])}
    plan_trace = plan.get("format_compilation") or {}
    if plan_trace.get("compile_fingerprint") != trace.get("compile_fingerprint"):
        blockers.append("Final Gate: Plan format_compilation fingerprint mismatch")
    for fr_id, version in sorted(current.items()):
        if fr_id not in compiled:
            blockers.append(f"Final Gate: Format Requirement {fr_id} was removed from compilation")
            continue
        if fr_id not in results:
            blockers.append(f"Final Gate: Format Requirement {fr_id} has no VERIFIED evidence")
            continue
        verification = results[fr_id]
        if verification.get("status") != "VERIFIED":
            blockers.append(
                f"Final Gate: Format Requirement {fr_id} is {verification.get('status')}"
            )
        if verification.get("fingerprint") != version.get("fingerprint"):
            blockers.append(f"Final Gate: Format Requirement {fr_id} semantic fingerprint changed")
        entry = compiled[fr_id]
        if entry.get("version_id") != version.get("version_id"):
            blockers.append(f"Final Gate: Format Requirement {fr_id} is not the current version")
        if not entry.get("format_operations"):
            blockers.append(f"Final Gate: Format Requirement {fr_id} Pass-2 operations were removed")
        if entry.get("recompute", {}).get("status") not in {None, "NOT_REQUIRED", "RESOLVED"}:
            blockers.append(f"Final Gate: Format Requirement {fr_id} has unresolved RECOMPUTE")
    if authorization is None and plan.get("authorization_mode") == "instruction_based":
        blockers.append("Final Gate: instruction authorization receipt is missing")
    return {
        "schema_version": "1.0",
        "rule_version": GATE_RULE_VERSION,
        "passed": not blockers,
        "blockers": blockers,
        "fr_status": {fr_id: results.get(fr_id, {}).get("status", "MISSING") for fr_id in sorted(current)},
    }

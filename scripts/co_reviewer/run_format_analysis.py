# -*- coding: utf-8 -*-
"""Effective DOCX Run format analysis and safety classification (Batch 2).

This module REUSES v232_contract.effective_run_properties.  It does not implement a
second Word style engine, does not build format maps and never edits a DOCX.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from lxml import etree as ET

from .document_engine import (
    NS, _applicable_format_flags, _direct_format_flags, parse_xml,
    run_plain_text, visible_text,
)
from .v232_contract import effective_run_properties, qn

ANALYSIS_VERSION = "run-safety/1.0"
SCHEMA_VERSION = "1.0"
SAFETY_CLASSES = frozenset({
    "SAFE_NEUTRAL", "SAFE_WITH_COMPATIBLE_CONTEXT", "UNSAFE_SCRIPT_FORMAT",
    "UNSAFE_STYLE_INHERITANCE", "UNRESOLVED_EFFECTIVE_FORMAT",
})
SAFE_BASE_CLASSES = frozenset({"SAFE_NEUTRAL", "SAFE_WITH_COMPATIBLE_CONTEXT"})
DECISIONS = frozenset({
    "PRESERVE", "STRIP", "RECOMPUTE",
    "EXPLICIT_OPERATION_REQUIRED", "BLOCKED_IF_UNKNOWN",
})
SOURCES = frozenset({
    "DIRECT_RUN", "CHARACTER_STYLE", "PARAGRAPH_STYLE",
    "TABLE_OR_NUMBERING_CONTEXT", "DOC_DEFAULTS", "THEME", "UNKNOWN",
})
BOOL_PROPERTIES = frozenset({
    "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "vanish",
})
TRACKED_PROPERTIES = (
    "vertAlign", "i", "iCs", "b", "bCs", "rFonts", "sz", "szCs", "color",
)
_OFF_VALUES = frozenset({"0", "false", "off"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _source_of(label: str | None) -> str:
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


def _bool_value(raw: Mapping[str, Any] | None) -> bool | None:
    if raw is None:
        return None
    value = raw.get(qn("val"))
    if value is None:
        return True
    return str(value).casefold() not in _OFF_VALUES


def _normalize_property(name: str, raw: Any) -> Any:
    if name in BOOL_PROPERTIES:
        return _bool_value(raw)
    if name == "vertAlign":
        return str(raw.get(qn("val"))) if isinstance(raw, Mapping) else str(raw)
    if name in {"sz", "szCs"}:
        try:
            return int(str(raw.get(qn("val"))))
        except (AttributeError, TypeError, ValueError):
            return None
    if name == "color":
        return str(raw.get(qn("val"))) if isinstance(raw, Mapping) else str(raw)
    if name == "rFonts" and isinstance(raw, Mapping):
        return {ET.QName(k).localname: str(v) for k, v in raw.items() if v is not None}
    if isinstance(raw, Mapping):
        return {ET.QName(k).localname: str(v) for k, v in raw.items() if v is not None}
    return raw


def _direct_rpr_map(direct_rpr: ET.Element | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if direct_rpr is None:
        return out
    for child in direct_rpr:
        if ET.QName(child).localname in {"rStyle", "rPrChange"}:
            continue
        out[ET.QName(child).localname] = dict(child.attrib)
    return out


def _fr_expected(fr: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(fr, Mapping):
        return {}
    return {
        key: value for key, value in fr.items()
        if key in {"token", "properties", "scope", "format_requirement_id", "version_id"}
    }


def _property_decisions(
    *,
    effective: Mapping[str, Any],
    resolution: Mapping[str, Any],
    sources: Mapping[str, str],
    fr_expected: Mapping[str, Any],
) -> list[dict[str, Any]]:
    wanted = fr_expected.get("properties") if isinstance(fr_expected.get("properties"), Mapping) else {}
    decisions: list[dict[str, Any]] = []
    for name in TRACKED_PROPERTIES:
        state = resolution.get(name, {})
        status = state.get("status") if isinstance(state, Mapping) else "UNRESOLVED"
        direct = state.get("direct") if isinstance(state, Mapping) else None
        value = effective.get(name)
        source = sources.get(name, "UNKNOWN")
        required = name in wanted
        reason = ""
        if status == "UNRESOLVED":
            decision = "BLOCKED_IF_UNKNOWN"
            reason = "effective value or provenance could not be resolved"
        elif name == "vertAlign":
            if required:
                decision = "EXPLICIT_OPERATION_REQUIRED"
                reason = "script alignment must be applied by an explicit FR operation"
            elif value in {"superscript", "subscript"}:
                decision = "STRIP"
                reason = "script alignment is not authorized by the current FR"
            else:
                decision = "PRESERVE"
                reason = "no script alignment is present or required"
        elif name in {"i", "iCs", "b", "bCs"}:
            if required:
                decision = "EXPLICIT_OPERATION_REQUIRED"
                reason = "FR requires this emphasis; use the scoped operation"
            elif value is True and source != "DIRECT_RUN":
                decision = "STRIP"
                reason = "inherited emphasis would leak into the replacement"
            elif value is True:
                decision = "PRESERVE"
                reason = "direct emphasis is explicit and compatible with the target context"
            else:
                decision = "PRESERVE"
                reason = "emphasis is absent and no FR requires it"
        elif name in {"rFonts", "sz", "szCs", "color"}:
            if value is None:
                decision = "RECOMPUTE" if required else "PRESERVE"
                reason = "no effective value; recompute from target context when required"
            elif source in {"DIRECT_RUN", "DOC_DEFAULTS", "THEME"}:
                decision = "PRESERVE"
                reason = "value comes from an explicit or default target context"
            else:
                decision = "RECOMPUTE"
                reason = "value comes from an inherited context and must be recomputed"
        else:
            decision = "BLOCKED_IF_UNKNOWN"
            reason = "no rule for this property"
        decisions.append({
            "property": name,
            "direct": direct,
            "effective": value,
            "provenance": source,
            "decision": decision,
            "reason": reason,
            **({"format_requirement_id": fr_expected.get("format_requirement_id")} if required else {}),
        })
    return decisions


def analyze_run(
    run: ET.Element,
    text: str,
    *,
    context: Mapping[str, Any],
    run_identity: Mapping[str, Any] | None = None,
    fr: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze one source Run. Pure read-only; never edits the XML."""
    direct_rpr = run.find("w:rPr", NS)
    direct_map = _direct_rpr_map(direct_rpr)
    direct_flags = sorted(_direct_format_flags(run))
    applicable_flags = sorted(_applicable_format_flags(run, text))
    resolution: dict[str, Any] = {}
    warnings: list[str] = []
    context_ready = True
    try:
        resolved = effective_run_properties(
            context.get("document_xml", b""),
            context.get("styles_xml"),
            context.get("paragraph_style_id"),
            context.get("run_style_id"),
            direct_rpr,
            paragraph_rpr=context.get("paragraph_rpr"),
            table_style_rpr=context.get("table_style_rpr"),
            numbering_rpr=context.get("numbering_rpr"),
            theme_xml=context.get("theme_xml"),
        )
    except Exception as exc:  # ContractError, XML errors, theme errors
        context_ready = False
        warnings.append(f"effective format resolution failed: {type(exc).__name__}: {exc}")
        resolved = {"properties": {}, "provenance": {}, "layers": [], "theme_font_map": {}}

    if context.get("styles_xml") is None:
        context_ready = False
        warnings.append("styles.xml is missing; inherited values cannot be resolved")
    fr_id = str(fr.get("format_requirement_id", "")) if isinstance(fr, Mapping) else ""
    if context.get("fr_current_ids") is not None and fr_id and fr_id not in set(context["fr_current_ids"]):
        context_ready = False
        warnings.append(f"Format Requirement {fr_id} is missing or not current")
    if fr_id and not fr:
        context_ready = False
        warnings.append(f"Format Requirement {fr_id} has no current version data")

    properties = resolved.get("properties", {}) or {}
    provenance = resolved.get("provenance", {}) or {}
    layers = resolved.get("layers", []) or []
    sources: dict[str, str] = {}
    effective: dict[str, Any] = {}
    for name in TRACKED_PROPERTIES:
        raw = properties.get(name)
        effective[name] = _normalize_property(name, raw) if raw is not None else None
        sources[name] = _source_of(provenance.get(name))
        if name == "rFonts":
            for slot in ("ascii", "hAnsi", "eastAsia", "cs"):
                label = provenance.get("rFonts." + slot)
                if label:
                    sources[f"rFonts.{slot}"] = _source_of(label)
        layers_with: list[dict[str, Any]] = []
        for layer in layers:
            layer_props = layer.get("properties", {}) if isinstance(layer, Mapping) else {}
            if name in layer_props:
                layers_with.append({
                    "layer": layer.get("name", ""),
                    "value": _normalize_property(name, layer_props[name]),
                })
        direct = _normalize_property(name, direct_map[name]) if name in direct_map else None
        status = "RESOLVED_ABSENT"
        if raw is not None:
            status = "RESOLVED_PRESENT"
        elif not context_ready:
            status = "UNRESOLVED"
        if len(layers_with) > 1:
            status = "CONFLICT_RESOLVED"
        resolution[name] = {
            "status": status,
            "direct": direct,
            "provenance": sources[name],
            "layers": layers_with,
        }

    critical_unresolved = [
        name for name in ("vertAlign", "i", "iCs", "b", "bCs")
        if resolution.get(name, {}).get("status") == "UNRESOLVED"
    ]
    script_effective = any(effective.get(name) in {"superscript", "subscript"} for name in ("vertAlign",))
    script_direct = any(name in direct_map for name in ("vertAlign",)) and effective.get("vertAlign") in {"superscript", "subscript"}
    reasons: list[str] = []
    if critical_unresolved or not context_ready:
        classification = "UNRESOLVED_EFFECTIVE_FORMAT"
        reasons.extend(warnings)
        if critical_unresolved:
            reasons.append("critical effective properties unresolved: " + ", ".join(critical_unresolved))
    elif script_effective or script_direct:
        classification = "UNSAFE_SCRIPT_FORMAT"
        reasons.append("effective or direct script alignment would contaminate the replacement")
    else:
        wanted = _fr_expected(fr).get("properties", {}) if isinstance(fr, Mapping) else {}
        inherited_conflict = [
            name for name in ("i", "iCs", "b", "bCs", "color")
            if name in wanted and effective.get(name) is not None
            and sources.get(name) not in {"DIRECT_RUN", "UNKNOWN"}
            and effective.get(name) != wanted.get(name)
        ]
        unknown_context = any(sources.get(name) == "UNKNOWN" for name in TRACKED_PROPERTIES if resolution.get(name, {}).get("status") == "RESOLVED_PRESENT")
        if inherited_conflict or unknown_context:
            classification = "UNSAFE_STYLE_INHERITANCE"
            reasons.append("inherited context cannot be safely reconciled: " + ", ".join(inherited_conflict or ["UNKNOWN provenance"]))
        elif any(effective.get(name) is not None for name in ("i", "iCs", "b", "bCs", "rFonts", "sz", "color")):
            classification = "SAFE_WITH_COMPATIBLE_CONTEXT"
            reasons.append("known non-script context is compatible with the FR")
        else:
            classification = "SAFE_NEUTRAL"
            reasons.append("effective format is resolved and neutral")

    decisions = _property_decisions(
        effective=effective, resolution=resolution, sources=sources,
        fr_expected=_fr_expected(fr),
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "run": dict(run_identity or {}),
        "text": text,
        "direct_format": {
            "flags": direct_flags,
            "applicable_flags": applicable_flags,
            "properties": {name: resolution[name]["direct"] for name in TRACKED_PROPERTIES},
        },
        "effective_format": {"properties": effective, "sources": sources},
        "provenance": sources,
        "resolution": resolution,
        "context_ready": context_ready,
        "warnings": warnings,
        "safety_classification": classification,
        "safety_reasons": reasons,
        "property_decisions": decisions,
        "format_requirement_id": fr_id or None,
    }
    report["analysis_fingerprint"] = hashlib.sha256(
        canonical_json({k: v for k, v in report.items() if k != "analysis_fingerprint"}).encode("utf-8")
    ).hexdigest()
    return report


def analyze_paragraph_runs(
    paragraph: ET.Element,
    *,
    context: Mapping[str, Any],
    part: str = "word/document.xml",
    p_idx: int = 0,
    fr: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Analyze all ordinary Runs in a paragraph using existing text/run helpers."""
    results: list[dict[str, Any]] = []
    local_context = dict(context)
    p_pr = paragraph.find("w:pPr", NS)
    if p_pr is not None:
        p_style = p_pr.find("w:pStyle", NS)
        if p_style is not None and p_style.get(qn("val")):
            local_context["paragraph_style_id"] = p_style.get(qn("val"))
        p_rpr = p_pr.find("w:rPr", NS)
        if p_rpr is not None:
            local_context["paragraph_rpr"] = p_rpr
    for index, run in enumerate(paragraph.findall(".//w:r", NS)):
        text = run_plain_text(run)
        if text is None:
            continue
        results.append(analyze_run(
            run, text, context=local_context, fr=fr,
            run_identity={"part": part, "p_idx": p_idx, "run_index": index,
                          "run_id": f"{part}:P{p_idx}:R{index}"},
        ))
    return results


def analyze_docx(
    docx_path: str | Path,
    *,
    styles_path: str | Path | None = None,
    theme_path: str | Path | None = None,
    fr: Mapping[str, Any] | None = None,
    fr_current_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only DOCX analysis entry point used by the CLI command."""
    path = Path(docx_path).expanduser().resolve()
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
        styles_xml = archive.read("word/styles.xml") if styles_path is None and "word/styles.xml" in archive.namelist() else None
        theme_xml = None
        if theme_path is None and "word/theme/theme1.xml" in archive.namelist():
            theme_xml = archive.read("word/theme/theme1.xml")
    if styles_path is not None:
        styles_xml = Path(styles_path).expanduser().read_bytes()
    if theme_path is not None:
        theme_xml = Path(theme_path).expanduser().read_bytes()
    root = parse_xml(document_xml)
    context = {
        "document_xml": document_xml, "styles_xml": styles_xml,
        "theme_xml": theme_xml, "fr_current_ids": fr_current_ids,
    }
    runs: list[dict[str, Any]] = []
    for p_idx, paragraph in enumerate(root.findall(".//w:body//w:p", NS)):
        runs.extend(analyze_paragraph_runs(paragraph, context=context, p_idx=p_idx, fr=fr))
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_version": ANALYSIS_VERSION,
        "source_docx": str(path),
        "runs": runs,
    }


def validate_run_format_analysis(report: Mapping[str, Any]) -> list[str]:
    """Runtime structural validation; JSON Schema is checked when available."""
    errors: list[str] = []
    if not isinstance(report, Mapping) or report.get("schema_version") != SCHEMA_VERSION:
        return ["run format analysis schema_version invalid"]
    runs = report.get("runs")
    if not isinstance(runs, list):
        return ["runs must be an array"]
    for index, row in enumerate(runs):
        label = f"runs[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{label} must be an object")
            continue
        if row.get("safety_classification") not in SAFETY_CLASSES:
            errors.append(f"{label}.safety_classification invalid")
        decisions = row.get("property_decisions")
        if not isinstance(decisions, list):
            errors.append(f"{label}.property_decisions must be an array")
        else:
            for d_index, decision in enumerate(decisions):
                if not isinstance(decision, Mapping) or decision.get("decision") not in DECISIONS:
                    errors.append(f"{label}.property_decisions[{d_index}] invalid")
        for section in ("direct_format", "effective_format", "resolution", "provenance"):
            if not isinstance(row.get(section), Mapping):
                errors.append(f"{label}.{section} must be an object")
        if not isinstance(row.get("analysis_fingerprint"), str) or len(row["analysis_fingerprint"]) != 64:
            errors.append(f"{label}.analysis_fingerprint invalid")
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return errors
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / "run-format-analysis.schema.json"
    if not schema_path.is_file():
        return errors + ["run format analysis schema is missing"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors.extend(
        "run format analysis schema " + "/".join(str(p) for p in e.absolute_path) + ": " + e.message
        for e in sorted(validator.iter_errors(report), key=lambda item: list(item.absolute_path))
    )
    return errors

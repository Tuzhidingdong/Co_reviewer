#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a reusable, source-hash-bound semantic/format profile from the first DOCX inventory pass.

This module never opens a DOCX. It consumes the paragraph/Run inventory produced by
co_reviewer_cli.inspect_docx, so profile creation does not parse source documents again.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

PROFILE_SCHEMA_VERSION = "1.0"

_REFERENCE_HEADINGS = {
    "references", "reference", "bibliography", "literature cited",
    "works cited", "引用文献", "参考文献", "参考资料",
}
_SYMBOL_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z])(?![A-Za-z0-9])"
    r"|([\u0370-\u03ff\u1f00-\u1fff])"
    r"|([\u2070-\u209f])"
    r"|([±×÷≤≥≈≠∝∞∑∏√∂∇∫∮∈∉∅∩∪→←↔↑↓⇒⇌])"
)
_REFERENCE_ENTRY_RE = re.compile(
    r"^\s*(?:\[\s*\d+\s*\]|\d+[.)]|\(\s*\d+\s*\))\s+"
    r"|^\s*[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:,|\s)"
)
_FALSE_VALUES = {"0", "false", "off", "no"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes | str) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _stable_id(*parts: Any) -> str:
    return "occ-" + _sha256(_canonical_json(list(parts)))[:20]


def _normalise_text(value: str) -> str:
    return " ".join((value or "").split())


def _is_reference_heading(paragraph: Mapping[str, Any]) -> bool:
    text = _normalise_text(str(paragraph.get("text", ""))).strip(" :：")
    folded = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", text).casefold()
    return folded in _REFERENCE_HEADINGS


def _is_heading(paragraph: Mapping[str, Any]) -> bool:
    style = str(paragraph.get("paragraph_style", "")).casefold()
    compact = re.sub(r"[^a-z0-9]", "", style)
    if compact.startswith("heading") or compact in {"title", "subtitle", "sectionheading"}:
        return True
    text = _normalise_text(str(paragraph.get("text", ""))).casefold()
    text = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", text)
    if re.match(
        r"^(?:appendix\b|supplementary (?:information|materials)\b|"
        r"acknowledg(?:e)?ments\b|funding\b|author contributions\b|"
        r"data availability\b|conflicts? of interest\b|competing interests\b|"
        r"ethics statement\b|abbreviations\b|footnotes\b)",
        text,
    ):
        return True
    para_format = paragraph.get("paragraph_format")
    if isinstance(para_format, Mapping):
        outline = para_format.get("outlineLvl")
        if isinstance(outline, Mapping):
            attributes = outline.get("attributes", {})
            outline = attributes.get("val") if isinstance(attributes, Mapping) else None
        if outline not in (None, "", "9"):
            return True
    return False


def _format_signature(span: Mapping[str, Any]) -> str:
    payload = {
        "format_properties": span.get("format_properties", {}),
        "unsupported_format_properties": span.get("unsupported_format_properties", []),
        "run_properties": span.get("run_properties", {}),
        "run_style": span.get("run_style", ""),
        "paragraph_style": span.get("paragraph_style", ""),
    }
    return _sha256(_canonical_json(payload))[:16]


def _is_special_format(span: Mapping[str, Any]) -> bool:
    props = span.get("format_properties")
    if isinstance(props, Mapping) and props:
        return True
    if span.get("unsupported_format_properties"):
        return True
    return bool(span.get("run_style"))


def _context(text: str, start: int | None, end: int | None, radius: int = 180) -> dict[str, str]:
    if start is None or end is None:
        return {"before": "", "after": "", "sentence": text[:radius * 2]}
    left = max(0, start - radius)
    right = min(len(text), end + radius)
    prefix = text[left:start]
    suffix = text[end:right]
    sent_start = max(prefix.rfind("."), prefix.rfind("?"), prefix.rfind("!"), prefix.rfind("\n")) + 1
    stops = [item for item in (suffix.find("."), suffix.find("?"), suffix.find("!"), suffix.find("\n")) if item >= 0]
    sent_end = min(stops) + 1 if stops else len(suffix)
    return {
        "before": prefix[-radius:],
        "after": suffix[:radius],
        "sentence": (prefix[sent_start:] + text[start:end] + suffix[:sent_end]).strip(),
    }


def _paragraph_zone_map(paragraphs: list[Mapping[str, Any]]) -> dict[tuple[str, int], str]:
    """Classify the bibliography interval using its heading and the next heading boundary."""
    zones: dict[tuple[str, int], str] = {}
    in_references = False
    for row in paragraphs:
        part = str(row.get("part", "word/document.xml"))
        p_idx = row.get("p_idx")
        if not isinstance(p_idx, int):
            continue
        key = (part, p_idx)
        text = str(row.get("text", ""))
        if part != "word/document.xml" or not text.strip():
            zones[key] = "body"
            continue
        if _is_reference_heading(row):
            in_references = True
            zones[key] = "references_heading"
            continue
        if in_references and _is_heading(row):
            in_references = False
        zones[key] = "references" if in_references else "body"
    return zones


def _reference_style_pattern(paragraph: Mapping[str, Any]) -> dict[str, Any]:
    spans = paragraph.get("format_spans", [])
    sequence: list[str] = []
    if isinstance(spans, list):
        for span in spans:
            if not isinstance(span, Mapping):
                continue
            signature = _format_signature({
                **span,
                "paragraph_style": paragraph.get("paragraph_style", ""),
            })
            if not sequence or sequence[-1] != signature:
                sequence.append(signature)
    return {
        "paragraph_style": paragraph.get("paragraph_style", ""),
        "paragraph_format": paragraph.get("paragraph_format", {}),
        "run_format_sequence": sequence,
    }


def _source_occurrence(
    *,
    role: str,
    source_sha256: str,
    paragraph: Mapping[str, Any],
    zone: str,
    text: str,
    start: int | None,
    end: int | None,
    format_properties: Mapping[str, Any] | None,
    unsupported_format_properties: list[str] | None = None,
    run_style: str = "",
    run_index: int | None = None,
    occurrence_kind: str,
) -> dict[str, Any]:
    part = str(paragraph.get("part", "word/document.xml"))
    p_idx = int(paragraph.get("p_idx", -1))
    para_text = str(paragraph.get("text", ""))
    occurrence_id = _stable_id(
        role, source_sha256, part, p_idx, start, end, occurrence_kind, text
    )
    return {
        "occurrence_id": occurrence_id,
        "document_role": role,
        "zone": zone,
        "occurrence_kind": occurrence_kind,
        "text": text,
        "part": part,
        "p_idx": p_idx,
        "para_id": paragraph.get("para_id"),
        "start_offset": start,
        "end_offset": end,
        "paragraph_style": paragraph.get("paragraph_style", ""),
        "paragraph_format": copy.deepcopy(paragraph.get("paragraph_format", {})),
        "run_index": run_index,
        "run_style": run_style,
        "format_properties": copy.deepcopy(dict(format_properties or {})),
        "unsupported_format_properties": sorted(set(unsupported_format_properties or [])),
        "context": _context(para_text, start, end),
        "semantic_role": None,
        "semantic_annotation_status": "pending_initial_annotation",
    }


def build_document_profile(report: Mapping[str, Any]) -> dict[str, Any]:
    role = str(report.get("role", "Document"))
    source_sha256 = str(report.get("source_sha256", ""))
    paragraphs = [
        row for row in report.get("paragraphs", [])
        if isinstance(row, Mapping) and isinstance(row.get("p_idx"), int)
    ]
    paragraphs.sort(key=lambda row: (str(row.get("part", "")), int(row.get("p_idx", 0))))
    zones = _paragraph_zone_map(paragraphs)

    body_occurrences: list[dict[str, Any]] = []
    reference_occurrences: list[dict[str, Any]] = []
    reference_entries: list[dict[str, Any]] = []
    paragraph_regions: list[dict[str, Any]] = []
    reference_groups: dict[str, dict[str, Any]] = {}

    for paragraph in paragraphs:
        part = str(paragraph.get("part", "word/document.xml"))
        p_idx = int(paragraph["p_idx"])
        text = str(paragraph.get("text", ""))
        zone = zones.get((part, p_idx), "body")
        if zone == "references_heading":
            paragraph_regions.append({
                "part": part, "p_idx": p_idx, "para_id": paragraph.get("para_id"),
                "zone": zone, "paragraph_style": paragraph.get("paragraph_style", ""),
            })
            continue
        if not text.strip():
            continue
        paragraph_regions.append({
            "part": part, "p_idx": p_idx, "para_id": paragraph.get("para_id"),
            "zone": zone, "paragraph_style": paragraph.get("paragraph_style", ""),
        })

        spans = paragraph.get("format_spans", [])
        spans = spans if isinstance(spans, list) else []
        span_rows = [row for row in spans if isinstance(row, Mapping)]

        if zone == "references":
            pattern = _reference_style_pattern(paragraph)
            pattern_sha = _sha256(_canonical_json(pattern))
            entry_id = _stable_id(role, source_sha256, part, p_idx, "reference-entry")
            reference_entries.append({
                "entry_id": entry_id,
                "part": part,
                "p_idx": p_idx,
                "para_id": paragraph.get("para_id"),
                "text": text,
                "entry_type_candidate": "numbered_or_author_year" if _REFERENCE_ENTRY_RE.search(text) else "continuation_or_unclassified",
                "paragraph_style": paragraph.get("paragraph_style", ""),
                "paragraph_format": copy.deepcopy(paragraph.get("paragraph_format", {})),
                "format_spans": copy.deepcopy(span_rows),
                "style_pattern_id": "ref-style-" + pattern_sha[:16],
                "style_pattern": pattern,
            })
            group = reference_groups.setdefault(pattern_sha, {
                "style_pattern_id": "ref-style-" + pattern_sha[:16],
                "pattern": pattern,
                "entry_ids": [],
                "sample_entries": [],
            })
            group["entry_ids"].append(entry_id)
            if len(group["sample_entries"]) < 5:
                group["sample_entries"].append({
                    "entry_id": entry_id, "text": text[:500],
                })

        for span in span_rows:
            span_text = str(span.get("text", ""))
            if not span_text:
                continue
            props = span.get("format_properties", {})
            if _is_special_format(span):
                occurrence = _source_occurrence(
                    role=role, source_sha256=source_sha256, paragraph=paragraph,
                    zone=zone, text=span_text, start=span.get("start_offset"),
                    end=span.get("end_offset"), format_properties=props,
                    unsupported_format_properties=list(span.get("unsupported_format_properties", [])),
                    run_style=str(span.get("run_style", "")),
                    run_index=span.get("run_index"), occurrence_kind="formatted_run",
                )
                (reference_occurrences if zone == "references" else body_occurrences).append(occurrence)

        # Retain each single-letter, Greek, script and mathematical-symbol occurrence
        # independently. In particular, different occurrences of "a" are never merged.
        for match in _SYMBOL_RE.finditer(text):
            symbol = match.group(0)
            start, end = match.span()
            source_span = next(
                (
                    span for span in span_rows
                    if isinstance(span.get("start_offset"), int)
                    and isinstance(span.get("end_offset"), int)
                    and span["start_offset"] <= start < span["end_offset"]
                ),
                {},
            )
            occurrence = _source_occurrence(
                role=role, source_sha256=source_sha256, paragraph=paragraph,
                zone=zone, text=symbol, start=start, end=end,
                format_properties=source_span.get("format_properties", {}),
                unsupported_format_properties=list(source_span.get("unsupported_format_properties", [])),
                run_style=str(source_span.get("run_style", "")),
                run_index=source_span.get("run_index"), occurrence_kind="symbol",
            )
            (reference_occurrences if zone == "references" else body_occurrences).append(occurrence)

    reference_groups_list = sorted(
        reference_groups.values(),
        key=lambda item: (len(item["entry_ids"]), item["style_pattern_id"]),
        reverse=True,
    )
    reference_style_state = (
        "no_reference_section" if not reference_entries
        else "uniform_pattern" if len(reference_groups_list) == 1
        else "multiple_patterns_no_automatic_majority_selection"
    )
    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "document_role": role,
        "source_sha256": source_sha256,
        "scope": "one exact source DOCX revision",
        "zone_policy": {
            "body": "all document content outside the detected References/Bibliography section",
            "references": "the detected bibliography interval; use only other reference entries as formatting sources",
        },
        "paragraph_regions": paragraph_regions,
        "body_occurrences": body_occurrences,
        "reference_occurrences": reference_occurrences,
        "reference_entries": reference_entries,
        "reference_style_groups": reference_groups_list,
        "reference_style_state": reference_style_state,
        "counts": {
            "body_occurrences": len(body_occurrences),
            "reference_occurrences": len(reference_occurrences),
            "reference_entries": len(reference_entries),
            "reference_style_groups": len(reference_groups_list),
        },
        "annotation_policy": {
            "status": "pending_initial_annotation",
            "instructions": [
                "Annotate occurrences by their individual contextual semantic role; never merge identical characters at different locations.",
                "The occurrence_id is stable for this source SHA and location. Preserve it in every annotation.",
                "Do not infer one global style for a symbol such as a or alpha.",
                "Reference-list occurrences and templates are isolated from body occurrences and must never be used as body formatting sources, or vice versa.",
                "Reference formatting should be inferred from comparable entries/fields within the same reference list, not from the body.",
            ],
        },
    }
    profile["profile_fingerprint"] = _sha256(_canonical_json(profile))
    return profile


def build_workspace_profile(inventory: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if isinstance(inventory, Mapping):
        reports = inventory.get("documents")
    else:
        reports = inventory
    if not isinstance(reports, list):
        raise ValueError("Workspace inventory must be an array of document reports.")
    documents: list[dict[str, Any]] = []
    updated_reports: list[dict[str, Any]] = []
    seen_roles: set[str] = set()
    for raw in reports:
        if not isinstance(raw, Mapping):
            raise ValueError("Workspace inventory contains a non-object document report.")
        report = copy.deepcopy(dict(raw))
        role = str(report.get("role", ""))
        if role in seen_roles:
            raise ValueError(f"Workspace inventory has duplicate document role {role!r}.")
        seen_roles.add(role)
        if not report.get("source_sha256"):
            raise ValueError(f"Workspace inventory report for {role!r} is missing source_sha256.")
        profile = build_document_profile(report)
        report["semantic_format_profile"] = profile
        updated_reports.append(report)
        documents.append(profile)
    workspace = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile_kind": "document_semantic_format_profile",
        "generated_from": "existing_workspace_inventory_no_docx_reopen",
        "documents": documents,
    }
    workspace["profile_fingerprint"] = _sha256(_canonical_json(workspace))
    return workspace, updated_reports


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            prefix=f".{path.name}.", suffix=".tmp",
            dir=path.parent, delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, help="workspace_inventory.json created by the initial audit")
    parser.add_argument("--out", required=True, help="new document_semantic_format_profile.json path")
    args = parser.parse_args()
    inventory_path = Path(args.inventory).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    if inventory_path == output_path:
        raise SystemExit("Profile output must not overwrite workspace_inventory.json.")
    raw = json.loads(inventory_path.read_text(encoding="utf-8"))
    profile, updated_reports = build_workspace_profile(raw)
    _atomic_write_json(output_path, profile)
    _atomic_write_json(inventory_path, updated_reports)
    print(f"[SEMANTIC PROFILE] {output_path}")
    print(f"[PROFILE FINGERPRINT] {profile['profile_fingerprint']}")
    print(f"[DOCUMENTS] {len(profile['documents'])}")
    for row in profile["documents"]:
        print(
            f"[{row['document_role']}] body occurrences={row['counts']['body_occurrences']}; "
            f"reference occurrences={row['counts']['reference_occurrences']}; "
            f"reference entries={row['counts']['reference_entries']}; "
            f"reference styles={row['counts']['reference_style_groups']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

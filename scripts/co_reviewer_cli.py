#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Conservative DOCX inspection and Word-native revision engine for Co_Reviewer."""
from __future__ import annotations
import argparse
import copy
import json
import io
import hashlib
import os
import re
import sys
import tempfile
import unicodedata
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping
from lxml import etree as ET
from review_audit import (
    build_decision_queue, build_review_audit, cache_summary,
    extract_comment_ranges, extract_figure_table_references,
    load_cached_inspection, parse_caption, write_audit_markdown,
)
from co_reviewer.document_engine import (
    W, XML, NS, parse_xml, qn, visible_text, has_drawing,
    extract_native_script_runs, run_plain_text, make_run,
    _set_run_property, _script_character, normalize_script_characters,
    make_insert_runs, revision_wrapper, replace_in_paragraph,
    next_revision_id, safe_part, prepare_document, write_prepared_document,
    apply_document, RevisionDocumentEngine,
)
from co_reviewer.identity import (
    sha256_file, canonical_fingerprint, revision_targets_fingerprint,
    revision_item_fingerprint, revision_verification_fingerprint,
    DocumentIdentityAndHash,
)
from co_reviewer.revision_models import (
    RevisionEvent,
    Patch,
    PatchOperation,
    RevisionLedger,
    RevisionModelError,
    build_revision_ledger,
    revision_ledger_from_mapping,
    response_units_to_legacy_plan,
)
from co_reviewer.working_view import (
    WorkingViewError, create_working_clean_view, inspect_clean_baseline,
    strip_docx_comments_bytes, accepted_view_docx_bytes,
)
from co_reviewer.usage_audit import (
    record_usage_event, read_usage_events, build_usage_report, estimate_usage_cost,
)
from co_reviewer.docx_quality import audit_docx_quality
from co_reviewer.response_renderer import render_response_letter
from co_reviewer.structure_classifier import classify_docx_structure
from co_reviewer.v232_contract import (
    ContractError, narrow_revision, build_package_reference_graph,
    commit_document_package, compile_clean_from_marked,
    build_revision_baseline_inventory, targeted_revision_gate,
    extract_docx_text_for_reference_graph, extract_docx_reference_segments,
    renumber_reference_graph,
    collect_revision_ids_from_lineage,
)

ET.register_namespace("w", W)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
ET.register_namespace("wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing")
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
def _resolve_batch_dir_argument(value: str | Path) -> Path:
    """Reject symlink components before canonicalizing a user-supplied batch path."""
    requested = Path(value).expanduser()
    candidate = Path(os.path.abspath(str(requested)))
    probe = candidate
    while True:
        if probe.is_symlink():
            raise ValueError(f"Batch directory path must not traverse a symbolic link: {probe}")
        if probe.parent == probe:
            break
        probe = probe.parent
    return candidate.resolve()


ET.register_namespace("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture")

PLACEHOLDER_RE = re.compile(
    r"(\[\[(?:TODO|TBD|XX|REF|FIG|TABLE)[^\]]*\]\]|\[(?:TODO|TBD|XX|REF|INSERT[^\]]*)\]|"
    r"\b(?:TODO|TBD|PLACEHOLDER)\b|Fig(?:ure)?\.?\s*S?[xX]+|Table\s*S?[xX]+|"
    r"Page\s*[xX]+|Lines?\s*[xX]+)", re.I
)
CAPTION_RE = re.compile(r"^(?:Fig(?:ure)?\.?|Table)\s*(?:S?\d+|S?[xX]+)", re.I)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

def inspect_docx(path: str, comment_author: str | None = None) -> dict[str, Any]:
    p = Path(path)
    report = {
        "file": str(p), "exists": p.is_file(), "media_count": 0, "paragraph_count": 0,
        "table_count": 0, "tracked_insertions": 0, "tracked_deletions": 0,
        "comments": [], "paragraphs": [], "placeholders": [], "chinese_notes": [],
        "captions_and_drawings": [], "figure_table_references": [], "errors": []
    }
    if not p.is_file():
        report["errors"].append("File does not exist.")
        return report
    if p.suffix.lower() != ".docx":
        report["errors"].append("Only .docx files are supported.")
        return report

    comment_metadata: dict[str, dict[str, Any]] = {}
    anchor_ranges: dict[str, list[dict[str, Any]]] = defaultdict(list)
    comment_references: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paragraph_lookup: dict[tuple[str, int], str] = {}

    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            report["media_count"] = sum(
                n.startswith("word/media/") and not n.endswith("/") for n in names
            )

            if "word/comments.xml" in names:
                croot = parse_xml(z.read("word/comments.xml"))
                for comment_xml_p_idx, comment in enumerate(croot.findall(".//w:comment", NS)):
                    comment_id = comment.get(qn("id"), "")
                    comment_metadata[comment_id] = {
                        "comment_xml_p_idx": comment_xml_p_idx,
                        "author": comment.get(qn("author"), ""),
                        "date": comment.get(qn("date"), ""),
                        "text": "".join(
                            node.text or "" for node in comment.findall(".//w:t", NS)
                        ).strip(),
                    }

            parts = []
            for name in names:
                if not name.startswith("word/") or not name.endswith(".xml"):
                    continue
                if (
                    name == "word/document.xml"
                    or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
                    or name in {"word/footnotes.xml", "word/endnotes.xml"}
                ):
                    parts.append(name)
            parts.sort(key=lambda value: (value != "word/document.xml", value))

            for part in parts:
                try:
                    root = parse_xml(z.read(part))
                except ET.XMLSyntaxError as exc:
                    report["errors"].append(f"{part}: invalid XML: {exc}")
                    continue

                report["tracked_insertions"] += len(root.findall(".//w:ins", NS))
                report["tracked_deletions"] += len(root.findall(".//w:del", NS))
                for comment_id, ranges in extract_comment_ranges(root, part).items():
                    anchor_ranges[comment_id].extend(ranges)

                paragraphs = root.findall(".//w:p", NS)
                for idx, para in enumerate(paragraphs):
                    txt = visible_text(para).strip()
                    report["paragraph_count"] += 1
                    para_id = para.get(
                        "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
                    )
                    style_node = para.find("w:pPr/w:pStyle", NS)
                    paragraph_style = style_node.get(qn("val"), "") if style_node is not None else ""
                    paragraph_has_drawing = has_drawing(para)
                    if txt:
                        report["paragraphs"].append({
                            "part": part, "p_idx": idx, "para_id": para_id, "text": txt,
                            "paragraph_style": paragraph_style,
                            "native_script_runs": extract_native_script_runs(para)
                        })
                        paragraph_lookup[(part, idx)] = txt

                    for marker in para.findall(".//w:commentReference", NS):
                        comment_id = marker.get(qn("id"), "")
                        if comment_id:
                            comment_references[comment_id].append({
                                "part": part, "p_idx": idx, "para_id": para_id
                            })

                    flags = sorted(set(PLACEHOLDER_RE.findall(txt)))
                    if flags:
                        report["placeholders"].append({
                            "part": part, "p_idx": idx, "matches": flags, "text": txt
                        })
                    if txt and CHINESE_RE.search(txt):
                        report["chinese_notes"].append({
                            "part": part, "p_idx": idx, "text": txt
                        })

                    caption = (
                        parse_caption(
                            txt, part, idx,
                            paragraph_style=paragraph_style,
                            has_drawing=paragraph_has_drawing,
                        )
                        if txt else None
                    )
                    if caption or paragraph_has_drawing:
                        report["captions_and_drawings"].append({
                            "part": part, "p_idx": idx, "para_id": para_id,
                            "has_drawing": paragraph_has_drawing,
                            "paragraph_style": paragraph_style,
                            "caption_kind": caption["kind"] if caption else None,
                            "caption_label": caption["label"] if caption else None,
                            "caption_detection_signals": caption["detection_signals"] if caption else [],
                            "detection_confidence": caption["detection_confidence"] if caption else "drawing_without_caption",
                            "text": txt[:500],
                        })
                    if txt:
                        report["figure_table_references"].extend(
                            extract_figure_table_references(txt, part, idx, caption=caption)
                        )

                if part == "word/document.xml":
                    report["table_count"] = len(root.findall(".//w:tbl", NS))

            # Emit exactly one inventory entry per native comment, even when its
            # selected range spans several paragraphs or Word parts.
            for comment_id, metadata in comment_metadata.items():
                if comment_author and comment_author.casefold() not in metadata["author"].casefold():
                    continue

                ranges = anchor_ranges.get(comment_id, [])
                resolved_ranges = [
                    item for item in ranges
                    if item.get("complete") and item.get("text")
                ]
                if resolved_ranges:
                    anchor_status = "range_resolved"
                elif ranges:
                    anchor_status = "range_incomplete" if any(not x.get("complete") for x in ranges) else "empty_range"
                elif comment_references.get(comment_id):
                    anchor_status = "reference_only"
                else:
                    anchor_status = "markers_missing"

                anchor_text = "\n[separate Word selection]\n".join(
                    item["text"] for item in resolved_ranges
                )
                locations = []
                for item in ranges:
                    locations.append({
                        "part": item.get("part"),
                        "start": item.get("start"),
                        "end": item.get("end"),
                        "paragraphs": item.get("paragraphs", []),
                        "complete": item.get("complete", False),
                    })
                first_range = ranges[0] if ranges else None
                reference_locations = comment_references.get(comment_id, [])
                first_location = (
                    first_range.get("start") if first_range else
                    (reference_locations[0] if reference_locations else {})
                ) or {}
                source_paragraphs = []
                for location in locations:
                    part = location.get("part")
                    for p_idx in location.get("paragraphs", []):
                        key = (part, int(p_idx))
                        if key in paragraph_lookup and paragraph_lookup[key] not in source_paragraphs:
                            source_paragraphs.append(paragraph_lookup[key])
                if not source_paragraphs and first_location:
                    source_text = paragraph_lookup.get(
                        (first_location.get("part"), int(first_location.get("p_idx", -1))), ""
                    )
                    if source_text:
                        source_paragraphs.append(source_text)

                report["comments"].append({
                    "part": first_location.get("part", "unknown"),
                    "p_idx": first_location.get("p_idx", -1),
                    "id": comment_id,
                    "comment_xml_p_idx": metadata.get("comment_xml_p_idx"),
                    "author": metadata["author"],
                    "date": metadata["date"],
                    "comment": metadata["text"],
                    "anchor_status": anchor_status,
                    "anchor_text": anchor_text,
                    "anchor_selections": [
                        {
                            "text": item.get("text", ""),
                            "part": item.get("part"),
                            "start": item.get("start"),
                            "end": item.get("end"),
                            "paragraphs": item.get("paragraphs", []),
                        }
                        for item in resolved_ranges
                    ],
                    "anchor_ranges": locations,
                    "anchor_paragraphs": sorted({
                        int(index)
                        for location in locations
                        for index in location.get("paragraphs", [])
                    }),
                    "reference_locations": reference_locations,
                    "anchor_paragraph": "\n".join(source_paragraphs),
                })

    except (OSError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        report["errors"].append(f"Unable to inspect DOCX: {exc}")
    return report

def validate_inspection_artifact_paths(source_paths: list[str], output_paths: list[str]) -> None:
    sources = {Path(path).expanduser().resolve() for path in source_paths}
    outputs = [Path(path).expanduser().resolve() for path in output_paths if path]
    duplicates = sorted({str(path) for path in outputs if outputs.count(path) > 1})
    if duplicates:
        raise ValueError("Inspection outputs must use distinct paths: " + ", ".join(duplicates))
    collisions = sorted(str(path) for path in outputs if path in sources)
    if collisions:
        raise ValueError("Inspection output would overwrite a source DOCX; choose another path: " + ", ".join(collisions))


def write_reports(reports: list[dict[str, Any]], md_path: str, json_path: str | None, max_items: int = 20) -> None:
    lines = ["# Co_Reviewer Workspace Digest", "", f"Generated: {datetime.now(timezone.utc).isoformat()}", ""]
    for r in reports:
        lines += [
            f"## {r['role']}: {r['file']}",
            f"- Exists: {r['exists']}",
            f"- Paragraphs: {r['paragraph_count']} | Tables: {r['table_count']} | Media: {r['media_count']}",
            f"- Existing tracked changes: {r['tracked_insertions']} insertions, {r['tracked_deletions']} deletions",
            f"- Comments: {len(r['comments'])} | Placeholders: {len(r['placeholders'])} | Chinese notes: {len(r['chinese_notes'])}",
            "", "### Reviewer comments"
        ]
        if r["role"] == "Reviewers":
            lines.append("#### Text paragraphs from the reviewer document")
            for paragraph in r["paragraphs"][:max_items]:
                lines.append(f"- [{paragraph['part']} P[{paragraph['p_idx']}]] {paragraph['text']}")
                for script_run in paragraph.get("native_script_runs", []):
                    lines.append(
                        f"  - [NATIVE {script_run['vertical_alignment']}] {script_run['text']}"
                    )
            if len(r["paragraphs"]) > max_items:
                lines.append(f"- ... {len(r['paragraphs']) - max_items} more paragraphs in JSON inventory.")
        for c in r["comments"][:max_items]:
            lines.append(f"- [{c['part']} P[{c['p_idx']}] Comment #{c['id']} / {c['author']}]: {c['comment']}")
            lines.append(f"  - Native Word anchor ({c.get('anchor_status', 'unknown')}): {c.get('anchor_text') or '[not recoverable from range markers]'}")
            lines.append(f"  - Containing paragraph: {c['anchor_paragraph']}")
        if len(r["comments"]) > max_items:
            lines.append(f"- ... {len(r['comments']) - max_items} more comments in JSON inventory.")
        lines += ["", "### Placeholders"]
        lines += [f"- [{x['part']} P[{x['p_idx']}]] {', '.join(x['matches'])}: {x['text']}" for x in r["placeholders"][:max_items]]
        if len(r["placeholders"]) > max_items:
            lines.append(f"- ... {len(r['placeholders']) - max_items} more placeholder records in JSON inventory.")
        lines += ["", "### Chinese notes (review manually; not automatically treated as errors)"]
        lines += [f"- [{x['part']} P[{x['p_idx']}]] {x['text']}" for x in r["chinese_notes"][:max_items]]
        if len(r["chinese_notes"]) > max_items:
            lines.append(f"- ... {len(r['chinese_notes']) - max_items} more notes in JSON inventory.")
        lines += ["", "### Figures, tables and drawings"]
        lines += [f"- [{x['part']} P[{x['p_idx']}] {'[DRAWING]' if x['has_drawing'] else '[CAPTION]'}] {x['text']}" for x in r["captions_and_drawings"][:max_items]]
        if len(r["captions_and_drawings"]) > max_items:
            lines.append(f"- ... {len(r['captions_and_drawings']) - max_items} more figure/table records in JSON inventory.")
        if r["errors"]:
            lines += ["", "### Errors"] + [f"- {e}" for e in r["errors"]]
        lines += ["", "---", ""]
    if len(lines) > 300:
        lines = lines[:297] + ["", "[TRUNCATED] Full paragraph/comment and structure inventory is available in the JSON file."]
    md_target = Path(md_path)
    md_target.parent.mkdir(parents=True, exist_ok=True)
    md_target.write_text("\n".join(lines), encoding="utf-8")
    if json_path:
        json_target = Path(json_path)
        json_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

_RUN_PROPERTY_ORDER = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
    "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "noProof",
    "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern",
    "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd",
    "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout",
    "specVanish", "oMath", "rPrChange",
)


def validate_plan(plan: Any, require_approval: bool = True, check_approval_integrity: bool = True, require_authorized_change: bool = False) -> list[str]:
    errors = []
    if not isinstance(plan, dict): return ["Plan must be a JSON object."]
    if plan.get("schema_version") != "1.0": errors.append("schema_version must be '1.0'.")
    if not isinstance(plan.get("approved"), bool): errors.append("approved must be a boolean.")
    if require_approval and plan.get("approved") is not True:
        errors.append("Human approval gate: top-level approved must be true.")
    items = plan.get("revision_items")
    if not isinstance(items, list) or not items: return errors + ["revision_items must be a non-empty array."]
    seen = set()
    for i, item in enumerate(items):
        at = f"revision_items[{i}]"
        if not isinstance(item, dict): errors.append(f"{at} must be an object."); continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip(): errors.append(f"{at}.id is required.")
        elif item_id in seen: errors.append(f"Duplicate revision item id: {item_id}")
        else: seen.add(item_id)
        if not isinstance(item.get("reviewer"), str) or not item["reviewer"].strip(): errors.append(f"{at}.reviewer is required.")
        if not isinstance(item.get("comment"), str) or not item["comment"].strip(): errors.append(f"{at}.comment is required.")
        if item.get("status") not in {"planned", "approved", "applied", "verified", "blocked"}:
            errors.append(f"{at}.status must be planned, approved, applied, verified or blocked.")
        if "preflight_fingerprint" in item and (
            not isinstance(item["preflight_fingerprint"], str)
            or re.fullmatch(r"[a-f0-9]{64}", item["preflight_fingerprint"]) is None
        ):
            errors.append(f"{at}.preflight_fingerprint must be a lowercase SHA-256 hex digest.")
        if item.get("status") in {"approved", "applied", "verified"}:
            if plan.get("approved") is not True:
                errors.append(f"{at}.approved state is inconsistent: approved/applied/verified items require top-level approved=true.")
            if check_approval_integrity:
                stored = item.get("approval_fingerprint")
                expected = revision_item_fingerprint(item)
                if not isinstance(stored, str) or stored != expected:
                    errors.append(
                        f"{at}.approval_fingerprint is missing or stale; re-approve this revision item before applying or verifying it."
                    )
        targets = item.get("targets")
        if not isinstance(targets, list) or not targets: errors.append(f"{at}.targets must be non-empty."); continue
        for j, t in enumerate(targets):
            tp = f"{at}.targets[{j}]"
            if not isinstance(t, dict): errors.append(f"{tp} must be an object."); continue
            for field in ("document", "input_file", "output_file", "find"):
                if not isinstance(t.get(field), str): errors.append(f"{tp}.{field} must be a string.")
                elif not t[field].strip(): errors.append(f"{tp}.{field} cannot be empty.")
            if not isinstance(t.get("replace"), str):
                errors.append(f"{tp}.replace must be a string; use an empty string to delete text.")
            authorization = t.get("authorized_change")
            if authorization is None and (
                not require_authorized_change
                or item.get("status") not in {"approved", "applied", "verified"}
            ):
                pass
            elif not isinstance(authorization, dict) or not isinstance(authorization.get("intent"), str) or not authorization.get("intent", "").strip():
                errors.append(f"{tp}.authorized_change.intent is required; human approval seals this exact scope.")
            elif not isinstance(authorization.get("explicitly_authorized_tokens", []), list):
                errors.append(f"{tp}.authorized_change.explicitly_authorized_tokens must be an array.")
            else:
                for token_index, token in enumerate(authorization.get("explicitly_authorized_tokens", [])):
                    if not isinstance(token, dict) or not isinstance(token.get("text"), str) or not isinstance(token.get("replacement"), str):
                        errors.append(f"{tp}.authorized_change.explicitly_authorized_tokens[{token_index}] requires exact text and replacement strings.")
                approved_spans = authorization.get("explicitly_authorized_spans", [])
                if not isinstance(approved_spans, list) or any(not isinstance(span, dict) for span in approved_spans):
                    errors.append(f"{tp}.authorized_change.explicitly_authorized_spans must be an array of objects.")
            protected_spans = t.get("protected_spans", [])
            if not isinstance(protected_spans, list) or any(not isinstance(span, dict) for span in protected_spans):
                errors.append(f"{tp}.protected_spans must be an array of objects.")
            if isinstance(t.get("document"), str) and t["document"] not in {"Reviewers", "Manuscript", "SI", "Other"}:
                errors.append(f"{tp}.document must be Reviewers, Manuscript, SI or Other.")
            if isinstance(t.get("find"), str) and not t["find"].strip(): errors.append(f"{tp}.find cannot be empty.")
            if "source_sha256" in t and (
                not isinstance(t["source_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", t["source_sha256"]) is None
            ):
                errors.append(f"{tp}.source_sha256 must be a lowercase SHA-256 hex digest.")
            if "p_idx" in t and (not isinstance(t["p_idx"], int) or isinstance(t["p_idx"], bool) or t["p_idx"] < 0): errors.append(f"{tp}.p_idx must be a non-negative integer.")
            if "part" in t:
                try: safe_part(str(t["part"]))
                except ValueError as exc: errors.append(f"{tp}.part: {exc}")
        if item.get("status") == "verified":
            if item.get("semantic_review_confirmed") is not True:
                errors.append(f"{at}.semantic_review_confirmed must be true for verified items.")
            verified_at = item.get("verified_at_utc")
            try:
                parsed_verified_at = datetime.fromisoformat(str(verified_at).replace("Z", "+00:00"))
                if parsed_verified_at.tzinfo is None:
                    raise ValueError("timezone is required")
            except ValueError:
                errors.append(f"{at}.verified_at_utc must be an ISO-8601 timestamp with timezone.")
            stored_verification = item.get("verification_fingerprint")
            expected_verification = revision_verification_fingerprint(item)
            if not isinstance(stored_verification, str) or stored_verification != expected_verification:
                errors.append(f"{at}.verification_fingerprint is missing or stale; re-run verification and confirm semantic review.")
            output_hashes = item.get("verified_output_hashes")
            if not isinstance(output_hashes, list) or not output_hashes:
                errors.append(f"{at}.verified_output_hashes must record every revised DOCX output.")
            else:
                expected_paths = {
                    os.path.normcase(str(Path(target["output_file"]).expanduser().resolve()))
                    for target in targets
                    if isinstance(target, dict) and isinstance(target.get("output_file"), str) and target.get("output_file")
                }
                recorded_paths = set()
                for hash_index, entry in enumerate(output_hashes):
                    if not isinstance(entry, dict):
                        errors.append(f"{at}.verified_output_hashes[{hash_index}] must be an object.")
                        continue
                    path_value, digest = entry.get("path"), entry.get("sha256")
                    if not isinstance(path_value, str) or not path_value.strip():
                        errors.append(f"{at}.verified_output_hashes[{hash_index}].path is required.")
                        continue
                    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
                        errors.append(f"{at}.verified_output_hashes[{hash_index}].sha256 must be a lowercase SHA-256 hex digest.")
                        continue
                    resolved_path = str(Path(path_value).expanduser().resolve())
                    path_key = os.path.normcase(resolved_path)
                    if path_key in recorded_paths:
                        errors.append(f"{at}.verified_output_hashes contains duplicate output paths.")
                        continue
                    recorded_paths.add(path_key)
                    try:
                        if not Path(resolved_path).is_file():
                            errors.append(f"{at}.verified_output_hashes references a missing DOCX: {resolved_path}")
                        elif sha256_file(resolved_path) != digest:
                            errors.append(f"{at}.verified_output_hashes is stale; revised DOCX changed after verification: {resolved_path}")
                    except OSError as exc:
                        errors.append(f"{at}.verified_output_hashes cannot read {resolved_path}: {exc}")
                if recorded_paths != expected_paths:
                    errors.append(f"{at}.verified_output_hashes must cover exactly the linked DOCX outputs.")
    return errors

def preflight_plan(plan: dict[str, Any], author: str = "Co_Reviewer Preflight") -> dict[str, Any]:
    """Validate eligible anchors and bind each target to its source DOCX version without writing DOCX files."""
    errors = validate_plan(plan, require_approval=False, check_approval_integrity=False)
    if errors:
        raise ValueError("Revision plan validation failed:\n- " + "\n- ".join(errors))

    eligible_items = [
        item for item in plan["revision_items"]
        if item.get("status") in {"planned", "approved"}
    ]
    if not eligible_items:
        raise ValueError("No planned or approved revision items are available for preflight.")

    grouped = defaultdict(list)
    target_refs = []
    for item in eligible_items:
        for target in item["targets"]:
            edit = {
                "find": target["find"], "replace": target["replace"],
                "part": target.get("part", "word/document.xml"),
                "revision_item_id": item["id"], "reviewer": item["reviewer"],
                "document": target["document"], "location": target.get("location", ""),
                "operation_id": target.get("operation_id"),
                "source_comment_ids": list(target.get("source_comment_ids", [])),
                "authorized_change": target.get("authorized_change"),
                "protected_spans": list(target.get("protected_spans", [])),
            }
            if "p_idx" in target:
                edit["p_idx"] = target["p_idx"]
            grouped[(target["input_file"], target["output_file"])].append(edit)
            target_refs.append((item, target))

    outputs = [_canonical_artifact_path(out) for _, out in grouped]
    inputs = [_canonical_artifact_path(inp) for inp, _ in grouped]
    if len(outputs) != len(set(outputs)):
        raise ValueError("Different input groups cannot write to the same output.")
    if set(outputs) & set(inputs):
        raise ValueError("An output path conflicts with an input path in this revision plan.")

    source_paths = {
        _canonical_artifact_path(inp): str(Path(inp).expanduser().resolve())
        for inp, _ in grouped
    }
    # Initial Manuscript/SI baselines are gated at document import/identity registration.
    # Do not re-run that gate here: an incremental batch may legitimately use a prior
    # Co_Reviewer Marked DOCX as its source, and accepted_view_docx_bytes resolves its
    # earlier Track Changes before preparing the next operation.
    source_hashes_before = {
        key: sha256_file(path) for key, path in source_paths.items()
    }

    prepared = []
    for (inp, out), edits in grouped.items():
        prepared.append(prepare_document(inp, out, edits, author))

    # Generate each operation's unique final-text locator during Preflight.
    from co_reviewer.word_pdf_mapper import build_unique_location_phrase
    targets_by_operation: dict[str, dict[str, Any]] = {}
    for item in eligible_items:
        for target in item["targets"]:
            operation_id = target.get("operation_id")
            # P0 legacy plans predate typed PatchOperation IDs. Keep their
            # native DOCX preflight/apply path operational; only a typed
            # RevisionLedger (checked before this function) requires exact IDs.
            if not isinstance(operation_id, str) or not operation_id.strip():
                continue
            if operation_id in targets_by_operation:
                raise ValueError(f"Duplicate operation_id in revision plan: {operation_id}.")
            targets_by_operation[operation_id] = target

    for ((inp, _out), edits), prepared_entry in zip(grouped.items(), prepared):
        source_bytes = accepted_view_docx_bytes(Path(inp).expanduser().read_bytes())
        with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as accepted_archive:
            accepted_root = parse_xml(accepted_archive.read("word/document.xml"))
        body_paragraphs = accepted_root.findall(".//w:body//w:p", NS)
        paragraph_texts = [visible_text(paragraph) for paragraph in body_paragraphs]
        tracked_results = prepared_entry["report"].get("results", [])
        if len(tracked_results) != len(edits):
            raise ValueError("Preflight result count does not match its prepared target list.")
        spans_by_operation: dict[str, tuple[int, int, int]] = {}
        for result in tracked_results:
            operation_id = result.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id.strip():
                # P0 plans remain executable but are not eligible for typed
                # ResponseUnit automatic location mapping.
                continue
            if operation_id not in targets_by_operation:
                raise ValueError(f"Prepared target has unknown operation_id {operation_id!r}.")
            target = targets_by_operation[operation_id]
            if target.get("part", "word/document.xml") != "word/document.xml":
                # Header/footer and other non-body parts are kept eligible for
                # exact revision application, but their locators are entered
                # through the explicit manual_required pathway.
                continue
            paragraph_index = result.get("p_idx")
            if not isinstance(paragraph_index, int) or not 0 <= paragraph_index < len(paragraph_texts):
                raise ValueError(f"Preflight could not resolve paragraph index for {operation_id}.")
            current_text = paragraph_texts[paragraph_index]
            old_text, new_text = str(result.get("find", "")), str(result.get("replace", ""))
            if current_text.count(old_text) != 1:
                raise ValueError(f"Preflight could not reproduce the unique text anchor for {operation_id}; no location phrase was guessed.")
            start = current_text.find(old_text)
            delta = len(new_text) - len(old_text)
            for prior_id, (prior_p, prior_start, prior_end) in list(spans_by_operation.items()):
                if prior_p != paragraph_index:
                    continue
                if start + len(old_text) <= prior_start:
                    spans_by_operation[prior_id] = (prior_p, prior_start + delta, prior_end + delta)
                elif start >= prior_end:
                    continue
                else:
                    # A later operation edits inside an earlier target. Both ResponseUnits
                    # point to the final surviving replacement span, not obsolete text.
                    spans_by_operation[prior_id] = (prior_p, start, start + len(new_text))
            paragraph_texts[paragraph_index] = current_text[:start] + new_text + current_text[start + len(old_text):]
            spans_by_operation[operation_id] = (paragraph_index, start, start + len(new_text))
        for operation_id, (paragraph_index, start, end) in spans_by_operation.items():
            phrase = build_unique_location_phrase(paragraph_texts, paragraph_index, start, end)
            target = targets_by_operation[operation_id]
            target["p_idx"] = paragraph_index
            target["location_phrase"] = phrase

    source_hashes_after = {
        key: sha256_file(path) for key, path in source_paths.items()
    }
    if source_hashes_before != source_hashes_after:
        raise ValueError("A source DOCX changed during preflight; no source fingerprints were recorded. Retry preflight.")

    changed_items = []
    for item in eligible_items:
        source_version_changed = False
        for target in item["targets"]:
            key = _canonical_artifact_path(target["input_file"])
            new_hash = source_hashes_after[key]
            if target.get("source_sha256") != new_hash:
                source_version_changed = True
            target["source_sha256"] = new_hash
        new_fingerprint = revision_targets_fingerprint(item)
        previous_fingerprint = item.get("preflight_fingerprint")
        target_scope_changed = (
            previous_fingerprint is not None
            and previous_fingerprint != new_fingerprint
        )
        if item.get("status") == "approved" and (source_version_changed or target_scope_changed):
            changed_items.append(item["id"])
        item["preflight_fingerprint"] = new_fingerprint

    document_reports = []
    for entry in prepared:
        document_report = copy.deepcopy(entry["report"])
        document_report["status"] = "preflighted"
        document_report["edits_prepared"] = document_report.pop("edits_applied", 0)
        for result in document_report.get("results", []):
            result["status"] = "preflighted"
            result.pop("delete_revision_id", None)
            result.pop("insert_revision_id", None)
            target = targets_by_operation.get(result.get("operation_id"))
            if target is not None:
                result["location_phrase"] = target.get("location_phrase")
        document_reports.append(document_report)
    results = [
        result
        for document_report in document_reports
        for result in document_report.get("results", [])
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "targets_total": len(target_refs),
        "targets_preflighted": len(results),
        "source_files": [
            {"input_file": path, "sha256": source_hashes_after[key]}
            for key, path in source_paths.items()
        ],
        "approval_scope_changed_items": sorted(set(changed_items)),
        "preflight_items": [
            {"revision_item_id": item["id"], "preflight_fingerprint": item["preflight_fingerprint"]}
            for item in eligible_items
        ],
        "documents": document_reports,
        "results": results
    }


def apply_plan(plan: dict[str, Any], author: str) -> list[dict[str, Any]]:
    errors = validate_plan(plan, True)
    if errors: raise ValueError("Revision plan validation failed:\n- " + "\n- ".join(errors))
    approved_items = [item for item in plan["revision_items"] if item.get("status") == "approved"]
    if not approved_items:
        raise ValueError("No revision items are currently approved for application.")
    authorization_errors = validate_plan(plan, True, require_authorized_change=True)
    if authorization_errors:
        raise ValueError("Revision plan authorization validation failed:\n- " + "\n- ".join(authorization_errors))
    grouped = defaultdict(list)
    for item in approved_items:
        for target in item["targets"]:
            edit = {
                "find": target["find"], "replace": target["replace"],
                "part": target.get("part", "word/document.xml"),
                "revision_item_id": item["id"], "reviewer": item["reviewer"],
                "document": target["document"], "location": target.get("location", ""),
                "operation_id": target.get("operation_id"),
                "source_comment_ids": list(target.get("source_comment_ids", [])),
                "location_phrase": target.get("location_phrase"),
                "authorized_change": target.get("authorized_change"),
                "protected_spans": list(target.get("protected_spans", [])),
            }
            if "p_idx" in target: edit["p_idx"] = target["p_idx"]
            grouped[(target["input_file"], target["output_file"])].append(edit)
    outputs = [str(Path(out).expanduser().resolve()) for _, out in grouped]
    inputs = [str(Path(inp).expanduser().resolve()) for inp, _ in grouped]
    if len(outputs) != len(set(outputs)): raise ValueError("Different input groups cannot write to the same output.")
    if set(outputs) & set(inputs): raise ValueError("An output path conflicts with an input path in this revision plan.")
    # Verify each preflighted source file before and after preparing the edits.
    # The approval fingerprint covers source_sha256 when it is present.
    source_hashes = {}
    for item in approved_items:
        expected_preflight = item.get("preflight_fingerprint")
        if expected_preflight is not None and (
            not isinstance(expected_preflight, str)
            or expected_preflight != revision_targets_fingerprint(item)
        ):
            raise ValueError(
                f"{item['id']} target scope has changed since preflight. "
                "Run preflight-plan again, review its refreshed report, and obtain renewed approval."
            )
        for target in item["targets"]:
            expected = target.get("source_sha256")
            if expected is None:
                continue  # Backward compatibility for plans created before source preflight.
            source_path = str(Path(target["input_file"]).expanduser().resolve())
            actual = sha256_file(source_path)
            if actual != expected:
                raise ValueError(
                    f"Source DOCX changed since preflight for {item['id']}: {source_path}. "
                    "Run preflight-plan again, review the refreshed plan, and obtain renewed approval."
                )
            source_hashes[source_path] = expected

    # Prepare every document in memory first. A bad target in a later file therefore
    # cannot leave earlier output files partially generated.
    prepared = []
    for (inp, out), edits in grouped.items():
        prepared.append(prepare_document(inp, out, edits, author))

    for source_path, expected in source_hashes.items():
        if sha256_file(source_path) != expected:
            raise ValueError(
                f"Source DOCX changed during apply preparation: {source_path}. No output was written."
            )
    # Marked delivery files retain native Track Changes but never carry internal Word comment bubbles.
    for item in prepared:
        cleaned_payload, hygiene = strip_docx_comments_bytes(item["payload"])
        item["payload"] = cleaned_payload
        # The report and RevisionLedger lineage must identify the exact bytes
        # that will be written, after comment-package hygiene has transformed
        # the prepared DOCX. Keeping the pre-hygiene hash breaks Marked lineage.
        item["report"]["output_sha256"] = hashlib.sha256(cleaned_payload).hexdigest()
        item["report"]["comment_hygiene"] = hygiene
    return [write_prepared_document(item) for item in prepared]


def _untracked_paragraph_text(paragraph: ET.Element) -> str:
    """Collect paragraph text outside tracked insertion/deletion/move wrappers."""
    excluded = {qn("ins"), qn("del"), qn("moveFrom"), qn("moveTo")}
    output: list[str] = []

    def visit(node: ET.Element) -> None:
        if node.tag in excluded:
            return
        if node.tag == qn("t") and node.text:
            output.append(node.text)
        elif node.tag == qn("tab"):
            output.append("\t")
        elif node.tag == qn("br"):
            output.append("\n")
        for child in node:
            visit(child)

    visit(paragraph)
    return "".join(output)


def _tracked_text_parts(archive: zipfile.ZipFile) -> dict[str, list[str]]:
    """Read text-bearing Word parts used by the DOCX inventory."""
    result: dict[str, list[str]] = {}
    for name in archive.namelist():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        if not (
            name == "word/document.xml"
            or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            or name in {"word/footnotes.xml", "word/endnotes.xml"}
        ):
            continue
        root = parse_xml(archive.read(name))
        paragraphs = (
            root.findall(".//w:body//w:p", NS)
            if name == "word/document.xml"
            else root.findall(".//w:p", NS)
        )
        result[name] = [_untracked_paragraph_text(paragraph) for paragraph in paragraphs]
    return result


def audit_untracked_text_changes(apply_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Detect text changed outside native Track Changes in every applied DOCX output."""
    violations: list[dict[str, Any]] = []
    documents_checked = 0

    for document_report in apply_reports:
        edits = [
            edit for edit in document_report.get("results", [])
            if isinstance(edit, dict) and edit.get("status") == "applied"
        ]
        if not edits:
            continue
        input_file = document_report.get("input_file")
        output_file = document_report.get("output_file")
        if not input_file or not output_file:
            violations.append({
                "output_file": output_file or "",
                "reason": "Apply report is missing input_file/output_file; untracked-edit audit cannot run."
            })
            continue
        source_path = Path(input_file).expanduser()
        output_path = Path(output_file).expanduser()
        try:
            with zipfile.ZipFile(source_path, "r") as source_archive:
                source_parts = _tracked_text_parts(source_archive)
            with zipfile.ZipFile(output_path, "r") as output_archive:
                output_parts = _tracked_text_parts(output_archive)
        except (OSError, KeyError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
            violations.append({
                "input_file": str(source_path),
                "output_file": str(output_path),
                "reason": f"Unable to audit untracked text changes: {exc}"
            })
            continue

        documents_checked += 1
        expected_parts = copy.deepcopy(source_parts)
        for edit in edits:
            part = edit.get("part", "word/document.xml")
            p_idx = edit.get("p_idx")
            find_text = edit.get("find", "")
            paragraphs = expected_parts.get(part)
            if paragraphs is None or not isinstance(p_idx, int) or isinstance(p_idx, bool) or not (0 <= p_idx < len(paragraphs)):
                violations.append({
                    "input_file": str(source_path), "output_file": str(output_path),
                    "part": part, "p_idx": p_idx,
                    "reason": "Applied target cannot be mapped to the source paragraph for untracked-edit audit."
                })
                continue
            if not isinstance(find_text, str) or paragraphs[p_idx].count(find_text) != 1:
                violations.append({
                    "input_file": str(source_path), "output_file": str(output_path),
                    "part": part, "p_idx": p_idx,
                    "reason": "The source anchor is not unique in untracked source text; audit cannot safely establish the expected baseline."
                })
                continue
            paragraphs[p_idx] = paragraphs[p_idx].replace(find_text, "", 1)

        for part in sorted(set(expected_parts) | set(output_parts)):
            expected = expected_parts.get(part)
            actual = output_parts.get(part)
            if expected != actual:
                violations.append({
                    "input_file": str(source_path), "output_file": str(output_path),
                    "part": part,
                    "reason": "Text outside native w:ins/w:del/w:moveFrom/w:moveTo differs from the source after accounting for approved deletions. This indicates a direct/untracked edit or an unsupported Word rewrite.",
                    "expected_untracked_paragraphs": expected,
                    "actual_untracked_paragraphs": actual
                })

    return {
        "passed": not violations,
        "documents_checked": documents_checked,
        "violations": violations
    }


def verify_apply_report(plan: dict[str, Any], apply_reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Mechanically verify each planned Track Changes edit in its generated DOCX."""
    report_index = {}
    for document_report in apply_reports:
        output_path = str(Path(document_report.get("output_file", "")).expanduser().resolve())
        for edit in document_report.get("results", []):
            key = (
                edit.get("revision_item_id"), edit.get("document"),
                output_path, edit.get("part", "word/document.xml"),
                edit.get("find"), edit.get("replace")
            )
            report_index.setdefault(key, []).append(edit)

    results = []
    for item in plan.get("revision_items", []):
        if item.get("status") not in {"applied", "verified"}:
            continue
        item_id = item.get("id", "")
        for target in item.get("targets", []):
            output_path = str(Path(target["output_file"]).expanduser().resolve())
            part = target.get("part", "word/document.xml")
            key = (item_id, target["document"], output_path, part, target["find"], target["replace"])
            matches = report_index.get(key, [])
            result = {
                "revision_item_id": item_id, "document": target["document"],
                "output_file": output_path, "part": part, "location": target.get("location", ""),
                "find": target["find"], "replace": target["replace"],
                "status": "failed", "checks": {}
            }
            if len(matches) != 1:
                result["reason"] = "Apply report is missing this target or contains duplicate records."
                results.append(result)
                continue
            edit = matches[0]
            if edit.get("status") != "applied":
                result["reason"] = "Apply report does not mark the edit as applied."
                results.append(result)
                continue
            source = Path(output_path)
            if not source.is_file():
                result["reason"] = "Revised output file does not exist."
                results.append(result)
                continue
            try:
                with zipfile.ZipFile(source, "r") as archive:
                    bad_member = archive.testzip()
                    if bad_member:
                        raise ValueError(f"ZIP CRC error in {bad_member}")
                    if part not in archive.namelist():
                        raise ValueError(f"Word XML part is missing: {part}")
                    root = parse_xml(archive.read(part))
                paragraphs = root.findall(".//w:body//w:p", NS) if part == "word/document.xml" else root.findall(".//w:p", NS)
                p_idx = edit.get("p_idx")
                if not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0 or p_idx >= len(paragraphs):
                    raise ValueError("Paragraph index from apply report is outside the output document.")
                paragraph = paragraphs[p_idx]
                delete_id = edit.get("delete_revision_id")
                insert_id = edit.get("insert_revision_id")
                delete_nodes = [
                    node for node in paragraph.findall(".//w:del", NS)
                    if node.get(qn("id")) == str(delete_id)
                ]
                deleted = "".join(
                    text_node.text or ""
                    for node in delete_nodes
                    for text_node in node.findall(".//w:delText", NS)
                )
                if insert_id is None:
                    insert_nodes = []
                    inserted = ""
                else:
                    insert_nodes = [
                        node for node in paragraph.findall(".//w:ins", NS)
                        if node.get(qn("id")) == str(insert_id)
                    ]
                    inserted = "".join(
                        text_node.text or ""
                        for node in insert_nodes
                        for text_node in node.findall(".//w:t", NS)
                    )
                old_ok = len(delete_nodes) == 1 and deleted == target["find"]
                new_text = edit.get("inserted_text", target["replace"])
                new_ok = (
                    len(insert_nodes) == 1 and inserted == new_text
                    if new_text else insert_id is None and not inserted
                )
                inserted_runs = [
                    run for node in insert_nodes
                    for run in node.findall(".//w:r", NS)
                ]
                expected_run_formats = edit.get("inserted_run_formats")
                if isinstance(expected_run_formats, list):
                    actual_run_formats = []
                    for run in inserted_runs:
                        run_text = "".join(
                            node.text or "" for node in run.findall("w:t", NS)
                        )
                        vertical = run.find("w:rPr/w:vertAlign", NS)
                        actual_run_formats.append({
                            "text": run_text,
                            "vertical_alignment": (
                                vertical.get(qn("val")) if vertical is not None else None
                            )
                        })
                    expected_script_signature = [
                        (character, entry.get("vertical_alignment"))
                        for entry in expected_run_formats
                        for character in entry.get("text", "")
                    ]
                    actual_script_signature = [
                        (character, entry.get("vertical_alignment"))
                        for entry in actual_run_formats
                        for character in entry.get("text", "")
                    ]
                    # Word may split/merge adjacent runs when saving. Compare
                    # per-character formatting, not incidental run boundaries.
                    native_script_format_preserved = (
                        actual_script_signature == expected_script_signature
                    )
                else:
                    native_script_format_preserved = True
                result["checks"] = {
                    "output_exists": True, "zip_crc": "passed", "paragraph_found": True,
                    "delete_revision_id": delete_id, "insert_revision_id": insert_id,
                    "exact_deletion_match": old_ok, "exact_insertion_match": new_ok,
                    "native_script_format_preserved": native_script_format_preserved
                }
                result["p_idx"] = p_idx
                if old_ok and new_ok and native_script_format_preserved:
                    result["status"] = "verified"
                elif not native_script_format_preserved:
                    result["reason"] = "Native subscript/superscript run formatting changed after the apply/save operation."
                else:
                    result["reason"] = "Expected deleted or inserted text was not found in the target paragraph."
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
                result["reason"] = str(exc)
            results.append(result)

    passed = sum(1 for item in results if item["status"] == "verified")
    untracked_audit = audit_untracked_text_changes(apply_reports)
    target_passed = bool(results) and passed == len(results)
    return {
        "scope": "Mechanical DOCX Track Changes verification and untracked-text audit only; scientific correctness and cross-document semantic consistency require separate review.",
        "targets_total": len(results), "targets_verified": passed,
        "targets_failed": len(results) - passed,
        "untracked_text_audit": untracked_audit,
        "passed": target_passed and untracked_audit["passed"],
        "results": results
    }



def _validate_active_workflow_audit_sources(args, workflow_ledger_path: str | Path) -> dict[str, str]:
    """Allow Marked active DOCX inputs only when paths/hashes equal published Workflow pointers."""
    from co_reviewer.batch_store import load_active_document_pointers
    from co_reviewer.workflow_engine import validate_workflow_ledger

    workflow_path = Path(workflow_ledger_path).expanduser().resolve()
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise ValueError(
            "Cannot run an active-version Audit from an invalid Workflow Ledger:\n- "
            + "\n- ".join(errors)
        )
    pointers = load_active_document_pointers(workflow)
    selected = {
        role: Path(path).expanduser().resolve()
        for role, path in (
            ("Reviewers", getattr(args, "rev", None)),
            ("Manuscript", getattr(args, "man", None)),
            ("SI", getattr(args, "si", None)),
        )
        if path
    }
    expected_roles = set(pointers)
    if set(selected) != expected_roles:
        missing = sorted(expected_roles - set(selected))
        extra = sorted(set(selected) - expected_roles)
        details = []
        if missing:
            details.append("missing active role(s): " + ", ".join(missing))
        if extra:
            details.append("unconfigured role(s): " + ", ".join(extra))
        raise ValueError(
            "Active-version Audit must include exactly the current Workflow Ledger roles ("
            + "; ".join(details)
            + ")."
        )

    expected_hashes: dict[str, str] = {}
    for role, path in selected.items():
        pointer = pointers[role]
        expected_path = Path(pointer["file"]).expanduser().resolve()
        expected_sha = pointer["sha256"]
        if path != expected_path:
            raise ValueError(
                f"Active-version Audit input path for {role} does not match the Workflow Ledger pointer: "
                f"expected {expected_path}; received {path}."
            )
        actual_sha = sha256_file(path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"Active-version Audit input hash for {role} does not match the published Workflow Ledger pointer: "
                f"expected {expected_sha}; received {actual_sha}; file={path}."
            )
        expected_hashes[role] = expected_sha
    return expected_hashes


def _inspect_selected_files(args):
    files = [("Reviewers", args.rev), ("Manuscript", args.man), ("SI", args.si)]
    selected = [(role, path) for role, path in files if path]
    if not selected:
        raise ValueError("Provide at least one of --rev, --man or --si.")
    usage_ledger = getattr(args, "usage_ledger", None)
    if usage_ledger:
        validate_inspection_artifact_paths(
            [path for _, path in selected], [usage_ledger]
        )
    cache_dir = getattr(args, "cache_dir", ".co-reviewer-cache")
    refresh = bool(getattr(args, "refresh_cache", False))
    reports = []
    cache_results = []
    for role, path in selected:
        result, cache_status = load_cached_inspection(
            path, args.comment_author, cache_dir, inspect_docx, refresh=refresh
        )
        result["role"] = role
        active_expected_hashes = getattr(args, "active_expected_sha256_by_role", {})
        if role in active_expected_hashes and (
            result.get("source_sha256") != active_expected_hashes[role]
            or sha256_file(path) != active_expected_hashes[role]
        ):
            raise ValueError(
                f"{role} changed during active Workflow Ledger SHA verification/inspection; refusing to audit a different document version."
            )
        if role in {"Manuscript", "SI"}:
            baseline = inspect_clean_baseline(
                path,
                allow_tracked_text_revisions=role in active_expected_hashes,
            )
            result["baseline_hygiene"] = baseline
            if role in active_expected_hashes and baseline.get("source_sha256") != active_expected_hashes[role]:
                raise ValueError(
                    f"{role} changed during active-version baseline inspection; refusing a mixed-version inventory."
                )
            if not baseline["clean"]:
                result.setdefault("errors", []).append(
                    f"Baseline is not clean for {role}: unresolved Word revision/comment markup "
                    f"{baseline['blocking_counts']}. Initial round inputs must be clean; active versions may contain only published text insertions/deletions."
                )
        reports.append(result)
        cache_results.append((str(path), cache_status))
        usage_ledger = getattr(args, "usage_ledger", None)
        if usage_ledger:
            paragraphs = result.get("paragraphs", [])
            parse_succeeded = not result.get("errors")
            record_usage_event(usage_ledger, {
                "event_type": "document_read",
                "stage": "parse_inventory",
                "actor": "system",
                "document_read": {
                    "role": role,
                    "path": str(Path(path).expanduser().resolve()),
                    "source_sha256": result.get("source_sha256"),
                    "cache_status": cache_status,
                    "parsed": cache_status == "miss",
                    "parse_succeeded": parse_succeeded,
                    "parse_error_count": len(result.get("errors", [])),
                    "full_text_extracted": cache_status == "miss" and parse_succeeded,
                    "paragraphs": int(result.get("paragraph_count", len(paragraphs))),
                    "characters": sum(
                        len(str(row.get("text") or "")) for row in paragraphs
                        if isinstance(row, dict)
                    ),
                    "context_scope": "none",
                    "measurement_scope": "programmatic_docx_inventory",
                },
            })
    return reports, cache_summary(cache_results)


def cmd_inspect(args):
    source_paths = [path for path in (args.rev, args.man, args.si) if path]
    output_paths = [args.out] + ([args.json_out] if args.json_out else [])
    validate_inspection_artifact_paths(source_paths, output_paths)
    reports, cache = _inspect_selected_files(args)
    write_reports(reports, args.out, args.json_out or None, max(1, args.max_items_per_section))
    count_errors = sum(len(x["errors"]) for x in reports)
    print(f"[OK] Markdown: {args.out}")
    if args.json_out: print(f"[OK] JSON: {args.json_out}")
    print(f"[CACHE] {cache['hits']} hit(s), {cache['misses']} parse(s); directory: {getattr(args, 'cache_dir', '.co-reviewer-cache')}")
    print(f"[SUMMARY] {len(reports)} document(s); {sum(x['paragraph_count'] for x in reports)} paragraphs; {sum(len(x['comments']) for x in reports)} comments; {count_errors} error(s)")
    return 1 if count_errors else 0


def cmd_inspect_docx_structure(args):
    source_path = Path(args.docx).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve() if args.out else None
    if output_path is not None and output_path == source_path:
        raise ValueError("Structure inventory output must not overwrite the source DOCX.")
    report = classify_docx_structure(source_path)
    if output_path is not None:
        write_json_atomic(output_path, report)
        print(f"[OK] Structure inventory: {output_path}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    summary = report["summary"]
    print(
        f"[STRUCTURE] {summary['package_parts']} package part(s); "
        f"{summary['xml_parts']} XML part(s); {summary['structures']} structure(s); "
        f"{len(report['issues'])} issue(s); package={report['package_status']}"
    )
    print(f"[STRUCTURE STATUS] {summary['structures_by_status']}")
    return 0 if report["package_status"] == "READY" else 2


def cmd_audit(args):
    source_paths = [path for path in (args.rev, args.man, args.si) if path]
    output_paths = [args.out, args.audit_md_out, args.audit_out, args.decision_queue_out]
    output_paths.extend([args.json_out] if args.json_out else [])
    active_workflow_ledger = getattr(args, "active_workflow_ledger", None)
    if active_workflow_ledger:
        workflow_path = Path(active_workflow_ledger).expanduser().resolve()
        if workflow_path in {
            Path(path).expanduser().resolve() for path in output_paths if path
        }:
            raise ValueError("Active Workflow Ledger path must be distinct from every Audit output.")
        args.active_expected_sha256_by_role = _validate_active_workflow_audit_sources(
            args, workflow_path
        )
    validate_inspection_artifact_paths(source_paths, output_paths)
    reports, cache = _inspect_selected_files(args)
    audit = build_review_audit(reports)
    decision_queue = build_decision_queue(reports, audit, getattr(args, "comment_ids", None))
    write_reports(reports, args.out, args.json_out or None, max(1, args.max_items_per_section))
    write_audit_markdown(audit, args.audit_md_out)
    write_json_atomic(args.audit_out, audit)
    write_json_atomic(args.decision_queue_out, decision_queue)
    count_errors = sum(len(x["errors"]) for x in reports)
    print(f"[OK] Workspace digest: {args.out}")
    if args.json_out: print(f"[OK] Parsed inventory: {args.json_out}")
    print(f"[OK] Deterministic audit: {args.audit_md_out} / {args.audit_out}")
    print(f"[OK] Bounded AI decision queue: {args.decision_queue_out}")
    print(f"[CACHE] {cache['hits']} hit(s), {cache['misses']} parse(s); directory: {getattr(args, 'cache_dir', '.co-reviewer-cache')}")
    print(f"[SUMMARY] {len(reports)} document(s); {sum(x['paragraph_count'] for x in reports)} paragraphs; {audit['comment_count']} reviewer comment(s); {count_errors} error(s)")
    print(f"[MATCHES] {audit['comment_target_status_counts']}")
    numbering = audit["figure_table_numbering"]
    print(f"[NUMBERING] {len(numbering['unresolved_references'])} workspace-unresolved reference(s); {len(numbering.get('ambiguous_references', []))} ambiguous/repeated target(s); {len(numbering['duplicate_labels_across_documents'])} cross-document label group(s); {len(numbering['unreferenced_captions'])} unreferenced caption(s)")
    summary = numbering.get("summary", {})
    if summary:
        print(
            "[NUMBERING DETAIL] "
            f"same-document={summary.get('resolved_same_document', 0)}; "
            f"cross-document={summary.get('resolved_cross_document', 0)}; "
            f"subpanel-to-main-figure={summary.get('subpanel_resolved', 0)}; "
            f"internal-same-label-different-caption={summary.get('same_document_same_label_different_captions', 0)}; "
            f"cross-document-same-label-different-caption={summary.get('cross_document_same_label_different_captions', 0)}"
        )
    return 1 if count_errors else 0

def cmd_record_usage_event(args):
    event_path = Path(args.event).expanduser().resolve()
    ledger_path = Path(args.ledger).expanduser().resolve()
    if event_path == ledger_path:
        raise ValueError("Usage event input and append-only ledger must be different files.")
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Usage event input must be one JSON object.")
    event = record_usage_event(ledger_path, payload)
    print(f"[OK] Usage event {event['event_id']} appended to {Path(args.ledger).resolve()}")
    return 0


def cmd_audit_usage(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    protected_inputs = {ledger_path}
    if args.pricing:
        pricing_path = Path(args.pricing).expanduser().resolve()
        protected_inputs.add(pricing_path)
    output_path = Path(args.out).expanduser().resolve() if args.out else None
    if output_path and output_path in protected_inputs:
        raise ValueError("Usage audit report must not overwrite its ledger or pricing input.")
    events, errors = read_usage_events(ledger_path)
    report = build_usage_report(events, errors)
    if args.pricing:
        pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
        report["cost_estimate"] = estimate_usage_cost(events, pricing)
    if output_path:
        write_json_atomic(output_path, report)
        print(f"[OK] Usage audit: {Path(args.out).resolve()}")
    docs = report["documents"]
    print(
        f"[USAGE] events={report['event_count']}; model events={report['model_usage_event_count']}; "
        f"document reads={docs['events']}; full-text extractions={docs['full_text_extractions']}; "
        f"cache hit/miss={docs['cache_hits']}/{docs['cache_misses']}"
    )
    if errors:
        print(f"[WARNING] {len(errors)} malformed ledger line(s); see report.")
    return 1 if errors else 0


def cmd_estimate_usage(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    pricing_path = Path(args.pricing).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    if output_path in {ledger_path, pricing_path}:
        raise ValueError("Usage estimate output must not overwrite its ledger or pricing input.")
    events, errors = read_usage_events(ledger_path)
    pricing = json.loads(pricing_path.read_text(encoding="utf-8"))
    estimate = estimate_usage_cost(events, pricing)
    estimate["invalid_ledger_lines"] = errors
    write_json_atomic(output_path, estimate)
    print(
        f"[COST ESTIMATE] {estimate['estimated_events']} event(s) priced; "
        f"{len(estimate['unpriced_event_ids'])} unpriced; total={estimate['total_estimated_cost']} "
        f"{estimate['currency']}"
    )
    print(f"[OK] Estimate report: {Path(args.out).resolve()}")
    return 1 if errors else 0


def cmd_audit_docx_quality(args):
    protected_inputs = {Path(args.docx).expanduser().resolve()}
    if args.pdf:
        protected_inputs.add(Path(args.pdf).expanduser().resolve())
    output_path = Path(args.out).expanduser().resolve() if args.out else None
    if output_path and output_path in protected_inputs:
        raise ValueError("DOCX quality report must not overwrite its DOCX or PDF input.")
    report = audit_docx_quality(
        args.docx, profile=args.profile, pdf_path=args.pdf,
        expected_font=args.expected_font,
    )
    if output_path:
        write_json_atomic(output_path, report)
        print(f"[OK] DOCX quality report: {Path(args.out).resolve()}")
    print(
        f"[DOCX QUALITY] {report['status']}; "
        f"passed={report['summary']['passed']}; warnings={report['summary']['warnings']}; "
        f"failures={report['summary']['failures']}"
    )
    return 1 if report["status"] == "FAIL" else 0


def cmd_validate(args):
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    errors = validate_plan(plan, not args.allow_unapproved)
    if errors:
        print("[INVALID]\n" + "\n".join("- " + x for x in errors)); return 2
    print(f"[VALID] {len(plan['revision_items'])} revision item(s); {sum(len(x['targets']) for x in plan['revision_items'])} target(s).")
    if args.allow_unapproved: print("[NOTE] Approval relaxed for validation only; apply always enforces it.")
    return 0

def write_json_atomic(path: str | Path, data: Any) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline=None,
            prefix=f".{target.name}.", suffix=".tmp",
            dir=target.parent, delete=False
        ) as handle:
            tmp = handle.name
            handle.write(payload)
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

def _batch_plan_snapshot(batch_dir: Path, stage: str) -> Path:
    from co_reviewer.batch_store import BatchStoreError
    target = batch_dir / f"revision_plan.{stage}.json"
    if target.exists():
        raise BatchStoreError(
            f"Immutable batch already contains the {stage} plan snapshot; allocate a new batch instead: {target}"
        )
    return target


def cmd_preflight(args):
    plan_path = Path(args.plan).expanduser().resolve()
    batch_dir = plan_path.parent if (plan_path.parent / "batch_intent.json").is_file() else None
    if batch_dir is not None and not getattr(args, "revision_ledger", None):
        raise ValueError("Preflight for an immutable batch requires --revision-ledger so Patch lifecycle evidence remains synchronized.")
    report_value = args.out or (
        str(batch_dir / "preflight_report.json") if batch_dir is not None else "preflight_report.json"
    )
    report_path = Path(report_value).expanduser().resolve()
    if batch_dir is not None:
        if batch_dir not in report_path.parents:
            raise ValueError("Preflight report for an immutable batch must be written inside its batch directory.")
        if report_path.exists():
            raise FileExistsError(f"Preflight report already exists for this immutable batch: {report_path}")
    output_plan_path = _batch_plan_snapshot(batch_dir, "preflighted") if batch_dir is not None else plan_path
    if batch_dir is not None and report_path == output_plan_path:
        raise ValueError("Preflight report and preflighted-plan snapshot must use different paths.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_apply_artifact_paths(plan, plan_path, report_path)

    revision_ledger = None
    ledger_path = None
    if getattr(args, "revision_ledger", None):
        ledger_path = Path(args.revision_ledger).expanduser().resolve()
        if ledger_path in {plan_path, output_plan_path, report_path}:
            raise ValueError("RevisionLedger path must be distinct from plan/preflight-report/snapshot paths.")
        revision_ledger = _load_revision_ledger(ledger_path)
        revision_ledger.assert_legacy_plan_matches(plan)

    report = preflight_plan(plan, args.author)
    if revision_ledger is not None:
        report_id = f"{report_path.name}:{report['generated_at']}"
        revision_ledger.record_legacy_preflight(
            report, plan, report_id=report_id, actor=args.author
        )

    # Batch plans/reports are append-only snapshots; the original plan remains unchanged.
    if batch_dir is not None:
        from co_reviewer.batch_store import write_json_new
        write_json_new(output_plan_path, plan)
        write_json_new(report_path, report)
    else:
        write_json_atomic(plan_path, plan)
        write_json_atomic(report_path, report)
    if revision_ledger is not None:
        write_json_atomic(ledger_path, revision_ledger.to_dict())
    print(f"[PREFLIGHT] {report['targets_preflighted']}/{report['targets_total']} target(s) are safe to apply.")
    print(f"[SOURCE] SHA-256 fingerprints recorded for {len(report['source_files'])} source DOCX file(s).")
    if report["approval_scope_changed_items"]:
        print("[REAPPROVAL] Source versions or preflighted target scopes changed for already-approved item(s): " + ", ".join(report["approval_scope_changed_items"]))
    print(f"[OK] report: {report_path}")
    if batch_dir is not None:
        print(f"[NEXT PLAN] {output_plan_path}")
    print("[NOTE] Preflight does not edit DOCX files or grant approval.")
    return 0


def cmd_approve_plan(args):
    plan_path = Path(args.plan).expanduser().resolve()
    batch_dir = plan_path.parent if (plan_path.parent / "batch_intent.json").is_file() else None
    if batch_dir is not None and not getattr(args, "revision_ledger", None):
        raise ValueError("Approval for an immutable batch requires --revision-ledger so approval lineage remains synchronized.")
    output_plan_path = _batch_plan_snapshot(batch_dir, "approved") if batch_dir is not None else plan_path
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    revision_ledger = None
    ledger_path = None
    if getattr(args, "revision_ledger", None):
        ledger_path = Path(args.revision_ledger).expanduser().resolve()
        if ledger_path in {plan_path, output_plan_path}:
            raise ValueError("RevisionLedger path must be distinct from both the input and approved-plan snapshot.")
        revision_ledger = _load_revision_ledger(ledger_path)
        revision_ledger.assert_legacy_plan_matches(plan)
    errors = validate_plan(plan, require_approval=False, check_approval_integrity=False)
    if errors:
        raise ValueError("Cannot approve an invalid revision plan:\n- " + "\n- ".join(errors))

    requested = list(dict.fromkeys(args.ids))
    if not requested:
        raise ValueError("Provide at least one revision item ID with --ids.")
    by_id = {item.get("id"): item for item in plan["revision_items"]}
    unknown = [item_id for item_id in requested if item_id not in by_id]
    if unknown:
        raise ValueError("Unknown revision item ID(s): " + ", ".join(unknown))

    for item_id in requested:
        item = by_id[item_id]
        expected_preflight = item.get("preflight_fingerprint")
        if (
            not isinstance(expected_preflight, str)
            or expected_preflight != revision_targets_fingerprint(item)
        ):
            raise ValueError(
                f"{item_id} has not passed source DOCX preflight or its target scope has changed since preflight. "
                "Run preflight-plan again and review its report before requesting approval."
            )
        for target in item["targets"]:
            expected = target.get("source_sha256")
            if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
                raise ValueError(
                    f"{item_id} has not passed source DOCX preflight. "
                    "Run preflight-plan and review its report before requesting approval."
                )
            actual = sha256_file(target["input_file"])
            if actual != expected:
                raise ValueError(
                    f"Source DOCX changed after preflight for {item_id}: {target['input_file']}. "
                    "Run preflight-plan again and review the refreshed source version before approval."
                )

    selected = set(requested)
    for item in plan["revision_items"]:
        state = item.get("status")
        if state in {"applied", "verified"} or (state == "approved" and item.get("id") not in selected):
            stored = item.get("approval_fingerprint")
            expected = revision_item_fingerprint(item)
            if not isinstance(stored, str) or stored != expected:
                raise ValueError(
                    f"{item.get('id')} has a stale approval/history fingerprint. "
                    "Do not rewrite a completed item; restore its recorded scope or resolve the plan history first."
                )

    for item_id in requested:
        state = by_id[item_id].get("status")
        if state not in {"planned", "approved"}:
            raise ValueError(
                f"{item_id} is {state}; only planned or previously approved items can be approved. "
                "Resolve/reopen the item explicitly before requesting approval again."
            )

    for item_id in requested:
        item = by_id[item_id]
        # The approve-plan command is the human authorization event. For legacy
        # operations without a separately authored intent, seal only the exact
        # text replacement; immutable scientific tokens remain unauthorized.
        for target in item.get("targets", []):
            if target.get("authorized_change") is None:
                target["authorized_change"] = {
                    "intent": "Human approved this exact text replacement through approve-plan",
                    "explicitly_authorized_tokens": [],
                    "explicitly_authorized_spans": [],
                }
        item["status"] = "approved"
        item["approval_fingerprint"] = revision_item_fingerprint(item)
    plan["approved"] = True
    ledger_payload = None
    if revision_ledger is not None:
        revision_ledger.record_legacy_approval(
            plan, requested, actor=getattr(args, "actor", "Human Author")
        )
        ledger_payload = revision_ledger.to_dict()
    if batch_dir is not None:
        from co_reviewer.batch_store import write_json_new
        write_json_new(output_plan_path, plan)
    else:
        write_json_atomic(plan_path, plan)
    if ledger_payload is not None:
        write_json_atomic(ledger_path, ledger_payload)
    print(f"[APPROVED] {len(requested)} revision item(s) sealed: {', '.join(requested)}")
    if batch_dir is not None:
        print(f"[NEXT PLAN] {output_plan_path}")
    print("[GUARD] Any later change to comments, proposed responses, document targets or source DOCX versions invalidates that item's approval.")
    return 0


def _canonical_artifact_path(value: str | Path) -> str:
    return os.path.normcase(str(Path(value).expanduser().resolve()))


def validate_apply_artifact_paths(
    plan: dict[str, Any], plan_path: str | Path, report_path: str | Path
) -> None:
    """Prevent JSON workflow artifacts from colliding with any referenced DOCX."""
    plan_key = _canonical_artifact_path(plan_path)
    report_key = _canonical_artifact_path(report_path)
    if plan_key == report_key:
        raise ValueError("The revision plan and apply report must use different file paths.")
    if not isinstance(plan, dict) or not isinstance(plan.get("revision_items"), list):
        return  # Let the normal revision-plan validator produce the schema error.

    for item in plan["revision_items"]:
        if not isinstance(item, dict):
            continue
        for target in item.get("targets", []):
            if not isinstance(target, dict):
                continue
            for field, label in (("input_file", "DOCX input"), ("output_file", "DOCX output")):
                value = target.get(field)
                if not isinstance(value, str) or not value.strip():
                    continue
                key = _canonical_artifact_path(value)
                if key == plan_key:
                    raise ValueError(
                        f"The revision plan path conflicts with a referenced {label}: {value}"
                    )
                if key == report_key:
                    raise ValueError(
                        f"The apply report path conflicts with a referenced {label}: {value}"
                    )




def _load_revision_ledger(path: str | Path) -> RevisionLedger:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return revision_ledger_from_mapping(payload)


def cmd_authorize_workflow_tasks(args):
    from co_reviewer.workflow_engine import (
        select_ready_tasks,
        transition_task,
        validate_workflow_ledger,
        workflow_scope_fingerprint,
    )

    if getattr(args, "plan_approved", False) is not True:
        raise ValueError(
            "Explicit --plan-approved confirmation is required before tasks can enter ready."
        )
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise ValueError(
            "Cannot authorize an invalid Workflow Ledger:\n- " + "\n- ".join(errors)
        )
    requested = list(dict.fromkeys(args.ids))
    if not requested:
        raise ValueError("At least one task ID must be explicitly approved.")
    by_id = {
        task["task_id"]: task
        for task in workflow.get("tasks", [])
        if isinstance(task, dict) and isinstance(task.get("task_id"), str)
    }
    unknown = [task_id for task_id in requested if task_id not in by_id]
    if unknown:
        raise ValueError("Unknown Workflow task ID(s): " + ", ".join(unknown))

    now = datetime.now(timezone.utc).isoformat()
    for task_id in requested:
        task = by_id[task_id]
        if task.get("status") not in {"planned", "blocked", "recheck_required"}:
            raise ValueError(
                f"Task {task_id} cannot be authorized from state {task.get('status')}."
            )
        if task.get("readiness") != "ready_for_author_approval":
            raise ValueError(
                f"Task {task_id} is not ready for author approval "
                f"(readiness={task.get('readiness')})."
            )
        if task.get("unresolved_questions"):
            raise ValueError(
                f"Task {task_id} still has unresolved questions; resolve them before approval."
            )
        if task.get("requires_user_approval") is not True:
            raise ValueError(
                f"Task {task_id} is missing the explicit user-approval requirement."
            )
        pending_dependencies = [
            dependency for dependency in task.get("dependencies", [])
            if by_id[dependency].get("status") not in {"verified", "completed"}
        ]
        if pending_dependencies:
            raise ValueError(
                f"Task {task_id} is blocked by unverified dependencies: "
                + ", ".join(pending_dependencies)
            )
        previous_status = task["status"]
        updated = transition_task(task, "ready")
        task.clear()
        task.update(updated)
        workflow.setdefault("events", []).append({
            "event_id": str(uuid.uuid4()),
            "task_id": task_id,
            "timestamp_utc": now,
            "event_type": "status_transition",
            "from_status": previous_status,
            "to_status": "ready",
            "reason": "Author explicitly approved this revision task plan.",
            "actor": args.actor,
            "fingerprint": workflow_scope_fingerprint(task),
        })

    errors = validate_workflow_ledger(workflow)
    if errors:
        raise ValueError(
            "Approved Workflow Ledger failed validation:\n- " + "\n- ".join(errors)
        )
    ready_batch = select_ready_tasks(workflow["tasks"])
    write_json_atomic(workflow_path, workflow)
    print(f"[APPROVED] {len(requested)} task(s): {', '.join(requested)}")
    print(
        "[READY BATCH] "
        + (", ".join(ready_batch) if ready_batch else "No conflict-free task currently executable.")
    )
    print(f"[OK] Workflow Ledger: {workflow_path}")
    return 0


def _workflow_sync_pending_path(workflow_path: Path) -> Path:
    return workflow_path.with_name("." + workflow_path.name + ".sync-pending.json")


def _atomic_copy_file(source: Path, target: Path) -> None:
    """Atomically replace target with the exact bytes of a staged transaction file."""
    import shutil

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".sync-tmp", dir=str(target.parent)
        )
        temporary_path = Path(raw_path)
        with os.fdopen(fd, "wb") as stream, source.open("rb") as staged:
            shutil.copyfileobj(staged, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _resume_workflow_sync_transaction(
    workflow_path: Path,
    revision_path: Path,
) -> dict[str, Any] | None:
    """Finish a staged two-ledger Workflow sync after any process interruption."""
    workflow_source_path = Path(workflow_path).expanduser()
    revision_source_path = Path(revision_path).expanduser()
    if workflow_source_path.is_symlink() or revision_source_path.is_symlink():
        raise ValueError("Workflow Ledger and RevisionLedger paths must not be symbolic links.")
    workflow_path = workflow_source_path.resolve()
    revision_path = revision_source_path.resolve()
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    pending_path = _workflow_sync_pending_path(workflow_path)
    if pending_path.is_symlink():
        raise ValueError(f"Pending Workflow sync marker must not be a symlink: {pending_path}")
    if pending_path.exists() and not pending_path.is_file():
        raise ValueError(f"Pending Workflow sync marker is not a regular file: {pending_path}")
    if not pending_path.is_file():
        # Crash window: the two after-images and immutable intent may have been
        # written, but the pending marker may not yet have been linked into place.
        # Such a transaction has not started replacing either Ledger. Promote it
        # only when exactly one matching intent is complete and both current
        # ledgers still equal its before-hashes.
        stage_root = workflow_path.parent / ".workflow_sync_transactions"
        if stage_root.is_symlink():
            raise ValueError(f"Workflow sync transaction root must not be a symbolic link: {stage_root}")
        matching: list[tuple[Path, dict[str, Any]]] = []
        if stage_root.is_dir():
            for stage_dir in sorted(path for path in stage_root.iterdir() if path.is_dir()):
                if stage_dir.is_symlink():
                    raise ValueError(f"Workflow sync transaction directory must not be a symbolic link: {stage_dir}")
                intent_path = stage_dir / "workflow_sync_intent.json"
                if not intent_path.exists():
                    continue
                if intent_path.is_symlink() or not intent_path.is_file():
                    raise ValueError(f"Workflow sync intent is not a regular file: {intent_path}")
                try:
                    candidate = json.loads(intent_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Cannot read orphaned Workflow sync intent: {exc}") from exc
                if (
                    candidate.get("workflow_ledger_file") != str(workflow_path)
                    or candidate.get("revision_ledger_file") != str(revision_path)
                ):
                    continue
                if (
                    candidate.get("schema_version") != "1.0"
                    or candidate.get("sync_id") != stage_dir.name
                    or intent_path.name != "workflow_sync_intent.json"
                ):
                    raise ValueError(f"Orphaned Workflow sync intent has invalid identity: {intent_path}")
                if (stage_dir / "workflow_sync_receipt.json").exists():
                    # A receipt means this transaction completed; the reconciler
                    # validates it separately. It is not eligible for promotion.
                    continue
                workflow_after = Path(str(candidate.get("workflow_after_file", ""))).expanduser()
                revision_after = Path(str(candidate.get("revision_after_file", ""))).expanduser()
                if (
                    workflow_after.is_symlink() or revision_after.is_symlink()
                    or workflow_after.resolve().parent != stage_dir.resolve()
                    or revision_after.resolve().parent != stage_dir.resolve()
                    or not workflow_after.is_file() or not revision_after.is_file()
                    or sha256_file(workflow_after) != candidate.get("workflow_after_sha256")
                    or sha256_file(revision_after) != candidate.get("revision_after_sha256")
                ):
                    raise ValueError(f"Orphaned Workflow sync after-images are missing or hash-invalid: {stage_dir}")
                if (
                    sha256_file(workflow_path) != candidate.get("workflow_before_sha256")
                    or sha256_file(revision_path) != candidate.get("revision_before_sha256")
                ):
                    raise ValueError(
                        "Orphaned Workflow sync intent has no pending marker and the current ledgers do not both "
                        "match its before-hashes. Refusing automatic promotion."
                    )
                matching.append((intent_path, candidate))
        if len(matching) > 1:
            raise ValueError(
                "More than one orphaned Workflow sync intent matches the current before-hashes; "
                "manual selection is required."
            )
        if not matching:
            return None
        orphan_intent_path, orphan_intent = matching[0]
        pending = {
            "schema_version": "1.0",
            "sync_id": orphan_intent["sync_id"],
            "workflow_ledger_file": str(workflow_path),
            "revision_ledger_file": str(revision_path),
            "intent_file": str(orphan_intent_path.resolve()),
            "intent_sha256": sha256_file(orphan_intent_path),
        }
        from co_reviewer.batch_store import write_json_new
        write_json_new(pending_path, pending)
        print(f"[WORKFLOW SYNC PROMOTED] Recovered pre-marker intent {orphan_intent['sync_id']}")
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read pending Workflow sync transaction: {exc}") from exc
    if not isinstance(pending, dict):
        raise ValueError("Pending Workflow sync transaction must be a JSON object.")
    if pending.get("schema_version") != "1.0":
        raise ValueError("Pending Workflow sync marker has an unsupported schema version.")
    if (
        Path(str(pending.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path
        or Path(str(pending.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path
    ):
        raise ValueError("Pending Workflow sync marker is bound to different ledger paths.")
    intent_candidate = Path(str(pending.get("intent_file", ""))).expanduser()
    if intent_candidate.is_symlink():
        raise ValueError(f"Workflow sync intent must not be a symlink: {intent_candidate}")
    intent_path = intent_candidate.resolve()
    if (
        Path(str(pending.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path
        or Path(str(pending.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path
        or not intent_path.is_file()
        or sha256_file(intent_path) != pending.get("intent_sha256")
    ):
        raise ValueError(
            "Pending Workflow sync transaction does not match the requested ledger paths or immutable intent."
        )
    try:
        intent = json.loads(intent_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read immutable Workflow sync intent: {exc}") from exc
    sync_id = intent.get("sync_id")
    if (
        intent.get("schema_version") != "1.0"
        or intent.get("sync_id") != pending.get("sync_id")
        or intent.get("workflow_ledger_file") != str(workflow_path)
        or intent.get("revision_ledger_file") != str(revision_path)
    ):
        raise ValueError("Pending Workflow sync intent has invalid identity or source paths.")
    stage_dir = intent_path.parent
    expected_stage_root = workflow_path.parent / ".workflow_sync_transactions"
    if expected_stage_root.is_symlink() or stage_dir.is_symlink():
        raise ValueError("Workflow sync transaction directories must not be symbolic links.")
    if (
        stage_dir.parent != expected_stage_root.resolve()
        or stage_dir.name != sync_id
        or intent_path.name != "workflow_sync_intent.json"
    ):
        raise ValueError("Workflow sync transaction escaped its expected round-local transaction directory.")
    if Path(str(intent.get("workflow_after_file", ""))).expanduser().resolve().parent != stage_dir:
        raise ValueError("Workflow sync staged Workflow Ledger escaped its transaction directory.")
    if Path(str(intent.get("revision_after_file", ""))).expanduser().resolve().parent != stage_dir:
        raise ValueError("Workflow sync staged RevisionLedger escaped its transaction directory.")
    staged_workflow = Path(intent["workflow_after_file"]).expanduser().resolve()
    staged_revision = Path(intent["revision_after_file"]).expanduser().resolve()
    expected_hashes = (
        (workflow_path, staged_workflow, intent.get("workflow_before_sha256"), intent.get("workflow_after_sha256"), "Workflow Ledger"),
        (revision_path, staged_revision, intent.get("revision_before_sha256"), intent.get("revision_after_sha256"), "RevisionLedger"),
    )
    for target, staged, before_sha, after_sha, label in expected_hashes:
        if (
            not isinstance(before_sha, str) or re.fullmatch(r"[a-f0-9]{64}", before_sha) is None
            or not isinstance(after_sha, str) or re.fullmatch(r"[a-f0-9]{64}", after_sha) is None
            or not staged.is_file() or sha256_file(staged) != after_sha
        ):
            raise ValueError(f"Workflow sync transaction has a missing or hash-invalid staged {label}.")
        if not target.is_file():
            raise ValueError(f"Workflow sync target is missing; refusing recovery: {target}")
        current_sha = sha256_file(target)
        if current_sha not in {before_sha, after_sha}:
            raise ValueError(
                f"{label} changed outside the pending sync transaction; refusing to overwrite it. "
                f"Current={current_sha}; before={before_sha}; staged-after={after_sha}."
            )
    # Forward-only recovery: finish either replacement that was not committed.
    for target, staged, before_sha, after_sha, label in expected_hashes:
        if sha256_file(target) == before_sha:
            _atomic_copy_file(staged, target)
        if sha256_file(target) != after_sha:
            raise ValueError(f"{label} did not reach the staged after-image during recovery.")
    receipt_path = stage_dir / "workflow_sync_receipt.json"
    if receipt_path.is_symlink():
        raise ValueError(f"Workflow sync receipt must not be a symbolic link: {receipt_path}")
    receipt = {
        "schema_version": "1.0",
        "status": "committed",
        "sync_id": sync_id,
        "committed_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_ledger_file": str(workflow_path),
        "workflow_ledger_sha256": intent["workflow_after_sha256"],
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256": intent["revision_after_sha256"],
        "intent_file": str(intent_path),
        "intent_sha256": sha256_file(intent_path),
    }
    if receipt_path.exists():
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        for key in (
            "schema_version", "status", "sync_id", "workflow_ledger_file",
            "workflow_ledger_sha256", "revision_ledger_file",
            "revision_ledger_sha256", "intent_file", "intent_sha256",
        ):
            if existing.get(key) != receipt.get(key):
                raise ValueError("Existing Workflow sync receipt conflicts with the pending transaction.")
    else:
        from co_reviewer.batch_store import write_json_new
        write_json_new(receipt_path, receipt)
    pending_path.unlink()
    return receipt


def _commit_workflow_sync_transaction(
    workflow_path: Path,
    revision_path: Path,
    workflow_after: dict[str, Any],
    revision_after: dict[str, Any],
    *,
    expected_workflow_before_sha256: str,
    expected_revision_before_sha256: str,
) -> dict[str, Any]:
    """Stage both ledger after-images, then commit them with crash-recoverable intent."""
    from co_reviewer.batch_store import write_json_new

    pending_path = _workflow_sync_pending_path(workflow_path)
    if pending_path.exists():
        raise ValueError(
            "A Workflow sync transaction is already pending. Run recover-workflow-sync before starting another sync."
        )
    workflow_before = sha256_file(workflow_path)
    revision_before = sha256_file(revision_path)
    if workflow_before != expected_workflow_before_sha256:
        raise ValueError("Workflow Ledger changed after it was loaded for synchronization; refusing a stale after-image.")
    if revision_before != expected_revision_before_sha256:
        raise ValueError("RevisionLedger changed after it was loaded for synchronization; refusing a stale after-image.")
    sync_id = uuid.uuid4().hex
    stage_dir = workflow_path.parent / ".workflow_sync_transactions" / sync_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    workflow_stage = stage_dir / "workflow_ledger.after.json"
    revision_stage = stage_dir / "revision_ledger.after.json"
    write_json_new(workflow_stage, workflow_after)
    write_json_new(revision_stage, revision_after)
    if sha256_file(workflow_path) != workflow_before or sha256_file(revision_path) != revision_before:
        raise ValueError("A source ledger changed while Workflow sync after-images were being staged.")
    intent_path = stage_dir / "workflow_sync_intent.json"
    intent = {
        "schema_version": "1.0",
        "sync_id": sync_id,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_ledger_file": str(workflow_path),
        "workflow_before_sha256": workflow_before,
        "workflow_after_file": str(workflow_stage),
        "workflow_after_sha256": sha256_file(workflow_stage),
        "revision_ledger_file": str(revision_path),
        "revision_before_sha256": revision_before,
        "revision_after_file": str(revision_stage),
        "revision_after_sha256": sha256_file(revision_stage),
    }
    write_json_new(intent_path, intent)
    pending = {
        "schema_version": "1.0",
        "sync_id": sync_id,
        "workflow_ledger_file": str(workflow_path),
        "revision_ledger_file": str(revision_path),
        "intent_file": str(intent_path),
        "intent_sha256": sha256_file(intent_path),
    }
    write_json_new(pending_path, pending)
    receipt = _resume_workflow_sync_transaction(workflow_path, revision_path)
    if receipt is None:
        raise ValueError("Workflow sync pending marker disappeared before commit recovery.")
    return receipt


def cmd_recover_workflow_sync(args):
    workflow_source_path = Path(args.workflow_ledger).expanduser()
    revision_source_path = Path(args.revision_ledger).expanduser()
    if workflow_source_path.is_symlink() or revision_source_path.is_symlink():
        raise ValueError("Workflow Ledger and RevisionLedger paths must not be symbolic links.")
    workflow_path = workflow_source_path.resolve()
    revision_path = revision_source_path.resolve()
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    receipt = _resume_workflow_sync_transaction(workflow_path, revision_path)
    if receipt is None:
        print("[NO PENDING WORKFLOW SYNC] Both ledgers are outside an interrupted sync transaction.")
        return 0
    print(f"[WORKFLOW SYNC RECOVERED] {receipt['sync_id']}")
    print(f"[WORKFLOW LEDGER] {workflow_path} | SHA-256 {receipt['workflow_ledger_sha256']}")
    print(f"[REVISION LEDGER] {revision_path} | SHA-256 {receipt['revision_ledger_sha256']}")
    print(f"[RECEIPT] {Path(receipt['intent_file']).with_name('workflow_sync_receipt.json')}")
    return 0

def cmd_sync_workflow_ledger(args):
    from co_reviewer.batch_store import _json_payload_sha256
    from co_reviewer.workflow_engine import (
        invalidate_affected_tasks,
        transition_task,
        validate_workflow_ledger,
        workflow_scope_fingerprint,
    )

    workflow_source_path = Path(args.workflow_ledger).expanduser()
    revision_source_path = Path(args.revision_ledger).expanduser()
    if workflow_source_path.is_symlink() or revision_source_path.is_symlink():
        raise ValueError("Workflow Ledger and RevisionLedger paths must not be symbolic links.")
    workflow_path = workflow_source_path.resolve()
    revision_path = revision_source_path.resolve()
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    pending_receipt = _resume_workflow_sync_transaction(workflow_path, revision_path)
    if pending_receipt is not None:
        print(f"[WORKFLOW SYNC RECOVERED BEFORE NEW SYNC] {pending_receipt['sync_id']}")
        print("[CONTINUE] The recovered after-images are now authoritative; reconcile the current Patch and ResponseUnit events against them.")
    workflow_sha_at_load = sha256_file(workflow_path)
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if sha256_file(workflow_path) != workflow_sha_at_load:
        raise ValueError("Workflow Ledger changed while it was being loaded for synchronization.")
    revision_sha_at_load = sha256_file(revision_path)
    revision_ledger = _load_revision_ledger(revision_path)
    if sha256_file(revision_path) != revision_sha_at_load:
        raise ValueError("RevisionLedger changed while it was being loaded for synchronization.")
    workflow_payload_at_load = _json_payload_sha256(workflow)
    revision_payload_at_load = _json_payload_sha256(revision_ledger.to_dict())
    errors = validate_workflow_ledger(workflow)
    if errors:
        raise ValueError(
            "Cannot synchronize an invalid Workflow Ledger:\n- " + "\n- ".join(errors)
        )
    expected_fingerprint = revision_ledger.workflow_ledger_fingerprint
    actual_fingerprint = canonical_fingerprint(workflow)
    if not expected_fingerprint or expected_fingerprint != actual_fingerprint:
        raise ValueError(
            "Workflow Ledger changed since this RevisionLedger batch was built. "
            "Refusing to apply stale Patch results to a different task graph."
        )

    tasks = workflow.get("tasks", [])
    task_by_id = {
        task.get("task_id"): task for task in tasks if isinstance(task, dict)
    }
    now = datetime.now(timezone.utc).isoformat()
    synchronized: list[str] = []
    response_completed_tasks: list[str] = []

    def latest_patch_event(patch_id: str, event_type: str) -> Any:
        matches = [
            event for event in revision_ledger.events
            if event.subject_type == "patch"
            and event.subject_id == patch_id
            and event.event_type == event_type
        ]
        return matches[-1] if matches else None

    for task_id, task in task_by_id.items():
        if task.get("status") in {"verified", "completed"}:
            continue
        active_patches = [
            patch for patch in revision_ledger.patches
            if patch.task_id == task_id
            and patch.status not in {"superseded", "blocked"}
        ]
        if not active_patches:
            continue
        if len(active_patches) != 1:
            raise ValueError(
                f"Task {task_id} has multiple active Patches; resolve the Patch lineage before synchronization."
            )
        patch = active_patches[0]
        if patch.status != "verified":
            continue
        if task.get("status") != "ready":
            raise ValueError(
                f"Verified Patch {patch.patch_id} is linked to Workflow task {task_id} "
                f"in unexpected state {task.get('status')}."
            )

        preflight_event = latest_patch_event(patch.patch_id, "preflight")
        approval_event = latest_patch_event(patch.patch_id, "approval")
        apply_event = latest_patch_event(patch.patch_id, "patch_applied")
        verification_event = latest_patch_event(patch.patch_id, "verification")
        if not all((preflight_event, approval_event, apply_event, verification_event)):
            raise ValueError(
                f"Patch {patch.patch_id} is marked verified but its complete preflight/approval/apply/verification event chain is missing."
            )
        if not patch.preflight_report_id or not patch.apply_report_ids or not patch.verification_report_id:
            raise ValueError(f"Patch {patch.patch_id} is missing lifecycle report references.")
        if any(not operation.source_sha256 for operation in patch.operations):
            raise ValueError(f"Patch {patch.patch_id} has an operation without a preflight source hash.")

        input_fingerprint = canonical_fingerprint(sorted(
            (
                operation.document,
                operation.input_file,
                operation.source_sha256,
            )
            for operation in patch.operations
        ))
        output_fingerprint = canonical_fingerprint(patch.verified_output_hashes)
        transitions = [
            (
                "preflighted",
                {
                    "input_fingerprint": input_fingerprint,
                    "preflight_report_id": patch.preflight_report_id,
                    "preflighted_at_utc": preflight_event.timestamp_utc,
                },
                preflight_event,
                "Synchronized from the RevisionLedger preflight event.",
            ),
            (
                "approved",
                {
                    "human_approval_confirmed": True,
                    "approved_by": approval_event.actor,
                    "approved_at_utc": approval_event.timestamp_utc,
                },
                approval_event,
                "Synchronized from the explicit Patch approval event.",
            ),
            (
                "applied",
                {
                    "output_fingerprint": output_fingerprint,
                    "execution_report_id": patch.apply_report_ids[-1],
                    "applied_at_utc": apply_event.timestamp_utc,
                },
                apply_event,
                "Synchronized from the DOCX apply report.",
            ),
            (
                "verified",
                {
                    "semantic_review_confirmed": True,
                    "verified_by": verification_event.actor,
                    "verified_at_utc": verification_event.timestamp_utc,
                    "verification_report_id": patch.verification_report_id,
                },
                verification_event,
                "Synchronized from mechanical verification and explicit semantic review.",
            ),
        ]
        for new_status, evidence, source_event, reason in transitions:
            previous_status = task["status"]
            updated = transition_task(
                task,
                new_status,
                evidence=evidence,
            )
            task.clear()
            task.update(updated)
            workflow.setdefault("events", []).append({
                "event_id": str(uuid.uuid4()),
                "task_id": task_id,
                "timestamp_utc": source_event.timestamp_utc or now,
                "event_type": "status_transition",
                "from_status": previous_status,
                "to_status": new_status,
                "reason": reason,
                "actor": source_event.actor,
                "fingerprint": workflow_scope_fingerprint(task),
            })
        synchronized.append(task_id)

    invalidated: list[str] = []
    reopened_units: list[str] = []
    if synchronized:
        reason = (
            "A verified Patch changed a target-conflict component or upstream dependency. "
            "Rebuild exact operations against the latest document output and obtain fresh approval."
        )
        previous_tasks = {
            task.get("task_id"): task
            for task in workflow.get("tasks", [])
            if isinstance(task, dict)
        }
        # A later immutable batch may deliberately continue from the exact output
        # of an already verified Patch. Such a predecessor remains a verified
        # historical step; do not create a same-target recheck loop solely because
        # the later Patch shares its paragraph/object target. If its ResponseUnit
        # already has final location evidence, keep the conservative invalidation
        # path so stale final locations cannot survive a new document revision.
        preserve_verified_predecessors: set[str] = set()
        for synchronized_task_id in synchronized:
            synchronized_patches = [
                patch for patch in revision_ledger.patches
                if patch.task_id == synchronized_task_id and patch.status == "verified"
            ]
            synchronized_task = previous_tasks.get(synchronized_task_id)
            if not synchronized_task:
                continue
            synchronized_groups = set(synchronized_task.get("target_groups", []))
            for current_patch in synchronized_patches:
                current_inputs_by_role: dict[str, set[Path]] = defaultdict(set)
                for operation in current_patch.operations:
                    current_inputs_by_role[operation.document].add(
                        Path(operation.input_file).expanduser().resolve()
                    )
                for predecessor in revision_ledger.patches:
                    if (
                        predecessor.task_id == synchronized_task_id
                        or predecessor.status != "verified"
                    ):
                        continue
                    predecessor_task = previous_tasks.get(predecessor.task_id)
                    if (
                        not predecessor_task
                        or predecessor_task.get("status") not in {"verified", "completed"}
                    ):
                        continue
                    shared_groups = synchronized_groups.intersection(
                        set(predecessor_task.get("target_groups", []))
                    )
                    if not shared_groups:
                        continue
                    follows_predecessor_output = any(
                        Path(operation.output_file).expanduser().resolve()
                        in current_inputs_by_role.get(operation.document, set())
                        for operation in predecessor.operations
                    )
                    if not follows_predecessor_output:
                        continue
                    linked_units = [
                        unit for unit in revision_ledger.response_units
                        if predecessor.task_id in unit.linked_task_ids
                    ]
                    has_final_locations = any(
                        unit.location_requirement != "not_required"
                        and (
                            unit.status in {"location_mapped", "completed"}
                            or bool(unit.location_map_fingerprint)
                            or bool(unit.final_locations)
                        )
                        for unit in linked_units
                    )
                    if not has_final_locations:
                        preserve_verified_predecessors.add(predecessor.task_id)

        updated_tasks, invalidated = invalidate_affected_tasks(
            workflow.get("tasks", []),
            synchronized,
            reason,
            preserve_verified_conflict_task_ids=sorted(preserve_verified_predecessors),
        )
        workflow["tasks"] = updated_tasks
        current_tasks = {
            task.get("task_id"): task
            for task in updated_tasks if isinstance(task, dict)
        }
        for task_id in invalidated:
            current = current_tasks[task_id]
            workflow.setdefault("events", []).append({
                "event_id": str(uuid.uuid4()),
                "task_id": task_id,
                "timestamp_utc": now,
                "event_type": "invalidation",
                "from_status": previous_tasks[task_id].get("status", "identified"),
                "to_status": "recheck_required",
                "reason": current.get("invalidation_reason", reason),
                "actor": "Co_Reviewer Workflow Engine",
                "fingerprint": workflow_scope_fingerprint(current),
            })
        reopened_units = revision_ledger.invalidate_response_units_for_tasks(
            invalidated,
            actor="Co_Reviewer Workflow Engine",
            reason=reason,
        )

    task_by_id = {
        task.get("task_id"): task
        for task in workflow.get("tasks", [])
        if isinstance(task, dict)
    }

    # Response-only/no-change tasks have no executable Patch to synchronize.
    # Complete them only when every reviewer comment has one completed,
    # semantically confirmed ResponseUnit. Patch-bearing tasks keep the full
    # Preflight -> Approval -> Apply -> Verify chain above.
    units_by_task_comment: dict[tuple[str, str], list[Any]] = {}
    for unit in revision_ledger.response_units:
        for linked_task_id in unit.linked_task_ids:
            units_by_task_comment.setdefault((linked_task_id, unit.comment_id), []).append(unit)
    for task_id, task in task_by_id.items():
        current_status = task.get("status")
        if current_status not in {"ready", "verified"}:
            continue
        comment_ids = task.get("source_comment_ids", [])
        if not isinstance(comment_ids, list) or not comment_ids:
            continue
        task_units = []
        for raw_comment_id in comment_ids:
            candidates = units_by_task_comment.get((task_id, str(raw_comment_id)), [])
            if (
                len(candidates) != 1
                or candidates[0].status != "completed"
                or not candidates[0].semantic_review_confirmed
                or candidates[0].disposition == "clarification_needed"
            ):
                task_units = []
                break
            task_units.append(candidates[0])
        if not task_units:
            continue
        task_patches = [
            patch for patch in revision_ledger.patches
            if patch.task_id == task_id and patch.status != "superseded"
        ]
        if current_status == "ready" and task_patches:
            continue
        if any(patch.status != "verified" for patch in task_patches):
            continue
        completion_payload = {
            "task_id": task_id,
            "response_unit_ids": sorted(unit.response_unit_id for unit in task_units),
            "response_fingerprints": sorted(
                canonical_fingerprint({
                    "response_unit_id": unit.response_unit_id,
                    "response_text": unit.response_text,
                    "disposition": unit.disposition,
                    "final_location_fingerprint": unit.final_location_fingerprint,
                })
                for unit in task_units
            ),
        }
        completion_fingerprint = canonical_fingerprint(completion_payload)
        previous_status = current_status
        completed_task = transition_task(
            task,
            "completed",
            evidence={
                "completion_record_id": "response-units:" + completion_fingerprint[:20],
                "completion_record_fingerprint": completion_fingerprint,
                "completed_at_utc": now,
            },
        )
        task.clear()
        task.update(completed_task)
        workflow.setdefault("events", []).append({
            "event_id": str(uuid.uuid4()),
            "task_id": task_id,
            "timestamp_utc": now,
            "event_type": "status_transition",
            "from_status": previous_status,
            "to_status": "completed",
            "reason": "Every reviewer comment has exactly one completed, semantically confirmed ResponseUnit.",
            "actor": "Co_Reviewer ResponseUnit Synchronizer",
            "fingerprint": workflow_scope_fingerprint(task),
        })
        response_completed_tasks.append(task_id)

    errors = validate_workflow_ledger(workflow)
    if errors:
        raise ValueError(
            "Synchronized Workflow Ledger failed validation:\n- " + "\n- ".join(errors)
        )
    revision_ledger.workflow_ledger_fingerprint = canonical_fingerprint(workflow)
    revision_ledger.validate_or_raise()

    if (
        _json_payload_sha256(workflow) == workflow_payload_at_load
        and _json_payload_sha256(revision_ledger.to_dict()) == revision_payload_at_load
    ):
        if sha256_file(workflow_path) != workflow_sha_at_load or sha256_file(revision_path) != revision_sha_at_load:
            raise ValueError("A Ledger changed during a no-op Workflow sync check; reload both files.")
        print("[WORKFLOW ALREADY SYNCHRONIZED] No Ledger state transition was needed; no duplicate sync transaction was created.")
        return 0

    sync_receipt = _commit_workflow_sync_transaction(
        workflow_path,
        revision_path,
        workflow,
        revision_ledger.to_dict(),
        expected_workflow_before_sha256=workflow_sha_at_load,
        expected_revision_before_sha256=revision_sha_at_load,
    )

    print(
        "[WORKFLOW VERIFIED] "
        + (", ".join(synchronized) if synchronized else "No newly verified Patch tasks to synchronize.")
    )
    if response_completed_tasks:
        print("[RESPONSE-ONLY COMPLETED] " + ", ".join(response_completed_tasks))
    if invalidated:
        print("[RECHECK REQUIRED] " + ", ".join(invalidated))
    if reopened_units:
        print("[RESPONSE REOPENED] " + ", ".join(reopened_units))
    print(f"[OK] Workflow Ledger: {workflow_path}")
    print(f"[OK] RevisionLedger: {revision_path}")
    print(f"[SYNC TRANSACTION] {sync_receipt['sync_id']} | committed | receipt: {Path(sync_receipt['intent_file']).with_name('workflow_sync_receipt.json')}")
    return 0


def _verify_workflow_preparation_manifest(
    manifest_path: str | Path,
    workflow_path: str | Path,
    inventory_path: str | Path,
    workflow: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind preparation to the exact current Workflow and audited inventory bytes."""
    manifest_file = Path(manifest_path).expanduser().resolve()
    workflow_file = Path(workflow_path).expanduser().resolve()
    inventory_file = Path(inventory_path).expanduser().resolve()
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Round/refresh manifest does not exist: {manifest_file}")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read round/refresh manifest: {exc}") from exc
    if not isinstance(manifest, Mapping):
        raise ValueError("Round/refresh manifest must be a JSON object.")
    manifest_round_id = manifest.get("review_round_id")
    if (
        not isinstance(manifest_round_id, str)
        or not manifest_round_id.strip()
        or manifest_round_id != workflow.get("revision_round_id")
    ):
        raise ValueError("Round/refresh manifest review_round_id does not match the supplied Workflow Ledger.")

    def resolve_manifest_path(value: Any, label: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Round/refresh manifest has no valid {label} path.")
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = manifest_file.parent / candidate
        return candidate.resolve()

    expected_workflow_value = manifest.get("current_workflow_ledger") or manifest.get("workflow_ledger")
    if expected_workflow_value:
        expected_workflow = resolve_manifest_path(expected_workflow_value, "current Workflow Ledger")
        if expected_workflow != workflow_file:
            raise ValueError("Workflow preparation input is not the current Workflow Ledger recorded in the manifest. "
                             f"Expected {expected_workflow}; received {workflow_file}.")
    expected_workflow_sha = manifest.get("workflow_ledger_sha256")
    if expected_workflow_sha is None and manifest.get("prepared_workflow_ledger"):
        prepared_path = resolve_manifest_path(manifest["prepared_workflow_ledger"], "prepared Workflow Ledger")
        if prepared_path == workflow_file:
            expected_workflow_sha = manifest.get("prepared_workflow_ledger_sha256")
    if expected_workflow_sha is not None:
        if (
            not isinstance(expected_workflow_sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected_workflow_sha) is None
            or not workflow_file.is_file()
            or sha256_file(workflow_file) != expected_workflow_sha
        ):
            raise ValueError("Current Workflow Ledger bytes do not match the path/hash recorded in the round/refresh manifest.")

    candidates: list[tuple[str, Mapping[str, Any], Mapping[str, Any]]] = []
    if isinstance(manifest.get("audit_artifacts"), Mapping):
        hashes = manifest.get("artifact_sha256", {})
        candidates.append(("refresh_manifest.audit_artifacts", manifest["audit_artifacts"], hashes if isinstance(hashes, Mapping) else {}))
    latest_inventory = manifest.get("latest_refresh_inventory")
    if isinstance(latest_inventory, str) and latest_inventory.strip():
        latest_hashes = manifest.get("latest_refresh_artifact_sha256", {})
        latest_hashes = dict(latest_hashes) if isinstance(latest_hashes, Mapping) else {}
        if manifest.get("latest_refresh_inventory_sha256"):
            latest_hashes.setdefault("workspace_inventory", manifest["latest_refresh_inventory_sha256"])
        candidates.append(("round_manifest.latest_refresh_inventory", {"workspace_inventory": latest_inventory}, latest_hashes))
    if isinstance(manifest.get("artifacts"), Mapping):
        hashes = manifest.get("artifact_sha256", {})
        candidates.append(("round_manifest.artifacts", manifest["artifacts"], hashes if isinstance(hashes, Mapping) else {}))

    matched: tuple[str, Mapping[str, Any]] | None = None
    for label, artifact_paths, artifact_hashes in candidates:
        expected_inventory = artifact_paths.get("workspace_inventory")
        if (
            isinstance(expected_inventory, str)
            and expected_inventory.strip()
            and resolve_manifest_path(expected_inventory, "workspace_inventory") == inventory_file
        ):
            matched = (label, artifact_hashes)
            break
    if matched is None:
        raise ValueError("Inventory path does not match the starter inventory or exact latest-refresh inventory recorded in the supplied manifest.")
    binding_label, hashes = matched
    expected_inventory_sha = hashes.get("workspace_inventory")
    if expected_inventory_sha is not None:
        if (
            not isinstance(expected_inventory_sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected_inventory_sha) is None
            or not inventory_file.is_file()
            or sha256_file(inventory_file) != expected_inventory_sha
        ):
            raise ValueError(f"Workspace Inventory bytes do not match the SHA-256 recorded by {binding_label}: {inventory_file}")
    elif not inventory_file.is_file():
        raise FileNotFoundError(f"Workspace Inventory does not exist: {inventory_file}")
    else:
        print(f"[LEGACY MANIFEST] {binding_label} has no inventory SHA-256; exact path was checked, but its bytes cannot be proven.", file=sys.stderr)
    return dict(manifest)

def cmd_prepare_workflow_execution(args):
    """Merge a prepared, evidence-bound execution scope into a new Workflow Ledger."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    scope_path = Path(args.execution_scope).expanduser().resolve()
    inventory_path = Path(args.inventory).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    reviewer_map_path = (
        Path(args.reviewer_map_out).expanduser().resolve()
        if args.reviewer_map_out
        else output_path.with_name("reviewer_map.json")
    )
    report_path = (
        Path(args.report).expanduser().resolve()
        if args.report
        else output_path.with_name("workflow_preparation_report.json")
    )
    inputs = {workflow_path, scope_path, inventory_path}
    manifest_argument = getattr(args, "round_manifest", None)
    root_manifest_argument = getattr(args, "round_root_manifest", None)
    binding_manifest = None
    root_manifest_path = Path(root_manifest_argument).expanduser().resolve() if root_manifest_argument else None
    if manifest_argument:
        binding_manifest = _verify_workflow_preparation_manifest(
            manifest_argument,
            workflow_path,
            inventory_path,
            json.loads(workflow_path.read_text(encoding="utf-8")),
        )
        manifest_file = Path(manifest_argument).expanduser().resolve()
        inputs.add(manifest_file)
        if root_manifest_path is None:
            is_root_manifest = (
                isinstance(binding_manifest.get("directories"), Mapping)
                and isinstance(binding_manifest.get("artifacts"), Mapping)
            )
            if not is_root_manifest:
                raise ValueError(
                    "--round-root-manifest is required when --round-manifest points to a dedicated refresh_manifest.json, "
                    "so the newly prepared Workflow Ledger can be indexed by the round."
                )
            root_manifest_path = manifest_file
    if root_manifest_path is not None:
        root_manifest_path = root_manifest_path.resolve()
        inputs.add(root_manifest_path)
        if not manifest_argument or root_manifest_path != Path(manifest_argument).expanduser().resolve():
            _verify_workflow_preparation_manifest(
                root_manifest_path,
                workflow_path,
                inventory_path,
                json.loads(workflow_path.read_text(encoding="utf-8")),
            )
    root_manifest_sha_at_load = (
        sha256_file(root_manifest_path)
        if root_manifest_path is not None and root_manifest_path.is_file()
        else None
    )
    outputs = [output_path, reviewer_map_path, report_path]
    if len(set(outputs)) != len(outputs) or any(path in inputs for path in outputs):
        raise ValueError("Prepared Workflow Ledger, reviewer map and report must use distinct paths from all inputs.")
    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing workflow-preparation outputs: " + ", ".join(existing)
        )

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    if getattr(args, "round_manifest", None):
        _verify_workflow_preparation_manifest(
            args.round_manifest,
            workflow_path,
            inventory_path,
            workflow,
        )
    from co_reviewer.workflow_preparation import prepare_workflow_execution

    prepared, reviewer_map, report = prepare_workflow_execution(
        workflow,
        scope,
        inventory,
        workflow_path=workflow_path,
        execution_scope_path=scope_path,
    )
    from co_reviewer.batch_store import write_json_new

    output_sha = write_json_new(output_path, prepared)
    reviewer_sha = write_json_new(reviewer_map_path, reviewer_map)
    report.update({
        "prepared_workflow_ledger": str(output_path),
        "prepared_workflow_ledger_sha256": output_sha,
        "reviewer_map_file": str(reviewer_map_path),
        "reviewer_map_sha256": reviewer_sha,
        "preparation_report": str(report_path),
    })
    report_sha = write_json_new(report_path, report)
    if root_manifest_path is not None:
        if (
            root_manifest_sha_at_load is None
            or not root_manifest_path.is_file()
            or sha256_file(root_manifest_path) != root_manifest_sha_at_load
        ):
            raise ValueError(
                "Root round_manifest.json changed during Workflow preparation; prepared artifacts are retained, "
                "but the manifest was not overwritten. Reconcile and inspect these outputs before continuing."
            )
        round_manifest = json.loads(root_manifest_path.read_text(encoding="utf-8"))
        if round_manifest.get("review_round_id") != prepared.get("revision_round_id"):
            raise ValueError("Round manifest review_round_id changed before prepared Workflow Ledger registration.")
        round_manifest.update({
            "current_workflow_ledger": str(output_path),
            "workflow_ledger_sha256": output_sha,
            "prepared_workflow_ledger": str(output_path),
            "prepared_workflow_ledger_sha256": output_sha,
            "current_reviewer_map": str(reviewer_map_path),
            "reviewer_map_sha256": reviewer_sha,
            "workflow_preparation_report": str(report_path),
            "workflow_preparation_report_sha256": report_sha,
        })
        write_json_atomic(root_manifest_path, round_manifest)
        print(f"[ROUND MANIFEST] Current Workflow Ledger registered: {root_manifest_path}")
    print(f"[PREPARED] {len(report['prepared_task_ids'])} task(s): {', '.join(report['prepared_task_ids'])}")
    print("[APPROVAL] No task was authorized; all author-approval gates remain pending.")
    print("[DOCUMENTS] No DOCX was edited. Batch-specific output paths are assigned later by build-revision-ledger.")
    print(f"[WORKFLOW LEDGER] {output_path} | {output_sha}")
    print(f"[REVIEWER MAP] {reviewer_map_path} | {reviewer_sha}")
    print(f"[REPORT] {report_path} | {report_sha}")
    return 0


def cmd_build_revision_ledger(args):
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    if output_path == workflow_path:
        raise ValueError("RevisionLedger output must not overwrite the Workflow Ledger.")

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    from co_reviewer.workflow_engine import (
        select_ready_tasks,
        validate_workflow_ledger,
        workflow_scope_fingerprint,
    )
    workflow_errors = validate_workflow_ledger(workflow)
    if workflow_errors:
        raise ValueError(
            "Cannot build a RevisionLedger from an invalid Workflow Ledger:\n- "
            + "\n- ".join(workflow_errors)
        )
    scheduler_batch = select_ready_tasks(workflow.get("tasks", []))
    selected_task_ids = list(dict.fromkeys(getattr(args, "task_ids", None) or scheduler_batch))
    unavailable = set(selected_task_ids) - set(scheduler_batch)
    if unavailable:
        raise ValueError(
            "RevisionLedger can only be built from the current conflict-free ready batch. "
            "Unavailable task ID(s): " + ", ".join(sorted(unavailable))
        )

    selected_document_task_ids = [
        task_id for task_id in selected_task_ids
        if any(
            isinstance(operation, dict) and isinstance(operation.get("document"), str)
            for task in workflow.get("tasks", [])
            if isinstance(task, dict) and task.get("task_id") == task_id
            for operation in task.get("patch_operations", [])
        )
    ]
    selected_roles = sorted({
        operation.get("document")
        for task in workflow.get("tasks", [])
        if isinstance(task, dict) and task.get("task_id") in selected_document_task_ids
        for operation in task.get("patch_operations", [])
        if isinstance(operation, dict) and isinstance(operation.get("document"), str)
    })
    batch_intent = None
    batch_document_paths: dict[str, dict[str, str]] = {}
    batch_id_for_intent = getattr(args, "batch_id", None)
    runs_root = None
    existing_patch_statuses: dict[str, str] = {}
    existing_revision_events = None
    existing_revision_patches_by_id: dict[str, Any] = {}
    if selected_roles and output_path.exists():
        existing_before_build = _load_revision_ledger(output_path)
        existing_patch_statuses = {
            patch.patch_id: patch.status for patch in existing_before_build.patches
        }
        existing_revision_patches_by_id = {
            patch.patch_id: patch for patch in existing_before_build.patches
        }
        existing_revision_events = existing_before_build.events
    if selected_roles:
        from co_reviewer.batch_store import (
            _safe_component,
            assert_no_uncommitted_role_batches,
            load_active_document_pointers,
        )
        runs_root = (
            Path(getattr(args, "runs_dir")).expanduser()
            if getattr(args, "runs_dir", None)
            else workflow_path.parent / "runs"
        )
        if not runs_root.is_absolute():
            runs_root = workflow_path.parent / runs_root
        runs_root = runs_root.resolve()
        assert_no_uncommitted_role_batches(
            runs_root,
            selected_roles,
            selected_task_ids,
            existing_patch_statuses,
            workflow,
            revision_events=existing_revision_events,
            revision_patches_by_id=existing_revision_patches_by_id,
            revision_ledger_path=output_path,
        )
        if batch_id_for_intent is None:
            batch_id_for_intent = (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-"
                + uuid.uuid4().hex[:8]
            )
        planned_batch_dir = runs_root / f"batch-{batch_id_for_intent}"
        if planned_batch_dir.exists():
            raise FileExistsError(
                f"Batch directory already exists; refusing to reuse it: {planned_batch_dir}"
            )
        active_pointers = load_active_document_pointers(workflow)
        for role in selected_roles:
            parent = active_pointers.get(role)
            if parent is None:
                raise ValueError(f"No active document pointer exists for batch role {role}.")
            batch_document_paths[role] = {
                "input_file": parent["file"],
                "output_file": str(
                    planned_batch_dir
                    / "documents"
                    / f"{_safe_component(role)}.marked.docx"
                ),
                "source_sha256": parent["sha256"],
            }

    document_paths = (
        json.loads(Path(args.document_paths).read_text(encoding="utf-8"))
        if args.document_paths else {}
    )
    reviewer_comments = (
        json.loads(Path(args.reviewer_comments).read_text(encoding="utf-8"))
        if args.reviewer_comments else {}
    )
    reviewer_map = (
        json.loads(Path(args.reviewer_map).read_text(encoding="utf-8"))
        if args.reviewer_map else {}
    )
    if not isinstance(document_paths, dict):
        raise ValueError("--document-paths must contain a JSON object.")
    if not isinstance(reviewer_comments, dict):
        raise ValueError("--reviewer-comments must contain a JSON object.")
    if not isinstance(reviewer_map, dict):
        raise ValueError("--reviewer-map must contain a JSON object.")

    source_artifacts = {workflow_path}
    for source_arg in (
        getattr(args, "document_paths", None),
        getattr(args, "reviewer_comments", None),
        getattr(args, "reviewer_map", None),
    ):
        if source_arg:
            source_artifacts.add(Path(source_arg).expanduser().resolve())
    if output_path in source_artifacts:
        raise ValueError(
            "RevisionLedger output must not overwrite the Workflow Ledger or any auxiliary input."
        )

    effective_document_paths = dict(document_paths)
    # Explicit no-change ResponseUnits still need a hash-bound RevisionDocument
    # so their existing text can be mapped in the final Word/PDF. When a role
    # has no PatchOperation in this batch, bind its passthrough record to the
    # currently active source path. RevisionLedger validation permits this
    # unchanged-source form only for roles without PatchOperations.
    from co_reviewer.batch_store import load_active_document_pointers
    active_pointers_for_anchors = load_active_document_pointers(workflow)
    for role, pointer in active_pointers_for_anchors.items():
        effective_document_paths.setdefault(role, {
            "input_file": pointer["file"],
            "output_file": pointer["file"],
            "source_sha256": pointer["sha256"],
        })
    effective_document_paths.update(batch_document_paths)
    ledger = build_revision_ledger(
        workflow,
        document_paths=effective_document_paths,
        reviewer_comments_by_id=reviewer_comments,
        reviewer_by_task=reviewer_map,
        selected_task_ids=selected_task_ids,
        force_document_paths=bool(batch_document_paths),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        existing_ledger = _load_revision_ledger(output_path)
        previous_fingerprints = existing_ledger.workflow_task_fingerprints
        incoming_fingerprints = ledger.workflow_task_fingerprints
        removed_tasks = set(previous_fingerprints) - set(incoming_fingerprints)
        if removed_tasks:
            raise ValueError(
                "The new Workflow Ledger removed task(s) from this review round: "
                + ", ".join(sorted(removed_tasks))
            )
        changed_scope_tasks = {
            task_id for task_id, fingerprint in previous_fingerprints.items()
            if incoming_fingerprints.get(task_id) != fingerprint
        }
        unselected_scope_changes = changed_scope_tasks - set(selected_task_ids)
        if unselected_scope_changes:
            raise ValueError(
                "Task scope changed outside the current author-approved ready batch; "
                "re-authorize those tasks before refreshing this RevisionLedger: "
                + ", ".join(sorted(unselected_scope_changes))
            )

        workflow_tasks_by_id = {
            task.get("task_id"): task
            for task in workflow.get("tasks", [])
            if isinstance(task, dict)
        }
        ready_events_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in workflow.get("events", []):
            if (
                isinstance(event, dict)
                and event.get("event_type") == "status_transition"
                and event.get("to_status") == "ready"
                and isinstance(event.get("task_id"), str)
                and isinstance(event.get("actor"), str)
                and event.get("actor").strip()
            ):
                ready_events_by_task[event["task_id"]].append(event)

        original_addition_patches = list(ledger.patches)
        superseded_in_addition: set[str] = set()
        for patch in original_addition_patches:
            task = workflow_tasks_by_id.get(patch.task_id)
            same_task_history = [
                item for item in existing_ledger.patches
                if item.task_id == patch.task_id
            ]
            active_history = [
                item for item in same_task_history
                if item.status not in {"superseded", "blocked"}
            ]
            if len(active_history) > 1:
                raise ValueError(
                    f"Task {patch.task_id} has multiple active Patches; resolve its lineage before rebasing."
                )

            prior = active_history[0] if active_history else None
            if prior is not None:
                same_patch_scope = (
                    prior.reviewer == patch.reviewer
                    and prior.comment == patch.comment
                    and prior.proposed_response == patch.proposed_response
                    and [item.to_dict() for item in prior.operations]
                    == [item.to_dict() for item in patch.operations]
                )
                if same_patch_scope:
                    if prior.status == "planned":
                        # Idempotent rerun: keep the existing Patch and rewrite
                        # incoming ResponseUnit links to its canonical ID. The
                        # workflow task's patch_id may have been re-numbered.
                        for unit in ledger.response_units:
                            if patch.patch_id in unit.linked_patch_ids:
                                unit.linked_patch_ids = [
                                    prior.patch_id if item == patch.patch_id else item
                                    for item in unit.linked_patch_ids
                                ]
                        superseded_in_addition.add(patch.patch_id)
                        continue
                    raise ValueError(
                        f"Task {patch.task_id} already has a {prior.status} Patch with unchanged operands. "
                        "Refresh its input DOCX from the latest verified document output before rebuilding."
                    )

                matching_ready_events = [
                    event for event in ready_events_by_task.get(patch.task_id, [])
                    if event.get("fingerprint") == workflow_scope_fingerprint(task)
                ]
                if not matching_ready_events:
                    raise ValueError(
                        f"Task {patch.task_id} has no author-approval event for the current Patch scope."
                    )
                actor = matching_ready_events[-1]["actor"]
                next_number = 2
                candidate_id = f"{patch.patch_id}-r{next_number}"
                existing_ids = {item.patch_id for item in existing_ledger.patches}
                while candidate_id in existing_ids:
                    next_number += 1
                    candidate_id = f"{patch.patch_id}-r{next_number}"
                replacement = replace(
                    patch,
                    patch_id=candidate_id,
                    supersedes_patch_id=prior.patch_id,
                )
                existing_ledger.supersede_patch(
                    prior.patch_id,
                    replacement,
                    actor=actor,
                    reason=(
                        task.get("invalidation_reason")
                        or "The author reapproved this task against a refreshed document target."
                    ),
                )
                for unit in ledger.response_units:
                    if patch.patch_id in unit.linked_patch_ids:
                        unit.linked_patch_ids = [
                            candidate_id if item == patch.patch_id else item
                            for item in unit.linked_patch_ids
                        ]
                superseded_in_addition.add(patch.patch_id)
            else:
                collisions = [
                    item for item in existing_ledger.patches
                    if item.patch_id == patch.patch_id and item.task_id != patch.task_id
                ]
                if collisions:
                    raise ValueError(
                        f"Patch ID {patch.patch_id} is already owned by another task."
                    )
                if any(item.patch_id == patch.patch_id for item in same_task_history):
                    next_number = 2
                    candidate_id = f"{patch.patch_id}-r{next_number}"
                    existing_ids = {item.patch_id for item in existing_ledger.patches}
                    while candidate_id in existing_ids:
                        next_number += 1
                        candidate_id = f"{patch.patch_id}-r{next_number}"
                    patch.patch_id = candidate_id
                    for unit in ledger.response_units:
                        if candidate_id not in unit.linked_patch_ids and (
                            patch.task_id in unit.linked_task_ids
                        ):
                            unit.linked_patch_ids.append(candidate_id)

        if superseded_in_addition:
            ledger.patches = [
                patch for patch in ledger.patches
                if patch.patch_id not in superseded_in_addition
            ]
        existing_ledger.merge_batch(
            ledger,
            allow_scope_refresh_for_task_ids=sorted(changed_scope_tasks),
        )
        ledger = existing_ledger
    revision_payload = ledger.to_dict()
    revision_before_sha = sha256_file(output_path) if output_path.is_file() else None
    revision_payload_text = json.dumps(revision_payload, ensure_ascii=False, indent=2)
    revision_after_sha = hashlib.sha256(
        revision_payload_text.replace("\n", os.linesep).encode("utf-8")
    ).hexdigest()
    if selected_roles:
        from co_reviewer.batch_store import create_batch_intent
        batch_intent = create_batch_intent(
            workflow,
            workflow_path,
            selected_document_task_ids,
            selected_roles,
            all_selected_task_ids=selected_task_ids,
            runs_dir=str(runs_root),
            batch_id=batch_id_for_intent,
            creation_revision_ledger_file=output_path,
            creation_revision_ledger_sha256_before=revision_before_sha,
            creation_revision_ledger_sha256_after=revision_after_sha,
        )
        for role, record in batch_intent["documents"].items():
            expected = batch_document_paths[role]
            if (
                Path(record["parent_file"]).expanduser().resolve()
                != Path(expected["input_file"]).expanduser().resolve()
                or Path(record["output_file"]).expanduser().resolve()
                != Path(expected["output_file"]).expanduser().resolve()
                or record["parent_sha256"] != expected["source_sha256"]
            ):
                raise ValueError(
                    f"Created batch intent differs from the prevalidated Patch paths for {role}."
                )
    if batch_intent is not None:
        from co_reviewer.batch_store import write_json_new
        scope = {
            "schema_version": "1.0",
            "batch_id": batch_intent["batch_id"],
            "revision_round_id": ledger.revision_round_id,
            "revision_ledger_file": str(output_path),
            "selected_task_ids": list(selected_task_ids),
            "batch_task_ids": list(batch_intent["selected_task_ids"]),
            "batch_intent_sha256": sha256_file(batch_intent["batch_intent_file"]),
            "patch_ids": [
                patch.patch_id for patch in ledger.patches
                if patch.task_id in selected_task_ids
                and patch.status != "superseded"
                and any(
                    Path(operation.output_file).expanduser().resolve()
                    == Path(batch_intent["documents"][operation.document]["output_file"]).expanduser().resolve()
                    for operation in patch.operations
                    if operation.document in batch_intent["documents"]
                )
            ],
            "documents": {
                role: {
                    "input_file": record["parent_file"],
                    "input_sha256": record["parent_sha256"],
                    "output_file": record["output_file"],
                }
                for role, record in batch_intent["documents"].items()
            },
        }
        if not scope["patch_ids"]:
            raise ValueError(
                "A document-changing batch has no Patch IDs bound to its immutable output paths; "
                "the intent-only directory is retained and no RevisionLedger update was committed."
            )
        scope_path = Path(batch_intent["batch_dir"]) / "batch_scope.json"
        scope_sha = write_json_new(scope_path, scope)
        current_revision_before_commit = sha256_file(output_path) if output_path.is_file() else None
        if current_revision_before_commit != revision_before_sha:
            raise ValueError(
                "RevisionLedger changed after batch creation was staged; neither the batch nor the new Ledger snapshot was committed."
            )
        write_json_atomic(output_path, revision_payload)
        committed_revision_sha = sha256_file(output_path)
        if committed_revision_sha != revision_after_sha:
            raise ValueError(
                "RevisionLedger write did not match the staged batch-creation after-image; preserve the batch and inspect both snapshots."
            )
        from co_reviewer.batch_store import write_json_new
        creation_receipt = {
            "schema_version": "1.0",
            "status": "committed",
            "batch_id": batch_intent["batch_id"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "revision_ledger_file": str(output_path),
            "revision_ledger_sha256_before": revision_before_sha,
            "revision_ledger_sha256_after": committed_revision_sha,
            "batch_intent_sha256": sha256_file(Path(batch_intent["batch_dir"]) / "batch_intent.json"),
            "batch_scope_sha256": scope_sha,
            "revision_ledger_committed": True,
            "no_patch_records_committed": False,
            "active_parent_pointers_unchanged": True,
            "execution_artifacts_present": False,
            "retained_batch_files": True,
        }
        write_json_new(Path(batch_intent["batch_dir"]) / "batch_creation_receipt.json", creation_receipt)
    else:
        write_json_atomic(output_path, revision_payload)
    print(f"[BATCH] Selected ready task(s): {', '.join(selected_task_ids)}")
    if batch_intent is not None:
        print(f"[IMMUTABLE BATCH] {batch_intent['batch_id']}")
        print(f"[BATCH DIRECTORY] {batch_intent['batch_dir']}")
        print(f"[BATCH INTENT] {batch_intent['batch_intent_file']}")
        print(f"[NEXT] Export plan with --batch-dir \"{batch_intent['batch_dir']}\"; never redirect output DOCX paths outside this batch.")
    print(f"[LEDGER] {len(ledger.patches)} Patch record(s); {len(ledger.response_units)} ResponseUnit(s).")
    print(f"[OK] RevisionLedger: {output_path}")
    return 0


def cmd_create_working_clean_views(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    ledger = _load_revision_ledger(ledger_path)
    if not ledger.documents:
        raise ValueError("RevisionLedger contains no RevisionDocument records.")

    plans = []
    reserved = {ledger_path}
    for document in ledger.documents:
        marked_path = Path(document.output_file).expanduser().resolve()
        input_path = Path(document.input_file).expanduser().resolve()
        if not marked_path.is_file():
            raise FileNotFoundError(
                f"Marked DOCX for {document.role} does not exist: {marked_path}"
            )
        clean_path = marked_path.with_name(
            f"{marked_path.stem}{args.suffix}{marked_path.suffix}"
        )
        if clean_path in reserved or clean_path in {marked_path, input_path}:
            raise ValueError(
                f"Working Clean View path collides with another artifact: {clean_path}"
            )
        if clean_path.exists():
            raise FileExistsError(
                f"Working Clean View already exists; refusing to overwrite: {clean_path}"
            )
        reserved.add(clean_path)
        plans.append((document, marked_path, clean_path))

    created: list[Path] = []
    now = datetime.now(timezone.utc).isoformat()
    from co_reviewer.word_pdf_mapper import build_unique_location_phrase
    try:
        for index, (document, marked_path, clean_path) in enumerate(plans):
            marked_bytes = marked_path.read_bytes()
            marked_sha256 = hashlib.sha256(marked_bytes).hexdigest()
            if document.output_sha256 and document.output_sha256 != marked_sha256:
                raise ValueError(
                    f"Marked DOCX for {document.role} changed since its last recorded output hash."
                )
            lineage_records = [
                record for patch in ledger.patches for record in patch.applied_revision_ids
            ]
            lineage = collect_revision_ids_from_lineage(
                lineage_records, marked_path, marked_sha256
            )
            clean_validation = compile_clean_from_marked(
                marked_bytes, expected_revision_ids=lineage["revision_ids"]
            )
            report = create_working_clean_view(marked_path, clean_path)
            created.append(clean_path)
            if report["source_sha256"] != clean_validation["marked_sha256"]:
                raise ValueError(f"Marked DOCX for {document.role} changed during Clean projection.")
            if report["output_sha256"] != clean_validation["clean_sha256"]:
                raise ValueError(f"Working Clean projection for {document.role} differs from the validated compiler output.")
            if document.output_sha256 and document.output_sha256 != report["source_sha256"]:
                raise ValueError(
                    f"Marked DOCX for {document.role} changed since its last recorded output hash."
                )
            old_clean_sha256 = document.working_clean_sha256
            ledger.documents[index] = replace(
                document,
                output_sha256=report["source_sha256"],
                working_clean_file=report["output_file"],
                working_clean_sha256=report["output_sha256"],
                working_view_fingerprint=report["working_view_fingerprint"],
            )
            with zipfile.ZipFile(clean_path, "r") as clean_archive:
                clean_names = set(clean_archive.namelist())
                clean_root = parse_xml(clean_archive.read("word/document.xml"))
                final_paragraph_nodes = clean_root.findall(".//w:body//w:p", NS)
                final_paragraphs = [visible_text(paragraph) for paragraph in final_paragraph_nodes]

                # Resolve no-change anchors in their declared Word part, using
                # paraId first and the exact source excerpt as the fallback.
                # Manual-required anchors are still revalidated in this current
                # Clean package, but do not need an automatic Word Find phrase.
                for unit in ledger.response_units:
                    if (
                        unit.disposition != "no_change"
                        or unit.location_requirement not in {"existing_anchor_required", "manual_required"}
                        or unit.anchor_document != document.role
                    ):
                        continue
                    anchor_part = unit.anchor_part
                    if not isinstance(anchor_part, str) or anchor_part not in clean_names:
                        raise ValueError(
                            f"Declared no-change anchor part {anchor_part!r} for "
                            f"{unit.response_unit_id} is missing from final {document.role} Clean DOCX."
                        )
                    try:
                        anchor_root = (
                            clean_root if anchor_part == "word/document.xml"
                            else parse_xml(clean_archive.read(anchor_part))
                        )
                    except Exception as exc:
                        raise ValueError(
                            f"Cannot read current Clean anchor part {anchor_part} for {unit.response_unit_id}: {exc}"
                        ) from exc
                    paragraph_nodes = anchor_root.findall(".//w:p", NS)
                    paragraph_texts = [visible_text(paragraph) for paragraph in paragraph_nodes]
                    para_id_key = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
                    anchor_indices = [
                        idx for idx, paragraph in enumerate(paragraph_nodes)
                        if unit.anchor_para_id and paragraph.get(para_id_key) == unit.anchor_para_id
                    ]
                    if len(anchor_indices) != 1:
                        anchor_indices = [
                            idx for idx, paragraph_text in enumerate(paragraph_texts)
                            if unit.anchor_source_excerpt and unit.anchor_source_excerpt in paragraph_text
                        ]
                    if len(anchor_indices) != 1:
                        raise ValueError(
                            f"Cannot uniquely resolve no-change anchor {unit.response_unit_id} in "
                            f"{anchor_part} of final {document.role} Clean DOCX; its paragraph ID or exact evidence excerpt is missing/ambiguous."
                        )
                    final_anchor_index = anchor_indices[0]
                    anchor_text = paragraph_texts[final_anchor_index]
                    if anchor_text.count(unit.anchor_source_excerpt) != 1:
                        raise ValueError(
                            f"No-change anchor excerpt for {unit.response_unit_id} occurs zero or multiple times "
                            f"in final paragraph {final_anchor_index} of {anchor_part}; refusing to guess."
                        )
                    anchor_start = anchor_text.find(unit.anchor_source_excerpt)
                    if anchor_start < 0:
                        raise ValueError(
                            f"No-change anchor excerpt for {unit.response_unit_id} is no longer present in final "
                            f"{document.role} Clean DOCX; do not reuse baseline page/line references."
                        )
                    unit.anchor_p_idx = final_anchor_index
                    if unit.location_requirement == "manual_required":
                        unit.location_phrase = None
                    else:
                        if anchor_part != "word/document.xml":
                            raise ValueError(
                                f"No-change anchor {unit.response_unit_id} is in {anchor_part}; "
                                "use location_requirement=manual_required instead of guessing a body line locator."
                            )
                        unit.location_phrase = build_unique_location_phrase(
                            final_paragraphs, final_anchor_index,
                            anchor_start, anchor_start + len(unit.anchor_source_excerpt),
                        )

            # Refresh operation locators against the final accepted text. The preflight
            # phrase remains valid when unchanged; otherwise use the surviving replacement.
            for patch in ledger.patches:
                refreshed_operations = []
                for operation in patch.operations:
                    if operation.document != document.role:
                        refreshed_operations.append(operation)
                        continue
                    phrase = operation.location_phrase
                    phrase_count = sum(paragraph.count(phrase) for paragraph in final_paragraphs) if phrase else 0
                    phrase_in_expected_paragraph = (
                        operation.p_idx is None
                        or (
                            0 <= operation.p_idx < len(final_paragraphs)
                            and bool(phrase)
                            and phrase in final_paragraphs[operation.p_idx]
                        )
                    )
                    if phrase_count != 1 or not phrase_in_expected_paragraph:
                        if operation.replace and operation.p_idx is not None and 0 <= operation.p_idx < len(final_paragraphs):
                            paragraph_text = final_paragraphs[operation.p_idx]
                            if paragraph_text.count(operation.replace) == 1:
                                start = paragraph_text.find(operation.replace)
                                phrase = build_unique_location_phrase(final_paragraphs, operation.p_idx, start, start + len(operation.replace))
                            else:
                                raise ValueError(f"Cannot refresh unique final location_phrase for {patch.patch_id}/{operation.operation_id}; the replacement no longer exists uniquely in the final Clean DOCX.")
                        else:
                            raise ValueError(f"Cannot refresh final location_phrase for delete-only or superseded operation {patch.patch_id}/{operation.operation_id}; its preflight flank anchor no longer survives in the final Clean DOCX.")
                    refreshed_operations.append(replace(operation, location_phrase=phrase))
                patch.operations = refreshed_operations

            if old_clean_sha256 and old_clean_sha256 != report["output_sha256"]:
                for unit in ledger.response_units:
                    stale = [location for location in unit.final_locations if location.document == document.role]
                    if not stale:
                        continue
                    unit.final_locations = [location for location in unit.final_locations if location.document != document.role]
                    unit.location_map_fingerprint = None
                    unit.location_map_file = None
                    unit.location_map_file_sha256 = None
                    unit.completed_at_utc = None
                    if unit.disposition != "clarification_needed" and unit.semantic_review_confirmed:
                        unit.status = "verified"
                    ledger.events.append(RevisionEvent(
                        event_id=str(uuid.uuid4()), subject_id=unit.response_unit_id,
                        subject_type="response_unit", event_type="document_role_locations_invalidated",
                        timestamp_utc=now, actor=args.actor,
                        details={
                            "document": document.role,
                            "previous_working_clean_sha256": old_clean_sha256,
                            "current_working_clean_sha256": report["output_sha256"],
                            "invalidated_locator_ids": [location.locator_id for location in stale],
                            "scope": "entire_document_role",
                        },
                    ))
            for patch in ledger.patches:
                if any(operation.document == document.role for operation in patch.operations):
                    ledger.events.append(RevisionEvent(
                        event_id=str(uuid.uuid4()),
                        subject_id=patch.patch_id,
                        subject_type="patch",
                        event_type="working_clean_view_created",
                        timestamp_utc=now,
                        actor=args.actor,
                        details={
                            "document": document.role,
                            "marked_file": report["source_file"],
                            "marked_sha256": report["source_sha256"],
                            "working_clean_file": report["output_file"],
                            "working_clean_sha256": report["output_sha256"],
                            "working_view_fingerprint": report["working_view_fingerprint"],
                            "projection_policy": "accept-insertions-reject-deletions-v1",
                        },
                    ))
        ledger.validate_or_raise()
        write_json_atomic(ledger_path, ledger.to_dict())
    except Exception:
        for path in created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    print(f"[WORKING CLEAN VIEW] Created {len(created)} immutable projection(s).")
    for _, _, path in plans:
        print(f"[VIEW] {path}")
    print(f"[OK] RevisionLedger updated: {ledger_path}")
    print("[GUARD] Source and Marked DOCX files were not modified; Word final-layout acceptance is separate.")
    return 0


def cmd_confirm_response_units(args):
    if not getattr(args, "semantic_review_confirmed", False):
        raise ValueError(
            "Explicit semantic-review acknowledgement is required before confirming ResponseUnits."
        )
    ledger_path = Path(args.ledger).expanduser().resolve()
    ledger = _load_revision_ledger(ledger_path)
    report_id = args.verification_report_id or (
        "response-review:" + datetime.now(timezone.utc).isoformat()
    )
    ledger.confirm_response_units(
        args.ids,
        verification_report_id=report_id,
        actor=args.actor,
    )
    write_json_atomic(ledger_path, ledger.to_dict())
    print(f"[RESPONSE VERIFIED] {len(args.ids)} ResponseUnit(s): {', '.join(args.ids)}")
    print("[GUARD] This records semantic review only; final PDF page/line mapping is still required.")
    return 0


def cmd_build_document_identity(args):
    from co_reviewer.docx_identity import build_document_identity_manifest

    inventory_path = Path(args.inventory).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    previous_path = (
        Path(args.previous_manifest).expanduser().resolve()
        if args.previous_manifest else None
    )
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    protected_inputs = {inventory_path}
    if previous_path:
        protected_inputs.add(previous_path)
    inventory_rows = (
        inventory.get("documents", [])
        if isinstance(inventory, dict) else inventory
    )
    from co_reviewer.working_view import inspect_clean_baseline
    for row in inventory_rows if isinstance(inventory_rows, list) else []:
        if isinstance(row, dict) and isinstance(row.get("file"), str):
            protected_inputs.add(Path(row["file"]).expanduser().resolve())
            if row.get("role") in {"Manuscript", "SI"}:
                baseline = inspect_clean_baseline(row["file"])
                if not baseline["clean"]:
                    issue_details = json.dumps(baseline["issues"], ensure_ascii=False, indent=2)
                    raise ValueError(
                        f"Initial {row['role']} baseline is not clean; document identity registration is blocked. "
                        f"Unresolved Word markup: {baseline['blocking_counts']}. "
                        "Resolve existing Track Changes and comments before starting this review round.\n"
                        f"Exact package/paragraph positions and excerpts:\n{issue_details}"
                    )
    if output_path in protected_inputs:
        raise ValueError("Identity manifest output must not overwrite an inventory or source DOCX file.")
    if output_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite an existing identity manifest: {output_path}"
        )
    previous = (
        json.loads(previous_path.read_text(encoding="utf-8"))
        if previous_path else None
    )
    manifest, report = build_document_identity_manifest(
        inventory,
        previous_manifest=previous,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_path, manifest)
    totals = report["totals"]
    print(
        f"[IDENTITY] {totals['documents']} document(s); {totals['objects']} object(s)."
    )
    print(
        "[MATCH] "
        f"native paraId={totals['matched_native_id']}; "
        f"unique exact fingerprint={totals['matched_exact_fingerprint']}; "
        f"new={totals['new_objects']}; "
        f"ambiguous={totals['ambiguous_matches']}."
    )
    print(f"[OK] Identity manifest: {output_path}")
    return 0


def cmd_map_response_locations(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    output_dir = Path(args.out_dir).expanduser().resolve()
    location_map_path = (
        Path(args.location_map).expanduser().resolve()
        if args.location_map
        else output_dir / "response_location_map.json"
    )
    if ledger_path == location_map_path:
        raise ValueError("Location map must not overwrite the RevisionLedger.")
    if location_map_path.suffix.casefold() != ".json":
        raise ValueError("Location map output must use a .json extension.")
    if location_map_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite an existing location map: {location_map_path}"
        )

    ledger_sha256_before = sha256_file(ledger_path)
    ledger = _load_revision_ledger(ledger_path)
    if sha256_file(ledger_path) != ledger_sha256_before:
        raise ValueError("RevisionLedger changed while it was being loaded; retry from the latest ledger.")
    from co_reviewer.word_pdf_mapper import generate_word_pdf_location_map

    location_map = generate_word_pdf_location_map(
        ledger,
        ledger_path=ledger_path,
        output_dir=output_dir,
    )
    quality_reports = []
    for index, document_output in enumerate(location_map.get("documents", []), 1):
        report = audit_docx_quality(
            document_output["output_file"],
            profile="word-final-clean",
            pdf_path=document_output["pdf_file"],
            word_page_count=document_output.get("word_page_count"),
        )
        if report["status"] == "FAIL":
            raise ValueError(
                f"Word/PDF output failed deterministic quality checks for "
                f"{document_output.get('document')}: {report['summary']}"
            )
        report_name = f"word_final_quality_{index:02d}.json"
        report_path = output_dir / report_name
        write_json_atomic(report_path, report)
        document_output["quality_report_file"] = str(report_path)
        document_output["quality_report_sha256"] = sha256_file(report_path)
        quality_reports.append({
            "document": document_output.get("document"),
            "report_file": str(report_path),
            "report_sha256": sha256_file(report_path),
            "status": report["status"],
        })
    location_map["quality_reports"] = quality_reports
    if location_map.get("revision_ledger_sha256") != ledger_sha256_before:
        raise ValueError("RevisionLedger changed before Word/PDF mapping began; refusing stale mapping.")
    if sha256_file(ledger_path) != ledger_sha256_before:
        raise ValueError("RevisionLedger changed during Word/PDF mapping; generated outputs were not attached.")
    location_map_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(location_map_path, location_map)
    location_map["location_map_file"] = str(location_map_path)
    location_map["location_map_file_sha256"] = sha256_file(location_map_path)
    ledger.record_response_locations(location_map, actor=args.actor)
    if sha256_file(ledger_path) != ledger_sha256_before:
        raise ValueError("RevisionLedger changed before location evidence could be recorded; refusing to overwrite it.")
    write_json_atomic(ledger_path, ledger.to_dict())

    mapped = sum(
        1 for unit in ledger.response_units
        if unit.status in {"location_mapped", "completed"}
    )
    print(f"[WORD/PDF MAPPED] {mapped} ResponseUnit(s) have verified final locations.")
    print(f"[DOCX/PDF OUTPUT] {output_dir}")
    print(f"[LOCATION MAP] {location_map_path}")
    print(f"[OK] RevisionLedger: {ledger_path}")
    return 0


def cmd_record_response_locations(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    location_map_path = Path(args.location_map).expanduser().resolve()
    if ledger_path == location_map_path:
        raise ValueError("Location map must not overwrite the RevisionLedger.")
    ledger = _load_revision_ledger(ledger_path)
    location_map = json.loads(location_map_path.read_text(encoding="utf-8"))
    if not isinstance(location_map, dict):
        raise ValueError("Location map JSON root must be an object.")
    from co_reviewer.word_pdf_mapper import assert_location_map_matches_ledger
    assert_location_map_matches_ledger(location_map, ledger_path)
    location_map["location_map_file"] = str(location_map_path)
    location_map["location_map_file_sha256"] = sha256_file(location_map_path)
    ledger.record_response_locations(location_map, actor=args.actor)
    write_json_atomic(ledger_path, ledger.to_dict())
    mapped = sum(1 for unit in ledger.response_units if unit.status in {"location_mapped", "completed"})
    print(f"[LOCATIONS] {mapped} ResponseUnit(s) now have final PDF page/line mappings.")
    print(f"[OK] RevisionLedger: {ledger_path}")
    return 0


def cmd_record_manual_response_locations(args):
    """Record author-entered table/non-body locators against Word-exported final files."""
    ledger_path = Path(args.ledger).expanduser().resolve()
    word_map_path = Path(args.word_map).expanduser().resolve()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.out).expanduser().resolve()
    if len({ledger_path, word_map_path, input_path, output_path}) != 4:
        raise ValueError("Ledger, Word location map, manual input and manual output must be four distinct files.")
    if output_path.exists():
        raise ValueError(f"Refusing to overwrite an existing manual location map: {output_path}")

    ledger = _load_revision_ledger(ledger_path)
    word_map = json.loads(word_map_path.read_text(encoding="utf-8"))
    manual_input = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(word_map, dict) or not isinstance(manual_input, dict):
        raise ValueError("Word location map and manual locator input must be JSON objects.")
    if word_map.get("generator") != "co-reviewer-word-pdf-mapper-v1":
        raise ValueError("--word-map must be a location map generated by Microsoft Word COM.")
    from co_reviewer.word_pdf_mapper import (
        WordPdfMappingError,
        assert_location_map_matches_ledger,
    )
    try:
        assert_location_map_matches_ledger(word_map, ledger_path)
    except WordPdfMappingError:
        # In a mixed automatic/manual batch, the ordinary Word map may already
        # have been recorded for automatic ResponseUnits. That write changes the
        # ledger SHA, so accept only when this exact immutable map file and its
        # location_map_id are already hash-bound by a mapped ResponseUnit.
        current_word_map_sha = sha256_file(word_map_path)
        current_map_id = word_map.get("location_map_id")
        registered = [
            unit for unit in ledger.response_units
            if unit.location_map_file
            and Path(unit.location_map_file).expanduser().resolve() == word_map_path
            and unit.location_map_file_sha256 == current_word_map_sha
            and unit.final_locations
            and all(location.location_map_id == current_map_id for location in unit.final_locations)
        ]
        if not registered:
            raise
    if not isinstance(manual_input.get("response_units"), list):
        raise ValueError("Manual locator input must contain a response_units array.")

    documents_by_role = {item.role: item for item in ledger.documents}
    output_rows = word_map.get("documents")
    if not isinstance(output_rows, list):
        raise ValueError("Word location map is missing its documents array.")
    word_outputs: dict[str, dict[str, Any]] = {}
    for row in output_rows:
        if not isinstance(row, dict) or not isinstance(row.get("document"), str):
            raise ValueError("Word map document rows must have a document role.")
        role = row["document"]
        if role in word_outputs:
            raise ValueError(f"Word map contains duplicate document role {role}.")
        audit = row.get("sequence_audit")
        if not isinstance(audit, dict) or audit.get("status") not in {"pass", "manual_review"}:
            raise ValueError(f"Word map has no recognised passing/manual-review sequence audit for {role}.")
        word_outputs[role] = row
    operation_owners = {
        operation.operation_id: (patch, operation)
        for patch in ledger.patches
        for operation in patch.operations
    }
    units_by_id = {unit.response_unit_id: unit for unit in ledger.response_units}
    submitted_by_unit: dict[str, dict[str, dict[str, Any]]] = {}
    for row in manual_input["response_units"]:
        if not isinstance(row, dict):
            raise ValueError("Manual response_units entries must be objects.")
        unit_id = row.get("response_unit_id")
        locator_key = row.get("locator_id", row.get("operation_id"))
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise ValueError("Each manual locator entry requires response_unit_id.")
        if not isinstance(locator_key, str) or not locator_key.strip():
            raise ValueError(f"Manual locator entry for {unit_id} requires locator_id (or operation_id for compatibility).")
        if unit_id not in units_by_id:
            raise ValueError(f"Unknown ResponseUnit in manual locator input: {unit_id}.")
        if locator_key in submitted_by_unit.setdefault(unit_id, {}):
            raise ValueError(f"Duplicate manual locator for {unit_id}/{locator_key}.")
        submitted_by_unit[unit_id][locator_key] = row

    for unit_id, locator_rows in submitted_by_unit.items():
        unit = units_by_id[unit_id]
        if unit.location_requirement != "manual_required":
            raise ValueError(
                f"Manual location input includes {unit_id}, which is not awaiting manual_required location evidence."
            )
        if unit.status != "verified" or not unit.semantic_review_confirmed:
            raise ValueError(
                f"Manual location input includes {unit_id}, which is not semantically verified and ready for final location."
            )

    location_map_id = "manual-locator-" + uuid.uuid4().hex
    response_rows: list[dict[str, Any]] = []
    for unit_id, unit in units_by_id.items():
        if unit.location_requirement != "manual_required":
            continue
        if unit.disposition not in {"revised", "no_change"} or unit.status != "verified" or not unit.semantic_review_confirmed:
            raise ValueError(
                f"{unit_id} must be a semantically verified revised or anchored no_change ResponseUnit before manual location."
            )
        submitted = submitted_by_unit.get(unit_id, {})
        expected_locators = (
            set(unit.linked_operation_ids)
            if unit.disposition == "revised"
            else {f"anchor:{unit.anchor_evidence_id}"}
        )
        if set(submitted) != expected_locators:
            missing = sorted(expected_locators - set(submitted))
            extra = sorted(set(submitted) - expected_locators)
            raise ValueError(
                f"Manual locators for {unit_id} must cover every exact linked locator; "
                f"missing={missing}, unexpected={extra}."
            )
        final_locations: list[dict[str, Any]] = []
        for locator_key in sorted(expected_locators):
            patch = operation = None
            if unit.disposition == "revised":
                operation_id = locator_key
                patch, operation = operation_owners[operation_id]
                if patch.patch_id not in unit.linked_patch_ids or unit.comment_id not in operation.source_comment_ids:
                    raise ValueError(f"Manual locator attribution mismatch for {unit_id}/{locator_key}.")
                if patch.status != "verified":
                    raise ValueError(f"Manual locator operation {locator_key} belongs to unverified Patch {patch.patch_id}.")
                role = operation.document
                stable_locator_id = f"manual:{locator_key}"
            else:
                if not unit.anchor_evidence_id or not unit.anchor_document or not unit.anchor_part or unit.anchor_p_idx is None:
                    raise ValueError(f"Manual no_change ResponseUnit {unit_id} has no confirmed Clean anchor evidence.")
                role = unit.anchor_document
                stable_locator_id = f"manual:anchor:{unit.anchor_evidence_id}"
            revision_document = documents_by_role.get(role)
            word_output = word_outputs.get(role)
            if revision_document is None or word_output is None:
                raise ValueError(f"Word COM final output is unavailable for document role {role}.")
            if word_output.get("source_working_clean_sha256") != revision_document.working_clean_sha256:
                raise ValueError(f"Word map for {role} does not match its current Working Clean SHA-256.")
            word_page_count = word_output.get("word_page_count")
            if not isinstance(word_page_count, int) or isinstance(word_page_count, bool) or word_page_count < 1:
                raise ValueError(f"Word map has no valid Word COM page count for {role}.")
            output_file = Path(str(word_output.get("output_file", ""))).expanduser().resolve()
            pdf_file = Path(str(word_output.get("pdf_file", ""))).expanduser().resolve()
            output_sha = word_output.get("output_sha256")
            pdf_sha = word_output.get("pdf_sha256")
            if not output_file.is_file() or sha256_file(output_file) != output_sha:
                raise ValueError(f"Word-final DOCX is missing or changed for {role}: {output_file}.")
            if not pdf_file.is_file() or sha256_file(pdf_file) != pdf_sha:
                raise ValueError(f"Word-final PDF is missing or changed for {role}: {pdf_file}.")

            source = submitted[locator_key]
            locator_text = source.get("locator_text")
            if (
                not isinstance(locator_text, str)
                or not locator_text.strip()
                or any(character in locator_text for character in "\r\n\t")
            ):
                raise ValueError(
                    f"Manual locator text for {unit_id}/{locator_key} must be non-empty and single-line."
                )
            page = source.get("page")
            line_start = source.get("line_start")
            line_end = source.get("line_end")
            if page is not None and (not isinstance(page, int) or isinstance(page, bool) or page < 1):
                raise ValueError(f"Manual page for {unit_id}/{locator_key} must be a positive integer or omitted.")
            if page is not None and page > word_page_count:
                raise ValueError(
                    f"Manual page {page} for {unit_id}/{locator_key} exceeds the Word COM page count "
                    f"{word_page_count} for {role}."
                )
            if line_start is not None or line_end is not None:
                if page is None or any(
                    not isinstance(value, int) or isinstance(value, bool) or value < 1
                    for value in (line_start, line_end)
                ):
                    raise ValueError(
                        f"Manual lines for {unit_id}/{locator_key} require page, line_start and line_end positive integers."
                    )
                if line_end < line_start:
                    raise ValueError(f"Manual line_end precedes line_start for {unit_id}/{locator_key}.")
            final_locations.append({
                "document": role,
                "output_file": str(output_file),
                "output_sha256": output_sha,
                "pdf_file": str(pdf_file),
                "pdf_sha256": pdf_sha,
                "page": page,
                "line_start": line_start,
                "line_end": line_end,
                "locator_text": locator_text.strip(),
                "target_sha256": hashlib.sha256(locator_text.strip().encode("utf-8")).hexdigest(),
                "patch_id": patch.patch_id if patch is not None else None,
                "operation_id": operation.operation_id if operation is not None else None,
                "locator_id": stable_locator_id,
                "source_clean_sha256": revision_document.working_clean_sha256,
                "location_map_id": location_map_id,
                "mapping_method": "manual",
            })
        fingerprint_payload = {
            "response_unit_id": unit_id,
            "location_map_id": location_map_id,
            "revision_ledger_sha256": sha256_file(ledger_path),
            "final_locations": final_locations,
            "mapping_method": "manual",
        }
        fingerprint = hashlib.sha256(json.dumps(
            fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        for location in final_locations:
            location["mapping_fingerprint"] = fingerprint
        response_rows.append({
            "response_unit_id": unit_id,
            "location_map_fingerprint": fingerprint,
            "final_locations": final_locations,
        })

    if not response_rows:
        raise ValueError("No manual_required ResponseUnits are awaiting manual locators.")
    expected_unit_ids = {row["response_unit_id"] for row in response_rows}
    unexpected_unit_ids = set(submitted_by_unit) - expected_unit_ids
    if unexpected_unit_ids:
        raise ValueError(
            "Manual input contains ResponseUnits that do not require manual locators: "
            + ", ".join(sorted(unexpected_unit_ids))
        )

    manual_map = {
        "schema_version": "0.5.1",
        "location_map_id": location_map_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "co-reviewer-manual-locator-v1",
        "revision_ledger_sha256": sha256_file(ledger_path),
        "line_numbering_policy": "manual-explicit-locator-v1",
        "documents": output_rows,
        "quality_reports": word_map.get("quality_reports", []),
        "response_units": response_rows,
        "skipped_response_units": [],
        "document_refresh_policy": (
            "Final DOCX/PDF artifacts are hash-bound to the current Working Clean inputs; "
            "locator text and optional page/line values were entered and confirmed by the author."
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_path, manual_map)
    manual_map["location_map_file"] = str(output_path)
    manual_map["location_map_file_sha256"] = sha256_file(output_path)
    ledger.record_response_locations(manual_map, actor=args.actor)
    write_json_atomic(ledger_path, ledger.to_dict())
    print(f"[MANUAL LOCATIONS] {len(response_rows)} ResponseUnit(s) recorded.")
    print(f"[LOCATION MAP] {output_path}")
    print(f"[OK] RevisionLedger: {ledger_path}")
    return 0


def cmd_complete_response_units(args):
    if not getattr(args, "ready_for_submission_confirmed", False):
        raise ValueError("Explicit ready-for-submission confirmation is required.")
    ledger_path = Path(args.ledger).expanduser().resolve()
    ledger = _load_revision_ledger(ledger_path)
    ledger.complete_response_units(
        args.ids,
        ready_for_submission_confirmed=True,
        actor=args.actor,
    )
    write_json_atomic(ledger_path, ledger.to_dict())
    print(f"[RESPONSE COMPLETE] {len(args.ids)} ResponseUnit(s): {', '.join(args.ids)}")
    print("[NOTE] This records completion only; it does not submit files to a journal.")
    return 0


def cmd_supersede_patch(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    replacement_path = Path(args.replacement_patch).expanduser().resolve()
    if ledger_path == replacement_path:
        raise ValueError("Replacement Patch input must not overwrite the RevisionLedger.")
    ledger = _load_revision_ledger(ledger_path)
    data = json.loads(replacement_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Replacement Patch JSON root must be an object.")
    raw_operations = data.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ValueError("Replacement Patch must supply an explicit non-empty operations array.")
    replacement = Patch(
        patch_id=data.get("patch_id"),
        task_id=data.get("task_id"),
        reviewer=data.get("reviewer"),
        comment=data.get("comment"),
        proposed_response=data.get("proposed_response", ""),
        operations=[
            PatchOperation.from_mapping(
                row, fallback_id=f"{data.get('patch_id', 'PATCH')}-OP{index + 1:03d}"
            )
            for index, row in enumerate(raw_operations)
        ],
        status="planned",
        supersedes_patch_id=data.get("supersedes_patch_id"),
    )
    ledger.supersede_patch(
        args.previous_patch_id,
        replacement,
        actor=args.actor,
        reason=args.reason,
    )
    write_json_atomic(ledger_path, ledger.to_dict())
    print(
        f"[SUPERSEDED] {args.previous_patch_id} → {replacement.patch_id}; "
        "linked ResponseUnits reopened for semantic review and new PDF line mapping."
    )
    print(f"[OK] RevisionLedger: {ledger_path}")
    return 0


def cmd_export_revision_plan(args):
    ledger_path = Path(args.ledger).expanduser().resolve()
    ledger = _load_revision_ledger(ledger_path)
    batch_dir = _resolve_batch_dir_argument(args.batch_dir) if getattr(args, "batch_dir", None) else None
    if batch_dir is not None:
        from co_reviewer.batch_store import read_batch_intent, validate_batch_creation_receipt
        intent = read_batch_intent(batch_dir)
        if intent.get("batch_creation_protocol_version") == "1.0":
            receipt = validate_batch_creation_receipt(batch_dir, intent)
            if receipt.get("status") != "committed":
                raise ValueError("Cannot export a revision plan from a batch abandoned before its RevisionLedger commit.")
        scope_ledger_roles = {
            operation.document
            for patch in ledger.patches
            if patch.status == "planned"
            for operation in patch.operations
        }
        if scope_ledger_roles != set(intent["documents"]):
            raise ValueError("The selected Batch directory does not match the planned Patch document roles.")
        output_value = args.out or str(batch_dir / "revision_plan.json")
    else:
        output_value = args.out or "revision_plan.json"
    output_path = Path(output_value).expanduser().resolve()
    if output_path == ledger_path:
        raise ValueError("Legacy revision plan output must not overwrite the RevisionLedger.")
    if batch_dir is not None and batch_dir not in output_path.parents:
        raise ValueError("An immutable-batch revision plan must be written inside its batch directory.")
    if output_path.exists():
        raise FileExistsError(f"Output already exists; choose a new path: {output_path}")
    plan = ledger.to_legacy_revision_plan()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if batch_dir is not None:
        from co_reviewer.batch_store import write_json_new
        write_json_new(output_path, plan)
    else:
        write_json_atomic(output_path, plan)
    print(f"[EXPORT] {len(plan['revision_items'])} pending Patch(es) exported.")
    print("[GUARD] The plan is unapproved; preflight and explicit approve-plan are still required.")
    print(f"[OK] Legacy revision plan: {output_path}")
    return 0


def cmd_apply(args):
    plan_path = Path(args.plan).expanduser().resolve()
    batch_dir = plan_path.parent if (plan_path.parent / "batch_intent.json").is_file() else None
    if batch_dir is not None and not getattr(args, "revision_ledger", None):
        raise ValueError("Apply for an immutable batch requires --revision-ledger so Patch execution state remains synchronized.")
    report_value = args.report or (
        str(batch_dir / "apply_report.json") if batch_dir is not None else "revision_apply_report.json"
    )
    report_path = Path(report_value).expanduser().resolve()
    if batch_dir is not None:
        if batch_dir not in report_path.parents:
            raise ValueError("Apply report for an immutable batch must be written inside its batch directory.")
        if report_path.exists():
            raise FileExistsError(
                f"Immutable batch apply report already exists; start a new batch instead of overwriting: {report_path}"
            )
    output_plan_path = _batch_plan_snapshot(batch_dir, "applied") if batch_dir is not None else plan_path
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if batch_dir is not None:
        if report_path == output_plan_path:
            raise ValueError("Apply report and applied-plan snapshot must use different paths.")
        for item in plan.get("revision_items", []):
            for target in item.get("targets", []):
                output_file = Path(target["output_file"]).expanduser().resolve()
                if batch_dir not in output_file.parents:
                    raise ValueError(
                        f"Batch output escapes its immutable directory: {output_file}"
                    )
                if output_file.exists():
                    raise FileExistsError(
                        f"Batch output already exists; no in-place retry is permitted: {output_file}"
                    )
    validate_apply_artifact_paths(plan, plan_path, report_path)
    revision_ledger = None
    if getattr(args, "revision_ledger", None):
        ledger_path = Path(args.revision_ledger).expanduser().resolve()
        if ledger_path in {
            plan_path.expanduser().resolve(),
            report_path.expanduser().resolve(),
            output_plan_path.expanduser().resolve(),
        }:
            raise ValueError("RevisionLedger path must be distinct from plan/report/snapshot paths.")
        revision_ledger = _load_revision_ledger(ledger_path)
        revision_ledger.assert_legacy_plan_matches(plan)

    # Keep an append-only report history across incremental revision iterations.
    # Load and validate it before writing any new DOCX output files.
    report_history = []
    if report_path.exists():
        report_history = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(report_history, list):
            raise ValueError("Existing apply report must be a JSON array; refusing to overwrite it.")
        if any(not isinstance(entry, dict) for entry in report_history):
            raise ValueError("Existing apply report contains an invalid entry; refusing to overwrite it.")

    reports = apply_plan(plan, args.author)
    report_history.extend(reports)
    applied_ids = {
        edit.get("revision_item_id")
        for report in reports
        for edit in report.get("results", [])
        if edit.get("status") == "applied"
    }
    for item in plan["revision_items"]:
        if item.get("status") == "approved" and item.get("id") in applied_ids:
            item["status"] = "applied"
    ledger_payload = None
    if revision_ledger is not None:
        revision_ledger.record_legacy_apply_reports(reports, plan)
        ledger_payload = revision_ledger.to_dict()

    # Batch artifacts are written once. Later states use a new immutable snapshot.
    if batch_dir is not None:
        from co_reviewer.batch_store import write_json_new
        write_json_new(report_path, report_history)
        write_json_new(output_plan_path, plan)
    else:
        write_json_atomic(report_path, report_history)
        write_json_atomic(plan_path, plan)
    if ledger_payload is not None:
        write_json_atomic(args.revision_ledger, ledger_payload)
        print(f"[LEDGER] RevisionLedger synchronized: {args.revision_ledger}")
    for r in reports: print(f"[DONE] {r['output_file']}: {r['edits_applied']}/{r['edits_requested']} edits")
    print(f"[STATE] Plan updated: {len(applied_ids)} revision item(s) marked applied.")
    if batch_dir is not None:
        print(f"[NEXT PLAN] {output_plan_path}")
    print(f"[OK] report: {report_path}")
    return 0


def cmd_verify(args):
    plan_path = Path(args.plan).expanduser().resolve()
    batch_dir = plan_path.parent if (plan_path.parent / "batch_intent.json").is_file() else None
    apply_report_value = args.apply_report or (
        str(batch_dir / "apply_report.json") if batch_dir is not None else "revision_apply_report.json"
    )
    apply_report_path = Path(apply_report_value).expanduser().resolve()
    out_value = args.out or (
        str(batch_dir / "verification_report.json") if batch_dir is not None else "verification_report.json"
    )
    output_path = Path(out_value).expanduser().resolve()
    if output_path == apply_report_path:
        raise ValueError("Verification report must not overwrite the Apply report.")
    if batch_dir is not None:
        if batch_dir not in apply_report_path.parents or batch_dir not in output_path.parents:
            raise ValueError("Verification inputs and outputs for an immutable batch must remain inside its batch directory.")
        if output_path.exists():
            raise FileExistsError(f"Verification report already exists for this immutable batch: {output_path}")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    apply_reports = json.loads(apply_report_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("revision_items"), list):
        raise ValueError("Plan must contain a revision_items array.")
    plan_errors = validate_plan(plan, require_approval=False)
    if plan_errors:
        raise ValueError("Invalid revision plan:\n- " + "\n- ".join(plan_errors))
    if plan.get("approved") is not True:
        raise ValueError("Verification requires the human-approved revision plan (approved=true).")
    if not isinstance(apply_reports, list):
        raise ValueError("Apply report must be a JSON array generated by the apply command.")
    report = verify_apply_report(plan, apply_reports)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if batch_dir is not None:
        from co_reviewer.batch_store import write_json_new
        write_json_new(output_path, report)
    else:
        write_json_atomic(output_path, report)
    print(f"[VERIFY] {report['targets_verified']}/{report['targets_total']} targets verified; {report['targets_failed']} failed.")
    audit = report.get("untracked_text_audit", {})
    print(
        f"[UNTRACKED TEXT AUDIT] {audit.get('documents_checked', 0)} document(s) checked; "
        f"{len(audit.get('violations', []))} untracked-text violation(s)."
    )
    for violation in audit.get("violations", []):
        print(
            f"[UNTRACKED TEXT] {violation.get('output_file', '')} "
            f"{violation.get('part', '')}: {violation.get('reason', '')}",
            file=sys.stderr
        )
    print("[SCOPE] Native Track Changes and untracked-text integrity only; scientific meaning and cross-document consistency still require review.")
    print(f"[OK] report: {output_path}")
    print("[NEXT] Mechanical verification is not semantic approval; review scientific consistency before marking items verified.")
    return 0 if report["passed"] else 2

def cmd_confirm_verified(args):
    """Promote selected applied items only after exact DOCX verification and semantic review."""
    if not getattr(args, "semantic_review_confirmed", False):
        raise ValueError(
            "Semantic review acknowledgement is required. Confirm revised Manuscript/SI claims, "
            "numeric values, units, sample identities, citations, figure/table references and cross-document consistency before running this command."
        )

    plan_path = Path(args.plan).expanduser().resolve()
    batch_dir = plan_path.parent if (plan_path.parent / "batch_intent.json").is_file() else None
    if batch_dir is not None and not getattr(args, "revision_ledger", None):
        raise ValueError("Verification confirmation for an immutable batch requires --revision-ledger.")
    apply_report_value = args.apply_report or (
        str(batch_dir / "apply_report.json") if batch_dir is not None else "revision_apply_report.json"
    )
    if batch_dir is not None and batch_dir not in Path(apply_report_value).expanduser().resolve().parents:
        raise ValueError("Apply report for an immutable batch must remain inside its batch directory.")
    output_plan_path = (
        batch_dir / "revision_plan.verified.json"
        if batch_dir is not None else plan_path
    )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or not isinstance(plan.get("revision_items"), list):
        raise ValueError("Plan must contain a revision_items array.")
    validate_apply_artifact_paths(plan, plan_path, apply_report_value)
    revision_ledger = None
    ledger_path: Path | None = None
    ledger_sha_at_load: str | None = None
    if getattr(args, "revision_ledger", None):
        ledger_path = Path(args.revision_ledger).expanduser().resolve()
        if ledger_path in {
            plan_path.expanduser().resolve(),
            Path(apply_report_value).expanduser().resolve(),
            output_plan_path.expanduser().resolve(),
        }:
            raise ValueError("RevisionLedger path must be distinct from plan/apply-report/verified-snapshot paths.")
        ledger_sha_at_load = sha256_file(ledger_path)
        revision_ledger = _load_revision_ledger(ledger_path)
        if sha256_file(ledger_path) != ledger_sha_at_load:
            raise ValueError("RevisionLedger changed while loading verification state.")
        revision_ledger.assert_legacy_plan_matches(plan)

    # If a process stopped after writing revision_plan.verified.json but before
    # committing the corresponding RevisionLedger update, reuse that immutable
    # snapshot only when every non-verification field still matches the applied
    # input plan. This makes the author confirmation stage resumable without
    # overwriting or silently adopting an unrelated verified plan.
    if batch_dir is not None and output_plan_path.exists():
        existing_verified = json.loads(output_plan_path.read_text(encoding="utf-8"))
        def verification_scope_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
            projected = copy.deepcopy(dict(payload))
            projected_items = []
            for row in projected.get("revision_items", []):
                item = dict(row)
                for key in (
                    "status", "semantic_review_confirmed", "verified_at_utc",
                    "verified_output_hashes", "verification_fingerprint",
                ):
                    item.pop(key, None)
                projected_items.append(item)
            projected["revision_items"] = projected_items
            return projected
        if (
            not isinstance(existing_verified, dict)
            or not isinstance(existing_verified.get("revision_items"), list)
            or verification_scope_projection(existing_verified) != verification_scope_projection(plan)
        ):
            raise ValueError(
                "An existing verified-plan snapshot belongs to a different applied plan; "
                "preserve it and do not overwrite this immutable batch."
            )
        for item_id in args.ids:
            existing_item = next(
                (row for row in existing_verified["revision_items"] if row.get("id") == item_id),
                None,
            )
            if not existing_item or existing_item.get("status") != "verified" or existing_item.get("semantic_review_confirmed") is not True:
                raise ValueError(
                    f"Existing verified-plan snapshot does not contain a semantically confirmed item: {item_id}"
                )
        plan = existing_verified
    errors = validate_plan(plan, require_approval=False)
    if errors:
        raise ValueError("Cannot verify an invalid revision plan:\n- " + "\n- ".join(errors))
    if plan.get("approved") is not True:
        raise ValueError("Verification requires the human-approved revision plan (approved=true).")

    requested = list(dict.fromkeys(args.ids))
    if not requested:
        raise ValueError("Provide at least one revision item ID with --ids.")
    by_id = {item.get("id"): item for item in plan["revision_items"]}
    unknown = [item_id for item_id in requested if item_id not in by_id]
    if unknown:
        raise ValueError("Unknown revision item ID(s): " + ", ".join(unknown))
    for item_id in requested:
        state = by_id[item_id].get("status")
        if state not in {"applied", "verified"}:
            raise ValueError(
                f"{item_id} is {state}; only applied items can be confirmed as verified."
            )

    apply_reports = json.loads(Path(apply_report_value).read_text(encoding="utf-8"))
    if not isinstance(apply_reports, list):
        raise ValueError("Apply report must be a JSON array generated by the apply command.")

    output_paths = {}
    for item_id in requested:
        for target in by_id[item_id]["targets"]:
            resolved = str(Path(target["output_file"]).expanduser().resolve())
            output_paths[os.path.normcase(resolved)] = resolved
    hashes_before = {key: sha256_file(path) for key, path in output_paths.items()}

    verification = verify_apply_report(plan, apply_reports)
    if not verification.get("untracked_text_audit", {}).get("passed", False):
        violations = verification.get("untracked_text_audit", {}).get("violations", [])
        details = "; ".join(
            f"{entry.get('output_file', '')} {entry.get('part', '')}: {entry.get('reason', '')}"
            for entry in violations[:5]
        )
        raise ValueError(
            "Cannot confirm verified while direct/untracked text changes are present or the "
            "untracked-text audit cannot complete. " + details
        )
    results_by_id = defaultdict(list)
    for result in verification["results"]:
        results_by_id[result.get("revision_item_id")].append(result)
    for item_id in requested:
        item_results = results_by_id.get(item_id, [])
        if len(item_results) != len(by_id[item_id]["targets"]):
            raise ValueError(
                f"{item_id} verification is incomplete: expected {len(by_id[item_id]['targets'])} target result(s), "
                f"found {len(item_results)}."
            )
        failed = [result for result in item_results if result.get("status") != "verified"]
        if failed:
            reasons = "; ".join(result.get("reason", "target verification failed") for result in failed)
            raise ValueError(f"{item_id} verification is incomplete: {reasons}")

    hashes_after = {key: sha256_file(path) for key, path in output_paths.items()}
    if hashes_before != hashes_after:
        raise ValueError("A revised DOCX changed during verification; no item was marked verified. Retry verification.")

    for item_id in requested:
        item = by_id[item_id]
        if item.get("status") == "verified" and item.get("semantic_review_confirmed") is True:
            existing_hashes = item.get("verified_output_hashes", [])
            expected_paths = {
                os.path.normcase(str(Path(target["output_file"]).expanduser().resolve()))
                for target in item["targets"]
            }
            existing_paths = {
                os.path.normcase(str(Path(row.get("path", "")).expanduser().resolve()))
                for row in existing_hashes if isinstance(row, Mapping)
            }
            if existing_paths != expected_paths or any(
                not isinstance(row, Mapping)
                or sha256_file(str(row.get("path", ""))) != row.get("sha256")
                for row in existing_hashes
            ):
                raise ValueError(f"Existing verified item {item_id} has stale or incomplete output hashes.")
            continue
        item["verified_output_hashes"] = [
            {"path": output_paths[key], "sha256": hashes_after[key]}
            for key in sorted(output_paths)
            if any(os.path.normcase(str(Path(target["output_file"]).expanduser().resolve())) == key
                   for target in item["targets"])
        ]
        item["semantic_review_confirmed"] = True
        item["verified_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        item["status"] = "verified"
        item["verification_fingerprint"] = revision_verification_fingerprint(item)

    errors = validate_plan(plan, require_approval=False)
    if errors:
        raise ValueError("Verification confirmation failed validation:\n- " + "\n- ".join(errors))
    ledger_payload = None
    if revision_ledger is not None:
        verification_id = (
            "confirm-verified:"
            + Path(apply_report_value).name
            + ":"
            + sha256_file(Path(apply_report_value))
        )
        revision_ledger.record_legacy_verified_items(
            plan,
            verification_report_id=verification_id,
        )
        ledger_payload = revision_ledger.to_dict()
    if batch_dir is not None:
        if output_plan_path.exists():
            existing_payload = json.loads(output_plan_path.read_text(encoding="utf-8"))
            if existing_payload != plan:
                raise ValueError("Existing verified-plan snapshot differs from the idempotently reconstructed confirmation.")
        else:
            from co_reviewer.batch_store import write_json_new
            write_json_new(output_plan_path, plan)
    else:
        write_json_atomic(plan_path, plan)
    if ledger_payload is not None:
        if sha256_file(ledger_path) != ledger_sha_at_load:
            raise ValueError(
                "RevisionLedger changed during semantic confirmation; verified plan snapshot is retained, "
                "but no stale Ledger state was written."
            )
        write_json_atomic(ledger_path, ledger_payload)
        print(f"[LEDGER] RevisionLedger synchronized: {ledger_path}")
    print(f"[VERIFIED] {len(requested)} revision item(s): {', '.join(requested)}")
    if batch_dir is not None:
        print(f"[NEXT PLAN] {output_plan_path}")
    print("[GUARD] Exact Track Changes were rechecked; current output DOCX hashes are sealed.")
    print("[HUMAN] Semantic review acknowledgement was recorded; the CLI does not judge scientific correctness.")
    return 0


def cmd_status(args):
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    errors = validate_plan(plan, require_approval=False)
    if errors:
        raise ValueError("Invalid revision plan:\n- " + "\n- ".join(errors))
    counts = defaultdict(int)
    for item in plan["revision_items"]:
        counts[item["status"]] += 1
    print(f"[SUMMARY] {len(plan['revision_items'])} revision item(s)")
    for status in ("planned", "approved", "applied", "verified", "blocked"):
        print(f"  {status}: {counts[status]}")
    for item in plan["revision_items"]:
        targets = len(item.get("targets", []))
        print(f"- {item['id']} | {item['reviewer']} | {item['status']} | {targets} target(s)")
    return 0

def cmd_reconcile_batch_creation(args):
    """Seal or safely abandon an interrupted pre-Apply batch-creation transaction."""
    from co_reviewer.batch_store import (
        load_active_document_pointers,
        read_batch_intent,
        validate_batch_creation_receipt,
        write_json_new,
    )

    batch_dir = _resolve_batch_dir_argument(args.batch_dir)
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    revision_path = Path(args.revision_ledger).expanduser().resolve()
    intent = read_batch_intent(batch_dir)
    if intent.get("batch_creation_protocol_version") != "1.0":
        print("[LEGACY BATCH] No creation-transaction reconciliation is required.")
        return 0
    if Path(str(intent.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path:
        raise ValueError("Batch creation intent belongs to a different Workflow Ledger.")
    if Path(str(intent.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path:
        raise ValueError("Batch creation intent belongs to a different RevisionLedger.")
    receipt_path = batch_dir / "batch_creation_receipt.json"
    if receipt_path.is_file():
        receipt = validate_batch_creation_receipt(batch_dir, intent)
        print(f"[BATCH CREATION ALREADY RECONCILED] {intent['batch_id']} | {receipt['status']}")
        return 0
    if workflow_path.is_symlink() or revision_path.is_symlink():
        raise ValueError("Batch creation reconciliation refuses symlinked Ledger paths.")
    if not workflow_path.is_file():
        raise ValueError(f"Batch creation Workflow Ledger is missing: {workflow_path}")
    workflow_sha = sha256_file(workflow_path)
    if workflow_sha != intent.get("workflow_ledger_sha256_at_prepare"):
        raise ValueError("Workflow Ledger changed before the batch-creation receipt was committed; manual reconciliation is required.")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    from co_reviewer.workflow_engine import validate_workflow_ledger
    workflow_errors = validate_workflow_ledger(workflow)
    if workflow_errors:
        raise ValueError("Batch creation Workflow Ledger is invalid: " + "; ".join(workflow_errors))
    if workflow.get("revision_round_id") != intent.get("revision_round_id"):
        raise ValueError("Batch creation Workflow Ledger belongs to a different review round.")
    active_pointers = load_active_document_pointers(workflow)
    for role, record in intent["documents"].items():
        pointer = active_pointers.get(role)
        parent_path = Path(record["parent_file"]).expanduser().resolve()
        if (
            pointer is None
            or pointer.get("file") != str(parent_path)
            or pointer.get("sha256") != record["parent_sha256"]
            or pointer.get("batch_id") != record.get("parent_batch_id")
            or not parent_path.is_file()
            or sha256_file(parent_path) != record["parent_sha256"]
        ):
            raise ValueError(f"Batch creation parent pointer/hash changed for document role {role}.")
    before_sha = intent.get("revision_ledger_sha256_before")
    after_sha = intent.get("revision_ledger_sha256_after")
    current_sha = sha256_file(revision_path) if revision_path.is_file() else None
    scope_path = batch_dir / "batch_scope.json"
    scope_exists = scope_path.is_file()
    if not scope_exists:
        if current_sha != before_sha:
            raise ValueError(
                "Intent-only batch has no scope and its RevisionLedger is not the exact pre-commit image; "
                "preserve it for manual investigation."
            )
        if revision_path.is_file():
            prior_ledger = _load_revision_ledger(revision_path)
            prior_ledger.validate_or_raise()
            if prior_ledger.revision_round_id != intent.get("revision_round_id"):
                raise ValueError("Pre-commit RevisionLedger belongs to a different review round.")
        allowed_names = {"batch_intent.json", "documents"}
        unexpected = [entry.name for entry in batch_dir.iterdir() if entry.name not in allowed_names]
        document_root = batch_dir / "documents"
        document_files = [path for path in document_root.rglob("*") if path.is_file()]
        if unexpected or document_files or document_root.is_symlink():
            raise ValueError(
                "Intent-only batch has execution artifacts or unexpected files; refusing automatic abandonment."
            )
        receipt = {
            "schema_version": "1.0",
            "status": "abandoned_before_ledger_commit",
            "batch_id": intent["batch_id"],
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "revision_ledger_file": str(revision_path),
            "revision_ledger_sha256_before": before_sha,
            "revision_ledger_sha256_after": after_sha,
            "batch_intent_sha256": sha256_file(batch_dir / "batch_intent.json"),
            "batch_scope_sha256": None,
            "revision_ledger_committed": False,
            "no_patch_records_committed": True,
            "active_parent_pointers_unchanged": True,
            "execution_artifacts_present": False,
            "retained_batch_files": True,
        }
        write_json_new(receipt_path, receipt)
        print(f"[BATCH CREATION RECONCILED] {intent['batch_id']} | abandoned_before_ledger_commit (intent-only)")
        print(f"[RECEIPT] {receipt_path}")
        return 0

    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read batch creation scope: {exc}") from exc
    if not isinstance(scope, Mapping):
        raise ValueError("Batch creation scope must be a JSON object.")
    intent_sha = sha256_file(batch_dir / "batch_intent.json")
    scope_sha = sha256_file(scope_path)
    patch_ids = scope.get("patch_ids")
    selected_task_ids = scope.get("selected_task_ids")
    batch_task_ids = scope.get("batch_task_ids")
    if (
        scope.get("batch_id") != intent.get("batch_id")
        or scope.get("batch_intent_sha256") != intent_sha
        or Path(str(scope.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path
        or scope.get("revision_round_id") != intent.get("revision_round_id")
        or not isinstance(patch_ids, list) or not patch_ids
        or any(not isinstance(value, str) or not value.strip() for value in patch_ids)
        or len(patch_ids) != len(set(patch_ids))
        or not isinstance(selected_task_ids, list)
        or selected_task_ids != intent.get("all_selected_task_ids", intent.get("selected_task_ids"))
        or not isinstance(batch_task_ids, list)
        or batch_task_ids != intent.get("selected_task_ids")
        or not isinstance(scope.get("documents"), Mapping)
        or set(scope["documents"]) != set(intent.get("documents", {}))
    ):
        raise ValueError("Batch creation scope does not match its immutable intent.")
    for role, record in intent["documents"].items():
        scope_record = scope["documents"].get(role)
        if (
            not isinstance(scope_record, Mapping)
            or Path(str(scope_record.get("input_file", ""))).expanduser().resolve()
                != Path(record["parent_file"]).expanduser().resolve()
            or scope_record.get("input_sha256") != record["parent_sha256"]
            or Path(str(scope_record.get("output_file", ""))).expanduser().resolve()
                != Path(record["output_file"]).expanduser().resolve()
        ):
            raise ValueError(f"Batch creation scope document path/hash differs for role {role}.")
    if current_sha == after_sha:
        ledger = _load_revision_ledger(revision_path)
        ledger.validate_or_raise()
        if ledger.revision_round_id != intent.get("revision_round_id"):
            raise ValueError("Committed batch-creation RevisionLedger belongs to a different review round.")
        if ledger.workflow_ledger_fingerprint != canonical_fingerprint(workflow):
            raise ValueError("Committed batch-creation RevisionLedger is not bound to the exact Workflow Ledger snapshot.")
        patch_by_id = {patch.patch_id: patch for patch in ledger.patches}
        missing_patch_ids = sorted(set(patch_ids) - set(patch_by_id))
        if missing_patch_ids:
            raise ValueError("Committed RevisionLedger is missing scoped Patch(es): " + ", ".join(missing_patch_ids))
        for patch_id in patch_ids:
            patch = patch_by_id[patch_id]
            if patch.status != "planned" or patch.task_id not in set(batch_task_ids):
                raise ValueError(f"Committed batch Patch {patch_id} is not a planned Patch in the declared task scope.")
            for operation in patch.operations:
                role_record = intent["documents"].get(operation.document)
                if (
                    not isinstance(role_record, Mapping)
                    or Path(operation.input_file).expanduser().resolve()
                        != Path(role_record["parent_file"]).expanduser().resolve()
                    or Path(operation.output_file).expanduser().resolve()
                        != Path(role_record["output_file"]).expanduser().resolve()
                    or operation.source_sha256 not in {None, role_record["parent_sha256"]}
                ):
                    raise ValueError(f"Committed Patch {patch_id} operation does not match its immutable batch paths/hashes.")
        allowed_names = {"batch_intent.json", "batch_scope.json", "documents"}
        unexpected = [entry.name for entry in batch_dir.iterdir() if entry.name not in allowed_names]
        document_root = batch_dir / "documents"
        document_files = [path for path in document_root.rglob("*") if path.is_file()]
        if unexpected or document_files or document_root.is_symlink():
            raise ValueError("Execution artifacts exist before the batch-creation receipt; refusing to seal it.")
        status = "committed"
    elif current_sha == before_sha:
        if revision_path.is_file():
            prior_ledger = _load_revision_ledger(revision_path)
            prior_ledger.validate_or_raise()
            existing_ids = {patch.patch_id for patch in prior_ledger.patches}
            overlap = sorted(set(patch_ids).intersection(existing_ids))
            if overlap:
                raise ValueError("Pre-commit RevisionLedger already contains scoped Patch IDs: " + ", ".join(overlap))
        allowed_names = {"batch_intent.json", "batch_scope.json", "documents"}
        unexpected = [entry.name for entry in batch_dir.iterdir() if entry.name not in allowed_names]
        document_root = batch_dir / "documents"
        document_files = [path for path in document_root.rglob("*") if path.is_file()]
        if unexpected or document_files or document_root.is_symlink():
            raise ValueError(
                "RevisionLedger is still at its pre-commit hash but execution artifacts exist; refusing automatic abandonment."
            )
        status = "abandoned_before_ledger_commit"
    else:
        raise ValueError(
            "RevisionLedger matches neither the immutable batch-creation before-image nor after-image; preserve both and investigate."
        )
    receipt = {
        "schema_version": "1.0",
        "status": status,
        "batch_id": intent["batch_id"],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256_before": before_sha,
        "revision_ledger_sha256_after": after_sha,
        "batch_intent_sha256": intent_sha,
        "batch_scope_sha256": scope_sha,
        "revision_ledger_committed": status == "committed",
        "no_patch_records_committed": status == "abandoned_before_ledger_commit",
        "active_parent_pointers_unchanged": True,
        "execution_artifacts_present": False,
        "retained_batch_files": True,
    }
    write_json_new(receipt_path, receipt)
    print(f"[BATCH CREATION RECONCILED] {intent['batch_id']} | {status}")
    print(f"[RECEIPT] {receipt_path}")
    return 0


def cmd_recover_failed_batch(args):
    """Recover an unpublished partial Apply without moving active document pointers."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    revision_path = Path(args.revision_ledger).expanduser().resolve()
    batch_dir = _resolve_batch_dir_argument(args.batch_dir)
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    from co_reviewer.batch_store import read_batch_intent, validate_batch_creation_receipt
    batch_intent = read_batch_intent(batch_dir)
    if batch_intent.get("batch_creation_protocol_version") == "1.0":
        creation_receipt_path = batch_dir / "batch_creation_receipt.json"
        if not creation_receipt_path.is_file():
            raise ValueError("Batch creation is not reconciled; run round reconciliation before failed-batch recovery.")
        creation_receipt = validate_batch_creation_receipt(batch_dir, batch_intent)
        if creation_receipt.get("status") != "committed":
            raise ValueError("A batch abandoned before its RevisionLedger commit cannot enter failed-Apply recovery.")
    workflow_sha_at_load = sha256_file(workflow_path)
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if sha256_file(workflow_path) != workflow_sha_at_load:
        raise ValueError("Workflow Ledger changed while loading failed-batch recovery state.")
    revision_sha_at_load = sha256_file(revision_path)
    ledger = _load_revision_ledger(revision_path)
    if sha256_file(revision_path) != revision_sha_at_load:
        raise ValueError("RevisionLedger changed while loading failed-batch recovery state.")
    from co_reviewer.batch_store import recover_unpublished_batch, write_json_new

    recovered_workflow, recovered_ledger, recovery_intent, workflow_changed, ledger_changed, already_recovered = recover_unpublished_batch(
        workflow,
        workflow_path,
        ledger,
        revision_path,
        batch_dir,
        actor=args.actor,
        reason=args.reason,
    )
    if already_recovered:
        print(f"[ALREADY RECOVERED] Batch {recovery_intent.get('batch_id')}")
        print(f"[RECEIPT] {batch_dir / 'batch_recovered.json'}")
        return 0

    if workflow_changed:
        current_raw_sha = sha256_file(workflow_path)
        current_payload_sha = _json_payload_sha256(
            json.loads(workflow_path.read_text(encoding="utf-8"))
        )
        before_raw_sha = recovery_intent["workflow_ledger_sha256_before"]
        before_payload_sha = recovery_intent["workflow_ledger_payload_sha256_before"]
        after_payload_sha = recovery_intent["workflow_ledger_sha256_after"]
        if current_raw_sha == before_raw_sha and current_payload_sha == before_payload_sha:
            if sha256_file(workflow_path) != before_raw_sha:
                raise ValueError("Workflow Ledger changed immediately before failed-batch recovery commit.")
            write_json_atomic(workflow_path, recovered_workflow)
        elif current_payload_sha != after_payload_sha:
            raise ValueError(
                "Workflow Ledger changed during recovery; active parent pointers were not changed."
            )
        if _json_payload_sha256(json.loads(workflow_path.read_text(encoding="utf-8"))) != after_payload_sha:
            raise ValueError("Workflow Ledger recovery write did not match its staged payload SHA-256.")

    if ledger_changed:
        current_raw_sha = sha256_file(revision_path)
        current_payload_sha = _json_payload_sha256(_load_revision_ledger(revision_path).to_dict())
        before_raw_sha = recovery_intent["revision_ledger_sha256_before"]
        before_payload_sha = recovery_intent["revision_ledger_payload_sha256_before"]
        after_payload_sha = recovery_intent["revision_ledger_sha256_after"]
        if current_raw_sha == before_raw_sha and current_payload_sha == before_payload_sha:
            if sha256_file(revision_path) != before_raw_sha:
                raise ValueError("RevisionLedger changed immediately before failed-batch recovery commit.")
            write_json_atomic(revision_path, recovered_ledger.to_dict())
        elif current_payload_sha != after_payload_sha:
            raise ValueError(
                "RevisionLedger changed during recovery; staged Patch lineage was not overwritten."
            )
        if _json_payload_sha256(_load_revision_ledger(revision_path).to_dict()) != after_payload_sha:
            raise ValueError("RevisionLedger recovery write did not match its staged payload SHA-256.")

    receipt_path = batch_dir / "batch_recovered.json"
    receipt = {
        "schema_version": "1.0",
        "status": "recovered",
        "recovery_id": recovery_intent["recovery_id"],
        "batch_id": recovery_intent["batch_id"],
        "recovered_at_utc": datetime.now(timezone.utc).isoformat(),
        "actor": recovery_intent["actor"],
        "reason": recovery_intent["reason"],
        "workflow_ledger_file": str(workflow_path),
        "workflow_ledger_sha256": recovery_intent["workflow_ledger_sha256_after"],
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256": recovery_intent["revision_ledger_sha256_after"],
        "batch_recovery_intent_sha256": sha256_file(batch_dir / "batch_recovery_intent.json"),
        "batch_intent_sha256": recovery_intent["batch_intent_sha256"],
        "batch_scope_sha256": recovery_intent["batch_scope_sha256"],
        "selected_task_ids": recovery_intent["selected_task_ids"],
        "invalidated_task_ids": recovery_intent["invalidated_task_ids"],
        "patch_ids": recovery_intent["patch_ids"],
        "parent_documents": recovery_intent["parent_documents"],
        "failed_output_files": recovery_intent["failed_output_files"],
        "retained_artifacts": recovery_intent["artifact_inventory"],
        "retained_batch_files": True,
    }
    write_json_new(receipt_path, receipt)
    print(f"[RECOVERED] Unpublished batch {receipt['batch_id']} was safely abandoned.")
    print("[PARENT] All active document pointers remain unchanged.")
    print("[WORKFLOW] Affected tasks and downstream dependents now require recheck and fresh author authorization.")
    print("[RETAINED] Partial DOCX outputs, plans and reports were not deleted or reused.")
    print("[LEDGER] A recovery event was recorded for every failed-batch Patch.")
    print("[NEXT] Re-authorize the affected task(s), then build a new batch from the active parent.")
    print(f"[RECEIPT] {receipt_path}")
    print(f"[OK] Workflow Ledger: {workflow_path}")
    print(f"[OK] RevisionLedger: {revision_path}")
    return 0




def cmd_retire_superseded_prepared_batch(args):
    """Retire an intent-only prepared batch after an exact successor was published."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    revision_path = Path(args.revision_ledger).expanduser().resolve()
    batch_dir = _resolve_batch_dir_argument(args.batch_dir)
    successor_batch_dir = _resolve_batch_dir_argument(args.successor_batch_dir)
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    ledger = _load_revision_ledger(revision_path)
    from co_reviewer.batch_store import retire_unscoped_prepared_batch

    receipt = retire_unscoped_prepared_batch(
        batch_dir,
        successor_batch_dir,
        workflow,
        workflow_path,
        ledger,
        revision_path,
        actor=args.actor,
        reason=args.reason,
    )
    print(f"[RETIRED] Intent-only prepared batch {receipt['batch_id']}")
    print(f"[SUCCESSOR] Published batch {receipt['successor_batch_id']}")
    print("[POINTERS] Workflow active document pointers were not changed.")
    print("[LEDGERS] Workflow Ledger and RevisionLedger were not modified.")
    print(f"[RECEIPT] {batch_dir / 'batch_superseded.json'}")
    return 0


def cmd_publish_batch(args):
    """Atomically publish all verified document outputs as the new active revision."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    revision_path = Path(args.revision_ledger).expanduser().resolve()
    batch_dir = _resolve_batch_dir_argument(args.batch_dir)
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be separate files.")
    from co_reviewer.batch_store import read_batch_intent, validate_batch_creation_receipt
    candidate_intent = read_batch_intent(batch_dir)
    if candidate_intent.get("batch_creation_protocol_version") == "1.0":
        if not (batch_dir / "batch_creation_receipt.json").is_file():
            raise ValueError("Batch creation is not reconciled; run round reconciliation before publication.")
        creation_receipt = validate_batch_creation_receipt(batch_dir, candidate_intent)
        if creation_receipt.get("status") != "committed":
            raise ValueError("A batch abandoned before its RevisionLedger commit cannot be published.")
    if (batch_dir / "batch_published.json").exists():
        from co_reviewer.batch_store import (
            _validate_committed_batch_publication,
            read_batch_intent,
        )
        existing_intent = read_batch_intent(batch_dir)
        if Path(str(existing_intent.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path:
            raise ValueError("Published batch belongs to a different Workflow Ledger.")
        if not workflow_path.is_file() or not revision_path.is_file():
            raise ValueError("Cannot verify existing publication without both current ledgers.")
        existing_workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        existing_ledger = _load_revision_ledger(revision_path)
        existing_receipt = json.loads((batch_dir / "batch_published.json").read_text(encoding="utf-8"))
        if Path(str(existing_receipt.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path:
            raise ValueError("Published batch receipt belongs to a different RevisionLedger.")
        _validate_committed_batch_publication(
            batch_dir,
            existing_intent,
            existing_workflow,
            {patch.patch_id: patch.status for patch in existing_ledger.patches},
        )
        print(f"[ALREADY PUBLISHED: VERIFIED] Batch {existing_receipt.get('batch_id', batch_dir.name)}")
        print("[RECEIPT] Intent, scope, Workflow events, output hashes and publication receipt are consistent.")
        return 0
    from co_reviewer.batch_store import (
        publish_active_pointers,
        read_batch_intent,
        write_json_new,
    )
    intent = read_batch_intent(batch_dir)
    scope_path = batch_dir / "batch_scope.json"
    if not scope_path.is_file():
        raise ValueError(f"Batch scope record is missing: {scope_path}")
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if not isinstance(scope, dict) or scope.get("batch_id") != intent["batch_id"]:
        raise ValueError("Batch scope does not match the selected batch intent.")
    if scope.get("batch_intent_sha256") != sha256_file(batch_dir / "batch_intent.json"):
        raise ValueError("Batch scope is not bound to the current immutable batch intent.")
    if set(scope.get("batch_task_ids", [])) != set(intent["selected_task_ids"]):
        raise ValueError("Batch scope document-changing task IDs differ from the immutable intent.")
    if set(scope.get("selected_task_ids", [])) != set(intent.get("all_selected_task_ids", intent["selected_task_ids"])):
        raise ValueError("Batch scope full task IDs differ from the immutable intent.")
    if Path(str(scope.get("revision_ledger_file", ""))).expanduser().resolve() != revision_path:
        raise ValueError("RevisionLedger path does not match the immutable batch scope.")
    batch_intent_sha256 = sha256_file(batch_dir / "batch_intent.json")
    batch_scope_sha256 = sha256_file(scope_path)
    revision_sha_before = sha256_file(revision_path)
    ledger = _load_revision_ledger(revision_path)
    if sha256_file(revision_path) != revision_sha_before:
        raise ValueError("RevisionLedger changed while preparing batch publication; reload it before publishing.")
    workflow_sha_before = sha256_file(workflow_path)
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if sha256_file(workflow_path) != workflow_sha_before:
        raise ValueError("Workflow Ledger changed while preparing batch publication; retry after reloading it.")
    expected_patch_ids = set(scope.get("patch_ids", []))
    if not expected_patch_ids:
        raise ValueError("Batch scope contains no Patch IDs.")
    workflow_tasks = {
        row.get("task_id"): row
        for row in workflow.get("tasks", [])
        if isinstance(row, dict)
    }
    batch_task_ids = intent["selected_task_ids"]
    incomplete_tasks = sorted(
        task_id for task_id in batch_task_ids
        if task_id not in workflow_tasks
        or workflow_tasks[task_id].get("status") not in {"verified", "completed"}
    )
    if incomplete_tasks:
        raise ValueError(
            "Synchronize verified Patch states to the Workflow Ledger before publishing. "
            "Task(s) not yet verified/completed: " + ", ".join(incomplete_tasks)
        )
    for role, record in scope.get("documents", {}).items():
        intent_record = intent["documents"].get(role)
        if (
            not isinstance(intent_record, dict)
            or Path(record.get("input_file", "")).expanduser().resolve()
            != Path(intent_record["parent_file"]).expanduser().resolve()
            or record.get("input_sha256") != intent_record["parent_sha256"]
            or Path(record.get("output_file", "")).expanduser().resolve()
            != Path(intent_record["output_file"]).expanduser().resolve()
        ):
            raise ValueError(f"Batch scope path/hash contract differs from its intent for role {role}.")
    patch_by_id = {patch.patch_id: patch for patch in ledger.patches}
    missing = sorted(expected_patch_ids - set(patch_by_id))
    if missing:
        raise ValueError("RevisionLedger is missing batch Patch(es): " + ", ".join(missing))
    unverified = sorted(
        patch_id for patch_id in expected_patch_ids
        if patch_by_id[patch_id].status != "verified"
    )
    if unverified:
        raise ValueError(
            "Batch publication requires every Patch to be mechanically verified and semantically confirmed: "
            + ", ".join(unverified)
        )
    # Final ResponseUnit completion and final Word/PDF locations are round-level
    # release gates. They are intentionally deferred until every batch is published,
    # because a later batch can shift pagination for an entire document role.
    if set(scope.get("documents", {})) != set(intent.get("documents", {})):
        raise ValueError("Batch scope document roles differ from the prepared batch intent.")
    updated, receipt = publish_active_pointers(
        workflow, workflow_path, batch_dir, ledger
    )
    # Validate and stage the receipt before replacing the sole mutable active pointer file.
    receipt["revision_ledger_file"] = str(revision_path)
    receipt["revision_ledger_sha256"] = revision_sha_before
    receipt["batch_intent_sha256"] = batch_intent_sha256
    receipt["batch_scope_sha256"] = batch_scope_sha256
    publish_intent_path = batch_dir / "batch_publish_intent.json"
    if publish_intent_path.exists():
        prior_intent = json.loads(publish_intent_path.read_text(encoding="utf-8"))
        prior_pointers = prior_intent.get("active_documents_to_publish")
        if (
            prior_intent.get("batch_id") != intent["batch_id"]
            or prior_intent.get("revision_ledger_sha256") != receipt["revision_ledger_sha256"]
            or prior_intent.get("batch_intent_sha256") != batch_intent_sha256
            or prior_intent.get("batch_scope_sha256") != batch_scope_sha256
            or not isinstance(prior_pointers, dict)
            or set(prior_pointers) != set(receipt["active_documents"])
            or any(
                prior_pointers[role].get("file") != value.get("file")
                or prior_pointers[role].get("sha256") != value.get("sha256")
                or prior_pointers[role].get("batch_id") != value.get("batch_id")
                for role, value in receipt["active_documents"].items()
            )
        ):
            raise ValueError("Existing batch publication intent does not match the verified outputs.")
        receipt["active_documents"] = prior_pointers
        updated.setdefault("active_documents", {}).update(prior_pointers)
    else:
        write_json_new(publish_intent_path, {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
            "active_documents_to_publish": receipt["active_documents"],
            "revision_ledger_sha256": receipt["revision_ledger_sha256"],
            "batch_intent_sha256": batch_intent_sha256,
            "batch_scope_sha256": batch_scope_sha256,
        })
    current_active = workflow.get("active_documents", {})
    pointers_already_committed = all(
        isinstance(current_active.get(role), dict)
        and current_active[role].get("file") == pointer.get("file")
        and current_active[role].get("sha256") == pointer.get("sha256")
        and current_active[role].get("batch_id") == pointer.get("batch_id")
        for role, pointer in receipt["active_documents"].items()
    )
    if sha256_file(revision_path) != revision_sha_before:
        raise ValueError(
            "RevisionLedger changed while publication was being prepared; the active pointer update was not committed."
        )
    if not pointers_already_committed:
        if sha256_file(workflow_path) != workflow_sha_before:
            raise ValueError(
                "Workflow Ledger changed during publication; the active pointer update was not committed. "
                "Reload workflow state and retry this same batch."
            )
        write_json_atomic(workflow_path, updated)
    write_json_new(batch_dir / "batch_published.json", receipt)
    print(f"[PUBLISHED] Batch {intent['batch_id']}")
    for role, pointer in receipt["active_documents"].items():
        print(f"[ACTIVE] {role}: {pointer['file']} | SHA-256 {pointer['sha256']}")
    print(f"[OK] Workflow Ledger: {workflow_path}")
    print("[IMMUTABLE] Prior batch outputs were retained; only active_documents pointers changed.")
    return 0


def cmd_rollback_batch(args):
    """Restore parent active pointers; never remove the published batch's files."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    batch_dir = _resolve_batch_dir_argument(args.batch_dir)
    if not (batch_dir / "batch_published.json").is_file():
        raise ValueError("Only a batch with a committed batch_published.json receipt can be rolled back.")
    from co_reviewer.batch_store import read_batch_intent, validate_batch_creation_receipt
    rollback_intent = read_batch_intent(batch_dir)
    if rollback_intent.get("batch_creation_protocol_version") == "1.0":
        creation_receipt_path = batch_dir / "batch_creation_receipt.json"
        if not creation_receipt_path.is_file():
            raise ValueError("Batch creation is not reconciled; run round reconciliation before rollback.")
        creation_receipt = validate_batch_creation_receipt(batch_dir, rollback_intent)
        if creation_receipt.get("status") != "committed":
            raise ValueError("A batch abandoned before its RevisionLedger commit cannot be rolled back.")
    receipt_path = batch_dir / "batch_rollback.json"
    if receipt_path.exists():
        from co_reviewer.batch_store import (
            _validate_committed_batch_rollback,
            read_batch_intent,
        )
        intent = read_batch_intent(batch_dir)
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        existing_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if Path(str(intent.get("workflow_ledger_file", ""))).expanduser().resolve() != workflow_path:
            raise ValueError("Rolled-back batch belongs to a different Workflow Ledger.")
        _validate_committed_batch_rollback(batch_dir, intent, workflow)
        print(f"[ALREADY ROLLED BACK: VERIFIED] Batch {existing_receipt.get('batch_id', batch_dir.name)}")
        print("[RECEIPT] Rollback intent, retained parent hashes and Workflow invalidation events are consistent.")
        return 0
    intent_path = batch_dir / "batch_intent.json"
    scope_path = batch_dir / "batch_scope.json"
    if not intent_path.is_file() or not scope_path.is_file():
        raise ValueError("Batch rollback requires its immutable intent and scope records.")
    intent = json.loads(intent_path.read_text(encoding="utf-8"))
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    if (
        intent.get("batch_id") != batch_dir.name.removeprefix("batch-")
        or scope.get("batch_id") != intent.get("batch_id")
        or scope.get("batch_intent_sha256") != sha256_file(intent_path)
    ):
        raise ValueError("Batch rollback intent/scope integrity check failed.")
    batch_intent_sha256 = sha256_file(intent_path)
    batch_scope_sha256 = sha256_file(scope_path)
    from co_reviewer.batch_store import rollback_active_pointers, write_json_new
    workflow_sha_before = sha256_file(workflow_path)
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if sha256_file(workflow_path) != workflow_sha_before:
        raise ValueError("Workflow Ledger changed while preparing rollback; reload it and retry.")
    updated, receipt = rollback_active_pointers(
        workflow, workflow_path, batch_dir, reason=args.reason
    )
    receipt["reason"] = args.reason
    receipt["batch_intent_sha256"] = batch_intent_sha256
    receipt["batch_scope_sha256"] = batch_scope_sha256
    rollback_intent_path = batch_dir / "batch_rollback_intent.json"
    if rollback_intent_path.exists():
        prior_intent = json.loads(rollback_intent_path.read_text(encoding="utf-8"))
        prior_pointers = prior_intent.get("restored_active_documents")
        if (
            prior_intent.get("batch_id") != receipt["batch_id"]
            or prior_intent.get("batch_intent_sha256") != batch_intent_sha256
            or prior_intent.get("batch_scope_sha256") != batch_scope_sha256
            or not isinstance(prior_pointers, dict)
            or set(prior_pointers) != set(receipt["restored_active_documents"])
            or any(
                prior_pointers[role].get("file") != value.get("file")
                or prior_pointers[role].get("sha256") != value.get("sha256")
                or prior_pointers[role].get("batch_id") != value.get("batch_id")
                for role, value in receipt["restored_active_documents"].items()
            )
        ):
            raise ValueError("Existing batch rollback intent differs from the requested parent pointers.")
        receipt["restored_active_documents"] = prior_pointers
        updated.setdefault("active_documents", {}).update(prior_pointers)
    else:
        write_json_new(rollback_intent_path, {
            "schema_version": "1.0",
            "batch_id": receipt["batch_id"],
            "batch_intent_sha256": batch_intent_sha256,
            "batch_scope_sha256": batch_scope_sha256,
            "reason": args.reason,
            "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
            "restored_active_documents": receipt["restored_active_documents"],
        })
    receipt["rollback_intent_sha256"] = sha256_file(rollback_intent_path)
    # Restore every document-role pointer in one atomic Workflow Ledger replace.
    current_active = workflow.get("active_documents", {})
    pointers_already_restored = all(
        isinstance(current_active.get(role), dict)
        and current_active[role].get("file") == pointer.get("file")
        and current_active[role].get("sha256") == pointer.get("sha256")
        and current_active[role].get("batch_id") == pointer.get("batch_id")
        for role, pointer in receipt["restored_active_documents"].items()
    )
    if not pointers_already_restored:
        if sha256_file(workflow_path) != workflow_sha_before:
            raise ValueError(
                "Workflow Ledger changed during rollback; no pointer changes were committed. "
                "Reload it and retry this same rollback."
            )
        write_json_atomic(workflow_path, updated)
    write_json_new(receipt_path, receipt)
    print(f"[ROLLED BACK] Batch {receipt['batch_id']}; active pointers restored to its parent versions.")
    print("[RETAINED] No DOCX, PDF, report or batch directory was deleted.")
    print(f"[OK] Workflow Ledger: {workflow_path}")
    return 0


def _archive_release_partial(path: Path, label: str) -> Path:
    """Preserve a mismatched generated artifact before regenerating its canonical file."""
    if path.is_symlink():
        raise ValueError(f"Refusing to move a Release Package symlink: {path}")
    archived = path.with_name(path.name + ".partial-" + uuid.uuid4().hex[:12])
    os.replace(path, archived)
    print(f"[PRESERVED PARTIAL] {label}: {archived}")
    return archived


def _write_release_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".release-tmp", dir=str(path.parent)
        )
        temporary = Path(raw_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _verify_completed_release_package(
    release_dir: Path,
    *,
    intent: Mapping[str, Any],
    receipt: Mapping[str, Any],
    workflow_path: Path,
    revision_path: Path,
) -> None:
    intent_path = release_dir / "release_package_intent.json"
    manifest_path = release_dir / "final_release_manifest.json"
    expected_inputs = {
        "workflow_ledger_file": str(workflow_path),
        "workflow_ledger_sha256": sha256_file(workflow_path),
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256": sha256_file(revision_path),
    }
    if (
        intent.get("schema_version") != "1.0"
        or receipt.get("schema_version") != "1.0"
        or receipt.get("status") != "complete"
        or receipt.get("release_id") != intent.get("release_id")
        or not isinstance(intent.get("release_id"), str)
        or not intent.get("release_id")
        or receipt.get("intent_sha256") != sha256_file(intent_path)
    ):
        raise ValueError("Existing Release Package intent/receipt identity is invalid.")
    for key, expected in expected_inputs.items():
        if intent.get(key) != expected or receipt.get(key) != expected:
            raise ValueError(
                f"Completed Release Package is bound to a different source snapshot ({key}); "
                "use a new release directory for the current Workflow/RevisionLedger."
            )
    for name in (
        "Response_to_Reviewers.md",
        "Response_to_Reviewers.docx",
        "Response_to_Reviewers.quality.json",
        "final_release_manifest.json",
    ):
        path = release_dir / name
        expected_sha = receipt.get("artifacts", {}).get(name)
        if not path.is_file() or not isinstance(expected_sha, str) or sha256_file(path) != expected_sha:
            raise ValueError(f"Completed Release Package artifact is missing or hash-stale: {path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        quality = json.loads((release_dir / "Response_to_Reviewers.quality.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot reload completed Release Package metadata: {exc}") from exc
    consistency_audit = manifest.get("deterministic_consistency_audit", {})
    if (
        manifest.get("release_status") != "READY"
        or consistency_audit.get("status") != "PASS"
        or consistency_audit.get("blocking_findings")
        or manifest.get("revision_round_id") != intent.get("revision_round_id")
        or manifest.get("source_ledgers", {}).get("workflow_ledger", {}).get("sha256")
            != expected_inputs["workflow_ledger_sha256"]
        or manifest.get("source_ledgers", {}).get("revision_ledger", {}).get("sha256")
            != expected_inputs["revision_ledger_sha256"]
        or quality.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}
    ):
        raise ValueError("Completed Release Package metadata does not match its committed intent.")
    from co_reviewer.release_manifest import _require_bound_response_quality_report
    _require_bound_response_quality_report(
        quality,
        docx_path=str(release_dir / "Response_to_Reviewers.docx"),
        docx_sha256=sha256_file(release_dir / "Response_to_Reviewers.docx"),
    )


def cmd_create_release_package(args):
    """Create or safely resume a hash-bound Release Package after every final gate passes."""
    workflow_path = Path(args.workflow_ledger).expanduser().resolve()
    revision_path = Path(args.revision_ledger).expanduser().resolve()
    if workflow_path == revision_path:
        raise ValueError("Workflow Ledger and RevisionLedger must be distinct files.")
    workflow_sha_snapshot = sha256_file(workflow_path)
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    if sha256_file(workflow_path) != workflow_sha_snapshot:
        raise ValueError("Workflow Ledger changed while loading the Release Package source snapshot.")
    revision_sha_snapshot = sha256_file(revision_path)
    ledger = _load_revision_ledger(revision_path)
    if sha256_file(revision_path) != revision_sha_snapshot:
        raise ValueError("RevisionLedger changed while loading the Release Package source snapshot.")

    from co_reviewer.workflow_engine import validate_workflow_ledger
    workflow_errors = validate_workflow_ledger(workflow)
    if workflow_errors:
        raise ValueError("Workflow Ledger is invalid:\n- " + "\n- ".join(workflow_errors))
    ledger.validate_or_raise()
    if workflow.get("revision_round_id") != ledger.revision_round_id:
        raise ValueError("Workflow Ledger and RevisionLedger belong to different review rounds.")
    pending_patches = [
        patch.patch_id for patch in ledger.patches
        if patch.status not in {"verified", "superseded"}
    ]
    if pending_patches:
        raise ValueError(
            "Final release is blocked by non-verified Patch(es): "
            + ", ".join(sorted(pending_patches))
        )
    pending_units = [
        unit.response_unit_id for unit in ledger.response_units
        if unit.status != "completed"
    ]
    if pending_units:
        raise ValueError(
            "Final release is blocked by incomplete ResponseUnit(s): "
            + ", ".join(sorted(pending_units))
        )
    if not workflow.get("active_documents") and ledger.documents:
        raise ValueError("Publish all verified batches before generating a final release package.")

    if args.out_dir:
        release_dir = Path(args.out_dir).expanduser()
        if not release_dir.is_absolute():
            release_dir = workflow_path.parent / release_dir
        raw_release_dir = release_dir
        for candidate in (raw_release_dir, *raw_release_dir.parents):
            if candidate.exists() and candidate.is_symlink():
                raise ValueError(f"Release output path contains a symbolic link: {candidate}")
        release_dir = raw_release_dir.resolve()
    else:
        release_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-" + uuid.uuid4().hex[:8]
        )
        release_dir = (workflow_path.parent / "runs" / f"release-{release_id}").resolve()

    if sha256_file(workflow_path) != workflow_sha_snapshot:
        raise ValueError("Workflow Ledger changed before Release Package intent binding; reload the round.")
    if sha256_file(revision_path) != revision_sha_snapshot:
        raise ValueError("RevisionLedger changed before Release Package intent binding; reload the round.")
    workflow_sha = workflow_sha_snapshot
    revision_sha = revision_sha_snapshot
    intent_path = release_dir / "release_package_intent.json"
    receipt_path = release_dir / "release_package_receipt.json"
    manifest_path = release_dir / "final_release_manifest.json"
    md_path = release_dir / "Response_to_Reviewers.md"
    docx_path = release_dir / "Response_to_Reviewers.docx"
    quality_path = release_dir / "Response_to_Reviewers.quality.json"

    for canonical_path in (intent_path, receipt_path, manifest_path, md_path, docx_path, quality_path):
        if canonical_path.is_symlink():
            raise ValueError(f"Release Package canonical artifact must not be a symbolic link: {canonical_path}")

    if release_dir.is_symlink():
        raise ValueError(f"Release output directory must not be a symbolic link: {release_dir}")
    if release_dir.exists() and not release_dir.is_dir():
        raise ValueError(f"Release output path is not a directory: {release_dir}")
    if not release_dir.exists():
        release_dir.mkdir(parents=True, exist_ok=False)

    binding = {
        "schema_version": "1.0",
        "release_id": uuid.uuid4().hex,
        "status": "building",
        "revision_round_id": ledger.revision_round_id,
        "workflow_ledger_file": str(workflow_path),
        "workflow_ledger_sha256": workflow_sha,
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256": revision_sha,
        "manuscript_title": args.manuscript_title,
        "author": args.author,
    }
    if intent_path.exists():
        if intent_path.is_symlink():
            raise ValueError(f"Release Package intent must not be a symlink: {intent_path}")
        try:
            intent = json.loads(intent_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Existing Release Package intent is unreadable: {exc}") from exc
        for key in (
            "schema_version", "revision_round_id", "workflow_ledger_file",
            "workflow_ledger_sha256", "revision_ledger_file",
            "revision_ledger_sha256", "manuscript_title", "author",
        ):
            if intent.get(key) != binding.get(key):
                raise ValueError(
                    f"Existing partial Release Package belongs to a different source snapshot or metadata ({key}); "
                    "preserve it and choose a new release directory."
                )
    else:
        existing_entries = list(release_dir.iterdir())
        known_crash_temporaries = (
            ".release_package_intent.json.",
            ".Response_to_Reviewers.md.",
            ".Response_to_Reviewers.quality.json.",
            ".Response_to_Reviewers.",
            ".final_release_manifest.json.",
            ".release_package_receipt.json.",
        )
        unexpected_entries = []
        for entry in existing_entries:
            if entry.is_symlink():
                raise ValueError(f"Release output directory contains a symlink; refusing to adopt it: {entry}")
            if entry.is_file() and any(entry.name.startswith(prefix) for prefix in known_crash_temporaries):
                _archive_release_partial(entry, "Orphaned release temporary")
            else:
                unexpected_entries.append(entry)
        if unexpected_entries:
            raise ValueError(
                "Release output directory contains files but has no release_package_intent.json. "
                "Unknown artifacts were preserved; choose a new directory: "
                + ", ".join(str(path) for path in unexpected_entries)
            )
        from co_reviewer.batch_store import write_json_new
        write_json_new(intent_path, binding)
        intent = json.loads(intent_path.read_text(encoding="utf-8"))

    intent_sha_for_build = sha256_file(intent_path)
    if receipt_path.exists():
        if receipt_path.is_symlink():
            raise ValueError(f"Release Package receipt must not be a symlink: {receipt_path}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if (
            intent.get("manuscript_title") != args.manuscript_title
            or intent.get("author") != args.author
            or intent.get("revision_round_id") != ledger.revision_round_id
        ):
            raise ValueError("Completed Release Package title/author/round differs from the requested release metadata.")
        _verify_completed_release_package(
            release_dir, intent=intent, receipt=receipt,
            workflow_path=workflow_path, revision_path=revision_path,
        )
        print(f"[ALREADY COMPLETE: VERIFIED] Release Package: {release_dir}")
        print(f"[FINAL RELEASE MANIFEST] {manifest_path}")
        print(f"[RELEASE RECEIPT] {receipt_path}")
        return 0

    response_plan = response_units_to_legacy_plan(ledger.response_units, ledger.patches)
    markdown = render_response_letter(response_plan)
    if md_path.exists():
        if md_path.is_symlink():
            raise ValueError(f"Response Letter Markdown must not be a symbolic link: {md_path}")
        try:
            current_markdown = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            current_markdown = None
        if current_markdown != markdown:
            _archive_release_partial(md_path, "Response Letter Markdown")
    if not md_path.exists():
        _write_release_text_atomic(md_path, markdown)

    try:
        from response_letter_docx import render_response_docx
    except ImportError as exc:
        raise ValueError(
            "Final release package requires python-docx; install project requirements."
        ) from exc
    for orphan_candidate in release_dir.glob(".Response_to_Reviewers.*.candidate.docx"):
        if orphan_candidate.is_symlink():
            raise ValueError(f"Release output contains a symlink candidate: {orphan_candidate}")
        if orphan_candidate.is_file():
            _archive_release_partial(orphan_candidate, "Orphaned Response Letter candidate")
    candidate_docx = release_dir / (".Response_to_Reviewers." + uuid.uuid4().hex + ".candidate.docx")
    render_response_docx(
        response_plan,
        candidate_docx,
        manuscript_title=args.manuscript_title,
        author=args.author,
    )
    from co_reviewer.release_manifest import _normalise_whitespace, _response_docx_body_text
    from docx import Document as _DocxDocument
    def _response_docx_header_metadata(path: Path) -> list[str]:
        paragraphs = [paragraph.text for paragraph in _DocxDocument(str(path)).paragraphs]
        summary_index = next(
            (index for index, value in enumerate(paragraphs) if value.startswith("Revision items: ")),
            None,
        )
        if summary_index is None or not paragraphs or paragraphs[0] != "Response to Reviewers":
            raise ValueError("Response Letter DOCX has a non-canonical title/metadata header.")
        return paragraphs[1:summary_index]
    if docx_path.exists():
        if docx_path.is_symlink():
            raise ValueError(f"Response Letter DOCX must not be a symbolic link: {docx_path}")
        existing_docx_matches = False
        try:
            existing_docx_matches = (
                _normalise_whitespace(_response_docx_body_text(docx_path))
                == _normalise_whitespace(_response_docx_body_text(candidate_docx))
                and _response_docx_header_metadata(docx_path)
                == _response_docx_header_metadata(candidate_docx)
            )
            existing_quality = audit_docx_quality(docx_path, profile="response-letter")
            existing_docx_matches = existing_docx_matches and existing_quality["status"] != "FAIL"
        except Exception:
            existing_docx_matches = False
        if not existing_docx_matches:
            _archive_release_partial(docx_path, "Response Letter DOCX")
    if docx_path.exists():
        candidate_docx.unlink(missing_ok=True)
    else:
        os.replace(candidate_docx, docx_path)

    response_quality = audit_docx_quality(docx_path, profile="response-letter")
    if response_quality["status"] == "FAIL":
        raise ValueError(
            "Generated Response Letter failed deterministic quality checks: "
            + json.dumps(response_quality["summary"], ensure_ascii=False)
        )
    if quality_path.exists():
        if quality_path.is_symlink():
            raise ValueError(f"Response Letter quality report must not be a symbolic link: {quality_path}")
        try:
            existing_quality = json.loads(quality_path.read_text(encoding="utf-8"))
            from co_reviewer.release_manifest import _require_bound_response_quality_report
            _require_bound_response_quality_report(
                existing_quality,
                docx_path=str(docx_path),
                docx_sha256=sha256_file(docx_path),
            )
            if existing_quality.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}:
                raise ValueError("Existing Response Letter quality report is not passing.")
            existing_quality_stable = dict(existing_quality)
            current_quality_stable = dict(response_quality)
            # The quality report carries a new generated_at_utc on each audit.
            # Compare the deterministic metrics and checks, not that timestamp.
            existing_quality_stable.pop("generated_at_utc", None)
            current_quality_stable.pop("generated_at_utc", None)
            if existing_quality_stable != current_quality_stable:
                raise ValueError("Existing Response Letter quality report differs from the current deterministic audit.")
            response_quality = existing_quality
        except Exception:
            _archive_release_partial(quality_path, "Response Letter quality report")
    if not quality_path.exists():
        from co_reviewer.batch_store import write_json_new
        write_json_new(quality_path, response_quality)
    if response_quality["status"] == "FAIL":
        raise ValueError(
            "Generated Response Letter failed deterministic quality checks: "
            + json.dumps(response_quality["summary"], ensure_ascii=False)
        )

    from co_reviewer.release_manifest import build_final_release_manifest
    manifest = build_final_release_manifest(
        ledger,
        workflow,
        revision_ledger_path=revision_path,
        workflow_ledger_path=workflow_path,
        response_letter_md=md_path,
        response_letter_docx=docx_path,
        response_letter_quality=quality_path,
    )
    consistency_audit = manifest.get("deterministic_consistency_audit", {})
    if (
        manifest.get("release_status") != "READY"
        or consistency_audit.get("status") != "PASS"
        or consistency_audit.get("blocking_findings")
    ):
        raise ValueError("Release Manifest is not READY with a passing deterministic consistency audit; completion receipt will not be written.")
    if manifest_path.exists():
        if manifest_path.is_symlink():
            raise ValueError(f"Final Release Manifest must not be a symbolic link: {manifest_path}")
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_manifest = dict(manifest)
            existing_stable = dict(existing_manifest)
            expected_manifest.pop("generated_at_utc", None)
            existing_stable.pop("generated_at_utc", None)
            manifest_matches = existing_stable == expected_manifest
        except (OSError, json.JSONDecodeError, TypeError):
            manifest_matches = False
        if not manifest_matches:
            _archive_release_partial(manifest_path, "Final Release Manifest")
    if not manifest_path.exists():
        from co_reviewer.batch_store import write_json_new
        write_json_new(manifest_path, manifest)

    if sha256_file(intent_path) != intent_sha_for_build:
        raise ValueError("Release Package intent changed during finalization; no completion receipt will be written.")
    if manifest_path.is_symlink() or md_path.is_symlink() or docx_path.is_symlink() or quality_path.is_symlink():
        raise ValueError("Release package canonical artifact path must not be a symbolic link.")
    artifacts = {
        "Response_to_Reviewers.md": sha256_file(md_path),
        "Response_to_Reviewers.docx": sha256_file(docx_path),
        "Response_to_Reviewers.quality.json": sha256_file(quality_path),
        "final_release_manifest.json": sha256_file(manifest_path),
    }
    receipt = {
        "schema_version": "1.0",
        "status": "complete",
        "release_id": intent["release_id"],
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_ledger_file": str(workflow_path),
        "workflow_ledger_sha256": workflow_sha,
        "revision_ledger_file": str(revision_path),
        "revision_ledger_sha256": revision_sha,
        "intent_file": str(intent_path),
        "intent_sha256": sha256_file(intent_path),
        "artifacts": artifacts,
    }
    if receipt_path.exists():
        raise ValueError("Release completion receipt appeared during finalization; inspect the concurrent run.")
    # The receipt is the commit marker. Recheck both mutable Ledger sources immediately
    # before writing it so a concurrent Workflow sync cannot make a stale Manifest
    # appear to be the final Release Package.
    if sha256_file(workflow_path) != workflow_sha_snapshot:
        raise ValueError(
            "Workflow Ledger changed during Release Package generation. No completion receipt was written; "
            "preserve this partial package and use a new release directory for the new Ledger snapshot."
        )
    if sha256_file(revision_path) != revision_sha_snapshot:
        raise ValueError(
            "RevisionLedger changed during Release Package generation. No completion receipt was written; "
            "preserve this partial package and use a new release directory for the new Ledger snapshot."
        )
    from co_reviewer.batch_store import write_json_new
    write_json_new(receipt_path, receipt)

    print(f"[RELEASE READY] {ledger.revision_round_id}")
    print(f"[RELEASE PACKAGE] {release_dir}")
    print(f"[RESPONSE MARKDOWN] {md_path}")
    print(f"[RESPONSE DOCX] {docx_path}")
    print(f"[QUALITY REPORT] {quality_path} | {response_quality['status']}")
    print(f"[FINAL RELEASE MANIFEST] {manifest_path}")
    print(f"[RELEASE RECEIPT] {receipt_path}")
    print(f"[MANIFEST SHA-256] {artifacts['final_release_manifest.json']}")
    print("[GATE] Active pointers, verified PatchOperations, completed ResponseUnits, final Word/PDF hashes, SEQ audit and exact Response Letter excerpts were rechecked.")
    return 0


def cmd_response(args):
    if getattr(args, "revision_ledger", None):
        ledger = _load_revision_ledger(args.revision_ledger)
        if not ledger.response_units:
            raise ValueError("RevisionLedger does not contain ResponseUnit records.")
        plan = response_units_to_legacy_plan(ledger.response_units, ledger.patches)
    else:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        errors = validate_plan(plan, require_approval=False)
        if errors:
            raise ValueError("Cannot render response letter from invalid plan:\n- " + "\n- ".join(errors))
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_response_letter(plan), encoding="utf-8")
    verified = sum(1 for item in plan["revision_items"] if item.get("status") == "verified")
    print(f"[OK] Response letter draft: {output}")
    print(f"[SUMMARY] {len(plan['revision_items'])} item(s); {verified} marked verified. Review all draft labels before submission.")
    return 0

def cmd_response_docx(args):
    if getattr(args, "revision_ledger", None):
        ledger = _load_revision_ledger(args.revision_ledger)
        if not ledger.response_units:
            raise ValueError("RevisionLedger does not contain ResponseUnit records.")
        plan = response_units_to_legacy_plan(ledger.response_units, ledger.patches)
    else:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        errors = validate_plan(plan, require_approval=False)
        if errors:
            raise ValueError("Cannot render response letter from invalid plan:\n- " + "\n- ".join(errors))
    try:
        from response_letter_docx import render_response_docx
    except ImportError as exc:
        raise ValueError("DOCX response output requires python-docx; install project requirements.") from exc
    output_path = Path(args.out).expanduser().resolve()
    quality_report_value = getattr(args, "quality_report", None)
    quality_path = (
        Path(quality_report_value).expanduser().resolve()
        if quality_report_value
        else output_path.with_suffix(".quality.json")
    )
    source_path = Path(args.revision_ledger or args.plan).expanduser().resolve()
    if quality_path in {output_path, source_path}:
        raise ValueError("DOCX quality report must be distinct from the Response Letter and its input plan/ledger.")
    if quality_path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing DOCX quality report: {quality_path}")
    output = render_response_docx(
        plan, args.out, manuscript_title=args.manuscript_title, author=args.author
    )
    quality = audit_docx_quality(output, profile="response-letter")
    write_json_atomic(quality_path, quality)
    if quality["status"] == "FAIL":
        raise ValueError(f"Generated Response Letter failed deterministic DOCX quality checks: {quality['summary']}")
    verified = sum(1 for item in plan["revision_items"] if item.get("status") == "verified")
    print(f"[OK] Response Letter DOCX: {output}")
    print(f"[DOCX QUALITY] {quality['status']}; report: {quality_path}")
    print(f"[SUMMARY] {len(plan['revision_items'])} item(s); {verified} marked verified. Draft labels are retained for all unfinished items.")
    return 0

def cmd_validate_edit_contract(args):
    original = Path(args.original).read_text(encoding="utf-8")
    proposed = Path(args.proposed).read_text(encoding="utf-8")
    authorization = json.loads(Path(args.authorization).read_text(encoding="utf-8"))
    report = narrow_revision(original, proposed, authorized_change=authorization)
    if args.out:
        write_json_atomic(Path(args.out), report)
        print(f"[CONTRACT REPORT] {Path(args.out).resolve()}")
    print(json.dumps({k: v for k, v in report.items() if k != "edits"}, ensure_ascii=False, indent=2))
    print(f"[MINIMAL DIFF] {len(report['edits'])} edit block(s); status={report['status']}")
    return 0 if report["status"] == "PASS" else 2

def cmd_revision_baseline(args):
    report = build_revision_baseline_inventory(args.docx)
    if args.out:
        write_json_atomic(Path(args.out), report)
        print(f"[REVISION BASELINE] {Path(args.out).resolve()}")
    print(f"[REVISION BASELINE] {report['existing_revision_count']} historical revision/comment structure(s)")
    print(f"[PACKAGE STATUS] {report['package_status']}")
    return 0 if report["package_status"] == "READY" else 2

def cmd_targeted_revision_gate(args):
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    target_data = json.loads(Path(args.targets).read_text(encoding="utf-8"))
    targets = target_data.get("targets") if isinstance(target_data, dict) else target_data
    if not isinstance(targets, list):
        raise ValueError("Target file must be an array or an object with a targets array")
    selected = json.loads(Path(args.selective_ids).read_text(encoding="utf-8")) if args.selective_ids else []
    report = targeted_revision_gate(inventory, targets, resolution_policy=args.policy,
                                    selectively_resolved_ids=selected)
    if args.out:
        write_json_atomic(Path(args.out), report)
        print(f"[REVISION GATE] {Path(args.out).resolve()}")
    print(f"[REVISION GATE] {report['status']}; overlaps={len(report['overlapping_revisions'])}; "
          f"unresolved={len(report['unresolved_conflicts'])}")
    if report["status"] == "RESOLUTION_ADAPTER_REQUIRED":
        print("[BLOCKED] Policy recorded, but no deterministic revision-resolution adapter is enabled; resolve the conflict in Word and re-import.")
    return 0 if report["status"] == "PASS" else 2

def cmd_build_package_reference_graph(args):
    documents = {}
    for item in args.document:
        if "=" not in item:
            raise ValueError("--document must use ROLE=PATH")
        role, filename = item.split("=", 1)
        if not role or role in documents:
            raise ValueError("Document roles must be non-empty and unique")
        documents[role] = extract_docx_reference_segments(filename)
    report = build_package_reference_graph(documents)
    if args.out:
        write_json_atomic(Path(args.out), report)
        print(f"[REFERENCE GRAPH] {Path(args.out).resolve()}")
    print(f"[REFERENCE GRAPH] {len(report['definitions'])} definition(s), "
          f"{len(report['mentions'])} mention(s), {report['unresolved_count']} unresolved/ambiguous")
    return 0

def cmd_renumber_package_graph(args):
    graph = json.loads(Path(args.graph).read_text(encoding="utf-8"))
    mapping = json.loads(Path(args.number_map).read_text(encoding="utf-8"))
    if not isinstance(mapping, dict):
        raise ValueError("Number map must be a JSON object keyed as DOCUMENT::OLD_NUMBER")
    confirmed = json.loads(Path(args.confirm_ambiguous_map).read_text(encoding="utf-8")) if args.confirm_ambiguous_map else {}
    if not isinstance(confirmed, dict):
        raise ValueError("Confirmed ambiguous map must be a JSON object mapping mention IDs to definition IDs")
    updated = renumber_reference_graph(
        graph, mapping, explicit_authorization=args.authorized,
        confirmed_ambiguous_mappings=confirmed,
    )
    if args.out:
        write_json_atomic(Path(args.out), updated)
        print(f"[REFERENCE GRAPH UPDATED] {Path(args.out).resolve()}")
    print(f"[REFERENCE UPDATES] definitions={len(updated['definition_updates'])}; mentions={len(updated['mention_updates'])}")
    return 0

def cmd_compile_clean_from_marked(args):
    marked = Path(args.marked).expanduser().resolve()
    output = Path(args.out).expanduser().resolve()
    if not marked.is_file():
        raise ValueError(f"Marked DOCX does not exist: {marked}")
    if marked == output or output.exists():
        raise ValueError("Clean output must be a new path distinct from Marked.")
    marked_bytes = marked.read_bytes()
    expected_revision_ids = []
    if args.revision_ledger:
        ledger_data = json.loads(Path(args.revision_ledger).read_text(encoding="utf-8"))
        ledger = revision_ledger_from_mapping(ledger_data)
        records = [record for patch in ledger.patches for record in patch.applied_revision_ids]
        lineage = collect_revision_ids_from_lineage(
            records, marked, hashlib.sha256(marked_bytes).hexdigest()
        )
        expected_revision_ids = lineage["revision_ids"]
    elif args.apply_report:
        raw_reports = json.loads(Path(args.apply_report).read_text(encoding="utf-8"))
        reports = raw_reports if isinstance(raw_reports, list) else [raw_reports]
        records = []
        for report in reports:
            if not isinstance(report, dict):
                raise ValueError("--apply-report entries must be objects.")
            revision_ids = sorted({
                str(result[key])
                for result in report.get("results", [])
                for key in ("delete_revision_id", "insert_revision_id")
                if result.get(key) is not None
            })
            records.append({
                "input_file": report.get("input_file"),
                "input_sha256": report.get("input_sha256"),
                "output_file": report.get("output_file"),
                "output_sha256": report.get("output_sha256"),
                "revision_ids": revision_ids,
            })
        lineage = collect_revision_ids_from_lineage(
            records, marked, hashlib.sha256(marked_bytes).hexdigest()
        )
        expected_revision_ids = lineage["revision_ids"]
    elif args.revision_ids:
        ids = json.loads(Path(args.revision_ids).read_text(encoding="utf-8"))
        if not isinstance(ids, list) or any(not isinstance(value, (str, int)) for value in ids):
            raise ValueError("--revision-ids must be a JSON array of revision IDs.")
        expected_revision_ids = [str(value) for value in ids]
    result = compile_clean_from_marked(marked_bytes, expected_revision_ids=expected_revision_ids)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as stream:
        stream.write(result.pop("clean_bytes"))
    report_path = Path(args.report).expanduser().resolve() if args.report else output.with_suffix(".compile.json")
    write_json_atomic(report_path, result)
    print(f"[CLEAN COMPILED] {output}")
    print(f"[MARKED SHA256] {result['marked_sha256']}")
    print(f"[CLEAN SHA256] {result['clean_sha256']}")
    print(f"[COMPILE REPORT] {report_path}")
    return 0

def cmd_commit_document_package(args):
    outputs = {}
    for item in args.document:
        if "=" not in item:
            raise ValueError("--document must use PACKAGE_PATH=DOCX_PATH")
        name, filename = item.split("=", 1)
        if name in outputs:
            raise ValueError("Duplicate package output path: " + name)
        outputs[name] = Path(filename).read_bytes()
    result = commit_document_package(outputs, args.release_root,
                                     metadata={"source": "Co_Reviewer CLI"})
    print(f"[PACKAGE COMMITTED] {result['package_id']}")
    print(f"[PACKAGE DIRECTORY] {result['directory']}")
    print(f"[COMMIT MARKER] {result['commit_marker']}")
    return 0

def build_parser():
    p = argparse.ArgumentParser(description="Co_Reviewer safe Word revision toolkit.")
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("inspect", help="Inspect DOCX files and create compact reports.")
    i.add_argument("--rev"); i.add_argument("--man"); i.add_argument("--si")
    i.add_argument("--comment-author", default=None)
    i.add_argument("--out", default="workspace_digest.md")
    i.add_argument("--json-out", default="workspace_inventory.json")
    i.add_argument("--max-items-per-section", type=int, default=50)
    i.add_argument("--cache-dir", default=".co-reviewer-cache")
    i.add_argument("--usage-ledger", help="Optional JSONL metadata ledger for DOCX parse/cache observations; no document text is stored.")
    i.add_argument("--refresh-cache", action="store_true", help="Rebuild inventories even when source hashes match.")
    i.set_defaults(func=cmd_inspect)
    si = sub.add_parser("inspect-docx-structure", help="Read-only inventory of OPC parts, relationships and OOXML structures.")
    si.add_argument("--docx", required=True)
    si.add_argument("--out", help="Optional JSON report path. Omit to print the report to stdout.")
    si.set_defaults(func=cmd_inspect_docx_structure)
    rb = sub.add_parser("revision-baseline", help="Zero-model-call inventory of historical revisions and comment structures.")
    rb.add_argument("--docx", required=True)
    rb.add_argument("--out")
    rb.set_defaults(func=cmd_revision_baseline)
    rgate = sub.add_parser("targeted-revision-gate", help="Check whether historical revisions overlap proposed edit targets.")
    rgate.add_argument("--inventory", required=True)
    rgate.add_argument("--targets", required=True)
    rgate.add_argument("--policy", choices=["ACCEPT_EXISTING", "REJECT_EXISTING", "SELECTIVE_ACCEPT_CONFLICTS"])
    rgate.add_argument("--selective-ids")
    rgate.add_argument("--out")
    rgate.set_defaults(func=cmd_targeted_revision_gate)
    vc = sub.add_parser("validate-edit-contract", help="Validate Authorized Change, protected spans and deterministic immutable token locks.")
    vc.add_argument("--original", required=True)
    vc.add_argument("--proposed", required=True)
    vc.add_argument("--authorization", required=True, help="JSON file containing intent and explicitly authorized tokens.")
    vc.add_argument("--out")
    vc.set_defaults(func=cmd_validate_edit_contract)
    rg = sub.add_parser("build-package-reference-graph", help="Build a directed cross-document definition/mention graph from extracted text.")
    rg.add_argument("--document", nargs="+", required=True, help="ROLE=DOCX_PATH entries for every managed DOCX file.")
    rg.add_argument("--out")
    rg.set_defaults(func=cmd_build_package_reference_graph)
    rn = sub.add_parser("renumber-package-graph", help="Generate authorized, resolved definition/mention renumbering updates.")
    rn.add_argument("--graph", required=True)
    rn.add_argument("--number-map", required=True)
    rn.add_argument("--authorized", action="store_true", help="Explicit package-wide authorization supplied by the user.")
    rn.add_argument("--confirm-ambiguous-map", help="JSON mapping of ambiguous mention IDs to user-confirmed definition IDs.")
    rn.add_argument("--out", required=True)
    rn.set_defaults(func=cmd_renumber_package_graph)
    cc = sub.add_parser("compile-clean-from-marked", help="Create a deterministic accepted-changes Clean derivative from Marked.")
    cc.add_argument("--marked", required=True)
    cc.add_argument("--out", required=True)
    cc.add_argument("--report")
    cc_group = cc.add_mutually_exclusive_group()
    cc_group.add_argument("--revision-ledger", help="RevisionLedger JSON; resolve exact applied revision-ID lineage by DOCX path and SHA-256.")
    cc_group.add_argument("--apply-report", help="Apply report JSON chain whose input/output SHA-256 values bind this Marked DOCX.")
    cc_group.add_argument("--revision-ids", help="JSON array of exact current revision IDs; use only when independently verified.")
    cc.set_defaults(func=cmd_compile_clean_from_marked)
    cp = sub.add_parser("commit-document-package", help="Validate and publish a multi-DOCX immutable package candidate.")
    cp.add_argument("--release-root", required=True)
    cp.add_argument("--document", nargs="+", required=True, help="PACKAGE_PATH=DOCX_PATH entries.")
    cp.set_defaults(func=cmd_commit_document_package)
    au = sub.add_parser("audit", help="Inspect once, reuse hash-bound caches, match comments and audit figure/table references.")
    au.add_argument("--rev"); au.add_argument("--man"); au.add_argument("--si")
    au.add_argument("--comment-author", default=None)
    au.add_argument("--out", default="workspace_digest.md")
    au.add_argument("--json-out", default="workspace_inventory.json")
    au.add_argument("--audit-md-out", default="review_audit_report.md")
    au.add_argument("--audit-out", default="review_audit_report.json")
    au.add_argument("--decision-queue-out", default="review_decision_queue.json")
    au.add_argument("--comment-ids", nargs="+", default=None, help="Build the model evidence queue only for these Word comment IDs; the audit report remains complete.")
    au.add_argument("--cache-dir", default=".co-reviewer-cache")
    au.add_argument("--usage-ledger", help="Optional JSONL metadata ledger for DOCX parse/cache observations; no document text is stored.")
    au.add_argument("--refresh-cache", action="store_true", help="Rebuild inventories even when source hashes match.")
    au.add_argument("--active-workflow-ledger", help="Use only for published Marked DOCX versions; verifies every input path/SHA against the Workflow Ledger's current active pointers before allowing native text insertion/deletion markup.")
    au.add_argument("--max-items-per-section", type=int, default=50)
    au.set_defaults(func=cmd_audit)
    v = sub.add_parser("validate-plan", help="Validate a plan without editing files.")
    v.add_argument("--plan", required=True); v.add_argument("--allow-unapproved", action="store_true")
    v.set_defaults(func=cmd_validate)
    pf = sub.add_parser("preflight-plan", help="Check revision targets and bind approval to source DOCX versions without editing files.")
    pf.add_argument("--plan", required=True)
    pf.add_argument("--out", help="Preflight report output path; defaults inside the plan's batch directory when applicable.")
    pf.add_argument("--author", default="Co_Reviewer Preflight")
    pf.add_argument("--revision-ledger", help="Synchronize preflight evidence/events into a v0.5.1 RevisionLedger.")
    pf.set_defaults(func=cmd_preflight)
    ap = sub.add_parser("approve-plan", help="Record explicit user approval for selected revision items.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--ids", nargs="+", required=True, help="Revision item IDs explicitly approved by the user.")
    ap.add_argument("--revision-ledger", help="Synchronize explicit approval evidence/events into a v0.5.1 RevisionLedger.")
    ap.add_argument("--actor", default="Human Author", help="Recorded approver label.")
    ap.set_defaults(func=cmd_approve_plan)
    a = sub.add_parser("apply", help="Apply an approved plan to separate DOCX output files.")
    a.add_argument("--plan", required=True); a.add_argument("--author", default="Co_Reviewer")
    a.add_argument("--report", help="Apply report path; defaults inside the plan's batch directory when applicable.")
    a.add_argument("--revision-ledger", help="Synchronize Patch state/events into a v0.5.1 RevisionLedger after apply.")
    a.set_defaults(func=cmd_apply)
    vfy = sub.add_parser("verify", help="Verify actual Word Track Changes against the approved plan.")
    vfy.add_argument("--plan", required=True)
    vfy.add_argument("--apply-report", help="Apply report path; defaults inside the plan's batch directory when applicable.")
    vfy.add_argument("--out", help="Verification report path; defaults inside the plan's batch directory when applicable.")
    vfy.set_defaults(func=cmd_verify)
    release = sub.add_parser("create-release-package", help="Build final Response Letter files and hash-bound release manifest after all final gates pass.")
    release.add_argument("--workflow-ledger", required=True)
    release.add_argument("--revision-ledger", required=True)
    release.add_argument("--out-dir", help="New release output directory; defaults to a unique runs/release-{id} directory.")
    release.add_argument("--manuscript-title", default="")
    release.add_argument("--author", default="")
    release.set_defaults(func=cmd_create_release_package)
    rsp = sub.add_parser("render-response", help="Render a conservative Response Letter from revision items or ResponseUnits.")
    rsp_inputs = rsp.add_mutually_exclusive_group(required=True)
    rsp_inputs.add_argument("--plan", help="Legacy revision plan.")
    rsp_inputs.add_argument("--revision-ledger", help="Render ResponseUnits from a v0.5.1 RevisionLedger.")
    rsp.add_argument("--out", default="Response_to_Reviewers.md")
    rsp.set_defaults(func=cmd_response)
    rspdocx = sub.add_parser("render-response-docx", help="Render a formatted Word Response Letter from revision items or ResponseUnits.")
    rspdocx_inputs = rspdocx.add_mutually_exclusive_group(required=True)
    rspdocx_inputs.add_argument("--plan", help="Legacy revision plan.")
    rspdocx_inputs.add_argument("--revision-ledger", help="Render ResponseUnits from a v0.5.1 RevisionLedger.")
    rspdocx.add_argument("--out", default="Response_to_Reviewers.docx")
    rspdocx.add_argument("--manuscript-title", default="")
    rspdocx.add_argument("--author", default="")
    rspdocx.add_argument("--quality-report", help="Optional path for the automatic DOCX quality report.")
    rspdocx.set_defaults(func=cmd_response_docx)
    cv = sub.add_parser("confirm-verified", help="Confirm selected items after exact DOCX verification and semantic review.")
    cv.add_argument("--plan", required=True)
    cv.add_argument("--apply-report", help="Apply report path; defaults inside the plan's batch directory when applicable.")
    cv.add_argument("--revision-ledger", help="Synchronize verified Patch state/events into the RevisionLedger.")
    cv.add_argument("--ids", nargs="+", required=True, help="Applied revision item IDs to confirm.")
    cv.add_argument("--semantic-review-confirmed", action="store_true", required=True,
                    help="Required acknowledgement that scientific meaning and cross-document consistency were reviewed.")
    cv.set_defaults(func=cmd_confirm_verified)
    syncwl = sub.add_parser("sync-workflow-ledger", help="Synchronize verified Patch lifecycle evidence back to the dependency graph.")
    syncwl.add_argument("--workflow-ledger", required=True)
    syncwl.add_argument("--revision-ledger", required=True)
    syncwl.set_defaults(func=cmd_sync_workflow_ledger)
    rws = sub.add_parser("recover-workflow-sync", help="Finish an interrupted two-ledger Workflow synchronization from its hash-bound staged after-images.")
    rws.add_argument("--workflow-ledger", required=True)
    rws.add_argument("--revision-ledger", required=True)
    rws.set_defaults(func=cmd_recover_workflow_sync)
    rbc = sub.add_parser("reconcile-batch-creation", help="Reconcile the commit receipt for an interrupted immutable batch creation.")
    rbc.add_argument("--workflow-ledger", required=True)
    rbc.add_argument("--revision-ledger", required=True)
    rbc.add_argument("--batch-dir", required=True)
    rbc.set_defaults(func=cmd_reconcile_batch_creation)
    prep_scope = sub.add_parser("prepare-workflow-execution", help="Validate exact evidence-linked PatchOperations and prepare a new pending Workflow Ledger without authorizing edits.")
    prep_scope.add_argument("--workflow-ledger", required=True)
    prep_scope.add_argument("--execution-scope", required=True, help="JSON scope containing exact PatchOperations and reviewed ResponseUnit dispositions.")
    prep_scope.add_argument("--inventory", required=True, help="The same source-hash-bound workspace inventory used for the Decision Plan.")
    prep_scope.add_argument("--out", required=True, help="New output path for the prepared Workflow Ledger; the input ledger is preserved.")
    prep_scope.add_argument("--reviewer-map-out", help="New reviewer-map JSON path; defaults beside --out.")
    prep_scope.add_argument("--report", help="New preparation report path; defaults beside --out.")
    prep_scope.add_argument(
        "--round-manifest",
        help="Optional round_manifest.json or refresh_manifest.json; binds the Workflow Ledger and inventory to the exact round/source snapshot.",
    )
    prep_scope.add_argument(
        "--round-root-manifest",
        help="Root round_manifest.json to update with the newly prepared Workflow Ledger/reviewer-map/report paths and hashes; required with a dedicated refresh_manifest.json.",
    )
    prep_scope.set_defaults(func=cmd_prepare_workflow_execution)
    awt = sub.add_parser("approve-workflow-tasks", help="Explicitly authorize planned tasks to enter the ready queue.")
    awt.add_argument("--workflow-ledger", required=True)
    awt.add_argument("--ids", nargs="+", required=True)
    awt.add_argument("--actor", default="Human Author")
    awt.add_argument("--plan-approved", action="store_true", required=True)
    awt.set_defaults(func=cmd_authorize_workflow_tasks)
    bl = sub.add_parser("build-revision-ledger", help="Build typed Patches from the current conflict-free ready batch and merge them into the round ledger.")
    bl.add_argument("--workflow-ledger", required=True)
    bl.add_argument("--task-ids", nargs="+", help="Optional subset of the current conflict-free ready batch.")
    bl.add_argument("--document-paths", help="Optional JSON map of document role to input_file/output_file.")
    bl.add_argument("--reviewer-comments", help="Optional JSON map of comment ID to exact reviewer comment text.")
    bl.add_argument("--reviewer-map", help="Optional JSON map of task ID to reviewer label; nested comment_ids maps preserve multiple reviewers within one task.")
    bl.add_argument("--out", default="revision_ledger.json")
    bl.add_argument("--runs-dir", help="Append-only batch root; defaults to runs/ beside the Workflow Ledger.")
    bl.add_argument("--batch-id", help="Optional unique batch ID; existing batch directories are never reused.")
    bl.set_defaults(func=cmd_build_revision_ledger)
    rec = sub.add_parser("recover-failed-batch", help="Abandon an unpublished failed Apply, retain its artifacts and require renewed author approval.")
    rec.add_argument("--workflow-ledger", required=True)
    rec.add_argument("--revision-ledger", required=True)
    rec.add_argument("--batch-dir", required=True)
    rec.add_argument("--reason", required=True, help="Why the failed, unpublished batch is being abandoned.")
    rec.add_argument("--actor", default="Human Author")
    rec.set_defaults(func=cmd_recover_failed_batch)
    retire = sub.add_parser("retire-superseded-prepared-batch", help="Record an orphaned intent-only preparation as superseded by an exact, verified published successor.")
    retire.add_argument("--workflow-ledger", required=True)
    retire.add_argument("--revision-ledger", required=True)
    retire.add_argument("--batch-dir", required=True, help="The stale prepared batch with immutable intent only and no scope/artifacts.")
    retire.add_argument("--successor-batch-dir", required=True, help="A later fully published batch from the exact same task set and parent documents.")
    retire.add_argument("--actor", required=True)
    retire.add_argument("--reason", required=True)
    retire.set_defaults(func=cmd_retire_superseded_prepared_batch)
    pub = sub.add_parser("publish-batch", help="Atomically switch Workflow Ledger active document pointers after all batch Patches are verified.")
    pub.add_argument("--workflow-ledger", required=True)
    pub.add_argument("--revision-ledger", required=True)
    pub.add_argument("--batch-dir", required=True)
    pub.set_defaults(func=cmd_publish_batch)
    rb = sub.add_parser("rollback-batch", help="Restore the parent active document pointers without deleting any committed output.")
    rb.add_argument("--workflow-ledger", required=True)
    rb.add_argument("--batch-dir", required=True)
    rb.add_argument("--reason", required=True)
    rb.set_defaults(func=cmd_rollback_batch)
    sp = sub.add_parser("supersede-patch", help="Replace a Patch while reopening linked ResponseUnits.")
    sp.add_argument("--ledger", required=True)
    sp.add_argument("--previous-patch-id", required=True)
    sp.add_argument("--replacement-patch", required=True, help="JSON with patch_id, task_id, reviewer, comment, proposed_response, supersedes_patch_id and exact operations.")
    sp.add_argument("--actor", default="Human Author")
    sp.add_argument("--reason", required=True)
    sp.set_defaults(func=cmd_supersede_patch)
    ex = sub.add_parser("export-revision-plan", help="Export new planned Patch operations to the legacy DOCX plan format.")
    ex.add_argument("--ledger", required=True)
    ex.add_argument("--batch-dir", help="Existing immutable runs/batch-{id} directory created by build-revision-ledger.")
    ex.add_argument("--out", help="Plan output path; defaults inside --batch-dir when supplied.")
    ex.set_defaults(func=cmd_export_revision_plan)
    wcv = sub.add_parser("create-working-clean-views", help="Create read-only accepted-changes projections from Marked DOCX outputs.")
    wcv.add_argument("--ledger", required=True)
    wcv.add_argument("--suffix", default=".working_clean")
    wcv.add_argument("--actor", default="Co_Reviewer Working View")
    wcv.set_defaults(func=cmd_create_working_clean_views)
    cru = sub.add_parser("confirm-response-units", help="Record explicit semantic review of response text.")
    cru.add_argument("--ledger", required=True)
    cru.add_argument("--ids", nargs="+", required=True)
    cru.add_argument("--verification-report-id")
    cru.add_argument("--actor", default="Human Author")
    cru.add_argument("--semantic-review-confirmed", action="store_true", required=True)
    cru.set_defaults(func=cmd_confirm_response_units)
    ident = sub.add_parser("build-document-identity", help="Parse source DOCX files into a persistent identity manifest; optionally match objects against a previous round.")
    ident.add_argument("--inventory", required=True, help="Hash-bound workspace_inventory.json from inspect/audit.")
    ident.add_argument("--previous-manifest", help="Optional prior identity manifest used for conservative object-ID reuse.")
    ident.add_argument("--out", default="document_identity_manifest.json")
    ident.set_defaults(func=cmd_build_document_identity)
    mloc = sub.add_parser("map-response-locations", help="Use Microsoft Word COM to paginate final Clean DOCX files, export PDFs and map verified Patch text to final page/line locations.")
    mloc.add_argument("--ledger", required=True)
    mloc.add_argument("--out-dir", required=True, help="New or empty directory for Word-final Clean DOCX and PDF artifacts.")
    mloc.add_argument("--location-map", help="Optional output JSON path; defaults to response_location_map.json inside --out-dir.")
    mloc.add_argument("--actor", default="Microsoft Word COM/PDF Mapper")
    mloc.set_defaults(func=cmd_map_response_locations)
    rloc = sub.add_parser("record-response-locations", help="Attach final PDF page/line locations to ResponseUnits.")
    rloc.add_argument("--ledger", required=True)
    rloc.add_argument("--location-map", required=True)
    rloc.add_argument("--actor", default="Co_Reviewer Location Mapper")
    rloc.set_defaults(func=cmd_record_response_locations)
    mrloc = sub.add_parser("record-manual-response-locations", help="Record explicit author-confirmed table or non-body locators against Word-exported final DOCX/PDF files.")
    mrloc.add_argument("--ledger", required=True)
    mrloc.add_argument("--word-map", required=True, help="Word COM map produced by map-response-locations; its role outputs must match the current Working Clean hashes.")
    mrloc.add_argument("--input", required=True, help="JSON entries with response_unit_id, operation_id (revised) or locator_id=anchor:<evidence_id> (no_change), locator_text and optional page/line_start/line_end.")
    mrloc.add_argument("--out", required=True, help="New manual-location map JSON path; existing files are never overwritten.")
    mrloc.add_argument("--actor", default="Human Author")
    mrloc.set_defaults(func=cmd_record_manual_response_locations)
    cru2 = sub.add_parser("complete-response-units", help="Complete units after verified patches and final PDF locations.")
    cru2.add_argument("--ledger", required=True)
    cru2.add_argument("--ids", nargs="+", required=True)
    cru2.add_argument("--actor", default="Human Author")
    cru2.add_argument("--ready-for-submission-confirmed", action="store_true", required=True)
    cru2.set_defaults(func=cmd_complete_response_units)
    rec = sub.add_parser("record-usage-event", help="Append one evidence-labelled metadata event to a JSONL usage ledger.")
    rec.add_argument("--ledger", default=".co-reviewer-usage.jsonl")
    rec.add_argument("--event", required=True, help="JSON file containing one metadata-only usage event.")
    rec.set_defaults(func=cmd_record_usage_event)
    aua = sub.add_parser("audit-usage", help="Aggregate document-read and explicitly reported token usage without inferring missing values.")
    aua.add_argument("--ledger", default=".co-reviewer-usage.jsonl")
    aua.add_argument("--pricing", help="Optional explicit pricing JSON; cost estimation requires disjoint bucket semantics.")
    aua.add_argument("--out", default="usage_audit_report.json")
    aua.set_defaults(func=cmd_audit_usage)
    est = sub.add_parser("estimate-usage", help="Estimate cost from explicit token events and a user-supplied pricing file.")
    est.add_argument("--ledger", default=".co-reviewer-usage.jsonl")
    est.add_argument("--pricing", required=True)
    est.add_argument("--out", default="usage_cost_estimate.json")
    est.set_defaults(func=cmd_estimate_usage)
    dq = sub.add_parser("audit-docx-quality", help="Audit DOCX package, revision state, placeholders, typography profile and optional PDF.")
    dq.add_argument("--docx", required=True)
    dq.add_argument("--profile", choices=["generic", "clean-manuscript", "marked-manuscript", "word-final-clean", "response-letter"], default="generic")
    dq.add_argument("--pdf", help="Optional related PDF for signature/page-integrity metadata.")
    dq.add_argument("--expected-font", help="Optional required Normal-style font name.")
    dq.add_argument("--out", help="Optional JSON report path.")
    dq.set_defaults(func=cmd_audit_docx_quality)
    st = sub.add_parser("status", help="Summarize revision item states without editing documents.")
    st.add_argument("--plan", required=True)
    st.set_defaults(func=cmd_status)
    return p

def main(argv=None):
    parser = build_parser(); args = parser.parse_args(argv)
    try: return args.func(args)
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())

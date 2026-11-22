# -*- coding: utf-8 -*-
"""WordprocessingML primitives and conservative native Track Changes engine.

This module is extracted from the v0.3 CLI without changing its edit semantics.
The CLI remains a compatibility facade while the engine boundary becomes testable.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import io
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from lxml import etree as ET
from .v232_contract import (
    attach_format_change, build_revision_baseline_inventory,
    effective_run_properties, narrow_revision, targeted_revision_gate,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W}
ET.register_namespace("w", W)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
ET.register_namespace("wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing")
ET.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
ET.register_namespace("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture")

def parse_xml(data: bytes) -> ET.Element:
    parser = ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, remove_comments=False, huge_tree=False)
    return ET.fromstring(data, parser=parser)

def qn(tag: str) -> str:
    return f"{{{W}}}{tag}"

def visible_text(paragraph: ET.Element) -> str:
    out = []
    for node in paragraph.iter():
        if node.tag == qn("t") and node.text:
            out.append(node.text)
        elif node.tag == qn("tab"):
            out.append("\t")
        elif node.tag == qn("br"):
            out.append("\n")
    return "".join(out)

def _xml_element_path(node: ET.Element) -> str:
    """Return a deterministic child-index path for an element in its source part."""
    segments = []
    current = node
    while current.getparent() is not None:
        parent = current.getparent()
        segments.append(str(list(parent).index(current)))
        current = parent
    return "/" + "/".join(reversed(segments))


def map_target_text_to_xml_nodes(
    root: ET.Element,
    paragraph: ET.Element,
    target_text: str,
) -> dict[str, Any]:
    """Map one unique ordinary-Run target to exact w:r/w:t nodes before mutation.

    This is an audit map, not an authorization. It deliberately refuses targets
    that cannot be represented as one unique contiguous ordinary Run sequence.
    """
    if not isinstance(root, ET.Element) or not isinstance(paragraph, ET.Element):
        raise TypeError("root and paragraph must be lxml elements.")
    if not isinstance(target_text, str) or not target_text:
        raise ValueError("target_text must be a non-empty string.")
    document_order = {node: index for index, node in enumerate(root.iter())}
    paragraph_text = visible_text(paragraph)
    occurrences = paragraph_text.count(target_text)
    target_start_global = paragraph_text.find(target_text)
    target_end_global = target_start_global + len(target_text)
    if occurrences == 0:
        raise ValueError("Target text was not found in the paragraph visible-text projection.")
    if occurrences > 1:
        raise ValueError("Target is ambiguous: it occurs more than once in the paragraph visible-text projection.")

    from .bounded_safe_span import collect_internal_bookmark_ids, is_transparent_paragraph_child

    internal_bookmark_ids = collect_internal_bookmark_ids(root)
    containers = [paragraph]
    containers.extend(
        node for node in paragraph.iter()
        if node is not paragraph and node.tag == qn("hyperlink")
    )
    matches = []
    for container in containers:
        children = list(container)
        index = 0
        while index < len(children):
            if children[index].tag != qn("r"):
                index += 1
                continue
            runs = []
            values = []
            while index < len(children):
                child = children[index]
                if child.tag == qn("r"):
                    value = run_plain_text(child)
                    if value is None:
                        break
                    runs.append(child)
                    values.append(value)
                    index += 1
                elif runs and is_transparent_paragraph_child(child, internal_bookmark_ids):
                    index += 1
                else:
                    break
            if not runs:
                index += 1
                continue
            joined = "".join(values)
            start = joined.find(target_text)
            while start >= 0:
                matches.append((container, runs, start, joined))
                start = joined.find(target_text, start + 1)

    if len(matches) != 1:
        raise ValueError(
            "Target refused: it does not map to exactly one contiguous ordinary Run sequence "
            "and may cross a protected or unsupported XML boundary."
        )
    container, runs, target_start, joined = matches[0]
    target_end = target_start + len(target_text)
    run_map = []
    run_cursor = 0
    for run_index, run in enumerate(runs):
        run_text = run_plain_text(run) or ""
        run_start, run_end = run_cursor, run_cursor + len(run_text)
        overlap_start, overlap_end = max(target_start, run_start), min(target_end, run_end)
        if overlap_start < overlap_end:
            text_nodes = []
            node_cursor = 0
            for child_index, child in enumerate(run):
                if child.tag != qn("t"):
                    continue
                value = child.text or ""
                node_start, node_end = node_cursor, node_cursor + len(value)
                text_start, text_end = max(overlap_start - run_start, node_start), min(overlap_end - run_start, node_end)
                if text_start < text_end:
                    text_nodes.append({
                        "text_node_index_in_run": child_index,
                        "text_node_document_order": document_order[child],
                        "text_node_xml_path": _xml_element_path(child),
                        "text_node_text_start": text_start - node_start,
                        "text_node_text_end": text_end - node_start,
                        "target_text": value[text_start - node_start:text_end - node_start],
                    })
                node_cursor = node_end
            run_map.append({
                "run_index_in_sequence": run_index,
                "run_document_order": document_order[run],
                "run_xml_path": _xml_element_path(run),
                "run_text_start": overlap_start - run_start,
                "run_text_end": overlap_end - run_start,
                "target_text": run_text[overlap_start - run_start:overlap_end - run_start],
                "text_nodes": text_nodes,
            })
        run_cursor = run_end

    mapped = "".join(
        node["target_text"] for run in run_map for node in run["text_nodes"]
    )
    if mapped != target_text:
        raise ValueError("Run/Text-node event map does not reconstruct the exact target text.")
    # Record every structural/non-text XML event in this paragraph, including zero-width
    # boundaries that are not represented in the visible-text projection.
    projection_positions: dict[ET.Element, tuple[int, int]] = {}
    projection_cursor = 0
    visible_text_tags = {qn("t"), qn("tab"), qn("br")}
    def index_projection(node: ET.Element) -> None:
        nonlocal projection_cursor
        start = projection_cursor
        if node.tag == qn("t"):
            projection_cursor += len(node.text or "")
        elif node.tag in {qn("tab"), qn("br")}:
            projection_cursor += 1
        else:
            for child in list(node):
                index_projection(child)
        projection_positions[node] = (start, projection_cursor)
    index_projection(paragraph)
    if projection_cursor != len(paragraph_text):
        raise ValueError("Structural event projection does not reconcile with visible_text().")

    formatting_nodes: set[ET.Element] = set()
    for property_node in paragraph.findall(".//w:pPr", NS) + paragraph.findall(".//w:rPr", NS):
        formatting_nodes.update(property_node.iter())
    event_families = {
        "bookmarkStart": "bookmark_boundary", "bookmarkEnd": "bookmark_boundary",
        "commentRangeStart": "comment_boundary", "commentRangeEnd": "comment_boundary",
        "commentReference": "comment_reference",
        "permStart": "permission_boundary", "permEnd": "permission_boundary",
        "fldChar": "field_marker", "instrText": "field_instruction",
        "hyperlink": "hyperlink_container",
        "drawing": "drawing_object", "pict": "drawing_object", "object": "drawing_object",
        "sdt": "content_control", "sdtContent": "content_control", "sdtPr": "content_control",
        "ins": "revision_wrapper", "del": "revision_wrapper",
        "moveFrom": "revision_wrapper", "moveTo": "revision_wrapper",
        "moveFromRangeStart": "revision_boundary", "moveFromRangeEnd": "revision_boundary",
        "moveToRangeStart": "revision_boundary", "moveToRangeEnd": "revision_boundary",
        "proofErr": "proofing_boundary", "tab": "visible_control", "br": "visible_control",
        "footnoteReference": "note_reference", "endnoteReference": "note_reference",
        "customXml": "custom_xml_container", "altChunk": "alternate_content",
        "lastRenderedPageBreak": "page_break_marker",
    }
    structural_events: list[dict[str, Any]] = []
    for node in paragraph.iter():
        if node is paragraph or node in formatting_nodes:
            continue
        if node.tag in {qn("p"), qn("pPr"), qn("r"), qn("rPr"), qn("t")} :
            continue
        if node not in projection_positions:
            raise ValueError("A paragraph XML event is missing its visible-text projection position.")
        start, end = projection_positions[node]
        local_name = ET.QName(node).localname
        family = event_families.get(local_name, "other_structural_xml")
        if start == end:
            if start < target_start_global:
                relation = "before_target"
            elif start == target_start_global:
                relation = "at_target_start"
            elif start == target_end_global:
                relation = "at_target_end"
            elif start < target_end_global:
                relation = "inside_target"
            else:
                relation = "after_target"
        elif end <= target_start_global:
            relation = "before_target"
        elif start >= target_end_global:
            relation = "after_target"
        elif start <= target_start_global and end >= target_end_global:
            relation = "contains_target"
        else:
            relation = "intersects_target"
        parent = node.getparent()
        depth = 0
        while parent is not None and parent is not paragraph:
            depth += 1
            parent = parent.getparent()
        attributes = {
            ET.QName(key).localname: value
            for key, value in sorted(node.attrib.items())
        }
        event = {
            "event_index": len(structural_events),
            "event_document_order": document_order.get(node),
            "event_kind": local_name,
            "event_family": family,
            "xml_path": _xml_element_path(node),
            "parent_xml_path": _xml_element_path(node.getparent()) if node.getparent() is not None else None,
            "nesting_depth": depth,
            "visible_text_start": start,
            "visible_text_end": end,
            "zero_width": start == end,
            "target_relation": relation,
            "attributes": attributes,
        }
        if local_name == "instrText":
            event["instruction_text"] = node.text or ""
        structural_events.append(event)

    field_stack: list[dict[str, Any]] = []
    field_ranges: list[dict[str, Any]] = []
    for event in structural_events:
        if event["event_kind"] != "fldChar":
            continue
        field_type = event["attributes"].get("fldCharType")
        if field_type == "begin":
            field_stack.append({
                "begin_event_index": event["event_index"],
                "begin_visible_text_offset": event["visible_text_start"],
                "separate_event_index": None,
                "result_start": None,
            })
        elif field_type == "separate" and field_stack:
            field_stack[-1]["separate_event_index"] = event["event_index"]
            field_stack[-1]["result_start"] = event["visible_text_end"]
        elif field_type == "end" and field_stack:
            field = field_stack.pop()
            field["end_event_index"] = event["event_index"]
            field["end_visible_text_offset"] = event["visible_text_start"]
            field["complete"] = field["separate_event_index"] is not None
            field["target_in_field_result"] = (
                field["result_start"] is not None
                and field["result_start"] <= target_start_global
                and target_end_global <= field["end_visible_text_offset"]
            )
            field_ranges.append(field)
    for field in field_stack:
        field["end_event_index"] = None
        field["end_visible_text_offset"] = None
        field["complete"] = False
        field["target_in_field_result"] = False
        field_ranges.append(field)
    boundary_events = [
        event for event in structural_events
        if event["zero_width"] or event["event_family"] in {
            "bookmark_boundary", "comment_boundary", "permission_boundary",
            "field_marker", "field_instruction", "revision_boundary",
            "proofing_boundary", "page_break_marker",
        }
    ]
    return {
        "schema_version": "1.0",
        "status": "MAPPED",
        "source_part_document_order": document_order.get(root),
        "paragraph_document_order": document_order[paragraph],
        "paragraph_xml_path": _xml_element_path(paragraph),
        "container_xml_path": _xml_element_path(container),
        "target_text": target_text,
        "target_start_in_run_sequence": target_start,
        "target_end_in_run_sequence": target_end,
        "target_start_in_paragraph": target_start_global,
        "target_end_in_paragraph": target_end_global,
        "paragraph_visible_text_length": len(paragraph_text),
        "run_map": run_map,
        "text_node_count": sum(len(run["text_nodes"]) for run in run_map),
        "structural_events": structural_events,
        "boundary_events": boundary_events,
        "field_ranges": field_ranges,
        "structural_event_count": len(structural_events),
        "structural_event_coverage_complete": len(structural_events) == sum(
            1 for node in paragraph.iter()
            if node is not paragraph and node not in formatting_nodes
            and node.tag not in {qn("p"), qn("pPr"), qn("r"), qn("rPr"), qn("t")}
        ),
        "mapping_reconstructed_exactly": True,
    }

def has_drawing(paragraph: ET.Element) -> bool:
    return any(x.tag in {qn("drawing"), qn("pict"), qn("object")} for x in paragraph.iter())

def extract_native_script_runs(paragraph: ET.Element) -> list[dict[str, str]]:
    """Expose native Word subscript/superscript runs in the document inventory."""
    result = []
    for run in paragraph.findall(".//w:r", NS):
        vertical = run.find("w:rPr/w:vertAlign", NS)
        if vertical is None or vertical.get(qn("val")) not in {"subscript", "superscript"}:
            continue
        text = "".join(node.text or "" for node in run.findall("w:t", NS))
        if text:
            result.append({"text": text, "vertical_alignment": vertical.get(qn("val"))})
    return result

def run_plain_text(run: ET.Element) -> str | None:
    allowed = {qn("rPr"), qn("t"), qn("lastRenderedPageBreak")}
    if any(child.tag not in allowed for child in list(run)):
        return None
    nodes = run.findall("w:t", NS)
    return "".join(x.text or "" for x in nodes) if nodes else None

def make_run(
    source: ET.Element,
    text: str,
    deleted: bool = False,
    *,
    preserve_rpr_change: bool = True,
) -> ET.Element | None:
    if not text:
        return None
    run = ET.Element(qn("r"))
    for key, value in source.attrib.items():
        run.set(key, value)
    props = source.find("w:rPr", NS)
    if props is not None:
        cloned_props = copy.deepcopy(props)
        if not preserve_rpr_change:
            # A new insertion has no historical formatting state of its own.
            # Do not carry the source run's prior rPrChange snapshot into w:ins.
            for historical_change in list(cloned_props.findall("w:rPrChange", NS)):
                cloned_props.remove(historical_change)
        run.append(cloned_props)
    node = ET.SubElement(run, qn("delText") if deleted else qn("t"))
    node.text = text
    if text[:1].isspace() or text[-1:].isspace():
        node.set(f"{{{XML}}}space", "preserve")
    return run

# WordprocessingML run-property order (ECMA-376). New properties are inserted
# at their schema position so Word does not need to repair our XML.

# WordprocessingML run-property order (ECMA-376). New properties are inserted
# at their schema position so Word does not need to repair our XML.
_RUN_PROPERTY_ORDER = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
    "strike", "dstrike", "outline", "shadow", "emboss", "imprint", "noProof",
    "snapToGrid", "vanish", "webHidden", "color", "spacing", "w", "kern",
    "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd",
    "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout",
    "specVanish", "oMath", "rPrChange",
)


def _set_run_property(run: ET.Element, name: str, value: str | None) -> None:
    """Replace one w:rPr property and keep the WordprocessingML property order."""
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = ET.Element(qn("rPr"))
        run.insert(0, rpr)
    for existing in list(rpr):
        if existing.tag == qn(name):
            rpr.remove(existing)
    if value is None:
        return
    element = ET.Element(qn(name), {qn("val"): value})
    try:
        wanted = _RUN_PROPERTY_ORDER.index(name)
    except ValueError:
        rpr.append(element)
        return
    insertion_index = len(rpr)
    for index, child in enumerate(list(rpr)):
        try:
            child_rank = _RUN_PROPERTY_ORDER.index(ET.QName(child).localname)
        except ValueError:
            continue
        if child_rank > wanted:
            insertion_index = index
            break
    rpr.insert(insertion_index, element)

def _run_format_signature(run: ET.Element) -> bytes:
    """Return current direct-format properties, excluding historical snapshots."""
    properties = run.find("w:rPr", NS)
    if properties is None:
        return b""
    current = copy.deepcopy(properties)
    for historical in list(current.findall("w:rPrChange", NS)):
        current.remove(historical)
    if not list(current) and not current.attrib:
        return b""
    return ET.tostring(current, method="c14n")


def _format_run_properties(run: ET.Element) -> ET.Element | None:
    """Copy direct run properties without the historical property snapshot."""
    properties = run.find("w:rPr", NS)
    if properties is None:
        return None
    result = copy.deepcopy(properties)
    for historical in list(result.findall("w:rPrChange", NS)):
        result.remove(historical)
    return result


def build_auto_replacement_format_map(
    spans: list[tuple[int, int, ET.Element, str]],
    *,
    match_start: int,
    old: str,
    new: str,
    allow_dominant_replace_fallback: bool = False,
) -> list[dict[str, int]] | None:
    """Compute a deterministic replacement_format_map from mixed-style source runs.

    Returns None when source runs have uniform formatting, when `new` is empty,
    or when a replacement span cannot be unambiguously anchored to a single
    source format signature (fail-closed).
    """
    if not old or not new:
        return None
    char_sigs: list[bytes] = []
    for k in range(len(old)):
        abs_pos = match_start + k
        sig = next(
            (
                _run_format_signature(run)
                for run_start, run_end, run, _ in spans
                if run_start <= abs_pos < run_end
            ),
            None,
        )
        if sig is None:
            return None
        char_sigs.append(sig)

    if len(set(char_sigs)) <= 1:
        return None

    mapped_offsets: list[int] = [0] * len(new)
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapped_offsets[j1 + k] = i1 + k
        elif tag == "replace":
            slice_sigs = char_sigs[i1:i2]
            if len(set(slice_sigs)) == 1:
                dominant_offset = i1
            elif allow_dominant_replace_fallback:
                counts: dict[bytes, int] = {}
                first_pos: dict[bytes, int] = {}
                for offset_idx, sig in enumerate(slice_sigs, start=i1):
                    counts[sig] = counts.get(sig, 0) + 1
                    if sig not in first_pos:
                        first_pos[sig] = offset_idx
                dominant_sig = max(counts, key=lambda s: (counts[s], -first_pos[s]))
                dominant_offset = first_pos[dominant_sig]
            else:
                return None
            for j in range(j1, j2):
                mapped_offsets[j] = dominant_offset
        elif tag == "insert":
            anchor = max(0, i1 - 1) if i1 > 0 else 0
            for j in range(j1, j2):
                mapped_offsets[j] = anchor

    segments: list[dict[str, int]] = []
    seg_start = 0
    seg_offset = mapped_offsets[0]
    seg_sig = char_sigs[seg_offset]
    for j in range(1, len(new)):
        off = mapped_offsets[j]
        sig = char_sigs[off]
        if sig != seg_sig:
            segments.append({
                "replacement_start": seg_start,
                "replacement_end": j,
                "source_offset": seg_offset,
            })
            seg_start = j
            seg_offset = off
            seg_sig = sig
    segments.append({
        "replacement_start": seg_start,
        "replacement_end": len(new),
        "source_offset": seg_offset,
    })
    return segments


def _build_mapped_insert_runs(
    spans: list[tuple[int, int, ET.Element, str]],
    *,
    match_start: int,
    source_length: int,
    replacement: str,
    format_map: list[dict[str, Any]],
) -> list[ET.Element]:
    """Build replacement Runs from an explicit full-coverage source-format map."""
    if not isinstance(format_map, list) or not format_map:
        raise ValueError("replacement_format_map must be a non-empty array for mixed-format replacements.")
    cursor = 0
    inserted: list[ET.Element] = []
    for index, segment in enumerate(format_map):
        if not isinstance(segment, dict):
            raise ValueError(f"replacement_format_map[{index}] must be an object.")
        start = segment.get("replacement_start")
        end = segment.get("replacement_end")
        source_offset = segment.get("source_offset")
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (start, end, source_offset)):
            raise ValueError(f"replacement_format_map[{index}] offsets must be integers.")
        if start != cursor or end <= start or end > len(replacement):
            raise ValueError(
                "replacement_format_map must cover replacement text contiguously, in order, "
                "without gaps or overlaps."
            )
        if source_offset < 0 or source_offset >= source_length:
            raise ValueError(f"replacement_format_map[{index}].source_offset is outside the deleted source anchor.")
        absolute_source_offset = match_start + source_offset
        source_run = next(
            (
                run for run_start, run_end, run, _ in spans
                if run_start <= absolute_source_offset < run_end
            ),
            None,
        )
        if source_run is None:
            raise ValueError(
                f"replacement_format_map[{index}] does not resolve to an ordinary source Run."
            )
        inserted.extend(make_insert_runs(source_run, replacement[start:end]))
        cursor = end
    if cursor != len(replacement):
        raise ValueError(
            "replacement_format_map must cover every replacement character exactly once."
        )
    return inserted


def _set_run_fonts(run: ET.Element, fonts: dict[str, str]) -> None:
    allowed = {"ascii", "hAnsi", "eastAsia", "cs"}
    if not isinstance(fonts, dict) or not fonts or set(fonts) - allowed:
        raise ValueError("rFonts format operation requires one or more of ascii/hAnsi/eastAsia/cs.")
    if any(not isinstance(value, str) or not value.strip() for value in fonts.values()):
        raise ValueError("Every requested font face must be a non-empty string.")
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = ET.Element(qn("rPr"))
        run.insert(0, rpr)
    rfonts = rpr.find("w:rFonts", NS)
    if rfonts is None:
        rfonts = ET.Element(qn("rFonts"))
        try:
            wanted = _RUN_PROPERTY_ORDER.index("rFonts")
        except ValueError:
            rpr.insert(0, rfonts)
        else:
            pos = len(rpr)
            for idx, child in enumerate(list(rpr)):
                try:
                    rank = _RUN_PROPERTY_ORDER.index(ET.QName(child).localname)
                except ValueError:
                    continue
                if rank > wanted:
                    pos = idx
                    break
            rpr.insert(pos, rfonts)
    for slot, face in fonts.items():
        rfonts.set(qn(slot), face)
        theme_slot = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme",
                      "eastAsia": "eastAsiaTheme", "cs": "cstheme"}[slot]
        rfonts.attrib.pop(qn(theme_slot), None)


def _apply_format_properties(run: ET.Element, properties: dict[str, Any]) -> None:
    boolean_names = {
        "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
        "outline", "shadow", "emboss", "imprint",
    }
    value_names = {
        "vertAlign": {"baseline", "superscript", "subscript"},
        "u": {"single", "double", "dotted", "dash", "wave", "none"},
        "highlight": {
            "black", "blue", "cyan", "green", "magenta", "red", "yellow",
            "white", "darkBlue", "darkCyan", "darkGreen", "darkMagenta",
            "darkRed", "darkYellow", "darkGray", "lightGray", "none",
        },
    }
    allowed = boolean_names | set(value_names) | {"sz", "szCs", "color", "position", "spacing", "rFonts", "rStyle"}
    if not isinstance(properties, dict) or not properties or set(properties) - allowed:
        raise ValueError("FormatChange contains an unsupported or empty property set.")
    for name, value in properties.items():
        if name == "rFonts":
            _set_run_fonts(run, value)
        elif name in boolean_names:
            if not isinstance(value, bool):
                raise ValueError(f"FormatChange {name} requires a boolean value.")
            _set_run_property(run, name, "1" if value else "0")
        elif name in {"sz", "szCs", "position", "spacing"}:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"FormatChange {name} requires an integer OOXML value.")
            if name in {"sz", "szCs"} and value < 1:
                raise ValueError(f"FormatChange {name} requires a positive integer OOXML value.")
            _set_run_property(run, name, str(value))
        elif name == "rStyle":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("FormatChange rStyle requires a non-empty character-style ID.")
            _set_run_property(run, name, value)
        elif name == "color":
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{6}|auto)", value):
                raise ValueError("FormatChange color requires six hex digits or 'auto'.")
            _set_run_property(run, name, value)
        elif name in value_names:
            if not isinstance(value, str) or value not in value_names[name]:
                raise ValueError(f"FormatChange {name} has an unsupported value.")
            _set_run_property(run, name, value)


def apply_format_operation(
    paragraph: ET.Element,
    find_text: str,
    properties: dict[str, Any],
    *,
    document_xml: bytes,
    styles_xml: bytes | None,
    theme_xml: bytes | None,
    author: str,
    date: str,
    next_revision_id: int,
    include_inserted_runs: bool = False,
    scope_find: str | None = None,
    scope_offset: int | None = None,
) -> tuple[bool, int, list[dict[str, Any]], str]:
    """Apply one explicitly authorized FormatChange to existing ordinary (or optionally inserted) text Runs."""
    if not isinstance(find_text, str) or not find_text:
        return False, next_revision_id, [], "FormatChange find text must be non-empty."
    if any(character in find_text for character in "\t\r\n"):
        return False, next_revision_id, [], "FormatChange cannot cross tabs or paragraph breaks."
    if any(ancestor.tag == qn("tbl") for ancestor in paragraph.iterancestors()):
        return False, next_revision_id, [], "FORMAT_CHANGE_TABLE_STYLE_RESOLUTION_REQUIRED"
    ppr = paragraph.find("w:pPr", NS)
    if ppr is not None and ppr.find("w:numPr", NS) is not None:
        return False, next_revision_id, [], "FORMAT_CHANGE_NUMBERING_STYLE_RESOLUTION_REQUIRED"
    paragraph_style = ppr.find("w:pStyle", NS) if ppr is not None else None
    paragraph_style_id = paragraph_style.get(qn("val")) if paragraph_style is not None else None
    paragraph_rpr = ppr.find("w:rPr", NS) if ppr is not None else None

    children = list(paragraph)
    # Each block: (start_idx, end_idx, entries, joined_text)
    # entry is ("run", elem) | ("ins", ins_elem, [run_elem, ...]) | ("skip", elem)
    blocks: list[tuple[int, int, list[tuple[Any, ...]], str]] = []
    index = 0
    while index < len(children):
        child = children[index]
        is_start = (
            child.tag == qn("r")
            or (include_inserted_runs and child.tag == qn("ins"))
        )
        if not is_start:
            index += 1
            continue
        start = index
        entries: list[tuple[Any, ...]] = []
        texts: list[str] = []
        has_text_run = False
        while index < len(children):
            node = children[index]
            if node.tag == qn("r"):
                value = run_plain_text(node)
                if value is None:
                    break
                entries.append(("run", node))
                texts.append(value)
                has_text_run = True
                index += 1
            elif include_inserted_runs and node.tag == qn("ins"):
                ins_children = list(node)
                ins_texts: list[str] = []
                valid_ins = True
                for ins_child in ins_children:
                    if ins_child.tag != qn("r"):
                        valid_ins = False
                        break
                    val = run_plain_text(ins_child)
                    if val is None:
                        valid_ins = False
                        break
                    ins_texts.append(val)
                if not valid_ins:
                    break
                entries.append(("ins", node, ins_children))
                texts.extend(ins_texts)
                if ins_children:
                    has_text_run = True
                index += 1
            elif include_inserted_runs and node.tag in {qn("del"), qn("moveFrom")}:
                entries.append(("skip", node))
                index += 1
            else:
                break
        if has_text_run:
            blocks.append((start, index, entries, "".join(texts)))
        elif index == start:
            index += 1

    if scope_find is not None and scope_offset is not None:
        scope_matches = []
        for start, end, entries, text in blocks:
            position = text.find(scope_find)
            while position >= 0:
                scope_matches.append((start, end, entries, text, position))
                position = text.find(scope_find, position + 1)
        if not scope_matches:
            return False, next_revision_id, [], "FormatChange scope_find not found in an ordinary Run sequence."
        if len(scope_matches) != 1:
            return False, next_revision_id, [], "FormatChange scope_find is ambiguous."
        start, end, entries, joined, scope_pos = scope_matches[0]
        match_start = scope_pos + scope_offset
        if joined[match_start:match_start + len(find_text)] != find_text:
            return False, next_revision_id, [], "FormatChange scope_offset does not match find text."
    else:
        matches = []
        for start, end, entries, text in blocks:
            position = text.find(find_text)
            while position >= 0:
                matches.append((start, end, entries, text, position))
                position = text.find(find_text, position + 1)
        if not matches:
            return False, next_revision_id, [], "FormatChange target not found in an ordinary Run sequence."
        if len(matches) != 1:
            return False, next_revision_id, [], "FormatChange target is ambiguous."
        start, end, entries, joined, match_start = matches[0]
    match_end = match_start + len(find_text)

    def _format_single_run(
        source_run: ET.Element,
        run_start: int,
        run_end: int,
        out_list: list[ET.Element],
        changes_out: list[dict[str, Any]],
        rev_id: int,
    ) -> tuple[bool, int, str]:
        overlap_start = max(match_start, run_start)
        overlap_end = min(match_end, run_end)
        if overlap_start >= overlap_end:
            out_list.append(copy.deepcopy(source_run))
            return True, rev_id, "ok"
        value = run_plain_text(source_run) or ""
        prefix = value[:overlap_start - run_start]
        selected = value[overlap_start - run_start:overlap_end - run_start]
        suffix = value[overlap_end - run_start:]
        prefix_run = make_run(source_run, prefix)
        if prefix_run is not None:
            out_list.append(prefix_run)
        selected_run = make_run(source_run, selected)
        if selected_run is None:
            return False, rev_id, "FormatChange selected Run could not be cloned."
        if selected_run.find("w:rPr/w:rPrChange", NS) is not None:
            return False, rev_id, "FORMAT_CHANGE_EXISTING_HISTORY_REQUIRES_RESOLUTION"
        run_rpr = source_run.find("w:rPr", NS)
        run_style = run_rpr.find("w:rStyle", NS) if run_rpr is not None else None
        run_style_id = run_style.get(qn("val")) if run_style is not None else None
        effective_before = effective_run_properties(
            document_xml, styles_xml, paragraph_style_id, run_style_id,
            run_rpr, paragraph_rpr=paragraph_rpr, theme_xml=theme_xml,
        )
        attach_format_change(
            selected_run, effective_before, list(properties),
            rev_id, author, date,
        )
        _apply_format_properties(selected_run, properties)
        out_list.append(selected_run)
        changes_out.append({
            "find": selected,
            "revision_id": rev_id,
            "properties": copy.deepcopy(properties),
            "old_effective_properties": copy.deepcopy(effective_before["properties"]),
        })
        rev_id += 1
        suffix_run = make_run(source_run, suffix)
        if suffix_run is not None:
            out_list.append(suffix_run)
        return True, rev_id, "ok"

    replacements: list[ET.Element] = []
    changes: list[dict[str, Any]] = []
    cursor = 0
    for entry in entries:
        kind = entry[0]
        if kind == "skip":
            replacements.append(copy.deepcopy(entry[1]))
        elif kind == "run":
            source_run = entry[1]
            value = run_plain_text(source_run) or ""
            run_start, run_end = cursor, cursor + len(value)
            cursor = run_end
            ok_run, next_revision_id, err_msg = _format_single_run(
                source_run, run_start, run_end, replacements, changes, next_revision_id
            )
            if not ok_run:
                return False, next_revision_id, [], err_msg
        elif kind == "ins":
            ins_node, ins_runs = entry[1], entry[2]
            new_ins = ET.Element(ins_node.tag, dict(ins_node.attrib))
            ins_out: list[ET.Element] = []
            for source_run in ins_runs:
                value = run_plain_text(source_run) or ""
                run_start, run_end = cursor, cursor + len(value)
                cursor = run_end
                ok_run, next_revision_id, err_msg = _format_single_run(
                    source_run, run_start, run_end, ins_out, changes, next_revision_id
                )
                if not ok_run:
                    return False, next_revision_id, [], err_msg
            for child_run in ins_out:
                new_ins.append(child_run)
            replacements.append(new_ins)

    for node in children[start:end]:
        paragraph.remove(node)
    insertion = min(start, len(list(paragraph)))
    for offset, node in enumerate(replacements):
        paragraph.insert(insertion + offset, node)
    return True, next_revision_id, changes, "applied"


def _script_character(character: str) -> tuple[str, str] | None:
    """Return native Word vertical alignment plus its ordinary base character."""
    decomposition = unicodedata.decomposition(character).split()
    if not decomposition or decomposition[0] not in {"<sub>", "<super>"}:
        character_name = unicodedata.name(character, "")
        if "SUPERSCRIPT" in character_name or "SUBSCRIPT" in character_name:
            raise ValueError(
                f"Unsupported Unicode script character U+{ord(character):04X} ({character_name}); "
                "use a supported Unicode sub/superscript character or request explicit Word formatting."
            )
        return None
    base = "".join(chr(int(codepoint, 16)) for codepoint in decomposition[1:])
    if not base:
        return None
    alignment = "subscript" if decomposition[0] == "<sub>" else "superscript"
    return alignment, base

def normalize_script_characters(text: str) -> str:
    """Translate Unicode compatibility sub/superscripts to their base text."""
    pieces = []
    for character in text:
        script = _script_character(character)
        pieces.append(script[1] if script else character)
    return "".join(pieces)

def make_insert_runs(source: ET.Element, text: str) -> list[ET.Element]:
    """Create red inserted runs and encode Unicode sub/superscripts as native Word formatting."""
    if not text:
        return []
    parsed = [(script[0], script[1]) if (script := _script_character(ch)) else (None, ch)
              for ch in text]
    has_explicit_script = any(alignment is not None for alignment, _ in parsed)
    segments: list[tuple[str | None, str]] = []
    for alignment, character in parsed:
        if segments and segments[-1][0] == alignment:
            segments[-1] = (alignment, segments[-1][1] + character)
        else:
            segments.append((alignment, character))

    runs = []
    for alignment, segment_text in segments:
        run = make_run(source, segment_text, preserve_rpr_change=False)
        if run is None:
            continue
        # When the replacement explicitly contains script characters, ordinary
        # characters form the baseline and must not inherit sub/superscript from
        # the first matched source run.
        if has_explicit_script:
            _set_run_property(run, "vertAlign", alignment)
        runs.append(run)
    return runs

def revision_wrapper(kind: str, rid: int, author: str, date: str, runs: list[ET.Element]) -> ET.Element:
    wrapper = ET.Element(qn(kind), {qn("id"): str(rid), qn("author"): author, qn("date"): date})
    for run in runs:
        wrapper.append(run)
    return wrapper

def replace_in_paragraph(
    para: ET.Element, old: str, new: str, author: str, date: str, next_id: int,
    replacement_format_map: list[dict[str, Any]] | None = None,
    allow_format_flatten: bool = False,
    auto_inherit_format_map: bool = False,
):
    from .bounded_safe_span import collect_internal_bookmark_ids, is_transparent_paragraph_child

    tree_root = para.getroottree().getroot() if para.getparent() is not None else para
    internal_bookmark_ids = collect_internal_bookmark_ids(tree_root)
    children = list(para)
    blocks = []
    i = 0
    while i < len(children):
        if children[i].tag != qn("r"):
            i += 1
            continue
        start, runs, texts = i, [], []
        last_run_idx = i
        while i < len(children):
            child = children[i]
            if child.tag == qn("r"):
                value = run_plain_text(child)
                if value is None:
                    break
                runs.append(child)
                texts.append(value)
                last_run_idx = i
                i += 1
            elif runs and is_transparent_paragraph_child(child, internal_bookmark_ids):
                i += 1
            else:
                break
        if runs:
            blocks.append((start, last_run_idx + 1, runs, "".join(texts)))
        else:
            i += 1
    matches = []
    for start, end, runs, text in blocks:
        pos = text.find(old)
        while pos >= 0:
            matches.append((start, end, runs, text, pos))
            pos = text.find(old, pos + 1)
    # Search all enabled text containers before selecting any location. A
    # plain Run match and a Hyperlink match are competing targets, not a reason
    # to prefer the ordinary Run merely because it was visited first.
    hyperlink_matches = [
        child for child in children
        if child.tag == qn("hyperlink") and old in visible_text(child)
    ]
    if matches and hyperlink_matches:
        return (
            False, next_id,
            "target is ambiguous across plain-text and hyperlink containers",
        )
    if not matches:
        # A target may be wholly contained in one simple hyperlink. Recurse into
        # that wrapper so the relationship ID and hyperlink attributes survive.
        if len(hyperlink_matches) == 1:
            return replace_in_paragraph(
                hyperlink_matches[0], old, new, author, date, next_id,
                replacement_format_map=replacement_format_map,
                allow_format_flatten=allow_format_flatten,
                auto_inherit_format_map=auto_inherit_format_map,
            )
        if len(hyperlink_matches) > 1:
            return False, next_id, "target is ambiguous across hyperlink containers"
        return False, next_id, "target not found in a supported plain-text run sequence"
    if len(matches) > 1:
        return False, next_id, "target is ambiguous: it occurs more than once in paragraph"
    start, end, runs, joined, match_start = matches[0]
    match_end = match_start + len(old)
    spans, cursor = [], 0
    for run in runs:
        value = run_plain_text(run) or ""
        spans.append((cursor, cursor + len(value), run, value))
        cursor += len(value)
    prefixes, deletions, suffixes = [], [], []
    first_matched = None
    last_matched = None
    matched_format_signatures: list[bytes] = []
    for run_start, run_end, source, value in spans:
        overlap_start = max(match_start, run_start)
        overlap_end = min(match_end, run_end)
        if overlap_start < overlap_end:
            prefix_text = value[:overlap_start - run_start]
            deleted_text = value[overlap_start - run_start:overlap_end - run_start]
            suffix_text = value[overlap_end - run_start:]
            matched_format_signatures.append(_run_format_signature(source))
        elif run_end <= match_start:
            prefix_text, deleted_text, suffix_text = value, "", ""
        elif run_start >= match_end:
            prefix_text, deleted_text, suffix_text = "", "", value
        else:
            return False, next_id, "run-to-text range mapping was inconsistent"
        frag = make_run(source, prefix_text)
        if frag is not None: prefixes.append(frag)
        if deleted_text:
            if first_matched is None: first_matched = source
            last_matched = source
            frag = make_run(source, deleted_text, deleted=True)
            if frag is not None: deletions.append(frag)
        frag = make_run(source, suffix_text)
        if frag is not None: suffixes.append(frag)
    if first_matched is None or last_matched is None:
        return False, next_id, "target could not be mapped to runs"
    if replacement_format_map is not None:
        if (
            len(set(matched_format_signatures)) > 1
            and len(replacement_format_map) == 1
            and isinstance(replacement_format_map[0], dict)
            and replacement_format_map[0].get("source_offset") == 0
            and not allow_format_flatten
        ):
            auto_map = build_auto_replacement_format_map(
                spans, match_start=match_start, old=old, new=new,
            )
            if auto_map is not None and len(auto_map) > 1:
                return (
                    False,
                    next_id,
                    "NAIVE_SINGLE_SEGMENT_FORMAT_FLATTEN_BLOCKED: "
                    "replacement_format_map uses a single source_offset=0 segment on mixed-format "
                    "source runs where replacement sub-spans align to distinct source formats; "
                    "supply a segmented replacement_format_map or set allow_format_flatten=True.",
                )
        try:
            insert_runs = _build_mapped_insert_runs(
                spans, match_start=match_start, source_length=len(old),
                replacement=new, format_map=replacement_format_map,
            )
        except ValueError as exc:
            return False, next_id, str(exc)
    elif len(set(matched_format_signatures)) > 1:
        auto_map = (
            build_auto_replacement_format_map(
                spans, match_start=match_start, old=old, new=new,
            )
            if auto_inherit_format_map
            else None
        )
        if auto_map is not None:
            try:
                insert_runs = _build_mapped_insert_runs(
                    spans, match_start=match_start, source_length=len(old),
                    replacement=new, format_map=auto_map,
                )
            except ValueError as exc:
                return False, next_id, str(exc)
        else:
            return (
                False,
                next_id,
                "MIXED_RUN_FORMAT_REPLACEMENT_REQUIRES_EXPLICIT_SEGMENT_PLAN: "
                "the target spans source runs with different direct formatting; "
                "supply a complete replacement_format_map tied to source offsets. "
                "No insertion was written.",
            )
    else:
        insert_runs = make_insert_runs(first_matched, new)
    child_pos_map = {child: idx for idx, child in enumerate(children)}
    first_matched_idx = child_pos_map[first_matched]
    last_matched_idx = child_pos_map[last_matched]
    transparent_before: list[ET.Element] = []
    transparent_within: list[ET.Element] = []
    transparent_after: list[ET.Element] = []
    for idx in range(start, end):
        node = children[idx]
        if is_transparent_paragraph_child(node, internal_bookmark_ids):
            if idx < first_matched_idx:
                transparent_before.append(node)
            elif idx < last_matched_idx:
                transparent_within.append(node)
            else:
                transparent_after.append(node)
    replacements = list(prefixes) + transparent_before
    if deletions:
        replacements.append(revision_wrapper("del", next_id, author, date, deletions)); next_id += 1
    if insert_runs:
        replacements.append(revision_wrapper("ins", next_id, author, date, insert_runs)); next_id += 1
    replacements.extend(transparent_within)
    replacements.extend(suffixes)
    replacements.extend(transparent_after)
    # Only the selected contiguous run block is replaced. All other children of
    # the current container remain intact; hyperlinks are edited inside their
    # existing wrapper so relationship IDs and link attributes are preserved.
    for node in children[start:end]:
        para.remove(node)
    insertion = min(start, len(list(para)))
    for offset, node in enumerate(replacements):
        para.insert(insertion + offset, node)
    return True, next_id, "applied"

def next_revision_id(root: ET.Element) -> int:
    ids = []
    for node in root.iter():
        if node.tag in {qn("ins"), qn("del"), qn("moveTo"), qn("moveFrom")}:
            try: ids.append(int(node.get(qn("id"), "0")))
            except ValueError: pass
    return max(ids, default=0) + 1

def safe_part(part: str) -> str:
    part = part.replace("\\", "/")
    supported = (
        part == "word/document.xml"
        or part in {"word/footnotes.xml", "word/endnotes.xml"}
        or re.fullmatch(r"word/(?:header|footer)\d+\.xml", part) is not None
    )
    if part.startswith("/") or ".." in Path(part).parts or not supported:
        raise ValueError(f"Unsupported or unsafe Word XML part path: {part}")
    return part

def _resolve_target_ref_paragraph(
    source: Path,
    part: str,
    edit: Mapping[str, Any],
    paragraphs: Sequence[ET.Element],
) -> tuple[int, dict[str, Any]]:
    """Resolve a stable TargetRef from exact source DOCX bytes; never guess by p_idx."""
    from .identity import TargetRef, canonical_fingerprint, normalize_visible_text, sha256_file
    from .docx_identity import _read_docx

    raw_ref = edit.get("target_ref")
    if not isinstance(raw_ref, Mapping):
        raise ValueError("Stable TargetRef must be an object.")
    try:
        ref = TargetRef(
            document_id=raw_ref.get("document_id"), revision_id=raw_ref.get("revision_id"),
            object_id=raw_ref.get("object_id"), object_type=raw_ref.get("object_type"),
            part=raw_ref.get("part"), object_fingerprint=raw_ref.get("object_fingerprint"),
            start_offset=raw_ref.get("start_offset"), end_offset=raw_ref.get("end_offset"),
            location_hint=raw_ref.get("location_hint"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Stable TargetRef is invalid: {exc}") from exc
    if ref.part != part:
        raise ValueError("TargetRef part does not match the PatchOperation part.")
    actual_sha256 = sha256_file(source)
    expected_sha256 = edit.get("source_sha256")
    if not isinstance(expected_sha256, str) or expected_sha256 != actual_sha256:
        raise ValueError("TargetRef source SHA-256 is absent or stale; re-preflight and re-authorize the Patch.")
    expected_revision_id = canonical_fingerprint({
        "document_id": ref.document_id, "source_sha256": actual_sha256,
    })
    if ref.revision_id != expected_revision_id:
        raise ValueError("TargetRef revision_id does not match the exact source DOCX version.")
    _, descriptors = _read_docx(source)
    if sha256_file(source) != actual_sha256:
        raise ValueError("Source DOCX changed while its TargetRef was being resolved.")
    hint = ref.location_hint if isinstance(ref.location_hint, Mapping) else {}
    native_id = hint.get("para_id")
    matches = []
    for descriptor in descriptors:
        if descriptor.get("part") != part or descriptor.get("object_type") != ref.object_type:
            continue
        payload = descriptor.get("payload")
        if not isinstance(payload, Mapping):
            continue
        fingerprint = canonical_fingerprint({
            "object_type": descriptor.get("object_type"),
            "part": descriptor.get("part"),
            "payload": payload,
        })
        if fingerprint != ref.object_fingerprint:
            continue
        if native_id and descriptor.get("native_id") != native_id:
            continue
        matches.append(descriptor)
    if len(matches) != 1:
        raise ValueError(
            "TargetRef object fingerprint must resolve to exactly one current DOCX object; "
            "no paragraph-index fallback is permitted."
        )
    resolved_hint = matches[0].get("location_hint")
    resolved_p_idx = resolved_hint.get("p_idx") if isinstance(resolved_hint, Mapping) else None
    if not isinstance(resolved_p_idx, int) or isinstance(resolved_p_idx, bool):
        raise ValueError("TargetRef resolved object has no unique paragraph location.")
    supplied_p_idx = edit.get("p_idx")
    if supplied_p_idx is not None and supplied_p_idx != resolved_p_idx:
        raise ValueError("TargetRef identity conflicts with the PatchOperation paragraph hint.")
    if resolved_p_idx < 0 or resolved_p_idx >= len(paragraphs):
        raise ValueError("TargetRef paragraph location is outside the current Word part.")
    normalized_text = normalize_visible_text(visible_text(paragraphs[resolved_p_idx]))
    normalized_find = normalize_visible_text(str(edit.get("find", "")))
    if not normalized_find or normalized_text.count(normalized_find) != 1:
        raise ValueError("TargetRef paragraph does not contain one unique exact source-text occurrence.")
    if ref.start_offset is not None and ref.end_offset is not None:
        if normalized_text[ref.start_offset:ref.end_offset] != normalized_find:
            raise ValueError("TargetRef character offsets do not identify the PatchOperation source text.")
    return resolved_p_idx, {
        "mode": "stable_target_ref", "status": "verified",
        "document_id": ref.document_id, "revision_id": ref.revision_id,
        "object_id": ref.object_id, "object_type": ref.object_type,
        "object_fingerprint": ref.object_fingerprint, "part": ref.part,
        "p_idx_location_hint": resolved_p_idx,
        "start_offset": ref.start_offset, "end_offset": ref.end_offset,
    }

def _compute_auto_format_map_for_paragraph(
    para: ET.Element,
    old: str,
    new: str,
    *,
    allow_dominant_replace_fallback: bool = False,
) -> list[dict[str, int]] | None:
    """Resolve unique plain-text run block in `para` and compute auto replacement_format_map."""
    from .bounded_safe_span import collect_internal_bookmark_ids, is_transparent_paragraph_child

    tree_root = para.getroottree().getroot() if para.getparent() is not None else para
    internal_bookmark_ids = collect_internal_bookmark_ids(tree_root)
    children = list(para)
    blocks = []
    i = 0
    while i < len(children):
        if children[i].tag != qn("r"):
            i += 1
            continue
        start, runs, texts = i, [], []
        last_run_idx = i
        while i < len(children):
            child = children[i]
            if child.tag == qn("r"):
                value = run_plain_text(child)
                if value is None:
                    break
                runs.append(child)
                texts.append(value)
                last_run_idx = i
                i += 1
            elif runs and is_transparent_paragraph_child(child, internal_bookmark_ids):
                i += 1
            else:
                break
        if runs:
            blocks.append((start, last_run_idx + 1, runs, "".join(texts)))
        else:
            i += 1
    matches = []
    for _start, _end, runs, text in blocks:
        pos = text.find(old)
        while pos >= 0:
            matches.append((runs, pos))
            pos = text.find(old, pos + 1)
    if len(matches) != 1:
        hyperlink_matches = [
            child for child in children
            if child.tag == qn("hyperlink") and old in visible_text(child)
        ]
        if not matches and len(hyperlink_matches) == 1:
            return _compute_auto_format_map_for_paragraph(hyperlink_matches[0], old, new)
        return None
    runs, match_start = matches[0]
    spans, cursor = [], 0
    for run in runs:
        value = run_plain_text(run) or ""
        spans.append((cursor, cursor + len(value), run, value))
        cursor += len(value)
    return build_auto_replacement_format_map(
        spans,
        match_start=match_start,
        old=old,
        new=new,
        allow_dominant_replace_fallback=allow_dominant_replace_fallback,
    )


def prepare_document(input_file: str, output_file: str, edits: list[dict[str, Any]], author: str):
    """Prepare and fully validate a DOCX in memory without writing the output path."""
    source, target = Path(input_file).expanduser().resolve(), Path(output_file).expanduser().resolve()
    if not source.is_file(): raise ValueError(f"Input file does not exist: {source}")
    if source.suffix.lower() != ".docx" or target.suffix.lower() != ".docx": raise ValueError("Input/output must be DOCX.")
    if source == target: raise ValueError("Refusing to overwrite input DOCX; choose a separate output path.")
    if target.exists(): raise ValueError(f"Output already exists; choose a new output path: {target}")
    if not edits: raise ValueError("No edits provided.")
    with zipfile.ZipFile(source) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"Input DOCX ZIP CRC validation failed in {bad_member}.")
        member_names = archive.namelist()
        if len(member_names) != len(set(member_names)):
            duplicates = sorted(
                name for name, count in Counter(member_names).items()
                if count > 1
            )
            raise ValueError("Input DOCX contains duplicate ZIP member names: " + ", ".join(duplicates[:10]))
        original = {name: archive.read(name) for name in member_names}
    if "word/document.xml" not in original: raise ValueError("Missing word/document.xml.")
    from .structure_classifier import (
        StructureClassificationError,
        assert_edit_target_supported_with_bounded_safe_span,
        classify_docx_package_members,
    )
    structure_inventory = classify_docx_package_members(
        original,
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_name=str(source),
    )
    from .manual_suggestion import ManualFallbackRequired, build_manual_suggestion

    # Use the full target-scoped history inventory, including precise visible
    # text intervals. A paragraph-only projection would incorrectly block a
    # second independent edit elsewhere in the same paragraph.
    revision_inventory = build_revision_baseline_inventory(source)
    if structure_inventory["package_status"] != "READY":
        blocking = [
            f"{item['code']}: {item['message']}"
            for item in structure_inventory["issues"]
            if item.get("severity") == "BLOCKED"
        ]
        raise StructureClassificationError(
            "DOCX package structure preflight is blocked: " + "; ".join(blocking[:12])
        )
    groups = defaultdict(list)
    for edit in edits: groups[safe_part(edit.get("part", "word/document.xml"))].append(edit)
    changed, edit_results = {}, []
    rid = 1
    for name, data in original.items():
        if name.startswith("word/") and name.endswith(".xml"):
            try:
                rid = max(rid, next_revision_id(parse_xml(data)))
            except ET.XMLSyntaxError:
                continue
    for part, part_edits in groups.items():
        if part not in original: raise ValueError(f"Word XML part not present: {part}")
        root = parse_xml(original[part])
        paras = root.findall(".//w:body//w:p", NS) if part == "word/document.xml" else root.findall(".//w:p", NS)
        pass1_inserted_paragraphs: set[int] = set()
        for edit in part_edits:
            old, new = str(edit.get("find", "")), str(edit.get("replace", ""))
            if not old.strip(): raise ValueError("Each find value must be non-empty.")
            # Direct engine callers receive no token-change permission by default.
            # The high-level plan approval gate separately requires an explicit
            # Authorized Change and seals it into the approval fingerprint.
            authorization = edit.get("authorized_change") or {
                "intent": "No protected-token changes are authorized by this operation"
            }
            contract = narrow_revision(
                old, new, authorized_change=authorization,
                protected_spans=edit.get("protected_spans", ()),
            )
            if contract["status"] != "PASS":
                raise ValueError(
                    "UNPROTECTED_TOKEN_TOUCHED: edit changes protected scientific/numeric/reference content "
                    "without explicit authorization; no output was written."
                )
            if any(ch in old for ch in "\t\r\n") or any(ch in new for ch in "\t\r\n"):
                raise ValueError("Tabs/newlines are not supported in safe find/replace text.")
            p_idx = edit.get("p_idx")
            target_identity_validation = None
            target_ref = edit.get("target_ref")
            target_identity_mode = edit.get("target_identity_mode")
            if target_ref is not None:
                if target_identity_mode != "stable_target_ref":
                    raise ValueError("TargetRef Apply requires target_identity_mode=stable_target_ref.")
                p_idx, target_identity_validation = _resolve_target_ref_paragraph(
                    source, part, edit, paras
                )
            elif target_identity_mode == "legacy_p_idx_explicit":
                if not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0 or p_idx >= len(paras):
                    raise ValueError("Explicit legacy identity mode requires a valid p_idx location hint.")
                if not isinstance(edit.get("source_sha256"), str) or hashlib.sha256(source.read_bytes()).hexdigest() != edit.get("source_sha256"):
                    raise ValueError("Explicit legacy p_idx mode requires the exact preflight source SHA-256.")
                target_identity_validation = {
                    "mode": "legacy_p_idx_explicit",
                    "status": "migration_compatibility_not_stable_identity",
                    "p_idx_location_hint": p_idx,
                }
            elif edit.get("operation_id"):
                raise ValueError(
                    "Typed PatchOperation has no stable TargetRef or explicit legacy identity mode; refusing implicit p_idx fallback."
                )
            else:
                if p_idx is not None and (
                    not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0
                ):
                    raise ValueError("Legacy untyped P0 operation has an invalid paragraph location hint.")
                target_identity_validation = {
                    "mode": "legacy_untyped_p0",
                    "status": "compatibility_not_stable_identity",
                    "p_idx_location_hint": p_idx,
                    "requires_unique_text_match": True,
                }
            if p_idx is None: candidates = list(enumerate(paras))
            else:
                if not isinstance(p_idx, int) or p_idx < 0 or p_idx >= len(paras):
                    raise ValueError(f"p_idx {p_idx!r} outside {part} paragraph range.")
                candidates = [(p_idx, paras[p_idx])]
            found = [(idx, p) for idx, p in candidates if old in visible_text(p)]
            if len(found) != 1:
                why = "not found" if not found else "ambiguous across paragraphs"
                raise ValueError(f"Target {old[:80]!r} {why} in {part}; no output was written.")
            idx, para = found[0]
            history_target = {"part": part, "p_idx": idx}
            paragraph_text_for_history = visible_text(para)
            target_positions = []
            cursor = 0
            while True:
                match_position = paragraph_text_for_history.find(old, cursor)
                if match_position < 0:
                    break
                target_positions.append(match_position)
                cursor = match_position + max(1, len(old))
            if len(target_positions) == 1:
                history_target["start_offset"] = target_positions[0]
                history_target["end_offset"] = target_positions[0] + len(old)
            history_gate = targeted_revision_gate(
                revision_inventory, [history_target]
            )
            if history_gate["status"] != "PASS":
                conflicts = [
                    f"{item.get('kind')} at {item.get('part')}:{item.get('element_path')}"
                    for item in history_gate["overlapping_revisions"]
                ]
                raise ValueError(
                    "HISTORICAL_REVISION_OVERLAP_USER_DECISION_REQUIRED: target intersects "
                    "existing tracked changes; resolve the overlapping history explicitly in Word "
                    "and re-import before editing. " + "; ".join(conflicts[:8])
                )
            is_pure_format_on_pass1 = (
                old == new
                and bool(edit.get("format_operations"))
                and idx in pass1_inserted_paragraphs
            )
            if is_pure_format_on_pass1:
                structural_event_map = None
                structure_safety = {"status": "SAFE_TWO_PASS_INSERTED_RUN", "passed": True}
            else:
                try:
                    structural_event_map = map_target_text_to_xml_nodes(root, para, old)
                    structure_safety = assert_edit_target_supported_with_bounded_safe_span(
                        structure_inventory,
                        part=part,
                        paragraph_index=idx,
                        paragraph=para,
                        part_root=root,
                        target_text=old,
                        allow_mixed_format=(
                            (old == new and bool(edit.get("format_operations")))
                            or bool(edit.get("replacement_format_map"))
                            or bool(edit.get("allow_format_flatten"))
                        ),
                    )
                except StructureClassificationError as exc:
                    paragraph_text = visible_text(para)
                    position = paragraph_text.find(old)
                    blocking_structures = [
                        {
                            "kind": item.get("kind"),
                            "structure_id": item.get("structure_id"),
                            "word_part": item.get("part"),
                            "element_path": item.get("element_path"),
                            "reason": item.get("reason"),
                            "details": item.get("details", {}),
                        }
                        for item in structure_inventory.get("structures", [])
                        if item.get("part") == part
                        and (
                            item.get("paragraph_index") == idx
                            or idx in (item.get("paragraph_indices") or [])
                        )
                        and item.get("status") in {"BLOCKED", "GUARDED"}
                    ]
                    role = edit.get("document") if edit.get("document") in {"Manuscript", "SI", "Reviewers", "Reply"} else "Manuscript"
                    target_identity = {"paragraph_index": idx}
                    for key in ("object_id", "para_id", "target_ref"):
                        if edit.get(key) is not None:
                            target_identity[key] = edit[key]
                    suggestion = build_manual_suggestion({
                        "document_role": role,
                        "source_document_path": str(source),
                        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "word_part": part,
                        "target": target_identity,
                        "reviewer_comment_ids": [str(value) for value in (edit.get("source_comment_ids") or [])],
                        "author_decision_ref": copy.deepcopy(edit.get("author_decision_ref")),
                        "operation_id": edit.get("operation_id"),
                        "source_excerpt": old,
                        "before_context": paragraph_text[max(0, position - 160):position] if position >= 0 else "",
                        "after_context": paragraph_text[position + len(old):position + len(old) + 160] if position >= 0 else "",
                        "blocking_reason": str(exc),
                        "blocking_structures": blocking_structures,
                        "missing_evidence": list(edit.get("missing_evidence") or []),
                        "proposed_edit": new,
                    })
                    raise ManualFallbackRequired(suggestion, str(exc)) from exc
            format_operations = edit.get("format_operations", [])
            format_authorization = edit.get("format_authorization")
            two_pass = bool(edit.get("two_pass", False) or edit.get("allow_two_pass_overlap", False))
            allow_format_flatten = bool(edit.get("allow_format_flatten", False))
            auto_inherit_format_map = bool(edit.get("auto_inherit_format_map", False))
            if not isinstance(format_operations, list):
                raise ValueError("format_operations must be an array.")
            if format_operations and old != new and not two_pass:
                raise ValueError(
                    "FORMAT_CHANGE_MUST_BE_A_PURE_FORMAT_OPERATION: split text replacement "
                    "and FormatChange into separately approved Patch operations."
                )
            if format_operations:
                if (
                    not isinstance(format_authorization, dict)
                    or not isinstance(format_authorization.get("intent"), str)
                    or not format_authorization.get("intent", "").strip()
                ):
                    raise ValueError(
                        "FORMAT_CHANGE_AUTHORIZATION_REQUIRED: format_operations require "
                        "a separately authorized format_authorization.intent."
                    )
            for format_index, format_operation in enumerate(format_operations):
                if not isinstance(format_operation, dict):
                    raise ValueError(f"format_operations[{format_index}] must be an object.")
                format_find = format_operation.get("find")
                format_properties = format_operation.get("properties")
                if not isinstance(format_find, str) or not format_find:
                    raise ValueError(f"format_operations[{format_index}].find must be non-empty.")
                if not isinstance(format_properties, dict) or not format_properties:
                    raise ValueError(f"format_operations[{format_index}].properties must be a non-empty object.")

            if visible_text(para).count(old) != 1:
                raise ValueError(f"Target {old[:80]!r} is ambiguous within paragraph P[{idx}]; no output was written.")
            replacement_format_map = edit.get("replacement_format_map")
            if replacement_format_map is not None and not isinstance(replacement_format_map, list):
                raise ValueError("replacement_format_map must be an array.")

            if old == new and not format_operations:
                raise ValueError(
                    "NO_OP_TEXT_PATCH: identical find/replace requires at least one authorized FormatChange."
                )

            # Pass 1: Text replacement (when old != new)
            if old != new:
                if replacement_format_map is None and auto_inherit_format_map:
                    replacement_format_map = _compute_auto_format_map_for_paragraph(para, old, new)
                delete_revision_id = rid
                insert_revision_id = rid + 1 if new else None
                ok, rid, reason = replace_in_paragraph(
                    para, old, new, author,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), rid,
                    replacement_format_map=replacement_format_map,
                    allow_format_flatten=allow_format_flatten,
                    auto_inherit_format_map=auto_inherit_format_map,
                )
                if not ok:
                    raise ValueError(
                        f"Safe edit refused at {part} P[{idx}]: {reason}. "
                        "Target may cross a hyperlink boundary or involve a field, drawing, bookmark, content control or other unsupported complex node."
                    )
                pass1_inserted_paragraphs.add(idx)
            else:
                delete_revision_id = None
                insert_revision_id = None
                reason = "format-only"

            # Pass 2: FormatChange (pure FormatChange or two-pass overlap)
            format_change_results = []
            for format_index, format_operation in enumerate(format_operations):
                format_find = format_operation["find"]
                format_properties = format_operation["properties"]
                scope_find = format_operation.get("scope_find")
                scope_offset = format_operation.get("scope_offset")
                format_paragraph_text = visible_text(para)
                if scope_find is not None and scope_offset is not None:
                    if format_paragraph_text.count(scope_find) != 1:
                        raise ValueError(
                            f"FormatChange scope_find {scope_find[:80]!r} is not unique in paragraph P[{idx}]."
                        )
                    scope_pos = format_paragraph_text.find(scope_find)
                    format_offset = scope_pos + scope_offset
                    if format_paragraph_text[format_offset:format_offset + len(format_find)] != format_find:
                        raise ValueError(
                            f"FormatChange scope_offset does not match {format_find[:80]!r} in paragraph P[{idx}]."
                        )
                else:
                    if format_paragraph_text.count(format_find) != 1:
                        raise ValueError(
                            f"FormatChange target {format_find[:80]!r} is not unique in paragraph P[{idx}]."
                        )
                    format_offset = format_paragraph_text.find(format_find)
                format_history_target = {
                    "part": part,
                    "p_idx": idx,
                    "start_offset": format_offset,
                    "end_offset": format_offset + len(format_find),
                }
                format_gate = targeted_revision_gate(
                    revision_inventory, [format_history_target]
                )
                if format_gate["status"] != "PASS":
                    raise ValueError(
                        "HISTORICAL_REVISION_OVERLAP_USER_DECISION_REQUIRED: FormatChange target "
                        "intersects existing revision history."
                    )
                ok_format, rid, change_records, format_reason = apply_format_operation(
                    para, format_find, format_properties,
                    document_xml=data,
                    styles_xml=original.get("word/styles.xml"),
                    theme_xml=next(
                        (payload for member, payload in original.items()
                         if member.startswith("word/theme/") and member.endswith(".xml")),
                        None,
                    ),
                    author=author,
                    date=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    next_revision_id=rid,
                    include_inserted_runs=True,
                    scope_find=scope_find,
                    scope_offset=scope_offset,
                )
                if not ok_format:
                    raise ValueError(
                        f"FormatChange refused at {part} P[{idx}]: {format_reason}"
                    )
                for record in change_records:
                    record["operation_index"] = format_index
                    record["operation_find"] = format_find
                    record["requested_properties"] = copy.deepcopy(format_properties)
                format_change_results.extend(change_records)
            inserted_run_formats = []
            inserted_run_format_signatures = []
            if insert_revision_id is not None:
                insertion = next(
                    (
                        node for node in para.findall(".//w:ins", NS)
                        if node.get(qn("id")) == str(insert_revision_id)
                    ),
                    None
                )
                if insertion is not None:
                    for inserted_run in insertion.findall(".//w:r", NS):
                        run_text = "".join(
                            node.text or "" for node in inserted_run.findall("w:t", NS)
                        )
                        vertical = inserted_run.find("w:rPr/w:vertAlign", NS)
                        rpr = inserted_run.find("w:rPr", NS)
                        bold = rpr.find("w:b", NS) if rpr is not None else None
                        italic = rpr.find("w:i", NS) if rpr is not None else None
                        size = rpr.find("w:sz", NS) if rpr is not None else None
                        fonts = rpr.find("w:rFonts", NS) if rpr is not None else None
                        vertical_value = vertical.get(qn("val")) if vertical is not None else None
                        inserted_run_formats.append({
                            "text": run_text,
                            "vertical_alignment": vertical_value,
                        })
                        inserted_run_format_signatures.append({
                            "text": run_text,
                            "vertical_alignment": vertical_value,
                            "bold": bold is not None and bold.get(qn("val"), "1") not in {"0", "false", "off"},
                            "italic": italic is not None and italic.get(qn("val"), "1") not in {"0", "false", "off"},
                            "font_size_half_points": size.get(qn("val")) if size is not None else None,
                            "fonts": dict(fonts.attrib) if fonts is not None else {},
                            "direct_rpr_sha256": hashlib.sha256(
                                ET.tostring(_format_run_properties(inserted_run), method="c14n")
                                if _format_run_properties(inserted_run) is not None else b""
                            ).hexdigest(),
                        })
            edit_result = {
                "part": part, "p_idx": idx, "find": old, "replace": new,
                "status": "applied", "delete_revision_id": delete_revision_id,
                "insert_revision_id": insert_revision_id,
                "inserted_text": old if old == new and format_operations else normalize_script_characters(new),
                "inserted_run_formats": inserted_run_formats,
                "inserted_run_format_signatures": inserted_run_format_signatures,
                "replacement_format_map": copy.deepcopy(replacement_format_map),
                "format_changes": format_change_results,
                "format_authorization": copy.deepcopy(format_authorization),
                "format_operations": copy.deepcopy(format_operations),
                "structure_safety": copy.deepcopy(structure_safety),
                "structural_event_map": copy.deepcopy(structural_event_map),
                "target_ref": copy.deepcopy(dict(target_ref)) if isinstance(target_ref, Mapping) else None,
                "target_identity_validation": copy.deepcopy(target_identity_validation),
                "authorization_validation": {
                    "status": contract["status"],
                    "diff_algorithm": contract["diff_algorithm"],
                    "authorization_intent": contract["authorization_intent"],
                    "token_lock": contract["token_lock"],
                }
            }
            for metadata_key in ("revision_item_id", "reviewer", "document", "location", "operation_id", "source_comment_ids", "author_decision_ref", "location_phrase"):
                if metadata_key in edit:
                    edit_result[metadata_key] = edit[metadata_key]
            edit_results.append(edit_result)
        changed[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    output = dict(original); output.update(changed)
    if set(output) != set(original): raise ValueError("DOCX package member inventory changed.")
    for name, data in output.items():
        if name.endswith((".xml", ".rels")): parse_xml(data)
        if name not in changed and data != original[name]: raise ValueError(f"Untouched package member changed: {name}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in output.items(): z.writestr(name, data)
    payload = buffer.getvalue()
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        bad = z.testzip()
        if bad: raise ValueError(f"ZIP CRC validation failed: {bad}")
        for name in z.namelist():
            if name.endswith((".xml", ".rels")): parse_xml(z.read(name))
    report = {
        "input_file": str(source), "output_file": str(target),
        "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(payload).hexdigest(),
        "edits_requested": len(edits), "edits_applied": len(edit_results),
        "modified_parts": sorted(changed), "results": edit_results,
        "validation": {"zip_crc": "passed", "xml_well_formed": "passed",
                       "package_member_inventory": "unchanged", "untouched_parts": "byte-identical"}
    }
    return {"target": target, "payload": payload, "report": report}

def write_prepared_document(prepared: dict[str, Any]) -> dict[str, Any]:
    target, payload = prepared["target"], prepared["payload"]
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{target.stem}.", suffix=".tmp", dir=target.parent, delete=False) as f:
            tmp = f.name
            f.write(payload)
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp): os.unlink(tmp)
    return prepared["report"]

def apply_document(input_file: str, output_file: str, edits: list[dict[str, Any]], author: str) -> dict[str, Any]:
    return write_prepared_document(prepare_document(input_file, output_file, edits, author))


class RevisionDocumentEngine:
    """Compatibility facade for the extracted deterministic DOCX engine."""

    parse_xml = staticmethod(parse_xml)
    visible_text = staticmethod(visible_text)
    replace_in_paragraph = staticmethod(replace_in_paragraph)
    apply_format_operation = staticmethod(apply_format_operation)
    prepare_document = staticmethod(prepare_document)
    write_prepared_document = staticmethod(write_prepared_document)
    apply_document = staticmethod(apply_document)
    safe_part = staticmethod(safe_part)
    normalize_script_characters = staticmethod(normalize_script_characters)

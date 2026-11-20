# -*- coding: utf-8 -*-
"""Exact, fail-closed mapping from paragraph text ranges to ordinary Word Runs.

This module only identifies an ALLOW_CANDIDATE range. It does not authorize an
edit; Patch authorization, Token Lock, historical-revision checks and verification
remain mandatory.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from lxml import etree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def _q(local: str) -> str:
    return f"{{{W}}}{local}"


def _run_text(run: ET._Element) -> str | None:
    allowed = {_q("rPr"), _q("t")}
    if any(child.tag not in allowed for child in list(run)):
        return None
    nodes = run.findall("w:t", NS)
    return "".join(node.text or "" for node in nodes) if nodes else None


def _visible_text(paragraph: ET._Element) -> str:
    pieces: list[str] = []
    for node in paragraph.iter():
        if node.tag == _q("t") and node.text:
            pieces.append(node.text)
        elif node.tag == _q("tab"):
            pieces.append("\t")
        elif node.tag == _q("br"):
            pieces.append("\n")
    return "".join(pieces)


def _run_blocks(paragraph: ET._Element) -> list[tuple[list[ET._Element], str]]:
    """Return maximal direct-child sequences of ordinary, text-only Runs."""
    children = list(paragraph)
    blocks: list[tuple[list[ET._Element], str]] = []
    index = 0
    while index < len(children):
        if children[index].tag != _q("r"):
            index += 1
            continue
        runs: list[ET._Element] = []
        texts: list[str] = []
        while index < len(children) and children[index].tag == _q("r"):
            value = _run_text(children[index])
            if value is None:
                break
            runs.append(children[index])
            texts.append(value)
            index += 1
        if runs:
            blocks.append((runs, "".join(texts)))
        else:
            index += 1
    return blocks


def _part_text_positions(root: ET._Element) -> tuple[dict[ET._Element, int], dict[str, dict[str, list[int]]]]:
    """Map paragraphs and bookmark markers to offsets in part-wide visible text."""
    paragraph_bases: dict[ET._Element, int] = {}
    markers: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"start": [], "end": []})
    cursor = 0
    paragraph_count = 0

    for node in root.iter():
        if node.tag == _q("p"):
            if paragraph_count:
                cursor += 1  # separator; it cannot be part of a safe replacement
            paragraph_bases[node] = cursor
            paragraph_count += 1
        elif node.tag == _q("bookmarkStart"):
            marker_id = node.get(_q("id"), "")
            markers[marker_id]["start"].append(cursor)
        elif node.tag == _q("bookmarkEnd"):
            marker_id = node.get(_q("id"), "")
            markers[marker_id]["end"].append(cursor)
        elif node.tag == _q("t") and node.text:
            cursor += len(node.text)
        elif node.tag in {_q("tab"), _q("br")}:
            cursor += 1

    return paragraph_bases, markers


def analyze_bounded_safe_span(
    root: ET._Element,
    paragraph: ET._Element,
    target_text: str,
) -> dict[str, Any]:
    """Locate one target in a single ordinary Run block and prove it avoids bookmarks."""
    if not isinstance(root, ET._Element) or not isinstance(paragraph, ET._Element):
        raise TypeError("root and paragraph must be lxml elements.")
    if not isinstance(target_text, str) or not target_text or any(ch in target_text for ch in "\t\r\n"):
        raise ValueError("target_text must be non-empty and cannot contain tabs or paragraph breaks.")

    visible = _visible_text(paragraph)
    if visible.count(target_text) != 1:
        return {
            "status": "BLOCKED",
            "allowed": False,
            "reason": "Target is not unique in the paragraph's visible text.",
        }

    matches: list[tuple[list[ET._Element], str, int]] = []
    for runs, joined in _run_blocks(paragraph):
        position = joined.find(target_text)
        while position >= 0:
            matches.append((runs, joined, position))
            position = joined.find(target_text, position + 1)
    if len(matches) != 1:
        return {
            "status": "BLOCKED",
            "allowed": False,
            "reason": (
                "Target does not map to exactly one contiguous ordinary Run sequence; "
                "it may cross a protected node or sit inside a nested structure."
            ),
        }

    paragraph_bases, marker_map = _part_text_positions(root)
    base = paragraph_bases.get(paragraph)
    if base is None:
        return {
            "status": "BLOCKED",
            "allowed": False,
            "reason": "Paragraph is not part of the supplied WordprocessingML part tree.",
        }

    local_start = visible.find(target_text)
    target_start = base + local_start
    target_end = target_start + len(target_text)
    protected_ranges: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []

    for marker_id, pair in sorted(marker_map.items()):
        starts, ends = pair["start"], pair["end"]
        if not marker_id or len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
            return {
                "status": "BLOCKED",
                "allowed": False,
                "reason": (
                    f"Bookmark marker group {marker_id!r} is unpaired, duplicated, empty-ID, "
                    "or out of document order; no safe span is inferred."
                ),
                "range_id": marker_id,
                "start_count": len(starts),
                "end_count": len(ends),
            }

        range_start, range_end = starts[0], ends[0]
        boundaries.append({"range_id": marker_id, "start": range_start, "end": range_end})
        overlaps = target_start < range_end and target_end > range_start
        crosses_collapsed_boundary = (
            range_start == range_end and target_start < range_start < target_end
        )
        if overlaps or crosses_collapsed_boundary:
            protected_ranges.append({
                "range_id": marker_id,
                "start": range_start,
                "end": range_end,
            })

    if protected_ranges:
        return {
            "status": "BLOCKED",
            "allowed": False,
            "reason": "The exact target interval overlaps or crosses a protected bookmark range.",
            "target_start": target_start,
            "target_end": target_end,
            "protected_ranges": protected_ranges,
            "bookmark_boundaries": boundaries,
        }

    runs, joined, run_local_start = matches[0]
    run_map: list[dict[str, Any]] = []
    xml_document_order = {node: index for index, node in enumerate(root.iter())}
    paragraph_child_positions = {
        child: index for index, child in enumerate(paragraph)
    }
    cursor = 0
    for run_index, run in enumerate(runs):
        value = _run_text(run) or ""
        left, right = cursor, cursor + len(value)
        overlap_start = max(run_local_start, left)
        overlap_end = min(run_local_start + len(target_text), right)
        if overlap_start < overlap_end:
            run_start = overlap_start - left
            run_end = overlap_end - left
            text_nodes: list[dict[str, Any]] = []
            text_cursor = 0
            for child_index, child in enumerate(run):
                if child.tag != _q("t"):
                    continue
                child_text = child.text or ""
                child_start = text_cursor
                child_end = child_start + len(child_text)
                text_overlap_start = max(run_start, child_start)
                text_overlap_end = min(run_end, child_end)
                if text_overlap_start < text_overlap_end:
                    text_nodes.append({
                        "text_node_index_in_run": child_index,
                        "text_node_document_order": xml_document_order[child],
                        "text_node_text_start": text_overlap_start - child_start,
                        "text_node_text_end": text_overlap_end - child_start,
                        "run_text_start": child_start,
                        "run_text_end": child_end,
                        "target_text": child_text[
                            text_overlap_start - child_start:text_overlap_end - child_start
                        ],
                    })
                text_cursor = child_end
            run_map.append({
                "run_index_in_safe_block": run_index,
                "paragraph_child_index": paragraph_child_positions[run],
                "run_document_order": xml_document_order[run],
                "paragraph_document_order": xml_document_order[paragraph],
                "run_text_start": run_start,
                "run_text_end": run_end,
                "target_text": value[run_start:run_end],
                "text_nodes": text_nodes,
            })
        cursor = right

    mapped_run_indices = sorted({
        row["run_index_in_safe_block"] for row in run_map
    })
    run_property_signatures = set()
    for run_index in mapped_run_indices:
        rpr = runs[run_index].find("w:rPr", NS)
        run_property_signatures.add(
            ET.tostring(rpr, encoding="utf-8") if rpr is not None else b""
        )
    if len(run_property_signatures) > 1:
        return {
            "status": "BLOCKED",
            "allowed": False,
            "reason": (
                "Target crosses ordinary Runs with different direct formatting; "
                "the current bounded-span writer does not infer a mixed-format replacement map."
            ),
            "target_start": target_start,
            "target_end": target_end,
            "run_map": run_map,
            "format_signature_count": len(run_property_signatures),
        }

    return {
        "status": "ALLOW_CANDIDATE",
        "allowed": True,
        "method": "bounded_safe_span_v1",
        "target_text": target_text,
        "target_start": target_start,
        "target_end": target_end,
        "paragraph_text_start": local_start,
        "run_map": run_map,
        "bookmark_boundaries": boundaries,
        "authorization_note": (
            "Structural permission only; Authorized Change, Token Lock, Protected Content, "
            "formatting, Patch approval and post-Apply verification remain mandatory."
        ),
    }

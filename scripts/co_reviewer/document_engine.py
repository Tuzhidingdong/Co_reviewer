# -*- coding: utf-8 -*-
"""WordprocessingML primitives and conservative native Track Changes engine.

This module is extracted from the v0.3 CLI without changing its edit semantics.
The CLI remains a compatibility facade while the engine boundary becomes testable.
"""
from __future__ import annotations

import copy
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
from typing import Any

from lxml import etree as ET
from .v232_contract import (
    attach_format_change, effective_run_properties, narrow_revision,
    targeted_revision_gate,
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
    allowed = {qn("rPr"), qn("t")}
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
    allowed = boolean_names | set(value_names) | {"sz", "szCs", "color", "position", "spacing", "rFonts"}
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
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"FormatChange {name} requires a positive integer OOXML value.")
            _set_run_property(run, name, str(value))
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
) -> tuple[bool, int, list[dict[str, Any]], str]:
    """Apply one explicitly authorized FormatChange to existing ordinary text Runs."""
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
    blocks = []
    index = 0
    while index < len(children):
        if children[index].tag != qn("r"):
            index += 1
            continue
        start, runs, texts = index, [], []
        while index < len(children) and children[index].tag == qn("r"):
            value = run_plain_text(children[index])
            if value is None:
                break
            runs.append(children[index])
            texts.append(value)
            index += 1
        if runs:
            blocks.append((start, start + len(runs), runs, "".join(texts)))
        else:
            index += 1

    matches = []
    for start, end, runs, text in blocks:
        position = text.find(find_text)
        while position >= 0:
            matches.append((start, end, runs, text, position))
            position = text.find(find_text, position + 1)
    if not matches:
        return False, next_revision_id, [], "FormatChange target not found in an ordinary Run sequence."
    if len(matches) != 1:
        return False, next_revision_id, [], "FormatChange target is ambiguous."
    start, end, runs, joined, match_start = matches[0]
    match_end = match_start + len(find_text)
    spans, cursor = [], 0
    for run in runs:
        value = run_plain_text(run) or ""
        spans.append((cursor, cursor + len(value), run, value))
        cursor += len(value)

    replacements: list[ET.Element] = []
    changes: list[dict[str, Any]] = []
    for run_start, run_end, source_run, value in spans:
        overlap_start = max(match_start, run_start)
        overlap_end = min(match_end, run_end)
        if overlap_start >= overlap_end:
            replacements.append(copy.deepcopy(source_run))
            continue
        prefix = value[:overlap_start - run_start]
        selected = value[overlap_start - run_start:overlap_end - run_start]
        suffix = value[overlap_end - run_start:]
        prefix_run = make_run(source_run, prefix)
        if prefix_run is not None:
            replacements.append(prefix_run)
        selected_run = make_run(source_run, selected)
        if selected_run is None:
            return False, next_revision_id, [], "FormatChange selected Run could not be cloned."
        if selected_run.find("w:rPr/w:rPrChange", NS) is not None:
            return False, next_revision_id, [], "FORMAT_CHANGE_EXISTING_HISTORY_REQUIRES_RESOLUTION"
        run_rpr = source_run.find("w:rPr", NS)
        run_style = run_rpr.find("w:rStyle", NS) if run_rpr is not None else None
        run_style_id = run_style.get(qn("val")) if run_style is not None else None
        effective_before = effective_run_properties(
            document_xml, styles_xml, paragraph_style_id, run_style_id,
            run_rpr, paragraph_rpr=paragraph_rpr, theme_xml=theme_xml,
        )
        revision_id = next_revision_id
        attach_format_change(
            selected_run, effective_before, list(properties),
            revision_id, author, date,
        )
        _apply_format_properties(selected_run, properties)
        replacements.append(selected_run)
        changes.append({
            "find": selected,
            "revision_id": revision_id,
            "properties": copy.deepcopy(properties),
            "old_effective_properties": copy.deepcopy(effective_before["properties"]),
        })
        next_revision_id += 1
        suffix_run = make_run(source_run, suffix)
        if suffix_run is not None:
            replacements.append(suffix_run)

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
):
    children = list(para)
    blocks = []
    i = 0
    while i < len(children):
        if children[i].tag != qn("r"):
            i += 1
            continue
        start, runs, texts = i, [], []
        while i < len(children) and children[i].tag == qn("r"):
            value = run_plain_text(children[i])
            if value is None:
                break
            runs.append(children[i]); texts.append(value); i += 1
        if runs:
            blocks.append((start, start + len(runs), runs, "".join(texts)))
        else:
            i += 1
    matches = []
    for start, end, runs, text in blocks:
        pos = text.find(old)
        while pos >= 0:
            matches.append((start, end, runs, text, pos))
            pos = text.find(old, pos + 1)
    if not matches:
        # A target may be wholly contained in one simple hyperlink. Recurse into
        # that wrapper so the relationship ID and hyperlink attributes survive.
        hyperlink_matches = [
            child for child in children
            if child.tag == qn("hyperlink") and old in visible_text(child)
        ]
        if len(hyperlink_matches) == 1:
            return replace_in_paragraph(
                hyperlink_matches[0], old, new, author, date, next_id,
                replacement_format_map=replacement_format_map,
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
            frag = make_run(source, deleted_text, deleted=True)
            if frag is not None: deletions.append(frag)
        frag = make_run(source, suffix_text)
        if frag is not None: suffixes.append(frag)
    if first_matched is None:
        return False, next_id, "target could not be mapped to runs"
    if replacement_format_map is not None:
        try:
            insert_runs = _build_mapped_insert_runs(
                spans, match_start=match_start, source_length=len(old),
                replacement=new, format_map=replacement_format_map,
            )
        except ValueError as exc:
            return False, next_id, str(exc)
    elif len(set(matched_format_signatures)) > 1:
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
    replacements = list(prefixes)
    if deletions:
        replacements.append(revision_wrapper("del", next_id, author, date, deletions)); next_id += 1
    if insert_runs:
        replacements.append(revision_wrapper("ins", next_id, author, date, insert_runs)); next_id += 1
    replacements.extend(suffixes)
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

    revision_kinds = {
        "text_revision_wrapper", "format_revision", "paragraph_revision",
        "table_revision", "move_revision", "comment_anchor", "comment_definition",
    }
    revision_inventory = {
        "revisions": [
            item for item in structure_inventory.get("structures", [])
            if item.get("kind") in revision_kinds or "revision" in str(item.get("kind", ""))
        ]
    }
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
            history_gate = targeted_revision_gate(
                revision_inventory, [{"part": part, "p_idx": idx}]
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
            try:
                structure_safety = assert_edit_target_supported_with_bounded_safe_span(
                    structure_inventory,
                    part=part,
                    paragraph_index=idx,
                    paragraph=para,
                    part_root=root,
                    target_text=old,
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
                    "reviewer_comment_ids": [str(value) for value in edit.get("source_comment_ids", [])],
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
            if not isinstance(format_operations, list):
                raise ValueError("format_operations must be an array.")
            if format_operations and old != new:
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
            format_change_results = []
            for format_index, format_operation in enumerate(format_operations):
                if not isinstance(format_operation, dict):
                    raise ValueError(f"format_operations[{format_index}] must be an object.")
                format_find = format_operation.get("find")
                format_properties = format_operation.get("properties")
                if not isinstance(format_find, str) or not format_find:
                    raise ValueError(f"format_operations[{format_index}].find must be non-empty.")
                if not isinstance(format_properties, dict) or not format_properties:
                    raise ValueError(f"format_operations[{format_index}].properties must be a non-empty object.")
                if old in format_find or format_find in old:
                    raise ValueError(
                        "FORMAT_CHANGE_OVERLAPS_TEXT_REPLACEMENT: format the retained source text "
                        "or map replacement formatting, but do not format characters being deleted."
                    )
                if visible_text(para).count(format_find) != 1:
                    raise ValueError(
                        f"FormatChange target {format_find[:80]!r} is not unique in paragraph P[{idx}]."
                    )
                format_gate = targeted_revision_gate(
                    revision_inventory, [{"part": part, "p_idx": idx}]
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
            if visible_text(para).count(old) != 1:
                raise ValueError(f"Target {old[:80]!r} is ambiguous within paragraph P[{idx}]; no output was written.")
            replacement_format_map = edit.get("replacement_format_map")
            if replacement_format_map is not None and not isinstance(replacement_format_map, list):
                raise ValueError("replacement_format_map must be an array.")
            if old == new and format_operations:
                # Explicit no-text-diff mode: the Patch is a pure FormatChange.
                # Do not generate a redundant delete/insert pair for unchanged text.
                delete_revision_id = None
                insert_revision_id = None
                reason = "format-only"
            else:
                if old == new:
                    raise ValueError(
                        "NO_OP_TEXT_PATCH: identical find/replace requires at least one authorized FormatChange."
                    )
                delete_revision_id = rid
                insert_revision_id = rid + 1 if new else None
                ok, rid, reason = replace_in_paragraph(
                    para, old, new, author,
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), rid,
                    replacement_format_map=replacement_format_map,
                )
                if not ok:
                    raise ValueError(
                        f"Safe edit refused at {part} P[{idx}]: {reason}. "
                        "Target may cross a hyperlink boundary or involve a field, drawing, bookmark, content control or other unsupported complex node."
                    )
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
                "authorization_validation": {
                    "status": contract["status"],
                    "diff_algorithm": contract["diff_algorithm"],
                    "authorization_intent": contract["authorization_intent"],
                    "token_lock": contract["token_lock"],
                }
            }
            for metadata_key in ("revision_item_id", "reviewer", "document", "location", "operation_id", "source_comment_ids", "location_phrase"):
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

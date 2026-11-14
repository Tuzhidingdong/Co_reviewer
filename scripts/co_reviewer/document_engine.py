# -*- coding: utf-8 -*-
"""WordprocessingML primitives and conservative native Track Changes engine.

This module is extracted from the v0.3 CLI without changing its edit semantics.
The CLI remains a compatibility facade while the engine boundary becomes testable.
"""
from __future__ import annotations

import copy
import io
import os
import re
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree as ET

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

def make_run(source: ET.Element, text: str, deleted: bool = False) -> ET.Element | None:
    if not text:
        return None
    run = ET.Element(qn("r"))
    for key, value in source.attrib.items():
        run.set(key, value)
    props = source.find("w:rPr", NS)
    if props is not None:
        run.append(copy.deepcopy(props))
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
        run = make_run(source, segment_text)
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

def replace_in_paragraph(para: ET.Element, old: str, new: str, author: str, date: str, next_id: int):
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
                hyperlink_matches[0], old, new, author, date, next_id
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
    for run_start, run_end, source, value in spans:
        overlap_start = max(match_start, run_start)
        overlap_end = min(match_end, run_end)
        if overlap_start < overlap_end:
            prefix_text = value[:overlap_start - run_start]
            deleted_text = value[overlap_start - run_start:overlap_end - run_start]
            suffix_text = value[overlap_end - run_start:]
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
        original = {name: archive.read(name) for name in archive.namelist()}
    if "word/document.xml" not in original: raise ValueError("Missing word/document.xml.")
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
            if visible_text(para).count(old) != 1:
                raise ValueError(f"Target {old[:80]!r} is ambiguous within paragraph P[{idx}]; no output was written.")
            delete_revision_id = rid
            insert_revision_id = rid + 1 if new else None
            ok, rid, reason = replace_in_paragraph(
                para, old, new, author, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), rid
            )
            if not ok:
                raise ValueError(
                    f"Safe edit refused at {part} P[{idx}]: {reason}. "
                    "Target may cross a hyperlink boundary or involve a field, drawing, bookmark, content control or other unsupported complex node."
                )
            inserted_run_formats = []
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
                        inserted_run_formats.append({
                            "text": run_text,
                            "vertical_alignment": (
                                vertical.get(qn("val")) if vertical is not None else None
                            )
                        })
            edit_result = {
                "part": part, "p_idx": idx, "find": old, "replace": new,
                "status": "applied", "delete_revision_id": delete_revision_id,
                "insert_revision_id": insert_revision_id,
                "inserted_text": normalize_script_characters(new),
                "inserted_run_formats": inserted_run_formats
            }
            for metadata_key in ("revision_item_id", "reviewer", "document", "location"):
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
    prepare_document = staticmethod(prepare_document)
    write_prepared_document = staticmethod(write_prepared_document)
    apply_document = staticmethod(apply_document)
    safe_part = staticmethod(safe_part)
    normalize_script_characters = staticmethod(normalize_script_characters)

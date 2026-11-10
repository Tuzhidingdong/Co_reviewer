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
        "captions_and_drawings": [], "errors": []
    }
    if not p.is_file():
        report["errors"].append("File does not exist.")
        return report
    if p.suffix.lower() != ".docx":
        report["errors"].append("Only .docx files are supported.")
        return report
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            report["media_count"] = sum(n.startswith("word/media/") and not n.endswith("/") for n in names)
            comments = {}
            if "word/comments.xml" in names:
                croot = parse_xml(z.read("word/comments.xml"))
                for c in croot.findall(".//w:comment", NS):
                    cid = c.get(qn("id"), "")
                    comments[cid] = {
                        "author": c.get(qn("author"), ""),
                        "text": "".join(x.text or "" for x in c.findall(".//w:t", NS)).strip()
                    }
            parts = []
            for n in names:
                if not n.startswith("word/") or not n.endswith(".xml"):
                    continue
                if n == "word/document.xml" or re.fullmatch(r"word/(?:header|footer)\d+\.xml", n) or n in {
                    "word/footnotes.xml", "word/endnotes.xml"
                }:
                    parts.append(n)
            parts.sort(key=lambda x: (x != "word/document.xml", x))
            for part in parts:
                try:
                    root = parse_xml(z.read(part))
                except ET.XMLSyntaxError as exc:
                    report["errors"].append(f"{part}: invalid XML: {exc}")
                    continue
                report["tracked_insertions"] += len(root.findall(".//w:ins", NS))
                report["tracked_deletions"] += len(root.findall(".//w:del", NS))
                paragraphs = root.findall(".//w:p", NS)
                for idx, para in enumerate(paragraphs):
                    txt = visible_text(para).strip()
                    report["paragraph_count"] += 1
                    if txt:
                        report["paragraphs"].append({"part": part, "p_idx": idx, "text": txt})
                    cids = {x.get(qn("id"), "") for x in para.findall(".//w:commentRangeStart", NS)}
                    cids.update(x.get(qn("id"), "") for x in para.findall(".//w:commentReference", NS))
                    for cid in sorted(cids):
                        c = comments.get(cid)
                        if c and (not comment_author or comment_author.casefold() in c["author"].casefold()):
                            report["comments"].append({
                                "part": part, "p_idx": idx, "id": cid, "author": c["author"],
                                "comment": c["text"], "anchor_paragraph": txt
                            })
                    flags = sorted(set(PLACEHOLDER_RE.findall(txt)))
                    if flags:
                        report["placeholders"].append({"part": part, "p_idx": idx, "matches": flags, "text": txt})
                    if txt and CHINESE_RE.search(txt):
                        report["chinese_notes"].append({"part": part, "p_idx": idx, "text": txt})
                    if CAPTION_RE.match(txt) or has_drawing(para):
                        report["captions_and_drawings"].append({
                            "part": part, "p_idx": idx, "has_drawing": has_drawing(para), "text": txt[:240]
                        })
                if part == "word/document.xml":
                    report["table_count"] = len(root.findall(".//w:tbl", NS))
    except (OSError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        report["errors"].append(f"Unable to inspect DOCX: {exc}")
    return report

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
            if len(r["paragraphs"]) > max_items:
                lines.append(f"- ... {len(r['paragraphs']) - max_items} more paragraphs in JSON inventory.")
        for c in r["comments"][:max_items]:
            lines.append(f"- [{c['part']} P[{c['p_idx']}] Comment #{c['id']} / {c['author']}]: {c['comment']}")
            lines.append(f"  - Anchored text: {c['anchor_paragraph']}")
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
    Path(md_path).write_text("\n".join(lines), encoding="utf-8")
    if json_path:
        Path(json_path).write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")

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
    insert_run = make_run(first_matched, new)
    replacements = list(prefixes)
    if deletions:
        replacements.append(revision_wrapper("del", next_id, author, date, deletions)); next_id += 1
    if insert_run is not None:
        replacements.append(revision_wrapper("ins", next_id, author, date, [insert_run])); next_id += 1
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
            edit_result = {
                "part": part, "p_idx": idx, "find": old, "replace": new,
                "status": "applied", "delete_revision_id": delete_revision_id,
                "insert_revision_id": insert_revision_id
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

def revision_item_fingerprint(item: dict[str, Any]) -> str:
    """Fingerprint the exact reviewer-facing approval scope, excluding lifecycle metadata."""
    payload = {
        "id": item.get("id"),
        "reviewer": item.get("reviewer"),
        "comment": item.get("comment"),
        "proposed_response": item.get("proposed_response", ""),
        "targets": item.get("targets", []),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def validate_plan(plan: Any, require_approval: bool = True, check_approval_integrity: bool = True) -> list[str]:
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
    return errors

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
                "document": target["document"], "location": target.get("location", "")
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
    source_hashes_before = {
        key: sha256_file(path) for key, path in source_paths.items()
    }

    prepared = []
    for (inp, out), edits in grouped.items():
        prepared.append(prepare_document(inp, out, edits, author))

    source_hashes_after = {
        key: sha256_file(path) for key, path in source_paths.items()
    }
    if source_hashes_before != source_hashes_after:
        raise ValueError("A source DOCX changed during preflight; no source fingerprints were recorded. Retry preflight.")

    changed_items = []
    for item, target in target_refs:
        key = _canonical_artifact_path(target["input_file"])
        new_hash = source_hashes_after[key]
        previous_hash = target.get("source_sha256")
        if item.get("status") == "approved" and previous_hash != new_hash:
            changed_items.append(item["id"])
        target["source_sha256"] = new_hash

    document_reports = []
    for entry in prepared:
        document_report = copy.deepcopy(entry["report"])
        document_report["status"] = "preflighted"
        document_report["edits_prepared"] = document_report.pop("edits_applied", 0)
        for result in document_report.get("results", []):
            result["status"] = "preflighted"
            result.pop("delete_revision_id", None)
            result.pop("insert_revision_id", None)
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
        "documents": document_reports,
        "results": results
    }


def apply_plan(plan: dict[str, Any], author: str) -> list[dict[str, Any]]:
    errors = validate_plan(plan, True)
    if errors: raise ValueError("Revision plan validation failed:\n- " + "\n- ".join(errors))
    approved_items = [item for item in plan["revision_items"] if item.get("status") == "approved"]
    if not approved_items:
        raise ValueError("No revision items are currently approved for application.")
    grouped = defaultdict(list)
    for item in approved_items:
        for target in item["targets"]:
            edit = {
                "find": target["find"], "replace": target["replace"],
                "part": target.get("part", "word/document.xml"),
                "revision_item_id": item["id"], "reviewer": item["reviewer"],
                "document": target["document"], "location": target.get("location", "")
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
    return [write_prepared_document(item) for item in prepared]


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
                new_text = target["replace"]
                new_ok = (
                    len(insert_nodes) == 1 and inserted == new_text
                    if new_text else insert_id is None and not inserted
                )
                result["checks"] = {
                    "output_exists": True, "zip_crc": "passed", "paragraph_found": True,
                    "delete_revision_id": delete_id, "insert_revision_id": insert_id,
                    "exact_deletion_match": old_ok, "exact_insertion_match": new_ok
                }
                result["p_idx"] = p_idx
                if old_ok and new_ok:
                    result["status"] = "verified"
                else:
                    result["reason"] = "Expected deleted or inserted text was not found in the target paragraph."
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
                result["reason"] = str(exc)
            results.append(result)

    passed = sum(1 for item in results if item["status"] == "verified")
    return {
        "scope": "Mechanical DOCX Track Changes verification only; scientific correctness and cross-document semantic consistency require separate review.",
        "targets_total": len(results), "targets_verified": passed,
        "targets_failed": len(results) - passed, "passed": bool(results) and passed == len(results),
        "results": results
    }

def render_response_letter(plan: dict[str, Any]) -> str:
    """Render a Response Letter draft from response text already supplied in the plan."""
    items = plan.get("revision_items", [])
    counts = defaultdict(int)
    for item in items:
        counts[item.get("status", "planned")] += 1
    lines = [
        "# Response to Reviewers", "",
        "> Working draft generated from the current revision plan. No scientific claims or response text are invented.",
        f"> Revision items: {len(items)} | Verified: {counts['verified']} | Applied but not verified: {counts['applied']} | Other/open: {len(items) - counts['verified'] - counts['applied']}",
        ""
    ]
    for item in items:
        item_id = item.get("id", "UNASSIGNED")
        reviewer = item.get("reviewer", "Reviewer")
        status = item.get("status", "planned")
        lines.extend([f"## {reviewer} — {item_id}", "", "**Comment**", "", str(item.get("comment", "")).strip(), ""])
        response = str(item.get("proposed_response", "")).strip()
        if status == "verified":
            lines.extend(["**Response**", "", response or "[Response text required; not generated automatically.]", ""])
        else:
            lines.extend([
                f"**Proposed response — DRAFT ({status}; not yet verified)**", "",
                response or "[Response text required; Co_Reviewer will not invent a reply.]",
                "",
                "> Do not use completion claims such as “we have revised” until the corresponding document changes have been verified.",
                ""
            ])
        lines.extend(["**Linked document changes**", ""])
        for target in item.get("targets", []):
            location = target.get("location") or "location not specified"
            output_name = Path(target.get("output_file", "")).name or "output file not specified"
            lines.append(f"- **{target.get('document', 'Document')}** — {location} (output: {output_name})")
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip() + "\n"

def cmd_inspect(args):
    files = [("Reviewers", args.rev), ("Manuscript", args.man), ("SI", args.si)]
    selected = [(role, path) for role, path in files if path]
    if not selected: raise ValueError("Provide at least one of --rev, --man or --si.")
    reports = []
    for role, path in selected:
        result = inspect_docx(path, args.comment_author); result["role"] = role; reports.append(result)
    write_reports(reports, args.out, args.json_out or None, max(1, args.max_items_per_section))
    count_errors = sum(len(x["errors"]) for x in reports)
    print(f"[OK] Markdown: {args.out}")
    if args.json_out: print(f"[OK] JSON: {args.json_out}")
    print(f"[SUMMARY] {len(reports)} document(s); {sum(x['paragraph_count'] for x in reports)} paragraphs; {sum(len(x['comments']) for x in reports)} comments; {count_errors} error(s)")
    return 1 if count_errors else 0

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

def cmd_preflight(args):
    plan_path = Path(args.plan)
    report_path = Path(args.out)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_apply_artifact_paths(plan, plan_path, report_path)
    report = preflight_plan(plan, args.author)

    # Persist the input-version fingerprints first. If report persistence then
    # fails, the plan remains safe but the user can simply rerun preflight.
    write_json_atomic(plan_path, plan)
    write_json_atomic(report_path, report)
    print(f"[PREFLIGHT] {report['targets_preflighted']}/{report['targets_total']} target(s) are safe to apply.")
    print(f"[SOURCE] SHA-256 fingerprints recorded for {len(report['source_files'])} source DOCX file(s).")
    if report["approval_scope_changed_items"]:
        print("[REAPPROVAL] Source versions changed for already-approved item(s): " + ", ".join(report["approval_scope_changed_items"]))
    print(f"[OK] report: {report_path}")
    print("[NOTE] Preflight does not edit DOCX files or grant approval.")
    return 0


def cmd_approve_plan(args):
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
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
        item["status"] = "approved"
        item["approval_fingerprint"] = revision_item_fingerprint(item)
    plan["approved"] = True
    write_json_atomic(plan_path, plan)
    print(f"[APPROVED] {len(requested)} revision item(s) sealed: {', '.join(requested)}")
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


def cmd_apply(args):
    plan_path = Path(args.plan)
    report_path = Path(args.report)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    validate_apply_artifact_paths(plan, plan_path, report_path)

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
    write_json_atomic(report_path, report_history)
    applied_ids = {
        edit.get("revision_item_id")
        for report in reports
        for edit in report.get("results", [])
        if edit.get("status") == "applied"
    }
    for item in plan["revision_items"]:
        if item.get("status") == "approved" and item.get("id") in applied_ids:
            item["status"] = "applied"
    write_json_atomic(plan_path, plan)
    for r in reports: print(f"[DONE] {r['output_file']}: {r['edits_applied']}/{r['edits_requested']} edits")
    print(f"[STATE] Plan updated: {len(applied_ids)} revision item(s) marked applied.")
    print(f"[OK] report: {args.report}")
    return 0


def cmd_verify(args):
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    apply_reports = json.loads(Path(args.apply_report).read_text(encoding="utf-8"))
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
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[VERIFY] {report['targets_verified']}/{report['targets_total']} targets verified; {report['targets_failed']} failed.")
    print("[SCOPE] Mechanical Track Changes only; scientific meaning and cross-document consistency still require review.")
    print(f"[OK] report: {args.out}")
    print("[NEXT] Mechanical verification is not semantic approval; review scientific consistency before marking items verified.")
    return 0 if report["passed"] else 2

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

def cmd_response(args):
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
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    errors = validate_plan(plan, require_approval=False)
    if errors:
        raise ValueError("Cannot render response letter from invalid plan:\n- " + "\n- ".join(errors))
    try:
        from response_letter_docx import render_response_docx
    except ImportError as exc:
        raise ValueError("DOCX response output requires python-docx; install project requirements.") from exc
    output = render_response_docx(
        plan, args.out, manuscript_title=args.manuscript_title, author=args.author
    )
    verified = sum(1 for item in plan["revision_items"] if item.get("status") == "verified")
    print(f"[OK] Response Letter DOCX: {output}")
    print(f"[SUMMARY] {len(plan['revision_items'])} item(s); {verified} marked verified. Draft labels are retained for all unfinished items.")
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
    i.set_defaults(func=cmd_inspect)
    v = sub.add_parser("validate-plan", help="Validate a plan without editing files.")
    v.add_argument("--plan", required=True); v.add_argument("--allow-unapproved", action="store_true")
    v.set_defaults(func=cmd_validate)
    pf = sub.add_parser("preflight-plan", help="Check revision targets and bind approval to source DOCX versions without editing files.")
    pf.add_argument("--plan", required=True)
    pf.add_argument("--out", default="preflight_report.json")
    pf.add_argument("--author", default="Co_Reviewer Preflight")
    pf.set_defaults(func=cmd_preflight)
    ap = sub.add_parser("approve-plan", help="Record explicit user approval for selected revision items.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--ids", nargs="+", required=True, help="Revision item IDs explicitly approved by the user.")
    ap.set_defaults(func=cmd_approve_plan)
    a = sub.add_parser("apply", help="Apply an approved plan to separate DOCX output files.")
    a.add_argument("--plan", required=True); a.add_argument("--author", default="Co_Reviewer")
    a.add_argument("--report", default="revision_apply_report.json"); a.set_defaults(func=cmd_apply)
    vfy = sub.add_parser("verify", help="Verify actual Word Track Changes against the approved plan.")
    vfy.add_argument("--plan", required=True)
    vfy.add_argument("--apply-report", default="revision_apply_report.json")
    vfy.add_argument("--out", default="revision_verification.json")
    vfy.set_defaults(func=cmd_verify)
    rsp = sub.add_parser("render-response", help="Render a conservative Response Letter draft from revision items.")
    rsp.add_argument("--plan", required=True)
    rsp.add_argument("--out", default="Response_to_Reviewers.md")
    rsp.set_defaults(func=cmd_response)
    rspdocx = sub.add_parser("render-response-docx", help="Render the revision plan as a formatted Word Response Letter.")
    rspdocx.add_argument("--plan", required=True)
    rspdocx.add_argument("--out", default="Response_to_Reviewers.docx")
    rspdocx.add_argument("--manuscript-title", default="")
    rspdocx.add_argument("--author", default="")
    rspdocx.set_defaults(func=cmd_response_docx)
    st = sub.add_parser("status", help="Summarize revision item states without editing documents.")
    st.add_argument("--plan", required=True)
    st.set_defaults(func=cmd_status)
    return p

def main(argv=None):
    parser = build_parser(); args = parser.parse_args(argv)
    try: return args.func(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile, ET.XMLSyntaxError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr); return 2

if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Parser-backed DOCX identity registry with conservative cross-revision matching.

Stable identity is anchored by Word paraId when it is unique. If no native ID
exists, only unique exact object-fingerprint matches are reused. Ambiguous
duplicates receive new IDs; paragraph indices remain location hints only.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from lxml import etree as ET

from .document_engine import NS, has_drawing, parse_xml, qn, visible_text
from .identity import (
    CitationProfile,
    DocumentIdentity,
    DocumentObjectIdentity,
    IdentityValidationError,
    TargetRef,
    build_document_identity,
    build_object_identity,
    canonical_fingerprint,
    normalize_visible_text,
    sha256_file,
)

W14_PARA_ID = "{http://schemas.microsoft.com/office/word/2010/wordml}paraId"
_WORD_PART = re.compile(
    r"^word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml$"
)
_ROLE_ALIASES = {"reply": "Reviewers", "reviewer": "Reviewers", "reviewers": "Reviewers"}
_MUTATING_ACTIONS = frozenset({"replace", "add", "remove", "renumber", "reference_update"})


class DocxIdentityError(ValueError):
    """Raised when a DOCX cannot be converted into an unambiguous identity registry."""


def _role(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    return _ROLE_ALIASES.get(stripped.casefold(), stripped)


def _local(element: ET._Element) -> str:
    return ET.QName(element).localname


def _attr(element: ET._Element | None, local_name: str) -> str | None:
    if element is None:
        return None
    value = element.get(qn(local_name))
    return value if value not in {"", None} else None


def _element_signature(element: ET._Element | None, selected: Sequence[str]) -> dict[str, Any]:
    if element is None:
        return {}
    result: dict[str, Any] = {}
    for name in selected:
        child = element.find(f"w:{name}", NS)
        if child is not None:
            result[name] = {
                "val": _attr(child, "val"),
                "attrs": {
                    ET.QName(key).localname: value
                    for key, value in sorted(child.attrib.items())
                },
                "children": [
                    {
                        "tag": _local(grandchild),
                        "val": _attr(grandchild, "val"),
                        "attrs": {
                            ET.QName(key).localname: value
                            for key, value in sorted(grandchild.attrib.items())
                        },
                    }
                    for grandchild in list(child)
                ],
            }
    return result


def _paragraph_formatting(paragraph: ET._Element) -> dict[str, Any]:
    ppr = paragraph.find("w:pPr", NS)
    para_props = _element_signature(
        ppr,
        ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "widowControl",
         "numPr", "tabs", "spacing", "ind", "jc", "outlineLvl", "textDirection"),
    )
    spans: list[dict[str, Any]] = []
    offset = 0
    for run in paragraph.findall(".//w:r", NS):
        run_text = visible_text(run)
        properties = _element_signature(
            run.find("w:rPr", NS),
            ("rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
             "strike", "dstrike", "color", "spacing", "position", "sz", "szCs",
             "highlight", "u", "vertAlign", "rtl", "lang", "shd"),
        )
        end = offset + len(run_text)
        if end > offset:
            if spans and spans[-1]["end"] == offset and spans[-1]["format"] == properties:
                spans[-1]["end"] = end
            else:
                spans.append({"start": offset, "end": end, "format": properties})
        offset = end
    return {"paragraph": para_props, "spans": spans}


def _paragraph_structure(paragraph: ET._Element) -> list[str]:
    result: list[str] = []

    def walk(node: ET._Element) -> None:
        for child in list(node):
            if child.tag in {
                qn("pPr"), qn("rPr"), qn("t"), qn("delText"), qn("instrText"),
            }:
                continue
            # Run boundaries are formatting implementation details, not
            # paragraph structure. Preserve semantic children such as drawings,
            # hyperlinks, bookmarks, field markers and footnote references.
            if child.tag != qn("r"):
                result.append(_local(child))
            walk(child)

    walk(paragraph)
    return result


def _ancestor(element: ET._Element, tag: str) -> ET._Element | None:
    current = element.getparent()
    while current is not None:
        if current.tag == qn(tag):
            return current
        current = current.getparent()
    return None


def _paragraph_type(part: str, paragraph: ET._Element, style_id: str | None) -> str:
    if part == "word/document.xml":
        if _ancestor(paragraph, "tbl") is not None:
            return "table_cell_paragraph"
        if has_drawing(paragraph):
            return "figure_paragraph"
        text = normalize_visible_text(visible_text(paragraph))
        if (style_id or "").casefold() == "caption" or re.match(
            r"^(fig(?:ure)?|table|scheme|equation)\s*[\dIVX]",
            text,
            re.IGNORECASE,
        ):
            return "caption_paragraph"
        return "body_paragraph"
    if part.startswith("word/header"):
        return "header_paragraph"
    if part.startswith("word/footer"):
        return "footer_paragraph"
    if part == "word/footnotes.xml":
        return "footnote_paragraph"
    if part == "word/endnotes.xml":
        return "endnote_paragraph"
    return "document_paragraph"


def _table_payload(table: ET._Element) -> dict[str, Any]:
    rows = table.findall("./w:tr", NS)
    row_payload = []
    for row in rows:
        cells = row.findall("./w:tc", NS)
        row_payload.append([
            normalize_visible_text(" ".join(
                visible_text(paragraph)
                for paragraph in cell.findall(".//w:p", NS)
            ))
            for cell in cells
        ])
    style = table.find("w:tblPr/w:tblStyle", NS)
    return {
        "rows": row_payload,
        "row_count": len(rows),
        "column_counts": [len(row) for row in row_payload],
        "table_style": _attr(style, "val"),
    }


def _table_formatting(table: ET._Element) -> dict[str, Any]:
    table_properties = table.find("w:tblPr", NS)
    rows = []
    for row in table.findall("./w:tr", NS):
        cells = []
        for cell in row.findall("./w:tc", NS):
            cells.append(_element_signature(
                cell.find("w:tcPr", NS),
                ("tcW", "gridSpan", "vAlign", "tcBorders", "shd", "noWrap", "textDirection"),
            ))
        rows.append({
            "row": _element_signature(
                row.find("w:trPr", NS),
                ("trHeight", "cantSplit", "tblHeader", "jc"),
            ),
            "cells": cells,
        })
    return {
        "table": _element_signature(
            table_properties,
            ("tblStyle", "tblW", "tblLayout", "tblInd", "tblBorders", "tblCellMar", "jc"),
        ),
        "rows": rows,
    }


def _part_descriptors(part: str, root: ET._Element) -> tuple[list[dict[str, Any]], list[str], list[Any]]:
    descriptors: list[dict[str, Any]] = []
    content_fragments: list[str] = []
    structure: list[Any] = []

    if part == "word/comments.xml":
        comments = root.findall(".//w:comment", NS)
        for comment_index, comment in enumerate(comments):
            paragraphs = comment.findall(".//w:p", NS)
            text = normalize_visible_text("\n".join(visible_text(p) for p in paragraphs))
            comment_id = _attr(comment, "id")
            payload = {"text": text, "paragraph_count": len(paragraphs)}
            descriptors.append({
                "object_type": "reviewer_comment",
                "part": part,
                "native_id": comment_id,
                "location_hint": {
                "comment_idx": comment_index,
                "comment_id": comment_id,
                "visible_text_length": len(text),
            },
                "payload": payload,
                "_formatting": {},
            })
            content_fragments.append(text)
            structure.append({"type": "reviewer_comment", "paragraph_count": len(paragraphs)})
        return descriptors, content_fragments, structure

    tables = root.findall(".//w:tbl", NS)
    for table_index, table in enumerate(tables):
        payload = _table_payload(table)
        descriptors.append({
            "object_type": "table",
            "part": part,
            "native_id": None,
            "location_hint": {"table_idx": table_index},
            "payload": payload,
            "_formatting": _table_formatting(table),
        })
        structure.append({
            "type": "table",
            "row_count": payload["row_count"],
            "column_counts": payload["column_counts"],
        })

    paragraphs = root.findall(".//w:p", NS)
    for paragraph_index, paragraph in enumerate(paragraphs):
        normalized_text = normalize_visible_text(visible_text(paragraph))
        ppr = paragraph.find("w:pPr", NS)
        style_id = _attr(ppr.find("w:pStyle", NS) if ppr is not None else None, "val")
        table = _ancestor(paragraph, "tbl")
        table_index = next(
            (index for index, item in enumerate(tables) if item == table),
            None,
        )
        payload = {
            "text": normalized_text,
            "structure": _paragraph_structure(paragraph),
        }
        hint: dict[str, Any] = {"p_idx": paragraph_index}
        para_id = paragraph.get(W14_PARA_ID)
        if para_id:
            hint["para_id"] = para_id
        hint["visible_text_length"] = len(normalized_text)
        if table_index is not None:
            hint["table_idx"] = table_index
            row = _ancestor(paragraph, "tr")
            cell = _ancestor(paragraph, "tc")
            if row is not None:
                rows = row.getparent().findall("./w:tr", NS)
                hint["row_idx"] = next(
                    (idx for idx, candidate in enumerate(rows) if candidate is row),
                    None,
                )
            if cell is not None:
                cells = cell.getparent().findall("./w:tc", NS)
                hint["cell_idx"] = next(
                    (idx for idx, candidate in enumerate(cells) if candidate is cell),
                    None,
                )

        object_type = _paragraph_type(part, paragraph, style_id)
        descriptors.append({
            "object_type": object_type,
            "part": part,
            "native_id": para_id,
            "location_hint": hint,
            "payload": payload,
            "_formatting": _paragraph_formatting(paragraph),
        })
        if normalized_text:
            content_fragments.append(normalized_text)
        structure.append({
            "type": object_type,
            "in_table": table_index is not None,
            "has_drawing": has_drawing(paragraph),
        })

    return descriptors, content_fragments, structure

def _citation_profile(parts: Mapping[str, ET._Element]) -> CitationProfile:
    modes: set[str] = set()
    examples: list[str] = []
    reference_labels: list[str] = []
    footnote_reference_count = 0

    for part, root in parts.items():
        for paragraph in root.findall(".//w:p", NS):
            text = normalize_visible_text(visible_text(paragraph))
            if part == "word/document.xml":
                for run in paragraph.findall(".//w:r", NS):
                    vertical = run.find("w:rPr/w:vertAlign", NS)
                    if _attr(vertical, "val") == "superscript":
                        run_text = "".join(node.text or "" for node in run.findall("w:t", NS)).strip()
                        if run_text and re.fullmatch(r"[\d,;–\-]+", run_text):
                            modes.add("numeric_superscript")
                            if run_text not in examples:
                                examples.append(run_text)
                for pattern, mode in (
                    (r"\[\s*\d+(?:\s*[,;\-–]\s*\d+)*\s*\]", "numeric_bracketed"),
                    (r"\(\s*\d+(?:\s*[,;\-–]\s*\d+)*\s*\)", "numeric_parenthetical"),
                    (r"\([A-Z][A-Za-z'’\-]+(?:\s+et\s+al\.)?,?\s+\d{4}[a-z]?\)", "author_year"),
                ):
                    for match in re.findall(pattern, text):
                        modes.add(mode)
                        if match not in examples:
                            examples.append(match)
        if part == "word/document.xml":
            footnote_reference_count += len(root.findall(".//w:footnoteReference", NS))

    if footnote_reference_count:
        modes.add("footnote")
        examples.append(f"footnoteReferences:{footnote_reference_count}")

    if len(modes) > 1:
        mode = "mixed"
    elif modes:
        mode = next(iter(modes))
    else:
        mode = "unknown"

    in_reference_section = False
    for root in parts.values():
        for paragraph in root.findall(".//w:p", NS):
            text = normalize_visible_text(visible_text(paragraph))
            if not text:
                continue
            if text.casefold() in {
                "references", "bibliography", "literature cited", "引用文献", "参考文献"
            }:
                in_reference_section = True
                continue
            if in_reference_section:
                match = re.match(r"^(\[\s*\d+\s*\]|\d+[.)])\s+", text)
                if match and match.group(1) not in reference_labels:
                    reference_labels.append(match.group(1))

    selected_examples = tuple(examples[:20])
    selected_labels = tuple(reference_labels[:100])
    evidence = {
        "mode_candidates": sorted(modes),
        "marker_examples": list(selected_examples),
        "reference_labels": list(selected_labels),
        "footnote_reference_count": footnote_reference_count,
    }
    return CitationProfile(
        mode=mode,
        numbering_scope="unknown",
        marker_examples=selected_examples,
        reference_labels=selected_labels,
        detection_method="docx_rule_heuristics_v1",
        evidence_fingerprint=canonical_fingerprint(evidence),
    )


def _read_docx(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not path.is_file():
        raise DocxIdentityError(f"DOCX source file does not exist: {path}")
    if path.suffix.casefold() != ".docx":
        raise DocxIdentityError(f"Identity parser currently requires .docx input: {path}")
    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = sorted(name for name in archive.namelist() if _WORD_PART.fullmatch(name))
            if "word/document.xml" not in names:
                raise DocxIdentityError(f"DOCX has no word/document.xml: {path}")
            roots = {name: parse_xml(archive.read(name)) for name in names}
    except (OSError, zipfile.BadZipFile, KeyError, ET.XMLSyntaxError) as exc:
        raise DocxIdentityError(f"Cannot parse DOCX {path}: {exc}") from exc

    descriptors: list[dict[str, Any]] = []
    content_by_part: dict[str, list[str]] = {}
    structure_by_part: dict[str, list[Any]] = {}
    formatting_by_part: dict[str, list[Any]] = {}
    for part, root in roots.items():
        part_descriptors, fragments, structure = _part_descriptors(part, root)
        descriptors.extend(part_descriptors)
        content_by_part[part] = fragments
        structure_by_part[part] = structure
        formatting_by_part[part] = [
            row.get("_formatting", {})
            for row in part_descriptors
        ]

    visible_text_value = "\n".join(
        f"[{part}]\n" + "\n".join(fragments)
        for part, fragments in sorted(content_by_part.items())
    )
    dimensions = {
        "structure": structure_by_part,
        "formatting": formatting_by_part,
    }
    return {
        "visible_text": visible_text_value,
        "structure": dimensions["structure"],
        "formatting": dimensions["formatting"],
        "citation_profile": _citation_profile(roots),
    }, descriptors


def _prior_indices(
    prior_manifest: Mapping[str, Any] | None,
    document_id: str,
) -> tuple[dict[tuple[str, str, str], list[Mapping[str, Any]]],
           dict[tuple[str, str, str], list[Mapping[str, Any]]]]:
    native: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    fingerprints: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    if not isinstance(prior_manifest, Mapping):
        return native, fingerprints
    for item in prior_manifest.get("objects", []):
        if not isinstance(item, Mapping) or item.get("document_id") != document_id:
            continue
        key = (str(item.get("part", "")), str(item.get("object_type", "")))
        native_id = item.get("native_id")
        if isinstance(native_id, str) and native_id:
            native[(key[0], key[1], native_id)].append(item)
        digest = item.get("object_fingerprint")
        if isinstance(digest, str):
            fingerprints[(key[0], key[1], digest)].append(item)
    return native, fingerprints


def _validate_inventory_paragraphs(
    inventory_row: Mapping[str, Any],
    descriptors: Sequence[Mapping[str, Any]],
    *,
    role: str,
) -> None:
    """Verify that inventory (part,p_idx,text,paraId) anchors describe this exact DOCX."""
    raw_paragraphs = inventory_row.get("paragraphs", [])
    if not isinstance(raw_paragraphs, list):
        raise DocxIdentityError(
            f"Inventory paragraphs for {role} must be an array; rebuild the workspace inventory."
        )
    descriptor_index: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
    for descriptor in descriptors:
        hint = descriptor.get("location_hint")
        if not isinstance(hint, Mapping):
            continue
        p_idx = hint.get("p_idx")
        part = descriptor.get("part")
        if isinstance(part, str) and isinstance(p_idx, int) and not isinstance(p_idx, bool):
            descriptor_index[(part, p_idx)].append(descriptor)

    seen: set[tuple[str, int]] = set()
    for index, row in enumerate(raw_paragraphs):
        if not isinstance(row, Mapping):
            raise DocxIdentityError(
                f"Inventory paragraphs[{index}] for {role} must be an object."
            )
        part = row.get("part")
        p_idx = row.get("p_idx")
        if (
            not isinstance(part, str) or not part
            or not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0
        ):
            raise DocxIdentityError(
                f"Inventory paragraphs[{index}] for {role} needs a valid part/p_idx."
            )
        key = (part, p_idx)
        if key in seen:
            raise DocxIdentityError(
                f"Inventory duplicates paragraph location {role}/{part}/p_idx={p_idx}."
            )
        seen.add(key)
        candidates = descriptor_index.get(key, [])
        if len(candidates) != 1:
            raise DocxIdentityError(
                f"Inventory paragraph {role}/{part}/p_idx={p_idx} resolves to "
                f"{len(candidates)} actual DOCX paragraphs. Rebuild inventory before "
                "creating object identities; no positional fallback is permitted."
            )
        candidate = candidates[0]
        expected_text = row.get("text")
        actual_text = candidate.get("payload", {}).get("text")
        if not isinstance(expected_text, str) or not isinstance(actual_text, str):
            raise DocxIdentityError(
                f"Inventory paragraph {role}/{part}/p_idx={p_idx} has invalid text."
            )
        if normalize_visible_text(expected_text) != normalize_visible_text(actual_text):
            raise DocxIdentityError(
                f"Inventory paragraph text does not match the actual DOCX at "
                f"{role}/{part}/p_idx={p_idx}. Rebuild inventory."
            )
        expected_para_id = row.get("para_id")
        actual_para_id = candidate.get("native_id")
        if expected_para_id not in {None, ""} and expected_para_id != actual_para_id:
            raise DocxIdentityError(
                f"Inventory paraId does not match the actual DOCX at "
                f"{role}/{part}/p_idx={p_idx}. Rebuild inventory."
            )


def build_document_identity_manifest(
    inventory: Any,
    *,
    previous_manifest: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build parser-backed document identities and a cross-revision object registry."""
    if isinstance(inventory, Mapping):
        rows = inventory.get("documents", [])
    elif isinstance(inventory, list):
        rows = inventory
    else:
        raise DocxIdentityError("Workspace inventory must be an array or an object with documents[].")
    if not isinstance(rows, list) or not rows:
        raise DocxIdentityError("Workspace inventory contains no document reports.")

    prior_documents: dict[str, Mapping[str, Any]] = {}
    prior_roles: dict[str, int] = Counter()
    if previous_manifest is not None and (
        not isinstance(previous_manifest, Mapping)
        or previous_manifest.get("schema_version") != "0.5.1"
        or not isinstance(previous_manifest.get("documents"), list)
        or not isinstance(previous_manifest.get("objects"), list)
    ):
        raise DocxIdentityError("Previous identity manifest must be a valid v0.5.1 documents/objects manifest.")
    if isinstance(previous_manifest, Mapping):
        if any(not isinstance(item, Mapping) for item in previous_manifest.get("documents", [])):
            raise DocxIdentityError("Previous identity manifest contains malformed document records.")
        if any(not isinstance(item, Mapping) for item in previous_manifest.get("objects", [])):
            raise DocxIdentityError("Previous identity manifest contains malformed object records.")
        for row in previous_manifest.get("documents", []):
            role = _role(row.get("role"))
            prior_roles[role] += 1
            prior_documents[role] = row
        duplicates = sorted(role for role, count in prior_roles.items() if count > 1)
        if duplicates:
            raise DocxIdentityError(
                "Previous identity manifest contains duplicate document role(s): "
                + ", ".join(duplicates)
            )

    roles_seen: set[str] = set()
    document_records: list[dict[str, Any]] = []
    object_records: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, Mapping):
            raise DocxIdentityError("Inventory document entries must be objects.")
        role = _role(row.get("role"))
        if role not in {"Reviewers", "Manuscript", "SI"}:
            raise DocxIdentityError(f"Unsupported inventory document role: {role!r}.")
        if role in roles_seen:
            raise DocxIdentityError(
                f"Only one current source document per role is supported; duplicate role: {role}."
            )
        roles_seen.add(role)
        raw_path = row.get("file")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise DocxIdentityError(f"Inventory file path is missing for role {role}.")
        source_path = Path(raw_path).expanduser().resolve()
        actual_sha = sha256_file(source_path)
        inventory_sha = row.get("source_sha256")
        if actual_sha != inventory_sha:
            raise DocxIdentityError(
                f"Inventory source hash is stale for {role}: {source_path}. "
                "Rebuild the workspace inventory before generating identities."
            )

        parsed, descriptors = _read_docx(source_path)
        _validate_inventory_paragraphs(row, descriptors, role=role)
        previous = prior_documents.get(role)
        document_id = (
            previous.get("document_id")
            if isinstance(previous, Mapping)
            and isinstance(previous.get("document_id"), str)
            else str(uuid.uuid4())
        )
        document = build_document_identity(
            role=role,
            source_sha256=actual_sha,
            visible_text=parsed["visible_text"],
            structure=parsed["structure"],
            formatting=parsed["formatting"],
            citation_state={
                "profile": parsed["citation_profile"].to_dict(),
                "markers": list(parsed["citation_profile"].marker_examples),
                "reference_labels": list(parsed["citation_profile"].reference_labels),
            },
            citation_profile=parsed["citation_profile"],
            document_id=document_id,
        )
        document_records.append(document.to_dict())

        old_native, old_fingerprints = _prior_indices(previous_manifest, document_id)
        current_native_counts = Counter(
            (item["part"], item["object_type"], item["native_id"])
            for item in descriptors
            if isinstance(item.get("native_id"), str) and item["native_id"]
        )
        current_fp_counts = Counter(
            (
                item["part"],
                item["object_type"],
                canonical_fingerprint({
                    "object_type": item["object_type"],
                    "part": item["part"],
                    "payload": item["payload"],
                }),
            )
            for item in descriptors
        )

        matched_native = 0
        matched_fingerprint = 0
        new_objects = 0
        ambiguous = 0
        table_id_by_index: dict[tuple[str, int], str] = {}

        for descriptor in descriptors:
            object_type = descriptor["object_type"]
            part = descriptor["part"]
            native_id = descriptor.get("native_id")
            fingerprint = canonical_fingerprint({
                "object_type": object_type,
                "part": part,
                "payload": descriptor["payload"],
            })
            object_id = None
            if isinstance(native_id, str) and native_id:
                native_key = (part, object_type, native_id)
                prior = old_native.get(native_key, [])
                if current_native_counts[native_key] == 1 and len(prior) == 1:
                    object_id = prior[0].get("object_id")
                    if isinstance(object_id, str) and object_id:
                        matched_native += 1
                elif prior or current_native_counts[native_key] > 1:
                    ambiguous += 1

            if object_id is None:
                fingerprint_key = (part, object_type, fingerprint)
                prior = old_fingerprints.get(fingerprint_key, [])
                if current_fp_counts[(part, object_type, fingerprint)] == 1 and len(prior) == 1:
                    prior_id = prior[0].get("object_id")
                    if isinstance(prior_id, str) and prior_id:
                        object_id = prior_id
                        matched_fingerprint += 1
                elif prior and current_fp_counts[(part, object_type, fingerprint)] > 1:
                    ambiguous += 1

            if object_id is None:
                object_id = str(uuid.uuid4())
                new_objects += 1

            hint = descriptor.get("location_hint", {})
            parent_object_id = None
            if object_type == "table" and isinstance(hint.get("table_idx"), int):
                table_id_by_index[(part, hint["table_idx"])] = object_id
            elif isinstance(hint.get("table_idx"), int):
                parent_object_id = table_id_by_index.get((part, hint["table_idx"]))

            identity = build_object_identity(
                document=document,
                object_type=object_type,
                part=part,
                object_payload=descriptor["payload"],
                object_id=object_id,
                native_id=native_id,
                parent_object_id=parent_object_id,
                location_hint=hint,
            )
            object_records.append(identity.to_dict())

        report_rows.append({
            "role": role,
            "object_count": len(descriptors),
            "matched_native_id": matched_native,
            "matched_exact_fingerprint": matched_fingerprint,
            "new_objects": new_objects,
            "ambiguous_matches": ambiguous,
        })

    manifest = {
        "schema_version": "0.5.1",
        "documents": document_records,
        "objects": object_records,
    }
    report = {
        "algorithm": "native-paraId-then-unique-exact-fingerprint-v1",
        "documents": report_rows,
        "totals": {
            "documents": len(document_records),
            "objects": len(object_records),
            "matched_native_id": sum(row["matched_native_id"] for row in report_rows),
            "matched_exact_fingerprint": sum(row["matched_exact_fingerprint"] for row in report_rows),
            "new_objects": sum(row["new_objects"] for row in report_rows),
            "ambiguous_matches": sum(row["ambiguous_matches"] for row in report_rows),
        },
    }
    return manifest, report


def validate_manifest_sources(
    manifest: Any,
    plan: Mapping[str, Any],
) -> list[str]:
    """Bind every manifest document identity to the exact source hashes in a plan."""
    errors: list[str] = []
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != "0.5.1":
        return ["Identity manifest schema_version must be '0.5.1'."]
    documents = manifest.get("documents")
    objects = manifest.get("objects")
    if not isinstance(documents, list) or not isinstance(objects, list):
        return ["Identity manifest must contain documents[] and objects[]."]
    by_role: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(documents):
        if not isinstance(row, Mapping):
            errors.append(f"Identity manifest documents[{index}] must be an object.")
            continue
        role = _role(row.get("role"))
        if role in by_role:
            errors.append(f"Identity manifest contains duplicate role {role}.")
        by_role[role] = row
    plan_rows = plan.get("source_files", [])
    if not isinstance(plan_rows, list):
        errors.append("Decision plan source_files must be an array.")
        plan_rows = []
    plan_by_role: dict[str, Mapping[str, Any]] = {}
    for row in plan_rows:
        if not isinstance(row, Mapping):
            continue
        role = _role(row.get("role"))
        if role in plan_by_role:
            errors.append(f"Decision plan contains more than one source file for role {role}; a role-specific identity is ambiguous.")
        plan_by_role[role] = row
    for role, source in plan_by_role.items():
        record = by_role.get(role)
        if record is None:
            errors.append(f"Identity manifest has no document identity for source role {role}.")
            continue
        if record.get("source_sha256") != source.get("sha256"):
            errors.append(
                f"Identity manifest source hash is stale for {role}; regenerate it from the current DOCX."
            )
    extra_roles = set(by_role) - set(plan_by_role)
    if extra_roles:
        errors.append(
            "Identity manifest contains role(s) not present in the decision plan: "
            + ", ".join(sorted(extra_roles))
        )

    document_versions: set[tuple[Any, Any]] = set()
    document_ids: set[str] = set()
    for index, row in enumerate(documents):
        if not isinstance(row, Mapping):
            continue
        document_id = row.get("document_id")
        revision_id = row.get("revision_id")
        if not isinstance(document_id, str) or not document_id:
            errors.append(f"Identity manifest documents[{index}].document_id must be non-empty.")
            continue
        if document_id in document_ids:
            errors.append(f"Identity manifest reuses document_id {document_id} for multiple logical documents.")
        document_ids.add(document_id)
        document_versions.add((document_id, revision_id))
        dimensions = row.get("dimensions")
        profile = row.get("citation_profile")
        try:
            if not isinstance(dimensions, Mapping) or not isinstance(profile, Mapping):
                raise IdentityValidationError("dimensions and citation_profile must be objects.")
            citation_profile = CitationProfile(
                mode=profile.get("mode"),
                style_name=profile.get("style_name"),
                numbering_scope=profile.get("numbering_scope"),
                marker_examples=tuple(profile.get("marker_examples", [])),
                reference_labels=tuple(profile.get("reference_labels", [])),
                detection_method=profile.get("detection_method"),
                evidence_fingerprint=profile.get("evidence_fingerprint"),
                profile_version=profile.get("profile_version", "1"),
            )
            DocumentIdentity(
                document_id=document_id,
                revision_id=revision_id,
                role=row.get("role"),
                source_sha256=row.get("source_sha256"),
                content_sha256=dimensions.get("content_sha256"),
                structure_sha256=dimensions.get("structure_sha256"),
                formatting_sha256=dimensions.get("formatting_sha256"),
                citation_sha256=dimensions.get("citation_sha256"),
                citation_profile=citation_profile,
            )
            expected_revision_id = canonical_fingerprint({
                "document_id": document_id,
                "source_sha256": row.get("source_sha256"),
            })
            if revision_id != expected_revision_id:
                errors.append(f"Identity manifest revision_id is inconsistent for role {_role(row.get('role'))}.")
        except (IdentityValidationError, TypeError, ValueError) as exc:
            errors.append(f"Identity manifest documents[{index}] is invalid: {exc}")

    object_keys: set[tuple[Any, Any, Any]] = set()
    object_ids: set[tuple[Any, Any, str]] = set()
    for index, row in enumerate(objects):
        if not isinstance(row, Mapping):
            errors.append(f"Identity manifest objects[{index}] must be an object.")
            continue
        key = (row.get("document_id"), row.get("revision_id"), row.get("object_id"))
        if key in object_keys:
            errors.append(f"Identity manifest contains duplicate object identity {key}.")
        object_keys.add(key)
        if (row.get("document_id"), row.get("revision_id")) not in document_versions:
            errors.append(f"Identity manifest object {row.get('object_id')} references an unknown document revision.")
            continue
        try:
            record = DocumentObjectIdentity(
                object_id=row.get("object_id"),
                document_id=row.get("document_id"),
                revision_id=row.get("revision_id"),
                object_type=row.get("object_type"),
                part=row.get("part"),
                object_fingerprint=row.get("object_fingerprint"),
                native_id=row.get("native_id"),
                parent_object_id=row.get("parent_object_id"),
                location_hint=row.get("location_hint"),
            )
            if record.object_type == "table_cell_paragraph" and not record.parent_object_id:
                errors.append(
                    f"Identity manifest table-cell paragraph {record.object_id} has no parent table object."
                )
            object_ids.add((record.document_id, record.revision_id, record.object_id))
        except (IdentityValidationError, TypeError, ValueError) as exc:
            errors.append(f"Identity manifest objects[{index}] is invalid: {exc}")

    for index, row in enumerate(objects):
        if not isinstance(row, Mapping) or not row.get("parent_object_id"):
            continue
        parent_key = (
            row.get("document_id"),
            row.get("revision_id"),
            row.get("parent_object_id"),
        )
        if parent_key not in object_ids:
            errors.append(
                f"Identity manifest object {row.get('object_id')} has an unknown parent object."
            )
    return errors


def build_target_refs_by_task(
    tasks: Any,
    manifest: Mapping[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Resolve decision-plan p_idx/paraId hints into stable TargetRefs."""
    if not isinstance(tasks, list):
        return {}, ["Decision-plan tasks must be an array."]
    documents = {
        _role(row.get("role")): row
        for row in manifest.get("documents", [])
        if isinstance(row, Mapping)
    }
    objects = [
        row for row in manifest.get("objects", [])
        if isinstance(row, Mapping)
    ]
    result: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            continue
        refs: list[dict[str, Any]] = []
        seen: set[tuple[str, int | None, int | None]] = set()
        for target_index, target in enumerate(task.get("targets", [])):
            if not isinstance(target, Mapping) or target.get("action") not in _MUTATING_ACTIONS:
                continue
            role = _role(target.get("document"))
            document = documents.get(role)
            if document is None:
                errors.append(f"Task {task_id} target[{target_index}] has no identity for role {role}.")
                continue
            part = target.get("part")
            p_idx = target.get("p_idx")
            if not isinstance(part, str) or not part or not isinstance(p_idx, int) or isinstance(p_idx, bool):
                errors.append(f"Task {task_id} target[{target_index}] lacks a valid part/p_idx.")
                continue
            candidates = [
                row for row in objects
                if row.get("document_id") == document.get("document_id")
                and row.get("revision_id") == document.get("revision_id")
                and row.get("part") == part
                and isinstance(row.get("location_hint"), Mapping)
                and row["location_hint"].get("p_idx") == p_idx
            ]
            expected_para_id = target.get("para_id")
            if isinstance(expected_para_id, str) and expected_para_id:
                candidates = [
                    row for row in candidates
                    if row.get("native_id") == expected_para_id
                ]
            if len(candidates) != 1:
                errors.append(
                    f"Task {task_id} target[{target_index}] resolves to {len(candidates)} DOCX objects "
                    f"at {role}/{part}/p_idx={p_idx}; refusing paragraph-index fallback."
                )
                continue
            item = candidates[0]
            start_offset = target.get("start_offset")
            end_offset = target.get("end_offset")
            location_hint = item.get("location_hint")
            visible_text_length = (
                location_hint.get("visible_text_length")
                if isinstance(location_hint, Mapping) else None
            )
            if start_offset is not None or end_offset is not None:
                if (
                    not isinstance(visible_text_length, int)
                    or isinstance(visible_text_length, bool)
                    or visible_text_length < 0
                ):
                    errors.append(
                        f"Task {task_id} target[{target_index}] has offsets but its parser-backed "
                        "object has no visible_text_length."
                    )
                    continue
                if (
                    not isinstance(start_offset, int) or isinstance(start_offset, bool)
                    or not isinstance(end_offset, int) or isinstance(end_offset, bool)
                    or start_offset < 0 or end_offset <= start_offset
                    or end_offset > visible_text_length
                ):
                    errors.append(
                        f"Task {task_id} target[{target_index}] character range "
                        f"{start_offset!r}:{end_offset!r} exceeds the resolved object's "
                        f"visible-text length {visible_text_length}."
                    )
                    continue
            try:
                ref = TargetRef(
                    document_id=item.get("document_id"),
                    revision_id=item.get("revision_id"),
                    object_id=item.get("object_id"),
                    object_type=item.get("object_type"),
                    part=item.get("part"),
                    object_fingerprint=item.get("object_fingerprint"),
                    start_offset=start_offset,
                    end_offset=end_offset,
                    location_hint=item.get("location_hint"),
                ).to_dict()
            except (IdentityValidationError, TypeError, ValueError) as exc:
                errors.append(f"Task {task_id} target[{target_index}] cannot form a TargetRef: {exc}")
                continue
            key = (ref["object_id"], ref["start_offset"], ref["end_offset"])
            if key not in seen:
                refs.append(ref)
                seen.add(key)
        result[task_id] = refs
    return result, errors

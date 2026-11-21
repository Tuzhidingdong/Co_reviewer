# -*- coding: utf-8 -*-
"""Create a read-only accepted-changes projection of a marked DOCX.

The source and marked DOCX are never modified. This is an internal working
projection for downstream revision analysis, not the final publication Clean
Manuscript or a substitute for Microsoft Word save/render validation.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import etree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _qn(local: str) -> str:
    return f"{{{W}}}{local}"


_ACCEPT_WRAPPERS = {_qn("ins"), _qn("moveTo")}
_REJECT_WRAPPERS = {_qn("del"), _qn("moveFrom")}
_UNSUPPORTED_STRUCTURAL_REVISIONS = {
    _qn("cellIns"),
    _qn("cellDel"),
    _qn("cellMerge"),
}
_REMOVE_ELEMENTS = {
    _qn("rPrChange"),
    _qn("pPrChange"),
    _qn("tblPrChange"),
    _qn("tblGridChange"),
    _qn("trPrChange"),
    _qn("tcPrChange"),
    _qn("numberingChange"),
    _qn("sectPrChange"),
    _qn("commentRangeStart"),
    _qn("commentRangeEnd"),
    _qn("commentReference"),
    _qn("moveFromRangeStart"),
    _qn("moveFromRangeEnd"),
    _qn("moveToRangeStart"),
    _qn("moveToRangeEnd"),
    _qn("customXmlInsRangeStart"),
    _qn("customXmlInsRangeEnd"),
    _qn("customXmlDelRangeStart"),
    _qn("customXmlDelRangeEnd"),
    _qn("customXmlMoveFromRangeStart"),
    _qn("customXmlMoveFromRangeEnd"),
    _qn("customXmlMoveToRangeStart"),
    _qn("customXmlMoveToRangeEnd"),
    _qn("conflictIns"),
    _qn("conflictDel"),
}
_ALL_REVISION_ELEMENTS = _ACCEPT_WRAPPERS | _REJECT_WRAPPERS | _REMOVE_ELEMENTS | {
    _qn("delText"),
    _qn("delInstrText"),
}
_COMMENT_PARTS = {
    "word/comments.xml",
    "word/commentsextended.xml",
    "word/commentsids.xml",
    "word/commentsextensible.xml",
    "word/people.xml",
}


class WorkingViewError(ValueError):
    """Raised when a clean working projection cannot be generated safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_deterministic_docx_archive(
    output_path: str | Path,
    package: dict[str, bytes],
) -> None:
    """Write stable DOCX ZIP bytes so identical Marked inputs yield identical Clean hashes.

    ZipFile.writestr(name, data) stamps each member with the current local time.
    Two equivalent Clean projections made on opposite sides of a two-second ZIP
    timestamp boundary would then have different SHA-256 values. Fixed metadata
    removes that false mismatch without weakening the content/hash verification.
    """
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in package.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def _xml_parser() -> ET.XMLParser:
    return ET.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        remove_comments=False,
        huge_tree=False,
    )


def _append_tail(parent: ET._Element, index: int, tail: str | None) -> None:
    if not tail:
        return
    children = list(parent)
    if index > 0 and children:
        previous = children[min(index - 1, len(children) - 1)]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail


def _remove_child(parent: ET._Element, child: ET._Element) -> None:
    index = parent.index(child)
    tail = child.tail
    parent.remove(child)
    _append_tail(parent, index, tail)


def _unwrap_child(parent: ET._Element, child: ET._Element) -> None:
    index = parent.index(child)
    inserted = list(child)
    tail = child.tail
    parent.remove(child)
    for offset, item in enumerate(inserted):
        parent.insert(index + offset, item)
    if tail:
        if inserted:
            inserted[-1].tail = (inserted[-1].tail or "") + tail
        else:
            _append_tail(parent, index, tail)


def _resolve_revisions(root: ET._Element) -> dict[str, int]:
    counts = {
        "insertions_accepted": 0,
        "deletions_rejected": 0,
        "moves_to_accepted": 0,
        "moves_from_rejected": 0,
        "revision_properties_removed": 0,
        "comment_anchors_removed": 0,
        "comment_parts_removed": 0,
        "comment_relationships_removed": 0,
        "comment_content_type_entries_removed": 0,
        "other_revision_markers_removed": 0,
    }

    def visit(parent: ET._Element) -> None:
        for child in list(parent):
            if child.tag in _ACCEPT_WRAPPERS:
                if child.tag == _qn("ins"):
                    counts["insertions_accepted"] += 1
                else:
                    counts["moves_to_accepted"] += 1
                visit(child)
                _unwrap_child(parent, child)
            elif child.tag in _REJECT_WRAPPERS:
                if child.tag == _qn("del"):
                    counts["deletions_rejected"] += 1
                else:
                    counts["moves_from_rejected"] += 1
                _remove_child(parent, child)
            elif child.tag in _UNSUPPORTED_STRUCTURAL_REVISIONS:
                raise WorkingViewError(
                    f"Unsupported table-structure revision element {ET.QName(child).localname}; "
                    "the Working Clean View refuses to guess the accepted table structure."
                )
            elif child.tag in _REMOVE_ELEMENTS:
                if child.tag in {
                    _qn("commentRangeStart"),
                    _qn("commentRangeEnd"),
                    _qn("commentReference"),
                }:
                    counts["comment_anchors_removed"] += 1
                elif child.tag in {
                    _qn("rPrChange"), _qn("pPrChange"), _qn("tblPrChange"),
                    _qn("tblGridChange"), _qn("trPrChange"), _qn("tcPrChange"),
                    _qn("sectPrChange"), _qn("numberingChange"),
                }:
                    counts["revision_properties_removed"] += 1
                else:
                    counts["other_revision_markers_removed"] += 1
                _remove_child(parent, child)
            else:
                visit(child)

    visit(root)
    return counts


def _remove_comment_relationships(data: bytes) -> tuple[bytes, int]:
    root = ET.fromstring(data, parser=_xml_parser())
    removed = 0
    for child in list(root):
        if not isinstance(child.tag, str):
            continue
        relationship_type = child.get("Type", "").casefold()
        if "/comments" in relationship_type or "/people" in relationship_type:
            root.remove(child)
            removed += 1
    if removed:
        return ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), removed
    return data, 0


def _remove_comment_content_types(data: bytes) -> tuple[bytes, int]:
    root = ET.fromstring(data, parser=_xml_parser())
    removed = 0
    for child in list(root):
        part_name = child.get("PartName", "").lstrip("/").casefold()
        if _is_comment_part_name(part_name):
            root.remove(child)
            removed += 1
    if removed:
        return ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), removed
    return data, 0



def _is_comment_part_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in _COMMENT_PARTS
        or (lowered.startswith("word/comments") and lowered.endswith(".xml"))
    )


def _comment_metadata_in_relationships(data: bytes, part: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(data, parser=_xml_parser())
    except ET.XMLSyntaxError as exc:
        raise WorkingViewError(f"Cannot inspect comment relationships in {part}: {exc}") from exc
    return [
        {
            "part": part,
            "id": child.get("Id", ""),
            "type": child.get("Type", ""),
            "target": child.get("Target", ""),
        }
        for child in root
        if isinstance(child.tag, str)
        and (
            "/comments" in child.get("Type", "").casefold()
            or "/people" in child.get("Type", "").casefold()
        )
    ]


def inspect_clean_baseline(
    input_file: str | Path,
    *,
    allow_tracked_text_revisions: bool = False,
) -> dict[str, Any]:
    """Scan unresolved revisions/comments; optionally allow only native text insertions/deletions.

    The optional allowance is intended solely for files whose exact SHA-256 has
    already been verified against a published Workflow Ledger active pointer.
    Formatting/move revisions and every comment part/anchor remain blocking.
    """
    source = Path(input_file).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".docx":
        raise WorkingViewError(f"Baseline source must be an existing DOCX: {source}")
    counts = {
        "insertions": 0, "deletions": 0, "move_from": 0, "move_to": 0,
        "formatting_changes": 0, "comments": 0, "comment_anchors": 0,
        "comment_parts": 0, "comment_relationships": 0,
        "comment_content_type_entries": 0,
    }
    issues: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(source, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise WorkingViewError(f"Baseline DOCX has a ZIP CRC error in {bad}.")
            names = set(archive.namelist())
            for part in sorted(name for name in names if _is_comment_part_name(name)):
                counts["comment_parts"] += 1
                issues.append({
                    "category": "comment_parts", "element": Path(part).name,
                    "part": part, "paragraph_index": None, "element_id": None,
                    "author": None, "text_excerpt": "",
                })
            for part in sorted(name for name in names if name.endswith(".rels")):
                for relationship in _comment_metadata_in_relationships(archive.read(part), part):
                    counts["comment_relationships"] += 1
                    issues.append({
                        "category": "comment_relationships", "element": "Relationship",
                        "part": part, "paragraph_index": None,
                        "element_id": relationship["id"], "author": None,
                        "text_excerpt": (
                            f'{relationship["type"]} -> {relationship["target"]}'
                        )[:240],
                    })
            content_types = ET.fromstring(
                archive.read("[Content_Types].xml"), parser=_xml_parser()
            )
            for child in content_types:
                part_name = child.get("PartName", "").lstrip("/")
                if _is_comment_part_name(part_name):
                    counts["comment_content_type_entries"] += 1
                    issues.append({
                        "category": "comment_content_type_entries",
                        "element": "Override", "part": "[Content_Types].xml",
                        "paragraph_index": None,
                        "element_id": part_name, "author": None, "text_excerpt": "",
                    })
            for part in sorted(name for name in names if name.startswith("word/") and name.endswith(".xml")):
                try:
                    root = ET.fromstring(archive.read(part), parser=_xml_parser())
                except ET.XMLSyntaxError as exc:
                    raise WorkingViewError(f"Cannot inspect baseline Word part {part}: {exc}") from exc
                paragraph_indices = {
                    node: index for index, node in enumerate(root.findall(".//w:p", {"w": W}))
                }
                for node in root.iter():
                    if not isinstance(node.tag, str) or ET.QName(node).namespace != W:
                        continue
                    local = ET.QName(node).localname
                    category = None
                    if local == "ins":
                        category = "insertions"
                    elif local == "del":
                        category = "deletions"
                    elif local in {"moveFrom", "moveFromRangeStart", "moveFromRangeEnd"}:
                        category = "move_from"
                    elif local in {"moveTo", "moveToRangeStart", "moveToRangeEnd"}:
                        category = "move_to"
                    elif local.endswith("Change"):
                        category = "formatting_changes"
                    elif local == "comment":
                        category = "comments"
                    elif local in {"commentRangeStart", "commentRangeEnd", "commentReference"}:
                        category = "comment_anchors"
                    if category is None:
                        continue
                    counts[category] += 1
                    paragraph = node if local == "p" else next(
                        (ancestor for ancestor in node.iterancestors() if ancestor.tag == _qn("p")),
                        None,
                    )
                    paragraph_index = paragraph_indices.get(paragraph) if paragraph is not None else None
                    text_value = "".join(item.text or "" for item in node.iter() if isinstance(item.tag, str) and item.tag in {_qn("t"), _qn("delText")}).strip()
                    issues.append({
                        "category": category,
                        "element": local,
                        "part": part,
                        "paragraph_index": paragraph_index,
                        "element_id": node.get(_qn("id")),
                        "author": node.get(_qn("author")),
                        "text_excerpt": text_value[:240],
                    })
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkingViewError(f"Cannot inspect baseline DOCX {source}: {exc}") from exc
    allowed_categories = (
        {"insertions", "deletions"}
        if allow_tracked_text_revisions
        else set()
    )
    allowed_counts = {
        key: value for key, value in counts.items()
        if key in allowed_categories and value
    }
    blocking = {
        key: value for key, value in counts.items()
        if key not in allowed_categories and value
    }
    return {
        "file": str(source),
        "source_sha256": _sha256(source.read_bytes()),
        "clean": not blocking,
        "mode": (
            "published_active_allow_text_revisions"
            if allow_tracked_text_revisions
            else "initial_clean_baseline"
        ),
        "counts": counts,
        "allowed_counts": allowed_counts,
        "blocking_counts": blocking,
        "issues": issues,
    }


def strip_docx_comments_bytes(payload: bytes) -> tuple[bytes, dict[str, Any]]:
    """Physically remove native Word comment parts, relationships and anchors from a DOCX payload."""
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise WorkingViewError("Marked DOCX payload must be non-empty bytes.")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            bad = archive.testzip()
            if bad:
                raise WorkingViewError(f"Prepared DOCX has a ZIP CRC error in {bad}.")
            package = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkingViewError(f"Cannot read prepared DOCX package: {exc}") from exc
    removed_parts = sorted(name for name in package if _is_comment_part_name(name))
    for name in removed_parts:
        del package[name]
    anchor_counts = 0
    relationship_counts = 0
    content_type_counts = 0
    for name, data in list(package.items()):
        if name.endswith(".rels"):
            root = ET.fromstring(data, parser=_xml_parser())
            part_relationship_count = 0
            for child in list(root):
                if not isinstance(child.tag, str):
                    continue
                rel_type = child.get("Type", "").casefold()
                if "/comments" in rel_type or "/people" in rel_type:
                    root.remove(child)
                    relationship_counts += 1
                    part_relationship_count += 1
            if part_relationship_count:
                package[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            continue
        if name == "[Content_Types].xml":
            root = ET.fromstring(data, parser=_xml_parser())
            for child in list(root):
                part_name = child.get("PartName", "").lstrip("/").casefold()
                if _is_comment_part_name(part_name):
                    root.remove(child)
                    content_type_counts += 1
            package[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            continue
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        root = ET.fromstring(data, parser=_xml_parser())
        part_anchor_count = 0
        for node in list(root.iter()):
            if isinstance(node.tag, str) and node.tag in {
                _qn("commentRangeStart"), _qn("commentRangeEnd"), _qn("commentReference")
            }:
                parent = node.getparent()
                if parent is not None:
                    _remove_child(parent, node)
                    anchor_counts += 1
                    part_anchor_count += 1
        if part_anchor_count:
            package[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in package.items():
            archive.writestr(name, data)
    cleaned = output.getvalue()
    with zipfile.ZipFile(io.BytesIO(cleaned), "r") as archive:
        names = set(archive.namelist())
        if any(_is_comment_part_name(name) for name in names):
            raise WorkingViewError("Comment package parts remain after Marked DOCX cleanup.")
        for name in names:
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = ET.fromstring(archive.read(name), parser=_xml_parser())
            residual = [ET.QName(node).localname for node in root.iter() if isinstance(node.tag, str) and node.tag in {
                _qn("commentRangeStart"), _qn("commentRangeEnd"), _qn("commentReference")
            }]
            if residual:
                raise WorkingViewError(f"Comment anchors remain in {name}: {', '.join(residual)}")
        for name in names:
            if name.endswith(".rels"):
                root = ET.fromstring(archive.read(name), parser=_xml_parser())
                if any("/comments" in child.get("Type", "").casefold() or "/people" in child.get("Type", "").casefold() for child in root):
                    raise WorkingViewError(f"Comment relationships remain in {name}.")
        types_root = ET.fromstring(archive.read("[Content_Types].xml"), parser=_xml_parser())
        if any(
            _is_comment_part_name(child.get("PartName", "").lstrip("/"))
            for child in types_root
        ):
            raise WorkingViewError("Comment content-type overrides remain after Marked DOCX cleanup.")
    return cleaned, {
        "removed_comment_parts": removed_parts,
        "removed_comment_anchors": anchor_counts,
        "removed_comment_relationships": relationship_counts,
        "removed_content_type_entries": content_type_counts,
        "validation": "passed",
    }


def accepted_view_docx_bytes(payload: bytes) -> bytes:
    """Return an in-memory accepted-revisions projection without leaving artifacts behind."""
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        raise WorkingViewError("Accepted-view projection requires non-empty DOCX bytes.")
    with tempfile.TemporaryDirectory(prefix="co-reviewer-accepted-view-") as temporary:
        source = Path(temporary) / "source.docx"
        output = Path(temporary) / "accepted.docx"
        source.write_bytes(bytes(payload))
        create_working_clean_view(source, output)
        return output.read_bytes()


def create_working_clean_view(
    input_file: str | Path,
    output_file: str | Path,
) -> dict[str, Any]:
    """Accept all tracked insertions/moves-to and reject deletions/moves-from.

    The output is a separate derivative DOCX. It preserves unrelated package
    members byte-for-byte, removes Word comment anchors/parts, turns tracking
    off in the derivative settings and rejects any residual revision markup.
    """
    source = Path(input_file).expanduser().resolve()
    target = Path(output_file).expanduser().resolve()
    if not source.is_file():
        raise WorkingViewError(f"Marked input DOCX does not exist: {source}")
    if source.suffix.casefold() != ".docx" or target.suffix.casefold() != ".docx":
        raise WorkingViewError("Working Clean View input and output must be DOCX.")
    if source == target:
        raise WorkingViewError("Working Clean View output must not overwrite its marked input.")
    if target.exists():
        raise WorkingViewError(f"Working Clean View output already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    source_bytes = source.read_bytes()
    source_hash = _sha256(source_bytes)
    try:
        with zipfile.ZipFile(source, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise WorkingViewError(f"Input DOCX has a ZIP CRC error in {bad_member}.")
            package = {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkingViewError(f"Cannot read marked DOCX package: {exc}") from exc

    if "word/document.xml" not in package or "[Content_Types].xml" not in package:
        raise WorkingViewError("Input DOCX is missing document.xml or [Content_Types].xml.")

    counts = {
        "insertions_accepted": 0,
        "deletions_rejected": 0,
        "moves_to_accepted": 0,
        "moves_from_rejected": 0,
        "revision_properties_removed": 0,
        "comment_anchors_removed": 0,
        "comment_parts_removed": 0,
        "comment_relationships_removed": 0,
        "comment_content_type_entries_removed": 0,
        "other_revision_markers_removed": 0,
    }
    changed_parts: list[str] = []

    for name, data in list(package.items()):
        if _is_comment_part_name(name):
            del package[name]
            counts["comment_parts_removed"] += 1
            continue
        if name.endswith(".rels"):
            updated, removed = _remove_comment_relationships(data)
            if removed:
                package[name] = updated
                counts["comment_relationships_removed"] += removed
            continue
        if name == "[Content_Types].xml":
            updated, removed = _remove_comment_content_types(data)
            if removed:
                package[name] = updated
                counts["comment_content_type_entries_removed"] += removed
            continue
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue

        try:
            root = ET.fromstring(data, parser=_xml_parser())
        except ET.XMLSyntaxError as exc:
            raise WorkingViewError(f"Cannot parse Word XML part {name}: {exc}") from exc

        if not any(
            isinstance(node.tag, str) and ET.QName(node).namespace == W
            for node in root.iter()
        ):
            continue

        part_counts = _resolve_revisions(root)
        if name == "word/settings.xml":
            for node in list(root):
                if node.tag in {_qn("trackRevisions"), _qn("revisionView")}:
                    root.remove(node)
                    part_counts["revision_properties_removed"] += 1
        if any(part_counts.values()):
            package[name] = ET.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )
            changed_parts.append(name)
            for key, value in part_counts.items():
                counts[key] += value

    # Check that no tracked revision containers, property snapshots or
    # comment anchors remain anywhere in Word XML parts.
    residual: list[str] = []
    for name, data in package.items():
        if not name.startswith("word/") or not name.endswith(".xml"):
            continue
        try:
            root = ET.fromstring(data, parser=_xml_parser())
        except ET.XMLSyntaxError as exc:
            raise WorkingViewError(f"Generated Word XML part {name} is malformed: {exc}") from exc
        for node in root.iter():
            if isinstance(node.tag, str) and ET.QName(node).namespace == W:
                if node.tag in _ALL_REVISION_ELEMENTS or node.tag in _UNSUPPORTED_STRUCTURAL_REVISIONS or node.tag in {
                    _qn("trackRevisions"), _qn("revisionView")
                }:
                    residual.append(f"{name}:{ET.QName(node).localname}")
                    if len(residual) >= 20:
                        break
        if residual:
            break
    if residual:
        raise WorkingViewError(
            "Working Clean View still contains revision markup: " + ", ".join(residual)
        )
    residual_comment_parts = [
        name for name in package if _is_comment_part_name(name)
    ]
    residual_comment_relationships: list[str] = []
    for name, data in package.items():
        if not name.endswith(".rels"):
            continue
        try:
            rel_root = ET.fromstring(data, parser=_xml_parser())
        except ET.XMLSyntaxError as exc:
            raise WorkingViewError(f"Cannot validate relationships in {name}: {exc}") from exc
        if any(
            "/comments" in child.get("Type", "").casefold()
            or "/people" in child.get("Type", "").casefold()
            for child in rel_root
        ):
            residual_comment_relationships.append(name)
    try:
        content_types_root = ET.fromstring(package["[Content_Types].xml"], parser=_xml_parser())
    except ET.XMLSyntaxError as exc:
        raise WorkingViewError(f"Cannot validate comment content types: {exc}") from exc
    residual_comment_content_types = [
        child.get("PartName", "")
        for child in content_types_root
        if _is_comment_part_name(child.get("PartName", "").lstrip("/"))
    ]
    if residual_comment_parts or residual_comment_relationships or residual_comment_content_types:
        raise WorkingViewError(
            "Working Clean View still contains Word comment package metadata: "
            f"parts={residual_comment_parts}, relationships={residual_comment_relationships}, "
            f"content_types={residual_comment_content_types}"
        )

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp.docx", dir=str(target.parent)
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        _write_deterministic_docx_archive(temp_path, package)
        with zipfile.ZipFile(temp_path, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise WorkingViewError(f"Generated DOCX has a ZIP CRC error in {bad_member}.")
            if "word/document.xml" not in archive.namelist():
                raise WorkingViewError("Generated DOCX is missing word/document.xml.")
            root = ET.fromstring(archive.read("word/document.xml"), parser=_xml_parser())
            residual_main = [
                ET.QName(node).localname
                for node in root.iter()
                if isinstance(node.tag, str)
                and ET.QName(node).namespace == W
                and node.tag in _ALL_REVISION_ELEMENTS
            ]
            if residual_main:
                raise WorkingViewError(
                    "Generated main document still contains revision markup: "
                    + ", ".join(residual_main[:20])
                )
        if _sha256(source.read_bytes()) != source_hash:
            raise WorkingViewError("Marked source DOCX changed while creating the Working Clean View.")
        os.replace(temp_path, target)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    output_hash = _sha256(target.read_bytes())
    now = datetime.now(timezone.utc).isoformat()
    return {
        "view_id": str(uuid.uuid4()),
        "created_at_utc": now,
        "source_file": str(source),
        "source_sha256": source_hash,
        "output_file": str(target),
        "output_sha256": output_hash,
        "working_view_fingerprint": _sha256(json.dumps(
            {
                "source_sha256": source_hash,
                "working_clean_sha256": output_hash,
                "policy": "accept-insertions-reject-deletions-v1",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")),
        "changed_word_parts": sorted(changed_parts),
        "counts": counts,
        "validation": {
            "input_unchanged": True,
            "output_exists": target.is_file(),
            "output_zip_crc": "passed",
            "residual_revision_markup": "none",
            "source_and_output_distinct": True,
        },
    }

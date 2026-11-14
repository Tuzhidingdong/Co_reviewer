# -*- coding: utf-8 -*-
"""Create a read-only accepted-changes projection of a marked DOCX.

The source and marked DOCX are never modified. This is an internal working
projection for downstream revision analysis, not the final publication Clean
Manuscript or a substitute for Microsoft Word save/render validation.
"""
from __future__ import annotations

import hashlib
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
_REMOVE_ELEMENTS = {
    _qn("rPrChange"),
    _qn("pPrChange"),
    _qn("tblPrChange"),
    _qn("tblGridChange"),
    _qn("trPrChange"),
    _qn("tcPrChange"),
    _qn("numberingChange"),
    _qn("sectPrChange"),
    _qn("cellIns"),
    _qn("cellDel"),
    _qn("cellMerge"),
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
    "word/commentsExtended.xml",
    "word/commentsIds.xml",
    "word/commentsExtensible.xml",
    "word/people.xml",
}


class WorkingViewError(ValueError):
    """Raised when a clean working projection cannot be generated safely."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        if part_name in _COMMENT_PARTS:
            root.remove(child)
            removed += 1
    if removed:
        return ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True), removed
    return data, 0


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
        "other_revision_markers_removed": 0,
    }
    changed_parts: list[str] = []

    for name, data in list(package.items()):
        if name in _COMMENT_PARTS:
            del package[name]
            continue
        if name == "word/_rels/document.xml.rels":
            updated, removed = _remove_comment_relationships(data)
            if removed:
                package[name] = updated
                counts["comment_anchors_removed"] += removed
            continue
        if name == "[Content_Types].xml":
            updated, removed = _remove_comment_content_types(data)
            if removed:
                package[name] = updated
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
                if node.tag in _ALL_REVISION_ELEMENTS or node.tag in {
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

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp.docx", dir=str(target.parent)
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)
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
        "working_view_fingerprint": _sha256(
            f"{source_hash}:{output_hash}:accept-insertions-reject-deletions-v1".encode("utf-8")
        ),
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

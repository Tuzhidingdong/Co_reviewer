# -*- coding: utf-8 -*-
"""Deterministic structural and formatting checks for generated DOCX/PDF artifacts."""
from __future__ import annotations

import hashlib
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
_COMMENT_PARTS = {
    "word/comments.xml",
    "word/commentsextended.xml",
    "word/commentsids.xml",
    "word/commentsextensible.xml",
    "word/people.xml",
}
PROFILES = {"generic", "clean-manuscript", "marked-manuscript", "word-final-clean", "response-letter"}
PLACEHOLDER_RE = re.compile(
    r"(\[\[(?:TODO|TBD|XX|REF|FIG|TABLE)[^\]]*\]\]|\[(?:TODO|TBD|XX|REF|INSERT[^\]]*)\]|"
    r"\b(?:TODO|TBD|PLACEHOLDER)\b|Fig(?:ure)?\.?\s*S?[xX]+|Table\s*S?[xX]+|"
    r"Page\s*[xX]+|Lines?\s*[xX]+)", re.I
)


class DocxQualityError(ValueError):
    """Raised when quality input or profile is invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _attr(node: ET.Element | None, name: str) -> str | None:
    return node.get(f"{{{W}}}{name}") if node is not None else None


def _font_names(node: ET.Element | None) -> list[str]:
    if node is None:
        return []
    fonts = node.find("w:rFonts", NS)
    if fonts is None:
        return []
    return sorted({value for key, value in fonts.attrib.items() if value and (key.endswith("}ascii") or key.endswith("}hAnsi") or key.endswith("}eastAsia") or key.endswith("}cs"))})


def _check(checks: list[dict[str, Any]], check_id: str, status: str, severity: str, summary: str, evidence: Any = None) -> None:
    checks.append({
        "id": check_id, "status": status, "severity": severity,
        "summary": summary, "evidence": evidence,
    })


def audit_docx_quality(
    docx_path: str | Path,
    *,
    profile: str = "generic",
    pdf_path: str | Path | None = None,
    word_page_count: int | None = None,
    expected_font: str | None = None,
) -> dict[str, Any]:
    source = Path(docx_path).expanduser().resolve()
    if profile not in PROFILES:
        raise DocxQualityError(f"Unsupported DOCX quality profile: {profile!r}.")
    if not source.is_file() or source.suffix.lower() != ".docx":
        raise DocxQualityError(f"Expected an existing .docx file: {source}")

    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "docx_file": str(source), "docx_sha256": _sha256(source),
        "docx_bytes": source.stat().st_size, "profile": profile,
    }
    try:
        with zipfile.ZipFile(source, "r") as archive:
            bad_member = archive.testzip()
            names = set(archive.namelist())
            required = {"[Content_Types].xml", "word/document.xml"}
            missing = sorted(required - names)
            if bad_member or missing:
                _check(checks, "package_integrity", "FAIL", "error",
                       "DOCX package is incomplete or corrupt.",
                       {"bad_member": bad_member, "missing_members": missing})
                return _report(source, metrics, checks, pdf_path)
            _check(checks, "package_integrity", "PASS", "info",
                   "DOCX ZIP package is readable and contains required parts.")
            xml_parts = [
                name for name in names
                if name.endswith(".xml") and (
                    name == "word/document.xml" or name == "word/styles.xml"
                    or name == "word/settings.xml" or name.startswith("word/header")
                    or name.startswith("word/footer") or name == "word/comments.xml"
                )
            ]
            roots: dict[str, ET.Element] = {}
            xml_errors = []
            for name in xml_parts:
                try:
                    roots[name] = ET.fromstring(archive.read(name))
                except ET.ParseError as exc:
                    xml_errors.append({"part": name, "error": str(exc)})
            if xml_errors:
                _check(checks, "xml_integrity", "FAIL", "error", "One or more Word XML parts are invalid.", xml_errors)
                return _report(source, metrics, checks, pdf_path)
            _check(checks, "xml_integrity", "PASS", "info", "Relevant Word XML parts parse successfully.")

            document = roots["word/document.xml"]
            paragraphs = document.findall(".//w:p", NS)
            tables = document.findall(".//w:tbl", NS)
            text_roots = [
                root for name, root in roots.items()
                if name == "word/document.xml"
                or re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
                or name in {"word/footnotes.xml", "word/endnotes.xml"}
            ]
            texts = [
                node.text or ""
                for root in text_roots
                for node in root.findall(".//w:t", NS)
            ]
            all_text = "\n".join(texts)
            metrics["paragraph_count"] = len(paragraphs)
            metrics["table_count"] = len(tables)
            metrics["text_characters"] = len(all_text)
            tracked = {
                local: sum(len(root.findall(f".//w:{local}", NS)) for root in text_roots)
                for local in ("ins", "del", "moveTo", "moveFrom")
            }
            metrics["tracked_revision_elements"] = tracked
            comments_count = len(roots.get("word/comments.xml", ET.Element("comments")).findall(".//w:comment", NS))
            comment_anchor_count = sum(
                sum(len(root.findall(f".//w:{local}", NS)) for root in text_roots)
                for local in ("commentRangeStart", "commentRangeEnd", "commentReference")
            )
            comment_package_parts = sorted(
                name for name in names
                if name.casefold() in _COMMENT_PARTS
                or (name.casefold().startswith("word/comments") and name.casefold().endswith(".xml"))
            )
            content_types_root = ET.fromstring(archive.read("[Content_Types].xml"))
            comment_content_type_entries = sorted(
                child.attrib.get("PartName", "")
                for child in content_types_root
                if child.attrib.get("PartName", "").lstrip("/").casefold() in _COMMENT_PARTS
                or child.attrib.get("PartName", "").lstrip("/").casefold().startswith("word/comments")
            )
            comment_relationships: list[dict[str, str]] = []
            relationship_scan_errors: list[dict[str, str]] = []
            for relationship_part in sorted(name for name in names if name.endswith(".rels")):
                try:
                    relationship_root = ET.fromstring(archive.read(relationship_part))
                except ET.ParseError as exc:
                    relationship_scan_errors.append({
                        "part": relationship_part, "error": str(exc)
                    })
                    continue
                for relationship in relationship_root:
                    relationship_type = relationship.attrib.get("Type", "").casefold()
                    if "/comments" in relationship_type or "/people" in relationship_type:
                        comment_relationships.append({
                            "part": relationship_part,
                            "type": relationship.attrib.get("Type", ""),
                            "target": relationship.attrib.get("Target", ""),
                        })
            metrics["comment_count"] = comments_count
            metrics["comment_anchor_count"] = comment_anchor_count
            metrics["comment_package_parts"] = comment_package_parts
            metrics["comment_content_type_entries"] = comment_content_type_entries
            metrics["comment_relationships"] = comment_relationships
            metrics["comment_relationship_scan_errors"] = relationship_scan_errors
            placeholders = sorted(set(match.group(0) for match in PLACEHOLDER_RE.finditer(all_text)))
            metrics["placeholder_tokens"] = placeholders
            placeholder_failure = bool(placeholders) and profile == "word-final-clean"
            placeholder_status = "FAIL" if placeholder_failure else ("WARN" if placeholders else "PASS")
            _check(
                checks, "placeholders", placeholder_status,
                "error" if placeholder_failure else ("warning" if placeholders else "info"),
                "Potential draft placeholders were found." if placeholders else "No common draft placeholders detected.",
                placeholders,
            )
            if profile in {"clean-manuscript", "word-final-clean", "response-letter"}:
                remaining = sum(tracked.values())
                _check(checks, "no_tracked_revisions", "FAIL" if remaining else "PASS",
                       "error" if remaining else "info",
                       "Final Clean output must not contain tracked revisions.",
                       {"counts": tracked, "total": remaining})
            elif profile == "marked-manuscript":
                _check(checks, "tracked_revision_inventory", "PASS", "info",
                       "Tracked revision elements were inventoried for Marked output.",
                       tracked)
            has_comments = bool(
                comments_count or comment_anchor_count or comment_package_parts
                or comment_content_type_entries or comment_relationships or relationship_scan_errors
            )
            comments_failure = has_comments and profile in {"clean-manuscript", "word-final-clean", "response-letter"}
            _check(
                checks, "comments",
                "FAIL" if comments_failure else ("WARN" if has_comments else "PASS"),
                "error" if comments_failure else ("warning" if has_comments else "info"),
                "Word comments, comment package parts, relationships or anchors remain in the document package."
                if has_comments else "No Word comments, comment package parts, relationships or anchors were detected.",
                {
                    "comments": comments_count,
                    "anchors": comment_anchor_count,
                    "package_parts": comment_package_parts,
                    "content_type_entries": comment_content_type_entries,
                    "relationships": comment_relationships,
                    "relationship_scan_errors": relationship_scan_errors,
                },
            )

            styles = roots.get("word/styles.xml")
            normal_style = None
            if styles is not None:
                normal_style = next(
                    (node for node in styles.findall("w:style", NS)
                     if _attr(node, "styleId") == "Normal"), None
                )
            normal_rpr = normal_style.find("w:rPr", NS) if normal_style is not None else None
            normal_fonts = _font_names(normal_rpr)
            size_node = normal_rpr.find("w:sz", NS) if normal_rpr is not None else None
            color_node = normal_rpr.find("w:color", NS) if normal_rpr is not None else None
            normal_size_pt = float(_attr(size_node, "val")) / 2 if _attr(size_node, "val") else None
            normal_color = _attr(color_node, "val")
            metrics["normal_style"] = {
                "font_names": normal_fonts, "font_size_pt": normal_size_pt,
                "font_color": normal_color,
            }
            expected = expected_font or ("Times New Roman" if profile == "response-letter" else None)
            if expected:
                ok = any(name.casefold() == expected.casefold() for name in normal_fonts)
                _check(checks, "normal_style_font", "PASS" if ok else "FAIL",
                       "info" if ok else "error",
                       f"Normal style font {'matches' if ok else 'does not match'} {expected}.",
                       {"expected": expected, "observed": normal_fonts})
            if profile == "response-letter":
                size_ok = normal_size_pt is not None and abs(normal_size_pt - 10.5) <= 0.1
                _check(checks, "normal_style_size", "PASS" if size_ok else "FAIL",
                       "info" if size_ok else "error",
                       "Response Letter Normal style should be 10.5 pt.",
                       {"expected_pt": 10.5, "observed_pt": normal_size_pt})
                color_ok = normal_color is None or normal_color.casefold() in {"000000", "auto"}
                _check(checks, "normal_style_color", "PASS" if color_ok else "FAIL",
                       "info" if color_ok else "error",
                       "Response Letter Normal style should use black text.",
                       {"expected": "000000/auto", "observed": normal_color})
            sections = document.findall(".//w:sectPr", NS)
            metrics["section_count"] = len(sections)
            metrics["section_geometry"] = []
            for section in sections:
                page = section.find("w:pgSz", NS)
                margins = section.find("w:pgMar", NS)
                metrics["section_geometry"].append({
                    "page_width_twips": _attr(page, "w"),
                    "page_height_twips": _attr(page, "h"),
                    "top_margin_twips": _attr(margins, "top"),
                    "bottom_margin_twips": _attr(margins, "bottom"),
                    "left_margin_twips": _attr(margins, "left"),
                    "right_margin_twips": _attr(margins, "right"),
                })
            if not sections:
                _check(checks, "section_geometry", "WARN", "warning",
                       "No section geometry was found in document.xml.")
            else:
                _check(checks, "section_geometry", "PASS", "info",
                       "Page and margin geometry was extracted for review.", metrics["section_geometry"])
    except zipfile.BadZipFile as exc:
        _check(checks, "package_integrity", "FAIL", "error", "DOCX is not a valid ZIP package.", str(exc))
        return _report(source, metrics, checks, pdf_path)

    return _report(source, metrics, checks, pdf_path, word_page_count=word_page_count)


def _report(
    source: Path,
    metrics: dict[str, Any],
    checks: list[dict[str, Any]],
    pdf_path: str | Path | None,
    *,
    word_page_count: int | None = None,
) -> dict[str, Any]:
    pdf_metrics = None
    if pdf_path is not None:
        pdf = Path(pdf_path).expanduser().resolve()
        if not pdf.is_file():
            _check(checks, "pdf_integrity", "FAIL", "error", "PDF file does not exist.", str(pdf))
        else:
            digest = _sha256(pdf)
            pdf_metrics = {"file": str(pdf), "sha256": digest, "bytes": pdf.stat().st_size}
            with pdf.open("rb") as handle:
                pdf_header = handle.read(5)
            if pdf.stat().st_size < 8 or pdf_header != b"%PDF-":
                _check(checks, "pdf_integrity", "FAIL", "error", "PDF header is invalid.", pdf_metrics)
            else:
                if word_page_count is not None:
                    if not isinstance(word_page_count, int) or isinstance(word_page_count, bool) or word_page_count < 1:
                        _check(checks, "pdf_integrity", "FAIL", "error",
                               "Word COM did not provide a positive final page count.",
                               {"word_page_count": word_page_count, **pdf_metrics})
                    else:
                        pdf_metrics["word_page_count"] = word_page_count
                        _check(checks, "pdf_integrity", "PASS", "info",
                               "PDF signature and SHA-256 are valid; page count was obtained from the same Word COM pagination session.",
                               pdf_metrics)
                else:
                    _check(checks, "pdf_integrity", "PASS", "info",
                           "PDF exists, is non-empty, has a valid PDF signature, and is SHA-256 hashed; page count was not inferred from PDF text.",
                           pdf_metrics)
    failures = sum(1 for row in checks if row["status"] == "FAIL")
    warnings = sum(1 for row in checks if row["status"] == "WARN")
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "FAIL" if failures else ("PASS_WITH_WARNINGS" if warnings else "PASS"),
        "docx": metrics,
        "pdf": pdf_metrics,
        "checks": checks,
        "summary": {"passed": sum(1 for row in checks if row["status"] == "PASS"),
                    "warnings": warnings, "failures": failures},
    }

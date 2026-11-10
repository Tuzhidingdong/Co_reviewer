#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render a Co_Reviewer revision plan as a conservative Word Response Letter."""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _set_run_font(run, name: str = "Times New Roman", size: float = 10.5, bold=None) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor(0, 0, 0)
    if bold is not None:
        run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("ascii", "hAnsi", "eastAsia", "cs"):
        rfonts.set(qn(f"w:{attr}"), name)


def _add_label(doc: Document, label: str, body: str | None = None):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(7)
    paragraph.paragraph_format.space_after = Pt(2)
    run = paragraph.add_run(label)
    _set_run_font(run, size=10.5, bold=True)
    if body is not None:
        text = doc.add_paragraph(body or "[Response text required; not generated automatically.]")
        text.paragraph_format.space_after = Pt(6)
    return paragraph


def _add_page_number(section) -> None:
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run()
    _set_run_font(run, size=9)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    result = OxmlElement("w:t")
    result.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, result, end])


def render_response_docx(
    plan: dict[str, Any],
    output_path: str | Path,
    manuscript_title: str = "",
    author: str = "",
) -> str:
    items = plan.get("revision_items")
    if not isinstance(items, list) or not items:
        raise ValueError("Revision plan must contain at least one revision item.")

    target = Path(output_path).expanduser().resolve()
    if target.suffix.lower() != ".docx":
        raise ValueError("Response Letter output path must end with .docx.")
    if target.exists():
        raise FileExistsError(f"Output already exists; choose a new path: {target}")

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = RGBColor(0, 0, 0)
    normal.paragraph_format.space_after = Pt(5)
    for style_name in ("Title", "Heading 1", "Heading 2"):
        style = doc.styles[style_name]
        style.font.name = "Times New Roman"
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.bold = True
    doc.styles["Title"].font.size = Pt(16)
    doc.styles["Heading 1"].font.size = Pt(13)
    doc.styles["Heading 2"].font.size = Pt(11)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("Response to Reviewers")
    if manuscript_title.strip():
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(manuscript_title.strip())
        _set_run_font(run, size=10.5, bold=True)
    if author.strip():
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(author.strip())
        _set_run_font(run, size=9)

    counts = {state: sum(1 for item in items if item.get("status") == state)
              for state in ("planned", "approved", "applied", "verified", "blocked")}
    summary = doc.add_paragraph(
        "Revision items: {total} | Verified: {verified} | Applied, not verified: {applied} | Open/blocked: {other}".format(
            total=len(items), verified=counts["verified"], applied=counts["applied"],
            other=counts["planned"] + counts["approved"] + counts["blocked"]
        )
    )
    summary.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in summary.runs:
        _set_run_font(run, size=9)

    for index, item in enumerate(items):
        reviewer = str(item.get("reviewer") or "Reviewer")
        item_id = str(item.get("id") or "UNASSIGNED")
        status = str(item.get("status") or "planned")
        heading = doc.add_paragraph(style="Heading 1")
        heading.paragraph_format.keep_with_next = True
        heading.add_run(f"{reviewer} — {item_id}")

        _add_label(doc, "Comment")
        comment = doc.add_paragraph(str(item.get("comment") or "[Comment text missing.]"))
        comment.paragraph_format.keep_together = True

        response = str(item.get("proposed_response") or "").strip()
        if status == "verified":
            _add_label(doc, "Response", response)
        else:
            _add_label(doc, f"Proposed response — DRAFT ({status}; not yet verified)")
            draft = doc.add_paragraph(
                response or "[Response text required; Co_Reviewer will not invent a reply.]"
            )
            draft.paragraph_format.keep_together = True
            note = doc.add_paragraph(
                "Do not use completion claims until the corresponding document changes have been verified."
            )
            for run in note.runs:
                _set_run_font(run, size=9)
                run.italic = True

        _add_label(doc, "Linked document changes")
        targets = item.get("targets", [])
        if not targets:
            doc.add_paragraph("[No linked document targets recorded.]")
        for target_item in targets:
            document = str(target_item.get("document") or "Document")
            location = str(target_item.get("location") or "location not specified")
            output_name = Path(str(target_item.get("output_file") or "")).name or "output file not specified"
            bullet = doc.add_paragraph(style="List Bullet")
            bullet.add_run(f"{document} — {location} (output: {output_name})")

    _add_page_number(section)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{target.stem}.", suffix=".tmp.docx",
            dir=target.parent, delete=False
        ) as handle:
            tmp = handle.name
        doc.save(tmp)
        with zipfile.ZipFile(tmp, "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"Generated DOCX ZIP validation failed: {bad_member}")
            for required in ("[Content_Types].xml", "word/document.xml"):
                if required not in archive.namelist():
                    raise ValueError(f"Generated DOCX is missing required package member: {required}")
        Document(tmp)
        os.replace(tmp, target)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return str(target)

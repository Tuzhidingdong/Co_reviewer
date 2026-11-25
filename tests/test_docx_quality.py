# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

import co_reviewer_cli as cli

from co_reviewer.docx_quality import DocxQualityError, audit_docx_quality
from response_letter_docx import render_response_docx


class DocxQualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _make_docx(self, name="sample.docx", text="Ordinary manuscript text."):
        path = self.root / name
        doc = Document()
        doc.add_paragraph(text)
        doc.save(path)
        return path

    def test_generic_profile_checks_package_and_xml(self):
        path = self._make_docx()
        report = audit_docx_quality(path)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["docx"]["paragraph_count"], 1)
        self.assertEqual(report["summary"]["failures"], 0)

    def test_clean_profile_rejects_tracked_changes(self):
        path = self._make_docx()
        doc = Document(path)
        paragraph = doc.paragraphs[0]._p
        insertion = OxmlElement("w:ins")
        insertion.set(qn("w:id"), "1")
        insertion.set(qn("w:author"), "Synthetic reviewer")
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = "tracked"
        run.append(text)
        insertion.append(run)
        paragraph.append(insertion)
        doc.save(path)
        report = audit_docx_quality(path, profile="word-final-clean")
        self.assertEqual(report["status"], "FAIL")
        check = next(row for row in report["checks"] if row["id"] == "no_tracked_revisions")
        self.assertEqual(check["evidence"]["total"], 1)

    def test_placeholder_is_reported_as_warning_not_silent_pass(self):
        path = self._make_docx(text="Result: [[TODO: confirm value]].")
        report = audit_docx_quality(path, profile="clean-manuscript")
        self.assertEqual(report["status"], "PASS_WITH_WARNINGS")
        self.assertTrue(report["docx"]["placeholder_tokens"])

    def test_response_letter_profile_accepts_renderer_typography(self):
        plan = {
            "revision_items": [{
                "id": "R1", "reviewer": "Reviewer 1", "status": "verified",
                "comment": "Please clarify.", "proposed_response": "We clarified the text.",
                "targets": [],
            }]
        }
        path = self.root / "Response.docx"
        render_response_docx(plan, path)
        report = audit_docx_quality(path, profile="response-letter")
        failures = [row for row in report["checks"] if row["status"] == "FAIL"]
        self.assertEqual(failures, [], msg=str(failures))

    def test_pdf_signature_is_checked_when_pdf_is_supplied(self):
        path = self._make_docx()
        pdf = self.root / "bad.pdf"
        pdf.write_bytes(b"NOT A PDF")
        report = audit_docx_quality(path, pdf_path=pdf)
        check = next(row for row in report["checks"] if row["id"] == "pdf_integrity")
        self.assertEqual(check["status"], "FAIL")
        self.assertEqual(report["status"], "FAIL")

    def test_final_quality_audit_detects_extended_comment_parts_and_relationships(self):
        path = self._make_docx()
        with zipfile.ZipFile(path, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}
        package["word/commentsExtended.xml"] = b'<w15:commentsEx xmlns:w15="urn:synthetic:w15"/>'
        rel_root = ET.fromstring(package["word/_rels/document.xml.rels"])
        ET.SubElement(rel_root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", {
            "Id": "rIdCommentsExtended",
            "Type": "http://schemas.microsoft.com/office/word/2018/wordml/commentsExtended",
            "Target": "commentsExtended.xml",
        })
        package["word/_rels/document.xml.rels"] = ET.tostring(
            rel_root, encoding="utf-8", xml_declaration=True
        )
        types_root = ET.fromstring(package["[Content_Types].xml"])
        ET.SubElement(types_root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
            "PartName": "/word/commentsExtended.xml",
            "ContentType": "application/xml",
        })
        package["[Content_Types].xml"] = ET.tostring(
            types_root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        report = audit_docx_quality(path, profile="word-final-clean")
        check = next(row for row in report["checks"] if row["id"] == "comments")
        self.assertEqual(check["status"], "FAIL")
        self.assertIn("word/commentsExtended.xml", report["docx"]["comment_package_parts"])
        self.assertEqual(len(report["docx"]["comment_relationships"]), 1)

    def test_missing_or_invalid_docx_is_rejected(self):
        with self.assertRaises(DocxQualityError):
            audit_docx_quality(self.root / "missing.docx")
        corrupt = self.root / "corrupt.docx"
        corrupt.write_bytes(b"not a zip")
        report = audit_docx_quality(corrupt)
        self.assertEqual(report["status"], "FAIL")

    def test_quality_report_cannot_overwrite_docx_or_pdf_inputs(self):
        parser = cli.build_parser()
        same_docx = str(self.root / "input.docx")
        args = parser.parse_args([
            "audit-docx-quality", "--docx", same_docx, "--out", same_docx,
        ])
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            cli.cmd_audit_docx_quality(args)

        same_pdf = str(self.root / "input.pdf")
        args = parser.parse_args([
            "audit-docx-quality", "--docx", str(self.root / "source.docx"),
            "--pdf", same_pdf, "--out", same_pdf,
        ])
        with self.assertRaisesRegex(ValueError, "must not overwrite"):
            cli.cmd_audit_docx_quality(args)

    def test_quality_cli_command_is_registered(self):
        parsed = cli.build_parser().parse_args([
            "audit-docx-quality", "--docx", "sample.docx",
            "--profile", "word-final-clean",
        ])
        self.assertEqual(parsed.func, cli.cmd_audit_docx_quality)
        self.assertEqual(parsed.profile, "word-final-clean")

    def test_cover_letter_profile_does_not_impose_response_letter_typography(self):
        from docx.shared import Pt

        path = self._make_docx()
        document = Document(path)
        document.styles["Normal"].font.name = "Arial"
        document.styles["Normal"].font.size = Pt(12)
        document.save(path)

        report = audit_docx_quality(path, profile="cover-letter")
        self.assertEqual(report["status"], "PASS", report)
        check_ids = {row["id"] for row in report["checks"]}
        self.assertNotIn("normal_style_font", check_ids)
        self.assertNotIn("normal_style_size", check_ids)
        self.assertNotIn("normal_style_color", check_ids)

    def test_cover_letter_profile_still_rejects_tracked_revisions(self):
        path = self._make_docx()
        document = Document(path)
        paragraph = document.paragraphs[0]._p
        insertion = OxmlElement("w:ins")
        insertion.set(qn("w:id"), "1")
        insertion.set(qn("w:author"), "Synthetic reviewer")
        run = OxmlElement("w:r")
        text_node = OxmlElement("w:t")
        text_node.text = "tracked"
        run.append(text_node)
        insertion.append(run)
        paragraph.append(insertion)
        document.save(path)

        report = audit_docx_quality(path, profile="cover-letter")
        self.assertEqual(report["status"], "FAIL")
        check = next(row for row in report["checks"] if row["id"] == "no_tracked_revisions")
        self.assertEqual(check["status"], "FAIL")

    def test_response_letter_profile_enforces_normal_style_font(self):
        path = self._make_docx()
        report = audit_docx_quality(path, profile="response-letter")
        self.assertEqual(report["status"], "FAIL")
        check = next(row for row in report["checks"] if row["id"] == "normal_style_font")
        self.assertEqual(check["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()

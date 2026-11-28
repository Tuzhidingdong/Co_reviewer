# -*- coding: utf-8 -*-
"""Batch 2: effective Run format analysis, provenance and safety classification."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.document_engine import _applicable_format_flags, _direct_format_flags, parse_xml  # noqa: E402
from co_reviewer.run_format_analysis import (  # noqa: E402
    analyze_docx, analyze_run, validate_run_format_analysis,
    SAFE_BASE_CLASSES,
)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


class RunFormatAnalysisBatch2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docx = self.root / "synthetic.docx"
        self._build_docx()

    def tearDown(self):
        self.temp.cleanup()

    def _build_docx(self):
        paragraphs = [
            ("plain", "", ""),
            ("direct-super", '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>', ""),
            ("direct-sub", '<w:rPr><w:vertAlign w:val="subscript"/></w:rPr>', ""),
            ("direct-i", '<w:rPr><w:i/></w:rPr>', ""),
            ("style-super", '<w:rPr><w:rStyle w:val="C1"/></w:rPr>', ""),
            ("para-i", "", "P1"),
            ("override-false", '<w:rPr><w:i w:val="0"/></w:rPr>', "P1"),
            ("theme-font", '<w:rPr><w:rFonts w:asciiTheme="minorHAnsi"/></w:rPr>', ""),
            ("missing-style", '<w:rPr><w:rStyle w:val="MISSING"/></w:rPr>', ""),
        ]
        body = []
        for text, rpr, pstyle in paragraphs:
            ppr = f'<w:pPr><w:pStyle w:val="{pstyle}"/></w:pPr>' if pstyle else ""
            body.append(f'<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>')
        document = (
            f'<w:document xmlns:w="{W}"><w:body>{"".join(body)}</w:body></w:document>'
        )
        styles = (
            f'<w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:styleId="P1"><w:rPr><w:i/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="P2"><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/></w:rPr></w:style>'
            '<w:style w:type="character" w:styleId="C1"><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
            '</w:styles>'
        )
        theme = (
            f'<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme name="synthetic">'
            '<a:majorFont><a:latin typeface="MajorFace"/><a:ea typeface="MajorEA"/><a:cs typeface="MajorCS"/></a:majorFont>'
            '<a:minorFont><a:latin typeface="MinorFace"/><a:ea typeface="MinorEA"/><a:cs typeface="MinorCS"/></a:minorFont>'
            '</a:fontScheme></a:themeElements></a:theme>'
        )
        content_types = (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '</Types>'
        )
        rels = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        )
        with zipfile.ZipFile(self.docx, "w") as z:
            z.writestr("[Content_Types].xml", content_types)
            z.writestr("_rels/.rels", rels)
            z.writestr("word/document.xml", document)
            z.writestr("word/styles.xml", styles)
            z.writestr("word/theme/theme1.xml", theme)

    def _report(self):
        report = analyze_docx(self.docx)
        self.assertEqual(validate_run_format_analysis(report), [])
        return {row["text"]: row for row in report["runs"]}

    # ---------------- A. Direct / Effective ----------------
    def test_direct_and_effective_are_separate(self):
        rows = self._report()
        plain = rows["plain"]
        self.assertEqual(plain["direct_format"]["properties"]["vertAlign"], None)
        self.assertEqual(plain["effective_format"]["properties"]["vertAlign"], None)
        self.assertEqual(plain["safety_classification"], "SAFE_WITH_COMPATIBLE_CONTEXT")
        self.assertEqual(rows["direct-super"]["safety_classification"], "UNSAFE_SCRIPT_FORMAT")
        self.assertEqual(rows["direct-sub"]["effective_format"]["properties"]["vertAlign"], "subscript")
        self.assertEqual(rows["direct-i"]["direct_format"]["properties"]["i"], True)

    def test_inherited_script_is_detected(self):
        rows = self._report()
        row = rows["style-super"]
        self.assertEqual(row["direct_format"]["properties"]["vertAlign"], None)
        self.assertEqual(row["effective_format"]["properties"]["vertAlign"], "superscript")
        self.assertEqual(row["provenance"]["vertAlign"], "CHARACTER_STYLE")
        self.assertEqual(row["safety_classification"], "UNSAFE_SCRIPT_FORMAT")

    def test_inherited_plain_context_is_not_unsafe(self):
        rows = self._report()
        row = rows["para-i"]
        self.assertTrue(row["direct_format"]["properties"]["i"] is None)
        self.assertTrue(row["effective_format"]["properties"]["i"] is True)
        self.assertEqual(row["provenance"]["i"], "PARAGRAPH_STYLE")
        self.assertEqual(row["safety_classification"], "SAFE_WITH_COMPATIBLE_CONTEXT")

    def test_direct_false_overrides_inherited_true(self):
        rows = self._report()
        row = rows["override-false"]
        self.assertIs(row["direct_format"]["properties"]["i"], False)
        self.assertIs(row["effective_format"]["properties"]["i"], False)
        self.assertEqual(row["resolution"]["i"]["status"], "CONFLICT_RESOLVED")
        self.assertEqual(row["provenance"]["i"], "DIRECT_RUN")

    def test_theme_provenance_and_doc_defaults(self):
        rows = self._report()
        row = rows["theme-font"]
        self.assertEqual(row["provenance"]["rFonts.ascii"], "THEME")
        self.assertEqual(row["effective_format"]["properties"]["rFonts"]["ascii"], "MinorFace")
        self.assertEqual(row["resolution"]["sz"]["provenance"], "DOC_DEFAULTS")

    def test_unresolved_context_never_becomes_safe(self):
        rows = self._report()
        row = rows["missing-style"]
        self.assertEqual(row["safety_classification"], "UNRESOLVED_EFFECTIVE_FORMAT")
        self.assertTrue(any("effective format resolution failed" in w for w in row["warnings"]))
        for decision in row["property_decisions"]:
            self.assertEqual(decision["decision"], "BLOCKED_IF_UNKNOWN")

    # ---------------- C. Safety classification ----------------
    def test_safe_base_classes(self):
        rows = self._report()
        self.assertIn(rows["plain"]["safety_classification"], SAFE_BASE_CLASSES)
        self.assertNotIn(rows["style-super"]["safety_classification"], SAFE_BASE_CLASSES)
        self.assertNotIn(rows["missing-style"]["safety_classification"], SAFE_BASE_CLASSES)

    def test_analyze_run_without_styles_is_unresolved(self):
        with zipfile.ZipFile(self.docx) as z:
            document_xml = z.read("word/document.xml")
            root = parse_xml(document_xml)
        run = root.findall(".//w:r", {"w": W})[0]
        report = analyze_run(
            run, "plain", context={"document_xml": document_xml, "styles_xml": None,
                                   "theme_xml": None, "fr_current_ids": None},
            run_identity={"part": "word/document.xml", "p_idx": 0, "run_index": 0, "run_id": "r0"},
        )
        self.assertEqual(report["safety_classification"], "UNRESOLVED_EFFECTIVE_FORMAT")

    # ---------------- D. Property decisions ----------------
    def test_property_decisions(self):
        rows = self._report()
        by_prop = lambda row: {d["property"]: d for d in row["property_decisions"]}
        self.assertEqual(by_prop(rows["direct-super"])["vertAlign"]["decision"], "STRIP")
        self.assertEqual(by_prop(rows["direct-i"])["i"]["decision"], "PRESERVE")
        self.assertEqual(by_prop(rows["para-i"])["i"]["decision"], "STRIP")
        self.assertEqual(by_prop(rows["theme-font"])["rFonts"]["decision"], "PRESERVE")

        fr = {
            "format_requirement_id": "FR-REQ-T-01-01",
            "version_id": "FR-REQ-T-01-01-v1",
            "properties": {"vertAlign": "superscript"},
            "token": "token-A",
            "scope": {"replacement": "x", "start": 0, "end": 1},
        }
        analysis = analyze_docx(self.docx, fr=fr, fr_current_ids=["FR-REQ-T-01-01"])
        super_row = next(r for r in analysis["runs"] if r["text"] == "direct-super")
        decision = {d["property"]: d for d in super_row["property_decisions"]}["vertAlign"]
        self.assertEqual(decision["decision"], "EXPLICIT_OPERATION_REQUIRED")
        self.assertEqual(decision["format_requirement_id"], "FR-REQ-T-01-01")

    def test_recompute_decision_for_inherited_font(self):
        with zipfile.ZipFile(self.docx) as z:
            document_xml = z.read("word/document.xml")
            styles_xml = z.read("word/styles.xml")
        root = parse_xml(document_xml)
        p = next(p for p in root.findall(".//w:p", {"w": W}) if p.find("w:pPr", {"w": W}) is not None)
        pPr = p.find("w:pPr", {"w": W})
        pStyle = pPr.find("w:pStyle", {"w": W})
        pStyle.set(f"{{{W}}}val", "P2")
        run = p.find("w:r", {"w": W})
        report = analyze_run(
            run, "plain",
            context={"document_xml": document_xml, "styles_xml": styles_xml,
                     "theme_xml": None, "paragraph_style_id": "P2", "fr_current_ids": None},
            run_identity={"part": "word/document.xml", "p_idx": 0, "run_index": 0, "run_id": "r0"},
        )
        decision = {d["property"]: d for d in report["property_decisions"]}["rFonts"]
        self.assertEqual(decision["decision"], "RECOMPUTE")

    # ---------------- E. CLI / regression ----------------
    def test_cli_command_and_schema_validation(self):
        out = self.root / "analysis.json"
        args = SimpleNamespace(docx=str(self.docx), styles=None, theme=None, out=str(out))
        self.assertEqual(cli.cmd_analyze_run_formats(args), 0)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["runs"]), 9)
        self.assertEqual(validate_run_format_analysis(payload), [])
        bad = json.loads(json.dumps(payload))
        bad["runs"][0]["safety_classification"] = "SAFE_BUT_UNKNOWN"
        self.assertTrue(validate_run_format_analysis(bad))

    def test_existing_direct_flags_behavior_unchanged(self):
        with zipfile.ZipFile(self.docx) as z:
            root = parse_xml(z.read("word/document.xml"))
        runs = root.findall(".//w:r", {"w": W})
        self.assertIn("superscript", _direct_format_flags(runs[1]))
        self.assertIn("i", _direct_format_flags(runs[3]))
        self.assertEqual(_applicable_format_flags(runs[0], "plain"), frozenset())


if __name__ == "__main__":
    unittest.main()

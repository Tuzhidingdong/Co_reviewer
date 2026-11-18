from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from docx import Document

from co_reviewer.document_engine import (
    make_insert_runs, make_run, parse_xml, prepare_document, qn, W,
)
from co_reviewer.structure_classifier import (
    BLOCKED,
    DEFERRED,
    GUARDED,
    SUPPORTED,
    StructureClassificationError,
    assert_edit_target_supported,
    classify_docx_structure,
)


PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _write_docx(path: Path, document_xml: str, *, document_rels: str = "", extra=None):
    members = {
        "[Content_Types].xml": (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            '</Types>'
        ).encode("utf-8"),
        "_rels/.rels": (
            f'<Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rIdDocument" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'
        ).encode("utf-8"),
        "word/document.xml": document_xml.encode("utf-8"),
        "word/_rels/document.xml.rels": (
            document_rels or f'<Relationships xmlns="{PKG_REL}"></Relationships>'
        ).encode("utf-8"),
        "word/styles.xml": (
            f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>'
        ).encode("utf-8"),
    }
    if extra:
        members.update(extra)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return members


def _document(body: str) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{W}" xmlns:m="{M}" xmlns:r="{R}">'
        f'<w:body>{body}<w:sectPr/></w:body></w:document>'
    )


class StructureClassifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docx = self.root / "structure.docx"

    def tearDown(self):
        self.temp.cleanup()

    def test_inventory_classifies_fields_bookmarks_comments_omml_and_nested_tables(self):
        body = (
            '<w:p><w:r><w:t>ordinary text</w:t></w:r></w:p>'
            '<w:p>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '</w:p>'
            '<w:p><w:bookmarkStart w:id="7" w:name="anchor"/>'
            '<w:r><w:t>bookmarked text</w:t></w:r><w:bookmarkEnd w:id="7"/></w:p>'
            '<w:p><w:commentRangeStart w:id="9"/>'
            '<w:r><w:t>commented text</w:t></w:r><w:commentRangeEnd w:id="9"/>'
            '<w:r><w:commentReference w:id="9"/></w:r></w:p>'
            '<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>outer cell</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>inner cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            '</w:tc></w:tr></w:tbl>'
        )
        rels = (
            f'<Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid" TargetMode="External"/>'
            '</Relationships>'
        )
        _write_docx(self.docx, _document(body), document_rels=rels)
        report = classify_docx_structure(self.docx)

        self.assertEqual(report["package_status"], "READY")
        self.assertEqual(report["classification_status"], "COMPLETE")
        self.assertIn("word/document.xml", {part["name"] for part in report["parts"]})
        document_relationships = [
            item for item in report["relationships"]
            if item["source_part"] == "word/document.xml"
        ]
        self.assertEqual(len(document_relationships), 2)
        self.assertIn("EXTERNAL", {item["status"] for item in report["relationships"]})
        kinds = {item["kind"] for item in report["structures"]}
        self.assertIn("complex_field", kinds)
        self.assertIn("bookmark_range", kinds)
        self.assertIn("comment_range", kinds)
        self.assertIn("omml_formula", kinds)
        self.assertIn("nested_table", kinds)

        field = next(item for item in report["structures"] if item["kind"] == "complex_field")
        self.assertTrue(field["details"]["complete"])
        self.assertTrue(field["details"]["separate_path"])
        self.assertEqual(field["status"], DEFERRED)

    def test_plain_paragraph_is_editable_but_special_structure_scopes_fail_closed(self):
        body = (
            '<w:p><w:r><w:t>ordinary text</w:t></w:r></w:p>'
            '<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'
            '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> PAGE </w:instrText></w:r></w:p>'
            '<w:p><w:bookmarkStart w:id="4" w:name="protected"/>'
            '<w:r><w:t>anchored</w:t></w:r><w:bookmarkEnd w:id="4"/></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        assert_edit_target_supported(report, part="word/document.xml", paragraph_index=0)
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(report, part="word/document.xml", paragraph_index=1)
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(report, part="word/document.xml", paragraph_index=2)
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(report, part="word/document.xml", paragraph_index=3)

    def test_unrecognized_wordprocessingml_element_is_reported_and_blocks_its_paragraph(self):
        body = (
            '<w:p><w:r><w:t>before</w:t></w:r>'
            '<w:unknownFutureStructure><w:r><w:t>protected</w:t></w:r></w:unknownFutureStructure>'
            '</w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)
        unknown = [item for item in report["structures"] if item["kind"] == "unknown_wordprocessingml_element"]
        self.assertTrue(unknown)
        self.assertTrue(all(item["status"] == BLOCKED for item in unknown))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(report, part="word/document.xml", paragraph_index=0)

    def test_markup_compatibility_alternate_content_is_blocked_for_target_paragraph(self):
        body = (
            '<w:p><w:r><w:t>before</w:t></w:r>'
            '<mc:AlternateContent><mc:Choice Requires="w14">'
            '<w:r><w:t>modern branch</w:t></w:r>'
            '</mc:Choice><mc:Fallback><w:r><w:t>fallback branch</w:t></w:r>'
            '</mc:Fallback></mc:AlternateContent></w:p>'
        )
        _write_docx(self.docx, (
            f'<?xml version="1.0"?><w:document xmlns:w="{W}" xmlns:m="{M}" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
            f'<w:body>{body}<w:sectPr/></w:body></w:document>'
        ))
        report = classify_docx_structure(self.docx)
        self.assertIn(
            "markup_compatibility_alternate_content",
            {item["kind"] for item in report["structures"]},
        )
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )

    def test_relationships_part_with_wrong_root_namespace_blocks_package(self):
        body = '<w:p><w:r><w:t>ordinary text</w:t></w:r></w:p>'
        malformed_rels = (
            '<Relationships xmlns="urn:wrong-relationships-namespace"></Relationships>'
        )
        _write_docx(self.docx, _document(body), document_rels=malformed_rels)
        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "BLOCKED")
        self.assertIn("INVALID_RELATIONSHIPS_ROOT", {item["code"] for item in report["issues"]})

    def test_unpaired_bookmark_is_reported_as_a_blocking_issue(self):
        body = (
            '<w:p><w:bookmarkStart w:id="4" w:name="unclosed"/>'
            '<w:r><w:t>text</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>later text</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)
        self.assertIn("UNPAIRED_BOOKMARK_RANGE", {item["code"] for item in report["issues"]})
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(report, part="word/document.xml", paragraph_index=0)

    def test_missing_relationship_target_blocks_package(self):
        body = '<w:p><w:hyperlink r:id="rIdMissing"><w:r><w:t>link</w:t></w:r></w:hyperlink></w:p>'
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "BLOCKED")
        self.assertIn("MISSING_RELATIONSHIP_ID", {item["code"] for item in report["issues"]})

    def test_malformed_xml_part_blocks_package_without_silent_skip(self):
        _write_docx(self.docx, _document('<w:p><w:r><w:t>text</w:t></w:r></w:p>'),
                    extra={"word/custom.xml": b"<broken"})
        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "BLOCKED")
        malformed = [item for item in report["parts"] if item["name"] == "word/custom.xml"]
        self.assertEqual(malformed[0]["parse_status"], "MALFORMED_XML")

    def test_python_docx_package_is_classified_and_plain_edit_scope_is_supported(self):
        document = Document()
        document.add_heading("Synthetic heading", level=1)
        document.add_paragraph("A plain paragraph eligible for a controlled edit.")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "cell one"
        table.cell(0, 1).text = "cell two"
        document.save(self.docx)

        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "READY", report["issues"])
        self.assertEqual(report["classification_status"], "COMPLETE")
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=1
        )

    def test_prepare_document_runs_classifier_and_blocks_only_intersecting_structure(self):
        body = (
            '<w:p><w:r><w:t>Replace this phrase</w:t></w:r></w:p>'
            '<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'
        )
        _write_docx(self.docx, _document(body))
        output = self.root / "prepared.docx"
        prepared = prepare_document(
            str(self.docx), str(output),
            [{"part": "word/document.xml", "p_idx": 0,
              "find": "Replace this phrase", "replace": "Updated phrase"}],
            "Structure classifier test",
        )
        self.assertEqual(prepared["report"]["edits_applied"], 1)
        self.assertFalse(output.exists(), "prepare_document must remain an in-memory prepare step.")

        blocked_body = (
            '<w:p><w:r><w:t>Replace this phrase</w:t></w:r>'
            '<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>'
        )
        blocked_docx = self.root / "blocked.docx"
        _write_docx(blocked_docx, _document(blocked_body))
        blocked_output = self.root / "blocked-output.docx"
        with self.assertRaises(StructureClassificationError):
            prepare_document(
                str(blocked_docx), str(blocked_output),
                [{"part": "word/document.xml", "p_idx": 0,
                  "find": "Replace this phrase", "replace": "Updated phrase"}],
                "Structure classifier test",
            )
        self.assertFalse(blocked_output.exists())

    def test_new_insert_runs_do_not_inherit_historical_rpr_change(self):
        source = parse_xml(
            f'<w:r xmlns:w="{W}"><w:rPr><w:rFonts w:ascii="Arial"/>'
            '<w:b/><w:rPrChange w:id="8"><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr>'
            '<w:t>old</w:t></w:r>'.encode("utf-8")
        )
        inserted = make_insert_runs(source, "new")[0]
        inserted_rpr = inserted.find("w:rPr", {"w": W})
        self.assertIsNotNone(inserted_rpr.find("w:b", {"w": W}))
        self.assertEqual(inserted_rpr.find("w:rFonts", {"w": W}).get(qn("ascii")), "Arial")
        self.assertIsNone(inserted_rpr.find("w:rPrChange", {"w": W}))

        # Ordinary fragments still retain the source's historical snapshot;
        # only newly inserted content is stripped of that prior revision state.
        preserved = make_run(source, "split-fragment")
        self.assertIsNotNone(preserved.find("w:rPr/w:rPrChange", {"w": W}))


    def test_settings_proofing_marker_does_not_block_unrelated_body_edit(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        settings = (
            f'<w:settings xmlns:w="{W}">'
            '<w:proofErr w:type="spellStart"/>'
            '</w:settings>'
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            '</Types>'
        ).encode("utf-8")
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": settings, "[Content_Types].xml": content_types},
        )
        report = classify_docx_structure(self.docx)
        marker = next(
            item for item in report["structures"]
            if item["part"] == "word/settings.xml" and item["element"] == "proofErr"
        )
        self.assertEqual(marker["status"], GUARDED)
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=0
        )

if __name__ == "__main__":
    unittest.main()

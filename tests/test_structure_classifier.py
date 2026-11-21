from __future__ import annotations

import io
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
    replace_in_paragraph,
)
from co_reviewer.structure_classifier import (
    BLOCKED,
    DEFERRED,
    GUARDED,
    SUPPORTED,
    StructureClassificationError,
    assert_edit_target_supported,
    assert_edit_target_supported_with_bounded_safe_span,
    classify_docx_structure,
    _PRESERVED_SETTINGS_ROOT_ELEMENTS,
    _PRESERVED_SETTINGS_CHILDREN,
    _PRESERVED_COMPAT_CHILDREN,
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

    def test_structural_event_map_tracks_text_ranges_fields_links_and_revision_wrappers(self):
        body = (
            '<w:p>'
            '<w:bookmarkStart w:id="7" w:name="bookmark"/>'
            '<w:r><w:t>A</w:t><w:t>B</w:t></w:r>'
            '<w:hyperlink r:id="rIdLink"><w:r><w:t>linked</w:t></w:r></w:hyperlink>'
            '<w:bookmarkEnd w:id="7"/>'
            '<w:commentRangeStart w:id="9"/>'
            '<w:r><w:t>commented</w:t></w:r>'
            '<w:commentRangeEnd w:id="9"/>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText xml:space="preserve"> REF _Ref1 </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>1</w:t></w:r>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:ins w:id="11" w:author="Synthetic Author">'
            '<w:r><w:t>inserted</w:t></w:r></w:ins>'
            '<w:r><w:drawing/></w:r>'
            '</w:p>'
        )
        rels = (
            f'<Relationships xmlns="{PKG_REL}">'
            '<Relationship Id="rIdLink" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.invalid" TargetMode="External"/>'
            '</Relationships>'
        )
        _write_docx(self.docx, _document(body), document_rels=rels)
        report = classify_docx_structure(self.docx)

        events = [
            row for row in report["structural_events"]
            if row["part"] == "word/document.xml"
        ]
        self.assertTrue(events)
        orders = [row["xml_order"] for row in events]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(len(orders), len(set(orders)))

        text_nodes = [
            row for row in events
            if row["element"] == "t" and row["namespace"] == W
        ]
        pair_text_nodes = [
            row for row in text_nodes if row.get("text_content") in {"A", "B"}
        ]
        self.assertEqual(len(pair_text_nodes), 2)
        self.assertEqual(
            [row["text_node_index_in_run"] for row in pair_text_nodes],
            [0, 1],
        )
        self.assertEqual(
            pair_text_nodes[0]["run_index"],
            pair_text_nodes[1]["run_index"],
        )
        self.assertEqual(pair_text_nodes[0]["text_offset_in_run"], {"start": 0, "end": 1})
        self.assertEqual(pair_text_nodes[1]["text_offset_in_run"], {"start": 1, "end": 2})

        bookmark_start = next(
            row for row in events
            if row["element"] == "bookmarkStart"
        )
        bookmark_end = next(
            row for row in events
            if row["element"] == "bookmarkEnd"
        )
        self.assertEqual(bookmark_start["paired_ranges"][0]["range_id"], "7")
        self.assertEqual(bookmark_end["paired_ranges"][0]["range_id"], "7")
        self.assertEqual(
            bookmark_start["paired_ranges"][0]["end_path"],
            bookmark_end["element_path"],
        )

        comment_start = next(
            row for row in events
            if row["element"] == "commentRangeStart"
        )
        comment_end = next(
            row for row in events
            if row["element"] == "commentRangeEnd"
        )
        self.assertEqual(comment_start["paired_ranges"][0]["range_id"], "9")
        self.assertEqual(comment_end["paired_ranges"][0]["range_id"], "9")

        field_events = [
            row for row in events
            if row["element"] in {"fldChar", "instrText"}
        ]
        self.assertEqual(len(field_events), 4)
        self.assertTrue(all(row["field_groups"][0]["field_id"] for row in field_events))
        self.assertEqual(
            {row["attributes"].get("w:fldCharType") for row in field_events if row["element"] == "fldChar"},
            {"begin", "separate", "end"},
        )

        hyperlink = next(row for row in events if row["element"] == "hyperlink")
        self.assertEqual(hyperlink["relationship_refs"][0]["relationship_id"], "rIdLink")
        self.assertEqual(hyperlink["relationship_refs"][0]["status"], "EXTERNAL")
        inserted = next(row for row in events if row["element"] == "ins")
        self.assertIn("text_revision_wrapper", {
            item["kind"] for item in inserted["structure_records"]
        })
        drawing = next(row for row in events if row["element"] == "drawing")
        self.assertIn("embedded_object", {
            item["kind"] for item in drawing["structure_records"]
        })
        self.assertEqual(report["summary"]["structural_events"], len(report["structural_events"]))
        self.assertIn("text_node", report["summary"]["structural_events_by_kind"])

    def test_bounded_safe_span_cannot_bypass_comment_field_or_drawing_boundaries(self):
        fixtures = [
            (
                "comment range",
                '<w:p><w:commentRangeStart w:id="8"/>'
                '<w:r><w:t>target wording</w:t></w:r>'
                '<w:commentRangeEnd w:id="8"/></w:p>',
            ),
            (
                "complex field",
                '<w:p>'
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText xml:space="preserve"> REF _Ref1 </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                '<w:r><w:t>target wording</w:t></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
                '</w:p>',
            ),
            (
                "embedded drawing",
                '<w:p><w:r><w:drawing/></w:r>'
                '<w:r><w:t>target wording</w:t></w:r></w:p>',
            ),
        ]
        for label, body in fixtures:
            with self.subTest(structure=label):
                _write_docx(self.docx, _document(body))
                report = classify_docx_structure(self.docx)
                with zipfile.ZipFile(self.docx, "r") as archive:
                    part_root = parse_xml(archive.read("word/document.xml"))
                paragraph = part_root.find(".//w:p", {"w": W})
                self.assertIsNotNone(paragraph)
                with self.assertRaises(StructureClassificationError):
                    assert_edit_target_supported_with_bounded_safe_span(
                        report,
                        part="word/document.xml",
                        paragraph_index=0,
                        paragraph=paragraph,
                        part_root=part_root,
                        target_text="target wording",
                    )

    def test_single_hyperlink_adapter_preserves_relationship_and_rejects_ambiguity(self):
        paragraph = parse_xml(
            f'<w:p xmlns:w="{W}" xmlns:r="{R}">'
            '<w:hyperlink r:id="rIdLink" w:history="1">'
            '<w:r><w:t>target wording</w:t></w:r>'
            '</w:hyperlink></w:p>'
        )
        changed, next_id, reason = replace_in_paragraph(
            paragraph, "target wording", "revised wording",
            "Synthetic Author", "2026-11-21T00:00:00Z", 1,
        )
        self.assertTrue(changed, reason)
        hyperlink = paragraph.find("w:hyperlink", {"w": W})
        self.assertIsNotNone(hyperlink)
        self.assertEqual(hyperlink.get(f"{{{R}}}id"), "rIdLink")
        self.assertEqual(hyperlink.get(f"{{{W}}}history"), "1")
        self.assertIn("revised wording", "".join(paragraph.itertext()))

        ambiguous = parse_xml(
            f'<w:p xmlns:w="{W}" xmlns:r="{R}">'
            '<w:hyperlink r:id="rIdOne"><w:r><w:t>target wording</w:t></w:r></w:hyperlink>'
            '<w:hyperlink r:id="rIdTwo"><w:r><w:t>target wording</w:t></w:r></w:hyperlink>'
            '</w:p>'
        )
        changed, _, reason = replace_in_paragraph(
            ambiguous, "target wording", "revised wording",
            "Synthetic Author", "2026-11-21T00:00:00Z", 1,
        )
        self.assertFalse(changed)
        self.assertIn("ambiguous across hyperlink", reason)

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

    def test_unpaired_bookmark_markers_only_block_their_own_paragraphs(self):
        body = (
            '<w:p><w:bookmarkStart w:id="4" w:name="unclosedStart"/>'
            '<w:r><w:t>start marker paragraph</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>ordinary paragraph after start</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>orphan end paragraph</w:t></w:r>'
            '<w:bookmarkEnd w:id="5"/></w:p>'
            '<w:p><w:r><w:t>ordinary paragraph after end</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>later ordinary paragraph</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        self.assertEqual(report["package_status"], "READY", report["issues"])
        self.assertIn("UNPAIRED_BOOKMARK_RANGE", {item["code"] for item in report["issues"]})
        orphan_records = [
            item for item in report["structures"]
            if item["kind"] == "incomplete_bookmark_range"
        ]
        self.assertEqual({item["paragraph_index"] for item in orphan_records}, {0, 2})
        self.assertTrue(all(
            item["paragraph_indices"] == [item["paragraph_index"]]
            and item["details"]["scope_policy"] == "marker_paragraph_only"
            for item in orphan_records
        ))

        for blocked_index in (0, 2):
            with self.assertRaises(StructureClassificationError):
                assert_edit_target_supported(
                    report, part="word/document.xml", paragraph_index=blocked_index
                )
        for editable_index in (1, 3, 4):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=editable_index
            )

    def test_valid_bookmark_pair_still_blocks_its_exact_cross_paragraph_range(self):
        body = (
            '<w:p><w:bookmarkStart w:id="8" w:name="validRange"/>'
            '<w:r><w:t>range start</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>range middle</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>range end</w:t></w:r><w:bookmarkEnd w:id="8"/></w:p>'
            '<w:p><w:r><w:t>outside the bookmark</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        valid = next(
            item for item in report["structures"]
            if item["kind"] == "bookmark_range"
        )
        self.assertEqual(valid["paragraph_indices"], [0, 1, 2])
        for blocked_index in (0, 1, 2):
            with self.assertRaises(StructureClassificationError):
                assert_edit_target_supported(
                    report, part="word/document.xml", paragraph_index=blocked_index
                )
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=3
        )

    def test_valid_bookmark_pair_with_body_level_end_maps_to_actual_xml_boundary(self):
        body = (
            '<w:p><w:r><w:t>before bookmark</w:t></w:r></w:p>'
            '<w:p><w:bookmarkStart w:id="18" w:name="bodyLevelEnd"/>'
            '<w:r><w:t>bookmark start paragraph</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>bookmark middle paragraph</w:t></w:r></w:p>'
            '<w:bookmarkEnd w:id="18"/>'
            '<w:p><w:r><w:t>after bookmark</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        self.assertEqual(report["package_status"], "READY", report["issues"])
        valid = next(
            item for item in report["structures"]
            if item["kind"] == "bookmark_range"
        )
        self.assertEqual(valid["paragraph_indices"], [1, 2])
        self.assertEqual(
            valid["details"]["scope_mapping"],
            "xml_document_order_paragraph_intersection",
        )
        for blocked_index in (1, 2):
            with self.assertRaises(StructureClassificationError):
                assert_edit_target_supported(
                    report, part="word/document.xml", paragraph_index=blocked_index
                )
        for editable_index in (0, 3):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=editable_index
            )

    def test_valid_bookmark_pair_with_body_level_start_maps_to_actual_xml_boundary(self):
        body = (
            '<w:p><w:r><w:t>before bookmark</w:t></w:r></w:p>'
            '<w:bookmarkStart w:id="19" w:name="bodyLevelStart"/>'
            '<w:p><w:r><w:t>bookmark start paragraph</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>bookmark end paragraph</w:t>'
            '<w:bookmarkEnd w:id="19"/></w:r></w:p>'
            '<w:p><w:r><w:t>after bookmark</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        self.assertEqual(report["package_status"], "READY", report["issues"])
        valid = next(
            item for item in report["structures"]
            if item["kind"] == "bookmark_range"
        )
        self.assertEqual(valid["paragraph_indices"], [1, 2])
        for blocked_index in (1, 2):
            with self.assertRaises(StructureClassificationError):
                assert_edit_target_supported(
                    report, part="word/document.xml", paragraph_index=blocked_index
                )
        for editable_index in (0, 3):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=editable_index
            )

    def test_unmatched_body_level_bookmark_remains_part_scoped_blocker(self):
        body = (
            '<w:p><w:r><w:t>before orphan marker</w:t></w:r></w:p>'
            '<w:bookmarkStart w:id="20" w:name="unmatchedBodyMarker"/>'
            '<w:p><w:r><w:t>after orphan marker</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)

        self.assertEqual(report["package_status"], "READY", report["issues"])
        self.assertIn("UNPAIRED_BOOKMARK_RANGE", {item["code"] for item in report["issues"]})
        orphan = next(
            item for item in report["structures"]
            if item["kind"] == "incomplete_bookmark_range"
        )
        self.assertEqual(orphan["paragraph_indices"], [])
        self.assertEqual(
            orphan["details"]["scope_policy"],
            "part_scope_no_paragraph_anchor",
        )
        for paragraph_index in (0, 1):
            with self.assertRaises(StructureClassificationError):
                assert_edit_target_supported(
                    report, part="word/document.xml", paragraph_index=paragraph_index
                )

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
        from co_reviewer.manual_suggestion import ManualFallbackRequired
        with self.assertRaises(ManualFallbackRequired) as caught:
            prepare_document(
                str(blocked_docx), str(blocked_output),
                [{"part": "word/document.xml", "p_idx": 0,
                  "find": "Replace this phrase", "replace": "Updated phrase"}],
                "Structure classifier test",
            )
        self.assertTrue(caught.exception.suggestion["not_applied"])
        self.assertFalse(caught.exception.suggestion["docx_write_performed"])
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


    def test_standard_word_settings_metadata_does_not_block_body_edits(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        setting_names = (
            "bordersDoNotSurroundHeader",
            "bordersDoNotSurroundFooter",
            "drawingGridHorizontalSpacing",
            "drawingGridVerticalSpacing",
            "displayHorizontalDrawingGridEvery",
            "displayVerticalDrawingGridEvery",
            "noPunctuationKerning",
            "hdrShapeDefaults",
        )
        settings_xml = (
            f'<w:settings xmlns:w="{W}">'
            + "".join(f"<w:{name}/>" for name in setting_names)
            + "</w:settings>"
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            "</Types>"
        ).encode("utf-8")
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": settings_xml, "[Content_Types].xml": content_types},
        )

        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "READY", report["issues"])
        settings = [
            item for item in report["structures"]
            if item["part"] == "word/settings.xml"
        ]
        self.assertEqual({item["element"] for item in settings}, set(setting_names))
        self.assertTrue(all(item["kind"] == "preserved_standard_metadata" for item in settings))
        self.assertTrue(all(item["status"] == SUPPORTED for item in settings))
        self.assertTrue(all(item["details"].get("preserve_unchanged") is True for item in settings))
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=0
        )
        with zipfile.ZipFile(self.docx, "r") as archive:
            self.assertEqual(archive.read("word/settings.xml"), settings_xml)

    def test_unknown_or_misplaced_settings_elements_remain_fail_closed(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        unknown_settings = f'<w:settings xmlns:w="{W}"><w:futureSetting/></w:settings>'.encode()
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            "</Types>"
        ).encode()
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": unknown_settings, "[Content_Types].xml": content_types},
        )
        report = classify_docx_structure(self.docx)
        self.assertTrue(any(
            item["kind"] == "unknown_wordprocessingml_element"
            and item["part"] == "word/settings.xml"
            and item["element"] == "futureSetting"
            and item["status"] == BLOCKED
            for item in report["structures"]
        ))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )

        misplaced_body = (
            '<w:p><w:bordersDoNotSurroundHeader/>'
            '<w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(misplaced_body))
        misplaced = classify_docx_structure(self.docx)
        self.assertTrue(any(
            item["kind"] == "misplaced_standard_metadata"
            and item["status"] == BLOCKED
            for item in misplaced["structures"]
        ))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                misplaced, part="word/document.xml", paragraph_index=0
            )

    def test_document_protection_and_revision_generation_settings_remain_guarded(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        settings_xml = (
            f'<w:settings xmlns:w="{W}">'
            '<w:documentProtection/>'
            '<w:doNotTrackMoves/>'
            '<w:doNotTrackFormatting/>'
            "</w:settings>"
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            "</Types>"
        ).encode("utf-8")
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": settings_xml, "[Content_Types].xml": content_types},
        )
        report = classify_docx_structure(self.docx)
        guarded = [
            item for item in report["structures"]
            if item["part"] == "word/settings.xml"
        ]
        self.assertEqual({item["element"] for item in guarded}, {
            "documentProtection", "doNotTrackMoves", "doNotTrackFormatting"
        })
        self.assertTrue(all(item["kind"] == "document_editing_setting" for item in guarded))
        self.assertTrue(all(item["status"] == GUARDED for item in guarded))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )

    def test_proofing_markers_are_nonblocking_but_permission_markers_remain_guarded(self):
        body = (
            '<w:p><w:proofErr w:type="spellStart"/>'
            '<w:r><w:t>ordinary target text</w:t></w:r>'
            '<w:proofErr w:type="spellEnd"/></w:p>'
            '<w:p><w:permStart w:id="7"/>'
            '<w:r><w:t>permission protected text</w:t></w:r>'
            '<w:permEnd w:id="7"/></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)
        proofing = [
            item for item in report["structures"]
            if item["kind"] == "proofing_range_marker"
        ]
        permissions = [
            item for item in report["structures"]
            if item["kind"] == "protected_range_marker"
        ]
        self.assertEqual(len(proofing), 2)
        self.assertTrue(all(item["status"] == SUPPORTED for item in proofing))
        self.assertEqual(
            {item["details"]["proofing_type"] for item in proofing},
            {"spellStart", "spellEnd"},
        )
        self.assertEqual(len(permissions), 2)
        self.assertTrue(all(item["status"] == GUARDED for item in permissions))
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=0
        )
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=1
            )

    def test_proofing_markers_are_preserved_by_ordinary_run_replacement(self):
        paragraph = parse_xml(
            f'<w:p xmlns:w="{W}">'
            '<w:proofErr w:type="spellStart"/>'
            '<w:r><w:t>Replace this phrase</w:t></w:r>'
            '<w:proofErr w:type="spellEnd"/>'
            "</w:p>".encode("utf-8")
        )
        applied, next_id, message = replace_in_paragraph(
            paragraph, "Replace this phrase", "Revised wording",
            "Test Author", "2026-11-19T00:00:00Z", 1,
        )
        self.assertTrue(applied, message)
        self.assertEqual(
            [item.get(f"{{{W}}}type") for item in paragraph.findall("w:proofErr", {"w": W})],
            ["spellStart", "spellEnd"],
        )
        self.assertEqual(
            "".join(item.text or "" for item in paragraph.findall(".//w:delText", {"w": W})),
            "Replace this phrase",
        )
        self.assertEqual(
            "".join(item.text or "" for item in paragraph.findall(".//w:ins//w:t", {"w": W})),
            "Revised wording",
        )
        children = list(paragraph)
        self.assertEqual(children[0].tag, f"{{{W}}}proofErr")
        self.assertEqual(children[-1].tag, f"{{{W}}}proofErr")

    def test_proofErr_in_settings_part_is_not_accepted_as_valid_proofing_metadata(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        settings_xml = (
            f'<w:settings xmlns:w="{W}">'
            '<w:proofErr w:type="spellStart"/>'
            "</w:settings>"
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            "</Types>"
        ).encode("utf-8")
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": settings_xml, "[Content_Types].xml": content_types},
        )
        report = classify_docx_structure(self.docx)
        marker = next(
            item for item in report["structures"]
            if item["part"] == "word/settings.xml" and item["element"] == "proofErr"
        )
        self.assertEqual(marker["status"], BLOCKED)
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )



    def test_standard_settings_hierarchies_and_style_hidden_metadata_are_preserved(self):
        body = '<w:p><w:r><w:t>Replace this phrase</w:t></w:r></w:p>'
        root_settings = sorted(
            _PRESERVED_SETTINGS_ROOT_ELEMENTS
            - {"footnotePr", "endnotePr", "compat", "docVars"}
        )
        compat_children = sorted(_PRESERVED_COMPAT_CHILDREN - {"compatSetting"})
        settings_xml = (
            f'<w:settings xmlns:w="{W}">'
            + "".join(f"<w:{name}/>" for name in root_settings)
            + '<w:footnotePr><w:pos/><w:numFmt/><w:numStart/><w:numRestart/><w:footnote/></w:footnotePr>'
            + '<w:endnotePr><w:pos/><w:numFmt/><w:numStart/><w:numRestart/><w:endnote/></w:endnotePr>'
            + '<w:compat>'
            + "".join(f"<w:{name}/>" for name in compat_children)
            + '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
            + '</w:compat>'
            + '<w:docVars><w:docVar w:name="LegacyVariable" w:val="KeepMe"/>'
            + '<w:docVar w:name="SecondVariable" w:val="AlsoKeepMe"/></w:docVars>'
            + '</w:settings>'
        ).encode("utf-8")
        styles_xml = (
            f'<w:styles xmlns:w="{W}">'
            '<w:style w:type="paragraph" w:styleId="Normal">'
            '<w:name w:val="Normal"/></w:style>'
            '<w:style w:type="character" w:styleId="InternalOnly">'
            '<w:name w:val="Internal Only"/><w:hidden/></w:style>'
            '</w:styles>'
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            '</Types>'
        ).encode("utf-8")
        original = _write_docx(
            self.docx, _document(body),
            extra={
                "word/settings.xml": settings_xml,
                "word/styles.xml": styles_xml,
                "[Content_Types].xml": content_types,
            },
        )

        report = classify_docx_structure(self.docx)
        self.assertEqual(report["package_status"], "READY", report["issues"])
        preserved = [
            item for item in report["structures"]
            if item["kind"] == "preserved_standard_metadata"
        ]
        expected_counts = {
            "footnotePr": 1, "pos": 2, "numFmt": 2, "numStart": 2,
            "numRestart": 2, "footnote": 1, "endnotePr": 1, "endnote": 1,
            "compat": 1, "compatSetting": 1,
            "docVars": 1, "docVar": 2, "hidden": 1,
        }
        expected_counts.update({name: 1 for name in root_settings})
        expected_counts.update({name: 1 for name in _PRESERVED_COMPAT_CHILDREN})
        actual_counts = {}
        for item in preserved:
            actual_counts[item["element"]] = actual_counts.get(item["element"], 0) + 1
            self.assertEqual(item["status"], SUPPORTED)
            self.assertTrue(item["details"].get("preserve_unchanged"))
            self.assertTrue(item["details"].get("metadata_context"))
        self.assertEqual(actual_counts, expected_counts)
        assert_edit_target_supported(
            report, part="word/document.xml", paragraph_index=0
        )

        prepared = prepare_document(
            str(self.docx), str(self.root / "prepared.docx"),
            [{
                "part": "word/document.xml", "p_idx": 0,
                "find": "Replace this phrase", "replace": "Updated wording",
            }],
            "Metadata preservation test",
        )
        self.assertEqual(prepared["report"]["modified_parts"], ["word/document.xml"])
        self.assertEqual(prepared["report"]["validation"]["untouched_parts"], "byte-identical")
        with zipfile.ZipFile(io.BytesIO(prepared["payload"]), "r") as archive:
            self.assertEqual(archive.read("word/settings.xml"), settings_xml)
            self.assertEqual(archive.read("word/styles.xml"), styles_xml)
            self.assertEqual(archive.read("[Content_Types].xml"), content_types)

    def test_unknown_children_inside_known_settings_containers_remain_fail_closed(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        settings_xml = (
            f'<w:settings xmlns:w="{W}">'
            '<w:footnotePr><w:futureFootnoteProperty/></w:footnotePr>'
            '<w:compat><w:futureCompatOption/></w:compat>'
            '<w:docVars><w:futureVariableShape/></w:docVars>'
            '</w:settings>'
        ).encode("utf-8")
        content_types = (
            f'<Types xmlns="{CT}">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
            '</Types>'
        ).encode("utf-8")
        _write_docx(
            self.docx, _document(body),
            extra={"word/settings.xml": settings_xml, "[Content_Types].xml": content_types},
        )
        report = classify_docx_structure(self.docx)
        blockers = [
            item for item in report["structures"]
            if item["status"] == BLOCKED and item["part"] == "word/settings.xml"
        ]
        self.assertEqual(
            {item["element"] for item in blockers},
            {"futureFootnoteProperty", "futureCompatOption", "futureVariableShape"},
        )
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )

    def test_settings_only_metadata_outside_registered_parent_context_remains_blocked(self):
        body = (
            '<w:p><w:spaceForUL/>'
            '<w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        )
        _write_docx(self.docx, _document(body))
        report = classify_docx_structure(self.docx)
        self.assertTrue(any(
            item["element"] == "spaceForUL"
            and item["status"] == BLOCKED
            for item in report["structures"]
        ))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )

    def test_style_hidden_is_only_preserved_in_a_style_definition(self):
        body = '<w:p><w:r><w:t>ordinary target text</w:t></w:r></w:p>'
        misplaced_styles = (
            f'<w:styles xmlns:w="{W}"><w:hidden/></w:styles>'
        ).encode("utf-8")
        _write_docx(self.docx, _document(body), extra={"word/styles.xml": misplaced_styles})
        report = classify_docx_structure(self.docx)
        self.assertTrue(any(
            item["element"] == "hidden"
            and item["part"] == "word/styles.xml"
            and item["status"] == BLOCKED
            for item in report["structures"]
        ))
        with self.assertRaises(StructureClassificationError):
            assert_edit_target_supported(
                report, part="word/document.xml", paragraph_index=0
            )


if __name__ == "__main__":
    unittest.main()

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import co_reviewer_cli as cli
from co_reviewer.working_view import (
    WorkingViewError, create_working_clean_view,
    inspect_clean_baseline, strip_docx_comments_bytes,
)
from test_revision_engine import make_docx


class WorkingCleanViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = self.root / "source.docx"
        self.marked = self.root / "marked.docx"
        self.clean = self.root / "working_clean.docx"

    def tearDown(self):
        self.temp.cleanup()

    def test_accepts_insertions_rejects_deletions_and_keeps_sources_immutable(self):
        make_docx(self.original, [[
            ("The ", False),
            ("old phrase", True),
            (" remains unchanged.", False),
        ]])
        cli.apply_document(
            str(self.original),
            str(self.marked),
            [{
                "p_idx": 0,
                "find": "old phrase",
                "replace": "new wording",
            }],
            "Working View Test",
        )
        marked_bytes = self.marked.read_bytes()
        original_bytes = self.original.read_bytes()

        report = create_working_clean_view(self.marked, self.clean)

        self.assertTrue(self.clean.is_file())
        self.assertEqual(self.marked.read_bytes(), marked_bytes)
        self.assertEqual(self.original.read_bytes(), original_bytes)
        with zipfile.ZipFile(self.clean) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            self.assertEqual(cli.visible_text(paragraph), "The new wording remains unchanged.")
            self.assertIsNone(root.find(".//w:ins", cli.NS))
            self.assertIsNone(root.find(".//w:del", cli.NS))
            self.assertEqual(archive.read("word/media/image1.png"), b"fixture-image-bytes")
        self.assertEqual(report["counts"]["insertions_accepted"], 1)
        self.assertEqual(report["counts"]["deletions_rejected"], 1)
        self.assertTrue(report["validation"]["input_unchanged"])
        self.assertEqual(report["validation"]["residual_revision_markup"], "none")
        self.assertNotEqual(report["source_sha256"], report["output_sha256"])

    def test_removes_comment_anchors_and_comment_package_parts(self):
        make_docx(self.original, [[("Keep this sentence.", False)]])
        with zipfile.ZipFile(self.original, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(package["word/document.xml"])
        paragraph = root.find(".//w:p", cli.NS)
        start = ET.Element(f"{{{cli.W}}}commentRangeStart", {f"{{{cli.W}}}id": "0"})
        end = ET.Element(f"{{{cli.W}}}commentRangeEnd", {f"{{{cli.W}}}id": "0"})
        run = ET.SubElement(paragraph, f"{{{cli.W}}}r")
        ET.SubElement(run, f"{{{cli.W}}}commentReference", {f"{{{cli.W}}}id": "0"})
        paragraph.insert(0, start)
        paragraph.insert(1, end)
        package["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        package["word/comments.xml"] = b'<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>'
        package["word/_rels/document.xml.rels"] = (
            b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rIdComment" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>'
            b'</Relationships>'
        )
        package["[Content_Types].xml"] = (
            b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
            b'</Types>'
        )
        with zipfile.ZipFile(self.marked, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        report = create_working_clean_view(self.marked, self.clean)
        with zipfile.ZipFile(self.clean) as archive:
            names = set(archive.namelist())
            root = ET.fromstring(archive.read("word/document.xml"))
            self.assertNotIn("word/comments.xml", names)
            self.assertIsNone(root.find(".//w:commentRangeStart", cli.NS))
            self.assertIsNone(root.find(".//w:commentRangeEnd", cli.NS))
            self.assertIsNone(root.find(".//w:commentReference", cli.NS))
            relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            self.assertEqual(len(list(relationships)), 0)
            types = ET.fromstring(archive.read("[Content_Types].xml"))
            self.assertEqual(len(list(types)), 0)
        self.assertGreater(report["counts"]["comment_anchors_removed"], 0)


    def test_baseline_gate_detects_all_unresolved_revision_and_comment_categories(self):
        make_docx(self.original, [[("A baseline paragraph with evidence.", False)]])
        with zipfile.ZipFile(self.original, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(package["word/document.xml"])
        paragraph = root.find(".//w:p", cli.NS)
        for local in ("ins", "del", "moveFrom", "moveTo"):
            wrapper = ET.SubElement(paragraph, f"{{{cli.W}}}{local}", {
                f"{{{cli.W}}}id": str(len(list(paragraph)) + 1),
                f"{{{cli.W}}}author": "Synthetic collaborator",
            })
            run = ET.SubElement(wrapper, f"{{{cli.W}}}r")
            ET.SubElement(run, f"{{{cli.W}}}t").text = f"sample {local} text"
        run = paragraph.find("w:r", cli.NS)
        run_properties = run.find("w:rPr", cli.NS)
        if run_properties is None:
            run_properties = ET.SubElement(run, f"{{{cli.W}}}rPr")
        ET.SubElement(run_properties, f"{{{cli.W}}}rPrChange", {
            f"{{{cli.W}}}id": "90",
        })
        paragraph_properties = paragraph.find("w:pPr", cli.NS)
        if paragraph_properties is None:
            paragraph_properties = ET.Element(f"{{{cli.W}}}pPr")
            paragraph.insert(0, paragraph_properties)
        ET.SubElement(paragraph_properties, f"{{{cli.W}}}pPrChange", {
            f"{{{cli.W}}}id": "91",
        })
        ET.SubElement(paragraph, f"{{{cli.W}}}commentRangeStart", {
            f"{{{cli.W}}}id": "7",
        })
        ET.SubElement(paragraph, f"{{{cli.W}}}commentRangeEnd", {
            f"{{{cli.W}}}id": "7",
        })
        reference_run = ET.SubElement(paragraph, f"{{{cli.W}}}r")
        ET.SubElement(reference_run, f"{{{cli.W}}}commentReference", {
            f"{{{cli.W}}}id": "7",
        })
        package["word/document.xml"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        package["word/comments.xml"] = (
            b'<?xml version="1.0"?>'
            b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:comment w:id="7" w:author="Synthetic collaborator"><w:p><w:r><w:t>Internal note</w:t></w:r></w:p></w:comment>'
            b'</w:comments>'
        )
        rel_root = ET.fromstring(package["word/_rels/document.xml.rels"])
        ET.SubElement(rel_root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", {
            "Id": "rIdComment",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
            "Target": "comments.xml",
        })
        package["word/_rels/document.xml.rels"] = ET.tostring(
            rel_root, encoding="utf-8", xml_declaration=True
        )
        content_root = ET.fromstring(package["[Content_Types].xml"])
        ET.SubElement(content_root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
            "PartName": "/word/comments.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        })
        package["[Content_Types].xml"] = ET.tostring(
            content_root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.original, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        report = inspect_clean_baseline(self.original)
        self.assertFalse(report["clean"])
        self.assertGreater(report["blocking_counts"]["insertions"], 0)
        self.assertGreater(report["blocking_counts"]["deletions"], 0)
        self.assertGreater(report["blocking_counts"]["move_from"], 0)
        self.assertGreater(report["blocking_counts"]["move_to"], 0)
        self.assertGreater(report["blocking_counts"]["formatting_changes"], 0)
        self.assertGreater(report["blocking_counts"]["comments"], 0)
        self.assertGreater(report["blocking_counts"]["comment_anchors"], 0)
        self.assertTrue(all("part" in issue and "category" in issue for issue in report["issues"]))

    def test_marked_comment_cleanup_removes_comment_parts_but_preserves_track_changes(self):
        make_docx(self.original, [[("A marked paragraph.", False)]])
        with zipfile.ZipFile(self.original, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(package["word/document.xml"])
        paragraph = root.find(".//w:p", cli.NS)
        insertion = ET.SubElement(paragraph, f"{{{cli.W}}}ins", {
            f"{{{cli.W}}}id": "11",
            f"{{{cli.W}}}author": "Co_Reviewer",
        })
        insertion_run = ET.SubElement(insertion, f"{{{cli.W}}}r")
        ET.SubElement(insertion_run, f"{{{cli.W}}}t").text = "revised"
        deletion = ET.SubElement(paragraph, f"{{{cli.W}}}del", {
            f"{{{cli.W}}}id": "12",
            f"{{{cli.W}}}author": "Co_Reviewer",
        })
        deletion_run = ET.SubElement(deletion, f"{{{cli.W}}}r")
        ET.SubElement(deletion_run, f"{{{cli.W}}}delText").text = "old"
        ET.SubElement(paragraph, f"{{{cli.W}}}commentRangeStart", {
            f"{{{cli.W}}}id": "7",
        })
        ET.SubElement(paragraph, f"{{{cli.W}}}commentRangeEnd", {
            f"{{{cli.W}}}id": "7",
        })
        reference_run = ET.SubElement(paragraph, f"{{{cli.W}}}r")
        ET.SubElement(reference_run, f"{{{cli.W}}}commentReference", {
            f"{{{cli.W}}}id": "7",
        })
        package["word/document.xml"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        package["word/comments.xml"] = (
            b'<?xml version="1.0"?>'
            b'<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:comment w:id="7"><w:p/></w:comment></w:comments>'
        )
        rel_root = ET.fromstring(package["word/_rels/document.xml.rels"])
        ET.SubElement(rel_root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", {
            "Id": "rIdComment",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments",
            "Target": "comments.xml",
        })
        package["word/_rels/document.xml.rels"] = ET.tostring(
            rel_root, encoding="utf-8", xml_declaration=True
        )
        content_root = ET.fromstring(package["[Content_Types].xml"])
        ET.SubElement(content_root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
            "PartName": "/word/comments.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
        })
        package["[Content_Types].xml"] = ET.tostring(
            content_root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.marked, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        cleaned, report = strip_docx_comments_bytes(self.marked.read_bytes())
        with zipfile.ZipFile(io.BytesIO(cleaned), "r") as archive:
            names = set(archive.namelist())
            document_root = ET.fromstring(archive.read("word/document.xml"))
            self.assertNotIn("word/comments.xml", names)
            self.assertIsNotNone(document_root.find(".//w:ins", cli.NS))
            self.assertIsNotNone(document_root.find(".//w:del", cli.NS))
            self.assertIsNone(document_root.find(".//w:commentRangeStart", cli.NS))
            self.assertIsNone(document_root.find(".//w:commentRangeEnd", cli.NS))
            self.assertIsNone(document_root.find(".//w:commentReference", cli.NS))
            relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            self.assertFalse(any("comments" in child.attrib.get("Type", "") for child in relationships))
            types = ET.fromstring(archive.read("[Content_Types].xml"))
            self.assertFalse(any("comments" in child.attrib.get("PartName", "") for child in types))
        self.assertEqual(report["validation"], "passed")
        self.assertEqual(report["removed_comment_parts"], ["word/comments.xml"])

    def test_removes_extended_comment_parts_relationships_and_content_types_case_insensitively(self):
        make_docx(self.original, [[("Keep this sentence.", False)]])
        with zipfile.ZipFile(self.original, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}

        comment_parts = {
            "word/commentsExtended.xml": b'<w15:commentsEx xmlns:w15="urn:synthetic:w15"/>',
            "word/commentsIds.xml": b'<w16cid:commentsIds xmlns:w16cid="urn:synthetic:w16cid"/>',
            "word/commentsExtensible.xml": b'<w16cex:commentsExtensible xmlns:w16cex="urn:synthetic:w16cex"/>',
            "word/people.xml": b'<w15:people xmlns:w15="urn:synthetic:w15"/>',
        }
        package.update(comment_parts)
        rel_root = ET.fromstring(package["word/_rels/document.xml.rels"])
        rel_types = {
            "commentsExtended": "http://schemas.microsoft.com/office/word/2018/wordml/commentsExtended",
            "commentsIds": "http://schemas.microsoft.com/office/word/2016/wordml/commentsIds",
            "commentsExtensible": "http://schemas.microsoft.com/office/word/2018/wordml/commentsExtensible",
            "people": "http://schemas.microsoft.com/office/word/2018/wordml/people",
        }
        for index, (part_name, relationship_type) in enumerate(rel_types.items(), 20):
            ET.SubElement(rel_root, "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship", {
                "Id": f"rId{index}",
                "Type": relationship_type,
                "Target": f"{part_name}.xml",
            })
        package["word/_rels/document.xml.rels"] = ET.tostring(
            rel_root, encoding="utf-8", xml_declaration=True
        )

        content_root = ET.fromstring(package["[Content_Types].xml"])
        for part_path in comment_parts:
            ET.SubElement(content_root, "{http://schemas.openxmlformats.org/package/2006/content-types}Override", {
                "PartName": f"/{part_path}",
                "ContentType": "application/xml",
            })
        package["[Content_Types].xml"] = ET.tostring(
            content_root, encoding="utf-8", xml_declaration=True
        )

        with zipfile.ZipFile(self.marked, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        baseline = inspect_clean_baseline(self.marked)
        self.assertFalse(baseline["clean"])
        self.assertEqual(baseline["blocking_counts"]["comment_parts"], 4)
        self.assertEqual(baseline["blocking_counts"]["comment_relationships"], 4)
        self.assertEqual(baseline["blocking_counts"]["comment_content_type_entries"], 4)

        create_working_clean_view(self.marked, self.clean)
        cleaned, strip_report = strip_docx_comments_bytes(self.marked.read_bytes())
        for payload in (self.clean.read_bytes(), cleaned):
            with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                names = {name.casefold() for name in archive.namelist()}
                self.assertFalse(any(name in {
                    "word/commentsextended.xml",
                    "word/commentsids.xml",
                    "word/commentsextensible.xml",
                    "word/people.xml",
                } for name in names))
                rel_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
                self.assertFalse(any(
                    "/comments" in child.attrib.get("Type", "").casefold()
                    or "/people" in child.attrib.get("Type", "").casefold()
                    for child in rel_root
                ))
                types_root = ET.fromstring(archive.read("[Content_Types].xml"))
                self.assertFalse(any(
                    child.attrib.get("PartName", "").casefold() in {
                        "/word/commentsextended.xml",
                        "/word/commentsids.xml",
                        "/word/commentsextensible.xml",
                        "/word/people.xml",
                    }
                    for child in types_root
                ))
        self.assertEqual(strip_report["validation"], "passed")
        self.assertEqual(len(strip_report["removed_comment_parts"]), 4)

    def test_refuses_unsupported_tracked_table_structure_instead_of_guessing(self):
        make_docx(self.original, [[("Table content remains.", False)]])
        with zipfile.ZipFile(self.original, "r") as archive:
            package = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(package["word/document.xml"])
        body = root.find(".//w:body", cli.NS)
        table = ET.SubElement(body, f"{{{cli.W}}}tbl")
        row = ET.SubElement(table, f"{{{cli.W}}}tr")
        row_properties = ET.SubElement(row, f"{{{cli.W}}}trPr")
        ET.SubElement(row_properties, f"{{{cli.W}}}cellDel", {
            f"{{{cli.W}}}id": "7",
        })
        package["word/document.xml"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.marked, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in package.items():
                archive.writestr(name, data)

        with self.assertRaisesRegex(WorkingViewError, "Unsupported table-structure revision"):
            create_working_clean_view(self.marked, self.clean)
        self.assertFalse(self.clean.exists())

    def test_refuses_overwrite_of_input_or_existing_output(self):
        make_docx(self.original, [[("No tracked changes.", False)]])
        with self.assertRaisesRegex(WorkingViewError, "must not overwrite"):
            create_working_clean_view(self.original, self.original)
        self.clean.write_bytes(b"existing")
        with self.assertRaisesRegex(WorkingViewError, "already exists"):
            create_working_clean_view(self.original, self.clean)
        self.assertEqual(self.clean.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()

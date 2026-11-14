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
from co_reviewer.working_view import WorkingViewError, create_working_clean_view
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

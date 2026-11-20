import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from docx import Document
from lxml import etree as ET
from co_reviewer.document_engine import prepare_document
from co_reviewer.manual_suggestion import ManualFallbackRequired

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def _run(text):
    run = ET.Element(f"{{{W}}}r")
    node = ET.SubElement(run, f"{{{W}}}t")
    node.text = text
    return run


def _bookmark_docx(path):
    document = Document()
    document.add_paragraph("placeholder")
    document.save(path)
    with zipfile.ZipFile(path, "r") as source:
        members = {name: source.read(name) for name in source.namelist()}
    root = ET.fromstring(members["word/document.xml"])
    paragraph = root.find(".//w:body/w:p", NS)
    for child in list(paragraph):
        if child.tag != f"{{{W}}}pPr":
            paragraph.remove(child)
    start_a = ET.Element(f"{{{W}}}bookmarkStart", {f"{{{W}}}id": "1", f"{{{W}}}name": "a"})
    end_a = ET.Element(f"{{{W}}}bookmarkEnd", {f"{{{W}}}id": "1"})
    start_b = ET.Element(f"{{{W}}}bookmarkStart", {f"{{{W}}}id": "2", f"{{{W}}}name": "b"})
    end_b = ET.Element(f"{{{W}}}bookmarkEnd", {f"{{{W}}}id": "2"})
    paragraph.extend([start_a, _run("protected one"), end_a, _run("safe wording"), start_b, _run("protected two"), end_b])
    members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)


class BoundedSafeSpanProductionTests(unittest.TestCase):
    def test_prepare_document_applies_only_the_plain_span_between_bookmark_ranges(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.docx"
            output = Path(temp) / "marked.docx"
            _bookmark_docx(source)
            source_bytes = source.read_bytes()
            prepared = prepare_document(
                str(source), str(output),
                [{
                    "part": "word/document.xml", "p_idx": 0,
                    "find": "safe wording", "replace": "revised wording",
                    "document": "Manuscript",
                }],
                "Test Author",
            )
            result = prepared["report"]["results"][0]
            self.assertEqual(result["structure_safety"]["method"], "bounded_safe_span_v1")
            self.assertEqual(source.read_bytes(), source_bytes)
            with zipfile.ZipFile(io.BytesIO(prepared["payload"]), "r") as archive:
                revised = ET.fromstring(archive.read("word/document.xml"))
            self.assertEqual("".join(node.text or "" for node in revised.findall(".//w:ins//w:t", NS)), "revised wording")
            self.assertEqual(len(revised.findall(".//w:bookmarkStart", NS)), 2)
            self.assertEqual(len(revised.findall(".//w:bookmarkEnd", NS)), 2)

    def test_prepare_document_returns_unapplied_manual_suggestion_for_text_inside_bookmark(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.docx"
            output = Path(temp) / "must-not-exist.docx"
            _bookmark_docx(source)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaises(ManualFallbackRequired) as caught:
                prepare_document(
                    str(source), str(output),
                    [{
                        "part": "word/document.xml", "p_idx": 0,
                        "find": "protected one", "replace": "changed text",
                        "document": "Manuscript", "operation_id": "OP-1",
                        "source_comment_ids": ["C1"],
                        "target_identity_mode": "legacy_p_idx_explicit",
                        "source_sha256": source_hash,
                    }],
                    "Test Author",
                )
            suggestion = caught.exception.suggestion
            self.assertTrue(suggestion["not_applied"])
            self.assertFalse(suggestion["docx_write_performed"])
            self.assertEqual(suggestion["source_sha256"], source_hash)
            self.assertEqual(suggestion["source_excerpt"], "protected one")
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

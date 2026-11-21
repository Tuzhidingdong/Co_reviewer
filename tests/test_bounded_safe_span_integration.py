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


def _paired_range_docx(path, start_local, end_local, *, range_id="7"):
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
    start = ET.Element(f"{{{W}}}{start_local}", {f"{{{W}}}id": range_id})
    end = ET.Element(f"{{{W}}}{end_local}", {f"{{{W}}}id": range_id})
    paragraph.extend([start, _run("protected wording"), end, _run("safe wording")])
    members["word/document.xml"] = ET.tostring(
        root, encoding="utf-8", xml_declaration=True
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, payload in members.items():
            target.writestr(name, payload)


def _bookmark_docx(path, safe_text="safe wording"):
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
    paragraph.extend([start_a, _run("protected one"), end_a, _run(safe_text), start_b, _run("protected two"), end_b])
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

    def test_bounded_safe_span_does_not_bypass_token_protected_span_or_format_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            token_source = root / "token-source.docx"
            token_output = root / "token-output.docx"
            _bookmark_docx(token_source, "capacity 125.0 mAh")
            token_before = token_source.read_bytes()
            with self.assertRaisesRegex(ValueError, "UNPROTECTED_TOKEN_TOUCHED"):
                prepare_document(
                    str(token_source), str(token_output),
                    [{
                        "part": "word/document.xml", "p_idx": 0,
                        "find": "125.0 mAh", "replace": "125 mAh",
                        "document": "Manuscript",
                    }],
                    "Test Author",
                )
            self.assertEqual(token_source.read_bytes(), token_before)
            self.assertFalse(token_output.exists())

            protected_source = root / "protected-source.docx"
            protected_output = root / "protected-output.docx"
            _bookmark_docx(protected_source)
            protected_before = protected_source.read_bytes()
            with self.assertRaisesRegex(ValueError, "UNPROTECTED_TOKEN_TOUCHED"):
                prepare_document(
                    str(protected_source), str(protected_output),
                    [{
                        "part": "word/document.xml", "p_idx": 0,
                        "find": "safe wording", "replace": "revised wording",
                        "document": "Manuscript",
                        "protected_spans": [{"start": 0, "end": 4}],
                    }],
                    "Test Author",
                )
            self.assertEqual(protected_source.read_bytes(), protected_before)
            self.assertFalse(protected_output.exists())

            format_source = root / "format-source.docx"
            format_output = root / "format-output.docx"
            _bookmark_docx(format_source)
            format_before = format_source.read_bytes()
            with self.assertRaisesRegex(
                ValueError, "FORMAT_CHANGE_AUTHORIZATION_REQUIRED"
            ):
                prepare_document(
                    str(format_source), str(format_output),
                    [{
                        "part": "word/document.xml", "p_idx": 0,
                        "find": "safe wording", "replace": "safe wording",
                        "document": "Manuscript",
                        "format_operations": [{
                            "find": "safe wording",
                            "properties": {"bold": True},
                        }],
                    }],
                    "Test Author",
                )
            self.assertEqual(format_source.read_bytes(), format_before)
            self.assertFalse(format_output.exists())

    def test_prepare_document_edits_only_text_outside_comment_and_permission_ranges(self):
        for label, start_local, end_local in (
            ("comment", "commentRangeStart", "commentRangeEnd"),
            ("permission", "permStart", "permEnd"),
        ):
            with self.subTest(range_kind=label), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / "source.docx"
                output = Path(temp) / "marked.docx"
                _paired_range_docx(source, start_local, end_local)
                source_before = source.read_bytes()

                prepared = prepare_document(
                    str(source), str(output),
                    [{
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "find": "safe wording",
                        "replace": "revised wording",
                        "document": "Manuscript",
                    }],
                    "Test Author",
                )
                result = prepared["report"]["results"][0]
                self.assertEqual(result["structure_safety"]["method"], "bounded_safe_span_v1")
                kinds = set(result["structure_safety"]["bounded_structure_kinds"])
                self.assertIn(
                    "comment_range" if label == "comment" else "permission_range",
                    kinds,
                )
                self.assertEqual(source.read_bytes(), source_before)

                with zipfile.ZipFile(io.BytesIO(prepared["payload"]), "r") as archive:
                    revised = ET.fromstring(archive.read("word/document.xml"))
                self.assertEqual(
                    len(revised.findall(f".//w:{start_local}", NS)),
                    1,
                )
                self.assertEqual(
                    len(revised.findall(f".//w:{end_local}", NS)),
                    1,
                )
                self.assertEqual(
                    "".join(node.text or "" for node in revised.findall(".//w:ins//w:t", NS)),
                    "revised wording",
                )
                self.assertEqual(
                    "".join(node.text or "" for node in revised.findall(".//w:del//w:t", NS)),
                    "safe wording",
                )

                blocked_output = Path(temp) / "blocked.docx"
                with self.assertRaises(ManualFallbackRequired) as caught:
                    prepare_document(
                        str(source), str(blocked_output),
                        [{
                            "part": "word/document.xml",
                            "p_idx": 0,
                            "find": "protected wording",
                            "replace": "must not change",
                            "document": "Manuscript",
                        }],
                        "Test Author",
                    )
                self.assertTrue(caught.exception.suggestion["not_applied"])
                self.assertFalse(blocked_output.exists())

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

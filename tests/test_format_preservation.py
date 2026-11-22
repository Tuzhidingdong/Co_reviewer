import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from lxml import etree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.document_engine import (
    NS,
    build_auto_replacement_format_map,
    replace_in_paragraph,
    visible_text,
)


def q(local: str) -> str:
    return f"{{{cli.W}}}{local}"


def make_styled_docx(path: Path, paragraphs: list[list[tuple[str, dict]]]) -> None:
    body = ET.Element(q("body"))
    for chunks in paragraphs:
        p = ET.SubElement(body, q("p"))
        for text, style in chunks:
            r = ET.SubElement(p, q("r"))
            if style:
                rpr = ET.SubElement(r, q("rPr"))
                if style.get("b"):
                    ET.SubElement(rpr, q("b"))
                if style.get("i"):
                    ET.SubElement(rpr, q("i"))
                if style.get("vertAlign"):
                    va = ET.SubElement(rpr, q("vertAlign"))
                    va.set(q("val"), style["vertAlign"])
            t = ET.SubElement(r, q("t"))
            if text != text.strip():
                t.set(f"{{{cli.XML}}}space", "preserve")
            t.text = text
    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class FormatPreservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "input.docx"
        self.output = self.root / "output.docx"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_build_auto_replacement_format_map_preserves_italic_and_uniform_returns_none(self):
        p = ET.Element(q("p"))
        for text, italic in [("The contact angle ", False), ("θ", True), (" is 10°", False)]:
            r = ET.SubElement(p, q("r"))
            if italic:
                rpr = ET.SubElement(r, q("rPr"))
                ET.SubElement(rpr, q("i"))
            t = ET.SubElement(r, q("t"))
            t.text = text

        runs = list(p)
        spans = []
        cursor = 0
        for r in runs:
            val = r.find("w:t", NS).text or ""
            spans.append((cursor, cursor + len(val), r, val))
            cursor += len(val)

        auto_map = build_auto_replacement_format_map(
            spans,
            match_start=0,
            old="The contact angle θ is 10°",
            new="The contact angle θ is 20°",
        )
        self.assertIsNotNone(auto_map)
        self.assertEqual(len(auto_map), 3)
        self.assertEqual(auto_map[0]["replacement_start"], 0)
        self.assertEqual(auto_map[1], {"replacement_start": 18, "replacement_end": 19, "source_offset": 18})
        self.assertEqual(auto_map[2]["replacement_end"], len("The contact angle θ is 20°"))

        # Uniform runs return None
        p_uniform = ET.Element(q("p"))
        for text in ["Hello ", "world"]:
            r = ET.SubElement(p_uniform, q("r"))
            t = ET.SubElement(r, q("t"))
            t.text = text
        u_spans = [(0, 6, p_uniform[0], "Hello "), (6, 11, p_uniform[1], "world")]
        self.assertIsNone(
            build_auto_replacement_format_map(u_spans, match_start=0, old="Hello world", new="Hello earth")
        )

    def test_auto_inherit_format_map_end_to_end_apply_and_verify(self):
        make_styled_docx(self.input, [[
            ("The contact angle ", {}),
            ("θ", {"i": True}),
            (" is ", {}),
            ("10", {"vertAlign": "subscript"}),
            (" degrees.", {}),
        ]])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "REV-FMT-01",
                "reviewer": "R1",
                "comment": "Update degree value while preserving italic θ and subscript",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript",
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                    "p_idx": 0,
                    "find": "The contact angle θ is 10 degrees.",
                    "replace": "The contact angle θ is 20 degrees.",
                    "auto_inherit_format_map": True,
                    "authorized_change": {
                        "intent": "Update value from 10 to 20",
                        "explicitly_authorized_tokens": [
                            {"kind": "number", "text": "10", "replacement": "20"}
                        ],
                    },
                }],
            }],
        }
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        results = cli.apply_plan(plan, "Test Author")
        self.assertEqual(len(results), 1)
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            para = root.find(".//w:p", NS)
            self.assertEqual(visible_text(para), "The contact angle θ is 20 degrees.")
            ins = para.find(".//w:ins", NS)
            ins_runs = ins.findall(".//w:r", NS)
            italic_runs = [r for r in ins_runs if r.find("w:rPr/w:i", NS) is not None]
            sub_runs = [
                r for r in ins_runs
                if (va := r.find("w:rPr/w:vertAlign", NS)) is not None and va.get(q("val")) == "subscript"
            ]
            self.assertEqual("".join(r.find("w:t", NS).text for r in italic_runs), "θ")
            self.assertEqual("".join(r.find("w:t", NS).text for r in sub_runs), "20")

    def test_ambiguous_mixed_style_replacement_still_rejected(self):
        make_styled_docx(self.input, [[
            ("old ", {"b": True}),
            ("phrase", {}),
        ]])
        with self.assertRaises(ValueError) as ctx:
            cli.apply_document(str(self.input), str(self.output), [{
                "p_idx": 0,
                "find": "old phrase",
                "replace": "new wording",
                "auto_inherit_format_map": True,
            }], "Test Author")
        self.assertIn("MIXED_RUN_FORMAT_REPLACEMENT_REQUIRES_EXPLICIT_SEGMENT_PLAN", str(ctx.exception))

    def test_naive_single_segment_flatten_blocked_unless_authorized(self):
        make_styled_docx(self.input, [[
            ("The contact angle ", {}),
            ("θ", {"i": True}),
            (" is 10 degrees.", {}),
        ]])
        naive_map = [{"replacement_start": 0, "replacement_end": len("The contact angle θ is 20 degrees."), "source_offset": 0}]
        with self.assertRaises(ValueError) as ctx:
            cli.apply_document(str(self.input), str(self.output), [{
                "p_idx": 0,
                "find": "The contact angle θ is 10 degrees.",
                "replace": "The contact angle θ is 20 degrees.",
                "replacement_format_map": naive_map,
                "authorized_change": {
                    "intent": "Update value",
                    "explicitly_authorized_tokens": [{"kind": "number", "text": "10", "replacement": "20"}],
                },
            }], "Test Author")
        self.assertIn("NAIVE_SINGLE_SEGMENT_FORMAT_FLATTEN_BLOCKED", str(ctx.exception))

        # Explicit allow_format_flatten=True overrides the guard
        cli.apply_document(str(self.input), str(self.output), [{
            "p_idx": 0,
            "find": "The contact angle θ is 10 degrees.",
            "replace": "The contact angle θ is 20 degrees.",
            "replacement_format_map": naive_map,
            "allow_format_flatten": True,
            "authorized_change": {
                "intent": "Intentional flatten",
                "explicitly_authorized_tokens": [{"kind": "number", "text": "10", "replacement": "20"}],
            },
        }], "Test Author")
        self.assertTrue(self.output.is_file())


if __name__ == "__main__":
    unittest.main()

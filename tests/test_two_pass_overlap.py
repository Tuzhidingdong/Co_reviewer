import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from lxml import etree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.document_engine import NS, apply_format_operation, visible_text


def q(local: str) -> str:
    return f"{{{cli.W}}}{local}"


def make_docx(path: Path, texts: list[str]) -> None:
    body = ET.Element(q("body"))
    for text in texts:
        p = ET.SubElement(body, q("p"))
        r = ET.SubElement(p, q("r"))
        t = ET.SubElement(r, q("t"))
        t.text = text
    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
        '<w:color w:val="000000"/>'
        '<w:vertAlign w:val="baseline"/>'
        '<w:i w:val="0"/>'
        '</w:rPr></w:rPrDefault></w:docDefaults>'
        '</w:styles>'
    ).encode("utf-8")
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/styles.xml": styles_xml,
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class TwoPassOverlapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "input.docx"
        self.output = self.root / "output.docx"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_two_target_compiled_plan_text_then_format_change_passes_verify(self):
        make_docx(self.input, ["The parameter x1 determines stability."])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "REV-2P-01",
                "reviewer": "R1",
                "comment": "Replace x1 with x2 and format 2 as subscript and x as italic",
                "status": "approved",
                "targets": [
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "p_idx": 0,
                        "find": "parameter x1",
                        "replace": "parameter x2",
                        "authorized_change": {"intent": "Update variable name"},
                    },
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "p_idx": 0,
                        "find": "parameter x2",
                        "replace": "parameter x2",
                        "authorized_change": {"intent": "Format subscript 2 and italic x"},
                        "format_authorization": {"intent": "Format subscript 2 and italic x"},
                        "format_operations": [
                            {"find": "x", "properties": {"i": True}},
                            {"find": "2", "properties": {"vertAlign": "subscript"}},
                        ],
                    },
                ],
            }],
        }
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"])
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])

        results = cli.apply_plan(plan, "Test Author")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            para = root.find(".//w:p", NS)
            self.assertEqual(visible_text(para), "The parameter x2 determines stability.")
            self.assertIsNotNone(para.find(".//w:del", NS))
            ins = para.find(".//w:ins", NS)
            self.assertIsNotNone(ins)
            rpr_changes = ins.findall(".//w:rPrChange", NS)
            self.assertGreaterEqual(len(rpr_changes), 2)

    def test_single_edit_two_pass_mode_with_font_color_and_vert_align(self):
        make_docx(self.input, ["Surface tension gamma is measured."])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "REV-2P-02",
                "reviewer": "R2",
                "comment": "Replace gamma with gamma0 and apply font/color/subscript",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript",
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                    "p_idx": 0,
                    "find": "tension gamma",
                    "replace": "tension gamma0",
                    "two_pass": True,
                    "authorized_change": {"intent": "Update symbol to gamma0"},
                    "format_authorization": {
                        "intent": "Style gamma0 with Times New Roman, color, and subscript 0",
                        "semantic_profile_sha256": "b" * 64,
                        "format_zone": "body",
                    },
                    "format_operations": [
                        {"find": "gamma", "scope_find": "tension gamma0", "scope_offset": "tension gamma0".index("gamma"), "properties": {"rFonts": {"ascii": "Times New Roman", "hAnsi": "Times New Roman"}, "color": "1F4E79"}},
                        {"find": "0", "scope_find": "tension gamma0", "scope_offset": "tension gamma0".index("0"), "properties": {"vertAlign": "subscript"}},
                    ],
                }],
            }],
        }
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        results = cli.apply_plan(plan, "Test Author")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

    def test_apply_format_operation_rejects_multiword_subscript_target(self):
        p = ET.Element(q("p"))
        run = ET.SubElement(p, q("r"))
        ET.SubElement(run, q("t")).text = "The whole sentence must remain on the baseline."
        doc_xml = ET.tostring(ET.Element(q("document")), encoding="utf-8")
        ok, next_id, changes, reason = apply_format_operation(
            p,
            "The whole sentence must remain on the baseline.",
            {"vertAlign": "subscript"},
            document_xml=doc_xml,
            styles_xml=None,
            theme_xml=None,
            author="Author",
            date="2026-01-01T00:00:00Z",
            next_revision_id=1,
        )
        self.assertFalse(ok)
        self.assertIn("VERTICAL_ALIGNMENT_TARGET_TOO_BROAD", reason)
        self.assertEqual(next_id, 1)
        self.assertEqual(changes, [])
        self.assertIsNone(run.find("w:rPr/w:vertAlign", NS))

    def test_apply_format_operation_include_inserted_runs_skips_del_and_splits_ins(self):
        p = ET.Element(q("p"))
        r_pre = ET.SubElement(p, q("r"))
        ET.SubElement(r_pre, q("t")).text = "Prefix "
        del_el = ET.SubElement(p, q("del"), {q("id"): "1", q("author"): "A", q("date"): "2026-01-01T00:00:00Z"})
        del_r = ET.SubElement(del_el, q("r"))
        ET.SubElement(del_r, q("delText")).text = "old"
        ins_el = ET.SubElement(p, q("ins"), {q("id"): "2", q("author"): "A", q("date"): "2026-01-01T00:00:00Z"})
        ins_r = ET.SubElement(ins_el, q("r"))
        ET.SubElement(ins_r, q("t")).text = "new_word"

        doc_xml = ET.tostring(ET.Element(q("document")), encoding="utf-8")
        ok, next_id, changes, reason = apply_format_operation(
            p,
            "new_word",
            {"i": True},
            document_xml=doc_xml,
            styles_xml=None,
            theme_xml=None,
            author="Author",
            date="2026-01-01T00:00:00Z",
            next_revision_id=3,
            include_inserted_runs=True,
        )
        self.assertTrue(ok, reason)
        self.assertEqual(next_id, 4)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["revision_id"], 3)
        self.assertIsNotNone(p.find(".//w:del", NS))
        ins_after = p.find(".//w:ins", NS)
        self.assertIsNotNone(ins_after)
        self.assertIsNotNone(ins_after.find(".//w:rPr/w:i", NS))
        self.assertIsNotNone(ins_after.find(".//w:rPr/w:rPrChange", NS))


if __name__ == "__main__":
    unittest.main()

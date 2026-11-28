"""Targeted regression for the two format-mapping defects fixed in this round.

A: complex-script properties (w:bCs/w:iCs) must not become ordinary b/i.
B: explicit format authorization on a linked format target must reach the
   text target that consumes replacement_format_map, without polluting others.
"""
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.document_engine import (
    NS, qn, _direct_format_flags, _applicable_format_flags,
    validate_replacement_format_map, build_auto_replacement_format_map,
)
from co_reviewer.plan_compiler import compile_revision_plan

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(path, paragraphs):
    """paragraphs: list of list of (text, rpr_tags dict). rpr_tags values: True or dict attrs."""
    def esc(name):
        return f"{{{W}}}{name}"

    import xml.etree.ElementTree as ET
    root = ET.Element(esc("document"))
    body = ET.SubElement(root, esc("body"))
    for chunks in paragraphs:
        p = ET.SubElement(body, esc("p"))
        for text, tags in chunks:
            r = ET.SubElement(p, esc("r"))
            if tags:
                rpr = ET.SubElement(r, esc("rPr"))
                for tag, attrs in tags.items():
                    el = ET.SubElement(rpr, esc(tag))
                    if isinstance(attrs, dict):
                        for key, value in attrs.items():
                            el.set(esc(key.split(":")[-1]), str(value))
            t = ET.SubElement(r, esc("t"))
            t.text = text
    ET.SubElement(body, esc("sectPr"))
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def spans_for(path, p_idx=0):
    with zipfile.ZipFile(path) as z:
        root = cli.parse_xml(z.read("word/document.xml"))
    para = root.findall(".//w:body//w:p", cli.NS)[p_idx]
    spans, cursor = [], 0
    for run in para.findall("w:r", cli.NS):
        value = cli.run_plain_text(run) or ""
        if not value:
            continue
        spans.append((cursor, cursor + len(value), run, value))
        cursor += len(value)
    return spans


class ComplexScriptSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, text, tags):
        make_docx(self.source, [[(text, tags)]])
        return spans_for(self.source)[0][2]

    def test_bcs_latin_does_not_become_b(self):
        run = self._run("plain latin", {"bCs": True})
        self.assertIn("bCs", _direct_format_flags(run))
        self.assertNotIn("b", _direct_format_flags(run))
        applicable = _applicable_format_flags(run, "plain latin")
        self.assertNotIn("bCs", applicable)
        self.assertNotIn("b", applicable)

    def test_ics_latin_does_not_become_i(self):
        run = self._run("plain latin", {"iCs": True})
        self.assertIn("iCs", _direct_format_flags(run))
        self.assertNotIn("i", _direct_format_flags(run))
        self.assertNotIn("iCs", _applicable_format_flags(run, "plain latin"))

    def test_plain_b_and_i_keep_normal_meaning(self):
        run_b = self._run("bold latin", {"b": True})
        self.assertIn("b", _applicable_format_flags(run_b, "bold latin"))
        run_i = self._run("italic latin", {"i": {"w:val": "1"}})
        self.assertIn("i", _applicable_format_flags(run_i, "italic latin"))

    def test_false_and_missing_properties_are_not_flags(self):
        run = self._run("latin", {"b": {"w:val": "0"}, "bCs": {"w:val": "false"}, "i": {"w:val": "off"}})
        self.assertEqual(_direct_format_flags(run), frozenset())
        self.assertEqual(_applicable_format_flags(run, "latin"), frozenset())

    def test_true_complex_script_bcs_and_ics_are_preserved(self):
        hebrew = "\u05d0\u05d1\u05d2"
        run_b = self._run(hebrew, {"bCs": True})
        self.assertIn("bCs", _applicable_format_flags(run_b, hebrew))
        self.assertNotIn("b", _applicable_format_flags(run_b, hebrew))
        run_i = self._run(hebrew, {"iCs": True})
        self.assertIn("iCs", _applicable_format_flags(run_i, hebrew))
        self.assertNotIn("i", _applicable_format_flags(run_i, hebrew))

    def test_cs_font_selector_alone_does_not_force_complex_context(self):
        run = self._run("latin", {"bCs": True, "rFonts": {"w:cs": "Arial"}})
        self.assertIn("bCs", _direct_format_flags(run))
        self.assertNotIn("bCs", _applicable_format_flags(run, "latin"))
        self.assertNotIn("b", _applicable_format_flags(run, "latin"))

    def test_rtl_language_forces_complex_script_context(self):
        run = self._run("latin", {"bCs": True, "lang": {"w:cs": "ar-SA"}})
        self.assertIn("bCs", _applicable_format_flags(run, "latin"))
        self.assertNotIn("b", _applicable_format_flags(run, "latin"))

    def test_mixed_latin_complex_and_common_follows_context(self):
        text = "ab \u05d0 12"
        make_docx(self.source, [[(text, {"bCs": True})]])
        span = spans_for(self.source)[0]
        flags = _applicable_format_flags(span[2], span[3])
        self.assertIn("bCs", flags)
        self.assertNotIn("b", flags)

    def test_local_bold_does_not_spread_with_auto_map(self):
        make_docx(self.source, [[
            ("alpha ", {}), ("beta", {"b": True}), (" gamma", {}),
        ]])
        spans = spans_for(self.source)
        result = build_auto_replacement_format_map(spans, match_start=0,
                                                   old="alpha beta gamma",
                                                   new="alpha beta delta gamma")
        insert = next(seg for seg in result
                      if seg["replacement_start"] <= 13 < seg["replacement_end"])
        record = next(row for row in spans if row[0] <= insert["source_offset"] < row[1])
        self.assertNotIn("b", _applicable_format_flags(record[2], record[3]))

    def test_bcs_map_on_latin_does_not_raise_ordinary_b(self):
        make_docx(self.source, [[("alpha ", {"bCs": True}), ("beta", {"bCs": True})]])
        spans = spans_for(self.source)
        validate_replacement_format_map(
            spans, match_start=0, source_length=len("alpha beta"),
            replacement="alpha beta delta",
            format_map=[
                {"replacement_start": 0, "replacement_end": 10, "source_offset": 0},
                {"replacement_start": 10, "replacement_end": 16, "source_offset": 6},
            ],
            authorized_ranges=None,
        )

    def test_true_complex_script_bcs_without_authorization_still_blocked(self):
        make_docx(self.source, [[
            ("\u05d0", {"bCs": True}), ("\u05d1", {}),
        ]])
        spans = spans_for(self.source)
        with self.assertRaisesRegex(ValueError, "UNAUTHORIZED_INHERITANCE"):
            validate_replacement_format_map(
                spans, match_start=0, source_length=2,
                replacement="\u05d0\u05d1\u05d2",
                format_map=[
                    {"replacement_start": 0, "replacement_end": 1, "source_offset": 0},
                    {"replacement_start": 1, "replacement_end": 3, "source_offset": 0},
                ],
                authorized_ranges=None,
            )


class AuthorizationPropagationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _compile(self, intents):
        return compile_revision_plan({
            "documents": {"Manuscript": {
                "input_file": str(self.source), "output_file": str(self.output),
            }},
            "intents": intents,
        })

    def _bold_source(self):
        make_docx(self.source, [[
            ("alpha ", {}), ("beta", {"b": True}), (" gamma", {}),
        ]])

    def _unauthorized_map(self):
        return [
            {"replacement_start": 0, "replacement_end": 11, "source_offset": 0},
            {"replacement_start": 11, "replacement_end": 16, "source_offset": 6},
            {"replacement_start": 16, "replacement_end": 22, "source_offset": 11},
        ]

    def _intent(self, item_id, *, with_auth):
        intent = {
            "id": item_id, "doc_role": "Manuscript",
            "find": "alpha beta gamma", "replace": "alpha beta delta gamma",
            "replacement_format_map": self._unauthorized_map(),
            "intent": "insert delta next to a locally bold run",
        }
        if with_auth:
            intent["format_intent"] = [{
                "find": "alpha beta delta gamma", "properties": {"b": True},
            }]
        return intent

    def test_linked_bold_authorization_reaches_text_target(self):
        self._bold_source()
        plan = self._compile([self._intent("AUTH-01", with_auth=True)])
        before = {
            "authorization_mode": plan["revision_items"][0].get("authorization_mode"),
            "targets": [(t["find"], t["replace"], t.get("p_idx"))
                        for t in plan["revision_items"][0]["targets"]],
        }
        self.assertTrue(cli.preflight_plan(plan)["passed"])
        self.assertEqual(plan["revision_items"][0].get("authorization_mode"), before["authorization_mode"])
        self.assertEqual([(t["find"], t["replace"], t.get("p_idx"))
                          for t in plan["revision_items"][0]["targets"]], before["targets"])

    def test_without_authorization_fail_closed(self):
        self._bold_source()
        with self.assertRaisesRegex(ValueError, "UNAUTHORIZED_INHERITANCE"):
            self._compile([self._intent("NOAUTH-01", with_auth=False)])

    def test_authorization_does_not_pollute_another_item(self):
        make_docx(self.source, [
            [("alpha ", {}), ("beta", {"b": True}), (" gamma", {})],
            [("delta ", {}), ("epsilon", {"b": True}), (" zeta", {})],
        ])
        second = {
            "id": "AUTH-B", "doc_role": "Manuscript",
            "find": "delta epsilon zeta", "replace": "delta epsilon eta zeta",
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 14, "source_offset": 0},
                {"replacement_start": 14, "replacement_end": 17, "source_offset": 6},
                {"replacement_start": 17, "replacement_end": 22, "source_offset": 14},
            ],
            "format_intent": [{"find": "delta epsilon eta zeta", "properties": {"b": True}}],
            "intent": "second authorized target",
        }
        plan = self._compile([self._intent("AUTH-A", with_auth=True), second])
        ops_a = cli._scoped_format_operations(plan, "AUTH-A", 0, plan["revision_items"][0]["targets"][0])
        ops_b = cli._scoped_format_operations(plan, "AUTH-B", 0, plan["revision_items"][1]["targets"][0])
        self.assertTrue(ops_a)
        self.assertTrue(ops_b)
        self.assertTrue(all(op.get("scope_find") == "alpha beta delta gamma" for op in ops_a))
        self.assertTrue(all(op.get("scope_find") == "delta epsilon eta zeta" for op in ops_b))
        self.assertTrue(cli.preflight_plan(plan)["passed"])

    def test_explicit_authorized_ranges_are_not_invented_without_ops(self):
        self._bold_source()
        make_docx(self.source, [[("plain source text", {})]])
        intent = {
            "id": "NOAUTH-03", "doc_role": "Manuscript",
            "find": "plain source text", "replace": "plain source new text",
            "format_intent": [{"find": "new", "properties": {"i": True}}],
            "intent": "authorize italic only",
        }
        plan = self._compile([intent])
        item = plan["revision_items"][0]
        text_target = next(t for t in item["targets"] if t.get("find") == "plain source text")
        scoped = cli._scoped_format_operations(plan, item["id"], 0, text_target)
        ranges = cli.authorized_ranges_from_format_operations(scoped, text_target["replace"])
        self.assertTrue(ranges)
        self.assertTrue(any(row["properties"].get("i") for row in ranges))
        self.assertFalse(any(row["properties"].get("b") for row in ranges))

    def test_multiple_targets_keep_explicit_binding(self):
        make_docx(self.source, [
            [("alpha ", {}), ("beta", {"b": True}), (" gamma", {})],
            [("one ", {}), ("two", {"b": True}), (" three", {})],
        ])
        plan = self._compile([
            self._intent("AUTH-A", with_auth=True),
            {
                "id": "AUTH-B", "doc_role": "Manuscript",
                "find": "one two three", "replace": "one two four three",
                "replacement_format_map": [
                    {"replacement_start": 0, "replacement_end": 8, "source_offset": 0},
                    {"replacement_start": 8, "replacement_end": 12, "source_offset": 4},
                    {"replacement_start": 12, "replacement_end": 18, "source_offset": 8},
                ],
                "format_intent": [{"find": "one two four three", "properties": {"b": True}}],
                "intent": "second authorized target",
            },
        ])
        self.assertTrue(cli.preflight_plan(plan)["passed"])


if __name__ == "__main__":
    unittest.main()

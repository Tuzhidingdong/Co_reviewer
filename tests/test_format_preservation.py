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
from co_reviewer.plan_compiler import compute_token_authorizations


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

        # Mixed-format replace slice returns None by default, or dominant signature when fallback enabled
        self.assertIsNone(
            build_auto_replacement_format_map(
                spans,
                match_start=0,
                old="The contact angle θ is 10°",
                new="The contact angle_phi_is 20°",
            )
        )
        mixed_replace_map = build_auto_replacement_format_map(
            spans,
            match_start=0,
            old="The contact angle θ is 10°",
            new="The contact angle_phi_is 20°",
            allow_dominant_replace_fallback=True,
        )
        self.assertIsNotNone(mixed_replace_map)

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

    def test_multiword_replacement_does_not_leak_sentence_subscript_and_keeps_local_scripts(self):
        original = "–5) and decreases to approximately 10 Å ps-1 at higher NHB (20–25)."
        replacement = "–5) and decreases rapidly to approximately 10 Å ps-1 at higher NHB (20–25)."
        expected_text = replacement
        make_styled_docx(self.input, [[(original, {"vertAlign": "subscript"})]])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "REV-SCRIPT-01",
                "reviewer": "R1",
                "comment": "Revise the sentence and retain only explicitly scoped script formatting",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript",
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                    "p_idx": 0,
                    "find": original,
                    "replace": replacement,
                    "two_pass": True,
                    "authorized_change": {"intent": "Revise sentence wording"},
                    "format_authorization": {
                        "intent": "Italicize N and subscript HB only",
                        "semantic_profile_sha256": "a" * 64,
                        "format_zone": "body",
                    },
                    "format_operations": [
                        {"find": "N", "scope_find": replacement, "scope_offset": replacement.index("N"), "properties": {"i": True}},
                        {"find": "HB", "scope_find": replacement, "scope_offset": replacement.index("HB"), "properties": {"vertAlign": "subscript"}},
                        {"find": "-1", "scope_find": replacement, "scope_offset": replacement.index("-1"), "properties": {"vertAlign": "superscript"}},
                    ],
                }],
            }],
        }
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        results = cli.apply_plan(plan, "Test Author")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            para = root.find(".//w:p", NS)
            self.assertEqual(visible_text(para), expected_text)
            insertion = para.find(".//w:ins", NS)
            self.assertIsNotNone(insertion)
            inserted_runs = insertion.findall(".//w:r", NS)
            inserted_text = "".join(
                node.text or "" for node in insertion.findall(".//w:t", NS)
            )
            self.assertEqual(inserted_text, expected_text)

            run_by_text = {
                "".join(node.text or "" for node in run.findall("w:t", NS)): run
                for run in inserted_runs
            }
            self.assertEqual(run_by_text["N"].find("w:rPr/w:i", NS).get(q("val")), "1")
            self.assertEqual(
                run_by_text["HB"].find("w:rPr/w:vertAlign", NS).get(q("val")),
                "subscript",
            )
            self.assertEqual(
                run_by_text["-1"].find("w:rPr/w:vertAlign", NS).get(q("val")),
                "superscript",
            )
            for text, run in run_by_text.items():
                vertical = run.find("w:rPr/w:vertAlign", NS)
                if text not in {"HB", "-1"}:
                    self.assertIn(
                        vertical.get(q("val")) if vertical is not None else None,
                        {None, "baseline"},
                        f"Unexpected script formatting leaked into {text!r}",
                    )

    def test_mapped_long_replacement_does_not_inherit_two_character_script_run(self):
        old = "The rate was 18 Å ps-1 in the initial run."
        new = "The rate reached approximately 19 Å ps-1 in the final ensemble."
        script_offset = old.index("-1")
        make_styled_docx(self.input, [[
            (old[:script_offset], {}),
            ("-1", {"vertAlign": "superscript"}),
            (old[script_offset + 2:], {}),
        ]])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "REV-MIXED-SCRIPT-01",
                "reviewer": "R1",
                "comment": "Revise the sentence while keeping only ps-1 exponent superscript",
                "status": "approved",
                "targets": [
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "p_idx": 0,
                        "find": old,
                        "replace": new,
                        "two_pass": True,
                        "replacement_format_map": [{
                            "replacement_start": 0,
                            "replacement_end": len(new),
                            "source_offset": script_offset,
                        }],
                        "authorized_change": {
                            **compute_token_authorizations(old, new),
                            "intent": "Revise the sentence wording",
                        },
                        "format_authorization": {
                            "intent": "Only the two-character -1 exponent in ps-1 is superscript",
                            "semantic_profile_sha256": "c" * 64,
                            "format_zone": "body",
                        },
                        "format_operations": [{
                            "find": "-1",
                            "scope_find": new,
                            "scope_offset": new.index("-1"),
                            "properties": {"vertAlign": "superscript"},
                        }],
                    },
                ],
            }],
        }
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(
            plan["revision_items"][0]
        )
        results = cli.apply_plan(plan, "Test Author")
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]["results"]), 1)
        self.assertEqual(results[0]["results"][0]["status"], "applied")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", NS)
            insertion = paragraph.find(".//w:ins", NS)
            self.assertIsNotNone(insertion)
            inserted_runs = insertion.findall(".//w:r", NS)
            inserted_text = "".join(
                node.text or "" for node in insertion.findall(".//w:t", NS)
            )
            self.assertEqual(inserted_text, new)
            scripted = []
            for run in inserted_runs:
                run_text = "".join(node.text or "" for node in run.findall("w:t", NS))
                vertical = run.find("w:rPr/w:vertAlign", NS)
                alignment = vertical.get(q("val")) if vertical is not None else None
                if alignment in {"superscript", "subscript"}:
                    scripted.append((run_text, alignment))
            self.assertEqual(scripted, [("-1", "superscript")])

    def test_verify_rejects_script_inherited_outside_authorized_target(self):
        inserted_text = "The entire sentence is superscript including -1"
        run = ET.Element(q("r"))
        rpr = ET.SubElement(run, q("rPr"))
        vertical = ET.SubElement(rpr, q("vertAlign"))
        vertical.set(q("val"), "superscript")
        text_node = ET.SubElement(run, q("t"))
        text_node.text = inserted_text
        result = cli._verify_inserted_script_scope(
            [run],
            inserted_text,
            [{
                "find": "-1",
                "scope_find": inserted_text,
                "scope_offset": inserted_text.index("-1"),
                "properties": {"vertAlign": "superscript"},
            }],
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["checked"])
        self.assertTrue(any("Unrequested superscript" in error for error in result["errors"]))

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

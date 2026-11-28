"""Targeted synthetic tests for general script-format preservation and Verify.

These tests are format-mechanical only. They use neutral placeholder text and
do not encode any manuscript-specific content, formulas or identifiers.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.document_engine import NS
from co_reviewer.plan_compiler import compute_token_authorizations
from test_format_preservation import make_styled_docx, q


def _paragraph(chunks: list[tuple[str, str | None]]) -> ET.Element:
    """Build a plain paragraph from (text, vertAlign) chunk pairs."""
    para = ET.Element(q("p"))
    for text, alignment in chunks:
        run = ET.SubElement(para, q("r"))
        if alignment:
            rpr = ET.SubElement(run, q("rPr"))
            vertical = ET.SubElement(rpr, q("vertAlign"))
            vertical.set(q("val"), alignment)
        node = ET.SubElement(run, q("t"))
        node.text = text
    return para


class ScriptFormatIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "input.docx"
        self.output = self.root / "output.docx"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(
        self,
        old: str,
        new: str,
        format_map: list[dict],
        *,
        format_operations: list[dict] | None = None,
        two_pass: bool = False,
    ) -> dict:
        target: dict = {
            "document": "Manuscript",
            "input_file": str(self.input),
            "output_file": str(self.output),
            "p_idx": 0,
            "find": old,
            "replace": new,
            "authorized_change": {
                **compute_token_authorizations(old, new),
                "intent": "Neutral placeholder text change",
            },
            "replacement_format_map": format_map,
        }
        if format_operations is not None:
            target["format_operations"] = format_operations
        if two_pass:
            target["two_pass"] = True
            target["format_authorization"] = {
                "intent": "Neutral placeholder formatting",
                "semantic_profile_sha256": "c" * 64,
                "format_zone": "body",
            }
        item = {
            "id": "REV-SCRIPT-TEST",
            "reviewer": "R1",
            "comment": "Neutral placeholder comment",
            "status": "approved",
            "targets": [target],
        }
        item["approval_fingerprint"] = cli.revision_item_fingerprint(item)
        return {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [item],
        }

    def _apply_and_verify(self, plan: dict) -> dict:
        results = cli.apply_plan(plan, "Test Author")
        self.assertEqual(results[0]["results"][0]["status"], "applied")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        return verification

    def _inserted_script_offsets(self) -> dict[int, str]:
        offsets: dict[int, str] = {}
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        paragraph = root.find(".//w:p", NS)
        insertion = paragraph.find(".//w:ins", NS)
        self.assertIsNotNone(insertion)
        cursor = 0
        for run in insertion.findall(".//w:r", NS):
            text = "".join(node.text or "" for node in run.findall("w:t", NS))
            vertical = run.find("w:rPr/w:vertAlign", NS)
            alignment = vertical.get(q("val")) if vertical is not None else None
            if alignment in {"superscript", "subscript"}:
                for offset in range(cursor, cursor + len(text)):
                    offsets[offset] = alignment
            cursor += len(text)
        return offsets

    # 1. Long mixed-format replacement preserves a short source script token
    #    without any explicit Pass-2 operations (the core regression fix).
    def test_long_replacement_preserves_source_script_without_explicit_pass2(self):
        old = "The rate was 10 Å ps-1 in run A."
        new = "The rate was 10 Å ps-1 in run A and also in run B."
        script_offset = old.index("-1")
        make_styled_docx(self.input, [[
            (old[:script_offset], {}),
            ("-1", {"vertAlign": "superscript"}),
            (old[script_offset + 2:], {}),
        ]])
        plan = self._plan(old, new, [{
            "replacement_start": 0,
            "replacement_end": len(new),
            "source_offset": script_offset,
        }])
        verification = self._apply_and_verify(plan)

        self.assertTrue(verification["passed"], verification)
        integrity = verification["results"][0]["checks"]["script_integrity"]
        self.assertTrue(integrity["passed"], integrity)
        self.assertTrue(integrity["checked"])
        self.assertTrue(integrity["source_script_tokens"])
        self.assertTrue(integrity["preserved_tokens"])
        expected_offsets = {
            row["offset"]: row["vertAlign"] for row in integrity["source_script_tokens"]
        }
        self.assertEqual(self._inserted_script_offsets(), expected_offsets)

    # 2. Short multi-character script token (e.g. "2+") is preserved.
    def test_short_script_token_is_preserved(self):
        old = "The ion was 2+ in solution."
        new = "The ion was 2+ right here."
        script_offset = old.index("2+")
        make_styled_docx(self.input, [[
            (old[:script_offset], {}),
            ("2+", {"vertAlign": "superscript"}),
            (old[script_offset + 2:], {}),
        ]])
        plan = self._plan(old, new, [{
            "replacement_start": 0,
            "replacement_end": len(new),
            "source_offset": script_offset,
        }])
        verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        offsets = self._inserted_script_offsets()
        self.assertEqual(offsets.get(new.index("2+")), "superscript")
        self.assertEqual(offsets.get(new.index("2+") + 1), "superscript")

    # 3. Ambiguous / unrecoverable script token fails closed with no output
    #    written when no explicit Pass-2 operations exist.
    def test_unrecoverable_script_token_fails_closed(self):
        old = "The value is 2+ ions today."
        new = "The value is ions today."
        script_offset = old.index("2+")
        make_styled_docx(self.input, [[
            (old[:script_offset], {}),
            ("2+", {"vertAlign": "superscript"}),
            (old[script_offset + 2:], {}),
        ]])
        plan = self._plan(old, new, [{
            "replacement_start": 0,
            "replacement_end": len(new),
            "source_offset": script_offset,
        }])
        with self.assertRaisesRegex(ValueError, "MAPPED_SCRIPT_SEGMENT_NEEDS_EXPLICIT_PASS2"):
            cli.apply_plan(plan, "Test Author")

    # 4. The same ambiguous mapping is allowed to degrade to baseline only
    #    when explicit Pass-2 format operations are authored.
    def test_explicit_pass2_allows_baseline_for_unrecoverable_script(self):
        old = "The value is 2+ ions today."
        new = "The value is ions today."
        script_offset = old.index("2+")
        make_styled_docx(self.input, [[
            (old[:script_offset], {}),
            ("2+", {"vertAlign": "superscript"}),
            (old[script_offset + 2:], {}),
        ]])
        plan = self._plan(
            old,
            new,
            [{
                "replacement_start": 0,
                "replacement_end": len(new),
                "source_offset": script_offset,
            }],
            format_operations=[{
                "find": "ions",
                "scope_find": new,
                "scope_offset": new.index("ions"),
                "properties": {"b": True},
            }],
            two_pass=True,
        )
        verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(self._inserted_script_offsets(), {})

    # 5. Independent Verify detects lost script in the Marked view.
    def test_verify_detects_lost_script_in_marked(self):
        old = "The value is 2+ ions today."
        new = "The value is 2+ ions in this solution today."
        script_offset = old.index("2+")
        source_para = _paragraph([
            (old[:script_offset], None),
            ("2+", "superscript"),
            (old[script_offset + 2:], None),
        ])
        marked_para = _paragraph([(new, None)])
        clean_para = _paragraph([(new, None)])
        integrity = cli.verify_script_integrity(
            source_para, marked_para, clean_para,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(integrity["passed"], integrity)
        self.assertTrue(integrity["lost_tokens"])
        self.assertEqual(integrity["expected_vertAlign"], "superscript")
        self.assertIn("marked", [row["view"] for row in integrity["lost_tokens"]])

    # 6. Independent Verify detects lost script in the Clean view.
    def test_verify_detects_lost_script_in_clean(self):
        old = "The value is 2+ ions today."
        new = "The value is 2+ ions in this solution today."
        script_offset = old.index("2+")
        source_para = _paragraph([
            (old[:script_offset], None),
            ("2+", "superscript"),
            (old[script_offset + 2:], None),
        ])
        marked_para = _paragraph([
            (new[:new.index("2+")], None),
            ("2+", "superscript"),
            (new[new.index("2+") + 2:], None),
        ])
        clean_para = _paragraph([(new, None)])
        integrity = cli.verify_script_integrity(
            source_para, marked_para, clean_para,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(integrity["passed"], integrity)
        self.assertTrue(integrity["lost_tokens"])
        self.assertEqual(
            {row["view"] for row in integrity["lost_tokens"]}, {"clean"}
        )

    # 7. Independent Verify rejects unauthorized script formatting.
    def test_verify_rejects_unexpected_script_token(self):
        old = "The value is plain today."
        new = "The value is plain and different today."
        source_para = _paragraph([(old, None)])
        chunks = [(new, None)]
        marked_para = _paragraph(chunks)
        clean_para = _paragraph(chunks)
        # Inject unauthorized script onto a token that has no source script.
        injected = _paragraph([
            (new[:new.index("plain")], None),
            ("plain", "superscript"),
            (new[new.index("plain") + 5:], None),
        ])
        integrity = cli.verify_script_integrity(
            source_para, injected, clean_para,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(integrity["passed"], integrity)
        self.assertTrue(integrity["unexpected_script_tokens"])
        self.assertEqual(integrity["unexpected_script_tokens"][0]["text"], "p")

    # 8. Independent Verify rejects script formatting applied to whitespace.
    def test_verify_rejects_script_on_whitespace(self):
        old = "The value is 2+ ions today."
        new = "The value is 2+ ions in this solution today."
        script_offset = old.index("2+")
        source_para = _paragraph([
            (old[:script_offset], None),
            ("2+", "superscript"),
            (old[script_offset + 2:], None),
        ])
        space = new.index(" ions")
        scripted_chunks = [
            (new[:new.index("2+")], None),
            ("2+", "superscript"),
            (new[new.index("2+") + 2:space], None),
            (" ", "superscript"),
            (new[space + 1:], None),
        ]
        marked_para = _paragraph(scripted_chunks)
        clean_para = _paragraph(scripted_chunks)
        integrity = cli.verify_script_integrity(
            source_para, marked_para, clean_para,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(integrity["passed"], integrity)
        self.assertTrue(integrity["unexpected_script_tokens"])
        self.assertTrue(any(row["text"] == " " for row in integrity["unexpected_script_tokens"]))
        self.assertIsNotNone(integrity["failure_reason"])
        self.assertIn("whitespace", integrity["failure_reason"])

    # 9. A sentence-sized script Run is not treated as a script token and
    #    must not leak script formatting into a plain replacement.
    def test_sentence_sized_script_run_is_not_a_token(self):
        old = "All of this sentence is subscript text."
        new = "All of this replacement is subscript text."
        make_styled_docx(self.input, [[(old, {"vertAlign": "subscript"})]])
        plan = self._plan(
            old,
            new,
            [{
                "replacement_start": 0,
                "replacement_end": len(new),
                "source_offset": 0,
            }],
            format_operations=[{
                "find": "replacement",
                "scope_find": new,
                "scope_offset": new.index("replacement"),
                "properties": {"i": True},
            }],
            two_pass=True,
        )
        verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(self._inserted_script_offsets(), {})

    # 10. A replacement with no script tokens produces no false failure.
    def test_no_script_tokens_produces_no_false_failure(self):
        old = "The answer is forty two."
        new = "The answer is forty two today."
        make_styled_docx(self.input, [[(old, {})]])
        plan = self._plan(old, new, [{
            "replacement_start": 0,
            "replacement_end": len(new),
            "source_offset": 0,
        }])
        verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        integrity = verification["results"][0]["checks"]["script_integrity"]
        self.assertTrue(integrity["passed"])
        self.assertFalse(integrity["lost_tokens"])
        self.assertFalse(integrity["unexpected_script_tokens"])

    # 11. Unicode compatibility script characters in the approved replacement
    #     are honored as authorized native Word script formatting.
    def test_unicode_script_characters_are_authorized(self):
        old = "The formula is AB2."
        new = "The formula is AB₂."
        source_para = _paragraph([(old, None)])
        marked_para = _paragraph([
            (new[:new.index("₂")], None),
            ("2", "subscript"),
            (new[new.index("₂") + 1:], None),
        ])
        clean_para = _paragraph([
            (new[:new.index("₂")], None),
            ("2", "subscript"),
            (new[new.index("₂") + 1:], None),
        ])
        integrity = cli.verify_script_integrity(
            source_para, marked_para, clean_para,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertTrue(integrity["passed"], integrity)
        self.assertTrue(integrity["preserved_tokens"])

    # 12. Explicit Pass-2 format operations are honored as authorized script.
    def test_explicit_script_format_operation_is_honored(self):
        old = "The label is X."
        new = "The label is X1."
        source_para = _paragraph([(old, None)])
        marked_para = _paragraph([
            (new[:new.index("1")], None),
            ("1", "subscript"),
            (new[new.index("1") + 1:], None),
        ])
        integrity = cli.verify_script_integrity(
            source_para, marked_para, marked_para,
            {
                "find": old,
                "replace": new,
                "document": "Manuscript",
                "p_idx": 0,
                "format_operations": [{
                    "find": "1",
                    "properties": {"vertAlign": "subscript"},
                }],
            },
        )
        self.assertTrue(integrity["passed"], integrity)
        self.assertTrue(integrity["preserved_tokens"])

    # 13. Verify never reuses the Apply baseline-reset rule: a long
    #     replacement with a source script token must keep that token script.
    def test_verify_does_not_reuse_baseline_reset_rule(self):
        old = "Measured value 3+ at start."
        new = "Measured value 3+ at start and again at end."
        script_offset = old.index("3+")
        source_para = _paragraph([
            (old[:script_offset], None),
            ("3+", "superscript"),
            (old[script_offset + 2:], None),
        ])
        # A baseline-reset verifier would expect no script at all; the
        # independent checker must instead expect script on the "3+" token.
        integrity = cli.verify_script_integrity(
            source_para, _paragraph([(new, None)]), _paragraph([(new, None)]),
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        expected = {
            row["offset"]: row["vertAlign"] for row in integrity["source_script_tokens"]
        }
        self.assertEqual(expected, {
            new.index("3+"): "superscript",
            new.index("3+") + 1: "superscript",
        })
        self.assertFalse(integrity["passed"])

    # 14. Ambiguous replacement text is recorded, not silently passed.
    def test_ambiguous_replacement_text_is_recorded(self):
        old = "The value is 2+ ions today."
        new = "The value is 2+ ions today."
        source_para = _paragraph([
            (old[:old.index("2+")], None),
            ("2+", "superscript"),
            (old[old.index("2+") + 2:], None),
        ])
        # Output paragraph does not contain the replacement text.
        integrity = cli.verify_script_integrity(
            source_para, _paragraph([("unrelated text", None)]), None,
            {"find": old, "replace": new, "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(integrity["passed"], integrity)
        self.assertTrue(integrity["ambiguous_mappings"])


if __name__ == "__main__":
    unittest.main()

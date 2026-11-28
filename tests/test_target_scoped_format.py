# -*- coding: utf-8 -*-
"""Generic regressions for target-scoped FormatChange, char-level script mapping and role-aware QA."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.final_gate import _infer_quality_profile  # noqa: E402
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402
from co_reviewer.document_engine import NS, parse_xml, qn  # noqa: E402
from lxml import etree as ET  # noqa: E402


def _paragraph(runs: list[tuple[str, str | None]]) -> ET.Element:
    paragraph = ET.Element(qn("p"))
    for text, alignment in runs:
        run = ET.SubElement(paragraph, qn("r"))
        if alignment:
            rpr = ET.SubElement(run, qn("rPr"))
            vertical = ET.SubElement(rpr, qn("vertAlign"))
            vertical.set(qn("val"), alignment)
        node = ET.SubElement(run, qn("t"))
        node.text = text
    return paragraph


def _make_docx(path: Path, paragraphs: list[list[tuple[str, dict]]]) -> None:
    doc = Document()
    for index, runs in enumerate(paragraphs):
        paragraph = doc.add_paragraph() if index == 0 else doc.add_paragraph()
        for text, props in runs:
            run = paragraph.add_run(text)
            if props.get("subscript"):
                run.font.subscript = True
            if props.get("superscript"):
                run.font.superscript = True
            if props.get("italic"):
                run.italic = True
            if props.get("bold"):
                run.bold = True
    doc.save(path)


class TargetScopedFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _plan(self, intents: list[dict]) -> dict:
        return compile_revision_plan({
            "documents": {
                "Manuscript": {
                    "input_file": str(self.source),
                    "output_file": str(self.output),
                }
            },
            "intents": intents,
        })

    def _run(self, plan: dict) -> dict:
        self.assertEqual(cli.validate_plan(plan, require_approval=False), [])
        self.assertTrue(cli.preflight_plan(plan)["passed"])
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        ids = [item["id"] for item in plan["revision_items"]]
        self.assertEqual(cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path), ids=ids, revision_ledger=None, actor="Neutral Test",
        )), 0)
        approved = json.loads(plan_path.read_text(encoding="utf-8"))
        reports = cli.apply_plan(approved, "Neutral Test")
        self.last_reports = reports
        for item in approved["revision_items"]:
            item["status"] = "applied"
        verification = cli.verify_apply_report(approved, reports)
        return verification

    def _inserted_script_offsets(self) -> dict[int, str]:
        offsets: dict[int, str] = {}
        with zipfile.ZipFile(self.output) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        for insertion in root.findall(".//w:ins", cli.NS):
            cursor = 0
            for run in insertion.findall(".//w:r", cli.NS):
                text = "".join(node.text or "" for node in run.findall("w:t", cli.NS))
                vertical = run.find("w:rPr/w:vertAlign", cli.NS)
                value = vertical.get(cli.qn("val")) if vertical is not None else None
                if value in {"superscript", "subscript"}:
                    for offset in range(cursor, cursor + len(text)):
                        offsets[offset] = value
                cursor += len(text)
        return offsets

    def test_partial_script_inside_one_source_run(self):
        old = "sample A2 value"
        new = "sample A2 value extended"
        _make_docx(self.source, [[
            ("sample A", {}), ("2", {"subscript" if False else "superscript": True}), (" value", {}),
        ]])
        plan = self._plan([{
            "id": "NEU-001", "doc_role": "Manuscript", "find": old, "replace": new,
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 9, "source_offset": 0},
                {"replacement_start": 9, "replacement_end": 10, "source_offset": 9},
                {"replacement_start": 10, "replacement_end": len(new), "source_offset": 10},
            ],
            "format_intent": {"find": "2", "vertAlign": "subscript"},
            "intent": "Neutral partial-script regression",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(self._inserted_script_offsets(), {8: "subscript"})

    def test_multiple_targets_same_paragraph_are_isolated(self):
        _make_docx(self.source, [[
            ("left ", {}), ("M", {}), (" mid ", {}),
            ("N", {}), (" right", {}),
        ]])
        plan = self._plan([
            {
                "id": "NEU-002A", "doc_role": "Manuscript", "find": "left M", "replace": "left M1",
                "format_intent": {"find": "1", "vertAlign": "subscript"},
                "intent": "Neutral first target",
            },
            {
                "id": "NEU-002B", "doc_role": "Manuscript", "find": "N right", "replace": "N1 right",
                "format_intent": {"find": "1", "vertAlign": "superscript"},
                "intent": "Neutral second target",
            },
        ])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertTrue(all(row["status"] == "verified" for row in verification["results"]))
        self.assertEqual(len(verification["results"]), 4)

    def test_same_format_operation_in_different_paragraphs(self):
        _make_docx(self.source, [
            [("alpha ", {}), ("T", {}), (" here", {})],
            [("beta ", {}), ("T", {}), (" here", {})],
        ])
        plan = self._plan([
            {
                "id": "NEU-003A", "doc_role": "Manuscript", "p_idx": 0,
                "find": "alpha T here", "replace": "alpha T1 here",
                "format_intent": {"find": "1", "vertAlign": "subscript"},
                "intent": "Neutral paragraph one",
            },
            {
                "id": "NEU-003B", "doc_role": "Manuscript", "p_idx": 1,
                "find": "beta T here", "replace": "beta T1 here",
                "format_intent": {"find": "1", "vertAlign": "subscript"},
                "intent": "Neutral paragraph two",
            },
        ])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)

    def test_two_pass_text_and_format_same_target(self):
        old = "value Q here"
        new = "value Q1 here"
        _make_docx(self.source, [[(old, {})]])
        plan = self._plan([{
            "id": "NEU-004", "doc_role": "Manuscript", "find": old, "replace": new,
            "two_pass": True,
            "format_authorization": {
                "intent": "Neutral two-pass",
                "semantic_profile_sha256": "a" * 64,
                "format_zone": "body",
            },
            "format_intent": {"find": "1", "vertAlign": "superscript"},
            "intent": "Neutral two-pass regression",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(self._inserted_script_offsets(), {7: "superscript"})

    def test_pure_format_change(self):
        _make_docx(self.source, [[("plain label here", {})]])
        plan = self._plan([{
            "id": "NEU-005", "doc_role": "Manuscript",
            "find": "plain label here", "replace": "plain label here",
            "format_authorization": {"intent": "Neutral pure format"},
            "format_intent": {"find": "label", "bold": True},
            "intent": "Neutral pure FormatChange",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertTrue(verification["results"][0]["checks"]["format_change_preserved"])

    def test_multi_character_run_split_and_merge(self):
        old = "pre SubSup post"
        new = "pre SubSup extra post"
        _make_docx(self.source, [[
            ("pre ", {}), ("Sub", {"subscript": True}), ("Sup", {"superscript": True}), (" post", {}),
        ]])
        plan = self._plan([{
            "id": "NEU-006", "doc_role": "Manuscript", "find": old, "replace": new,
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 4, "source_offset": 0},
                {"replacement_start": 4, "replacement_end": 7, "source_offset": 4},
                {"replacement_start": 7, "replacement_end": 10, "source_offset": 7},
                {"replacement_start": 10, "replacement_end": len(new), "source_offset": 11},
            ],
            "intent": "Neutral multi-character run split",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)

    def test_length_change_uses_exact_source_offsets(self):
        old = "unit k2 stop"
        new = "unit k2 with a longer tail stop"
        _make_docx(self.source, [[
            ("unit k", {}), ("2", {"subscript": True}), (" stop", {}),
        ]])
        plan = self._plan([{
            "id": "NEU-007", "doc_role": "Manuscript", "find": old, "replace": new,
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 6, "source_offset": 0},
                {"replacement_start": 6, "replacement_end": 7, "source_offset": 6},
                {"replacement_start": 7, "replacement_end": len(new), "source_offset": 7},
            ],
            "intent": "Neutral length-change mapping",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(self._inserted_script_offsets(), {6: "subscript"})

    def test_unauthorized_script_change_fails_closed(self):
        from lxml import etree as ET
        source = _paragraph([("neutral text", None)])
        marked = _paragraph([("neutral ", None), ("text", "subscript")])
        result = cli.verify_script_integrity(
            source, marked, marked,
            {"find": "neutral text", "replace": "neutral text", "document": "Manuscript", "p_idx": 0},
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["unexpected_script_tokens"])

    def test_duplicate_anchor_fails_closed(self):
        _make_docx(self.source, [[("repeat token repeat token", {})]])
        with self.assertRaisesRegex(ValueError, "multiple times"):
            self._plan([{
                "id": "NEU-009", "doc_role": "Manuscript",
                "find": "token", "replace": "item", "intent": "Neutral duplicate anchor",
            }])

    def test_marked_role_allows_tracked_revisions_but_clean_role_rejects_them(self):
        _make_docx(self.source, [[("neutral marked text", {})]])
        report = cli.apply_document(
            str(self.source), str(self.output),
            [{"find": "neutral marked text", "replace": "neutral marked text changed",
              "revision_item_id": "NEU-010", "reviewer": "Neutral", "document": "Manuscript",
              "format_operations": [], "authorized_change": {"intent": "Neutral mark"}}],
            "Neutral Test",
        )
        self.assertTrue(report)
        marked = cli.audit_docx_quality(str(self.output), profile="marked-manuscript")
        self.assertEqual(marked["summary"]["failures"], 0)
        clean = cli.audit_docx_quality(str(self.output), profile="clean-manuscript")
        self.assertGreater(clean["summary"]["failures"], 0)
        self.assertEqual(
            _infer_quality_profile(self.output, None, "marked-cover-letter"),
            "marked-manuscript",
        )
        self.assertEqual(
            _infer_quality_profile(self.output, None, "clean-cover-letter"),
            "cover-letter",
        )

    def test_one_failed_target_does_not_pollute_sibling(self):
        _make_docx(self.source, [[
            ("left ", {}), ("M", {}), (" mid ", {}),
            ("N", {}), (" right", {}),
        ]])
        plan = self._plan([
            {
                "id": "NEU-011A", "doc_role": "Manuscript", "find": "left M", "replace": "left M1",
                "format_intent": {"find": "1", "vertAlign": "subscript"},
                "intent": "Neutral first sibling",
            },
            {
                "id": "NEU-011B", "doc_role": "Manuscript", "find": "N right", "replace": "N1 right",
                "format_intent": {"find": "1", "vertAlign": "superscript"},
                "intent": "Neutral second sibling",
            },
        ])
        verification = self._run(plan)
        self.assertTrue(verification["passed"])
        # Tamper only the second target's approved format property, then re-run
        # independent Verify against the unchanged Apply report.
        tampered_plan = copy.deepcopy(plan)
        for item in tampered_plan["revision_items"]:
            item["status"] = "applied"
            if item["id"] == "NEU-011B":
                for target in item["targets"]:
                    for operation in target.get("format_operations", []):
                        operation["properties"] = {"vertAlign": "subscript"}
        tampered = cli.verify_apply_report(tampered_plan, self.last_reports)
        by_id = {row["revision_item_id"]: row for row in tampered["results"]}
        self.assertEqual(by_id["NEU-011A"]["status"], "verified")
        self.assertEqual(by_id["NEU-011B"]["status"], "failed")

    def test_verify_recomputes_expectation_from_plan(self):
        old = "base value R2 end"
        new = "base value R2 with tail"
        _make_docx(self.source, [[("base value R", {}), ("2", {"subscript": True}), (" end", {})]])
        plan = self._plan([{
            "id": "NEU-012", "doc_role": "Manuscript", "find": old, "replace": new,
            "replacement_format_map": [
                {"replacement_start": 0, "replacement_end": 12, "source_offset": 0},
                {"replacement_start": 12, "replacement_end": 13, "source_offset": 12},
                {"replacement_start": 13, "replacement_end": len(new), "source_offset": 13},
            ],
            "intent": "Neutral independent expectation",
        }])
        verification = self._run(plan)
        self.assertTrue(verification["passed"])
        tampered_plan = copy.deepcopy(plan)
        for item in tampered_plan["revision_items"]:
            target = item["targets"][0]
            target["replace"] = target["replace"].replace("2", "3", 1)
        self.assertFalse(cli.verify_apply_report(tampered_plan, self.last_reports)["passed"])


if __name__ == "__main__":
    unittest.main()

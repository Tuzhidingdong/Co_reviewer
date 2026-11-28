"""General character-level format fidelity and independent Verify regression.

All fixtures use neutral placeholder text. No manuscript-specific terms,
labels or measured values appear in this module.
"""
import copy
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from lxml import etree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.document_engine import NS, qn, visible_text, _applicable_format_flags
from co_reviewer.plan_compiler import compile_revision_plan
from test_format_preservation import make_styled_docx, q


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class CharacterFormatIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def test_complex_script_flags_are_not_applied_without_context(self):
        run = ET.Element(_W + "r")
        rpr = ET.SubElement(run, _W + "rPr")
        ET.SubElement(rpr, _W + "bCs")
        ET.SubElement(rpr, _W + "iCs")
        ET.SubElement(run, _W + "t").text = "αβ"
        self.assertEqual(_applicable_format_flags(run, "αβ"), frozenset())

    def _source(self, paragraphs):
        make_styled_docx(self.source, paragraphs)

    def _compile(self, intents):
        return compile_revision_plan({
            "documents": {"Manuscript": {
                "input_file": str(self.source), "output_file": str(self.output),
            }},
            "intents": intents,
        })

    def _apply_verify(self, plan):
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
        for item in approved["revision_items"]:
            item["status"] = "applied"
        return cli.verify_apply_report(approved, reports)

    def _output_paragraph(self, path, index=0):
        with zipfile.ZipFile(path) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        return root.findall(".//w:body//w:p", NS)[index]

    def test_local_italic_does_not_spread_to_inserted_text(self):
        self._source([[("alpha ", {}), ("beta", {"i": True}), (" gamma", {})]])
        plan = self._compile([{
            "id": "CF-ITALIC-01", "doc_role": "Manuscript",
            "find": "alpha beta gamma", "replace": "alpha beta delta gamma",
            "intent": "Insert a new token next to a locally italic Run.",
        }])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["character_format_integrity"]["status"], "PASS")
        para = self._output_paragraph(self.output)
        inserted = "".join(
            node.text or "" for ins in para.findall(".//w:ins", NS)
            for node in ins.findall(".//w:t", NS))
        self.assertIn("delta", inserted)

    def test_local_bold_does_not_spread_to_inserted_text(self):
        self._source([[("alpha ", {}), ("beta", {"b": True}), (" gamma", {})]])
        plan = self._compile([{
            "id": "CF-BOLD-01", "doc_role": "Manuscript",
            "find": "alpha beta gamma", "replace": "alpha beta delta gamma",
            "intent": "Insert a new token next to a locally bold Run.",
        }])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["character_format_integrity"]["status"], "PASS")

    def test_explicit_map_cannot_inherit_local_italic(self):
        self._source([[("alpha ", {}), ("beta", {"i": True}), (" gamma", {})]])
        with self.assertRaises(ValueError) as caught:
            self._compile([{
                "id": "CF-BADMAP-01", "doc_role": "Manuscript",
                "find": "alpha beta gamma", "replace": "alpha beta delta gamma",
                "replacement_format_map": [
                    {"replacement_start": 0, "replacement_end": 10, "source_offset": 6},
                    {"replacement_start": 10, "replacement_end": 19, "source_offset": 0},
                ],
                "intent": "Attempt to stretch a local italic Run over new text.",
            }])
        self.assertIn("REPLACEMENT_FORMAT_MAP_UNAUTHORIZED_INHERITANCE", str(caught.exception))

    def test_explicit_map_cannot_inherit_local_bold(self):
        self._source([[("alpha ", {}), ("beta", {"b": True}), (" gamma", {})]])
        with self.assertRaises(ValueError) as caught:
            self._compile([{
                "id": "CF-BADMAP-02", "doc_role": "Manuscript",
                "find": "alpha beta gamma", "replace": "alpha beta delta gamma",
                "replacement_format_map": [
                    {"replacement_start": 0, "replacement_end": 10, "source_offset": 6},
                    {"replacement_start": 10, "replacement_end": 19, "source_offset": 0},
                ],
                "intent": "Attempt to stretch a local bold Run over new text.",
            }])
        self.assertIn("REPLACEMENT_FORMAT_MAP_UNAUTHORIZED_INHERITANCE", str(caught.exception))

    def test_authorized_italic_and_bold_are_executed(self):
        self._source([[("alpha beta gamma", {})]])
        plan = self._compile([{
            "id": "CF-AUTH-01", "doc_role": "Manuscript",
            "find": "alpha beta gamma", "replace": "*alpha* **beta** gamma",
            "intent": "Explicitly authorize italic and bold on individual tokens.",
        }])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        para = self._output_paragraph(self.output)
        actual = {}
        cursor = 0
        for run in para.findall(".//w:r", NS):
            text = "".join(node.text or "" for node in run.findall("w:t", NS))
            flags = cli._direct_format_flags(run)
            for offset in range(len(text)):
                actual[cursor + offset] = flags
            cursor += len(text)
        final_text = visible_text(para)
        alpha = final_text.index("alpha")
        beta = final_text.index("beta")
        self.assertTrue(all("i" in actual[alpha + k] for k in range(len("alpha"))))
        self.assertTrue(all("b" in actual[beta + k] for k in range(len("beta"))))

    def test_mixed_run_with_scripts_is_preserved(self):
        self._source([[
            ("alpha ", {}), ("beta", {"i": True}), (" ", {}),
            ("sub", {"vertAlign": "subscript"}), (" ", {}),
            ("sup", {"vertAlign": "superscript"}), (" end", {}),
        ]])
        plan = self._compile([{
            "id": "CF-SCRIPT-01", "doc_role": "Manuscript",
            "find": "alpha beta sub sup end",
            "replace": "alpha beta sub sup new end",
            "intent": "Insert a plain token beside preserved script tokens.",
        }])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["character_format_integrity"]["status"], "PASS")
        self.assertTrue(all(
            row["checks"]["script_integrity"]["passed"]
            for row in verification["results"]
            if row["checks"].get("script_integrity", {}).get("checked")
        ))

    def test_replacement_spanning_multiple_runs_uses_baseline_for_new_text(self):
        self._source([[("alpha ", {}), ("beta", {"i": True}), (" gamma", {})]])
        plan = self._compile([{
            "id": "CF-MULTIRUN-01", "doc_role": "Manuscript",
            "find": "alpha beta gamma", "replace": "alpha beta gamma delta",
            "intent": "Append a token across a multi-Run source span.",
        }])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["character_format_integrity"]["status"], "PASS")

    def test_independent_verify_detects_unauthorized_italic(self):
        self._source([[("alpha beta gamma", {})]])
        make_styled_docx(self.output, [[("alpha delta gamma", {})]])
        source_para = self._output_paragraph(self.source)
        output_para = self._output_paragraph(self.output)
        target = {
            "document": "Manuscript", "p_idx": 0,
            "find": "alpha beta gamma", "replace": "alpha delta gamma",
        }
        report = cli.verify_character_format_integrity(
            source_para, output_para, output_para, target)
        self.assertEqual(report["status"], "PASS")

        # Simulate an unauthorized whole-phrase italic in the actual output.
        make_styled_docx(self.output, [[
            ("alpha ", {}), ("delta", {"i": True}), (" gamma", {}),
        ]])
        tampered = self._output_paragraph(self.output)
        report = cli.verify_character_format_integrity(
            source_para, tampered, tampered, target)
        self.assertEqual(report["status"], "FAIL")
        self.assertFalse(report["passed"])
        violations = report["violations"]
        self.assertTrue(violations)
        sample = violations[0]
        for field in ("view", "document", "paragraph_anchor", "offset",
                      "text", "expected_format", "actual_format", "reason"):
            self.assertIn(field, sample)

    def test_marked_and_clean_views_are_both_checked(self):
        self._source([[("alpha beta gamma", {})]])
        make_styled_docx(self.output, [[("alpha delta gamma", {})]])
        source_para = self._output_paragraph(self.source)
        marked = self._output_paragraph(self.output)
        make_styled_docx(self.output, [[
            ("alpha ", {}), ("delta", {"i": True}), (" gamma", {}),
        ]])
        clean = self._output_paragraph(self.output)
        target = {
            "document": "Manuscript", "p_idx": 0,
            "find": "alpha beta gamma", "replace": "alpha delta gamma",
        }
        report = cli.verify_character_format_integrity(
            source_para, marked, clean, target)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            {row["view"] for row in report["violations"]}, {"clean"})

    def test_two_targets_same_paragraph_have_no_format_bleed(self):
        self._source([[("alpha beta gamma", {})]])
        plan = self._compile([
            {"id": "CF-TWO-01", "doc_role": "Manuscript",
             "find": "alpha", "replace": "alpha1",
             "format_intent": {"find": "1", "vertAlign": "superscript"},
             "intent": "Scoped script on first token."},
            {"id": "CF-TWO-02", "doc_role": "Manuscript",
             "find": "gamma", "replace": "gamma2",
             "format_intent": {"find": "2", "vertAlign": "subscript"},
             "intent": "Scoped script on a different token."},
        ])
        verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["character_format_integrity"]["status"], "PASS")

    def test_final_gate_blocks_when_character_format_is_not_pass(self):
        from co_reviewer.final_gate import evaluate_final_gate
        base_verify = {
            "passed": True, "targets_failed": 0,
            "untracked_text_audit": {"passed": True, "violations": []},
            "results": [],
        }
        good = copy.deepcopy(base_verify)
        good["character_format_integrity"] = {"status": "PASS", "violations": []}
        report = evaluate_final_gate(verify_report=good, docs=[])
        self.assertIn(report["gates"]["character_format_integrity"], {"PASS"})

        for status in ("FAIL", "NOT_VERIFIED"):
            bad = copy.deepcopy(base_verify)
            bad["character_format_integrity"] = {"status": status, "violations": []}
            report = evaluate_final_gate(verify_report=bad, docs=[])
            self.assertEqual(report["verdict"], "BLOCKED")
            self.assertEqual(report["gates"]["character_format_integrity"], status)

        missing = copy.deepcopy(base_verify)
        report = evaluate_final_gate(verify_report=missing, docs=[])
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["character_format_integrity"], "NOT_VERIFIED")

    def test_final_gate_blocks_without_character_format_evidence(self):
        from co_reviewer.final_gate import evaluate_final_gate
        report = evaluate_final_gate(
            verify_report={
                "passed": True, "targets_failed": 0,
                "untracked_text_audit": {"passed": True, "violations": []},
                "results": [],
            }, docs=[])
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertIn("character_format_integrity", report["gates"])


if __name__ == "__main__":
    unittest.main()

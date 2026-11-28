"""General regression tests for format-operation loss and vacuous verification.

All fixtures use neutral placeholder text; no manuscript-specific vocabulary.
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
from co_reviewer.document_engine import NS, qn, validate_replacement_format_map_in_paragraph
from co_reviewer.plan_compiler import compile_revision_plan, parse_inline_format_markup
from test_format_preservation import make_styled_docx, q


class FormatOperationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _make(self, paragraphs):
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
        return approved, reports, cli.verify_apply_report(approved, reports)

    def _format_target(self, plan, item_id):
        item = next(i for i in plan["revision_items"] if i["id"] == item_id)
        return next(t for t in item["targets"] if t.get("format_operations"))

    # ------------------------------------------------------------------
    # Positive: nested markup and token fidelity
    # ------------------------------------------------------------------
    def test_nested_markup_keeps_italic_subscript_superscript_and_combines_properties(self):
        self._make([[("alpha italic beta sub gamma sup delta", {})]])
        plan = self._compile([{
            "id": "NEST-01", "doc_role": "Manuscript",
            "find": "alpha italic beta sub gamma sup delta",
            "replace": "**alpha *italic* beta ~sub~ gamma ^sup^ delta**",
            "intent": "Nested refinement of an authorized bold span.",
        }])
        target = self._format_target(plan, "NEST-01")
        props = [op["properties"] for op in target["format_operations"]]
        self.assertIn({"b": True}, props)
        self.assertIn({"b": True, "i": True}, props)
        self.assertIn({"b": True, "vertAlign": "subscript"}, props)
        self.assertIn({"b": True, "vertAlign": "superscript"}, props)
        contract_props = [tuple(sorted(row["properties"].items()))
                          for row in target["format_operation_contract"]]
        self.assertIn((("b", True),), contract_props)
        self.assertIn((("b", True), ("i", True)), contract_props)
        self.assertIn((("b", True), ("vertAlign", "subscript")), contract_props)
        self.assertIn((("b", True), ("vertAlign", "superscript")), contract_props)
        _approved, _reports, verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["format_operation_contract_integrity"]["status"], "PASS")

    def test_required_character_formats_are_executed_and_verified(self):
        tokens = [
            ("v", "*v*", {"i": True}),
            ("O2", "O~2~", {"vertAlign": "subscript"}),
            ("N2", "N~2~", {"vertAlign": "subscript"}),
            ("Fe2+", "Fe^2+^", {"vertAlign": "superscript"}),
            ("Fe3+", "Fe^3+^", {"vertAlign": "superscript"}),
            ("cm-1", "cm^-1^", {"vertAlign": "superscript"}),
            ("gamma", "*gamma*", {"i": True}),
            ("pseudo", "*ps*seudo", {"i": True}),
            ("Ca(OH)2", "Ca(OH)~2~", {"vertAlign": "subscript"}),
            ("CaCO3", "CaCO~3~", {"vertAlign": "subscript"}),
        ]
        self._make([[("sample token " + token, {})] for token, _rep, _p in tokens])
        intents = [{
            "id": f"TOK-{index:02d}", "doc_role": "Manuscript", "target_p_idx": index,
            "find": "sample token " + token,
            "replace": "sample token " + replacement,
            "intent": "Authorized token format.",
        } for index, (token, replacement, _p) in enumerate(tokens)]
        plan = self._compile(intents)
        _approved, _reports, verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["targets_failed"], 0)

    def test_bold_italic_and_script_stay_within_exact_ranges(self):
        self._make([[("bold x italic y sub z sup w", {})]])
        plan = self._compile([{
            "id": "RANGE-01", "doc_role": "Manuscript",
            "find": "bold x italic y sub z sup w",
            "replace": "bold **x** italic *y* sub ~z~ sup ^w^",
            "intent": "Adjacent independent format ranges.",
        }])
        _approved, _reports, verification = self._apply_verify(plan)
        self.assertTrue(verification["passed"], verification)
        with zipfile.ZipFile(self.output) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        text = "".join(node.text or "" for node in root.findall(".//w:t", NS))
        self.assertEqual(text, "bold x italic y sub z sup w")
        self.assertEqual(verification["format_operation_contract_integrity"]["status"], "PASS")

    # ------------------------------------------------------------------
    # Negative: lost / duplicated / shifted operations and vacuous checks
    # ------------------------------------------------------------------
    def test_dropped_nested_operation_is_blocked_by_validate_plan(self):
        self._make([[("alpha italic beta", {})]])
        plan = self._compile([{
            "id": "LOSS-01", "doc_role": "Manuscript",
            "find": "alpha italic beta",
            "replace": "**alpha *italic* beta**",
            "intent": "Nested italic must survive.",
        }])
        target = self._format_target(plan, "LOSS-01")
        target["format_operations"] = [op for op in target["format_operations"]
                                       if "i" not in op["properties"]]
        errors = cli.validate_plan(plan, require_approval=False)
        self.assertTrue(any("FORMAT_OPERATION_CONTRACT_BLOCKED" in error for error in errors), errors)

    def test_duplicate_format_operation_is_blocked(self):
        self._make([[("alpha beta", {})]])
        plan = self._compile([{
            "id": "DUP-01", "doc_role": "Manuscript",
            "find": "alpha beta", "replace": "*alpha* beta",
            "intent": "Italic on alpha.",
        }])
        target = self._format_target(plan, "DUP-01")
        target["format_operations"].append(copy.deepcopy(target["format_operations"][0]))
        errors = cli.validate_plan(plan, require_approval=False)
        self.assertTrue(any("duplicate format operation" in error for error in errors), errors)

    def test_shifted_character_range_is_blocked(self):
        self._make([[("alpha beta", {})]])
        plan = self._compile([{
            "id": "SHIFT-01", "doc_role": "Manuscript",
            "find": "alpha beta", "replace": "*alpha* beta",
            "intent": "Italic on alpha.",
        }])
        target = self._format_target(plan, "SHIFT-01")
        target["format_operations"][0]["scope_offset"] += 1
        errors = cli.validate_plan(plan, require_approval=False)
        self.assertTrue(any("FORMAT_OPERATION_CONTRACT_BLOCKED" in error for error in errors), errors)

    def test_contract_script_without_located_token_is_not_a_vacuous_pass(self):
        self._make([[("Fe2+ sample", {})]])
        source_para = self._paragraph(0)
        marked = self._paragraph(0)
        target = {
            "document": "Manuscript", "p_idx": 0,
            "find": "Fe2+ sample", "replace": "Fe2+ sample",
            "format_operation_contract": [
                {"start": 2, "end": 4, "properties": {"vertAlign": "superscript"}},
            ],
        }
        report = cli.verify_script_integrity(source_para, marked, marked, target)
        self.assertFalse(report["passed"])
        self.assertIn("contract requires script", str(report.get("failure_reason", "")))

    def test_unauthorized_format_change_detected_by_contract_verify(self):
        self._make([[("alpha beta", {})]])
        source_para = self._paragraph(0)
        marked = self._paragraph(0)
        with zipfile.ZipFile(self.source) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        run = root.find(".//w:r", NS)
        pr = ET.SubElement(run.find("w:rPr", NS) if run.find("w:rPr", NS) is not None
                           else ET.SubElement(run, q("rPr")), q("b"))
        target = {
            "document": "Manuscript", "p_idx": 0,
            "find": "alpha beta", "replace": "alpha beta",
            "format_operation_contract": [
                {"start": 0, "end": 5, "properties": {"i": True}},
            ],
        }
        report = cli.verify_format_operation_contract(source_para, root.findall(".//w:p", NS)[0],
                                                       root.findall(".//w:p", NS)[0], target)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["violations"])

    def test_final_gate_blocks_on_contract_failure(self):
        from co_reviewer.final_gate import evaluate_final_gate
        verify = {
            "passed": True, "targets_failed": 0,
            "untracked_text_audit": {"passed": True, "violations": []},
            "results": [],
            "character_format_integrity": {"status": "PASS", "violations": []},
            "format_operation_contract_integrity": {
                "status": "FAIL", "violations": [{"reason": "contract token not located"}],
            },
        }
        report = evaluate_final_gate(verify_report=verify, docs=[])
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["gates"]["format_operation_contract"], "FAIL")

    def test_verify_final_round_accepts_full_plan_with_linked_format_target(self):
        import hashlib
        self._make([[("alpha beta", {})]])
        plan, reports, verification = self._apply_verify(self._compile([{
            "id": "LINK-01", "doc_role": "Manuscript",
            "find": "alpha beta", "replace": "*alpha* beta",
            "intent": "Italic on alpha.",
        }]))
        doc_hash = hashlib.sha256(self.output.read_bytes()).hexdigest()
        result = cli.verify_final_round(
            plan, verification, [], {str(self.output): doc_hash})
        self.assertTrue(result["passed"], result)
        self.assertNotEqual(result["round_status"], "BLOCKED")

    def test_empty_runs_do_not_hang_format_map_validator(self):
        make_styled_docx(self.source, [[("alpha beta gamma", {})]])
        with zipfile.ZipFile(self.source) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        para = root.findall(".//w:p", NS)[0]
        empty = ET.Element(q("r"))
        ET.SubElement(empty, q("rPr"))
        para.insert(0, copy.deepcopy(empty))
        para.append(copy.deepcopy(empty))
        runs = para.findall(q("r"))
        para.insert(para.index(runs[1]), copy.deepcopy(empty))
        validate_replacement_format_map_in_paragraph(
            para, "alpha beta", "alpha beta gamma", [
                {"replacement_start": 0, "replacement_end": 16, "source_offset": 0},
            ])

    def _paragraph(self, index):
        with zipfile.ZipFile(self.source) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        return root.findall(".//w:body//w:p", NS)[index]


if __name__ == "__main__":
    unittest.main()

"""Test-first regression suite for the minimal format fix.

Created before any production-code change in this task. Historical Round A/B/C
artifacts are read-only baselines; no prior Plan/Coverage/Apply/Verify artifact
is regenerated here.
"""
import json
import sys
import unittest
import zipfile
from pathlib import Path

from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.document_engine import (  # noqa: E402
    NS, _direct_format_flags, visible_text, run_plain_text,
    validate_replacement_format_map,
)
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402
from test_format_preservation import make_styled_docx, q  # noqa: E402

RUNS = Path(r"E:\Writting\Capillary_bridge\08\co_reviewer_runs")
A = RUNS / "round_full_20261126_apply"
B = RUNS / "round-20261127-093653-90b36443"
C = RUNS / "round_real_acceptance_20261127_170000"
SIX = ["MT-09-S1", "MT-09-S2", "MT-10-S1-TEXT", "MT-11-WORDING", "MT-13", "MT-15"]
FIVE = SIX[:-1]


def docx_flags(path, paragraph_index):
    with zipfile.ZipFile(path) as archive:
        root = cli.parse_xml(archive.read("word/document.xml"))
    para = root.findall(".//w:body//w:p", NS)[paragraph_index]
    text = visible_text(para)
    flags = [frozenset() for _ in text]
    cursor = 0
    for run in para.findall(".//w:r", NS):
        value = "".join(node.text or "" for node in run.findall("w:t", NS))
        if not value:
            continue
        props = _direct_format_flags(run)
        for offset in range(cursor, cursor + len(value)):
            flags[offset] = props
        cursor += len(value)
    return text, flags


def source_spans(path, paragraph_index):
    with zipfile.ZipFile(path) as archive:
        root = cli.parse_xml(archive.read("word/document.xml"))
    para = root.findall(".//w:body//w:p", NS)[paragraph_index]
    spans, cursor = [], 0
    for child in para:
        if child.tag != q("r"):
            continue
        value = run_plain_text(child)
        if value is None:
            continue
        if value:
            spans.append((cursor, cursor + len(value), child, value))
            cursor += len(value)
    return spans


class ABaselineCompatibilityTests(unittest.TestCase):
    """A round is the historical success baseline for unaffected content."""

    def test_unaffected_paragraphs_keep_text_and_character_formats(self):
        source = A / "input" / "art_file_water_WangChunli_clean.docx"
        clean = A / "output" / "art_file_water_WangChunli_clean.clean.docx"
        for index in (0, 1, 2, 3, 5, 6, 8, 20, 21, 23, 32, 33):
            src_text, src_flags = docx_flags(source, index)
            out_text, out_flags = docx_flags(clean, index)
            self.assertEqual(src_text, out_text, f"P{index} text changed")
            self.assertEqual(src_flags, out_flags, f"P{index} character format changed")

    def test_a_round_mixed_format_runs_are_preserved(self):
        clean = A / "output" / "art_file_water_WangChunli_clean.clean.docx"
        text, flags = docx_flags(clean, 22)
        self.assertIn("NHB", text)
        italic_n = [i for i, c in enumerate(text) if c == "N" and "i" in flags[i]]
        subscript_hb = [i for i in range(len(text) - 1)
                        if text[i:i + 2] == "HB" and "subscript" in flags[i + 1]]
        superscript_cm = [i for i in range(len(text) - 1)
                          if text[i:i + 2] == "-1" and "superscript" in flags[i + 1]]
        self.assertTrue(italic_n, "italic N lost")
        self.assertTrue(subscript_hb, "subscript HB lost")
        self.assertTrue(superscript_cm, "superscript cm-1 lost")

    def test_si_round_mixed_format_runs_are_preserved(self):
        source = A / "input" / "SI_water_WangChunli_clean.docx"
        clean = A / "output" / "SI_water_WangChunli_clean.clean.docx"
        for index in (0, 1, 2, 3, 20, 40, 60):
            src_text, src_flags = docx_flags(source, index)
            out_text, out_flags = docx_flags(clean, index)
            self.assertEqual(src_text, out_text)
            self.assertEqual(src_flags, out_flags)

    def test_mt08b_and_si09_use_confirmed_correct_formats_not_a_round_defect(self):
        clean_mt = B / "output" / "art_file_water_WangChunli_clean.clean.docx"
        text, flags = docx_flags(clean_mt, 24)
        phrase = "the instantaneous O2 speed (vO2)"
        start = text.find(phrase)
        self.assertGreaterEqual(start, 0)
        for offset in range(start, start + len(phrase)):
            char = text[offset]
            if char == "v":
                self.assertIn("i", flags[offset])
            else:
                self.assertNotIn("i", flags[offset], f"unexpected italic at {char!r}")
            if char == "2":
                self.assertIn("subscript", flags[offset])
        clean_si = B / "output" / "SI_water_WangChunli_clean.clean.docx"
        caption, si_flags = docx_flags(clean_si, 62)
        self.assertTrue(caption.startswith("Fig. S14."))
        self.assertTrue(si_flags[0])
        for offset in range(9, len(caption)):
            self.assertNotIn("b", si_flags[offset], f"unexpected bold at {offset}")
        for token in ("Ca(OH)2", "CaCO3"):
            pos = caption.find(token)
            self.assertGreaterEqual(pos, 0)
            self.assertIn("subscript", si_flags[pos + len(token) - 1])


class BCollapseReproductionTests(unittest.TestCase):
    """B round defect must be captured from real saved artifacts."""

    def test_b_round_compiled_plan_collapsed_six_targets_to_bold_only(self):
        plan = json.loads((B / "plan/revision_plan.instruction_authorized.json").read_text(encoding="utf-8"))
        for item in plan["revision_items"]:
            if item["id"] not in SIX:
                continue
            target = next(t for t in item["targets"] if t.get("format_operations"))
            ops = target["format_operations"]
            self.assertEqual(len(ops), 1, f"{item['id']}: expected B collapse")
            self.assertEqual(ops[0]["properties"], {"b": True})

    def test_b_round_intents_have_no_canonical_nested_format_data(self):
        intents = json.loads((B / "plan/intents.json").read_text(encoding="utf-8"))["intents"]
        for row in intents:
            if row["id"] not in SIX:
                continue
            self.assertTrue(str(row.get("replace", "")).startswith("**"))
            self.assertNotIn("explicit_nested_ops", row)
            self.assertNotIn("format_intent", row)

    def test_side_channel_nested_ops_must_enter_canonical_intent(self):
        # DEFECT REPRODUCTION: production code currently has no canonicalizer.
        from co_reviewer import plan_compiler
        canonicalize = getattr(plan_compiler, "canonicalize_explicit_nested_format_ops", None)
        self.assertIsNotNone(
            canonicalize,
            "DEFECT REPRODUCED: no production path consumes explicit_nested_ops",
        )
        intent = {
            "id": "NEST-SIDE-01", "doc_role": "Manuscript",
            "find": "alpha beta", "replace": "**alpha beta**",
            "explicit_nested_ops": [
                {"find": "beta", "properties": {"i": True}},
            ],
        }
        ops = canonicalize(intent)
        self.assertTrue(any(op.get("find") == "beta" and op.get("properties", {}).get("i")
                            for op in ops), ops)


class ValidatorExplicitAuthorizationTests(unittest.TestCase):
    """Five blocked targets must pass only via character-scoped authorization."""

    def test_current_round_intents_compile_with_explicit_format_authorization(self):
        # DEFECT REPRODUCTION: compile currently raises UNAUTHORIZED_INHERITANCE.
        payload = json.loads((C / "plan/intents.json").read_text(encoding="utf-8"))
        for intent in payload["intents"]:
            if intent.get("action") == "insert_paragraph":
                intent.pop("explicitly_authorized_insertions", None)
        coverage = json.loads((C / "plan/instruction_coverage.json").read_text(encoding="utf-8"))
        for requirement in coverage["requirements"]:
            requirement["instruction_action"] = "insert"
            requirement["instruction_polarity"] = "affirmative"
        plan = compile_revision_plan(payload, base_dir=None, instruction_coverage=coverage)
        ids = {item["id"] for item in plan["revision_items"]}
        self.assertTrue(set(FIVE) <= ids)
        for item in plan["revision_items"]:
            if item["id"] in SIX:
                target = next(t for t in item["targets"] if t.get("format_operation_contract"))
                self.assertTrue(target["format_operation_contract"])

    def test_mt15_keeps_both_superscript_operations(self):
        payload = json.loads((C / "plan/intents.json").read_text(encoding="utf-8"))
        for intent in payload["intents"]:
            if intent.get("action") == "insert_paragraph":
                intent.pop("explicitly_authorized_insertions", None)
        coverage = json.loads((C / "plan/instruction_coverage.json").read_text(encoding="utf-8"))
        for requirement in coverage["requirements"]:
            requirement["instruction_action"] = "insert"
            requirement["instruction_polarity"] = "affirmative"
        plan = compile_revision_plan(payload, base_dir=None, instruction_coverage=coverage)
        item = next(i for i in plan["revision_items"] if i["id"] == "MT-15")
        target = next(t for t in item["targets"] if t.get("format_operations"))
        alignments = [op["properties"]["vertAlign"] for op in target["format_operations"]
                      if "vertAlign" in op["properties"]]
        self.assertEqual(alignments.count("superscript"), 2, alignments)

    def test_unauthorized_bold_outside_scope_still_blocked(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.docx"
            make_styled_docx(source, [[
                ("alpha ", {"i": True}), ("beta", {"b": True}), (" gamma", {}),
            ]])
            spans = source_spans(source, 0)
            old = "alpha beta gamma"
            new = "alpha beta delta"
            # map inserts "delta" onto a bold source Run; no bold authorization
            fm = [
                {"replacement_start": 0, "replacement_end": 6, "source_offset": 0},
                {"replacement_start": 6, "replacement_end": 10, "source_offset": 6},
                {"replacement_start": 10, "replacement_end": 16, "source_offset": 6},
            ]
            with self.assertRaises(ValueError) as caught:
                validate_replacement_format_map(
                    spans, match_start=0, source_length=len(old), replacement=new,
                    format_map=fm,
                    authorized_ranges=[
                        {"start": 0, "end": 5, "properties": {"i": True}},
                    ])
            self.assertIn("UNAUTHORIZED_INHERITANCE", str(caught.exception))

    def test_explicit_bold_within_scope_is_accepted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.docx"
            make_styled_docx(source, [[("alpha beta gamma", {})]])
            spans = source_spans(source, 0)
            old = "alpha beta gamma"
            new = "alpha beta delta"
            fm = [
                {"replacement_start": 0, "replacement_end": 11, "source_offset": 0},
                {"replacement_start": 11, "replacement_end": 16, "source_offset": 11},
            ]
            validate_replacement_format_map(
                spans, match_start=0, source_length=len(old), replacement=new,
                format_map=fm,
                authorized_ranges=[
                    {"start": 11, "end": 16, "properties": {"b": True}},
                ])


class NewChecksAndMatureContractProtectionTests(unittest.TestCase):
    def test_final_gate_blocks_when_contract_checks_are_missing_or_failing(self):
        from co_reviewer.final_gate import evaluate_final_gate
        base = {
            "passed": True, "targets_failed": 0,
            "untracked_text_audit": {"passed": True, "violations": []},
            "results": [],
            "character_format_integrity": {"status": "PASS", "violations": []},
        }
        good = dict(base, format_operation_contract_integrity={"status": "PASS", "violations": []})
        self.assertIn(evaluate_final_gate(verify_report=good, docs=[])["verdict"], {"READY", "BLOCKED"})
        for status in ("FAIL", "NOT_VERIFIED"):
            bad = dict(base, format_operation_contract_integrity={"status": status, "violations": []})
            report = evaluate_final_gate(verify_report=bad, docs=[])
            self.assertEqual(report["verdict"], "BLOCKED")
        missing = dict(base)
        self.assertEqual(evaluate_final_gate(verify_report=missing, docs=[])["verdict"], "BLOCKED")

    def test_a_round_marked_has_native_track_changes_and_clean_has_none(self):
        marked = A / "output" / "art_file_water_WangChunli_clean.marked.docx"
        clean = A / "output" / "art_file_water_WangChunli_clean.clean.docx"
        with zipfile.ZipFile(marked) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        self.assertTrue(root.findall(".//w:ins", NS))
        self.assertTrue(root.findall(".//w:del", NS))
        with zipfile.ZipFile(clean) as archive:
            root = cli.parse_xml(archive.read("word/document.xml"))
        self.assertEqual(root.findall(".//w:ins", NS), [])
        self.assertEqual(root.findall(".//w:del", NS), [])

    def test_reference_transaction_module_remains_available(self):
        from co_reviewer import reference_transaction
        for name in ("compile_reference_transaction", "verify_reference_transaction_outputs"):
            self.assertTrue(hasattr(reference_transaction, name), name)


if __name__ == "__main__":
    unittest.main()

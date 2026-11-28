# -*- coding: utf-8 -*-
"""Instruction-analysis field production: validation and downstream checks."""
from __future__ import annotations

import hashlib, json, sys, tempfile, unittest
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from co_reviewer.instruction_analysis import (  # noqa: E402
    analysis_sha256, analysis_to_coverage, crosscheck_coverage,
    crosscheck_plan, validate_instruction_analysis,
)
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402


class InstructionAnalysisProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.doc = self.root / "manuscript.docx"
        d = Document(); d.add_paragraph("Claim."); d.add_paragraph("References"); d.add_paragraph("[1] Existing.")
        d.save(self.doc)

    def tearDown(self):
        self.temp.cleanup()

    def _analysis(self, polarity="affirmative", action="insert", text="请在参考文献列表末尾增加以下文献："):
        lines = [text, "  `[77] Gamma Author (2021). Test Journal, 5, 11-20.`"]
        txt = self.root / "instruction.txt"
        txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        a = {
            "schema_version": "1.0",
            "source_instruction": {"file": str(txt), "sha256": hashlib.sha256(txt.read_bytes()).hexdigest()},
            "analyses": [{
                "requirement_id": "REQ-A-01", "instruction_action": action,
                "instruction_polarity": polarity, "source_line_start": 1, "source_line_end": 2,
                "source_locator": "L1–L2", "instruction_excerpt": "\n".join(lines),
                "instruction_object": "[77]", "analysis_version": "agent-v1",
            }],
        }
        a["analysis_sha256"] = analysis_sha256(a)
        return a

    def _coverage(self, analysis):
        row = analysis["analyses"][0]
        cov = {
            "schema_version": "1.1",
            "source_instruction": dict(analysis["source_instruction"]),
            "requirements": [{
                "requirement_id": "REQ-A-01", "task_id": "A",
                "source_line_start": 1, "source_line_end": 2,
                "source_locator": "L1–L2", "instruction_excerpt": row["instruction_excerpt"],
                "status": "planned", "revision_targets": [{"revision_item_id": "REV-A1", "target_index": 0}],
            }],
        }
        return analysis_to_coverage(cov, analysis)

    def _plan(self, analysis, coverage):
        return compile_revision_plan({
            "documents": {"Manuscript": {"input_file": str(self.doc), "output_file": str(self.root / "marked.docx")}},
            "intents": [{
                "id": "REV-A1", "doc_role": "Manuscript", "action": "insert_paragraph",
                "position": "after", "find": "References",
                "inserted_text": "[77] Gamma Author (2021). Test Journal, 5, 11-20.",
                "intent": "Insert requested reference.", "instruction_requirement_ids": ["REQ-A-01"],
                "input_file": str(self.doc), "output_file": str(self.root / "marked.docx"),
            }],
        }, instruction_coverage=coverage)

    def test_01_all_polarity_states_validate(self):
        for polarity in ("affirmative", "negative", "conditional", "unadopted", "ambiguous"):
            with self.subTest(polarity=polarity):
                self.assertEqual(validate_instruction_analysis(self._analysis(polarity)), [])

    def test_02_invalid_enum_rejected(self):
        a = self._analysis(polarity="maybe")
        self.assertTrue(any("instruction_polarity invalid" in e for e in validate_instruction_analysis(a)))
        a = self._analysis(action="explode")
        self.assertTrue(any("instruction_action invalid" in e for e in validate_instruction_analysis(a)))

    def test_03_source_sha_and_excerpt_mismatch_rejected(self):
        a = self._analysis()
        a["source_instruction"]["sha256"] = "0" * 64
        self.assertTrue(any("sha256 mismatch" in e for e in validate_instruction_analysis(a)))
        a = self._analysis()
        a["analyses"][0]["instruction_excerpt"] = "forged"
        errs = validate_instruction_analysis(a)
        # hash becomes stale as well; excerpt mismatch must still be among errors
        self.assertTrue(any("differs from source" in e or "analysis_sha256" in e for e in errs))

    def test_04_analysis_to_coverage_copies_fields(self):
        a = self._analysis()
        cov = self._coverage(a)
        self.assertEqual(cov["requirements"][0]["instruction_polarity"], "affirmative")
        self.assertEqual(cov["requirements"][0]["instruction_action"], "insert")
        self.assertEqual(crosscheck_coverage(cov, a), [])

    def test_05_coverage_flip_detected(self):
        a = self._analysis()
        cov = self._coverage(a)
        cov["requirements"][0]["instruction_polarity"] = "affirmative-x"
        self.assertTrue(any("differs from instruction_analysis" in e for e in crosscheck_coverage(cov, a)))

    def test_06_negative_and_conditional_plans_rejected(self):
        for polarity in ("negative", "conditional", "unadopted", "ambiguous"):
            with self.subTest(polarity=polarity):
                a = self._analysis(polarity=polarity)
                cov = self._coverage(a)
                with self.assertRaisesRegex(ValueError, "NEGATIVE_POLARITY|POLARITY_UNKNOWN"):
                    self._plan(a, cov)

    def test_07_affirmative_plan_inherits_and_passes_crosscheck(self):
        a = self._analysis()
        cov = self._coverage(a)
        plan = self._plan(a, cov)
        self.assertEqual(crosscheck_plan(plan, a), [])
        src = plan["revision_items"][0]["targets"][0]["authorized_change"]["authorization_source"]
        self.assertEqual(src["instruction_polarity"], "affirmative")
        self.assertEqual(src["instruction_action"], "insert")

    def test_08_plan_polarity_flip_detected(self):
        a = self._analysis()
        plan = self._plan(a, self._coverage(a))
        plan["revision_items"][0]["targets"][0]["authorized_change"]["authorization_source"]["instruction_polarity"] = "negative"
        errors = crosscheck_plan(plan, a)
        self.assertTrue(any("differs from instruction_analysis" in e for e in errors))

    def test_09_mixed_insert_replace_format_preserves_other_targets(self):
        a = self._analysis()
        cov = self._coverage(a)
        intent_replace = {
            "id": "REP-1", "doc_role": "Manuscript", "find": "Claim.", "replace": "Claim updated.",
            "intent": "Update prose", "instruction_requirement_ids": ["REP-REQ"],
            "input_file": str(self.doc), "output_file": str(self.root / "marked.docx"),
        }
        rep_req = cov["requirements"][0].copy()
        rep_req.update({"requirement_id": "REP-REQ", "revision_targets": [{"revision_item_id": "REP-1", "target_index": 0}]})
        cov["requirements"].append(rep_req)
        plan = compile_revision_plan({
            "documents": {"Manuscript": {"input_file": str(self.doc), "output_file": str(self.root / "marked.docx")}},
            "intents": [{
                "id": "REV-A1", "doc_role": "Manuscript", "action": "insert_paragraph",
                "position": "after", "find": "References",
                "inserted_text": "[77] Gamma Author (2021). Test Journal, 5, 11-20.",
                "intent": "Insert requested reference.", "instruction_requirement_ids": ["REQ-A-01"],
                "input_file": str(self.doc), "output_file": str(self.root / "marked.docx"),
            }, intent_replace],
        }, instruction_coverage=cov)
        self.assertEqual(plan["revision_items"][1]["targets"][0]["replace"], "Claim updated.")
        self.assertEqual(crosscheck_plan(plan, a), [])


if __name__ == "__main__":
    unittest.main()

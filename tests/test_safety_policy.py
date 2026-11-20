import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.safety_policy import assess_task_risk, validate_batch_risk_tiers
from co_reviewer.workflow_engine import workflow_tasks_from_decision_plan


def _task(**overrides):
    task = {
        "task_id": "T1",
        "task_type": "local_revision",
        "target_documents": ["Manuscript"],
        "targets": [
            {"document": "Manuscript", "part": "word/document.xml", "p_idx": 0,
             "action": "replace", "evidence_ids": ["E1"]}
        ],
        "evidence": [
            {"evidence_id": "E1", "evidence_type": "paragraph",
             "match_status": "confirmed", "source_excerpt": "The wording is clear."}
        ],
        "proposed_action": "Improve the sentence wording.",
        "proposed_response": "",
        "unresolved_questions": [],
    }
    task.update(overrides)
    return task


class RiskPolicyTests(unittest.TestCase):
    def test_ordinary_single_document_language_edit_is_tier_zero(self):
        self.assertEqual(assess_task_risk(_task())["risk_tier"], "TIER_0")

    def test_scientific_tokens_and_cross_document_scope_are_tier_one(self):
        numeric = _task(
            target_documents=["Manuscript"],
            patch_operations=[{"find": "10 mg", "replace": "12 mg"}],
        )
        self.assertEqual(assess_task_risk(numeric)["risk_tier"], "TIER_1")
        cross_document = _task(
            task_type="cross_document_consistency",
            target_documents=["Manuscript", "SI"],
        )
        self.assertEqual(assess_task_risk(cross_document)["risk_tier"], "TIER_1")

    def test_formatting_formula_and_reference_changes_are_tier_one(self):
        for phrase in (
            "Change the heading font to bold.",
            "Correct the equation layout.",
            "Update the citation reference.",
            "Correct the sample identifier.",
            "Revise the concentration condition.",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    assess_task_risk(_task(proposed_action=phrase))["risk_tier"],
                    "TIER_1",
                )

    def test_ambiguous_target_evidence_is_tier_two(self):
        task = _task(evidence=[
            {"evidence_id": "E1", "evidence_type": "paragraph",
             "match_status": "ambiguous", "source_excerpt": "Candidate paragraph."}
        ])
        assessment = assess_task_risk(task)
        self.assertEqual(assessment["risk_tier"], "TIER_2")
        self.assertTrue(any("not_confirmed" in reason for reason in assessment["risk_reasons"]))

    def test_mixed_risk_tiers_cannot_share_one_batch(self):
        with self.assertRaisesRegex(ValueError, "Mixed-risk batches"):
            validate_batch_risk_tiers([_task(), _task(
                task_id="T2", task_type="global_numbering",
                target_documents=["Manuscript", "SI"]
            )])

    def test_tier_two_cannot_enter_a_patch_batch(self):
        blocked = _task(evidence=[
            {"evidence_id": "E1", "evidence_type": "paragraph",
             "match_status": "unresolved", "source_excerpt": "No exact match."}
        ])
        with self.assertRaisesRegex(ValueError, "Tier 2 task"):
            validate_batch_risk_tiers([blocked])

    def test_decision_plan_adapter_seals_deterministic_risk_assessment(self):
        rows = workflow_tasks_from_decision_plan([_task()])
        self.assertEqual(rows[0]["risk_tier"], "TIER_0")
        self.assertEqual(rows[0]["risk_policy_version"], "1.0")
        self.assertTrue(rows[0]["risk_reasons"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseUnit,
    response_units_to_legacy_plan,
)
from co_reviewer.release_manifest import audit_patch_operation_deltas
from co_reviewer.response_renderer import render_response_letter
from response_letter_docx import render_response_docx


HASH_A = "a" * 64
HASH_B = "b" * 64


class ResponseRendererPatchExcerptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.operation = PatchOperation.from_mapping({
            "operation_id": "OP-RESULT-01",
            "document": "Manuscript",
            "input_file": str(self.root / "source.docx"),
            "output_file": str(self.root / "marked.docx"),
            "part": "word/document.xml",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "find": "The conductivity was 1.2 mS cm-1.",
            "replace": "The ionic conductivity was 1.2 mS cm-1 at 25 °C.",
            "source_sha256": HASH_A,
            "evidence_ids": ["E-1"],
            "source_comment_ids": ["C-Reviewer-1"],
            "location_phrase": "ionic conductivity was 1.2 mS cm-1 at 25 °C",
        })
        self.patch = Patch(
            patch_id="P-RESULT-01",
            task_id="T-RESULT-01",
            reviewer="Reviewer 1",
            comment="Please clarify the conductivity statement.",
            proposed_response="We clarified the conductivity statement.",
            operations=[self.operation],
            status="verified",
            preflight_fingerprint=HASH_A,
            preflight_report_id="preflight-01",
            approval_fingerprint=HASH_B,
            apply_report_ids=["apply-01"],
            verification_report_id="verify-01",
            verified_output_hashes=[{
                "path": str(self.root / "marked.docx"),
                "sha256": HASH_B,
            }],
        )
        self.unit = ResponseUnit(
            response_unit_id="RU-RESULT-C1",
            reviewer="Reviewer 1",
            comment_id="C-Reviewer-1",
            comment_text="Please clarify the conductivity statement.",
            response_text="We clarified the conductivity statement.",
            linked_task_ids=["T-RESULT-01"],
            linked_patch_ids=["P-RESULT-01"],
            linked_operation_ids=["OP-RESULT-01"],
            disposition="revised",
            location_requirement="required",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="semantic-review-01",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_markdown_response_uses_exact_verified_patch_replace_text(self):
        plan = response_units_to_legacy_plan([self.unit], [self.patch])
        change = plan["revision_items"][0]["quoted_changes"][0]
        self.assertEqual(change["quote_text"], self.operation.replace)
        self.assertEqual(change["operation_id"], self.operation.operation_id)
        self.assertTrue(change["verified"])
        markdown = render_response_letter(plan)
        self.assertIn(self.operation.replace, markdown)
        self.assertIn("PatchOperation OP-RESULT-01", markdown)
        self.assertIn("no model-generated replacement text is used", markdown.lower())

    def test_patch_operation_delta_audit_surfaces_numeric_unit_citation_and_identifier_changes(self):
        operation = PatchOperation.from_mapping({
            "operation_id": "OP-MARKERS-01",
            "document": "Manuscript",
            "input_file": str(self.root / "source.docx"),
            "output_file": str(self.root / "marked.docx"),
            "part": "word/document.xml",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "find": "Sample AB-12 showed 1.2 mS cm-1 [4].",
            "replace": "Sample AB-12 showed 1.5 mS cm-1 [5] (Fig. 2).",
            "evidence_ids": ["E-MARKER"],
            "source_comment_ids": ["C-Reviewer-1"],
        })
        patch = Patch(
            patch_id="P-MARKERS-01",
            task_id="T-MARKERS-01",
            reviewer="Reviewer 1",
            comment="Please correct the result and reference.",
            proposed_response="The result and reference were updated.",
            operations=[operation],
        )
        response_unit = SimpleNamespace(
            response_unit_id="RU-MARKERS-C1",
            linked_operation_ids=["OP-MARKERS-01"],
            reviewer="Reviewer 1",
            status="completed",
            semantic_review_confirmed=True,
            comment_id="C-Reviewer-1",
        )
        inventory = audit_patch_operation_deltas(
            SimpleNamespace(response_units=[response_unit], patches=[patch])
        )
        row = inventory[0]
        self.assertEqual(row["review_status"], "human_semantic_review_completed")
        self.assertEqual(row["deltas"]["numeric_values"]["removed"], ["1.2"])
        self.assertEqual(row["deltas"]["numeric_values"]["added"], ["1.5"])
        self.assertEqual(row["deltas"]["units"], {"removed": [], "added": []})
        self.assertEqual(row["deltas"]["citation_markers"]["removed"], ["[4]"])
        self.assertEqual(row["deltas"]["citation_markers"]["added"], ["[5]"])
        self.assertEqual(
            row["deltas"]["figure_table_equation_references"]["added"],
            ["Fig. 2"],
        )
        self.assertEqual(
            row["deltas"]["sample_or_component_identifiers"],
            {"removed": [], "added": []},
        )
        self.assertIn("numeric_values", row["changed_categories"])
        self.assertIn("citation_markers", row["changed_categories"])

    def test_docx_response_contains_the_same_exact_patch_excerpt(self):
        plan = response_units_to_legacy_plan([self.unit], [self.patch])
        output = self.root / "Response_to_Reviewers.docx"
        render_response_docx(plan, output, manuscript_title="Synthetic manuscript")
        document = Document(str(output))
        rendered = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn(self.operation.replace, rendered)
        self.assertIn("PatchOperation OP-RESULT-01", rendered)
        self.assertIn("Verified revised text", rendered)
        self.assertTrue(output.is_file())


if __name__ == "__main__":
    unittest.main()

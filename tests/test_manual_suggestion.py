import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.manual_suggestion import build_manual_suggestion
import co_reviewer_cli as cli

HASH = "a" * 64


def _payload(**overrides):
    value = {
        "document_role": "Manuscript",
        "source_document_path": "input/Manuscript.docx",
        "source_sha256": HASH,
        "word_part": "word/document.xml",
        "target": {"paragraph_index": 4, "object_id": "OBJ-4"},
        "reviewer_comment_ids": ["C1"],
        "operation_id": "OP-1",
        "source_excerpt": "The exact blocked wording.",
        "before_context": "Previous sentence. ",
        "after_context": " Following sentence.",
        "blocking_reason": "The requested range intersects a protected bookmark.",
        "blocking_structures": [{"kind": "bookmark_range", "structure_id": "S1"}],
        "missing_evidence": [],
        "proposed_edit": "A proposed replacement.",
    }
    value.update(overrides)
    return value


class ManualSuggestionTests(unittest.TestCase):
    def test_suggestion_is_explicitly_unapplied_and_hash_bound(self):
        suggestion = build_manual_suggestion(_payload())
        self.assertEqual(suggestion["status"], "BLOCKED_MANUAL_REVIEW")
        self.assertTrue(suggestion["not_applied"])
        self.assertFalse(suggestion["docx_write_performed"])
        self.assertEqual(suggestion["source_sha256"], HASH)
        self.assertEqual(suggestion["proposed_edit"], "A proposed replacement.")

    def test_missing_evidence_removes_proposed_edit_instead_of_guessing(self):
        suggestion = build_manual_suggestion(_payload(
            missing_evidence=["The calculation result needed to support this claim."]
        ))
        self.assertIsNone(suggestion["proposed_edit"])
        self.assertIn("Clarify", suggestion["human_action_required"])

    def test_suggestion_requires_exact_source_and_stable_location_hint(self):
        with self.assertRaisesRegex(ValueError, "source_excerpt"):
            build_manual_suggestion(_payload(source_excerpt=""))
        with self.assertRaisesRegex(ValueError, "target requires"):
            build_manual_suggestion(_payload(target={}))

    def test_schema_bound_identifiers_reject_duplicates_and_invalid_operation_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicates"):
            build_manual_suggestion(_payload(reviewer_comment_ids=["C1", "C1"]))
        with self.assertRaisesRegex(ValueError, "operation_id"):
            build_manual_suggestion(_payload(operation_id=7))

    def test_cli_writes_a_new_manual_suggestion_without_overwriting_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "diagnostic.json"
            output = root / "manual-suggestion.json"
            source.write_text(json.dumps(_payload()), encoding="utf-8")
            result = cli.cmd_create_manual_suggestion(SimpleNamespace(input=str(source), out=str(output)))
            self.assertEqual(result, 0)
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(saved["not_applied"])
            self.assertEqual(json.loads(source.read_text(encoding="utf-8"))["source_sha256"], HASH)
            with self.assertRaises(FileExistsError):
                cli.cmd_create_manual_suggestion(SimpleNamespace(input=str(source), out=str(output)))


if __name__ == "__main__":
    unittest.main()

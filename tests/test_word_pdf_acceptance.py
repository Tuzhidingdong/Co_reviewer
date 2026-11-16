from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from co_reviewer.revision_models import revision_ledger_from_mapping
from co_reviewer.word_pdf_acceptance import build_word_pdf_acceptance_ledger


class WordPdfAcceptanceFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture_dir = self.root / "word-fixture"
        self.fixture_dir.mkdir()
        self.source = self.fixture_dir / "word_compatibility_input.docx"
        self.marked = self.fixture_dir / "word_compatibility_revised.docx"
        self.verification = self.fixture_dir / "word_compatibility_verification_after_word.json"
        self.source.write_bytes(b"synthetic source package")
        self.marked.write_bytes(b"synthetic Word-saved Marked package")
        self.verification.write_text(
            json.dumps({
                "passed": True,
                "targets_total": 5,
                "targets_verified": 5,
                "targets_failed": 0,
                "untracked_text_audit": {
                    "passed": True,
                    "violations": [],
                },
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_builds_valid_hash_bound_mapping_ledger_from_post_word_fixture(self) -> None:
        source_bytes = self.source.read_bytes()
        marked_bytes = self.marked.read_bytes()
        ledger_path = self.root / "acceptance" / "revision_ledger.json"

        result = build_word_pdf_acceptance_ledger(self.fixture_dir, ledger_path)

        self.assertTrue(ledger_path.is_file())
        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.marked.read_bytes(), marked_bytes)
        payload = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger = revision_ledger_from_mapping(payload)
        ledger.validate_or_raise()
        self.assertEqual(result["response_unit_id"], "RU-WORD-QA-COM-01")
        self.assertEqual(len(ledger.documents), 1)
        self.assertEqual(len(ledger.patches), 1)
        self.assertEqual(ledger.patches[0].status, "verified")
        self.assertEqual(ledger.response_units[0].status, "verified")
        operation = ledger.patches[0].operations[0]
        self.assertEqual(operation.operation_id, "WORD-QA-COM-OP1")
        self.assertEqual(operation.location_phrase, "1.0 M")
        self.assertEqual(operation.source_sha256, hashlib.sha256(source_bytes).hexdigest())
        self.assertEqual(
            ledger.patches[0].verified_output_hashes,
            [{"path": str(self.marked.resolve()), "sha256": hashlib.sha256(marked_bytes).hexdigest()}],
        )

    def test_refuses_to_build_from_incomplete_post_word_verification(self) -> None:
        self.verification.write_text(
            json.dumps({
                "passed": False,
                "targets_total": 5,
                "targets_verified": 4,
                "targets_failed": 1,
                "untracked_text_audit": {"passed": False, "violations": [{"reason": "fixture"}]},
            }),
            encoding="utf-8",
        )
        ledger_path = self.root / "revision_ledger.json"
        with self.assertRaisesRegex(ValueError, "passing five-target post-Word report"):
            build_word_pdf_acceptance_ledger(self.fixture_dir, ledger_path)
        self.assertFalse(ledger_path.exists())

    def test_refuses_to_overwrite_existing_revision_ledger(self) -> None:
        ledger_path = self.root / "revision_ledger.json"
        ledger_path.write_text("preserve-me", encoding="utf-8")
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            build_word_pdf_acceptance_ledger(self.fixture_dir, ledger_path)
        self.assertEqual(ledger_path.read_text(encoding="utf-8"), "preserve-me")


if __name__ == "__main__":
    unittest.main()

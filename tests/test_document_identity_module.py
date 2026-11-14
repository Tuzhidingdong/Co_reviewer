from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from co_reviewer.identity import (
    DocumentIdentityAndHash,
    revision_item_fingerprint,
    revision_targets_fingerprint,
    revision_verification_fingerprint,
    sha256_file,
)


class DocumentIdentityModuleBoundaryTests(unittest.TestCase):
    def test_file_sha256_is_stable_and_shared_by_facade(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.docx"
            path.write_bytes(b"synthetic-docx-bytes")
            expected = sha256_file(path)
            self.assertEqual(len(expected), 64)
            self.assertEqual(DocumentIdentityAndHash.sha256_file(path), expected)

    def test_revision_target_fingerprint_tracks_exact_target_scope(self):
        item = {"targets": [{"part": "word/document.xml", "p_idx": 3, "find": "old"}]}
        original = revision_targets_fingerprint(item)
        item["targets"][0]["find"] = "changed"
        self.assertNotEqual(original, revision_targets_fingerprint(item))

    def test_approval_fingerprint_tracks_reviewer_facing_scope(self):
        item = {
            "id": "R1-C1",
            "reviewer": "Reviewer 1",
            "comment": "Please clarify the mechanism.",
            "proposed_response": "We clarified the mechanism.",
            "targets": [{"document": "Manuscript", "find": "old", "replace": "new"}],
        }
        original = revision_item_fingerprint(item)
        changed = dict(item, proposed_response="We added new evidence.")
        self.assertNotEqual(original, revision_item_fingerprint(changed))

    def test_verification_fingerprint_tracks_exact_output_hashes(self):
        item = {
            "approval_fingerprint": "a" * 64,
            "verified_output_hashes": [{"path": "marked.docx", "sha256": "b" * 64}],
            "semantic_review_confirmed": True,
            "verified_at_utc": "2026-11-14T00:00:00+00:00",
        }
        original = revision_verification_fingerprint(item)
        changed = dict(item, verified_output_hashes=[{"path": "marked.docx", "sha256": "c" * 64}])
        self.assertNotEqual(original, revision_verification_fingerprint(changed))
        self.assertEqual(
            DocumentIdentityAndHash.revision_verification_fingerprint(item),
            original,
        )


if __name__ == "__main__":
    unittest.main()

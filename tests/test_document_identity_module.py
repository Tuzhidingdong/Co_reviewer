from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from co_reviewer.identity import (
    CitationProfile,
    DocumentIdentityAndHash,
    IdentityValidationError,
    TargetRef,
    build_document_identity,
    build_object_identity,
    hash_citation_state,
    hash_content,
    hash_formatting,
    hash_structure,
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

    def test_five_document_identity_dimensions_are_independent(self):
        profile = CitationProfile(
            mode="numeric_superscript",
            style_name="Vancouver",
            numbering_scope="document",
            marker_examples=("¹", "²"),
            reference_labels=("1", "2"),
            detection_method="rule_based",
        )
        identity = build_document_identity(
            role="Manuscript",
            source_sha256="a" * 64,
            visible_text="The  Zn  concentration was 1 M.",
            structure={"paragraphs": 2, "tables": 1},
            formatting={"styles": ["Normal", "Caption"]},
            citation_state={"markers": ["1", "2"], "references": ["ref-1", "ref-2"]},
            citation_profile=profile,
            document_id="document-001",
        )
        payload = identity.to_dict()
        self.assertEqual(payload["document_id"], "document-001")
        self.assertEqual(set(payload["dimensions"]), {
            "content_sha256", "structure_sha256",
            "formatting_sha256", "citation_sha256",
        })
        self.assertEqual(len(identity.identity_fingerprint), 64)
        self.assertEqual(identity.content_sha256, hash_content("The Zn concentration was 1 M."))

    def test_document_identity_changes_revision_and_only_changed_dimensions(self):
        common = {
            "role": "Manuscript",
            "visible_text": "A sample was measured.",
            "structure": {"paragraphs": 1},
            "formatting": {"style": "Normal"},
            "citation_state": {"markers": []},
            "document_id": "document-stable",
        }
        first = build_document_identity(source_sha256="a" * 64, **common)
        second = build_document_identity(
            source_sha256="b" * 64,
            **{**common, "visible_text": "A sample was measured twice."},
        )
        self.assertEqual(first.document_id, second.document_id)
        self.assertNotEqual(first.revision_id, second.revision_id)
        self.assertNotEqual(first.source_sha256, second.source_sha256)
        self.assertNotEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.structure_sha256, second.structure_sha256)
        self.assertEqual(first.formatting_sha256, second.formatting_sha256)
        self.assertEqual(first.citation_sha256, second.citation_sha256)

    def test_stable_object_id_is_separate_from_changing_location_hint(self):
        first_document = build_document_identity(
            role="Manuscript",
            source_sha256="a" * 64,
            visible_text="Paragraph A.",
            structure={"paragraphs": 2},
            formatting={},
            citation_state={},
            document_id="document-stable",
        )
        second_document = build_document_identity(
            role="Manuscript",
            source_sha256="b" * 64,
            visible_text="Paragraph A. Paragraph B.",
            structure={"paragraphs": 3},
            formatting={},
            citation_state={},
            document_id="document-stable",
        )
        first_object = build_object_identity(
            document=first_document,
            object_id="object-para-001",
            object_type="body_paragraph",
            part="word/document.xml",
            object_payload={"text": "Paragraph A."},
            native_id="A001",
            location_hint={"p_idx": 0},
        )
        second_object = build_object_identity(
            document=second_document,
            object_id="object-para-001",
            object_type="body_paragraph",
            part="word/document.xml",
            object_payload={"text": "Paragraph A."},
            native_id="A001",
            location_hint={"p_idx": 2},
        )
        old_ref = TargetRef.from_object(first_object)
        new_ref = TargetRef.from_object(second_object)
        self.assertEqual(old_ref.object_id, new_ref.object_id)
        self.assertNotEqual(old_ref.revision_id, new_ref.revision_id)
        self.assertNotEqual(old_ref.location_hint, new_ref.location_hint)

    def test_target_ref_supports_character_ranges_and_rejects_invalid_ranges(self):
        document = build_document_identity(
            role="Manuscript",
            source_sha256="a" * 64,
            visible_text="0123456789",
            structure={"paragraphs": 1},
            formatting={},
            citation_state={},
            document_id="document-001",
        )
        record = build_object_identity(
            document=document,
            object_type="body_paragraph",
            part="word/document.xml",
            object_payload={"text": "0123456789"},
        )
        target = TargetRef.from_object(record, start_offset=2, end_offset=5)
        self.assertEqual((target.start_offset, target.end_offset), (2, 5))
        with self.assertRaises(IdentityValidationError):
            TargetRef.from_object(record, start_offset=5, end_offset=2)

    def test_citation_profile_is_part_of_citation_identity(self):
        numeric = CitationProfile(
            mode="numeric_bracketed",
            marker_examples=("[1]", "[2]"),
            detection_method="manual_confirmed",
        )
        author_year = CitationProfile(
            mode="author_year",
            marker_examples=("(Smith, 2020)",),
            detection_method="manual_confirmed",
        )
        self.assertNotEqual(numeric.fingerprint, author_year.fingerprint)
        self.assertNotEqual(
            hash_citation_state({"profile": numeric.to_dict()}),
            hash_citation_state({"profile": author_year.to_dict()}),
        )

    def test_formatting_and_structure_hashes_are_separate(self):
        structure_a = hash_structure({"paragraphs": [{"type": "heading"}, {"type": "body"}]})
        structure_b = hash_structure({"paragraphs": [{"type": "body"}, {"type": "heading"}]})
        format_a = hash_formatting({"font": "Times New Roman", "size_pt": 12})
        format_b = hash_formatting({"font": "Arial", "size_pt": 12})
        self.assertNotEqual(structure_a, structure_b)
        self.assertNotEqual(format_a, format_b)

    def test_identity_rejects_non_serializable_or_malformed_payloads(self):
        with self.assertRaises(IdentityValidationError):
            hash_structure({"unsupported": object()})
        with self.assertRaises(IdentityValidationError):
            CitationProfile(mode={"not": "a mode"})

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

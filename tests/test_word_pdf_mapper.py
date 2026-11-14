# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import co_reviewer_cli as cli
from co_reviewer.identity import sha256_file
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseLocation,
    ResponseUnit,
    RevisionDocument,
    RevisionLedger,
)
from co_reviewer.word_pdf_mapper import (
    WordPdfMappingError,
    _normalise_pdf_text,
    _validate_mapping_phrase,
    build_mapping_jobs,
)


class WordPdfMapperContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_docx = self.root / "input.docx"
        self.marked_docx = self.root / "marked.docx"
        self.clean_docx = self.root / "working_clean.docx"
        self.pdf = self.root / "final.pdf"
        self.input_docx.write_bytes(b"input docx")
        self.marked_docx.write_bytes(b"marked docx")
        self.clean_docx.write_bytes(b"accepted-changes clean view")
        self.pdf.write_bytes(b"%PDF-1.4 synthetic")
        self.operation = PatchOperation.from_mapping({
            "operation_id": "OP-1",
            "document": "Manuscript",
            "input_file": str(self.input_docx),
            "output_file": str(self.marked_docx),
            "part": "word/document.xml",
            "find": "old phrase",
            "replace": "new phrase",
            "source_sha256": sha256_file(self.input_docx),
            "evidence_ids": ["E1"],
        })
        self.patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please revise this sentence.",
            proposed_response="We revised the sentence.",
            operations=[self.operation],
            status="verified",
            preflight_fingerprint="a" * 64,
            preflight_report_id="PF-1",
            approval_fingerprint="b" * 64,
            apply_report_ids=["AP-1"],
            verification_report_id="VF-1",
            verified_output_hashes=[{
                "path": str(self.marked_docx),
                "sha256": sha256_file(self.marked_docx),
            }],
        )
        view_fingerprint = hashlib.sha256(json.dumps({
            "source_sha256": sha256_file(self.marked_docx),
            "working_clean_sha256": sha256_file(self.clean_docx),
            "policy": "accept-insertions-reject-deletions-v1",
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        self.document = RevisionDocument(
            document_id="doc-manuscript",
            revision_id="rev-1",
            role="Manuscript",
            input_file=str(self.input_docx),
            output_file=str(self.marked_docx),
            source_sha256=sha256_file(self.input_docx),
            output_sha256=sha256_file(self.marked_docx),
            working_clean_file=str(self.clean_docx),
            working_clean_sha256=sha256_file(self.clean_docx),
            working_view_fingerprint=view_fingerprint,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _ledger(self, *, unit_status="verified", patch_status="verified", operation=None):
        patch = self.patch
        if operation is not None:
            patch = Patch(
                patch_id="P1",
                task_id="T1",
                reviewer="Reviewer 1",
                comment="Please revise this sentence.",
                proposed_response="We revised the sentence.",
                operations=[operation],
                status=patch_status,
                preflight_fingerprint="a" * 64 if patch_status in {"preflighted", "approved", "applied", "verified"} else None,
                preflight_report_id="PF-1" if patch_status in {"preflighted", "approved", "applied", "verified"} else None,
                approval_fingerprint="b" * 64 if patch_status in {"approved", "applied", "verified"} else None,
                apply_report_ids=["AP-1"] if patch_status in {"applied", "verified"} else [],
                verification_report_id="VF-1" if patch_status == "verified" else None,
                verified_output_hashes=[{"path": str(self.marked_docx), "sha256": sha256_file(self.marked_docx)}] if patch_status == "verified" else [],
            )
        unit = ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise this sentence.",
            response_text="We revised the sentence.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            status=unit_status,
            semantic_review_confirmed=unit_status == "verified",
            verification_report_id="SEM-1" if unit_status == "verified" else None,
        )
        return RevisionLedger(
            revision_round_id="round-1",
            documents=[self.document],
            patches=[patch],
            response_units=[unit],
        )

    def test_mapping_jobs_bind_verified_patch_to_current_clean_view(self):
        jobs = build_mapping_jobs(self._ledger())
        self.assertEqual(len(jobs["Manuscript"]), 1)
        job = jobs["Manuscript"][0]
        self.assertEqual(job.phrase, "new phrase")
        self.assertEqual(job.source_clean_docx, str(self.clean_docx.resolve()))
        self.assertEqual((job.patch_id, job.operation_id), ("P1", "OP-1"))

    def test_mapping_refuses_response_unit_without_semantic_confirmation(self):
        with self.assertRaisesRegex(WordPdfMappingError, "semantic review"):
            build_mapping_jobs(self._ledger(unit_status="draft"))

    def test_mapping_refuses_patch_that_is_not_verified(self):
        with self.assertRaisesRegex(WordPdfMappingError, "not verified"):
            build_mapping_jobs(self._ledger(patch_status="planned", operation=self.operation))

    def test_mapping_refuses_deletions_and_non_body_word_parts(self):
        deletion = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "replace": "",
        })
        with self.assertRaisesRegex(WordPdfMappingError, "non-empty surviving text"):
            build_mapping_jobs(self._ledger(operation=deletion))
        header = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "part": "word/header1.xml",
        })
        with self.assertRaisesRegex(WordPdfMappingError, "main document body"):
            build_mapping_jobs(self._ledger(operation=header))

    def test_word_find_phrase_limit_and_pdf_text_normalisation(self):
        with self.assertRaisesRegex(WordPdfMappingError, "255 characters"):
            _validate_mapping_phrase("x" * 256)
        with self.assertRaisesRegex(WordPdfMappingError, "single-paragraph"):
            _validate_mapping_phrase("first line\nsecond line")
        self.assertEqual(
            _normalise_pdf_text("Alpha\u00a0 beta\nGAMMA"),
            "alpha beta gamma",
        )

    def test_response_location_rejects_final_docx_changed_after_mapping(self):
        output_hash = sha256_file(self.clean_docx)
        location = ResponseLocation(
            document="Manuscript",
            output_file=str(self.clean_docx),
            pdf_file=str(self.pdf),
            page=1,
            line_start=3,
            line_end=4,
            pdf_sha256=sha256_file(self.pdf),
            location_map_id="map-1",
            mapping_fingerprint="c" * 64,
            output_sha256=output_hash,
        )
        self.assertEqual(location.output_sha256, output_hash)
        self.clean_docx.write_bytes(b"changed after mapping")
        with self.assertRaisesRegex(RevisionModelError, "DOCX changed"):
            ResponseLocation(
                document="Manuscript",
                output_file=str(self.clean_docx),
                pdf_file=str(self.pdf),
                page=1,
                line_start=3,
                line_end=4,
                pdf_sha256=sha256_file(self.pdf),
                location_map_id="map-1",
                mapping_fingerprint="c" * 64,
                output_sha256=output_hash,
            )

    def test_word_pdf_mapper_cli_is_registered(self):
        parsed = cli.build_parser().parse_args([
            "map-response-locations",
            "--ledger", str(self.root / "revision_ledger.json"),
            "--out-dir", str(self.root / "word_pdf_output"),
        ])
        self.assertEqual(parsed.func, cli.cmd_map_response_locations)
        self.assertEqual(parsed.out_dir, str(self.root / "word_pdf_output"))


if __name__ == "__main__":
    unittest.main()

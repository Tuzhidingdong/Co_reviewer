# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import co_reviewer_cli as cli
from co_reviewer.identity import sha256_file
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseLocation,
    ResponseUnit,
    RevisionDocument,
    RevisionLedger,
    RevisionModelError,
)
from co_reviewer.word_pdf_mapper import (
    WordPdfMappingError,
    _audit_sequence_fields,
    _read_word_position,
    _reusable_role_mapping,
    _validate_mapping_phrase,
    assert_location_map_matches_ledger,
    build_mapping_jobs,
    build_unique_location_phrase,
)


class WordPdfMapperContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input_docx = self.root / "input.docx"
        self.marked_docx = self.root / "marked.docx"
        self.clean_docx = self.root / "working_clean.docx"
        self.pdf = self.root / "final.pdf"
        self.map_file = self.root / "location_map.json"
        self.input_docx.write_bytes(b"input docx")
        self.marked_docx.write_bytes(b"marked docx")
        self.clean_docx.write_bytes(b"accepted-changes clean view")
        self.pdf.write_bytes(b"%PDF-1.4 synthetic")
        self.map_file.write_text(json.dumps({
            "documents": [{
                "document": "Manuscript",
                "word_page_count": 3,
                "sequence_audit": {
                    "status": "manual_review",
                    "detected": {"Figure": [], "Table": []},
                    "issues": [],
                    "manual_review": [{"reason": "Synthetic fixture has no native sequence fields."}],
                },
            }]
        }), encoding="utf-8")
        self.operation = PatchOperation.from_mapping({
            "operation_id": "OP-1",
            "document": "Manuscript",
            "input_file": str(self.input_docx),
            "output_file": str(self.marked_docx),
            "part": "word/document.xml",
            "p_idx": 0,
            "find": "old phrase",
            "replace": "new phrase",
            "source_sha256": sha256_file(self.input_docx),
            "evidence_ids": ["E1"],
            "source_comment_ids": ["C1"],
            "location_phrase": "new phrase",
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

    def _unit(self, *, status="verified", operation_id="OP-1"):
        return ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise this sentence.",
            response_text="We revised the sentence.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=[operation_id] if operation_id else [],
            disposition="revised",
            location_requirement="required",
            status=status,
            semantic_review_confirmed=status == "verified",
            verification_report_id="SEM-1" if status == "verified" else None,
        )

    def _ledger(self, *, unit_status="verified", patch_status="verified", operation=None):
        selected_operation = operation or self.operation
        patch = self.patch
        if selected_operation is not self.operation or patch_status != "verified":
            patch = Patch(
                patch_id="P1",
                task_id="T1",
                reviewer="Reviewer 1",
                comment="Please revise this sentence.",
                proposed_response="We revised the sentence.",
                operations=[selected_operation],
                status=patch_status,
                preflight_fingerprint="a" * 64 if patch_status in {"preflighted", "approved", "applied", "verified"} else None,
                preflight_report_id="PF-1" if patch_status in {"preflighted", "approved", "applied", "verified"} else None,
                approval_fingerprint="b" * 64 if patch_status in {"approved", "applied", "verified"} else None,
                apply_report_ids=["AP-1"] if patch_status in {"applied", "verified"} else [],
                verification_report_id="VF-1" if patch_status == "verified" else None,
                verified_output_hashes=[{"path": str(self.marked_docx), "sha256": sha256_file(self.marked_docx)}] if patch_status == "verified" else [],
            )
        return RevisionLedger(
            revision_round_id="round-1",
            documents=[self.document],
            patches=[patch],
            response_units=[self._unit(status=unit_status)],
        )

    def test_mapping_jobs_bind_exact_operation_to_current_clean_role(self):
        jobs = build_mapping_jobs(self._ledger())
        self.assertEqual(len(jobs["Manuscript"]), 1)
        job = jobs["Manuscript"][0]
        self.assertEqual(job.phrase, "new phrase")
        self.assertEqual(job.source_clean_docx, str(self.clean_docx.resolve()))
        self.assertEqual((job.patch_id, job.operation_id, job.locator_id), ("P1", "OP-1", "OP-1"))

    def test_manual_required_non_body_target_requests_role_export_without_auto_mapping(self):
        operation = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "part": "word/header1.xml",
        })
        manual_patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please revise the table.",
            proposed_response="We revised the table.",
            operations=[operation],
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
        manual_unit = ResponseUnit(
            response_unit_id="RU-MANUAL-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise the table.",
            response_text="We revised the table.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-1"],
            disposition="revised",
            location_requirement="manual_required",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="SEM-MANUAL",
        )
        ledger = RevisionLedger(
            revision_round_id="round-manual",
            documents=[self.document],
            patches=[manual_patch],
            response_units=[manual_unit],
        )
        jobs = build_mapping_jobs(ledger)
        self.assertEqual(jobs, {"Manuscript": []})

    def test_manual_location_cli_records_operation_bound_table_locator(self):
        operation = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "part": "word/header1.xml",
        })
        patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Please revise the table.",
            proposed_response="We revised the table.",
            operations=[operation],
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
        unit = ResponseUnit(
            response_unit_id="RU-MANUAL-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise the table.",
            response_text="We revised the table.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-1"],
            disposition="revised",
            location_requirement="manual_required",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="SEM-MANUAL",
        )
        no_change = ResponseUnit(
            response_unit_id="RU-MANUAL-ANCHOR",
            reviewer="Reviewer 2",
            comment_id="C2",
            comment_text="The method is already described in Table 2.",
            response_text="We refer the reviewer to the existing table.",
            disposition="no_change",
            location_requirement="manual_required",
            resolution_reason="The confirmed existing anchor is a table cell and requires an explicit locator.",
            anchor_evidence_id="E-ANCHOR-2",
            anchor_document="Manuscript",
            anchor_part="word/document.xml",
            anchor_p_idx=0,
            anchor_source_excerpt="Existing table description",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="SEM-ANCHOR",
        )
        ledger = RevisionLedger(
            revision_round_id="round-manual-cli",
            documents=[self.document],
            patches=[patch],
            response_units=[unit, no_change],
        )
        ledger_path = self.root / "revision_ledger.json"
        word_map_path = self.root / "word_location_map.json"
        input_path = self.root / "manual_locations.json"
        output_path = self.root / "manual_location_map.json"
        cli.write_json_atomic(ledger_path, ledger.to_dict())
        cli.write_json_atomic(word_map_path, {
            "generator": "co-reviewer-word-pdf-mapper-v1",
            "revision_ledger_sha256": sha256_file(ledger_path),
            "documents": [{
                "document": "Manuscript",
                "source_working_clean_sha256": sha256_file(self.clean_docx),
                "output_file": str(self.marked_docx),
                "output_sha256": sha256_file(self.marked_docx),
                "pdf_file": str(self.pdf),
                "pdf_sha256": sha256_file(self.pdf),
                "word_page_count": 3,
                "sequence_audit": {
                    "status": "manual_review",
                    "detected": {"Figure": [], "Table": []},
                    "issues": [],
                    "manual_review": [{"reason": "Synthetic fixture."}],
                },
            }],
            "quality_reports": [],
        })
        cli.write_json_atomic(input_path, {
            "response_units": [
                {
                    "response_unit_id": "RU-MANUAL-C1",
                    "operation_id": "OP-1",
                    "locator_text": "Table S2, Supplementary Information",
                },
                {
                    "response_unit_id": "RU-MANUAL-ANCHOR",
                    "locator_id": "anchor:E-ANCHOR-2",
                    "locator_text": "Table 2, Method description",
                },
            ]
        })
        args = SimpleNamespace(
            ledger=str(ledger_path),
            word_map=str(word_map_path),
            input=str(input_path),
            out=str(output_path),
            actor="Synthetic Author",
        )
        self.assertEqual(cli.cmd_record_manual_response_locations(args), 0)
        updated = cli._load_revision_ledger(str(ledger_path))
        mapped = updated.response_units[0]
        self.assertEqual(mapped.status, "location_mapped")
        self.assertEqual(mapped.final_locations[0].mapping_method, "manual")
        self.assertIsNone(mapped.final_locations[0].page)
        self.assertEqual(
            mapped.final_locations[0].display_text,
            "Table S2, Supplementary Information",
        )
        self.assertEqual(updated.response_units[1].status, "location_mapped")
        self.assertEqual(updated.response_units[1].final_locations[0].locator_id, "manual:anchor:E-ANCHOR-2")
        self.assertEqual(updated.response_units[1].final_locations[0].mapping_method, "manual")
        self.assertIsNone(updated.response_units[1].final_locations[0].page)
        self.assertEqual(
            updated.response_units[1].final_locations[0].display_text,
            "Table 2, Method description",
        )
        self.assertTrue(output_path.is_file())

    def test_unconfirmed_response_unit_is_excluded_not_guessed(self):
        jobs = build_mapping_jobs(self._ledger(unit_status="draft"))
        self.assertEqual(jobs, {})

    def test_mapping_refuses_patch_that_is_not_verified(self):
        with self.assertRaisesRegex(WordPdfMappingError, "not verified"):
            build_mapping_jobs(self._ledger(patch_status="planned"))

    def test_delete_only_preflight_anchor_uses_four_words_on_each_side(self):
        final_paragraphs = [
            "Introductory words before the left flank alpha beta gamma delta right side one two three four continues."
        ]
        boundary = final_paragraphs[0].index(" right side")
        phrase = build_unique_location_phrase(final_paragraphs, 0, boundary, boundary)
        self.assertLessEqual(len(phrase), 255)
        self.assertIn("alpha beta gamma delta", phrase)
        self.assertIn("right side one two three four", phrase)
        deletion = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "replace": "",
            "location_phrase": phrase,
        })
        jobs = build_mapping_jobs(self._ledger(operation=deletion))
        self.assertEqual(jobs["Manuscript"][0].phrase, phrase)

    def test_location_phrase_expands_until_unique(self):
        paragraphs = [
            "same prefix target anchor phrase end alpha one two three",
            "same prefix target anchor phrase end beta four five six",
        ]
        start = paragraphs[0].index("target anchor phrase")
        end = start + len("target anchor phrase")
        phrase = build_unique_location_phrase(paragraphs, 0, start, end)
        self.assertIn("same prefix", phrase)
        self.assertIn("alpha", phrase)
        matches = sum(paragraph.count(phrase) for paragraph in paragraphs)
        self.assertEqual(matches, 1)

    def test_non_body_word_parts_remain_fail_closed(self):
        header = PatchOperation.from_mapping({
            **self.operation.to_dict(),
            "part": "word/header1.xml",
        })
        with self.assertRaisesRegex(WordPdfMappingError, "main-body paragraphs"):
            build_mapping_jobs(self._ledger(operation=header))

    def test_no_change_requires_existing_anchor_but_reply_only_is_skipped(self):
        no_change = ResponseUnit(
            response_unit_id="RU-T2-C2",
            reviewer="Reviewer 2",
            comment_id="C2",
            comment_text="The original method description is already clear.",
            response_text="We respectfully note that the method is already described.",
            disposition="no_change",
            location_requirement="existing_anchor_required",
            resolution_reason="The confirmed paragraph already contains the requested method detail.",
            anchor_evidence_id="E-ANCHOR",
            anchor_document="Manuscript",
            anchor_part="word/document.xml",
            anchor_p_idx=0,
            anchor_source_excerpt="Existing confirmed anchor excerpt",
            location_phrase="Existing confirmed anchor excerpt",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="SEM-C2",
        )
        reply_only = ResponseUnit(
            response_unit_id="RU-T3-C3",
            reviewer="Reviewer 3",
            comment_id="C3",
            comment_text="Please explain the rationale.",
            response_text="We have explained the rationale in the response.",
            disposition="reply_only",
            location_requirement="not_required",
            resolution_reason="Pure theoretical explanation without a claim about a manuscript passage.",
            status="verified",
            semantic_review_confirmed=True,
            verification_report_id="SEM-C3",
        )
        ledger = RevisionLedger(
            revision_round_id="round-mixed",
            documents=[self.document],
            patches=[self.patch],
            response_units=[self._unit(), no_change, reply_only],
        )
        jobs = build_mapping_jobs(ledger)
        role_jobs = jobs["Manuscript"]
        self.assertEqual(
            {(job.response_unit_id, job.locator_id) for job in role_jobs},
            {("RU-T1-C1", "OP-1"), ("RU-T2-C2", "anchor:E-ANCHOR")},
        )
        self.assertNotIn("RU-T3-C3", {job.response_unit_id for job in role_jobs})

    def test_role_reuse_requires_every_locator_and_matching_docx_pdf_hashes(self):
        source_clean_hash = sha256_file(self.clean_docx)
        output_sha = sha256_file(self.marked_docx)
        pdf_sha = sha256_file(self.pdf)
        location = ResponseLocation(
            document="Manuscript",
            output_file=str(self.marked_docx),
            pdf_file=str(self.pdf),
            page=2,
            line_start=8,
            line_end=10,
            pdf_sha256=pdf_sha,
            location_map_id="map-1",
            mapping_fingerprint="c" * 64,
            output_sha256=output_sha,
            locator_id="OP-1",
            source_clean_sha256=source_clean_hash,
        )
        unit = ResponseUnit(
            response_unit_id="RU-T1-C1",
            reviewer="Reviewer 1",
            comment_id="C1",
            comment_text="Please revise this sentence.",
            response_text="We revised the sentence.",
            linked_task_ids=["T1"],
            linked_patch_ids=["P1"],
            linked_operation_ids=["OP-1"],
            disposition="revised",
            location_requirement="required",
            status="location_mapped",
            semantic_review_confirmed=True,
            verification_report_id="SEM-1",
            location_map_fingerprint="c" * 64,
            location_map_file=str(self.map_file),
            location_map_file_sha256=sha256_file(self.map_file),
            final_locations=[location],
        )
        from co_reviewer.word_pdf_mapper import MappingJob
        job = MappingJob("RU-T1-C1", "OP-1", "P1", "OP-1", "Manuscript", "new phrase", str(self.clean_docx))
        reused = _reusable_role_mapping(
            "Manuscript", [job], {unit.response_unit_id: unit}, source_clean_hash
        )
        self.assertIsNotNone(reused)
        self.assertTrue(reused[1]["reused"])
        self.assertEqual(reused[0][job.key], (2, 8, 10))

        second_job = MappingJob("RU-T2-C2", "OP-2", "P2", "OP-2", "Manuscript", "other phrase", str(self.clean_docx))
        incomplete = _reusable_role_mapping(
            "Manuscript", [job, second_job], {unit.response_unit_id: unit}, source_clean_hash
        )
        self.assertIsNone(incomplete)

    def test_native_figure_and_table_sequence_fields_are_checked_mechanically(self):
        class FakeText:
            def __init__(self, text):
                self.Text = text

        class FakeField:
            def __init__(self, code, result):
                self.Code = FakeText(code)
                self.Result = FakeText(result)

        class FakeFields:
            def __init__(self, rows):
                self.rows = rows
                self.Count = len(rows)
            def Item(self, index):
                return self.rows[index - 1]

        class FakeDocument:
            def __init__(self, rows):
                self.Fields = FakeFields([
                    FakeField(code, value) for code, value in rows
                ])

        passing = _audit_sequence_fields(FakeDocument([
            ("SEQ Figure \\* ARABIC", "1"),
            ("SEQ Figure \\* ARABIC", "2"),
            ("SEQ Table \\* ARABIC", "1"),
        ]))
        self.assertEqual(passing["status"], "pass")
        self.assertEqual([row["value"] for row in passing["detected"]["Figure"]], [1, 2])
        self.assertEqual([row["value"] for row in passing["detected"]["Table"]], [1])

        failing = _audit_sequence_fields(FakeDocument([
            ("SEQ Figure \\* ARABIC", "1"),
            ("SEQ Figure \\* ARABIC", "3"),
            ("SEQ Table \\* ARABIC", "1"),
        ]))
        self.assertEqual(failing["status"], "fail")
        self.assertEqual(failing["issues"][0]["observed"], [1, 3])

        manual = _audit_sequence_fields(FakeDocument([
            ("SEQ Figure \\s 1 \\* ARABIC", "1"),
            ("SEQ Table \\* ARABIC", "1"),
        ]))
        self.assertEqual(manual["status"], "manual_review")
        self.assertTrue(manual["manual_review"])

    def test_word_position_uses_continuous_line_number_information(self):
        class FakeRange:
            def __init__(self, values):
                self.values = values
            def Information(self, code):
                return self.values[code]

        class FakeDocument:
            def Range(self, start, end):
                values = (
                    {3: 2, 10: 17, 12: False}
                    if start == 20
                    else {3: 2, 10: 19, 12: False}
                )
                return FakeRange(values)

        self.assertEqual(
            _read_word_position(FakeDocument(), 20, 25, label="RU-1/OP-1"),
            (2, 17, 19, 2),
        )

    def test_word_position_refuses_table_cell_line_numbers(self):
        class FakeRange:
            def Information(self, code):
                if code == 12:
                    return True
                return 2 if code == 3 else 7

        class FakeDocument:
            def Range(self, start, end):
                return FakeRange()

        with self.assertRaisesRegex(WordPdfMappingError, "inside a table"):
            _read_word_position(FakeDocument(), 5, 10, label="RU-1/OP-1")

    def test_word_find_phrase_limit_and_single_paragraph_requirement(self):
        with self.assertRaisesRegex(WordPdfMappingError, "255 characters"):
            _validate_mapping_phrase("x" * 256)
        with self.assertRaisesRegex(WordPdfMappingError, "single-paragraph"):
            _validate_mapping_phrase("first line\nsecond line")

    def test_response_location_rejects_final_docx_changed_after_mapping(self):
        output_hash = sha256_file(self.clean_docx)
        location_kwargs = {
            "document": "Manuscript",
            "output_file": str(self.clean_docx),
            "pdf_file": str(self.pdf),
            "page": 1,
            "line_start": 3,
            "line_end": 4,
            "pdf_sha256": sha256_file(self.pdf),
            "location_map_id": "map-1",
            "mapping_fingerprint": "c" * 64,
            "output_sha256": output_hash,
            "locator_id": "OP-1",
            "source_clean_sha256": sha256_file(self.input_docx),
        }
        location = ResponseLocation(**location_kwargs)
        self.assertEqual(location.output_sha256, output_hash)
        self.clean_docx.write_bytes(b"changed after mapping")
        with self.assertRaisesRegex(RevisionModelError, "DOCX changed"):
            ResponseLocation(**location_kwargs)

    def test_location_map_must_match_current_revision_ledger_hash(self):
        ledger_path = self.root / "revision_ledger.json"
        ledger_path.write_text('{"revision_round_id":"round-1"}', encoding="utf-8")
        expected = sha256_file(ledger_path)
        self.assertEqual(
            assert_location_map_matches_ledger(
                {"revision_ledger_sha256": expected}, ledger_path
            ),
            expected,
        )
        ledger_path.write_text('{"revision_round_id":"round-2"}', encoding="utf-8")
        with self.assertRaisesRegex(WordPdfMappingError, "different RevisionLedger version"):
            assert_location_map_matches_ledger(
                {"revision_ledger_sha256": expected}, ledger_path
            )

    def test_location_map_requires_a_valid_ledger_hash(self):
        ledger_path = self.root / "revision_ledger.json"
        ledger_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(WordPdfMappingError, "valid revision_ledger_sha256"):
            assert_location_map_matches_ledger(
                {"revision_ledger_sha256": "not-a-hash"}, ledger_path
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

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.release_manifest import ReleaseGateError, build_final_release_manifest
from co_reviewer.revision_models import ResponseUnit, RevisionLedger
from co_reviewer.workflow_engine import build_workflow_ledger


STAMP = "2026-11-15T08:00:00+08:00"


class ReleaseManifestGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Manuscript.docx"
        self.source.write_bytes(b"synthetic source document")
        self.workflow = build_workflow_ledger(
            {
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                }],
                "tasks": [{
                    "task_id": "T1",
                    "sequence": 1,
                    "task_type": "response_only",
                    "title": "Respond to reviewer comment",
                    "source_comment_ids": ["C1"],
                    "target_documents": [],
                    "evidence": [{
                        "evidence_id": "E-C1",
                        "document": "Reviewers",
                        "part": "word/comments.xml",
                        "p_idx": 0,
                        "source_excerpt": "Please clarify this statement.",
                        "evidence_type": "reviewer_comment",
                        "source_comment_id": "C1",
                        "match_status": "confirmed",
                    }],
                    "proposed_action": "Provide an explanatory response.",
                    "targets": [],
                    "dependencies": [],
                    "readiness": "ready_for_author_approval",
                    "unresolved_questions": [],
                    "confidence": 1.0,
                    "status": "pending",
                    "requires_user_approval": True,
                }],
            },
            "round-release-tests",
            STAMP,
        )
        self.revision_path = self.root / "revision_ledger.json"
        self.workflow_path = self.root / "workflow_ledger.json"
        self.revision_path.write_text("{}", encoding="utf-8")
        self.workflow_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _unit(self, *, status="verified", comment_id="C1"):
        return ResponseUnit(
            response_unit_id=f"RU-{comment_id}",
            reviewer="Reviewer 1",
            comment_id=comment_id,
            comment_text="Please clarify this statement.",
            response_text="We have clarified the statement.",
            linked_task_ids=["T1"],
            disposition="reply_only",
            location_requirement="not_required",
            resolution_reason="The response is conceptual explanation and makes no manuscript-change claim.",
            status=status,
            semantic_review_confirmed=True,
            verification_report_id="semantic-review-01",
            completed_at_utc=STAMP if status == "completed" else None,
        )

    def _call(self, unit):
        ledger = RevisionLedger(
            revision_round_id="round-release-tests",
            response_units=[unit],
        )
        return build_final_release_manifest(
            ledger,
            self.workflow,
            revision_ledger_path=self.revision_path,
            workflow_ledger_path=self.workflow_path,
            response_letter_md=self.root / "missing.md",
            response_letter_docx=self.root / "missing.docx",
            response_letter_quality=self.root / "missing-quality.json",
        )

    def test_release_is_blocked_until_every_response_unit_is_completed(self):
        with self.assertRaisesRegex(ReleaseGateError, "explicitly completed"):
            self._call(self._unit(status="verified"))

    def test_release_is_blocked_when_a_workflow_comment_has_no_matching_response_unit(self):
        with self.assertRaisesRegex(ReleaseGateError, "comment C1 must map to exactly one completed ResponseUnit"):
            self._call(self._unit(status="completed", comment_id="C2"))


if __name__ == "__main__":
    unittest.main()

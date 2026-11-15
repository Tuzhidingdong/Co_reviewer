from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.batch_store import (
    BatchStoreError,
    create_batch_intent,
    load_active_document_pointers,
    publish_active_pointers,
    rollback_active_pointers,
    write_json_new,
)
from co_reviewer.workflow_engine import build_workflow_ledger, validate_workflow_ledger


STAMP = "2026-11-15T08:00:00+08:00"


class BatchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Manuscript.docx"
        self.source.write_bytes(b"synthetic source DOCX payload")
        self.workflow_path = self.root / "workflow_ledger.json"
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
                    "title": "Synthetic batch parent task",
                    "source_comment_ids": ["C1"],
                    "target_documents": [],
                    "evidence": [],
                    "proposed_action": "A synthetic workflow task used only for batch-store tests.",
                    "targets": [],
                    "dependencies": [],
                    "readiness": "ready_for_author_approval",
                    "unresolved_questions": [],
                    "confidence": 1.0,
                    "status": "pending",
                    "requires_user_approval": True,
                }],
            },
            "round-batch-tests",
            STAMP,
        )
        self.workflow_path.write_text(
            json.dumps(self.workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.runs_dir = self.root / "runs"

    def tearDown(self):
        self.temp.cleanup()

    def _intent(self, batch_id="run-001"):
        return create_batch_intent(
            self.workflow,
            self.workflow_path,
            ["T1"],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id=batch_id,
        )

    def test_batch_intent_is_append_only_and_outputs_are_inside_unique_directory(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        self.assertEqual(batch_dir.name, "batch-run-001")
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        self.assertEqual(output.parent, batch_dir / "documents")
        self.assertEqual(
            intent["documents"]["Manuscript"]["parent_sha256"],
            hashlib.sha256(self.source.read_bytes()).hexdigest(),
        )
        self.assertTrue((batch_dir / "batch_intent.json").is_file())
        with self.assertRaises(BatchStoreError):
            self._intent()
        with self.assertRaises(BatchStoreError):
            write_json_new(batch_dir / "batch_intent.json", {"rewrite": True})

    def test_active_pointer_rejects_source_changed_outside_ledger(self):
        load_active_document_pointers(self.workflow)
        self.source.write_bytes(b"externally modified source")
        with self.assertRaisesRegex(BatchStoreError, "changed outside the Workflow Ledger pointer"):
            load_active_document_pointers(self.workflow)

    def test_publish_switches_all_active_pointers_only_after_verified_hash_match(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        output.write_bytes(b"verified synthetic Marked DOCX")
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        patch = SimpleNamespace(
            patch_id="P1",
            status="verified",
            operations=[SimpleNamespace(document="Manuscript", output_file=str(output))],
            verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
        )
        revision_ledger = SimpleNamespace(patches=[patch])

        updated, receipt = publish_active_pointers(
            self.workflow, self.workflow_path, batch_dir, revision_ledger
        )
        pointer = updated["active_documents"]["Manuscript"]
        self.assertEqual(pointer["file"], str(output.resolve()))
        self.assertEqual(pointer["sha256"], output_sha)
        self.assertEqual(pointer["batch_id"], "run-001")
        self.assertEqual(receipt["status"], "published")
        self.assertEqual(validate_workflow_ledger(updated), [])
        self.assertEqual(
            self.workflow["active_documents"]["Manuscript"]["file"],
            str(self.source),
        )

    def test_publish_rejects_unverified_patch_without_changing_parent_ledger(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        output.write_bytes(b"not yet verified")
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        revision_ledger = SimpleNamespace(patches=[SimpleNamespace(
            patch_id="P1",
            status="applied",
            operations=[SimpleNamespace(document="Manuscript", output_file=str(output))],
            verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
        )])
        before = json.dumps(self.workflow, sort_keys=True)
        with self.assertRaisesRegex(BatchStoreError, "not verified"):
            publish_active_pointers(self.workflow, self.workflow_path, batch_dir, revision_ledger)
        self.assertEqual(json.dumps(self.workflow, sort_keys=True), before)

    def test_rollback_restores_parent_pointer_and_retains_batch_output(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        output.write_bytes(b"published synthetic Marked DOCX")
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        published = json.loads(json.dumps(self.workflow))
        published["active_documents"] = {
            "Manuscript": {
                "file": str(output.resolve()),
                "sha256": output_sha,
                "batch_id": "run-001",
                "updated_at_utc": STAMP,
            }
        }
        self.assertEqual(validate_workflow_ledger(published), [])

        updated, receipt = rollback_active_pointers(
            published,
            self.workflow_path,
            batch_dir,
            reason="Synthetic rollback test.",
        )
        restored = updated["active_documents"]["Manuscript"]
        self.assertEqual(restored["file"], str(self.source.resolve()))
        self.assertEqual(restored["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertIsNone(restored["batch_id"])
        self.assertEqual(updated["tasks"][0]["status"], "recheck_required")
        self.assertEqual(receipt["status"], "rolled_back")
        self.assertTrue(receipt["retained_batch_files"])
        self.assertTrue(output.is_file())
        self.assertEqual(output.read_bytes(), b"published synthetic Marked DOCX")
        self.assertEqual(validate_workflow_ledger(updated), [])


if __name__ == "__main__":
    unittest.main()

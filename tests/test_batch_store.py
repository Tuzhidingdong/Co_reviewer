from __future__ import annotations

import copy
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
    _batch_artifact_inventory,
    assert_no_uncommitted_role_batches,
    create_batch_intent,
    load_active_document_pointers,
    publish_active_pointers,
    read_batch_intent,
    recover_unpublished_batch,
    retire_unscoped_prepared_batch,
    rollback_active_pointers,
    write_json_new,
)
from co_reviewer.revision_models import Patch, PatchOperation, RevisionDocument, RevisionLedger
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

    def test_batch_inventory_excludes_only_root_level_recovery_controls(self):
        batch_dir = self.root / "batch-inventory"
        documents_dir = batch_dir / "documents"
        documents_dir.mkdir(parents=True)
        (batch_dir / "batch_recovery_intent.json").write_text(
            "root recovery control", encoding="utf-8"
        )
        (batch_dir / "batch_recovered.json").write_text(
            "root recovery receipt", encoding="utf-8"
        )
        nested_intent = documents_dir / "batch_recovery_intent.json"
        nested_receipt = documents_dir / "batch_recovered.json"
        ordinary = documents_dir / "partial-output.bin"
        nested_intent.write_text("retained nested intent-name artifact", encoding="utf-8")
        nested_receipt.write_text("retained nested receipt-name artifact", encoding="utf-8")
        ordinary.write_bytes(b"partial output")

        inventory = _batch_artifact_inventory(batch_dir)
        by_path = {row["path"]: row for row in inventory}

        self.assertNotIn("batch_recovery_intent.json", by_path)
        self.assertNotIn("batch_recovered.json", by_path)
        for path in (nested_intent, nested_receipt, ordinary):
            relative = path.relative_to(batch_dir).as_posix()
            self.assertIn(relative, by_path)
            self.assertEqual(by_path[relative]["size_bytes"], path.stat().st_size)
            self.assertEqual(
                by_path[relative]["sha256"],
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )

    def test_batch_inventory_rejects_symbolic_links(self):
        batch_dir = self.root / "batch-symlink"
        batch_dir.mkdir()
        external = self.root / "outside-batch.bin"
        external.write_bytes(b"outside batch content")
        link = batch_dir / "linked-artifact.bin"
        try:
            link.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Symbolic links are unavailable in this environment: {exc}")

        with self.assertRaisesRegex(BatchStoreError, "must not be a symbolic link"):
            _batch_artifact_inventory(batch_dir)

        external_batch = self.root / "external-batch"
        external_batch.mkdir()
        batch_directory_link = self.root / "batch-directory-link"
        try:
            batch_directory_link.symlink_to(external_batch, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symbolic links are unavailable in this environment: {exc}")
        with self.assertRaisesRegex(BatchStoreError, "Batch directory must not be a symbolic link"):
            _batch_artifact_inventory(batch_directory_link)
        with self.assertRaisesRegex(BatchStoreError, "Batch directory must not be a symbolic link"):
            read_batch_intent(batch_directory_link)

    def test_role_lock_rejects_symlinked_batch_directories(self):
        self.runs_dir.mkdir()
        external_batch = self.root / "batch-external"
        external_batch.mkdir()
        (external_batch / "batch-intent-placeholder").write_text("not a real batch", encoding="utf-8")
        batch_directory_link = self.runs_dir / "batch-external"
        try:
            batch_directory_link.symlink_to(external_batch, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Directory symbolic links are unavailable in this environment: {exc}")

        with self.assertRaisesRegex(BatchStoreError, "Batch directory must not be a symbolic link"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {},
                self.workflow,
            )

    def test_role_lock_rejects_artifacts_without_intent_but_allows_empty_pre_intent_shell(self):
        self.runs_dir.mkdir()

        # A crash immediately after creating the batch directory and its
        # empty documents/ child but before writing batch_intent.json is safe.
        empty_pre_intent = self.runs_dir / "batch-pre-intent"
        (empty_pre_intent / "documents").mkdir(parents=True)
        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T1"],
            {},
            self.workflow,
        )

        # Any other payload without an immutable intent has no trustworthy
        # parent/task binding and must stop the next batch from being created.
        orphan = self.runs_dir / "batch-orphaned-payload"
        orphan.mkdir()
        (orphan / "batch_scope.json").write_text(
            json.dumps({"batch_id": "orphaned"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BatchStoreError, "contains artifacts without an immutable batch intent"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {},
                self.workflow,
            )

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

    def test_uncommitted_batch_locks_same_document_role_until_patch_superseded(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        scope = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "batch_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_intent.json").read_bytes()
            ).hexdigest(),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "patch_ids": ["P1"],
            "documents": {},
        }
        (batch_dir / "batch_scope.json").write_text(
            json.dumps(scope, ensure_ascii=False),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BatchStoreError, "reserved by uncommitted batch"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T2"],
                {"P1": "planned"},
                self.workflow,
            )
        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T2"],
            {"P1": "superseded"},
            self.workflow,
        )



    def _make_orphaned_prepared_batch_with_published_successor(self):
        orphan = self._intent(batch_id="orphan-prepared")
        successor = create_batch_intent(
            self.workflow,
            self.workflow_path,
            ["T1"],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id="successor-published",
        )
        successor_dir = Path(successor["batch_dir"])
        output = Path(successor["documents"]["Manuscript"]["output_file"])
        output.write_bytes(b"verified successor marked DOCX")
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        revision_path = self.root / "revision_ledger.json"
        revision_path.write_text("{}", encoding="utf-8")
        operation = SimpleNamespace(
            document="Manuscript",
            input_file=str(self.source.resolve()),
            output_file=str(output.resolve()),
            source_sha256=source_sha,
        )
        patch = SimpleNamespace(
            patch_id="P1",
            task_id="T1",
            status="verified",
            operations=[operation],
            verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
        )
        scope = {
            "schema_version": "1.0",
            "batch_id": successor["batch_id"],
            "revision_ledger_file": str(revision_path.resolve()),
            "batch_intent_sha256": hashlib.sha256(
                (successor_dir / "batch_intent.json").read_bytes()
            ).hexdigest(),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "patch_ids": ["P1"],
            "documents": {
                "Manuscript": {
                    "input_file": str(self.source.resolve()),
                    "input_sha256": source_sha,
                    "output_file": str(output.resolve()),
                }
            },
        }
        write_json_new(successor_dir / "batch_scope.json", scope)
        revision_ledger = SimpleNamespace(patches=[patch])
        published_workflow, publish_receipt = publish_active_pointers(
            self.workflow,
            self.workflow_path,
            successor_dir,
            revision_ledger,
        )
        intent_sha = hashlib.sha256((successor_dir / "batch_intent.json").read_bytes()).hexdigest()
        scope_sha = hashlib.sha256((successor_dir / "batch_scope.json").read_bytes()).hexdigest()
        revision_sha = hashlib.sha256(revision_path.read_bytes()).hexdigest()
        publish_receipt.update({
            "revision_ledger_file": str(revision_path.resolve()),
            "revision_ledger_sha256": revision_sha,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
        })
        publish_intent = {
            "schema_version": "1.0",
            "batch_id": successor["batch_id"],
            "prepared_at_utc": STAMP,
            "active_documents_to_publish": publish_receipt["active_documents"],
            "revision_ledger_sha256": revision_sha,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
        }
        write_json_new(successor_dir / "batch_publish_intent.json", publish_intent)
        write_json_new(successor_dir / "batch_published.json", publish_receipt)
        self.workflow_path.write_text(
            json.dumps(published_workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return orphan, successor, revision_ledger, revision_path, published_workflow

    def test_intent_only_prepared_batch_can_be_retired_by_exact_published_successor(self):
        orphan, successor, ledger, revision_path, workflow = (
            self._make_orphaned_prepared_batch_with_published_successor()
        )
        orphan_dir = Path(orphan["batch_dir"])
        workflow_before = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        ledger_before = revision_path.read_bytes()
        receipt = retire_unscoped_prepared_batch(
            orphan_dir,
            successor["batch_dir"],
            workflow,
            self.workflow_path,
            ledger,
            revision_path,
            actor="Acceptance Tester",
            reason="The intent-only preparation was replaced by the exact verified published batch.",
        )
        self.assertEqual(receipt["status"], "superseded_prepared")
        self.assertTrue((orphan_dir / "batch_superseded.json").is_file())
        self.assertEqual(
            json.loads(self.workflow_path.read_text(encoding="utf-8")),
            workflow_before,
        )
        self.assertEqual(revision_path.read_bytes(), ledger_before)
        self.assertEqual(
            load_active_document_pointers(workflow)["Manuscript"]["batch_id"],
            successor["batch_id"],
        )
        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T2"],
            {"P1": "verified"},
            workflow,
            revision_patches_by_id={"P1": ledger.patches[0]},
            revision_ledger_path=revision_path,
        )

    def test_intent_only_prepared_batch_retirement_rejects_extra_artifacts(self):
        orphan, successor, ledger, revision_path, workflow = (
            self._make_orphaned_prepared_batch_with_published_successor()
        )
        orphan_dir = Path(orphan["batch_dir"])
        (orphan_dir / "unexpected.txt").write_text("not an intent-only orphan", encoding="utf-8")
        with self.assertRaisesRegex(BatchStoreError, "additional artifacts"):
            retire_unscoped_prepared_batch(
                orphan_dir,
                successor["batch_dir"],
                workflow,
                self.workflow_path,
                ledger,
                revision_path,
                actor="Acceptance Tester",
                reason="Must not retire a batch with additional artifacts.",
            )

    def test_intent_only_prepared_batch_retirement_receipt_is_hash_bound(self):
        orphan, successor, ledger, revision_path, workflow = (
            self._make_orphaned_prepared_batch_with_published_successor()
        )
        orphan_dir = Path(orphan["batch_dir"])
        retire_unscoped_prepared_batch(
            orphan_dir,
            successor["batch_dir"],
            workflow,
            self.workflow_path,
            ledger,
            revision_path,
            actor="Acceptance Tester",
            reason="Hash-binding regression fixture.",
        )
        receipt_path = orphan_dir / "batch_superseded.json"
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["successor_batch_id"] = "forged-successor"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.assertRaisesRegex(BatchStoreError, "missing/unsafe successor"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T2"],
                {"P1": "verified"},
                workflow,
                revision_patches_by_id={"P1": ledger.patches[0]},
                revision_ledger_path=revision_path,
            )


    def test_role_lock_rejects_receipts_without_a_complete_transaction_chain(self):
        cases = (
            ("batch_published.json", "publication intent and receipt"),
            ("batch_rollback.json", "incomplete publication transaction"),
        )
        for receipt_name, expected_error in cases:
            with self.subTest(receipt=receipt_name):
                intent = self._intent(batch_id=f"receipt-{receipt_name.split('.')[0]}")
                batch_dir = Path(intent["batch_dir"])
                (batch_dir / receipt_name).write_text(
                    json.dumps({"batch_id": intent["batch_id"], "status": "published"}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(BatchStoreError, expected_error):
                    assert_no_uncommitted_role_batches(
                        self.runs_dir,
                        ["Manuscript"],
                        ["T2"],
                        {},
                        self.workflow,
                    )

    def test_hash_bound_publication_and_rollback_chains_release_role_reservation(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        output.write_bytes(b"verified synthetic published DOCX")
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
        revision_path = self.root / "revision_ledger.json"
        revision_path.write_text("{}", encoding="utf-8")
        scope = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "revision_ledger_file": str(revision_path.resolve()),
            "batch_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_intent.json").read_bytes()
            ).hexdigest(),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "patch_ids": ["P1"],
            "documents": {
                "Manuscript": {
                    "input_file": str(self.source.resolve()),
                    "input_sha256": source_sha,
                    "output_file": str(output.resolve()),
                }
            },
        }
        write_json_new(batch_dir / "batch_scope.json", scope)
        patch = SimpleNamespace(
            patch_id="P1",
            status="verified",
            operations=[SimpleNamespace(document="Manuscript", output_file=str(output))],
            verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
        )
        published_workflow, publish_receipt = publish_active_pointers(
            self.workflow,
            self.workflow_path,
            batch_dir,
            SimpleNamespace(patches=[patch]),
        )
        intent_sha = hashlib.sha256((batch_dir / "batch_intent.json").read_bytes()).hexdigest()
        scope_sha = hashlib.sha256((batch_dir / "batch_scope.json").read_bytes()).hexdigest()
        revision_sha = hashlib.sha256(revision_path.read_bytes()).hexdigest()
        publish_receipt.update({
            "revision_ledger_file": str(revision_path.resolve()),
            "revision_ledger_sha256": revision_sha,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
        })
        publish_intent = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "prepared_at_utc": STAMP,
            "active_documents_to_publish": publish_receipt["active_documents"],
            "revision_ledger_sha256": revision_sha,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
        }
        write_json_new(batch_dir / "batch_publish_intent.json", publish_intent)
        write_json_new(batch_dir / "batch_published.json", publish_receipt)

        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T2"],
            {"P1": "verified"},
            published_workflow,
        )

        reason = "Synthetic rollback for transaction-chain test."
        rolled_back_workflow, rollback_receipt = rollback_active_pointers(
            published_workflow,
            self.workflow_path,
            batch_dir,
            reason=reason,
        )
        rollback_intent = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
            "reason": reason,
            "prepared_at_utc": STAMP,
            "restored_active_documents": rollback_receipt["restored_active_documents"],
        }
        write_json_new(batch_dir / "batch_rollback_intent.json", rollback_intent)
        rollback_receipt.update({
            "reason": reason,
            "batch_intent_sha256": intent_sha,
            "batch_scope_sha256": scope_sha,
            "rollback_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_rollback_intent.json").read_bytes()
            ).hexdigest(),
        })
        write_json_new(batch_dir / "batch_rollback.json", rollback_receipt)

        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T2"],
            {"P1": "verified"},
            rolled_back_workflow,
        )

    def test_full_reauthorized_task_set_can_rebase_to_a_new_batch_directory(self):
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        scope = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "batch_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_intent.json").read_bytes()
            ).hexdigest(),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "patch_ids": ["P1"],
            "documents": {},
        }
        (batch_dir / "batch_scope.json").write_text(
            json.dumps(scope, ensure_ascii=False),
            encoding="utf-8",
        )
        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T1"],
            {"P1": "applied"},
            self.workflow,
        )


    def test_partial_unpublished_batch_recovery_is_idempotent_and_rebases_from_parent(self):
        task_template = {
            "task_type": "response_only",
            "target_documents": [],
            "evidence": [],
            "proposed_action": "A synthetic workflow task used only for batch recovery DAG tests.",
            "targets": [],
            "readiness": "ready_for_author_approval",
            "unresolved_questions": [],
            "confidence": 1.0,
            "status": "pending",
            "requires_user_approval": True,
        }
        task_one = {
            **task_template,
            "task_id": "T1",
            "sequence": 1,
            "title": "Synthetic failed batch task",
            "source_comment_ids": ["C1"],
            "dependencies": [],
        }
        task_two = {
            **task_template,
            "task_id": "T2",
            "sequence": 2,
            "title": "Synthetic downstream dependent task",
            "source_comment_ids": ["C2"],
            "dependencies": ["T1"],
        }
        self.workflow = build_workflow_ledger(
            {
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(self.source),
                    "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                }],
                "tasks": [task_one, task_two],
            },
            "round-batch-tests",
            STAMP,
        )
        self.workflow_path.write_text(
            json.dumps(self.workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        intent = self._intent()
        batch_dir = Path(intent["batch_dir"])
        output = Path(intent["documents"]["Manuscript"]["output_file"])
        partial_bytes = b"partial DOCX written before Apply failed"
        output.write_bytes(partial_bytes)
        source_sha = hashlib.sha256(self.source.read_bytes()).hexdigest()
        revision_path = self.root / "revision_ledger.json"
        operation = PatchOperation.from_mapping({
            "operation_id": "P1-OP1",
            "document": "Manuscript",
            "input_file": str(self.source),
            "output_file": str(output),
            "part": "word/document.xml",
            "find": "old phrase",
            "replace": "new phrase",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "location": "Synthetic paragraph",
            "source_sha256": source_sha,
            "evidence_ids": ["E1"],
            "source_comment_ids": ["C1"],
        })
        patch = Patch(
            patch_id="P1",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Synthetic reviewer comment.",
            proposed_response="Synthetic response.",
            operations=[operation],
            status="approved",
            preflight_fingerprint="a" * 64,
            preflight_report_id="preflight-P1",
            approval_fingerprint="b" * 64,
        )
        document = RevisionDocument(
            document_id="Manuscript",
            revision_id="revision-1",
            role="Manuscript",
            input_file=str(self.source),
            output_file=str(output),
            source_sha256=source_sha,
        )
        ledger = RevisionLedger(
            "round-batch-tests",
            documents=[document],
            patches=[patch],
        )
        revision_path.write_text(
            json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        scope = {
            "schema_version": "1.0",
            "batch_id": intent["batch_id"],
            "revision_round_id": "round-batch-tests",
            "revision_ledger_file": str(revision_path.resolve()),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "batch_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_intent.json").read_bytes()
            ).hexdigest(),
            "patch_ids": ["P1"],
            "documents": {
                "Manuscript": {
                    "input_file": str(self.source.resolve()),
                    "input_sha256": source_sha,
                    "output_file": str(output.resolve()),
                }
            },
        }
        (batch_dir / "batch_scope.json").write_text(
            json.dumps(scope, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        first_workflow, first_ledger, recovery_intent, workflow_changed, ledger_changed, already = recover_unpublished_batch(
            self.workflow,
            self.workflow_path,
            ledger,
            revision_path,
            batch_dir,
            actor="Human Author",
            reason="Synthetic partial Apply failure.",
        )
        self.assertTrue(workflow_changed)
        self.assertTrue(ledger_changed)
        self.assertFalse(already)
        self.assertEqual(first_ledger.patches[0].status, "abandoned")
        task_states = {
            row["task_id"]: row["status"] for row in first_workflow["tasks"]
        }
        self.assertEqual(task_states, {
            "T1": "recheck_required",
            "T2": "recheck_required",
        })
        self.assertEqual(recovery_intent["invalidated_task_ids"], ["T1", "T2"])
        self.assertEqual(output.read_bytes(), partial_bytes)

        # Simulate a crash after the first atomic file replacement (Workflow Ledger).
        self.workflow_path.write_text(
            json.dumps(first_workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resumed_workflow, resumed_ledger, resumed_intent, workflow_changed, ledger_changed, already = recover_unpublished_batch(
            first_workflow,
            self.workflow_path,
            ledger,
            revision_path,
            batch_dir,
            actor="Human Author",
            reason="This retry must use the staged reason.",
        )
        self.assertFalse(workflow_changed)
        self.assertTrue(ledger_changed)
        self.assertFalse(already)
        self.assertEqual(resumed_intent["recovery_id"], recovery_intent["recovery_id"])
        revision_path.write_text(
            json.dumps(resumed_ledger.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # A second retry after both Ledger writes must not mutate either Ledger.
        final_workflow, final_ledger, final_intent, workflow_changed, ledger_changed, already = recover_unpublished_batch(
            resumed_workflow,
            self.workflow_path,
            resumed_ledger,
            revision_path,
            batch_dir,
            actor="Human Author",
            reason="Idempotent receipt completion.",
        )
        self.assertFalse(workflow_changed)
        self.assertFalse(ledger_changed)
        self.assertFalse(already)
        self.assertEqual(final_intent["recovery_id"], recovery_intent["recovery_id"])
        self.assertEqual(final_ledger.patches[0].status, "abandoned")
        self.assertEqual(
            load_active_document_pointers(final_workflow)["Manuscript"]["file"],
            str(self.source.resolve()),
        )

        # Do not allow a new batch between the staged Ledger writes and final receipt.
        with self.assertRaisesRegex(BatchStoreError, "staged recovery transaction"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
            )
        receipt = {
            "schema_version": "1.0",
            "status": "recovered",
            "recovery_id": recovery_intent["recovery_id"],
            "batch_id": intent["batch_id"],
            "recovered_at_utc": STAMP,
            "actor": recovery_intent["actor"],
            "reason": recovery_intent["reason"],
            "workflow_ledger_file": recovery_intent["workflow_ledger_file"],
            "revision_ledger_file": recovery_intent["revision_ledger_file"],
            "workflow_ledger_sha256": recovery_intent["workflow_ledger_sha256_after"],
            "revision_ledger_sha256": recovery_intent["revision_ledger_sha256_after"],
            "batch_recovery_intent_sha256": hashlib.sha256(
                (batch_dir / "batch_recovery_intent.json").read_bytes()
            ).hexdigest(),
            "batch_intent_sha256": recovery_intent["batch_intent_sha256"],
            "batch_scope_sha256": recovery_intent["batch_scope_sha256"],
            "selected_task_ids": recovery_intent["selected_task_ids"],
            "invalidated_task_ids": recovery_intent["invalidated_task_ids"],
            "patch_ids": ["P1"],
            "parent_documents": recovery_intent["parent_documents"],
            "failed_output_files": recovery_intent["failed_output_files"],
            "retained_artifacts": recovery_intent["artifact_inventory"],
            "retained_batch_files": True,
        }
        receipt_path = batch_dir / "batch_recovered.json"
        write_json_new(receipt_path, {**receipt, "retained_artifacts": []})
        with self.assertRaisesRegex(BatchStoreError, "receipt does not attest"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
                revision_events=final_ledger.events,
            )
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        for ledger_field in ("workflow_ledger_file", "revision_ledger_file"):
            tampered_receipt = dict(receipt)
            tampered_receipt[ledger_field] = str(self.root / "forged-ledger.json")
            receipt_path.write_text(
                json.dumps(tampered_receipt, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(BatchStoreError, "ledger file paths differ"):
                assert_no_uncommitted_role_batches(
                    self.runs_dir,
                    ["Manuscript"],
                    ["T1"],
                    {"P1": "abandoned"},
                    final_workflow,
                    revision_events=final_ledger.events,
                )
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        recovery_intent_path = batch_dir / "batch_recovery_intent.json"
        original_recovery_intent = recovery_intent_path.read_text(encoding="utf-8")
        tampered_recovery_intent = json.loads(original_recovery_intent)
        tampered_recovery_intent["workflow_after"]["active_documents"]["Manuscript"]["file"] = str(
            self.root / "forged-parent.docx"
        )
        recovery_intent_path.write_text(
            json.dumps(tampered_recovery_intent, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tampered_receipt = dict(receipt)
        tampered_receipt["batch_recovery_intent_sha256"] = hashlib.sha256(
            recovery_intent_path.read_bytes()
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(tampered_receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BatchStoreError, "snapshot hash"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
                    revision_events=final_ledger.events,
            )
        recovery_intent_path.write_text(original_recovery_intent, encoding="utf-8")
        # Rebind the receipt to the exact restored immutable intent bytes.
        receipt["batch_recovery_intent_sha256"] = hashlib.sha256(
            recovery_intent_path.read_bytes()
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        event_tampered_intent = json.loads(original_recovery_intent)
        event_tampered_intent["workflow_after"]["events"] = [
            row for row in event_tampered_intent["workflow_after"]["events"]
            if not (
                isinstance(row, dict)
                and row.get("task_id") == "T2"
                and row.get("event_type") == "invalidation"
                and row.get("batch_id") == intent["batch_id"]
            )
        ]
        event_tampered_intent["workflow_ledger_sha256_after"] = hashlib.sha256(
            json.dumps(
                event_tampered_intent["workflow_after"],
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
        ).hexdigest()
        recovery_intent_path.write_text(
            json.dumps(event_tampered_intent, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        event_tampered_receipt = dict(receipt)
        event_tampered_receipt["workflow_ledger_sha256"] = (
            event_tampered_intent["workflow_ledger_sha256_after"]
        )
        event_tampered_receipt["batch_recovery_intent_sha256"] = hashlib.sha256(
            recovery_intent_path.read_bytes()
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(event_tampered_receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BatchStoreError, "invalidation event"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
                    revision_events=final_ledger.events,
            )
        recovery_intent_path.write_text(original_recovery_intent, encoding="utf-8")
        # Rebind the receipt to the exact restored immutable intent bytes.
        receipt["batch_recovery_intent_sha256"] = hashlib.sha256(
            recovery_intent_path.read_bytes()
        ).hexdigest()
        receipt_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(BatchStoreError, "without the current RevisionLedger events"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
            )

        with self.assertRaisesRegex(BatchStoreError, "RevisionLedger recovery event lineage"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
                revision_events=[],
            )

        altered_revision_events = copy.deepcopy(final_ledger.events)
        altered_revision_events[0].details["reason"] = "Tampered persisted recovery event."
        with self.assertRaisesRegex(BatchStoreError, "RevisionLedger recovery event lineage"):
            assert_no_uncommitted_role_batches(
                self.runs_dir,
                ["Manuscript"],
                ["T1"],
                {"P1": "abandoned"},
                final_workflow,
                revision_events=altered_revision_events,
            )

        assert_no_uncommitted_role_batches(
            self.runs_dir,
            ["Manuscript"],
            ["T1"],
            {"P1": "abandoned"},
            final_workflow,
            revision_events=final_ledger.events,
        )
        next_intent = create_batch_intent(
            final_workflow,
            self.workflow_path,
            ["T1"],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id="run-002",
        )
        next_output = Path(next_intent["documents"]["Manuscript"]["output_file"])
        self.assertNotEqual(next_output, output)
        self.assertEqual(next_intent["documents"]["Manuscript"]["parent_sha256"], source_sha)

        replacement_operation = PatchOperation.from_mapping({
            "operation_id": "P2-OP1",
            "document": "Manuscript",
            "input_file": str(self.source),
            "output_file": str(next_output),
            "part": "word/document.xml",
            "find": "old phrase",
            "replace": "corrected phrase",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "location": "Synthetic paragraph",
            "source_sha256": source_sha,
            "evidence_ids": ["E1"],
            "source_comment_ids": ["C1"],
        })
        replacement = Patch(
            patch_id="P2",
            task_id="T1",
            reviewer="Reviewer 1",
            comment="Synthetic reviewer comment.",
            proposed_response="Reapproved response.",
            operations=[replacement_operation],
            supersedes_patch_id="P1",
        )
        final_ledger.supersede_patch(
            "P1",
            replacement,
            actor="Human Author",
            reason="Re-authorized from the unchanged active parent.",
        )
        self.assertEqual(final_ledger.patches[0].status, "superseded")
        self.assertEqual(final_ledger.patches[1].operations[0].input_file, str(self.source))
        incoming_document = RevisionDocument(
            document_id="Manuscript",
            revision_id="revision-2",
            role="Manuscript",
            input_file=str(self.source),
            output_file=str(next_output),
            source_sha256=source_sha,
        )
        final_ledger.merge_batch(
            RevisionLedger("round-batch-tests", documents=[incoming_document])
        )
        self.assertEqual(final_ledger.documents[0].input_file, str(self.source))
        self.assertEqual(final_ledger.documents[0].output_file, str(next_output))

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

    def test_two_sequential_publications_keep_initial_inventory_separate_from_history(self):
        workflow = copy.deepcopy(self.workflow)
        initial_source = dict(workflow["source_files"][0])
        workflow["tasks"][0]["source_versions"] = [dict(initial_source)]
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        published_versions = []

        for index in (1, 2):
            batch_id = f"sequential-{index:03d}"
            # Separate run roots keep this test focused on publication-ledger
            # semantics; role-lock lifecycle is covered by its own tests.
            intent = create_batch_intent(
                workflow,
                self.workflow_path,
                ["T1"],
                ["Manuscript"],
                runs_dir=self.root / f"runs-{index:03d}",
                batch_id=batch_id,
            )
            batch_dir = Path(intent["batch_dir"])
            output = Path(intent["documents"]["Manuscript"]["output_file"])
            output.write_bytes(f"sequential published DOCX {index}".encode("utf-8"))
            output_sha = hashlib.sha256(output.read_bytes()).hexdigest()
            source_sha = hashlib.sha256(
                Path(intent["documents"]["Manuscript"]["parent_file"]).read_bytes()
            ).hexdigest()
            self.assertEqual(
                intent["documents"]["Manuscript"]["parent_sha256"],
                source_sha,
            )

            patch = SimpleNamespace(
                patch_id=f"P-{index}",
                task_id="T1",
                status="verified",
                operations=[SimpleNamespace(
                    document="Manuscript",
                    input_file=intent["documents"]["Manuscript"]["parent_file"],
                    output_file=str(output),
                    source_sha256=source_sha,
                )],
                verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
            )
            workflow, receipt = publish_active_pointers(
                workflow,
                self.workflow_path,
                batch_dir,
                SimpleNamespace(patches=[patch]),
            )
            self.assertEqual(receipt["status"], "published")
            version = {
                "role": "Manuscript",
                "file": str(output.resolve()),
                "sha256": output_sha,
            }
            published_versions.append(version)

            # Simulate the downstream re-preparation step which records the
            # version used as its input. It must remain history, not be copied
            # into the single initial source_files inventory.
            if index == 1:
                workflow["tasks"][0]["source_versions"].append(dict(version))

            self.assertEqual(validate_workflow_ledger(workflow), [])
            self.workflow_path.write_text(
                json.dumps(workflow, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        self.assertEqual(len(workflow["source_files"]), 1)
        self.assertEqual(workflow["source_files"][0], initial_source)
        self.assertEqual(
            workflow["tasks"][0]["source_versions"],
            [initial_source, published_versions[0]],
        )
        self.assertEqual(
            workflow["active_documents"]["Manuscript"]["file"],
            published_versions[1]["file"],
        )
        self.assertEqual(
            [event["batch_id"] for event in workflow["events"]
             if event.get("event_type") == "batch_published"],
            ["sequential-001", "sequential-002"],
        )
        self.assertEqual(validate_workflow_ledger(workflow), [])

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
        source_sha = hashlib.sha256(
            Path(intent["documents"]["Manuscript"]["parent_file"]).read_bytes()
        ).hexdigest()
        patch = SimpleNamespace(
            patch_id="P1",
            task_id="T1",
            status="verified",
            operations=[SimpleNamespace(
                document="Manuscript",
                input_file=intent["documents"]["Manuscript"]["parent_file"],
                output_file=str(output),
                source_sha256=source_sha,
            )],
            verified_output_hashes=[{"path": str(output), "sha256": output_sha}],
        )
        published, publish_receipt = publish_active_pointers(
            self.workflow,
            self.workflow_path,
            batch_dir,
            SimpleNamespace(patches=[patch]),
        )
        self.assertEqual(publish_receipt["status"], "published")
        self.assertEqual(
            [event["event_type"] for event in published["events"]],
            ["batch_published"],
        )
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

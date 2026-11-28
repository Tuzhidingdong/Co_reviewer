from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docx import Document
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from co_reviewer.release_package import (
    ReleasePackageTransactionError,
    collect_release_package_artifacts,
    commit_release_package,
    verify_release_package,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


class ReleasePackageTransactionTests(unittest.TestCase):
    def _fixture(self, root: Path):
        round_root = root / "round"
        round_root.mkdir()
        sources = round_root / "sources"
        sources.mkdir()
        runs = round_root / "runs"
        batch_one_dir = runs / "batch-b001"
        batch_two_dir = runs / "batch-b002"
        batch_one_documents = batch_one_dir / "documents"
        batch_two_documents = batch_two_dir / "documents"
        batch_one_documents.mkdir(parents=True)
        batch_two_documents.mkdir(parents=True)

        initial = sources / "论文初稿.docx"
        batch_one_marked = batch_one_documents / "Manuscript.marked.docx"
        marked = batch_two_documents / "Manuscript.marked.docx"
        working_clean = runs / "Manuscript.working-clean.docx"
        final_clean = runs / "Manuscript.final-clean.docx"
        response_docx = round_root / "Response_to_Reviewers.docx"
        for path, text in (
            (initial, "Original manuscript"),
            (batch_one_marked, "First published Marked manuscript"),
            (marked, "Second published Marked manuscript"),
            (working_clean, "Working Clean manuscript"),
            (final_clean, "Final Clean manuscript"),
            (response_docx, "Response to Reviewers"),
        ):
            _docx(path, text)

        final_pdf = runs / "Manuscript.final.pdf"
        final_pdf.write_bytes(b"%PDF-1.7 synthetic final PDF")
        response_md = round_root / "Response_to_Reviewers.md"
        response_md.write_text("# Response to Reviewers\n", encoding="utf-8")
        response_quality = round_root / "Response_to_Reviewers.quality.json"
        response_quality.write_text('{"status":"PASS"}\n', encoding="utf-8")

        workflow_path = round_root / "workflow_ledger.json"
        revision_path = round_root / "revision_ledger.json"
        workflow_path.write_text(
            json.dumps({
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(initial),
                    "sha256": _sha(initial),
                }]
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        revision_path.write_text('{"schema_version":"0.5.1"}\n', encoding="utf-8")
        intent_path = round_root / "release_package_intent.json"
        intent_path.write_text('{"schema_version":"1.0","status":"building"}\n', encoding="utf-8")

        initial_sha = _sha(initial)
        batch_one_sha = _sha(batch_one_marked)
        batch_two_sha = _sha(marked)
        batch_one_intent = {
            "batch_id": "b001",
            "selected_task_ids": ["T1"],
            "documents": {
                "Manuscript": {
                    "parent_file": str(initial),
                    "parent_sha256": initial_sha,
                    "output_file": str(batch_one_marked),
                }
            },
        }
        batch_one_intent_path = batch_one_dir / "batch_intent.json"
        batch_one_intent_path.write_text(
            json.dumps(batch_one_intent, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        batch_one_scope = {
            "batch_id": "b001",
            "batch_intent_sha256": _sha(batch_one_intent_path),
            "documents": {"Manuscript": {
                "input_file": str(initial),
                "input_sha256": initial_sha,
                "output_file": str(batch_one_marked),
            }},
        }
        (batch_one_dir / "batch_scope.json").write_text(
            json.dumps(batch_one_scope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (batch_one_dir / "batch_published.json").write_text(
            json.dumps({
                "batch_id": "b001",
                "status": "published",
                "active_documents": {"Manuscript": {
                    "file": str(batch_one_marked),
                    "sha256": batch_one_sha,
                    "batch_id": "b001",
                }},
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        batch_two_intent = {
            "batch_id": "b002",
            "selected_task_ids": ["T2"],
            "documents": {
                "Manuscript": {
                    "parent_file": str(batch_one_marked),
                    "parent_sha256": batch_one_sha,
                    "output_file": str(marked),
                }
            },
        }
        batch_two_intent_path = batch_two_dir / "batch_intent.json"
        batch_two_intent_path.write_text(
            json.dumps(batch_two_intent, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        batch_two_scope = {
            "batch_id": "b002",
            "batch_intent_sha256": _sha(batch_two_intent_path),
            "documents": {"Manuscript": {
                "input_file": str(batch_one_marked),
                "input_sha256": batch_one_sha,
                "output_file": str(marked),
            }},
        }
        (batch_two_dir / "batch_scope.json").write_text(
            json.dumps(batch_two_scope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (batch_two_dir / "batch_published.json").write_text(
            json.dumps({
                "batch_id": "b002",
                "status": "published",
                "active_documents": {"Manuscript": {
                    "file": str(marked),
                    "sha256": batch_two_sha,
                    "batch_id": "b002",
                }},
            }, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        manifest = {
            "schema_version": "1.0",
            "release_status": "READY",
            "revision_round_id": "round-release-package-test",
            "source_ledgers": {
                "workflow_ledger": {"file": str(workflow_path), "sha256": _sha(workflow_path)},
                "revision_ledger": {"file": str(revision_path), "sha256": _sha(revision_path)},
            },
            "active_documents": {
                "Manuscript": {
                    "file": str(marked),
                    "sha256": _sha(marked),
                    "batch_id": "b002",
                }
            },
            "marked_documents": {
                "Manuscript": {
                    "active_file": str(marked),
                    "active_sha256": _sha(marked),
                    "active_batch_id": "b002",
                    "working_clean_file": str(working_clean),
                    "working_clean_sha256": _sha(working_clean),
                }
            },
            "batches": [
                {"batch_id": "b001", "directory": str(batch_one_dir)},
                {"batch_id": "b002", "directory": str(batch_two_dir)},
            ],
            "recovered_batches": [],
            "final_documents": {
                "Manuscript": {
                    "document": "Manuscript",
                    "final_clean_file": str(final_clean),
                    "final_clean_sha256": _sha(final_clean),
                    "final_pdf_file": str(final_pdf),
                    "final_pdf_sha256": _sha(final_pdf),
                    "quality_report": {
                        "file": str(response_quality),
                        "sha256": _sha(response_quality),
                        "status": "PASS",
                        "release_reaudit_status": "PASS",
                    },
                }
            },
            "location_maps": [],
            "artifacts": {
                "response_letter_markdown": {"file": str(response_md), "sha256": _sha(response_md)},
                "response_letter_docx": {"file": str(response_docx), "sha256": _sha(response_docx)},
                "response_letter_quality": {
                    "file": str(response_quality),
                    "sha256": _sha(response_quality),
                    "bytes": response_quality.stat().st_size,
                    "status": "PASS",
                    "release_reaudit_status": "PASS",
                },
            },
            "qa_remediation": {"status": "NOT_USED", "remediation_round": 0},
        }
        manifest_path = round_root / "final_release_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        records = collect_release_package_artifacts(
            manifest,
            release_manifest_path=manifest_path,
            release_intent_path=intent_path,
            workflow_ledger=workflow,
        )
        release_dir = round_root / "release"
        release_dir.mkdir()
        return {
            "round_root": round_root,
            "release_dir": release_dir,
            "manifest": manifest,
            "manifest_path": manifest_path,
            "intent_path": intent_path,
            "workflow_path": workflow_path,
            "revision_path": revision_path,
            "records": records,
            "initial": initial,
        }

    def test_release_package_commits_all_artifacts_and_recovers_unmarked_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            source_hashes = {
                str(fixture["workflow_path"]): _sha(fixture["workflow_path"]),
                str(fixture["revision_path"]): _sha(fixture["revision_path"]),
            }
            release_id = "release-test-001"
            result = commit_release_package(
                fixture["release_dir"],
                fixture["records"],
                release_id=release_id,
                source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                source_snapshot_hashes=source_hashes,
            )
            self.assertEqual(result["status"], "COMMITTED")
            self.assertGreaterEqual(result["artifact_count"], 10)
            self.assertTrue(Path(result["package_manifest_file"]).is_file())
            self.assertTrue(Path(result["commit_marker_file"]).is_file())

            manifest = verify_release_package(
                fixture["release_dir"], expected_release_id=release_id
            )
            self.assertEqual(manifest["package_id"], result["package_id"])
            package_schema = json.loads(
                (ROOT / "schemas" / "committed-release-package.schema.json").read_text(encoding="utf-8")
            )
            Draft202012Validator(package_schema).validate(manifest)
            categories = {
                category
                for item in manifest["artifacts"]
                for category in item["categories"]
            }
            self.assertTrue({"initial-source", "marked-active", "working-clean", "final-clean", "final-pdf", "source-ledger", "response-artifact", "batches-history"}.issubset(categories))
            batch_history_names = {
                name
                for row in manifest["artifacts"]
                for name in row.get("logical_names", [])
                if name.startswith("batches/")
            }
            self.assertTrue(any(name.startswith("batches/b001/") for name in batch_history_names))
            self.assertTrue(any(name.startswith("batches/b002/") for name in batch_history_names))
            self.assertTrue(any(
                row.get("path", "").endswith("Manuscript.marked.docx")
                and "b001" in row.get("roles", [])
                for row in manifest["artifacts"]
            ))
            self.assertTrue(any(
                row.get("path", "").endswith("Manuscript.marked.docx")
                and "b002" in row.get("roles", [])
                for row in manifest["artifacts"]
            ))

            again = commit_release_package(
                fixture["release_dir"],
                fixture["records"],
                release_id=release_id,
                source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                source_snapshot_hashes=source_hashes,
            )
            self.assertEqual(again["package_id"], result["package_id"])

            marker = Path(result["commit_marker_file"])
            marker.unlink()
            with self.assertRaisesRegex(ReleasePackageTransactionError, "no final commit marker"):
                verify_release_package(fixture["release_dir"], expected_release_id=release_id)

            recovered = commit_release_package(
                fixture["release_dir"],
                fixture["records"],
                release_id=release_id,
                source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                source_snapshot_hashes=source_hashes,
            )
            self.assertEqual(recovered["package_id"], result["package_id"])
            self.assertTrue(marker.is_file())

            packaged_pdf = (
                Path(result["package_directory"])
                / next(row["path"] for row in manifest["artifacts"] if row["path"].endswith("Manuscript.final.pdf"))
            )
            packaged_pdf.write_bytes(packaged_pdf.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ReleasePackageTransactionError, "hash/size mismatch"):
                verify_release_package(fixture["release_dir"], expected_release_id=release_id)

    def test_interruption_after_directory_publish_before_marker_recovers_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            workflow_path = fixture["workflow_path"]
            original_workflow = workflow_path.read_bytes()
            source_hashes = {
                str(workflow_path): _sha(workflow_path),
                str(fixture["revision_path"]): _sha(fixture["revision_path"]),
            }
            from co_reviewer import release_package as package_module
            original_verify = package_module._verify_package_directory_against_manifest
            mutated = {"done": False}

            def verify_then_mutate(package_dir, manifest):
                result = original_verify(package_dir, manifest)
                if not mutated["done"]:
                    workflow_path.write_bytes(original_workflow + b" ")
                    mutated["done"] = True
                return result

            marker = fixture["release_dir"] / "release_package_commit.json"
            with mock.patch(
                "co_reviewer.release_package._verify_package_directory_against_manifest",
                side_effect=verify_then_mutate,
            ):
                with self.assertRaisesRegex(
                    ReleasePackageTransactionError,
                    "changed during package staging",
                ):
                    commit_release_package(
                        fixture["release_dir"],
                        fixture["records"],
                        release_id="release-test-interrupted",
                        source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                        source_snapshot_hashes=source_hashes,
                    )
            self.assertTrue(mutated["done"])
            self.assertFalse(marker.exists())
            package_directories = list(fixture["release_dir"].glob("package-*"))
            self.assertEqual(len(package_directories), 1)
            self.assertTrue((package_directories[0] / "package_manifest.json").is_file())

            # Restore the exact ledger snapshot. The unmarked candidate must be
            # revalidated and only then receive its external commit marker.
            workflow_path.write_bytes(original_workflow)
            recovered = commit_release_package(
                fixture["release_dir"],
                fixture["records"],
                release_id="release-test-interrupted",
                source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                source_snapshot_hashes=source_hashes,
            )
            self.assertEqual(recovered["status"], "COMMITTED")
            self.assertTrue(marker.is_file())
            verified = verify_release_package(
                fixture["release_dir"], expected_release_id="release-test-interrupted"
            )
            self.assertEqual(verified["package_id"], recovered["package_id"])

    def test_unlisted_package_artifact_blocks_committed_package_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            result = commit_release_package(
                fixture["release_dir"],
                fixture["records"],
                release_id="release-test-extra",
                source_release_manifest_sha256=_sha(fixture["manifest_path"]),
            )
            rogue = Path(result["package_directory"]) / "unlisted.bin"
            rogue.write_bytes(b"unlisted artifact")
            with self.assertRaisesRegex(
                ReleasePackageTransactionError,
                "inventory differs from its Manifest",
            ):
                verify_release_package(
                    fixture["release_dir"], expected_release_id="release-test-extra"
                )

    def test_source_snapshot_change_blocks_package_commit_before_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            workflow_path = fixture["workflow_path"]
            before = _sha(workflow_path)
            workflow_path.write_text(workflow_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            # Simulate a stale Manifest collector snapshot but provide a fresh
            # artifact record so the transaction-level expected-ledger guard is
            # the gate that detects the change.
            workflow_record = next(
                row for row in fixture["records"]
                if row["source_file"] == str(workflow_path.resolve())
            )
            workflow_record["sha256"] = _sha(workflow_path)
            workflow_record["bytes"] = workflow_path.stat().st_size
            with self.assertRaisesRegex(ReleasePackageTransactionError, "changed while packaging"):
                commit_release_package(
                    fixture["release_dir"],
                    fixture["records"],
                    release_id="release-test-race",
                    source_release_manifest_sha256=_sha(fixture["manifest_path"]),
                    source_snapshot_hashes={
                        str(workflow_path): before,
                        str(fixture["revision_path"]): _sha(fixture["revision_path"]),
                    },
                )
            self.assertFalse((fixture["release_dir"] / "release_package_commit.json").exists())


if __name__ == "__main__":
    unittest.main()

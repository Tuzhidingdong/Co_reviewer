from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

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
        batch_dir = runs / "batch-b001"
        documents_dir = batch_dir / "documents"
        documents_dir.mkdir(parents=True)

        initial = sources / "Manuscript.original.docx"
        marked = documents_dir / "Manuscript.marked.docx"
        working_clean = runs / "Manuscript.working-clean.docx"
        final_clean = runs / "Manuscript.final-clean.docx"
        response_docx = round_root / "Response_to_Reviewers.docx"
        for path, text in (
            (initial, "Original manuscript"),
            (marked, "Marked manuscript"),
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

        (batch_dir / "batch_intent.json").write_text('{"batch_id":"b001"}\n', encoding="utf-8")
        (batch_dir / "batch_scope.json").write_text('{"batch_id":"b001"}\n', encoding="utf-8")
        (batch_dir / "batch_published.json").write_text('{"batch_id":"b001"}\n', encoding="utf-8")

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
                    "batch_id": "b001",
                }
            },
            "marked_documents": {
                "Manuscript": {
                    "active_file": str(marked),
                    "active_sha256": _sha(marked),
                    "active_batch_id": "b001",
                    "working_clean_file": str(working_clean),
                    "working_clean_sha256": _sha(working_clean),
                }
            },
            "batches": [{
                "batch_id": "b001",
                "directory": str(batch_dir),
            }],
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
        return {
            "round_root": round_root,
            "release_dir": round_root / "release",
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
            categories = {
                category
                for item in manifest["artifacts"]
                for category in item["categories"]
            }
            self.assertTrue({"initial-source", "marked-active", "working-clean", "final-clean", "final-pdf", "source-ledger", "response-artifact", "batches-history"}.issubset(categories))

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

    def test_source_snapshot_change_blocks_package_commit_before_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(Path(temporary))
            workflow_path = fixture["workflow_path"]
            before = _sha(workflow_path)
            workflow_path.write_text(workflow_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
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

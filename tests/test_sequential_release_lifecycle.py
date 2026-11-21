from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import co_reviewer_cli as cli
from co_reviewer.batch_store import create_batch_intent, load_active_document_pointers
from co_reviewer.docx_quality import audit_docx_quality
from co_reviewer.release_package import verify_release_package
from co_reviewer.revision_models import (
    Patch,
    PatchOperation,
    ResponseLocation,
    ResponseUnit,
    RevisionDocument,
    RevisionLedger,
    _fingerprint,
)
from co_reviewer.workflow_engine import (
    build_workflow_ledger,
    invalidate_downstream,
    transition_task,
    validate_workflow_ledger,
)
from co_reviewer.workflow_preparation import _refresh_task_evidence


STAMP = "2026-11-21T12:00:00+00:00"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")



def _write_minimal_pdf(path: Path, text: str) -> None:
    """Write a deterministic one-page PDF fixture without optional PDF libraries."""
    safe_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
def _make_source_docx(path: Path, text: str) -> None:
    document = Document()
    document.add_paragraph(text)
    document.save(path)


def _make_marked_docx(source: Path, output: Path, revisions: list[tuple[str, str, str]]) -> None:
    """Create a small valid DOCX whose exact tracked-change IDs match the two Patch records."""
    with zipfile.ZipFile(source, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    root = etree.fromstring(members["word/document.xml"])
    paragraph = root.find(".//w:p", NS)
    if paragraph is None:
        raise AssertionError("Synthetic source DOCX has no paragraph.")
    for child in list(paragraph):
        if child.tag != f"{{{W_NS}}}pPr":
            paragraph.remove(child)

    prefix = etree.SubElement(paragraph, f"{{{W_NS}}}r")
    etree.SubElement(prefix, f"{{{W_NS}}}t").text = "Sample ID: "
    for revision_id, old_text, new_text in revisions:
        deleted = etree.SubElement(
            paragraph,
            f"{{{W_NS}}}del",
            {f"{{{W_NS}}}id": revision_id, f"{{{W_NS}}}author": "Synthetic Author", f"{{{W_NS}}}date": STAMP},
        )
        deleted_run = etree.SubElement(deleted, f"{{{W_NS}}}r")
        etree.SubElement(deleted_run, f"{{{W_NS}}}delText").text = old_text
        inserted = etree.SubElement(
            paragraph,
            f"{{{W_NS}}}ins",
            {f"{{{W_NS}}}id": str(int(revision_id) + 1000), f"{{{W_NS}}}author": "Synthetic Author", f"{{{W_NS}}}date": STAMP},
        )
        inserted_run = etree.SubElement(inserted, f"{{{W_NS}}}r")
        etree.SubElement(inserted_run, f"{{{W_NS}}}t").text = new_text
    suffix = etree.SubElement(paragraph, f"{{{W_NS}}}r")
    etree.SubElement(suffix, f"{{{W_NS}}}t").text = "."
    members["word/document.xml"] = etree.tostring(
        root, encoding="UTF-8", xml_declaration=True, standalone=True
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _complete_task(task: dict, task_id: str) -> dict:
    ready = transition_task(task, "ready")
    fingerprint = hashlib.sha256(f"completion:{task_id}".encode("utf-8")).hexdigest()
    return transition_task(
        ready,
        "completed",
        evidence={
            "completion_record_id": f"COMPLETE-{task_id}",
            "completion_record_fingerprint": fingerprint,
            "completed_at_utc": STAMP,
        },
    )


def _make_patch(
    *,
    patch_id: str,
    task_id: str,
    operation_id: str,
    comment_id: str,
    input_file: Path,
    output_file: Path,
    old_text: str,
    new_text: str,
    revision_id: str,
) -> Patch:
    input_sha = _sha(input_file)
    output_sha = _sha(output_file)
    operation = PatchOperation(
        operation_id=operation_id,
        document="Manuscript",
        input_file=str(input_file),
        output_file=str(output_file),
        part="word/document.xml",
        p_idx=0,
        find=old_text,
        replace=new_text,
        source_sha256=input_sha,
        source_comment_ids=(comment_id,),
        location="Manuscript body paragraph 1",
        authorized_change={
            "intent": "Apply the author-approved sample-identifier correction.",
            "explicitly_authorized_tokens": [{
                "kind": "sample_identifier",
                "text": old_text,
                "replacement": new_text,
            }],
        },
    )
    return Patch(
        patch_id=patch_id,
        task_id=task_id,
        reviewer="Reviewer 1",
        comment=f"Please update the sample identifier from {old_text} to {new_text}.",
        proposed_response=f"We updated the identifier to {new_text}.",
        operations=[operation],
        status="verified",
        preflight_fingerprint=hashlib.sha256(f"preflight:{patch_id}".encode()).hexdigest(),
        preflight_report_id=f"PREFLIGHT-{patch_id}",
        approval_fingerprint=hashlib.sha256(f"approval:{patch_id}".encode()).hexdigest(),
        apply_report_ids=[f"APPLY-{patch_id}"],
        applied_revision_ids=[{
            "input_file": str(input_file),
            "output_file": str(output_file),
            "input_sha256": input_sha,
            "output_sha256": output_sha,
            "revision_ids": [revision_id],
        }],
        verification_report_id=f"VERIFY-{patch_id}",
        verified_output_hashes=[{"path": str(output_file), "sha256": output_sha}],
    )


def _make_verified_response_unit(
    *, unit_id: str, task_id: str, patch_id: str, operation_id: str, comment_id: str
) -> ResponseUnit:
    return ResponseUnit(
        response_unit_id=unit_id,
        reviewer="Reviewer 1",
        comment_id=comment_id,
        comment_text=f"Please correct the identifier for {comment_id}.",
        response_text=f"We updated the sample identifier in the manuscript for {comment_id}.",
        linked_task_ids=[task_id],
        linked_patch_ids=[patch_id],
        linked_operation_ids=[operation_id],
        disposition="revised",
        location_requirement="manual_required",
        resolution_reason="The author approved the exact identifier correction.",
        status="verified",
        semantic_review_confirmed=True,
        verification_report_id=f"SEMANTIC-{unit_id}",
    )


def _save_revision_ledger(path: Path, ledger: RevisionLedger) -> None:
    _write_json(path, ledger.to_dict())


class SequentialReleaseLifecycleIntegrationTests(unittest.TestCase):
    """Join two real batch publications, downstream evidence refresh and Release Package creation."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "Manuscript.docx"
        self.reviewers = self.root / "Reviewers.docx"
        _make_source_docx(self.source, "Sample ID: ZB-01.")
        _make_source_docx(self.reviewers, "Reviewer comments C1 and C2.")
        self.workflow_path = self.root / "workflow_ledger.json"
        self.revision_path = self.root / "revision_ledger.json"
        self.runs_dir = self.root / "runs"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _publish(
        self,
        workflow: dict,
        *,
        task_id: str,
        patch: Patch,
        batch_id: str,
        ledger: RevisionLedger,
    ) -> tuple[dict, Path]:
        intent = create_batch_intent(
            workflow,
            self.workflow_path,
            [task_id],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id=batch_id,
        )
        batch_dir = Path(intent["batch_dir"])
        intent_path = batch_dir / "batch_intent.json"
        record = intent["documents"]["Manuscript"]
        scope = {
            "schema_version": "1.0",
            "batch_id": batch_id,
            "revision_round_id": workflow["revision_round_id"],
            "revision_ledger_file": str(self.revision_path),
            "selected_task_ids": [task_id],
            "batch_task_ids": [task_id],
            "batch_intent_sha256": _sha(intent_path),
            "patch_ids": [patch.patch_id],
            "documents": {
                "Manuscript": {
                    "input_file": record["parent_file"],
                    "input_sha256": record["parent_sha256"],
                    "output_file": record["output_file"],
                }
            },
        }
        _write_json(batch_dir / "batch_scope.json", scope)
        _save_revision_ledger(self.revision_path, ledger)
        args = SimpleNamespace(
            workflow_ledger=str(self.workflow_path),
            revision_ledger=str(self.revision_path),
            batch_dir=str(batch_dir),
            numbering_plan=None,
            numbering_approval=None,
        )
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_publish_batch(args), 0)
        updated = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        receipt_path = batch_dir / "batch_published.json"
        self.assertTrue(receipt_path.is_file())
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "published")
        self.assertEqual(receipt["batch_id"], batch_id)
        return updated, batch_dir

    def test_two_publications_refresh_downstream_source_then_create_ready_release_package(self) -> None:
        source_record = {"role": "Manuscript", "file": str(self.source), "sha256": _sha(self.source)}
        reviewer_record = {"role": "Reviewers", "file": str(self.reviewers), "sha256": _sha(self.reviewers)}
        tasks = []
        for index, comment_id in ((1, "C1"), (2, "C2")):
            tasks.append({
                "task_id": f"T{index}",
                "sequence": index,
                "task_type": "local_revision",
                "title": f"Apply the approved sample identifier correction {index}",
                "source_comment_ids": [comment_id],
                "target_documents": ["Manuscript"],
                "evidence": [
                    {
                        "evidence_id": f"E-{comment_id}",
                        "document": "Reviewers",
                        "part": "word/comments.xml",
                        "p_idx": 0,
                        "source_comment_id": comment_id,
                        "source_excerpt": f"Please correct the sample identifier for {comment_id}.",
                        "evidence_type": "reviewer_comment",
                        "match_status": "confirmed",
                    },
                    {
                        "evidence_id": f"E-MAN-{comment_id}",
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "source_excerpt": "Sample ID: ZB-01.",
                        "evidence_type": "paragraph",
                        "match_status": "confirmed",
                    },
                ],
                "proposed_action": f"Update the manuscript identifier for {comment_id}.",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "action": "replace",
                    "evidence_ids": [f"E-MAN-{comment_id}"],
                }],
                "dependencies": [] if index == 1 else ["T1"],
                "readiness": "ready_for_author_approval",
                "unresolved_questions": [],
                "confidence": 1.0,
                "status": "pending",
                "requires_user_approval": True,
            })
        workflow = build_workflow_ledger(
            {"source_files": [source_record, reviewer_record], "tasks": tasks},
            "round-two-publication-release",
            STAMP,
        )
        workflow["tasks"][0] = _complete_task(workflow["tasks"][0], "T1")
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # Publication 1: the first task changes the active Manuscript pointer.
        first_intent = create_batch_intent(
            workflow,
            self.workflow_path,
            ["T1"],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id="release-batch-001",
        )
        first_output = Path(first_intent["documents"]["Manuscript"]["output_file"])
        _make_marked_docx(self.source, first_output, [("100", "ZB-01", "ZB-02")])
        patch_one = _make_patch(
            patch_id="P1",
            task_id="T1",
            operation_id="OP1",
            comment_id="C1",
            input_file=self.source,
            output_file=first_output,
            old_text="ZB-01",
            new_text="ZB-02",
            revision_id="100",
        )
        unit_one = _make_verified_response_unit(
            unit_id="RU-C1", task_id="T1", patch_id="P1", operation_id="OP1", comment_id="C1"
        )
        ledger_one = RevisionLedger(
            revision_round_id=workflow["revision_round_id"],
            patches=[patch_one],
            response_units=[unit_one],
        )
        batch_one_dir = Path(first_intent["batch_dir"])
        first_record = first_intent["documents"]["Manuscript"]
        _write_json(batch_one_dir / "batch_scope.json", {
            "schema_version": "1.0",
            "batch_id": "release-batch-001",
            "revision_round_id": workflow["revision_round_id"],
            "revision_ledger_file": str(self.revision_path),
            "selected_task_ids": ["T1"],
            "batch_task_ids": ["T1"],
            "batch_intent_sha256": _sha(batch_one_dir / "batch_intent.json"),
            "patch_ids": ["P1"],
            "documents": {
                "Manuscript": {
                    "input_file": first_record["parent_file"],
                    "input_sha256": first_record["parent_sha256"],
                    "output_file": first_record["output_file"],
                }
            },
        })
        _save_revision_ledger(self.revision_path, ledger_one)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_publish_batch(SimpleNamespace(
                workflow_ledger=str(self.workflow_path),
                revision_ledger=str(self.revision_path),
                batch_dir=str(batch_one_dir),
                numbering_plan=None,
                numbering_approval=None,
            )), 0)
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        published_one = workflow["active_documents"]["Manuscript"]
        self.assertEqual(published_one["batch_id"], "release-batch-001")
        self.assertEqual(Path(published_one["file"]), first_output.resolve())

        # Invalidate and re-prepare T2 against the actual first publication.
        workflow["tasks"] = invalidate_downstream(
            workflow["tasks"],
            "T1",
            "The first publication changed the active Manuscript source for the dependent task.",
        )
        task_two = next(row for row in workflow["tasks"] if row["task_id"] == "T2")
        self.assertEqual(task_two["status"], "recheck_required")
        current_pointers = load_active_document_pointers(workflow)
        inventory_by_role = {
            "Manuscript": {
                "paragraphs": [{
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "text": "Sample ID: ZB-02.",
                }]
            },
            "Reviewers": {"comments": []},
        }
        refresh_spec = {
            "evidence": [{
                "evidence_id": "E-MAN-C2-REFRESH",
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
                "source_excerpt": "Sample ID: ZB-02.",
                "evidence_type": "paragraph",
                "match_status": "confirmed",
            }],
            "targets": [{
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
                "action": "replace",
                "evidence_ids": ["E-MAN-C2-REFRESH"],
            }],
        }
        if task_two.get("target_refs"):
            refresh_spec["target_refs"] = task_two["target_refs"]
        self.assertTrue(_refresh_task_evidence(
            task_two, refresh_spec, inventory_by_role, current_pointers
        ))
        task_two = transition_task(task_two, "ready")
        task_two = transition_task(
            task_two,
            "completed",
            evidence={
                "completion_record_id": "COMPLETE-T2-REFRESHED",
                "completion_record_fingerprint": hashlib.sha256(b"COMPLETE-T2-REFRESHED").hexdigest(),
                "completed_at_utc": STAMP,
            },
        )
        workflow["tasks"] = [
            task_two if row["task_id"] == "T2" else row for row in workflow["tasks"]
        ]
        self.workflow_path.write_text(
            json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.assertTrue(any(
            row["role"] == "Manuscript"
            and Path(row["file"]).resolve() == first_output.resolve()
            and row["sha256"] == _sha(first_output)
            for row in task_two["source_versions"]
        ))
        self.assertEqual(workflow["source_files"], [source_record, reviewer_record])

        # Publication 2 is prepared from batch 1's exact active parent and its
        # Patch/receipt is committed through the same CLI publication path.
        second_intent = create_batch_intent(
            workflow,
            self.workflow_path,
            ["T2"],
            ["Manuscript"],
            runs_dir=self.runs_dir,
            batch_id="release-batch-002",
        )
        second_output = Path(second_intent["documents"]["Manuscript"]["output_file"])
        _make_marked_docx(
            self.source,
            second_output,
            [("100", "ZB-01", "ZB-02"), ("101", "ZB-02", "ZB-03")],
        )
        patch_two = _make_patch(
            patch_id="P2",
            task_id="T2",
            operation_id="OP2",
            comment_id="C2",
            input_file=first_output,
            output_file=second_output,
            old_text="ZB-02",
            new_text="ZB-03",
            revision_id="101",
        )
        unit_two = _make_verified_response_unit(
            unit_id="RU-C2", task_id="T2", patch_id="P2", operation_id="OP2", comment_id="C2"
        )
        ledger_two = RevisionLedger(
            revision_round_id=workflow["revision_round_id"],
            patches=[patch_one, patch_two],
            response_units=[unit_one, unit_two],
        )
        second_batch_dir = Path(second_intent["batch_dir"])
        second_record = second_intent["documents"]["Manuscript"]
        _write_json(second_batch_dir / "batch_scope.json", {
            "schema_version": "1.0",
            "batch_id": "release-batch-002",
            "revision_round_id": workflow["revision_round_id"],
            "revision_ledger_file": str(self.revision_path),
            "selected_task_ids": ["T2"],
            "batch_task_ids": ["T2"],
            "batch_intent_sha256": _sha(second_batch_dir / "batch_intent.json"),
            "patch_ids": ["P2"],
            "documents": {
                "Manuscript": {
                    "input_file": second_record["parent_file"],
                    "input_sha256": second_record["parent_sha256"],
                    "output_file": second_record["output_file"],
                }
            },
        })
        _save_revision_ledger(self.revision_path, ledger_two)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_publish_batch(SimpleNamespace(
                workflow_ledger=str(self.workflow_path),
                revision_ledger=str(self.revision_path),
                batch_dir=str(second_batch_dir),
                numbering_plan=None,
                numbering_approval=None,
            )), 0)
        workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        self.assertEqual(workflow["active_documents"]["Manuscript"]["batch_id"], "release-batch-002")
        self.assertEqual(
            [row["batch_id"] for row in workflow["events"] if row.get("event_type") == "batch_published"],
            ["release-batch-001", "release-batch-002"],
        )
        self.assertEqual(len(workflow["source_files"]), 2)
        self.assertEqual(workflow["source_files"], [source_record, reviewer_record])

        # Create one hash-bound final Clean/PDF/quality/map set only after the
        # second publication, and bind both historical ResponseUnits to that
        # same final artifact pair. This test uses explicit manual locators;
        # it does not claim Word COM pagination acceptance.
        clean = self.root / "final" / "Manuscript.working_clean.docx"
        _make_source_docx(clean, "Sample ID: ZB-03.")
        pdf_path = self.root / "final" / "Manuscript.pdf"
        _write_minimal_pdf(pdf_path, "Sample ID: ZB-03.")
        clean_sha, pdf_sha = _sha(clean), _sha(pdf_path)
        quality_path = self.root / "final" / "Manuscript.quality.json"
        quality = audit_docx_quality(
            clean,
            profile="word-final-clean",
            pdf_path=pdf_path,
            word_page_count=1,
        )
        self.assertIn(quality.get("status"), {"PASS", "PASS_WITH_WARNINGS"}, quality)
        _write_json(quality_path, quality)
        quality_sha = _sha(quality_path)

        sequence_audit = {
            "status": "manual_review",
            "detected": {"Figure": [], "Table": []},
            "issues": [],
            "manual_review": [{
                "reason": "This release-lifecycle fixture uses explicit manual locators; Word COM pagination is a separate acceptance."
            }],
        }
        location_map_id = "manual-two-publication-map"
        map_fingerprint = hashlib.sha256(json.dumps({
            "location_map_id": location_map_id,
            "document": "Manuscript",
            "output_sha256": clean_sha,
            "pdf_sha256": pdf_sha,
            "response_unit_ids": ["RU-C1", "RU-C2"],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        location_map_path = self.root / "final" / "manual_location_map.json"
        location_map_payload = {
            "schema_version": "1.0",
            "location_map_id": location_map_id,
            "mapping_fingerprint": map_fingerprint,
            "generator": "co-reviewer-manual-lifecycle-test",
            "documents": [{
                "document": "Manuscript",
                "output_file": str(clean),
                "output_sha256": clean_sha,
                "pdf_file": str(pdf_path),
                "pdf_sha256": pdf_sha,
                "source_clean_sha256": clean_sha,
                "word_page_count": 1,
                "sequence_audit": sequence_audit,
                "quality_report_file": str(quality_path),
                "quality_report_sha256": quality_sha,
            }],
            "response_units": [
                {"response_unit_id": "RU-C1", "locator_ids": ["manual:OP1"]},
                {"response_unit_id": "RU-C2", "locator_ids": ["manual:OP2"]},
            ],
        }
        _write_json(location_map_path, location_map_payload)
        location_map_sha = _sha(location_map_path)

        def completed_unit(
            unit_id: str, task_id: str, patch_id: str, operation_id: str, comment_id: str
        ) -> ResponseUnit:
            location = ResponseLocation(
                document="Manuscript",
                output_file=str(clean),
                pdf_file=str(pdf_path),
                page=1,
                line_start=1,
                line_end=1,
                pdf_sha256=pdf_sha,
                location_map_id=location_map_id,
                mapping_fingerprint=map_fingerprint,
                output_sha256=clean_sha,
                locator_id=f"manual:{operation_id}",
                source_clean_sha256=clean_sha,
                mapping_method="manual",
                locator_text="Paragraph 1, identifier sentence",
            )
            return ResponseUnit(
                response_unit_id=unit_id,
                reviewer="Reviewer 1",
                comment_id=comment_id,
                comment_text=f"Please correct identifier {comment_id}.",
                response_text=f"We updated the sample identifier for {comment_id}.",
                linked_task_ids=[task_id],
                linked_patch_ids=[patch_id],
                linked_operation_ids=[operation_id],
                disposition="revised",
                location_requirement="manual_required",
                resolution_reason="The author approved the exact identifier correction.",
                status="completed",
                semantic_review_confirmed=True,
                verification_report_id=f"SEMANTIC-{unit_id}",
                location_map_fingerprint=map_fingerprint,
                location_map_file=str(location_map_path),
                location_map_file_sha256=location_map_sha,
                final_locations=[location],
                completed_at_utc=STAMP,
            )

        final_ledger = RevisionLedger(
            revision_round_id=workflow["revision_round_id"],
            documents=[RevisionDocument(
                document_id="DOC-MANUSCRIPT",
                revision_id="REV-MANUSCRIPT",
                role="Manuscript",
                input_file=str(self.source),
                output_file=str(second_output),
                source_sha256=_sha(self.source),
                output_sha256=_sha(second_output),
                working_clean_file=str(clean),
                working_clean_sha256=clean_sha,
                working_view_fingerprint=_fingerprint({
                    "source_sha256": _sha(second_output),
                    "working_clean_sha256": clean_sha,
                    "policy": "accept-insertions-reject-deletions-v1",
                }),
            )],
            patches=[patch_one, patch_two],
            response_units=[
                completed_unit("RU-C1", "T1", "P1", "OP1", "C1"),
                completed_unit("RU-C2", "T2", "P2", "OP2", "C2"),
            ],
        )
        _save_revision_ledger(self.revision_path, final_ledger)
        self.assertEqual(validate_workflow_ledger(workflow), [])
        self.assertEqual(
            [row["file"] for row in workflow["tasks"][1]["source_versions"] if row["role"] == "Manuscript"],
            [str(self.source.resolve()), str(first_output.resolve())],
        )

        release_dir = self.root / "runs" / "release-two-publications"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.cmd_create_release_package(SimpleNamespace(
                workflow_ledger=str(self.workflow_path),
                revision_ledger=str(self.revision_path),
                out_dir=str(release_dir),
                manuscript_title="Synthetic two-publication lifecycle fixture",
                author="Synthetic Author",
            )), 0)

        manifest = json.loads((release_dir / "final_release_manifest.json").read_text(encoding="utf-8"))
        receipt = json.loads((release_dir / "release_package_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["release_status"], "READY")
        self.assertEqual(
            [row["batch_id"] for row in manifest["batches"]],
            ["release-batch-001", "release-batch-002"],
        )
        self.assertEqual(receipt["status"], "complete")
        self.assertTrue(Path(receipt["package_manifest_file"]).is_file())
        self.assertTrue(Path(receipt["commit_marker_file"]).is_file())
        package = verify_release_package(release_dir, expected_release_id=receipt["release_id"])
        self.assertGreater(package["artifact_count"], 0)
        self.assertEqual(
            workflow["source_files"],
            [source_record, reviewer_record],
            "Historical published versions must not be appended to or duplicated in initial source_files.",
        )
        self.assertTrue(any(
            row["role"] == "Manuscript"
            and Path(row["file"]).resolve() == first_output.resolve()
            for row in workflow["tasks"][1]["source_versions"]
        ))


if __name__ == "__main__":
    unittest.main()

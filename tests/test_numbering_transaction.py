from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from co_reviewer.revision_models import PatchOperation, RevisionModelError, validate_author_decision_ref
from co_reviewer.numbering_transaction import (
    NumberingTransactionError,
    _existing_numbering_package_receipt,
    authorize_numbering_impact_plan,
    build_numbering_impact_plan,
    commit_numbering_package_transaction,
    validate_numbering_impact_approval,
    validate_numbering_patch_operation_coverage,
    verify_numbering_package_outputs,
)
from co_reviewer.v232_contract import (
    ContractError,
    build_package_reference_graph,
    commit_document_package,
    extract_docx_reference_segments,
    renumber_reference_graph,
)


class NumberingTransactionTests(unittest.TestCase):
    def _document(self, path: Path, paragraphs: list[str]) -> None:
        document = Document()
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        document.save(path)

    def _source_fixture(self, root: Path):
        manuscript = root / "Manuscript.docx"
        si = root / "SI.docx"
        self._document(manuscript, [
            "Figure 1. Main manuscript result.",
            "The trend is shown in Fig. 1.",
        ])
        self._document(si, [
            "Figure S2. Supplementary result.",
            "See Figure 1 for the corresponding manuscript result.",
        ])
        documents = {
            "Manuscript": extract_docx_reference_segments(manuscript),
            "SI": extract_docx_reference_segments(si),
        }
        graph = build_package_reference_graph(documents)
        return graph, {"Manuscript": manuscript, "SI": si}

    def _impact_plan(self, root: Path):
        graph, sources = self._source_fixture(root)
        plan = build_numbering_impact_plan(
            graph,
            {"Manuscript::figure::1": "2"},
            sources,
        )
        return plan, sources

    def test_impact_plan_is_complete_non_authorizing_and_hash_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            self.assertEqual(plan["status"], "awaiting_authorization")
            self.assertFalse(plan["numbering_mutation_authorized"])
            self.assertEqual(plan["affected_document_roles"], ["Manuscript", "SI"])
            self.assertEqual(plan["change_counts"]["definitions"], 1)
            self.assertEqual(plan["change_counts"]["mentions"], 2)
            self.assertEqual(
                plan["impact_plan_sha256"],
                __import__("hashlib").sha256(
                    __import__("json").dumps(
                        {key: value for key, value in plan.items() if key != "impact_plan_sha256"},
                        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            )
            self.assertTrue(all(path.is_file() for path in sources.values()))
            self.assertFalse((root / "Manuscript.docx").read_bytes() == b"")

    def test_authorization_requires_exact_impact_plan_sha_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, _ = self._impact_plan(root)
            with self.assertRaisesRegex(NumberingTransactionError, "displayed impact plan"):
                authorize_numbering_impact_plan(plan, "0" * 64, "Human Author")
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author",
                approved_at_utc="2026-11-20T12:00:00+00:00",
            )
            validated = validate_numbering_impact_approval(
                plan, approval, expected_plan_sha256=plan["impact_plan_sha256"]
            )
            self.assertEqual(validated["approved_by"], "Human Author")
            tampered = dict(plan)
            tampered["mention_updates"] = []
            with self.assertRaisesRegex(NumberingTransactionError, "hash"):
                validate_numbering_impact_approval(tampered, approval)

    def test_post_publication_graph_must_match_all_documents_and_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)

            manuscript_output = root / "Manuscript.marked.docx"
            si_output = root / "SI.marked.docx"
            manuscript = Document(sources["Manuscript"])
            for paragraph in manuscript.paragraphs:
                for run in paragraph.runs:
                    run.text = run.text.replace("Figure 1", "Figure 2").replace("Fig. 1", "Fig. 2")
            manuscript.save(manuscript_output)

            si = Document(sources["SI"])
            for paragraph in si.paragraphs:
                for run in paragraph.runs:
                    run.text = run.text.replace("Figure 1", "Figure 2")
            si.save(si_output)

            verification = verify_numbering_package_outputs(
                plan,
                {"Manuscript": manuscript_output, "SI": si_output},
            )
            self.assertEqual(verification["status"], "PASS")
            self.assertEqual(verification["graph_counts"]["unresolved"], 0)
            self.assertEqual(verification["graph_counts"]["ambiguous"], 0)
            self.assertEqual(set(verification["output_documents"]), {"Manuscript", "SI"})

    def test_post_publication_graph_rejects_partial_propagation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            manuscript_output = root / "Manuscript.marked.docx"
            si_output = root / "SI.marked.docx"

            manuscript = Document(sources["Manuscript"])
            for paragraph in manuscript.paragraphs:
                for run in paragraph.runs:
                    run.text = run.text.replace("Figure 1", "Figure 2").replace("Fig. 1", "Fig. 2")
            manuscript.save(manuscript_output)
            # Deliberately leave the SI cross-document mention unchanged.
            Document(sources["SI"]).save(si_output)

            with self.assertRaisesRegex(NumberingTransactionError, "Post-publication Reference Graph mismatch"):
                verify_numbering_package_outputs(
                    plan,
                    {"Manuscript": manuscript_output, "SI": si_output},
                )

    def test_verification_recomputes_proposal_from_approved_source_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author"
            )
            manuscript_output = root / "Manuscript.marked.docx"
            si_output = root / "SI.marked.docx"
            manuscript = Document(sources["Manuscript"])
            for paragraph in manuscript.paragraphs:
                for run in paragraph.runs:
                    run.text = run.text.replace("Figure 1", "Figure 2").replace("Fig. 1", "Fig. 2")
            manuscript.save(manuscript_output)
            si = Document(sources["SI"])
            for paragraph in si.paragraphs:
                for run in paragraph.runs:
                    run.text = run.text.replace("Figure 1", "Figure 2")
            si.save(si_output)

            forged = __import__("copy").deepcopy(plan)
            forged["proposed_graph"]["definitions"][0]["number"] = "999"
            unsigned = {key: value for key, value in forged.items() if key != "impact_plan_sha256"}
            forged["impact_plan_sha256"] = __import__("hashlib").sha256(
                __import__("json").dumps(
                    unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            forged_approval = authorize_numbering_impact_plan(
                forged, forged["impact_plan_sha256"], "Human Author"
            )
            validate_numbering_impact_approval(forged, forged_approval)
            with self.assertRaisesRegex(NumberingTransactionError, "not the deterministic result"):
                verify_numbering_package_outputs(
                    forged,
                    {"Manuscript": manuscript_output, "SI": si_output},
                )

            stale_root = root / "stale"
            stale_root.mkdir()
            stale_plan, stale_sources = self._impact_plan(stale_root)
            stale_approval = authorize_numbering_impact_plan(
                stale_plan, stale_plan["impact_plan_sha256"], "Human Author"
            )
            stale = Document(stale_sources["Manuscript"])
            stale.add_paragraph("Changed after plan approval.")
            stale.save(stale_sources["Manuscript"])
            with self.assertRaisesRegex(NumberingTransactionError, "approved SHA-256"):
                verify_numbering_package_outputs(
                    stale_plan,
                    {"Manuscript": stale_sources["Manuscript"], "SI": stale_sources["SI"]},
                )

    def test_global_numbering_patch_operations_cover_exactly_changed_paragraphs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, _ = self._impact_plan(root)
            required = set()
            for field in ("definition_updates", "mention_updates"):
                for row in plan[field]:
                    required.add((row["document"], row["part"], row["paragraph_index"]))
            operations = []
            for index, (role, part, p_idx) in enumerate(sorted(required), start=1):
                operations.append({
                    "operation_id": f"GN-OP-{index:03d}",
                    "document": role,
                    "part": part,
                    "p_idx": p_idx,
                    "target_identity_mode": "legacy_p_idx_explicit",
                })
            task = {"task_id": "GN1", "patch_operations": operations}
            report = validate_numbering_patch_operation_coverage(plan, [task])
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["target_paragraph_count"], len(required))

            missing = {"task_id": "GN1", "patch_operations": operations[:-1]}
            with self.assertRaisesRegex(NumberingTransactionError, "Missing="):
                validate_numbering_patch_operation_coverage(plan, [missing])

            extra = {"task_id": "GN1", "patch_operations": operations + [{
                "operation_id": "GN-EXTRA",
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 99,
            }]}
            with self.assertRaisesRegex(NumberingTransactionError, "unexpected="):
                validate_numbering_patch_operation_coverage(plan, [extra])

    def test_author_decision_can_be_patch_provenance_without_fabricating_comment_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author",
                approved_at_utc="2026-11-20T12:00:00+00:00",
            )
            self.assertEqual(validate_author_decision_ref(approval), approval)
            operation = PatchOperation.from_mapping({
                "operation_id": "GN-OP-001",
                "document": "Manuscript",
                "input_file": str(sources["Manuscript"]),
                "output_file": str(root / "Manuscript.numbered.docx"),
                "part": "word/document.xml",
                "p_idx": 0,
                "find": "Figure 1",
                "replace": "Figure 2",
                "source_comment_ids": [],
                "author_decision_ref": approval,
                "target_identity_mode": "legacy_p_idx_explicit",
            })
            self.assertEqual(operation.source_comment_ids, ())
            self.assertEqual(operation.author_decision_ref, approval)
            self.assertEqual(operation.to_legacy_target()["source_comment_ids"], [])
            self.assertEqual(operation.to_legacy_target()["author_decision_ref"], approval)
            self.assertEqual(operation.to_dict()["author_decision_ref"], approval)

            tampered_approval = dict(approval)
            tampered_approval["approved_by"] = "Someone Else"
            with self.assertRaisesRegex(RevisionModelError, "approval SHA-256"):
                PatchOperation.from_mapping({
                    "operation_id": "GN-OP-002",
                    "document": "Manuscript",
                    "input_file": str(sources["Manuscript"]),
                    "output_file": str(root / "Manuscript.bad.docx"),
                    "part": "word/document.xml",
                    "find": "Figure 1",
                    "replace": "Figure 2",
                    "source_comment_ids": [],
                    "author_decision_ref": tampered_approval,
                    "target_identity_mode": "legacy_p_idx_explicit",
                })

            with self.assertRaisesRegex(RevisionModelError, "source_comment_ids or a hash-bound author_decision_ref"):
                PatchOperation.from_mapping({
                    "operation_id": "GN-OP-003",
                    "document": "Manuscript",
                    "input_file": str(sources["Manuscript"]),
                    "output_file": str(root / "Manuscript.no-provenance.docx"),
                    "part": "word/document.xml",
                    "find": "Figure 1",
                    "replace": "Figure 2",
                    "source_comment_ids": [],
                    "target_identity_mode": "legacy_p_idx_explicit",
                })

    def test_numbering_package_commit_validates_full_batch_and_resume_integrity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author",
                approved_at_utc="2026-11-20T12:00:00+00:00",
            )

            batch_id = "numbering-commit-001"
            revision_round_id = "round-numbering-commit"
            batch_dir = root / f"batch-{batch_id}"
            documents_dir = batch_dir / "documents"
            documents_dir.mkdir(parents=True)
            output_documents = {}
            for role, source in sources.items():
                output_path = documents_dir / f"{role}.marked.docx"
                document = Document(source)
                for paragraph in document.paragraphs:
                    paragraph.text = (
                        paragraph.text
                        .replace("Figure 1", "Figure 2")
                        .replace("Fig. 1", "Fig. 2")
                    )
                document.save(output_path)
                output_documents[role] = output_path

            required_targets = {
                (row["document"], row["part"], row["paragraph_index"])
                for field in ("definition_updates", "mention_updates")
                for row in plan[field]
            }
            operations = [
                {
                    "operation_id": f"GN-COMMIT-{index:03d}",
                    "document": role,
                    "part": part,
                    "p_idx": p_idx,
                    "author_decision_ref": approval,
                    "target_identity_mode": "legacy_p_idx_explicit",
                }
                for index, (role, part, p_idx) in enumerate(sorted(required_targets), start=1)
            ]
            workflow_task = {
                "task_id": "GN-COMMIT-001",
                "task_type": "global_numbering",
                "status": "verified",
                "author_decision_ref": approval,
                "patch_operations": operations,
            }
            workflow_path = root / "workflow_ledger.json"
            revision_path = root / "revision_ledger.json"
            revision_path.write_text('{"schema_version":"0.5.1"}\n', encoding="utf-8")
            patch_id = "PATCH-GN-COMMIT-001"
            revision_ledger = SimpleNamespace(
                patches=[SimpleNamespace(patch_id=patch_id, status="verified")]
            )

            intent_documents = {}
            scope_documents = {}
            pointers = {}
            for role, source in sources.items():
                output_path = output_documents[role]
                source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
                output_sha = hashlib.sha256(output_path.read_bytes()).hexdigest()
                intent_documents[role] = {
                    "parent_file": str(source.resolve()),
                    "parent_sha256": source_sha,
                    "parent_batch_id": "initial",
                    "output_file": str(output_path.resolve()),
                }
                scope_documents[role] = {
                    "input_file": str(source.resolve()),
                    "input_sha256": source_sha,
                    "output_file": str(output_path.resolve()),
                }
                pointers[role] = {
                    "file": str(output_path.resolve()),
                    "sha256": output_sha,
                    "batch_id": batch_id,
                    "updated_at_utc": "2026-11-20T12:30:00+00:00",
                }

            intent = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "revision_round_id": revision_round_id,
                "workflow_ledger_file": str(workflow_path.resolve()),
                "selected_task_ids": ["GN-COMMIT-001"],
                "all_selected_task_ids": ["GN-COMMIT-001"],
                "documents": intent_documents,
            }
            intent_path = batch_dir / "batch_intent.json"
            intent_path.write_text(json.dumps(intent, ensure_ascii=False, indent=2), encoding="utf-8")
            intent_sha = hashlib.sha256(intent_path.read_bytes()).hexdigest()
            scope = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "revision_round_id": revision_round_id,
                "revision_ledger_file": str(revision_path.resolve()),
                "selected_task_ids": ["GN-COMMIT-001"],
                "batch_task_ids": ["GN-COMMIT-001"],
                "patch_ids": [patch_id],
                "batch_intent_sha256": intent_sha,
                "documents": scope_documents,
            }
            scope_path = batch_dir / "batch_scope.json"
            scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=2), encoding="utf-8")
            scope_sha = hashlib.sha256(scope_path.read_bytes()).hexdigest()
            revision_sha = hashlib.sha256(revision_path.read_bytes()).hexdigest()

            workflow = {
                "schema_version": "0.5.1",
                "revision_round_id": revision_round_id,
                "active_documents": pointers,
                "tasks": [workflow_task],
                "events": [{
                    "event_id": "EV-GN-PUBLISHED",
                    "task_id": "GN-COMMIT-001",
                    "timestamp_utc": "2026-11-20T12:30:00+00:00",
                    "event_type": "batch_published",
                    "batch_id": batch_id,
                    "documents": pointers,
                }],
            }
            workflow_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
            publication_intent = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "revision_ledger_sha256": revision_sha,
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
                "active_documents_to_publish": pointers,
            }
            (batch_dir / "batch_publish_intent.json").write_text(
                json.dumps(publication_intent, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            publication_receipt = {
                "schema_version": "1.0",
                "batch_id": batch_id,
                "status": "published",
                "workflow_ledger_file": str(workflow_path.resolve()),
                "revision_ledger_file": str(revision_path.resolve()),
                "revision_ledger_sha256": revision_sha,
                "batch_intent_sha256": intent_sha,
                "batch_scope_sha256": scope_sha,
                "selected_task_ids": ["GN-COMMIT-001"],
                "active_documents": pointers,
            }
            (batch_dir / "batch_published.json").write_text(
                json.dumps(publication_receipt, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            with mock.patch("co_reviewer.workflow_engine.validate_workflow_ledger", return_value=[]):
                receipt = commit_numbering_package_transaction(
                    plan,
                    approval,
                    batch_dir=batch_dir,
                    workflow_ledger_path=workflow_path,
                    revision_ledger_path=revision_path,
                    revision_ledger=revision_ledger,
                    release_root=root / "numbering-releases",
                )

            self.assertEqual(receipt["status"], "COMMITTED")
            self.assertEqual(receipt["batch_id"], batch_id)
            self.assertTrue(Path(receipt["package_manifest"]).is_file())
            self.assertTrue(Path(receipt["commit_marker"]).is_file())
            self.assertEqual(len(receipt["output_documents"]), 2)

            packaged_docx = (
                Path(receipt["package_directory"])
                / "Manuscript"
                / "Manuscript.marked.docx"
            )
            packaged_docx.write_bytes(packaged_docx.read_bytes() + b"tampered")
            with mock.patch("co_reviewer.workflow_engine.validate_workflow_ledger", return_value=[]):
                with self.assertRaisesRegex(NumberingTransactionError, "integrity validation"):
                    commit_numbering_package_transaction(
                        plan,
                        approval,
                        batch_dir=batch_dir,
                        workflow_ledger_path=workflow_path,
                        revision_ledger_path=revision_path,
                        revision_ledger=revision_ledger,
                        release_root=root / "numbering-releases",
                    )

    def test_existing_numbering_package_receipt_rejects_tampered_docx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, sources = self._impact_plan(root)
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author",
                approved_at_utc="2026-11-20T12:00:00+00:00",
            )
            package_root = root / "numbering-transaction" / "package"
            result = commit_document_package(
                {"Manuscript/Manuscript.docx": sources["Manuscript"].read_bytes()},
                package_root,
                metadata={"numbering_transaction": {
                    "impact_plan_sha256": plan["impact_plan_sha256"],
                    "approval_sha256": approval["approval_sha256"],
                    "batch_id": "batch-numbering-001",
                }},
            )
            prior = _existing_numbering_package_receipt(
                package_root,
                plan_sha=plan["impact_plan_sha256"],
                approval_sha=approval["approval_sha256"],
                batch_id="batch-numbering-001",
            )
            self.assertEqual(prior["package_id"], result["package_id"])

            packaged_docx = Path(result["directory"]) / "Manuscript" / "Manuscript.docx"
            packaged_docx.write_bytes(packaged_docx.read_bytes() + b"tampered")
            with self.assertRaisesRegex(NumberingTransactionError, "integrity validation"):
                _existing_numbering_package_receipt(
                    package_root,
                    plan_sha=plan["impact_plan_sha256"],
                    approval_sha=approval["approval_sha256"],
                    batch_id="batch-numbering-001",
                )

    def test_reference_graph_covers_static_object_kinds_and_numeric_citations(self):
        paragraphs = [
            "Figure 1. Workflow overview.",
            "Table 1. Summary of conditions.",
            "Equation 2. Governing relation.",
            "Scheme S1. Reaction route.",
            "Section 2.1. Experimental methods.",
            "[1] Example reference record.",
            "As shown in Figure 1 and Table 1, Eq. 2, Scheme S1, Section 2.1 and citation [1] are relevant.",
        ]
        segments = [
            {
                "part": "word/document.xml",
                "paragraph_index": index,
                "text": value,
                "reference_mechanism": "static_text",
                "word_field_instructions": [],
                "word_field_markers_present": False,
            }
            for index, value in enumerate(paragraphs)
        ]
        graph = build_package_reference_graph({"Manuscript": segments})
        definitions = {
            (row["kind"], str(row["number"]))
            for row in graph["definitions"]
        }
        self.assertEqual(definitions, {
            ("figure", "1"),
            ("table", "1"),
            ("equation", "2"),
            ("scheme", "S1"),
            ("section", "2.1"),
            ("citation", "1"),
        })
        self.assertTrue(graph["mentions"])
        self.assertTrue(all(row["resolution"] == "resolved" for row in graph["mentions"]))
        self.assertEqual(len(graph["edges"]), len(graph["mentions"]))
        self.assertEqual(graph["unresolved_count"], 0)
        self.assertEqual(graph["ambiguous_count"], 0)

        updated = renumber_reference_graph(
            graph,
            {
                "Manuscript::figure::1": "2",
                "Manuscript::table::1": "2",
                "Manuscript::equation::2": "3",
                "Manuscript::scheme::S1": "S2",
                "Manuscript::section::2.1": "2.2",
                "Manuscript::citation::1": "2",
            },
            explicit_authorization=True,
        )
        self.assertEqual(len(updated["definition_updates"]), 6)
        self.assertEqual(updated["unresolved_count"], 0)
        self.assertEqual(
            {(row["kind"], str(row["number"])) for row in updated["definitions"]},
            {
                ("figure", "2"),
                ("table", "2"),
                ("equation", "3"),
                ("scheme", "S2"),
                ("section", "2.2"),
                ("citation", "2"),
            },
        )

    def test_reference_graph_exposes_ambiguous_unresolved_ranges_and_native_fields(self):
        documents = {
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "Figure 1. Main figure."},
                {"part": "word/document.xml", "paragraph_index": 1, "text": "[1] Example citation."},
            ],
            "SI": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "Figure 1. Supplementary figure."},
            ],
            "Response": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "See Figure 1 and Figure 99."},
                {"part": "word/document.xml", "paragraph_index": 1, "text": "The cited sources are [1-3]."},
            ],
            "FieldHost": [
                {
                    "part": "word/document.xml",
                    "paragraph_index": 0,
                    "text": "Figure 1. Field-generated caption.",
                    "reference_mechanism": "mixed_with_word_field",
                    "word_field_instructions": ["SEQ Figure \\* ARABIC"],
                    "word_field_markers_present": True,
                },
                {
                    "part": "word/document.xml",
                    "paragraph_index": 1,
                    "text": "See Figure 1.",
                    "reference_mechanism": "mixed_with_word_field",
                    "word_field_instructions": ["REF _Ref123"],
                    "word_field_markers_present": True,
                },
            ],
        }
        graph = build_package_reference_graph(documents)
        response_mentions = [
            row for row in graph["mentions"] if row["document"] == "Response"
        ]
        ambiguous = next(row for row in response_mentions if row["number"] == "1" and row["kind"] == "figure")
        unresolved = next(row for row in response_mentions if row["number"] == "99")
        citation_range = next(row for row in response_mentions if row["kind"] == "citation")
        self.assertEqual(ambiguous["resolution"], "ambiguous")
        self.assertEqual(len(ambiguous["candidate_definition_ids"]), 2)
        self.assertEqual(unresolved["resolution"], "unresolved")
        self.assertEqual(citation_range["resolution"], "manual_review_required")
        self.assertEqual(graph["ambiguous_count"], 1)
        self.assertGreaterEqual(graph["unresolved_count"], 3)

        self.assertEqual(len(graph["field_references"]), 2)
        self.assertEqual(
            {row["field_type"] for row in graph["field_references"]},
            {"SEQ", "REF"},
        )
        self.assertTrue(all(
            row["resolution"] == "manual_review_required"
            for row in graph["field_references"]
        ))
        self.assertGreaterEqual(graph["manual_field_review_count"], 3)
        with self.assertRaisesRegex(
            ContractError,
            "NATIVE_WORD_FIELD_REFERENCE_REQUIRES_MANUAL_MAPPING",
        ):
            renumber_reference_graph(
                graph,
                {"Manuscript::figure::1": "2"},
                explicit_authorization=True,
            )

    def test_impact_plan_rejects_stale_graph_source_and_omitted_roles(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph, sources = self._source_fixture(root)
            stale = Document(sources["Manuscript"])
            stale.add_paragraph("An unrelated appended line.")
            stale.save(sources["Manuscript"])
            with self.assertRaisesRegex(NumberingTransactionError, "exact source DOCX snapshots"):
                build_numbering_impact_plan(
                    graph, {"Manuscript::figure::1": "2"}, sources
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            graph, sources = self._source_fixture(root)
            with self.assertRaisesRegex(NumberingTransactionError, "omissions and extra documents"):
                build_numbering_impact_plan(
                    graph, {"Manuscript::figure::1": "2"},
                    {"Manuscript": sources["Manuscript"]},
                )


if __name__ == "__main__":
    unittest.main()

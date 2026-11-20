from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from co_reviewer.numbering_transaction import (
    NumberingTransactionError,
    authorize_numbering_impact_plan,
    build_numbering_impact_plan,
    validate_numbering_impact_approval,
    verify_numbering_package_outputs,
)
from co_reviewer.v232_contract import build_package_reference_graph, extract_docx_reference_segments


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
            approval = authorize_numbering_impact_plan(
                plan, plan["impact_plan_sha256"], "Human Author"
            )
            validate_numbering_impact_approval(plan, approval)

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

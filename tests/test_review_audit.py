from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from argparse import Namespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli
from review_audit import (
    audit_figure_table_numbering,
    build_decision_queue,
    build_review_audit,
    extract_comment_ranges,
    load_cached_inspection,
)


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def q(local: str) -> str:
    return f"{{{W}}}{local}"


def write_docx(path: Path, paragraphs, comments=None) -> Path:
    document = ET.Element(q("document"))
    body = ET.SubElement(document, q("body"))
    for paragraph_spec in paragraphs:
        paragraph = ET.SubElement(body, q("p"))
        for item in paragraph_spec:
            kind = item[0]
            value = item[1] if len(item) > 1 else ""
            if kind == "start":
                ET.SubElement(paragraph, q("commentRangeStart"), {q("id"): str(value)})
            elif kind == "end":
                ET.SubElement(paragraph, q("commentRangeEnd"), {q("id"): str(value)})
            elif kind == "reference":
                run = ET.SubElement(paragraph, q("r"))
                ET.SubElement(run, q("commentReference"), {q("id"): str(value)})
            else:
                run = ET.SubElement(paragraph, q("r"))
                node = ET.SubElement(run, q("t"))
                node.text = str(value)
    ET.SubElement(body, q("sectPr"))

    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(document, encoding="utf-8", xml_declaration=True),
    }
    if comments is not None:
        comment_root = ET.Element(q("comments"))
        for comment in comments:
            node = ET.SubElement(comment_root, q("comment"), {
                q("id"): str(comment["id"]),
                q("author"): comment.get("author", "Reviewer"),
                q("date"): comment.get("date", "2026-11-13T00:00:00Z"),
            })
            para = ET.SubElement(node, q("p"))
            run = ET.SubElement(para, q("r"))
            text = ET.SubElement(run, q("t"))
            text.text = comment["text"]
        members["word/comments.xml"] = ET.tostring(comment_root, encoding="utf-8", xml_declaration=True)

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return path


class ReviewAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_native_comment_anchor_recovers_selected_text_across_paragraphs(self):
        source = self.root / "Reply.docx"
        write_docx(
            source,
            [
                [("text", "The selected phrase is "), ("start", "7"), ("text", "alpha")],
                [("text", "beta"), ("end", "7"), ("text", " and then the sentence continues.")],
            ],
            [{"id": "7", "text": "Please clarify this point."}],
        )
        report = cli.inspect_docx(str(source))
        self.assertEqual(len(report["comments"]), 1)
        comment = report["comments"][0]
        self.assertEqual(comment["anchor_status"], "range_resolved")
        self.assertEqual(comment["anchor_text"], "alpha\nbeta")
        self.assertEqual(len(comment["anchor_selections"]), 1)
        self.assertEqual(comment["anchor_selections"][0]["text"], "alpha\nbeta")
        self.assertEqual(comment["anchor_paragraphs"], [0, 1])
        self.assertEqual(comment["part"], "word/document.xml")
        self.assertEqual(comment["p_idx"], 0)

    def test_comment_reference_without_range_is_not_misreported_as_selected_text(self):
        source = self.root / "Reply.docx"
        write_docx(
            source,
            [[("text", "A whole paragraph with only a point comment."), ("reference", "3")]],
            [{"id": "3", "text": "What does this refer to?"}],
        )
        report = cli.inspect_docx(str(source))
        comment = report["comments"][0]
        self.assertEqual(comment["anchor_status"], "reference_only")
        self.assertEqual(comment["anchor_text"], "")
        self.assertIn("whole paragraph", comment["anchor_paragraph"])

    def test_comment_anchor_matches_one_target_document_exactly(self):
        reply = self.root / "Reply.docx"
        manuscript = self.root / "Manuscript.docx"
        si = self.root / "SI.docx"
        anchor = "Table S5. Comprehensive DFT computational parameters"
        write_docx(
            reply,
            [[("text", "Please revise "), ("start", "1"), ("text", anchor), ("end", "1"), ("text", ".")]],
            [{"id": "1", "text": "Please align the table numbering."}],
        )
        write_docx(manuscript, [[("text", "The SI contains Table S5 with the computational details.")]])
        write_docx(si, [[("text", anchor)]])
        reports = []
        for role, path in [("Reviewers", reply), ("Manuscript", manuscript), ("SI", si)]:
            data = cli.inspect_docx(str(path))
            data["role"] = role
            reports.append(data)

        audit = build_review_audit(reports)
        self.assertEqual(audit["comment_count"], 1)
        result = audit["comment_matches"][0]
        self.assertEqual(result["target_status"], "exact_unique")
        self.assertEqual(result["confirmed_matches"][0]["document"], "SI")
        self.assertEqual(result["confirmed_matches"][0]["p_idx"], 0)

    def test_same_anchor_in_two_targets_is_ambiguous_not_confirmed(self):
        reply = self.root / "Reply.docx"
        manuscript = self.root / "Manuscript.docx"
        si = self.root / "SI.docx"
        anchor = "Figure S7. Atomic coordination around the vacancy"
        write_docx(
            reply,
            [[("start", "2"), ("text", anchor), ("end", "2")]],
            [{"id": "2", "text": "Please clarify the figure."}],
        )
        write_docx(manuscript, [[("text", anchor)]])
        write_docx(si, [[("text", anchor)]])
        reports = []
        for role, path in [("Reviewers", reply), ("Manuscript", manuscript), ("SI", si)]:
            data = cli.inspect_docx(str(path))
            data["role"] = role
            reports.append(data)
        result = build_review_audit(reports)["comment_matches"][0]
        self.assertEqual(result["target_status"], "ambiguous_exact_match")
        self.assertEqual({item["document"] for item in result["confirmed_matches"]}, {"Manuscript", "SI"})

    def test_unresolved_native_anchor_uses_paragraph_context_for_candidates_only(self):
        reply = self.root / "Reply.docx"
        manuscript = self.root / "Manuscript.docx"
        si = self.root / "SI.docx"
        source_context = (
            "Atomic Coordination Analysis from Figures S5-S7. "
            "At the oxygen vacancy site, uranium coordinates with oxygen atoms."
        )
        write_docx(
            reply,
            [[("text", source_context), ("reference", "9")]],
            [{"id": "9", "text": "Should this atomic coordination analysis be added to the main text?"}],
        )
        write_docx(
            manuscript,
            [[("text", "The oxygen vacancy site modifies uranium coordination, as supported by the atomic coordination analysis in Figures S5-S7.")]],
        )
        write_docx(si, [[("text", "Figure S5. Coordination around the oxygen vacancy.")]])
        reports = []
        for role, path in [("Reviewers", reply), ("Manuscript", manuscript), ("SI", si)]:
            data = cli.inspect_docx(str(path))
            data["role"] = role
            reports.append(data)

        result = build_review_audit(reports)["comment_matches"][0]
        self.assertEqual(result["target_status"], "anchor_unresolved")
        self.assertEqual(result["confirmed_matches"], [])
        self.assertTrue(result["candidate_matches"])
        self.assertTrue(
            any(row["document"] == "Manuscript" for row in result["candidate_matches"])
        )
        queue = build_decision_queue(reports, {"comment_matches": [result], "source_files": []})
        self.assertTrue(queue["items"][0]["evidence_excerpts"])
        self.assertEqual(queue["items"][0]["decision"]["status"], "pending")
        self.assertTrue(queue["items"][0]["decision"]["requires_user_approval"])

    def test_numbering_audit_finds_gaps_duplicates_and_unresolved_references(self):
        manuscript = self.root / "Manuscript.docx"
        si = self.root / "SI.docx"
        write_docx(
            manuscript,
            [
                [("text", "As shown in Figure S3, the coordination changes; Figure S1 is the baseline.")],
                [("text", "The full data are listed in Table S2.")],
            ],
        )
        write_docx(
            si,
            [
                [("text", "Figure S1. Baseline structure.")],
                [("text", "Figure S3. Defect structure.")],
                [("text", "Table S1. First computational table.")],
                [("text", "Table S2. Second computational table.")],
                [("text", "Table S2. Duplicate computational table.")],
            ],
        )
        reports = []
        for role, path in [("Manuscript", manuscript), ("SI", si)]:
            data = cli.inspect_docx(str(path))
            data["role"] = role
            reports.append(data)

        audit = audit_figure_table_numbering(reports)
        self.assertEqual(audit["unresolved_references"], [])
        self.assertTrue(
            any(
                row["kind"] == "table" and row["label"] == "S2"
                for row in audit["duplicate_labels_across_documents"]
            ) is False
        )
        # Duplicate captions in one document are reported under that document.
        self.assertEqual(
            len(audit["by_document"]["SI"]["duplicate_captions"]), 1
        )
        # Figure captions skip S2, which must be reported as a numbering gap.
        self.assertTrue(
            any(
                row["kind"] == "figure" and "S2" in row["missing_labels"]
                for row in audit["by_document"]["SI"]["numbering_gaps"]
            )
        )
        # First-mention order decreases from S3 to S1.
        self.assertTrue(
            any(
                row["kind"] == "figure" and row["label"] == "S1"
                for row in audit["by_document"]["Manuscript"]["out_of_order_first_mentions"]
            )
        )

    def test_cache_reuses_inventory_until_source_bytes_change(self):
        source = self.root / "Manuscript.docx"
        write_docx(source, [[("text", "Original paragraph.")]])
        cache = self.root / ".co-reviewer-cache"
        calls = []

        def inspector(path, author):
            calls.append((path, author))
            return cli.inspect_docx(path, author)

        first, first_status = load_cached_inspection(source, None, cache, inspector)
        second, second_status = load_cached_inspection(source, None, cache, inspector)
        self.assertEqual(first_status, "miss")
        self.assertEqual(second_status, "hit")
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["source_sha256"], second["source_sha256"])

        write_docx(source, [[("text", "Changed paragraph.")]])
        third, third_status = load_cached_inspection(source, None, cache, inspector)
        self.assertEqual(third_status, "miss")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(second["source_sha256"], third["source_sha256"])

    def test_inspection_outputs_cannot_overwrite_source_docx(self):
        source = self.root / "Manuscript.docx"
        write_docx(source, [[("text", "Keep the source intact.")]])
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "overwrite a source DOCX"):
            cli.validate_inspection_artifact_paths([str(source)], [str(source)])
        self.assertEqual(source.read_bytes(), original)

    def test_audit_cli_writes_bounded_decision_queue_and_uses_cache_on_second_run(self):
        reply = self.root / "Reply.docx"
        manuscript = self.root / "Manuscript.docx"
        si = self.root / "SI.docx"
        anchor = "Figure S1. A specific structure."
        write_docx(
            reply,
            [[("text", "Please consider "), ("start", "5"), ("text", anchor), ("end", "5")]],
            [{"id": "5", "text": "Should this be described in the main text?"}],
        )
        write_docx(manuscript, [[("text", "The structure is discussed in section 3.")]])
        write_docx(si, [[("text", anchor)]])
        args = Namespace(
            rev=str(reply), man=str(manuscript), si=str(si),
            comment_author=None,
            out=str(self.root / "workspace_digest.md"),
            json_out=str(self.root / "workspace_inventory.json"),
            audit_md_out=str(self.root / "review_audit_report.md"),
            audit_out=str(self.root / "review_audit_report.json"),
            decision_queue_out=str(self.root / "review_decision_queue.json"),
            cache_dir=str(self.root / ".co-reviewer-cache"),
            refresh_cache=False,
            max_items_per_section=50,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_audit(args), 0)
        audit = json.loads(Path(args.audit_out).read_text(encoding="utf-8"))
        queue = json.loads(Path(args.decision_queue_out).read_text(encoding="utf-8"))
        self.assertEqual(audit["comment_matches"][0]["target_status"], "exact_unique")
        self.assertEqual(queue["items"][0]["decision"]["status"], "pending")
        self.assertTrue(queue["instructions"])
        self.assertTrue(Path(args.audit_md_out).is_file())

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(cli.cmd_audit(args), 0)
        self.assertIn("[CACHE] 3 hit(s), 0 parse(s)", output.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

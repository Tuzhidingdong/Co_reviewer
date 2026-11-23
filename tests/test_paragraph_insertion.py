from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from docx.enum.style import WD_STYLE_TYPE

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli
from co_reviewer.plan_compiler import compile_revision_plan


class ParagraphInsertionTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"
        self.plan_path = self.root / "revision_plan.json"
        document = Document()
        anchor = document.add_paragraph("Anchor paragraph used for insertion.")
        anchor.style = "Heading 2"
        anchor.runs[0].bold = True
        document.add_paragraph("Unchanged trailing paragraph.")
        document.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def _compile(self, position: str):
        return compile_revision_plan({
            "documents": {
                "Manuscript": {
                    "input_file": str(self.source),
                    "output_file": str(self.output),
                }
            },
            "intents": [{
                "id": "INS-001",
                "document": "Manuscript",
                "action": "insert_paragraph",
                "anchor_text": "Anchor paragraph used for insertion.",
                "position": position,
                "inserted_text": "New approved paragraph content.",
                "p_idx": 0,
                "intent": "Insert one approved paragraph adjacent to the exact anchor.",
            }],
        })

    def test_insert_paragraph_runs_through_preflight_approval_apply_and_verify(self):
        plan = self._compile("after")
        target = plan["revision_items"][0]["targets"][0]
        self.assertEqual(target["action"], "insert_paragraph")
        self.assertEqual(target["target_identity_mode"], "legacy_p_idx_explicit")

        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"])
        self.assertTrue(plan["revision_items"][0]["preflight_fingerprint"])
        self.assertTrue(target["location_phrase"])

        self.plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        rc = cli.cmd_approve_plan(SimpleNamespace(
            plan=str(self.plan_path),
            ids=["INS-001"],
            revision_ledger=None,
            actor="Unit Test Author",
        ))
        self.assertEqual(rc, 0)
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))

        apply_reports = cli.apply_plan(plan, "Unit Test Author")
        self.assertEqual(len(apply_reports), 1)
        self.assertEqual(apply_reports[0]["results"][0]["action"], "insert_paragraph")
        self.assertEqual(apply_reports[0]["results"][0]["inserted_text"], "New approved paragraph content.")
        plan["revision_items"][0]["status"] = "applied"

        verification = cli.verify_apply_report(plan, apply_reports)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["targets_verified"], 1)

        import zipfile
        from co_reviewer.document_engine import NS, parse_xml, qn, visible_text

        with zipfile.ZipFile(self.output) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        paragraphs = root.findall(".//w:body//w:p", NS)
        visible = [visible_text(paragraph) for paragraph in paragraphs]
        self.assertEqual(visible, [
            "Anchor paragraph used for insertion.",
            "New approved paragraph content.",
            "Unchanged trailing paragraph.",
        ])
        inserted = paragraphs[1]
        style = inserted.find("w:pPr/w:pStyle", NS)
        self.assertIsNotNone(style)
        self.assertEqual(style.get(qn("val")), "Heading2")
        insertion = inserted.find("w:ins", NS)
        self.assertIsNotNone(insertion)
        inserted_run = insertion.find("w:r", NS)
        bold = inserted_run.find("w:rPr/w:b", NS)
        self.assertIsNotNone(bold)

    def test_before_insertion_is_ordered_before_anchor(self):
        from co_reviewer.document_engine import apply_document, parse_xml, qn, visible_text
        import hashlib
        import zipfile

        output = self.root / "before.docx"
        apply_document(
            str(self.source),
            str(output),
            [{
                "action": "insert_paragraph",
                "operation_id": "INS-BEFORE-001",
                "find": "Anchor paragraph used for insertion.",
                "replace": "Before anchor.",
                "anchor_text": "Anchor paragraph used for insertion.",
                "inserted_text": "Before anchor.",
                "position": "before",
                "p_idx": 0,
                "target_identity_mode": "legacy_p_idx_explicit",
                "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                "authorized_change": {"intent": "Insert a paragraph before the exact anchor."},
            }],
            "Unit Test Author",
        )
        with zipfile.ZipFile(output) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        paragraphs = root.findall(".//w:body//w:p", {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
        self.assertEqual(visible_text(paragraphs[0]), "Before anchor.")
        self.assertEqual(visible_text(paragraphs[1]), "Anchor paragraph used for insertion.")
        marker = [
            node for node in root.findall(".//w:bookmarkStart", {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
            if (node.get(qn("name")) or "").startswith("_CR_OP_")
        ]
        self.assertEqual(len(marker), 1)

    def test_operation_marker_prevents_reapplying_the_same_operation(self):
        import hashlib
        from co_reviewer.document_engine import apply_document

        first = self.root / "first_marked.docx"
        second = self.root / "second_marked.docx"
        edit = {
            "action": "insert_paragraph",
            "operation_id": "INS-IDEMPOTENT-001",
            "find": "Anchor paragraph used for insertion.",
            "replace": "One inserted paragraph.",
            "anchor_text": "Anchor paragraph used for insertion.",
            "inserted_text": "One inserted paragraph.",
            "position": "after",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "authorized_change": {"intent": "Insert once.", "explicitly_authorized_insertions": []},
        }
        apply_document(str(self.source), str(first), [edit], "Unit Test Author")
        replay = dict(edit)
        replay["source_sha256"] = hashlib.sha256(first.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "IDEMPOTENT_INSERTION_BLOCKED"):
            apply_document(str(first), str(second), [replay], "Unit Test Author")

    def test_patch_operation_insertion_fields_round_trip(self):
        from co_reviewer.revision_models import PatchOperation

        operation = PatchOperation.from_mapping({
            "operation_id": "INS-MODEL-001",
            "document": "Manuscript",
            "input_file": str(self.source),
            "output_file": str(self.root / "model_marked.docx"),
            "part": "word/document.xml",
            "find": "Anchor paragraph used for insertion.",
            "replace": "Model-level inserted paragraph.",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "action": "insert_paragraph",
            "anchor_text": "Anchor paragraph used for insertion.",
            "position": "before",
            "inserted_text": "Model-level inserted paragraph.",
            "source_comment_ids": ["C-INSERT-1"],
            "authorized_change": {"intent": "Insert one paragraph."},
        })
        restored = PatchOperation.from_mapping(operation.to_dict())
        self.assertEqual(restored.action, "insert_paragraph")
        self.assertEqual(restored.anchor_text, operation.anchor_text)
        self.assertEqual(restored.position, "before")
        self.assertEqual(restored.inserted_text, operation.inserted_text)
        legacy = restored.to_legacy_target()
        self.assertEqual(legacy["action"], "insert_paragraph")
        self.assertEqual(legacy["operation_id"], "INS-MODEL-001")

    def test_duplicate_operation_ids_are_rejected_before_any_write(self):
        import hashlib
        from co_reviewer.document_engine import apply_document

        output = self.root / "duplicate_ids.docx"
        edit = {
            "action": "insert_paragraph",
            "operation_id": "INS-DUPLICATE-001",
            "find": "Anchor paragraph used for insertion.",
            "replace": "Duplicate operation.",
            "anchor_text": "Anchor paragraph used for insertion.",
            "inserted_text": "Duplicate operation.",
            "position": "after",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
            "authorized_change": {"intent": "Insert exactly once."},
        }
        with self.assertRaisesRegex(ValueError, "DUPLICATE_OPERATION_ID"):
            apply_document(
                str(self.source), str(output), [dict(edit), dict(edit)], "Unit Test Author"
            )
        self.assertFalse(output.exists())

    def test_mixed_direct_run_format_anchor_fails_closed(self):
        import hashlib
        from co_reviewer.document_engine import apply_document

        source = self.root / "mixed_format.docx"
        output = self.root / "mixed_format_marked.docx"
        document = Document()
        paragraph = document.add_paragraph()
        paragraph.add_run("Mixed ")
        bold = paragraph.add_run("format")
        bold.bold = True
        document.save(source)
        edit = {
            "action": "insert_paragraph",
            "operation_id": "INS-MIXED-FORMAT-001",
            "find": "Mixed format",
            "replace": "Inserted text.",
            "anchor_text": "Mixed format",
            "inserted_text": "Inserted text.",
            "position": "after",
            "p_idx": 0,
            "target_identity_mode": "legacy_p_idx_explicit",
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "authorized_change": {"intent": "Insert with an explicit format source."},
        }
        with self.assertRaisesRegex(ValueError, "PARAGRAPH_INSERTION_AMBIGUOUS_CHARACTER_FORMAT"):
            apply_document(str(source), str(output), [edit], "Unit Test Author")
        self.assertFalse(output.exists())

    def test_inserted_native_subscript_text_is_normalized_and_tracked(self):
        import hashlib
        import zipfile
        from co_reviewer.document_engine import NS, apply_document, parse_xml, qn, visible_text

        output = self.root / "native_subscript_marked.docx"
        inserted = "Na₂SO₄"
        apply_document(
            str(self.source),
            str(output),
            [{
                "action": "insert_paragraph",
                "operation_id": "INS-NATIVE-SCRIPT-001",
                "find": "Anchor paragraph used for insertion.",
                "replace": inserted,
                "anchor_text": "Anchor paragraph used for insertion.",
                "inserted_text": inserted,
                "position": "after",
                "p_idx": 0,
                "target_identity_mode": "legacy_p_idx_explicit",
                "source_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                "authorized_change": {
                    "intent": "Insert a formula with exact native subscript tokens.",
                    "explicitly_authorized_insertions": [
                        {"kind": "formula", "text": inserted},
                    ],
                },
            }],
            "Unit Test Author",
        )
        with zipfile.ZipFile(output) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        paragraphs = root.findall(".//w:body//w:p", NS)
        self.assertEqual(visible_text(paragraphs[1]), "Na2SO4")
        insertion = paragraphs[1].find("w:ins", NS)
        self.assertIsNotNone(insertion)
        subscript_runs = [
            run for run in insertion.findall(".//w:r", NS)
            if (
                (alignment := run.find("w:rPr/w:vertAlign", NS)) is not None
                and alignment.get(qn("val")) == "subscript"
            )
        ]
        self.assertEqual(
            ["".join(node.text or "" for node in run.findall("w:t", NS)) for run in subscript_runs],
            ["2", "4"],
        )


if __name__ == "__main__":
    unittest.main()

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

        revised = Document(self.output)
        visible = [paragraph.text for paragraph in revised.paragraphs]
        self.assertEqual(visible, [
            "Anchor paragraph used for insertion.",
            "New approved paragraph content.",
            "Unchanged trailing paragraph.",
        ])
        self.assertEqual(revised.paragraphs[1].style.name, "Heading 2")
        self.assertTrue(revised.paragraphs[1].runs[0].bold)

    def test_before_insertion_is_ordered_before_anchor(self):
        from co_reviewer.document_engine import apply_document, parse_xml, qn
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
        revised = Document(output)
        self.assertEqual(revised.paragraphs[0].text, "Before anchor.")
        self.assertEqual(revised.paragraphs[1].text, "Anchor paragraph used for insertion.")

        with zipfile.ZipFile(output) as archive:
            root = parse_xml(archive.read("word/document.xml"))
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


if __name__ == "__main__":
    unittest.main()

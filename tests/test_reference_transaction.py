from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from co_reviewer.reference_transaction import (
    ReferenceTransactionError,
    compile_reference_transaction,
)


class ReferenceTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manuscript = self.root / "manuscript.docx"
        self.si = self.root / "si.docx"
        for path, paragraphs in (
            (self.manuscript, ["The claim is supported by 4.", "References", "[4] Smith (2020). Journal details."]),
            (self.si, ["Supporting information."]),
        ):
            document = Document()
            for value in paragraphs:
                document.add_paragraph(value)
            document.save(path)
        self.graph = {
            "schema_version": "1.2",
            "documents": ["Manuscript", "SI"],
            "definitions": [{
                "id": "def-4",
                "document": "Manuscript",
                "part": "word/document.xml",
                "paragraph_index": 2,
                "kind": "citation",
                "number": "4",
                "text": "[4]",
                "reference_mechanism": "static_text",
            }],
            "mentions": [
                {
                    "id": "men-4-a", "document": "Manuscript",
                    "part": "word/document.xml", "paragraph_index": 0,
                    "kind": "citation", "number": "4", "text": "4",
                    "target_definition_ids": ["def-4"], "resolution": "resolved",
                },
                {
                    "id": "men-7-new", "document": "Manuscript",
                    "part": "word/document.xml", "paragraph_index": 0,
                    "kind": "citation", "number": "7", "text": "7",
                    "target_definition_ids": [], "resolution": "unresolved",
                },
            ],
        }
        self.spec = {
            "transaction_id": "REF-TXN-001",
            "reviewer": "Author",
            "comment": "Add one reference, update one entry and renumber its in-text citation.",
            "proposed_response": "The reference list and all linked citations were updated.",
            "source_comment_ids": ["C-1"],
            "evidence_ids": ["E-1"],
            "documents": {
                "Manuscript": {
                    "input_file": str(self.manuscript),
                    "output_file": str(self.root / "manuscript_marked.docx"),
                },
                "SI": {
                    "input_file": str(self.si),
                    "output_file": str(self.root / "si_marked.docx"),
                },
            },
            "additions": [{
                "operation_id": "REF-ADD-001",
                "document": "Manuscript",
                "p_idx": 1,
                "anchor_text": "References",
                "position": "after",
                "inserted_text": "[7] New reference entry.",
                "reference_number": "7",
                "linked_mention_ids": ["men-7-new"],
                "authorized_change": {
                    "intent": "Add the approved bibliography entry.",
                    "explicitly_authorized_insertions": [{"kind": "citation", "text": "[7]"}],
                },
            }],
            "entry_updates": [{
                "operation_id": "REF-ENTRY-001",
                "definition_id": "def-4",
                "document": "Manuscript",
                "p_idx": 2,
                "find": "[4] Smith (2020). Journal details.",
                "replace": "[5] Smith (2020). Journal details.",
                "new_number": "5",
                "authorized_change": {
                    "intent": "Renumber the exact bibliography entry.",
                    "explicitly_authorized_tokens": [
                        {"kind": "citation", "text": "[4]", "replacement": "[5]"}
                    ],
                },
            }],
            "citation_updates": [{
                "operation_id": "REF-CITE-001",
                "mention_id": "men-4-a",
                "document": "Manuscript",
                "p_idx": 0,
                "find": "4",
                "replace": "5",
                "new_number": "5",
                "authorized_change": {
                    "intent": "Update the exact linked in-text citation occurrence.",
                    "explicitly_authorized_tokens": [
                        {"kind": "number", "text": "4", "replacement": "5"}
                    ],
                },
            }],
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_compiles_three_reference_operation_types_into_one_pending_patch(self):
        plan = compile_reference_transaction(self.spec, self.graph)
        self.assertFalse(plan["approved"])
        self.assertEqual(len(plan["revision_items"]), 1)
        targets = plan["revision_items"][0]["targets"]
        self.assertEqual(len(targets), 3)
        self.assertEqual(
            [row["action"] for row in targets],
            ["insert_paragraph", "replace", "replace"],
        )
        transaction = plan["reference_transaction"]
        self.assertEqual(transaction["definition_ids_updated"], ["def-4"])
        self.assertEqual(transaction["citation_mention_ids_updated"], ["men-4-a"])
        self.assertEqual(transaction["definition_ids_added"][0]["linked_mention_ids"], ["men-7-new"])

    def test_blocks_a_partial_citation_renumbering(self):
        self.spec["citation_updates"] = []
        with self.assertRaisesRegex(ReferenceTransactionError, "every graph-linked citation occurrence"):
            compile_reference_transaction(self.spec, self.graph)

    def test_blocks_an_addition_that_omits_an_existing_same_number_mention(self):
        self.spec["additions"][0]["linked_mention_ids"] = []
        with self.assertRaisesRegex(ReferenceTransactionError, "every unresolved static mention"):
            compile_reference_transaction(self.spec, self.graph)


if __name__ == "__main__":
    unittest.main()

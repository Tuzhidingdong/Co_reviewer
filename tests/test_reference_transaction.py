from __future__ import annotations

import hashlib
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
            (self.manuscript, ["The claim is supported by [4].", "References", "[4] Smith (2020). Journal details."]),
            (self.si, ["Supporting information cites [7]."]),
        ):
            document = Document()
            for value in paragraphs:
                document.add_paragraph(value)
            document.save(path)
        self.graph = {
            "schema_version": "1.2",
            "documents": ["Manuscript", "SI"],
            "document_segments": {
                "Manuscript": [
                    {"part": "word/document.xml", "paragraph_index": 0, "in_reference_section": False},
                    {"part": "word/document.xml", "paragraph_index": 1, "in_reference_section": True},
                    {"part": "word/document.xml", "paragraph_index": 2, "in_reference_section": True},
                ],
                "SI": [
                    {"part": "word/document.xml", "paragraph_index": 0, "in_reference_section": False},
                ],
            },
            "source_files": {
                "Manuscript": {
                    "path": str(self.manuscript.resolve()),
                    "sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
                },
                "SI": {
                    "path": str(self.si.resolve()),
                    "sha256": hashlib.sha256(self.si.read_bytes()).hexdigest(),
                },
            },
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
                    "kind": "citation", "number": "4", "text": "4", "offset": 27,
                    "target_definition_ids": ["def-4"], "resolution": "resolved",
                },
                {
                    "id": "men-7-new", "document": "Manuscript",
                    "part": "word/document.xml", "paragraph_index": 0,
                    "kind": "citation", "number": "7", "text": "7",
                    "target_definition_ids": [], "resolution": "unresolved",
                },
                {
                    "id": "men-7-si", "document": "SI",
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
                "target_identity_mode": "legacy_p_idx_explicit",
                "document": "Manuscript",
                "p_idx": 1,
                "anchor_text": "References",
                "position": "after",
                "inserted_text": "[7] New reference entry.",
                "reference_number": "7",
                "linked_mention_ids": ["men-7-new", "men-7-si"],
                "authorized_change": {
                    "intent": "Add the approved bibliography entry.",
                    "explicitly_authorized_insertions": [{"kind": "citation", "text": "[7]"}],
                },
            }],
            "entry_updates": [{
                "operation_id": "REF-ENTRY-001",
                "target_identity_mode": "legacy_p_idx_explicit",
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
                "target_identity_mode": "legacy_p_idx_explicit",
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
        self.assertEqual(transaction["definition_ids_added"][0]["linked_mention_ids"], ["men-7-new", "men-7-si"])

    def test_explicit_format_maps_flow_through_reference_transaction_patch_and_preflight(self):
        import co_reviewer_cli as cli

        entry_replacement = self.spec["entry_updates"][0]["replace"]
        citation_replacement = self.spec["citation_updates"][0]["replace"]
        self.spec["entry_updates"][0]["replacement_format_map"] = [{
            "replacement_start": 0,
            "replacement_end": len(entry_replacement),
            "source_offset": 0,
        }]
        self.spec["citation_updates"][0]["replacement_format_map"] = [{
            "replacement_start": 0,
            "replacement_end": len(citation_replacement),
            "source_offset": 0,
        }]

        plan = compile_reference_transaction(self.spec, self.graph)
        targets_by_operation = {
            target["operation_id"]: target
            for target in plan["revision_items"][0]["targets"]
        }
        self.assertEqual(
            targets_by_operation["REF-ENTRY-001"]["replacement_format_map"],
            self.spec["entry_updates"][0]["replacement_format_map"],
        )
        self.assertEqual(
            targets_by_operation["REF-CITE-001"]["replacement_format_map"],
            self.spec["citation_updates"][0]["replacement_format_map"],
        )
        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"], preflight)

    def test_blocks_a_partial_citation_renumbering(self):
        self.spec["citation_updates"] = []
        with self.assertRaisesRegex(ReferenceTransactionError, "every graph-linked citation occurrence"):
            compile_reference_transaction(self.spec, self.graph)

    def test_blocks_an_addition_that_omits_an_existing_same_number_mention(self):
        self.spec["additions"][0]["linked_mention_ids"] = []
        with self.assertRaisesRegex(ReferenceTransactionError, "every unresolved static mention"):
            compile_reference_transaction(self.spec, self.graph)

    def test_rebuilds_output_reference_graph_and_checks_definitions_and_mentions(self):
        from co_reviewer.reference_transaction import verify_reference_transaction_outputs

        output = self.root / "manuscript_marked.docx"
        document = Document()
        document.add_paragraph("The claim is supported by [5]. The newly linked citation is [7].")
        document.add_paragraph("References")
        document.add_paragraph("[7] New reference entry.")
        document.add_paragraph("[5] Smith (2020). Journal details.")
        document.save(output)

        from co_reviewer.v232_contract import build_package_reference_graph, extract_docx_reference_segments

        output_graph = build_package_reference_graph({
            "Manuscript": extract_docx_reference_segments(output),
            "SI": extract_docx_reference_segments(self.si),
        })
        linked_mention_ids = sorted(
            row["id"] for row in output_graph["mentions"]
            if row.get("kind") == "citation" and str(row.get("number")) == "7"
        )
        plan = {
            "reference_transaction": {
                "documents": {
                    "Manuscript": {
                        "input_file": str(self.manuscript),
                        "output_file": str(output),
                        "source_sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
                    },
                    "SI": {
                        "input_file": str(self.si),
                        "output_file": str(self.root / "si_marked.docx"),
                        "source_sha256": hashlib.sha256(self.si.read_bytes()).hexdigest(),
                    },
                },
                "added_definition_expectations": [{
                    "document": "Manuscript",
                    "number": "7",
                    "operation_id": "REF-ADD-001",
                    "part": "word/document.xml",
                    "anchor_p_idx": 1,
                    "position": "after",
                    "inserted_text": "[7] New reference entry.",
                    "linked_mention_ids": linked_mention_ids,
                    "linked_mention_count": len(linked_mention_ids),
                }],
                "updated_definition_expectations": [{
                    "document": "Manuscript",
                    "operation_id": "REF-ENTRY-001",
                    "part": "word/document.xml",
                    "source_p_idx": 2,
                    "find_text": "[4] Smith (2020). Journal details.",
                    "old_number": "4",
                    "new_number": "5",
                    "updated_entry_text": "[5] Smith (2020). Journal details.",
                }],
                "updated_mention_expectations": [{
                    "document": "Manuscript",
                    "operation_id": "REF-CITE-001",
                    "part": "word/document.xml",
                    "source_p_idx": 0,
                    "source_offset": "The claim is supported by [4].".index("4"),
                    "find_text": "4",
                    "replace_text": "5",
                    "old_number": "4",
                    "new_number": "5",
                    "mention_id": "men-4-a",
                }],
            }
        }
        report = verify_reference_transaction_outputs(
            plan,
            [{
                "input_file": str(self.manuscript.resolve()),
                "output_file": str(output.resolve()),
                "input_sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
                "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "results": [
                    {
                        "document": "Manuscript",
                        "operation_id": "REF-ADD-001",
                        "action": "insert_paragraph",
                        "part": "word/document.xml",
                        "p_idx": 1,
                        "inserted_p_idx": 2,
                        "status": "applied",
                    },
                    {
                        "document": "Manuscript",
                        "operation_id": "REF-ENTRY-001",
                        "action": "replace",
                        "part": "word/document.xml",
                        "p_idx": 2,
                        "find": "[4] Smith (2020). Journal details.",
                        "replace": "[5] Smith (2020). Journal details.",
                        "status": "applied",
                    },
                    {
                        "document": "Manuscript",
                        "operation_id": "REF-CITE-001",
                        "action": "replace",
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "find": "4",
                        "replace": "5",
                        "status": "applied",
                    }
                ]
            }],
        )
        self.assertTrue(report["checked"])
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["unresolved_count"], 0)

    def test_reference_transaction_runs_as_one_approved_apply_verify_unit(self):
        import json
        from types import SimpleNamespace
        from docx import Document
        import co_reviewer_cli as cli
        from co_reviewer.v232_contract import (
            build_package_reference_graph,
            extract_docx_reference_segments,
        )

        manuscript = self.root / "integrated_manuscript.docx"
        si = self.root / "integrated_si.docx"
        manuscript_output = self.root / "integrated_manuscript_marked.docx"
        si_output = self.root / "integrated_si_marked.docx"
        plan_path = self.root / "integrated_reference_plan.json"

        document = Document()
        document.add_paragraph("The claim cites [4] and [7].")
        document.add_paragraph("A second claim also cites [4].")
        document.add_paragraph("References")
        document.add_paragraph("[4] Smith (2020). Journal details.")
        document.save(manuscript)
        document = Document()
        document.add_paragraph("Supporting information cites [7].")
        document.save(si)

        source_paths = {"Manuscript": manuscript, "SI": si}
        graph = build_package_reference_graph({
            role: extract_docx_reference_segments(path)
            for role, path in source_paths.items()
        })
        graph["source_files"] = {
            role: {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for role, path in source_paths.items()
        }

        unresolved_seven = [
            row for row in graph["mentions"]
            if row.get("kind") == "citation"
            and str(row.get("number")) == "7"
            and row.get("resolution") == "unresolved"
        ]
        self.assertEqual({row["document"] for row in unresolved_seven}, {"Manuscript", "SI"})
        linked_mention_ids = sorted(row["id"] for row in unresolved_seven)

        definition_four = next(
            row for row in graph["definitions"]
            if row.get("document") == "Manuscript"
            and row.get("kind") == "citation"
            and str(row.get("number")) == "4"
        )
        mention_four_rows = sorted(
            (
                row for row in graph["mentions"]
                if row.get("document") == "Manuscript"
                and row.get("kind") == "citation"
                and str(row.get("number")) == "4"
                and row.get("resolution") == "resolved"
            ),
            key=lambda row: (row.get("paragraph_index", -1), row.get("offset", -1)),
        )
        self.assertEqual(len(mention_four_rows), 2)

        spec = {
            "transaction_id": "REF-TXN-INTEGRATION-001",
            "reviewer": "Author",
            "comment": "Insert one graph-linked bibliography entry and renumber all linked occurrences.",
            "proposed_response": "The bibliography entry and linked citation occurrences were updated.",
            "source_comment_ids": ["C-REF-INTEGRATION"],
            "evidence_ids": ["E-REF-INTEGRATION"],
            "documents": {
                "Manuscript": {
                    "input_file": str(manuscript),
                    "output_file": str(manuscript_output),
                },
                "SI": {
                    "input_file": str(si),
                    "output_file": str(si_output),
                },
            },
            "additions": [{
                "operation_id": "REF-ADD-INTEGRATION",
                "target_identity_mode": "legacy_p_idx_explicit",
                "document": "Manuscript",
                "p_idx": 2,
                "anchor_text": "References",
                "position": "after",
                "inserted_text": "[7] New reference entry.",
                "reference_number": "7",
                "linked_mention_ids": linked_mention_ids,
                "authorized_change": {
                    "intent": "Insert the explicitly approved bibliography entry.",
                    "explicitly_authorized_insertions": [
                        {"kind": "citation", "text": "[7]"},
                    ],
                },
            }],
            "entry_updates": [{
                "operation_id": "REF-ENTRY-INTEGRATION",
                "target_identity_mode": "legacy_p_idx_explicit",
                "definition_id": definition_four["id"],
                "document": "Manuscript",
                "p_idx": definition_four["paragraph_index"],
                "find": "[4] Smith (2020). Journal details.",
                "replace": "[5] Smith (2020). Journal details.",
                "new_number": "5",
                "authorized_change": {
                    "intent": "Renumber the exact graph-identified bibliography entry.",
                    "explicitly_authorized_tokens": [
                        {"kind": "citation", "text": "[4]", "replacement": "[5]"},
                    ],
                },
            }],
            "citation_updates": [
                {
                    "operation_id": f"REF-CITE-INTEGRATION-{index + 1}",
                    "target_identity_mode": "legacy_p_idx_explicit",
                    "mention_id": mention["id"],
                    "document": "Manuscript",
                    "p_idx": mention["paragraph_index"],
                    "find": mention["text"],
                    "replace": "5",
                    "new_number": "5",
                    "authorized_change": {
                        "intent": "Update the exact graph-identified in-text citation occurrence.",
                        "explicitly_authorized_tokens": [
                            {"kind": "number", "text": "4", "replacement": "5"},
                        ],
                    },
                }
                for index, mention in enumerate(mention_four_rows)
            ],
        }

        plan = compile_reference_transaction(spec, graph)
        self.assertFalse(plan["approved"])
        self.assertEqual(len(plan["revision_items"][0]["targets"]), 4)
        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"], preflight)

        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        approve_rc = cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path),
            ids=[plan["revision_items"][0]["id"]],
            revision_ledger=None,
            actor="Unit Test Author",
        ))
        self.assertEqual(approve_rc, 0)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

        apply_reports = cli.apply_plan(plan, "Unit Test Author")
        self.assertEqual(len(apply_reports), 1)
        plan["revision_items"][0]["status"] = "applied"

        verification = cli.verify_apply_report(plan, apply_reports)
        self.assertTrue(verification["passed"], verification)
        inserted_before_renumbered_entry = next(
            row for row in verification["results"]
            if row.get("action") == "insert_paragraph"
            and row.get("anchor_text") == "[33] Synthetic old reference A."
        )
        self.assertTrue(
            inserted_before_renumbered_entry["checks"]["anchor_text_after_approved_edits"],
            inserted_before_renumbered_entry,
        )
        self.assertTrue(verification["reference_transaction"]["checked"])
        self.assertTrue(verification["reference_transaction"]["passed"])
        self.assertEqual(verification["targets_verified"], 4)
        self.assertEqual(verification["reference_transaction"]["unresolved_count"], 0)

        import zipfile
        from co_reviewer.document_engine import NS, parse_xml, visible_text
        with zipfile.ZipFile(manuscript_output) as archive:
            output_root = parse_xml(archive.read("word/document.xml"))
        output_paragraphs = output_root.findall(".//w:body//w:p", NS)
        output_text = [visible_text(paragraph) for paragraph in output_paragraphs]
        self.assertIn("The claim cites [5] and [7].", output_text)
        self.assertIn("A second claim also cites [5].", output_text)
        self.assertEqual(output_text[3], "[7] New reference entry.")
        self.assertEqual(output_text[4], "[5] Smith (2020). Journal details.")

    def test_transaction_metadata_is_bound_to_standard_plan_approval(self):
        import copy
        import co_reviewer_cli as cli

        plan = compile_reference_transaction(self.spec, self.graph)
        self.assertEqual(cli.validate_plan(plan, require_approval=False), [])

        tampered = copy.deepcopy(plan)
        tampered["reference_transaction"]["added_definition_expectations"][0]["linked_mention_ids"].pop()
        errors = cli.validate_plan(tampered, require_approval=False)
        self.assertTrue(
            any("transaction_fingerprint is missing or stale" in error for error in errors),
            errors,
        )

        tampered_operations = copy.deepcopy(plan)
        tampered_operations["revision_items"][0]["targets"][0]["operation_id"] = "OTHER-OP"
        errors = cli.validate_plan(tampered_operations, require_approval=False)
        self.assertTrue(any("operation_ids must exactly match" in error for error in errors), errors)

    def test_preflight_refuses_source_changes_after_reference_graph_creation(self):
        import co_reviewer_cli as cli

        plan = compile_reference_transaction(self.spec, self.graph)
        changed = Document(self.manuscript)
        changed.add_paragraph("A source change after graph creation.")
        changed.save(self.manuscript)
        with self.assertRaisesRegex(ValueError, "changed after the Reference Graph was built"):
            cli.preflight_plan(plan)

    def test_reference_insertion_requires_separate_exact_authorization_for_non_label_numbers(self):
        self.spec["additions"][0]["inserted_text"] = "[7] New reference published in 2024."
        with self.assertRaisesRegex(
            ReferenceTransactionError,
            "INSERTED_TOKEN_NOT_EXACTLY_AUTHORIZED",
        ):
            compile_reference_transaction(self.spec, self.graph)

        self.spec["additions"][0]["authorized_change"]["explicitly_authorized_insertions"].append({
            "kind": "number",
            "text": "2024",
        })
        plan = compile_reference_transaction(self.spec, self.graph)
        insertion = next(
            target
            for target in plan["revision_items"][0]["targets"]
            if target["operation_id"] == "REF-ADD-001"
        )
        self.assertEqual(insertion["replace"], "[7] New reference published in 2024.")

    def test_unbracketed_reference_label_uses_exact_number_token_authorization(self):
        self.spec["additions"][0]["inserted_text"] = "7. New reference entry."
        self.spec["additions"][0]["authorized_change"]["explicitly_authorized_insertions"] = [{
            "kind": "number",
            "text": "7",
        }]
        plan = compile_reference_transaction(self.spec, self.graph)
        insertion = next(
            target
            for target in plan["revision_items"][0]["targets"]
            if target["operation_id"] == "REF-ADD-001"
        )
        self.assertEqual(insertion["replace"], "7. New reference entry.")

    def test_reuses_released_labels_only_with_explicit_mention_rebinding_and_full_renumbering(self):
        import copy
        import json
        from types import SimpleNamespace
        import co_reviewer_cli as cli
        from co_reviewer.v232_contract import (
            build_package_reference_graph,
            extract_docx_reference_segments,
        )

        manuscript = self.root / "reuse_manuscript.docx"
        si = self.root / "reuse_si.docx"
        manuscript_output = self.root / "reuse_manuscript_marked.docx"
        si_output = self.root / "reuse_si_marked.docx"
        plan_path = self.root / "reuse_reference_plan.json"

        document = Document()
        document.add_paragraph("Claim alpha [33].")
        document.add_paragraph("Claim beta [34].")
        document.add_paragraph("Claim gamma [35].")
        document.add_paragraph("References")
        document.add_paragraph("[33] Synthetic old reference A.")
        document.add_paragraph("[34] Synthetic old reference B.")
        document.add_paragraph("[35] Synthetic old reference C.")
        document.save(manuscript)
        document = Document()
        document.add_paragraph("New source A [33].")
        document.add_paragraph("New source B [34].")
        document.save(si)

        source_paths = {"Manuscript": manuscript, "SI": si}
        graph = build_package_reference_graph({
            role: extract_docx_reference_segments(path)
            for role, path in source_paths.items()
        })
        graph["source_files"] = {
            role: {
                "path": str(path.resolve()),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for role, path in source_paths.items()
        }

        definitions = {
            str(row["number"]): row
            for row in graph["definitions"]
            if row.get("document") == "Manuscript" and row.get("kind") == "citation"
        }
        manuscript_mentions = {
            str(row["number"]): row
            for row in graph["mentions"]
            if row.get("document") == "Manuscript"
            and row.get("kind") == "citation"
            and row.get("resolution") == "resolved"
        }
        si_mentions = {
            str(row["number"]): row
            for row in graph["mentions"]
            if row.get("document") == "SI" and row.get("kind") == "citation"
        }
        self.assertEqual(set(definitions), {"33", "34", "35"})
        self.assertEqual(set(manuscript_mentions), {"33", "34", "35"})
        self.assertEqual(set(si_mentions), {"33", "34"})
        self.assertEqual(
            {row.get("resolution_scope") for row in si_mentions.values()},
            {"package"},
        )
        self.assertTrue(all(
            row.get("target_definition_ids") == [definitions[str(row["number"])]["id"]]
            for row in si_mentions.values()
        ))

        additions = []
        for index, number in enumerate(("33", "34"), start=1):
            additions.append({
                "operation_id": f"REF-ADD-REUSE-{index}",
                "target_identity_mode": "legacy_p_idx_explicit",
                "document": "Manuscript",
                "p_idx": 3 if number == "33" else 4,
                "anchor_text": "References" if number == "33" else "[33] Synthetic old reference A.",
                "position": "after" if number == "33" else "before",
                "inserted_text": f"[{number}] Synthetic new reference entry.",
                "reference_number": number,
                "linked_mention_ids": (
                    [si_mentions[number]["id"]] if number == "33" else []
                ),
                "authorized_change": {
                    "intent": f"Add the new reference {number} after releasing the old label.",
                    "explicitly_authorized_insertions": [
                        {"kind": "citation", "text": f"[{number}]"},
                    ],
                },
            })

        entry_updates = []
        citation_updates = []
        for old_number, new_number in (("33", "35"), ("34", "36"), ("35", "37")):
            definition = definitions[old_number]
            label = chr(65 + int(old_number) - 33)
            entry_updates.append({
                "operation_id": f"REF-ENTRY-REUSE-{old_number}",
                "target_identity_mode": "legacy_p_idx_explicit",
                "definition_id": definition["id"],
                "document": "Manuscript",
                "p_idx": definition["paragraph_index"],
                "find": f"[{old_number}] Synthetic old reference {label}.",
                "replace": f"[{new_number}] Synthetic old reference {label}.",
                "new_number": new_number,
                "authorized_change": {
                    "intent": f"Move the old reference label {old_number} to {new_number}.",
                    "explicitly_authorized_tokens": [
                        {"kind": "citation", "text": f"[{old_number}]", "replacement": f"[{new_number}]"},
                    ],
                },
            })
            mention = manuscript_mentions[old_number]
            citation_updates.append({
                "operation_id": f"REF-CITE-REUSE-{old_number}",
                "target_identity_mode": "legacy_p_idx_explicit",
                "mention_id": mention["id"],
                "document": "Manuscript",
                "p_idx": mention["paragraph_index"],
                "find": mention["text"],
                "replace": new_number,
                "new_number": new_number,
                "authorized_change": {
                    "intent": f"Update every old citation occurrence from {old_number} to {new_number}.",
                    "explicitly_authorized_tokens": [
                        {"kind": "number", "text": old_number, "replacement": new_number},
                    ],
                },
            })

        # SI [34] still refers to the old bibliography definition. Do not
        # silently rebind it to the new [34] entry; explicitly renumber it
        # together with the old definition to [36].
        si_old_34 = si_mentions["34"]
        citation_updates.append({
            "operation_id": "REF-CITE-REUSE-SI-34",
            "target_identity_mode": "legacy_p_idx_explicit",
            "mention_id": si_old_34["id"],
            "document": "SI",
            "p_idx": si_old_34["paragraph_index"],
            "find": si_old_34["text"],
            "replace": "36",
            "new_number": "36",
            "authorized_change": {
                "intent": "Keep this SI citation linked to the old reference moved to [36].",
                "explicitly_authorized_tokens": [
                    {"kind": "number", "text": "34", "replacement": "36"},
                ],
            },
        })

        spec = {
            "transaction_id": "REF-TXN-RELEASE-REUSE-001",
            "reviewer": "Author",
            "comment": "Shift three existing references and reuse two released labels.",
            "proposed_response": "Existing definitions and linked mentions were updated atomically.",
            "source_comment_ids": ["C-REUSE-001"],
            "evidence_ids": ["E-REUSE-001"],
            "documents": {
                "Manuscript": {
                    "input_file": str(manuscript),
                    "output_file": str(manuscript_output),
                },
                "SI": {
                    "input_file": str(si),
                    "output_file": str(si_output),
                },
            },
            "additions": additions,
            "entry_updates": entry_updates,
            "citation_updates": citation_updates,
        }

        unsafe_without_renumbering = copy.deepcopy(spec)
        unsafe_without_renumbering["entry_updates"] = []
        unsafe_without_renumbering["citation_updates"] = []
        with self.assertRaisesRegex(ReferenceTransactionError, "not moved away"):
            compile_reference_transaction(unsafe_without_renumbering, graph)

        unsafe_partial_renumbering = copy.deepcopy(spec)
        unsafe_partial_renumbering["citation_updates"].pop()
        with self.assertRaisesRegex(ReferenceTransactionError, "every graph-linked citation occurrence"):
            compile_reference_transaction(unsafe_partial_renumbering, graph)

        plan = compile_reference_transaction(spec, graph)
        self.assertFalse(plan["approved"])
        transaction = plan["reference_transaction"]
        self.assertEqual(
            [row["number"] for row in transaction["definition_ids_added"]],
            ["33", "34"],
        )
        self.assertEqual(
            [row["linked_mention_ids"] for row in transaction["definition_ids_added"]],
            [[si_mentions["33"]["id"]], []],
        )
        self.assertIn(
            si_mentions["34"]["id"],
            transaction["citation_mention_ids_updated"],
        )
        self.assertEqual(
            set(transaction["definition_ids_updated"]),
            {row["id"] for row in definitions.values()},
        )
        self.assertEqual(
            set(transaction["citation_mention_ids_updated"]),
            {row["id"] for row in manuscript_mentions.values()} | {si_mentions["34"]["id"]},
        )

        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"], preflight)
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        approve_rc = cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path),
            ids=[plan["revision_items"][0]["id"]],
            revision_ledger=None,
            actor="Unit Test Author",
        ))
        self.assertEqual(approve_rc, 0)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

        apply_reports = cli.apply_plan(plan, "Unit Test Author")
        self.assertEqual(len(apply_reports), 2)
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, apply_reports)
        self.assertTrue(verification["passed"], verification)
        self.assertTrue(verification["reference_transaction"]["checked"])
        self.assertTrue(verification["reference_transaction"]["passed"], verification["reference_transaction"])

        output_graph = build_package_reference_graph({
            "Manuscript": extract_docx_reference_segments(manuscript_output),
            "SI": extract_docx_reference_segments(si_output),
        })
        output_definitions = {
            str(row["number"]): row
            for row in output_graph["definitions"]
            if row.get("document") == "Manuscript" and row.get("kind") == "citation"
        }
        self.assertEqual(set(output_definitions), {"33", "34", "35", "36", "37"})
        output_mentions = {row["id"]: row for row in output_graph["mentions"]}
        self.assertEqual(
            output_mentions[si_mentions["33"]["id"]]["target_definition_ids"],
            [output_definitions["33"]["id"]],
        )
        self.assertEqual(
            output_mentions[si_mentions["34"]["id"]]["target_definition_ids"],
            [output_definitions["36"]["id"]],
        )
        self.assertEqual(output_mentions[si_mentions["34"]["id"]]["number"], "36")

    def test_reference_operation_needs_targetref_or_explicit_migration_mode(self):
        self.spec["entry_updates"][0].pop("target_identity_mode")
        with self.assertRaisesRegex(
            ReferenceTransactionError,
            "must supply a registered TargetRef or explicitly declare",
        ):
            compile_reference_transaction(self.spec, self.graph)


if __name__ == "__main__":
    unittest.main()

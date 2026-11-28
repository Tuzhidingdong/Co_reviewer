# -*- coding: utf-8 -*-
"""Minimal regressions: label-only entry expectations and same-anchor insert order."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.reference_transaction import compile_reference_transaction  # noqa: E402
from co_reviewer.v232_contract import (  # noqa: E402
    build_package_reference_graph, extract_docx_reference_segments,
)
from co_reviewer.document_engine import NS, parse_xml, visible_text  # noqa: E402


class VerifyContractAndInsertOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manuscript = self.root / "manuscript.docx"
        self.marked = self.root / "manuscript_marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _build(self, *, additions):
        doc = Document()
        doc.add_paragraph("Claim alpha [33]. Claim beta [34]. Claim gamma [35].")
        doc.add_paragraph("References")
        doc.add_paragraph("[33] Old reference A.")
        doc.add_paragraph("[34] Old reference B.")
        doc.add_paragraph("[35] Old reference C.")
        doc.save(self.manuscript)
        graph = build_package_reference_graph({
            "Manuscript": extract_docx_reference_segments(self.manuscript)})
        graph["source_files"] = {"Manuscript": {
            "path": str(self.manuscript.resolve()),
            "sha256": hashlib.sha256(self.manuscript.read_bytes()).hexdigest(),
        }}
        definitions = {str(r["number"]): r for r in graph["definitions"]
                       if r.get("document") == "Manuscript" and r.get("kind") == "citation"}
        mentions = {str(r["number"]): r for r in graph["mentions"]
                    if r.get("document") == "Manuscript" and r.get("kind") == "citation"
                    and r.get("resolution") == "resolved"}
        entry_updates, citation_updates = [], []
        for old, new in (("35", "37"), ("34", "36"), ("33", "35")):
            entry_updates.append({
                "operation_id": "REF-ENTRY-" + old,
                "target_identity_mode": "legacy_p_idx_explicit",
                "definition_id": definitions[old]["id"],
                "document": "Manuscript", "p_idx": definitions[old]["paragraph_index"],
                "find": "[%s]" % old, "replace": "[%s]" % new, "new_number": new,
                "authorized_change": {"intent": "Renumber label.",
                    "explicitly_authorized_tokens": [
                        {"kind": "citation", "text": "[%s]" % old,
                         "replacement": "[%s]" % new}]},
            })
            citation_updates.append({
                "operation_id": "REF-CITE-" + old,
                "target_identity_mode": "legacy_p_idx_explicit",
                "mention_id": mentions[old]["id"],
                "document": "Manuscript", "p_idx": mentions[old]["paragraph_index"],
                "find": old, "replace": new, "new_number": new,
                "authorized_change": {"intent": "Renumber citation.",
                    "explicitly_authorized_tokens": [
                        {"kind": "number", "text": old, "replacement": new}]},
            })
        spec = {
            "transaction_id": "REF-TXN-CONTRACT",
            "reviewer": "Author",
            "comment": "Verify contract and insertion order.",
            "proposed_response": "Done.",
            "source_comment_ids": ["C-1"],
            "evidence_ids": ["E-1"],
            "documents": {"Manuscript": {
                "input_file": str(self.manuscript), "output_file": str(self.marked)}},
            "additions": additions,
            "entry_updates": entry_updates,
            "citation_updates": citation_updates,
        }
        return compile_reference_transaction(spec, graph), graph

    def _apply_and_verify(self, plan):
        self.assertEqual(cli.validate_plan(plan, require_approval=False), [])
        self.assertTrue(cli.preflight_plan(plan)["passed"])
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path), ids=[i["id"] for i in plan["revision_items"]],
            revision_ledger=None, actor="Unit Test Author")), 0)
        approved = json.loads(plan_path.read_text(encoding="utf-8"))
        reports = cli.apply_plan(approved, "Unit Test Author")
        for item in approved["revision_items"]:
            item["status"] = "applied"
        return approved, reports, cli.verify_apply_report(approved, reports)

    def _output_text(self, p_idx):
        with __import__("zipfile").ZipFile(self.marked) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        return visible_text(root.findall(".//w:p", {"w": NS["w"]})[p_idx])

    def test_label_only_entry_expectation_verifies_full_entry(self):
        plan, _graph = self._build(additions=[])
        approved, _reports, verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertTrue(verification["reference_transaction"]["passed"])
        # Full entry, not a bare label, is what Verify compared.
        self.assertTrue(self._output_text(2).startswith("[35] Old reference A."))

        # Negative: wrong approved label must fail.
        bad = copy.deepcopy(approved)
        bad["reference_transaction"]["updated_definition_expectations"][0][
            "updated_entry_text"] = "[36]"
        bad_v = cli.verify_apply_report(bad, _reports)
        self.assertFalse(bad_v["passed"])
        self.assertTrue(any("Bibliography update" in e for e in
                            bad_v["reference_transaction"]["errors"]))

        # Negative: wrong authoritative source label must fail.
        bad = copy.deepcopy(approved)
        bad["reference_transaction"]["updated_definition_expectations"][0][
            "find_text"] = "[34]"
        bad_v = cli.verify_apply_report(bad, _reports)
        self.assertFalse(bad_v["passed"])

    def test_same_anchor_insertions_keep_plan_order(self):
        instruction_lines = [
            "[T] 新增两条文献。",
            "`[33] New reference A.`",
            "`[34] New reference B.`",
        ]
        instruction = self.root / "insert_instructions.txt"
        instruction.write_text("\n".join(instruction_lines) + "\n", encoding="utf-8")
        base_source = {
            "source_file": str(instruction),
            "source_sha256": hashlib.sha256(instruction.read_bytes()).hexdigest(),
            "requirement_id": "REQ-T-INS",
            "source_locator": "L1–L3",
            "instruction_excerpt": "\n".join(instruction_lines),
            "instruction_action": "insert",
            "instruction_polarity": "affirmative",
        }
        def addition(op, text):
            return {
                "operation_id": op,
                "target_identity_mode": "legacy_p_idx_explicit",
                "document": "Manuscript", "p_idx": 1,
                "anchor_text": "References", "position": "after",
                "inserted_text": text, "reference_number": text[1:3],
                "linked_mention_ids": [],
                "authorized_change": {"intent": "Insert entry.",
                    "authorization_source": dict(base_source, instruction_object=text[:4]),
                    "explicitly_authorized_insertions": [
                        {"kind": "citation", "text": text[:4],
                         "instruction_requirement_id": "REQ-T-INS"}]},
            }
        plan, _graph = self._build(additions=[
            addition("REF-ADD-33", "[33] New reference A."),
            addition("REF-ADD-34", "[34] New reference B."),
        ])
        approved, reports, verification = self._apply_and_verify(plan)
        self.assertTrue(verification["passed"], verification)
        self.assertTrue(self._output_text(2).startswith("[33] New reference A."))
        self.assertTrue(self._output_text(3).startswith("[34] New reference B."))

        # Negative: swapped final insertion indices must fail the order check.
        bad = copy.deepcopy(approved)
        bad_reports = copy.deepcopy(reports)
        rows = [r for rep in bad_reports for r in rep.get("results", [])
                if r.get("action") == "insert_paragraph"]
        self.assertEqual(len(rows), 2)
        rows[0]["inserted_p_idx"], rows[1]["inserted_p_idx"] = (
            rows[1]["inserted_p_idx"], rows[0]["inserted_p_idx"])
        bad_v = cli.verify_apply_report(bad, bad_reports)
        self.assertFalse(bad_v["passed"])
        self.assertTrue(any("insertion order" in e for e in
                            bad_v["reference_transaction"]["errors"]))


if __name__ == "__main__":
    unittest.main()

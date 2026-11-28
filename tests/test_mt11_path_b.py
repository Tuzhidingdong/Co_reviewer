# -*- coding: utf-8 -*-
"""Synthetic end-to-end validation of path B for the MT-11 mixed-format citation case."""
from __future__ import annotations

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
from docx.oxml import OxmlElement
from docx.oxml.ns import qn as docx_qn

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402
from co_reviewer.reference_transaction import (  # noqa: E402
    ReferenceTransactionError, compile_reference_transaction,
)
from co_reviewer.v232_contract import (  # noqa: E402
    build_package_reference_graph, extract_docx_reference_segments,
)
from co_reviewer.document_engine import (  # noqa: E402
    NS, parse_xml, visible_text, run_plain_text, qn,
)
from co_reviewer.working_view import accepted_view_docx_bytes  # noqa: E402


def _add_run(paragraph, text, *, italic=False, superscript=False):
    run = paragraph.add_run(text)
    if italic:
        run.italic = True
    if superscript:
        rpr = run._element.get_or_add_rPr()
        val = OxmlElement("w:vertAlign")
        val.set(docx_qn("w:val"), "superscript")
        rpr.append(val)
    return run


class Mt11PathBTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manuscript = self.root / "manuscript.docx"
        self.marked = self.root / "manuscript_marked.docx"
        self.clean = self.root / "manuscript_clean.docx"
        self.si = self.root / "si.docx"

        # P[28]-like paragraph with an independent [35] Run, a superscript -1 Run
        # and an italic gamma Run.
        doc = Document()
        p = doc.add_paragraph()
        _add_run(p, "Claim alpha [33]. Claim beta [34]. Meanwhile, the development of the feature at 925 cm")
        _add_run(p, "-1", superscript=True)
        _add_run(p, " is assigned to the Fe–O–H bending vibration ")
        _add_run(p, "[35]")
        _add_run(p, ", confirming the continuous crystallization of ")
        _add_run(p, "γ", italic=True)
        _add_run(p, "-FeOOH components.")
        doc.add_paragraph("References")
        doc.add_paragraph("[33] Old reference A.")
        doc.add_paragraph("[34] Old reference B.")
        doc.add_paragraph("[35] Old reference C.")
        doc.save(self.manuscript)

        si_doc = Document()
        si_doc.add_paragraph("Supporting information.")
        si_doc.save(self.si)

        self.graph = build_package_reference_graph({
            "Manuscript": extract_docx_reference_segments(self.manuscript),
            "SI": extract_docx_reference_segments(self.si),
        })
        self.graph["source_files"] = {
            role: {"path": str(path.resolve()),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for role, path in (("Manuscript", self.manuscript), ("SI", self.si))
        }
        self.find = (
            "Meanwhile, the development of the feature at 925 cm-1 is assigned to "
            "the Fe–O–H bending vibration [35], confirming the continuous "
            "crystallization of γ-FeOOH components."
        )
        self.replace = (
            "Meanwhile, the broad band initially centered near 925 cm-1 "
            "(associated with surface-adsorbed water libration) attenuates, "
            "whereas the emergence of Fe–O–H bending vibrations near 1023 cm-1 "
            "[37] alongside the sulfate triplet confirms the progressive "
            "crystallization of γ-FeOOH."
        )
        self.p_idx = 0
        with zipfile.ZipFile(self.manuscript) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        paragraphs = root.findall(".//w:p", {"w": NS["w"]})
        self.source_text = visible_text(paragraphs[self.p_idx])
        self.find_start = self.source_text.find(self.find)
        self.assertGreaterEqual(self.find_start, 0)
        self.assertEqual(self.source_text.count(self.find), 1)

    def tearDown(self):
        self.temp.cleanup()

    def _build_spec(self):
        definitions = {
            str(row["number"]): row for row in self.graph["definitions"]
            if row.get("document") == "Manuscript" and row.get("kind") == "citation"
        }
        mentions = {
            str(row["number"]): row for row in self.graph["mentions"]
            if row.get("document") == "Manuscript"
            and row.get("kind") == "citation" and row.get("resolution") == "resolved"
        }
        labels = {"33": "A", "34": "B", "35": "C"}
        entry_updates, citation_updates = [], []
        # Dependency-safe authored order: consume original 35 before 33->35.
        for old, new in (("35", "37"), ("34", "36"), ("33", "35")):
            definition = definitions[old]
            entry_updates.append({
                "operation_id": "REF-ENTRY-%s" % old,
                "target_identity_mode": "legacy_p_idx_explicit",
                "definition_id": definition["id"], "document": "Manuscript",
                "p_idx": definition["paragraph_index"],
                "find": "[%s] Old reference %s." % (old, labels[old]),
                "replace": "[%s] Old reference %s." % (new, labels[old]),
                "new_number": new,
                "authorized_change": {"intent": "Renumber exact bibliography entry.",
                    "explicitly_authorized_tokens": [
                        {"kind": "citation", "text": "[%s]" % old,
                         "replacement": "[%s]" % new}]},
            })
            mention = mentions[old]
            citation_updates.append({
                "operation_id": "RT-CU-%s" % old,
                "target_identity_mode": "legacy_p_idx_explicit",
                "mention_id": mention["id"], "document": "Manuscript",
                "p_idx": mention["paragraph_index"],
                "find": mention["text"], "replace": new, "new_number": new,
                "authorized_change": {"intent": "Renumber exact graph-linked citation.",
                    "explicitly_authorized_tokens": [
                        {"kind": "number", "text": old, "replacement": new}]},
            })
        return {
            "transaction_id": "REF-TXN-MT11-PATHB",
            "reviewer": "Author",
            "comment": "Renumber three graph-linked citations; one owned by prose.",
            "proposed_response": "Each citation was written exactly once.",
            "source_comment_ids": ["MT-10"], "evidence_ids": ["E-MT11"],
            "documents": {
                "Manuscript": {"input_file": str(self.manuscript),
                               "output_file": str(self.marked)},
                "SI": {"input_file": str(self.si),
                       "output_file": str(self.root / "si_marked.docx")},
            },
            "additions": [], "entry_updates": entry_updates,
            "citation_updates": citation_updates,
        }

    def _build_plan(self, *, format_map=None, authorized=None):
        spec = self._build_spec()
        transaction_plan = compile_reference_transaction(spec, self.graph)
        ordinary_plan = compile_revision_plan({
            "documents": {"Manuscript": {
                "input_file": str(self.manuscript), "output_file": str(self.marked)}},
            "intents": [{
                "id": "MT-11", "doc_role": "Manuscript", "reviewer": "Human Author",
                "find": self.find, "replace": self.replace,
                "intent": "Rewrite prose and apply its approved citation renumbering.",
            }],
        })
        prose_target = ordinary_plan["revision_items"][0]["targets"][0]
        prose_target["authorized_change"] = authorized or {
            "intent": "Rewrite prose under the approved citation renumbering.",
            "explicitly_authorized_insertions": [
                {"kind": "citation", "text": "[37]"},
                {"kind": "quantity", "text": "1023 cm-1"},
            ],
            "explicitly_authorized_deletions": [{"kind": "citation", "text": "[35]"}],
        }
        script_out = self.replace.index("-1")
        script_out_2 = self.replace.index("-1", script_out + 2)
        if format_map is None:
            f35 = self.find.index("35") - 1
            r37 = self.replace.index("37") - 1
            gamma = self.find.index("γ")
            tail_start = r37 + 4
            gamma_out = self.replace.index("γ", tail_start)
            # Only gamma inherits the italic source Run; cm-1 script is
            # authorized explicitly by inline markup in the intent.
            format_map = [
                {"replacement_start": 0, "replacement_end": r37,
                 "source_offset": 0},
                {"replacement_start": r37, "replacement_end": r37 + 4,
                 "source_offset": f35},
                {"replacement_start": tail_start, "replacement_end": gamma_out,
                 "source_offset": 0},
                {"replacement_start": gamma_out, "replacement_end": gamma_out + 1,
                 "source_offset": gamma},
                {"replacement_start": gamma_out + 1, "replacement_end": len(self.replace),
                 "source_offset": 0},
            ]
        prose_target["replacement_format_map"] = format_map
        merged = json.loads(json.dumps(transaction_plan))
        merged["revision_items"].extend(json.loads(json.dumps(ordinary_plan["revision_items"])))
        return merged

    def _approve_apply_verify(self, plan):
        self.assertEqual(cli.validate_plan(plan, require_approval=False), [])
        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"], preflight)
        self.assertEqual(preflight["targets_total"], 7)
        self.assertEqual(preflight["targets_preflighted"], 7)
        self.assertEqual(preflight["targets_delegated"], 1)
        plan_path = self.root / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path), ids=[i["id"] for i in plan["revision_items"]],
            revision_ledger=None, actor="Unit Test Author")), 0)
        approved = json.loads(plan_path.read_text(encoding="utf-8"))
        apply_reports = cli.apply_plan(approved, "Unit Test Author")
        for item in approved["revision_items"]:
            item["status"] = "applied"
        verification = cli.verify_apply_report(approved, apply_reports)
        self.assertTrue(verification["passed"], verification)
        return preflight, apply_reports, verification

    def _paragraph_text(self, path, p_idx=0):
        payload = accepted_view_docx_bytes(path.read_bytes())
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            root = parse_xml(archive.read("word/document.xml"))
        return visible_text(root.findall(".//w:p", {"w": NS["w"]})[p_idx])

    def test_path_b_full_lifecycle_and_real_docx_output(self):
        plan = self._build_plan()
        preflight, apply_reports, verification = self._approve_apply_verify(plan)
        prose_target = plan["revision_items"][1]["targets"][0]
        delegations = prose_target.get("reference_citation_delegations", [])
        self.assertEqual(len(delegations), 1)
        self.assertEqual(delegations[0]["citation_operation_id"], "RT-CU-35")
        applied_ops = [r.get("operation_id") for rep in apply_reports for r in rep.get("results", [])]
        self.assertEqual(applied_ops.count("RT-CU-35"), 0)
        self.assertEqual(applied_ops.count("RT-CU-34"), 1)
        self.assertEqual(applied_ops.count("RT-CU-33"), 1)

        # Compile a real Clean DOCX from the Marked output.
        reports_path = self.root / "apply_reports.json"
        reports_path.write_text(json.dumps(apply_reports, ensure_ascii=False), encoding="utf-8")
        cli.cmd_compile_clean_from_marked(SimpleNamespace(
            marked=str(self.marked), out=str(self.clean),
            revision_ledger=None, apply_report=str(reports_path),
            revision_ids=None, report=None, intents=None, doc_map=None))
        self.assertTrue(self.clean.is_file())

        marked_text = self._paragraph_text(self.marked)
        clean_text = self._paragraph_text(self.clean)
        self.assertEqual(marked_text.count(self.replace), 1)
        self.assertEqual(clean_text.count(self.replace), 1)
        self.assertEqual(marked_text.count("[37]"), 1)
        # The only remaining [35] is the earlier citation renumbered 33->35.
        self.assertEqual(marked_text[marked_text.find(self.replace):].count("[35]"), 0)

        # Coordinates in both output views.
        for path in (self.marked, self.clean):
            text = self._paragraph_text(path)
            self.assertEqual(text.count(self.replace), 1)
            pos = text.find(self.replace) + cli._exact_token_positions(self.replace, "37")[0]
            self.assertEqual(text[pos:pos + 2], "37")
            self.assertEqual(text.index("37"), pos)

        # Reference Graph rebuilt from both outputs: number, offset and links.
        for path in (self.marked, self.clean):
            graph = build_package_reference_graph({
                "Manuscript": extract_docx_reference_segments(path)})
            mentions = {
                str(row["number"]): row for row in graph["mentions"]
                if row.get("document") == "Manuscript" and row.get("kind") == "citation"
            }
            self.assertEqual(set(mentions), {"35", "36", "37"})
            self.assertEqual(mentions["37"]["offset"],
                             self._paragraph_text(path).index("37"))
            self.assertEqual(
                mentions["37"]["target_definition_ids"],
                [next(d["id"] for d in graph["definitions"]
                      if d.get("document") == "Manuscript" and str(d.get("number")) == "37")],
            )

        # OOXML sanity: no nested w:ins/w:del, formats preserved.
        with zipfile.ZipFile(self.marked) as archive:
            root = parse_xml(archive.read("word/document.xml"))
        paragraph = root.findall(".//w:p", {"w": NS["w"]})[0]
        ins_nodes = paragraph.findall(".//w:ins", {"w": NS["w"]})
        del_nodes = paragraph.findall(".//w:del", {"w": NS["w"]})
        for inserted in ins_nodes:
            for deleted in del_nodes:
                self.assertFalse(deleted in list(inserted.iter()))
        superscript = {"superscript" for node in paragraph.findall(".//w:vertAlign", {"w": NS["w"]})
                       if node.get(docx_qn("w:val")) == "superscript"}
        self.assertTrue(superscript)
        self.assertTrue(paragraph.findall(".//w:i", {"w": NS["w"]}))

    def test_format_scope_fail_closed_variants(self):
        base = self._build_plan()
        f35 = self.find.index("35") - 1
        r37 = self.replace.index("37") - 1
        gamma = self.find.index("γ")

        # independent citation segment missing (merged into prose segment)
        bad = json.loads(json.dumps(base))
        bad["revision_items"][1]["targets"][0]["replacement_format_map"] = [
            {"replacement_start": 0, "replacement_end": len(self.replace),
             "source_offset": 0}]
        with self.assertRaisesRegex(ValueError, "FORMAT_SCOPE_BLOCKED"):
            cli.preflight_plan(bad)

        # wrong source_offset for the citation segment -> fails deeper contract
        bad = json.loads(json.dumps(base))
        bad["revision_items"][1]["targets"][0]["replacement_format_map"][1]["source_offset"] = gamma
        with self.assertRaises(ValueError):
            cli.preflight_plan(bad)

        # missing authorization for the citation surface -> delegation blocked
        bad = json.loads(json.dumps(base))
        bad["revision_items"][1]["targets"][0]["authorized_change"] = {
            "intent": "Rewrite prose only."}
        with self.assertRaises(ValueError):
            cli.preflight_plan(bad)

    def test_negative_anchor_offset_and_ownership_contracts(self):
        base = self._build_plan()
        target = base["revision_items"][1]["targets"][0]
        f35 = self.find.index("35") - 1
        r37 = self.replace.index("37") - 1
        gamma = self.find.index("γ")
        kwargs = dict(old_find=self.find, old_replace=self.replace,
                      old_at=f35 + 1, new_at=r37 + 1,
                      token="35", token_new="37",
                      find_start=self.find_start,
                      find_end=self.find_start + len(self.find))

        self.assertTrue(cli._mixed_format_citation_segment_is_exact(target, **kwargs))

        # source_offset pointing to a different Run must fail
        wrong = json.loads(json.dumps(target))
        cite_index = next(index for index, segment
                          in enumerate(wrong["replacement_format_map"])
                          if segment["replacement_start"] == r37)
        wrong["replacement_format_map"][cite_index]["source_offset"] = gamma
        self.assertFalse(cli._mixed_format_citation_segment_is_exact(wrong, **kwargs))

        # duplicated source citation token must fail at the ownership gate
        duplicated = json.loads(json.dumps(target))
        duplicated["find"] = self.find + " " + self.find
        self.assertFalse(cli._mixed_format_delegation_is_proven(duplicated, "35", "37"))

        # missing target citation token must fail at the ownership gate
        missing = json.loads(json.dumps(target))
        missing["replace"] = self.replace.replace("[37]", "[35]")
        self.assertFalse(cli._mixed_format_delegation_is_proven(missing, "35", "37"))

        # tampered delegation operation id must fail validation
        tampered = json.loads(json.dumps(base))
        cli.preflight_plan(tampered)
        tampered["revision_items"][1]["targets"][0]["reference_citation_delegations"][0][
            "citation_operation_id"] = "RT-CU-34"
        errors = cli.validate_plan(tampered, require_approval=False)
        self.assertTrue(errors, "tampered delegation operation_id must be rejected")


if __name__ == "__main__":
    unittest.main()

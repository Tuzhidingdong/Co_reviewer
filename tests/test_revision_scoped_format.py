# -*- coding: utf-8 -*-
"""Minimal regressions for format-only changes scoped inside existing w:ins revisions."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from lxml import etree

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.document_engine import NS, parse_xml, qn, visible_text  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


class RevisionScopedFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "marked.docx"
        self.output = self.root / "marked_format_fixed.docx"

        doc = Document()
        doc.add_paragraph("Claim: Instantaneous O2 speeds exceed 20 Å ps-1.")
        doc.add_paragraph("[33] de Faria. J. Raman Spectrosc. 1997, 28, 873-878.")
        doc.save(self.source)
        self._wrap_in_insertion(0, "Instantaneous O2")
        self._wrap_in_insertion(1, "1997, 28")

        self.sha = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def _wrap_in_insertion(self, p_idx, text):
        """Wrap the run containing `text` in a native w:ins revision."""
        with zipfile.ZipFile(self.source) as z:
            parts = {n: z.read(n) for n in z.namelist()}
        root = parse_xml(parts["word/document.xml"])
        para = root.findall(".//w:p", {"w": NS["w"]})[p_idx]
        target_run = next(
            r for r in para.findall("w:r", {"w": NS["w"]})
            if text in "".join(t.text or "" for t in r.findall("w:t", {"w": NS["w"]}))
        )
        parent = target_run.getparent()
        index = parent.index(target_run)
        ins = etree.Element(qn("ins"), {qn("id"): "900", qn("author"): "Human Author",
                                        qn("date"): "2026-11-25T00:00:00Z"})
        parent.remove(target_run)
        ins.append(target_run)
        parent.insert(index, ins)
        parts["word/document.xml"] = etree.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(self.source, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in parts.items():
                z.writestr(name, data)

    def _target(self, tid, p_idx, find, replace, properties, scope=None, offset=None,
                flag=True, op_find=None):
        operation = {"find": op_find or find, "properties": properties}
        if scope is not None:
            operation["scope_find"] = scope
            operation["scope_offset"] = offset
        target = {
            "document": "Manuscript", "input_file": str(self.source),
            "output_file": str(self.output), "part": "word/document.xml",
            "p_idx": p_idx, "source_sha256": self.sha,
            "find": find, "replace": replace,
            "authorized_change": {"intent": "Authorized scoped format remediation."},
            "format_authorization": {"intent": "Authorized scoped format remediation."},
            "format_operations": [operation],
        }
        if flag:
            target["revision_scoped_format_change"] = True
        return {"id": tid, "reviewer": "Human Author", "comment": "Scoped format remediation",
                "status": "planned", "targets": [target]}

    def _plan(self, *targets):
        return {"schema_version": "1.0", "approved": False,
                "revision_items": list(targets)}

    def _run(self, plan, *, apply=False):
        self.assertEqual(cli.validate_plan(plan, require_approval=False), [])
        self.assertTrue(cli.preflight_plan(plan)["passed"])
        if not apply:
            return None
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

    def test_scoped_subscript_and_bold_inside_revisions(self):
        plan = self._plan(
            self._target("MT-06-FORMAT", 0, "Instantaneous O2", "Instantaneous O2",
                         {"vertAlign": "subscript"}, scope="Instantaneous O2", offset=15,
                         op_find="2"),
            self._target("MT-10-FORMAT", 1, "28", "28", {"b": True}),
        )
        approved, reports, verification = self._run(plan, apply=True)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["targets_failed"], 0)

        with zipfile.ZipFile(self.output) as z:
            root = parse_xml(z.read("word/document.xml"))
        paras = root.findall(".//w:p", {"w": NS["w"]})
        # O2 digit is subscript, O is ordinary.
        runs = paras[0].findall(".//w:r", {"w": NS["w"]})
        for run in runs:
            text = "".join(t.text or "" for t in run.findall("w:t", {"w": NS["w"]}))
            vertical = run.find("w:rPr/w:vertAlign", {"w": NS["w"]})
            align = vertical.get(qn("val")) if vertical is not None else None
            if text == "2":
                self.assertEqual(align, "subscript")
            if text.startswith("Instantaneous O"):
                self.assertIn(align, (None, "baseline"))
        # Volume 28 is bold with a native format-change snapshot.
        bold_run = next(
            r for r in paras[1].findall(".//w:r", {"w": NS["w"]})
            if "".join(t.text or "" for t in r.findall("w:t", {"w": NS["w"]})) == "28"
        )
        self.assertIsNotNone(bold_run.find("w:rPr/w:b", {"w": NS["w"]}))
        self.assertIsNotNone(bold_run.find("w:rPr/w:rPrChange", {"w": NS["w"]}))
        self.assertEqual(visible_text(paras[0]).count("Instantaneous O2"), 1)

    def test_fail_closed_variants(self):
        good = self._target("MT-06-FORMAT", 0, "Instantaneous O2", "Instantaneous O2",
                            {"vertAlign": "subscript"}, scope="Instantaneous O2", offset=15,
                            op_find="2")
        # No explicit flag -> generic revision gate must refuse.
        no_flag = copy.deepcopy(good)
        no_flag["targets"][0].pop("revision_scoped_format_change")
        try:
            cli.preflight_plan(self._plan(no_flag))
            self.fail("missing flag must fail closed")
        except ValueError as exc:
            self.assertIn("HISTORICAL_REVISION_OVERLAP", str(exc))

        # Text-changing target may not use the flag.
        text_change = copy.deepcopy(good)
        text_change["targets"][0]["replace"] = "Instantaneous O3"
        self.assertTrue(any("revision_scoped_format_change" in e
                            for e in cli.validate_plan(self._plan(text_change), require_approval=False)))

        # Wrong scope offset must not pass validate.
        wrong_scope = copy.deepcopy(good)
        wrong_scope["targets"][0]["format_operations"][0]["scope_offset"] = 14
        self.assertTrue(any("scope_offset" in e
                            for e in cli.validate_plan(self._plan(wrong_scope), require_approval=False)))


if __name__ == "__main__":
    unittest.main()

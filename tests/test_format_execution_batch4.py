# -*- coding: utf-8 -*-
"""Batch 4: Preflight, real Apply, independent Verify and Final Gate for FRs."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from lxml import etree as ET  # noqa: E402

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.document_engine import NS, parse_xml, qn, visible_text  # noqa: E402
from co_reviewer.format_compiler import compile_format_requirements  # noqa: E402
from co_reviewer.format_execution import (  # noqa: E402
    assign_target_occurrences, build_execution_record, format_apply_preflight,
    format_final_gate_guard, recompute_properties, target_occurrence_id,
    validate_target_occurrences, verify_format_execution,
)
from co_reviewer.format_requirements import semantic_fingerprint, version_sha256  # noqa: E402
from co_reviewer.instruction_analysis import analysis_sha256  # noqa: E402
from co_reviewer.revision_models import revision_item_fingerprint  # noqa: E402
from co_reviewer.run_format_analysis import analyze_docx  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
FR_ID = "FR-REQ-T-01-01"
VERSION_ID = f"{FR_ID}-v1"


class FormatExecutionBatch4Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docx = self.root / "source.docx"
        self.output = self.root / "marked.docx"

    def tearDown(self):
        self.temp.cleanup()

    def _build_docx(self, runs, table=False, numbering=False):
        body = "".join(f'<w:r>{rpr}<w:t>{text}</w:t></w:r>' for text, rpr in runs)
        ppr = ""
        if numbering:
            ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        paragraph = f'<w:p>{ppr}{body}</w:p>'
        if table:
            paragraph = '<w:tbl><w:tr><w:tc>' + paragraph + '</w:tc></w:tr></w:tbl>'
        document = f'<w:document xmlns:w="{W}"><w:body>{paragraph}</w:body></w:document>'
        styles = (
            f'<w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:styleId="P2">'
            '<w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/></w:rPr></w:style>'
            '</w:styles>'
        )
        theme = (
            f'<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme name="s">'
            '<a:majorFont><a:latin typeface="M"/></a:majorFont>'
            '<a:minorFont><a:latin typeface="m"/></a:minorFont>'
            '</a:fontScheme></a:themeElements></a:theme>'
        )
        with zipfile.ZipFile(self.docx, "w") as z:
            z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
            z.writestr("word/document.xml", document)
            z.writestr("word/styles.xml", styles)
            z.writestr("word/theme/theme1.xml", theme)

    def _fr_version(self, token="token-A", properties=None):
        version = {
            "format_requirement_id": FR_ID, "version_id": VERSION_ID,
            "parent_version_id": None, "parent_fingerprint": None,
            "instruction_id": "T-01", "source_line_start": 1, "source_line_end": 1,
            "source_locator": "L1–L1", "source_excerpt": "T-01: superscript token-A.",
            "document_role": "Manuscript",
            "target_ref": {"part": "word/document.xml", "p_idx": 0,
                           "revision_item_id": "REV-1", "target_index": 0},
            "token": token,
            "properties": properties if properties is not None else {"vertAlign": "superscript"},
            "scope": {"replacement": "token-A value", "start": 0, "end": 7, "occurrence_index": 0},
            "semantic_role": "exponent", "status": "confirmed", "created_by": "agent",
            "created_at_utc": "2026-11-28T00:00:00Z", "reason": "synthetic FR",
            "reviewer_decision_ref": None, "authorization_ref": None,
        }
        version["fingerprint"] = semantic_fingerprint(version)
        version["version_sha256"] = version_sha256(version)
        return version

    def _analysis(self):
        payload = {
            "schema_version": "1.0",
            "source_instruction": {"file": str(self.root / "instructions.txt"),
                                   "sha256": hashlib.sha256(b"T-01\n").hexdigest()},
            "analyses": [{
                "requirement_id": "REQ-T-01-01", "instruction_action": "replace",
                "instruction_polarity": "affirmative", "instruction_object": "token-A",
                "source_line_start": 1, "source_line_end": 1, "source_locator": "L1–L1",
                "instruction_excerpt": "T-01: superscript token-A.",
            }],
            "format_requirements": [{
                "schema_version": "1.0", "format_requirement_id": FR_ID,
                "requirement_id": "REQ-T-01-01", "current_version_id": VERSION_ID,
                "versions": [self.fr_version],
            }],
        }
        payload["analysis_sha256"] = analysis_sha256(payload)
        return payload

    def _coverage(self):
        return {
            "schema_version": "1.1",
            "source_instruction": dict(self.analysis["source_instruction"]),
            "requirements": [{
                "requirement_id": "REQ-T-01-01", "task_id": "T-01",
                "instruction_action": "replace", "instruction_polarity": "affirmative",
                "instruction_object": "token-A",
                "source_line_start": 1, "source_line_end": 1, "source_locator": "L1–L1",
                "instruction_excerpt": "T-01: superscript token-A.", "status": "planned",
                "format_requirement_ids": [FR_ID],
                "revision_targets": [
                    {"revision_item_id": "REV-1", "target_index": 0},
                    {"revision_item_id": "REV-1", "target_index": 1},
                ],
            }],
        }

    def _plan(self):
        sha = hashlib.sha256(self.docx.read_bytes()).hexdigest()
        return {
            "schema_version": "1.0", "approved": False,
            "revision_items": [{
                "id": "REV-1", "reviewer": "Author", "comment": "T-01",
                "status": "planned", "targets": [{
                    "target_id": "TGT-TEXT", "document": "Manuscript",
                    "input_file": str(self.docx), "output_file": str(self.output),
                    "part": "word/document.xml", "p_idx": 0, "source_sha256": sha,
                    "find": "token-A", "replace": "token-A value",
                    "instruction_requirement_ids": ["REQ-T-01-01"],
                    "source_comment_ids": [],
                    "authorized_change": {"intent": "T-01 authorized format change"},
                }],
            }],
        }

    def _compile(self):
        return compile_format_requirements(self.plan, self.analysis, self.coverage, self.run_analysis)

    def _inputs(self, runs=None, properties=None, fr_token=None):
        self._build_docx(runs or [("Use ", ""), ("token-A", ""), (" safely.", "")])
        self.fr_version = self._fr_version(
            token=fr_token or "token-A",
            properties=properties or {"vertAlign": "superscript"},
        )
        self.analysis = self._analysis()
        self.coverage = self._coverage()
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = recompute_properties(self._compile(), docx_path=self.docx)
        return assign_target_occurrences(trace, docx_path=self.docx)

    def _authorize(self, plan):
        for item in plan["revision_items"]:
            item["approval_fingerprint"] = revision_item_fingerprint(item)
            item["status"] = "approved"
        plan["authorization_mode"] = "author_approval"
        plan["approved"] = True
        plan["author_approval"] = cli._human_author_approval_record(
            plan, [item["id"] for item in plan["revision_items"]], "Neutral Test"
        )
        return plan

    def _preflight(self, trace, plan=None):
        return format_apply_preflight(
            plan=plan or self._authorize(copy.deepcopy(self.plan)),
            analysis=self.analysis, coverage=self.coverage,
            run_analysis=self.run_analysis, trace=trace, docx_path=self.docx,
        )

    # ---------------- A. RECOMPUTE ----------------
    def test_recompute_from_paragraph_style_context(self):
        trace = self._inputs()
        styled = self.root / "styled.docx"
        with zipfile.ZipFile(self.docx) as src, zipfile.ZipFile(styled, "w") as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "word/document.xml":
                    root = parse_xml(payload)
                    paragraph = root.findall(".//w:p", NS)[0]
                    ppr = ET.SubElement(paragraph, qn("pPr"))
                    ET.SubElement(ppr, qn("pStyle")).set(qn("val"), "P2")
                    payload = ET.tostring(root)
                dst.writestr(name, payload)
        run_analysis = analyze_docx(styled)
        trace["results"][0]["property_decisions"]["rFonts"] = "RECOMPUTE"
        resolved = recompute_properties(trace, docx_path=styled)
        record = resolved["results"][0]["recompute"]
        self.assertEqual(record["status"], "RESOLVED", record)
        self.assertEqual(record["properties"]["rFonts"]["value"], {
            "ascii": "Times New Roman", "hAnsi": "Times New Roman",
        })
        self.assertEqual(record["rule_version"], "recompute/1.0")
        self.assertTrue(any(op.get("recomputed") for op in resolved["results"][0]["format_operations"]))

    def test_recompute_is_deterministic(self):
        trace = self._inputs()
        trace["results"][0]["property_decisions"]["sz"] = "RECOMPUTE"
        a = recompute_properties(trace, docx_path=self.docx)
        b = recompute_properties(trace, docx_path=self.docx)
        self.assertEqual(a["compile_fingerprint"], b["compile_fingerprint"])

    def test_recompute_missing_context_blocked(self):
        trace = self._inputs()
        trace["results"][0]["property_decisions"]["rFonts"] = "RECOMPUTE"
        missing = self.root / "no_styles.docx"
        with zipfile.ZipFile(self.docx) as src, zipfile.ZipFile(missing, "w") as dst:
            for name in src.namelist():
                if name != "word/styles.xml":
                    dst.writestr(name, src.read(name))
        resolved = recompute_properties(trace, docx_path=missing)
        self.assertEqual(resolved["status"], "BLOCKED")
        self.assertTrue(resolved["recompute_blockers"])

    def test_unresolved_recompute_blocks_preflight(self):
        trace = self._inputs()
        trace["results"][0]["recompute"] = {"status": "BLOCKED"}
        preflight = self._preflight(trace)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("unresolved RECOMPUTE" in b for b in preflight["blockers"]))

    # ---------------- B. TARGET OCCURRENCE ----------------
    def test_target_occurrence_is_stable_and_recomputable(self):
        trace = self._inputs()
        first = trace["results"][0]["target_occurrence_id"]
        self.assertEqual(validate_target_occurrences(trace, docx_path=self.docx), [])
        again = assign_target_occurrences(trace, docx_path=self.docx)
        self.assertEqual(again["results"][0]["target_occurrence_id"], first)

    def test_same_text_in_different_paragraphs_is_distinct(self):
        self._build_docx([("token-A", ""), ("token-A", "")])
        self.run_analysis = __import__(
            "co_reviewer.run_format_analysis", fromlist=["analyze_docx"]
        ).analyze_docx(self.docx)
        parts = hashlib.sha256(self.docx.read_bytes()).hexdigest()
        args = dict(docx_sha256=parts, part="word/document.xml",
                    source_start=0, source_end=7, run_start=0, run_end=0,
                    find_text="token-A", structure={"in_table": False, "in_numbering": False})
        first = target_occurrence_id(p_idx=0, **args)
        second = target_occurrence_id(p_idx=1, **args)
        self.assertNotEqual(first, second)

    def test_multi_fr_same_target_compatible(self):
        trace = self._inputs()
        second = copy.deepcopy(trace["results"][0])
        second["format_requirement_id"] = "FR-REQ-T-01-02"
        second["format_operations"] = [dict(second["format_operations"][0],
                                            properties={"b": True}, operation_id="other")]
        trace["results"].append(second)
        self.assertEqual(validate_target_occurrences(trace, docx_path=self.docx), [])

    def test_multi_fr_same_target_conflicting_values_blocked(self):
        trace = self._inputs()
        second = copy.deepcopy(trace["results"][0])
        second["format_requirement_id"] = "FR-REQ-T-01-02"
        second["format_operations"] = [dict(second["format_operations"][0],
                                            properties={"vertAlign": "subscript"},
                                            operation_id="other")]
        trace["results"].append(second)
        errors = validate_target_occurrences(trace, docx_path=self.docx)
        self.assertTrue(any("conflicting format value" in e for e in errors), errors)

    def test_different_replacement_same_target_blocked(self):
        trace = self._inputs()
        second = copy.deepcopy(trace["results"][0])
        second["format_requirement_id"] = "FR-REQ-T-01-02"
        second["replace"] = "different"
        trace["results"].append(second)
        errors = validate_target_occurrences(trace, docx_path=self.docx)
        self.assertTrue(any("different replacement text" in e for e in errors), errors)

    def test_old_occurrence_invalid_after_docx_change(self):
        trace = self._inputs()
        original = copy.deepcopy(trace)
        self._build_docx([("Use ", ""), ("token-A!", ""), (" safely.", "")])
        errors = validate_target_occurrences(original, docx_path=self.docx)
        self.assertTrue(any("does not match current DOCX" in e for e in errors), errors)

    # ---------------- C. PREFLIGHT ----------------
    def test_preflight_passes_valid_inputs(self):
        trace = self._inputs()
        plan = self._authorize(copy.deepcopy(self.plan))
        preflight = format_apply_preflight(
            plan=plan, analysis=self.analysis, coverage=self.coverage,
            run_analysis=self.run_analysis, trace=trace, docx_path=self.docx,
        )
        self.assertTrue(preflight["passed"], preflight["blockers"])
        self.assertEqual(preflight["fr_status"]["FR-REQ-T-01-01"], "COMPILED")

    def test_preflight_blocks_sha_mismatch(self):
        trace = self._inputs()
        plan = self._authorize(copy.deepcopy(self.plan))
        plan["revision_items"][0]["targets"][0]["source_sha256"] = "0" * 64
        preflight = self._preflight(trace, plan=plan)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("SHA mismatch" in b for b in preflight["blockers"]))

    def test_preflight_blocks_stale_run_analysis(self):
        trace = self._inputs()
        self.run_analysis["source_docx_sha256"] = "0" * 64
        preflight = self._preflight(trace)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("Run Analysis source DOCX SHA" in b for b in preflight["blockers"]))

    def test_preflight_blocks_missing_map_and_ops(self):
        trace = self._inputs()
        trace["results"][0]["replacement_format_map"] = []
        trace["results"][0]["format_operations"] = []
        preflight = self._preflight(trace)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("replacement_format_map missing" in b for b in preflight["blockers"]))
        self.assertTrue(any("no Pass-2 format operation" in b for b in preflight["blockers"]))

    def test_preflight_blocks_unsafe_base_run(self):
        trace = self._inputs()
        self.run_analysis["runs"][1]["safety_classification"] = "UNSAFE_SCRIPT_FORMAT"
        preflight = self._preflight(trace)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("unsafe base Run" in b for b in preflight["blockers"]))

    def test_preflight_blocks_trace_tamper(self):
        trace = self._inputs()
        trace["results"][0]["token"] = "changed"
        preflight = self._preflight(trace)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("Compile Trace" in b for b in preflight["blockers"]))

    def test_preflight_blocks_missing_authorization(self):
        trace = self._inputs()
        plan = copy.deepcopy(self.plan)
        plan["authorization_mode"] = "instruction_based"
        preflight = self._preflight(trace, plan=plan)
        self.assertFalse(preflight["passed"])
        self.assertTrue(any("authorization receipt" in b for b in preflight["blockers"]))

    # ---------------- D. REAL APPLY / VERIFY / GATE ----------------
    def _full_chain(self, trace=None):
        from co_reviewer.format_compiler import apply_compiled_formats
        trace = trace if trace is not None else self._inputs()
        plan = self._authorize(apply_compiled_formats(copy.deepcopy(self.plan), trace))
        errors = cli.validate_plan(plan, require_approval=False)
        self.assertEqual(errors, [], errors)
        reports = cli.apply_plan(plan, "Neutral Test")
        self.assertTrue(self.output.exists())
        record = build_execution_record(
            preflight={"passed": True, "status": "PASSED", "docx_sha256": hashlib.sha256(self.docx.read_bytes()).hexdigest()},
            trace=trace, apply_reports=reports,
        )
        verification = verify_format_execution(
            docx_path=self.output, analysis=self.analysis, trace=trace,
            original_docx=self.docx, execution_record=record,
        )
        gate = format_final_gate_guard(
            plan={"format_compilation": trace}, analysis=self.analysis,
            trace=trace, format_verification=verification,
            authorization={"authorization_sha256": "x"},
        )
        return trace, reports, record, verification, gate

    def _output_paragraph(self):
        with zipfile.ZipFile(self.output) as z:
            root = parse_xml(z.read("word/document.xml"))
        return root.findall(".//w:p", NS)[0]

    def test_end_to_end_real_apply_verify_and_gate(self):
        trace, reports, record, verification, gate = self._full_chain()
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["results"][0]["status"], "VERIFIED")
        self.assertTrue(gate["passed"], gate)
        # Real DOCX evidence: the token Run carries w:vertAlign=superscript.
        paragraph = self._output_paragraph()
        from co_reviewer.document_engine import run_plain_text
        aligned = [run for run in paragraph.findall(".//w:r", NS)
                   if run.find("w:rPr/w:vertAlign", NS) is not None]
        self.assertTrue(aligned)
        aligned_text = "".join(run_plain_text(run) or "" for run in aligned)
        self.assertIn("token-A", aligned_text)
        for run in aligned:
            self.assertEqual(run.find("w:rPr/w:vertAlign", NS).get(qn("val")), "superscript")
        self.assertEqual(record["requirements"][0]["execution_status"], "APPLIED")
        self.assertEqual(hashlib.sha256(self.docx.read_bytes()).hexdigest(),
                         hashlib.sha256(self.docx.read_bytes()).hexdigest())

    def test_verify_detects_missing_format_in_actual_docx(self):
        trace, _reports, record, _verification, _gate = self._full_chain()
        mutated = self.root / "mutated.docx"
        with zipfile.ZipFile(self.output) as src, zipfile.ZipFile(mutated, "w") as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "word/document.xml":
                    root = parse_xml(payload)
                    for run in root.findall(".//w:r", NS):
                        rpr = run.find("w:rPr", NS)
                        if rpr is not None:
                            align = rpr.find("w:vertAlign", NS)
                            if align is not None:
                                rpr.remove(align)
                    payload = __import__("lxml.etree", fromlist=["tostring"]).tostring(root)
                dst.writestr(name, payload)
        result = verify_format_execution(
            docx_path=mutated, analysis=self.analysis, trace=trace,
            original_docx=self.docx, execution_record=record,
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["results"][0]["status"], "FAILED")
        self.assertTrue(any("vertAlign" in e for e in result["results"][0]["errors"]))

    def test_verify_detects_wrong_format_value(self):
        trace, _reports, record, _verification, _gate = self._full_chain()
        mutated = self.root / "wrong.docx"
        with zipfile.ZipFile(self.output) as src, zipfile.ZipFile(mutated, "w") as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "word/document.xml":
                    root = parse_xml(payload)
                    for run in root.findall(".//w:r", NS):
                        align = run.find("w:rPr/w:vertAlign", NS)
                        if align is not None:
                            align.set(qn("val"), "subscript")
                    payload = __import__("lxml.etree", fromlist=["tostring"]).tostring(root)
                dst.writestr(name, payload)
        result = verify_format_execution(
            docx_path=mutated, analysis=self.analysis, trace=trace,
            original_docx=self.docx, execution_record=record,
        )
        self.assertEqual(result["results"][0]["status"], "FAILED")

    def test_verify_detects_non_target_text_change(self):
        trace, _reports, record, _verification, _gate = self._full_chain()
        mutated = self.root / "text_changed.docx"
        with zipfile.ZipFile(self.output) as src, zipfile.ZipFile(mutated, "w") as dst:
            for name in src.namelist():
                payload = src.read(name)
                if name == "word/document.xml":
                    payload = payload.replace(b" safely.", b" UNSAFE.")
                dst.writestr(name, payload)
        result = verify_format_execution(
            docx_path=mutated, analysis=self.analysis, trace=trace,
            original_docx=self.docx, execution_record=record,
        )
        self.assertFalse(result["results"][0]["non_target_text_preserved"])

    # ---------------- F. FINAL GATE PROTECTION ----------------
    def test_gate_blocks_failed_and_missing_fr(self):
        trace, _reports, _record, verification, _gate = self._full_chain()
        verification["results"][0]["status"] = "FAILED"
        guard = format_final_gate_guard(
            plan={"format_compilation": trace}, analysis=self.analysis,
            trace=trace, format_verification=verification,
            authorization={"authorization_sha256": "x"},
        )
        self.assertFalse(guard["passed"])
        self.assertTrue(any("is FAILED" in b for b in guard["blockers"]))
        verification["results"] = []
        guard = format_final_gate_guard(
            plan={"format_compilation": trace}, analysis=self.analysis,
            trace=trace, format_verification=verification,
            authorization={"authorization_sha256": "x"},
        )
        self.assertTrue(any("no VERIFIED evidence" in b for b in guard["blockers"]))

    def test_gate_repair_cannot_change_semantics_or_delete_ops(self):
        trace, _reports, _record, verification, _gate = self._full_chain()
        changed_fr = copy.deepcopy(self.analysis)
        version = changed_fr["format_requirements"][0]["versions"][0]
        version["token"] = "changed"
        version["fingerprint"] = semantic_fingerprint(version)
        guard = format_final_gate_guard(
            plan={"format_compilation": trace}, analysis=changed_fr,
            trace=trace, format_verification=verification,
            authorization={"authorization_sha256": "x"},
        )
        self.assertTrue(any("semantic fingerprint changed" in b for b in guard["blockers"]))
        reduced = copy.deepcopy(trace)
        reduced["results"][0]["format_operations"] = []
        guard = format_final_gate_guard(
            plan={"format_compilation": trace}, analysis=self.analysis,
            trace=reduced, format_verification=verification,
            authorization={"authorization_sha256": "x"},
        )
        self.assertTrue(any("Pass-2 operations were removed" in b for b in guard["blockers"]))

    def test_final_gate_cli_honours_format_guard(self):
        trace, _reports, _record, verification, gate = self._full_chain()
        guard_path = self.root / "format_gate_report.json"
        guard_path.write_text(json.dumps(gate, ensure_ascii=False), encoding="utf-8")
        report = __import__("co_reviewer.final_gate", fromlist=["evaluate_final_gate"]).evaluate_final_gate(
            plan=None, verify_report={"passed": True, "targets_failed": 0},
            docs=[], format_gate=gate,
        )
        self.assertEqual(report["verdict"], "BLOCKED")
        gate["passed"] = True
        gate["fr_status"] = {"FR-REQ-T-01-01": "VERIFIED"}
        report = __import__("co_reviewer.final_gate", fromlist=["evaluate_final_gate"]).evaluate_final_gate(
            plan=None, verify_report={"passed": True, "targets_failed": 0},
            docs=[], format_gate=gate,
        )
        self.assertNotIn("Format execution gate failed", str(report["blockers"]))


    # ---------------- CLI / STRUCTURE INTEGRATION ----------------
    def _cli_bundle(self, trace):
        from co_reviewer.format_compiler import apply_compiled_formats
        plan = self._authorize(apply_compiled_formats(copy.deepcopy(self.plan), trace))
        paths = {
            "plan": self.root / "plan_round.json",
            "analysis": self.root / "analysis_round.json",
            "coverage": self.root / "coverage_round.json",
            "run": self.root / "run_round.json",
            "apply_report": self.root / "apply_round.json",
            "verify_report": self.root / "verify_round.json",
            "preflight": self.root / "preflight_round.json",
            "record": self.root / "execution_round.json",
            "format_verify": self.root / "format_verify_round.json",
        }
        paths["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        paths["analysis"].write_text(json.dumps(self.analysis, ensure_ascii=False), encoding="utf-8")
        paths["coverage"].write_text(json.dumps(self.coverage, ensure_ascii=False), encoding="utf-8")
        paths["run"].write_text(json.dumps(self.run_analysis, ensure_ascii=False), encoding="utf-8")
        return plan, paths

    def test_cli_apply_and_verify_with_preflight(self):
        trace = self._inputs()
        plan, paths = self._cli_bundle(trace)
        apply_args = SimpleNamespace(
            plan=str(paths["plan"]), author="Neutral Test", report=str(paths["apply_report"]),
            revision_ledger=None, instruction_coverage=str(paths["coverage"]),
            instruction_authorization=None,
            instruction_analysis=None, format_analysis=str(paths["analysis"]),
            format_run_analysis=str(paths["run"]), format_preflight_out=str(paths["preflight"]),
            format_execution_record_out=str(paths["record"]),
        )
        self.assertEqual(cli.cmd_apply(apply_args), 0)
        preflight = json.loads(paths["preflight"].read_text(encoding="utf-8"))
        record = json.loads(paths["record"].read_text(encoding="utf-8"))
        self.assertTrue(preflight["passed"], preflight["blockers"])
        self.assertEqual(record["requirements"][0]["execution_status"], "APPLIED")
        verify_args = SimpleNamespace(
            plan=str(paths["plan"]), apply_report=str(paths["apply_report"]),
            out=str(paths["verify_report"]), instruction_coverage=None,
            instruction_authorization=None, instruction_analysis=None,
            format_analysis=str(paths["analysis"]),
            format_execution_record=str(paths["record"]),
            format_verification_out=str(paths["format_verify"]),
        )
        self.assertEqual(cli.cmd_verify(verify_args), 0)
        format_verification = json.loads(paths["format_verify"].read_text(encoding="utf-8"))
        self.assertEqual(format_verification["results"][0]["status"], "VERIFIED")
        self.assertTrue(paths["verify_report"].is_file())

    def test_cli_apply_preflight_blocks_stale_analysis(self):
        trace = self._inputs()
        plan, paths = self._cli_bundle(trace)
        stale = copy.deepcopy(self.run_analysis)
        stale["source_docx_sha256"] = "0" * 64
        paths["run"].write_text(json.dumps(stale, ensure_ascii=False), encoding="utf-8")
        apply_args = SimpleNamespace(
            plan=str(paths["plan"]), author="Neutral Test", report=str(paths["apply_report"]),
            revision_ledger=None, instruction_coverage=str(paths["coverage"]),
            instruction_authorization=None,
            instruction_analysis=None, format_analysis=str(paths["analysis"]),
            format_run_analysis=str(paths["run"]), format_preflight_out=str(paths["preflight"]),
            format_execution_record_out=None,
        )
        with self.assertRaises(ValueError) as caught:
            cli.cmd_apply(apply_args)
        self.assertIn("Format Apply Preflight BLOCKED", str(caught.exception))
        self.assertFalse(paths["apply_report"].exists())

    def test_apply_cross_run_and_partial_run_targets(self):
        for index, runs in enumerate((
            [("Use ", ""), ("token", ""), ("-A", ""), (" safely.", "")],
            [("Use token-A safely.", "")],
        )):
            with self.subTest(runs=runs):
                self.output = self.root / f"marked_{index}.docx"
                self._build_docx(runs)
                self.fr_version = self._fr_version()
                self.analysis = self._analysis()
                self.coverage = self._coverage()
                self.run_analysis = analyze_docx(self.docx)
                self.plan = self._plan()
                trace = assign_target_occurrences(
                    recompute_properties(self._compile(), docx_path=self.docx),
                    docx_path=self.docx,
                )
                _trace, _reports, _record, verification, gate = self._full_chain()
                self.assertTrue(verification["passed"], verification)
                self.assertTrue(gate["passed"], gate)

    def test_apply_inside_table_and_numbering(self):
        self._build_docx([("Use ", ""), ("token-A", ""), (" safely.", "")],
                         table=True, numbering=True)
        self.fr_version = self._fr_version()
        self.analysis = self._analysis()
        self.coverage = self._coverage()
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = assign_target_occurrences(
            recompute_properties(self._compile(), docx_path=self.docx), docx_path=self.docx,
        )
        self.assertTrue(trace["results"][0]["target_occurrence"]["structure_context"]["in_table"])
        # The existing engine fail-closes table format changes without explicit
        # table-style resolution; Batch 4 must not bypass that guard.
        from co_reviewer.format_compiler import apply_compiled_formats
        plan = self._authorize(apply_compiled_formats(copy.deepcopy(self.plan), trace))
        with self.assertRaises(ValueError) as caught:
            cli.apply_plan(plan, "Neutral Test")
        self.assertIn("FORMAT_CHANGE_TABLE_STYLE_RESOLUTION_REQUIRED", str(caught.exception))
        self.assertFalse(self.output.exists())

    def test_instruction_based_receipt_and_fr_anchor_are_checked(self):
        from co_reviewer.format_compiler import apply_compiled_formats
        from co_reviewer.identity import revision_targets_fingerprint
        from co_reviewer.instruction_coverage import (
            authorize_instruction_based_plan, instruction_coverage_sha256,
        )
        trace = self._inputs()
        instruction = self.root / "instruction.txt"
        instruction.write_text("T-01: superscript token-A.\n", encoding="utf-8")
        self.analysis["source_instruction"] = {
            "file": str(instruction),
            "sha256": hashlib.sha256(instruction.read_bytes()).hexdigest(),
        }
        self.analysis["analysis_sha256"] = analysis_sha256(self.analysis)
        self.coverage["source_instruction"] = dict(self.analysis["source_instruction"])
        self.coverage["coverage_sha256"] = instruction_coverage_sha256(self.coverage)
        plan = apply_compiled_formats(copy.deepcopy(self.plan), trace)
        plan["approved"] = True
        plan["authorization_mode"] = "instruction_based"
        for item in plan["revision_items"]:
            item["status"] = "approved"
            item["authorization_mode"] = "instruction_based"
            for target in item["targets"]:
                target["instruction_requirement_ids"] = ["REQ-T-01-01"]
                target["source_sha256"] = hashlib.sha256(self.docx.read_bytes()).hexdigest()
            item["preflight_fingerprint"] = revision_targets_fingerprint(item)
        paths = {
            "plan": self.root / "ib_plan.json", "coverage": self.root / "ib_coverage.json",
            "analysis": self.root / "ib_analysis.json", "assessment": self.root / "ib_assessment.json",
        }
        paths["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        paths["coverage"].write_text(json.dumps(self.coverage, ensure_ascii=False), encoding="utf-8")
        paths["analysis"].write_text(json.dumps(self.analysis, ensure_ascii=False), encoding="utf-8")
        assessment = {
            "schema_version": "1.0", "status": "eligible",
            "preflight_plan_sha256": hashlib.sha256(paths["plan"].read_bytes()).hexdigest(),
            "source_instruction_sha256": self.analysis["source_instruction"]["sha256"],
            "coverage_sha256": self.coverage["coverage_sha256"],
            "reviewed_requirement_ids": ["REQ-T-01-01"],
            "reviewed_target_refs": [
                {"revision_item_id": "REV-1", "target_index": 0},
                {"revision_item_id": "REV-1", "target_index": 1},
            ],
            "unresolved_conflicts": [], "uncovered_requirements": [],
            "out_of_scope_targets": [], "unresolved_questions": [],
        }
        paths["assessment"].write_text(json.dumps(assessment, ensure_ascii=False), encoding="utf-8")
        receipt = authorize_instruction_based_plan(
            coverage=self.coverage, plan=plan, assessment=assessment,
            plan_file=paths["plan"], coverage_file=paths["coverage"],
            assessment_file=paths["assessment"], analysis_file=paths["analysis"],
            analysis_file_sha256=hashlib.sha256(paths["analysis"].read_bytes()).hexdigest(),
            format_requirements_present=True,
        )
        preflight = format_apply_preflight(
            plan=plan, analysis=self.analysis, coverage=self.coverage,
            run_analysis=self.run_analysis, trace=trace, docx_path=self.docx,
            authorization=receipt, analysis_file=paths["analysis"],
            coverage_file=paths["coverage"], plan_file=paths["plan"],
        )
        self.assertTrue(preflight["passed"], preflight["blockers"])
        paths["analysis"].write_text("{}\n", encoding="utf-8")
        broken = format_apply_preflight(
            plan=plan, analysis=self.analysis, coverage=self.coverage,
            run_analysis=self.run_analysis, trace=trace, docx_path=self.docx,
            authorization=receipt, analysis_file=paths["analysis"],
            coverage_file=paths["coverage"], plan_file=paths["plan"],
        )
        self.assertFalse(broken["passed"])
        self.assertTrue(any("Authorization" in b for b in broken["blockers"]))

    def test_explicit_false_property_is_verified(self):
        trace = self._inputs(properties={"vertAlign": "baseline"})
        _trace, _reports, _record, verification, _gate = self._full_chain(trace)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["results"][0]["expected"], {"vertAlign": "baseline"})


if __name__ == "__main__":
    unittest.main()

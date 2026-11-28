# -*- coding: utf-8 -*-
"""Batch 3: deterministic format compiler, safe base map and Pass-2 trace."""
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

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.format_requirements import semantic_fingerprint, version_sha256  # noqa: E402
from co_reviewer.instruction_analysis import analysis_sha256  # noqa: E402
from co_reviewer.format_compiler import (  # noqa: E402
    apply_compiled_formats, compile_format_requirements, validate_compile_trace,
)
from co_reviewer.run_format_analysis import analyze_docx  # noqa: E402

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
FR_ID = "FR-REQ-T-01-01"
VERSION_ID = f"{FR_ID}-v1"


class FormatCompilerBatch3Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docx = self.root / "synthetic.docx"
        self._build_docx(runs=[("Use ", ""), ("token-A", ""), (" safely.", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.fr_version = self._fr_version()
        self.analysis = self._analysis()
        self.coverage = self._coverage()
        self.plan = self._plan()

    def tearDown(self):
        self.temp.cleanup()

    def _build_docx(self, runs, table=False, numbering=False):
        body = "".join(
            f'<w:r>{rpr}<w:t>{text}</w:t></w:r>' for text, rpr in runs
        )
        ppr = ""
        if numbering:
            ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        paragraph = f'<w:p>{ppr}{body}</w:p>'
        if table:
            paragraph = (
                '<w:tbl><w:tr><w:tc>' + paragraph + '</w:tc></w:tr></w:tbl>'
            )
        document = f'<w:document xmlns:w="{W}"><w:body>{paragraph}</w:body></w:document>'
        styles = (
            f'<w:styles xmlns:w="{W}">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
            '<w:style w:type="character" w:styleId="SUP"><w:rPr><w:vertAlign w:val="superscript"/></w:rPr></w:style>'
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
            "format_requirement_id": FR_ID,
            "version_id": VERSION_ID,
            "parent_version_id": None,
            "parent_fingerprint": None,
            "instruction_id": "T-01",
            "source_line_start": 1,
            "source_line_end": 1,
            "source_locator": "L1–L1",
            "source_excerpt": "T-01: superscript token-A.",
            "document_role": "Manuscript",
            "target_ref": {"part": "word/document.xml", "p_idx": 0, "revision_item_id": "REV-1", "target_index": 0},
            "token": token,
            "properties": properties if properties is not None else {"vertAlign": "superscript"},
            "scope": {"replacement": "token-A value", "start": 0, "end": 7, "occurrence_index": 0},
            "semantic_role": "exponent",
            "status": "confirmed",
            "created_by": "agent",
            "created_at_utc": "2026-11-28T00:00:00Z",
            "reason": "synthetic FR",
            "reviewer_decision_ref": None,
            "authorization_ref": None,
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
                "revision_targets": [{"revision_item_id": "REV-1", "target_index": 0}],
            }],
        }

    def _plan(self, *, conflicting_op=False):
        sha = hashlib.sha256(self.docx.read_bytes()).hexdigest()
        targets = [{
            "target_id": "TGT-TEXT", "document": "Manuscript",
            "input_file": str(self.docx), "output_file": str(self.root / "marked.docx"),
            "part": "word/document.xml", "p_idx": 0, "source_sha256": sha,
            "find": "token-A", "replace": "token-A value",
            "instruction_requirement_ids": ["REQ-T-01-01"],
            "source_comment_ids": [],
        }]
        if conflicting_op:
            targets.append({
                "target_id": "TGT-FMT", "document": "Manuscript",
                "input_file": str(self.docx), "output_file": str(self.root / "marked.docx"),
                "find": "token-A value", "replace": "token-A value", "p_idx": 0,
                "format_operations": [{
                    "find": "token-A", "properties": {"i": True},
                    "scope_find": "token-A value", "scope_offset": 0,
                }],
                "applies_to_target_index": 0,
            })
        return {
            "schema_version": "1.0", "approved": False,
            "revision_items": [{
                "id": "REV-1", "reviewer": "Author", "comment": "T-01",
                "status": "planned", "targets": targets,
            }],
        }

    def _compile(self):
        return compile_format_requirements(self.plan, self.analysis, self.coverage, self.run_analysis)

    # ---------------- A/B/C/D ----------------
    def test_compile_single_safe_run(self):
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])
        self.assertEqual(validate_compile_trace(trace), [])
        entry = trace["results"][0]
        self.assertEqual(entry["base_run_id"], "word/document.xml:P0:R1")
        map_text = "".join("x" * (s["replacement_end"] - s["replacement_start"]) for s in entry["replacement_format_map"])
        self.assertEqual(len(map_text), len("token-A value"))
        self.assertEqual(entry["format_operations"][0]["properties"], {"vertAlign": "superscript"})
        self.assertEqual(entry["format_operations"][0]["format_requirement_id"], FR_ID)
        self.assertEqual(entry["format_operations"][0]["format_requirement_version_id"], VERSION_ID)

    def test_apply_compiled_formats_creates_format_target(self):
        trace = self._compile()
        plan = apply_compiled_formats(copy.deepcopy(self.plan), trace)
        self.assertEqual(plan["revision_items"][0]["targets"][0]["replacement_format_map"], trace["results"][0]["replacement_format_map"])
        fmt = plan["revision_items"][0]["targets"][1]
        compiled_op = trace["results"][0]["format_operations"][0]
        self.assertEqual(fmt["format_operations"][0]["find"], compiled_op["find"])
        self.assertEqual(fmt["format_operations"][0]["properties"], compiled_op["properties"])
        self.assertEqual(fmt["format_operations"][0]["source_occurrence_id"], f"{FR_ID}:{VERSION_ID}")
        self.assertEqual(fmt["format_requirement_ids"], [FR_ID])
        self.assertEqual(plan["format_compilation"]["compile_fingerprint"], trace["compile_fingerprint"])

    def test_determinism(self):
        a = self._compile()
        b = self._compile()
        self.assertEqual(a["compile_fingerprint"], b["compile_fingerprint"])
        plan_a = apply_compiled_formats(copy.deepcopy(self.plan), a)
        plan_b = apply_compiled_formats(copy.deepcopy(self.plan), b)
        self.assertEqual(plan_a, plan_b)

    def test_cross_run_target(self):
        self._build_docx([("Use ", ""), ("token-", ""), ("A", ""), (" safely.", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])
        self.assertEqual(len(trace["results"][0]["run_ids"]), 2)

    def test_unsafe_direct_script_blocked(self):
        self._build_docx([("Use ", ""), ("token-A", '<w:rPr><w:vertAlign w:val="superscript"/></w:rPr>'), (" safely.", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")
        self.assertTrue(any("could not be compiled" in b for b in trace["blockers"]))

    def test_styled_superscript_blocked(self):
        self._build_docx([("Use ", ""), ('token-A', '<w:rPr><w:rStyle w:val="SUP"/></w:rPr>'), (" safely.", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")

    def test_ambiguous_target_blocked(self):
        self._build_docx([("token-A ", ""), ("token-A", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")

    def test_conflicting_existing_operation_blocked(self):
        self.plan = self._plan(conflicting_op=True)
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")

    def test_docx_sha_mismatch_blocked(self):
        self.plan["revision_items"][0]["targets"][0]["source_sha256"] = "0" * 64
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")
        self.assertTrue(any("SHA-256 mismatch" in b for b in trace["blockers"]))

    def test_table_context_mismatch_blocked(self):
        self.run_analysis["runs"][1]["structure_context"] = {"in_table": True, "in_numbering": False}
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")

    def test_trace_tamper_detected(self):
        trace = self._compile()
        trace["results"][0]["token"] = "changed"
        self.assertTrue(validate_compile_trace(trace))

    def test_cli_compile_plan_integration(self):
        instruction = self.root / "instruction.txt"
        instruction.write_text("T-01: superscript token-A.\n", encoding="utf-8")
        self.analysis["source_instruction"] = {
            "file": str(instruction), "sha256": hashlib.sha256(instruction.read_bytes()).hexdigest(),
        }
        self.coverage["source_instruction"] = dict(self.analysis["source_instruction"])
        self.analysis["analysis_sha256"] = analysis_sha256(self.analysis)
        paths = {
            "intents": self.root / "intents.json",
            "analysis": self.root / "instruction_analysis.json",
            "coverage": self.root / "instruction_coverage.json",
            "run_analysis": self.root / "run_analysis.json",
            "out": self.root / "plan.json",
        }
        paths["intents"].write_text(json.dumps({
            "documents": {"Manuscript": {"input_file": str(self.docx),
                                         "output_file": str(self.root / "marked.docx")}},
            "intents": [{
                "id": "REV-1", "doc_role": "Manuscript", "find": "token-A",
                "replace": "token-A value", "intent": "T-01",
                "instruction_requirement_ids": ["REQ-T-01-01"],
                "input_file": str(self.docx), "output_file": str(self.root / "marked.docx"),
            }],
        }, ensure_ascii=False), encoding="utf-8")
        paths["analysis"].write_text(json.dumps(self.analysis, ensure_ascii=False), encoding="utf-8")
        paths["coverage"].write_text(json.dumps(self.coverage, ensure_ascii=False), encoding="utf-8")
        paths["run_analysis"].write_text(json.dumps(self.run_analysis, ensure_ascii=False), encoding="utf-8")
        args = SimpleNamespace(
            intents=str(paths["intents"]), out=str(paths["out"]), doc_map=None,
            instruction_coverage=str(paths["coverage"]),
            instruction_analysis=str(paths["analysis"]),
            run_format_analysis=str(paths["run_analysis"]),
        )
        self.assertEqual(cli.cmd_compile_plan(args), 0)
        plan = json.loads(paths["out"].read_text(encoding="utf-8"))
        self.assertEqual(plan["format_compilation"]["status"], "COMPILED")
        self.assertEqual(plan["revision_items"][0]["targets"][1]["format_operations"][0]["source_occurrence_id"], f"{FR_ID}:{VERSION_ID}")
        self.assertEqual(plan["format_compilation"]["results"][0]["format_operations"][0]["format_requirement_id"], FR_ID)


    # ---------------- F/G. Structure, multi-property, compatibility ----------------
    def test_table_and_numbering_context_compiles(self):
        self._build_docx([("Use ", ""), ("token-A", ""), (" safely.", "")],
                         table=True, numbering=True)
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        target_row = self.run_analysis["runs"][1]
        self.assertEqual(target_row["structure_context"], {"in_table": True, "in_numbering": True})
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])

    def test_multiple_properties_become_distinct_operations(self):
        self.fr_version = self._fr_version(properties={"vertAlign": "superscript", "b": True})
        self.analysis = self._analysis()
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])
        ops = trace["results"][0]["format_operations"]
        self.assertEqual(len(ops), 2)
        self.assertEqual(len({op["operation_id"] for op in ops}), 2)
        self.assertEqual({next(iter(op["properties"])) for op in ops}, {"vertAlign", "b"})

    def test_adjacent_script_run_does_not_contaminate_map(self):
        self._build_docx([
            ("Use ", ""), ("token-A", ""), (" safely. ", ""),
            ("bad", '<w:rPr><w:vertAlign w:val="subscript"/></w:rPr>'),
        ])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])
        entry = trace["results"][0]
        self.assertEqual(entry["base_run_id"], "word/document.xml:P0:R1")
        for segment in entry["replacement_format_map"]:
            self.assertLessEqual(segment["source_offset"], 6)

    def test_unresolved_target_is_blocked(self):
        self._build_docx([("Use ", ""), ("token-A", '<w:rPr><w:rStyle w:val="MISSING"/></w:rPr>'), (" safely.", "")])
        self.run_analysis = analyze_docx(self.docx)
        self.plan = self._plan()
        trace = self._compile()
        self.assertEqual(trace["status"], "BLOCKED")
        self.assertEqual(self.run_analysis["runs"][1]["safety_classification"], "UNRESOLVED_EFFECTIVE_FORMAT")

    def test_repeated_apply_is_idempotent(self):
        trace = self._compile()
        once = apply_compiled_formats(copy.deepcopy(self.plan), trace)
        twice = apply_compiled_formats(copy.deepcopy(once), trace)
        ops_once = once["revision_items"][0]["targets"][1]["format_operations"]
        ops_twice = twice["revision_items"][0]["targets"][1]["format_operations"]
        self.assertEqual(len(ops_once), len(ops_twice))
        self.assertEqual(ops_once, ops_twice)

    def test_unicode_target_compiles(self):
        self._build_docx([("Use ", ""), ("α β", ""), (" done", "")])
        self.fr_version = self._fr_version(token="α β")
        self.analysis = self._analysis()
        self.run_analysis = analyze_docx(self.docx)
        plan = self._plan()
        plan["revision_items"][0]["targets"][0].update({"find": "α β", "replace": "α β 2"})
        self.plan = plan
        trace = self._compile()
        self.assertEqual(trace["status"], "COMPILED", trace["blockers"])

    def test_non_format_plan_is_left_unchanged(self):
        instruction = self.root / "instruction.txt"
        instruction.write_text("T-01: superscript token-A.\n", encoding="utf-8")
        analysis = copy.deepcopy(self.analysis)
        del analysis["format_requirements"]
        analysis["source_instruction"] = {
            "file": str(instruction), "sha256": hashlib.sha256(instruction.read_bytes()).hexdigest(),
        }
        analysis["analysis_sha256"] = analysis_sha256(analysis)
        coverage = copy.deepcopy(self.coverage)
        coverage["source_instruction"] = dict(analysis["source_instruction"])
        coverage["requirements"][0].pop("format_requirement_ids")
        paths = {
            "intents": self.root / "intents_plain.json",
            "analysis": self.root / "analysis_plain.json",
            "coverage": self.root / "coverage_plain.json",
            "run_analysis": self.root / "run_plain.json",
            "out": self.root / "plan_plain.json",
        }
        paths["intents"].write_text(json.dumps({
            "documents": {"Manuscript": {"input_file": str(self.docx),
                                         "output_file": str(self.root / "marked.docx")}},
            "intents": [{
                "id": "REV-1", "doc_role": "Manuscript", "find": "token-A",
                "replace": "token-A value", "intent": "T-01",
                "input_file": str(self.docx), "output_file": str(self.root / "marked.docx"),
            }],
        }, ensure_ascii=False), encoding="utf-8")
        paths["analysis"].write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        paths["coverage"].write_text(json.dumps(coverage, ensure_ascii=False), encoding="utf-8")
        paths["run_analysis"].write_text(json.dumps(self.run_analysis, ensure_ascii=False), encoding="utf-8")
        args = SimpleNamespace(
            intents=str(paths["intents"]), out=str(paths["out"]), doc_map=None,
            instruction_coverage=str(paths["coverage"]),
            instruction_analysis=str(paths["analysis"]),
            run_format_analysis=str(paths["run_analysis"]),
        )
        self.assertEqual(cli.cmd_compile_plan(args), 0)
        plan = json.loads(paths["out"].read_text(encoding="utf-8"))
        self.assertNotIn("format_compilation", plan)
        self.assertNotIn("format_operations", plan["revision_items"][0]["targets"][0])


if __name__ == "__main__":
    unittest.main()

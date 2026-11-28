# -*- coding: utf-8 -*-
"""Batch 1: Format Requirements data model, version chain and external integrity anchor."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import co_reviewer_cli as cli  # noqa: E402
from co_reviewer.format_requirements import (  # noqa: E402
    canonical_json, semantic_fingerprint, version_sha256,
)
from co_reviewer.instruction_analysis import (  # noqa: E402
    analysis_sha256, analysis_to_coverage, crosscheck_coverage,
    validate_instruction_analysis,
)
from co_reviewer.instruction_coverage import (  # noqa: E402
    InstructionCoverageError, authorize_instruction_based_plan,
    instruction_coverage_sha256, validate_instruction_based_authorization,
)
from co_reviewer.identity import revision_targets_fingerprint  # noqa: E402


class FormatRequirementsBatch1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.instruction = self.root / "instructions.txt"
        self.instruction.write_text(
            "T-01: replace exact old phrase with exact new phrase.\n"
            "format requirement: token-A superscript.\n", encoding="utf-8")
        self.sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

    def tearDown(self):
        self.temp.cleanup()

    def _version(self, *, fr_id="FR-REQ-T-01-EDIT-01", version="v1", parent=None,
                 parent_fp=None, token="token-A", props=None, scope=None, excerpt=None,
                 source_excerpt=None):
        v = {
            "format_requirement_id": fr_id,
            "version_id": f"{fr_id}-{version}",
            "parent_version_id": parent,
            "parent_fingerprint": parent_fp,
            "instruction_id": "T-01",
            "source_line_start": 2,
            "source_line_end": 2,
            "source_locator": "L2–L2",
            "source_excerpt": excerpt or source_excerpt or "format requirement: token-A superscript.",
            "document_role": "Manuscript",
            "target_ref": {"part": "word/document.xml", "p_idx": 0},
            "token": token,
            "properties": props if props is not None else {"vertAlign": "superscript"},
            "scope": scope if scope is not None else {
                "replacement": "replacement token-A", "start": 11, "end": 18, "occurrence_index": 0,
            },
            "semantic_role": "exponent",
            "status": "confirmed",
            "created_by": "instruction-understanding-agent",
            "created_at_utc": "2026-11-28T00:00:00Z",
            "reason": "authored from original instruction",
            "reviewer_decision_ref": None,
            "authorization_ref": None,
        }
        v["fingerprint"] = semantic_fingerprint(v)
        v["version_sha256"] = version_sha256(v)
        return v

    def _fr(self, versions, *, fr_id="FR-REQ-T-01-EDIT-01", current=None):
        return {
            "schema_version": "1.0",
            "format_requirement_id": fr_id,
            "requirement_id": "REQ-T-01-EDIT",
            "current_version_id": current or versions[-1]["version_id"],
            "versions": versions,
        }

    def _analysis(self, fr_rows=None, analyses=None):
        a = {
            "schema_version": "1.0",
            "source_instruction": {"file": str(self.instruction), "sha256": self.sha(self.instruction)},
            "analyses": analyses if analyses is not None else [{
                "requirement_id": "REQ-T-01-EDIT", "instruction_action": "replace",
                "instruction_polarity": "affirmative", "instruction_object": "token-A",
                "source_line_start": 1, "source_line_end": 1, "source_locator": "L1–L1",
                "instruction_excerpt": "T-01: replace exact old phrase with exact new phrase.",
            }],
        }
        if fr_rows is not None:
            a["format_requirements"] = fr_rows
        a["analysis_sha256"] = analysis_sha256(a)
        return a

    def _coverage(self, analysis):
        cov = {
            "schema_version": "1.1",
            "source_instruction": dict(analysis["source_instruction"]),
            "requirements": [{
                "requirement_id": "REQ-T-01-EDIT", "task_id": "T-01",
                "source_line_start": 1, "source_line_end": 1, "source_locator": "L1–L1",
                "instruction_excerpt": "T-01: replace exact old phrase with exact new phrase.",
                "status": "planned",
                "revision_targets": [{"revision_item_id": "T-01", "target_index": 0}],
            }],
        }
        return analysis_to_coverage(cov, analysis)

    # ---------------- A. Schema and basic structure ----------------
    def test_valid_format_requirement_analysis(self):
        v = self._version()
        errors = validate_instruction_analysis(self._analysis([self._fr([v])]))
        self.assertEqual(errors, [])

    def test_missing_required_semantic_field(self):
        v = self._version()
        del v["token"]
        errors = validate_instruction_analysis(self._analysis([self._fr([v])]))
        self.assertTrue(any("token required" in e for e in errors), errors)

    def test_invalid_token_and_properties(self):
        v = self._version(token="   ")
        errors = validate_instruction_analysis(self._analysis([self._fr([v])]))
        self.assertTrue(any("token required" in e for e in errors), errors)
        v2 = self._version(props={})
        errors = validate_instruction_analysis(self._analysis([self._fr([v2])]))
        self.assertTrue(any("properties must be a non-empty object" in e for e in errors), errors)

    def test_scope_missing(self):
        v = self._version()
        del v["scope"]
        errors = validate_instruction_analysis(self._analysis([self._fr([v])]))
        self.assertTrue(any("scope invalid" in e for e in errors), errors)

    def test_duplicate_ids_and_bad_current_pointer(self):
        v = self._version()
        fr = self._fr([v])
        fr2 = copy.deepcopy(fr)
        errors = validate_instruction_analysis(self._analysis([fr, fr2]))
        self.assertTrue(any("duplicated" in e for e in errors), errors)
        bad = self._fr([v], current="FR-REQ-T-01-EDIT-01-v9")
        errors = validate_instruction_analysis(self._analysis([bad]))
        self.assertTrue(any("current_version_id" in e for e in errors), errors)

    def test_dangling_coverage_fr_reference(self):
        v = self._version()
        analysis = self._analysis([self._fr([v])])
        cov = self._coverage(analysis)
        cov["requirements"][0]["format_requirement_ids"] = ["FR-MISSING-01"]
        errors = crosscheck_coverage(cov, analysis)
        self.assertTrue(any("unknown format requirement" in e for e in errors), errors)

    # ---------------- B. Fingerprint ----------------
    def test_fingerprint_semantic_boundary(self):
        v = self._version()
        baseline = semantic_fingerprint(v)
        same = self._version()
        self.assertEqual(semantic_fingerprint(same), baseline)
        for field, value in (("source_excerpt", "different excerpt"),
                             ("token", "token-B"),
                             ("properties", {"i": True})):
            changed = self._version(**{field: value}) if field != "properties" else self._version(props=value)
            self.assertNotEqual(semantic_fingerprint(changed), baseline, field)
        scope = copy.deepcopy(v["scope"]); scope["end"] = 19
        self.assertNotEqual(semantic_fingerprint(self._version(scope=scope)), baseline)
        status_only = copy.deepcopy(v); status_only["status"] = "superseded"
        self.assertEqual(semantic_fingerprint(status_only), baseline)
        self.assertEqual(canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')

    def test_version_sha_is_repeatable_and_self_excluding(self):
        v = self._version()
        self.assertEqual(version_sha256(v), version_sha256(copy.deepcopy(v)))
        with_sha = copy.deepcopy(v)
        with_sha["version_sha256"] = version_sha256(v)
        self.assertEqual(version_sha256(with_sha), version_sha256(v))

    # ---------------- C. Version chain ----------------
    def test_valid_parent_chain(self):
        v1 = self._version()
        v2 = self._version(version="v2", parent=v1["version_id"], parent_fp=v1["fingerprint"], token="token-B")
        errors = validate_instruction_analysis(self._analysis([self._fr([v1, v2])]))
        self.assertEqual(errors, [])

    def test_parent_missing_and_fingerprint_mismatch(self):
        v2 = self._version(version="v2", parent="FR-REQ-T-01-EDIT-01-v0", parent_fp="0" * 64)
        errors = validate_instruction_analysis(self._analysis([self._fr([v2])]))
        self.assertTrue(any("parent_version_id not found" in e for e in errors), errors)
        v1 = self._version()
        v2 = self._version(version="v2", parent=v1["version_id"], parent_fp="0" * 64)
        errors = validate_instruction_analysis(self._analysis([self._fr([v1, v2])]))
        self.assertTrue(any("parent_fingerprint inconsistent" in e for e in errors), errors)

    def test_history_tamper_and_duplicate_version_content(self):
        v1 = self._version()
        tampered = copy.deepcopy(v1); tampered["token"] = "token-B"
        errors = validate_instruction_analysis(self._analysis([self._fr([tampered])]))
        self.assertTrue(any("version_sha256 mismatch" in e for e in errors), errors)
        v2 = self._version(fr_id="FR-B")
        fr1 = self._fr([v1], fr_id="FR-A")
        fr2 = self._fr([copy.deepcopy(v2)], fr_id="FR-B")
        fr2["versions"][0]["version_id"] = v1["version_id"]
        errors = validate_instruction_analysis(self._analysis([fr1, fr2]))
        self.assertTrue(any("version_id reused" in e for e in errors), errors)

    def test_cycle_and_broken_current(self):
        v1 = self._version(version="v1", parent="FR-REQ-T-01-EDIT-01-v2", parent_fp="0" * 64)
        v2 = self._version(version="v2", parent="FR-REQ-T-01-EDIT-01-v1", parent_fp="0" * 64)
        errors = validate_instruction_analysis(self._analysis([self._fr([v1, v2])]))
        self.assertTrue(any("cyclic" in e or "parent_fingerprint" in e for e in errors), errors)
        broken = self._fr([self._version()], current="FR-MISSING-v1")
        errors = validate_instruction_analysis(self._analysis([broken]))
        self.assertTrue(any("current_version_id" in e for e in errors), errors)

    # ---------------- D. External integrity anchor ----------------
    def _authorization_objects(self, analysis):
        source = self.root / "source.docx"
        source.write_bytes(b"immutable original DOCX bytes")
        view = {
            "schema_version": "1.0", "approved": False,
            "revision_items": [{
                "id": "T-01", "reviewer": "Author Instruction",
                "comment": "T-01", "proposed_response": "", "status": "planned",
                "targets": [{
                    "document": "Manuscript", "input_file": str(source),
                    "output_file": str(self.root / "source.marked.docx"),
                    "part": "word/document.xml", "p_idx": 0,
                    "find": "old phrase", "replace": "new phrase",
                    "source_sha256": self.sha(source), "source_comment_ids": [],
                    "instruction_requirement_ids": ["REQ-T-01-EDIT"],
                    "authorized_change": {"intent": "T-01"},
                }],
            }],
        }
        view["revision_items"][0]["preflight_fingerprint"] = revision_targets_fingerprint(view["revision_items"][0])
        cov = self._coverage(analysis)
        cov["coverage_sha256"] = instruction_coverage_sha256(cov)
        assessment = {
            "schema_version": "1.0", "status": "eligible",
            "preflight_plan_sha256": "", "source_instruction_sha256": analysis["source_instruction"]["sha256"],
            "coverage_sha256": cov["coverage_sha256"],
            "reviewed_requirement_ids": ["REQ-T-01-EDIT"],
            "reviewed_target_refs": [{"revision_item_id": "T-01", "target_index": 0}],
            "unresolved_conflicts": [], "uncovered_requirements": [],
            "out_of_scope_targets": [], "unresolved_questions": [],
        }
        plan = copy.deepcopy(view); plan["approved"] = True; plan["authorization_mode"] = "instruction_based"
        for item in plan["revision_items"]:
            item["status"] = "approved"; item["authorization_mode"] = "instruction_based"
        paths = {
            "plan": self.root / "plan.json", "coverage": self.root / "coverage.json",
            "assessment": self.root / "assessment.json", "analysis": self.root / "analysis.json",
        }
        paths["plan"].write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        paths["coverage"].write_text(json.dumps(cov, ensure_ascii=False), encoding="utf-8")
        assessment["preflight_plan_sha256"] = self.sha(paths["plan"])
        paths["assessment"].write_text(json.dumps(assessment, ensure_ascii=False), encoding="utf-8")
        paths["analysis"].write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        return paths, plan, cov, assessment

    def _authorize(self, analysis, *, analysis_file=True, present=True):
        paths, plan, cov, assessment = self._authorization_objects(analysis)
        if not analysis_file:
            with self.assertRaises(InstructionCoverageError):
                authorize_instruction_based_plan(
                    coverage=cov, plan=plan, assessment=assessment,
                    plan_file=paths["plan"], coverage_file=paths["coverage"],
                    assessment_file=paths["assessment"],
                    format_requirements_present=present,
                )
            return None, paths, plan, cov
        receipt = authorize_instruction_based_plan(
            coverage=cov, plan=plan, assessment=assessment,
            plan_file=paths["plan"], coverage_file=paths["coverage"],
            assessment_file=paths["assessment"],
            analysis_file=paths["analysis"] if present else None,
            analysis_file_sha256=self.sha(paths["analysis"]) if present else None,
            format_requirements_present=present,
        )
        return receipt, paths, plan, cov

    def test_external_anchor_roundtrip_and_tamper(self):
        analysis = self._analysis([self._fr([self._version()])])
        receipt, paths, plan, cov = self._authorize(analysis)
        self.assertTrue(receipt["format_requirements_present"])
        self.assertEqual(receipt["format_requirements_file_sha256"], self.sha(paths["analysis"]))
        errors = validate_instruction_based_authorization(
            authorization=receipt, coverage=cov, plan=plan,
            plan_file=paths["plan"], coverage_file=paths["coverage"],
            analysis_file=paths["analysis"],
        )
        self.assertEqual(errors, [])
        # External tamper, not only internal fingerprint.
        payload = json.loads(paths["analysis"].read_text(encoding="utf-8"))
        payload["analyses"][0]["instruction_object"] = "changed"
        paths["analysis"].write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        errors = validate_instruction_based_authorization(
            authorization=receipt, coverage=cov, plan=plan,
            plan_file=paths["plan"], coverage_file=paths["coverage"],
            analysis_file=paths["analysis"],
        )
        self.assertTrue(any("Format Requirements file SHA-256 changed" in e for e in errors), errors)

    def test_external_anchor_missing_file_or_anchor(self):
        analysis = self._analysis([self._fr([self._version()])])
        receipt, paths, plan, cov = self._authorize(analysis)
        paths["analysis"].unlink()
        errors = validate_instruction_based_authorization(
            authorization=receipt, coverage=cov, plan=plan,
            plan_file=paths["plan"], coverage_file=paths["coverage"],
            analysis_file=paths["analysis"],
        )
        self.assertTrue(any("Format Requirements file is missing" in e for e in errors), errors)
        self._authorize(analysis, analysis_file=False)

    def test_fr_coverage_reference_not_semantic_copy(self):
        analysis = self._analysis([self._fr([self._version()])])
        cov = self._coverage(analysis)
        self.assertEqual(cov["requirements"][0]["format_requirement_ids"], ["FR-REQ-T-01-EDIT-01"])
        self.assertNotIn("token", cov["requirements"][0])
        self.assertNotIn("properties", cov["requirements"][0])
        self.assertNotIn("scope", cov["requirements"][0])


if __name__ == "__main__":
    unittest.main()

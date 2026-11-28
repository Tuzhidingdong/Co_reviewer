# -*- coding: utf-8 -*-
"""Instruction-backed INSERT authorization: source, completeness, mixed plans."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from co_reviewer.insert_authorization import (  # noqa: E402
    PROVENANCE_FIELD,
    SOURCE_FIELD,
    build_insert_authorization,
    validate_insert_authorization,
    validate_plan_insert_authorizations,
    validate_workflow_insert_authorizations,
)
from co_reviewer.plan_compiler import compile_revision_plan  # noqa: E402
from co_reviewer.v232_contract import extract_immutable_tokens  # noqa: E402
from co_reviewer.revision_models import (  # noqa: E402
    Patch, PatchOperation, RevisionLedger,
)

REFERENCE = "[33] New Author (2020). Journal Name 12, 34-40."
INSTRUCTION_LINES = [
    "[MT-10] Insert one reference.",
    "- 修改为：",
    f"  `{REFERENCE}`",
]


class InsertAuthorizationSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.docx"
        self.output = self.root / "marked.docx"
        doc = Document()
        doc.add_paragraph("Reference section")
        doc.add_paragraph("Trailing paragraph")
        doc.save(self.source)
        self.instruction = self.root / "instructions.txt"
        self.instruction.write_text("\n".join(INSTRUCTION_LINES) + "\n", encoding="utf-8")
        self.coverage = {
            "schema_version": "1.1",
            "source_instruction": {
                "file": str(self.instruction),
                "sha256": hashlib.sha256(self.instruction.read_bytes()).hexdigest(),
            },
            "requirements": [self._requirement()],
        }
        self.intent = {
            "id": "INS-1",
            "doc_role": "Manuscript",
            "reviewer": "Author",
            "action": "insert_paragraph",
            "anchor_text": "Reference section",
            "position": "after",
            "inserted_text": REFERENCE,
            "intent": "Insert the reference requested by MT-10",
            "instruction_requirement_ids": ["REQ-MT-10-INS"],
        }

    def tearDown(self):
        self.temp.cleanup()

    def _requirement(self):
        return {
            "requirement_id": "REQ-MT-10-INS",
            "task_id": "MT-10",
            "source_line_start": 1,
            "source_line_end": 3,
            "source_locator": "L1–L3",
            "instruction_excerpt": "\n".join(INSTRUCTION_LINES),
            "status": "planned",
            "instruction_action": "insert",
            "instruction_polarity": "affirmative",
            "revision_targets": [{"revision_item_id": "INS-1", "target_index": 0}],
        }

    def _payload(self, intents=None):
        return {
            "documents": {"Manuscript": {
                "input_file": str(self.source), "output_file": str(self.output),
            }},
            "intents": intents if intents is not None else [dict(self.intent)],
        }

    def _compiled(self, intents=None, coverage=None):
        return compile_revision_plan(
            self._payload(intents), base_dir=self.root,
            instruction_coverage=coverage if coverage is not None else self.coverage,
        )

    def _insert_target(self, plan):
        return plan["revision_items"][0]["targets"][0]

    # --- provenance and generation ---

    def test_01_auto_authorization_is_grounded_in_instruction(self):
        plan = self._compiled()
        target = self._insert_target(plan)
        auth = target["authorized_change"]
        source = auth[SOURCE_FIELD]
        self.assertEqual(source["requirement_id"], "REQ-MT-10-INS")
        self.assertEqual(source["source_locator"], "L1–L3")
        self.assertEqual(source["source_file"], str(self.instruction))
        self.assertEqual(source["source_sha256"], self.coverage["source_instruction"]["sha256"])
        expected = extract_immutable_tokens(REFERENCE)
        self.assertEqual(
            [(row["kind"], row["text"]) for row in auth["explicitly_authorized_insertions"]],
            [(row["kind"], row["text"]) for row in expected],
        )
        for row in auth["explicitly_authorized_insertions"]:
            self.assertEqual(row[PROVENANCE_FIELD], "REQ-MT-10-INS")
        self.assertEqual(target["instruction_requirement_ids"], ["REQ-MT-10-INS"])

    def test_02_end_to_end_txt_to_intent_to_plan(self):
        # Simulate the generator: read the original TXT, derive excerpt and intent.
        lines = self.instruction.read_text(encoding="utf-8").splitlines()
        excerpt = "\n".join(lines[0:3])
        coverage = json.loads(json.dumps(self.coverage))
        coverage["requirements"][0]["instruction_excerpt"] = excerpt
        intent = dict(self.intent)
        intent["inserted_text"] = lines[2].strip().strip("`")
        plan = compile_revision_plan(
            self._payload([intent]), base_dir=self.root, instruction_coverage=coverage,
        )
        auth = self._insert_target(plan)["authorized_change"]
        self.assertIn(auth[SOURCE_FIELD]["source_file"], str(self.instruction))
        self.assertEqual(auth[SOURCE_FIELD]["instruction_excerpt"], excerpt)
        self.assertTrue(
            intent["inserted_text"] in auth[SOURCE_FIELD]["instruction_excerpt"],
            "authorization must come from the instruction excerpt, not from inserted text",
        )

    def test_03_token_only_self_authorization_is_rejected(self):
        intent = dict(self.intent)
        intent["explicitly_authorized_insertions"] = [
            {"kind": row["kind"], "text": row["text"]}
            for row in extract_immutable_tokens(REFERENCE)
        ]
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_CHECK_FAILED"):
            self._compiled([intent])

    def test_04_missing_coverage_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_SOURCE_REQUIRED"):
            compile_revision_plan(self._payload(), base_dir=self.root)

    def test_05_instruction_without_insert_text_is_rejected(self):
        coverage = json.loads(json.dumps(self.coverage))
        coverage["requirements"][0]["instruction_excerpt"] = "[MT-10] Insert something else."
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_EXCERPT_SOURCE_MISMATCH"):
            self._compiled(coverage=coverage)

    def test_06_wrong_requirement_link_is_rejected(self):
        coverage = json.loads(json.dumps(self.coverage))
        coverage["requirements"][0]["revision_targets"] = [
            {"revision_item_id": "OTHER-1", "target_index": 0}
        ]
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_REQUIREMENT_UNLINKED"):
            self._compiled(coverage=coverage)

    def test_07_source_sha_mismatch_is_rejected(self):
        coverage = json.loads(json.dumps(self.coverage))
        coverage["source_instruction"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_SOURCE_SHA_MISMATCH"):
            self._compiled(coverage=coverage)

    # --- completeness / exactness ---

    def _with_auth(self, mutate):
        auth = build_insert_authorization(
            item_id="INS-1", target_index=0, inserted_text=REFERENCE,
            instruction_coverage=self.coverage, requirement_id="REQ-MT-10-INS",
        )
        mutate(auth)
        errors = validate_insert_authorization(
            item_id="INS-1", target_index=0, inserted_text=REFERENCE,
            authorized_change=auth, instruction_coverage=self.coverage,
        )
        return errors

    def test_08_missing_token_detected(self):
        errors = self._with_auth(lambda a: a["explicitly_authorized_insertions"].pop())
        self.assertTrue(any("TOKEN_SET_MISMATCH" in e for e in errors), errors)

    def test_09_extra_token_detected(self):
        errors = self._with_auth(
            lambda a: a["explicitly_authorized_insertions"].append(
                {"kind": "number", "text": "999", PROVENANCE_FIELD: "REQ-MT-10-INS"}))
        self.assertTrue(any("TOKEN_SET_MISMATCH" in e for e in errors), errors)

    def test_10_wrong_kind_detected(self):
        def mutate(a):
            a["explicitly_authorized_insertions"][0]["kind"] = "citation-wrong"
        errors = self._with_auth(mutate)
        self.assertTrue(any("TOKEN_SET_MISMATCH" in e for e in errors), errors)

    def test_11_unlinked_token_detected(self):
        def mutate(a):
            a["explicitly_authorized_insertions"][0][PROVENANCE_FIELD] = "REQ-OTHER"
        errors = self._with_auth(mutate)
        self.assertTrue(any("TOKEN_UNLINKED" in e for e in errors), errors)

    # --- multi-insert + mixed task types ---

    def test_12_two_inserts_link_to_own_requirements(self):
        second_text = "[34] Second Author (2021). Other Journal 5, 1-9."
        second_intent = {
            **self.intent, "id": "INS-2", "inserted_text": second_text,
            "instruction_requirement_ids": ["REQ-MT-10-INS2"],
        }
        req2 = self._requirement()
        req2.update({
            "requirement_id": "REQ-MT-10-INS2",
            "instruction_excerpt": f"[MT-10] Insert second reference.\n`{second_text}`",
            "instruction_action": "insert",
            "instruction_polarity": "affirmative",
            "revision_targets": [{"revision_item_id": "INS-2", "target_index": 0}],
        })
        second_lines = req2["instruction_excerpt"].splitlines()
        self.instruction.write_text(
            "\n".join(INSTRUCTION_LINES) + "\n" + "\n".join(second_lines) + "\n",
            encoding="utf-8",
        )
        req2.update({
            "source_line_start": len(INSTRUCTION_LINES) + 1,
            "source_line_end": len(INSTRUCTION_LINES) + len(second_lines),
            "source_locator": f"L{len(INSTRUCTION_LINES)+1}–L{len(INSTRUCTION_LINES)+len(second_lines)}",
        })
        self.coverage["source_instruction"]["sha256"] = hashlib.sha256(
            self.instruction.read_bytes()
        ).hexdigest()
        coverage = json.loads(json.dumps(self.coverage))
        coverage["requirements"].append(req2)
        plan = compile_revision_plan(
            self._payload([dict(self.intent), second_intent]), base_dir=self.root,
            instruction_coverage=coverage,
        )
        first_auth = plan["revision_items"][0]["targets"][0]["authorized_change"]
        second_auth = plan["revision_items"][1]["targets"][0]["authorized_change"]
        self.assertEqual(first_auth[SOURCE_FIELD]["requirement_id"], "REQ-MT-10-INS")
        self.assertEqual(second_auth[SOURCE_FIELD]["requirement_id"], "REQ-MT-10-INS2")

    def test_13_mixed_insert_replace_format_preserves_other_targets(self):
        replace_intent = {
            "id": "REP-1", "doc_role": "Manuscript", "reviewer": "Author",
            "find": "Trailing paragraph", "replace": "Trailing paragraph updated",
            "intent": "Update trailing prose", "instruction_requirement_ids": ["REQ-REP"],
        }
        format_intent = {
            "id": "FMT-1", "doc_role": "Manuscript", "reviewer": "Author",
            "find": "Trailing paragraph", "replace": "Trailing paragraph",
            "intent": "Italicize phrase", "instruction_requirement_ids": ["REQ-FMT"],
            "format_intent": [{"find": "Trailing", "properties": {"i": True}}],
        }
        for req_id in ("REQ-REP", "REQ-FMT"):
            req = self._requirement()
            req.update({
                "requirement_id": req_id,
                "revision_targets": [{"revision_item_id": req_id.split("-")[1], "target_index": 0}],
            })
            self.coverage["requirements"].append(req)
        plan = compile_revision_plan(
            self._payload([dict(self.intent), replace_intent, format_intent]),
            base_dir=self.root, instruction_coverage=self.coverage,
        )
        items = {item["id"]: item for item in plan["revision_items"]}
        self.assertEqual(items["INS-1"]["targets"][0]["action"], "insert_paragraph")
        replace_target = items["REP-1"]["targets"][0]
        self.assertNotIn("action", replace_target)
        self.assertNotIn(SOURCE_FIELD, replace_target["authorized_change"])
        fmt_targets = items["FMT-1"]["targets"]
        self.assertTrue(any(t.get("format_operations") for t in fmt_targets))


class InsertAuthorizationV2Tests(InsertAuthorizationSourceTests):
    """V2: source authenticity, polarity, object identity and entry boundaries."""

    def _coverage_with(self, **changes):
        coverage = json.loads(json.dumps(self.coverage))
        coverage["requirements"][0].update(changes)
        return coverage

    def test_14_negative_instruction_with_full_text_is_rejected(self):
        # The full INSERT text is present, but the requirement is explicitly negative.
        text = "[MT-10] 不要增加这条参考文献：" + REFERENCE
        lines = [text]
        self.instruction.write_text("\n".join(lines) + "\n", encoding="utf-8")
        coverage = self._coverage_with(
            instruction_excerpt=text, source_line_start=1, source_line_end=1,
            source_locator="L1–L1", instruction_polarity="negative",
        )
        coverage["source_instruction"]["sha256"] = hashlib.sha256(
            self.instruction.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_NEGATIVE_POLARITY"):
            self._compiled(coverage=coverage)

    def test_15_conditional_and_unadopted_are_rejected(self):
        for polarity in ("conditional", "not_adopted", "unknown"):
            with self.subTest(polarity=polarity):
                coverage = self._coverage_with(instruction_polarity=polarity)
                with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION"):
                    self._compiled(coverage=coverage)

    def test_16_tampered_excerpt_with_correct_sha_is_rejected(self):
        coverage = self._coverage_with(instruction_excerpt="forged excerpt " + REFERENCE)
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_EXCERPT_SOURCE_MISMATCH"):
            self._compiled(coverage=coverage)

    def test_17_wrong_line_number_is_rejected(self):
        coverage = self._coverage_with(
            source_line_start=2, source_line_end=3, source_locator="L2–L3",
        )
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_EXCERPT_SOURCE_MISMATCH"):
            self._compiled(coverage=coverage)

    def test_18_object_identity_authorizes_full_record(self):
        # Instruction names only the object; full bibliographic text comes later.
        text = "[MT-10] 新增文献 [33]。"
        self.instruction.write_text(text + "\n", encoding="utf-8")
        coverage = self._coverage_with(
            instruction_excerpt=text, source_line_start=1, source_line_end=1,
            source_locator="L1–L1", instruction_object="[33]",
        )
        coverage["source_instruction"]["sha256"] = hashlib.sha256(
            self.instruction.read_bytes()
        ).hexdigest()
        plan = self._compiled(coverage=coverage)
        self.assertEqual(
            self._insert_target(plan)["authorized_change"][SOURCE_FIELD]["instruction_object"],
            "[33]",
        )

    def test_19_object_not_unique_is_rejected(self):
        text = "[MT-10] 新增文献 [33]，另见 [33]。"
        self.instruction.write_text(text + "\n", encoding="utf-8")
        coverage = self._coverage_with(
            instruction_excerpt=text, source_line_start=1, source_line_end=1,
            source_locator="L1–L1", instruction_object="[33]",
        )
        coverage["source_instruction"]["sha256"] = hashlib.sha256(
            self.instruction.read_bytes()
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "INSERT_AUTHORIZATION_NOT_IN_INSTRUCTION"):
            self._compiled(coverage=coverage)

    def test_20_ledger_export_boundary_rejects_token_only(self):
        plan = self._compiled()
        target = self._insert_target(plan)
        token_only = json.loads(json.dumps(target["authorized_change"]))
        token_only["explicitly_authorized_insertions"] = [
            {k: v for k, v in row.items() if k != PROVENANCE_FIELD}
            for row in token_only["explicitly_authorized_insertions"]
        ]
        target["authorized_change"] = token_only
        errors = validate_plan_insert_authorizations(plan)
        self.assertTrue(any("TOKEN_UNLINKED" in e for e in errors), errors)

    def test_21_workflow_boundary_rejects_token_only(self):
        plan = self._compiled()
        target = self._insert_target(plan)
        op = dict(target)
        op["action"] = "insert_paragraph"
        workflow = {"tasks": [{"task_id": "T-1", "patch_operations": [op]}]}
        self.assertEqual(validate_workflow_insert_authorizations(workflow), [])
        op["authorized_change"] = {
            "intent": "token only",
            "explicitly_authorized_insertions": [
                {"kind": r["kind"], "text": r["text"]}
                for r in extract_immutable_tokens(REFERENCE)
            ],
        }
        errors = validate_workflow_insert_authorizations(workflow)
        self.assertTrue(any("SOURCE_FIELD_MISSING" in e for e in errors), errors)


    def test_22_ledger_export_boundary_rejects_token_only(self):
        plan = self._compiled()
        target = self._insert_target(plan)
        def make_ledger(auth):
            op = PatchOperation(
                operation_id="LEDGER-INS-001", document="Manuscript",
                input_file=str(self.source), output_file=str(self.root / "out.docx"),
                part="word/document.xml", p_idx=0,
                find=target["anchor_text"], replace=target["inserted_text"],
                source_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest(),
                instruction_requirement_ids=("REQ-MT-10-INS",),
                target_identity_mode="legacy_p_idx_explicit",
                authorized_change=auth, action="insert_paragraph",
                anchor_text=target["anchor_text"], position="after",
                inserted_text=target["inserted_text"],
            )
            return RevisionLedger("round-test", patches=[Patch(
                patch_id="P-1", task_id="T-1", reviewer="Author", comment="Insert",
                proposed_response="Done", operations=[op],
            )])
        token_only = json.loads(json.dumps(target["authorized_change"]))
        token_only["explicitly_authorized_insertions"] = [
            {k: v for k, v in row.items() if k != PROVENANCE_FIELD}
            for row in token_only["explicitly_authorized_insertions"]
        ]
        exported = make_ledger(token_only).to_legacy_revision_plan()
        errors = validate_plan_insert_authorizations(exported)
        self.assertTrue(any("TOKEN_UNLINKED" in e for e in errors), errors)
        exported_ok = make_ledger(target["authorized_change"]).to_legacy_revision_plan()
        self.assertEqual(validate_plan_insert_authorizations(exported_ok), [])


if __name__ == "__main__":
    unittest.main()

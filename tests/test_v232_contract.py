from __future__ import annotations
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from lxml import etree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from co_reviewer.v232_contract import (
    ContractError, extract_immutable_tokens, validate_immutable_token_lock,
    narrow_revision, myers_diff, effective_run_properties, build_rpr_change_snapshot,
    attach_format_change, build_package_reference_graph, renumber_reference_graph,
    assert_safe_paragraph_split, assert_safe_paragraph_merge, commit_document_package,
    remediation_transition, compile_clean_from_marked, qn, W,
    build_revision_baseline_inventory, targeted_revision_gate,
    collect_revision_ids_from_lineage, extract_docx_text_for_reference_graph,
    validate_committed_document_package,
)
from co_reviewer.historical_revisions import resolve_historical_revisions
from co_reviewer.omml_converter import (
    OmmlConversionError, convert_latex_to_omml, create_omml_preview_docx,
)

class V232ContractTests(unittest.TestCase):
    def test_immutable_tokens_block_numeric_and_citation_drift(self):
        original = "The capacity was 125.0 mAh g-1 (n=3, p<0.05) [12, 13] (Fig. 2)."
        changed = "The capacity was 125 mAh g-1 (n=3, p<0.05) [12, 13] (Fig. 2)."
        result = validate_immutable_token_lock(original, changed)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "UNPROTECTED_TOKEN_TOUCHED")
        allowed = validate_immutable_token_lock(original, changed, {
            "explicitly_authorized_tokens": [{"kind": "quantity", "text": "125.0 mAh", "replacement": "125 mAh"}]
        })
        self.assertTrue(allowed["allowed"])

    def test_myers_diff_reconstructs_both_sides(self):
        a = ["The", " ", "old", " ", "value", "."]
        b = ["The", " ", "new", " ", "value", "."]
        script = myers_diff(a, b)
        self.assertEqual("".join(x for op, x in script if op in ("=", "-")), "".join(a))
        self.assertEqual("".join(x for op, x in script if op in ("=", "+")), "".join(b))
        result = narrow_revision("The old value is 10 mg.", "The new value is 10 mg.",
                                 authorized_change={"intent": "Replace old with new"})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["edits"]), 1)

    def test_diff_rejects_protected_numeric_drift(self):
        result = narrow_revision("The value is 10 mg.", "The value is 12 mg.",
                                 authorized_change={"intent": "Improve wording"})
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["token_lock"]["status"], "UNPROTECTED_TOKEN_TOUCHED")

    def test_effective_style_cascade_and_nonempty_false_snapshot(self):
        styles = f'''<w:styles xmlns:w="{W}">
          <w:docDefaults><w:rPrDefault><w:rPr><w:i/></w:rPr></w:rPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:styleId="Base"><w:rPr><w:b/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Body"><w:basedOn w:val="Base"/></w:style>
        </w:styles>'''.encode()
        effective = effective_run_properties(b"<doc/>", styles, "Body", None, None)
        self.assertIn("i", effective["properties"])
        self.assertIn("b", effective["properties"])
        snapshot = build_rpr_change_snapshot(effective, ["i", "b", "caps"])
        self.assertIsNotNone(snapshot.find(qn("i")))
        self.assertIsNotNone(snapshot.find(qn("b")))
        self.assertEqual(snapshot.find(qn("caps")).get(qn("val")), "0")

    def test_rprchange_forbidden_inside_insertion(self):
        ins = ET.Element(qn("ins"))
        run = ET.SubElement(ins, qn("r"))
        with self.assertRaisesRegex(ContractError, "FORBIDDEN"):
            attach_format_change(run, {"properties": {}}, ["i"], 1, "A", "2026-01-01T00:00:00Z")

    def test_package_graph_is_directed_and_renumber_requires_authorization(self):
        graph = build_package_reference_graph({
            "manuscript": "Figure 1. Main result. See Fig. S2.",
            "SI": "Figure S2. Detail. See Fig. 1.",
            "response": "We revised Fig. S2 and Figure 1."
        })
        self.assertTrue(any(e["source_document"] == "SI" and e["target_document"] == "manuscript" for e in graph["edges"]))
        with self.assertRaisesRegex(ContractError, "explicit"):
            renumber_reference_graph(graph, {"SI::S2": "S3"}, explicit_authorization=False)

    def test_package_graph_updates_resolved_mentions_only_and_includes_sections(self):
        graph = build_package_reference_graph({
            "manuscript": "Figure 1. Main result.\nSection 2.1. Methods.",
            "SI": "Figure S2. Detail.\nSee Eq. (3) and Section 2.1."
        })
        updated = renumber_reference_graph(graph, {"manuscript::1": "2", "manuscript::2.1": "2.2"},
                                           explicit_authorization=True)
        self.assertTrue(updated["numbering_mutation_authorized"])
        self.assertTrue(any(x["new_text"].endswith("2") for x in updated["mention_updates"]))
        self.assertTrue(any(x["new_number"] == "2.2" for x in updated["definition_updates"]))

    def test_paragraph_split_and_merge_guards(self):
        p = ET.fromstring(f'<w:p xmlns:w="{W}"><w:r><w:t>plain</w:t></w:r></w:p>')
        assert_safe_paragraph_split(p, 1)
        field = ET.fromstring(f'<w:p xmlns:w="{W}"><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>')
        with self.assertRaises(ContractError):
            assert_safe_paragraph_split(field, 1)
        nested_bookmark = ET.fromstring(f'<w:p xmlns:w="{W}"><w:hyperlink><w:bookmarkStart w:id="1" w:name="a"/></w:hyperlink></w:p>')
        with self.assertRaisesRegex(ContractError, "PROTECTED_OOXML"):
            assert_safe_paragraph_split(nested_bookmark, 1)
        left = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:p>')
        right = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr></w:p>')
        with self.assertRaisesRegex(ContractError, "LIST_SEMANTICS"):
            assert_safe_paragraph_merge(left, right)
        styled_left = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:pStyle w:val="A"/></w:pPr></w:p>')
        styled_right = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:pStyle w:val="B"/></w:pPr></w:p>')
        with self.assertRaisesRegex(ContractError, "PARAGRAPH_PROPERTIES"):
            assert_safe_paragraph_merge(styled_left, styled_right)

    def test_package_transaction_publishes_manifest_commit_marker_and_revalidates_docx_bytes(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as z:
            z.writestr("[Content_Types].xml", f'<Types xmlns="{ "http://schemas.openxmlformats.org/package/2006/content-types" }"/>')
            z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body/></w:document>')
        with tempfile.TemporaryDirectory() as tmp:
            result = commit_document_package({"manuscript/Marked.docx": payload.getvalue()}, tmp)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertTrue(Path(result["commit_marker"]).is_file())
            self.assertTrue((Path(result["directory"]) / "package_manifest.json").is_file())
            verified = validate_committed_document_package(
                result["directory"], result["commit_marker"]
            )
            self.assertEqual(verified["package_id"], result["package_id"])
            self.assertEqual(verified["documents"][0]["path"], "manuscript/Marked.docx")

            packaged_docx = Path(result["directory"]) / "manuscript" / "Marked.docx"
            packaged_docx.write_bytes(packaged_docx.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ContractError, "size/SHA-256 mismatch"):
                validate_committed_document_package(
                    result["directory"], result["commit_marker"]
                )

    def test_remediation_has_exactly_one_marked_reentry(self):
        patch = {"target_artifact": "Marked", "operation_id": "OP1", "find": "a", "replace": "b", "reason": "fix"}
        first = remediation_transition({"phase": "QA", "remediation_round": 0}, patch)
        self.assertEqual(first["next_phase"], "COMPILE_CLEAN")
        second = remediation_transition({"phase": "QA", "remediation_round": 1}, patch)
        self.assertEqual(second["phase"], "BLOCKED")
        passed = remediation_transition({
            "phase": "QA", "remediation_round": 1, "qa_status": "PASS"
        })
        self.assertEqual(passed["phase"], "COMPLETE")
        warned = remediation_transition({
            "phase": "QA", "remediation_round": 1, "qa_status": "PASS_WITH_WARNINGS"
        })
        self.assertEqual(warned["phase"], "COMPLETE")
        failed = remediation_transition({
            "phase": "QA", "remediation_round": 1, "qa_status": "FAIL"
        })
        self.assertEqual(failed["phase"], "BLOCKED")
        deletion = remediation_transition({"phase": "QA", "remediation_round": 0}, {
            "target_artifact": "Marked", "operation_id": "op-delete",
            "find": "obsolete phrase", "replace": "", "reason": "Remove duplicated text",
        })
        self.assertEqual(deletion["next_phase"], "COMPILE_CLEAN")


    def test_targeted_revision_gate_blocks_only_overlapping_history(self):
        inventory = {"revisions": [
            {"id": "r1", "part": "word/document.xml", "paragraph_index": 2, "kind": "text_revision_wrapper"},
            {"id": "r2", "part": "word/document.xml", "paragraph_index": 8, "kind": "text_revision_wrapper"},
        ]}
        blocked = targeted_revision_gate(inventory, [{"part": "word/document.xml", "p_idx": 2}])
        self.assertEqual(blocked["status"], "USER_DECISION_REQUIRED")
        selected = targeted_revision_gate(inventory, [{"part": "word/document.xml", "p_idx": 2}],
                                           resolution_policy="ACCEPT_EXISTING")
        self.assertEqual(selected["status"], "RESOLUTION_READY")
        self.assertTrue(selected["must_resolve_before_discovery_or_write"])
        self.assertFalse(selected["resolution_applied"])
        self.assertEqual(len(selected["unrelated_revisions_preserved"]), 1)

    def test_engine_blocks_unapproved_numeric_token_changes(self):
        from co_reviewer.document_engine import prepare_document
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.docx"
            output = Path(tmp) / "output.docx"
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as z:
                z.writestr("[Content_Types].xml", f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
                z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
                z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>The value is 10 mg.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
            source.write_bytes(payload.getvalue())
            with self.assertRaisesRegex(ValueError, "UNPROTECTED_TOKEN_TOUCHED"):
                prepare_document(str(source), str(output), [{"p_idx": 0, "find": "10 mg", "replace": "12 mg"}], "Tester")
            self.assertFalse(output.exists())

    def test_clean_compilation_is_bound_to_marked_bytes(self):
        # A minimal OPC package with a tracked insertion.
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as z:
            z.writestr("[Content_Types].xml", f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
            z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
            z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body><w:p><w:ins w:id="1" w:author="A"><w:r><w:t>new</w:t></w:r></w:ins></w:p></w:body></w:document>')
            z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        result = compile_clean_from_marked(payload.getvalue(), expected_revision_ids=["1"])
        self.assertEqual(len(result["marked_sha256"]), 64)
        self.assertEqual(len(result["clean_sha256"]), 64)
        self.assertEqual(result["historical_revision_policy"], "PASS_NO_UNREGISTERED_REVISION_IDS")
        with self.assertRaisesRegex(ContractError, "HISTORICAL_REVISION_POLICY_BLOCKED"):
            compile_clean_from_marked(payload.getvalue(), expected_revision_ids=[])


    def test_token_lock_blocks_unapproved_insertions_and_reordering(self):
        inserted = validate_immutable_token_lock("The result was stable.", "The result was 99 stable.")
        self.assertFalse(inserted["allowed"])
        reordered = validate_immutable_token_lock("The values were 10 and 20 mg.", "The values were 20 and 10 mg.")
        self.assertFalse(reordered["allowed"])

    def test_token_lock_allows_only_individually_authorized_multi_token_insertions(self):
        original = "The values were recorded."
        proposed = "The values were 1.2 and 3.4."
        approvals = [
            {"kind": "number", "text": "1.2"},
            {"kind": "number", "text": "3.4"},
        ]
        accepted = validate_immutable_token_lock(original, proposed, {
            "explicitly_authorized_insertions": approvals
        })
        self.assertTrue(accepted["allowed"], msg=str(accepted["conflicts"]))

        partial = validate_immutable_token_lock(original, proposed, {
            "explicitly_authorized_insertions": approvals[:1]
        })
        self.assertFalse(partial["allowed"])
        self.assertIn(
            "INSERTED_TOKEN_NOT_EXACTLY_AUTHORIZED",
            {item["reason"] for item in partial["conflicts"]},
        )

        repeated_value = validate_immutable_token_lock(
            "The measurements were recorded.",
            "The measurements were 1.2 and 1.2.",
            {"explicitly_authorized_insertions": approvals[:1]},
        )
        self.assertFalse(repeated_value["allowed"])
        repeated_value_approved = validate_immutable_token_lock(
            "The measurements were recorded.",
            "The measurements were 1.2 and 1.2.",
            {"explicitly_authorized_insertions": approvals[:1] * 2},
        )
        self.assertTrue(repeated_value_approved["allowed"])

        deleted_values = validate_immutable_token_lock(
            "The measurements were 1.2 and 3.4.",
            "The measurements were recorded.",
            {"explicitly_authorized_deletions": [
                {"kind": "number", "text": "1.2"},
                {"kind": "number", "text": "3.4"},
            ]},
        )
        self.assertTrue(deleted_values["allowed"])
        partially_deleted = validate_immutable_token_lock(
            "The measurements were 1.2 and 3.4.",
            "The measurements were recorded.",
            {"explicitly_authorized_deletions": [
                {"kind": "number", "text": "1.2"},
            ]},
        )
        self.assertFalse(partially_deleted["allowed"])
        self.assertIn(
            "DELETED_TOKEN_NOT_EXACTLY_AUTHORIZED",
            {item["reason"] for item in partially_deleted["conflicts"]},
        )

    def test_token_lock_requires_exact_old_to_new_authorization(self):
        original = "The value was 125.0 mg."
        proposed = "The value was 125 mg."
        wrong_pair = validate_immutable_token_lock(original, proposed, {
            "explicitly_authorized_tokens": [{"kind": "quantity", "text": "125.0 mg", "replacement": "126 mg"}]
        })
        self.assertFalse(wrong_pair["allowed"])
        exact_pair = validate_immutable_token_lock(original, proposed, {
            "explicitly_authorized_tokens": [{"kind": "quantity", "text": "125.0 mg", "replacement": "125 mg"}]
        })
        self.assertTrue(validate_immutable_token_lock("value 10 mg", "value 12 mg", {
            "explicitly_authorized_tokens": [{"kind": "quantity", "text": "10 mg", "replacement": "12 mg"}]
        })["allowed"])
        self.assertTrue(exact_pair["allowed"])


    def test_protected_spans_require_separate_exact_authorization(self):
        blocked = narrow_revision("alpha beta", "gamma beta",
                                  authorized_change={"intent": "Revise wording"},
                                  protected_spans=[{"start": 0, "end": 5}])
        self.assertEqual(blocked["status"], "BLOCKED")
        approved = narrow_revision("alpha beta", "gamma beta",
                                   authorized_change={"intent": "Revise wording",
                                       "explicitly_authorized_spans": [{"start": 0, "end": 5}]},
                                   protected_spans=[{"start": 0, "end": 5}])
        self.assertEqual(approved["status"], "PASS")
        insertion = narrow_revision("alpha beta", "alpha X beta",
                                    authorized_change={"intent": "Insert phrase"},
                                    protected_spans=[{"start": 0, "end": 10}])
        self.assertEqual(insertion["status"], "BLOCKED")


    def test_engine_blocks_only_target_overlapping_historical_revisions(self):
        from co_reviewer.document_engine import prepare_document
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "history.docx"
            output = Path(tmp) / "history_out.docx"
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as z:
                z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
                z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
                z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body><w:p><w:ins w:id="1" w:author="A"><w:r><w:t>old text</w:t></w:r></w:ins></w:p><w:p><w:r><w:t>unrelated text</w:t></w:r></w:p><w:sectPr/></w:body></w:document>')
            source.write_bytes(payload.getvalue())
            with self.assertRaisesRegex(ValueError, "HISTORICAL_REVISION_OVERLAP_USER_DECISION_REQUIRED"):
                prepare_document(str(source), str(output), [{
                    "p_idx": 0, "find": "old text", "replace": "new text",
                    "authorized_change": {"intent": "Revise wording"}
                }], "Tester")
            self.assertFalse(output.exists())
            unrelated = prepare_document(str(source), str(output), [{
                "p_idx": 1, "find": "unrelated text", "replace": "updated text",
                "authorized_change": {"intent": "Revise unrelated wording"}
            }], "Tester")
            self.assertTrue(unrelated["payload"])


    def test_package_commit_rejects_windows_traversal_and_case_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ContractError, "Unsafe package output path"):
                commit_document_package({"..\\escape.docx": b"not-a-docx"}, tmp)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
                archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body><w:p/></w:body></w:document>')
            valid_docx = payload.getvalue()
            with self.assertRaisesRegex(ContractError, "Duplicate or case-colliding"):
                commit_document_package({"A.docx": valid_docx, "a.DOCX": valid_docx}, tmp)


    def test_reference_renumber_requires_confirmed_ambiguous_mapping(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 1, "text": "Figure 1. Main result."},
                {"part": "word/document.xml", "paragraph_index": 2, "text": "Figure 2. Other result."},
            ],
            "SI": [
                {"part": "word/document.xml", "paragraph_index": 4, "text": "Figure 1. Supplementary result."},
                {"part": "word/document.xml", "paragraph_index": 5, "text": "See Figure 1 for details."},
            ],
            "Response": [
                {"part": "word/document.xml", "paragraph_index": 2, "text": "See Figure 1."},
            ],
        })
        mention = next(m for m in graph["mentions"] if m["document"] == "Response" and m["resolution"] == "ambiguous")
        with self.assertRaisesRegex(ContractError, "AMBIGUOUS_REFERENCE_MAPPING_REQUIRES_CONFIRMATION"):
            renumber_reference_graph(graph, {"Manuscript::1": "3"}, explicit_authorization=True)
        manuscript_definition = next(d for d in graph["definitions"] if d["document"] == "Manuscript" and d["number"] == "1")
        updated = renumber_reference_graph(
            graph, {"Manuscript::1": "3"}, explicit_authorization=True,
            confirmed_ambiguous_mappings={mention["id"]: manuscript_definition["id"]},
        )
        resolved = next(m for m in updated["mentions"] if m["id"] == mention["id"])
        self.assertEqual(resolved["resolution"], "confirmed_by_user")
        self.assertEqual(resolved["number"], "3")
        self.assertEqual(updated["ambiguous_count"], 0)
        self.assertEqual(updated["unresolved_count"], 0)
        response_edges = [edge for edge in updated["edges"] if edge["source_document"] == "Response"]
        self.assertEqual(len(response_edges), 1)
        self.assertEqual(response_edges[0]["target_document"], "Manuscript")
        self.assertEqual(response_edges[0]["target_definition_id"], manuscript_definition["id"])


    def test_revision_id_lineage_collects_all_incremental_ids_by_hash(self):
        records = [
            {"input_file": "source.docx", "input_sha256": "0" * 64,
             "output_file": "marked_round_1.docx", "output_sha256": "1" * 64,
             "revision_ids": ["1", "2"]},
            {"input_file": "marked_round_1.docx", "input_sha256": "1" * 64,
             "output_file": "marked_round_2.docx", "output_sha256": "2" * 64,
             "revision_ids": ["3", "4"]},
        ]
        result = collect_revision_ids_from_lineage(records, "marked_round_2.docx", "2" * 64)
        self.assertEqual(result["revision_ids"], ["1", "2", "3", "4"])
        self.assertEqual(len(result["lineage"]), 2)



    def test_reference_graph_normalizes_aliases_and_keeps_word_paragraph_anchors(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 2,
                 "text": "Figure 2a. Main result. See Fig. 2a."},
                {"part": "word/document.xml", "paragraph_index": 5,
                 "text": "Equation (3). Governing expression."},
            ],
            "SI": [
                {"part": "word/document.xml", "paragraph_index": 7,
                 "text": "See Eq. (3) and Figure 2a."},
            ],
            "Response": [
                {"part": "word/document.xml", "paragraph_index": 1,
                 "text": "We revised Figure 2a and Eq. (3)."},
            ],
        })
        equation_mention = next(
            item for item in graph["mentions"]
            if item["document"] == "SI" and item["kind"] == "equation"
        )
        self.assertEqual(equation_mention["resolution"], "resolved")
        self.assertEqual(equation_mention["resolution_scope"], "package")
        self.assertEqual(equation_mention["part"], "word/document.xml")
        self.assertEqual(equation_mention["paragraph_index"], 7)
        figure_mention = next(
            item for item in graph["mentions"]
            if item["document"] == "SI" and item["kind"] == "figure"
        )
        self.assertEqual(figure_mention["resolution"], "resolved")
        self.assertEqual(
            figure_mention["target_definition_ids"][0],
            next(d["id"] for d in graph["definitions"]
                 if d["document"] == "Manuscript" and d["number"] == "2a"),
        )

    def test_reference_graph_prefers_local_numbering_and_preserves_ambiguity(self):
        graph = build_package_reference_graph({
            "Manuscript": "Figure 1. Main manuscript result. See Figure 1.",
            "SI": "Figure 1. Supplementary result. See Figure 1.",
            "Response": "See Figure 1.",
        })
        local = next(m for m in graph["mentions"] if m["document"] == "Manuscript")
        self.assertEqual(local["resolution"], "resolved")
        self.assertEqual(local["resolution_scope"], "document")
        response = next(m for m in graph["mentions"] if m["document"] == "Response")
        self.assertEqual(response["resolution"], "ambiguous")
        self.assertEqual(len(response["candidate_definition_ids"]), 2)


    def test_effective_run_properties_resolves_default_style_theme_and_overlay_precedence(self):
        styles = f'''<w:styles xmlns:w="{W}">
          <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/><w:i/></w:rPr></w:rPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:rPr><w:b/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Body"><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="222222"/></w:rPr></w:style>
          <w:style w:type="character" w:styleId="Emphasis"><w:rPr><w:i w:val="0"/></w:rPr></w:style>
        </w:styles>'''.encode()
        theme = b'''<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements><a:fontScheme><a:majorFont><a:latin typeface="MajorFace"/><a:ea typeface="MajorEA"/><a:cs typeface="MajorCS"/></a:majorFont><a:minorFont><a:latin typeface="MinorFace"/><a:ea typeface="MinorEA"/><a:cs typeface="MinorCS"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>'''
        paragraph_direct = ET.fromstring(f'<w:rPr xmlns:w="{W}"><w:color w:val="333333"/></w:rPr>')
        direct = ET.fromstring(f'<w:rPr xmlns:w="{W}"><w:rStyle w:val="Emphasis"/><w:rFonts w:ascii="DirectFace"/></w:rPr>')
        effective = effective_run_properties(
            b'<w:document xmlns:w="' + W.encode() + b'"/>',
            styles, None, None, direct,
            paragraph_rpr=paragraph_direct,
            table_style_rpr=ET.fromstring(f'<w:rPr xmlns:w="{W}"><w:sz w:val="22"/></w:rPr>'),
            numbering_rpr=ET.fromstring(f'<w:rPr xmlns:w="{W}"><w:color w:val="111111"/></w:rPr>'),
            theme_xml=theme,
        )
        props = effective["properties"]
        self.assertEqual(props["rFonts"][qn("ascii")], "DirectFace")
        self.assertNotIn(qn("asciiTheme"), props["rFonts"])
        self.assertEqual(props["rFonts"][qn("hAnsi")], "MinorFace")
        self.assertIsNotNone(props["b"])
        self.assertEqual(props["i"][qn("val")], "0")
        self.assertEqual(props["color"][qn("val")], "333333")
        self.assertEqual(props["sz"][qn("val")], "22")

    def test_format_snapshot_blocks_unknown_non_boolean_old_dimensions(self):
        with self.assertRaisesRegex(ContractError, "unknown effective old value"):
            build_rpr_change_snapshot({"properties": {}}, ["color"])


    def _revision_docx(self, directory: Path) -> Path:
        path = directory / "historical.docx"
        document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <w:document xmlns:w="{W}"><w:body>
          <w:p><w:ins w:id="1" w:author="Old"><w:r><w:t>accepted text</w:t></w:r></w:ins></w:p>
          <w:p><w:del w:id="2" w:author="Old"><w:r><w:delText>deleted text</w:delText></w:r></w:del></w:p>
          <w:p><w:ins w:id="3" w:author="Other"><w:r><w:t>unrelated history</w:t></w:r></w:ins></w:p>
          <w:p><w:r><w:rPr><w:b/><w:rPrChange w:id="4" w:author="Old"><w:rPr><w:i/></w:rPr></w:rPrChange></w:rPr><w:t>formatted</w:t></w:r></w:p>
          <w:sectPr/>
        </w:body></w:document>'''
        members = {
            "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
            "word/document.xml": document_xml,
        }
        path = directory / "historical.docx"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, value in members.items():
                archive.writestr(name, value)
        return path

    def test_cli_historical_resolver_publishes_docx_and_hash_report(self):
        import hashlib
        import json
        import co_reviewer_cli as cli
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._revision_docx(root)
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"]
                        if (x.get("details") or {}).get("revision_id") == "1")
            inventory_path = root / "baseline.json"
            targets_path = root / "targets.json"
            output = root / "resolved.docx"
            report = root / "resolved.json"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            targets_path.write_text(json.dumps({"targets": [{
                "part": item["part"], "p_idx": item["paragraph_index"]
            }]}), encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = cli.main([
                    "resolve-historical-revisions",
                    "--marked", str(source),
                    "--inventory", str(inventory_path),
                    "--targets", str(targets_path),
                    "--policy", "ACCEPT_EXISTING",
                    "--out", str(output),
                    "--report", str(report),
                ])
            self.assertEqual(code, 0)
            self.assertEqual(source.read_bytes(), payload)
            self.assertTrue(output.is_file())
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(result["source_sha256"], hashlib.sha256(payload).hexdigest())
            self.assertEqual(result["output_sha256"], hashlib.sha256(output.read_bytes()).hexdigest())

    def test_cli_report_path_collision_does_not_publish_partial_docx(self):
        import json
        import co_reviewer_cli as cli
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._revision_docx(root)
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"]
                        if (x.get("details") or {}).get("revision_id") == "1")
            inventory_path = root / "baseline.json"
            targets_path = root / "targets.json"
            output = root / "resolved.docx"
            inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
            targets_path.write_text(json.dumps({"targets": [{
                "part": item["part"], "p_idx": item["paragraph_index"]
            }]}), encoding="utf-8")
            stderr = StringIO()
            with redirect_stdout(StringIO()), redirect_stderr(stderr):
                code = cli.main([
                    "resolve-historical-revisions",
                    "--marked", str(source),
                    "--inventory", str(inventory_path),
                    "--targets", str(targets_path),
                    "--policy", "ACCEPT_EXISTING",
                    "--out", str(output),
                    "--report", str(source),
                ])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertEqual(source.read_bytes(), payload)
            self.assertIn("Resolution report must use a new path", stderr.getvalue())

    def test_accept_historical_text_revision_only_in_target_and_preserve_unrelated(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._revision_docx(Path(tmp))
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"] if (x.get("details") or {}).get("revision_id") == "1")
            report = resolve_historical_revisions(payload, inventory, [
                {"part": item["part"], "p_idx": item["paragraph_index"]}
            ], policy="ACCEPT_EXISTING")
            self.assertEqual(report["status"], "RESOLVED")
            with zipfile.ZipFile(io.BytesIO(report["docx_bytes"])) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            paragraphs = root.findall(".//w:body/w:p", {"w": W})
            self.assertEqual("".join(paragraphs[0].itertext()), "accepted text")
            self.assertIsNone(paragraphs[0].find("w:ins", {"w": W}))
            self.assertIsNotNone(paragraphs[2].find("w:ins", {"w": W}))
            self.assertEqual(len(report["preserved_unrelated"]), 3)

    def test_reject_historical_deletion_restores_deleted_text_without_source_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._revision_docx(Path(tmp))
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"] if (x.get("details") or {}).get("revision_id") == "2")
            report = resolve_historical_revisions(payload, inventory, [
                {"part": item["part"], "p_idx": item["paragraph_index"]}
            ], policy="REJECT_EXISTING")
            with zipfile.ZipFile(io.BytesIO(report["docx_bytes"])) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.findall(".//w:body/w:p", {"w": W})[1]
            self.assertEqual("".join(paragraph.itertext()), "deleted text")
            self.assertIsNone(paragraph.find("w:del", {"w": W}))
            self.assertIsNotNone(paragraph.find(".//w:t", {"w": W}))
            self.assertEqual(source.read_bytes(), payload)

    def test_reject_format_revision_restores_previous_run_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._revision_docx(Path(tmp))
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"] if (x.get("details") or {}).get("revision_id") == "4")
            report = resolve_historical_revisions(payload, inventory, [
                {"part": item["part"], "p_idx": item["paragraph_index"]}
            ], policy="REJECT_EXISTING")
            with zipfile.ZipFile(io.BytesIO(report["docx_bytes"])) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            run_props = root.findall(".//w:body/w:p[4]/w:r/w:rPr", {"w": W})[0]
            self.assertIsNotNone(run_props.find("w:i", {"w": W}))
            self.assertIsNone(run_props.find("w:b", {"w": W}))
            self.assertIsNone(run_props.find("w:rPrChange", {"w": W}))

    def test_stale_baseline_and_unsupported_comment_history_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._revision_docx(Path(tmp))
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            with self.assertRaisesRegex(ContractError, "STALE"):
                resolve_historical_revisions(payload + b"x", inventory, [], policy="ACCEPT_EXISTING")
            inventory["revisions"].append({
                "structure_id": "comment-1", "kind": "comment_reference",
                "part": "word/document.xml", "paragraph_index": 0, "element": "commentReference",
                "element_path": "/w:document[1]/w:body[1]/w:p[1]/w:commentReference[1]",
            })
            with self.assertRaisesRegex(ContractError, "UNSUPPORTED_ADAPTER"):
                resolve_historical_revisions(payload, inventory, [
                    {"part": "word/document.xml", "p_idx": 0}
                ], policy="ACCEPT_EXISTING")

    def test_selective_resolution_requires_exact_structure_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._revision_docx(Path(tmp))
            payload = source.read_bytes()
            inventory = build_revision_baseline_inventory(payload)
            item = next(x for x in inventory["revisions"] if (x.get("details") or {}).get("revision_id") == "1")
            targets = [{"part": item["part"], "p_idx": item["paragraph_index"]}]
            report = resolve_historical_revisions(
                payload, inventory, targets, policy="SELECTIVE_ACCEPT_CONFLICTS",
                selectively_resolved_ids=[item["structure_id"]],
            )
            self.assertEqual(report["status"], "RESOLVED")
            self.assertEqual(len(report["resolved"]), 1)
            with self.assertRaisesRegex(ContractError, "SELECTED_REVISION_NOT"):
                resolve_historical_revisions(
                    payload, inventory, targets, policy="SELECTIVE_ACCEPT_CONFLICTS",
                    selectively_resolved_ids=["wrong-id"],
                )

    def test_omml_converter_creates_fraction_scripts_radical_and_greek(self):
        xml = convert_latex_to_omml(r"x_{i}^{2} = \frac{\alpha + 1}{\sqrt{b}} \pm \mathrm{mAh}")
        root = ET.fromstring(xml)
        self.assertEqual(ET.QName(root).localname, "oMath")
        self.assertTrue(root.findall(".//m:sSubSup", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}))
        self.assertTrue(root.findall(".//m:f", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}))
        self.assertTrue(root.findall(".//m:rad", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}))
        texts = "".join(node.text or "" for node in root.findall(".//m:t", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}))
        self.assertIn("α", texts)
        self.assertIn("±", texts)
        self.assertIn("mAh", texts)

    def test_omml_converter_rejects_unsupported_commands_and_malformed_groups(self):
        with self.assertRaisesRegex(OmmlConversionError, "Unsupported LaTeX command"):
            convert_latex_to_omml(r"\begin{matrix}a&b\end{matrix}")
        with self.assertRaisesRegex(OmmlConversionError, "Missing closing"):
            convert_latex_to_omml(r"\frac{a}{b")

    def test_omml_preview_docx_is_synthetic_and_contains_equation(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "equation_preview.docx"
            result = create_omml_preview_docx(r"E=mc^{2}", output)
            self.assertTrue(output.is_file())
            self.assertEqual(result["equation_count"], "1")
            with zipfile.ZipFile(output) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
                self.assertTrue(root.findall(".//m:oMath", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}))
                self.assertFalse(root.findall(".//w:ins", {"w": W}))
                self.assertFalse(root.findall(".//w:del", {"w": W}))

    def test_omml_preview_never_overwrites_existing_docx(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing.docx"
            output.write_bytes(b"keep")
            with self.assertRaisesRegex(OmmlConversionError, "refuses to overwrite"):
                create_omml_preview_docx("x^2", output)
            self.assertEqual(output.read_bytes(), b"keep")


    def test_package_graph_resolves_and_renumbers_static_numeric_citations(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "[1] Smith et al. Reference one."},
                {"part": "word/document.xml", "paragraph_index": 1, "text": "[2] Jones et al. Reference two."},
                {"part": "word/document.xml", "paragraph_index": 2, "text": "The method follows prior work [1, 2]."},
            ]
        })
        definitions = [row for row in graph["definitions"] if row["kind"] == "citation"]
        mentions = [row for row in graph["mentions"] if row["kind"] == "citation"]
        self.assertEqual({row["number"] for row in definitions}, {"1", "2"})
        self.assertEqual({row["number"] for row in mentions}, {"1", "2"})
        self.assertTrue(all(row["resolution"] == "resolved" for row in mentions))
        updated = renumber_reference_graph(
            graph,
            {"Manuscript::citation::1": "4"},
            explicit_authorization=True,
        )
        self.assertTrue(any(
            row["kind"] == "citation" and row["old_number"] == "1" and row["new_number"] == "4"
            for row in updated["definition_updates"]
        ))
        self.assertTrue(any(
            row["kind"] == "citation" and row["old_text"] == "1" and row["new_text"] == "4"
            for row in updated["mention_updates"]
        ))

    def test_legacy_reference_number_key_rejects_cross_kind_collision(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "Figure 1. Main result."},
                {"part": "word/document.xml", "paragraph_index": 1, "text": "[1] Smith et al. Reference one."},
                {"part": "word/document.xml", "paragraph_index": 2, "text": "See Fig. 1 and [1]."},
            ]
        })
        with self.assertRaisesRegex(ContractError, "UNIQUE_REFERENCE_KIND_REQUIRED"):
            renumber_reference_graph(
                graph, {"Manuscript::1": "2"}, explicit_authorization=True
            )

    def test_native_word_reference_fields_are_separate_and_block_number_propagation(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "Figure 1. Main result."},
                {
                    "part": "word/document.xml", "paragraph_index": 1,
                    "text": "See Figure 1 and [1].",
                    "reference_mechanism": "mixed_with_word_field",
                    "word_field_instructions": [" REF _Ref123 \\h "],
                    "word_field_markers_present": True,
                },
                {"part": "word/document.xml", "paragraph_index": 2, "text": "[1] Smith et al. Reference one."},
            ]
        })
        self.assertEqual(len(graph["field_references"]), 1)
        self.assertEqual(graph["field_references"][0]["field_type"], "REF")
        mixed_mentions = [
            row for row in graph["mentions"]
            if row["paragraph_index"] == 1
        ]
        self.assertTrue(mixed_mentions)
        self.assertTrue(all(row["resolution"] == "manual_review_required" for row in mixed_mentions))
        with self.assertRaisesRegex(ContractError, "NATIVE_WORD_FIELD_REFERENCE_REQUIRES_MANUAL_MAPPING"):
            renumber_reference_graph(
                graph, {"Manuscript::figure::1": "2"}, explicit_authorization=True
            )

    def test_page_number_fields_do_not_block_unrelated_reference_renumbering(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "Figure 1. Main result."},
                {
                    "part": "word/document.xml", "paragraph_index": 1,
                    "text": "Page 1",
                    "reference_mechanism": "mixed_with_word_field",
                    "word_field_instructions": [" PAGE "],
                    "word_field_markers_present": True,
                },
            ]
        })
        self.assertEqual(graph["field_references"][0]["field_type"], "PAGE")
        updated = renumber_reference_graph(
            graph, {"Manuscript::figure::1": "2"}, explicit_authorization=True
        )
        self.assertEqual(updated["definition_updates"][0]["new_number"], "2")

    def test_citation_ranges_require_manual_mapping_before_numbering(self):
        graph = build_package_reference_graph({
            "Manuscript": [
                {"part": "word/document.xml", "paragraph_index": 0, "text": "[1] Smith. One."},
                {"part": "word/document.xml", "paragraph_index": 1, "text": "[2] Jones. Two."},
                {"part": "word/document.xml", "paragraph_index": 2, "text": "[3] Lee. Three."},
                {"part": "word/document.xml", "paragraph_index": 3, "text": "See prior work [1-3]."},
            ]
        })
        range_mention = next(
            row for row in graph["mentions"]
            if row["kind"] == "citation" and row["number"] == "1-3"
        )
        self.assertEqual(range_mention["resolution"], "manual_review_required")
        with self.assertRaisesRegex(ContractError, "UNRESOLVED_REFERENCE_BLOCKS_NUMBERING_PROPAGATION"):
            renumber_reference_graph(
                graph, {"Manuscript::citation::1": "4"}, explicit_authorization=True
            )



if __name__ == "__main__":
    unittest.main()

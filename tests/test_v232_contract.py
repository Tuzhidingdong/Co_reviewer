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
)

class V232ContractTests(unittest.TestCase):
    def test_immutable_tokens_block_numeric_and_citation_drift(self):
        original = "The capacity was 125.0 mAh g-1 (n=3, p<0.05) [12, 13] (Fig. 2)."
        changed = "The capacity was 125 mAh g-1 (n=3, p<0.05) [12, 13] (Fig. 2)."
        result = validate_immutable_token_lock(original, changed)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "UNPROTECTED_TOKEN_TOUCHED")
        allowed = validate_immutable_token_lock(original, changed, {
            "explicitly_authorized_tokens": [{"kind": "number", "text": "125.0"}]
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
        snapshot = build_rpr_change_snapshot(effective, ["i", "b"])
        self.assertEqual(snapshot.find(qn("i")).get(qn("val")), "0")
        self.assertIsNotNone(snapshot.find(qn("b")))

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

    def test_paragraph_split_and_merge_guards(self):
        p = ET.fromstring(f'<w:p xmlns:w="{W}"><w:r><w:t>plain</w:t></w:r></w:p>')
        assert_safe_paragraph_split(p, 1)
        field = ET.fromstring(f'<w:p xmlns:w="{W}"><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>')
        with self.assertRaises(ContractError):
            assert_safe_paragraph_split(field, 1)
        left = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:p>')
        right = ET.fromstring(f'<w:p xmlns:w="{W}"><w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr></w:p>')
        with self.assertRaisesRegex(ContractError, "LIST_SEMANTICS"):
            assert_safe_paragraph_merge(left, right)

    def test_package_transaction_publishes_manifest_commit_marker(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as z:
            z.writestr("[Content_Types].xml", f'<Types xmlns="{ "http://schemas.openxmlformats.org/package/2006/content-types" }"/>')
            z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body/></w:document>')
        with tempfile.TemporaryDirectory() as tmp:
            result = commit_document_package({"manuscript/Marked.docx": payload.getvalue()}, tmp)
            self.assertEqual(result["status"], "COMMITTED")
            self.assertTrue(Path(result["commit_marker"]).is_file())
            self.assertTrue((Path(result["directory"]) / "package_manifest.json").is_file())

    def test_remediation_has_exactly_one_marked_reentry(self):
        patch = {"target_artifact": "Marked", "operation_id": "OP1", "find": "a", "replace": "b", "reason": "fix"}
        first = remediation_transition({"phase": "QA", "remediation_round": 0}, patch)
        self.assertEqual(first["next_phase"], "COMPILE_CLEAN")
        second = remediation_transition({"phase": "QA", "remediation_round": 1}, patch)
        self.assertEqual(second["phase"], "BLOCKED")

    def test_clean_compilation_is_bound_to_marked_bytes(self):
        # A minimal OPC package with a tracked insertion.
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as z:
            z.writestr("[Content_Types].xml", f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')
            z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
            z.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body><w:p><w:ins w:id="1" w:author="A"><w:r><w:t>new</w:t></w:r></w:ins></w:p></w:body></w:document>')
            z.writestr("word/_rels/document.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        result = compile_clean_from_marked(payload.getvalue())
        self.assertEqual(len(result["marked_sha256"]), 64)
        self.assertEqual(len(result["clean_sha256"]), 64)
        self.assertEqual(result["historical_revision_policy"], "BLOCKED_UNLESS_BASELINE_EXPLICITLY_RESOLVED")

if __name__ == "__main__":
    unittest.main()

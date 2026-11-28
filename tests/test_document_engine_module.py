from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from co_reviewer.document_engine import (
    RevisionDocumentEngine,
    W,
    next_revision_id,
    normalize_script_characters,
    parse_xml,
    qn,
    safe_part,
    visible_text,
    map_target_text_to_xml_nodes,
    apply_document,
)
from co_reviewer.docx_identity import (
    build_document_identity_manifest,
    build_target_refs_by_task,
)
from co_reviewer.identity import sha256_file

NS = {"w": W}


class DocumentEngineModuleBoundaryTests(unittest.TestCase):
    def test_extracted_engine_facade_exposes_existing_primitives(self):
        self.assertEqual(RevisionDocumentEngine.normalize_script_characters("SO₄²⁻"), "SO42−")
        self.assertEqual(RevisionDocumentEngine.safe_part("word/document.xml"), "word/document.xml")

    def test_visible_text_preserves_tab_and_break_projection(self):
        xml = (
            f'<w:p xmlns:w="{W}">'
            '<w:r><w:t>Zn</w:t><w:tab/><w:t>2+</w:t><w:br/><w:t>aq</w:t></w:r>'
            '</w:p>'
        ).encode("utf-8")
        paragraph = parse_xml(xml)
        self.assertEqual(visible_text(paragraph), "Zn\t2+\naq")

    def test_structural_event_map_resolves_exact_runs_text_nodes_and_xml_order(self):
        root = parse_xml((
            f'<w:document xmlns:w="{W}"><w:body><w:p>'
            '<w:r><w:t>prefix target </w:t><w:t>middle</w:t></w:r>'
            '<w:r><w:t> suffix end</w:t></w:r>'
            '</w:p></w:body></w:document>'
        ).encode("utf-8"))
        paragraph = root.find(".//w:p", NS)
        event_map = map_target_text_to_xml_nodes(
            root, paragraph, "target middle suffix"
        )
        self.assertEqual(event_map["status"], "MAPPED")
        self.assertTrue(event_map["mapping_reconstructed_exactly"])
        self.assertEqual(
            [run["target_text"] for run in event_map["run_map"]],
            ["target middle", " suffix"],
        )
        text_nodes = [
            node for run in event_map["run_map"] for node in run["text_nodes"]
        ]
        self.assertEqual(
            [node["target_text"] for node in text_nodes],
            ["target ", "middle", " suffix"],
        )
        self.assertEqual(
            [node["text_node_text_start"] for node in text_nodes],
            [7, 0, 0],
        )
        self.assertEqual(
            [node["text_node_document_order"] for node in text_nodes],
            sorted(node["text_node_document_order"] for node in text_nodes),
        )
        self.assertTrue(all(node["text_node_xml_path"].startswith("/") for node in text_nodes))

    def test_structural_event_map_records_nested_bookmark_comment_field_hyperlink_and_revision_events(self):
        root = parse_xml((
            f'<w:document xmlns:w="{W}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<w:body><w:p>'
            '<w:bookmarkStart w:id="1" w:name="target-anchor"/>'
            '<w:commentRangeStart w:id="7"/>'
            '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            '<w:r><w:instrText> REF _Ref7 </w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:hyperlink r:id="rId4"><w:r><w:t>target phrase</w:t></w:r></w:hyperlink>'
            '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
            '<w:commentRangeEnd w:id="7"/><w:bookmarkEnd w:id="1"/>'
            '<w:commentReference w:id="7"/>'
            '<w:ins w:id="8" w:author="Test"><w:r><w:t> after</w:t></w:r></w:ins>'
            '</w:p></w:body></w:document>'
        ).encode("utf-8"))
        paragraph = root.find(".//w:p", NS)
        event_map = map_target_text_to_xml_nodes(root, paragraph, "target phrase")
        self.assertTrue(event_map["structural_event_coverage_complete"])
        kinds = [event["event_kind"] for event in event_map["structural_events"]]
        for expected in ("bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "commentReference", "fldChar", "instrText", "hyperlink", "ins"):
            self.assertIn(expected, kinds)
        bookmark = next(event for event in event_map["structural_events"] if event["event_kind"] == "bookmarkStart")
        self.assertEqual(bookmark["attributes"]["name"], "target-anchor")
        hyperlink = next(event for event in event_map["structural_events"] if event["event_kind"] == "hyperlink")
        self.assertEqual(hyperlink["target_relation"], "contains_target")
        self.assertTrue(all(event["xml_path"].startswith("/") for event in event_map["structural_events"]))
        self.assertEqual(len(event_map["field_ranges"]), 1)
        self.assertTrue(event_map["field_ranges"][0]["complete"])
        self.assertTrue(event_map["field_ranges"][0]["target_in_field_result"])
        self.assertTrue(any(event["target_relation"] == "at_target_start" for event in event_map["boundary_events"]))

    def test_structural_event_map_fails_closed_when_target_crosses_non_run_boundary(self):
        root = parse_xml((
            f'<w:document xmlns:w="{W}"><w:body><w:p>'
            '<w:r><w:t>before</w:t></w:r>'
            '<w:bookmarkStart w:id="1" w:name="anchor"/>'
            '<w:r><w:t>after</w:t></w:r>'
            '</w:p></w:body></w:document>'
        ).encode("utf-8"))
        paragraph = root.find(".//w:p", NS)
        with self.assertRaisesRegex(ValueError, "contiguous ordinary Run sequence"):
            map_target_text_to_xml_nodes(root, paragraph, "beforeafter")

    def test_revision_id_scans_existing_native_revision_wrappers(self):
        xml = (
            f'<w:document xmlns:w="{W}">'
            '<w:body><w:p><w:ins w:id="3"/><w:del w:id="8"/></w:p></w:body>'
            '</w:document>'
        ).encode("utf-8")
        self.assertEqual(next_revision_id(parse_xml(xml)), 9)

    def test_safe_part_rejects_traversal_and_unsupported_parts(self):
        with self.assertRaises(ValueError):
            safe_part("../word/document.xml")
        with self.assertRaises(ValueError):
            safe_part("word/comments.xml")

    def test_qn_uses_the_wordprocessingml_namespace(self):
        self.assertEqual(qn("p"), f"{{{W}}}p")


    def test_registered_target_ref_reaches_native_docx_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root_dir = Path(temporary)
            source = root_dir / "source.docx"
            output = root_dir / "marked.docx"
            document = Document()
            document.add_paragraph("First paragraph stays unchanged.")
            document.add_paragraph("Second stable phrase.")
            document.save(source)
            source_bytes = source.read_bytes()
            source_sha256 = sha256_file(source)

            inventory = [{
                "role": "Manuscript",
                "file": str(source),
                "source_sha256": source_sha256,
                "paragraphs": [],
            }]
            manifest, _ = build_document_identity_manifest(inventory)
            task = {
                "task_id": "T-STABLE-1",
                "targets": [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 1,
                    "action": "replace",
                }],
            }
            refs_by_task, identity_errors = build_target_refs_by_task(
                [task], manifest
            )
            self.assertEqual(identity_errors, [])
            self.assertEqual(len(refs_by_task["T-STABLE-1"]), 1)
            target_ref = refs_by_task["T-STABLE-1"][0]

            apply_document(
                str(source),
                str(output),
                [{
                    "document": "Manuscript",
                    "part": "word/document.xml",
                    "p_idx": 1,
                    "find": "Second stable phrase.",
                    "replace": "Second revised phrase.",
                    "source_sha256": source_sha256,
                    "target_ref": target_ref,
                    "target_identity_mode": "stable_target_ref",
                }],
                "Identity integration test",
            )
            self.assertEqual(source.read_bytes(), source_bytes)
            with zipfile.ZipFile(output) as archive:
                marked_root = parse_xml(archive.read("word/document.xml"))
                paragraphs = marked_root.findall(".//w:body/w:p", NS)
                self.assertEqual(len(paragraphs), 2)
                self.assertEqual(
                    visible_text(paragraphs[0]),
                    "First paragraph stays unchanged.",
                )
                self.assertEqual(
                    visible_text(paragraphs[1]),
                    "Second revised phrase.",
                )
                deleted = "".join(
                    node.text or ""
                    for node in paragraphs[1].findall(
                        ".//w:del//w:delText", NS
                    )
                )
                inserted = "".join(
                    node.text or ""
                    for node in paragraphs[1].findall(".//w:ins//w:t", NS)
                )
                self.assertEqual(deleted, "Second stable phrase.")
                self.assertEqual(inserted, "Second revised phrase.")

            # A matching paragraph index and source excerpt must never rescue
            # a TargetRef whose parser-backed object fingerprint is stale.
            tampered_ref = dict(target_ref)
            tampered_ref["object_fingerprint"] = "0" * 64
            tampered_output = root_dir / "tampered-ref.docx"
            with self.assertRaisesRegex(
                ValueError,
                "exactly one current DOCX object",
            ):
                apply_document(
                    str(source),
                    str(tampered_output),
                    [{
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 1,
                        "find": "Second stable phrase.",
                        "replace": "Unsafe fallback.",
                        "source_sha256": source_sha256,
                        "target_ref": tampered_ref,
                        "target_identity_mode": "stable_target_ref",
                    }],
                    "Identity integration test",
                )
            self.assertFalse(tampered_output.exists())

            # TargetRefs are revision-bound: changing the DOCX invalidates the
            # old reference even when its paragraph hint and visible text remain.
            changed_source = root_dir / "changed-source.docx"
            changed_document = Document(source)
            changed_document.add_paragraph("Unrelated later addition.")
            changed_document.save(changed_source)
            stale_output = root_dir / "stale-ref.docx"
            with self.assertRaisesRegex(
                ValueError,
                "revision_id does not match the exact source DOCX version",
            ):
                apply_document(
                    str(changed_source),
                    str(stale_output),
                    [{
                        "document": "Manuscript",
                        "part": "word/document.xml",
                        "p_idx": 1,
                        "find": "Second stable phrase.",
                        "replace": "Stale reference must block.",
                        "source_sha256": sha256_file(changed_source),
                        "target_ref": target_ref,
                        "target_identity_mode": "stable_target_ref",
                    }],
                    "Identity integration test",
                )
            self.assertFalse(stale_output.exists())



if __name__ == "__main__":
    unittest.main()

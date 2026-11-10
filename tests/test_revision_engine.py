import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli

def q(local):
    return f"{{{cli.W}}}{local}"

def make_docx(path, chunks):
    body = ET.Element(q("body"))
    for paragraph_chunks in chunks:
        p = ET.SubElement(body, q("p"))
        for value, bold in paragraph_chunks:
            r = ET.SubElement(p, q("r"))
            if bold:
                props = ET.SubElement(r, q("rPr"))
                ET.SubElement(props, q("b"))
            t = ET.SubElement(r, q("t"))
            t.text = value
    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
        "word/media/image1.png": b"fixture-image-bytes"
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return members

class RevisionEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "input.docx"
        self.output = self.root / "output.docx"

    def tearDown(self):
        self.temp.cleanup()

    def test_replacement_preserves_run_format_and_package_members(self):
        original = make_docx(self.input, [[("The ", False), ("old ", True), ("phrase", False), (" remains.", False)]])
        report = cli.apply_document(str(self.input), str(self.output), [{
            "p_idx": 0, "find": "old phrase", "replace": "new wording"
        }], "Test Author")
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            self.assertEqual(cli.visible_text(paragraph), "The new wording remains.")
            deleted = "".join(x.text or "" for x in paragraph.findall(".//w:delText", cli.NS))
            inserted = "".join(x.text or "" for x in paragraph.findall(".//w:ins/w:r/w:t", cli.NS))
            self.assertEqual(deleted, "old phrase")
            self.assertEqual(inserted, "new wording")
            self.assertIsNotNone(paragraph.find(".//w:del/w:r/w:rPr/w:b", cli.NS))
            self.assertIsNotNone(paragraph.find(".//w:ins/w:r/w:rPr/w:b", cli.NS))
            self.assertEqual(archive.read("word/media/image1.png"), original["word/media/image1.png"])
            self.assertEqual(set(archive.namelist()), set(original))
            # The prefix and suffix still exist as ordinary text outside revisions.
            self.assertEqual("".join(x.text or "" for x in paragraph.findall("./w:r/w:t", cli.NS)), "The  remains.")
        self.assertEqual(report["validation"]["untouched_parts"], "byte-identical")

    def test_trailing_runs_are_preserved_when_target_ends_mid_run(self):
        make_docx(self.input, [[
            ("before ", False),
            ("abc plus suffix", True),
            (" and more later text", False)
        ]])
        report = cli.apply_document(str(self.input), str(self.output), [{
            "p_idx": 0, "find": "abc", "replace": "XYZ"
        }], "Test Author")
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            self.assertEqual(cli.visible_text(paragraph), "before XYZ plus suffix and more later text")
            deleted = "".join(x.text or "" for x in paragraph.findall(".//w:del//w:delText", cli.NS))
            inserted = "".join(x.text or "" for x in paragraph.findall(".//w:ins//w:t", cli.NS))
            self.assertEqual(deleted, "abc")
            self.assertEqual(inserted, "XYZ")
        self.assertEqual(report["edits_applied"], 1)

    def test_apply_report_verifies_edits_and_response_letter_marks_draft(self):
        make_docx(self.input, [[("The mechanism is unclear.", False)]])
        plan = {
            "schema_version": "1.0", "approved": True,
            "revision_items": [{
                "id": "R1-C1", "reviewer": "Reviewer 1",
                "comment": "Please clarify the mechanism.",
                "proposed_response": "We have clarified the mechanism.",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript", "input_file": str(self.input),
                    "output_file": str(self.output), "p_idx": 0,
                    "location": "Section 3.2", "find": "mechanism is unclear",
                    "replace": "mechanism is now explained in detail"
                }]
            }]
        }
        reports = cli.apply_plan(plan, "Test Author")
        self.assertEqual(reports[0]["results"][0]["revision_item_id"], "R1-C1")
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_verified"], 1)
        plan["revision_items"][0]["status"] = "applied"
        draft = cli.render_response_letter(plan)
        self.assertIn("DRAFT (applied; not yet verified)", draft)
        self.assertIn("Section 3.2", draft)
        plan["revision_items"][0]["status"] = "verified"
        final_draft = cli.render_response_letter(plan)
        self.assertIn("**Response**", final_draft)
        self.assertNotIn("not yet verified", final_draft)


    def test_verifier_requires_the_exact_revision_id(self):
        make_docx(self.input, [[("old wording", False)]])
        # Put a pre-existing deletion with the same text in the source. A substring-only
        # verifier would incorrectly pass if the new deletion record disappeared.
        with zipfile.ZipFile(self.input, "r") as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(members["word/document.xml"])
        paragraph = root.find(".//w:p", cli.NS)
        old_deletion = ET.Element(q("del"), {q("id"): "41"})
        old_run = ET.SubElement(old_deletion, q("r"))
        ET.SubElement(old_run, q("delText")).text = "old wording"
        old_insertion = ET.Element(q("ins"), {q("id"): "42"})
        new_run = ET.SubElement(old_insertion, q("r"))
        ET.SubElement(new_run, q("t")).text = "some other earlier insertion"
        paragraph.insert(0, old_deletion)
        paragraph.insert(1, old_insertion)
        members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(self.input, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)

        plan = {
            "schema_version": "1.0", "approved": True,
            "revision_items": [{
                "id": "R1-C9", "reviewer": "Reviewer 1",
                "comment": "Please correct this wording.",
                "proposed_response": "We corrected the wording.",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript", "input_file": str(self.input),
                    "output_file": str(self.output), "p_idx": 0,
                    "location": "Section 1", "find": "old wording",
                    "replace": "new wording"
                }]
            }]
        }
        reports = cli.apply_plan(plan, "Test Author")
        edit = reports[0]["results"][0]
        self.assertGreater(edit["delete_revision_id"], 42)
        # Remove the new deletion but retain the earlier deletion with identical text.
        with zipfile.ZipFile(self.output, "r") as archive:
            output_members = {name: archive.read(name) for name in archive.namelist()}
        out_root = ET.fromstring(output_members["word/document.xml"])
        out_paragraph = out_root.find(".//w:p", cli.NS)
        for node in list(out_paragraph.findall(".//w:del", cli.NS)):
            if node.get(q("id")) == str(edit["delete_revision_id"]):
                for parent in out_paragraph.iter():
                    if node in list(parent):
                        parent.remove(node)
                        break
        output_members["word/document.xml"] = ET.tostring(out_root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(self.output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in output_members.items():
                archive.writestr(name, data)

        verification = cli.verify_apply_report(plan, reports)
        self.assertFalse(verification["passed"])
        self.assertEqual(verification["results"][0]["status"], "failed")
        self.assertFalse(verification["results"][0]["checks"]["exact_deletion_match"])


    def test_multiple_edits_in_one_paragraph_keep_unique_revision_ids(self):
        make_docx(self.input, [[
            ("First old phrase; second old phrase.", False)
        ]])
        plan = {
            "schema_version": "1.0", "approved": True,
            "revision_items": [
                {
                    "id": "R1-C1", "reviewer": "Reviewer 1", "comment": "Update the first phrase.",
                    "status": "approved",
                    "targets": [{
                        "document": "Manuscript", "input_file": str(self.input),
                        "output_file": str(self.output), "p_idx": 0, "location": "First sentence",
                        "find": "First old phrase", "replace": "First revised phrase"
                    }]
                },
                {
                    "id": "R1-C2", "reviewer": "Reviewer 1", "comment": "Update the second phrase.",
                    "status": "approved",
                    "targets": [{
                        "document": "Manuscript", "input_file": str(self.input),
                        "output_file": str(self.output), "p_idx": 0, "location": "Second sentence",
                        "find": "second old phrase", "replace": "second revised phrase"
                    }]
                }
            ]
        }
        reports = cli.apply_plan(plan, "Test Author")
        edits = reports[0]["results"]
        self.assertEqual(len(edits), 2)
        self.assertEqual(len({e["delete_revision_id"] for e in edits}), 2)
        self.assertEqual(len({e["insert_revision_id"] for e in edits}), 2)
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_verified"], 2)
        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            self.assertEqual(
                cli.visible_text(paragraph),
                "First revised phrase; second revised phrase."
            )


    def test_deletion_only_revision_is_verified(self):
        make_docx(self.input, [[("Remove this redundant phrase from the sentence.", False)]])
        plan = {
            "schema_version": "1.0", "approved": True,
            "revision_items": [{
                "id": "R1-C7", "reviewer": "Reviewer 1",
                "comment": "Remove the redundant phrase.",
                "proposed_response": "We removed the redundant phrase.",
                "status": "approved",
                "targets": [{
                    "document": "Manuscript", "input_file": str(self.input),
                    "output_file": str(self.output), "p_idx": 0,
                    "location": "Section 2", "find": "redundant phrase",
                    "replace": ""
                }]
            }]
        }
        reports = cli.apply_plan(plan, "Test Author")
        edit = reports[0]["results"][0]
        self.assertIsNone(edit["insert_revision_id"])
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertTrue(verification["results"][0]["checks"]["exact_deletion_match"])
        self.assertTrue(verification["results"][0]["checks"]["exact_insertion_match"])

    def test_missing_target_fails_without_output(self):
        make_docx(self.input, [[("A sentence.", False)]])
        with self.assertRaisesRegex(ValueError, "not found"):
            cli.apply_document(str(self.input), str(self.output), [{
                "p_idx": 0, "find": "not here", "replace": "replacement"
            }], "Test Author")
        self.assertFalse(self.output.exists())

    def test_ambiguous_target_in_paragraph_fails(self):
        make_docx(self.input, [[("repeat and repeat", False)]])
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            cli.apply_document(str(self.input), str(self.output), [{
                "p_idx": 0, "find": "repeat", "replace": "change"
            }], "Test Author")
        self.assertFalse(self.output.exists())

    def test_text_inside_hyperlink_preserves_relationship_and_track_changes(self):
        make_docx(self.input, [[("placeholder", False)]])
        relationship_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        office_rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        with zipfile.ZipFile(self.input, "r") as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        body = ET.Element(cli.qn("body"))
        paragraph = ET.SubElement(body, cli.qn("p"))
        before = ET.SubElement(paragraph, cli.qn("r"))
        ET.SubElement(before, cli.qn("t")).text = "Before "
        hyperlink = ET.SubElement(
            paragraph, cli.qn("hyperlink"), {f"{{{office_rel_ns}}}id": "rId5"}
        )
        linked_run = ET.SubElement(hyperlink, cli.qn("r"))
        ET.SubElement(linked_run, cli.qn("t")).text = "original link text"
        after = ET.SubElement(paragraph, cli.qn("r"))
        ET.SubElement(after, cli.qn("t")).text = " after."
        root = ET.Element(cli.qn("document"))
        root.append(body)
        members["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        relationships = ET.Element(f"{{{relationship_ns}}}Relationships")
        ET.SubElement(relationships, f"{{{relationship_ns}}}Relationship", {
            "Id": "rId5",
            "Type": f"{office_rel_ns}/hyperlink",
            "Target": "https://example.org/original",
            "TargetMode": "External"
        })
        members["word/_rels/document.xml.rels"] = ET.tostring(
            relationships, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.input, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        rels_before = members["word/_rels/document.xml.rels"]

        cli.apply_document(str(self.input), str(self.output), [{
            "p_idx": 0, "find": "original link text", "replace": "updated link text"
        }], "Test Author")

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:p", cli.NS)
            hyperlink = paragraph.find("w:hyperlink", cli.NS)
            self.assertIsNotNone(hyperlink)
            self.assertEqual(hyperlink.get(f"{{{office_rel_ns}}}id"), "rId5")
            deleted = "".join(
                node.text or "" for node in hyperlink.findall(".//w:delText", cli.NS)
            )
            inserted = "".join(
                node.text or "" for node in hyperlink.findall(".//w:ins/w:r/w:t", cli.NS)
            )
            self.assertEqual(deleted, "original link text")
            self.assertEqual(inserted, "updated link text")
            self.assertEqual(
                cli.visible_text(paragraph), "Before updated link text after."
            )
            self.assertEqual(archive.read("word/_rels/document.xml.rels"), rels_before)

    def test_hyperlink_boundary_is_rejected(self):
        root = ET.Element(q("document"))
        body = ET.SubElement(root, q("body"))
        p = ET.SubElement(body, q("p"))
        r = ET.SubElement(p, q("r"))
        ET.SubElement(r, q("t")).text = "link "
        hyperlink = ET.SubElement(p, q("hyperlink"))
        link_run = ET.SubElement(hyperlink, q("r"))
        ET.SubElement(link_run, q("t")).text = "target"
        with zipfile.ZipFile(self.input, "w") as archive:
            archive.writestr("word/document.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
        with self.assertRaisesRegex(ValueError, "not found|refused"):
            cli.apply_document(str(self.input), str(self.output), [{
                "p_idx": 0, "find": "link target", "replace": "replacement"
            }], "Test Author")
        self.assertFalse(self.output.exists())

    def test_human_approval_is_required(self):
        plan = {
            "schema_version": "1.0", "approved": False,
            "revision_items": [{
                "id": "R1-C1", "comment": "Comment", "status": "planned",
                "targets": [{
                    "document": "Manuscript", "input_file": "in.docx",
                    "output_file": "out.docx", "find": "old", "replace": "new"
                }]
            }]
        }
        errors = cli.validate_plan(plan)
        self.assertTrue(any("approved" in error for error in errors))
        self.assertTrue(any("status" in error for error in errors))


    def test_multi_document_plan_preflights_before_writing(self):
        first_input = self.root / "first.docx"
        second_input = self.root / "second.docx"
        first_output = self.root / "first_revised.docx"
        second_output = self.root / "second_revised.docx"
        make_docx(first_input, [[("old wording", False)]])
        make_docx(second_input, [[("different wording", False)]])
        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [
                {
                    "id": "R1-C1", "reviewer": "Reviewer 1", "comment": "Update the manuscript.",
                    "status": "approved",
                    "targets": [{
                        "document": "Manuscript", "input_file": str(first_input),
                        "output_file": str(first_output), "p_idx": 0,
                        "find": "old wording", "replace": "new wording"
                    }]
                },
                {
                    "id": "R1-C2", "reviewer": "Reviewer 1", "comment": "Update the SI.",
                    "status": "approved",
                    "targets": [{
                        "document": "SI", "input_file": str(second_input),
                        "output_file": str(second_output), "p_idx": 0,
                        "find": "missing anchor", "replace": "replacement"
                    }]
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "not found"):
            cli.apply_plan(plan, "Test Author")
        self.assertFalse(first_output.exists())
        self.assertFalse(second_output.exists())


    def test_table_cell_text_is_revised_without_flattening_table_structure(self):
        make_docx(self.input, [[("document placeholder", False)]])
        with zipfile.ZipFile(self.input, "r") as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(members["word/document.xml"])
        body = root.find(".//w:body", cli.NS)
        table = ET.SubElement(body, q("tbl"))
        row = ET.SubElement(table, q("tr"))
        cell = ET.SubElement(row, q("tc"))
        paragraph = ET.SubElement(cell, q("p"))
        run = ET.SubElement(paragraph, q("r"))
        ET.SubElement(run, q("t")).text = "The table-cell value is outdated."
        members["word/document.xml"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.input, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)

        cli.apply_document(str(self.input), str(self.output), [{
            "find": "The table-cell value is outdated.",
            "replace": "The table-cell value is updated."
        }], "Test Author")

        with zipfile.ZipFile(self.output) as archive:
            output_root = ET.fromstring(archive.read("word/document.xml"))
            table = output_root.find(".//w:tbl", cli.NS)
            self.assertIsNotNone(table)
            self.assertEqual(len(table.findall(".//w:tr", cli.NS)), 1)
            self.assertEqual(len(table.findall(".//w:tc", cli.NS)), 1)
            self.assertEqual(
                cli.visible_text(table.find(".//w:p", cli.NS)),
                "The table-cell value is updated."
            )
            deleted = "".join(
                node.text or "" for node in table.findall(".//w:del//w:delText", cli.NS)
            )
            inserted = "".join(
                node.text or "" for node in table.findall(".//w:ins/w:r/w:t", cli.NS)
            )
            self.assertEqual(deleted, "The table-cell value is outdated.")
            self.assertEqual(inserted, "The table-cell value is updated.")

    def test_inspector_indexes_plain_reviewer_paragraphs(self):
        make_docx(self.input, [[("Reviewer 1: Please clarify the mechanism.", False)], [("Please add the missing control experiment.", False)]])
        report = cli.inspect_docx(str(self.input))
        self.assertEqual(report["paragraph_count"], 2)
        self.assertEqual(report["paragraphs"][0]["p_idx"], 0)
        self.assertIn("clarify the mechanism", report["paragraphs"][0]["text"])

if __name__ == "__main__":
    unittest.main()

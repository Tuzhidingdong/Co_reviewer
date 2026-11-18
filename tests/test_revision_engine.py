import contextlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
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

def seal_approved_items(plan):
    for item in plan["revision_items"]:
        if item.get("status") == "approved":
            for target in item.get("targets", []):
                target.setdefault("authorized_change", {
                    "intent": "Human-approved exact text correction",
                    "explicitly_authorized_tokens": [],
                    "explicitly_authorized_spans": [],
                })
            item["approval_fingerprint"] = cli.revision_item_fingerprint(item)
    return plan


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
        seal_approved_items(plan)
        reports = cli.apply_plan(plan, "Test Author")
        self.assertEqual(reports[0]["results"][0]["revision_item_id"], "R1-C1")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_verified"], 1)
        draft = cli.render_response_letter(plan)
        self.assertIn("DRAFT (applied; not yet verified)", draft)
        self.assertIn("Section 3.2", draft)
        plan_path = self.root / "edits_plan.json"
        report_path = self.root / "apply_report.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        report_path.write_text(json.dumps(reports), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Semantic review acknowledgement is required"):
            cli.cmd_confirm_verified(Namespace(
                plan=str(plan_path), apply_report=str(report_path), ids=["R1-C1"],
                semantic_review_confirmed=False
            ))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.cmd_confirm_verified(Namespace(
                plan=str(plan_path), apply_report=str(report_path), ids=["R1-C1"],
                semantic_review_confirmed=True
            )), 0)
        verified_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(verified_plan["revision_items"][0]["status"], "verified")
        self.assertEqual(cli.validate_plan(verified_plan), [])
        final_draft = cli.render_response_letter(verified_plan)
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
        history_paragraph = ET.SubElement(root.find(".//w:body", cli.NS), q("p"))
        history_paragraph.insert(0, old_deletion)
        history_paragraph.insert(1, old_insertion)
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
        seal_approved_items(plan)
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

        plan["revision_items"][0]["status"] = "applied"
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
        seal_approved_items(plan)
        reports = cli.apply_plan(plan, "Test Author")
        edits = reports[0]["results"]
        self.assertEqual(len(edits), 2)
        self.assertEqual(len({e["delete_revision_id"] for e in edits}), 2)
        self.assertEqual(len({e["insert_revision_id"] for e in edits}), 2)
        for item in plan["revision_items"]:
            item["status"] = "applied"
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
        seal_approved_items(plan)
        reports = cli.apply_plan(plan, "Test Author")
        edit = reports[0]["results"][0]
        self.assertIsNone(edit["insert_revision_id"])
        plan["revision_items"][0]["status"] = "applied"
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
            archive.writestr(
                "[Content_Types].xml",
                b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
            )
            archive.writestr(
                "_rels/.rels",
                b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
            )
            archive.writestr(
                "word/_rels/document.xml.rels",
                b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
            )
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
        self.assertTrue(any("Human approval gate" in error for error in errors))
        self.assertFalse(any(".status must be" in error for error in errors))


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
        for item in plan["revision_items"]:
            for target in item["targets"]:
                target["authorized_change"] = {
                    "intent": "Human-approved exact text correction",
                    "explicitly_authorized_tokens": [],
                }
        seal_approved_items(plan)
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
        body.remove(table)
        sect_pr = body.find("w:sectPr", cli.NS)
        if sect_pr is not None:
            body.insert(list(body).index(sect_pr), table)
        else:
            body.append(table)
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

    def test_real_python_docx_package_remains_openable_after_tracked_revision(self):
        from docx import Document

        document = Document()
        document.add_heading("Results", level=1)
        paragraph = document.add_paragraph()
        paragraph.add_run("The electrolyte concentration was ")
        emphasized = paragraph.add_run("0.5 M")
        emphasized.bold = True
        paragraph.add_run(" during cycling.")
        table = document.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        table.cell(0, 0).text = "Sample"
        table.cell(0, 1).text = "ZnSO4"
        section = document.sections[0]
        section.header.paragraphs[0].text = "Co_Reviewer package fixture"
        section.footer.paragraphs[0].text = "Internal review copy"
        document.core_properties.title = "DOCX integration fixture"
        document.save(self.input)

        report = cli.apply_document(str(self.input), str(self.output), [{
            "p_idx": 1,
            "find": "0.5 M",
            "replace": "1.0 M",
            "authorized_change": {
                "intent": "Correct the reported concentration",
                "explicitly_authorized_tokens": [{"kind": "quantity", "text": "0.5 M", "replacement": "1.0 M"}],
            },
        }], "Test Author")

        reopened = Document(self.output)
        self.assertEqual(reopened.core_properties.title, "DOCX integration fixture")
        self.assertEqual(reopened.paragraphs[0].style.name, "Heading 1")
        self.assertEqual(reopened.tables[0].cell(0, 0).text, "Sample")
        self.assertEqual(reopened.tables[0].cell(0, 1).text, "ZnSO4")
        self.assertEqual(reopened.sections[0].header.paragraphs[0].text, "Co_Reviewer package fixture")
        self.assertEqual(reopened.sections[0].footer.paragraphs[0].text, "Internal review copy")

        with zipfile.ZipFile(self.input) as source, zipfile.ZipFile(self.output) as revised:
            self.assertEqual(set(source.namelist()), set(revised.namelist()))
            for name in source.namelist():
                if name != "word/document.xml":
                    self.assertEqual(source.read(name), revised.read(name), name)

            root = ET.fromstring(revised.read("word/document.xml"))
            body_paragraphs = root.findall(".//w:body//w:p", cli.NS)
            target_paragraph = body_paragraphs[1]
            deletion = target_paragraph.find(".//w:del", cli.NS)
            insertion = target_paragraph.find(".//w:ins", cli.NS)
            self.assertIsNotNone(deletion)
            self.assertIsNotNone(insertion)
            self.assertEqual(deletion.get(q("author")), "Test Author")
            self.assertEqual(insertion.get(q("author")), "Test Author")
            self.assertTrue(deletion.get(q("date")))
            self.assertTrue(insertion.get(q("date")))
            self.assertEqual(
                "".join(node.text or "" for node in deletion.findall(".//w:delText", cli.NS)),
                "0.5 M"
            )
            self.assertEqual(
                "".join(node.text or "" for node in insertion.findall(".//w:t", cli.NS)),
                "1.0 M"
            )
            self.assertIsNotNone(insertion.find(".//w:rPr/w:b", cli.NS))

        self.assertEqual(report["validation"]["package_member_inventory"], "unchanged")
        self.assertEqual(report["validation"]["untouched_parts"], "byte-identical")

    def test_one_revision_item_tracks_and_verifies_body_header_and_footer(self):
        from docx import Document

        document = Document()
        document.add_paragraph("The electrolyte concentration was 0.5 M.")
        section = document.sections[0]
        section.header.paragraphs[0].text = "Manuscript ID: ABC-01 | archive"
        section.footer.paragraphs[0].text = "Internal review copy 01 - confidential"
        document.save(self.input)

        with zipfile.ZipFile(self.input) as archive:
            names = set(archive.namelist())
        self.assertIn("word/document.xml", names)
        self.assertIn("word/header1.xml", names)
        self.assertIn("word/footer1.xml", names)

        inventory = cli.inspect_docx(str(self.input))
        indexed_parts = {entry["part"] for entry in inventory["paragraphs"]}
        self.assertTrue({
            "word/document.xml", "word/header1.xml", "word/footer1.xml"
        }.issubset(indexed_parts))
        self.assertTrue(any(
            entry["part"] == "word/header1.xml" and "ABC-01" in entry["text"]
            for entry in inventory["paragraphs"]
        ))
        self.assertTrue(any(
            entry["part"] == "word/footer1.xml" and "Internal review copy 01" in entry["text"]
            for entry in inventory["paragraphs"]
        ))

        plan = {
            "schema_version": "1.0",
            "approved": True,
            "revision_items": [{
                "id": "R1-C12",
                "reviewer": "Reviewer 1",
                "comment": "Keep the manuscript text and document labels consistent.",
                "proposed_response": "We updated all three locations consistently.",
                "status": "approved",
                "targets": [
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "part": "word/document.xml",
                        "p_idx": 0,
                        "find": "0.5 M",
                        "replace": "1.0 M",
                        "location": "Main text",
                        "authorized_change": {
                            "intent": "Human-approved concentration correction",
                            "explicitly_authorized_tokens": [{"kind": "quantity", "text": "0.5 M", "replacement": "1.0 M"}],
                        }
                    },
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "part": "word/header1.xml",
                        "p_idx": 0,
                        "find": "ABC-01",
                        "replace": "ABC-02",
                        "location": "Header",
                        "authorized_change": {
                            "intent": "Human-approved identifier correction",
                            "explicitly_authorized_tokens": [{"kind": "number", "text": "01", "replacement": "02"}],
                        }
                    },
                    {
                        "document": "Manuscript",
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                        "part": "word/footer1.xml",
                        "p_idx": 0,
                        "find": "Internal review copy 01",
                        "replace": "Internal review copy 02",
                        "location": "Footer",
                        "authorized_change": {
                            "intent": "Human-approved footer identifier correction",
                            "explicitly_authorized_tokens": [{"kind": "number", "text": "01", "replacement": "02"}],
                        }
                    }
                ]
            }]
        }
        seal_approved_items(plan)
        reports = cli.apply_plan(plan, "Test Author")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["edits_applied"], 3)
        self.assertEqual(
            {result["part"] for result in reports[0]["results"]},
            {"word/document.xml", "word/header1.xml", "word/footer1.xml"}
        )
        self.assertEqual(
            len({result["delete_revision_id"] for result in reports[0]["results"]}),
            3
        )
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, reports)
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_total"], 3)
        self.assertEqual(verification["targets_verified"], 3)

        with zipfile.ZipFile(self.output) as source:
            for part, old, new in (
                ("word/document.xml", "0.5 M", "1.0 M"),
                ("word/header1.xml", "ABC-01", "ABC-02"),
                ("word/footer1.xml", "Internal review copy 01", "Internal review copy 02")
            ):
                root = ET.fromstring(source.read(part))
                deleted = "".join(
                    node.text or "" for node in root.findall(".//w:del//w:delText", cli.NS)
                )
                inserted = "".join(
                    node.text or "" for node in root.findall(".//w:ins//w:t", cli.NS)
                )
                self.assertIn(old, deleted)
                self.assertIn(new, inserted)
            self.assertIsNone(source.testzip())

        reopened = Document(self.output)
        # python-docx intentionally does not interpret w:ins/w:del as ordinary
        # run content. Reopening proves package readability; raw XML assertions
        # above verify the inserted tracked text itself.
        self.assertEqual(
            reopened.paragraphs[0].text, "The electrolyte concentration was ."
        )
        self.assertIn("Manuscript ID: ", reopened.sections[0].header.paragraphs[0].text)
        self.assertIn(" | archive", reopened.sections[0].header.paragraphs[0].text)
        # python-docx hides both w:del and w:ins text from ordinary paragraph.text.
        # The original footer phrase is therefore checked in the raw XML above;
        # only the unchanged suffix should remain visible after reopening.
        self.assertEqual(reopened.sections[0].footer.paragraphs[0].text, " - confidential")

    def test_inspector_indexes_plain_reviewer_paragraphs(self):
        make_docx(self.input, [[("Reviewer 1: Please clarify the mechanism.", False)], [("Please add the missing control experiment.", False)]])
        report = cli.inspect_docx(str(self.input))
        self.assertEqual(report["paragraph_count"], 2)
        self.assertEqual(report["paragraphs"][0]["p_idx"], 0)
        self.assertIn("clarify the mechanism", report["paragraphs"][0]["text"])

    def test_unicode_script_insertions_use_native_word_format(self):
        make_docx(self.input, [[("The sulfate formula is SO4.", False)]])
        target = {
            "document": "Manuscript",
            "input_file": str(self.input),
            "output_file": str(self.output),
            "p_idx": 0,
            "location": "Body paragraph 1",
            "find": "SO4",
            "replace": "SO₄²⁻",
            "authorized_change": {
                "intent": "Apply requested chemical formula typography",
                "explicitly_authorized_tokens": [{"kind": "formula", "text": "SO4", "replacement": "SO₄²⁻"}],
            },
        }
        report = cli.apply_document(
            str(self.input), str(self.output),
            [{
                **target,
                "revision_item_id": "R1-C1",
                "reviewer": "Reviewer 1"
            }],
            "Co_Reviewer test"
        )

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            paragraph = root.find(".//w:body//w:p", cli.NS)
            insertion = paragraph.find(".//w:ins", cli.NS)
            self.assertIsNotNone(insertion)
            runs = insertion.findall(".//w:r", cli.NS)
            self.assertEqual(
                ["".join(node.text or "" for node in run.findall("w:t", cli.NS))
                 for run in runs],
                ["SO", "4", "2−"]
            )

            alignments = []
            for run in runs:
                vertical = run.find("w:rPr/w:vertAlign", cli.NS)
                alignments.append(vertical.get(cli.qn("val")) if vertical is not None else None)

            self.assertEqual(alignments, [None, "subscript", "superscript"])
            self.assertEqual(cli.visible_text(paragraph), "The sulfate formula is SO42−.")

        edit = report["results"][0]
        self.assertEqual(edit["replace"], "SO₄²⁻")
        self.assertEqual(edit["inserted_text"], "SO42−")
        self.assertEqual(
            edit["inserted_run_formats"],
            [
                {"text": "SO", "vertical_alignment": None},
                {"text": "4", "vertical_alignment": "subscript"},
                {"text": "2−", "vertical_alignment": "superscript"}
            ]
        )
        plan = {
            "revision_items": [{
                "id": "R1-C1",
                "status": "applied",
                "targets": [target]
            }]
        }
        verification = cli.verify_apply_report(plan, [report])
        self.assertTrue(verification["passed"])
        self.assertEqual(verification["targets_verified"], 1)
        self.assertTrue(
            verification["results"][0]["checks"]["native_script_format_preserved"]
        )
        with self.assertRaisesRegex(ValueError, "Unsupported Unicode script character"):
            cli.make_insert_runs(ET.Element(cli.qn("r")), "\u0670")



    def test_verifier_detects_direct_untracked_text_changes(self):
        make_docx(self.input, [
            [("The sample was Sample A.", False)],
            [("The measured capacity was 100 mAh g-1.", False)]
        ])
        output_report = cli.apply_document(
            str(self.input), str(self.output), [{
                "p_idx": 0,
                "find": "Sample A",
                "replace": "Sample B",
                "revision_item_id": "R1-C1",
                "reviewer": "Reviewer 1",
                "document": "Manuscript"
            }], "Co_Reviewer test"
        )

        # Simulate an accidental direct edit to an unrelated paragraph after
        # the Track Changes engine has produced the marked output.
        with zipfile.ZipFile(self.output, "r") as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        root = ET.fromstring(members["word/document.xml"])
        paragraphs = root.findall(".//w:body//w:p", cli.NS)
        plain_run = paragraphs[1].find("w:r/w:t", cli.NS)
        plain_run.text = "The measured capacity was 120 mAh g-1."
        members["word/document.xml"] = ET.tostring(
            root, encoding="utf-8", xml_declaration=True
        )
        with zipfile.ZipFile(self.output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)

        plan = {
            "approved": True,
            "revision_items": [{
                "id": "R1-C1", "status": "applied",
                "reviewer": "Reviewer 1", "comment": "Please revise the sample.",
                "approval_fingerprint": "0" * 64,
                "targets": [{
                    "document": "Manuscript",
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                    "p_idx": 0,
                    "find": "Sample A",
                    "replace": "Sample B"
                }]
            }]
        }
        # The plan's approval fingerprint is only needed by validate_plan; seal
        # the item after its target fields have been assembled.
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(
            plan["revision_items"][0]
        )
        verification = cli.verify_apply_report(plan, [output_report])
        self.assertFalse(verification["passed"])
        audit = verification["untracked_text_audit"]
        self.assertFalse(audit["passed"])
        self.assertEqual(len(audit["violations"]), 1)
        self.assertIn("untracked", audit["violations"][0]["reason"].lower())

    def test_inspector_reports_native_word_script_runs(self):
        paragraph = ET.Element(cli.qn("p"))
        for value, alignment in (("SO", None), ("4", "subscript"), ("2−", "superscript")):
            run = ET.SubElement(paragraph, cli.qn("r"))
            if alignment:
                rpr = ET.SubElement(run, cli.qn("rPr"))
                ET.SubElement(rpr, cli.qn("vertAlign"), {cli.qn("val"): alignment})
            ET.SubElement(run, cli.qn("t")).text = value

        self.assertEqual(
            cli.extract_native_script_runs(paragraph),
            [
                {"text": "4", "vertical_alignment": "subscript"},
                {"text": "2−", "vertical_alignment": "superscript"}
            ]
        )


if __name__ == "__main__":
    unittest.main()

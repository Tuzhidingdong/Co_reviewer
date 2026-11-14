# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree as ET
from docx import Document

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from co_reviewer.docx_identity import (
    DocxIdentityError,
    build_document_identity_manifest,
    build_target_refs_by_task,
    validate_manifest_sources,
)
from co_reviewer.identity import sha256_file
from co_reviewer_cli import build_parser


W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _set_para_ids(path: Path, ids_by_text: dict[str, str]) -> None:
    temporary = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "word/document.xml":
                root = ET.fromstring(data)
                for paragraph in root.findall(".//{%s}p" % W):
                    text = "".join(node.text or "" for node in paragraph.findall(".//{%s}t" % W))
                    if text in ids_by_text:
                        paragraph.set("{%s}paraId" % W14, ids_by_text[text])
                data = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(item, data)
    temporary.replace(path)


def _inventory(path: Path, role: str = "Manuscript") -> list[dict]:
    return [{
        "role": role,
        "file": str(path),
        "source_sha256": sha256_file(path),
        "paragraphs": [],
    }]


class DocxIdentityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _docx(self, name: str, paragraphs: list[str], para_ids: dict[str, str] | None = None) -> Path:
        path = self.root / name
        document = Document()
        for text in paragraphs:
            document.add_paragraph(text)
        document.save(path)
        if para_ids:
            _set_para_ids(path, para_ids)
        return path

    def test_native_para_ids_preserve_object_identity_when_paragraph_moves(self):
        first = self._docx(
            "first.docx",
            ["Alpha remains.", "Bravo remains."],
            {"Alpha remains.": "A001", "Bravo remains.": "B001"},
        )
        first_manifest, first_report = build_document_identity_manifest(_inventory(first))
        first_objects = {
            row["native_id"]: row for row in first_manifest["objects"]
            if row.get("native_id") in {"A001", "B001"}
        }
        second = self._docx(
            "second.docx",
            ["Inserted before both.", "Alpha remains.", "Bravo remains."],
            {"Alpha remains.": "A001", "Bravo remains.": "B001"},
        )
        second_manifest, second_report = build_document_identity_manifest(
            _inventory(second), previous_manifest=first_manifest
        )
        second_objects = {
            row["native_id"]: row for row in second_manifest["objects"]
            if row.get("native_id") in {"A001", "B001"}
        }
        self.assertEqual(
            first_objects["A001"]["object_id"],
            second_objects["A001"]["object_id"],
        )
        self.assertEqual(
            first_objects["B001"]["object_id"],
            second_objects["B001"]["object_id"],
        )
        self.assertEqual(second_objects["A001"]["location_hint"]["p_idx"], 1)
        self.assertEqual(second_report["totals"]["matched_native_id"], 2)
        self.assertNotEqual(
            first_manifest["documents"][0]["revision_id"],
            second_manifest["documents"][0]["revision_id"],
        )

    def test_unique_exact_fingerprint_reuses_id_without_native_para_id(self):
        first = self._docx("first.docx", ["Unique sentence."])
        manifest, _ = build_document_identity_manifest(_inventory(first))
        first_id = next(
            row["object_id"] for row in manifest["objects"]
            if row["object_type"] == "body_paragraph"
        )
        second = self._docx("second.docx", ["A new paragraph.", "Unique sentence."])
        updated, report = build_document_identity_manifest(
            _inventory(second), previous_manifest=manifest
        )
        matched = next(
            row for row in updated["objects"]
            if row["location_hint"].get("p_idx") == 1
            and row["object_type"] == "body_paragraph"
        )
        self.assertEqual(matched["object_id"], first_id)
        self.assertEqual(report["totals"]["matched_exact_fingerprint"], 1)

    def test_duplicate_identical_paragraphs_are_not_falsely_matched(self):
        first = self._docx("first.docx", ["Repeat.", "Repeat."])
        manifest, _ = build_document_identity_manifest(_inventory(first))
        second = self._docx("second.docx", ["Repeat.", "Repeat."])
        updated, report = build_document_identity_manifest(
            _inventory(second), previous_manifest=manifest
        )
        old_ids = {
            row["object_id"] for row in manifest["objects"]
            if row["object_type"] == "body_paragraph"
        }
        new_ids = {
            row["object_id"] for row in updated["objects"]
            if row["object_type"] == "body_paragraph"
        }
        self.assertFalse(old_ids & new_ids)
        self.assertGreater(report["totals"]["ambiguous_matches"], 0)

    def test_registry_includes_tables_and_table_cell_paragraph_anchors(self):
        path = self.root / "table.docx"
        document = Document()
        document.add_paragraph("Before table.")
        table = document.add_table(rows=1, cols=2)
        table.cell(0, 0).text = "Cell A"
        table.cell(0, 1).text = "Cell B"
        document.save(path)
        manifest, _ = build_document_identity_manifest(_inventory(path))
        types = [row["object_type"] for row in manifest["objects"]]
        self.assertIn("table", types)
        self.assertIn("table_cell_paragraph", types)
        self.assertEqual(
            len([row for row in manifest["objects"] if row["object_type"] == "table"]),
            1,
        )
        table_object = next(row for row in manifest["objects"] if row["object_type"] == "table")
        cell_paragraphs = [
            row for row in manifest["objects"]
            if row["object_type"] == "table_cell_paragraph"
        ]
        self.assertEqual(len(cell_paragraphs), 2)
        self.assertTrue(all(row["parent_object_id"] == table_object["object_id"] for row in cell_paragraphs))
        self.assertEqual(
            sorted(row["location_hint"]["cell_idx"] for row in cell_paragraphs),
            [0, 1],
        )

    def test_target_locations_resolve_to_stable_refs_and_validate_against_source(self):
        path = self._docx(
            "target.docx",
            ["First paragraph.", "Target paragraph."],
            {"Target paragraph.": "ABCD1234"},
        )
        inventory = _inventory(path)
        manifest, _ = build_document_identity_manifest(inventory)
        document = manifest["documents"][0]
        target_task = {
            "task_id": "T1",
            "targets": [{
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 1,
                "para_id": "ABCD1234",
                "action": "replace",
            }],
        }
        refs, errors = build_target_refs_by_task([target_task], manifest)
        self.assertEqual(errors, [])
        self.assertEqual(refs["T1"][0]["document_id"], document["document_id"])
        self.assertTrue(refs["T1"][0]["object_id"])
        self.assertEqual(
            validate_manifest_sources(manifest, {
                "source_files": [{
                    "role": "Manuscript",
                    "file": str(path),
                    "sha256": sha256_file(path),
                }]
            }),
            [],
        )

    def test_formatting_identity_ignores_run_splitting_when_style_is_equal(self):
        first_path = self.root / "single-run.docx"
        first_doc = Document()
        first_doc.add_paragraph("Same visible text.")
        first_doc.save(first_path)

        second_path = self.root / "split-runs.docx"
        second_doc = Document()
        paragraph = second_doc.add_paragraph()
        paragraph.add_run("Same ")
        paragraph.add_run("visible ")
        paragraph.add_run("text.")
        second_doc.save(second_path)

        first_manifest, _ = build_document_identity_manifest(_inventory(first_path))
        second_manifest, _ = build_document_identity_manifest(_inventory(second_path))
        first_dims = first_manifest["documents"][0]["dimensions"]
        second_dims = second_manifest["documents"][0]["dimensions"]
        self.assertEqual(first_dims["structure_sha256"], second_dims["structure_sha256"])
        self.assertEqual(first_dims["formatting_sha256"], second_dims["formatting_sha256"])

    def test_format_only_change_does_not_change_content_or_structure_identity(self):
        from docx.shared import Pt

        first = self._docx("plain.docx", ["Same visible text."])
        first_manifest, _ = build_document_identity_manifest(_inventory(first))
        second_path = self.root / "formatted.docx"
        document = Document()
        paragraph = document.add_paragraph("Same visible text.")
        paragraph.runs[0].font.name = "Arial"
        paragraph.runs[0].font.size = Pt(11)
        document.save(second_path)
        second_manifest, _ = build_document_identity_manifest(_inventory(second_path))
        first_dimensions = first_manifest["documents"][0]["dimensions"]
        second_dimensions = second_manifest["documents"][0]["dimensions"]
        self.assertEqual(first_dimensions["content_sha256"], second_dimensions["content_sha256"])
        self.assertEqual(first_dimensions["structure_sha256"], second_dimensions["structure_sha256"])
        self.assertNotEqual(first_dimensions["formatting_sha256"], second_dimensions["formatting_sha256"])

    def test_inventory_paragraph_text_must_match_actual_docx(self):
        path = self._docx("inventory-mismatch.docx", ["Actual paragraph."])
        inventory = _inventory(path)
        inventory[0]["paragraphs"] = [{
            "part": "word/document.xml",
            "p_idx": 0,
            "para_id": None,
            "text": "Stale or wrong paragraph.",
        }]
        with self.assertRaisesRegex(DocxIdentityError, "text does not match the actual DOCX"):
            build_document_identity_manifest(inventory)

    def test_inventory_para_id_must_match_actual_docx(self):
        path = self._docx(
            "inventory-paraid.docx",
            ["Actual paragraph."],
            {"Actual paragraph.": "ABCD1234"},
        )
        inventory = _inventory(path)
        inventory[0]["paragraphs"] = [{
            "part": "word/document.xml",
            "p_idx": 0,
            "para_id": "WRONG123",
            "text": "Actual paragraph.",
        }]
        with self.assertRaisesRegex(DocxIdentityError, "paraId does not match the actual DOCX"):
            build_document_identity_manifest(inventory)

    def test_character_range_cannot_exceed_actual_object_text(self):
        path = self._docx("offset.docx", ["Short text."])
        manifest, _ = build_document_identity_manifest(_inventory(path))
        refs, errors = build_target_refs_by_task([{
            "task_id": "T1",
            "targets": [{
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
                "action": "replace",
                "start_offset": 0,
                "end_offset": 999,
            }],
        }], manifest)
        self.assertEqual(refs["T1"], [])
        self.assertTrue(any("exceeds the resolved object's visible-text length" in error for error in errors))

    def test_stale_inventory_hash_is_rejected_before_identity_generation(self):
        path = self._docx("stale.docx", ["Original."])
        report = _inventory(path)
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(DocxIdentityError, "hash is stale"):
            build_document_identity_manifest(report)

    def test_cli_refuses_to_write_identity_manifest_over_source_docx(self):
        from argparse import Namespace
        from co_reviewer_cli import cmd_build_document_identity
        from docx import Document

        source = self._docx("protected.docx", ["Do not overwrite this file."])
        inventory_path = self.root / "inventory.json"
        inventory_path.write_text(json.dumps(_inventory(source)), encoding="utf-8")
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "source DOCX"):
            cmd_build_document_identity(Namespace(
                inventory=str(inventory_path),
                out=str(source),
                previous_manifest=None,
            ))
        self.assertEqual(source.read_bytes(), original)

    def test_target_para_id_mismatch_fails_closed(self):
        path = self._docx(
            "target.docx",
            ["Target paragraph."],
            {"Target paragraph.": "ABCD1234"},
        )
        manifest, _ = build_document_identity_manifest(_inventory(path))
        refs, errors = build_target_refs_by_task([{
            "task_id": "T1",
            "targets": [{
                "document": "Manuscript",
                "part": "word/document.xml",
                "p_idx": 0,
                "para_id": "WRONG",
                "action": "replace",
            }],
        }], manifest)
        self.assertEqual(refs["T1"], [])
        self.assertTrue(any("resolves to 0 DOCX objects" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

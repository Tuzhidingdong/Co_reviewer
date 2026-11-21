from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from docx import Document
from lxml import etree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from co_reviewer.v232_contract import (
    W,
    build_revision_baseline_inventory,
    targeted_revision_gate,
)


def _write_pretracked_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("placeholder")
    document.save(path)
    temporary = path.with_suffix(".rewritten.docx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename == "word/document.xml":
                root = ET.fromstring(payload)
                paragraph = root.find(".//{%s}p" % W)
                for child in list(paragraph):
                    paragraph.remove(child)
                deletion = ET.SubElement(paragraph, "{%s}del" % W)
                deletion.set("{%s}id" % W, "6")
                deletion.set("{%s}author" % W, "Synthetic Reviewer")
                deleted_run = ET.SubElement(deletion, "{%s}r" % W)
                deleted_text = ET.SubElement(deleted_run, "{%s}delText" % W)
                deleted_text.text = "old phrase"
                insert = ET.SubElement(paragraph, "{%s}ins" % W)
                insert.set("{%s}id" % W, "7")
                insert.set("{%s}author" % W, "Synthetic Reviewer")
                run = ET.SubElement(insert, "{%s}r" % W)
                text = ET.SubElement(run, "{%s}t" % W)
                text.text = "tracked phrase"
                normal_run = ET.SubElement(paragraph, "{%s}r" % W)
                normal_text = ET.SubElement(normal_run, "{%s}t" % W)
                normal_text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                normal_text.text = " and independent target"
                payload = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            target.writestr(item, payload)
    temporary.replace(path)


class TargetScopedHistoricalRevisionTests(unittest.TestCase):
    def test_uncertain_cross_paragraph_ranges_and_part_scope_remain_fail_closed(self):
        cross_paragraph_inventory = {
            "revisions": [{
                "kind": "comment_range",
                "part": "word/document.xml",
                "paragraph_index": 1,
                "paragraph_indices": [1, 2],
                "text_ranges": [],
                "text_range_precision": "paragraph_fallback",
            }]
        }
        middle_paragraph_target = targeted_revision_gate(
            cross_paragraph_inventory,
            [{
                "part": "word/document.xml",
                "p_idx": 2,
                "start_offset": 4,
                "end_offset": 9,
            }],
        )
        self.assertEqual(middle_paragraph_target["status"], "USER_DECISION_REQUIRED")
        outside_paragraph_target = targeted_revision_gate(
            cross_paragraph_inventory,
            [{
                "part": "word/document.xml",
                "p_idx": 3,
                "start_offset": 4,
                "end_offset": 9,
            }],
        )
        self.assertEqual(outside_paragraph_target["status"], "PASS")

        part_inventory = {
            "revisions": [{
                "kind": "incomplete_comment_range",
                "part": "word/document.xml",
                "paragraph_index": None,
                "paragraph_indices": [],
                "text_ranges": [],
                "text_range_precision": "part_scope",
                "details": {"scope_policy": "part_scope_no_paragraph_anchor"},
            }]
        }
        for paragraph_index in (0, 5, 50):
            with self.subTest(paragraph_index=paragraph_index):
                result = targeted_revision_gate(
                    part_inventory,
                    [{"part": "word/document.xml", "p_idx": paragraph_index,
                      "start_offset": 0, "end_offset": 1}],
                )
                self.assertEqual(result["status"], "USER_DECISION_REQUIRED")

    def test_disjoint_text_in_same_paragraph_is_not_blocked_by_historical_insertion(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.docx"
            _write_pretracked_docx(source)
            inventory = build_revision_baseline_inventory(source)
            wrappers = [
                row for row in inventory["revisions"]
                if row.get("kind") == "text_revision_wrapper"
            ]
            self.assertEqual(len(wrappers), 2)
            deletion = next(row for row in wrappers if row["element"] == "del")
            insertion = next(row for row in wrappers if row["element"] == "ins")
            self.assertEqual(deletion["text_range_precision"], "exact_visible_text")
            self.assertEqual(deletion["text_ranges"], [
                {"paragraph_index": 0, "start_offset": 0, "end_offset": 0}
            ])
            self.assertEqual(insertion["text_range_precision"], "exact_visible_text")
            self.assertEqual(insertion["text_ranges"], [
                {"paragraph_index": 0, "start_offset": 0, "end_offset": len("tracked phrase")}
            ])

            visible_text = "tracked phrase and independent target"
            target_start = visible_text.index("independent target")
            target_end = target_start + len("independent target")
            disjoint = targeted_revision_gate(
                inventory,
                [{
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "start_offset": target_start,
                    "end_offset": target_end,
                }],
            )
            self.assertEqual(disjoint["status"], "PASS")
            self.assertEqual(len(disjoint["unrelated_revisions_preserved"]), 2)
            self.assertEqual(disjoint["overlapping_revisions"], [])

            overlapping = targeted_revision_gate(
                inventory,
                [{
                    "part": "word/document.xml",
                    "p_idx": 0,
                    "start_offset": 0,
                    "end_offset": 8,
                }],
            )
            self.assertEqual(overlapping["status"], "USER_DECISION_REQUIRED")
            self.assertEqual(len(overlapping["overlapping_revisions"]), 2)
            self.assertTrue(overlapping["must_resolve_before_discovery_or_write"])


if __name__ == "__main__":
    unittest.main()

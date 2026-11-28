# -*- coding: utf-8 -*-
"""Batch 5: synthetic gap coverage for T11-15 and T10-07."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from co_reviewer.document_engine import _applicable_format_flags  # noqa: E402
from co_reviewer.format_requirements import validate_format_requirements  # noqa: E402
from lxml import etree as ET  # noqa: E402
import test_format_requirements_batch1 as b1  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class Batch5MatrixGapTests(unittest.TestCase):
    def setUp(self):
        self.helper = b1.FormatRequirementsBatch1Tests()
        self.helper.setUp()

    def tearDown(self):
        self.helper.tearDown()

    def test_t11_15_concurrent_version_fork_is_blocked(self):
        v1 = self.helper._version()
        left = self.helper._version(version="v2", parent=v1["version_id"],
                                    parent_fp=v1["fingerprint"],
                                    props={"vertAlign": "subscript"})
        right = self.helper._version(version="v3", parent=v1["version_id"],
                                     parent_fp=v1["fingerprint"],
                                     props={"vertAlign": "superscript"})
        analysis = self.helper._analysis([
            self.helper._fr([v1, left, right], current=left["version_id"])
        ])
        errors = validate_format_requirements(analysis)
        self.assertTrue(any("concurrent version fork" in error for error in errors), errors)

    def test_t11_10_legal_single_parent_chain_still_valid(self):
        v1 = self.helper._version()
        v2 = self.helper._version(version="v2", parent=v1["version_id"],
                                  parent_fp=v1["fingerprint"])
        analysis = self.helper._analysis([self.helper._fr([v1, v2])])
        self.assertEqual(validate_format_requirements(analysis), [])

    def test_t10_07_complex_script_flags_are_not_applied_without_context(self):
        run = ET.Element(W + "r")
        rpr = ET.SubElement(run, W + "rPr")
        ET.SubElement(rpr, W + "bCs")
        ET.SubElement(rpr, W + "iCs")
        ET.SubElement(run, W + "t").text = "αβ"
        self.assertEqual(_applicable_format_flags(run, "αβ"), frozenset())


if __name__ == "__main__":
    unittest.main()

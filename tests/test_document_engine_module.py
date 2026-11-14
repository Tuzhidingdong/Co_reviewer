from __future__ import annotations

import sys
import unittest
from pathlib import Path

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
)


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


if __name__ == "__main__":
    unittest.main()

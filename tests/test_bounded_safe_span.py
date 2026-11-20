import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lxml import etree as ET
from co_reviewer.bounded_safe_span import analyze_bounded_safe_span

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def _document(body: str):
    root = ET.fromstring(
        f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    )
    return root, root.find(".//w:p", NS)


def _run(text: str) -> str:
    return f"<w:r><w:t>{text}</w:t></w:r>"


class BoundedSafeSpanTests(unittest.TestCase):
    def test_text_between_two_bookmark_ranges_is_an_allow_candidate(self):
        root, paragraph = _document(
            '<w:p><w:bookmarkStart w:id="1" w:name="a"/>'
            + _run("protected one")
            + '<w:bookmarkEnd w:id="1"/>'
            + _run("independent safe wording")
            + '<w:bookmarkStart w:id="2" w:name="b"/>'
            + _run("protected two")
            + '<w:bookmarkEnd w:id="2"/></w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "independent safe wording")
        self.assertTrue(result["allowed"])
        self.assertEqual(result["status"], "ALLOW_CANDIDATE")
        self.assertEqual(result["method"], "bounded_safe_span_v1")
        self.assertEqual(len(result["bookmark_boundaries"]), 2)
        self.assertEqual("".join(row["target_text"] for row in result["run_map"]), "independent safe wording")

    def test_text_inside_a_bookmark_range_remains_blocked(self):
        root, paragraph = _document(
            '<w:p><w:bookmarkStart w:id="1" w:name="a"/>'
            + _run("protected wording")
            + '<w:bookmarkEnd w:id="1"/></w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "protected wording")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["protected_ranges"][0]["range_id"], "1")

    def test_target_crossing_non_run_bookmark_boundary_is_blocked(self):
        root, paragraph = _document(
            '<w:p>' + _run("left side")
            + '<w:bookmarkStart w:id="1" w:name="a"/>'
            + _run("protected")
            + '<w:bookmarkEnd w:id="1"/>'
            + _run("right side") + '</w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "sideprotected")
        self.assertFalse(result["allowed"])

    def test_unpaired_or_duplicated_bookmark_markers_fail_closed(self):
        root, paragraph = _document(
            '<w:p><w:bookmarkStart w:id="1" w:name="a"/>'
            + _run("text")
            + '</w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "text")
        self.assertFalse(result["allowed"])
        self.assertIn("unpaired", result["reason"])

    def test_duplicate_target_text_is_not_assigned_a_guessed_offset(self):
        root, paragraph = _document(
            '<w:p>' + _run("repeat") + _run(" and repeat") + '</w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "repeat")
        self.assertFalse(result["allowed"])
        self.assertIn("not unique", result["reason"])


if __name__ == "__main__":
    unittest.main()

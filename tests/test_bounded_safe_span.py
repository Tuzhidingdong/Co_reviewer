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

    def test_mixed_direct_formatting_runs_fail_closed(self):
        root, paragraph = _document(
            '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>mixed </w:t></w:r>'
            '<w:r><w:rPr><w:i/></w:rPr><w:t>formatting</w:t></w:r></w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "mixed formatting")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("different direct formatting", result["reason"])

    def test_adjacent_ordinary_runs_with_identical_formatting_are_supported(self):
        root, paragraph = _document(
            '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>same </w:t></w:r>'
            '<w:r><w:rPr><w:b/></w:rPr><w:t>format</w:t></w:r></w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "same format")
        self.assertTrue(result["allowed"])
        self.assertEqual(len(result["run_map"]), 2)

    def test_safe_span_maps_target_to_actual_run_and_text_nodes_in_xml_order(self):
        root, paragraph = _document(
            '<w:p>'
            '<w:r><w:t>prefix target </w:t><w:t>middle</w:t></w:r>'
            '<w:r><w:t> suffix</w:t></w:r>'
            '</w:p>'
        )
        result = analyze_bounded_safe_span(
            root, paragraph, "target middle suffix"
        )
        self.assertTrue(result["allowed"])
        run_map = result["run_map"]
        self.assertEqual(len(run_map), 2)
        self.assertEqual(
            [item["target_text"] for item in run_map],
            ["target middle", " suffix"],
        )
        text_nodes = [
            node for run in run_map for node in run["text_nodes"]
        ]
        self.assertEqual(
            [node["target_text"] for node in text_nodes],
            ["target ", "middle", " suffix"],
        )
        self.assertEqual(
            [node["text_node_document_order"] for node in text_nodes],
            sorted(node["text_node_document_order"] for node in text_nodes),
        )
        self.assertTrue(all(
            isinstance(run["run_document_order"], int)
            and isinstance(run["paragraph_document_order"], int)
            for run in run_map
        ))
        self.assertEqual(
            [(node["text_node_text_start"], node["text_node_text_end"]) for node in text_nodes],
            [(7, 14), (0, 6), (0, 7)],
        )

    def test_duplicate_target_text_is_not_assigned_a_guessed_offset(self):
        root, paragraph = _document(
            '<w:p>' + _run("repeat") + _run(" and repeat") + '</w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "repeat")
        self.assertFalse(result["allowed"])
        self.assertIn("not unique", result["reason"])


if __name__ == "__main__":
    unittest.main()

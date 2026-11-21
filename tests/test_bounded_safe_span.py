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

    def test_nested_bookmark_ranges_block_inner_text_but_allow_text_after_outer_end(self):
        root, paragraph = _document(
            '<w:p><w:bookmarkStart w:id="outer" w:name="outer"/>'
            + _run("outer protected ")
            + '<w:bookmarkStart w:id="inner" w:name="inner"/>'
            + _run("inner protected")
            + '<w:bookmarkEnd w:id="inner"/>'
            + _run(" outer protected")
            + '<w:bookmarkEnd w:id="outer"/>'
            + _run("outside")
            + '</w:p>'
        )
        nested = analyze_bounded_safe_span(root, paragraph, "inner protected")
        self.assertFalse(nested["allowed"])
        self.assertEqual(
            {row["range_id"] for row in nested["protected_ranges"]},
            {"outer", "inner"},
        )
        outside = analyze_bounded_safe_span(root, paragraph, "outside")
        self.assertTrue(outside["allowed"])
        self.assertEqual(
            {row["range_id"] for row in outside["bookmark_boundaries"]},
            {"outer", "inner"},
        )

    def test_intersecting_bookmark_ranges_block_overlap_and_allow_text_after_both(self):
        root, paragraph = _document(
            '<w:p><w:bookmarkStart w:id="one" w:name="one"/>'
            + _run("one only ")
            + '<w:bookmarkStart w:id="two" w:name="two"/>'
            + _run("overlap")
            + '<w:bookmarkEnd w:id="one"/>'
            + _run("two only")
            + '<w:bookmarkEnd w:id="two"/>'
            + _run("safe")
            + '</w:p>'
        )
        overlap = analyze_bounded_safe_span(root, paragraph, "overlap")
        self.assertFalse(overlap["allowed"])
        self.assertEqual(
            {row["range_id"] for row in overlap["protected_ranges"]},
            {"one", "two"},
        )
        safe = analyze_bounded_safe_span(root, paragraph, "safe")
        self.assertTrue(safe["allowed"])

    def test_text_immediately_adjacent_to_bookmark_endpoints_is_not_inside_range(self):
        root, paragraph = _document(
            '<w:p>'
            + _run("left")
            + '<w:bookmarkStart w:id="1" w:name="protected"/>'
            + _run("protected")
            + '<w:bookmarkEnd w:id="1"/>'
            + _run("right")
            + '</w:p>'
        )
        left = analyze_bounded_safe_span(root, paragraph, "left")
        right = analyze_bounded_safe_span(root, paragraph, "right")
        inside = analyze_bounded_safe_span(root, paragraph, "protected")
        self.assertTrue(left["allowed"])
        self.assertTrue(right["allowed"])
        self.assertFalse(inside["allowed"])

    def test_text_outside_comment_range_is_a_candidate_but_comment_text_is_blocked(self):
        root, paragraph = _document(
            '<w:p><w:r><w:t>before </w:t></w:r>'
            '<w:commentRangeStart w:id="7"/>'
            + _run("comment protected")
            + '<w:commentRangeEnd w:id="7"/>'
            + _run(" after")
            + '</w:p>'
        )
        inside = analyze_bounded_safe_span(root, paragraph, "comment protected")
        outside = analyze_bounded_safe_span(root, paragraph, "after")
        self.assertFalse(inside["allowed"])
        self.assertIn("comment_range", {row["range_kind"] for row in inside["protected_ranges"]})
        self.assertTrue(outside["allowed"])
        self.assertEqual(
            {row["range_kind"] for row in outside["protected_boundaries"]},
            {"comment_range"},
        )

    def test_text_outside_permission_and_move_ranges_is_a_candidate(self):
        root, paragraph = _document(
            '<w:p><w:permStart w:id="p1" w:edGrp="everyone"/>'
            + _run("permission protected")
            + '<w:permEnd w:id="p1"/>'
            + _run(" middle ")
            + '<w:moveFromRangeStart w:id="m1"/>'
            + _run("moved protected")
            + '<w:moveFromRangeEnd w:id="m1"/>'
            + _run(" outside")
            + '</w:p>'
        )
        permission_text = analyze_bounded_safe_span(root, paragraph, "permission protected")
        moved_text = analyze_bounded_safe_span(root, paragraph, "moved protected")
        outside = analyze_bounded_safe_span(root, paragraph, "outside")
        self.assertFalse(permission_text["allowed"])
        self.assertFalse(moved_text["allowed"])
        self.assertTrue(outside["allowed"])
        self.assertEqual(
            {row["range_kind"] for row in outside["protected_boundaries"]},
            {"permission_range", "move_from_range"},
        )

    def test_overlapping_comment_and_custom_xml_ranges_block_their_intersection(self):
        root, paragraph = _document(
            '<w:p><w:commentRangeStart w:id="c1"/>'
            + _run("comment only ")
            + '<w:customXmlInsRangeStart w:id="x1"/>'
            + _run("overlap")
            + '<w:commentRangeEnd w:id="c1"/>'
            + _run(" custom only")
            + '<w:customXmlInsRangeEnd w:id="x1"/>'
            + _run(" safe")
            + '</w:p>'
        )
        overlap = analyze_bounded_safe_span(root, paragraph, "overlap")
        safe = analyze_bounded_safe_span(root, paragraph, "safe")
        self.assertFalse(overlap["allowed"])
        self.assertEqual(
            {row["range_kind"] for row in overlap["protected_ranges"]},
            {"comment_range", "custom_xml_insert_range"},
        )
        self.assertTrue(safe["allowed"])

    def test_foreign_namespace_text_does_not_shift_word_range_coordinates(self):
        math_ns = "http://schemas.openxmlformats.org/officeDocument/2006/math"
        root = ET.fromstring(
            f'<w:document xmlns:w="{W}" xmlns:m="{math_ns}"><w:body>'
            '<w:p><m:oMath><m:t>x</m:t></m:oMath>'
            '<w:commentRangeStart w:id="c1"/>'
            '<w:r><w:t>protected</w:t></w:r>'
            '<w:commentRangeEnd w:id="c1"/>'
            '<w:r><w:t>safe</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        paragraph = root.find(".//w:p", NS)
        protected = analyze_bounded_safe_span(root, paragraph, "protected")
        safe = analyze_bounded_safe_span(root, paragraph, "safe")
        self.assertFalse(protected["allowed"])
        self.assertTrue(
            safe["allowed"],
            "Non-WordprocessingML m:t nodes are not in the visible Word text coordinate space.",
        )

    def test_unpaired_non_bookmark_range_markers_fail_closed(self):
        root, paragraph = _document(
            '<w:p><w:commentRangeStart w:id="c1"/>'
            + _run("not safely bounded")
            + '</w:p>'
        )
        result = analyze_bounded_safe_span(root, paragraph, "not safely bounded")
        self.assertFalse(result["allowed"])
        self.assertIn("comment_range", result["reason"])
        self.assertIn("unpaired", result["reason"])

    def test_bookmark_range_across_paragraphs_blocks_middle_but_not_outer_text(self):
        root, first = _document(
            '<w:p>' + _run("safe before")
            + '<w:bookmarkStart w:id="cross" w:name="cross-paragraph"/>'
            + _run("protected start")
            + '</w:p>'
            + '<w:p>' + _run("protected middle") + '</w:p>'
            + '<w:p>' + _run("protected end")
            + '<w:bookmarkEnd w:id="cross"/>'
            + _run("safe after")
            + '</w:p>'
        )
        paragraphs = root.findall(".//w:p", NS)
        self.assertEqual(len(paragraphs), 3)
        before = analyze_bounded_safe_span(root, paragraphs[0], "safe before")
        middle = analyze_bounded_safe_span(root, paragraphs[1], "protected middle")
        after = analyze_bounded_safe_span(root, paragraphs[2], "safe after")
        self.assertTrue(before["allowed"])
        self.assertFalse(middle["allowed"])
        self.assertEqual(middle["protected_ranges"][0]["range_id"], "cross")
        self.assertTrue(after["allowed"])
        self.assertEqual(
            {row["range_id"] for row in after["bookmark_boundaries"]},
            {"cross"},
        )

    def test_duplicate_out_of_order_and_collapsed_bookmark_markers_fail_closed(self):
        cases = [
            (
                "duplicate start",
                '<w:bookmarkStart w:id="1" w:name="a"/>'
                '<w:bookmarkStart w:id="1" w:name="b"/>'
                + _run("target")
                + '<w:bookmarkEnd w:id="1"/>',
            ),
            (
                "out of order",
                '<w:bookmarkEnd w:id="2"/>'
                + _run("target")
                + '<w:bookmarkStart w:id="2" w:name="late"/>',
            ),
            (
                "collapsed boundary",
                _run("left")
                + '<w:bookmarkStart w:id="3" w:name="empty"/>'
                + '<w:bookmarkEnd w:id="3"/>'
                + _run("right"),
            ),
        ]
        for label, markup in cases:
            with self.subTest(case=label):
                root, paragraph = _document("<w:p>" + markup + "</w:p>")
                target = "left" if label == "collapsed boundary" else "target"
                if label == "collapsed boundary":
                    target = "leftright"
                result = analyze_bounded_safe_span(root, paragraph, target)
                self.assertFalse(result["allowed"])

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

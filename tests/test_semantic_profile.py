from __future__ import annotations

from co_reviewer.semantic_profile import build_workspace_profile


def _span(index, text, start, end, properties=None):
    return {
        "run_index": index,
        "text": text,
        "start_offset": start,
        "end_offset": end,
        "run_style": "",
        "format_properties": properties or {},
        "run_properties": {},
    }


def _paragraph(p_idx, text, *, style="Normal", spans=None, paragraph_format=None):
    return {
        "part": "word/document.xml",
        "p_idx": p_idx,
        "para_id": f"para-{p_idx}",
        "text": text,
        "paragraph_style": style,
        "paragraph_format": paragraph_format or {},
        "format_spans": spans or [],
    }


def test_profile_keeps_same_letter_occurrences_separate_by_location_and_format():
    paragraphs = [
        _paragraph(
            0,
            "a + a = 1; H₂O; α",
            spans=[
                _span(0, "a + ", 0, 4),
                _span(1, "a", 4, 5, {"i": True}),
                _span(2, " = 1; H", 5, 12),
                _span(3, "₂", 12, 13, {"vertAlign": "subscript"}),
                _span(4, "O; ", 13, 16),
                _span(5, "α", 16, 17, {"i": True}),
            ],
        ),
    ]
    workspace, updated = build_workspace_profile([
        {
            "role": "Manuscript",
            "source_sha256": "a" * 64,
            "paragraphs": paragraphs,
        }
    ])
    profile = workspace["documents"][0]
    a_occurrences = [
        row for row in profile["body_occurrences"]
        if row["occurrence_kind"] == "symbol" and row["text"] == "a"
    ]
    assert len(a_occurrences) == 2
    assert a_occurrences[0]["occurrence_id"] != a_occurrences[1]["occurrence_id"]
    assert a_occurrences[0]["format_properties"] == {}
    assert a_occurrences[1]["format_properties"] == {"i": True}
    assert a_occurrences[0]["start_offset"] == 0
    assert a_occurrences[1]["start_offset"] == 4
    assert profile["body_occurrences"]


def test_reference_list_has_its_own_zone_and_style_groups():
    paragraphs = [
        _paragraph(0, "The variable a is used.", spans=[_span(0, "The variable a is used.", 0, 23)]),
        _paragraph(1, "References", style="Heading1"),
        _paragraph(
            2,
            "[1] Smith A. Example title. Journal 2024.",
            style="Bibliography",
            spans=[
                _span(0, "[1] Smith A. Example title. ", 0, 29),
                _span(1, "Journal", 29, 36, {"i": True}),
                _span(2, " 2024.", 36, 42),
            ],
        ),
        _paragraph(
            3,
            "[2] Jones B. Another title. Journal 2023.",
            style="Bibliography",
            spans=[
                _span(0, "[2] Jones B. Another title. ", 0, 28),
                _span(1, "Journal", 28, 35, {"i": True}),
                _span(2, " 2023.", 35, 41),
            ],
        ),
        _paragraph(4, "Appendix A", style="Heading1"),
        _paragraph(5, "A variable a appears in the appendix.", spans=[]),
    ]
    workspace, updated = build_workspace_profile([
        {
            "role": "Manuscript",
            "source_sha256": "b" * 64,
            "paragraphs": paragraphs,
        }
    ])
    profile = workspace["documents"][0]
    assert [row["p_idx"] for row in profile["reference_entries"]] == [2, 3]
    assert all(row["zone"] == "references" for row in profile["reference_occurrences"])
    assert all(row["zone"] == "body" for row in profile["body_occurrences"])
    assert profile["reference_style_state"] == "uniform_pattern"
    updated_doc = updated[0]
    assert "semantic_format_profile" in updated_doc
    assert updated_doc["semantic_format_profile"]["profile_fingerprint"] == profile["profile_fingerprint"]


def test_mixed_reference_patterns_are_reported_without_majority_selection():
    paragraphs = [
        _paragraph(0, "References", style="Heading1"),
        _paragraph(1, "[1] A. Title.", style="Bibliography", paragraph_format={"ind": {"left": "720"}}),
        _paragraph(2, "[2] B. Title.", style="Normal", paragraph_format={"ind": {"left": "0"}}),
    ]
    workspace, _ = build_workspace_profile([
        {
            "role": "Manuscript",
            "source_sha256": "c" * 64,
            "paragraphs": paragraphs,
        }
    ])
    profile = workspace["documents"][0]
    assert profile["reference_style_state"] == "multiple_patterns_no_automatic_majority_selection"
    assert len(profile["reference_style_groups"]) == 2

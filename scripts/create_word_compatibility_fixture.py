#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create deterministic DOCX files for the manual Microsoft Word acceptance check."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from docx import Document


def build_fixture(output_dir: Path) -> dict[str, Path]:
    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "input": output_dir / "word_compatibility_input.docx",
        "output": output_dir / "word_compatibility_revised.docx",
        "plan": output_dir / "word_compatibility_plan.json",
        "preflight": output_dir / "word_compatibility_preflight.json",
        "apply_report": output_dir / "word_compatibility_apply_report.json",
        "verification": output_dir / "word_compatibility_verification_before_word.json",
        "word_verification": output_dir / "word_compatibility_verification_after_word.json",
    }
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing acceptance artifacts. "
            "Choose a new --output-dir or move these files first: " + ", ".join(existing)
        )

    document = Document()
    document.add_paragraph("The electrolyte concentration was 0.5 M.")
    document.add_paragraph("The sulfate species in the electrolyte was SO4.")
    table = document.add_table(rows=1, cols=1)
    table.style = "Table Grid"
    table.cell(0, 0).text = "The measured cell voltage was 1.8 V."

    section = document.sections[0]
    section.header.paragraphs[0].text = "Manuscript ID: ABC-01 | archive"
    section.footer.paragraphs[0].text = "Internal review copy 01 - confidential"
    document.core_properties.title = "Co_Reviewer Microsoft Word compatibility fixture"
    document.core_properties.subject = (
        "Synthetic fixture for checking Word-native Track Changes after a Word save round-trip"
    )
    document.save(paths["input"])

    input_path = str(paths["input"])
    output_path = str(paths["output"])
    target_specs = [
        ("word/document.xml", "0.5 M", "1.0 M", "Main body paragraph"),
        ("word/document.xml", "SO4", "SO₄²⁻", "Native subscript and superscript"),
        ("word/document.xml", "1.8 V", "2.0 V", "Table cell"),
        ("word/header1.xml", "ABC-01", "ABC-02", "Header"),
        ("word/footer1.xml", "Internal review copy 01", "Internal review copy 02", "Footer"),
    ]
    targets = [
        {
            "document": "Manuscript",
            "input_file": input_path,
            "output_file": output_path,
            "part": part,
            "find": find,
            "replace": replace,
            "location": location,
        }
        for part, find, replace, location in target_specs
    ]
    plan = {
        "schema_version": "1.0",
        "approved": False,
        "revision_items": [
            {
                "id": "WORD-QA-01",
                "reviewer": "Co_Reviewer Word compatibility check",
                "comment": (
                    "Synthetic acceptance check: verify native Track Changes, red insertions and scientific script formatting "
                    "in two body paragraphs, a table cell, the header and the footer."
                ),
                "proposed_response": (
                    "The synthetic DOCX fixture was revised in five locations for Word "
                    "compatibility inspection. This is a test response, not manuscript content."
                ),
                "status": "planned",
                "targets": targets,
            }
        ],
    }
    paths["plan"].write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create synthetic DOCX and revision-plan files for Microsoft Word acceptance."
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory for the isolated Word compatibility test artifacts.",
    )
    args = parser.parse_args()
    paths = build_fixture(Path(args.output_dir))
    print("[CREATED] Synthetic source DOCX and revision plan.")
    for name in ("input", "output", "plan", "preflight", "apply_report", "verification", "word_verification"):
        print(f"[{name.upper()}] {paths[name]}")
    print("[SCOPE] Two body paragraphs (including native sub/superscript), table cell, header and footer; no real manuscript files are used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

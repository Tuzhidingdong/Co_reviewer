import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import co_reviewer_cli as cli
from co_reviewer.plan_compiler import (
    compile_revision_plan,
    compute_token_authorizations,
    normalize_format_properties,
    parse_inline_format_markup,
)


def q(local: str) -> str:
    return f"{{{cli.W}}}{local}"


def make_mixed_docx(path: Path) -> None:
    body = ET.Element(q("body"))

    # P[0]: mixed normal + italic
    p0 = ET.SubElement(body, q("p"))
    for text, italic in [("The contact angle ", False), ("θ", True), (" is 10 degrees.", False)]:
        r = ET.SubElement(p0, q("r"))
        if italic:
            ET.SubElement(ET.SubElement(r, q("rPr")), q("i"))
        t = ET.SubElement(r, q("t"))
        if text != text.strip():
            t.set(f"{{{cli.XML}}}space", "preserve")
        t.text = text

    # P[1]: plain text for inline markup + explicit format_intent
    p1 = ET.SubElement(body, q("p"))
    ET.SubElement(ET.SubElement(p1, q("r")), q("t")).text = "We use H2O and model alpha in Eq. 1."

    # P[2]: four direct Run formats for explicit segmented replacement mapping.
    p2 = ET.SubElement(body, q("p"))
    for text, formatting in [
        ("Plain", None),
        ("Italic", "italic"),
        ("Sub", "subscript"),
        ("Sup", "superscript"),
    ]:
        run = ET.SubElement(p2, q("r"))
        if formatting == "italic":
            ET.SubElement(ET.SubElement(run, q("rPr")), q("i"))
        elif formatting in {"subscript", "superscript"}:
            vertical = ET.SubElement(ET.SubElement(run, q("rPr")), q("vertAlign"))
            vertical.set(q("val"), formatting)
        ET.SubElement(run, q("t")).text = text

    ET.SubElement(body, q("sectPr"))
    root = ET.Element(q("document"))
    root.append(body)
    members = {
        "[Content_Types].xml": b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        "word/document.xml": ET.tostring(root, encoding="utf-8", xml_declaration=True),
        "word/_rels/document.xml.rels": b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>',
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


class PlanCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "manuscript.docx"
        self.output = self.root / "manuscript_marked.docx"
        make_mixed_docx(self.input)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parse_inline_markup_and_normalize_properties(self):
        plain, ops = parse_inline_format_markup("H~2~O and *alpha* and x^2^ with **bold** and \\*literal\\*")
        self.assertEqual(plain, "H2O and alpha and x2 with bold and *literal*")
        self.assertEqual(len(ops), 4)
        self.assertEqual(ops[0], {"find": "2", "properties": {"vertAlign": "subscript"}})
        self.assertEqual(ops[1], {"find": "alpha", "properties": {"i": True}})
        self.assertEqual(ops[2], {"find": "2", "properties": {"vertAlign": "superscript"}})
        self.assertEqual(ops[3], {"find": "bold", "properties": {"b": True}})

        norm = normalize_format_properties({"italic": True, "font": "Times New Roman", "color": "#1F4E79", "size": 24})
        self.assertEqual(norm, {
            "i": True,
            "rFonts": {"ascii": "Times New Roman", "hAnsi": "Times New Roman"},
            "color": "1F4E79",
            "sz": 24,
        })

    def test_compute_token_authorizations(self):
        auth = compute_token_authorizations("is 10 degrees", "is 25 degrees")
        self.assertEqual(
            auth["explicitly_authorized_tokens"],
            [{"kind": "number", "text": "10", "replacement": "25"}],
        )

    def test_compile_revision_plan_mixed_runs_inline_markup_and_format_intent(self):
        intents = {
            "documents": {
                "Manuscript": {
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                }
            },
            "intents": [
                {
                    "id": "REV-001",
                    "doc_role": "Manuscript",
                    "reviewer": "R1",
                    "find": "The contact angle θ is 10 degrees.",
                    "replace": "The contact angle θ is 25 degrees.",
                    "intent": "Update contact angle measurement",
                },
                {
                    "id": "REV-002",
                    "doc_role": "Manuscript",
                    "reviewer": "R2",
                    "find": "We use H2O and model alpha",
                    "replace": "We use D~2~O and model *beta*",
                    "format_intent": {"find": "D2O", "font": "Times New Roman", "color": "0000FF"},
                    "intent": "Switch solvent to D2O and parameter to italic beta",
                },
            ],
        }
        plan = compile_revision_plan(intents)
        errors = cli.validate_plan(plan, require_approval=False, check_approval_integrity=False)
        self.assertEqual(errors, [], errors)

        item1 = plan["revision_items"][0]
        self.assertEqual(len(item1["targets"]), 1)
        t1 = item1["targets"][0]
        self.assertEqual(t1["p_idx"], 0)
        self.assertIn("replacement_format_map", t1)
        self.assertEqual(
            t1["authorized_change"]["explicitly_authorized_tokens"],
            [{"kind": "number", "text": "10", "replacement": "25"}],
        )

        item2 = plan["revision_items"][1]
        self.assertEqual(len(item2["targets"]), 2)
        self.assertEqual(item2["targets"][0]["replace"], "We use D2O and model beta")
        self.assertEqual(item2["targets"][1]["find"], "We use D2O and model beta")
        self.assertEqual(item2["targets"][1]["replace"], "We use D2O and model beta")
        self.assertEqual(len(item2["targets"][1]["format_operations"]), 3)

    def test_explicit_mixed_run_format_map_survives_compile_preflight_apply_and_verify(self):
        from types import SimpleNamespace

        map_segments = [
            {"replacement_start": 0, "replacement_end": 5, "source_offset": 0},
            {"replacement_start": 5, "replacement_end": 15, "source_offset": 5},
            {"replacement_start": 15, "replacement_end": 18, "source_offset": 11},
            {"replacement_start": 18, "replacement_end": 21, "source_offset": 14},
        ]
        plan = compile_revision_plan({
            "documents": {
                "Manuscript": {
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                }
            },
            "intents": [{
                "id": "MIXED-EXPLICIT-001",
                "doc_role": "Manuscript",
                "find": "PlainItalicSubSup",
                "replace": "FreshItalicPartSubSup",
                "replacement_format_map": map_segments,
                "intent": "Replace a synthetic four-format Run span without flattening.",
            }],
        })
        target = plan["revision_items"][0]["targets"][0]
        self.assertEqual(target["replacement_format_map"], map_segments)
        self.assertEqual(
            cli.validate_plan(plan, require_approval=False, check_approval_integrity=False),
            [],
        )

        preflight = cli.preflight_plan(plan)
        self.assertTrue(preflight["passed"], preflight)
        plan_path = self.root / "explicit_mixed_plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        rc = cli.cmd_approve_plan(SimpleNamespace(
            plan=str(plan_path),
            ids=["MIXED-EXPLICIT-001"],
            revision_ledger=None,
            actor="Unit Test Author",
        ))
        self.assertEqual(rc, 0)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

        apply_reports = cli.apply_plan(plan, "Unit Test Author")
        self.assertEqual(len(apply_reports), 1)
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, apply_reports)
        self.assertTrue(verification["passed"], verification)
        self.assertEqual(verification["targets_verified"], 1)

        with zipfile.ZipFile(self.output) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        paragraphs = root.findall(".//" + q("p"))
        inserted_runs = []
        for paragraph in paragraphs:
            for insertion in paragraph.findall(".//" + q("ins")):
                inserted_runs.extend(insertion.findall(q("r")))
        run_records = []
        for run in inserted_runs:
            text_value = "".join(node.text or "" for node in run.findall(".//" + q("t")))
            rpr = run.find(q("rPr"))
            italic = rpr is not None and rpr.find(q("i")) is not None
            vertical = rpr.find(q("vertAlign")) if rpr is not None else None
            run_records.append((text_value, italic, vertical.get(q("val")) if vertical is not None else None))
        self.assertEqual(run_records, [
            ("Fresh", False, None),
            ("ItalicPart", True, None),
            ("Sub", False, "subscript"),
            ("Sup", False, "superscript"),
        ])

    def test_explicit_mixed_run_format_map_rejects_incomplete_coverage(self):
        bad_map = [
            {"replacement_start": 0, "replacement_end": 5, "source_offset": 0},
            {"replacement_start": 6, "replacement_end": 21, "source_offset": 5},
        ]
        plan = compile_revision_plan({
            "documents": {
                "Manuscript": {
                    "input_file": str(self.input),
                    "output_file": str(self.output),
                }
            },
            "intents": [{
                "id": "MIXED-INVALID-001",
                "doc_role": "Manuscript",
                "find": "PlainItalicSubSup",
                "replace": "FreshItalicPartSubSup",
                "replacement_format_map": bad_map,
                "intent": "Negative test for incomplete source mapping.",
            }],
        })
        errors = cli.validate_plan(plan, require_approval=False, check_approval_integrity=False)
        self.assertTrue(any("replacement_format_map" in error for error in errors), errors)

    def test_cli_compile_plan_end_to_end_pipeline(self):
        intents_path = self.root / "intents.json"
        plan_path = self.root / "revision_plan.json"
        intents_path.write_text(
            json.dumps({
                "documents": {
                    "Manuscript": {
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                    }
                },
                "intents": [{
                    "id": "REV-CLI-01",
                    "doc_role": "Manuscript",
                    "find": "The contact angle θ is 10 degrees.",
                    "replace": "The contact angle θ is 30 degrees.",
                    "intent": "Update contact angle to 30 degrees",
                }],
            }),
            encoding="utf-8",
        )
        cli_script = Path(__file__).resolve().parents[1] / "scripts" / "co_reviewer_cli.py"
        proc = subprocess.run(
            [sys.executable, str(cli_script), "compile-plan", "--intents", str(intents_path), "--out", str(plan_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertTrue(plan_path.is_file())

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        cli.preflight_plan(plan)
        plan["approved"] = True
        plan["revision_items"][0]["status"] = "approved"
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        results = cli.apply_plan(plan, "Compiler Test")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)

    def test_compile_and_apply_scoped_ambiguous_format_tokens(self):
        plan = compile_revision_plan(
            {
                "documents": {
                    "Manuscript": {
                        "input_file": str(self.input),
                        "output_file": str(self.output),
                    }
                },
                "intents": [
                    {
                        "id": "REV-SCOPE-01",
                        "doc_role": "Manuscript",
                        "find": "The contact angle θ is 10 degrees.",
                        "replace": "The contact angle *θ* (*L*/*S* = 1.2) is 10 degrees.",
                        "intent": "Add L/S with italic L and S even though S/L/t appear elsewhere in paragraph",
                    }
                ],
            }
        )
        cli.preflight_plan(plan)
        plan["approved"] = True
        plan["revision_items"][0]["status"] = "approved"
        plan["revision_items"][0]["approval_fingerprint"] = cli.revision_item_fingerprint(plan["revision_items"][0])
        results = cli.apply_plan(plan, "Compiler Scope Test")
        plan["revision_items"][0]["status"] = "applied"
        verification = cli.verify_apply_report(plan, results)
        self.assertTrue(verification["passed"], verification)


if __name__ == "__main__":
    unittest.main()

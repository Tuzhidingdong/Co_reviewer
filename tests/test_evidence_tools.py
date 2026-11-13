import contextlib
import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import evidence_tools as evidence


class StructuredEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_json_summary_is_bounded_and_supports_exact_json_pointers(self):
        source = self.root / "verification.json"
        original = {
            "status": "ok",
            "records": [
                {"id": "A", "value": 1, "payload": "x" * 2000},
                {"id": "B", "value": 2},
                {"id": "C", "value": 3},
                {"id": "D", "value": 4},
            ],
        }
        source.write_text(json.dumps(original), encoding="utf-8")
        original_hash = evidence.sha256_file(source)
        report = evidence.summarize_json(
            original, ["/records/2/value", "/records/10/value"], sample_items=2, max_chars=120
        )
        self.assertEqual(report["selected_paths"]["/records/2/value"], 3)
        self.assertIn("error", report["selected_paths"]["/records/10/value"])
        self.assertEqual(report["structure"]["arrays"][0]["length"], 4)
        self.assertEqual(evidence.sha256_file(source), original_hash)
        self.assertEqual(report["sample_policy"]["full_source_preserved"], True)

    def test_jsonl_summary_counts_records_without_emitting_full_source(self):
        source = self.root / "events.jsonl"
        source.write_text(
            '{"event":"start","payload":"' + ("z" * 1000) + '"}\n'
            '{"event":"finish","ok":true}\n',
            encoding="utf-8",
        )
        report = evidence.summarize_jsonl(source, sample_items=1, max_chars=100)
        self.assertEqual(report["structure"]["record_count"], 2)
        sample = report["structure"]["first_records_bounded"][0]
        self.assertLessEqual(len(sample["payload"]), 100)
        self.assertEqual(report["format"], "jsonl")

    def test_xml_summary_uses_streaming_parser_and_bounded_text_samples(self):
        source = self.root / "report.xml"
        source.write_text(
            "<root><item id='1'>first</item><item id='2'>"
            + ("longtext" * 100)
            + "</item></root>",
            encoding="utf-8",
        )
        report = evidence.summarize_xml(source, sample_items=1, max_chars=110)
        self.assertEqual(report["structure"]["root_tag"], "root")
        self.assertEqual(report["structure"]["element_count"], 3)
        self.assertEqual(len(report["structure"]["bounded_text_samples"]), 1)
        self.assertLessEqual(len(report["structure"]["bounded_text_samples"][0]["text"]), 110)

    def test_structured_summary_command_writes_digest_and_machine_report(self):
        source = self.root / "data.json"
        source.write_text('{"items":[{"id":1},{"id":2}]}', encoding="utf-8")
        out_dir = self.root / "out"
        args = Namespace(
            input=str(source), format="auto", out_dir=str(out_dir),
            cache_dir=str(self.root / "cache"), path=["/items/1"],
            sample_items=2, max_chars=200,
        )
        first_output = io.StringIO()
        with contextlib.redirect_stdout(first_output):
            self.assertEqual(evidence.cmd_structured_summary(args), 0)
        report = json.loads((out_dir / "structured_evidence.json").read_text(encoding="utf-8"))
        digest = (out_dir / "structured_evidence.md").read_text(encoding="utf-8")
        self.assertEqual(report["selected_paths"]["/items/1"]["id"], 2)
        self.assertIn("SHA-256", digest)
        self.assertIn("CACHE MISS", first_output.getvalue())

        second_args = Namespace(**{**vars(args), "out_dir": str(self.root / "second")})
        second_output = io.StringIO()
        with contextlib.redirect_stdout(second_output):
            self.assertEqual(evidence.cmd_structured_summary(second_args), 0)
        self.assertIn("CACHE HIT", second_output.getvalue())
        second = json.loads(
            (Path(second_args.out_dir) / "structured_evidence.json").read_text(encoding="utf-8")
        )
        self.assertEqual(report["cache"]["key"], second["cache"]["key"])
        self.assertTrue(second["cache"]["hit"])


try:
    import fitz  # type: ignore
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


@unittest.skipUnless(HAS_PYMUPDF, "Optional PyMuPDF dependency is not installed.")
class PdfEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf_path = self.root / "numbered.pdf"
        document = fitz.open()
        page = document.new_page(width=612, height=792)
        # Separate margin spans resemble a Word-exported line-number column.
        for number, y in ((1, 100), (2, 120), (2, 140), (1, 160)):
            page.insert_text((28, y), str(number), fontsize=9)
            page.insert_text((82, y), f"Manuscript line {number} with selectable text.", fontsize=10)
        document.save(self.pdf_path)
        document.close()

    def tearDown(self):
        self.temp.cleanup()

    def _args(self, out_name="out"):
        return Namespace(
            pdf=str(self.pdf_path),
            out_dir=str(self.root / out_name),
            cache_dir=str(self.root / "cache"),
            render_pages=[1],
            text_pages=[1],
            render_dpi=72,
            max_text_chars=1000,
            no_line_number_check=False,
        )

    def test_pdf_inspection_detects_candidates_and_duplicate_or_decreasing_order(self):
        args = self._args()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(evidence.cmd_pdf_inspect(args), 0)
        report = json.loads((Path(args.out_dir) / "pdf_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(report["summary"]["page_count"], 1)
        self.assertGreaterEqual(report["summary"]["line_number_candidates"], 4)
        finding_codes = {item["code"] for item in report["findings"]}
        self.assertTrue(
            {
                "duplicate_line_number_candidates",
                "non_increasing_line_numbers",
                "line_number_gaps",
            } & finding_codes
        )
        self.assertTrue((Path(args.out_dir) / "page-001.png").is_file())
        self.assertTrue(report["selected_page_text"]["1"])

    def test_pdf_cache_reuses_identical_source_and_parameters(self):
        args = self._args()
        first_output = io.StringIO()
        with contextlib.redirect_stdout(first_output):
            evidence.cmd_pdf_inspect(args)
        second_args = self._args("second")
        second_output = io.StringIO()
        with contextlib.redirect_stdout(second_output):
            evidence.cmd_pdf_inspect(second_args)
        self.assertIn("CACHE MISS", first_output.getvalue())
        self.assertIn("CACHE HIT", second_output.getvalue())
        first = json.loads((Path(args.out_dir) / "pdf_evidence.json").read_text(encoding="utf-8"))
        second = json.loads((Path(second_args.out_dir) / "pdf_evidence.json").read_text(encoding="utf-8"))
        self.assertEqual(first["cache"]["key"], second["cache"]["key"])
        self.assertTrue(second["cache"]["hit"])

    def test_pdf_cache_key_changes_when_source_changes(self):
        args = self._args()
        with contextlib.redirect_stdout(io.StringIO()):
            evidence.cmd_pdf_inspect(args)
        first = json.loads((Path(args.out_dir) / "pdf_evidence.json").read_text(encoding="utf-8"))
        with fitz.open(self.pdf_path) as document:
            document[0].insert_text((82, 200), "A later change to the source PDF.", fontsize=10)
            document.save(self.root / "changed.pdf")
        changed_args = self._args("changed")
        changed_args.pdf = str(self.root / "changed.pdf")
        with contextlib.redirect_stdout(io.StringIO()):
            evidence.cmd_pdf_inspect(changed_args)
        changed = json.loads((Path(changed_args.out_dir) / "pdf_evidence.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first["cache"]["key"], changed["cache"]["key"])
        self.assertFalse(changed["cache"]["hit"])

    def test_invalid_page_selection_is_rejected(self):
        args = self._args()
        args.render_pages = [99]
        with self.assertRaises(ValueError):
            with contextlib.redirect_stdout(io.StringIO()):
                evidence.cmd_pdf_inspect(args)


if __name__ == "__main__":
    unittest.main()

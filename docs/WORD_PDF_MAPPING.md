# Final Word/PDF pagination and ResponseUnit line mapping

## Purpose

`map-response-locations` creates a separate final Clean DOCX and PDF using the installed Microsoft Word engine, then records exact target locations for semantically reviewed ResponseUnits. It does not modify the source Manuscript, SI, Marked DOCX or Working Clean View.

## Prerequisites

- Windows with desktop Microsoft Word installed.
- A validated RevisionLedger with the last Patch batch marked `verified`.
- A current Working Clean View created after the final batch.
- Every ResponseUnit to be mapped must already be semantically confirmed.
- Install the optional dependencies in the repository environment:

```powershell
pip install -e ".[word-pdf]"
```

## Run

```powershell
python scripts/co_reviewer_cli.py map-response-locations `
  --ledger .\workspace\revision_ledger.json `
  --out-dir .\workspace\word_pdf_final
```

The output directory must be new or empty. The command creates one Word-final Clean DOCX and PDF for each document role, and a `response_location_map.json` file. It then records the map in the RevisionLedger and changes eligible ResponseUnits to `location_mapped`.

## Evidence and fail-closed cases

- Word opens the hash-verified Working Clean View read-only, enables continuous line numbering, repaginates, saves a separate DOCX and exports PDF.
- The replacement text in every linked verified Patch must occur exactly once in the Word-final DOCX.
- The same target phrase must be extractable from the corresponding PDF page.
- Word is explicitly switched to Print Layout view before line numbering is queried. Page and line numbers are read from Word COM after pagination; both the first and last target characters use Word's `wdFirstCharacterLineNumber` constant (10). `wdWithInTable` (12) is used only to reject table-cell locations because Word line numbering is not reliable there. The PDF page text is an independent page-level check.
- The map stores the final DOCX and PDF SHA-256 values. Ledger validation rechecks them.
- The command also creates a deterministic quality report for every final Clean DOCX/PDF, stores each report path and SHA-256 in the location map, and refuses to attach locations when a required Clean/PDF check fails.
- The standalone `record-response-locations` command also compares the map's `revision_ledger_sha256` with the exact current RevisionLedger bytes before attaching locations; stale maps must be regenerated and cannot be reused after any ledger change.
- Automatic mapping currently supports unique, single-paragraph body-text replacement text outside tables in `word/document.xml` that fits Word's 255-character Find limit and stays on one page. Deletions, ambiguous text, multi-page spans, header/footer targets and response-only units stop for manual resolution rather than receiving guessed locations.

## Acceptance boundary

The producer's code and contract are implemented, but execution on the user's Windows/Word installation remains required. A successful command should be followed by opening the generated Clean DOCX and PDF in Word/PDF viewers, checking continuous line numbers and confirming that the displayed target text matches the recorded page/line range. The existing Marked DOCX remains a separate output and is not modified by this command.

# Final Word/PDF pagination and ResponseUnit line mapping

## Purpose

`map-response-locations` creates a separate final Clean DOCX and PDF using the installed Microsoft Word engine, then records Word-COM page and line evidence for semantically reviewed, location-required ResponseUnits. It does not modify the source Manuscript, SI, Marked DOCX or Working Clean View.

## Prerequisites

- Windows with desktop Microsoft Word installed.
- A validated RevisionLedger with the last Patch batch marked `verified`.
- A current Working Clean View created after the final batch.
- Every location-required ResponseUnit to be mapped must already be semantically confirmed. Reply-only and clarification-needed units are excluded from automatic mapping.
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

The output directory must be new or empty. For each changed document role, the command creates one Word-final Clean DOCX/PDF and refreshes every eligible ResponseUnit locator in that role during one Word COM session. An unchanged role is reused only when all its locators are bound to the same current Working Clean SHA-256 and the previous final DOCX/PDF hashes still match. The combined map is then recorded in the RevisionLedger.

## Evidence and fail-closed cases

- Word opens the hash-verified Working Clean View read-only, enables continuous line numbering, repaginates, saves a separate DOCX and exports PDF.
- The exact location phrase for every eligible verified PatchOperation or existing-text anchor must occur uniquely in the Word-final DOCX.
- Page and line values are read directly from Word COM ranges after field refresh and repagination. PyMuPDF performs only an independent page-level presence check that the Word-reported page in the exported PDF contains the target phrase; it never calculates or changes Word page/line coordinates. The PDF remains hash-bound as the publication artifact.
- Word is explicitly switched to Print Layout view before line numbering is queried. Page and line numbers are read from Word COM after pagination; both the first and last target characters use Word's `wdFirstCharacterLineNumber` constant (10). `wdWithInTable` (12) is used only to reject table-cell locations because Word line numbering is not reliable there. The PDF page text is an independent page-level check.
- The map stores the final DOCX and PDF SHA-256 values. Ledger validation rechecks them.
- Before export, Word refreshes only built-in `SEQ`, `REF`, `PAGE` and `PAGEREF` fields; citation-manager `ADDIN` fields are left untouched. Word then repaginates. The command also creates a deterministic quality report for every final Clean DOCX/PDF, stores each report path and SHA-256 in the location map, and refuses to attach locations when a required Clean/PDF check fails.
- The standalone `record-response-locations` command also compares the map's `revision_ledger_sha256` with the exact current RevisionLedger bytes before attaching locations; stale maps must be regenerated and cannot be reused after any ledger change.
- Preflight automatically expands a local word-flank window until `location_phrase` is unique across accepted document text (maximum 255 characters). Delete-only operations use the four words before and four words after the deletion boundary. `no_change` units bind a confirmed existing paragraph and are re-mapped in the final Clean document. `reply_only` units use `not_required` and receive no fabricated locations. Table cells, multi-page spans and unsupported non-body targets remain fail-closed.

## Acceptance boundary

The producer's code and contract are implemented, but execution on the user's Windows/Word installation remains required. A successful command should be followed by opening the generated Clean DOCX and PDF in Word/PDF viewers, checking continuous line numbers and confirming that the displayed target text matches the recorded page/line range. The existing Marked DOCX remains a separate output and is not modified by this command.

# Local evidence and context-reduction pipeline

**Scope:** offline, model-agnostic extraction and bounded summaries. These tools reduce repeated parsing and context transfer; they do not replace scientific review, approval, Word Apply/Verify, or user visual acceptance.

## Commands

Install the optional PDF dependency:

    python -m pip install -r requirements-pdf.txt

JSON, JSONL, XML and local CrossRef-response commands use the Python standard library and do not require PyMuPDF.

### PDF diagnostics

    python scripts/evidence_tools.py pdf-inspect --pdf Manuscript_clean_layout.pdf --out-dir reports/pdf-stage1
    python scripts/evidence_tools.py pdf-inspect --pdf Manuscript_clean_layout.pdf --out-dir reports/pdf-stage1 --render-pages 1 13 25 --text-pages 13

Outputs include a compact Markdown page map, a machine-readable geometry report, and only the explicitly selected page renders. Text-line geometry and left-margin line-number candidates are diagnostic heuristics. They are not Word pagination evidence, line-number correctness proof, visual quality approval, or a replacement for the role-scoped Word COM/PDF mapper. No OCR is performed. Image-only pages are flagged for separate handling.

### JSON / JSONL / XML structure summaries

    python scripts/evidence_tools.py structured-summary --input verification_stage1.json --out-dir reports/verification-summary
    python scripts/evidence_tools.py structured-summary --input verification_stage1.json --out-dir reports/verification-summary --path /checks/13 --path /summary
    python scripts/evidence_tools.py structured-summary --input events.jsonl --format jsonl --out-dir reports/jsonl-summary
    python scripts/evidence_tools.py structured-summary --input paragraph_runs.xml --format xml --out-dir reports/xml-summary

Summaries bound depth, samples and text length. JSON Pointer excerpts are exact selections from the original JSON structure; JSONL/XML samples are bounded structural diagnostics, not exhaustive semantic validation. The source files are never rewritten. Retain full raw artifacts on disk and open exact source records when a value needs authoritative review.

### CrossRef result indexing

    python scripts/evidence_tools.py crossref-index --input crossref_response.json --out-dir reports/crossref-index

Every returned CrossRef item is represented by its record index, selected bibliographic fields and exact JSON Pointer to the original item. Missing DOI/title and duplicate DOI/title groups are flagged for review. This is not DOI authenticity verification and does not establish that a record is the cited work; the original CrossRef response remains the evidence source.

## Cache and integrity behavior

Cache entries are keyed by source SHA-256, operation, tool/schema version and every relevant extraction/sampling/render parameter (including the PyMuPDF version for PDF work). A changed source or option creates a distinct entry. Each cache manifest records SHA-256 digests for every artifact; missing or modified artifacts invalidate the entry and force regeneration. Temporary entries are promoted only after the source hash is rechecked. Raw source documents are not modified.

The default local cache directory is .co-reviewer-cache and is git-ignored. Keep complete reports and raw artifacts on disk; pass compact Markdown digests and only relevant excerpts to the Supervisor. Do not infer model-context exposure or token counts from file reads/cache hits.

## Separation from authoritative gates

These utilities do not change Inventory, Workflow Ledger, RevisionLedger, source documents or Patch authorization; authorize edits or make scientific decisions; establish DOI authenticity or resolve citation correctness automatically; certify Word layout, pagination, visual typography or figure quality; or supersede exact Word COM mapping, semantic verification, reviewer-comment coverage or user Word GUI acceptance.

All heuristic findings are review triggers, never automatic acceptance.

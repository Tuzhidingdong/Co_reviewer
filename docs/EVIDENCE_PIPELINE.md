# V0.3 local evidence pipeline

V0.3 reduces repeated context transfer without changing the scientific review, approval, Word editing or verification gates. It is model-agnostic: no model names, model-specific prompt formats, tokenizers or provider parameters are used.

## 1. PDF inspection and cache

Install the optional PDF dependency once:

    python -m pip install -r requirements-pdf.txt

After Word exports the layout PDF, inspect its text layer and geometry locally:

    python scripts/evidence_tools.py pdf-inspect --pdf Manuscript_clean_layout.pdf --out-dir reports/pdf-stage1

The command writes:
- pdf_evidence.md: compact page map, counts, automatic findings and paths to selected renders;
- pdf_evidence.json: detailed page geometry and candidate line-number positions;
- page-NNN.png: only the pages explicitly requested for visual review.

Render only pages that need visual confirmation. For example:

    python scripts/evidence_tools.py pdf-inspect --pdf Manuscript_clean_layout.pdf --out-dir reports/pdf-stage1 --render-pages 1 13 25 --text-pages 13

The line-number detector examines numeric text spans in the left margin and reports duplicates or non-increasing candidates within a page. It is a diagnostic heuristic, not proof that all line numbers are correct. It does not assume that numbering continues between pages, because journal and Word settings may restart numbering. Confirm flagged pages visually and retain a small representative visual check of pages without flags.

The cache key includes the source PDF SHA-256, tool/schema version, PyMuPDF version and inspection/render parameters. Re-running with identical inputs and parameters reuses the same cached extraction and renders. A changed PDF or parameter set creates a new cache entry. The cache is stored locally in .co_reviewer_cache by default; source PDFs are never modified.

For a later Stage 4 PDF:
- Compare the file SHA-256 before reading or rendering it again.
- If the SHA-256 and inspection parameters are unchanged, reuse the prior evidence package.
- If the SHA-256 differs, run inspection again; do not reuse stale page coordinates or line-number findings.

The tool reads the PDF's selectable text layer. It does not run OCR. Image-only pages are explicitly flagged for separate review; do not treat them as empty or verified. Geometry checks do not certify typography, figure legibility, clipping or overall visual quality.

## 2. JSON, JSONL and XML summaries

Keep full JSON/XML artifacts on disk. Generate a bounded structural summary:

    python scripts/evidence_tools.py structured-summary --input verification_stage1.json --out-dir reports/verification-summary

For a JSON file, request exact JSON Pointer excerpts rather than dumping the full object:

    python scripts/evidence_tools.py structured-summary --input verification_stage1.json --out-dir reports/verification-summary --path /checks/13 --path /summary

For XML or JSONL, the same command creates a bounded structure/index report:

    python scripts/evidence_tools.py structured-summary --input paragraph_runs.xml --format xml --out-dir reports/xml-summary

The structural-summary cache key includes source SHA-256, format, tool/schema version, requested JSON Pointers and sampling limits. Identical inputs and parameters reuse the cached summary; changed source bytes or options produce a new entry. The report identifies file SHA-256 and structure, and makes clear that sampled counts are not exhaustive semantic validation. JSON/XML source files are not rewritten. When an exact value must be checked, retrieve the corresponding path/element from the original source.

## CrossRef reference metadata

For a CrossRef response JSON, index every returned record without transferring its complete metadata payload:

    python scripts/evidence_tools.py crossref-index --input crossref_response.json --out-dir reports/crossref-index

The compact Markdown index includes each record's DOI, title, first author, year, journal and an exact JSON Pointer back to the original item. The machine report also flags missing DOI/title fields and duplicate DOI/title groups. It processes all CrossRef records rather than sampling the first few.

This index is not DOI authenticity verification and does not decide whether the returned work is the correct cited source. Open the original record at its evidence pointer for exact author lists, publication details or any conflicting entry. The cache is keyed by source SHA-256 and evidence-tool/schema version; changed source bytes require a new index.

## 3. Context delivery rules

- Send the compact Markdown digest to the Supervisor by default, not the full machine report.
- Keep raw inventories, OCR/measurement output, complete CrossRef responses and XML execution logs as local evidence.
- Retrieve exact fields/records only for failed, conflicting, uncertain or reviewer-relevant items.
- Preserve complete audit coverage locally. A short report must include total counts, pass/fail counts, omitted-item counts and evidence pointers; it must not silently sample away failures.
- For visual PDF checks, inspect the rendered pages selected by findings plus a representative sample. Do not feed all page screenshots and all extracted text into one context by default.
- Run the complete quality suite, but keep its full output in the local log. The PowerShell runner should print only the check summary, failures and log path.

## 4. Quality boundaries

V0.3 does not reduce or automate away:
- reviewer-comment coverage and stable revision-item mapping;
- source preflight, explicit user approval and stale-approval rejection;
- native Word Track Changes and whole-document untracked-text audit;
- exact revision-ID/text verification;
- scientific and cross-document semantic review;
- visual inspection of output DOCX/PDF where layout matters.

The evidence tools are diagnostics and context reducers. They must fail visibly on unreadable inputs, missing dependencies or invalid page selections. Heuristic findings are review triggers, never automatic acceptance.

# Co_Reviewer usage, document-read and Word-output quality audit

## Purpose and evidence boundaries

This module makes work observable without pretending that every event is measurable.

- Programmatic DOCX parsing and cache reuse are recorded separately from model-context exposure.
- Actual model usage must be reported by the code that made the model call or by a trustworthy provider/agent usage export.
- Token buckets are not combined unless their accounting relationship is known.
- Missing measurements remain null/unavailable, not zero.
- Word/PDF quality reports are deterministic package checks. They do not establish visual or scientific correctness.

## Record DOCX parse/cache observations

`inspect` and `audit` accept the optional `--usage-ledger` argument:

    python scripts/co_reviewer_cli.py audit --rev ./Reviewer_Comments.docx --man ./Manuscript.docx --si ./Supporting_Information.docx --usage-ledger ./.co-reviewer-usage.jsonl

Each selected file produces one `document_read` event containing the source SHA-256, role, cache hit/miss, whether a parse/full-text extraction occurred, and extracted paragraph/character counts. The event sets `context_scope` to `none`: these counts describe Co_Reviewer's programmatic inventory, not LLM prompt content.

A second run with unchanged files should report cache hits and no new full-text extraction events, assuming the same cache directory and cache schema.

## Record actual model usage

P1 does not intercept Antigravity or OpenCode model calls. Record model usage only from a trusted provider/agent metadata export. Do not infer token counts from prompt/response text, text length, cache reads or filesystem activity.

Prepare a metadata-only JSON event using the schema in `schemas/usage-event.schema.json`, then append and audit it:

```powershell
python scripts/co_reviewer_cli.py record-usage-event --ledger ./.co-reviewer-usage.jsonl --event ./model_usage_event.json
python scripts/co_reviewer_cli.py audit-usage --ledger ./.co-reviewer-usage.jsonl --out ./usage_audit_report.json
```

Keep the five token buckets separate: `input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `output_tokens` and `reasoning_tokens`. Missing measurements remain null/unavailable, not zero. `reasoning_relation` must reflect provider accounting evidence; an unknown relationship is never silently combined. `audit-usage` groups recorded model events by stage, actor and provider/model, and reports programmatic document reads separately. A `document_read` event never proves that the model saw the file.

## Optional cost estimate

Provide only rates verified for the relevant provider/model and accounting scheme. The pricing key is `provider/model`; every priced row must explicitly declare `accounting_model: disjoint_buckets`.

    {
      "schema_version": "1.0",
      "currency": "USD",
      "rates": {
        "provider-name/model-name": {
          "accounting_model": "disjoint_buckets",
          "input_per_million": 1.0,
          "cache_read_per_million": 0.1,
          "cache_write_per_million": 0.5,
          "output_per_million": 2.0
        }
      }
    }

    python scripts/co_reviewer_cli.py estimate-usage --ledger ./.co-reviewer-usage.jsonl --pricing ./usage_pricing.json --out ./usage_cost_estimate.json

Events with unavailable, partial, ambiguous or non-disjoint accounting evidence are listed as unpriced. The estimate is not a billing statement.

## DOCX and PDF quality checks

Supported profiles:

| Profile | Main gate |
|---|---|
| `generic` | DOCX ZIP and relevant Word XML integrity; inventory and warnings |
| `clean-manuscript` | No tracked revisions; placeholders/comments are surfaced |
| `marked-manuscript` | Inventory tracked revision elements |
| `word-final-clean` | No tracked revisions; suitable for final Word/PDF mapping |
| `response-letter` | No tracked revisions; Normal style Times New Roman, 10.5 pt, black |

Run a standalone audit:

    python scripts/co_reviewer_cli.py audit-docx-quality --docx ./Response_to_Reviewers.docx --profile response-letter --out ./Response_to_Reviewers.quality.json
    python scripts/co_reviewer_cli.py audit-docx-quality --docx ./Manuscript.final-clean.docx --profile word-final-clean --pdf ./Manuscript.final-clean.pdf --out ./Manuscript.quality.json

Response Letter rendering emits a `.quality.json` report automatically. `map-response-locations` audits each Word-final Clean DOCX and PDF, records the report file and hash in `response_location_map.json`, and refuses to attach location evidence when a deterministic check fails.

## Not covered / acceptance still required

- Automatic Antigravity/OpenCode model-call capture is outside P1. A future P2 integration may attach provider-specific usage extraction at the real call boundaries.
- No reliable model-context count is inferred from local file reads, cache operations, prompt size or total token usage.
- The quality checker does not replace Microsoft Word GUI inspection, Word save-round-trip acceptance, visual figure/table checks, or scientific semantic review.
- Actual page/line positions continue to come from Word COM after pagination and must be backed by the exported PDF.

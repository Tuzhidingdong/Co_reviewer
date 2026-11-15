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

The actual Supervisor/Worker caller should wrap each model invocation with `co_reviewer.usage_adapter.invoke_with_usage`. The wrapper is provider-neutral: the caller supplies its real invocation, provider-specific usage extractor, actor/stage/provider/model metadata, and explicit context scope. It does not inspect prompt/response text, infer tokens from text length, or infer model exposure from filesystem reads. The provider integration still has to attach this wrapper at its real call boundary.

Example integration shape (replace the placeholders with the actual caller and documented provider usage fields):

```python
from co_reviewer.usage_adapter import invoke_with_usage

response = invoke_with_usage(
    ledger_path=".co-reviewer-usage.jsonl",
    invoke=lambda: invoke_provider_call(...),
    stage="decision_synthesis",
    actor="supervisor",
    provider="provider-name",
    model="model-name",
    context_scope="excerpt",  # caller's explicit dispatch knowledge
    usage_extractor=extract_provider_usage,
    reasoning_relation="unknown",  # set only from provider accounting evidence
    job_id="optional-job-id",
    session_id="optional-session-id",
)
```

The extractor returns only canonical integer/null buckets (`input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `output_tokens`, `reasoning_tokens`). If no trusted usage object is available, omit the extractor and the event remains `unavailable`; partial measurements remain partial. Failed calls produce an unavailable event and re-raise the original exception. If the model call succeeds but the ledger cannot be written, `UsageAdapterError.result` retains the completed result; do not retry the billable call blindly.

The legacy manual event-file route below remains available for provider usage exports. Example:

    {
      "event_type": "model_usage",
      "stage": "decision_synthesis",
      "actor": "supervisor",
      "provider": "provider-name",
      "model": "model-name",
      "job_id": "optional-job-id",
      "session_id": "optional-session-id",
      "token_usage": {
        "source": "measured",
        "reasoning_relation": "included_in_output",
        "input_tokens": 12000,
        "cache_read_tokens": 8000,
        "cache_write_tokens": 0,
        "output_tokens": 1800,
        "reasoning_tokens": 900
      }
    }

Append it using:

    python scripts/co_reviewer_cli.py record-usage-event --ledger ./.co-reviewer-usage.jsonl --event ./model_usage_event.json
    python scripts/co_reviewer_cli.py audit-usage --ledger ./.co-reviewer-usage.jsonl --out ./usage_audit_report.json

The five token buckets are retained separately. `reasoning_relation` must describe whether reasoning is included in output or separately billable; an `unknown` relationship is never silently combined.

`audit-usage` groups model events by stage, actor and provider/model, reports actual caller-supplied model context scope, call outcomes and observed durations, and separately reports programmatic read scopes and repeated full-document extractions by source SHA-256. A programmatic `document_read` event never proves that the model saw the file.

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

- The provider-neutral wrapper is an integration seam, not an automatically attached hook. This repository does not contain Antigravity or OpenCode invocation code; automatic capture remains inactive until those external call sites attach `invoke_with_usage` with their documented provider usage extractors.
- No reliable model-context count is inferred from local file reads, cache operations, prompt size or total token usage.
- The quality checker does not replace Microsoft Word GUI inspection, Word save-round-trip acceptance, visual figure/table checks, or scientific semantic review.
- Actual page/line positions continue to come from Word COM after pagination and must be backed by the exported PDF.

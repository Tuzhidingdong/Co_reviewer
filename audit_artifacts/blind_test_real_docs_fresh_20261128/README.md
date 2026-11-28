# Audit evidence — real DOCX blind test (public-repository sanitized copy)

**Baseline commit:** `32e08dd42613c38a101b720cbf420e33fcff4813`
**Round:** `blind_test_real_docs_fresh_20261128`
**GitHub visibility:** `public` — sanitized upload only.

## Why this copy is sanitized

`Tuzhidingdong/co_reviewer` is a **public** repository. The audit rule forbids publishing
DOCX/PDF bodies, verbatim manuscript or instruction text, personal names, or raw content logs.
Therefore this directory contains only sanitized reports and machine evidence. No acceptance
value, SHA-256, ID, status, timestamp or correction step was changed for redaction. All SHA-256
values bind the **original local Round artifacts**, which remain untouched in the local Round.

## Uploaded

- `FINAL_REPORT.md` — sanitized final report (all hashes, counts, statuses, timestamps preserved)
- `RUN_LOG.md` — sanitized full run log, including all 8 fail-closed correction steps with error codes
- `MACHINE_EVIDENCE_INDEX.json` — single consolidated machine evidence record
- `plan/instruction_analysis.json` — requirement IDs / actions / polarities / source line numbers + hashes; excerpts redacted
- `plan/instruction_coverage.json` — statuses, target links, coverage SHA; excerpts redacted
- `plan/intents.json` — intent IDs, roles, target indices, text lengths, source-offset format maps; text redacted
- `plan/revision_plan.json`, `plan/revision_plan.preflighted.json`, `plan/revision_plan.instruction_authorized.json` — item/target IDs, fingerprints, hashes, target structure; text redacted
- `plan/instruction_authorization_assessment.json`, `plan/instruction_based_authorization.json` — assessment and machine receipt metadata
- `reports/preflight_report.json`, `apply_report.json`, `verify_report.json`, `final_gate_report.json`, `final_gate_report.md`, `independent_acceptance.json`, `hashes.json` — sanitized projections

## Withheld (and why)

| File / class | Reason |
|---|---|
| 3 × Marked DOCX, 3 × Clean DOCX | Contain manuscript body text and author names — public repo |
| 6 × PDF render | Rendered body text/images from those DOCX — public repo |
| 4 × original input DOCX/TXT | Original manuscript/cover letter/SI/instruction text — never uploaded, never modified |
| Unsantized logs and JSON with verbatim excerpts/find/replace | Contain verbatim text content — public repo |
| `logs/*_paragraphs.json`, `logs/*_targets.txt`, helper scripts | Contain manuscript/instruction text or are local-only inspection tools |

The complete unsanitized package and all six Marked/Clean DOCX plus six PDFs remain in the
local Round directory only. They were not moved, deleted, overwritten, or published.

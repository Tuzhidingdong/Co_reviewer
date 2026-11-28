# PR #2 Scope Disposition — V2.3.2 (2026-11-21)

## Decision

**Do not merge PR #2 or its divergent branch. The in-scope evidence-extraction and cache capabilities have already been selectively integrated and extended on the V2.3.2 RC branch. Keep PR #2 open for historical review; no additional wholesale or duplicate port is justified for M1.**

- PR: https://github.com/Tuzhidingdong/Co_reviewer/pull/2
- PR head branch: `upgrade/v0.3-evidence-pipeline`
- PR base branch: `upgrade/foundation-v0.2`
- RC integration branch: `upgrade/v0.5.1-p1-rc-closeout-20261120`
- RC integration PR: #9 (Draft/Open/Unmerged)
- The PR #2 branch is divergent (31 commits ahead / 39 behind its foundation base in the recorded ancestry audit); its branch must not be merged wholesale into the RC line.

## Exact RC implementation evidence

At RC code HEAD `bb6231b2a71d80b0a38899351f1491194023b014`:

| Capability | RC implementation | Test evidence |
|---|---|---|
| PDF text/geometry inspection, bounded selected-page extraction/rendering and diagnostic line-number candidates | `scripts/evidence_tools.py` | `tests/test_evidence_tools.py`: geometry/line candidates, identical-source-and-parameter cache reuse, source-change cache invalidation, invalid page selection |
| JSON summaries and exact JSON Pointer excerpts | `scripts/evidence_tools.py` | bounded-output and exact-pointer regression |
| JSONL streaming summaries | `scripts/evidence_tools.py` | record-count and bounded-sample regression |
| XML streaming summaries | `scripts/evidence_tools.py` | streaming parser and bounded-sample regression |
| Full-record CrossRef index and missing/duplicate DOI/title diagnostics | `scripts/evidence_tools.py` | record coverage, anomaly and cache-reuse regression |
| Tamper-resistant cache reuse | `scripts/evidence_tools.py` | RC adds `cache_artifact_hashes` and `cache_manifest_matches`; tampered cache artifacts are rejected and rebuilt |
| DOCX inventory reuse keyed to source SHA-256 and cache schema | `scripts/review_audit.py::load_cached_inspection` | `tests/test_review_audit.py::test_cache_reuses_inventory_until_source_bytes_change` and CLI second-run cache test |
| Whole-role Word/PDF mapping reuse only when the exact working-Clean source hash and all prior output/PDF hashes match | `scripts/co_reviewer/word_pdf_mapper.py::_reusable_role_mapping` | `tests/test_word_pdf_mapper.py` mapping-reuse and stale-hash cases |

Content-level comparison against PR #2's head confirms that the RC has the same core PDF/structured-data/CrossRef entry points and corresponding regression methods. The RC implementation is not a blind copy: its evidence tool adds cache-artifact hash verification and a tampered-artifact rebuild test. The optional PDF dependency remains isolated in `requirements-pdf.txt`; base DOCX requirements are not made dependent on PDF tooling.

## Scope and safety boundaries

1. Cache identity includes source SHA-256, operation, tool/schema version and operation parameters. A changed source or changed extraction parameters must not reuse the previous entry.
2. A cache hit is accepted only when its manifest/key and every recorded artifact hash match. Cache reuse is not evidence that source content was semantically verified.
3. PDF line-number candidates and geometry checks are diagnostics, not proof of visual pagination or line correctness. They do not replace actual Word COM/PDF mapping acceptance.
4. Image-only PDF pages are not OCR'ed automatically. The report flags the limitation; no silent image-text inference is introduced.
5. JSON/XML summaries are bounded indexes/excerpts, not replacements for original evidence. Original files remain unchanged.
6. CrossRef indexing is a record index and anomaly detector, not DOI authenticity or bibliographic truth verification.
7. No model/provider-specific extraction, prompts, token accounting or external service is added.

## AC disposition

- **AC-07 implementation slice:** present in the RC branch through content-hash inventory reuse, optional PDF/structured-data/CrossRef evidence caches, and hash-verified Word/PDF mapping reuse.
- **AC-07 integrated acceptance:** remains part of the final WS-5 current-HEAD evidence cycle; this disposition does not promote the whole AC to PASS by itself.
- **PR #2 convergence:** no merge and no branch rewrite. PR #2 remains open. Its divergent commits are not an RC dependency because the relevant in-scope capability is already represented by the RC files above.

This is a scoped implementation decision under the frozen REV2 plan; it does not amend the plan, change PR #9's base, or close any M1 acceptance requirement.

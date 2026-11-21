# v0.3 Original Test Baseline — V2.3.2 Disposition

**Baseline commit:** `7b0fef325a5f8282a39259d39bb3cc0118e24a68`  
**Audit date:** 2026-11-21  
**Current branch:** `upgrade/v0.5.1-p1-rc-closeout-20261120`

## Result

The four original v0.3 regression modules were compared method-by-method against the baseline commit:

| Original module | Baseline methods | Current methods | Original names missing |
|---|---:|---:|---:|
| `tests/test_decision_plan.py` | 13 | 25 | 0 |
| `tests/test_review_audit.py` | 15 | 15 | 0 |
| `tests/test_revision_engine.py` | 19 | 27 | 0 |
| `tests/test_revision_workflow.py` | 29 | 29 | 0 |
| **Total** | **76** | **96** | **0** |

All 76 original test method names remain present. Method-body comparison found 9 adaptations: 7 in `test_revision_engine.py` and 2 in `test_revision_workflow.py`. The other 67 original method bodies are unchanged. Added tests increase coverage; they do not replace the original method set.

## Adaptation register

1. **`test_replacement_preserves_run_format_and_package_members`** — the original mixed-format replacement scenario is retained, now with an explicit per-character `replacement_format_map`. The test still verifies native Track Changes, original formatting inheritance, exact package-member inventory, and byte-identical untouched parts. Companion tests now reject mixed-format replacement when the author has not supplied an exact format map. This strengthens, rather than weakens, the format-preservation contract.
2. **`test_verifier_requires_the_exact_revision_id`** — pre-existing tracked revisions are placed in a separate paragraph so this test isolates exact revision-ID matching. The V2.3.2 target-scoped historical-revision gate independently classifies revisions overlapping the target as `USER_DECISION_REQUIRED` until an explicit ACCEPT/REJECT/SELECTIVE resolution is authorized. The test does not authorize bypassing that gate.
3. **`test_hyperlink_boundary_is_rejected`** — the synthetic DOCX fixture now contains the required OPC package parts and a relationship, ensuring the test reaches the hyperlink adapter boundary rather than failing earlier on an incomplete package.
4. **`test_multi_document_plan_preflights_before_writing`** — each target now carries an explicit `authorized_change`, so the test exercises the V2.3.2 authorization contract while retaining the same multi-document preflight/no-write assertions.
5. **`test_real_python_docx_package_remains_openable_after_tracked_revision`** — the numeric concentration replacement now carries exact human-authorized quantity text/replacement tokens. DOCX readability, native revisions, formatting, package inventory, and untouched-part byte identity remain checked.
6. **`test_one_revision_item_tracks_and_verifies_body_header_and_footer`** — the body quantity and header/footer identifiers each carry explicit exact-token authorization. The three-part Track Changes and verification coverage is unchanged.
7. **`test_unicode_script_insertions_use_native_word_format`** — the chemistry-formula typography replacement now carries explicit formula authorization; native subscript/superscript assertions remain unchanged.
8. **`test_incremental_iterations_preserve_docx_changes_and_apply_report_history`** — the original same-paragraph, two-iteration scenario is retained. The second iteration now carries a new explicit approval/authorization fingerprint for its new Marked parent.
9. **`test_end_to_end_one_reviewer_item_updates_manuscript_and_si`** — both the Manuscript and SI identifier operations now contain exact human-approved token changes. The inventory → preflight → approval → Apply → Verify → Response draft sequence remains covered.

No original test was removed to make a refactor pass. The stricter V2.3.2 rules are exercised by explicit positive and negative tests alongside the retained v0.3 regression names.

## Acceptance boundary

The method-presence comparison is a source audit, not a claim that the whole legacy baseline has passed on every supported platform. The current-head Python 3.10/3.13 and Windows/PowerShell CI result must be recorded separately in `docs/STATUS.md`. Word COM, Word GUI and real-review-round acceptance remain separate gates.

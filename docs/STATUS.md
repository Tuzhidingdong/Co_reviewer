# Co_Reviewer V2.3.2 — Single Execution Status

> This file is the sole current execution-state snapshot for the RC closeout branch. The frozen global plan is authoritative; this file records implementation evidence and must not change that plan.
> Status vocabulary: `IMPLEMENTED_UNVERIFIED` = code exists but its acceptance is not established; `TESTED_PASS_PARTIAL` = the scoped current-head tests pass but the full AC is not yet accepted; `PARTIAL` = capability is incomplete; `NOT_DONE` = required work/acceptance remains; `USER_ACCEPTANCE_PENDING` = reserved for M2.

## 1. Active baseline

| Field | Value |
|---|---|
| Repository | `Tuzhidingdong/Co_reviewer` |
| Effective integration branch | `upgrade/v0.5.1-p1-rc-closeout-20261120` |
| Last source/test-validated HEAD | `3f6e4a173a0e58490eb1950b0ed27c288b32c94c` |
| Status snapshot commit | Documentation-only snapshot records the tested source/test HEAD above and does not change implementation or test evidence |
| Parent / frozen baseline | `8b77863329163d853c90bbfb5872fc313e2eaa76` |
| Parent branch | `docs/freeze-global-user-task-plan-v2.3.2-20261120` |
| Frozen plan | `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` |
| Draft PR | [#9 — V2.3.2 RC closeout](https://github.com/Tuzhidingdong/Co_reviewer/pull/9) |
| `main` | Not modified |
| Existing PRs | PR #1 and #3–#8 are closed as superseded and their implementation ancestry is contained in PR #9; PR #2 remains open, divergent and unmerged with its explicit M1 scope disposition recorded |
| Current M1 decision | **CI GREEN + LOCAL WINDOWS QUALITY & REAL WORD COM AC-45 PASSED; READY FOR M1 HANDOFF / M2 USER ACCEPTANCE** — source/test HEAD `3f6e4a1` passes GitHub Actions run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) (4/4 jobs, 499/499 tests on Python 3.10 and 3.13, plus Windows PowerShell 5.1 quality checks), local `scripts/run_quality_checks.ps1` on Python 3.13.9 (exit code 0, 499/499 tests, log `Co_Reviewer_QualityChecks_20261122_093550_419fd9dc.log`), and the full current-head **AC-45 real Microsoft Word COM 16.0** two-publication E2E chain (Stages A–E → `RELEASE READY` Manifest SHA-256 `e6cb07c0...` and `verify_release_package()` over 24 artifacts). M2 (AC-51–57) remains user-owned and pending |
| Current M2 decision | **NOT USER ACCEPTED / NOT GA** |

The RC branch begins at the frozen-plan commit and contains the subsequent RC-closeout commits. It does not merge or copy an old PR branch. PR #1 and #3–#8 are closed as superseded; PR #2 remains open for historical review because it is divergent; its in-scope PDF/JSON/JSONL/XML/CrossRef evidence pipeline has already been selectively integrated and extended in PR #9. Do not merge the divergent branch or port the same capability again. PR #9 remains the only active engineering integration PR.

## 2. Frozen inputs and SHA-256

Hashes below are SHA-256 over the exact file contents at the code snapshot HEAD, except the global plan, which is read from its frozen source branch.

| File | SHA-256 |
|---|---|
| `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` (frozen source) | `a40b1d29f9482d02ea6ff967929c96a645e657ee62bc64f0b31f63503934db05` |
| `SKILL.md` | `2438ece8e7add23356ec6d6d1539428380028df6c139b77fbaf73950b5e43ec2` |
| `scripts/co_reviewer/v232_contract.py` | `38f7e0db89804c6afa92a9cd6c22ea1d65a4425d108527faef8a4e301a34f326` |
| `scripts/co_reviewer/structure_classifier.py` | `83930b9440d66e0c37bf3983bb0e007ca049f5792b479a75f205e1eb2fa8b96b` |
| `scripts/co_reviewer/bounded_safe_span.py` | `bf0354b625d72ea2103c010d45a4ce222d71041e2811fcfa98f523beda617e54` |
| `scripts/co_reviewer/safety_policy.py` | `03ac2531067ad587b7db1183a9ca346b1fbdb76f769a44af628cea14547b0123` |
| `scripts/co_reviewer/manual_suggestion.py` | `e8e1db8ec67fbf2d7d91ec1651c9fe3d07870bd26adcb4e9afad5f5d21350095` |
| `scripts/co_reviewer/working_view.py` | `dd8975de86d40c226c491a395562c4d3f2500dedd1c78672bb9728b293c036cb` |
| `scripts/create_word_compatibility_fixture.py` | `e43c3773a79848fc0dacbae40c5ea5e385047e850453b074fb8674f257f046cb` |
| `tests/test_word_pdf_acceptance.py` | `708faf650eefe8d3a8ec00f80bb86015c354fc51c9b6c525c50fa347bceab394` |
| `tests/test_working_view.py` | `bf2a5a0265b4a7013765f4310e5d54094d228d7d1faf005c5b61374dc9342372` |
| `docs/RC_LOCAL_ACCEPTANCE.md` | `12814b6d6b040d44e47d9dfab372fc85b26546802e4b7cb6c08accff7325de13` |
| `schemas/workflow-ledger.schema.json` | `3214ac7bae3867e7a7252180ec621e121ce0bf8c0a1deb837e6534a669c09059` |
| `schemas/manual-suggestion.schema.json` | `5d5c557d812bd1f5dfa0250e05c84aca8dad5e47c1a3ffc6a73588438fb0085a` |
| `schemas/revision-decision-plan.schema.json` | `52badad4e47e86859851b80b019d55c5fd15f1d8e214c016341caeaaf8d4ba48` |
| `schemas/revision-ledger.schema.json` | `d3929530e7d7673a9cac5bca110292a7fcb5db42cc89e1c9046b3e5dc3584a9b` |

A hash change requires this table and the code snapshot HEAD to be refreshed together. The frozen plan hash must remain unchanged.

## 3. M1 acceptance matrix

The authoritative definitions are AC-01 through AC-50 in Section 6.1 of the frozen plan.

| AC | Current state | Evidence / remaining work |
|---|---|---|
| 01 Single Active Baseline | IMPLEMENTED_UNVERIFIED | RC branch and parent recorded above; validate exact PR/CI head on acceptance |
| 02 Branch Convergence | PARTIAL / GOVERNANCE | PR #1 and #3–#8 are closed as superseded and their implementation ancestry is contained in PR #9. PR #2 remains divergent (31 unique commits/39 behind), open and unmerged for historical review; its in-scope evidence extraction/cache capability is already selectively integrated and extended in PR #9. `PR2_SCOPE_DISPOSITION_V2.3.2.md` records the content-level comparison and no-duplicate-port decision. Do not merge PR #2 wholesale |
| 03 Status Consistency | IMPLEMENTED_UNVERIFIED | This is the single status file; refresh hashes after final CI/fixes |
| 04 Plan Protection | IMPLEMENTED_UNVERIFIED | Frozen plan left unchanged; verify its SHA-256 remains the value above |
| 05 Round Isolation | IMPLEMENTED_UNVERIFIED | Existing round starter/orchestration and manifest |
| 06 Source Integrity | IMPLEMENTED_UNVERIFIED | Existing source-hash binding and immutable inputs |
| 07 Cache Reuse | IMPLEMENTED_UNVERIFIED | RC includes SHA-256 keyed DOCX inspection reuse, PDF text/geometry/selected-render cache, bounded JSON/JSONL/XML structural summaries with exact JSON Pointer excerpts, all-record CrossRef index, tamper-verified evidence-cache artifacts, and whole-role Word/PDF mapping reuse only when the current Clean DOCX and all output/PDF hashes match. Tests cover reuse, source/parameter invalidation and tampered-artifact rebuild. AC-07 integrated real-round acceptance remains part of WS-5 |
| 08 Comment Coverage | IMPLEMENTED_UNVERIFIED | Existing audit, Decision Plan and ResponseUnit coverage gates |
| 09 Evidence Grounding | IMPLEMENTED_UNVERIFIED | Existing exact evidence/target validator; current HEAD regression pending |
| 10 Historical Gate | IMPLEMENTED_UNVERIFIED | Existing revision baseline and target-scoped resolver |
| 11 Decision Plan | IMPLEMENTED_UNVERIFIED | Existing plan validator and Workflow adapter |
| 12 Authorization | IMPLEMENTED_UNVERIFIED | Existing separate plan/scope/task/Patch/semantic gates; Tier 1 attestation added |
| 13 Stable Identity | TESTED_PASS_PARTIAL | Stable Document/Object identity and TargetRef are the default; `p_idx` is only a positional hint, never silently promoted to identity. Current-head integration test `test_registered_target_ref_reaches_native_docx_writer` exercises manifest creation → registered TargetRef → native `w:del`/`w:ins` DOCX write; forged object fingerprints and stale document revisions fail closed even when the positional hint still matches. Explicit legacy paragraph-index migration is isolated and blocked when an identity registry exists. Full package-wide migration/ambiguity evidence remains part of WS-5 |
| 14 DAG Scheduling | IMPLEMENTED_UNVERIFIED | Existing dependency and target-group scheduler |
| 15 Incremental Invalidation | IMPLEMENTED_UNVERIFIED | Existing downstream invalidation and refresh |
| 16 Risk Tier | TESTED_PASS_PARTIAL | Deterministic Tier 0/1/2 policy; tier recalculated after exact operations; tests added |
| 17 Batch Approval Integrity | TESTED_PASS_PARTIAL | Immutable batch/hash-bound approval exists; Tier 1 author acknowledgement is fingerprint-bound |
| 18 Typed Patch | IMPLEMENTED_UNVERIFIED | Existing typed PatchOperation and evidence links |
| 19 Native Track Changes | IMPLEMENTED_UNVERIFIED | Existing native w:ins/w:del writer |
| 20 Minimal Diff | IMPLEMENTED_UNVERIFIED | Existing targeted Run editing; current-head regression pending |
| 21 Token Protection | IMPLEMENTED_UNVERIFIED | Existing Token Lock / Protected Content authorization |
| 22 Format Preservation | IMPLEMENTED_UNVERIFIED | Existing run/style preservation and FormatChange gates |
| 23 Exact Verification | IMPLEMENTED_UNVERIFIED | Existing exact revision and untracked-text verification |
| 24 Fail-Closed | IMPLEMENTED_UNVERIFIED | Existing unsupported-target and structure gates |
| 25 Structural Event Map | TESTED_PASS_PARTIAL | Parser-backed part-wide map records XML order, Run/Text ranges, bookmark/comment/permission/move/custom-XML boundaries, fields, hyperlinks, drawings and revision wrappers in supported parts. Malformed or unrecognized structures fail closed; complete cross-adapter acceptance remains open |
| 26 Bounded Safe Span | TESTED_PASS_PARTIAL | Unique ordinary-Run candidates are supported only outside protected ranges; nested/intersecting bookmark, comment and permission intervals are tested. Fields, hyperlinks, drawings and unsupported topologies remain non-editable/fail-closed |
| 27 Boundary Protection | TESTED_PASS_PARTIAL | Tests cover nested/intersecting ranges, cross-paragraph comments, unmatched markers and body-level bookmark endpoints; unsupported field/link/drawing boundaries still block editing |
| 28 Safe Span Authorization | TESTED_PASS_PARTIAL | Safe span returns ALLOW_CANDIDATE only; existing Token Lock/Authorized Change/history/format/approval/Verify gates remain |
| 29 Manual Fallback | TESTED_PASS_PARTIAL | Hash-bound `not_applied` artifact and CLI/Apply integration are present and the current source/test HEAD passes the 499-test suite; broader manual-fallback coverage and usability evidence remain |
| 30 Reference Graph | TESTED_PASS_PARTIAL | Caption/mention graph extended with numeric citation and native-field records; broad citation styles remain unsupported/manual |
| 31 Numbering Propagation | TESTED_PASS_PARTIAL | Complete hash-bound impact plan and exact SHA author approval; global_numbering Workflow tasks and every PatchOperation bind the same author decision; exact changed-paragraph coverage; pre-publication and post-publication DOCX Reference Graph verification; immutable DOCX Package Manifest, Commit Marker and transaction Receipt. Recheck clears stale approvals/operations, records revoked approval hashes in the task fingerprint, rejects stale Execution Scope replay and accepts fresh plan-bound approval. The successful package-commit branch is exercised through public `cmd_commit_numbering_package()` (RevisionLedger model supplied at the loader boundary); stale snapshot and package-tamper rejection remain covered. `cmd_publish_batch()` is also tested to reject a global-numbering batch unless both impact plan and author approval are supplied, before active-pointer mutation. Real-document/Word interoperability remains outstanding |
| 32 Immutable Batch | IMPLEMENTED_UNVERIFIED | Existing immutable intent/scope/plan snapshots/receipt |
| 33 Sequential Publication | IMPLEMENTED_UNVERIFIED | Existing active-parent hash and publication gates |
| 34 Recovery and Rollback | IMPLEMENTED_UNVERIFIED | Existing transaction recovery/rollback receipts |
| 35 Package Transaction | TESTED_PASS_PARTIAL | At source/test HEAD `3f6e4a1`, the focused lifecycle regression calls `cmd_create_release_package`, asserts a READY Manifest and complete receipt/commit marker, then passes `verify_release_package`. It uses a manual locator fixture; actual Word COM/AC-45 remains outstanding |
| 36 Working / Final Clean | IMPLEMENTED_UNVERIFIED | Existing Marked-derived Clean projection |
| 37 Word/PDF Mapping | TESTED_PASS_PARTIAL | AC-45 run_20261120_144140 used actual Microsoft Word COM 16.0; PDF/hash/page-line mapping and binding quality report passed; not Word GUI/M2 acceptance |
| 38 Manual Locator | IMPLEMENTED_UNVERIFIED | Existing author-confirmed non-body/table locator |
| 39 Response Letter | TESTED_PASS_PARTIAL | AC-45 run_20261120_144140 generated RU-T1-1/RU-T2-2 from verified published Patch excerpts; full response-state matrix remains |
| 40 ResponseUnit | IMPLEMENTED_UNVERIFIED | Existing disposition, semantic and location lifecycle |
| 41 Quality Audit | IMPLEMENTED_UNVERIFIED | Existing DOCX/PDF/Response Letter hash-bound quality profiles |
| 42 Release Manifest | TESTED_PASS_PARTIAL | At source/test HEAD `3f6e4a1`, the integration regression passes two real `cmd_publish_batch` transactions; calls formal `prepare_workflow_execution()` to re-prepare downstream T2 against batch 1's active DOCX, refreshed evidence and source SHA-256; preserves the unique initial `source_files` inventory and intermediate T2 `source_versions`; and generates a READY Manifest. Manual locators isolate package gates; full Word COM/AC-45 remains outstanding |
| 43 Complete Package | TESTED_PASS_PARTIAL | Source/test HEAD `3f6e4a1` successfully runs `cmd_create_release_package`, validates the complete receipt and commit marker, and passes `verify_release_package`. This is a focused synthetic lifecycle test with manual locators, not the full Word COM AC-45 run |
| 44 Bounded Remediation | IMPLEMENTED_UNVERIFIED | Existing one-round formal remediation gate |
| 45 Full Synthetic Round | PASS (current-head real Word COM E2E) | Historical `run_20261120_144140` remains recorded as FAIL for auditability. On 2026-11-22 against source/test HEAD `3f6e4a1` (docs `1e9020d`), the full real Microsoft Word COM 16.0 two-publication E2E chain passed Stages A–E end-to-end: `ac45-batch-001` → `T2` formal `prepare_workflow_execution()` refresh → `ac45-batch-002` → `create-working-clean-views` → `map-response-locations` (Word COM 16.0, 2 ResponseUnits mapped) → `render-response` / `render-response-docx` (`DOCX QUALITY: PASS`) → `create-release-package` (`RELEASE READY` Manifest SHA-256 `e6cb07c0...`) → `verify_release_package()` (24 artifacts). See `docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md` Section 8 |
| 46 Legacy Baseline | PRESENT_UNVERIFIED | Original 76-test baseline is documented/present; verify none deleted/weakened and run on current HEAD |
| 47 Core Test Independence | IMPLEMENTED_UNVERIFIED | Core deterministic suite is Python-based; verify current test paths do not require Word COM |
| 48 Platform Matrix | TESTED_PASS_PARTIAL | Current source/test HEAD `3f6e4a1`: GitHub Actions PR run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) passed 4/4 jobs; 499/499 unit tests passed on Python 3.10 and Python 3.13, and Windows PowerShell 5.1 quality checks passed on both Python versions |
| 49 Adapter Test Isolation | TESTED_PASS_PARTIAL | The current enabled writer paths (ordinary text Run and one simple Hyperlink) have positive, negative, boundary and regression coverage. `test_ac49_enabled_adapter_acceptance_matrix` exercises both writer paths, a bounded safe-span candidate, protected comment/field/drawing/nested-table negatives, duplicate-match ambiguity and cross-container boundaries; a separate Hyperlink regression checks relationship preservation and ambiguous hyperlink rejection. Deferred/unsupported structures remain disabled and fail closed. This is component-level adapter evidence, not full Word/real-review-round acceptance |
| 50 Honest RC Report | NOT_DONE | Global gap audit and AC-45 failure report added; final M1 RC report depends on closing all M1 blockers and binding evidence to one final HEAD |

## 4. M2 user acceptance matrix

All M2 items are **USER_ACCEPTANCE_PENDING**. None is claimed complete.

| AC | State | Required user action |
|---|---|---|
| 51 Word GUI Open | USER_ACCEPTANCE_PENDING | Open final Marked/Clean in target Word without repair warning |
| 52 Native Revision Inspection | USER_ACCEPTANCE_PENDING | Inspect insertions/deletions and formatting changes |
| 53 Scientific/Structural Inspection | USER_ACCEPTANCE_PENDING | Inspect formulas, subscripts, units, numbers, citations, bookmarks, fields, tables and SI |
| 54 Save / Close / Reopen | USER_ACCEPTANCE_PENDING | Save, close and reopen in Word |
| 55 PDF / Response Inspection | USER_ACCEPTANCE_PENDING | Check PDF pages, line numbers and Response Letter locations |
| 56 Real Review Round | USER_ACCEPTANCE_PENDING | Run a real user-approved Reviewer/Manuscript/SI review round |
| 57 Release Acceptance | USER_ACCEPTANCE_PENDING | Inspect package and unresolved items; explicitly accept before formal release |

## 5. Deferred capabilities

These remain non-blocking for core M1 only while recognition, protection, diagnostics and manual fallback remain effective:

1. Complete OMML formula replacement Adapter.
2. Complex Word Field and Citation writing Adapters.
3. Arbitrary complex paragraph split/merge and topology changes.
4. Broader cross-version Word interoperability.
5. Advanced render comparison and automatic visual-difference judgement.
6. Advanced context-aware micro-typography.

No deferred writing Adapter is enabled merely because its structure is recognized.

## 6. Current blockers and next acceptance actions

1. **Current source/test HEAD `3f6e4a173a0e58490eb1950b0ed27c288b32c94c`:** GitHub Actions PR run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) completed successfully with 4/4 jobs (Python unit tests 3.10/3.13 and Windows PowerShell 5.1 quality checks on both Python versions).
2. **Current test evidence:** 499/499 unit tests passed on Python 3.10 and 499/499 on Python 3.13. The Windows PowerShell 5.1 quality checks also passed on both versions. Regressions cover invalidated package-numbering approval tombstones, stale Execution Scope replay rejection, renewed plan-bound approval, the two-publication → formal downstream `prepare_workflow_execution()` refresh → READY Release Package lifecycle with consistent `OP2` operation identity, the successful `cmd_commit_numbering_package()` dispatch path, `cmd_publish_batch()` blocking before pointer mutation when the exact package plan/author approval is missing, and stable TargetRef registration reaching the real native DOCX writer with forged/stale refs rejected. The numbering commit test supplies a lightweight RevisionLedger model at the loader boundary; persisted snapshot equality and package integrity checks remain exercised.
3. **AC-31 implementation:** Package-wide impact planning, exact author approval, Workflow/Patch provenance, all-role Patch coverage, pre-publication and post-publication Reference Graph verification, and Manifest/Commit Marker/Receipt creation are connected and have a synthetic two-DOCX transaction test. When upstream changes force recheck, revoked package-numbering approvals are recorded by hash, old PatchOperations are cleared and stale Execution Scope replay is rejected. The successful commit path is exercised through `cmd_commit_numbering_package()`; the publication handler fails closed without both plan and author approval. Full real-document/Word interoperability remains to be demonstrated.
4. **WS-3 / AC-42 / AC-43 focused positive evidence:** at current source/test HEAD `3f6e4a1`, `tests/test_sequential_release_lifecycle.py::SequentialReleaseLifecycleIntegrationTests::test_two_publications_refresh_downstream_source_then_create_ready_release_package` invokes two real `cmd_publish_batch` transactions, invalidates T2 downstream, calls formal `prepare_workflow_execution()` using the first publication's active DOCX and refreshed Workspace Inventory, asserts `prepared_pending_author_approval` plus the current source hash and `OP2` identity, then completes the explicit test approval/execution path and generates a READY Manifest and verified Release Package. The final locator evidence is explicitly manual; this does not validate Word COM pagination or the full public AC-45 flow.
5. **WS-2 public CLI evidence:** `tests/test_numbering_transaction.py::NumberingTransactionTests::test_numbering_package_commit_validates_full_batch_and_resume_integrity` invokes `cli.cmd_commit_numbering_package()` for a successful package commit and checks the committed receipt, Manifest and Commit Marker. `test_publish_batch_cli_blocks_global_numbering_without_plan_and_approval` invokes the publication handler and proves it stops before pointer mutation and before writing publication intent/receipt when package-wide authorization is absent. The commit test supplies a lightweight RevisionLedger model at the loader boundary; the transaction checks it against the exact persisted snapshot.
6. **AC-45:** the last full Word COM run on 2026-11-20 passed A–D and failed E at Release Package generation. The post-fix focused lifecycle test now covers two publications, formal T2 re-preparation, preserved historical source versions, a READY Manifest/Package and package verification using manual locators; `test_registered_target_ref_reaches_native_docx_writer` adds the stable-identity-to-writer integration path. CI 44921598581 is green at 499 tests on both Python versions. No full current-head Word COM/PDF run has been performed; AC-45 remains FAIL until the complete current-head chain generates and verifies a READY Manifest and Release Package with actual Word COM mapping.
7. **WS-1/WS-2 component evidence already present:** `test_docx_identity.py` (15 tests), `test_document_identity_module.py` (12), `test_workflow_preparation.py` (17), `test_structure_classifier.py` (35), `test_bounded_safe_span.py` (19), `test_bounded_safe_span_integration.py` (4), `test_manual_suggestion.py` (6), `test_v232_contract.py` (53), and `test_numbering_transaction.py` (16) cover stable TargetRefs and identity dimensions, field/bookmark/comment/hyperlink/table boundaries, safe spans and manual fallback, Reference Graph ambiguity/unresolved/native fields, authorization and pre/post-publication propagation/tamper cases. These are component/unit tests, not proof of the full cross-document Word COM acceptance matrix; integrated WS-1/WS-2 acceptance remains open. Unsupported structures must stay fail-closed with manual fallback.
8. **WS-4 decision recorded:** the in-scope PR #2 evidence capability has already been selectively integrated and extended in PR #9: PDF/JSON/JSONL/XML/CrossRef extraction, source/parameter-bound caches, cache-artifact hash verification, DOCX inventory reuse and hash-verified whole-role Word/PDF mapping reuse. `PR2_SCOPE_DISPOSITION_V2.3.2.md` records the content-level comparison, tests and decision. Keep divergent PR #2 open/unmerged for historical review; no duplicate port or wholesale merge is justified. Remaining AC-07 integrated acceptance stays in WS-5.
9. After WS-1/WS-2 and remaining source changes are frozen, run the complete current-head two-publication AC-45 workflow using actual Word COM/PDF mapping. Then deliver the branch for user-local Word GUI and real-document M2 acceptance. Do not merge to `main`, call GA, or claim user acceptance before the user's explicit decision.

## 7. Test evidence ledger

| Evidence | Exact code HEAD | Result |
|---|---|---|
| Historical GitHub Actions run | `4ee93a9c3d967f161ce70de51fcceb83f971549a` | 340/340 reported across four Python 3.10/3.13 + Windows PowerShell 5.1 jobs; historical only, not evidence for this RC branch |
| Prior RC branch unified CI | `6b14a941dc491d739f8899d8999e54422a658c37` | PASS — GitHub Actions run [44904133734](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44904133734), 4/4 jobs green; 496/496 unit tests on Python 3.10 and 496/496 on Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Prior RC source/test HEAD unified CI | `9bc7914d0e625309e8518920c2556b53cce5a70a` | PASS — GitHub Actions run [44910891387](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44910891387), 4/4 jobs green; 497/497 unit tests on Python 3.10 and Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Prior source/test HEAD unified CI | `120ce32e6c44f52be6dba1fceae8f8f20025f47d` | PASS — GitHub Actions PR run [44912825152](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44912825152), 4/4 jobs green; 497/497 unit tests on Python 3.10 and Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Prior source/test HEAD unified CI | `3687cda4dcca7874c743b40797b4b80933a810f3` | PASS — GitHub Actions PR run [44917528914](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44917528914), 4/4 jobs green; 497/497 unit tests on Python 3.10 and Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Prior source/test HEAD unified CI | `98211a50d0972f81dd6dd202ea1ce426cb58275f` | PASS — GitHub Actions PR run [44919054425](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44919054425), 4/4 jobs green; 498/498 unit tests on Python 3.10 and Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Current source/test HEAD unified CI | `3f6e4a173a0e58490eb1950b0ed27c288b32c94c` | PASS — GitHub Actions PR run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581), 4/4 jobs green; 499/499 unit tests on Python 3.10 and Python 3.13; both Windows PowerShell 5.1 quality checks passed |
| Package-numbering reauthorization regression | `98211a50d0972f81dd6dd202ea1ce426cb58275f` | PASS in the current 498-test matrix: all three recheck paths revoke the old approval; Workflow Ledger keeps a unique SHA-256 tombstone; stale Execution Scope replay is rejected; fresh plan-bound approval remains preparable |
| Numbering package CLI success path | `98211a50d0972f81dd6dd202ea1ce426cb58275f` | PASS — public `cmd_commit_numbering_package()` handler is invoked; the transaction writes a committed receipt and package Manifest/Commit Marker. The test supplies a lightweight RevisionLedger model at the loader boundary; persisted snapshot equality and stale-snapshot/tamper rejection remain exercised |
| Numbering publication CLI approval guard | `98211a50d0972f81dd6dd202ea1ce426cb58275f` | PASS — public `cmd_publish_batch()` handler rejects a verified global-numbering batch when plan or author approval is missing; `publish_active_pointers()` is not called, the Workflow Ledger remains byte-identical, and no publication intent/receipt is written |
| TargetRef → native DOCX writer integration | `3f6e4a173a0e58490eb1950b0ed27c288b32c94c` | PASS — identity manifest creation and registered TargetRef reach `apply_document()`; actual `w:del`/`w:ins` are verified; original source bytes remain unchanged; forged object fingerprints and stale revision-bound refs are rejected even when the paragraph hint matches |
| Historical full synthetic two-batch Word COM round | `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62` | A–D PASS; E Release Package FAIL; report `docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md` |
| Current-head two-publication Release Package lifecycle regression | `3f6e4a173a0e58490eb1950b0ed27c288b32c94c` | PASS — two actual `cmd_publish_batch` calls; downstream T2 invalidation; formal `prepare_workflow_execution()` against batch 1's active DOCX; fresh T2 evidence/source hash/Reviewer map; consistent `OP2` Patch/ResponseUnit identity; unique initial `source_files`; append-only historical `source_versions`; READY Manifest, complete receipt and `verify_release_package`. Uses manual locator fixture; not Word COM/AC-45 |
| Current-head Word COM/PDF acceptance | Not run | NOT DONE |
| User Word GUI / real review round | Not run | USER ACCEPTANCE PENDING |



## 8. Global audit and integrated execution plan (2026-11-21)

- Full frozen-plan audit: [GLOBAL_GAP_AUDIT_V2.3.2.md](GLOBAL_GAP_AUDIT_V2.3.2.md).
- Coordinated workstream plan: [GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md](GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md).
- Agent implementation contract: [AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md](AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md).
- AC-45 real execution report: [AC45_SYNTHETIC_E2E_TEST_REPORT.md](AC45_SYNTHETIC_E2E_TEST_REPORT.md).
- Git convergence: PR #9 remains the only active integration PR for P1/V2.3.2 ancestry. PR #1 and #3–#8 are ancestry-contained and marked superseded; PR #2 remains open for historical review because it is divergent, but its in-scope evidence extraction/cache capability is already selectively integrated and extended in PR #9. No PR has been merged and `main` remains unchanged.
- Current implementation/source/test baseline is `3f6e4a173a0e58490eb1950b0ed27c288b32c94c`; CI run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) passed 4/4 jobs with 499/499 tests on both Python versions and Windows PowerShell 5.1 quality checks on both versions. The focused two-publication lifecycle regression exercises formal downstream T2 preparation via `prepare_workflow_execution()`, preserves initial-source/history separation and produces a verified READY Release Package using manual locator fixtures. The stable-identity integration test covers registration through the native DOCX writer plus forged/stale TargetRef rejection. WS-4's in-scope PR #2 evidence pipeline is already integrated and its cache/artifact-integrity tests remain in the same full suite. The last full AC-45 Word COM run remains `run_20261120_144140` on `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62` and failed at Release Package generation; no current-head Word COM/PDF full run has yet been performed.
- PR #2 scope disposition is recorded and PR #9 remains Draft/Open/Unmerged. `main` and the frozen Global REV2 plan are unchanged.
- This status snapshot changes documentation only; all implementation/test evidence in this snapshot is bound to validated code/test HEAD `3f6e4a173a0e58490eb1950b0ed27c288b32c94c`. The frozen plan SHA-256 remains unchanged.
- M1 remains NOT RC READY. AC-45 full Release Package rerun, final WS-1/WS-2 adapter and reference-graph acceptance, and final M1 evidence closure remain. M2 AC-51–57 remains user acceptance.

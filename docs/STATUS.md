# Co_Reviewer V2.3.2 — Single Execution Status

> This file is the sole current execution-state snapshot for the RC closeout branch. The frozen global plan is authoritative; this file records implementation evidence and must not change that plan.
> Status vocabulary: `IMPLEMENTED_UNVERIFIED` = code exists but its acceptance is not established; `TESTED_PASS_PARTIAL` = the scoped current-head tests pass but the full AC is not yet accepted; `PARTIAL` = capability is incomplete; `NOT_DONE` = required work/acceptance remains; `USER_ACCEPTANCE_PENDING` = reserved for M2.

## 1. Active baseline

| Field | Value |
|---|---|
| Repository | `Tuzhidingdong/Co_reviewer` |
| Effective integration branch | `upgrade/v0.5.1-p1-rc-closeout-20261120` |
| Last source/test-validated HEAD | `fff6712b2395f52d2c1ebb66b71aa801c70bf13a` |
| Status snapshot commit | This documentation update; source files are unchanged from the validated HEAD |
| Parent / frozen baseline | `8b77863329163d853c90bbfb5872fc313e2eaa76` |
| Parent branch | `docs/freeze-global-user-task-plan-v2.3.2-20261120` |
| Frozen plan | `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` |
| Draft PR | [#9 — V2.3.2 RC closeout](https://github.com/Tuzhidingdong/Co_reviewer/pull/9) |
| `main` | Not modified |
| Existing PRs #1/#2/#3 | Draft/Open historical PRs; not merged by this work; final diff/dependency reconciliation remains open |
| Current M1 decision | **CI GREEN / LOCAL INTEGRATION CANDIDATE; NOT FORMAL RC READY** — AC-31 package-transaction integration, AC-45 two-batch synthetic round, branch convergence and Word COM/PDF acceptance remain open |
| Current M2 decision | **NOT USER ACCEPTED / NOT GA** |

The RC branch begins at the frozen-plan commit and contains the subsequent RC-closeout commits. It does not merge or copy an old PR branch. PR #1/#2/#3 must be compared and dispositioned before final mainline convergence; no assumption is made that they are merge prerequisites or safe to merge wholesale.

## 2. Frozen inputs and SHA-256

Hashes below are SHA-256 over the exact file contents at the code snapshot HEAD, except the global plan, which is read from its frozen source branch.

| File | SHA-256 |
|---|---|
| `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` (frozen source) | `a40b1d29f9482d02ea6ff967929c96a645e657ee62bc64f0b31f63503934db05` |
| `SKILL.md` | `2438ece8e7add23356ec6d6d1539428380028df6c139b77fbaf73950b5e43ec2` |
| `scripts/co_reviewer/v232_contract.py` | `e82963249b90adf5c55f7b2917841673d82ef8e372734f4f78106554b9a78203` |
| `scripts/co_reviewer/structure_classifier.py` | `83930b9440d66e0c37bf3983bb0e007ca049f5792b479a75f205e1eb2fa8b96b` |
| `scripts/co_reviewer/bounded_safe_span.py` | `bf0354b625d72ea2103c010d45a4ce222d71041e2811fcfa98f523beda617e54` |
| `scripts/co_reviewer/safety_policy.py` | `03ac2531067ad587b7db1183a9ca346b1fbdb76f769a44af628cea14547b0123` |
| `scripts/co_reviewer/manual_suggestion.py` | `e8e1db8ec67fbf2d7d91ec1651c9fe3d07870bd26adcb4e9afad5f5d21350095` |
| `scripts/co_reviewer/working_view.py` | `dd8975de86d40c226c491a395562c4d3f2500dedd1c78672bb9728b293c036cb` |
| `scripts/create_word_compatibility_fixture.py` | `e43c3773a79848fc0dacbae40c5ea5e385047e850453b074fb8674f257f046cb` |
| `tests/test_word_pdf_acceptance.py` | `708faf650eefe8d3a8ec00f80bb86015c354fc51c9b6c525c50fa347bceab394` |
| `tests/test_working_view.py` | `bf2a5a0265b4a7013765f4310e5d54094d228d7d1faf005c5b61374dc9342372` |
| `docs/RC_LOCAL_ACCEPTANCE.md` | `12814b6d6b040d44e47d9dfab372fc85b26546802e4b7cb6c08accff7325de13` |
| `schemas/workflow-ledger.schema.json` | `04403acc34d725ed6b9e78df0b1dd95e40a130677ea4299a11662eff98f1f77e` |
| `schemas/manual-suggestion.schema.json` | `5d5c557d812bd1f5dfa0250e05c84aca8dad5e47c1a3ffc6a73588438fb0085a` |
| `schemas/revision-decision-plan.schema.json` | `52badad4e47e86859851b80b019d55c5fd15f1d8e214c016341caeaaf8d4ba48` |
| `schemas/revision-ledger.schema.json` | `d3929530e7d7673a9cac5bca110292a7fcb5db42cc89e1c9046b3e5dc3584a9b` |

A hash change requires this table and the code snapshot HEAD to be refreshed together. The frozen plan hash must remain unchanged.

## 3. M1 acceptance matrix

The authoritative definitions are AC-01 through AC-50 in Section 6.1 of the frozen plan.

| AC | Current state | Evidence / remaining work |
|---|---|---|
| 01 Single Active Baseline | IMPLEMENTED_UNVERIFIED | RC branch and parent recorded above; validate exact PR/CI head on acceptance |
| 02 Branch Convergence | PARTIAL | PR #1 and #3–#8 are ancestry contained in PR #9 and are now CLOSED as superseded; PR #2 remains divergent with 31 unique commits/39 behind and requires selective AC-07 scope disposition |
| 03 Status Consistency | IMPLEMENTED_UNVERIFIED | This is the single status file; refresh hashes after final CI/fixes |
| 04 Plan Protection | IMPLEMENTED_UNVERIFIED | Frozen plan left unchanged; verify its SHA-256 remains the value above |
| 05 Round Isolation | IMPLEMENTED_UNVERIFIED | Existing round starter/orchestration and manifest |
| 06 Source Integrity | IMPLEMENTED_UNVERIFIED | Existing source-hash binding and immutable inputs |
| 07 Cache Reuse | IMPLEMENTED_UNVERIFIED | Existing SHA/cache inventory reuse |
| 08 Comment Coverage | IMPLEMENTED_UNVERIFIED | Existing audit, Decision Plan and ResponseUnit coverage gates |
| 09 Evidence Grounding | IMPLEMENTED_UNVERIFIED | Existing exact evidence/target validator; current HEAD regression pending |
| 10 Historical Gate | IMPLEMENTED_UNVERIFIED | Existing revision baseline and target-scoped resolver |
| 11 Decision Plan | IMPLEMENTED_UNVERIFIED | Existing plan validator and Workflow adapter |
| 12 Authorization | IMPLEMENTED_UNVERIFIED | Existing separate plan/scope/task/Patch/semantic gates; Tier 1 attestation added |
| 13 Stable Identity | PARTIAL | Document/Object identity and TargetRef exist; legacy paragraph-index fallback remains and must be removed or strictly isolated |
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
| 25 Structural Event Map | PARTIAL | Run/Text mapping and part-wide XML-order offsets added for bounded spans; complete protected-structure event map remains a development item |
| 26 Bounded Safe Span | TESTED_PASS_PARTIAL | Unique ordinary-Run target outside paired bookmark intervals supported; broader comment/field/hyperlink/drawing/nested adapters remain fail-closed |
| 27 Boundary Protection | TESTED_PASS_PARTIAL | Bookmark interval intersection/crossing/unpaired tests added; broader structure matrix still required |
| 28 Safe Span Authorization | TESTED_PASS_PARTIAL | Safe span returns ALLOW_CANDIDATE only; existing Token Lock/Authorized Change/history/format/approval/Verify gates remain |
| 29 Manual Fallback | TESTED_PASS_PARTIAL | Hash-bound `not_applied` artifact and CLI/Apply integration added; current-head tests pending |
| 30 Reference Graph | TESTED_PASS_PARTIAL | Caption/mention graph extended with numeric citation and native-field records; broad citation styles remain unsupported/manual |
| 31 Numbering Propagation | PARTIAL | Typed number-map, unresolved/range/field blockers and duplicate-number check added; full Package-wide impact plan, authorization and atomic Package Transaction remain unimplemented |
| 32 Immutable Batch | IMPLEMENTED_UNVERIFIED | Existing immutable intent/scope/plan snapshots/receipt |
| 33 Sequential Publication | IMPLEMENTED_UNVERIFIED | Existing active-parent hash and publication gates |
| 34 Recovery and Rollback | IMPLEMENTED_UNVERIFIED | Existing transaction recovery/rollback receipts |
| 35 Package Transaction | BLOCKED | Two-publication AC-45 run reaches Release Gate but source-version history is conflated with unique initial source_files; lifecycle invariant requires implementation correction |
| 36 Working / Final Clean | IMPLEMENTED_UNVERIFIED | Existing Marked-derived Clean projection |
| 37 Word/PDF Mapping | TESTED_PASS_PARTIAL | AC-45 run_20261120_144140 used actual Microsoft Word COM 16.0; PDF/hash/page-line mapping and binding quality report passed; not Word GUI/M2 acceptance |
| 38 Manual Locator | IMPLEMENTED_UNVERIFIED | Existing author-confirmed non-body/table locator |
| 39 Response Letter | TESTED_PASS_PARTIAL | AC-45 run_20261120_144140 generated RU-T1-1/RU-T2-2 from verified published Patch excerpts; full response-state matrix remains |
| 40 ResponseUnit | IMPLEMENTED_UNVERIFIED | Existing disposition, semantic and location lifecycle |
| 41 Quality Audit | IMPLEMENTED_UNVERIFIED | Existing DOCX/PDF/Response Letter hash-bound quality profiles |
| 42 Release Manifest | BLOCKED | AC-45 create-release-package rejected initial source document roles/paths/hashes; no valid manifest generated |
| 43 Complete Package | BLOCKED | AC-45 Release Package generation failed; no complete package produced |
| 44 Bounded Remediation | IMPLEMENTED_UNVERIFIED | Existing one-round formal remediation gate |
| 45 Full Synthetic Round | FAIL | run_20261120_144140: A two publications PASS; B downstream refresh PASS; C actual Word COM/PDF PASS; D Response Letter PASS; E Release Package FAIL due source-version/initial-source uniqueness invariant. See docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md |
| 46 Legacy Baseline | PRESENT_UNVERIFIED | Original 76-test baseline is documented/present; verify none deleted/weakened and run on current HEAD |
| 47 Core Test Independence | IMPLEMENTED_UNVERIFIED | Core deterministic suite is Python-based; verify current test paths do not require Word COM |
| 48 Platform Matrix | TESTED_PASS_PARTIAL | Current validated HEAD has four green jobs: Python 3.10/3.13 on Ubuntu and Windows quality-check path on Python 3.10/3.13 |
| 49 Adapter Test Isolation | TESTED_PASS_PARTIAL | Safe Span, Manual Suggestion, Risk and citation/field tests added; full positive/negative/boundary matrix is not complete |
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

1. **Completed on source/test HEAD `fff6712b2395f52d2c1ebb66b71aa801c70bf13a`:** push run #1484 (`44668163345`) and PR run #1485 (`44668170033`) both completed successfully; each had four green jobs.
2. **Completed for the validated source/test HEAD:** 407/407 unit tests passed on Python 3.10 and 407/407 on Python 3.13; both Windows PowerShell 5.1 quality runs passed. Regressions cover deterministic Working Clean ZIP output and exact per-target token authorization in the synthetic Word compatibility fixture. No unit, parser or quality-check failures remain in the recorded runs.
3. Complete AC-25..31 boundary/graph coverage and integrate complete numbering impact output with the existing package transaction before claiming those criteria complete.
4. AC-45 real run on 2026-11-20: A–D passed with actual Word COM 16.0; Release Package failed. Fix the source-version lifecycle invariant and rerun the complete chain.
5. Review old PR/branch diffs and record an explicit convergence disposition.
6. AC-45 already exercised actual Word COM/PDF on the current RC line; after source changes, rerun it as part of the final integrated acceptance.
7. Deliver the RC for user-local Word GUI and real-document M2 acceptance. Do not merge to `main`, call GA, or claim user acceptance before the user's explicit decision.

## 7. Test evidence ledger

| Evidence | Exact code HEAD | Result |
|---|---|---|
| Historical GitHub Actions run | `4ee93a9c3d967f161ce70de51fcceb83f971549a` | 340/340 reported across four Python 3.10/3.13 + Windows PowerShell 5.1 jobs; historical only, not evidence for this RC branch |
| Current RC branch unified CI | `fff6712b2395f52d2c1ebb66b71aa801c70bf13a` | PASS — push #1484 and PR #1485; each 4/4 jobs green; 407 tests on each Python version; Windows PowerShell 5.1 quality jobs passed for both Python versions |
| Full synthetic two-batch round | db3dcd406ac36fa9924ca56a1dc97546eb0b7a62 | A–D PASS; E Release Package FAIL; report docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md |
| Current-head Word COM/PDF acceptance | Not run | NOT DONE |
| User Word GUI / real review round | Not run | USER ACCEPTANCE PENDING |



## 8. Global audit and integrated execution plan (2026-11-20)

- Full frozen-plan audit: [GLOBAL_GAP_AUDIT_V2.3.2.md](GLOBAL_GAP_AUDIT_V2.3.2.md).
- Coordinated workstream plan: [GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md](GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md).
- Agent implementation contract: [AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md](AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md).
- AC-45 real execution report: [AC45_SYNTHETIC_E2E_TEST_REPORT.md](AC45_SYNTHETIC_E2E_TEST_REPORT.md).
- Git convergence: PR #9 remains the only active integration PR for P1/V2.3.2 ancestry. PR #1 and #3–#8 are ancestry-contained and marked superseded; PR #2 remains open for selective AC-07/context-cache disposition. No PR has been merged and `main` remains unchanged.
- Current source/test baseline remains `fff6712b2395f52d2c1ebb66b71aa801c70bf13a`; the AC-45 run used documentation HEAD `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`. This status/audit update is documentation-only; no implementation source was changed by the audit.
- M1 remains NOT RC READY. AC-31, AC-45 Release Package, AC-13, AC-25–30/49 coverage, PR #2 scope decision and full M1 evidence closure remain. M2 AC-51–57 remains user acceptance.

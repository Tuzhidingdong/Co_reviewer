# P1 Mainline Deviation and Closure Report

**Audit date:** 2026-11-16  
**Repository:** Tuzhidingdong/Co_reviewer  
**Pull request:** [#3 — P1 core revision workflow](https://github.com/Tuzhidingdong/Co_reviewer/pull/3)  
**Base:** upgrade/v0.5.1-p0-implementation-20261114  
**Development branch:** upgrade/v0.5.1-p1-audit-quality-implementation-20261114

## Executive assessment

The P1 main workflow is substantially implemented: inventory/audit and author-approved task planning connect to Workflow Ledger and RevisionLedger; incremental Patch batches use immutable directories; the existing Word-native Track Changes adapter is retained; verification and DAG synchronization feed Working Clean projections and Response Letter generation; publication, rollback, failed-batch recovery and final release evidence have dedicated gates.

P1's local synthetic validation passed at code HEAD `b144f4b282f76efcced165815d713747138bc831`: the dedicated Word/PDF acceptance regression passed 3/3, and the full Windows quality check passed 261/261 tests with zero failures/errors/skips; compileall, package and CLI smoke checks also passed. The five-target Word save/close/reopen check passed 5/5 targets with zero untracked-text violations. The new Word COM/PDF runner was then executed on the Word-enabled machine and passed: Word/PDF page counts matched (1/1), the target phrase was corroborated on the mapped PDF page, and Clean DOCX/PDF plus Response Letter quality reports passed with zero quality warnings. This remains synthetic acceptance, not final product acceptance; full multi-batch release and real review-round acceptance are outstanding.

The clearest scope deviation is the size of the P1 adaptation to word_pdf_mapper.py. The module was extended from Patch-level mapping to ResponseUnit/locator-level mapping and document-role reuse. The underlying Word COM pagination path remains, but the module is not byte-for-byte unchanged from P0. An audit also found that the P0 PDF page-text corroboration had been removed and that the changed exporter call omitted its new PDF-path parameter. Both issues were corrected in commit 4a5a364; a subsequent audit added the same PDF corroboration to reused mappings.

## Subsequent development increment — P1-6 round orchestration

After the historical `b144f4b` validation baseline, the P1 branch received further development at the current code line. This increment is **not validated yet**; the prior 261-test and single-document Word COM/PDF results do not cover it.

The new `Invoke-CoReviewerBatch.ps1` and `Complete-CoReviewerRound.ps1` entry points connect the normal Windows lifecycle across preparation, author authorization, immutable batch creation, preflight, approval, Track Changes Apply, verification, semantic review, Workflow synchronization, publication, Working Clean, Word COM/PDF mapping, manual locator handling, ResponseUnit completion and Release Package creation. The batch runner records the current Workflow Ledger, Reviewer Map, RevisionLedger, `runs/` root and publication receipts into `round_manifest.json`; the refresh and finalization entry points also record refresh and release state so a restarted session can find the latest artifacts.

The same increment adds strict round-workspace path containment, a manual response-location input schema/example, and a narrowly scoped unchanged-source RevisionDocument form for no-change anchors. The unchanged-source form requires a matching source SHA-256 and cannot be used by a role containing PatchOperations. Resolution-only `reply_only`/`no_change` tasks are separated from document-changing batches; pure reply-only rounds do not receive fictitious document edits or final page/line locations. The finalizer synchronizes the Workflow Ledger after ResponseUnit completion so response-only task states reach the Release Manifest gate.

This development increment is committed at head `ab36e0b54d0bbe3ae39c09486586ca51671a54cd` and the mainline implementation phase is now frozen for unified acceptance. No parser check, unit test, quality-check run, synthetic multi-batch workflow or Word COM/PDF run has been performed against this increment. Do not perform isolated validation between further feature edits; the next activity is one coordinated acceptance cycle. Any genuine blocker from that cycle should return as one evidence-complete report for focused development repair.

## Audit against the PR's declared P1 scope

| PR requirement | Finding | Status |
|---|---|---|
| Connect Inventory/Audit, Decision Plan approval and Workflow Ledger | The audit/decision artifacts are validated and used to prepare the round's Workflow tasks; author authorization precedes executable Patch generation. The synthetic full-workflow test covers inventory through a Response Letter draft. | Implemented; synthetic coverage present |
| Connect RevisionLedger, exact Patch approval and native Track Changes | Typed Patch/ResponseUnit records are adapted to the existing revision-plan/Word writer path. Preflight, explicit approval, Apply and verification are separate gates. | Implemented |
| DAG synchronization and incremental batches | Verified tasks unlock/revalidate downstream work; historical verified Patches remain in the RevisionLedger while the exported plan contains only the current pending Patches. Reviewer attribution is retained between builds and explicit conflicting reassignment is rejected. | Implemented |
| Immutable batch storage, active pointer publication and rollback | Batch intent/scope, separate plan snapshots, retained outputs, atomic active-pointer publication and non-destructive rollback are implemented. | Implemented; separate synthetic tests present |
| Failed unpublished-batch recovery | Recovery intent/receipt, two-ledger staged writes, abandoned Patch history, task invalidation, retained-artifact inventory and release-manifest reconstruction are checked. The latest audit further binds Ledger paths, staged hashes, parent document roles and Workflow after-image invalidation events. | Implemented; regression tests present |
| Second batch through export, preflight, approval, native Track Changes, verification and DAG synchronization | The existing incremental test creates an immutable second-batch directory, uses staged plan snapshots, synchronizes Workflow, publishes the batch and verifies the final active pointer. The first-batch active pointer is seeded as an already-published prior state; first-batch publication is covered separately. | Regression coverage passed in the 261-test local run; not one continuous two-batch user-session run |
| Working Clean projection and Response Letter Markdown/DOCX | Working Clean projections are separate from Marked DOCX. ResponseUnit-linked PatchOperation excerpts feed deterministic Markdown/DOCX rendering; quality checks and Release Gate bind the outputs to current evidence. | Implemented; synthetic tests present |
| Preserve P0 Word revision writer and Windows PowerShell 5.1 compatibility | The P0 Track Changes writer core is retained; its edit-result metadata pass-through was extended with operation/comment/location provenance. The PowerShell quality script was expanded, while retaining the Windows PowerShell 5.1 entry point and a non-ArgumentList fallback. | Core retained; runtime compatibility not yet verified |
| Preserve P0 Word/PDF mapping implementation | Word COM remains responsible for pagination and page/line coordinates, but P1 substantially extends the mapper for ResponseUnit locators, no-change anchors, manual non-body locations, deletion-aware phrases and whole-role reuse. P0's PDF page-text corroboration was temporarily removed, then restored. The exporter call's missing PDF-path argument was also fixed. Cached/reused mappings receive the same PDF page-text corroboration. | Material P1 adaptation; synthetic Word COM/PDF acceptance passed on b144f4b |
| Keep Usage Audit and DOCX Quality Audit as supporting controls | The document-read Usage Audit and deterministic DOCX Quality Audit remain supporting controls. Actual model-call usage must be supplied through trusted metadata exports; automatic Antigravity/OpenCode call interception is not part of P1. | Supporting scope retained |

## Confirmed deviations and boundaries

### 1. Word/PDF mapper adaptation

P1's response model requires more precise location identity than the P0 mapper's earlier Patch/operation-centric shape. The mapper now resolves each eligible ResponseUnit locator, supports existing-text anchors for no-change units, marks table/non-body locations for manual entry, creates unique phrase windows for deletions and reuses an unchanged document role only when the whole role's source/output evidence is consistent.

This is a meaningful change to a P0-adjacent module, despite retaining the underlying Word COM workflow. It is therefore recorded as a **material compatibility adaptation**, not as an unchanged P0 component.

The audit found two regressions in that adaptation:
- the PDF page-text corroboration and its optional PyMuPDF dependency had been removed;
- the _export_one_document call omitted the required output_pdf argument.

Both were fixed in 4a5a364. The independent PDF check only confirms that the target phrase is present on the page already reported by Word. It does not infer page or line coordinates. The follow-up role-reuse audit also ensures a cached page/line is not reused without that check.

### 2. Second-batch integration coverage

The second-batch test now covers immutable directory creation, batch plan export, staged preflight/approval/apply snapshots, Track Changes, verification, semantic confirmation, Workflow synchronization and publication. It simulates the previously published first batch by seeding the verified first output as the active parent; it does not combine the first batch's full creation/publication procedure and the second batch into one giant integration test. Those first-batch storage/publication cases remain separately covered.

### 3. Validation scope

Automated JSON Schema meta-schema validation for every repository schema and Draft 2020-12 validation of a generated Release Manifest instance are included in the synthetic suite and passed as part of the 261-test local run at `b144f4b`. Word COM pagination/export has since also passed on the separate synthetic acceptance fixture. Microsoft Word visual inspection of the final Clean DOCX/PDF and a complete multi-batch/real review round remain separate acceptance steps.

## Validation status and remaining acceptance gates

The local Windows report at `b144f4b` records: the dedicated Word/PDF acceptance tests PASS (3/3); the full quality check PASS (261 tests, zero failures/errors/skips); compileall, package checks and CLI smoke checks PASS; and overall PowerShell wrapper exit code 0. The subsequent read-only tester report records the Word COM/PDF synthetic runner PASS at the same code HEAD.

GitHub Actions Run #1148 for `e7f818f` remains marked failed, but all four matrix jobs expose zero steps in the Actions API. No executable test output was available from that run. The local report is therefore the current executed automated evidence; the Actions run is an infrastructure/status issue whose cause is not established by the available job records.

The local report did not identify the PowerShell edition/version. Therefore the result proves that the Windows PowerShell quality-check script ran successfully, but it does not independently establish that the host was Windows PowerShell 5.1.

The remaining acceptance gates, to be executed together after the development freeze, are:
1. Complete one continuous synthetic end-to-end review round through the new PowerShell orchestration: Reviewer comment inventory, Decision Plan and execution-scope preparation, at least two sequential immutable batches based on successive published active pointers, a dependent-task refresh/re-prepare cycle, resolution-only reply_only/no_change cases, final Working Clean, Word COM/PDF mapping, automatic and manual location evidence, ResponseUnit completion, Workflow synchronization and Release Package/Manifest.
2. Run the complete project quality/test suite once after the continuous workflow path is complete; do not interleave partial test rounds with the remaining implementation.
3. Visually inspect the final Clean DOCX/PDF in Microsoft Word and confirm final page/line presentation; automated COM/PDF corroboration does not establish visual or scientific correctness.
4. Run the complete workflow on user-approved real Reviewers, Manuscript and SI files. Keep PR #3 Draft/Open/Unmerged until these acceptance gates pass.

## Closure checklist

1. Run the continuous synthetic end-to-end workflow described above using isolated Temp files only; do not repeat the already-passing 261-test suite or Word COM fixture unless relevant code changes.
2. Record the generated Release Manifest, response outputs, active document pointers, batch publication receipts and all file hashes; preserve the complete run directory.
3. Obtain the user's visual Word/PDF acceptance, then exercise the same workflow with user-approved real review files. Keep PR #3 Draft and unmerged until acceptance passes.

## Verdict

The P1 implementation is substantially aligned with its declared mainline. The latest code audit also closes continuous batch-parent replay and explicit Patch supersession event lineage in the Release Manifest and RevisionLedger validator. The remaining deviation is the material but purposeful Word/PDF mapper adaptation. Automated validation has passed locally; acceptance remains conditional on the outstanding Word GUI/COM and real-workflow gates.

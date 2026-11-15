# P1 Mainline Deviation and Closure Report

**Audit date:** 2026-11-15  
**Repository:** Tuzhidingdong/Co_reviewer  
**Pull request:** [#3 — P1 core revision workflow](https://github.com/Tuzhidingdong/Co_reviewer/pull/3)  
**Base:** upgrade/v0.5.1-p0-implementation-20261114  
**Development branch:** upgrade/v0.5.1-p1-audit-quality-implementation-20261114

## Executive assessment

The P1 main workflow is substantially implemented: inventory/audit and author-approved task planning connect to Workflow Ledger and RevisionLedger; incremental Patch batches use immutable directories; the existing Word-native Track Changes adapter is retained; verification and DAG synchronization feed Working Clean projections and Response Letter generation; publication, rollback, failed-batch recovery and final release evidence have dedicated gates.

P1 is **not accepted or test-verified**. GitHub Actions has repeatedly failed before runner allocation, and no Python, PowerShell 5.1 or Word COM test process has completed in this implementation session. The local Windows/Word acceptance remains outstanding.

The clearest scope deviation is the size of the P1 adaptation to word_pdf_mapper.py. The module was extended from Patch-level mapping to ResponseUnit/locator-level mapping and document-role reuse. The underlying Word COM pagination path remains, but the module is not byte-for-byte unchanged from P0. An audit also found that the P0 PDF page-text corroboration had been removed and that the changed exporter call omitted its new PDF-path parameter. Both issues were corrected in commit 4a5a364; a subsequent audit added the same PDF corroboration to reused mappings.

## Audit against the PR's declared P1 scope

| PR requirement | Finding | Status |
|---|---|---|
| Connect Inventory/Audit, Decision Plan approval and Workflow Ledger | The audit/decision artifacts are validated and used to prepare the round's Workflow tasks; author authorization precedes executable Patch generation. The synthetic full-workflow test covers inventory through a Response Letter draft. | Implemented; synthetic coverage present |
| Connect RevisionLedger, exact Patch approval and native Track Changes | Typed Patch/ResponseUnit records are adapted to the existing revision-plan/Word writer path. Preflight, explicit approval, Apply and verification are separate gates. | Implemented |
| DAG synchronization and incremental batches | Verified tasks unlock/revalidate downstream work; historical verified Patches remain in the RevisionLedger while the exported plan contains only the current pending Patches. Reviewer attribution is retained between builds and explicit conflicting reassignment is rejected. | Implemented |
| Immutable batch storage, active pointer publication and rollback | Batch intent/scope, separate plan snapshots, retained outputs, atomic active-pointer publication and non-destructive rollback are implemented. | Implemented; separate synthetic tests present |
| Failed unpublished-batch recovery | Recovery intent/receipt, two-ledger staged writes, abandoned Patch history, task invalidation, retained-artifact inventory and release-manifest reconstruction are checked. The latest audit further binds Ledger paths, staged hashes, parent document roles and Workflow after-image invalidation events. | Implemented; regression tests present |
| Second batch through export, preflight, approval, native Track Changes, verification and DAG synchronization | The existing incremental test now also creates a real immutable second-batch directory, uses staged plan snapshots, synchronizes Workflow, publishes the batch and verifies the final active pointer. The first-batch active pointer is seeded as an already-published prior state; first-batch publication is covered separately. | Integration test strengthened; execution blocked |
| Working Clean projection and Response Letter Markdown/DOCX | Working Clean projections are separate from Marked DOCX. ResponseUnit-linked PatchOperation excerpts feed deterministic Markdown/DOCX rendering; quality checks and Release Gate bind the outputs to current evidence. | Implemented; synthetic tests present |
| Preserve P0 Word revision writer and Windows PowerShell 5.1 compatibility | The P0 Track Changes writer core is retained; its edit-result metadata pass-through was extended with operation/comment/location provenance. The PowerShell quality script was expanded, while retaining the Windows PowerShell 5.1 entry point and a non-ArgumentList fallback. | Core retained; runtime compatibility not yet verified |
| Preserve P0 Word/PDF mapping implementation | Word COM remains responsible for pagination and page/line coordinates, but P1 substantially extends the mapper for ResponseUnit locators, no-change anchors, manual non-body locations, deletion-aware phrases and whole-role reuse. P0's PDF page-text corroboration was temporarily removed, then restored. The exporter call's missing PDF-path argument was also fixed. Cached/reused mappings now receive the same PDF page-text corroboration. | Material P1 adaptation; compatibility repair committed; Word COM not run |
| Keep Usage Audit and DOCX Quality Audit as supporting controls | They are implemented as audit/quality support. The provider-neutral Usage Adapter is only an integration seam; actual Antigravity/OpenCode invocation hooks are outside this repository. | Correctly supporting scope; external hooks remain unintegrated |

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

### 3. Usage Adapter integration seam

The provider-neutral invoke_with_usage wrapper is a P2 supporting interface. It does not itself intercept external Antigravity or OpenCode calls. Those integration points and provider-specific usage extractors remain outside this repository; automatic production token capture is not claimed.

### 4. Validation scope

Automated JSON Schema meta-schema validation for every repository schema and Draft 2020-12 validation of a generated Release Manifest instance have now been added to the synthetic suite; they remain unverified until a runner executes the tests. Dedicated Draft 2020-12 schemas and instance checks also cover failed-batch Recovery Intent and Receipt, including the exact invalidated DAG task set. Synthetic tests are present in the repository but must not be described as passing until a runner actually executes them. Word COM pagination/export, PowerShell 5.1 execution and visual inspection of the final Word/PDF pair remain separate acceptance steps.

## Current verification blocker

The latest inspected Actions run at this report refresh is Run #1130, associated with commit daa0352. All four Ubuntu/Windows matrix jobs report runner_id=0, an empty runner name and zero steps. No Python compilation, unit test, PowerShell script or Word COM step ran. The run therefore provides no evidence of a code pass or code failure.

Earlier runs show the same pre-runner failure pattern. Identical retries are not a useful code-debugging loop and have not been repeated as a substitute for test results.

## Closure checklist

1. Obtain a functioning GitHub Actions runner or use the user's approved Windows/Python environment to execute the complete synthetic suite.
2. Execute the Windows PowerShell 5.1 quality-check script on the target environment and inspect its persistent log.
3. Perform the user's own Microsoft Word COM/GUI acceptance: open the generated Marked and final Clean DOCX/PDF outputs, verify native Track Changes and actual page/line mapping.
4. Execute the newly added full JSON Schema meta-schema checks and representative Release Manifest instance validation on a functioning runner; these are now part of the synthetic suite but have not yet executed.
5. Reconcile any failures, then update the PR's validation status. Keep PR #3 Draft until these gates produce actual results.

## Verdict

The P1 implementation is substantially aligned with its declared mainline. The remaining deviations are now explicit: a material but purposeful mapper adaptation, an intentionally unintegrated external Usage Adapter seam, and acceptance/validation that has not run. The code and test additions should not be treated as accepted until the runner and local Word/PowerShell gates complete.

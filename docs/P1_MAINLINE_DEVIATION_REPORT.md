# P1 Mainline Deviation and Closure Report

**Audit date:** 2026-11-16  
**Repository:** Tuzhidingdong/Co_reviewer  
**Pull request:** [#3 — P1 core revision workflow](https://github.com/Tuzhidingdong/Co_reviewer/pull/3)  
**Base:** upgrade/v0.5.1-p0-implementation-20261114  
**Development branch:** upgrade/v0.5.1-p1-audit-quality-implementation-20261114

## Executive assessment

The P1 main workflow is substantially implemented: inventory/audit and author-approved task planning connect to Workflow Ledger and RevisionLedger; incremental Patch batches use immutable directories; the existing Word-native Track Changes adapter is retained; verification and DAG synchronization feed Working Clean projections and Response Letter generation; publication, rollback, failed-batch recovery and final release evidence have dedicated gates.

P1's automated synthetic validation passed locally at code HEAD `e7f818f7915d097e09ce0a74975fe2344d68e7fa`: both targeted recovery tests and all 258 unit tests passed; compileall, dependency and CLI smoke checks passed (exit code 0). The user then ran the updated five-target compatibility fixture at `f2f2913`, saved/reopened it in Word and obtained a passing post-Word verification: 5/5 targets, zero failures and zero untracked-text violations. This is still not final product acceptance. The new Word COM/PDF mapping runner has now been added, but has not yet been independently tested or run; real review-round acceptance also remains outstanding.

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

Automated JSON Schema meta-schema validation for every repository schema and Draft 2020-12 validation of a generated Release Manifest instance are included in the synthetic suite and passed as part of the 258-test local run at `e7f818f`. Word COM pagination/export, Microsoft Word visual inspection and a real user review round remain separate acceptance steps.

## Validation status and remaining acceptance gates

The local Windows report at `e7f818f` records: both targeted recovery/release tests PASS; the full quality check PASS (258 tests, zero failures/errors/skips); compileall, dependency checks and CLI smoke checks PASS; and overall PowerShell wrapper exit code 0.

GitHub Actions Run #1148 for `e7f818f` remains marked failed, but all four matrix jobs expose zero steps in the Actions API. No executable test output was available from that run. The local report is therefore the current executed automated evidence; the Actions run is an infrastructure/status issue whose cause is not established by the available job records.

The local report did not identify the PowerShell edition/version. Therefore the result proves that the Windows PowerShell quality-check script ran successfully, but it does not independently establish that the host was Windows PowerShell 5.1.

The remaining acceptance gates are:
1. Independently test the newly added synthetic Word COM/PDF fixture builder, verification helper and PowerShell runner without executing Word COM as the test agent.
2. On the user's Word-enabled machine, run `scripts/run_word_pdf_mapping_acceptance.ps1` against the five-target synthetic fixture. Verify Word page count, exported PDF, exact PDF-page text corroboration, location-map hashes, final Clean DOCX quality and Response Letter Markdown/DOCX quality.
3. Complete the real workflow acceptance on supplied review files, including real Manuscript/SI incremental batches, manual table/non-body handling where applicable, final Response Letter and Release Package.

## Closure checklist

1. Run read-only regression tests for the newly added Word COM/PDF acceptance fixture and PowerShell runner; only the developer changes code.
2. Execute the synthetic Word COM/PDF mapping runner on the user's existing five-target fixture and inspect its page/line map, final Clean DOCX/PDF and Response Letter outputs.
3. Complete the user's real workflow acceptance on supplied review files. Keep PR #3 Draft and unmerged until Word/COM and real-workflow acceptance pass.

## Verdict

The P1 implementation is substantially aligned with its declared mainline. The latest code audit also closes continuous batch-parent replay and explicit Patch supersession event lineage in the Release Manifest and RevisionLedger validator. The remaining deviation is the material but purposeful Word/PDF mapper adaptation. Automated validation has passed locally; acceptance remains conditional on the outstanding Word GUI/COM and real-workflow gates.

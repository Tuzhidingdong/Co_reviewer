# Co_Reviewer v0.5.1 P1 Implementation Status

## Scope

This document records the P1 implementation added on top of the P0 development baseline. It does not certify the project as accepted or production-ready. PR #3 remains Draft and is not merged.

## P1-4 — Immutable batch storage and active-document publication

Implemented in `scripts/co_reviewer/batch_store.py` and `scripts/co_reviewer_cli.py`:

- A document-changing ready batch receives a unique `runs/batch-{id}/` directory before Patch execution.
- `batch_intent.json` binds the review round, selected task IDs, parent active document path/SHA-256, and a new output path under the batch's `documents/` directory.
- `batch_scope.json` binds the exact Patch IDs, task IDs, document input/output paths and intent hash.
- A document role is reserved by any unresolved batch based on that parent version. A second batch cannot branch from the same active role while an earlier batch is uncommitted.
- The original revision plan is preserved. Preflight, approval, apply and verification produce new plan snapshots. Batch plans/reports are written as new artifacts and refuse overwrite.
- Apply always targets new batch output DOCX files. A failed or partial batch is retained and is not reused.
- `publish-batch` checks the batch intent/scope, unchanged parent pointers, exact output paths, Patch verification hashes and synchronized Workflow task states. It changes all affected `active_documents` pointers in one atomic Workflow Ledger replacement.
- `rollback-batch` restores the parent active pointers, records intent/receipt metadata, marks affected tasks and dependents `recheck_required`, and retains all batch files. Publish/rollback retry paths preserve the original intent values and avoid overwriting receipts.
- Final ResponseUnit location completion is a review-round release gate, not a prerequisite for each intermediate batch publication. Later batches can change pagination for the whole document role; therefore final Word/PDF mapping is deferred until all document-changing batches are published.

Primary commands:

```powershell
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --reviewer-map reviewer_map.json --out revision_ledger.json
python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --batch-dir "runs/batch-{id}"
python scripts/co_reviewer_cli.py preflight-plan --plan "runs/batch-{id}/revision_plan.json" --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py approve-plan --plan "runs/batch-{id}/revision_plan.preflighted.json" --revision-ledger revision_ledger.json --ids P1
python scripts/co_reviewer_cli.py apply --plan "runs/batch-{id}/revision_plan.approved.json" --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py verify --plan "runs/batch-{id}/revision_plan.applied.json"
python scripts/co_reviewer_cli.py confirm-verified --plan "runs/batch-{id}/revision_plan.applied.json" --revision-ledger revision_ledger.json --ids P1 --semantic-review-confirmed
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-{id}"
```

Use the exact `[NEXT PLAN]` snapshot path reported by each command. Do not copy later-stage data back into an earlier snapshot.

### P1-4 failed unpublished-batch recovery

The dedicated `recover-failed-batch` command now closes the previous recovery boundary:

```powershell
python scripts/co_reviewer_cli.py recover-failed-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-{id}" --reason "Explain why this unpublished Apply batch is being abandoned."
```

- Recovery is allowed only when there is no publish/rollback intent or receipt and every active document pointer still matches the immutable batch parent path, SHA-256 and parent batch ID.
- The command validates the immutable intent/scope, exact RevisionLedger path and round, selected task/Patch ownership, operation input/output paths and source hashes. Verified Patches are never abandoned.
- Before changing either Ledger, it writes an immutable recovery intent containing the exact post-recovery Workflow Ledger snapshot, both before/after Ledger hashes, recovery events, active-parent hashes and a SHA-256/size inventory of every retained batch artifact, including partial DOCX outputs.
- It marks affected Workflow tasks and downstream dependents `recheck_required`, so previous ready authorization cannot be reused. The RevisionLedger marks each failed-batch Patch `abandoned` and appends one recovery event per Patch. Active document pointers remain unchanged.
- Workflow Ledger and RevisionLedger are replaced atomically as individual files, with a staged recovery intent and exact after-hashes. If interrupted between the two writes, rerunning the command resumes the same staged transaction; it does not replay Apply or generate new Patch IDs. The final `batch_recovered.json` receipt is written only after both Ledger hashes match the staged intent.
- The abandoned Patch remains in history. A replacement Patch may supersede it only when its input path and SHA-256 exactly match the active parent recorded in the recovery event, and its output path differs from every failed-batch output. RevisionDocument rebasing accepts the parent reset only when the same recovery event proves the previous output belonged to the failed batch. Normal applied/verified Patch supersession still requires the predecessor output.
- Recovered batches release their document-role reservation only after the recovery receipt, staged recovery intent, original batch intent/scope hashes, retained-artifact inventory and RevisionLedger Patch states agree. A present-but-tampered receipt does not release the lock.
- The retained-artifact inventory excludes only the two recovery control files at the batch root. Nested files with the same basenames remain hash-inventoried, and symbolic links anywhere in the batch tree fail closed instead of following targets outside the retained batch artifacts. Recovery Intent and Receipt both bind the exact invalidated DAG task set; the final Release Manifest exposes that set instead of requiring consumers to inspect the external intent file.
- The scheduler releases a document-role reservation for a published or rolled-back batch only after validating the paired transaction intent/receipt, immutable intent/scope hashes, Patch lineage, output/parent hashes and exact per-task Workflow events. Orphaned, partial or inconsistent publication/rollback markers fail closed.
- An `abandoned` Patch is audit history only. Old preflighted/approved legacy plans that still reference it are rejected by the Ledger-to-plan guard; a new Patch must supersede the abandoned record from its recorded active parent. All partial outputs and evidence remain available for audit. The author must reauthorize the invalidated Workflow task(s) and use a new batch directory.

This closes the implementation gap for an unpublished partial Apply. It does not make an incomplete DOCX valid, automatically retry the edit, or certify the replacement Patch; preflight, explicit approval, Track Changes application, verification and publication remain separate gates.

## P1-5 — Response Renderer and Final Release Gate

Implemented:

- `response_units_to_legacy_plan` uses exact `ResponseUnit.linked_operation_ids`, explicit Patch ownership, and `PatchOperation.source_comment_ids`. It rejects unknown/misattributed operations rather than inferring links.
- Revised text is taken from the exact `PatchOperation.replace`; delete-only text from `PatchOperation.find`; no-change evidence from the confirmed existing-anchor excerpt. The renderer does not call a model to regenerate manuscript body text.
- Markdown and DOCX use the shared deterministic `scripts/co_reviewer/response_renderer.py` adapter. DOCX output highlights the cited excerpts and labels document role, final locator and operation ID.
- `scripts/co_reviewer/release_manifest.py` checks the current on-disk RevisionLedger/Workflow Ledger hashes, active-document pointers, immutable batch intent/scope/publication receipts, task/comment coverage, verified Patch and operation attribution, completed ResponseUnits, final Word Clean DOCX/PDF hashes, Word COM page counts, Figure/Table SEQ records, quality reports and exact excerpts in both Response Letter formats. The manifest now reconstructs every published batch from Workflow events, including batches later rolled back, and verifies publish intent/receipt plus rollback intent/receipt hash binding and parent-pointer chains; it no longer omits earlier history merely because a later batch owns the active pointer. It separately captures unpublished failed-Apply recoveries from the per-Patch RevisionLedger recovery events and verifies the recovery intent, receipt, active-parent hashes and retained artifact inventory. It also requires the original selected-task sets to match the current Workflow Ledger and the recovery parent roles, paths and hashes to match the original immutable batch intent. Recovery receipts must bind both Ledger file paths to the original batch intent/scope, all staged before/after Ledger hashes must be valid SHA-256, and the staged Workflow after-image is hash-recomputed; it must preserve every failed-batch parent pointer and place each selected task in `recheck_required` with exactly one matching batch invalidation event. Every ResponseUnit must also link to a still-current Workflow task and a comment ID actually declared by that task; extra or orphan ResponseUnits cannot enter the final Response Letter. It also regenerates a canonical Response Letter DOCX from the current ResponseUnits/PatchOperations and compares the complete semantic body, so a manually edited or stale DOCX cannot pass merely by carrying a fresh quality report and retaining a few expected excerpts. The comparison also rejects extra tables, inline shapes, text boxes, unexpected sections or unrecognised header paragraphs that could carry untracked response content. For final manuscript/SI files, every matching location map must also carry a valid quality report bound to the same DOCX hash, PDF hash and Word COM page count, and every map's SEQ audit must agree. Active batch intent/scope/receipt records are also cross-checked against the RevisionLedger Patch IDs (including entries later superseded), current Workflow task IDs, selected task sets, document roles, parent hashes and output paths; a self-consistent but incomplete scope record cannot hide a Patch or refer to a removed Workflow task. Stored DOCX quality reports must also declare the matching `word-final-clean` or `response-letter` profile. Their embedded Word page count must independently pass the same positive non-boolean integer check as the location map.
- Every location-required ResponseUnit must have exactly one final locator per linked PatchOperation (or its single existing-text anchor); missing or duplicate automatic locators cannot be hidden by a non-empty partial location list. Manual table/non-body locators follow the same exact-coverage rule. The exact-coverage check also runs when a RevisionLedger is reloaded or validated, so editing a serialized ledger cannot bypass the recording-time guard.
- The Release Gate also reruns the repository's deterministic Figure/Table reference audit on all current Working Clean Manuscript/SI files. Unresolved/ambiguous references, duplicate captions, numbering gaps, out-of-order first mentions and cross-document same-label/different-caption collisions block release; non-blocking unreferenced captions and repeated-caption identity candidates remain visible review flags.
- A second deterministic PatchOperation audit inventories added/removed numeric values, recognised unit strings, Figure/Table/Equation/Section/Scheme references, bracketed citation/DOI markers and sample/component identifier candidates across exact `find/replace` operands. It requires every operation to have completed, explicitly attributed ResponseUnit semantic-review evidence before including those deltas in the release manifest. Numeric matching excludes recognised unit/reference/citation/identifier spans to avoid double counting.
- The Figure/Table/Equation token audit recognises both expanded labels (`Figure 2`, `Equation 1`) and common punctuated abbreviations (`Fig. 2`, `Eq. 1`, including `Supplementary Fig. S1`). The final Release Gate accepts only schema-defined Word quality statuses (`PASS`/`PASS_WITH_WARNINGS`) and SEQ statuses (`pass`/`manual_review`); unknown states fail closed. It also validates the complete SEQ audit shape and status/findings consistency, plus a positive non-boolean integer Word COM page count; a boolean or malformed report cannot satisfy the gate.
- These lexical deltas are evidence for the author's review, not a scientific correctness verdict. The tool does not infer unit conversions, whether two sample labels mean the same physical sample, or whether changed citation meaning is valid; those judgments remain explicit human scientific/semantic review attached to the Patch/ResponseUnit workflow.
- `schemas/final-release-manifest.schema.json` defines the hash-bound release package structure and requires a passing deterministic consistency-audit section.
- `create-release-package` creates a new `runs/release-{id}/` directory containing Markdown and DOCX Response Letters, its DOCX quality report, and `final_release_manifest.json`. The manifest is written only after the release gate passes. Failed release directories are retained and not reused.

Primary finalization commands, after all document-changing batches are published:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-P1-C1 --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-P1-C1 --ready-for-submission-confirmed
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
```

The map-response-locations command writes its generated map and location evidence back to the RevisionLedger. Do not call record-response-locations again for that same output. The standalone command is only for attaching a separately generated, still-unrecorded map that matches the exact current Ledger. Use record-manual-response-locations for author-confirmed table/non-body cases explicitly marked manual_required; never infer page/line values.

## Synthetic test coverage added

- `tests/test_batch_store.py`: unique batch storage, no-overwrite semantics, same-role reservation, active-pointer source hash integrity, verified-only publication, atomic pointer result and non-destructive rollback, and interrupted failed-batch recovery with receipt integrity. Retained-inventory regressions cover root-only control exclusions, nested same-named artifacts and symbolic-link rejection. Transaction regressions cover rejection of bare publication/rollback receipts and acceptance of complete hash-bound publication and rollback chains. Recovery DAG coverage now includes selected, conflicting and downstream dependent tasks; a regression removes a downstream invalidation event and recomputes the staged snapshot hash to confirm the transaction is rejected. Dedicated Recovery Intent and Receipt JSON Schemas are validated against generated instances in `tests/test_release_manifest.py`. Runtime recovery receipt checks now compare the entire staged per-Patch event set to the persisted RevisionLedger events before releasing a batch lock; tests reject missing and altered persisted events. The batch scheduler receives the current Ledger event list from `cmd_build_revision_ledger`; without it a recovered batch cannot release its document-role reservation. The idempotent recovery command and Release Gate use the same exact event comparison. Runtime receipt validation also requires the exact pre-recovery Patch status map and one well-formed staged RevisionLedger recovery event per Patch; the Release Gate compares those records to the persisted RevisionLedger.
- `tests/test_revision_models.py`: abandoned-plan replay rejection, exact final-location coverage for every linked PatchOperation or anchor, rejection of missing/duplicate locator records, and persisted-ledger validation of location coverage. `tests/test_revision_ledger_cli.py` also verifies that CLI batch-directory arguments reject final-directory symlinks and symlinked parent paths before canonicalization. All four CLI batch-directory entry points (export, recover, publish and rollback) apply this check before resolving the path.
- `tests/test_response_renderer.py`: exact PatchOperation excerpt extraction, same text in Markdown/DOCX, and numeric/unit/citation/Figure/identifier delta inventory, including punctuated `Fig.` references.
- `tests/test_release_manifest.py`: release blocking for incomplete ResponseUnits, reply-only completion without invented document locations, comment-to-ResponseUnit coverage, unresolved Figure/Table references, strict quality/SEQ status allowlists, SEQ audit structure consistency, positive non-boolean Word page counts, final-map consistency, DOCX/PDF/page-count/profile-bound quality reports, exact batch task/Patch/document scope, historical published/rolled-back batch-chain retention with rollback-intent hash tamper rejection, recovered-unpublished batch evidence with retained-inventory, forged Ledger-path rejection, Workflow after-image hash/pointer/invalidation-event tamper rejection, and stale-parent/task rejection, and tampered Response Letter DOCX rejection. Continuous published-batch parent-pointer replay now covers sequential publications and rollbacks; a regression rehashes a discontinuous later parent chain to verify that cross-batch history is still rejected. Patch supersession now requires matching predecessor/replacement events for every lineage edge, and the final Manifest exposes predecessor IDs plus those events. Recovered-batch downstream task IDs must also remain present in the current Workflow Ledger.
- `tests/test_schema_contracts.py` validates all repository JSON Schema definitions against their declared meta-schemas; `tests/test_release_manifest.py` validates a generated representative Release Manifest instance with Draft 2020-12 and format checking.

## Validation status and remaining boundary

**Current acceptance baseline:** code HEAD `b144f4b282f76efcced165815d713747138bc831`.

- Dedicated Word/PDF acceptance regression: **3/3 tests passed**.
- Windows quality-check run: **261/261 tests passed**, zero failures/errors/skips; compileall, package checks and core CLI smoke checks passed (exit code 0).
- The PowerShell parser check for run_word_pdf_mapping_acceptance.ps1 passed.
- The five-target Microsoft Word save/close/reopen round-trip passed post-Word verification: **5/5 targets, 0 failures, 0 untracked-text violations**.
- The real Microsoft Word COM/PDF synthetic runner completed on the exact code HEAD above: runner exit code 0 and `[WORD/PDF ACCEPTANCE] PASS`. Word and PDF page counts matched (1/1); the Word-reported PDF page contained the target phrase; final Clean DOCX/PDF and Response Letter quality reports were PASS with zero quality warnings.
- The fitz API deprecation warning was non-blocking and did not appear as a quality-report warning.
- The location map's revision_ledger_sha256 is expected to differ from the final on-disk RevisionLedger hash: the map binds the pre-recording Ledger snapshot, then the same command records the map file's own SHA-256 and location evidence into the Ledger, changing the Ledger bytes. This is the intended two-stage evidence chain, not by itself a hash failure.
- The one-document synthetic mapping fixture does not exercise a complete multi-batch review round, cross-document final release, manual table/non-body locator input or all Release Manifest gates together.
- **Still not accepted:** a complete end-to-end review round covering incremental batches through publication, final Word/PDF mapping and Release Package; visual inspection of the generated final Clean DOCX/PDF; and a real user-supplied Manuscript/Reviewers/SI round.

## Completed synthetic Word acceptance

The five-target compatibility fixture has passed the Microsoft Word save/close/reopen round-trip and its post-Word verifier (5/5 targets, zero failures, zero untracked-text violations). The separate Word COM/PDF synthetic runner has also passed on code HEAD b144f4b282f76efcced165815d713747138bc831. Its outputs are recorded in the tester report; there is no need to repeat either synthetic acceptance run unless the relevant code changes.

This is still a synthetic one-ResponseUnit mapping run. It does not constitute full multi-batch workflow acceptance, visual acceptance of the final Clean DOCX/PDF, or acceptance of a real review round.

## Next acceptance boundary

Run one complete synthetic review round through the actual CLI/PowerShell entry points, including Reviewer comment inventory, Decision Plan approval, immutable batch creation, preflight/approval, native Track Changes Apply, verification, Workflow synchronization, batch publication, final Working Clean, Word COM/PDF mapping, ResponseUnit completion and final Release Package/Manifest. Keep all generated test files isolated under a new Temp directory. After that, the user must visually inspect the final Word/PDF output and the workflow must be exercised with user-approved real review files.

## Mainline audit and deviation report

- See [docs/P1_MAINLINE_DEVIATION_REPORT.md](P1_MAINLINE_DEVIATION_REPORT.md) for the cross-scope audit against the original P1 PR contract, including the Word/PDF mapper adaptation, current test boundaries, Actions runner blocker and remaining acceptance conditions.
- The second-incremental-batch integration test seeds a previously published first-batch document pointer, creates a new immutable batch for the dependent task, and exercises plan export, preflight, approval, Track Changes Apply, verification, Workflow synchronization and publication. This test passed as part of the 258-test local suite at `e7f818f`; that integration coverage does not exercise Microsoft Word COM.
- The P0 Word/PDF mapper's independent PDF page-text corroboration has been restored. It only checks that the Word-reported PDF page contains the target phrase; Word COM remains the sole source of page/line coordinates. Reused role mappings now undergo the same page-level corroboration before reuse.


## Immediate local acceptance path

From the repository root, in Windows PowerShell 5.1, execute the existing quality-check entry point with the intended Python 3.10+ interpreter:

```powershell
.\scripts\run_quality_checks.ps1 -PythonExe "D:\Anaconda\python.exe" -InstallDependencies
```

This installs the test extra, compiles `scripts` and `tests`, runs the complete `unittest` suite, and checks the core CLI entry points. It writes a persistent log and prints its path. Do not treat a partial test run as PASS.

After the automated suite passes, perform the separate Microsoft Word GUI/COM acceptance on the existing Windows installation: open a generated Marked DOCX and confirm native Track Changes; run `map-response-locations` on a completed synthetic/approved test ledger; confirm Word-generated Clean DOCX, PDF, page/line map and quality report agree. Word/COM execution is not performed by the automated quality-check script.

P1 remains Draft until the automated suite and the user's Word/PowerShell acceptance evidence are available. When a check fails, preserve the complete output and log; diagnose the first failure rather than repeating the whole workflow without a specific fix.

## Branch / PR

- Base: `upgrade/v0.5.1-p0-implementation-20261114`
- Development branch: `upgrade/v0.5.1-p1-audit-quality-implementation-20261114`
- Latest development commit: follow the current head of PR #3; this status file intentionally does not pin a commit SHA that becomes stale as implementation continues.
- PR #3: Draft, open, not merged.

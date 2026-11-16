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
- `create-release-package` writes Markdown and DOCX Response Letters, the DOCX quality report and `final_release_manifest.json` under an immutable intent-bound output directory. It resumes a partial package only when round, Workflow/RevisionLedger paths and hashes, title and author still match; inconsistent generated artifacts are retained with `.partial-<id>` suffixes. The completion receipt is written last and hashes all four delivered artifacts. Changed source snapshots or title/author require a new output directory.

Primary finalization commands, after all document-changing batches are published:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-P1-C1 --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-P1-C1 --ready-for-submission-confirmed
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
```

The map-response-locations command writes its generated map and location evidence back to the RevisionLedger. Do not call record-response-locations again for that same output. The standalone command is only for attaching a separately generated, still-unrecorded map that matches the exact current Ledger. Use record-manual-response-locations for author-confirmed table/non-body cases explicitly marked manual_required; never infer page/line values.

## P1-6 — User-facing round orchestration and resolution-only continuity

Added two Windows PowerShell entry points that connect the existing round lifecycle without replacing its underlying CLI state machine:

- `scripts/Invoke-CoReviewerBatch.ps1`: prepares a new Workflow Ledger/Reviewer Map from an execution scope when supplied; authorizes selected ready tasks; builds the current immutable batch; resolves the actual batch directory; exports and preflights the plan; displays the exact PatchOperations; requires explicit Patch approval; applies Word-native Track Changes; verifies exact edits and the untracked-text audit; displays the Marked DOCX paths/hashes; requires semantic review; synchronizes Workflow state; requires publication confirmation; and validates the publication receipt against the new active-document pointers and hashes.
- `scripts/Complete-CoReviewerRound.ps1`: requires all active Patches to be verified and published; creates or reuses only a complete hash-current Working Clean projection set; requests final semantic review; performs Word COM/PDF mapping; pauses for explicit table/non-body locators; records author-confirmed manual locators; completes ResponseUnits; synchronizes Workflow task completion; and creates or resumes a matching intent-bound Release Package. It refuses to overwrite partial/stale Working Clean or Word/PDF mapping artifacts; partial Release Package artifacts are resumed only under the same source/metadata intent.
- `schemas/manual-response-locations.schema.json` and `examples/manual_response_locations.example.json`: define the author-supplied locator structure for manual table/non-body location evidence. Example page/line values are illustrative only.

Resolution-only continuity is also connected to the RevisionLedger. The build-revision-ledger CLI now supplies current active-document paths/hashes for no-change anchors. A no-change-only task can therefore create ResponseUnits without a fictitious document-changing batch. RevisionDocument permits input=output only as a strictly hash-checked unchanged-source passthrough for roles with no PatchOperations. When later batches merge another no-change anchor against the same active role, the merge preserves the existing document lineage and validates either its published output hash or, before a Working Clean projection exists, its unchanged-source hash. Roles with PatchOperations cannot use this passthrough exception.

Pure reply_only rounds can proceed without invented document versions or final page/line locations. The round finalizer synchronizes Workflow completion after ResponseUnits have been semantically confirmed and completed, so response-only tasks do not remain ready at the Release Manifest gate.

**Development boundary:** these P1-6 files and core-model/CLI changes are development work added after the historical `b144f4b` acceptance baseline. They have deliberately **not** been tested during implementation. Do not treat PowerShell syntax, command compatibility, no-change merge continuity or full release integration as accepted until the deferred unified acceptance stage.

## P1-8 — Crash-recoverable batch, Workflow sync and Release transactions

This increment connects the operational failure windows across the batch runner, refresh entry point, CLI and finalizer. It is implementation-only at this point; no tests or PowerShell commands were run against this code increment.

- `Reconcile-CoReviewerRound.ps1` is the common recovery/index-rebuild entry point used before a new batch scope, refresh and finalization. It reads each batch's own immutable Workflow/RevisionLedger binding, validates persisted transaction intent/receipt pairs, verifies corresponding append-only Workflow events and output hashes, and reconstructs `round_manifest.json` history and current active pointers. The manifest is not treated as a transaction commit record.
- Publication and rollback retries validate immutable intent/scope hashes, receipt identity, Patch lineage, per-task Workflow events and retained output/parent hashes. Historical published batches remain valid when a later batch has changed the current active pointer.
- A mechanically verified batch whose publication was interrupted is recognized as `verified_unpublished`. The batch runner asks for explicit resume confirmation, reuses the existing immutable applied/verified plan and DOCX outputs, idempotently completes missing semantic confirmation/Workflow sync, and separately asks for publication approval. No Apply rerun occurs. Unverified/partially applied batches require explicit failed-batch recovery and remain retained for evidence refresh.
- Workflow/RevisionLedger synchronization stages both after-images and a hash-bound intent before writing either ledger. A pending marker enables forward-only recovery between the two atomic file replacements. A unique complete intent left before marker creation can be promoted only when both ledgers still match the recorded before-hashes. Out-of-band hashes stop recovery without overwrite. Sync intent/receipt records and schemas are retained in a round-local transaction directory.
- Failed-batch recovery verifies the Workflow invalidation event history when a receipt already exists, so later legitimate task progress does not erase the recovery evidence and missing/altered invalidation events cannot be treated as success.
- Release Package generation has immutable `release_package_intent.json` and a final `release_package_receipt.json`. The intent binds round, title/author, Ledger paths and exact source hashes. Missing/invalid generated artifacts are rebuilt only within the same matching intent; mismatched generated artifacts are preserved with `.partial-<id>` names. Unknown directory contents are not adopted. The receipt is written only after a READY Release Manifest and passing deterministic consistency audit; it hashes all four delivered files. A completed package is revalidated on retry, and a changed source snapshot or metadata requires a new output directory.
- New JSON Schemas describe Release Package intent/receipt and Workflow sync intent/pending/receipt. Regression cases cover two-Ledger crash recovery, the pre-marker intent crash window, stale-source rejection, publication retry idempotency and partial Release Package resumption/source-change rejection.
- Reconciliation also retains and verifies historical Workflow sync transaction directories for previous prepared Workflow Ledger versions. Missing historical batch directories, missing sync intents/receipts, altered transaction hashes or incomplete syncs now fail closed rather than silently shrinking Manifest history.
- Starter and refreshed audit/inventory artifacts are SHA-256-bound in their respective manifests. Decision Plan validation accepts either the starter `round_manifest.json`, the exact latest-refresh artifacts recorded in that root manifest, or a dedicated `refresh_manifest.json` paired with the root manifest; same-path content changes are rejected. After writing a new Workflow Ledger it registers its current path/hash in the root manifest. Older workspaces without artifact hashes remain path-validated with an explicit legacy warning.
- The lower-level `prepare-workflow-execution` CLI now accepts `--round-manifest` and verifies the current Workflow Ledger path/hash plus the exact starter or latest-refresh Inventory path/SHA-256 before generating outputs. The PowerShell batch runner passes the root manifest automatically; direct CLI examples use the same binding. Preparation therefore cannot silently consume a replaced inventory file after Decision Plan validation.
- Successful preparation writes the new prepared Workflow Ledger, Reviewer Map and report paths/hashes back to the root `round_manifest.json`. The CLI supports a separate `--round-root-manifest` when the evidence source is a dedicated `refresh_manifest.json`; the main PowerShell route passes the root manifest for both validation and registration. Historical Workflow sync path references are normalized relative to the round root during reconciliation.
- Refresh and finalization now stop after reconciliation when a non-terminal unpublished batch remains; finalization cannot bypass a verified-but-unpublished batch merely because its Patch records already say verified.

The earlier local validation baseline at `b144f4b` remains historical only. It does not cover this increment. Unified validation must occur once after development is frozen; no result in this section should be read as a test pass.

## Synthetic test coverage added
- The workflow execution-scope contract is now represented by `schemas/workflow-execution-scope.schema.json`; the new `prepare-workflow-execution` CLI creates a separate pending Workflow Ledger, reviewer map and preparation report from an exact author-reviewed scope. `tests/test_workflow_preparation.py` has been added for source-hash binding, same-target evidence matching, author-approval preservation and input immutability. These newly authored tests are **not yet run**; they are part of the deferred unified acceptance stage.
- `build-revision-ledger` now validates candidate Patch/ResponseUnit construction and existing-ledger merge before creating a batch intent. The regression test for this lifecycle ordering remains part of the unified test stage.
- `scripts/Start-CoReviewerRound.ps1` creates an isolated round workspace, copies and hash-compares the original inputs, runs Audit into the round's reports folder and writes a round manifest. The normal workflow instructions in `SKILL.md`, `README.md` and `docs/WORKFLOW.md` now use the same execution-scope preparation path instead of asking the Supervisor to hand-edit Workflow Ledger JSON.
- `scripts/Refresh-CoReviewerRound.ps1` reads the current `active_documents` pointers from the latest prepared Workflow Ledger, verifies their SHA-256 values, and writes a new Audit/Inventory/Decision Queue under `reports/after-batch-<id>/`. `prepare-workflow-execution` now accepts refreshed `evidence`/`targets` for `recheck_required` tasks, verifies the current inventory's exact part/paragraph/text, preserves prior evidence and source versions, updates target groups, and leaves approval pending. The refresh example is `examples/workflow_execution_scope_refresh.example.json`; refresh-specific regression cases are authored but not yet run.
- Active-version Audit now has an explicit `--active-workflow-ledger` mode. It requires every supplied Reviewers/Manuscript/SI path and SHA-256 to match the current active pointers before allowing only native text insertions/deletions in published Marked documents. Formatting/move revisions and all comment parts/anchors remain blocking; the ordinary initial Audit still uses the strict clean-baseline rule. The PowerShell refresh script performs an earlier hash check as well, and regression tests for both the policy and pointer binding are authored but not yet run.

- `tests/test_batch_store.py`: unique batch storage, no-overwrite semantics, same-role reservation, active-pointer source hash integrity, verified-only publication, atomic pointer result and non-destructive rollback, and interrupted failed-batch recovery with receipt integrity. Retained-inventory regressions cover root-only control exclusions, nested same-named artifacts and symbolic-link rejection. Transaction regressions cover rejection of bare publication/rollback receipts and acceptance of complete hash-bound publication and rollback chains. Recovery DAG coverage now includes selected, conflicting and downstream dependent tasks; a regression removes a downstream invalidation event and recomputes the staged snapshot hash to confirm the transaction is rejected. Dedicated Recovery Intent and Receipt JSON Schemas are validated against generated instances in `tests/test_release_manifest.py`. Runtime recovery receipt checks now compare the entire staged per-Patch event set to the persisted RevisionLedger events before releasing a batch lock; tests reject missing and altered persisted events. The batch scheduler receives the current Ledger event list from `cmd_build_revision_ledger`; without it a recovered batch cannot release its document-role reservation. The idempotent recovery command and Release Gate use the same exact event comparison. Runtime receipt validation also requires the exact pre-recovery Patch status map and one well-formed staged RevisionLedger recovery event per Patch; the Release Gate compares those records to the persisted RevisionLedger.
- `tests/test_revision_models.py`: abandoned-plan replay rejection, exact final-location coverage for every linked PatchOperation or anchor, rejection of missing/duplicate locator records, and persisted-ledger validation of location coverage. `tests/test_revision_ledger_cli.py` also verifies that CLI batch-directory arguments reject final-directory symlinks and symlinked parent paths before canonicalization. All four CLI batch-directory entry points (export, recover, publish and rollback) apply this check before resolving the path.
- `tests/test_response_renderer.py`: exact PatchOperation excerpt extraction, same text in Markdown/DOCX, and numeric/unit/citation/Figure/identifier delta inventory, including punctuated `Fig.` references.
- `tests/test_revision_models.py`: unchanged-source no-change anchors, rejection when the same document role has PatchOperations, rejection of a changed source hash, and repeated no-change anchor merge against the same active version. `tests/test_word_pdf_mapper.py` also rejects manual locator input for a ResponseUnit that is not awaiting `manual_required` evidence. These new regressions are authored but not run.
- `tests/test_release_manifest.py`: release blocking for incomplete ResponseUnits, reply-only completion without invented document locations, comment-to-ResponseUnit coverage, unresolved Figure/Table references, strict quality/SEQ status allowlists, SEQ audit structure consistency, positive non-boolean Word page counts, final-map consistency, DOCX/PDF/page-count/profile-bound quality reports, exact batch task/Patch/document scope, historical published/rolled-back batch-chain retention with rollback-intent hash tamper rejection, recovered-unpublished batch evidence with retained-inventory, forged Ledger-path rejection, Workflow after-image hash/pointer/invalidation-event tamper rejection, and stale-parent/task rejection, and tampered Response Letter DOCX rejection. Continuous published-batch parent-pointer replay now covers sequential publications and rollbacks; a regression rehashes a discontinuous later parent chain to verify that cross-batch history is still rejected. Patch supersession now requires matching predecessor/replacement events for every lineage edge, and the final Manifest exposes predecessor IDs plus those events. Recovered-batch downstream task IDs must also remain present in the current Workflow Ledger.
- `tests/test_schema_contracts.py` validates all repository JSON Schema definitions against their declared meta-schemas; `tests/test_release_manifest.py` validates a generated representative Release Manifest instance with Draft 2020-12 and format checking.

## Historical acceptance evidence and current implementation status

**Important:** the test/acceptance results below are historical evidence for code HEAD `b144f4b282f76efcced165815d713747138bc831` only. They do not validate later commits, including the intent-only prepared-batch retirement fix or the current workflow-entry implementation. Per the current development direction, no further tests are being run mid-implementation. The complete test and acceptance suites will be run together after the end-to-end product workflow is implemented.

### Historical baseline on b144f4b


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

## Development freeze and unified acceptance boundary

The P1 mainline implementation is connected through the user-facing round lifecycle at development head `0a22a02863faf2b896bb08a7c633f7201e07adf4`: isolated round creation, Audit/Inventory, author-approved Decision Plan, execution-scope preparation, sequential immutable batches, active-version refresh, resolution-only `reply_only`/`no_change` handling, Working Clean, Word COM/PDF mapping, manual locator input, ResponseUnit completion, Workflow synchronization and Release Package/Manifest generation.

A source-level integration review after the orchestration freeze identified two lifecycle gaps and added fixes without running tests: (1) `validate_decision_plan.py --round-manifest` now derives the exact `review_round_id` created by `Start-CoReviewerRound.ps1` and requires the manifest to contain both source artifact paths and checks that the supplied audit/inventory paths match them, preventing an otherwise guaranteed round-ID mismatch; (2) `Complete-CoReviewerRound.ps1` persists the Word/PDF output directory before mapping and automatically reuses it on resume, so users do not need to manually re-enter the path after a manual-location pause. The corresponding regression tests and workflow documentation were added/updated. These changes are untested and remain within the single deferred unified acceptance cycle.

**Development phase is now frozen for unified acceptance.** No tests, PowerShell parser checks, quality checks, or end-to-end execution have been run against the post-`b144f4b` implementation. Do not start isolated test/fix cycles or ask the Agent to test intermediate commits. The next activity is one coordinated acceptance cycle after this freeze; any genuine blocker found there should be returned as one evidence-complete report for a focused development correction.

The unified acceptance cycle must cover:
- A fresh isolated round with copied/hash-tracked inputs and Audit outputs inside the workspace.
- Decision Plan validation, structured execution-scope preparation, and the separate prepared Workflow Ledger/Reviewer Map.
- At least two sequential immutable batches based on successively published active pointers, including a dependent-task refresh/re-prepare/re-authorize cycle.
- Resolution-only `reply_only` and `no_change` cases, including current-active-document anchor binding and no fake document batch for `reply_only`.
- Final Working Clean, Word COM/PDF mapping for every location-required document role, automatic and manual locator evidence, ResponseUnit completion, Workflow synchronization, Response Letter and Release Manifest.
- One complete quality/test suite after the continuous user workflow succeeds, followed by the user's separate Word GUI/PDF visual acceptance and a user-approved real review round.

Historical test evidence at `b144f4b` remains historical only. Until the unified cycle and the user's visual/real-workflow gates pass, do not report P1 as accepted or production-ready.

## Mainline audit and deviation report

- See [docs/P1_MAINLINE_DEVIATION_REPORT.md](P1_MAINLINE_DEVIATION_REPORT.md) for the cross-scope audit against the original P1 PR contract, including the Word/PDF mapper adaptation, current test boundaries, Actions runner blocker and remaining acceptance conditions.
- The second-incremental-batch integration test seeds a previously published first-batch document pointer, creates a new immutable batch for the dependent task, and exercises plan export, preflight, approval, Track Changes Apply, verification, Workflow synchronization and publication. This test passed as part of the 258-test local suite at `e7f818f`; that integration coverage does not exercise Microsoft Word COM.
- The P0 Word/PDF mapper's independent PDF page-text corroboration has been restored. It only checks that the Word-reported PDF page contains the target phrase; Word COM remains the sole source of page/line coordinates. Reused role mappings now undergo the same page-level corroboration before reuse.


## Unified acceptance path (deferred until implementation is complete)

Do not use this section during the current implementation phase. Once the end-to-end workflow and its user-facing entry points are complete, perform one unified run from the repository root in Windows PowerShell 5.1:

```powershell
.\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe"
```

Then run one continuous synthetic review round through the documented CLI/PowerShell path, including two published batches, final Working Clean, Word COM/PDF mapping for every changed document role, ResponseUnit completion, Response Letter and Release Manifest validation. Keep all generated artifacts under a fresh temporary round workspace. Do not rerun isolated subsets after every small code edit; diagnose and fix the first failure from the unified run.

After the unified synthetic run passes, the user performs the separate Microsoft Word GUI/PDF visual acceptance and a real user-approved review round. PR #3 remains Draft/Open/Unmerged until those acceptance gates are documented.

## Branch / PR

- Base: `upgrade/v0.5.1-p0-implementation-20261114`
- Development branch: `upgrade/v0.5.1-p1-audit-quality-implementation-20261114`
- Latest development commit: follow the current head of PR #3; this status file intentionally does not pin a commit SHA that becomes stale as implementation continues.
- PR #3: Draft, open, not merged.


## P1 development-freeze source review — batch creation and Ledger identity

A final source-only integration review identified two remaining transaction hand-offs and closed them in the P1 development line:

- New immutable document-changing batches bind their creation intent to the exact pre-commit and expected post-commit RevisionLedger SHA-256 values. The creation sequence writes the immutable intent and scope, commits the single RevisionLedger replacement, verifies its post-image, then writes a hash-bound `batch_creation_receipt.json` marker.
- The round reconciler seals a missing creation receipt as committed only when the exact post-image and Patch/path/task contracts are present. It records an `abandoned_before_ledger_commit` receipt for either an intent-only interruption (before scope creation) or a scoped pre-commit interruption only when the exact pre-image remains, no scoped Patch IDs were committed, active parent pointers are unchanged, and no execution artifacts exist. Ambiguous or conflicting states fail closed. A safely abandoned intent no longer reserves the document role, so the same still-authorized task set can be rebuilt in a new immutable batch.
- Plan export, publication, rollback and failed-batch recovery require a committed creation receipt for protocol-v1 batches; the scheduler blocks missing receipts until round reconciliation runs. Final Release Manifest independently revalidates and records the creation receipt for every published/recovered protocol-v1 batch, with the strict release-manifest schema requiring the corresponding nullable receipt record and an explicit validation check.
- `round_manifest.current_revision_ledger` is now the authoritative RevisionLedger path for Reconcile, Refresh and the batch entry point. Reconcile rejects an explicitly supplied older RevisionLedger path, matching the current-Workflow-path guard.
- The batch-intent schema conditionally requires the protocol-v1 RevisionLedger path and before/after hashes. A dedicated batch-creation-receipt schema defines committed and safely abandoned states, with schema contract coverage for both new receipts and legacy intent compatibility.

The regression coverage is authored but **not run**. It remains part of the single deferred unified acceptance cycle; these source changes are not a test pass.


## Final source-audit hardening after the P1 development freeze

A final read-only review of the batch reservation path found that `assert_no_uncommitted_role_batches` ignored a non-empty `batch-*` directory when its immutable `batch_intent.json` was missing and no known transaction receipt existed. The round reconciler already blocked this state, but callers using the lower-level batch builder could bypass that protection. The role-lock validator now rejects every such orphaned payload. It permits only the exact empty `documents/` shell that `create_batch_intent` may leave if interrupted between directory creation and writing the intent. A regression case covers both states. This source change is untested and remains part of the single unified acceptance cycle.


## Unified GitHub Actions findings and focused corrections

The first real unified CI run after the runner-account issue was resolved used commit `c2e987e2ae6434e0d0cf0e3035bb6c0f2724941d` (Run [43846338542](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/43846338542)). Runner allocation, dependency installation, Python compilation on both versions and the Windows PowerShell parser stage succeeded. The run then exposed the following genuine issues:

- Workflow/RevisionLedger synchronization called `_json_payload_sha256` without importing the existing canonical helper from `batch_store`; this broke both the full-workflow integration path and the incremental-batch synchronization path.
- Workflow execution preparation attempted to import `write_json_atomic` from `batch_store`, although the function is defined in `co_reviewer_cli.py` itself.
- PDF page-text verification tests import PyMuPDF, but the `test` extra did not install that test dependency.
- Two regression tests had expectations inconsistent with eager model validation / the current fail-closed active-pointer error: the no-change/Patch role conflict is rejected during `RevisionLedger` construction, and a changed active file is rejected by the active-pointer hash guard before the helper emits its former message.
- The Windows quality-check step passed a PowerShell command result object to a string parameter. It now resolves the exact active interpreter using `python -c "import sys; print(sys.executable)"` and passes an explicit string path.

The focused corrections import the canonical sync hash helper, use the existing local atomic JSON writer, install PyMuPDF through the test extra, align the two regression expectations with the actual guards, and make the Windows PowerShell 5.1 workflow invocation explicit. These corrections are not yet accepted: the follow-up unified run must confirm them. No production workflow or approval gates have been relaxed.

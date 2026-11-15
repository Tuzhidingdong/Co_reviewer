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
- An `abandoned` Patch is audit history only. Old preflighted/approved legacy plans that still reference it are rejected by the Ledger-to-plan guard; a new Patch must supersede the abandoned record from its recorded active parent. All partial outputs and evidence remain available for audit. The author must reauthorize the invalidated Workflow task(s) and use a new batch directory.

This closes the implementation gap for an unpublished partial Apply. It does not make an incomplete DOCX valid, automatically retry the edit, or certify the replacement Patch; preflight, explicit approval, Track Changes application, verification and publication remain separate gates.

## P1-5 — Response Renderer and Final Release Gate

Implemented:

- `response_units_to_legacy_plan` uses exact `ResponseUnit.linked_operation_ids`, explicit Patch ownership, and `PatchOperation.source_comment_ids`. It rejects unknown/misattributed operations rather than inferring links.
- Revised text is taken from the exact `PatchOperation.replace`; delete-only text from `PatchOperation.find`; no-change evidence from the confirmed existing-anchor excerpt. The renderer does not call a model to regenerate manuscript body text.
- Markdown and DOCX use the shared deterministic `scripts/co_reviewer/response_renderer.py` adapter. DOCX output highlights the cited excerpts and labels document role, final locator and operation ID.
- `scripts/co_reviewer/release_manifest.py` checks the current on-disk RevisionLedger/Workflow Ledger hashes, active-document pointers, immutable batch intent/scope/publication receipts, task/comment coverage, verified Patch and operation attribution, completed ResponseUnits, final Word Clean DOCX/PDF hashes, Word COM page counts, Figure/Table SEQ records, quality reports and exact excerpts in both Response Letter formats.
- Every location-required ResponseUnit must have exactly one final locator per linked PatchOperation (or its single existing-text anchor); missing or duplicate automatic locators cannot be hidden by a non-empty partial location list. Manual table/non-body locators follow the same exact-coverage rule. The exact-coverage check also runs when a RevisionLedger is reloaded or validated, so editing a serialized ledger cannot bypass the recording-time guard.
- The Release Gate also reruns the repository's deterministic Figure/Table reference audit on all current Working Clean Manuscript/SI files. Unresolved/ambiguous references, duplicate captions, numbering gaps, out-of-order first mentions and cross-document same-label/different-caption collisions block release; non-blocking unreferenced captions and repeated-caption identity candidates remain visible review flags.
- A second deterministic PatchOperation audit inventories added/removed numeric values, recognised unit strings, Figure/Table/Equation/Section/Scheme references, bracketed citation/DOI markers and sample/component identifier candidates across exact `find/replace` operands. It requires every operation to have completed, explicitly attributed ResponseUnit semantic-review evidence before including those deltas in the release manifest. Numeric matching excludes recognised unit/reference/citation/identifier spans to avoid double counting.
- The Figure/Table/Equation token audit recognises both expanded labels (`Figure 2`, `Equation 1`) and common punctuated abbreviations (`Fig. 2`, `Eq. 1`, including `Supplementary Fig. S1`). The final Release Gate accepts only schema-defined Word quality statuses (`PASS`/`PASS_WITH_WARNINGS`) and SEQ statuses (`pass`/`manual_review`); unknown states fail closed.
- These lexical deltas are evidence for the author's review, not a scientific correctness verdict. The tool does not infer unit conversions, whether two sample labels mean the same physical sample, or whether changed citation meaning is valid; those judgments remain explicit human scientific/semantic review attached to the Patch/ResponseUnit workflow.
- `schemas/final-release-manifest.schema.json` defines the hash-bound release package structure and requires a passing deterministic consistency-audit section.
- `create-release-package` creates a new `runs/release-{id}/` directory containing Markdown and DOCX Response Letters, its DOCX quality report, and `final_release_manifest.json`. The manifest is written only after the release gate passes. Failed release directories are retained and not reused.

Primary finalization commands, after all batches are published:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-P1-C1 --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final
python scripts/co_reviewer_cli.py record-response-locations --ledger revision_ledger.json --location-map word_pdf_final/response_location_map.json
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-P1-C1 --ready-for-submission-confirmed
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
```

Use the manual-location command for table/non-body cases explicitly marked `manual_required`; do not infer page/line values.

## Synthetic test coverage added

- `tests/test_batch_store.py`: unique batch storage, no-overwrite semantics, same-role reservation, active-pointer source hash integrity, verified-only publication, atomic pointer result and non-destructive rollback.
- `tests/test_response_renderer.py`: exact PatchOperation excerpt extraction, same text in Markdown/DOCX, and numeric/unit/citation/Figure/identifier delta inventory.
- `tests/test_release_manifest.py`: release blocking for incomplete ResponseUnits, reply-only completion without invented document locations, comment-to-ResponseUnit coverage and unresolved Figure/Table references.

These tests are present in the repository but have **not been established as passing** in this implementation session.

## Validation status and remaining boundary

- The four relevant JSON Schema files (`workflow-ledger`, `final-release-manifest`, `revision-ledger`, `response-location-map`) were parsed as JSON successfully. This is a JSON syntax check only, not a full JSON Schema validation.
- The existing Windows matrix runs the PowerShell 5.1 quality-check script and remains the Windows-specific gate. A separate Ubuntu Python-unit matrix (Python 3.10 and 3.13) has been added to isolate compile/unit-test execution from Windows runner allocation; this does not replace the Windows job or Word GUI/COM acceptance.
- GitHub Actions Run #997 for commit `18a8caf` created all four Ubuntu/Windows Python matrix jobs, but all four ended before runner assignment (`runner_id=0`, empty `runner_name`, zero steps). Runs for commits `77488b8` and `a7f1831` showed the same pre-runner failure; retrying the failed Windows jobs did not change it. No Python test process started, so these results cannot be attributed to code failures or treated as test passes. The issue is currently an Actions execution-environment blocker affecting both OS families; the exact account/repository-side cause is not exposed by the available job records.
- No real manuscript, Worker, model call, local Word COM run or final Windows acceptance was performed.
- The final acceptance boundary remains: run the complete synthetic suite on the target Windows/Python environment, then perform the user's own Word GUI/COM acceptance. Do not describe static inspection or schema parsing as final acceptance.
- P2 Usage Adapter is not included here. It still depends on locating/attaching the actual Supervisor/Worker model invocation wrapper; no complete automatic token capture claim is made.

## Branch / PR

- Base: `upgrade/v0.5.1-p0-implementation-20261114`
- Development branch: `upgrade/v0.5.1-p1-audit-quality-implementation-20261114`
- Latest development commit: `18a8caf293ef696f16a73c2a817fca456c1f42fd`.
- PR #3: Draft, open, not merged.

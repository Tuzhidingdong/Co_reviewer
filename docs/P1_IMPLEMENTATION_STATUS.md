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

## P1-5 — Response Renderer and Final Release Gate

Implemented:

- `response_units_to_legacy_plan` uses exact `ResponseUnit.linked_operation_ids`, explicit Patch ownership, and `PatchOperation.source_comment_ids`. It rejects unknown/misattributed operations rather than inferring links.
- Revised text is taken from the exact `PatchOperation.replace`; delete-only text from `PatchOperation.find`; no-change evidence from the confirmed existing-anchor excerpt. The renderer does not call a model to regenerate manuscript body text.
- Markdown and DOCX use the shared deterministic `scripts/co_reviewer/response_renderer.py` adapter. DOCX output highlights the cited excerpts and labels document role, final locator and operation ID.
- `scripts/co_reviewer/release_manifest.py` checks the current on-disk RevisionLedger/Workflow Ledger hashes, active-document pointers, immutable batch intent/scope/publication receipts, task/comment coverage, verified Patch and operation attribution, completed ResponseUnits, final Word Clean DOCX/PDF hashes, Word COM page counts, Figure/Table SEQ records, quality reports and exact excerpts in both Response Letter formats.
- The Release Gate also reruns the repository's deterministic Figure/Table reference audit on all current Working Clean Manuscript/SI files. Unresolved/ambiguous references, duplicate captions, numbering gaps, out-of-order first mentions and cross-document same-label/different-caption collisions block release; non-blocking unreferenced captions and repeated-caption identity candidates remain visible review flags.
- Numerical values, units, sample identities and citation semantics are not inferred as equivalent by string matching. Their review remains an explicit human scientific/semantic confirmation attached to the Patch/ResponseUnit workflow; the consistency audit states this limitation rather than claiming automated scientific verification.
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
- `tests/test_response_renderer.py`: exact PatchOperation excerpt extraction and same text in Markdown/DOCX.
- `tests/test_release_manifest.py`: release blocking for incomplete ResponseUnits, reply-only completion without invented document locations, and comment-to-ResponseUnit coverage.

These tests are present in the repository but have **not been established as passing** in this implementation session.

## Validation status and remaining boundary

- The four relevant JSON Schema files (`workflow-ledger`, `final-release-manifest`, `revision-ledger`, `response-location-map`) were parsed as JSON successfully. This is a JSON syntax check only, not a full JSON Schema validation.
- A GitHub Actions run was triggered for the latest branch head. Both Python matrix jobs ended in failure, but GitHub returned no job steps and the job-log endpoint returned 404. There is no evidence that a runner executed the Python test suite; the result cannot be attributed to a test failure or treated as a pass.
- No real manuscript, Worker, model call, local Word COM run or final Windows acceptance was performed.
- The final acceptance boundary remains: run the complete synthetic suite on the target Windows/Python environment, then perform the user's own Word GUI/COM acceptance. Do not describe static inspection or schema parsing as final acceptance.
- P2 Usage Adapter is not included here. It still depends on locating/attaching the actual Supervisor/Worker model invocation wrapper; no complete automatic token capture claim is made.

## Branch / PR

- Base: `upgrade/v0.5.1-p0-implementation-20261114`
- Development branch: `upgrade/v0.5.1-p1-audit-quality-implementation-20261114`
- PR #3: Draft, open, not merged.

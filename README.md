# Co_Reviewer

**A traceable multi-document academic peer-review revision toolkit.**

Co_Reviewer coordinates revisions across reviewer comments, the manuscript, and Supporting Information (SI). It turns each reviewer comment into a revision item, links that item to one or more document edits, and produces auditable Word files with native Track Changes.

## v0.5.1 P0/P1 implementation

The current implementation branch extends the original revision engine with typed Patches/ResponseUnits, immutable revision batches, atomic active-document publication, deterministic Response Letter excerpts and a final release manifest. For the current end-to-end command sequence, use [docs/WORKFLOW.md](docs/WORKFLOW.md) and the repository [SKILL.md](SKILL.md). Implementation and validation boundaries are recorded in [docs/P1_IMPLEMENTATION_STATUS.md](docs/P1_IMPLEMENTATION_STATUS.md).

**Important:** older examples later in this README show the legacy revision-plan adapter. On the P1 implementation branch, do not send Marked DOCX outputs to a shared/reused path. Use the `runs/batch-{id}/` lifecycle documented above; publish a batch only after its Patch and Workflow task verification passes. Final page/line mapping is a round-level step after all document batches are published.

## What it does

- Inspects DOCX comments, native Word selection ranges, paragraphs, tables, captions/drawings, placeholders, media and existing tracked changes.
- Reconstructs selected text between each Word comment's range markers; distinguishes the selected text from its containing paragraph and does not infer an anchor from a comment reference alone.
- Reuses SHA-256-bound parsed inventories so unchanged DOCX files are not reparsed; deterministic matching, numbering audits and evidence-packet generation run against the cached inventory.
- Produces a compact Markdown digest, machine-readable inventory, cross-document match report, classified figure/table reference audit and a bounded semantic-decision queue.
- Synthesizes a complete review round into atomic, deduplicated tasks with evidence provenance and an explicit dependency graph; all reviewer comments in scope are covered in one decision pass.
- Validates pending decision plans against source hashes, native comment IDs, inventory coordinates, quoted evidence, target/evidence links, dependency order and mandatory user-approval states.
- Represents reviewer comments as stable revision items (for example, R2-C3) with linked targets in multiple documents.
- Requires explicit human approval before applying edits; a SHA-256 fingerprint binds approval to the exact reviewer comment, proposed response, document targets and (when preflighted) source DOCX versions.
- Applies every planned text change as native Word insertions/deletions (`w:ins`/`w:del`) while preserving unaffected run formatting and package parts. Direct text rewrites are not permitted in a Marked deliverable.
- Converts supported Unicode subscript/superscript characters in replacement text (for example `SO₄²⁻`) into native Word `w:vertAlign` subscript/superscript runs; verification checks inserted text and preservation of native script formatting after a Word save round-trip.
- Supports explicitly targeted body, header and footer Word XML parts; paragraph indices are local to the selected part.
- Preserves hyperlink relationships when an edit is wholly contained in a single simple hyperlink; fails closed when a target crosses a hyperlink boundary or unsupported Word structure.
- Validates the output ZIP package and XML before writing the revised DOCX.
- Verifies the exact native insertion/deletion revisions and audits the entire output text against its source, rejecting direct/untracked text edits outside planned revisions; renders a Response Letter draft without inventing replies.

## Quick start

Python 3.10+ is required. Install the DOCX/XML dependencies with:

```powershell
python -m pip install -r requirements.txt
```

### 1. Start an isolated review round

From the repository root, run the round starter. It copies source documents into a unique workspace, preserves the originals, creates the standard folders and performs the single inventory/audit pass. Every output is written inside that workspace, not into the repository root:

```powershell
.\scripts\Start-CoReviewerRound.ps1 -ReviewersDocx "D:\Review\Reviewers.docx" -ManuscriptDocx "D:\Review\Manuscript.docx" -SupportingInformationDocx "D:\Review\SI.docx" -PythonExe "D:\Software\Anacoda\python.exe"
```

Copy the printed `[WORKSPACE]` path into:

```powershell
$roundRoot = "<printed workspace path>"
```

The `round_manifest.json` records copied inputs, audit/inventory artifact paths and their SHA-256 hashes, and round directories. Decision Plan validation must use this manifest so the generated Workflow Ledger inherits the exact random round ID; do not invent or hard-code a separate round ID. Supporting Information is optional. The starter never modifies the selected originals and refuses to reuse an existing round folder.

The Audit outputs are:
- `reports/workspace_inventory.json`: paragraph, comment, anchor, caption and reference inventory.
- `reports/review_audit_report.json/.md`: cross-document matches and deterministic figure/table reference findings.
- `reports/review_decision_queue.json`: bounded evidence packets for semantic review.

Historical baselines are stored as `reports/Reviewers_revision_baseline.json`, `reports/Manuscript_revision_baseline.json` and optional `reports/SI_revision_baseline.json`. After mapping exact candidate targets, use `targeted-revision-gate`; for supported, author-approved historical conflicts use `resolve-historical-revisions` to a new DOCX path. See the Stage 1.5 instructions in [SKILL.md](SKILL.md).
The first run parses each DOCX once per SHA-256/cache-schema version. Unchanged files are reused from the local cache; a changed source invalidates only its own cache entry. The round starter also creates zero-model-call historical revision baseline reports for Reviewers, Manuscript and optional SI. Similarity/token-overlap matches are candidates, not confirmed targets. Figure/table numbering results are diagnostic; the tool never renumbers a document automatically.

### 2. Prepare and validate the Decision Plan

Review the complete decision queue and audit together. Split compound reviewer requests into atomic tasks, preserve every source comment ID, include target-document evidence for each proposed edit, record dependencies and keep all tasks pending with user approval required. Do not invent scientific claims, numerical values, citations or replacement text.

Create `plan/revision_decision_plan.json` conforming to `schemas/revision-decision-plan.schema.json`, then validate it against the exact round inventory/audit:

```powershell
python scripts/validate_decision_plan.py --plan "$roundRoot\plan\revision_decision_plan.json" --audit "$roundRoot\reports\review_audit_report.json" --inventory "$roundRoot\reports\workspace_inventory.json" --workflow-ledger-out "$roundRoot\plan\workflow_ledger.json" --round-manifest "$roundRoot\round_manifest.json"
```

Use `--identity-manifest` only when a current parser-backed identity manifest matches the copied source DOCX hashes. The generated Workflow Ledger remains planned; validation does not authorize edits.

### 3. Prepare exact PatchOperations without hand-editing the Workflow Ledger

Keep the validated `workflow_ledger.json` unchanged. Create `plan/workflow_execution_scope.json` using `schemas/workflow-execution-scope.schema.json`; use `examples/workflow_execution_scope.example.json` as a structural template and replace every illustrative value with current-round IDs and evidence. Bind it to the initial Workflow Ledger:

```powershell
$workflow = Join-Path $roundRoot "plan\workflow_ledger.json"
$workflowSha = (Get-FileHash -LiteralPath $workflow -Algorithm SHA256).Hash.ToLowerInvariant()
```

The execution scope records the current round ID and that hash, plus the author-reviewed execution details:
- One exact PatchOperation per mutating Decision Plan target, with operation ID, document role, Word part, paragraph index, find/replace, same-target evidence IDs and source reviewer comment IDs.
- Reviewed proposed response wording for each executable task.
- Explicit ResponseUnit dispositions for reply-only, no-change and clarification-needed comments. A no-change unit requires anchor evidence; a reply-only unit uses `not_required` location.
- Optional reviewer label overrides. Otherwise the preparation command takes reviewer labels from native Word comment authors in the inventory.

Do not put source/output paths or source hashes into the scope; the CLI resolves them from the current active document pointers. Run:

```powershell
python scripts/co_reviewer_cli.py prepare-workflow-execution --workflow-ledger $workflow --execution-scope "$roundRoot\plan\workflow_execution_scope.json" --inventory "$roundRoot\reports\workspace_inventory.json" --round-manifest "$roundRoot\round_manifest.json" --round-root-manifest "$roundRoot\round_manifest.json" --out "$roundRoot\plan\workflow_ledger.prepared.json" --reviewer-map-out "$roundRoot\plan\reviewer_map.json" --report "$roundRoot\reports\workflow_preparation_report.json"
```

The command checks the current Workflow Ledger path/hash and the exact inventory path/SHA-256 recorded by the root round manifest (starter or latest refresh), in addition to task/comment ownership, target/evidence matching, PatchOperation structure, reviewer attribution and ResponseUnit classification. It writes a new prepared Workflow Ledger, reviewer map and report, then records their paths and hashes back into the root round manifest so later Refresh/Reconcile calls find the same current Workflow version. When `--round-manifest` is a dedicated `refresh_manifest.json`, also pass `--round-root-manifest` for that round's root manifest. It does not change task status, grant author approval or edit a DOCX. Review the generated report and exact proposed responses before continuing.
This is the **manual/low-level preparation route**. For the PowerShell orchestrator in Section 4, do not run this command first: pass the author-reviewed ExecutionScope and Inventory to `Invoke-CoReviewerBatch.ps1`, which performs this preparation once and registers the outputs. Do not run both preparation routes for the same scope.

### Recommended Windows workflow: use the round orchestrators

For normal Windows use, the following two PowerShell entry points connect the existing CLI stages while retaining explicit author approval and semantic-review gates. The lower-level commands below remain available for advanced recovery and diagnosis.

**A. Prepare and publish one document-changing batch**

After the Supervisor has prepared the author-reviewed `revision_decision_plan.json` and `workflow_execution_scope.json`, run this from the repository root:

```powershell
$roundRoot = "<printed workspace path>"
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "plan\workflow_ledger.json" -ExecutionScope "plan\workflow_execution_scope.json" -Inventory "reports\workspace_inventory.json" -TaskIds T1 -PythonExe "D:\Software\Anacoda\python.exe"
```

The PowerShell command below assumes you skipped the manual preparation command above. It prepares exactly once from the base Workflow Ledger and supplied ExecutionScope/Inventory. If you already used the manual CLI route, omit `-ExecutionScope` and `-Inventory`, pass the registered prepared Workflow Ledger as `-WorkflowLedger`, and pass the generated Reviewer Map with `-ReviewerMap`.
The entry point prepares a new Workflow Ledger, Reviewer Map and report, then connects task authorization, immutable batch construction, plan export, preflight, exact Patch approval, Track Changes Apply, verification, untracked-text audit, semantic confirmation, Workflow synchronization and publication. It prints the actual generated paths and batch ID; use those returned paths rather than guessing names.

The script pauses for exact author confirmations at task authorization, Patch approval, scientific semantic review and publication. It stops at the first error and preserves the batch artifacts. It does not retry a failed batch in place.
Before preparing another scope, the batch runner calls `Reconcile-CoReviewerRound.ps1`. The refresh and finalization entry points use the same reconciler. It resolves hash-bound two-Ledger sync transactions, completes only already-authorized publication/rollback/recovery transactions, validates their original batch bindings and receipts, and reconstructs the round-manifest transaction index from those records. You can invoke it manually after an interrupted PowerShell session:

```powershell
.\scripts\Reconcile-CoReviewerRound.ps1 -RoundRoot $roundRoot -PythonExe "D:\Software\Anacoda\python.exe"
```

The manifest is an index, not the commit authority. A publication is committed only when its immutable batch intent/scope, Workflow publication events, active-document pointer hashes and `batch_published.json` receipt agree. If the process stops after pointer replacement but before the receipt or manifest update, reconciliation resumes that same publication from its persisted intent; it does not rerun Apply. A receipt that already exists is revalidated against the append-only Workflow event history and retained output hashes, including when a later batch has replaced the active pointer.

A batch that reached mechanical verification but stopped before publication is recorded as `verified_unpublished`. Re-entering the batch runner with that state requires an explicit `RESUME VERIFIED BATCH` confirmation, reuses the existing Applied/Verified plan and DOCX outputs, completes only missing semantic/Workflow state records, then asks for a separate publication confirmation. It never reapplies the DOCX edits. A partially applied or merely approved batch is not automatically retried: the runner requires explicit `RECOVER FAILED BATCH`, preserves all outputs, invalidates the affected Workflow DAG and stops so the author can refresh evidence before preparing a new batch.

The script updates `round_manifest.json` with the latest prepared Workflow Ledger, Reviewer Map, RevisionLedger, runs directory, preparation history and publication receipt. The refresher records refreshed inventory/audit paths, and the finalizer records final Word/PDF and Release Package paths. All round inputs and outputs remain inside that workspace.

**B. Continue with another batch**

After the previous batch is published, use the current prepared Workflow Ledger and Reviewer Map printed by the previous invocation. Reuse the same RevisionLedger and `runs/` directory:

```powershell
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "<current prepared Workflow Ledger>" -ReviewerMap "<current Reviewer Map>" -TaskIds T2 -PythonExe "D:\Software\Anacoda\python.exe"
```

If a dependent task becomes `recheck_required`, first refresh the latest active documents:

```powershell
.\scripts\Refresh-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<current prepared Workflow Ledger>" -BatchLabel "<published batch id>" -PythonExe "D:\Software\Anacoda\python.exe"
```

Create a new execution-scope file from the refreshed inventory, bind it to the current Workflow Ledger SHA-256, and call `Invoke-CoReviewerBatch.ps1` with the new `-ExecutionScope` and `-Inventory` paths. The command creates new prepared Ledger/Reviewer Map/report files. Do not reuse old paragraph coordinates or approval evidence. Run document-changing and resolution-only tasks separately.

**C. Finalize once all document-changing batches are published**

```powershell
.\scripts\Complete-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -RevisionLedger "plan\revision_ledger.json" -ManuscriptTitle "Manuscript title" -Author "Corresponding Author" -PythonExe "D:\Software\Anacoda\python.exe"
```

The finalizer creates or reuses a complete hash-current Working Clean set, displays Marked/Clean paths and hashes for author inspection, records semantic review, performs final Microsoft Word COM/PDF mapping, handles manual table/non-body locations, completes ResponseUnits, synchronizes the Workflow Ledger, and creates a new Release Package.

Pure reply-only rounds do not receive artificial document versions or page/line locations. Explicitly approved no-change comments are anchored to their current active DOCX and mapped during finalization.

If table/non-body locations require author input, the finalizer prints the exact ResponseUnit and locator IDs, then pauses without completing the round. Prepare a JSON file conforming to `schemas/manual-response-locations.schema.json` using `examples/manual_response_locations.example.json` as a structural example. Replace every illustrative value with the actual text and final location visible in the Word/PDF output; never copy the example page numbers.

On resume, the finalizer automatically reuses the Word/PDF directory recorded in `round_manifest.json`; add `-ManualLocationsJson` and omit `-WordPdfOutputDirectory` unless selecting that recorded directory explicitly:

```powershell
.\scripts\Complete-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -RevisionLedger "plan\revision_ledger.json" -WordPdfOutputDirectory "output\word_pdf_final-<stamp>" -ManualLocationsJson "plan\manual_locations.json" -ManuscriptTitle "Manuscript title" -Author "Corresponding Author" -PythonExe "D:\Software\Anacoda\python.exe"
```

A partial/stale Working Clean set or Word/PDF output directory is preserved and never overwritten. Release Package generation has a separate intent/receipt protocol: the intent binds the exact review round, title/author and Workflow/RevisionLedger path+SHA-256 snapshot; a matching partial package can resume in the same directory, while a changed source snapshot or metadata requires a new directory. Mismatched generated files are preserved under `.partial-<id>` names before they are rebuilt. The completion receipt is written last and hashes the Response Letter Markdown/DOCX, quality report and READY Final Release Manifest. A completed package is revalidated on retry rather than regenerated against newer source ledgers. The package is generated locally and is not submitted to a journal. Final Word GUI/PDF visual inspection remains a separate author acceptance step.

### 4. Low-level CLI alternative: authorize tasks and build an immutable batch

Only after the author accepts the prepared scope, authorize the selected tasks:

```powershell
$preparedWorkflow = Join-Path $roundRoot "plan\workflow_ledger.prepared.json"
$reviewerMap = Join-Path $roundRoot "plan\reviewer_map.json"
$revisionLedger = Join-Path $roundRoot "plan\revision_ledger.json"
$runs = Join-Path $roundRoot "runs"

python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger $preparedWorkflow --ids T1 --plan-approved --actor "Human Author"
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger $preparedWorkflow --reviewer-map $reviewerMap --runs-dir $runs --out $revisionLedger
python scripts/co_reviewer_cli.py export-revision-plan --ledger $revisionLedger --batch-dir "<full batch directory printed by build-revision-ledger>"
```

Use the actual task IDs and Patch IDs. The scheduler enforces dependencies and target-group conflicts. Candidate Patch/ResponseUnit data is validated before the immutable batch intent is created; the batch pins active parent paths/hashes and assigns new Marked DOCX output paths. Reuse the same prepared Workflow Ledger, RevisionLedger and `runs/` folder throughout the round.

Workflow/RevisionLedger synchronization is a two-file transaction. It stages both after-images and an immutable intent under `plan/.workflow_sync_transactions/<sync_id>/`, then writes a pending marker before replacing either ledger. The `recover-workflow-sync` command completes the same transaction forward after a process interruption. If the marker itself was not written, recovery may promote one unique intent only when both current ledgers still match its exact before-hashes. A hash outside the recorded before/after pair is an out-of-band change and causes recovery to stop without overwriting it:

```powershell
python scripts/co_reviewer_cli.py recover-workflow-sync --workflow-ledger "<Workflow Ledger>" --revision-ledger "<RevisionLedger>"
```

### 5. Low-level CLI alternative: preflight, approve, apply, verify and publish

Each immutable batch uses append-only plan snapshots. Use the exact paths printed by each command:

```powershell
python scripts/co_reviewer_cli.py preflight-plan --plan "<batch>\revision_plan.json" --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py approve-plan --plan "<batch>\revision_plan.preflighted.json" --ids P-T1 --revision-ledger $revisionLedger --actor "Human Author"
python scripts/co_reviewer_cli.py apply --plan "<batch>\revision_plan.approved.json" --revision-ledger $revisionLedger --author "Author Name"
python scripts/co_reviewer_cli.py verify --plan "<batch>\revision_plan.applied.json"
python scripts/co_reviewer_cli.py confirm-verified --plan "<batch>\revision_plan.applied.json" --ids P-T1 --semantic-review-confirmed --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --batch-dir "<full batch directory>"
```

Replace the example IDs with the actual Patch IDs in that batch. Both task authorization and exact Patch approval are mandatory. Apply writes native Word Track Changes to separate Marked DOCX files. Verify checks exact revisions and whole-document untracked-text integrity. Publish only after all Patches are verified and Workflow task state is synchronized. A later batch must use the newly published active pointers, not the original input DOCX.

### Refresh a dependent task after an upstream batch is published

After `sync-workflow-ledger`/`publish-batch`, downstream or conflicting tasks may be marked `recheck_required`. Do not reuse their old paragraph coordinates or manually edit the prepared Workflow Ledger. Audit the **current active document versions** and prepare a fresh, author-approved scope:

```powershell
$iterationLabel = "<published-batch-id>"
.\scripts\Refresh-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger $preparedWorkflow -BatchLabel $iterationLabel -PythonExe "D:\Software\Anacoda\python.exe"
$iteration = Join-Path $roundRoot ("reports\after-batch-" + $iterationLabel)
```

The refresher verifies every active file against its Workflow Ledger SHA-256 before running Audit, and passes that Ledger to the CLI for a second path/hash check. The generated `refresh_manifest.json` binds the exact latest audit/inventory paths and their SHA-256 hashes; the root `round_manifest.json` also records the latest-refresh paths/hashes. If you create a revised Decision Plan from this refresh, pass the dedicated `refresh_manifest.json` as `--round-manifest` and the root `round_manifest.json` as `--round-root-manifest` (or pass the root manifest for both, using its latest-refresh pointers). The validator checks both audit/inventory path+hash bindings and registers the newly generated Workflow Ledger path/hash back into the root manifest. Only these published active versions may contain native text insertions/deletions; formatting/move revisions and every comment part/anchor remain blocking. The initial round starter retains the strict clean-baseline gate. It includes SI only when that role is configured and never changes the Workflow Ledger or source DOCX files.

Create a new execution-scope file from `examples/workflow_execution_scope_refresh.example.json`. For each `recheck_required` document task, include both `evidence` and `targets` from the new inventory, assign fresh evidence IDs, and bind each operation's `find` text to its current target excerpt. If the task uses persistent TargetRefs, refresh `target_refs` as well. The current Workflow Ledger hash—not the initial round hash—must be used:

```powershell
$workflowSha = (Get-FileHash -LiteralPath $preparedWorkflow -Algorithm SHA256).Hash.ToLowerInvariant()
```

Then prepare to a **new** Ledger/map/report path, keeping the previous prepared Ledger and its history intact:

```powershell
$refreshedWorkflow = Join-Path $roundRoot "plan\workflow_ledger.prepared.after-<batch-id>.json"
$refreshedReviewerMap = Join-Path $roundRoot "plan\reviewer_map.after-<batch-id>.json"
$refreshScope = Join-Path $roundRoot "plan\workflow_execution_scope.after-<batch-id>.json"
python scripts/co_reviewer_cli.py prepare-workflow-execution --workflow-ledger $preparedWorkflow --execution-scope $refreshScope --inventory (Join-Path $iteration "workspace_inventory.json") --round-manifest (Join-Path $roundRoot "round_manifest.json") --round-root-manifest (Join-Path $roundRoot "round_manifest.json") --out $refreshedWorkflow --reviewer-map-out $refreshedReviewerMap --report (Join-Path $iteration "workflow_preparation_report.json")
```

Review the refreshed evidence, targets, exact PatchOperations and proposed response. Then set `$preparedWorkflow = $refreshedWorkflow` and `$reviewerMap = $refreshedReviewerMap`, explicitly authorize the now-eligible task, and build the next batch with the **same** `$revisionLedger` and `$runs` paths. The preparation step preserves old evidence rows, appends current evidence, and updates the task source-version references; it does not authorize or edit a document. A `no_change` ResponseUnit on a `recheck_required` task must also point to a newly refreshed `anchor_evidence_id` from the current active DOCX; an old-version anchor is rejected. Pure `reply_only` units do not need a document anchor.

The batch lifecycle, recovery boundaries and final release requirements are documented in [docs/WORKFLOW.md](docs/WORKFLOW.md). Do not delete or reuse a failed batch. A scope-less, intent-only orphan is not a failed Apply and must only be resolved through the dedicated retirement command after a fully verified matching successor publication.

### 6. Low-level CLI alternative: finalize the round and create the Release Package

After all document-changing batches have been published, create Working Clean projections and review the complete cross-document result. Confirm the semantics of each ResponseUnit, then use Microsoft Word COM on Windows to generate final Word-layout Clean DOCX/PDF files and actual page/line locations:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger $revisionLedger
python scripts/co_reviewer_cli.py confirm-response-units --ledger $revisionLedger --ids RU-T1-1 --semantic-review-confirmed --actor "Human Author"
python scripts/co_reviewer_cli.py map-response-locations --ledger $revisionLedger --out-dir (Join-Path $roundRoot "output\word_pdf_final")
python scripts/co_reviewer_cli.py complete-response-units --ledger $revisionLedger --ids RU-T1-1 --ready-for-submission-confirmed --actor "Human Author"
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --out-dir (Join-Path $runs "release-final") --manuscript-title "Manuscript title" --author "Corresponding Author"
```

Use the actual ResponseUnit IDs. The Word COM mapper is the only source of page/line values; PDF extraction independently checks page-level target presence. The mapper records location evidence in the RevisionLedger, so do not separately record the same map again. Complete each ResponseUnit only after semantic review, final location evidence (when required) and explicit author readiness.

The final command creates a unique `runs/release-{id}/` directory with Markdown/DOCX Response Letters, a quality report and `final_release_manifest.json`. It rechecks current pointers, Patch evidence, completed ResponseUnits, Word/PDF hashes, page counts, figure/table audit and exact Response Letter excerpts. A generated package is still subject to the author's final Word/PDF visual review; synthetic workflow acceptance is not scientific peer review.

## Safe revision engine: supported scope

- A target must occur exactly once in its selected paragraph.
- Ordinary paragraph and table-cell text can be edited when the target is contained in a contiguous sequence of supported plain-text runs. Header/footer edits must identify their exact `word/headerN.xml` or `word/footerN.xml` part and use that part's own paragraph index.
- Text wholly contained in one simple hyperlink can be edited inside the hyperlink wrapper while preserving its relationship ID.
- Existing run properties on text before, inside and after the target are retained where supported. Native sub/superscript run properties in unchanged text remain untouched.
- Targets crossing hyperlink boundaries, fields, drawings, bookmarks, content controls or other unsupported complex structures are refused rather than guessed.
- Input files are never overwritten; output paths must be separate.
- Untouched DOCX package members are checked byte-for-byte, and the package member inventory must remain unchanged.

This is a conservative initial compatibility boundary, not support for every possible WordprocessingML construct. Unsupported cases are reported and should be handled through a separately reviewed route.

## Supervisor–Worker model

Antigravity acts as Supervisor: it interprets reviewer intent, drafts revision plans, resolves cross-document logic and requests human approval. OpenCode Workers perform bounded inspection, deterministic edits and verification. The Supervisor reads concise reports and selected evidence rather than loading entire DOCX files.

Batched two-stage processing is the default efficiency strategy, not a hard limit on iterations. Local requests (for example, only revising Figure 4) should touch only the relevant targets.

## Project boundaries

Co_Reviewer manages peer-review revision orchestration and document consistency. It does not invent scientific results, experiments, citations or reviewer responses. Scientific claims and new data must come from user-provided evidence or explicitly verified sources.

## Current limitations

- DOCX only; legacy DOC and PDF are not editable targets.
- The current revision backend supports conservative plain-text-run replacements, table-cell paragraphs and text wholly contained in one simple hyperlink; it does not support arbitrary edits across complex Word structures.
- `render-response` writes Markdown; `render-response-docx` creates an editable Word draft using the built-in clean layout. Journal-specific templates are not yet applied automatically.
- Cross-document semantic consistency is reviewed by the Supervisor/author; the CLI verifies exact Word revision IDs and their text, but does not judge scientific meaning.
- Microsoft Word COM pagination/PDF mapping is available on Windows with Microsoft Word installed and the optional Word/PDF dependencies; it is not a cross-platform substitute for local Word acceptance.

## Deterministic OMML preview (no manuscript editing)

The isolated `preview-omml-docx` command converts a limited LaTeX subset into a synthetic Word document so formula rendering can be inspected:

```powershell
python scripts/co_reviewer_cli.py preview-omml-docx --expression "E=mc^{2}" --out "artifacts/omml-preview/equation_preview.docx"
```

The converter supports simple symbols/Greek letters, fractions, square roots, subscript/superscript, selected accents and basic text styles. Unsupported commands fail closed. The preview contains no Track Changes and never edits a Manuscript/SI. It is not proof that whole-formula revision wrappers work. Existing OMML remains blocked for production edits until the formula adapter passes the relevant Microsoft Word save/reopen acceptance.

## Microsoft Word compatibility acceptance

The automated tests verify the generated WordprocessingML, but they cannot prove how Microsoft Word displays and re-saves Track Changes. A separate synthetic acceptance fixture is provided for that purpose. It uses no manuscript or SI files.

Run from the repository root in PowerShell, using the same Python environment that passed the quality checks:

```powershell
.\\scripts\\run_word_compatibility_check.ps1 -PythonExe "D:\\Software\\Anacoda\\python.exe"
```

The runner creates a local test folder under `artifacts/word-compatibility-check`, performs preflight, approves the synthetic test item, applies five native Track Changes replacements, and verifies exact deletion/insertion text plus the absence of untracked text changes before Word is opened. The fixture covers two body paragraphs (including a scientific formula with subscript and superscript), one table cell, the header and the footer. Generated files are ignored by Git.

Open `word_compatibility_revised.docx` in Microsoft Word. On the Review tab, set Display for Review to **All Markup** and ensure insertions and deletions are shown. Inspect all five locations: each old value should appear as a deletion and each new value as an insertion; the formula should display 4 as subscript and 2− as superscript using native Word formatting. Color is not an acceptance criterion. Do not accept or reject any changes. Save the same DOCX, close Word, and reopen it.

After saving the same DOCX in Word, close Word, reopen the file, and confirm that the five changes remain visible with All Markup. Then run the repository runner in post-Word verification mode:

```powershell
.\\scripts\\run_word_compatibility_check.ps1 -PythonExe "D:\\Software\\Anacoda\\python.exe" -VerifyAfterWord
```

The runner writes `word_compatibility_verification_after_word.json` and prints the verification summary. A successful post-Word verification means all five planned target edits and their deleted/inserted text survived the Word save round-trip. It does not replace visual inspection or the separate Word COM pagination/PDF location-mapping acceptance. Do not run `confirm-verified` on this synthetic fixture; semantic confirmation is intended for actual manuscript/SI revisions after scientific review.

Use the same `-OutputDirectory` value on both invocations if you override the default output directory.

## Microsoft Word COM and PDF location acceptance

After the five-target Word compatibility fixture has been inspected in Word, saved, closed, reopened and passed `-VerifyAfterWord`, run the separate synthetic pagination/PDF acceptance. This path creates a test-only RevisionLedger from that fixture, creates its Working Clean View, asks Microsoft Word COM to produce the final Clean DOCX/PDF and page/line map, corroborates the Word-reported page against extracted PDF text, and renders a location-aware Response Letter draft with a quality report.

Install the optional Word/PDF dependencies into the same Python environment if they are not already available:

```powershell
& "D:\Software\Anacoda\python.exe" -m pip install -e ".[word-pdf]"
```

Run from the repository root:

```powershell
.\scripts\run_word_pdf_mapping_acceptance.ps1 -FixtureDirectory "C:\Users\Administrator\AppData\Local\Temp\Co_Reviewer_Word_Acceptance_f2f2913" -PythonExe "D:\Software\Anacoda\python.exe"
```

The runner refuses to overwrite existing acceptance outputs. By default it writes a new `word_pdf_mapping_acceptance` folder inside the fixture directory. It verifies the Word COM page count against the exported PDF, the exact body phrase on the Word-reported PDF page, hash-bound final DOCX/PDF and DOCX quality reports, the RevisionLedger location record, and the rendered Response Letter Markdown/DOCX.

The synthetic ResponseUnit remains `location_mapped`, not `completed`: the fixture does not provide author readiness for submission and does not create a journal-ready release package. It is only evidence that the Word COM/PDF mapping and Response Letter rendering path works on an isolated synthetic document. Real Manuscript/SI batches, semantic review, manual table/non-body locators and final release acceptance remain separate.

## Tests

Run the complete local checks on Windows from PowerShell:

    .\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe"

Replace the interpreter path with the intended Python 3.10+ environment. To install the declared dependencies into that environment first:

    .\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe" -InstallDependencies

The script compiles source and tests, runs the unit suite and checks the key CLI entry points. It does not replace opening the generated DOCX files in Microsoft Word.

Individual commands:

    python -m unittest discover -s tests -v
    python scripts/co_reviewer_cli.py --help
    python scripts/co_reviewer_cli.py approve-plan --help
    python scripts/co_reviewer_cli.py confirm-verified --help


## Usage and Word-output quality audit

Co_Reviewer can optionally record SHA-bound DOCX parse/cache observations with `inspect`/`audit --usage-ledger`. Caller-supplied model events can be appended with `record-usage-event`, summarized by `audit-usage`, and priced with `estimate-usage` only when explicit disjoint-bucket rates and complete token evidence are available. The ledger stores metadata, not manuscript text; filesystem reads do not prove model-context exposure.

`audit-docx-quality` checks DOCX package/XML integrity, tracked changes, comments, draft placeholders, selected typography profiles, and optional PDF integrity. Response Letter DOCX generation and Word/PDF location mapping also emit quality reports. These deterministic checks complement—but do not replace—Microsoft Word visual acceptance, PDF page/line verification, or scientific semantic review. See `docs/COST_AND_QUALITY_AUDIT.md`.

## P0 integrity gates and final-location rules

The initial Manuscript and SI baselines must be clean before a revision round is registered. The gate scans native insertions/deletions, move revisions, every Word *Change element, native comment definitions and comment anchors. Existing unresolved markup blocks the round with package-part and paragraph evidence; it is not silently accepted as the new baseline.

Final Marked and Clean deliverables physically remove Word comment definitions, comment anchors, comment-related package parts, relationships and content-type overrides. Marked files retain the in-line w:ins and w:del Track Changes that record the current Co_Reviewer edits. Do not remove those revisions as a shortcut for comment cleanup.

Every executable PatchOperation must carry a non-empty, exact source_comment_ids list. Each revised ResponseUnit must link the precise operation IDs that declare that comment ID. No post-hoc inference from task text or target overlap is permitted. The ResponseUnit disposition and locator requirement are explicit: reply_only/not_required; no_change/existing_anchor_required or no_change/manual_required (both require a confirmed Clean anchor); revised/required or revised/manual_required; clarification_needed remains blocked.

Final body-text page/line positions come only from Microsoft Word COM on the final Clean DOCX. Mapping is grouped by document role. If the role's Working Clean SHA changes, all existing page/line evidence for that role is invalidated and the role is repaginated/remapped together. Each mapped ResponseUnit is bound to the final DOCX SHA, PDF SHA, map artifact SHA, locator ID and source Working Clean SHA. The ledger rejects mixed final DOCX/PDF versions within one document role.

After Word refreshes built-in SEQ, REF, PAGE and PAGEREF fields (not citation-manager ADDIN fields), it repaginates and exports the PDF. Native numeric SEQ Figure and SEQ Table results are checked for gaps, duplicates and unexpected starting numbers. A deterministic numbering error blocks export. Chapter-reset, non-numeric or manually typed caption schemes are explicitly marked manual_review; they are not reported as automatically certified.

### Table and non-body manual locators

When a target cannot safely receive an automatic body-line locator (for example, a table cell or unsupported header/footer object), set location_requirement to manual_required. The Word mapper still exports the final DOCX/PDF for that document role but does not invent a line number. After map-response-locations, enter one locator row for every exact linked PatchOperation:

    {
      "response_units": [
        {
          "response_unit_id": "RU-T1-C1",
          "operation_id": "OP-1",
          "locator_text": "Table S2, Supplementary Information",
          "page": null,
          "line_start": null,
          "line_end": null
        }
      ]
    }

Then record the author-confirmed locators against the Word-exported artifacts:

    python scripts/co_reviewer_cli.py record-manual-response-locations --ledger revision_ledger.json --word-map word_pdf_final/response_location_map.json --input manual_locations.json --out manual_location_map.json --actor "Human Author"

The command requires exact operation coverage for every manual-required ResponseUnit, validates the output DOCX/PDF and current Working Clean hashes, and stores mapping_method=manual with a stable locator ID. locator_text is mandatory. Page is optional; line numbers may be supplied only when the author has checked them. When line numbers are supplied, page, line_start and line_end must all be positive integers. Do not fill uncertain line numbers with estimates. A manual locator is visibly distinct from a Word COM location in the ledger and Response Letter.


A no_change ResponseUnit may use manual_required when its confirmed existing anchor is a table cell or non-body part. The Clean-view step must still re-resolve its paraId or exact source excerpt in the declared Word part. The manual-input row uses locator_id (for example, anchor:E-ANCHOR-2), not operation_id; it records a locator, never an executable Patch.


For a `no_change/manual_required` unit, the input uses the confirmed anchor key rather than a PatchOperation:

```json
{
  "response_units": [
    {
      "response_unit_id": "RU-T2-C3",
      "locator_id": "anchor:E-ANCHOR-2",
      "locator_text": "Table 2, Methods, row 3"
    }
  ]
}
```

The anchor evidence must already have been confirmed and re-resolved in the current Working Clean part. This records only the author's final locator; it does not create a PatchOperation or claim that the manuscript was changed.

## P1 immutable batch lifecycle

Every document-changing ready batch is assigned a unique `runs/batch-{id}/` directory while its RevisionLedger Patches are being built. The current Workflow Ledger `active_documents` map is captured as the parent version (path + SHA-256); each document role receives a new Marked DOCX output path under that batch's `documents/` folder. The active pointer is not changed by Apply.

### Batch execution

1. Build the batch. The command prints the unique batch ID, directory and `batch_intent.json` path:

```powershell
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --task-ids T1 T2 --out revision_ledger.json
```

2. Export and preflight the immutable batch plan:

```powershell
python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --batch-dir "runs/batch-<id>"
python scripts/co_reviewer_cli.py preflight-plan --plan "runs/batch-<id>/revision_plan.json" --revision-ledger revision_ledger.json
```

3. Use the exact `[NEXT PLAN]` path printed by each command. The plan files are append-only stage snapshots; do not edit or overwrite the earlier snapshot:

```text
revision_plan.json
revision_plan.preflighted.json
revision_plan.approved.json
revision_plan.applied.json
revision_plan.verified.json
```

```powershell
python scripts/co_reviewer_cli.py approve-plan --plan "runs/batch-<id>/revision_plan.preflighted.json" --revision-ledger revision_ledger.json --ids P1
python scripts/co_reviewer_cli.py apply --plan "runs/batch-<id>/revision_plan.approved.json" --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py verify --plan "runs/batch-<id>/revision_plan.applied.json"
python scripts/co_reviewer_cli.py confirm-verified --plan "runs/batch-<id>/revision_plan.applied.json" --revision-ledger revision_ledger.json --ids P1 --semantic-review-confirmed
```

4. Synchronize verified Patch lifecycle state to the Workflow Ledger, then publish the batch. Publication checks the immutable batch intent/scope, parent pointers, verified Patch hashes and Workflow task states. It deliberately does **not** require final ResponseUnit completion or final Word/PDF locations: a later batch can change pagination across the whole document role.

```powershell
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-<id>"
```

5. After **all** document-changing batches have been published, create the final Working Clean views, confirm ResponseUnit semantics, map final Word/PDF locations and complete the ResponseUnits:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-P1-C1 --verification-report-id "semantic-review-P1-C1" --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir "word_pdf_final"
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-P1-C1 --ready-for-submission-confirmed
```

The map-response-locations command writes the Word COM map and records its location evidence in the RevisionLedger in the same command. Do **not** run record-response-locations again for that same map. The standalone command is for attaching a separately generated, still-unrecorded map after verifying that it matches the exact current Ledger. For manual_required table/non-body locators, use record-manual-response-locations with the exact author-confirmed locator input described above before completing those ResponseUnits.

6. Create the final release package only after all batches are published and every required ResponseUnit is completed.

### Rollback and failed batches

A published batch can be rolled back without deleting any Marked DOCX, Clean DOCX, PDF, plan, report or batch directory:

```powershell
python scripts/co_reviewer_cli.py rollback-batch --workflow-ledger workflow_ledger.json --batch-dir "runs/batch-<id>" --reason "Explain why this committed batch is being rolled back."
```

Rollback restores all parent active pointers atomically and marks the affected tasks/dependents `recheck_required`. If an uncommitted batch has already created a partial output, do not reuse or overwrite that output. Preserve the failed batch as evidence; explicitly supersede/re-authorize the affected Patch and build a fresh batch with new output paths. A second batch must not silently reuse the failed batch's files.

Publication and rollback use intent/receipt records so a retry after an interrupted pointer update can finish the receipt without overwriting later Workflow Ledger edits. If a concurrent Workflow Ledger update is detected before pointer replacement, the command fails closed.
An orphaned preparation that stopped **before** writing `batch_scope.json` is a different case from a failed Apply and must not be passed to `recover-failed-batch`. It may be retired only when its batch directory contains exactly the immutable `batch_intent.json` plus an empty `documents/` directory, every declared parent still matches its recorded hash, and a later batch has published the exact same task set and document roles from those same parents. The successor publication, scope, Patch operation paths and hashes are revalidated before an append-only `batch_superseded.json` receipt is written:

```powershell
python scripts/co_reviewer_cli.py retire-superseded-prepared-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-{orphan-id}" --successor-batch-dir "runs/batch-{published-id}" --actor "Human Author" --reason "Intent-only preparation replaced by the exact verified published batch."
```

This retirement changes neither Ledger nor active-document pointers. It refuses batches with a scope, any extra artifact, any output, incomplete publication evidence, or a successor based on different parents. Do not manually delete an orphaned batch or fabricate its missing scope.

## P1 final Response Letter and release manifest

The Markdown and DOCX Response Letter renderers use the same deterministic ResponseUnit/PatchOperation adapter. For revised units, quoted replacement text is copied from the exact verified `PatchOperation.replace`; for delete-only operations it quotes the exact `find` text; for `no_change` it quotes the confirmed existing-anchor excerpt. The renderer never asks a model to rewrite or repeat the manuscript change. The DOCX highlights these excerpts and labels their document role, final locator and operation ID.

After all relevant Workflow tasks are resolved, all Patches are verified, every reviewer comment has exactly one completed ResponseUnit, all final locations and quality reports are current, and every changed document role is published, create the final package:

```powershell
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
```

The command creates a new unique `runs/release-{id}/` directory containing:

- `Response_to_Reviewers.md`
- `Response_to_Reviewers.docx`
- `Response_to_Reviewers.quality.json`
- `final_release_manifest.json`

The Release Gate reopens and rehashes current files, checks active pointers against Marked DOCX files, verifies Patch and ResponseUnit links, rechecks every final Word Clean DOCX/PDF pair, Word page counts, Figure/Table sequence audits and DOCX quality reports, and confirms the Response Letter contains the exact verified PatchOperation excerpts. The manifest is written only if every gate passes. A failed package directory is retained for diagnosis; create a fresh release directory after fixing the cause.

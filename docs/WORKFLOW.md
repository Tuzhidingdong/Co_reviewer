# Co_Reviewer workflow guide

## Revision round structure

Keep original inputs immutable. Use a dedicated folder per round:

    revision_round_01/
      input/
        Reviewers.docx
        Manuscript.docx
        SI.docx
      plan/
        revision_decision_plan.json
        workflow_ledger.json
        reviewer_map.json
        revision_ledger.json
        edits_plan_batch_01.json
        edits_plan_batch_02.json
      output/
        Manuscript_marked.docx
        SI_marked.docx
        word_pdf_final/
        Response_to_Reviewers.docx
        Response_to_Reviewers.md
        revision_apply_report.json
      reports/
        workspace_digest.md
        workspace_inventory.json
        review_audit_report.md
        review_audit_report.json
        review_decision_queue.json
        preflight_report_batch_01.json
        revision_verification_batch_01.json
      .co-reviewer-cache/
        docx-v1-<source-sha256>-<author-filter>.json

## Single-pass inventory, native anchors and cached audit

On Windows, create an isolated round workspace and run the single audit pass with the repository starter:

```powershell
.\scripts\Start-CoReviewerRound.ps1 -ReviewersDocx "D:\Review\Reviewers.docx" -ManuscriptDocx "D:\Review\Manuscript.docx" -SupportingInformationDocx "D:\Review\SI.docx" -PythonExe "D:\Software\Anacoda\python.exe"
```

Set `$roundRoot` to the printed workspace path. The starter copies the original files into `input/`, creates `plan/`, `reports/`, `output/`, `runs/` and cache folders, runs audit once, and writes `round_manifest.json`. It refuses to reuse a previous workspace and keeps audit outputs out of the Git repository root. On other platforms, use the same directory layout and direct every CLI output explicitly into `reports/`.

The first run parses each available DOCX once and caches its inventory against the source SHA-256 and cache-schema version. Re-running with unchanged inputs reuses those inventories; it recalculates matches/numbering from cached JSON without reparsing the DOCX. If one source changes, only that source inventory is refreshed. Use `--refresh-cache` only when deliberately forcing a fresh parse. Keep the cache local; it is ignored by Git and is not a deliverable.

For each native Word comment, the inspector reconstructs the selected text between the matching `commentRangeStart` and `commentRangeEnd` markers. It reports the selected range separately from the containing paragraph. A comment reference without a recoverable range is labelled reference-only/unresolved; never treat the entire containing paragraph as the Word selection. Cross-paragraph ranges retain all touched paragraph indices.

Cross-document matching has three evidence levels:
- Exact and unique native-selection text in one target document: confirmed target candidate for review.
- Exact text in more than one target document: ambiguous; no single document is inferred.
- Token-overlap text: candidate only; it is never promoted to a confirmed target.

The figure/table audit parses caption labels and textual Figure/Fig./Table references. It reports per-document caption order, first-mention order, duplicate captions, numbering gaps, unresolved references and unreferenced captions, plus labels repeated across documents. These are diagnostic flags, not a renumbering plan; the script never edits numbering automatically. Captions encoded only inside images, floating shapes or unsupported text boxes may be missed and must be reviewed.

The decision queue includes each comment, the native anchor, confirmed/candidate locations and a bounded number of nearby paragraph excerpts. Feed only the relevant item from this queue to the model. Semantic disposition (Manuscript, SI, Reply only, multiple documents, no change or clarification needed) remains a pending proposal with evidence/rationale fields and an explicit user-approval requirement. The audit does not write a revision plan or modify DOCX files.

## Revision item identity

Use stable IDs such as R1-C1, R2-C4 or EDITOR-C2. Do not renumber an item merely because another item is added. If a reviewer comment contains several independent requests, split it into sub-items while retaining the original comment text.

## Cross-document mapping

One revision item may have multiple targets. A response is not complete until each promised target has an output or a documented reason it is not applicable. Check numbers, units, figure labels, sample names and calculation settings across Manuscript and SI.

## Local and iterative requests

For a local request, include only affected revision items and targets. Do not rebuild the entire plan or re-inspect all files when source files and paragraph indices have not changed. If an earlier edit changes paragraph structure, re-inspect the affected file before preparing further index-based edits.

## Safe text matching

Use paragraph indices from the latest inventory. For body text, target `word/document.xml`; for header or footer text, target its specific `word/headerN.xml` or `word/footerN.xml` part and use the index reported within that part. Never reuse a body paragraph index for a header/footer target. A target must be unique within its paragraph. Do not shorten the anchor to a generic phrase. A replacement wholly contained within one simple hyperlink is supported and retains the hyperlink wrapper and relationship. A replacement crossing the hyperlink boundary, field, drawing, bookmark or content control is refused. Report the limitation; do not disable validation.

## Language and tense quality gate

Before the user approves an edit, read the complete sentence containing the anchor and enough adjacent text to understand the paragraph's time reference. Draft in context rather than replacing an isolated phrase. Preserve the surrounding tense unless the scientific time reference genuinely changes; do not mechanically convert revisions to past or present. Completed procedures and observations are generally past tense; established scientific facts and what figures/tables show are often present tense. Also check subject–verb agreement, articles, number, prepositions, parallel structure and transitions. The approval preview must show the original full sentence, proposed full sentence and exact replacement span. Flag ambiguity rather than guessing.

## Native Word scientific notation and Marked-version integrity

When a replacement needs subscripts/superscripts, put the corresponding supported Unicode script characters in the plan (for example `SO₄²⁻`). The CLI converts compatibility-decomposable subscript/superscript characters to ordinary base characters in separate runs with native Word `w:vertAlign` values. Do not use caret notation or HTML as a formatting substitute. Unsupported Unicode script characters are refused during preflight; use an explicit, reviewed formatting route instead.

Every textual change in the Marked Manuscript/SI must use native Word Track Changes: deleted text in `w:del`/`w:delText`, inserted text in `w:ins`/`w:t`. Font color is not an acceptance criterion. All Marked-file edits must pass through the approved revision plan and CLI `apply`; do not directly rewrite text with `python-docx`, XML text replacement, another DOCX writer, or Word with Track Changes turned off. Unsupported targets must be blocked, not silently changed directly. `verify` checks exact revision IDs/text and audits all text outside revision wrappers against the source so unrelated direct text changes fail verification.

## Workflow Ledger, Patch batches and approval

The Workflow Ledger is the round-level control plane; the RevisionLedger is the durable record of Patch and ResponseUnit execution. Do not hand-edit Workflow Ledger state or maintain a separate legacy plan as a source of truth.

After the author accepts the pending Decision Plan's scientific dispositions and target scope, validate provenance and create the initial Workflow Ledger:

```powershell
python scripts/validate_decision_plan.py --plan "$roundRoot\plan\revision_decision_plan.json" --audit "$roundRoot\reports\review_audit_report.json" --inventory "$roundRoot\reports\workspace_inventory.json" --workflow-ledger-out "$roundRoot\plan\workflow_ledger.json" --round-manifest "$roundRoot\round_manifest.json"
```

Use a parser-backed identity manifest only when its source hashes match the current inputs. The resulting tasks remain planned and require explicit approval.

### Prepare exact execution scope

Keep `workflow_ledger.json` unchanged as the validated initial record. Create a separate `workflow_execution_scope.json` under `plan/` with:

- The schema version, exact round ID and SHA-256 of the initial Workflow Ledger.
- One scope entry per task with exact Patch IDs, author-reviewed Response Letter wording and PatchOperations.
- Exactly one PatchOperation for each mutating Decision Plan target. Each operation supplies operation ID, document role, Word part, paragraph index, exact find/replace, same-target evidence IDs and source reviewer comment IDs. Do not supply file paths or hashes; the CLI resolves them from current active document pointers.
- Explicit ResponseUnit classifications for reply-only, no-change and clarification-needed comments. No-change units must identify their confirmed anchor evidence.
- An optional reviewer_map for corrections/overrides. By default native comment authors are read from the source inventory.

The structure is defined by `schemas/workflow-execution-scope.schema.json`. The preparation command checks the exact Workflow Ledger path/SHA, the inventory path/SHA-256 recorded by the root round manifest (starter or latest refresh), task and comment ownership, target/evidence mapping, PatchOperation structure and ResponseUnit dispositions. It writes a new prepared Workflow Ledger and reviewer map, then records their paths and hashes in the root round manifest without changing task status, authorizing work or editing any DOCX. For a dedicated `refresh_manifest.json`, pass it as `--round-manifest` and pass the root `round_manifest.json` separately as `--round-root-manifest`:

```powershell
$workflow = Join-Path $roundRoot "plan\workflow_ledger.json"
$preparedWorkflow = Join-Path $roundRoot "plan\workflow_ledger.prepared.json"
$scope = Join-Path $roundRoot "plan\workflow_execution_scope.json"
$inventory = Join-Path $roundRoot "reports\workspace_inventory.json"
$reviewerMap = Join-Path $roundRoot "plan\reviewer_map.json"
$prepReport = Join-Path $roundRoot "reports\workflow_preparation_report.json"
python scripts/co_reviewer_cli.py prepare-workflow-execution --workflow-ledger $workflow --execution-scope $scope --inventory $inventory --round-manifest (Join-Path $roundRoot "round_manifest.json") --round-root-manifest (Join-Path $roundRoot "round_manifest.json") --out $preparedWorkflow --reviewer-map-out $reviewerMap --report $prepReport
```

Review the generated report and proposed responses with the author. Any change to the scope requires regenerating the prepared outputs; do not hand-edit Workflow approval/status/fingerprint fields.
This is the manual CLI route. If you use the PowerShell batch orchestrator instead, skip this direct preparation call; the orchestrator prepares once from the supplied ExecutionScope/Inventory. Never prepare the same scope twice. After manual preparation, pass the registered prepared Workflow Ledger and Reviewer Map to the PowerShell runner and omit `-ExecutionScope`/`-Inventory`.

### Authorize and build the next batch

Only after the author accepts the prepared task scope, authorize the currently eligible tasks:

```powershell
python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger $preparedWorkflow --ids T1 --plan-approved --actor "Human Author"
```

Use actual task IDs. Dependencies and target-group conflicts are enforced by the scheduler. A dependent task remains planned until its dependencies are verified.

Build the current conflict-free batch using the same prepared Workflow Ledger, reviewer map, RevisionLedger path and round runs directory:

```powershell
$revisionLedger = Join-Path $roundRoot "plan\revision_ledger.json"
$runs = Join-Path $roundRoot "runs"
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger $preparedWorkflow --reviewer-map $reviewerMap --runs-dir $runs --out $revisionLedger
```

The CLI validates candidate Patch and ResponseUnit records before creating a batch intent. Each accepted batch receives a new immutable `runs/batch-{id}/` directory, pins its parent active document paths/hashes and assigns new Marked DOCX output paths. It then prints the Batch ID and directory.

Export its plan into that directory:

```powershell
python scripts/co_reviewer_cli.py export-revision-plan --ledger $revisionLedger --batch-dir "<full batch directory>"
```

The exported plan is an adapter for the existing Word-native Track Changes writer. It is not a competing source of truth. Later batches reuse the same prepared Workflow Ledger and RevisionLedger paths and the same runs root; each batch receives its own new directory and append-only plan/report snapshots.

### Preflight, approve, apply, verify and publish

Use the exact paths printed by the CLI. Each lifecycle stage creates a new immutable snapshot:

```powershell
python scripts/co_reviewer_cli.py preflight-plan --plan "<batch>\revision_plan.json" --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py approve-plan --plan "<batch>\revision_plan.preflighted.json" --ids P-T1 --revision-ledger $revisionLedger --actor "Human Author"
python scripts/co_reviewer_cli.py apply --plan "<batch>\revision_plan.approved.json" --revision-ledger $revisionLedger --author "Author Name"
python scripts/co_reviewer_cli.py verify --plan "<batch>\revision_plan.applied.json"
python scripts/co_reviewer_cli.py confirm-verified --plan "<batch>\revision_plan.applied.json" --ids P-T1 --semantic-review-confirmed --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --batch-dir "<full batch directory>"
```

Approval uses Patch IDs, not Workflow task IDs. The CLI reports the next approved/applied/verified plan path after each stage. Verify must pass, including the untracked-text audit, before semantic confirmation and publication. Publication requires every Patch in the batch verified and the Workflow task state synchronized. It atomically moves active document pointers to the new Marked DOCX outputs. A later batch must use those latest published active pointers, never the original input documents.

### Recheck a dependent task against the latest active document version

After an upstream batch is synchronized and published, affected/downstream tasks may enter `recheck_required`. A document-changing task in that state must not reuse the old target paragraph index or old evidence. Run `scripts/Refresh-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger $preparedWorkflow -BatchLabel <published-batch-id> -PythonExe <python-path>`. The script verifies all active file hashes, then passes the current Workflow Ledger to the Audit CLI for a second path/hash check. The generated `refresh_manifest.json` binds the new inventory/audit paths and SHA-256 values; use `--round-manifest <refresh_manifest.json> --round-root-manifest <round_manifest.json>` when validating a refreshed Decision Plan, so the output Workflow Ledger is registered as the current round version. Only those exact published active versions may contain native text insertions/deletions; formatting/move revisions and all comment parts/anchors remain blocking. The initial round's clean-baseline rule remains strict. Audit outputs go into a fresh `reports/after-batch-<id>/` folder.

Create a new execution-scope file using `examples/workflow_execution_scope_refresh.example.json`. Include both `evidence` and `targets` for every rechecked document-changing task. Give all refreshed evidence rows new IDs, require `match_status=confirmed`, and ensure the current inventory contains the same part, paragraph index and excerpt. PatchOperations must use the refreshed target coordinates and an exact `find` excerpt from the new evidence. Refresh `target_refs` too when persistent object references are used.

Set `workflow_ledger_sha256` to the SHA-256 of the **current prepared Workflow Ledger after publication/synchronization**, not the initial Workflow Ledger. Run `prepare-workflow-execution` using that current prepared Ledger as input, the root `round_manifest.json` (which records the exact latest-refresh inventory path/hash), and a new output path for the next prepared Ledger/reviewer map/report. The command preserves the earlier evidence rows, appends new evidence and adds the new active file/hash to task `source_versions`; it does not authorize the task. Review the result, explicitly re-authorize the task after dependencies are satisfied, then build the next immutable batch with the same RevisionLedger and runs directory. Continue using the new prepared Workflow Ledger path as the current Workflow state. A rechecked `no_change` ResponseUnit must use a fresh `anchor_evidence_id` from the refreshed current-version evidence; the earlier anchor is stale. Pure `reply_only` units need no document anchor.

If Apply leaves partial outputs, preserve the failed batch and use `recover-failed-batch` only when its immutable scope proves the exact Patch ownership and its parent active pointers have not changed. Never delete or reuse a failed batch.

A prepared batch that contains only its immutable `batch_intent.json`, has no `batch_scope.json`, no other artifacts and an empty `documents/` folder is an orphaned preparation—not a failed Apply. Do not pass it to `recover-failed-batch` and do not delete it manually. It can be retired only through `retire-superseded-prepared-batch` when a complete verified successor publication covers the exact same task set, roles and parent document hashes:

```powershell
python scripts/co_reviewer_cli.py retire-superseded-prepared-batch --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --batch-dir "<orphan batch directory>" --successor-batch-dir "<verified published successor batch>" --actor "Human Author" --reason "Intent-only preparation replaced by the exact verified published batch."
```

The command appends a hash-bound `batch_superseded.json` receipt and changes neither Ledger nor active pointers. It refuses any orphan with additional artifacts or any successor that does not pass the complete publication checks.

A published batch can be rolled back without deleting files:

```powershell
python scripts/co_reviewer_cli.py rollback-batch --workflow-ledger $preparedWorkflow --batch-dir "<published batch directory>" --reason "Describe the reason"
```

Rollback restores parent pointers and marks affected tasks/dependents `recheck_required`; the new scope must be refreshed and reapproved. After all document-changing batches for the round are published, perform final Working Clean and Word/PDF mapping once for the whole round. Later batches can change pagination, so do not map final page/line locations between batches.

## Recommended Windows orchestration

The individual CLI commands above define the lifecycle and remain available for advanced recovery. For normal Windows operation, use the two PowerShell entry points so the prepared Ledger/map paths, immutable batch IDs and stage transitions are carried forward explicitly.

### Run one document-changing batch

```powershell
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "plan\workflow_ledger.json" -ExecutionScope "plan\workflow_execution_scope.json" -Inventory "reports\workspace_inventory.json" -TaskIds T1 -PythonExe "D:\Software\Anacoda\python.exe"
```

This entry point runs preparation (when `-ExecutionScope` and `-Inventory` are supplied), explicit task authorization, RevisionLedger construction, immutable batch export, preflight, exact Patch approval, Apply, Verify, untracked-text audit, author semantic confirmation, Workflow synchronization and batch publication. The author must enter exact confirmation phrases at the authorization, approval, semantic-review and publication gates. A failed batch is preserved; the runner never retries it in place.
Before preparing the next execution scope, the runner invokes `Reconcile-CoReviewerRound.ps1`. Refresh and finalization invoke it too. The reconciler resolves only transactions already authorized by persisted intent, validates the original batch-to-Ledger bindings and receipts, and rebuilds the round manifest's active pointers and transaction histories. After a publish/rollback/recovery operation commits but a later receipt or manifest write is interrupted, the next invocation resumes that same transaction; it does not rerun Apply.

The reconciler distinguishes published, rolled-back/recovered, verified-but-unpublished and failed/unverified states. A verified-unpublished batch requires an explicit resume confirmation and reuses its immutable verified plan/output; semantic and Workflow state records are made idempotent before a separate publication confirmation. An unverified partial batch requires explicit failed-batch recovery, retains its files, invalidates the selected/dependent Workflow tasks and stops for a new evidence refresh. The tool never silently retries or abandons the batch.

The two-Ledger sync stages immutable after-images and intent before writing either file. A pending marker (or one unique pre-marker intent whose both before-hashes still match) allows forward-only recovery. Any unknown hash or conflicting transaction stops without overwriting the current ledgers.

The batch runner also updates `round_manifest.json` with the current prepared Ledger/Reviewer Map, RevisionLedger, runs directory and immutable publication history. The refresher records each active-version inventory and its manifest; the finalizer records final Word/PDF and Release Package paths. Round inputs and outputs are confined to the round workspace.

After publication, retain the actual `[WORKFLOW LEDGER]`, `[REVIEWER MAP]`, `[REVISION LEDGER]`, `[BATCH DIRECTORY]` and `[PUBLICATION RECEIPT]` paths printed by the script. For the next ready task, pass the latest prepared Workflow Ledger and Reviewer Map and reuse the same RevisionLedger and `runs/` root:

```powershell
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -ReviewerMap "<latest Reviewer Map>" -TaskIds T2 -PythonExe "D:\Software\Anacoda\python.exe"
```

When publication marks downstream work `recheck_required`, refresh before creating the next scope:

```powershell
.\scripts\Refresh-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -BatchLabel "<published batch id>" -PythonExe "D:\Software\Anacoda\python.exe"
```

Use the new inventory under `reports/after-batch-<id>/`, calculate the SHA-256 of the current prepared Workflow Ledger, and author a new execution scope against that current version. Invoke the batch runner with the new `-ExecutionScope` and `-Inventory`; it creates a new prepared Ledger, Reviewer Map and report. Old coordinates and previous approvals are not reusable.

Do not mix document-changing tasks and resolution-only tasks in one invocation. The runner can build ResponseUnits for explicit `reply_only`/`no_change` tasks without creating a fictitious immutable document batch. `no_change` requires a confirmed current active Manuscript/SI anchor; `reply_only` uses `location_requirement=not_required` and never receives a fabricated page/line.

### Complete the round once, after all document-changing batches are published

```powershell
.\scripts\Complete-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -RevisionLedger "plan\revision_ledger.json" -ManuscriptTitle "Manuscript title" -Author "Corresponding Author" -PythonExe "D:\Software\Anacoda\python.exe"
```

The finalizer creates/reuses only a complete hash-current Working Clean set, shows final Marked/Clean locations and hashes for author review, requests semantic confirmation, performs final Word COM/PDF pagination/mapping, records manual locators when required, completes ResponseUnits, synchronizes Workflow task states and creates or resumes the Release Package after reconciliation.

If a table/non-body locator is required, the script pauses with the exact ResponseUnit and locator IDs. Prepare a manual locator input conforming to `schemas/manual-response-locations.schema.json`, using `examples/manual_response_locations.example.json` only as a structure. Replace every illustrative value with the true final Word/PDF locator; do not reuse its sample page numbers. On resume, the finalizer automatically reuses the Word/PDF directory recorded in `round_manifest.json`; pass `-ManualLocationsJson` and omit `-WordPdfOutputDirectory` unless you deliberately need to select that same recorded directory explicitly. Already-recorded manual locators are not applied a second time.

Working Clean and Word/PDF artifacts are reused only when complete and hash-current; partial/stale outputs are preserved. Release Package generation uses an immutable intent/receipt: the intent binds the exact Workflow/RevisionLedger path+hash snapshot and title/author. A partial package resumes in the same directory only while these bindings match; inconsistent generated files are retained as `.partial-<id>` artifacts before rebuilding. The completion receipt is written last and binds the Response Letter Markdown/DOCX, quality report and READY Release Manifest. A changed source Ledger or metadata requires a new release output directory. The package is not journal submission and does not replace the author's final Word GUI/PDF visual acceptance.

## Clean views, final Word/PDF locations and Response Letter

After **all** document-changing batches have been published, use the round's canonical paths:

```powershell
$preparedWorkflow = Join-Path $roundRoot "plan\workflow_ledger.prepared.json"
$revisionLedger = Join-Path $roundRoot "plan\revision_ledger.json"
$runs = Join-Path $roundRoot "runs"
$wordPdfFinal = Join-Path $roundRoot "output\word_pdf_final"
```

Create separate accepted-changes projections from the active Marked DOCX outputs:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger $revisionLedger
```

Working Clean Views are intermediate review projections, not final Word-layout Clean deliverables. Inspect Marked and Working Clean DOCX files together and check claims, sample identities, numerical values, units, figures/tables and cross-references across Manuscript and SI.

After the author confirms each ResponseUnit's wording and scientific/semantic consistency, record that review:

```powershell
python scripts/co_reviewer_cli.py confirm-response-units --ledger $revisionLedger --ids RU-T1-1 --semantic-review-confirmed --actor "Human Author"
```

On Windows with Microsoft Word installed and the optional Word/PDF dependencies, generate final Word-layout Clean DOCX files, PDFs and actual page/line locations:

```powershell
python scripts/co_reviewer_cli.py map-response-locations --ledger $revisionLedger --out-dir $wordPdfFinal
```

The page and line references must come from real Word COM pagination and corresponding PDF artifacts. XML paragraph indices are not page/line numbers. The mapper refreshes Word SEQ/REF/PAGE/PAGEREF fields, repaginates and checks native Figure/Table SEQ continuity. Unsupported chapter-reset/non-native numbering is marked for manual review; actual table-cell and non-body locators require explicit author-confirmed manual location records. Do not infer a page or line from paragraph indices or PDF text extraction.

For a table/non-body target, provide an explicit author-confirmed locator against the exact Word/PDF export:

```powershell
python scripts/co_reviewer_cli.py record-manual-response-locations --ledger $revisionLedger --word-map (Join-Path $wordPdfFinal "response_location_map.json") --input (Join-Path $roundRoot "plan\manual_locations.json") --out (Join-Path $wordPdfFinal "manual_location_map.json")
```

Only after final locations are attached and the author accepts readiness, complete each relevant ResponseUnit. A conceptual `reply_only` unit with `location_requirement=not_required` can be completed after semantic review without fabricating a manuscript location:

```powershell
python scripts/co_reviewer_cli.py complete-response-units --ledger $revisionLedger --ids RU-T1-1 --ready-for-submission-confirmed --actor "Human Author"
```

Create the final release package only after every active Patch is verified, every Workflow task/comment is resolved, and every ResponseUnit is completed:

```powershell
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --out-dir (Join-Path $runs "release-final") --manuscript-title "Manuscript title" --author "Corresponding Author"
```

The command writes the selected release directory containing `Response_to_Reviewers.md`, `Response_to_Reviewers.docx`, its DOCX quality report and `final_release_manifest.json`. It rechecks active pointers, immutable batch receipts, verified PatchOperation excerpts, response attribution, final DOCX/PDF hashes, Word page counts, SEQ audits, a deterministic Manuscript/SI Figure/Table caption-reference audit and quality reports. Unresolved/ambiguous references, duplicate captions, numbering gaps, out-of-order first mentions and cross-document same-label/different-caption collisions block release. Numeric values, units, sample identities and citation semantics remain covered by the explicit human scientific-review gate; the tool does not claim to infer scientific equivalence. If any gate fails, no manifest is produced; the incomplete release directory is retained, and the next attempt must use a new directory.

The Response Letter automatically quotes actual replacement/deleted text from each exact linked PatchOperation and places it below the author's response argument with its verified final document location. No model call is made to rewrite or repeat the manuscript body text. Draft/location-pending labels remain visible before final completion. Never invent response wording or page/line locations.


## V2.3.2 RC safety and author-facing fallback

### Exact-range structural gate

The existing paragraph-level structure classifier remains the first gate. If a paragraph is blocked solely because it contains paired bookmark ranges, the RC closeout adapter may prove an exact Bounded Safe Span only when the requested text is unique and maps to one contiguous ordinary Run sequence outside every bookmark interval. This returns `ALLOW_CANDIDATE`, not authorization. The historical revision, evidence, Token Lock, Protected Content, format, immutable Patch approval, Track Changes Apply and Verify gates remain mandatory. No bookmark or other Word structure may be moved or altered to manufacture an editable range.

All unsupported or uncertain structures remain blocked. Current automatic span support is deliberately narrow; Fields, Comment Ranges, hyperlinks, drawings, OMML, revision wrappers, malformed bookmarks, nested structures and targets crossing a protected boundary must use manual handling or clarification.

### Risk tiers and batch selection

The Workflow Ledger stores deterministic `risk_tier`, `risk_policy_version` and `risk_reasons`. These fields enter the task scope fingerprint:

- `TIER_0`: confirmed ordinary wording work in one document scope.
- `TIER_1`: science/numeric/unit/citation/reference/formatting changes, cross-document work or numbering. Prepare a separate high-risk batch and show it distinctly for author review.
- `TIER_2`: unresolved or ambiguous target/evidence, missing evidence or clarification required. Do not generate a Patch.

Batch construction never mixes tiers. The default route selects one safe tier at a time and holds Tier 2 out; explicit `--task-ids` must also be risk-homogeneous. Risk is recalculated after exact PatchOperations are added to the prepared Workflow Ledger. A change to risk classification changes the scope fingerprint and invalidates stale authorization.

For Tier 1 tasks, the author must make a separate explicit high-risk review acknowledgement when approving the Workflow task. Use both `--high-risk-review-confirmed` and `--high-risk-review-note` with `approve-workflow-tasks`. The CLI records reviewer identity/time and the note inside the Workflow task fingerprint. A recheck clears this evidence; renewed scope requires renewed Tier 1 review. This is in addition to Patch Approval and Semantic Review.

### Manual Suggestion artifacts

When Apply encounters a blocked structure, it writes a new, source-hash-bound JSON artifact to `<batch>/manual_suggestions/` (or beside a non-batch plan). The CLI prints the artifact path and SHA-256, then stops; Apply remains failed/blocked and does not continue to publication. The artifact is explicitly `not_applied=true` and `docx_write_performed=false`; it is not a Patch and cannot be mistaken for revised manuscript text.

The suggestion includes exact source text, document role/Word part, location hint, adjacent context, blocking structures/reason, missing evidence and a proposal only when evidence is sufficient. A missing scientific result removes the proposed edit and requires clarification. Manual intervention must be followed by a fresh, author-approved execution scope and the normal Patch lifecycle. The optional standalone command `create-manual-suggestion --input <diagnostic.json> --out <new.json>` follows the same no-overwrite rule.

### Citation and Word-field reference graph

The graph now distinguishes static numeric citation definitions/mentions from native Word field instructions. Static `[n]` citations may resolve only to a unique numbered reference-list definition. Citation ranges (for example `[1-3]`) and paragraphs mixing ordinary text with Word fields remain manual-review records. Native `REF`, `PAGEREF`, `SEQ`, `CITATION` and `ADDIN` fields are not misrepresented as static references; unresolved native-field references block package-wide numbering propagation. `PAGE`/`NUMPAGES` metadata fields are recorded but do not independently block unrelated numbering.

Prefer typed number-map keys `DOCUMENT::KIND::OLD_NUMBER`, such as `Manuscript::citation::1` or `Manuscript::figure::1`. The legacy `DOCUMENT::OLD_NUMBER` key is valid only when the graph contains exactly one reference kind with that number. Ambiguous, unresolved or duplicate resulting references block the whole propagation plan.

## Local automated checks on Windows

These commands are reserved for the unified acceptance stage after the end-to-end workflow implementation is complete. From Windows PowerShell 5.1, change to the repository root and run:

```powershell
.\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe"
```

The script compiles `scripts` and `tests`, runs the complete unit suite and checks CLI entry points. It writes a persistent log and prints its path. It does not automate Microsoft Word's visual/COM acceptance.

## Response Letter DOCX

The current response renderers consume ResponseUnits from the RevisionLedger. Use the round's RevisionLedger rather than a separately maintained legacy revision plan:

    python scripts/co_reviewer_cli.py render-response-docx --revision-ledger revision_ledger.json --out Response_to_Reviewers.docx
    python scripts/co_reviewer_cli.py render-response --revision-ledger revision_ledger.json --out Response_to_Reviewers.md

The renderer preserves draft/location-pending/completed distinctions, reviewer attribution and linked document locations. Missing reply text and missing PDF page/line mappings remain explicit; the renderer never invents them. Existing output files are not overwritten; choose a new path for each generated version.


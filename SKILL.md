---
name: co-reviewer
description: Coordinates academic peer-review revisions across reviewer comments, manuscript and Supporting Information with traceable tasks, human approval and safe Word-native Track Changes.
---

# Co_Reviewer — Multi-document peer-review revision

## Role boundary

Co_Reviewer manages a revision round. It does not invent experiments, results, numerical values, citations or scientific claims. Use supplied evidence and clearly identified verified sources. A manuscript-writing agent may help with scientific prose; Co_Reviewer owns reviewer-response mapping, revision tracking, cross-document synchronization and delivery validation.

## Operating principles

1. **Supervisor–Worker separation:** Antigravity is Supervisor; OpenCode Worker performs bulk DOCX inspection, deterministic edits and package validation.
2. **Context discipline:** Supervisor reads the Markdown digest, selected excerpts and JSON task summary, not the entire DOCX by default.
3. **Human approval:** No edits are applied until the user reviews the revision plan. The apply command independently enforces this gate.
4. **Fail closed:** Ambiguous targets or unsupported Word structures are reported, not modified by approximate matching.
5. **Incremental work:** A request to modify only one figure, paragraph, reviewer item or SI entry touches only relevant targets. Reuse prior inventory when source files are unchanged.
6. **Auditable output:** Preserve inputs, write separate revised copies, and return a report linking each revision item to its targets.
7. **Production-Skill data isolation:** Keep this Skill domain- and dataset-agnostic. Never copy test fixtures, manuscript-specific wording, measured values, scientific symbol strings, sample identifiers, citation examples, or figure/table-specific content into production instructions. Keep concrete synthetic examples in tests/fixtures; use neutral placeholders in this Skill.

## V2.3.2 RC safety, blocked operations and risk tiers

### Bounded Safe Span is only a structural candidate

The structure classifier may return `ALLOW_CANDIDATE` for an exact target that maps to one contiguous sequence of ordinary Word Runs outside every paired bookmark range. This narrow path may permit ordinary text elsewhere in the same paragraph when a bookmark range is present. It must never modify, copy, delete, reorder or rebalance bookmark/comment markers.

A Bounded Safe Span result is **not write authorization**. The operation must still pass the historical-revision gate, evidence-linked Authorized Change, Token Lock, Protected Content, mixed-format mapping, immutable Patch approval, native Track Changes Apply and exact Verify gates. The current automatic range adapter supports direct ordinary Runs and bookmark intervals only. Fields, Comment Ranges, hyperlinks, drawings, OMML, revision wrappers, malformed/unpaired markers, nested/unsupported structures and any target that cannot be uniquely mapped remain fail-closed.

### Deterministic Risk Tiers

Every prepared task receives a code-derived risk classification. Never copy a model-suggested tier or lower a tier manually:

- `TIER_0`: ordinary, single-scope wording changes with confirmed target/evidence and no scientific/reference/formatting signals.
- `TIER_1`: numeric/scientific tokens, units, citations, references, formatting changes, numbering, global or cross-document consistency, or multiple document roles. Keep these in a separate high-risk batch and show the exact operands and impact.
- `TIER_2`: ambiguous/unresolved target or evidence, missing linked evidence, unresolved questions or a non-executable disposition. Do not create a Patch; obtain clarification or keep the item blocked.

Automatic batch construction selects one non-Tier-2 risk tier at a time. Explicit task selection must also be homogeneous. The selected tier, policy version and deterministic reasons are part of the Workflow task scope fingerprint. Preparation recalculates the tier after exact PatchOperations are supplied, so a numeric or reference token in a `find/replace` operand can raise a previously low-risk task to Tier 1. Any later scope change invalidates preflight/approval fingerprints and requires renewed authorization.

For Tier 1 tasks, the author must provide a separate explicit risk-review confirmation when approving Workflow tasks. Include `--high-risk-review-confirmed` and a concise `--high-risk-review-note` describing the scientific, numeric, reference, formatting or cross-document scope reviewed. The evidence is stored in the Workflow task and bound into its scope fingerprint. After any upstream change/recheck, the prior Tier 1 review is cleared and must be renewed.

### Blocked-edit Manual Suggestions

A structure-safety failure may produce an independent JSON artifact under the immutable batch's `manual_suggestions/` folder. It contains the source-document SHA-256, Word part, stable/location hint, exact source excerpt, context anchors, blocking reason, involved structures, missing evidence and (only when evidence is sufficient) a proposed edit.

A Manual Suggestion is diagnostic only: `not_applied=true`, `docx_write_performed=false`. It is not a Patch, does not alter a DOCX, cannot be approved as an applied edit, and must never enter Marked/Clean or a ResponseUnit as completed work. If scientific evidence is missing, the suggestion must omit the proposed edit and ask for clarification rather than invent wording. Inspect the exact target in Word and create a new, user-approved scope after manual handling.

For an external diagnostic JSON, the optional CLI is:

```powershell
python scripts/co_reviewer_cli.py create-manual-suggestion --input blocked_target.json --out manual_suggestion.json
```

The output must be a new path; an existing artifact is never overwritten.

### Citation and native Word field handling

The package reference graph recognizes static numeric citations and numbered reference-list entries, separately from Figure/Table/Equation/Scheme/Section references. Ambiguous citation ranges are retained as manual-review items rather than expanded by guesswork.

Native Word `REF`, `PAGEREF`, `NOTEREF`, `SEQ`, `CITATION`, `ADDIN` and other unclassified field instructions are reported as native-field references, not static text. The current release candidate does not provide a general Field/Citation writing Adapter. Package-wide numbering propagation must stop when a potentially affected native field, unresolved mention or range cannot be uniquely mapped. Page metadata fields such as `PAGE` and `NUMPAGES` are recorded separately and do not by themselves block unrelated reference numbering.

For renumbering, use a typed key that identifies the document role, reference kind and identifier. Accept a legacy untyped key only when it resolves uniquely to one reference kind. Never rely on an untyped key when different reference kinds can share an identifier.

Bibliography paragraph insertion is supported through the same typed Patch/Revision Plan transaction as ordinary paragraph insertion. The insertion adapter currently accepts only a unique ordinary-text anchor paragraph directly under `w:body` or `w:tc`, in `word/document.xml`; it inherits that paragraph's paragraph/run formatting, adds a native tracked `w:ins`, and records a deterministic operation bookmark. It refuses duplicate operation markers, unsupported Word structures, ambiguous anchors, or unresolved historical revisions. Every insertion requires an explicit operation ID, before/after position, exact inserted text and human approval.

For a coordinated bibliography change, use `build-package-reference-graph` to inventory definitions and mentions, then author a graph-bound reference transaction specification conforming to `schemas/reference-transaction.schema.json`. The graph must retain exact source paths and SHA-256 values. Every operation must supply a registered `target_ref` with `target_identity_mode: stable_target_ref`; a caller may use `legacy_p_idx_explicit` only as an explicit migration compatibility choice, never as an implicit fallback. Compile it with `compile-reference-transaction --graph <graph.json> --spec <transaction.json> --out <new-plan.json>`. One transaction Patch groups the required bibliography paragraph insertion(s), existing bibliography-entry edits and every graph-linked in-text citation occurrence update. The compiler checks source document roles/locations, exact graph IDs, explicit reviewer/evidence provenance and exact authorization for changed protected citation/number tokens. It blocks partial renumbering, graph-ambiguous or manual-review occurrences, duplicate same-paragraph text anchors, grouped/ranged citations and native Word fields that lack a supported writer. It never approves or edits a DOCX.

Run the resulting plan through the ordinary `preflight-plan`, explicit author `approve-plan`, `apply` and `verify` commands. Verify rebuilds the package Reference Graph from the actual output DOCX files and checks the expected added/updated definitions and linked mention counts; a failed graph check fails the Verify gate. Do not use the deprecated `renumber-package-graph` shortcut; it remains disabled. The graph is an analysis layer, while the reference transaction compiler coordinates executable edits through the normal Patch lifecycle.

## Workflow

### Stage 1 — Start a review round and inventory

On Windows, start a round workspace from the repository root. This copies the supplied DOCX files into an isolated input folder, preserves the originals, creates the plan/reports/output/runs/cache folders, and runs one inventory-and-audit pass. All generated reports stay inside the round workspace rather than appearing in the repository root:

```powershell
.\scripts\Start-CoReviewerRound.ps1 `
  -ReviewersDocx "<path-to-reviewer-comments.docx>" `
  -ManuscriptDocx "<path-to-manuscript.docx>" `
  -SupportingInformationDocx "<path-to-supporting-information.docx>" `
  -PythonExe "<path-to-python.exe>"
```

Copy the exact `[WORKSPACE]` path printed by the starter into `$roundRoot`. Its `round_manifest.json` gives the exact input copies, hashes, inventory/audit paths and standard directories. Supporting Information is optional. The workspace is unique; the starter refuses to reuse an existing round folder. On non-Windows systems, create the same folder structure and call the `audit` CLI directly, directing every output into the round's `reports/` folder.

The starter runs the same `audit` command once. The command parses each source DOCX once per SHA-256/version and stores the resulting paragraph/comment/Run-format inventory in the local cache. The deterministic audit then reuses those inventories for native comment anchors, cross-document matching and figure/table consistency. It does not send entire DOCX files to the model.

Immediately after that pass, the starter builds `reports/document_semantic_format_profile.json` from the existing `workspace_inventory.json`. This step does **not** reopen or reparse any DOCX. The per-document profile is also embedded into the inventory so Workflow Preparation can validate later format-source references against the exact source SHA.

The inventory includes ordinary reviewer-document paragraphs and native Word comments. For each native comment, the selected text is reconstructed from matching `commentRangeStart` and `commentRangeEnd` markers in the same Word part. The comment body is separately retained with its index in `word/comments.xml`, so it remains citable even when no selected range is recoverable. A reference-only comment or missing marker is not assigned a guessed selection; its containing paragraph is separate. One comment is recorded once even when its range crosses paragraphs.

The audit confirms a cross-document target only when the native selection text matches exactly and uniquely in Manuscript or SI. Token-overlap matches are candidates only; ambiguous or missing targets remain unresolved. Figure/table auditing reports caption order, first-mention order, duplicates, gaps, unresolved references and unreferenced captions. It is diagnostic only and never renumbers documents.

### Stage 1.1 — Build and reuse the document semantic/format profile

The initial audit also creates `reports/document_semantic_format_profile.json`. Read this profile during the initial manuscript analysis; do not reopen the entire original DOCX later just to rediscover formatting.

The profile deliberately separates two zones:

- `body_occurrences`: independent instances of single-letter symbols, Greek letters, superscript/subscript characters, mathematical symbols and directly formatted Runs, each with its own occurrence ID, position, paragraph style, format properties and surrounding context.
- `reference_occurrences` and `reference_entries`: bibliography-only instances, paragraphs and reference-list style groups. These are a separate formatting system. Never use a body occurrence as a reference-list formatting source, or a reference entry as a body formatting source.

The profile records every occurrence separately. Occurrences are never merged or assigned one global format merely because they share the same visible character; their contexts, positions and format properties remain distinct.

For body edits, choose a source occurrence only when it has the same contextual semantic role as the new content; glyph identity alone is insufficient. For a bibliography edit, compare the corresponding bibliographic field against comparable entries in the same reference list. Use the profile's style groups and comparable entries; never select the most frequent pattern automatically when multiple patterns exist.

During the initial manuscript analysis, create `reports/document_semantic_annotations.json` conforming to `schemas/document-semantic-annotations.schema.json`. This is a sparse, occurrence-specific semantic archive bound to the profile's workspace fingerprint and each document's source/profile hashes. Annotate only occurrences that may need semantic disambiguation or reusable special formatting; do not spend tokens classifying ordinary prose characters.

Each annotation must identify the exact `occurrence_id`, its contextual `semantic_role`, short evidence from the archived context or an explicit definition, and confidence. Use `semantic_group` only when multiple occurrences represent the same concept. Never merge occurrences merely because their visible glyph is the same. For references, identify bibliographic field roles (author, title, journal, year, volume, pages, DOI, etc.) separately from body symbols. The reference field format source must still come from another comparable entry in the same reference list.

The profile stores direct Run properties and surrounding text. The annotation file stores the initial semantic interpretation. Later tasks must consult these two artifacts rather than rereading the whole DOCX. When choosing a format source, first find an annotation with the same contextual semantic role/group, then select its corresponding occurrence ID. If no matching annotation exists, use the archived contexts to make a bounded decision for that operation; do not rescan the original DOCX or invent format properties.

If a new character needs a non-default format, use a profile occurrence as its source. In the executable scope, declare `two_pass: true`, provide `format_authorization` with the target document's per-document `profile_fingerprint` (not the aggregate workspace profile fingerprint) and the zone, and add `format_operations` with `source_occurrence_id`, `find`, `scope_find` (the exact replacement text) and `scope_offset`. Do not invent the `properties` values: Workflow Preparation resolves those values from the cited source occurrence. The code rejects unknown IDs, stale profiles, unsupported direct properties, cross-zone sources and paragraph-style mismatches. For text that needs no special formatting, omit FormatChange and allow the existing source paragraph/run style to apply. For `vertAlign=subscript` or `vertAlign=superscript`, `find` must be an exact local token and must exactly equal the referenced occurrence's `text`; it must contain no whitespace. Never apply vertical alignment to a multiword phrase or sentence. Format separate local tokens with separate operations.

The reference profile reports multiple style groups without choosing a majority. If references are inconsistent, use a structurally and semantically comparable entry; do not silently normalize the whole list during a one-entry correction.

The command also writes a bounded `review_decision_queue.json` conforming to `schemas/review-decision.schema.json`. Each comment packet contains the native anchor once, compact match coordinates, and at most 8 context excerpts of at most 1,200 characters each. Use `--comment-ids <comment-id>` when only one comment needs semantic analysis; the full audit report remains complete while the AI queue contains only the selected item. Use this as the only model input for semantic decisions, one comment at a time.

For each selected item, the Supervisor must propose a structured decision with: `disposition` (use the schema values `manuscript`, `si`, `reply_only`, `manuscript_and_si`, `no_change`, or `clarification_needed`), evidence-based `rationale`, exact `proposed_targets` (document, part, paragraph index, action and source excerpt), `response_outline`, `supporting_evidence`, `missing_evidence_or_questions`, and a confidence estimate. Mark the proposal as `proposed` or `needs_clarification`; `requires_user_approval` must remain true. Do not create/apply revision targets merely because a candidate match exists. Show the proposed decision to the user and create the revision plan only after the user accepts the disposition and target scope.

The XML inventory provides stable part/paragraph/paraId coordinates, not rendered page and line numbers. Word pagination and line wrapping depend on layout. Do not invent page/line values from XML; obtain them only after rendering/layout inspection in Word or report the stable paragraph coordinates instead. The model must decide whether the response belongs in Manuscript, SI, Reply only, multiple documents, no change, or needs clarification; it must cite the supplied evidence and leave every decision pending until the user approves a revision plan. Do not infer scientific results or invent edits.

### Stage 1.5 — Historical revision baseline and target-scoped resolution

Start-CoReviewerRound.ps1 also runs revision-baseline on each copied Reviewers/Manuscript/SI DOCX and records the report paths and hashes in round_manifest.json. The command is zero-model-call and never modifies the source. Inspect reports/Manuscript_revision_baseline.json and, when present, reports/SI_revision_baseline.json before semantic discovery or any edit.

After exact candidate targets have been mapped, but before expensive manuscript discovery and before writing, run the target-scoped gate against the corresponding role's baseline. The target JSON must use exact Word part and zero-based p_idx values:

```powershell
$baseline = Join-Path $roundRoot "reports\Manuscript_revision_baseline.json"
$targets = Join-Path $roundRoot "plan\historical_targets.json"
$gate = Join-Path $roundRoot "reports\Manuscript_revision_gate.json"
python scripts/co_reviewer_cli.py targeted-revision-gate --inventory $baseline --targets $targets --out $gate
```

If the gate reports no overlap, continue. If it reports overlapping history, stop and show the author the exact structure IDs, Word parts, paragraphs and element types. Obtain an explicit choice of ACCEPT_EXISTING, REJECT_EXISTING, or SELECTIVE_ACCEPT_CONFLICTS; never infer that choice from model confidence. The selective list contains baseline structure_id values, not Word's numeric w:id.

For a supported conflict, resolve to a new DOCX path and retain the JSON evidence:

```powershell
$resolved = Join-Path $roundRoot "input\resolved\Manuscript.docx"
$resolutionReport = Join-Path $roundRoot "reports\Manuscript_history_resolution.json"
python scripts/co_reviewer_cli.py resolve-historical-revisions --marked (Join-Path $roundRoot "input\Manuscript.docx") --inventory $baseline --targets $targets --policy ACCEPT_EXISTING --out $resolved --report $resolutionReport
```

Replace the policy only with the author's explicit choice. For SELECTIVE_ACCEPT_CONFLICTS, additionally pass --selective-ids with a JSON-array file. The resolver checks that the baseline SHA matches the exact input, writes a new DOCX, and verifies unrelated historical structures remain unchanged. It supports native w:ins/w:del, paired move wrappers and standard property snapshots with valid prior-state payloads. Comments/anchors, paragraph-mark topology deletions, table-structure changes, numbering changes, unsupported snapshots, ambiguous paths and malformed move pairs remain blocked. Never delete or flatten those structures manually.

When any source document is resolved, preserve the original round as evidence and start a fresh round using the new resolved copy as the Manuscript/SI input. This regenerates the audit, decision queue, source hashes and round manifest; do not reuse the old Decision Plan, inventory or approval fingerprints.

Use `--comment-author` only when filtering by the comment author's name. Do not filter comments by the manuscript author's name. `--refresh-cache` forces re-parsing; otherwise a changed DOCX SHA-256 invalidates only that file's cached inventory. Matching, numbering and decision-queue generation are cheap deterministic passes over the cached inventories and can be repeated without reopening the source DOCX files.

### Stage 2 — Build and validate a pending Decision Plan

After the user accepts the proposed dispositions and target scope, create one revision_decision_plan.json for the round, conforming to schemas/revision-decision-plan.schema.json.

Each task must be atomic, retain every source reviewer comment ID, link exact source evidence, identify all affected documents/Word parts/paragraphs, declare dependencies, and keep status "pending" and requires_user_approval true. Include exact evidence for each Manuscript or SI target. A reviewer-comment excerpt is not a substitute for target-document evidence. Do not include executable Patch operations in the Decision Plan; that plan records the scientific/structural decision and evidence scope.

Validate provenance and persist the initial Workflow Ledger in one pass:

    python scripts/validate_decision_plan.py --plan "$roundRoot\plan\revision_decision_plan.json" --audit "$roundRoot\reports\review_audit_report.json" --inventory "$roundRoot\reports\workspace_inventory.json" --workflow-ledger-out "$roundRoot\plan\workflow_ledger.json" --round-manifest "$roundRoot\round_manifest.json"

Use --identity-manifest document_identity_manifest.json when a current parser-backed identity manifest is available. Do not invent a TargetRef or reuse an old identity manifest against changed source hashes. The validator must pass before the Workflow Ledger is used. If evidence, comment coverage, source hashes, target coordinates or dependency order fail, correct the plan from the actual inventory and rerun validation; do not weaken the validator.

### Stage 2.5 — Prepare the executable scope without editing the Workflow Ledger by hand

Do not hand-edit `workflow_ledger.json`. Keep the validated Decision Plan and initial Workflow Ledger as immutable inputs. Create a separate `workflow_execution_scope.json` under the round's `plan/` folder. It contains only the author-reviewed execution details for tasks that are ready to proceed:

- `schema_version: "1.0"`, the exact `revision_round_id`, and the SHA-256 of the validated initial Workflow Ledger (`workflow_ledger_sha256`).
- For each task, its existing `task_id`, `patch_id`, reviewed `proposed_response`, and exactly one `patch_operations` entry per mutating Decision Plan target.
- Each PatchOperation supplies an `operation_id`, `document`, Word `part`, `p_idx`, exact `find`/`replace`, evidence IDs attached to that same target, and source reviewer comment IDs. Do not supply input/output paths or source hashes; the preparation command resolves those from the current active-document pointers.
- When newly inserted text contains characters whose format must be taken from a semantically equivalent source occurrence, add `two_pass: true`, `format_authorization: { intent, semantic_profile_sha256, format_zone }`, and `format_operations`. Each format operation must identify the exact inserted substring with `find`, `scope_find` equal to the full replacement, `scope_offset`, and a `source_occurrence_id` from the current document profile. Do not author the properties manually; preparation fills them from the referenced occurrence. The source and target must be in the same document zone (body or references) and paragraph style.
- Optional `response_unit_specs` explicitly classify comments as `revised`, `reply_only`, `no_change` or `clarification_needed`. For `no_change`, include a current evidence `anchor_evidence_id` and use `existing_anchor_required` or `manual_required`; for `reply_only`, use `not_required`.
- Optional `reviewer_map` overrides. By default the preparation command uses the native reviewer author from the source inventory and falls back to `Reviewer` only when the author label is unavailable.

The JSON structure is defined by `schemas/workflow-execution-scope.schema.json`; use `examples/workflow_execution_scope.example.json` as a structural template and replace every illustrative ID, target and text with current-round evidence. The preparation command verifies the Workflow Ledger file hash, the inventory path/SHA-256 recorded by the root round manifest (starter or latest refresh), task IDs, exact target/evidence matching, operation/comment ownership and ResponseUnit disposition consistency. After creating a **new** prepared Workflow Ledger, reviewer map and preparation report, it records their paths and hashes in the root round manifest. If `--round-manifest` points to a dedicated `refresh_manifest.json`, also supply `--round-root-manifest` to update the root index. It does not authorize tasks or edit a DOCX:

```powershell
$workflow = Join-Path $roundRoot "plan\workflow_ledger.json"
$preparedWorkflow = Join-Path $roundRoot "plan\workflow_ledger.prepared.json"
$scope = Join-Path $roundRoot "plan\workflow_execution_scope.json"
$inventory = Join-Path $roundRoot "reports\workspace_inventory.json"
$reviewerMap = Join-Path $roundRoot "plan\reviewer_map.json"
$prepReport = Join-Path $roundRoot "reports\workflow_preparation_report.json"
python scripts/co_reviewer_cli.py prepare-workflow-execution --workflow-ledger $workflow --execution-scope $scope --inventory $inventory --round-manifest (Join-Path $roundRoot "round_manifest.json") --round-root-manifest (Join-Path $roundRoot "round_manifest.json") --out $preparedWorkflow --reviewer-map-out $reviewerMap --report $prepReport
```

Review the preparation report, proposed responses, exact operations and reviewer attribution with the author. If anything changes, update the execution-scope file and rerun preparation to a new output path. Never edit task status, approval events, source hashes or fingerprints manually.
This is the manual/low-level route. When using the PowerShell batch orchestrator below, skip this direct preparation call and let `Invoke-CoReviewerBatch.ps1` prepare once from the supplied ExecutionScope/Inventory. Do not prepare the same scope twice. If the direct CLI route was already used, invoke the PowerShell runner with the registered prepared Workflow Ledger and Reviewer Map, and omit `-ExecutionScope`/`-Inventory`.

Only after the author accepts the exact task scope, authorize the selected tasks using the **prepared** Workflow Ledger:

```powershell
python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger $preparedWorkflow --ids <task-id-1> <task-id-2> --plan-approved --actor "Human Author"
```

Use actual task IDs. This is an explicit human gate; it does not edit a DOCX. A dependent task remains planned until its dependencies are verified. Do not authorize it prematurely.

### Stage 2.6 — Build an immutable Patch batch

Build typed Patch/ResponseUnit records from the current conflict-free ready batch. Use the prepared Workflow Ledger and the round's `runs/` directory:

```powershell
$revisionLedger = Join-Path $roundRoot "plan\revision_ledger.json"
$runs = Join-Path $roundRoot "runs"
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger $preparedWorkflow --reviewer-map $reviewerMap --runs-dir $runs --out $revisionLedger
```

The CLI validates the ready task set and candidate Patch/ResponseUnit records before it creates the immutable batch intent. This avoids leaving an intent-only directory when the submitted execution data or Ledger merge is invalid. It then prints the unique Batch ID and full directory. Copy that exact path into `$batchDir` and export the plan:

```powershell
$batchDir = "<full path printed as [BATCH DIRECTORY]>"
python scripts/co_reviewer_cli.py export-revision-plan --ledger $revisionLedger --batch-dir $batchDir
```

The exported plan is only an adapter for the existing Word-native Track Changes writer; the Workflow Ledger and RevisionLedger remain the round's sources of truth. Later batches reuse the same two Ledger paths and the same `runs/` root, while each receives a new immutable batch directory and new plan snapshots.

### Recommended Windows execution path

For an ordinary review round, use the round orchestrators below rather than manually chaining every CLI stage. The underlying CLI commands documented later remain the advanced/manual recovery path.

**Publish one document-changing batch** after the user has approved the Decision Plan and exact execution scope:

```powershell
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "plan\workflow_ledger.json" -ExecutionScope "plan\workflow_execution_scope.json" -Inventory "reports\workspace_inventory.json" -TaskIds <task-id> -PythonExe "<path-to-python.exe>"
```

The script creates a new prepared Workflow Ledger, Reviewer Map and preparation report; authorizes the selected task; builds one immutable batch; exports/preflights the plan; requests exact Patch approval; applies Word-native Track Changes; verifies the output and untracked-text audit; asks for semantic review; synchronizes the Workflow Ledger; and requests explicit publication approval. It reports the actual generated paths and never retries or overwrites a failed batch.
Before preparing the next scope, it invokes `Reconcile-CoReviewerRound.ps1` against the round's Workflow Ledger, RevisionLedger and fixed `runs/` root. Refresh and finalization invoke the same reconciler. The round manifest is a recoverable index: immutable batch intent/scope, Workflow events, active pointers, hash-bound receipts and transaction intents are the commit evidence. A lost PowerShell response after publication does not require rerunning Apply; reconciliation resumes/validates the same batch and restores the manifest history.

A `verified_unpublished` batch can be resumed only through an explicit `RESUME VERIFIED BATCH` confirmation. The existing verified plan and DOCX outputs are reused; missing semantic confirmation and Workflow state records are made idempotent, then publication has its own explicit confirmation. Applied-but-unverified or approved-but-unapplied batches instead require explicit `RECOVER FAILED BATCH`; their artifacts stay preserved and the invalidated Workflow DAG must be refreshed before new work. No batch is silently reapplied or automatically abandoned.

The two-Ledger Workflow sync uses staged after-images, immutable intent, a pending marker and a completion receipt. Recovery is forward-only and refuses unknown current hashes. If only the intent exists because the process stopped before writing the pending marker, it is promoted only when it is unique and both ledgers still match the recorded before-hashes.


For another batch, pass the latest prepared Workflow Ledger and Reviewer Map reported by the previous invocation, while keeping the same RevisionLedger and `runs/` root:
The batch runner records the current prepared Ledger/map, RevisionLedger, runs directory and immutable publication history in `round_manifest.json`. The refresh and finalization entry points also record refresh inventories, final Word/PDF outputs and Release Package paths. All round inputs and outputs must remain inside the round workspace.


```powershell
.\scripts\Invoke-CoReviewerBatch.ps1 -RoundRoot $roundRoot -WorkflowLedger "<current prepared Workflow Ledger>" -ReviewerMap "<current Reviewer Map>" -TaskIds <task-id> -PythonExe "<path-to-python.exe>"
```

If a dependency is marked `recheck_required`, first run `Refresh-CoReviewerRound.ps1` against the latest published active document pointers. The resulting `refresh_manifest.json` binds the new audit/inventory files by path and SHA-256; use it with `validate_decision_plan.py --round-manifest` and also pass the root round manifest through `--round-root-manifest`, so the newly generated Workflow Ledger path/hash is registered before the next Reconcile or batch. For normal dependent-task execution, author a new execution scope using that refresh inventory and the current prepared Workflow Ledger SHA-256, then call `Invoke-CoReviewerBatch.ps1` with the new `-ExecutionScope` and `-Inventory`. The script prepares new output Ledger/map/report files. Do not reuse old paragraph coordinates or approvals. Document-changing and resolution-only tasks are separate invocations.

Resolution-only tasks are supported: the batch entry point records their ResponseUnits in the shared RevisionLedger without manufacturing an immutable document batch. Explicit `no_change` anchors bind to their current active Manuscript/SI document; `reply_only` units do not require fake document edits or final locations.

**Finalize once all document-changing batches have been published:**

```powershell
.\scripts\Complete-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger "<latest prepared Workflow Ledger>" -RevisionLedger "plan\revision_ledger.json" -ManuscriptTitle "<manuscript-title>" -Author "<corresponding-author>" -PythonExe "<path-to-python.exe>"
```

The finalizer reuses only a complete hash-current Working Clean set; otherwise it creates the projections once. It displays final Marked/Clean paths and hashes, asks for complete semantic review, performs Word COM pagination/PDF export and location mapping, records manual table/non-body locators when required, completes ResponseUnits, synchronizes Workflow task states and creates or resumes a Release Package. It reconciles outstanding batch and Workflow transactions before consuming active state.

For manual table/non-body locations, the script pauses and reports the exact ResponseUnit/locator IDs. Provide an author-confirmed JSON conforming to `schemas/manual-response-locations.schema.json`; `examples/manual_response_locations.example.json` is structural only, and its sample page numbers must not be reused. On resume, the finalizer automatically reuses the Word/PDF directory recorded in `round_manifest.json`; pass `-ManualLocationsJson` and omit `-WordPdfOutputDirectory` unless you deliberately need to select that same recorded directory explicitly. It never reapplies manual locators already committed to the RevisionLedger. A Release Package has its own immutable `release_package_intent.json` and final `release_package_receipt.json`. The intent binds the exact round, Workflow/RevisionLedger paths and hashes, manuscript title and author. A matching partial package resumes in that directory; mismatched generated artifacts are preserved with `.partial-<id>` suffixes before rebuilding. The completion receipt is written last and binds the four delivered files by SHA-256. A different Ledger snapshot or title/author requires a new release directory. The generated package is not submitted to a journal. Final Word GUI/PDF visual inspection remains a separate author acceptance step.

### Stage 3 — Low-level CLI alternative: preflight and approve the exact immutable Patch batch

Use the `$batchDir` captured in Stage 2.6. Each stage creates a new immutable plan snapshot in that directory:

```powershell
python scripts/co_reviewer_cli.py preflight-plan --plan (Join-Path $batchDir "revision_plan.json") --revision-ledger $revisionLedger
```

Review the preflight output, exact target operations, parent document hashes and planned Marked output paths. If preflight succeeds, approve the exact **Patch IDs** (not the Workflow task IDs) from the preflighted snapshot:

```powershell
python scripts/co_reviewer_cli.py approve-plan --plan (Join-Path $batchDir "revision_plan.preflighted.json") --ids <patch-id> --revision-ledger $revisionLedger --actor "Human Author"
```

Replace `<patch-id>` with the IDs printed in the batch plan. Both task-scope authorization and exact Patch approval are required. Neither command edits a DOCX. If the source file or operands change, rebuild and re-authorize the affected task scope; do not reuse stale approval.

### Stage 4 — Low-level CLI alternative: apply approved changes using native Word Track Changes

Apply only the approved snapshot:

```powershell
python scripts/co_reviewer_cli.py apply --plan (Join-Path $batchDir "revision_plan.approved.json") --revision-ledger $revisionLedger --author "<author-name>"
```

The CLI writes a separate Marked DOCX for each affected document role under the immutable batch's `documents/` folder and creates `revision_plan.applied.json` plus an Apply report. Inputs are never overwritten. Every textual change must be native Word Track Changes: deletions inside `w:del/w:delText` and insertions inside `w:ins/w:t`. Font color or direct XML text replacement is not a substitute.

Never use python-docx, raw XML text replacement, another writer, or Word with Track Changes disabled to edit a Marked deliverable. Unsupported targets must be refused and reported.

### Stage 5 — Low-level CLI alternative: verify, synchronize the DAG and publish the batch

Verify the actual applied snapshot:

```powershell
python scripts/co_reviewer_cli.py verify --plan (Join-Path $batchDir "revision_plan.applied.json")
```

Mechanical verification checks exact revision IDs and deleted/inserted text, whole-document untracked-text integrity, supported native subscript/superscript formatting and the Apply report. It does not judge scientific correctness.

Only after the author has reviewed the revised Manuscript/SI and confirmed scientific meaning/cross-document consistency, record verification against the exact Patch IDs:

```powershell
python scripts/co_reviewer_cli.py confirm-verified --plan (Join-Path $batchDir "revision_plan.applied.json") --revision-ledger $revisionLedger --ids <patch-id> --semantic-review-confirmed
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --batch-dir $batchDir
```

Publication atomically moves the Workflow Ledger's active-document pointers to the verified Marked DOCX outputs. Do not publish before every Patch in the batch is verified and the Workflow Ledger is synchronized. A later batch must be built from the newly published active pointers, never from the original source DOCX. Continue until all in-scope document-changing tasks are verified or explicitly blocked/deferred. Preserve the same Workflow Ledger, RevisionLedger and `runs/` root throughout the round.

### Recheck dependent tasks against the current active documents

After an upstream batch is verified and published, `sync-workflow-ledger` can mark dependent/conflicting tasks `recheck_required`. Before re-authorizing a document-changing task, run `scripts/Refresh-CoReviewerRound.ps1 -RoundRoot $roundRoot -WorkflowLedger $preparedWorkflow -BatchLabel <published-batch-id> -PythonExe <python-path>`. It verifies each current active file against its Ledger SHA, then asks the Audit CLI to re-check the same Workflow Ledger pointers. Only the verified active versions may contain native text insertion/deletion markup; formatting/move revisions and all comment structures remain blocking. The initial round's clean-baseline gate is unchanged. The script audits the latest Manuscript/SI Marked DOCX with the unchanged Reviewers document and writes all new inventory/audit outputs to `reports/after-batch-<id>/`.

Build a new `workflow_execution_scope` from `examples/workflow_execution_scope_refresh.example.json`. Include both `evidence` and `targets` for each rechecked document-changing task. Evidence IDs must be new; each evidence row must match a paragraph/part in the newly generated inventory and have `match_status=confirmed`. Each refreshed target references that new evidence. Each PatchOperation must use the new target coordinates and an exact `find` excerpt from the current paragraph. Refresh `target_refs` when the task uses persistent identity references.

Bind `workflow_ledger_sha256` to the current prepared Workflow Ledger after the upstream publication/synchronization. Run `prepare-workflow-execution` with the current prepared Ledger as input, the root `round_manifest.json` (which records the latest-refresh inventory path/hash), and a **new** output path for the next prepared Ledger/reviewer map/report. The command preserves the earlier evidence history, appends the fresh evidence, updates active `source_versions` and target groups, and leaves the task in `recheck_required` with approval still pending. A `no_change` ResponseUnit must replace its old anchor with a newly refreshed `anchor_evidence_id`; only a pure `reply_only` unit is exempt from document evidence refresh. Review the new scope, explicitly re-authorize the now-eligible task, then build the next batch with the same RevisionLedger and runs directory. Never hand-edit active pointers, source versions, status or approval fingerprints.

### Stage 6 — Prepare Clean views, final Word/PDF locations and Response Letter

After the final Patch batch is verified and the Workflow Ledger is synchronized, create a separate accepted-changes projection for review:

    python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json

This is an intermediate Working Clean View, not the final publication Clean DOCX. It accepts tracked insertions, rejects deletions, removes comment/revision markup, and preserves the Marked source. Inspect the Marked DOCX and Working Clean View together; do not overwrite either.

Review the complete cross-document result: Manuscript, SI, figures/tables/references, numerical values, units, sample identifiers, and every proposed reviewer response. Only after the author confirms the response wording and its semantic consistency, record that review:

    python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids <response-unit-id-1> <response-unit-id-2> --semantic-review-confirmed --actor "Human Author"

Use the actual ResponseUnit IDs. A semantically reviewed ResponseUnit is not yet a submission-ready response.

On Windows with Microsoft Word installed, generate the final Word-layout Clean DOCX, PDF and evidence-backed page/line map:

    python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final

This step requires the hash-bound Working Clean Views and real Microsoft Word COM pagination. It exports final-clean DOCX/PDF files, verifies the target phrases against the PDFs, and records actual page/line locations. Do not calculate page or line values from XML or estimate them from paragraph counts. If Word COM is unavailable or a target is in an unsupported structure (including a table cell), report the blocker and leave the ResponseUnit incomplete. record-response-locations is for attaching a separately generated, independently validated location map; do not fabricate one.

Only after every required ResponseUnit has semantic review and final PDF location evidence, and the author confirms readiness, mark the selected units complete:

    python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids <response-unit-id-1> <response-unit-id-2> --ready-for-submission-confirmed --actor "Human Author"

Render the editable Response Letter from the RevisionLedger:

    python scripts/co_reviewer_cli.py render-response-docx --revision-ledger revision_ledger.json --out Response_to_Reviewers.docx --manuscript-title "Manuscript title" --author "Corresponding Author" --quality-report ResponseLetter_quality_report.json

Also retain the Markdown draft when useful:

    python scripts/co_reviewer_cli.py render-response --revision-ledger revision_ledger.json --out Response_to_Reviewers.md

The renderer must preserve draft/location-pending/completed distinctions and never invent a response or final page/line reference. Run the relevant DOCX quality profile and inspect the Word/PDF outputs before delivery. Deliver the Marked Manuscript/SI, final Clean Manuscript/SI, Response Letter, verification reports, PDF-backed location map, quality reports, and a concise list of unresolved/deferred items. A clean DOCX without the Word/PDF-backed map is not final page/line evidence.

## Token and delegation rules

- Delegate deterministic parsing, indexing, XML operations and mechanical checks.
- Keep Supervisor context to compact reports and selected evidence.
- Batch independent operations where safe; do not impose a fixed maximum number of Worker calls.
- Reuse inventory for unchanged source files; re-inspect changed inputs or requested scopes only.
- Before starting any OpenCode Worker, first inspect the currently available OpenCode Go models, then show the user the proposed role, selected model, active project root, inputs, expected outputs and validation boundary. Do not start a Worker invisibly; obtain explicit authorization first. After completion, report Job ID, Session ID, project, role, model, final state, concise result summary and token usage. Every Worker task must identify role, model, project, inputs, expected outputs and validation requirements.


## Usage and output-quality evidence

When running `inspect` or `audit`, optionally pass `--usage-ledger .co-reviewer-usage.jsonl` to record programmatic DOCX parse/cache events. These events record source hashes and metadata only; they do not store manuscript text and set `context_scope` to `none`.

Actual model-call usage is not intercepted automatically in P1. Record usage only from trusted provider/agent metadata exports using `record-usage-event`; do not infer model input from file reads or token counts from text length. `audit-usage` reports recorded token buckets and programmatic document-read events separately. `estimate-usage` requires an explicit pricing file with disjoint bucket accounting and complete measurements.

Run `audit-docx-quality --docx <file.docx> --profile <profile> --out <report.json>` for structural/typography checks. Supported profiles are `generic`, `clean-manuscript`, `marked-manuscript`, `word-final-clean` and `response-letter`. `render-response-docx` and `map-response-locations` also emit deterministic quality reports automatically.

A DOCX quality report is not proof of visual correctness. Final pagination and page/line locations must still come from Microsoft Word COM and the corresponding exported PDF; scientific correctness requires human semantic review.

## P0 integrity gates and final-location rules

The initial Manuscript and SI baselines must be clean before a revision round is registered. The gate scans native insertions/deletions, move revisions, every Word *Change element, native comment definitions and comment anchors. Existing unresolved markup blocks the round with package-part and paragraph evidence; it is not silently accepted as the new baseline.

Final Marked and Clean deliverables physically remove Word comment definitions, comment anchors, comment-related package parts, relationships and content-type overrides. Marked files retain the in-line w:ins and w:del Track Changes that record the current Co_Reviewer edits. Do not remove those revisions as a shortcut for comment cleanup.

Every executable PatchOperation must carry a non-empty, exact source_comment_ids list. Each revised ResponseUnit must link the precise operation IDs that declare that comment ID. No post-hoc inference from task text or target overlap is permitted. The ResponseUnit disposition and locator requirement are explicit: reply_only/not_required; no_change/existing_anchor_required or no_change/manual_required (both require a confirmed Clean anchor); revised/required or revised/manual_required; clarification_needed remains blocked.

Final body-text page/line positions come only from Microsoft Word COM on the final Clean DOCX. Mapping is grouped by document role. If the role's Working Clean SHA changes, all existing page/line evidence for that role is invalidated and the role is repaginated/remapped together. Each mapped ResponseUnit is bound to the final DOCX SHA, PDF SHA, map artifact SHA, locator ID and source Working Clean SHA. The ledger rejects mixed final DOCX/PDF versions within one document role.

After Word refreshes built-in SEQ, REF, PAGE and PAGEREF fields (not citation-manager ADDIN fields), it repaginates and exports the PDF. Native numeric SEQ Figure and SEQ Table results are checked for gaps, duplicates and unexpected starting numbers. A deterministic numbering error blocks export. Chapter-reset, non-numeric or manually typed caption schemes are explicitly marked manual_review; they are not reported as automatically certified.

### Word bookmark ranges and malformed markers

The structure classifier pairs `w:bookmarkStart` and `w:bookmarkEnd` by identical `w:id` and forward XML document order. A valid pair is guarded only across the paragraphs whose XML subtrees intersect the actual marker-to-marker interval. Bookmark markers may legally occur as direct children of `w:body` or other supported containers, outside any `w:p`; an absent nearest paragraph does not mean the paired endpoint is missing. Resolve both marker positions from the part's XML order, then map the interval to intersecting paragraphs. Never extend a valid range to the beginning/end of the part merely because one endpoint is outside a paragraph.

An unmatched bookmark marker is a non-conformant orphan, not an open-ended range: never treat an unmatched start as protecting every paragraph through the end of the document, or an unmatched end as protecting every paragraph before it. Preserve the original marker unchanged and block edits only in the paragraph containing that orphan; when the orphan is outside all paragraphs and has no paragraph anchor, keep the affected part blocked. Duplicate or misordered marker groups remain fail-closed over the bounded XML document-order interval between observed markers.

Never delete, auto-close, renumber or relocate bookmark markers to make a plan pass. The missing boundary cannot be inferred safely from the remaining XML. Report the exact marker ID, marker paths, XML order and affected paragraph scope; let the user decide whether a separate, explicitly authorized document repair is warranted. Re-run the official `validate-plan` and `preflight-plan` gates after a classifier update. Do not claim that reducing a false-positive range validates unrelated formatting, text-match or evidence blockers.

### Table and non-body manual locators

When a target cannot safely receive an automatic body-line locator (including table cells and unsupported header/footer objects), set location_requirement to manual_required. The Word mapper still exports the final DOCX/PDF for that document role but does not invent a line number. After map-response-locations, enter one locator row for every exact linked PatchOperation:

    {
      "response_units": [
        {
          "response_unit_id": "<response-unit-id>",
          "operation_id": "<patch-operation-id>",
          "locator_text": "<author-confirmed locator text>",
          "page": null,
          "line_start": null,
          "line_end": null
        }
      ]
    }

Then record the author-confirmed locators against the Word-exported artifacts:

    python scripts/co_reviewer_cli.py record-manual-response-locations --ledger revision_ledger.json --word-map word_pdf_final/response_location_map.json --input manual_locations.json --out manual_location_map.json --actor "Human Author"

The command requires exact operation coverage for every manual-required ResponseUnit, validates the output DOCX/PDF and current Working Clean hashes, and stores mapping_method=manual with a stable locator ID. locator_text is mandatory. Page is optional; line numbers may be supplied only when the author has checked them. When line numbers are supplied, page, line_start and line_end must all be positive integers. Do not fill uncertain line numbers with estimates. A manual locator is visibly distinct from a Word COM location in the ledger and Response Letter.


A no_change ResponseUnit may use manual_required when its confirmed existing anchor is a table cell or non-body part. The Clean-view step must still re-resolve its paraId or exact source excerpt in the declared Word part. The manual-input row uses `locator_id`, formed from the anchor namespace and the current anchor identifier, not `operation_id`; it records a locator, never an executable Patch.


For a `no_change/manual_required` unit, the input uses the confirmed anchor key rather than a PatchOperation:

```json
{
  "response_units": [
    {
      "response_unit_id": "<response-unit-id>",
      "locator_id": "anchor:<anchor-id>",
      "locator_text": "<author-confirmed locator text>"
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
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --task-ids <task-id-1> <task-id-2> --out revision_ledger.json
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
python scripts/co_reviewer_cli.py approve-plan --plan "runs/batch-<id>/revision_plan.preflighted.json" --revision-ledger revision_ledger.json --ids <patch-id>
python scripts/co_reviewer_cli.py apply --plan "runs/batch-<id>/revision_plan.approved.json" --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py verify --plan "runs/batch-<id>/revision_plan.applied.json"
python scripts/co_reviewer_cli.py confirm-verified --plan "runs/batch-<id>/revision_plan.applied.json" --revision-ledger revision_ledger.json --ids <patch-id> --semantic-review-confirmed
```

4. After every document-changing batch for the round has been published, create the final Working Clean projections, confirm ResponseUnit text and map final Word locations. Do not perform final page/line mapping between intermediate batches because later batches can shift the whole document's pagination:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids <response-unit-id> --verification-report-id "semantic-review-<response-unit-id>" --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final
python scripts/co_reviewer_cli.py record-response-locations --ledger revision_ledger.json --location-map word_pdf_final/response_location_map.json
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids <response-unit-id> --ready-for-submission-confirmed
```

For `manual_required` table/non-body locators, use `record-manual-response-locations` against the final Word/PDF map with the exact author-confirmed locator input described above. `reply_only` units with `not_required` location can be completed after semantic review without generating fake document locations.

5. Synchronize verified Patch lifecycle state to the Workflow Ledger, then publish the entire batch. Publication rechecks parent pointers, every Patch verification hash and all selected document-task states; final ResponseUnit page/line completion is intentionally a round-level release gate performed after every batch. The only active-document-state change is one atomic replacement of the Workflow Ledger file:

```powershell
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-<id>"
```

### Rollback and failed batches

A published batch can be rolled back without deleting any Marked DOCX, Clean DOCX, PDF, plan, report or batch directory:

```powershell
python scripts/co_reviewer_cli.py rollback-batch --workflow-ledger workflow_ledger.json --batch-dir "runs/batch-<id>" --reason "Explain why this committed batch is being rolled back."
```

Rollback restores all parent active pointers atomically and marks the affected tasks/dependents `recheck_required`. For an unpublished batch whose Apply step failed or left partial outputs, use the explicit recovery command:

```powershell
python scripts/co_reviewer_cli.py recover-failed-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-{id}" --reason "Explain why the failed Apply batch is being abandoned."
```

Recovery fails closed when a publish/rollback transaction exists, an active parent path/hash/batch ID has changed, intent/scope ownership is invalid, or any Patch is already verified. It stages and hashes the Workflow Ledger recheck, Patch abandonment events and retained batch artifact inventory before writing either Ledger. If interrupted between Ledger writes, rerun the same recovery command; it resumes the staged transaction. It never changes active document pointers or deletes/reuses partial outputs. Reauthorize the invalidated Workflow tasks, then build a fresh batch. A replacement Patch may restart from the recorded active parent only when the recovery event proves its path and SHA-256; ordinary applied/verified supersession must continue from its predecessor output. Never reuse a failed output path.

Publication and rollback use intent/receipt records so a retry after an interrupted pointer update can finish the receipt without overwriting later Workflow Ledger edits. If a concurrent Workflow Ledger update is detected before pointer replacement, the command fails closed.

## P1 final Response Letter and release manifest

The Markdown and DOCX Response Letter renderers use the same deterministic ResponseUnit/PatchOperation adapter. For revised units, quoted replacement text is copied from the exact verified `PatchOperation.replace`; for delete-only operations it quotes the exact `find` text; for `no_change` it quotes the confirmed existing-anchor excerpt. The renderer never asks a model to rewrite or repeat the manuscript change. The DOCX highlights these excerpts and labels their document role, final locator and operation ID.

After all relevant Workflow tasks are resolved, all Patches are verified, every reviewer comment has exactly one completed ResponseUnit, all final locations and quality reports are current, and every changed document role is published, create the final package:

```powershell
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger $preparedWorkflow --revision-ledger $revisionLedger --out-dir (Join-Path $runs "release-final")
```

The command creates a new unique `runs/release-{id}/` directory containing:

- `Response_to_Reviewers.md`
- `Response_to_Reviewers.docx`
- `Response_to_Reviewers.quality.json`
- `final_release_manifest.json`

The Release Gate reopens and rehashes current files, checks active pointers against Marked DOCX files, verifies Patch and ResponseUnit links, rechecks every final Word Clean DOCX/PDF pair, Word page counts, Figure/Table sequence audits and DOCX quality reports, and confirms the Response Letter contains the exact verified PatchOperation excerpts. The manifest is written only if every gate passes. A failed package directory is retained for diagnosis; create a fresh release directory after fixing the cause.


### Stage 1.6 — Deterministic OMML preview (formula editing still gated)

A limited, deterministic LaTeX-to-OMML converter is available for a synthetic Word preview. It supports simple symbols/Greek letters, fractions, square roots, subscript/superscript, selected accents and plain/bold/italic text runs. Unsupported commands fail closed.

Create a preview outside the round's source folders:

\`\`\`powershell
python scripts/co_reviewer_cli.py preview-omml-docx --expression "<supported-LaTeX-expression>" --out "$roundRoot\reports\equation_preview.docx"
\`\`\`

Open the preview in Microsoft Word and inspect formula rendering, save/close/reopen it, and preserve the preview as a separate test artifact. This is not an edit of Manuscript/SI, contains no Track Changes, and does not enable formula replacement in production. The OMML structure classifier must continue to block edits to existing formulas until a separate whole-formula revision wrapper passes the supported Word-version acceptance.
## Frozen semantic-profile-first editing policy (2026-11-22)

Formatting decisions must be front-loaded into the initial source inventory and reusable profile. Do not add a second whole-manuscript read after the first inventory solely to rediscover symbol meanings or formatting.

Do not use an LLM after Apply to re-judge whether the same semantic/format decision was correct, and do not introduce repeated AI QA/remediation loops. The author-approved content plan and the profile-backed format source are the decision inputs. After Apply, retain only deterministic execution-integrity checks needed to prove that the authorized target was edited, native Track Changes was written, the source was not overwritten, and the DOCX package remains structurally readable. Existing deterministic transaction, hash, exact-text and Marked-to-Clean safeguards are not replaced by an LLM check.

## Mandatory production editing path and formatting safeguards

- All Manuscript/SI Marked edits MUST use the repository's approved revision-plan/Patch lifecycle and the official CLI Apply/Verify path. Do not create or invoke task-local apply_adapter.py, robust_replace, direct OOXML mutation, format_pass.py, verify_normalized.py, or any other substitute writer/verifier for a production deliverable.
- A failed official preflight, approval, Apply, or verification is a hard stop. Preserve the failed plan and evidence, report the exact blocker, and request an engineering change to the repository implementation. Never bypass a fail-closed gate to finish a task.
- The OOXML classifier treats paragraph-scoped `w:proofErr` as a spelling/grammar annotation, not an editing-permission range. It preserves these anchors and treats them as boundaries between ordinary Run blocks; replacements must not cross them. `w:permStart` / `w:permEnd` remain protected. Misplaced proofing anchors remain blocked.
- Standard Word metadata exemptions are bound to exact package part, Word namespace, and parent-child context in the V2.3.2 engineering contract. This includes the registered `footnotePr`, `endnotePr`, `compat`, `docVars` and `doNotIncludeSubdocsInStats` settings subtrees, plus `w:hidden` only as a direct child of a `w:style` in `word/styles.xml`. These parts remain byte-identical. Unknown children, unregistered compatibility options, misplaced elements, and document-protection/revision-generation settings remain fail-closed.
- A mixed-format replacement may use the official `replacement_format_map` only when every replacement character is explicitly mapped to a source character offset. Each segment has exactly `replacement_start`, `replacement_end`, and `source_offset`; segments must be ordered, contiguous, non-overlapping, and cover the complete replacement. `source_offset` is an offset into the exact `find` text and selects the source Run whose direct formatting will be inherited by that replacement segment. If an author cannot justify the map character-by-character, the operation must fail closed rather than copy the first Run's formatting.
- For a text replacement that requires special formatting on newly inserted characters, use one explicit `two_pass: true` PatchOperation: Pass 1 inserts the exact replacement under native Track Changes; Pass 2 applies FormatChange only to exact `scope_find`/`scope_offset` spans inside that replacement. Every FormatChange must cite a `source_occurrence_id` from the current semantic/format profile. Workflow Preparation resolves its property values from that source occurrence and rejects stale profiles, unknown occurrences, body/reference cross-borrowing, and paragraph-style mismatches. The engine stores the old effective property snapshot in native `w:rPrChange`. Pure format-only operations with `find == replace` remain supported for existing text.
- A replacement spanning multiple words must never inherit subscript/superscript across the whole sentence merely because the matched source Run has that vertical alignment. A mapped replacement segment may retain implicit superscript/subscript only when it is whitespace-free and its length exactly matches the selected source span from that Run. Longer, whitespace-containing or length-mismatched replacement text must be baseline for vertical alignment; restore required script only through explicit Unicode script characters or separate token-level Pass-2 FormatChanges. A sub/superscript FormatChange over any whitespace-containing target is refused by the engine.
- Verify must independently compare actual inserted character-level vertical alignment against the exact authorized Pass-2 script targets. Any unrequested superscript/subscript character outside those target spans fails Verify, even if the Apply report records the same unexpected formatting. It also checks exact inserted revision IDs, text, full paragraph position, and historical format snapshots. A passing substring-presence check is not sufficient.

### One-round QA remediation (Phase 7)

- Remediation is allowed only for a hash-bound `word-final-clean` QA failure on the current Working Clean DOCX recorded in RevisionLedger, and only when every failing check is the local `placeholders` check. Structural/package/XML failures, tracked revisions, comments, formatting/semantic defects, and every other QA failure remain hard stops and require a separate authorized engineering or review path. The single operation must anchor every exact placeholder token reported by QA and remove all of them in its replacement. First prepare one new, human-approved, single-operation Patch in an immutable batch based on the exact current Marked path and SHA-256. The Patch output must be the batch-assigned new Marked path.
- Start the bounded cycle with:

```powershell
python scripts/co_reviewer_cli.py begin-qa-remediation --qa-report "runs/qa-before.quality.json" --revision-ledger revision_ledger.json --batch-dir "runs/batch-<id>" --patch-id "<patch-id>" --reason "Describe the specific local mechanical correction supported by the failed QA evidence." --out "runs/qa-remediation-started.json"
```

- The command proves that the failed QA report is the `word-final-clean` profile and that all failed findings are exact, reported placeholders covered by the Patch find/replace. It binds the failed QA report, source Clean, current Marked hash, approved Patch, batch intent/scope and approval event into RevisionLedger. It refuses a second start for the same RevisionLedger. Then use only the normal `apply`, `verify`, `confirm-verified`, `sync-workflow-ledger`, and `publish-batch` lifecycle. Do not directly modify Clean.
- After the batch is published, compile and QA the new Clean from Marked with:

```powershell
python scripts/co_reviewer_cli.py complete-qa-remediation --state "runs/qa-remediation-started.json" --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir "runs/batch-<id>" --clean-out "runs/remediation-final.clean.docx" --qa-report-out "runs/remediation-final.quality.json" --profile word-final-clean --out "runs/qa-remediation-completed.json"
```

- This command regenerates the accepted-changes projection from the exact published Marked bytes, verifies it against the deterministic compiler, updates the RevisionLedger Clean pointer/hash/fingerprint, and performs the second QA pass. A PASS or PASS_WITH_WARNINGS completes remediation. A second QA FAIL records `BLOCKED`; there is no third attempt. The final Release Gate rejects missing, duplicate, incomplete, failed, or hash-mismatched remediation history.

### Original instruction coverage and approval (mandatory)

Before Apply, split the original task instruction into **atomic requirements**, not merely comment IDs or paragraph-level intents. A single source comment can contain several independent actions (for example, insert two bibliography entries, update a new in-text citation, renumber old bibliography labels, and update every affected in-text occurrence). Record each independently. An unsupported operation must not cause related, independently executable operations to disappear.

After compiling the plan, create `instruction_coverage.json` with:
- `schema_version: "1.0"`;
- `source_instruction: {file, sha256}` bound to the exact original instruction file; `file` must be an absolute path.
- one unique `requirement_id` per atomic requirement;
- `source_locator` and `instruction_excerpt` pointing to the original wording;
- `status`: `planned`, `no_change`, `manual_completed`, `not_applicable`, `blocked`, or `deferred`;
- for `planned`, `revision_targets` must list every exact `{revision_item_id, target_index}` that implements the atomic requirement. Do not link only at revision-item level: an item may contain multiple independent Targets, and every Plan Target must be linked to at least one atomic source requirement;
- for `no_change` / `not_applicable`, evidence; for `manual_completed`, an evidence file and its SHA-256; for `blocked` / `deferred`, a specific reason;
- `coverage_sha256`, calculated by `co_reviewer.instruction_coverage.instruction_coverage_sha256` over the manifest excluding the `coverage_sha256` field.

The coverage manifest must enumerate every requested text, format, citation, bibliography, table/figure and cross-document operation. Every compiled Plan Target (not merely each RevisionItem) must link back to at least one `planned` requirement. The manifest validator checks exact target indices and rejects unlinked targets. Show the complete atomic requirement table, disposition, revision-item links, and coverage SHA-256 to the author. Obtain explicit author review of that exact manifest before producing an approval receipt:

```powershell
python scripts/co_reviewer_cli.py approve-instruction-coverage --coverage "$roundRoot\instruction_coverage.json" --approved-coverage-sha256 <reviewed-coverage-sha256> --approved-by "Human Author" --out "$roundRoot\instruction_coverage_approval.json"
```

Do not create or claim this approval without the author's explicit approval of the displayed coverage. Any `blocked` or `deferred` requirement remains unresolved and must block Final Gate. A paragraph-insertion limitation may be recorded as one blocked requirement, but independent existing citation/label renumbering must still be assessed and planned separately wherever the package graph and safe Patch workflow support it.

### Standard Plan Compilation (`compile-plan`)

- Use `compile-plan` as the standard high-level plan compilation path to convert concise edit intents (including inline formatting `*italic*`, `^sup^`, `~sub~`, `**bold**` and explicit `FormatChange` intents) into a validated `revision_plan.json` with two-pass `Text` + `FormatChange` orchestration:

```powershell
python scripts/co_reviewer_cli.py compile-plan --intents "$roundRoot\intents.json" --out "$roundRoot\revision_plan.json"
```

### Mandatory Final Delivery Hard Gate (`final-gate`) & Zero-Silent-Fallback

- Before declaring any deliverable complete, you MUST run `final-gate` and obtain `READY` (exit code `0`):

```powershell
python scripts/co_reviewer_cli.py final-gate --plan "$roundRoot\revision_plan.json" --verify-report "$roundRoot\verify_report.json" --instruction-coverage "$roundRoot\instruction_coverage.json" --instruction-coverage-approval "$roundRoot\instruction_coverage_approval.json" --docs "$roundRoot\manuscript_marked.docx" --out-json "$roundRoot\final_gate_report.json" --out-md "$roundRoot\final_gate_report.md"
```

- `final-gate` now requires the source-instruction coverage manifest and a separate author-approval receipt. It checks the original instruction file SHA-256, coverage/approval hashes, every atomic disposition, all mapped plan IDs, and that no compiled plan item is unlinked. Missing approval, omitted/unlinked requirements, or any `blocked`/`deferred` requirement returns `BLOCKED`, even when every submitted plan target passes.
- `final-gate` deterministically enforces:
  1. original instruction coverage is author-approved and complete;
  2. `verify` passed (`targets_failed == 0`, `untracked_text_audit.violations == 0`).
  2. `audit-docx-quality` passed (`summary.failures == 0`) on every delivered DOCX.
  3. Microsoft Word COM PDF rendering succeeded with a non-empty `%PDF-` file beside each delivered DOCX.
  4. **Zero-Silent-Fallback**: silent fallback to `manual_fallback_checklist.md`, unapplied `manual_suggestions/*.json`, `blocked`/`failed` targets, or skipped `FormatChange` operations is strictly forbidden. Any non-zero `manual_fallback_count`, `blocked_count`, or `skipped_format_ops` causes `final-gate` to return `BLOCKED` (exit code `1`) unless the user has explicitly approved the waiver and `--waive-fallback-reason` is recorded in both `final_gate_report.json` and `final_gate_report.md`.

- At task start, confirm the actual project root (git rev-parse --show-toplevel), HEAD, working-tree status, and the resolved Skill/workflow paths. Do not assume a hard-coded repository path. If the globally loaded @Co_Reviewer Skill points to a different repository snapshot than the active project, stop and report the mismatch; do not silently use either snapshot.

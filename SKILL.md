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

## Workflow

### Stage 1 — Inventory

Run one inventory-and-audit pass with the available input files:

    python scripts/co_reviewer_cli.py audit --rev Reply.docx --man Manuscript.docx --si SI.docx --out workspace_digest.md --json-out workspace_inventory.json --audit-md-out review_audit_report.md --audit-out review_audit_report.json --decision-queue-out review_decision_queue.json --cache-dir .co-reviewer-cache

Omit unavailable arguments. The command parses each source DOCX once per SHA-256/version and stores the resulting paragraph/comment inventory in the local cache. The deterministic audit then reuses those in-memory inventories for native comment anchors, cross-document matching and figure/table consistency. It does not send entire DOCX files to the model.

The inventory includes ordinary reviewer-document paragraphs and native Word comments. For each native comment, the selected text is reconstructed from matching `commentRangeStart` and `commentRangeEnd` markers in the same Word part. The comment body is separately retained with its index in `word/comments.xml`, so it remains citable even when no selected range is recoverable. A reference-only comment or missing marker is not assigned a guessed selection; its containing paragraph is separate. One comment is recorded once even when its range crosses paragraphs.

The audit confirms a cross-document target only when the native selection text matches exactly and uniquely in Manuscript or SI. Token-overlap matches are candidates only; ambiguous or missing targets remain unresolved. Figure/table auditing reports caption order, first-mention order, duplicates, gaps, unresolved references and unreferenced captions. It is diagnostic only and never renumbers documents.

The command also writes a bounded `review_decision_queue.json` conforming to `schemas/review-decision.schema.json`. Each comment packet contains the native anchor once, compact match coordinates, and at most 8 context excerpts of at most 1,200 characters each. Use `--comment-ids 3` (replace 3 with the Word comment ID) when only one comment needs semantic analysis; the full audit report remains complete while the AI queue contains only the selected item. Use this as the only model input for semantic decisions, one comment at a time.

For each selected item, the Supervisor must propose a structured decision with: `disposition` (use the schema values `manuscript`, `si`, `reply_only`, `manuscript_and_si`, `no_change`, or `clarification_needed`), evidence-based `rationale`, exact `proposed_targets` (document, part, paragraph index, action and source excerpt), `response_outline`, `supporting_evidence`, `missing_evidence_or_questions`, and a confidence estimate. Mark the proposal as `proposed` or `needs_clarification`; `requires_user_approval` must remain true. Do not create/apply revision targets merely because a candidate match exists. Show the proposed decision to the user and create the revision plan only after the user accepts the disposition and target scope.

The XML inventory provides stable part/paragraph/paraId coordinates, not rendered page and line numbers. Word pagination and line wrapping depend on layout. Do not invent page/line values from XML; obtain them only after rendering/layout inspection in Word or report the stable paragraph coordinates instead. The model must decide whether the response belongs in Manuscript, SI, Reply only, multiple documents, no change, or needs clarification; it must cite the supplied evidence and leave every decision pending until the user approves a revision plan. Do not infer scientific results or invent edits.

Use `--comment-author` only when filtering by the comment author's name. Do not filter comments by the manuscript author's name. `--refresh-cache` forces re-parsing; otherwise a changed DOCX SHA-256 invalidates only that file's cached inventory. Matching, numbering and decision-queue generation are cheap deterministic passes over the cached inventories and can be repeated without reopening the source DOCX files.

### Stage 2 — Build and validate a pending Decision Plan

After the user accepts the proposed dispositions and target scope, create one revision_decision_plan.json for the round, conforming to schemas/revision-decision-plan.schema.json.

Each task must be atomic, retain every source reviewer comment ID, link exact source evidence, identify all affected documents/Word parts/paragraphs, declare dependencies, and keep status "pending" and requires_user_approval true. Include exact evidence for each Manuscript or SI target. A reviewer-comment excerpt is not a substitute for target-document evidence. Do not include executable Patch operations in the Decision Plan; that plan records the scientific/structural decision and evidence scope.

Validate provenance and persist the initial Workflow Ledger in one pass:

    python scripts/validate_decision_plan.py --plan revision_decision_plan.json --audit review_audit_report.json --inventory workspace_inventory.json --workflow-ledger-out workflow_ledger.json --revision-round-id round-2026-11-14

Use --identity-manifest document_identity_manifest.json when a current parser-backed identity manifest is available. Do not invent a TargetRef or reuse an old identity manifest against changed source hashes. The validator must pass before the Workflow Ledger is used. If evidence, comment coverage, source hashes, target coordinates or dependency order fail, correct the plan from the actual inventory and rerun validation; do not weaken the validator.

### Stage 2.5 — Prepare the executable workflow scope

The generated Workflow Ledger is the round's control plane. It preserves the pending Decision Plan tasks and their evidence, dependencies and target groups. Before authorizing a task, enrich the corresponding Workflow Ledger task with the exact executable scope:

- patch_id and patch_operations: one operation per exact document target, including document role, immutable input DOCX path, distinct output DOCX path, Word part, paragraph index, exact find/replace strings, readable location, and evidence IDs for that same target.
- proposed_response: the author-reviewed response wording. It must be grounded in the reviewer comment and supplied evidence; leave it empty or flag clarification rather than inventing a response.
- Preserve the Decision Plan task ID, source comment IDs, evidence, target references/groups and dependencies.

Every Patch operation must be fully specified and supported by its evidence IDs. Never infer replacement text from a reviewer comment alone, silently choose between ambiguous targets, or modify a source DOCX. If a target is unresolved, keep its task blocked or pending clarification and do not add an executable Patch.

Reviewer attribution is separate from task identity. Populate reviewer_map.json so each source comment ID retains its actual reviewer label. For a task combining comments from different reviewers, use this nested form:

    {
      "T1": {
        "comment_ids": {
          "1": "Reviewer 1",
          "2": "Reviewer 2"
        }
      }
    }

The RevisionLedger will label the Patch with the distinct reviewers involved and each ResponseUnit with the reviewer attached to its own comment ID. Do not let one task-level reviewer label overwrite the identities of comments from other reviewers. Without an explicit map, the builder uses the generic label "Reviewer". For incremental batches, the RevisionLedger preserves any reviewer identity already recorded when a later batch uses this generic fallback; a generic placeholder can be upgraded when an explicit label is supplied, but one explicit reviewer label cannot be silently replaced by a different explicit label. Prefer preparing the complete reviewer map before the first build so every ResponseUnit has correct attribution from the start.

The exact operations, proposed response and reviewer attribution become part of the author-approval fingerprint. Finish preparing them before asking the author to authorize the task. Then authorize only the selected, fully prepared tasks:

    python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger workflow_ledger.json --ids T1 T2 --plan-approved --actor "Human Author"

Use the actual task IDs. This command is the explicit transition from planned to ready; it does not edit a DOCX. Do not edit task status or approval events manually.

### Stage 2.6 — Build the RevisionLedger and current Patch batch

Build the typed Patch/ResponseUnit records from the current conflict-free ready batch:

    python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --reviewer-map reviewer_map.json --out revision_ledger.json

The scheduler selects only ready tasks that do not conflict on target groups and whose dependencies are satisfied. Use --task-ids only to select a subset of that ready batch; it cannot bypass a dependency or conflict. When later batches become ready, run the command again against the same Workflow Ledger and RevisionLedger paths so the new batch is merged without erasing prior Patch history.

The RevisionLedger is the durable execution record for the round. It holds document identities/paths, exact Patch operations, reviewer-linked ResponseUnits, workflow fingerprints and lifecycle events. Do not hand-edit Patch IDs, statuses, fingerprints, source hashes or event history. If the command refuses a task because reviewer-comment evidence, target evidence, paths or operation data are incomplete, fix the source Workflow Ledger and repeat the appropriate approval step; never weaken its validation.

Export the newly planned Patch operations to the existing Word-native revision engine format:

    python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --out edits_plan_batch_01.json

This legacy-shaped edits_plan_batch_01.json is an adapter payload for the existing Track Changes writer, not a replacement for the Workflow Ledger or RevisionLedger. Do not maintain a separate manually edited revision plan as a competing source of truth. The export command refuses to overwrite an existing file, so use a new unique plan/report filename for every later batch (for example, edits_plan_batch_02.json). Reuse the same Workflow Ledger and RevisionLedger paths to preserve round history.

### Stage 3 — Preflight and approve the exact Patch batch

Before approval, run the no-write source preflight and synchronize its evidence into the RevisionLedger:

    python scripts/co_reviewer_cli.py preflight-plan --plan edits_plan_batch_01.json --out preflight_report_batch_01.json --revision-ledger revision_ledger.json

Present the preflight result, exact target operations, input/output paths and source hashes to the author. A failed or ambiguous target blocks the batch. If an input source or Patch operand changes after preflight, rebuild/re-authorize the affected scope and rerun preflight; do not reuse stale approval.

After the author approves the exact preflighted Patch IDs, record that approval in both the plan and RevisionLedger:

    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan_batch_01.json --ids T1 T2 --revision-ledger revision_ledger.json --actor "Human Author"

This is a second, narrower gate: approve-workflow-tasks authorizes the prepared task scope, while approve-plan binds approval to the exact preflighted Patch operations and source versions. Both are required. Do not apply a Patch that has not passed both gates. Use a new preflight-report filename for each later batch. Keep the same revision_apply_report.json across batches because apply appends execution records; give each batch its own Decision/Revision Plan and verification-report filename.

### Stage 4 — Apply approved changes using native Word Track Changes

Apply only the approved Patch batch to separate Marked DOCX outputs:

    python scripts/co_reviewer_cli.py apply --plan edits_plan_batch_01.json --author "Author Name" --report revision_apply_report.json --revision-ledger revision_ledger.json

Inputs are immutable. Write revised copies to distinct output paths. The legacy Word writer remains the only document-writing implementation. Every textual change in a Marked Manuscript or SI must be represented by native Word Track Changes: removed text inside w:del/w:delText, and added text inside w:ins/w:t. Font color is not a substitute.

Never directly rewrite a paragraph/run with python-docx, replace XML text nodes, use a second DOCX writer, or edit Marked outputs with Track Changes turned off. Targets crossing unsupported Word structures must be refused and reported, not bypassed by replacing the whole paragraph.

### Stage 5 — Verify, synchronize the DAG and continue incremental batches

Run mechanical verification against the actual apply report:

    python scripts/co_reviewer_cli.py verify --plan edits_plan_batch_01.json --apply-report revision_apply_report.json --out revision_verification_batch_01.json

This checks exact revision IDs and deleted/inserted text, whole-document untracked-text integrity, and supported native subscript/superscript formatting. It does not judge scientific correctness.

Only after mechanical verification and human semantic review of the revised Manuscript/SI pass, seal the verified Patch outputs:

    python scripts/co_reviewer_cli.py confirm-verified --plan edits_plan_batch_01.json --apply-report revision_apply_report.json --ids T1 T2 --semantic-review-confirmed --revision-ledger revision_ledger.json

Then synchronize Patch lifecycle evidence back to the dependency graph:

    python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json

The Workflow Ledger is the authoritative task-state/DAG view; the RevisionLedger is the authoritative Patch/ResponseUnit history. Verified upstream changes may invalidate dependent or same-target tasks. Review the invalidation reason, refresh evidence/targets against the latest output DOCX, re-authorize the affected task, and build the next ready Patch batch. Never apply a later batch against an outdated document version. Continue until all in-scope tasks are verified or explicitly blocked/deferred.

### Stage 6 — Prepare Clean views, final Word/PDF locations and Response Letter

After the final Patch batch is verified and the Workflow Ledger is synchronized, create a separate accepted-changes projection for review:

    python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json

This is an intermediate Working Clean View, not the final publication Clean DOCX. It accepts tracked insertions, rejects deletions, removes comment/revision markup, and preserves the Marked source. Inspect the Marked DOCX and Working Clean View together; do not overwrite either.

Review the complete cross-document result: Manuscript, SI, figures/tables/references, numerical values, units, sample identifiers, and every proposed reviewer response. Only after the author confirms the response wording and its semantic consistency, record that review:

    python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-T1-1 RU-T2-2 --semantic-review-confirmed --actor "Human Author"

Use the actual ResponseUnit IDs. A semantically reviewed ResponseUnit is not yet a submission-ready response.

On Windows with Microsoft Word installed, generate the final Word-layout Clean DOCX, PDF and evidence-backed page/line map:

    python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final

This step requires the hash-bound Working Clean Views and real Microsoft Word COM pagination. It exports final-clean DOCX/PDF files, verifies the target phrases against the PDFs, and records actual page/line locations. Do not calculate page or line values from XML or estimate them from paragraph counts. If Word COM is unavailable or a target is in an unsupported structure (for example, a table cell), report the blocker and leave the ResponseUnit incomplete. record-response-locations is for attaching a separately generated, independently validated location map; do not fabricate one.

Only after every required ResponseUnit has semantic review and final PDF location evidence, and the author confirms readiness, mark the selected units complete:

    python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-T1-1 RU-T2-2 --ready-for-submission-confirmed --actor "Human Author"

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

At an actual Supervisor/Worker model-call boundary, wrap the invocation with `co_reviewer.usage_adapter.invoke_with_usage`; supply the provider's documented usage extractor and explicit `context_scope`, stage, actor, provider/model, and job/session identifiers. Missing usage stays unavailable; never infer model input from file reads or token counts from text length. The wrapper is not active until the external Antigravity/OpenCode call site attaches it. Use `record-usage-event` only for trusted metadata events exported separately. `audit-usage` reports token buckets, model-call context scopes, outcomes and observed durations by stage, actor and provider/model. `estimate-usage` requires an explicit pricing file with disjoint bucket accounting and complete measurements.

Run `audit-docx-quality --docx <file.docx> --profile <profile> --out <report.json>` for structural/typography checks. Supported profiles are `generic`, `clean-manuscript`, `marked-manuscript`, `word-final-clean` and `response-letter`. `render-response-docx` and `map-response-locations` also emit deterministic quality reports automatically.

A DOCX quality report is not proof of visual correctness. Final pagination and page/line locations must still come from Microsoft Word COM and the corresponding exported PDF; scientific correctness requires human semantic review.

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

4. After every document-changing batch for the round has been published, create the final Working Clean projections, confirm ResponseUnit text and map final Word locations. Do not perform final page/line mapping between intermediate batches because later batches can shift the whole document's pagination:

```powershell
python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json
python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-P1-C1 --verification-report-id "semantic-review-P1-C1" --semantic-review-confirmed
python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final
python scripts/co_reviewer_cli.py record-response-locations --ledger revision_ledger.json --location-map word_pdf_final/response_location_map.json
python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-P1-C1 --ready-for-submission-confirmed
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
python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
```

The command creates a new unique `runs/release-{id}/` directory containing:

- `Response_to_Reviewers.md`
- `Response_to_Reviewers.docx`
- `Response_to_Reviewers.quality.json`
- `final_release_manifest.json`

The Release Gate reopens and rehashes current files, checks active pointers against Marked DOCX files, verifies Patch and ResponseUnit links, rechecks every final Word Clean DOCX/PDF pair, Word page counts, Figure/Table sequence audits and DOCX quality reports, and confirms the Response Letter contains the exact verified PatchOperation excerpts. The manifest is written only if every gate passes. A failed package directory is retained for diagnosis; create a fresh release directory after fixing the cause.

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

Python 3.10+ is required. Install the DOCX/XML dependencies with: python -m pip install -r requirements.txt.

PowerShell — one parse/cache pass followed by deterministic auditing:

    python scripts/co_reviewer_cli.py audit --rev .\input\Reply.docx --man .\input\Manuscript.docx --si .\input\SI.docx --out workspace_digest.md --json-out workspace_inventory.json --audit-md-out review_audit_report.md --audit-out review_audit_report.json --decision-queue-out review_decision_queue.json --cache-dir .co-reviewer-cache

By default, the decision queue includes all Word comments in the round. Add `--comment-ids 3` only when the user explicitly requests a selected subset; do not process a normal review round one comment per user turn.

The command writes:
- `workspace_inventory.json`: parsed paragraph, native comment, anchor, caption and reference inventory.
- `review_audit_report.json/.md`: exact and candidate cross-document matches plus classified figure/table references, subpanels/ranges, internal collisions and cross-document caption groups.
- `review_decision_queue.json`: a bounded packet conforming to `schemas/review-decision.schema.json`; it includes each native anchor once and at most 8 context excerpts (1,200 characters each).

For the complete decision stage, analyse all queue items plus the workspace-level audit together. Split compound reviewer requests into atomic tasks, merge overlapping tasks while retaining all source comment IDs, and add global consistency tasks that are not anchored to one comment. Draft one `revision_decision_plan.json` conforming to `schemas/revision-decision-plan.schema.json`; every task remains pending and requires author approval. The validator checks provenance and dependency integrity, not scientific correctness. Present the proposed dispositions and targets to the author first; only after the author accepts that scope should the Workflow Ledger be created and the execution tasks prepared.

Each DOCX inventory is cached by its SHA-256 and parser cache-schema version. The first run parses the files; a later run with unchanged source bytes reports cache hits and performs matching/numbering over the cached JSON without reopening the DOCX. If one input changes, only that input is reparsed. Use `--refresh-cache` to force a fresh parse. The cache directory is local and ignored by Git.

A native target match is confirmed only when a resolved Word selection matches exactly and uniquely in a target document. Similarity/token-overlap results are candidates, not confirmed locations. Figure/table numbering checks are diagnostic only; the tool never renumbers files.

After the author accepts the proposed dispositions and target scope, create one pending Decision Plan conforming to `schemas/revision-decision-plan.schema.json`. Validate provenance and create the round's Workflow Ledger together:

    python scripts/validate_decision_plan.py --plan revision_decision_plan.json --audit review_audit_report.json --inventory workspace_inventory.json --workflow-ledger-out workflow_ledger.json --revision-round-id round-2026-11-14

Use `--identity-manifest` only with a parser-backed manifest whose source hashes match the current inputs. The Decision Plan remains pending; this command does not authorize edits.

Before authorizing tasks, enrich the relevant Workflow Ledger tasks with exact executable Patch operations: document role, immutable input path, separate output path, Word part, paragraph index, exact find/replace strings, readable location and same-target evidence IDs. Add only an author-reviewed proposed response. Preserve task IDs, reviewer comment IDs, evidence, dependencies and target groups. Do not add executable operations for unresolved or ambiguous targets.

Preserve reviewer identity per comment. When one task combines comments from different reviewers, provide a nested `reviewer_map.json`:

    {
      "T1": {
        "comment_ids": {
          "1": "Reviewer 1",
          "2": "Reviewer 2"
        }
      }
    }

Prepare the complete reviewer map before the first batch when possible. Across later batches, the RevisionLedger preserves previously recorded reviewer identities when a generic fallback is used; it permits upgrading a generic placeholder to an explicit label but rejects conflicting explicit reassignment.

Then authorize only the selected, fully prepared task scopes and build the conflict-free Patch batch:

    python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger workflow_ledger.json --ids T1 --plan-approved --actor "Human Author"
    python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --reviewer-map reviewer_map.json --out revision_ledger.json
    python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --out edits_plan_batch_01.json

The Workflow Ledger controls task dependencies and target conflicts. The RevisionLedger is the durable Patch/ResponseUnit history. The exported revision plan is only an adapter for the existing Word-native Track Changes writer; do not create a competing manually maintained plan. Later batches reuse the same Workflow Ledger and RevisionLedger paths but receive new numbered plan files.

Preflight the exact Patch operations, then obtain the second approval bound to their source hashes and operands:

    python scripts/co_reviewer_cli.py preflight-plan --plan edits_plan_batch_01.json --out preflight_report_batch_01.json --revision-ledger revision_ledger.json
    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan_batch_01.json --ids T1 --revision-ledger revision_ledger.json --actor "Human Author"

Both task-scope authorization and exact Patch approval are required. Apply only after both gates pass:

    python scripts/co_reviewer_cli.py apply --plan edits_plan_batch_01.json --author "Author Name" --report revision_apply_report.json --revision-ledger revision_ledger.json
    python scripts/co_reviewer_cli.py verify --plan edits_plan_batch_01.json --apply-report revision_apply_report.json --out revision_verification_batch_01.json
    python scripts/co_reviewer_cli.py confirm-verified --plan edits_plan_batch_01.json --apply-report revision_apply_report.json --ids T1 --semantic-review-confirmed --revision-ledger revision_ledger.json
    python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json

Reuse `revision_apply_report.json` across batches because the apply command appends execution records. Give every batch its own plan, preflight report and verification report. When a verified task unlocks dependents or invalidates an overlapping task, refresh the affected evidence and target paths against the latest DOCX outputs, obtain renewed authorization, and build the next ready batch. Never apply a dependent Patch against an outdated document.

After all in-scope Patch batches are verified, create separate Working Clean projections:

    python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json

Working Clean Views are intermediate accepted-changes projections, not final Word-layout Clean files. Inspect Marked and Working Clean outputs together, then confirm the author-reviewed ResponseUnits:

    python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-T1-1 --semantic-review-confirmed --actor "Human Author"

On Windows with Microsoft Word installed and the optional Word/PDF dependencies, generate the final Word-layout Clean DOCX, PDF and actual page/line map:

    python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final

Only actual Word COM pagination and the exported PDF can establish final page/line numbers. Do not calculate them from XML or estimate them. Complete ResponseUnits only after semantic review, PDF-backed locations and explicit author readiness:

    python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-T1-1 --ready-for-submission-confirmed --actor "Human Author"

Render the Response Letter from the RevisionLedger, preserving draft/location-pending/completed states:

    python scripts/co_reviewer_cli.py render-response-docx --revision-ledger revision_ledger.json --out Response_to_Reviewers.docx --manuscript-title "Manuscript title" --author "Corresponding Author" --quality-report ResponseLetter_quality_report.json
    python scripts/co_reviewer_cli.py render-response --revision-ledger revision_ledger.json --out Response_to_Reviewers.md

A semantically reviewed ResponseUnit without final PDF locations is still location-pending, not submission-ready. Deliver Marked and final Clean Manuscript/SI files, the Response Letter, verification/quality reports, PDF-backed location map and unresolved-item list. See `examples/revision_plan.example.json` and `docs/WORKFLOW.md`.

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

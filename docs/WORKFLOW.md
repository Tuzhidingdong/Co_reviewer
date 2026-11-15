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

Start with one audit command rather than separate per-feature document reads:

    python scripts/co_reviewer_cli.py audit --rev revision_round_01/input/Reply.docx --man revision_round_01/input/Manuscript.docx --si revision_round_01/input/SI.docx --out revision_round_01/reports/workspace_digest.md --json-out revision_round_01/reports/workspace_inventory.json --audit-md-out revision_round_01/reports/review_audit_report.md --audit-out revision_round_01/reports/review_audit_report.json --decision-queue-out revision_round_01/reports/review_decision_queue.json --cache-dir revision_round_01/.co-reviewer-cache

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

The current round-level control plane is the Workflow Ledger; the RevisionLedger is the durable record of Patch and ResponseUnit execution. Do not start from a separately maintained legacy revision plan.

After the author accepts the pending Decision Plan's scientific dispositions and target scope, validate it and persist the Workflow Ledger:

    python scripts/validate_decision_plan.py --plan revision_decision_plan.json --audit reports/review_audit_report.json --inventory reports/workspace_inventory.json --workflow-ledger-out workflow_ledger.json --revision-round-id round-2026-11-14

Use a parser-backed identity manifest only when its source hashes match the current inputs. The resulting tasks remain planned and require explicit approval.

Before task authorization, enrich each executable task with exact `patch_operations`: one per target, each containing the document role, immutable input DOCX path, separate output DOCX path, Word part, paragraph index, exact find/replace strings, readable location and same-target evidence IDs. Include only an author-reviewed proposed response. Do not create operations for unresolved/ambiguous targets. Preserve all source comment IDs and add a `reviewer_map.json` so each comment retains its actual reviewer. For example:

    {
      "T1": {
        "comment_ids": {
          "1": "Reviewer 1",
          "2": "Reviewer 2"
        }
      }
    }

Prepare the complete reviewer map before the first batch when possible. If a later build uses the generic fallback, the RevisionLedger retains any reviewer identity already recorded; a generic placeholder can be upgraded when an explicit reviewer label is supplied, but an existing explicit label cannot be silently replaced by a different explicit label.

Authorize the prepared task scope, then build the conflict-free ready batch. The command allocates one immutable directory under `runs/batch-{id}/`, pins its parent document paths and hashes, and chooses new output DOCX paths before any edit begins:

```powershell
python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger workflow_ledger.json --ids T1 --plan-approved --actor "Human Author"
python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --reviewer-map reviewer_map.json --out revision_ledger.json
```

The command prints the new batch ID and directory. Export its plan into that directory:

```powershell
python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --batch-dir runs/batch-{id}
```

The scheduler enforces dependency and target-group conflict boundaries. `--task-ids` can select only a subset of the currently ready batch. A document role is reserved by an uncommitted batch so a second batch cannot branch from the same active parent version. Do not reuse an existing batch directory; an interrupted or failed batch is retained and must be resolved or superseded before another batch for that document role is created.

Preflight, approval, apply and verification each create a new plan snapshot. The original plan is not overwritten:

```powershell
python scripts/co_reviewer_cli.py preflight-plan --plan runs/batch-{id}/revision_plan.json --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py approve-plan --plan runs/batch-{id}/revision_plan.preflighted.json --ids T1 --revision-ledger revision_ledger.json --actor "Human Author"
python scripts/co_reviewer_cli.py apply --plan runs/batch-{id}/revision_plan.approved.json --revision-ledger revision_ledger.json --author "Author Name"
python scripts/co_reviewer_cli.py verify --plan runs/batch-{id}/revision_plan.applied.json
python scripts/co_reviewer_cli.py confirm-verified --plan runs/batch-{id}/revision_plan.applied.json --ids T1 --semantic-review-confirmed --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json
python scripts/co_reviewer_cli.py publish-batch --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --batch-dir runs/batch-{id}
```

Use the next plan snapshot printed by each command. The batch Apply report and verification report are created inside the same batch directory; never redirect them to a previously used file. If Apply partially writes files and then fails, do not delete or reuse those outputs. The incomplete batch remains uncommitted; inspect it and supersede or resolve the affected Patch before preparing a new batch.

`publish-batch` checks the parent active pointers, batch intent/scope hashes, every expected Patch verification record, and the synchronized Workflow task states. It then atomically replaces the Workflow Ledger's `active_documents` object so all roles in that batch become active together. Previous outputs remain untouched.

To roll back a published batch, restore only its parent pointers:

```powershell
python scripts/co_reviewer_cli.py rollback-batch --workflow-ledger workflow_ledger.json --batch-dir runs/batch-{id} --reason "Describe the reason"
```

Rollback never deletes a DOCX, PDF, report or batch directory. It records a receipt and moves affected tasks and their dependents to `recheck_required`; a new revision must be explicitly prepared and verified.

After all batches for the round have been published, create Working Clean Views, complete the final ResponseUnit/Word-PDF mapping, and only then create the release package. Do not perform the final page/line map after each intermediate batch: a later batch can change pagination across the entire document role.


## Clean views, final Word/PDF locations and Response Letter

After **all** document-changing batches have been published, create separate accepted-changes projections from the active Marked DOCX outputs:

    python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json

Working Clean Views are intermediate review projections, not final Word-layout Clean deliverables. Inspect Marked and Working Clean DOCX files together and check claims, sample identities, numerical values, units, figures/tables and cross-references across Manuscript and SI.

After the author confirms each ResponseUnit's wording and scientific/semantic consistency, record that review:

    python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-T1-1 --semantic-review-confirmed --actor "Human Author"

On Windows with Microsoft Word installed and the optional Word/PDF dependencies, generate final Word-layout Clean DOCX files, PDFs and actual page/line locations:

    python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final

The page and line references must come from real Word COM pagination and corresponding PDF artifacts. XML paragraph indices are not page/line numbers. The mapper refreshes Word SEQ/REF/PAGE/PAGEREF fields, repaginates and checks native Figure/Table SEQ continuity. Unsupported chapter-reset/non-native numbering is marked for manual review; actual table-cell and non-body locators require explicit author-confirmed manual location records. Do not infer a page or line from paragraph indices or PDF text extraction.

For a table/non-body target, provide an explicit author-confirmed locator against the exact Word/PDF export:

    python scripts/co_reviewer_cli.py record-manual-response-locations --ledger revision_ledger.json --word-map word_pdf_final/response_location_map.json --input manual_locations.json --out word_pdf_final/manual_location_map.json

Only after final locations are attached and the author accepts readiness, complete each relevant ResponseUnit. A conceptual `reply_only` unit with `location_requirement=not_required` can be completed after semantic review without fabricating a manuscript location:

    python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-T1-1 --ready-for-submission-confirmed --actor "Human Author"

Create the final release package only after every active Patch is verified, every Workflow task/comment is resolved, and every ResponseUnit is completed:

    python scripts/co_reviewer_cli.py create-release-package --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json --manuscript-title "Manuscript title" --author "Corresponding Author"

The command writes a new `runs/release-{id}/` directory containing `Response_to_Reviewers.md`, `Response_to_Reviewers.docx`, its DOCX quality report and `final_release_manifest.json`. It rechecks active pointers, immutable batch receipts, verified PatchOperation excerpts, response attribution, final DOCX/PDF hashes, Word page counts, SEQ audits, a deterministic Manuscript/SI Figure/Table caption-reference audit and quality reports. Unresolved/ambiguous references, duplicate captions, numbering gaps, out-of-order first mentions and cross-document same-label/different-caption collisions block release. Numeric values, units, sample identities and citation semantics remain covered by the explicit human scientific-review gate; the tool does not claim to infer scientific equivalence. If any gate fails, no manifest is produced; the incomplete release directory is retained, and the next attempt must use a new directory.

The Response Letter automatically quotes actual replacement/deleted text from each exact linked PatchOperation and places it below the author's response argument with its verified final document location. No model call is made to rewrite or repeat the manuscript body text. Draft/location-pending labels remain visible before final completion. Never invent response wording or page/line locations.


## Local automated checks on Windows

From PowerShell at any working directory, run:

    .\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe"

Replace the interpreter path with the intended Python 3.10+ environment. To install dependencies into that environment before the checks:

    .\scripts\run_quality_checks.ps1 -PythonExe "D:\Software\Anacoda\python.exe" -InstallDependencies

This runs Python compilation, the complete unit suite and CLI entry-point checks. It does not automate Microsoft Word's visual/compatibility acceptance.

## Response Letter DOCX

The current response renderers consume ResponseUnits from the RevisionLedger. Use the round's RevisionLedger rather than a separately maintained legacy revision plan:

    python scripts/co_reviewer_cli.py render-response-docx --revision-ledger revision_ledger.json --out Response_to_Reviewers.docx
    python scripts/co_reviewer_cli.py render-response --revision-ledger revision_ledger.json --out Response_to_Reviewers.md

The renderer preserves draft/location-pending/completed distinctions, reviewer attribution and linked document locations. Missing reply text and missing PDF page/line mappings remain explicit; the renderer never invents them. Existing output files are not overwritten; choose a new path for each generated version.


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

Authorize the prepared task scope, then build the conflict-free ready batch:

    python scripts/co_reviewer_cli.py approve-workflow-tasks --workflow-ledger workflow_ledger.json --ids T1 --plan-approved --actor "Human Author"
    python scripts/co_reviewer_cli.py build-revision-ledger --workflow-ledger workflow_ledger.json --reviewer-map reviewer_map.json --out revision_ledger.json
    python scripts/co_reviewer_cli.py export-revision-plan --ledger revision_ledger.json --out edits_plan_batch_01.json

The scheduler enforces dependency and target-group conflict boundaries. `--task-ids` can select only a subset of the currently ready batch. Use a unique exported plan filename for every batch; the exporter does not overwrite an existing plan. Keep the same Workflow Ledger and RevisionLedger paths so task state and Patch history remain connected.

## Preflight, exact Patch approval, apply and verification

Preflight the exact exported Patch batch and synchronize the source/version evidence into the RevisionLedger:

    python scripts/co_reviewer_cli.py preflight-plan --plan edits_plan_batch_01.json --out preflight_report_batch_01.json --revision-ledger revision_ledger.json

Review the exact edits, paths and source hashes with the author. Then record the second approval, bound to the preflighted Patch operations:

    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan_batch_01.json --ids T1 --revision-ledger revision_ledger.json --actor "Human Author"

Both gates are required: `approve-workflow-tasks` authorizes the prepared task scope; `approve-plan` authorizes the exact preflighted Patch operations and source versions. A task-scope approval alone is not permission to edit a DOCX.

Apply only after both approvals:

    python scripts/co_reviewer_cli.py apply --plan edits_plan_batch_01.json --author "Author Name" --report output/revision_apply_report.json --revision-ledger revision_ledger.json

Inputs must remain immutable, and each output DOCX path must be distinct. The existing Word-native Track Changes writer is the only writer. Do not edit a Marked DOCX directly or bypass a refused complex target.

Run exact mechanical verification:

    python scripts/co_reviewer_cli.py verify --plan edits_plan_batch_01.json --apply-report output/revision_apply_report.json --out reports/revision_verification_batch_01.json

Only after mechanical verification and separate scientific/semantic review of the revised Manuscript/SI pass, seal the Patch outputs and synchronize task state:

    python scripts/co_reviewer_cli.py confirm-verified --plan edits_plan_batch_01.json --apply-report output/revision_apply_report.json --ids T1 --semantic-review-confirmed --revision-ledger revision_ledger.json
    python scripts/co_reviewer_cli.py sync-workflow-ledger --workflow-ledger workflow_ledger.json --revision-ledger revision_ledger.json

Use unique plan, preflight and verification report filenames for each later batch. Reuse the same `output/revision_apply_report.json` within the round because each apply run appends its execution records. After synchronization, review newly ready tasks and any same-target/dependency invalidations. Refresh affected evidence and paths against the latest outputs, obtain renewed authorization and build the next conflict-free batch. Do not apply a downstream task against an outdated DOCX.

## Clean views, final Word/PDF locations and Response Letter

After the in-scope Patch batches are verified, produce separate accepted-changes projections:

    python scripts/co_reviewer_cli.py create-working-clean-views --ledger revision_ledger.json

Working Clean Views are intermediate review projections, not final Word-layout Clean deliverables. Inspect Marked and Working Clean DOCX files together and check claims, sample identities, numerical values, units, figures/tables and cross-references across Manuscript and SI.

After the author confirms each ResponseUnit's wording and semantic consistency, record that review:

    python scripts/co_reviewer_cli.py confirm-response-units --ledger revision_ledger.json --ids RU-T1-1 --semantic-review-confirmed --actor "Human Author"

On Windows with Microsoft Word installed and the optional Word/PDF dependencies, generate final Word-layout Clean DOCX files, PDFs and actual page/line locations:

    python scripts/co_reviewer_cli.py map-response-locations --ledger revision_ledger.json --out-dir word_pdf_final

The page and line references must come from real Word COM pagination and corresponding PDFs. XML paragraph indices are not page/line numbers. If Word COM is unavailable or a target is unsupported for reliable location mapping, leave the ResponseUnit incomplete and report the blocker.

Only after the author accepts readiness and PDF-backed location evidence is attached, complete the relevant ResponseUnits:

    python scripts/co_reviewer_cli.py complete-response-units --ledger revision_ledger.json --ids RU-T1-1 --ready-for-submission-confirmed --actor "Human Author"

Render Response Letter files from the RevisionLedger:

    python scripts/co_reviewer_cli.py render-response --revision-ledger revision_ledger.json --out output/Response_to_Reviewers.md
    python scripts/co_reviewer_cli.py render-response-docx --revision-ledger revision_ledger.json --out output/Response_to_Reviewers.docx --manuscript-title "Manuscript title" --author "Corresponding Author"

A semantically reviewed ResponseUnit without final PDF mapping is still location-pending, not submission-ready. Keep draft/location-pending/completed labels intact. Never invent response wording or page/line locations.

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


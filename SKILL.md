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

Run the CLI with available input files:

    python scripts/co_reviewer_cli.py inspect --rev Reviewers.docx --man Manuscript.docx --si SI.docx --out workspace_digest.md --json-out workspace_inventory.json

Omit unavailable arguments. Review both ordinary text paragraphs in the Reviewer Comments DOCX and native Word comments/anchors, plus paragraph/table indices, figure/table captions, drawings, existing Track Changes, placeholders and Chinese notes. The JSON inventory retains all non-empty paragraphs; the Markdown digest shows a bounded reviewer-text excerpt. Chinese notes are review flags, not automatic errors.

Use --comment-author only when filtering by the comment author's name. Do not filter comments by the manuscript author's name.

### Stage 2 — Build a revision plan

Create a JSON plan following schemas/revision-plan.schema.json. Assign each reviewer concern a stable ID such as R2-C3. Each revision item may link multiple targets in Manuscript and SI. Record document role, input/output paths, Word XML part, paragraph index, precise existing text anchor, replacement and a readable location.

Include the proposed response. Scientific content must be traceable to supplied data, figures, tables, calculations or verified sources.

### Stage 3 — User review and approval

Present the reviewer comment, proposed response, affected files/locations, exact proposed edits, evidence questions and conflicts. Ask the user to approve or revise the plan. Approval may be partial: only selected revision items should be marked approved.

Approval may be partial. Confirm the exact Revision Item IDs that the user approved; do not manually edit status or fingerprint fields. Stage 4 records this approval with `approve-plan`. Any later change to a comment, proposed response or target invalidates that item's approval, and the revised scope must be shown to the user and approved again. A planned/unapproved task must never be applied.

### Stage 4 — Apply

After Stage 3 user approval, record the selected approvals, then validate:

    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan.json --ids R2-C3 R3-C1
    python scripts/co_reviewer_cli.py validate-plan --plan edits_plan.json

Then apply:

    python scripts/co_reviewer_cli.py apply --plan edits_plan.json --author "Author Name" --report revision_apply_report.json

Inputs are immutable. Write revised copies to distinct output paths. After the CLI confirms success, it automatically persists successfully applied items as applied in the plan. Only items explicitly marked approved are applied; planned, applied, verified and blocked items remain untouched. Reuse the same apply-report path across incremental iterations; the CLI appends each iteration so previous revision evidence remains available for full-plan verification. A target wholly contained in one simple hyperlink may be edited inside that hyperlink while preserving its relationship and wrapper. Targets crossing a hyperlink boundary, fields, drawings, bookmarks, content controls or other unsupported XML must still be refused. If a target is refused, report the location and request a safer anchor or use a separately reviewed editing route. Never bypass the refusal by replacing the whole paragraph.

### Stage 5 — Verify and report

Run the mechanical verification command:

    python scripts/co_reviewer_cli.py verify --plan edits_plan.json --apply-report revision_apply_report.json --out revision_verification.json

This checks the exact Word revision IDs recorded by the apply report and confirms that those specific deletion/insertion elements contain the expected text at the reported location. It does not validate scientific meaning. Then inspect the actual revised documents and check that Reviewer responses, Manuscript and SI agree on claims, values, units, sample names and figure/table numbering.

Only after both mechanical and semantic checks pass, change the corresponding plan item status to verified. Render the Response Letter draft:

    python scripts/co_reviewer_cli.py render-response --plan edits_plan.json --out Response_to_Reviewers.md

The Markdown and DOCX renderers never invent response text. Items not marked verified must remain visibly labelled as drafts; do not submit them as completed responses. Use `render-response-docx` for an editable Word response letter and `status` to summarize task progress. Return the response draft, revised Manuscript and SI files, verification report and unresolved items.

## Token and delegation rules

- Delegate deterministic parsing, indexing, XML operations and mechanical checks.
- Keep Supervisor context to compact reports and selected evidence.
- Batch independent operations where safe; do not impose a fixed maximum number of Worker calls.
- Reuse inventory for unchanged source files; re-inspect changed inputs or requested scopes only.
- Before starting any OpenCode Worker, first inspect the currently available OpenCode Go models, then show the user the proposed role, selected model, active project root, inputs, expected outputs and validation boundary. Do not start a Worker invisibly; obtain explicit authorization first. After completion, report Job ID, Session ID, project, role, model, final state, concise result summary and token usage. Every Worker task must identify role, model, project, inputs, expected outputs and validation requirements.

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

Create a JSON plan following schemas/revision-plan.schema.json. Assign each reviewer concern a stable ID such as R2-C3. Each revision item may link multiple targets in Manuscript and SI. Record document role, input/output paths, Word XML part, paragraph index, precise existing text anchor, replacement and a readable location. For body text use `word/document.xml`; header/footer text uses its specific `word/headerN.xml` or `word/footerN.xml` part. The paragraph index is local to that part, so take it from that part's inventory entry rather than reusing a body index.

Include the proposed response. Scientific content must be traceable to supplied data, figures, tables, calculations or verified sources.

### Language, tense and sentence-context gate

Before presenting any proposed edit for approval, inspect the complete sentence containing the anchor and enough adjacent text to understand its time reference. Draft the replacement in context, not as an isolated phrase. For every changed sentence:

- Preserve the tense already established by the surrounding scientific narrative unless the reviewer explicitly requires a change in time reference. Do not convert all revisions mechanically to past or present tense.
- As a default convention, describe completed experiments, procedures and observations in the past tense; use present tense for established scientific facts, generally valid statements, and what a figure/table currently shows. Interpret results in the tense that matches the claim and the surrounding paragraph.
- Check subject–verb agreement, singular/plural, articles, prepositions, parallel structure, pronoun/reference clarity and transitions into the unchanged text.
- Read the full sentence before and after the edit, then read the whole paragraph once more. An insertion that is grammatically correct in isolation but makes the paragraph tense or logic inconsistent must be revised before approval.
- Show the user the original sentence, proposed complete sentence and exact replacement span. Briefly note the tense choice where it is not obvious.
- If the evidence or temporal meaning is ambiguous, flag the uncertainty and ask rather than guessing. Never change scientific meaning, sample identity, numerical values or causal strength merely to improve English.

### Scientific notation and Word character formatting

Use native Word character formatting in delivered DOCX files. When proposing a new subscript or superscript in a replacement, encode it with the corresponding Unicode subscript/superscript characters in the plan (for example, `SO₄²⁻`); the revision engine converts supported characters to ordinary base characters inside runs with native Word `w:vertAlign` formatting. Do not substitute caret notation such as `SO4^2-`, HTML tags, or manually typed baseline digits when a true subscript/superscript is intended. The engine fails closed for unsupported Unicode characters explicitly named as subscript/superscript; flag them for an explicit formatting route. During review, inspect both the rendered appearance and the underlying Word formatting.

### Stage 2.5 — Preflight the source documents

Before asking for approval, run the no-write preflight:

    python scripts/co_reviewer_cli.py preflight-plan --plan edits_plan.json --out preflight_report.json

This checks that all planned/approved anchors are unique and safely editable, prepares the proposed edits in memory without writing any DOCX, records a SHA-256 fingerprint of each exact input DOCX in the relevant targets, and stores an item-level fingerprint over the complete preflighted target set. Present the preflight result and affected source files to the user. A failed preflight blocks approval/application until the target is corrected. If a source DOCX or any target field changes later, rerun preflight and obtain renewed approval for every affected item.

### Stage 3 — User review and approval

Present the reviewer comment, proposed response, affected files/locations, exact proposed edits, evidence questions and conflicts. Ask the user to approve or revise the plan. Approval may be partial: only selected revision items should be marked approved.

Approval may be partial. Confirm the exact Revision Item IDs that the user approved; do not manually edit status or fingerprint fields. Stage 4 records this approval with `approve-plan`. Any later change to a comment, proposed response, target or preflighted source DOCX version invalidates that item's approval, and the revised scope must be shown to the user and approved again. A planned/unapproved task must never be applied.

### Stage 4 — Apply

After Stage 3 user approval, record the selected approvals, then validate:

    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan.json --ids R2-C3 R3-C1
    python scripts/co_reviewer_cli.py validate-plan --plan edits_plan.json

Then apply:

    python scripts/co_reviewer_cli.py apply --plan edits_plan.json --author "Author Name" --report revision_apply_report.json

Inputs are immutable. Write revised copies to distinct output paths. After the CLI confirms success, it automatically persists successfully applied items as applied in the plan. Only items explicitly marked approved are applied; planned, applied, verified and blocked items remain untouched. Reuse the same apply-report path across incremental iterations; the CLI appends each iteration so previous revision evidence remains available for full-plan verification. Before applying, the CLI rejects plan/report paths that collide with any referenced DOCX input or output. A target wholly contained in one simple hyperlink may be edited inside that hyperlink while preserving its relationship and wrapper. Targets crossing a hyperlink boundary, fields, drawings, bookmarks, content controls or other unsupported XML must still be refused. If a target is refused, report the location and request a safer anchor or use a separately reviewed editing route. Never bypass the refusal by replacing the whole paragraph.

### Marked-version Track Changes integrity gate

- Every textual change in a Marked Manuscript or SI must be represented by native Word Track Changes: removed text inside `w:del`/`w:delText`, and added text inside `w:ins`/`w:t`. This is the acceptance criterion; font color is not.
- All changes must be applied through the approved revision plan and `co_reviewer_cli.py apply`. Never directly rewrite a paragraph/run with `python-docx`, replace XML text nodes, use a second DOCX writer, or edit the Marked output in Word with Track Changes turned off. Do not let another writing worker save over the Marked DOCX.
- If a target cannot be represented safely as native Track Changes, block that target and report it. Do not silently make the text change directly, even if the final wording is correct.
- The `verify` command checks each planned deletion/insertion revision ID and exact text, then audits the whole output DOCX against its source. Text outside native revision wrappers must be unchanged except for the original anchor text removed into the planned deletion. Any other untracked text difference fails verification.
- Text color is controlled by Word's review display preferences and is not used to decide whether a change is a tracked revision. Never fake Track Changes with color alone.

### Stage 5 — Verify and report

Run the mechanical verification command:

    python scripts/co_reviewer_cli.py verify --plan edits_plan.json --apply-report revision_apply_report.json --out revision_verification.json

This checks exact Word revision IDs, deleted/inserted text, whole-document untracked-text integrity, and whether recorded native subscript/superscript formatting survives the DOCX save round-trip. It does not validate scientific meaning. Then inspect the actual revised documents and check that Reviewer responses, Manuscript and SI agree on claims, values, units, sample names and figure/table numbering.

Only after both mechanical and semantic checks pass, record the state through the guarded command (never edit `status` manually):

    python scripts/co_reviewer_cli.py confirm-verified --plan edits_plan.json --apply-report revision_apply_report.json --ids R2-C3 R3-C1 --semantic-review-confirmed

The command re-runs exact Track Changes verification against the current output DOCX files, records their SHA-256 hashes, and requires an explicit semantic-review acknowledgement. If a revised DOCX changes later, plan validation and Response Letter rendering reject the stale verified state. This is a workflow-integrity check, not an identity signature or an automated judgment of scientific correctness.

Render the Response Letter draft:

    python scripts/co_reviewer_cli.py render-response --plan edits_plan.json --out Response_to_Reviewers.md

The Markdown and DOCX renderers never invent response text. Items not marked verified must remain visibly labelled as drafts; do not submit them as completed responses. Use `render-response-docx` for an editable Word response letter and `status` to summarize task progress. Return the response draft, revised Manuscript and SI files, verification report and unresolved items.

## Token and delegation rules

- Delegate deterministic parsing, indexing, XML operations and mechanical checks.
- Keep Supervisor context to compact reports and selected evidence.
- Batch independent operations where safe; do not impose a fixed maximum number of Worker calls.
- Reuse inventory for unchanged source files; re-inspect changed inputs or requested scopes only.
- Before starting any OpenCode Worker, first inspect the currently available OpenCode Go models, then show the user the proposed role, selected model, active project root, inputs, expected outputs and validation boundary. Do not start a Worker invisibly; obtain explicit authorization first. After completion, report Job ID, Session ID, project, role, model, final state, concise result summary and token usage. Every Worker task must identify role, model, project, inputs, expected outputs and validation requirements.

# Co_Reviewer v0.5.1 Workflow Engine Contract

## Why this layer exists

The review-decision plan and the legacy DOCX revision plan serve different jobs. The decision plan records reviewer-facing tasks, evidence, proposed actions and dependencies. The legacy revision plan is an execution payload for concrete DOCX edits and correctly requires at least one document target.

The v0.5.1 Workflow Ledger is the common lifecycle layer between those concerns. It keeps response-only work, author decisions and evidence requests in the same review-round task graph without pretending that they are DOCX edits.

## Canonical lifecycle

| State | Meaning | Gate to advance |
|---|---|---|
| identified | Comment/task has been inventoried | Task is classified |
| planned | Proposed action and evidence are recorded | Evidence and target scope are adequate |
| blocked | Progress is unsafe or impossible | Explicit reason and a resolved blocker |
| ready | Dependencies and evidence are ready for preflight | Preflight starts |
| preflighted | Source and target preconditions were checked | Human approves the exact scope |
| approved | Exact task scope has human approval | Only then may a patch be applied |
| applied | A change has been written to a working document | Mechanical and semantic verification |
| verified | Required checks and human semantic review are recorded | Final location/response assembly |
| completed | Linked output and response obligations are complete | Any upstream change forces recheck |
| recheck_required | A prior decision or result may be stale | Re-evaluate and re-approve as needed |

The engine enforces transition order and state evidence. Moving to \`preflighted\` requires a source/input SHA-256 fingerprint, report ID and timezone-aware timestamp. Moving to \`approved\` requires an explicit human-confirmation record, approver and timestamp; the approval fingerprint binds that record to the exact task scope and preflight. Applying requires an output fingerprint and execution report. Verification requires a report and explicit semantic-review confirmation. Completion requires a separate completion record.

These fingerprints are integrity checks, not cryptographic proof of a person's identity. A production UI/CLI adapter must only set \`human_approval_confirmed\` from a direct user action and must retain the corresponding event/audit record. The engine does not claim that a state label proves scientific correctness: evidence grounding, deterministic checks and human semantic review remain separate acceptance gates.

A change to the task scope (targets, evidence, proposed action, dependencies or target groups) invalidates the existing preflight/approval fingerprints. Upstream changes move every dependent task to \`recheck_required\`; returning from that state clears previous execution/approval/verification evidence so it cannot be reused silently.

## Scheduling rules

1. The task DAG is authoritative. Unknown dependencies and task-level cycles are rejected.
2. A task can be selected only when it is explicitly ready and every dependency is verified or completed. One returned batch is target-conflict-free: at most one task may reserve a target-group conflict component, while tasks without document targets can proceed independently.
3. Target groups are derived from stable document objects when TargetRefs exist; the legacy document/part/p_idx locator remains a compatibility fallback. The quotient graph maps task dependencies onto target groups.
4. When that quotient graph contains a cycle although the task DAG is acyclic, its strongly connected target groups are condensed into one lock/review component. The task-level order inside that component remains authoritative.
5. After a Patch is verified and synchronized, every other task in the same target-conflict component and every transitive dependent task moves to `recheck_required`. Its previous status is retained in `recheck_from`; old approvals and verification are not reused.
6. ResponseUnits linked to invalidated tasks reopen as drafts. Semantic confirmation, final location map and PDF page/line references are cleared; their responses must be reviewed again against the updated document output.

## Response-only and DOCX execution boundary

task_type=response_only is a valid Workflow Ledger task and has no document target group. It stays in the unified review lifecycle and is materialized as a ResponseUnit without a fake DOCX Patch.

The legacy revision_plan.schema.json and validate_plan() remain DOCX-edit-only. They continue to require non-empty targets; response-only tasks remain in the RevisionLedger/ResponseUnit pipeline and are never passed to the DOCX writer.

## Current anchor and migration limits

The identity layer defines five distinct document dimensions: exact source bytes, normalized visible content, document structure, formatting state and citation state. A CitationProfile records the explicit citation mode, style label, numbering scope, examples, reference labels, detection method and optional evidence fingerprint. These dimensions are independent; a content edit need not be mistaken for a formatting or citation edit.

Persistent object IDs are represented by DocumentObjectIdentity records and are kept separate from location hints such as paragraph indexes. The parser-backed `build-document-identity` command reads actual DOCX WordprocessingML, validates every Inventory paragraph anchor against DOCX text and native `w14:paraId`, then creates document dimensions and the object registry. A unique native `paraId` or a unique exact fingerprint may preserve an object ID across revisions; duplicate/ambiguous matches receive new IDs instead of guessed continuity.

TargetRef binds a task to a document revision, object ID, object fingerprint, Word part and optional character offsets checked against parser-derived visible-text length. Supplying `--identity-manifest` to `validate_decision_plan.py` converts plan anchors into stable TargetRefs and schedules by `document_id|object=object_id`. The legacy `document|part|p_idx` group remains only as a compatibility path when no identity manifest is supplied. Workflow Ledger validation binds each DocumentIdentity source hash to the same role's `source_files` hash.

The Workflow Engine is used by decision-plan validation, ledger creation/validation, ready-batch materialization and execution synchronization. `build-revision-ledger` converts only the current conflict-free ready batch into typed Patches; those Patches are bridged to the legacy DOCX writer with fresh preflight and explicit approval. After verified changes, same-target peers and downstream tasks are invalidated before the next batch can be authorized. A reapproved task is rebased from the previous verified DOCX output, creates a new superseding Patch ID, and retains prior execution history. The CLI remains the controlled execution bridge rather than silently applying work.

## Patch execution bridge

The implementation now has separate typed records for \`PatchOperation\`, \`Patch\`, \`RevisionDocument\`, \`RevisionEvent\` and \`RevisionLedger\`.

A decision task is not automatically converted into a text edit. A task may contain \`patch_operations\` only when the authoring stage supplies the exact \`find\`, \`replace\`, Word part, source/target DOCX paths and evidence IDs. Missing or inferred replacement operands are rejected. The bridge exports these explicit operations into the existing DOCX revision-plan format; the existing WordprocessingML/Track Changes writer remains the only writer.

The recommended command sequence is:

1. \`build-revision-ledger --workflow-ledger ... --out revision_ledger.json\` — validate the Workflow Ledger and create typed Patch and ResponseUnit records.
2. \`export-revision-plan --ledger revision_ledger.json --out revision_plan.json\` — emit only new planned Patches as a pending, unapproved DOCX execution plan.
3. \`preflight-plan --plan revision_plan.json --revision-ledger revision_ledger.json\` — bind the patch to exact source DOCX hashes and record the preflight event.
4. \`approve-plan --plan revision_plan.json --ids ... --revision-ledger revision_ledger.json\` — seal the exact reviewer-facing scope and record the approver.
5. \`apply --plan revision_plan.json --revision-ledger revision_ledger.json\` — execute through the legacy writer and synchronize Patch state/events.
6. \`confirm-verified --plan revision_plan.json --apply-report ... --ids ... --semantic-review-confirmed --revision-ledger revision_ledger.json\` — recheck native Track Changes and output hashes, then record the human semantic-review acknowledgement.
7. \`create-working-clean-views --ledger revision_ledger.json\` — create immutable accepted-changes projections from the final Marked DOCX outputs and bind both file hashes into the ledger.

When a RevisionLedger is supplied, the adapter checks patch IDs, reviewer, original comment, proposed response and every exact target before preflight/apply/verify. A mismatch stops the operation. Exported legacy plans are always pending; approval is never silently copied from a previous ledger state.

## Working Clean View

\`create-working-clean-views --ledger revision_ledger.json\` creates one derivative DOCX per marked \`RevisionDocument\`. It accepts native insertions and move-to text, rejects deletions and move-from text, removes revision property snapshots and comment anchors/parts, and turns off tracking in the derivative. It never edits the source or Marked DOCX.

The output path, exact Marked DOCX hash, Working Clean View hash and projection-policy fingerprint are persisted in the RevisionLedger. The ledger rechecks both DOCX files before serialization, so a later change makes the saved Working Clean View evidence stale.

This is an internal accepted-changes projection for downstream analysis and cross-document consistency checks. It is not the final publication Clean Manuscript and does not establish actual Microsoft Word pagination. The projection fails closed on table-structure revision elements it cannot safely resolve rather than guessing the table state.

## Incremental Patch supersession

The \`supersede-patch\` command creates a new planned Patch with a new ID and an explicit \`supersedes_patch_id\`. The predecessor's record and apply/verification history remain in the RevisionLedger. If the predecessor had already been applied or verified, every replacement operation must use its output DOCX as the new input.

Supersession reopens linked ResponseUnits as drafts, relinks them to the replacement Patch, and clears semantic-review confirmation and old final PDF locations. This prevents an earlier response or line map from being carried forward after a document change. The replacement must pass preflight, explicit author approval, apply and verification again. `build-revision-ledger` can create this replacement automatically only after the author has reapproved the changed task scope; it preserves the prior Patch and records a new ID/lineage.

Only new \`planned\` Patches are exported to a fresh legacy revision plan. Already preflighted, approved, applied, verified or superseded Patches are never silently reset to pending for replay. The lineage validator rejects cross-task replacements, duplicate successors, unknown predecessors and cycles.

## ResponseUnit closure

A ResponseUnit is independent of DOCX Patch execution. Its lifecycle is:

\`draft → verified → location_mapped → completed\`

- \`confirm-response-units\` records an explicit semantic-review report. It does not claim that page/line locations are known.
- \`record-response-locations\` consumes a location-map JSON artifact. Every final location must include its actual PDF path and SHA-256; the CLI also records the mapping JSON file path and SHA-256. The model rechecks these file hashes before serializing a mapped or completed ledger.
- \`complete-response-units\` requires an explicit ready-for-submission acknowledgement and rejects a ResponseUnit while any linked Patch is unverified.
- \`render-response\` and \`render-response-docx\` can consume the RevisionLedger. A semantically reviewed ResponseUnit without final PDF locations is rendered as \`location_pending\`, not as a final verified response.

The Windows-only `map-response-locations` producer generates the final Clean DOCX and PDF with Microsoft Word COM, reads the actual page/line positions after Word pagination, verifies target text on the corresponding PDF page, and binds both outputs by SHA-256. It remains pending execution/acceptance on the user's installed Windows/Word environment. A manually typed page/line string is not sufficient evidence by itself.

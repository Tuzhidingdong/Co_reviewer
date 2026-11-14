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
2. A task can be selected for execution only when it is explicitly ready and every dependency is verified or completed.
3. Target groups are derived from the document objects a task may change. The quotient graph maps task dependencies onto target groups.
4. When that quotient graph contains a cycle although the task DAG is acyclic, its strongly connected target groups are condensed into one lock/review component. The task-level order inside that component remains authoritative.
5. If an upstream task changes, every transitive dependent task moves to recheck_required. The prior state is retained in recheck_from; the engine does not silently preserve a downstream approval or verification.

## Response-only and DOCX execution boundary

task_type=response_only is a valid Workflow Ledger task and has no document target group. It stays in the unified review lifecycle and can later be linked to a ResponseUnit.

The legacy revision_plan.schema.json and validate_plan() remain DOCX-edit-only. They continue to require non-empty targets; response-only tasks must not be passed to that executor. A later adapter will emit DOCX revision items only for mutating tasks and keep response-only items in the ledger/response pipeline.

## Current anchor and migration limits

The identity layer now defines five distinct document dimensions: exact source bytes, normalized visible content, document structure, formatting state and citation state. A CitationProfile records the explicit citation mode, style label, numbering scope, examples, reference labels, detection method and optional evidence fingerprint. These dimensions are independent; a content edit need not be mistaken for a formatting or citation edit.

Persistent object IDs are represented by DocumentObjectIdentity records and are kept separate from location hints such as paragraph indexes. TargetRef binds a task to a document revision, object ID, object fingerprint, Word part and optional character offsets. The Workflow Ledger can persist document identities and an object registry; it validates that each TargetRef resolves to the matching registered object and revision.

When TargetRefs are supplied to build_workflow_ledger(), the scheduler prefers stable document/object groups (document_id|object=object_id). Unmigrated tasks fall back to document|part|p_idx groups. The p_idx fallback is provisional and should disappear once the parser/registry migration is complete. Generated object IDs must be persisted in the object registry and reused only when the object mapping across revisions has been confirmed.

The Workflow Engine is used by pending decision-plan validation, ledger creation/validation and scheduling summaries. The old CLI revision-item apply/verify commands remain compatible and are not yet fully migrated to the ledger. Typed Patch/RevisionLedger execution, Working Clean View, Word COM pagination, final PDF line mapping and the complete ResponseUnit renderer remain later integration stages.

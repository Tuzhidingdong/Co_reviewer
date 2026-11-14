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

The engine enforces transition order. It does not claim that a state label proves scientific correctness: evidence grounding, deterministic checks and human semantic review remain separate acceptance gates.

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

The current decision-plan adapter identifies a provisional target group as document|part|p_idx. This is only a bridge from the v0.3 inventory model. It is not a persistent identity. P0 identity work must replace paragraph indexes with stable internal object IDs and bind target groups to document/object fingerprints.

The Workflow Engine is now used by pending decision-plan validation and scheduling summaries. The old CLI revision-item apply/verify commands remain compatible and are not yet fully migrated to the ledger. Word COM pagination, final PDF line mapping, patch supersession and the complete ResponseUnit renderer remain later integration stages.

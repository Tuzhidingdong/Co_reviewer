# Co_Reviewer v0.5.1 P0 Implementation Status

## Branch and safety boundary

- Repository: \`Tuzhidingdong/Co_reviewer\`
- Implementation branch: \`upgrade/v0.5.1-p0-implementation-20261114\`
- Base branch: \`upgrade/execution-integrity-v0.3\`
- Base commit: \`7b0fef325a5f8282a39259d39bb3cc0118e24a68\`
- Code head before this status update: \`57184d869910469e3e9860cab204445b701a8829\`
- \`main\` is unchanged; no PR has been merged.
- No formal installed Skill directory, manuscript, SI, reviewer file or user experiment data has been modified.
- No Worker has been started.

## Implemented

### P0-01 — Preserve the v0.3 regression baseline

- Added \`docs/BASELINE_V0.3.md\`, retaining the inventory of all 76 original test methods.
- Existing regression tests have not been removed or weakened. New tests are additive.
- The historical local pass on the original v0.3 source is not evidence that this branch passes.

### P0-02 — Establish behavior-preserving module boundaries

- Added \`scripts/co_reviewer/document_engine.py\` for the existing WordprocessingML and native Track Changes implementation.
- Added \`scripts/co_reviewer/identity.py\` to centralize the existing file and workflow fingerprints.
- Kept \`scripts/co_reviewer_cli.py\` as the compatibility entry point and routed existing functions through the extracted modules.
- This identity extraction is not yet the complete stable-object/citation identity system.

### P0-03 — Introduce the common Workflow Engine

Added \`scripts/co_reviewer/workflow_engine.py\` with:

- One lifecycle state model: \`identified → planned → ready → preflighted → approved → applied → verified → completed\`, plus explicit \`blocked\` and \`recheck_required\` paths.
- Task DAG validation and deterministic topological scheduling.
- Target Group Quotient DAG construction. Cycles introduced only by shared target groups are condensed into lock/review components; actual task-level dependency cycles remain errors.
- Readiness gating: a task is selectable only when it is explicitly ready and its dependencies are verified or completed.
- Downstream invalidation: changing an upstream task moves all transitive dependents to \`recheck_required\`.
- Scope/input, approval, output, verification and completion fingerprints. Direct task-scope changes make previous preflight/approval fingerprints stale.
- Evidence requirements for preflight, explicit human approval, execution, semantic verification and completion.
- Decision-plan adaptation that preserves the evidence and task payload instead of flattening it.

### P0-04 — Unify the decision plan without breaking the DOCX executor

- \`scripts/validate_decision_plan.py\` now consumes the shared Workflow Engine for dependency validation and scheduling.
- Its CLI reports the task and target-group schedule summary.
- Added \`schemas/workflow-ledger.schema.json\`, \`build_workflow_ledger()\` and \`validate_workflow_ledger()\`.
- Added \`docs/WORKFLOW_ENGINE.md\` to define lifecycle rules and boundaries.
- A \`response_only\` task remains in the Workflow Ledger and has no document target group.
- The legacy \`revision-plan.schema.json\` and \`validate_plan()\` intentionally remain DOCX-edit-only and continue requiring non-empty document targets. The new ledger is the common layer; response-only work is not forced into a fake DOCX edit.

## Regression and CI status

- Original baseline: 76 test methods.
- Added tests: 9 module-boundary tests, 14 Workflow Engine tests, and 2 decision-plan/ledger integration tests.
- Expected current total: 101 test methods. This is an inventory count, not a test-pass claim.
- The current branch has not yet been executed on the user's Windows Python environment.
- GitHub Actions previously failed before runner assignment: both matrix jobs reported \`runner_id=0\`, no runner name and no steps. No checkout, compilation or test execution occurred. This is an infrastructure/runner blocker, not evidence of a Python test failure.
- Microsoft Word GUI opening, Track Changes rendering, save-round-trip, pagination and PDF line mapping are also not yet accepted.

## Remaining roadmap

1. **Finish P0 identity/model foundation:** stable \`TargetRef\`/object IDs, document and citation identity dimensions, and migration away from paragraph-index target-group keys.
2. **Connect the execution layer:** typed \`Patch\` and \`RevisionLedger\`, patch supersession/conflict detection, and a Working Clean View; keep the existing DOCX engine as the writer.
3. **Complete final-document location:** Word COM pagination and actual PDF-backed page/line mapping; Response Units must consume this final location map.
4. **End-to-end acceptance:** run the full regression suite on Windows, then separate synthetic Word GUI/save-round-trip and final PDF/Response Letter integration checks.

## Verification boundary

A structurally valid plan, a passing unit suite or a successful DOCX XML write does not establish scientific correctness. The evidence chain, cross-document consistency and semantic acceptability remain separate review gates, with scientific claims grounded in the user's supplied results.

# Co_Reviewer v0.5.1 P0 Implementation Status

## Branch and safety boundary

- Repository: \`Tuzhidingdong/Co_reviewer\`
- Implementation branch: \`upgrade/v0.5.1-p0-implementation-20261114\`
- Base branch: \`upgrade/execution-integrity-v0.3\`
- Base commit: \`7b0fef325a5f8282a39259d39bb3cc0118e24a68\`
- Code head before this status update: \`e26cb81576b6db22bff56647a50e9b7f268fed61\`
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
- Added \`scripts/co_reviewer/identity.py\` to centralize the existing file and workflow fingerprints, then extended it with the v0.5.1 identity foundation.
- Kept \`scripts/co_reviewer_cli.py\` as the compatibility entry point and routed existing functions through the extracted modules.
- The existing identity hashes remain available; new semantic identity dimensions are additive.

### P0-03 — Introduce the common Workflow Engine and durable ledger

Added \`scripts/co_reviewer/workflow_engine.py\` with:

- One lifecycle state model: \`identified → planned → ready → preflighted → approved → applied → verified → completed\`, plus explicit \`blocked\` and \`recheck_required\` paths.
- Task DAG validation and deterministic topological scheduling.
- Target Group Quotient DAG construction. Cycles introduced only by shared target groups are condensed into lock/review components; actual task-level dependency cycles remain errors.
- Readiness gating: a task is selectable only when it is explicitly ready and its dependencies are verified or completed.
- Downstream invalidation: changing an upstream task moves every transitive dependent to \`recheck_required\`.
- Scope/input, approval, output, verification and completion fingerprints. Changes to a task's evidence, proposed action, dependencies or targets make prior preflight/approval fingerprints stale.
- Evidence requirements for preflight, explicit human approval, execution, semantic verification and completion.
- Decision-plan adaptation that preserves the evidence and task payload instead of flattening it.
- \`build_workflow_ledger()\` and \`validate_workflow_ledger()\` for a persistent per-round task ledger.

The old \`revision-plan.schema.json\` and \`validate_plan()\` remain DOCX-edit-only and continue requiring non-empty document targets. The new Workflow Ledger is the common task layer; response-only work is not represented as a fake DOCX edit.

### P0-04 — Establish five-dimensional document and object identity

Extended \`scripts/co_reviewer/identity.py\` with:

- Exact source-byte SHA-256.
- Normalized visible-content SHA-256.
- Structure SHA-256.
- Formatting SHA-256.
- Citation-state SHA-256, including a versioned \`CitationProfile\` for citation mode, style, numbering scope, examples and detection evidence.
- Persistent \`DocumentObjectIdentity\` records whose object IDs are independent from paragraph-index location hints.
- Stable \`TargetRef\` references bound to a document revision, object ID, part, object fingerprint and optional character range.

Added \`schemas/document-identity.schema.json\`. The Workflow Ledger schema now accepts document identities, an object registry and task-level TargetRefs. Ledger validation checks that references resolve to the registered document revision and object fingerprint.

The scheduler prefers stable \`document_id|object=object_id\` groups when TargetRefs are supplied. The legacy \`document|part|p_idx\` key remains only as a migration fallback. Parser-backed identity registration and cross-revision object matching are not yet finished.

### Decision-plan integration

- \`scripts/validate_decision_plan.py\` now consumes the shared Workflow Engine for dependency validation and scheduling.
- Its CLI reports the task and target-group schedule summary.
- Added \`schemas/workflow-ledger.schema.json\` and \`docs/WORKFLOW_ENGINE.md\`.
- \`response_only\` tasks remain in the Workflow Ledger without document target groups or DOCX targets.

## Regression and CI status

- Original baseline: 76 test methods.
- Current added tests: 5 document-engine module tests, 12 identity tests, 16 Workflow Engine tests, and 2 additional decision-plan/ledger integration tests.
- Expected current total: 111 test methods. This is an inventory count, not a test-pass claim.
- The current branch has not yet been executed on the user's Windows Python environment.
- GitHub Actions previously failed before runner assignment: both matrix jobs reported \`runner_id=0\`, no runner name and no steps. No checkout, compilation or test execution occurred. This is an infrastructure/runner blocker, not evidence of a Python test failure.
- Microsoft Word GUI opening, Track Changes rendering, save-round-trip, pagination and PDF line mapping are also not yet accepted.

## Remaining roadmap

1. **Finish parser-backed identity migration:** create and persist object records from actual DOCX structures, map objects across revisions, profile real citation systems and remove the \`p_idx\` fallback from scheduling.
2. **Connect the execution layer:** typed \`Patch\`, \`RevisionLedger\` and \`ResponseUnit\` models; patch supersession/conflict detection; Working Clean View; migrate the existing DOCX writer through a compatibility adapter.
3. **Complete final-document location:** Word COM pagination and actual PDF-backed page/line mapping; Response Units must consume this final location map.
4. **End-to-end acceptance:** run the full regression suite on Windows, then separate synthetic Word GUI/save-round-trip and final PDF/Response Letter integration checks.

## Verification boundary

A structurally valid plan, a passing unit suite or a successful DOCX XML write does not establish scientific correctness. The evidence chain, cross-document consistency and semantic acceptability remain separate review gates, with scientific claims grounded in the user's supplied results.

Approval fingerprints and \`human_approval_confirmed\` are integrity/audit fields, not cryptographic proof of user identity. A production adapter must set approval state only from a direct user confirmation action and preserve that event.

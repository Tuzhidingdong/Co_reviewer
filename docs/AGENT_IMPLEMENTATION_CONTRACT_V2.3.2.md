# Agent Implementation Contract — Co_Reviewer V2.3.2 Global Closeout

This is the single implementation brief for the Agent. It supersedes ad-hoc one-defect-at-a-time prompts. The Agent executes the coordinated workstreams in `docs/GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md`; it does not redefine scope.

## Authority and branch

- Frozen requirements: `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md`.
- Normative architecture: `docs/V2.3.2_ENGINEERING_CONTRACT.md`.
- Current execution map: `docs/GLOBAL_GAP_AUDIT_V2.3.2.md`.
- Integrated task graph: `docs/GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md`.
- Work only on `upgrade/v0.5.1-p1-rc-closeout-20261120` / PR #9.
- Do not change the frozen plan, `main`, PR base, or merge state.
- Do not open parallel micro-PRs. Integrate work into the single RC branch.
- Do not ask the user to restate decisions already frozen in the plan.

## Mission

Complete all M1 implementation and engineering acceptance work AC-01–AC-50. M2 AC-51–AC-57 remains the user's final real-environment acceptance. Do not present the project for M2 before M1 is complete.

## Execution model

Treat WS-0 through WS-5 as one coordinated program with workstream checkpoints. Do not stop after fixing one failing test and ask for a new prompt. Inspect all dependent modules and implement the complete bounded workstream before reporting. Parallel workers are allowed only for genuinely independent workstreams; every worker must be explicitly authorized by the Supervisor with role/model/project/scope, and no invisible worker invocation is allowed.

### Required work

1. **WS-0:** use Git ancestry evidence; close only PRs proven already contained in PR #9. Keep PR #2 open and selectively audit/port its evidence pipeline only if it fills AC-07/§3.6 without duplication. Never bulk merge a divergent branch.
2. **WS-1:** complete AC-13/25–29/49 identity and structure safety scope; preserve fail-closed and authorization invariants.
3. **WS-2:** complete AC-30/31 Package-wide reference impact and atomic numbering transaction.
4. **WS-3:** fix the AC-45 source-version lifecycle invariant and close the continuous release package workflow.
5. **WS-4:** resolve the PR #2 cache/context optimization scope with a reasoned decision and selective port if needed.
6. **WS-5:** verify every M1 AC on one final HEAD; add acceptance evidence for existing modules marked IMPLEMENTED_UNVERIFIED; run one coordinated final CI + AC-45 + real Word COM/PDF acceptance; produce final RC evidence.

## Non-negotiable engineering invariants

- Original DOCX files are immutable.
- Marked is the only revision-bearing source of truth; Clean is deterministically derived from Marked.
- Every write uses the formal authorized Patch → preflight → approval → Apply → Verify → semantic confirmation → Publish path.
- No direct DOCX editing, bypass writer, gate skipping, or Clean repair.
- Never invent experiments, calculations, data, citations, references or scientific claims.
- Preserve historical versions and append-only audit provenance.
- Exactly one active version per logical document role.
- Initial source files are distinct from historical source-version records.
- Approval/fingerprint changes require invalidation and renewed authorization; never silently mutate approved scope.
- Package-wide numbering propagation is atomic: complete impact set and explicit author approval, or no changes.
- Safe Span is a candidate only; it never bypasses Token Lock, Protected Content, Authorized Change, history, formatting, approval or Verify.
- Unsupported OOXML structures remain fail-closed with precise diagnostics/manual fallback.
- Deferred Adapters remain disabled for automatic writing unless their own contracts and acceptance are complete.
- Test-paper content never enters production Skill/rules.

## Anti-churn / test discipline

- Do not repeat the already-green 407-test matrix or CI without a code change or specific regression reason.
- Do not run the full suite after each small commit.
- Add regression tests before fixes for confirmed defects.
- For each workstream, run focused tests once after the implementation is complete.
- Freeze source changes, then run the complete final matrix and one complete AC-45 workflow.
- If a test fails, diagnose from the failure evidence and make one bounded correction; do not repeat the same command without a changed hypothesis.
- Do not change assertions, fixtures or expected outputs merely to get green.
- Report real Word COM only when the actual COM runner ran; Word GUI/M2 must remain user acceptance.

## Checkpoint reporting

Provide only three substantive checkpoints, not a stream of micro-updates:

1. **Architecture/workstream closure:** source changes complete for WS-1/WS-2/WS-3/WS-4, with changed files, invariants and focused test evidence.
2. **Integrated acceptance:** one report of all final CI jobs, test counts, AC-45 stage outcomes, Word COM/PDF details and manifest/package hashes.
3. **Handoff:** final branch/HEAD, PR URL, M1 AC matrix, deferred adapters, residual risks and exact user M2 steps.

If a blocking design ambiguity is discovered that cannot be resolved from the frozen plan/contract, stop that specific operation and ask one precise question. Do not ask the user to decide implementation details already determined by the contract.

## Final deliverables

- Updated source/tests/schemas/docs in the single RC branch.
- Updated `docs/STATUS.md` with every AC and exact evidence HEAD.
- Updated `docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md` with the final successful continuous run.
- Final M1 RC report, including the enabled/deferred Adapter matrix and explicit M2 boundary.
- One Draft PR #9 kept unmerged pending the user's final authority.

# V2.3.2 Global Engineering Execution Plan

**Authority:** `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` (frozen Global REV2).  
**Baseline:** `upgrade/v0.5.1-p1-rc-closeout-20261120`.  
**Purpose:** One coordinated engineering convergence, not a sequence of isolated micro-fixes.  
**Plan changes:** This execution plan does not amend or relax the frozen acceptance criteria.

## 1. Delivery objective

Deliver one M1 Engineering RC candidate satisfying every M1 Blocking criterion AC-01–AC-50. Keep M2 AC-51–AC-57 as explicit user acceptance work. Do not hand the project to the user for M2 while M1 implementation blockers remain.

The full current gap assessment is in `docs/GLOBAL_GAP_AUDIT_V2.3.2.md`. Agent execution rules are in `docs/AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md`.

## 2. One integration line and repository governance

- Continue on `upgrade/v0.5.1-p1-rc-closeout-20261120` and PR #9. It is already descended from the frozen-plan baseline and contains the selected P1/V2.3.2 ancestry.
- Do not create parallel feature PRs for each small finding. Work in coordinated workstreams and integrate into this one branch.
- PR #1 and #3–#8 are ancestry already contained in PR #9; close them as superseded after adding a clear explanation. Do not merge them separately.
- Keep PR #2 open. Its 31 unique commits are divergent and its hash-keyed evidence extraction/cache tooling is absent from RC. Audit its feature against AC-07 and §3.6, then port only relevant files/tests/docs if justified. Do not merge its divergent branch wholesale.
- Do not modify `main`, do not merge PR #9, and do not change the frozen plan.

## 3. Coordinated workstreams

Workstreams may proceed in parallel only where their source files and invariants are independent. Integrate all results into the single RC branch before final validation.

### WS-0 — Baseline, branch convergence and scope salvage

**AC:** 01–04, 07 (scope review), 02.

1. Preserve the frozen plan and establish exact baseline parent/HEAD/hash inventory.
2. Close superseded PRs #1 and #3–#8 after confirming their heads are ancestors of PR #9.
3. Keep PR #2 open. Compare its `scripts/evidence_tools.py`, cache schema/keys, PDF/JSON/XML/CrossRef behavior, tests and optional dependencies with current cache/document-read/Word-PDF mechanisms.
4. Make an explicit scope decision for PR #2: selectively port in-scope hash-keyed extraction/context-digest capability, or record a reasoned defer decision. Never bulk merge its 31-commit divergent branch.
5. Keep one STATUS snapshot and one active PR.

**Exit:** one active engineering PR, historical PR disposition recorded, PR #2 scope decision explicit, frozen plan hash unchanged.

### WS-1 — Stable identity and OOXML structure/safety completeness

**AC:** 13, 25–29, 49; dependencies: existing document engine and safety gates.

1. Remove or strictly constrain legacy paragraph-index fallback so it cannot silently serve as stable object identity. Require stable Document/Object identity and explicit ambiguity/blocking where not resolvable.
2. Complete the structural event map needed by the frozen plan: Run, Text Node, XML order, bookmark start/end, comment range start/end, field boundaries, hyperlinks, drawings, revision wrappers and other protected structures in supported parts.
3. Generalize Bounded Safe Span only where exact contiguous mapping and marker boundaries are proven. Preserve fail-closed behavior for unsupported adapters.
4. Complete positive/negative/boundary coverage for single, nested, intersecting, adjacent, crossing and malformed bookmark/comment/field/hyperlink boundaries, including endpoints outside paragraph nodes where legal.
5. Preserve the rule that Safe Span only returns a candidate; Token Lock, Authorized Change, Protected Content, historical gate, formatting and immutable approval remain mandatory.
6. Ensure blocked operations produce exact hash-bound unapplied suggestions where evidence permits; no suggestion may be represented as a Patch or written into DOCX.
7. Maintain byte-identical protected parts and unchanged source hashes.

**Exit:** stable identity cannot rely on guessed p_idx; full supported structure event mapping and Adapter matrix pass; unsupported structures remain fail-closed with usable manual fallback.

### WS-2 — Reference Graph and Package-wide numbering transaction

**AC:** 30–31; dependencies: Patch/Workflow/Package Transaction.

1. Inventory definitions and mentions for Figure, Table, Equation, Section, Citation and other managed objects supported by the frozen contract.
2. Represent uniquely resolved, ambiguous and unresolved edges explicitly; distinguish static text from native Word fields.
3. Produce a complete Package-wide numbering impact plan, including every affected definition and mention across Manuscript/SI and the selected round.
4. Require explicit author approval bound to the exact immutable impact plan and source hashes.
5. Apply all related definition/mention changes as one Package Transaction; partial propagation must block.
6. Verify the final graph for broken, duplicated, conflicting and unresolved affected references. Unknown/ambiguous edges block the related transaction as a whole.
7. Keep unsupported citation/field writing adapters deferred and fail-closed; diagnostics still need complete coverage.

**Exit:** a single authorized numbering change either commits all known affected references atomically or commits none; full evidence and regression tests exist.

### WS-3 — Publication/source-version lifecycle and complete Release Package

**AC:** 32–45, with primary blockers 35, 42, 43, 45; depends on WS-0 baseline.

1. Resolve the AC-45 defect by separating:
   - append-only historical source-version provenance,
   - exactly one active source version per logical document role,
   - deduplicated initial source_files for the release.
2. Preserve batch-1 and batch-2 provenance. Never delete history or falsify parent lineage to satisfy a unique-role validator.
3. Do not mutate source_versions after preflight/approval in a way that changes the approved fingerprint. If any approved input changes, invalidate and re-run preflight/approval.
4. Preserve sequential publication, downstream invalidation/recheck, recovery and rollback semantics.
5. Generate a complete manifest and package after two sequential publications, using actual final Clean, Marked, Response Letter, PDF, mapping, quality reports, batch receipts, Workflow/RevisionLedger and transaction evidence.
6. Verify hashes and semantic correspondence across every package artifact. Re-running against the same intent must be deterministic/idempotent; changed source intent must require a new output directory.
7. Exercise negative release gates: missing/duplicate role, stale source hash, stale approval, incomplete ResponseUnit, failed quality report, unpublished batch and incomplete transaction must block.
8. Add the complete regression test first, prove it fails on the prior implementation, then fix the invariant.

**Exit:** the continuous AC-45 run reaches a READY Release Manifest and complete Release Package; all artifact hashes and source lineage validate.

### WS-4 — Cache/context optimization scope integration

**AC:** 07 and §3.6; dependent on WS-0 PR #2 audit.

1. Compare PR #2 evidence cache with current SHA-keyed inventory, document-read cache, Word/PDF mapping and local evidence artifacts.
2. Retain useful model-agnostic extraction only where it reduces repeated file parsing/context transfer without duplicating authoritative evidence or treating heuristics as verification.
3. If ported, include its tests, schema/versioned cache keys, optional dependency handling and docs. Preserve source SHA and parameters in cache identity; changed inputs/options invalidate cache.
4. PDF text/geometry and line-number candidates remain diagnostics, not visual or pagination proof. CrossRef indexing is not DOI authenticity verification.
5. Keep raw artifacts on disk; send compact digests by default. Never infer model-context exposure or token counts from filesystem reads/cache hits.

**Exit:** AC-07/context requirements are either demonstrably satisfied by the integrated cache path or a precise scope/defer record is added without changing the frozen plan.

### WS-5 — Whole-M1 verification and release evidence

**AC:** 03, 05–12, 14–24, 32–50.

1. Map each M1 AC to exact source/test/report evidence on one final code HEAD.
2. For existing implementation marked IMPLEMENTED_UNVERIFIED, do not add features by assumption; add the smallest acceptance test or workflow evidence needed to prove the requirement. Implement only if evidence exposes a concrete gap.
3. Confirm original 76 tests remain and are not weakened.
4. Confirm core deterministic tests do not require Word COM.
5. Run supported Python/Windows/PowerShell matrix once on the final integrated code.
6. Run the continuous two-publication workflow including dependency refresh, real Word COM/PDF mapping, Response Letter and Release Package.
7. Record CI run IDs, exact HEADs, test counts, COM version, artifacts and hashes. Do not mix evidence across commits.
8. Produce a truthful final RC report with enabled/deferred Adapter matrix and all remaining M2 items.

**Exit:** all AC-01–AC-50 M1 blocking criteria have evidence-based PASS status on one final HEAD; otherwise report specific remaining blocker and do not call RC ready.

## 4. Dependency order

```text
WS-0 baseline / ancestry / PR2 scope
  ├── WS-1 stable identity + structure safety ──┐
  ├── WS-2 reference graph + numbering txn ─────┤
  ├── WS-3 source-version + release lifecycle ──┼── WS-5 integrated M1 acceptance
  └── WS-4 cache/context salvage (after PR2) ───┘
```

WS-1, WS-2 and WS-3 can be implemented in parallel only with separate ownership and explicit interface contracts; their integration and final tests occur together. WS-4 is an isolated selective-port decision. WS-5 runs after all source changes freeze.

## 5. Test policy — one coordinated acceptance cycle

- Do not repeatedly run all tests after every small change.
- Each workstream adds focused tests and performs only local development checks needed to validate its own changes.
- At integration freeze, run the relevant cross-module tests once.
- Run the full supported CI matrix and AC-45 end-to-end workflow once against the final integrated HEAD.
- A failure produces one evidence-complete diagnosis and a bounded correction, not repeated blind retries.
- Never weaken assertions, skip failing cases, or replace real Word COM evidence with mocks.
- Do not ask the user to run BAT or perform Word GUI acceptance until M1 is ready for handoff.

## 6. Completion and handoff

M1 handoff requires:
- AC-01–AC-50 each marked PASS or explicitly DEFERRED only where the frozen plan permits it; no M1 blocker remains.
- One effective integration branch and one Draft PR with exact parent/HEAD.
- Final CI, complete synthetic workflow, actual Word COM/PDF evidence, manifest and package hashes.
- Updated STATUS and final RC report bound to the same HEAD.
- Explicit M2 instructions for AC-51–AC-57.

M2/GA still requires the user's Word GUI inspection, Save/Close/Reopen, scientific/structural inspection, real review round and explicit Release Package acceptance.

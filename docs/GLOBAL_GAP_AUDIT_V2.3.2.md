# V2.3.2 Global Gap Audit — Frozen Plan REV2

**Audit date:** 2026-11-20  
**Repository:** `Tuzhidingdong/Co_reviewer`  
**Normative source:** `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` on `docs/freeze-global-user-task-plan-v2.3.2-20261120`  
**Audited implementation line:** `upgrade/v0.5.1-p1-rc-closeout-20261120`, source/test baseline `fff6712b2395f52d2c1ebb66b71aa801c70bf13a`, documentation/CI head `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`  
**Plan protection:** This audit does not modify the frozen plan. It updates execution interpretation only.

## 1. Executive decision

The V2.3.2 implementation is **not yet complete apart from user acceptance**. Several M1 requirements still require implementation, not merely local acceptance:

- AC-31 Package-wide numbering propagation is only partially implemented and is not integrated as one authorized Package Transaction.
- AC-45 now has a real two-publication workflow test: A–D pass, but Release Package creation fails because source-version history/current-source identity is modelled inconsistently at the release boundary.
- AC-13 retains a legacy paragraph-index fallback.
- AC-25's complete structural event map is incomplete; AC-26/27 support a bounded bookmark subset, not the full required structure boundary matrix.
- AC-30's Reference Graph covers a subset of definition/mention types and relation states.
- AC-49's per-Adapter positive/negative/boundary/regression matrix is incomplete.
- AC-02 branch convergence is not complete because PR #2 contains a unique, divergent evidence-cache pipeline not present in the RC line.

This is therefore an engineering completion problem plus verification—not an M2-only acceptance handoff.

## 2. Evidence hierarchy and interpretation

The audit uses, in descending authority:

1. Frozen Global REV2 Task Plan and V2.3.2 Engineering Contract.
2. Current `docs/STATUS.md` and P0/P1 implementation records, checked against the Git graph.
3. PR compare metadata and file-level patches for PR #1–#9.
4. The real AC-45 run report supplied for run `run_20261120_144140` at HEAD `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`.

The AC-45 run used Python 3.13.9, actual Microsoft Word COM 16.0 and real CLI operations on synthetic DOCX files in an isolated worktree. It was not a mock workflow. It ran one existing integration test as a baseline; it did not rerun the 407-test matrix. The previous 407/407 Python 3.10 and 407/407 Python 3.13 runs and four-job CI results remain evidence for their exact recorded code HEADs, not new test results from this AC-45 run.

## 3. M1 acceptance matrix — current consolidated disposition

Status meanings:
- **IMPLEMENTED / UNVERIFIED**: code is recorded as present; full criterion has not been demonstrated on the current integrated baseline.
- **PARTIAL / DEVELOPMENT REQUIRED**: frozen-plan scope is not fully implemented.
- **PASS / SCOPED**: the named criterion slice was exercised successfully; this does not silently promote a broader AC.
- **BLOCKED / DEVELOPMENT REQUIRED**: implementation exists but an integration invariant prevents the required result.
- **NOT DONE**: required work/evidence is absent.

| AC | Requirement | Consolidated status | What is established / what remains |
|---|---|---|---|
| 01 | Single Active Baseline | PARTIAL | RC line and frozen-plan parent are selected. PR convergence and final mainline path still need explicit closure. |
| 02 | Branch Convergence | PARTIAL — DEVELOPMENT/GOVERNANCE | PR #1 and #3–#8 code ancestry is included in PR #9. PR #2 is divergent (31 unique commits, 39 behind its foundation base) and its cache/evidence tooling is absent from RC; selectively assess and port only relevant in-scope capability. |
| 03 | Status Consistency | PARTIAL | STATUS exists, but AC-45 results and PR ancestry findings were not yet recorded there at audit start. Update after this audit. |
| 04 | Plan Protection | PASS / SCOPED | Frozen plan is on its own branch and was not changed by the RC implementation line. Keep immutable. |
| 05 | Round Isolation | IMPLEMENTED / UNVERIFIED | Round starter, manifests and path containment are present; complete lifecycle evidence remains tied to AC-45. |
| 06 | Source Integrity | IMPLEMENTED / UNVERIFIED | Source hashes and immutable input checks exist; confirm through global acceptance, including multi-document paths. |
| 07 | Cache Reuse | IMPLEMENTED / UNVERIFIED + SCOPE REVIEW | RC has document-read/cache observations. PR #2 has a separate hash-keyed PDF/JSON/XML/CrossRef evidence cache. Decide and selectively integrate relevant context-reduction capability; do not merge its divergent branch wholesale. |
| 08 | Comment Coverage | IMPLEMENTED / UNVERIFIED | Audit, Decision Plan and ResponseUnit coverage gates exist; prove every comment reaches exactly one terminal disposition in complete round. |
| 09 | Evidence Grounding | IMPLEMENTED / UNVERIFIED | Exact evidence/target validation exists; current integrated round coverage is incomplete. |
| 10 | Historical Gate | IMPLEMENTED / UNVERIFIED | Hash-bound historical inventory and target-scoped resolver exist; unsupported structure cases remain blocked. |
| 11 | Decision Plan | IMPLEMENTED / UNVERIFIED | Schema, validation and Workflow preparation exist; complete end-to-end author approval path remains to be demonstrated. |
| 12 | Authorization | IMPLEMENTED / UNVERIFIED | Separate plan/scope/Patch/semantic gates and Tier 1 attestation exist; verify all gates in the continuous round. |
| 13 | Stable Identity | PARTIAL — DEVELOPMENT REQUIRED | Document/Object identity and TargetRef exist, but legacy paragraph-index fallback remains. Replace or strictly isolate the fallback so it cannot be used as guessed identity. |
| 14 | DAG Scheduling | IMPLEMENTED / UNVERIFIED | Dependency scheduler exists; AC-45 proves one downstream refresh path, not the full conflict-free scheduling contract. |
| 15 | Incremental Invalidation | PASS / SCOPED | AC-45 demonstrated T1 publication invalidating T2 to recheck_required with reason/source evidence; broader TargetRef dependency matrix remains to be verified. |
| 16 | Risk Tier | TESTED / PARTIAL | Deterministic Tier 0/1/2 implementation and regression coverage exist; broader acceptance remains. |
| 17 | Batch Approval Integrity | TESTED / PARTIAL | Immutable hash-bound batch approval and Tier 1 acknowledgement exist; continuous lifecycle evidence remains. |
| 18 | Typed Patch | IMPLEMENTED / UNVERIFIED | Typed Patch and evidence links exist; global criterion remains to be verified. |
| 19 | Native Track Changes | PASS / SCOPED | AC-45 real CLI run observed native w:ins/w:del in both publications; broader Adapter matrix remains. |
| 20 | Minimal Diff | IMPLEMENTED / UNVERIFIED | Targeted Run editing exists; current-head full regression and broader structures remain. |
| 21 | Token Protection | IMPLEMENTED / UNVERIFIED | Token Lock / Protected Content and exact authorization exist; broad scientific token matrix remains. |
| 22 | Format Preservation | IMPLEMENTED / UNVERIFIED | Format map and deterministic formatting contracts exist; full Word/Adapter acceptance remains. |
| 23 | Exact Verification | IMPLEMENTED / UNVERIFIED | Verify path exists and was used in both AC-45 batches; broader negative/tamper cases remain. |
| 24 | Fail-Closed | IMPLEMENTED / UNVERIFIED | Unsupported structures and missing evidence are guarded; full boundary matrix remains. |
| 25 | Structural Event Map | PARTIAL — DEVELOPMENT REQUIRED | Run/Text offsets exist, but the complete event map across protected markers, comments, fields, hyperlinks, drawings and nested structures is incomplete. |
| 26 | Bounded Safe Span | TESTED / PARTIAL | Unique ordinary Run spans outside paired bookmark intervals are supported; broader required structure boundaries remain fail-closed. |
| 27 | Boundary Protection | TESTED / PARTIAL | Bookmark interval/crossing/unpaired handling has tests; complete nested/intersecting/comment/field/hyperlink matrix remains. |
| 28 | Safe Span Authorization | TESTED / PARTIAL | Safe span is only ALLOW_CANDIDATE and existing authorization/token/format/verify gates remain; full criterion not yet accepted. |
| 29 | Manual Fallback | TESTED / PARTIAL | Hash-bound not_applied suggestion artifact and CLI integration exist; full coverage and usability evidence remain. |
| 30 | Reference Graph | TESTED / PARTIAL — DEVELOPMENT REQUIRED | Caption/mention graph, numeric citations and native-field diagnostics exist; full definitions/mentions, ambiguity and unresolved relation coverage is incomplete. |
| 31 | Numbering Propagation | PARTIAL — DEVELOPMENT REQUIRED | Typed number map and unresolved/range/field blockers exist; complete Package-wide impact plan, author authorization, atomic Package Transaction and final graph consistency are not implemented/accepted. |
| 32 | Immutable Batch | IMPLEMENTED / UNVERIFIED | Intent/scope/snapshots/receipts exist; AC-45 exercised sequential batches. Full crash/recovery matrix remains. |
| 33 | Sequential Publication | PASS / SCOPED | AC-45 proved batch-1 then batch-2, with batch-2 using batch-1 active parent. |
| 34 | Recovery and Rollback | IMPLEMENTED / UNVERIFIED | Recovery/rollback receipts and reconciliation exist; full crash windows not exercised in AC-45. |
| 35 | Package Transaction | BLOCKED — DEVELOPMENT REQUIRED | Release gate rejects the two-publication ledger because historical source_versions are conflated with unique initial source_files. Must fix lifecycle semantics without deleting history or changing approved inputs. |
| 36 | Working / Final Clean | PASS / SCOPED | AC-45 produced final Clean from the publication chain; deterministic compilation remains supported by prior tests. |
| 37 | Word/PDF Mapping | PASS / SCOPED | AC-45 executed actual Word COM 16.0, generated PDF with hashes and page/line evidence, quality report PASS. This is not Word GUI/M2 acceptance. |
| 38 | Manual Locator | IMPLEMENTED / UNVERIFIED | Author-confirmed manual locator path exists; AC-45 did not exercise table/non-body manual locations. |
| 39 | Response Letter | PASS / SCOPED | AC-45 generated replies from verified Patch excerpts for RU-T1-1 and RU-T2-2. Broader response-state matrix remains. |
| 40 | ResponseUnit | PASS / SCOPED | AC-45 exercised two ResponseUnits tied to published Patch evidence; complete disposition matrix remains. |
| 41 | Quality Audit | PASS / SCOPED | Word/PDF binding quality report passed in AC-45; full release artifact quality profile still awaits package creation. |
| 42 | Release Manifest | BLOCKED — DEVELOPMENT REQUIRED | Manifest was not generated because Release Package precondition failed on source role/path/hash uniqueness. |
| 43 | Complete Package | BLOCKED — DEVELOPMENT REQUIRED | No complete Release Package was generated; AC-45 cannot pass. |
| 44 | Bounded Remediation | IMPLEMENTED / UNVERIFIED | One-round remediation state machine and Release Gate checks exist; not exercised in AC-45. |
| 45 | Full Synthetic Round | FAIL — DEVELOPMENT REQUIRED | A–D pass; E Release Package fails. Detailed real-run report: `docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md`. |
| 46 | Legacy Baseline | PRESENT / UNVERIFIED | Original 76-test baseline is documented/present; verify none removed/weakened on final code line. |
| 47 | Core Test Independence | IMPLEMENTED / UNVERIFIED | Core deterministic tests are Python-based; verify Word COM is not a prerequisite in the full core test path. |
| 48 | Platform Matrix | TESTED / PARTIAL | 4/4 CI jobs passed on source/test HEAD fff6712; 4/4 jobs also passed on documentation HEAD db3dcd4. Prior 407/407 Python 3.10 and 407/407 Python 3.13 results remain bound to fff6712. |
| 49 | Adapter Test Isolation | PARTIAL — DEVELOPMENT REQUIRED | Safe Span, Manual Suggestion, Risk and citation/field tests exist; complete enabled-Adapter positive/negative/boundary/regression matrix is incomplete. |
| 50 | Honest RC Report | NOT DONE | This audit and AC-45 report improve evidence, but final RC report depends on closing all M1 blockers and binding every claim to the final HEAD. |

## 4. AC-45 real execution result

Run: `run_20261120_144140`  
Branch/HEAD: `upgrade/v0.5.1-p1-rc-closeout-20261120` / `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`  
Environment: Python 3.13.9; Microsoft Word COM 16.0; isolated worktree; actual CLI + synthetic DOCX; no mock.

| Stage | Result | Evidence |
|---|---|---|
| A — Two sequential publications | PASS | batch-1 then batch-2; second reads first active parent; both preflight/apply/verify/publish; native w:ins/w:del; targets_verified=1 per batch |
| B — Downstream refresh | PASS | T2 moved to recheck_required with recheck_from and invalidation_reason; active-ledger re-audit, re-prepare and re-authorization succeeded; unrelated content not rewritten |
| C — Word/PDF mapping | PASS | Actual Word COM 16.0; final Clean DOCX/PDF with SHA-256; Word page 1 line 1–1; binding quality report PASS |
| D — Response Letter | PASS | RU-T1-1 / RU-T2-2 responses derived from verified Patch excerpts |
| E — Release Package | FAIL | create-release-package rejected Workflow Ledger initial source document roles/paths/hashes as invalid or duplicated; no manifest generated |

### Root cause as observed

The second publication leaves T2.source_versions containing the superseded batch-1 active version. Removing it makes batch-2 publish validation fail. Registering it preserves publication lineage but causes the release gate to interpret multiple versions as duplicate initial source_files. Pruning/rebuilding source_versions after approval changes preflight/approval fingerprints. Three attempted variants did not produce a valid package.

Required semantic separation:

1. Immutable historical source-version/provenance records.
2. One current active version per logical document role.
3. A deduplicated initial-source inventory for the Release Package.

The fix must preserve all history, keep one current active version, deduplicate only the initial-source inventory by stable logical source identity, and never mutate approved inputs silently.

## 5. GitHub branch/PR convergence audit

GitHub compare metadata and patches establish:

| PR | Branch relationship | Finding | Disposition |
|---|---|---|---|
| #1 | `upgrade/foundation-v0.2` is an ancestor of P0; PR #1 head is contained in P0/P1/PR #9 | Foundation implementation is already in the selected integration ancestry | Superseded by PR #9; close as superseded |
| #2 | `upgrade/v0.3-evidence-pipeline` diverged from foundation: 31 commits ahead and 39 behind; its evidence_tools.py and evidence-pipeline docs are absent from RC | Unique model-agnostic PDF/JSON/XML/CrossRef extraction/cache capability may support AC-07 and context optimization; do not merge branch wholesale | Keep open for selective scope audit/port decision |
| #3 | P1 implementation branch is ancestor of PR #9 | P1 core workflow is in selected integration ancestry | Superseded by PR #9; close as superseded |
| #4 | V2.3.2 format-integrity branch is ancestor of PR #8 and PR #9 | Its contract/format/authorization safeguards are included | Superseded by PR #9; close as superseded |
| #5 | 4 commits after PR #4; ancestor of #6/#7/#8/#9 | Word settings/proofing anchor increment included later | Superseded by PR #9; close as superseded |
| #6 | 7 additional commits after #5; ancestor of #7/#8/#9 | Context-bound Word metadata classification included later | Superseded by PR #9; close as superseded |
| #7 | 4 additional commits after #6; ancestor of #8/#9 | Orphan bookmark scoping included later | Superseded by PR #9; close as superseded |
| #8 | 5 additional commits after #7; exact PR #8 head is the merge base of PR #9 | Latest bookmark body-boundary implementation is already the PR #9 parent | Superseded by PR #9; close as superseded |
| #9 | Selected RC line; includes P1 and PR #4–#8 ancestry | Sole active engineering integration PR | Keep Draft/Open until M1 evidence is complete |

No PR was merged. `main` remains unchanged. PR #2 must not be forgotten: it contains unique code not in the selected line. Its features should be assessed against AC-07 and §3.6, then ported selectively only if they fit the frozen scope and do not duplicate/contradict current usage/cache mechanisms.

## 6. Explicit non-goals and M2 boundary

- This audit does not modify the frozen plan or relax any AC.
- It does not declare M1 RC Ready.
- It does not declare M2 accepted or GA.
- Actual Word COM success does not substitute for Word GUI inspection, Save/Close/Reopen, real scientific review and user Release acceptance.
- Deferred OMML/complex Field/Citation/topology writing remains deferred only if recognized, preserved, fail-closed and routed to manual fallback as required by the frozen plan.

## 7. Required next engineering outcome

Execute the single integrated work plan in `docs/GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md`, under the implementation contract in `docs/AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md`. Do not create a new micro-task for each newly discovered sub-defect. The Agent's work is one coordinated M1 closeout against the frozen plan; report only at workstream checkpoints and final evidence review.

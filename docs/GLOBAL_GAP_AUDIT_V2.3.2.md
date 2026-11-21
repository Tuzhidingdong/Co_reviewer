# V2.3.2 Global Gap Audit — Frozen Plan REV2

**Audit date:** 2026-11-21  
**Repository:** `Tuzhidingdong/Co_reviewer`  
**Normative source:** `docs/USER_TASK_PLAN_V2.3.2_GLOBAL.md` on `docs/freeze-global-user-task-plan-v2.3.2-20261120`  
**Audited implementation line:** `upgrade/v0.5.1-p1-rc-closeout-20261120`, source/test HEAD `3f6e4a173a0e58490eb1950b0ed27c288b32c94c`; latest status snapshot is recorded in `docs/STATUS.md`. GitHub Actions run `44921598581` passed 4/4 jobs (499 tests on Python 3.10/3.13; Windows PowerShell 5.1 quality checks on both).  
**Plan protection:** This audit does not modify the frozen plan. It updates execution interpretation only.

## 1. Executive decision

The V2.3.2 implementation is **not yet M1 RC-ready**. The current line has materially advanced since the original 2026-11-20 audit: the WS-3 source-version lifecycle fix now has a focused two-publication → downstream re-prepare → READY Release Package regression; WS-2 numbering authorization/transaction paths have focused positive and fail-closed tests; and WS-1 has a new stable-identity-to-native-writer integration regression. Remaining M1 closure is now primarily broader end-to-end/adapter evidence and a current-head full AC-45 Word COM run, not absence of all these component implementations.

- AC-31 now has a package-wide hash-bound impact plan, explicit author authorization, Workflow/Patch provenance, pre/post-publication Reference Graph checks and immutable package transaction artifacts; real-document/Word interoperability and full-round acceptance remain.
- AC-45's historical full Word COM run still fails at Release Package creation. The source-version lifecycle defect now has a passing focused two-publication package regression with manual locators, but the full Word COM chain has not yet been rerun on the fixed head.
- AC-13 now defaults to registered stable TargetRefs; positional `p_idx` is advisory only, and the explicit legacy migration mode is isolated and blocked when an identity registry exists. A new test now exercises the path through the native DOCX writer.
- AC-25–29 have parser-backed structural event maps, bounded safe-span and fail-closed protection for supported ranges, manual fallback and authorization preservation. Cross-adapter and broader topology acceptance remain incomplete; unsupported Fields/links/drawings remain blocked.
- AC-30's Reference Graph now covers caption/mention relations, numeric citations, native-field diagnostics and ambiguous/unresolved states in the supported subset; broader citation styles and object types remain manual/partial.
- AC-49 has component-level positive/negative/boundary/regression coverage for the currently enabled ordinary-Run and simple-Hyperlink writer paths; full Word/real-review-round acceptance remains.
- AC-02 remains a scoped governance item: PR #2 is divergent and its extra PDF/JSON/XML/CrossRef pipeline is explicitly deferred from M1; do not bulk-merge it.

This remains an M1 engineering closeout plus verification—not an M2-only acceptance handoff. `docs/STATUS.md` is the current execution-state snapshot; this audit is synchronized to its evidence without changing the frozen plan.

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
| 03 | Status Consistency | TESTED / PARTIAL | `docs/STATUS.md` records the current code/test HEAD, CI run, AC45 historical failure and current focused lifecycle evidence; final RC report still awaits M1 closure. |
| 04 | Plan Protection | PASS / SCOPED | Frozen plan is on its own branch and was not changed by the RC implementation line. Keep immutable. |
| 05 | Round Isolation | IMPLEMENTED / UNVERIFIED | Round starter, manifests and path containment are present; complete lifecycle evidence remains tied to AC-45. |
| 06 | Source Integrity | IMPLEMENTED / UNVERIFIED | Source hashes and immutable input checks exist; confirm through global acceptance, including multi-document paths. |
| 07 | Cache Reuse | IMPLEMENTED / UNVERIFIED | PR #9 includes SHA-256 keyed DOCX inventory reuse, PDF text/geometry/selected-render cache, bounded JSON/JSONL/XML structural summaries with exact JSON Pointers, all-record CrossRef indexing, cache-artifact hash verification/rebuild, and whole-role Word/PDF mapping reuse guarded by current Clean DOCX and all output/PDF hashes. Tests cover reuse, source/parameter invalidation and tampered-artifact rebuild; full AC-07 integrated real-round acceptance remains part of WS-5. |
| 08 | Comment Coverage | IMPLEMENTED / UNVERIFIED | Audit, Decision Plan and ResponseUnit coverage gates exist; prove every comment reaches exactly one terminal disposition in complete round. |
| 09 | Evidence Grounding | IMPLEMENTED / UNVERIFIED | Exact evidence/target validation exists; current integrated round coverage is incomplete. |
| 10 | Historical Gate | IMPLEMENTED / UNVERIFIED | Hash-bound historical inventory and target-scoped resolver exist; unsupported structure cases remain blocked. |
| 11 | Decision Plan | IMPLEMENTED / UNVERIFIED | Schema, validation and Workflow preparation exist; complete end-to-end author approval path remains to be demonstrated. |
| 12 | Authorization | IMPLEMENTED / UNVERIFIED | Separate plan/scope/Patch/semantic gates and Tier 1 attestation exist; verify all gates in the continuous round. |
| 13 | Stable Identity | TESTED / PARTIAL | Stable TargetRefs are default and p_idx is positional only; explicit legacy mode is isolated and blocked with an identity registry. New current-head test exercises manifest → TargetRef → native writer and forged/stale ref rejection. Full package-wide migration/ambiguity evidence remains. |
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
| 25 | Structural Event Map | TESTED / PARTIAL | Parser-backed part-wide event inventory records XML order, Run/Text ranges, bookmark/comment/permission/move/custom-XML boundaries, fields, hyperlinks, drawings and revision wrappers in supported parts. Malformed/unrecognized structures fail closed; complete cross-adapter acceptance remains open. |
| 26 | Bounded Safe Span | TESTED / PARTIAL | Ordinary-Run candidates outside protected ranges are supported; nested/intersecting bookmark, comment and permission intervals are tested. Fields, hyperlinks, drawings and unsupported topologies remain non-editable/fail-closed. |
| 27 | Boundary Protection | TESTED / PARTIAL | Tests cover nested/intersecting ranges, cross-paragraph comments, unmatched markers and body-level bookmark endpoints; unsupported field/link/drawing boundaries still block editing. |
| 28 | Safe Span Authorization | TESTED / PARTIAL | Safe span returns ALLOW_CANDIDATE only; Token Lock, Authorized Change, history, format, approval and Verify gates remain mandatory. Full integrated acceptance remains open. |
| 29 | Manual Fallback | TESTED / PARTIAL | Hash-bound `not_applied` artifact and CLI/Apply integration exist; focused tests pass, while broader coverage and usability evidence remain. |
| 30 | Reference Graph | TESTED / PARTIAL | Caption/mention graph is extended with numeric citation and native-field records plus ambiguous/unresolved diagnostics; broad citation styles and further object types remain unsupported/manual. |
| 31 | Numbering Propagation | TESTED / PARTIAL | Package-wide hash-bound impact plan and exact SHA author approval are bound to Workflow/Patch operations; exact changed-paragraph coverage, pre/post-publication graph checks, immutable Manifest/Commit Marker/Receipt, stale-approval revocation and public CLI commit/fail-closed publish tests exist. Real-document/Word interoperability and full acceptance remain. |
| 32 | Immutable Batch | IMPLEMENTED / UNVERIFIED | Intent/scope/snapshots/receipts exist; AC-45 exercised sequential batches. Full crash/recovery matrix remains. |
| 33 | Sequential Publication | PASS / SCOPED | AC-45 proved batch-1 then batch-2, with batch-2 using batch-1 active parent. |
| 34 | Recovery and Rollback | IMPLEMENTED / UNVERIFIED | Recovery/rollback receipts and reconciliation exist; full crash windows not exercised in AC-45. |
| 35 | Package Transaction | TESTED / PARTIAL | Focused integration regression now executes two real `cmd_publish_batch` transactions, formal downstream re-prepare, unique initial `source_files` plus append-only intermediate `source_versions`, then READY Manifest/Receipt/Commit Marker and `verify_release_package`. Manual locators isolate package gates; Word COM/AC45 remains. |
| 36 | Working / Final Clean | PASS / SCOPED | AC-45 produced final Clean from the publication chain; deterministic compilation remains supported by prior tests. |
| 37 | Word/PDF Mapping | PASS / SCOPED | AC-45 executed actual Word COM 16.0, generated PDF with hashes and page/line evidence, quality report PASS. This is not Word GUI/M2 acceptance. |
| 38 | Manual Locator | IMPLEMENTED / UNVERIFIED | Author-confirmed manual locator path exists; AC-45 did not exercise table/non-body manual locations. |
| 39 | Response Letter | PASS / SCOPED | AC-45 generated replies from verified Patch excerpts for RU-T1-1 and RU-T2-2. Broader response-state matrix remains. |
| 40 | ResponseUnit | PASS / SCOPED | AC-45 exercised two ResponseUnits tied to published Patch evidence; complete disposition matrix remains. |
| 41 | Quality Audit | PASS / SCOPED | Word/PDF binding quality report passed in AC-45; full release artifact quality profile still awaits package creation. |
| 42 | Release Manifest | TESTED / PARTIAL | The focused two-publication lifecycle regression preserves initial source inventory and historical source versions, refreshes T2 against the first published active DOCX, and generates a READY Manifest. Manual locator evidence only; full Word COM AC45 remains. |
| 43 | Complete Package | TESTED / PARTIAL | The focused lifecycle regression validates the complete receipt and commit marker and passes `verify_release_package`; it uses synthetic/manual locator evidence and is not a full Word COM AC45 run. |
| 44 | Bounded Remediation | IMPLEMENTED / UNVERIFIED | One-round remediation state machine and Release Gate checks exist; not exercised in AC-45. |
| 45 | Full Synthetic Round | FAIL — DEVELOPMENT REQUIRED | A–D pass; E Release Package fails. Detailed real-run report: `docs/AC45_SYNTHETIC_E2E_TEST_REPORT.md`. |
| 46 | Legacy Baseline | PRESENT / UNVERIFIED | Original 76-test baseline is documented/present; verify none removed/weakened on final code line. |
| 47 | Core Test Independence | IMPLEMENTED / UNVERIFIED | Core deterministic tests are Python-based; verify Word COM is not a prerequisite in the full core test path. |
| 48 | Platform Matrix | TESTED / PARTIAL | Current source/test HEAD `3f6e4a1`: Actions run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) passed 4/4 jobs; 499/499 tests passed on Python 3.10 and 3.13; Windows PowerShell 5.1 quality checks passed on both versions. |
| 49 | Adapter Test Isolation | TESTED / PARTIAL | Currently enabled ordinary-Run and simple-Hyperlink writer paths have positive/negative/boundary/regression tests, including protected comment/field/drawing/nested-table negatives, duplicate-match ambiguity, cross-container boundaries and hyperlink relationship preservation. Component-level evidence only; full Word/real-round acceptance remains. |
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

The focused post-fix evidence is now present in the current source/test line: `tests/test_sequential_release_lifecycle.py::SequentialReleaseLifecycleIntegrationTests::test_two_publications_refresh_downstream_source_then_create_ready_release_package` executes two real publication transactions, formally re-prepares downstream T2 against batch 1's active DOCX, preserves append-only history and unique initial inputs, generates a READY Manifest/Package and passes package verification. It uses explicit manual locators; it is not the full AC-45 Word COM chain.

The current-head CI run [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) passed 4/4 jobs: 499/499 unit tests on Python 3.10 and Python 3.13, plus Windows PowerShell 5.1 quality checks on both versions. The added `test_registered_target_ref_reaches_native_docx_writer` exercises identity registration through actual native DOCX writing and proves forged fingerprints and stale revision-bound TargetRefs fail closed. These are post-fix component/integration test results; they do not retroactively change the historical Word COM result above.


## 5. GitHub branch/PR convergence audit

GitHub compare metadata and patches establish:

| PR | Branch relationship | Finding | Disposition |
|---|---|---|---|
| #1 | `upgrade/foundation-v0.2` is an ancestor of P0; PR #1 head is contained in P0/P1/PR #9 | Foundation implementation is already in the selected integration ancestry | Superseded by PR #9; close as superseded |
| #2 | `upgrade/v0.3-evidence-pipeline` remains divergent (31 commits ahead and 39 behind its foundation base); PR #2's branch is not part of PR #9 ancestry | Its required model-agnostic PDF/JSON/JSONL/XML/CrossRef extraction/cache capability has already been selectively ported and extended on PR #9, with stronger cache artifact-integrity checks and tests | Keep PR #2 open/unmerged for historical review; no duplicate port or wholesale merge |
| #3 | P1 implementation branch is ancestor of PR #9 | P1 core workflow is in selected integration ancestry | Superseded by PR #9; close as superseded |
| #4 | V2.3.2 format-integrity branch is ancestor of PR #8 and PR #9 | Its contract/format/authorization safeguards are included | Superseded by PR #9; close as superseded |
| #5 | 4 commits after PR #4; ancestor of #6/#7/#8/#9 | Word settings/proofing anchor increment included later | Superseded by PR #9; close as superseded |
| #6 | 7 additional commits after #5; ancestor of #7/#8/#9 | Context-bound Word metadata classification included later | Superseded by PR #9; close as superseded |
| #7 | 4 additional commits after #6; ancestor of #8/#9 | Orphan bookmark scoping included later | Superseded by PR #9; close as superseded |
| #8 | 5 additional commits after #7; exact PR #8 head is the merge base of PR #9 | Latest bookmark body-boundary implementation is already the PR #9 parent | Superseded by PR #9; close as superseded |
| #9 | Selected RC line; includes P1 and PR #4–#8 ancestry | Sole active engineering integration PR | Keep Draft/Open until M1 evidence is complete |

No PR was merged. `main` remains unchanged. PR #2 remains open and divergent for historical review, but its in-scope AC-07/§3.6 evidence-extraction and context-cache capability is already selectively integrated and extended in PR #9 as recorded in `docs/PR2_SCOPE_DISPOSITION_V2.3.2.md`. Do not merge PR #2 or port the same tools again.

## 6. Explicit non-goals and M2 boundary

- This audit does not modify the frozen plan or relax any AC.
- It does not declare M1 RC Ready.
- It does not declare M2 accepted or GA.
- Actual Word COM success does not substitute for Word GUI inspection, Save/Close/Reopen, real scientific review and user Release acceptance.
- Deferred OMML/complex Field/Citation/topology writing remains deferred only if recognized, preserved, fail-closed and routed to manual fallback as required by the frozen plan.

## 7. Required next engineering outcome

Execute the single integrated work plan in `docs/GLOBAL_ENGINEERING_EXECUTION_PLAN_V2.3.2.md`, under the implementation contract in `docs/AGENT_IMPLEMENTATION_CONTRACT_V2.3.2.md`. Do not create a new micro-task for each newly discovered sub-defect. The Agent's work is one coordinated M1 closeout against the frozen plan; report only at workstream checkpoints and final evidence review.

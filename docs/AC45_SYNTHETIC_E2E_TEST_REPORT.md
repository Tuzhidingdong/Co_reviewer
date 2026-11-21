# AC-45 Synthetic End-to-End Test Report — 2026-11-20

**Run:** `run_20261120_144140`  
**Branch:** `upgrade/v0.5.1-p1-rc-closeout-20261120`  
**HEAD:** `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`  
**Worktree:** independent isolated worktree  
**Evidence directory:** `docs/ac45_e2e_evidence/run_20261120_144140/`  
**Environment:** Python 3.13.9; Microsoft Word COM 16.0 available  
**Execution:** real CLI + synthetic DOCX + real Word COM; no mock  
**Cleanup:** temporary directories removed; retained evidence directory is the audit record.

## 1. Scope and execution discipline

- Reviewed `docs/STATUS.md`, `docs/RC_LOCAL_ACCEPTANCE.md` and the frozen Global REV2 acceptance requirements.
- Ran one existing integration test as a baseline: 1 passed.
- Did not rerun the prior 407-test Python 3.10/3.13 matrix or CI. Those results remain tied to their previously recorded HEADs and are not represented as new evidence from this run.
- Tracked files under `scripts/`, `tests/`, `schemas/`, `SKILL.md` and other implementation paths were unchanged by this diagnostic run. Only this report and the evidence directory are added as records.

## 2. Results

| Stage | Result | Actual execution evidence |
|---|---|---|
| A. Two sequential publications | **PASS** | batch-1 → batch-2 executed continuously. Batch-2 read batch-1's published active parent. Both rounds executed preflight/apply/verify/publish-batch; native `w:ins`/`w:del` were present; `targets_verified=1` for each batch. |
| B. Downstream dependency refresh | **PASS** | After T1 publication, T2 became `recheck_required` with `recheck_from` and `invalidation_reason`. Active-ledger re-audit, downstream re-prepare and renewed authorization completed. Unaffected content was not rewritten. |
| C. Word/PDF mapping | **PASS — REAL WORD COM** | Microsoft Word COM 16.0 generated final Clean DOCX and PDF. SHA-256 values were recorded. Word COM page/line result was page 1, line 1–1. Binding quality report passed. No skipped unit. This is not Word GUI/M2 acceptance. |
| D. Response Letter | **PASS** | Response Letter content came from verified Patch excerpts. RU-T1-1 and RU-T2-2 each mapped to the corresponding published patch evidence. |
| E. Release Package | **FAIL** | `create-release-package` rejected the Workflow Ledger: `Workflow Ledger initial source document roles/paths/hashes are invalid or duplicated.` No valid manifest was generated. |

## 3. Root cause — source-version lifecycle / release source inventory

After two publications, T2's `source_versions` contains the superseded batch-1 active version.

Three attempted variants were exercised:

1. Do not register the historical version: batch-2 `publish-batch` validation fails.
2. Register the historical version: publication succeeds, but Release Gate treats the version as a duplicate initial `source_files` role/path/hash.
3. Delete or prune duplicate history after the fact: preflight/approval fingerprint becomes stale.

None produced a valid Release Package.

This is an integration/data-model defect, not an environment limitation or mock discrepancy.

The implementation must distinguish three concepts:

- **Historical source versions:** append-only provenance for every source/active version used by a batch.
- **Current active source version:** exactly one current version per logical document role.
- **Initial release source files:** deduplicated initial input set, unique by stable logical source identity and not a flattened copy of all historical versions.

The fix must preserve full history and the latest active pointer, while constructing the release's initial source inventory from the correct initial snapshot. It must not silently mutate approved inputs. If any field included in the approved fingerprint changes, preflight and approval must be invalidated and repeated.

## 4. Final acceptance decision

**AC-45 = FAIL / NOT ACCEPTED.**

Stages A–D have been demonstrated by actual execution. Stage E fails, so the complete required chain does not close. AC-45 cannot be marked PASS or PARTIAL at the criterion level because the frozen plan requires a complete successful workflow through Release Package.

**AC-31 = NOT TESTED.** This run did not trigger a figure/table/equation numbering impact transaction. The existing STATUS PARTIAL disposition remains.

**M2 = NOT TESTED / USER ACCEPTANCE PENDING.** Real Word COM execution is not Word GUI inspection, Save/Close/Reopen by the user, real manuscript review or Release Package acceptance.

## 5. Remaining engineering work

1. Correct source-version lifecycle semantics so historical versions remain auditable, the current active version is unique, and the release initial-source inventory is deduplicated by logical source identity.
2. Add a regression test that executes both complete publication lifecycles and then calls `create-release-package`; do not seed an already-final ledger.
3. Verify stale approval invalidation, history preservation, manifest hashes and artifact lineage.
4. Re-run the complete AC-45 workflow with real Word COM/PDF mapping and require a READY manifest and complete package.
5. Separately complete AC-31 and M2.

## 6. Honest boundary

This report does not claim a formal RC, does not modify the frozen plan, and does not convert Word COM into Word GUI acceptance. No source, test, schema or Skill change was made by the diagnostic run.

## 7. Post-failure lifecycle fix and current-head evidence — 2026-11-21

The failure described in Sections 2–5 is the result of the historical full Word COM run at HEAD `db3dcd406ac36fa9924ca56a1dc97546eb0b7a62`. It remains the latest full Word COM execution and must remain recorded as **AC-45 FAIL**.

A later focused regression on the integration line now covers the source-version lifecycle defect without mutating approved inputs:

- Test: `tests/test_sequential_release_lifecycle.py::SequentialReleaseLifecycleIntegrationTests::test_two_publications_refresh_downstream_source_then_create_ready_release_package`.
- It executes two real `cmd_publish_batch` transactions, then formally re-prepares downstream T2 with `prepare_workflow_execution()` against batch 1's published active DOCX and refreshed evidence/source SHA-256.
- It preserves the unique initial `source_files` inventory and append-only intermediate `source_versions`, then creates a READY Manifest/Release Package with a complete receipt and commit marker and passes `verify_release_package()`.
- This focused lifecycle fixture uses explicit manual locators. It isolates and validates the corrected release/source-version semantics, but does **not** run Word COM/PDF pagination or prove a full AC-45 chain.

The same current source/test HEAD adds `tests/test_document_engine_module.py::DocumentEngineModuleBoundaryTests::test_registered_target_ref_reaches_native_docx_writer`, which exercises parser-backed identity manifest generation → registered TargetRef → native DOCX Track Changes writer. It verifies source-byte immutability and rejects forged object fingerprints and stale document revisions even when paragraph hints match.

Current source/test HEAD: `3f6e4a173a0e58490eb1950b0ed27c288b32c94c`.

Current GitHub Actions run: [44921598581](https://github.com/Tuzhidingdong/Co_reviewer/actions/runs/44921598581) — **4/4 jobs passed**; **499/499 tests** on Python 3.10 and Python 3.13; Windows PowerShell 5.1 quality checks passed on both Python versions.

### Updated acceptance interpretation

- The historical Word COM Release Package failure is not erased or rewritten.
- The source-version/initial-source separation now has a passing post-fix focused two-publication package regression.
- AC-45 remains **FAIL / NOT ACCEPTED** until one current-head execution runs the complete chain with real Word COM: two sequential publications, formal downstream refresh/re-authorization, Word COM/PDF mapping, Response Letter, READY Release Package, and final package verification.
- M2 Word GUI, Save/Close/Reopen, real scientific review and explicit user Release acceptance remain separate user-acceptance requirements.


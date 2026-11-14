# Co_Reviewer v0.5.1 P0 Implementation Status

## Branch and safety boundary

- Repository: \`Tuzhidingdong/Co_reviewer\`
- Implementation branch: \`upgrade/v0.5.1-p0-implementation-20261114\`
- Base branch: \`upgrade/execution-integrity-v0.3\`
- Base commit: \`7b0fef325a5f8282a39259d39bb3cc0118e24a68\`
- Branch remains separate from the release branch; no feature PR has been merged.
- The formal installed Skill directory and all source Manuscript/SI/Reviewer files remain untouched.
- No Worker has been started.

During an earlier connector operation, a test-only file was briefly written to the default branch because the branch argument was omitted. The file was checked and removed in a follow-up commit; the current default-branch tree does not contain it. No Co_Reviewer feature implementation was merged to the default branch.

## Implemented

### P0-01 — Preserve the v0.3 regression baseline

- Added \`docs/BASELINE_V0.3.md\`, preserving the exact inventory of the 76 original test methods.
- Existing baseline tests have not been deleted or weakened.

### P0-02 — Establish behavior-preserving module boundaries

- Added \`scripts/co_reviewer/document_engine.py\` for existing WordprocessingML parsing, Run handling and native Track Changes behavior.
- Added \`scripts/co_reviewer/identity.py\` for existing file and approval fingerprints, with the new identity model layered alongside the legacy hashes.
- Kept \`scripts/co_reviewer_cli.py\` as the compatibility command entry point.

### P0-03 — Unified task workflow and scheduling

Added \`scripts/co_reviewer/workflow_engine.py\`:

- Lifecycle states and legal transitions.
- Task DAG validation, deterministic dependency order and target-group quotient graph.
- Condensation of target-group cycles while still rejecting true task dependency cycles.
- Ready-task gating and conflict-free candidate batches.
- Transitive downstream invalidation when an upstream task changes.
- Fingerprints binding task scope, input versions, preflight, human approval, output, verification and completion evidence.
- A durable Workflow Ledger with build and validation APIs.

The decision-plan validator now consumes the common workflow engine. The old DOCX-only revision plan still requires real document targets; Response-only tasks are preserved in the Workflow Ledger instead of being converted into fake DOCX edits.

### P0-04 — Document and object identity foundation

Added five distinct identity dimensions:

1. Exact source-byte hash.
2. Normalized visible-content hash.
3. Structure hash.
4. Formatting hash.
5. Citation-state hash with a versioned CitationProfile.

Added \`DocumentIdentity\`, \`DocumentObjectIdentity\` and \`TargetRef\`. The Workflow Ledger can persist document revisions and an object registry, and validates that stable target references match the registered object fingerprint. The scheduler prefers \`document_id|object=object_id\` groups when TargetRefs exist. The legacy \`document|part|p_idx\` key remains a migration fallback until parser-backed stable object matching is complete.

### P0-05 — Typed Patch and RevisionLedger execution bridge

Added \`scripts/co_reviewer/revision_models.py\` and \`schemas/revision-ledger.schema.json\`:

- \`PatchOperation\` represents an exact \`find/replace\` operation in a Word part, with distinct input/output DOCX paths and evidence IDs.
- A generic \`proposed_action\` is never treated as executable text. Missing exact operands are rejected.
- \`Patch\` groups operations for one task and retains supersession lineage.
- \`RevisionLedger\` stores document revision records, Patches and append-only execution events.
- The legacy DOCX writer remains the only document writer. The adapter exports pending, unapproved legacy revision items and refuses to execute if reviewer text, proposed response or exact targets drift from the Patch record.
- With \`--revision-ledger\`, preflight, approval, apply and verified confirmation are synchronized back to the new ledger, including fingerprints and events.
- The old CLI entry points and schema remain compatible for users who do not opt into the new ledger.

Patch supersession now has a controlled implementation: it records predecessor/successor lineage, preserves old execution history, requires replacements of applied/verified Patches to start from the predecessor's output DOCX, and reopens linked ResponseUnits. Executed Patches cannot be silently re-exported as fresh pending work. Ready DAG batches now materialize directly into typed Patches. Verified changes invalidate same-target peers and downstream tasks, and linked ResponseUnits are reopened; remaining acceptance is to exercise multi-round Patch supersession/rebase against actual updated DOCX files.

### P0-07 — Working Clean View projection

Added \`scripts/co_reviewer/working_view.py\` and the \`create-working-clean-views\` CLI command:

- Creates a separate DOCX derivative from each Marked DOCX in the RevisionLedger.
- Accepts \`w:ins\`/\`w:moveTo\` content and rejects \`w:del\`/\`w:moveFrom\` content.
- Removes revision-property snapshots and comment anchors/parts in the derivative.
- Refuses unsupported table-structure revision markers rather than guessing.
- Does not overwrite or mutate the source or Marked DOCX.
- Persists the Working Clean View path, Marked source hash, output hash and policy fingerprint in the RevisionLedger; subsequent ledger validation rechecks both files.

This is an internal accepted-changes projection for downstream task analysis. It is not the final publication Clean Manuscript and does not replace Word GUI/save-round-trip and final layout validation.

### P0-06 — ResponseUnit lifecycle and final-location evidence

Added a separate \`ResponseUnit\` model:

- One reviewer comment and response, linked to relevant task IDs, Patches and document targets.
- Semantic review is a separate explicit transition; it does not imply final page/line locations.
- Final location mapping requires page, line range, actual PDF path and PDF SHA-256.
- The source location-map JSON file and its SHA-256 are also stored. PDF/map bytes are rechecked before a mapped/completed RevisionLedger can be serialized.
- Completion requires semantic review, final locations, explicit ready-for-submission confirmation and verified status for every linked Patch.
- Markdown/DOCX response renderers distinguish \`location_pending\` from a fully mapped response. They do not invent line numbers.

CLI commands now include:

- \`build-revision-ledger\`
- \`export-revision-plan\`
- \`confirm-response-units\`
- \`record-response-locations\`
- \`supersede-patch\`
- \`create-working-clean-views\`
- \`complete-response-units\`

The location commands include a Windows-only `map-response-locations` producer:

- Opens only the ledger's hash-verified Working Clean View copies in a new, hidden Microsoft Word COM instance; source and Marked DOCX files are not overwritten.
- Enables continuous line numbering, lets Word paginate, saves a separate Word-final Clean DOCX and exports its PDF.
- Locates exact Patch replacement text in the Word layout, reads actual page/line positions through Word COM, and confirms the target text is extractable from the same PDF page.
- Requires semantically confirmed ResponseUnits and verified Patches. It fails closed for ambiguous/missing text, deletions without surviving text, non-body Word parts, multi-page targets and unsupported phrase lengths.
- Binds each mapping to both final DOCX and PDF SHA-256 values, plus the immutable location-map JSON. A later change to any bound artifact invalidates the mapping.

Install the optional Windows dependencies with `pip install -e ".[word-pdf]"`. The producer has been implemented but has **not yet been executed on the user's Windows/Word installation**; COM pagination, line-number correspondence and PDF extraction remain pending local acceptance. The external `record-response-locations` command remains available for independently produced maps.

## Regression inventory and validation status

- The preserved v0.3 regression baseline contains 76 original test methods.
- The current tracked `tests/*.py` source inventory contains **179 unique test methods**.
- This is a source inventory count only. It is not an executed-test count, a CI result or a pass claim. Recalculate it whenever tests are added or removed.

The current branch has not yet completed a full Windows run with the configured Python interpreter. Earlier GitHub Actions attempts failed before a runner was assigned (the job records had no runner and no execution steps). No claim is made that the current branch passes unit tests.

Microsoft Word GUI opening, native Track Changes rendering, Word save-round-trip, COM pagination and final PDF line extraction remain unaccepted.

## Remaining roadmap — in priority order

1. **Expand parser-backed identity coverage:** real DOCX parsing now creates document/object identities, reuses unique native paraIds and unique exact fingerprints across revisions, and converts plan targets to stable TargetRefs. Remaining acceptance: validate citation heuristics and broaden cross-version matching/complex object types; retain p_idx only as a location hint.
2. **Complete the execution state machine:** ready batches, same-target invalidation and audited same-round Patch rebasing are implemented in the CLI bridge. Remaining acceptance: execute multi-round rebases against real Word-generated DOCX outputs and validate linked cross-document tasks. Keep the legacy DOCX writer as the writer until a replacement is proven equivalent.
3. **Validate the final-location producer on Windows:** the Word COM/PDF producer now exists; next acceptance must confirm Word pagination, continuous line numbers, PDF page correspondence and unchanged source files on the user's installed Word version.
4. **End-to-end acceptance:** run all tests on Windows, then separately validate synthetic Word GUI/save-round-trip, multi-document iterative patching and final Response Letter consistency.

## Verification boundary

A schema-valid Ledger, a passing unit suite or a successful DOCX XML write does not establish scientific correctness or real Word rendering. The evidence chain, cross-document scientific consistency and semantic acceptability remain separate review gates. All scientific content must remain grounded in user-supplied results.

Approval flags/fingerprints provide integrity and audit linkage, not cryptographic proof of a person's identity. Production approval must originate from a direct user confirmation action and be retained in the event history.

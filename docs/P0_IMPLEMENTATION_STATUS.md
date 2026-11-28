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

### P0-08 — Evidence-labelled usage and document-read audit

Added `scripts/co_reviewer/usage_audit.py` and usage-event/pricing schemas:

- Append-only JSONL events distinguish stage activity, programmatic DOCX reads, model-usage events and notes.
- Optional `--usage-ledger` on `inspect` and `audit` records source SHA-256, cache hit/miss, parse/full-text extraction flags, paragraph/character counts and explicit `context_scope=none` for programmatic parsing.
- `record-usage-event`, `audit-usage` and `estimate-usage` support caller-supplied model measurements, stage/actor/provider/model grouping, repeated full-read detection and explicit rate estimates.
- Missing token buckets remain null. Reasoning/output overlap is recorded, and cost estimation refuses unavailable, partial or non-disjoint accounting data. Prompt and manuscript text are not stored.
- Filesystem/parse counts never imply that content entered model context; context scope is recorded only when the caller supplies that evidence.

### P0-09 — Deterministic DOCX/PDF quality audit

Added `scripts/co_reviewer/docx_quality.py`, report schema and `audit-docx-quality`:

- Checks package ZIP and relevant Word XML integrity, paragraph/table counts, tracked-revision inventory, comments, likely placeholders, Normal-style typography and section geometry.
- Profiles distinguish generic, clean manuscript, marked manuscript, Word-final Clean and Response Letter outputs.
- Optional PDF checking binds file SHA-256 and signature, and uses PyMuPDF page metadata when available.
- Response Letter rendering emits a quality report automatically. Word COM/PDF mapping emits a per-document quality report and refuses to attach final locations when deterministic Clean/PDF checks fail.
- These checks do not claim visual acceptance; actual Word layout, PDF page correspondence and scientific semantics remain separate gates.

## Regression inventory and validation status

- The preserved v0.3 regression baseline contains 76 original test methods.
- The current tracked `tests/*.py` source inventory contains **199 unique test methods**.
- This is a source inventory count only. It is not an executed-test count, a CI result or a pass claim. Recalculate it whenever tests are added or removed.

The P1 follow-on branch adds 19 test methods; its full Windows run remains pending after implementation. Earlier GitHub Actions attempts failed before a runner was assigned (the job records had no runner and no execution steps). No claim is made that the current branch passes unit tests.

Microsoft Word GUI opening, native Track Changes rendering, Word save-round-trip, COM pagination and final PDF line extraction remain unaccepted.

## Remaining roadmap — in priority order

1. **Expand parser-backed identity coverage:** real DOCX parsing now creates document/object identities, reuses unique native paraIds and unique exact fingerprints across revisions, and converts plan targets to stable TargetRefs. Remaining acceptance: validate citation heuristics and broaden cross-version matching/complex object types; retain p_idx only as a location hint.
2. **Complete the execution state machine:** ready batches, same-target invalidation and audited same-round Patch rebasing are implemented in the CLI bridge. Remaining acceptance: execute multi-round rebases against real Word-generated DOCX outputs and validate linked cross-document tasks. Keep the legacy DOCX writer as the writer until a replacement is proven equivalent.
3. **Validate the final-location producer on Windows:** the Word COM/PDF producer now exists; next acceptance must confirm Word pagination, continuous line numbers, PDF page correspondence and unchanged source files on the user's installed Word version.
4. **End-to-end acceptance:** run all tests on Windows, then separately validate synthetic Word GUI/save-round-trip, multi-document iterative patching and final Response Letter consistency.

## Verification boundary

A schema-valid Ledger, a passing unit suite or a successful DOCX XML write does not establish scientific correctness or real Word rendering. The evidence chain, cross-document scientific consistency and semantic acceptability remain separate review gates. All scientific content must remain grounded in user-supplied results.

Approval flags/fingerprints provide integrity and audit linkage, not cryptographic proof of a person's identity. Production approval must originate from a direct user confirmation action and be retained in the event history.

## V2.3.2 frozen-contract implementation update — 2026-11-18

The normative architecture and state-machine contract is recorded in docs/V2.3.2_ENGINEERING_CONTRACT.md. The implementation is being added as executable primitives rather than prompt-only requirements.

Implemented in this update:
- scripts/co_reviewer/v232_contract.py: deterministic immutable token extraction/locking; Myers word-token minimal diff; authorization/protected-span checks; Effective Style cascade; non-empty rPrChange snapshots with explicit false inherited booleans; prohibition on formatting revision nesting inside w:ins; typed local micro-typography decisions; package-wide directed definition/mention graph; explicitly authorized graph renumber planning; paragraph split/merge topology guards; immutable package staging/version directory/commit marker; bounded remediation transition; Marked-to-Clean compilation bound to explicitly registered revision IDs.
- document_engine.prepare_document() now applies the immutable token lock to every exact-text operation. A numeric/scientific/reference token change is rejected unless the operation supplies an explicit authorized token.
- PatchOperation carries optional authorized_change and protected_spans metadata into the deterministic writer. The revision-plan schema describes both fields.
- CLI commands expose revision baseline inventory, target-overlap gate, edit-contract validation, package-wide DOCX reference graph, authorized renumber planning, Marked-to-Clean compilation and package commit.
- New synthetic contract tests cover token drift, Myers reconstruction, style snapshots, w:ins formatting restriction, cross-document graph updates, structural guards, package commit, one-round remediation, Clean compilation and writer integration.

Important boundaries:
- revision-baseline is a zero-model-call inventory. targeted-revision-gate identifies overlaps and records the selected policy; actual acceptance/rejection of legacy revisions must still use a supported revision-resolution adapter. It does not silently rewrite the source.
- The package commit marker is the release visibility gate. A version directory without a valid root marker is an uncommitted candidate.
- The reference graph emits only uniquely resolved edges. Ambiguous/unresolved mentions are not renumbered.
- OMML writing remains blocked. No model-authored OMML is accepted; a deterministic converter plus Word round-trip is still required.
- Effective Style resolution currently covers docDefaults, paragraph basedOn chain, character basedOn chain and direct Run properties. Theme-font resolution and complete Word style semantics require additional adapter coverage.
- No real Microsoft Word GUI/COM accept/reject, formula editing or cross-version rendering claim is made by these code changes.

The GitHub Actions result for each commit must be checked independently. The user’s unified local Windows test and final Word GUI acceptance have not yet occurred.


## V2.3.2 implementation increment — target-scoped historical revision resolution

The isolated development branch adds a deterministic Accept/Reject adapter exposed through `resolve-historical-revisions`. It binds the inventory to the exact source SHA-256, resolves only target-overlapping records, and writes the result to a new DOCX path. Unrelated historical structures are checked for unchanged canonical XML.

Supported: native `w:ins`/`w:del` wrappers, paired `w:moveFrom`/`w:moveTo` wrappers, and standard property snapshots `rPrChange`, `pPrChange`, `tblPrChange`, `tblGridChange`, `trPrChange`, `tcPrChange`, and `sectPrChange` when the prior-property snapshot has the exact expected shape.

Fail-closed: comment anchors/definitions, paragraph-mark topology deletions, table cell structure revisions, move-range markers, `numberingChange`, malformed or unpaired move wrappers, unsupported property snapshots, stale inventories and ambiguous element paths. These cases are not silently removed or normalized.

Five focused resolver regressions cover Accept, Reject, property restoration, selective structure-ID selection, stale baselines and unsupported structures. This increment does not claim Word GUI/save-round-trip acceptance and does not enable OMML writing. The complete CI matrix and the user's separate local Word/real-review acceptance remain required.

### Round-start historical baseline integration

`Start-CoReviewerRound.ps1` now creates SHA-256-bound, zero-model-call `revision-baseline` inventories for Reviewers, Manuscript and optional SI and records their paths/hashes/counts in `round_manifest.json`. The Skill and Agent workflow require a target-scoped gate before expensive discovery or writes, explicit author selection of Accept/Reject policy, a new output DOCX for supported resolution, and a fresh round after a source baseline has changed. This path does not authorize automatic acceptance of comment anchors or unsupported table/numbering structures.


### Deterministic OMML preview implementation

Added `scripts/co_reviewer/omml_converter.py` and the `preview-omml-docx` CLI command. It converts a deliberately limited LaTeX subset to a synthetic DOCX with OMML and checks the package/XML after creation. Unit tests cover fractions, radicals, Greek letters, scripts, unsupported-command rejection, package presence and no-overwrite behavior. This is a formula-rendering preview only; no existing Manuscript/SI is changed and no Track Changes are written around OMML. The existing structure classifier remains fail-closed for formula edits until the whole-formula revision wrapper and supported Word-version save/reopen acceptance are completed.

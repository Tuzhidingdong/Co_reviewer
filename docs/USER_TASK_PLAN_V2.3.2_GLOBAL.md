# User Task Plan — CO-REVIEWER-V2.3.2-GLOBAL-REV2

**Plan:** Co_Reviewer V2.3.2 Global Product Plan  
**Plan version:** Global REV2  
**Normative architecture:** V2.3.2 Frozen Engineering Contract  
**Ownership:** User-owned; frozen on 2026-11-20 at the user's request  
**Execution status:** The current implementation baseline and progress are maintained separately in STATUS.md.

This document defines the enduring product objective, scope, architecture constraints and acceptance criteria for Co_Reviewer. Antigravity and all Workers must preserve the objective, scope, constraints and acceptance criteria. Execution progress, branch state, test evidence, risks and unfinished work belong in the sibling STATUS.md.

This plan may only be changed when the user explicitly requests or approves a plan revision.

---

# 1. Objective

Build a general-purpose, reliable, traceable, human-authorized academic peer-review revision Skill and execution system.

Co_Reviewer manages the complete academic manuscript revision process across Reviewer Comments, Manuscript, Supporting Information (SI) and Response Letter. It coordinates evidence, decisions, edits, document versions, approvals, verification and final delivery.

It is not a prompt that simply asks a large language model to rewrite a manuscript. It is a document-engineering system in which models assist with bounded semantic reasoning, deterministic code performs document operations and safety verification, and the user retains authority over consequential decisions.

## 1.1 Complete user-visible workflow

The user supplies:

- Reviewer Comments or reviewer-response documents.
- The Manuscript DOCX.
- Optional Supporting Information DOCX.
- Existing experimental results, computational results, figures, tables, data and other supporting evidence.
- User decisions about the revision scope and scientific interpretation.

Co_Reviewer must:

1. Create an isolated Review Round Workspace and inventory the inputs and their hashes.
2. Inspect Reviewer Comments, native Word comments, target text and relevant document structures.
3. Audit the correspondence between each reviewer comment and the Manuscript / SI.
4. Distinguish exact confirmed matches, candidates, ambiguous matches and unresolved targets.
5. Propose evidence-grounded dispositions for each reviewer comment.
6. Create a user-reviewable Revision Decision Plan.
7. After user approval, prepare the exact execution scope and typed Patch operations.
8. Modify the Manuscript / SI using native Word Track Changes.
9. Verify edits, formatting, scientific tokens, document structures and cross-document consistency.
10. Execute multiple revision batches incrementally according to task dependencies and document versions.
11. Generate Working Clean, final Clean and a complete Response Letter.
12. Use Microsoft Word to generate final DOCX / PDF outputs and actual page-location evidence.
13. Produce a complete Release Package with documents, reports, hashes and transaction evidence.

Every reviewer comment must remain traceable through:

Reviewer Comment → Evidence → Decision → Target → Authorized Change → PatchOperation → Marked Revision → Verification → ResponseUnit → Final Location → Release.

Uncertainty must remain explicit. The system must not use model guesses to promote uncertain evidence or targets into completed work.

## 1.2 Scientific responsibility boundary

Co_Reviewer is a peer-review revision management and execution system, not an autonomous research system.

It must not:

- Invent experiments, calculations, simulations, characterization results or scientific values.
- Fabricate citations, references or scientific conclusions.
- Present unsupported speculation as validated results.
- Decide independently that new experiments or calculations should be performed.
- Treat text similarity, token overlap or model confidence as scientific evidence.
- Change the scientific meaning of the paper without user authorization.

Scientific content must be grounded in user-provided research results, existing manuscript content and explicitly supplied evidence.

When evidence is insufficient, the system must ask a precise question, provide a bounded suggestion, or leave the task in clarification_needed / blocked status.

## 1.3 Product completion milestones

Product completion is divided into two milestones.

**M1 — Engineering Release Candidate (RC)**

The engineering work completes the agreed implementation scope, code tests, CI, synthetic end-to-end workflow and applicable automated Word interoperability checks. The system has a usable core workflow, and unsupported structures can be identified, safely blocked and routed to manual handling.

M1 does not mean the user has completed final acceptance and does not authorize automatic publication.

**M2 — User-Accepted General Availability (GA)**

The user completes real Microsoft Word GUI inspection and runs a user-approved real review round using actual Reviewer / Manuscript / SI files. The user accepts the output documents and the complete workflow.

Only after M2 may V2.3.2 be declared user-accepted and eligible for GA release.

---

# 2. Current state

## 2.1 Normative architecture baseline

The frozen architecture contract is:

docs/V2.3.2_ENGINEERING_CONTRACT.md

It defines the following invariants:

- Immutable original DOCX.
- Marked as the sole revision-bearing source of truth.
- Deterministic Clean compilation from Marked.
- Target-scoped historical revision handling.
- Authorized Change and Protected Content.
- Token Lock and minimal-diff narrowing.
- Effective Style and FormatChange.
- Structure-specific OOXML Adapters.
- Package Transaction.
- Cross-document reference graph.
- One bounded QA remediation round.
- Separate automated and real Word GUI acceptance levels.

The architecture is frozen; this does not mean every capability is implemented or accepted.

Current implementation and evidence documents include:

| File | Responsibility |
|---|---|
| SKILL.md | Product operating rules and workflow |
| docs/V2.3.2_ENGINEERING_CONTRACT.md | Frozen architecture contract |
| docs/P0_IMPLEMENTATION_STATUS.md | P0 implementation and remaining acceptance |
| docs/P1_IMPLEMENTATION_STATUS.md | P1 implementation and acceptance status |
| docs/P1_MAINLINE_DEVIATION_REPORT.md | P1 deviations and verification boundaries |
| docs/BASELINE_V0.3.md | Original 76-test regression baseline |
| docs/DOCUMENT_IDENTITY.md | Document identity and TargetRef rules |
| schemas/ | Structured data contracts |
| tests/ | Unit, integration, structure and regression tests |

## 2.2 Current Git and implementation baseline issue

The V2.3.2 contract, P0/P1 implementation and subsequent fixes are distributed across multiple branches and Draft PRs.

Known historical references include:

- V2.3.2 contract branch: fix/v232-format-integrity-20261118, contract commit b55c688f004b03cb5c5ceebcd9ffb69d78950c60.
- P0/P1 implementation record branch: upgrade/v0.5.1-p1-audit-quality-implementation-20261114, record commit 4ee93a9c3d967f161ce70de51fcceb83f971549a.
- Fifth-round blind-test code: fix/bookmark-body-boundary-scope-20261119, commit 9f94b4da4b08222b7054f2591d5a5a073eb59f79.
- Fifth-round redacted evidence: evidence/round5-redacted-20261119, commit d1032ccba9f1b5d69a970c28518bb09e4733756a.

The default main branch must not be assumed to contain the complete V2.3.2 specification and implementation.

The project must first converge on one explicitly recorded integration baseline rather than continuing to stack unconstrained changes across multiple branches.

## 2.3 P0 foundation

The implementation already contains important capabilities:

- Preservation of the original 76 v0.3 tests.
- Document Engine and Identity modularization.
- Workflow Engine and task DAG.
- DocumentIdentity, DocumentObjectIdentity and TargetRef.
- Typed Patch, RevisionLedger and execution events.
- Native Track Changes.
- Working Clean projection.
- ResponseUnit lifecycle.
- Usage Audit.
- DOCX Quality Audit.
- Revision Baseline and Targeted Historical Revision Gate.
- Token Lock, Myers Diff and Protected Content.
- Basic Effective Style and FormatChange.
- Marked / Clean mechanisms.
- Package Staging and Commit Marker.
- Foundation for a cross-document definition / mention graph.

Existing implementation does not by itself establish full product acceptance.

## 2.4 P1 workflow capabilities

Existing or partially implemented features include:

- Round Workspace creation.
- Reviewer Audit / Inventory.
- Decision Plan and Execution Scope.
- Workflow / RevisionLedger synchronization.
- Immutable Batch Storage.
- Active Document Publication.
- Incremental multi-batch revision.
- Batch Recovery / Rollback.
- Round Reconciliation.
- Patch Supersession.
- Working Clean and ResponseUnit.
- Response Letter Renderer.
- Word COM / PDF Mapping.
- Manual Locator.
- Release Package and Manifest.

Historical validation must remain tied to its exact code HEAD.

For example, the historical 261/261 test run and Word/PDF acceptance on HEAD b144f4b282f76efcced165815d713747138bc831, and later CI results on other commits, prove only those specific versions. They must not be presented as evidence that the current development HEAD has passed the unified acceptance cycle.

## 2.5 Current structure-safety and usability findings

Fifth-round blind test:

| Metric | Result |
|---|---:|
| Total directives | 80 |
| APPLIED | 6 |
| BLOCKED | 74 |
| Manuscript Track Changes | 6 insertions / 6 deletions |
| SI Track Changes | 2 insertions / 2 deletions |
| Valid bookmark pairs | Manuscript 18; SI 30 |
| Original source hashes | Unchanged |
| Marked / Clean / PDF | Generated for both documents |
| Word COM | Completed for both documents |
| QA | PASS |

The 74 primary blockers:

| Reason | Count |
|---|---:|
| BOOKMARK_RANGE_GUARD | 66 |
| NO_EXACT_TARGET | 4 |
| UNSAFE_FIND_WINDOW | 3 |
| EMBEDDED_OBJECT | 1 |

Additional findings:

- 64 of 66 bookmark-blocked targets involved multiple intersecting bookmark ranges.
- The 31 proofErr intersections were classified SUPPORTED and must not be counted as independent primary blockers.
- Some targets had multiple candidate matches or insufficient unique-location evidence and must not be guessed.
- Drawing and unknown extension structures must remain protected.
- The current editor supports some contiguous Run operations, but structural gating remains too paragraph-oriented in some cases.

The plan therefore requires improving safe-range recognition and manual fallback usability, not simply reducing the BLOCKED count.

## 2.6 Main engineering risks

1. Multiple branches, PRs and status documents can create baseline drift.
2. Paragraph-level structural blocking may prevent safe edits elsewhere in the same paragraph.
3. Cross-document numbering changes need complete propagation and conflict-blocking rules.
4. Repeated JSON / Ledger transfer may cause unnecessary context and Token costs.
5. Excessive one-worker-per-step invocation may cause avoidable authorization friction and model overhead.
6. Word COM, Python, PowerShell and Word GUI dependencies must be separated by layer.
7. Complex OOXML Adapters must be delivered in phases so that a non-core Adapter cannot indefinitely delay the core RC.

---

# 3. Required outcome

## 3.1 Complete product workflow

Co_Reviewer must provide a complete and recoverable review-revision lifecycle:

Round Start → Inventory / Audit → Historical Revision Gate → Decision Plan → User Approval → Execution Scope → Workflow Preparation → Patch Batch → Preflight → Patch Approval → Native Track Changes Apply → Verify → Semantic Review → Publish → Refresh → Final Clean → Word/PDF → Response Letter → Release Package.

It must support:

- Single-comment handling.
- Batch comment handling.
- Manuscript and SI synchronization.
- reply_only.
- no_change.
- clarification_needed.
- Task dependencies.
- Multiple batches.
- Interrupted execution recovery.
- Complete final delivery.

No stage may bypass a required authorization or verification gate.

## 3.2 Precise editing and Bounded Safe Span

Build a reusable OOXML structure-tree classification and text-range mapping mechanism.

Map target text ranges to:

- Run.
- Text Node.
- XML document order.
- Bookmark Start / End.
- Comment Range Start / End.
- Field Boundary.
- Hyperlink.
- Drawing.
- Revision Wrapper.
- Other protected structures.

Advance structural safety decisions from whole-paragraph blocking to Bounded Safe Span analysis.

A safe span must satisfy all of the following:

1. Source text is uniquely located.
2. The exact edit range maps to a contiguous, supported ordinary Run sequence.
3. The edit range does not cross or modify protected structure boundaries.
4. XML order and all protected marker positions remain unchanged.
5. The decision does not depend on approximate matching or guessed character positions.
6. The operation still passes Authorized Change, Protected Content, Token Lock and format checks.

The mere presence of a bookmark in a paragraph must not automatically block every ordinary text edit elsewhere in that paragraph.

When multiple bookmarks intersect, all actual boundaries must be represented. No marker may be omitted, and no editable region may be manufactured by deleting or redefining markers.

A safe-span classification is only permission to proceed to the remaining authorization and validation gates. It is not write authorization by itself.

## 3.3 Usable fallback for blocked operations

When an operation cannot be safely automated, the system should still help the user complete the work.

Without changing the BLOCKED status or writing to the DOCX, produce an independent manual_suggestion or equivalent diagnostic artifact containing:

- Document role and Word part.
- Stable object identity and location hint.
- Exact source excerpt.
- Before / after context anchors.
- Specific blocking reason.
- Involved Word structures.
- Missing safety evidence.
- A proposed edit based only on available evidence.
- An explicit statement that the proposal has not been written to the DOCX.
- Whether the user must edit manually in Word or supply clarification.

A suggestion must never be represented as an Applied Patch or as an already revised manuscript passage.

When scientific evidence is missing, report the missing evidence instead of manufacturing a plausible scientific rewrite.

## 3.4 Cross-document reference and numbering synchronization

Build an auditable Reference Definition / Mention Graph.

For Figure, Table, Equation, Section, Citation and other managed objects:

- Identify definitions.
- Identify all known mentions.
- Distinguish native Word reference fields from static text.
- Record uniquely resolved, ambiguous and unresolved relationships.
- Show the impact of every proposed numbering change.

Numbering changes must follow complete propagation rules:

1. The user explicitly authorizes the definition or numbering change.
2. The system resolves all known affected reference edges.
3. It checks for ambiguity, omissions and unresolved mentions.
4. It generates a complete Package-wide Change Plan.
5. The user reviews the exact impact list.
6. All affected definitions and mentions are handled as one authorized batch.
7. The complete change is verified and committed through the Package Transaction.
8. The final graph is checked for known broken, duplicated or conflicting references.

If a potentially affected reference cannot be uniquely resolved, the related numbering propagation operation must be blocked as a whole. The system must not update only the subset it happened to find.

Static text references remain static text and must not be misrepresented as Word REF fields.

## 3.5 Risk tiers and batch approval

Reduce repetitive interaction costs without reducing user authority.

Support deterministic Risk Tiers:

- **Tier 0 — Low Risk:** Local language edits within confirmed ranges, without scientific-token changes or structural-boundary interactions.
- **Tier 1 — Elevated Risk:** Changes involving scientific meaning, numeric values, units, formulas, references, cross-document synchronization, structure or formatting.
- **Tier 2 — Unsupported / Ambiguous:** Insufficient evidence, non-unique targets or unsupported Adapters. These must be blocked or sent for clarification.

Risk Tier must be based on deterministic features and explicit rules. A model may recommend classification but must not lower the risk tier by itself.

Approval rules:

1. The user still reviews the Revision Plan.
2. The user still reviews the Execution Scope.
3. Patch Approval remains bound to an exact immutable Batch and its operations.
4. Semantic Review remains a separate stage.
5. Tier 0 operations may be grouped into a user-reviewed batch summary when independent and risk-consistent.
6. Tier 1 changes must be highlighted and must not be hidden inside ordinary language-edit batches.
7. Tier 2 must not be released through batch approval.
8. Any change to batch content, source or hash invalidates the old approval.

The user need not mechanically approve every ordinary punctuation or safe language replacement individually, but must be able to review and authorize the exact batch scope.

## 3.6 Token and context optimization

Use the filesystem and Ledgers as persistent sources of truth. Model context carries only necessary information.

The Supervisor normally receives:

- Current task summary.
- Current effective baseline.
- Current Batch ID.
- Current comment requiring a decision.
- Relevant target and bounded evidence excerpts.
- State changes and blocker summary.
- Paths to necessary detailed diagnostic files.

Do not repeatedly load complete Inventory, Workflow Ledger, RevisionLedger and all historical reports into model context.

Workers receive only the inputs and verification boundaries required for their assigned work.

Refresh must use TargetRefs, target groups, dependency edges and data versions for incremental invalidation. A change to one target must not automatically cause re-analysis of every Reviewer Comment and the entire document.

Final pagination evidence is a document-role-level dependency: when the final Clean hash for a document role changes, that role's pagination and mapping must be regenerated. This must not force unrelated scientific semantic discovery to run again.

## 3.7 Independently testable OOXML and Word architecture

Separate the system into three layers.

**Core Deterministic Layer**

- Document structure parsing.
- Range / Safe Span model.
- Target Identity.
- Token Lock.
- Patch / Workflow / Ledger.
- Reference Graph.
- Marked / Clean projection.
- Package Transaction.
- Verify / Quality Audit.

Core deterministic logic must not require Microsoft Word.

**OOXML Adapter Layer**

- Ordinary text.
- Formatting.
- Bookmark / Comment.
- Field / Hyperlink.
- Table / List / Section.
- OMML.
- Drawing / Embedded Objects.
- Other specialized structures.

Each writing Adapter requires its own supported-range contract, safety rules, tests and Word acceptance evidence.

**Word Interoperability Layer**

- Microsoft Word COM.
- Native revision rendering.
- Final pagination and line numbers.
- PDF export and correspondence checks.
- Word Save / Close / Reopen.

COM must not become a prerequisite for the basic state machine, Token Lock, Reference Graph, Patch verification or core deterministic tests.

---

# 4. Scope

## In scope

### A. Skill and global workflow

- Unified Round workflow.
- Supervisor / Worker responsibilities.
- User approval and permission gates.
- Batch Risk Tiers.
- Batch approval summaries.
- Complete state machine and recovery.
- Low-context, low-Token execution protocol.
- Global Task Plan / STATUS separation.

### B. Baseline and project governance

- One effective development baseline.
- Branch dependencies and PR convergence strategy.
- Hash binding among normative documents, implementation files and test evidence.
- Current status snapshot.
- Isolation of test results by exact HEAD.
- Prevention of unconstrained parallel edits to the same core module.

### C. Inventory, Audit and Evidence

- Reviewer / Manuscript / SI Inventory.
- Native comment and reviewer anchor extraction.
- Exact target matching.
- Figure / Table / Reference Audit.
- Historical revision baseline.
- Evidence Packet.
- Cache and incremental refresh.

### D. Decision, Workflow and stable identity

- Decision Plan.
- Execution Scope.
- Workflow Ledger.
- DocumentIdentity / ObjectIdentity / TargetRef.
- Task DAG and conflict management.
- Risk Tier and approval summary.
- Incremental Invalidation.

### E. Document safety and editing

- Bounded Safe Span.
- XML tree and interval classification.
- Typed Patch.
- Protected Content / Authorized Change.
- Token Lock / Myers Diff.
- Effective Style / FormatChange.
- Native Track Changes.
- Marked / Clean.
- Structure-specific Adapters.
- Manual-edit suggestion fallback.

### F. Multi-document consistency

- Reviewer / Manuscript / SI / Response Letter relationships.
- Figure / Table / Equation / Citation Reference Graph.
- Package-wide Numbering Change Plan.
- Unique reference propagation.
- Ambiguity blocking.
- Incremental dependency refresh.

### G. Multi-batch execution and final delivery

- Immutable Batch.
- Apply / Verify / Publish.
- Rollback / Recovery / Reconcile.
- Working Clean.
- Final Clean.
- Word COM / PDF.
- Final Location Map.
- Response Letter.
- Release Manifest / Package.

### H. Layered testing and compatibility

- Python / Windows / PowerShell testing.
- OOXML Adapter testing.
- Synthetic end-to-end workflow.
- Word COM interoperability.
- Word GUI manual acceptance.

## Out of scope

- Autonomous scientific research or creation of unprovided experimental data.
- Scientific conclusions without evidence.
- Unapproved decisions to add experiments or calculations.
- Unconditional automatic writing support for every Word OOXML structure.
- Relaxing safety gates to increase APPLIED counts.
- Embedding one test paper's content in production code or Skill rules.
- Replacing incremental invalidation with full re-execution.
- Splitting every Worker substep into a separate invocation.
- Automatically approving changes not reviewed by the user.
- Automatically merging PRs, publishing releases or submitting manuscripts.
- Indefinitely delaying the core RC to implement one complex Adapter.

---

# 5. Architecture / constraints

## Runtime(s)

- Python 3.10 / 3.13 support matrix.
- Windows.
- Windows PowerShell 5.1.
- Microsoft Word Desktop / COM.
- Antigravity Supervisor.
- OpenCode GO Worker.
- GitHub / GitHub Actions.

## Required tools / integrations

- Existing scripts/co_reviewer modules.
- scripts/co_reviewer_cli.py.
- Round / Batch / Refresh / Reconcile / Finalization PowerShell entry points.
- python-docx and OOXML processing.
- Word COM / PDF Mapper.
- JSON Schema contracts.
- GitHub CI.
- Formal artifacts and reports under the current Round Workspace.

No parallel document-writing system may be introduced to bypass the existing formal execution path.

## Model or cost constraints

### 1. Disk is the source of truth

- Workflow Ledger, RevisionLedger, Batch artifacts, Inventory, Evidence and Reports are persistent sources of truth.
- Model context carries IDs, paths, hashes, current states and bounded summaries.
- Do not repeatedly serialize complete JSON artifacts into model context.
- Only comments and evidence required for the current decision should enter the model.
- Deterministically computable work must not be delegated to a model.

### 2. Batch authorization for Workers

Before starting a Worker, show:

- Role.
- Model.
- Project Root.
- Inputs.
- Expected outputs.
- Verification boundary.

The user may authorize a group of consecutive subtasks that share the same role, project, model and execution scope.

A changed authorization scope, new project, new model or new high-risk action requires renewed authorization.

Invisible Worker calls are prohibited.

After completion, report Job ID, Session ID, final state and available Token evidence.

### 3. Incremental computation and model calls

- Reuse trusted Inventory when source hashes are unchanged.
- Refresh only affected targets and dependencies.
- Do not re-analyze the entire document after a local change.
- Do not repeat model decisions for unaffected tasks.
- Run full Word pagination only when the final document-role version changes.
- Preserve evidence of actual model calls and Token usage; do not infer actual Token usage from text length.

## Compatibility constraints

### 1. Frozen V2.3.2 invariants

The following may not change:

1. Original DOCX is immutable.
2. Marked is the sole revision-bearing source of truth.
3. Clean is deterministically generated from Marked.
4. Historical revisions are inventoried before model-intensive discovery.
5. Every edit has precise authorization and evidence.
6. WordprocessingML is modified only through structure-specific Adapters.
7. Unsupported structures fail closed.
8. All final managed files belong to one Package Transaction.
9. Cross-file references use a directed definition / mention graph.
10. Phase 7 remediation is limited to one round.
11. Automated tests do not replace Word GUI acceptance.

### 2. Bounded Safe Span

Structural safety must distinguish:

- An edit fully inside an independently safe text span.
- An edit fully inside a protected span.
- An edit crossing a protected boundary.
- An edit involving multiple protected structures.
- An edit whose exact location cannot be uniquely mapped.

Only an operation located within a supported safe span, and passing all other authorization and formatting checks, may proceed.

Text inside bookmark or comment ranges remains protected by default. Editing inside those ranges requires explicit semantic rules, a dedicated Adapter and separate acceptance.

All boundaries must be considered when multiple bookmarks intersect. No marker may be ignored.

### 3. Reference propagation

- Reference discovery and mutation authority are separate.
- Only uniquely resolved relationships may enter automatic propagation.
- Numbering changes require Package-wide authorization.
- All affected definitions and mentions must be covered by the change plan.
- Ambiguous or unresolved affected references block the related numbering transaction.
- Unrelated ordinary-text revisions may proceed independently.
- The final graph must be checked for known broken, duplicate or conflicting references.

### 4. Word and COM decoupling

Core logic must not require Microsoft Word.

Word COM is used only for:

- Actual Word layout.
- Pagination and line numbers.
- Native revision interoperability.
- Save / Close / Reopen.
- PDF correspondence checks.

A COM failure must not be misreported as failure of pure Python core logic. A core logic PASS must not be misreported as Word compatibility PASS.

### 5. Formatting and document safety

- Only supported contiguous ordinary Run sequences may use the ordinary text Adapter.
- Mixed formatting requires a complete replacement_format_map.
- FormatChange requires explicit authorization and a complete prior effective-format snapshot.
- Do not cross fields, formulas, hyperlinks, content controls, bookmark/comment boundaries or unknown extension structures.
- Do not rebuild entire paragraphs or use regex / XML string replacement.
- Never independently modify Clean.
- Never overwrite committed Batch artifacts or historical evidence.

## Other non-negotiable rules

### Unified factual baseline

STATUS.md is the single current execution-state snapshot.

It must record:

- One effective integration branch and HEAD SHA.
- Parent commit and PR dependency relationships.
- Hashes of current Skill, Contract and key Schemas.
- Current implementation state.
- Status and evidence for every AC.
- M1 status.
- M2 status.
- Deferred Adapter list.
- Current blockers and user decisions pending.
- Exact HEAD of the latest executed test evidence.

Test results from different branches must not be combined.

Until the integration baseline is explicitly selected and recorded, multiple Workers must not make unconstrained parallel changes to the same core module on different branches.

The main branch may be designated the complete release baseline only after baseline convergence and acceptance.

### Generality and test isolation

- Production Skill must not contain a test paper's scientific content.
- Test fixtures and evidence must be isolated from production code.
- No single paper or journal may become an implicit general architecture assumption.
- Every new writing Adapter requires its own structure test matrix.

### User authority

- The user owns the global plan.
- The user approves revision decisions.
- The user approves exact execution scope.
- The user approves exact Patch Batches.
- The user confirms scientific semantics.
- The user performs final Word GUI acceptance.
- The user decides PR merge and formal release.

---

# 6. Acceptance criteria

Acceptance is divided into:

- **M1 Blocking:** Required for an Engineering RC.
- **M2 Blocking:** Required for user acceptance and GA.
- **Deferred Capability:** Automatic writing for these complex structures is not required for the core RC, but structure recognition, protection, diagnostics and safe fallback are required.

## 6.1 M1 — Engineering Release Candidate

### A. Baseline and engineering governance

- [ ] **AC-01 — Single Active Baseline:** One effective integration development baseline is explicitly recorded with branch, HEAD SHA and parent commit.
- [ ] **AC-02 — Branch Convergence:** Every PR and branch required for RC has an explicit dependency, integration order and diff review; no code is copied or lost without provenance.
- [ ] **AC-03 — Status Consistency:** STATUS.md records exact hashes for current code, Skill, Contract, status files and test evidence; evidence from different HEADs remains isolated.
- [ ] **AC-04 — Plan Protection:** The global User Task Plan is not changed by Workers; progress is recorded in STATUS.

### B. Workspace and evidence

- [ ] **AC-05 — Round Isolation:** Each round uses an isolated workspace with explicit input/output paths and a manifest.
- [ ] **AC-06 — Source Integrity:** All original files remain unchanged and SHA-256 is verifiable.
- [ ] **AC-07 — Cache Reuse:** Inventory for unchanged hashes is reused; changed files are selectively reparsed.
- [ ] **AC-08 — Comment Coverage:** Every Reviewer Comment enters audit and receives exactly one final disposition; no comment is silently omitted.
- [ ] **AC-09 — Evidence Grounding:** Confirmed targets have unique evidence; ambiguous candidates cannot be automatically promoted.
- [ ] **AC-10 — Historical Gate:** Initial historical revisions are inventoried; target-overlapping history is resolved only through an explicit user policy.

### C. Decision and Workflow

- [ ] **AC-11 — Decision Plan:** Every task has Reviewer Comment, Evidence, Target, Disposition and Dependency.
- [ ] **AC-12 — Authorization:** Revision Plan, Execution Scope, Patch Approval and Semantic Review have separate verifiable user authorization records.
- [ ] **AC-13 — Stable Identity:** Supported document objects have traceable identities; ambiguous objects are not matched by guessed paragraph indices.
- [ ] **AC-14 — DAG Scheduling:** Actual dependencies are respected and ready batches are conflict-free.
- [ ] **AC-15 — Incremental Invalidation:** Upstream changes invalidate only affected TargetRefs, target groups and downstream dependencies, not all unrelated tasks by default.
- [ ] **AC-16 — Risk Tier:** Ordinary text, high-risk scientific edits and unsupported operations are deterministically classified; low-risk edits can be summarized as batches and high-risk edits are highlighted.
- [ ] **AC-17 — Batch Approval Integrity:** Batch authorization is bound to immutable batch content and hashes; any batch change invalidates prior approval.

### D. Patch and Word editing

- [ ] **AC-18 — Typed Patch:** Every executable edit has an exact target, source text, replacement text, Evidence ID and Reviewer Comment ID.
- [ ] **AC-19 — Native Track Changes:** All text revisions use native Word w:ins / w:del.
- [ ] **AC-20 — Minimal Diff:** XML write scope matches the authorized edit; unnecessary paragraph or Run reconstruction is absent.
- [ ] **AC-21 — Token Protection:** Numeric values, units, precision, formulas, citations and figure/table numbering require exact authorization when changed.
- [ ] **AC-22 — Format Preservation:** Unchanged text retains character-level formatting; mixed-format replacements have complete mappings; FormatChange has a valid prior effective-format snapshot.
- [ ] **AC-23 — Exact Verification:** Apply verifies the complete target paragraph and operation order and detects all unauthorized direct-text changes.
- [ ] **AC-24 — Fail-Closed:** Non-unique targets, unsupported structure crossings and missing safety evidence are blocked.

### E. OOXML safety and usability

- [ ] **AC-25 — Structural Event Map:** Target text is mapped to actual Runs, Text Nodes and XML document order.
- [ ] **AC-26 — Bounded Safe Span:** When a paragraph contains multiple bookmarks or other protected structures, the system can identify independent safe text spans between actual boundaries.
- [ ] **AC-27 — Boundary Protection:** Edits that cross or alter protected boundaries are blocked; intersecting and nested bookmark ranges are not omitted.
- [ ] **AC-28 — Safe Span Does Not Bypass Authorization:** A safe-span candidate must still pass Token Lock, Authorized Change, Protected Content and formatting checks.
- [ ] **AC-29 — Manual Fallback:** Blocked operations can provide exact document anchors, reasons and an explicitly unapplied suggestion where evidence allows; missing evidence is clearly reported.

### F. Cross-document and transaction integrity

- [ ] **AC-30 — Reference Graph:** Definitions, mentions, unique relations, ambiguous relations and unresolved relations are auditable.
- [ ] **AC-31 — Numbering Propagation:** Number changes require a complete impact list, user Package-wide authorization and consistency verification; ambiguous edges block the related numbering transaction.
- [ ] **AC-32 — Immutable Batch:** Every Batch has Parent Hash, Intent, Scope, Plan Snapshots and Receipt.
- [ ] **AC-33 — Sequential Publication:** Later batches use the latest verified Active Document as parent.
- [ ] **AC-34 — Recovery and Rollback:** Interrupted recovery, failed batch handling and Rollback preserve historical artifacts and reject unknown hash states.
- [ ] **AC-35 — Package Transaction:** Multi-file Release is visible only after Manifest, Receipt and Commit Marker validation.

### G. Final delivery and automated validation

- [ ] **AC-36 — Working / Final Clean:** Working Clean and Final Clean are separate; Final Clean is deterministically compiled only from final Marked.
- [ ] **AC-37 — Word/PDF Mapping:** Page and line numbers are produced by actual Word COM and corroborated against the corresponding PDF.
- [ ] **AC-38 — Manual Locator:** Table and non-body targets support author-confirmed manual locations; page and line numbers are never estimated.
- [ ] **AC-39 — Response Letter:** Revision excerpts come from exact verified PatchOperations; No-change and Reply-only do not create fictitious edits.
- [ ] **AC-40 — ResponseUnit:** Every Reviewer Comment's ResponseUnit agrees with Patch status, semantic review and location requirements.
- [ ] **AC-41 — Quality Audit:** All final DOCX / Response Letter files pass their designated quality profiles, with reports bound to file hashes.
- [ ] **AC-42 — Release Manifest:** The Manifest validates required tasks, Patches, batches, ResponseUnits, quality reports and transaction evidence.
- [ ] **AC-43 — Complete Package:** The complete delivery package contains required Marked, Clean, Response Letter, PDF, location evidence and quality reports.
- [ ] **AC-44 — Bounded Remediation:** Only one formal bounded remediation is allowed; a second QA failure blocks Release.
- [ ] **AC-45 — Full Synthetic Round:** One continuous synthetic workflow passes, covering at least two sequentially published batches, dependency refresh, final mapping, Response Letter and Release Package.

### H. Testing and architectural independence

- [ ] **AC-46 — Legacy Baseline:** All 76 original v0.3 tests remain; none is deleted or weakened.
- [ ] **AC-47 — Core Test Independence:** Workflow, Patch, Token Lock, Reference Graph, transactions, projections and core OOXML tests do not require Microsoft Word.
- [ ] **AC-48 — Platform Matrix:** The supported Python versions, Windows and PowerShell 5.1 test matrix passes.
- [ ] **AC-49 — Adapter Test Isolation:** Each enabled writing Adapter has dedicated positive, negative, boundary and regression tests.
- [ ] **AC-50 — Honest RC Report:** The RC report distinguishes code tests, CI, Word COM, unimplemented Adapters and manual acceptance.

## 6.2 M2 — User Acceptance / GA

- [ ] **AC-51 — Word GUI Open:** The user opens final Marked and Clean DOCX in the target Microsoft Word environment without repair warnings.
- [ ] **AC-52 — Native Revision Inspection:** The user can inspect native Track Changes insertions, deletions and relevant formatting changes.
- [ ] **AC-53 — Scientific and Structural Inspection:** The user inspects formulas, subscripts/superscripts, units, numbers, citations, bookmarks, fields, tables and SI consistency.
- [ ] **AC-54 — Save / Close / Reopen:** The user saves, closes and reopens the files in Word; structure and content remain correct.
- [ ] **AC-55 — PDF and Response Inspection:** The user confirms final PDF pagination, line numbers and Response Letter locations.
- [ ] **AC-56 — Real Review Round:** A complete review round is performed with user-approved real Reviewer / Manuscript / SI files.
- [ ] **AC-57 — Release Acceptance:** The user inspects the Release Package and unresolved items and explicitly accepts before formal publication.

## 6.3 Deferred capabilities — non-blocking for core RC

Automatic writing for the following capabilities is not a default M1 blocker, provided recognition, protection, diagnostics and safe manual fallback are implemented:

1. Complete OMML formula replacement Adapter.
2. Complex Word Field and Citation writing Adapters.
3. Arbitrary complex paragraph split / merge and topology changes.
4. Broader cross-version Word interoperability.
5. Advanced render comparison and automatic visual-difference judgement.
6. Advanced context-aware micro-typography.

Deferred does not mean ignored:

- Relevant structures must be recognized.
- Original structure must be preserved.
- Unsupported automatic edits must be blocked.
- Clear manual location and diagnostic evidence must be provided.
- Each Adapter must pass independent tests, Word interoperability and acceptance before being enabled.
- Deferred capabilities must not be reported as implemented.

---

# 7. Tests / evidence

## Required tests

### 7.1 Global baseline tests

- Git branch and PR dependency convergence.
- STATUS consistency with exact code HEAD.
- Skill, Contract and Schema hashes.
- Original 76-test regression baseline.
- CLI and PowerShell compatibility.

### 7.2 Core deterministic tests

- DOCX Inventory / Cache.
- Reviewer Comment Anchor.
- Exact / Ambiguous Target.
- Decision Plan Provenance.
- Workflow DAG.
- Document Identity / TargetRef.
- Token Lock / Protected Spans.
- Myers Diff.
- Effective Style / FormatChange.
- Patch / RevisionLedger.
- Marked / Clean.
- Package Transaction.
- Release Manifest.

### 7.3 OOXML tree and interval safety matrix

Must include:

- Edits before, inside and after a single bookmark.
- Safe text between multiple bookmarks in the same paragraph.
- Nested bookmarks.
- Intersecting bookmarks.
- Edits adjacent to bookmark boundaries.
- Edits crossing bookmark boundaries.
- Comment Range Start / End.
- Field boundaries.
- proofErr.
- Hyperlink.
- Content Control.
- Drawing / Embedded Object.
- Revision Wrapper.
- Table / List / Section.
- Mixed-format multiple Runs.
- Unknown Extension.

For each scenario, define the expected outcome:

ALLOW_CANDIDATE / BLOCKED / MANUAL_REVIEW.

ALLOW_CANDIDATE means only that the structural gate permits further checks. It does not bypass authorization or verification.

Tests must prove:

- Protected nodes are unchanged.
- Unrelated text remains unchanged.
- Bookmark markers are not lost, copied, reordered or duplicated.
- Every boundary is identified when bookmarks intersect.
- Complete target text and Run formatting mappings are correct.

### 7.4 Reference Graph tests

- Unique references.
- Duplicate references.
- Missing references.
- Ambiguous references.
- Definition renumbering.
- Multiple static-text mentions within one document.
- Cross-document Manuscript / SI references.
- Fully authorized propagation.
- Transaction blocking when a partial reference remains unresolved.
- Post-publication graph consistency.

### 7.5 Manual fallback tests

- Blocked target with a unique anchor.
- Blocked target with multiple similar candidates.
- Target inside a Table Cell.
- Target inside a field or formula.
- Insufficient scientific evidence.
- Cases requiring user clarification.

All suggestions must carry an explicit not-applied indication and must not be written into formal DOCX outputs.

### 7.6 Risk approval and batch tests

- Multiple low-risk operations approved as one batch.
- High-risk operations visibly highlighted.
- Different risk tiers are not conflated in approval summaries.
- Batch content or hash change invalidates old approval.
- One Worker authorization covers consecutive subtasks within the same scope.
- A changed Worker role, model, project or execution scope requires renewed authorization.

### 7.7 Integration and Word tests

Pure Python deterministic tests must not depend on Word COM.

Word COM tests are separate and cover:

- DOCX opening.
- Native Track Changes.
- Save / Close / Reopen.
- Word Pagination.
- PDF Export.
- Word / PDF page correspondence.
- Location evidence validation.

Finally run one independent synthetic full review round. All files must remain inside its dedicated test workspace.

## Regression tests

Preserve:

- All 76 original v0.3 regression tests.
- All existing P0 / P1 tests.
- Workflow / Batch / Recovery / Release.
- Bookmark / Comment / Field / Hyperlink.
- Effective Style / FormatChange.
- Token Lock / Protected Content.
- Marked / Clean.
- Package Transaction.
- Reference Graph.
- ResponseUnit / Response Letter.
- Word/PDF Mapper.
- Windows paths, Symlinks and short / long paths.
- Fifth-round classification and bookmark-boundary scenarios.

Fifth-round test data may be used only as test fixtures and evidence reports; it must not enter production Skill or production behavior.

## Test execution strategy

1. During development, run only the minimum self-checks required by the current change.
2. Do not repeatedly run the full test suite when no implementation or evidence has changed.
3. Related code changes, tests and reviews may be grouped into one authorized Worker execution scope.
4. After a critical implementation phase is frozen, run the full automated suite and CI once.
5. Then execute one continuous synthetic end-to-end round.
6. Diagnose genuine failures with complete evidence and fix them in a focused batch.
7. After a fix, run the necessary targeted regression tests.
8. Record Word COM and user Word GUI acceptance separately.

---

# 8. Deliverables

## 8.1 Global Skill and governance files

- Complete SKILL.md.
- This user-owned User Task Plan.
- The unique current STATUS.md.
- V2.3.2 Frozen Engineering Contract.
- P0 / P1 / P2 implementation status and deviation reports.
- Unified Workflow / Data Contract documentation.
- Stable identity and TargetRef documentation.
- All production JSON Schemas.

## 8.2 Engineering implementation

- Python core logic.
- OOXML structure tree and Range / Safe Span mechanism.
- Structure-specific Adapters.
- CLI.
- PowerShell 5.1 workflow entry points.
- Workflow / Revision / Batch / Recovery / Release transactions.
- Reference Graph.
- DOCX Quality Audit.
- Word COM / PDF Mapper.

## 8.3 Testing and evidence

- Original 76-test regression report.
- Current code HEAD-specific automated test report.
- GitHub CI report.
- OOXML structure safety matrix.
- Reference Graph validation report.
- Synthetic end-to-end Round.
- Word COM acceptance results.
- Release Manifest validation report.
- SHA-256 and transaction evidence.
- Unsupported Adapter and manual-handling list.

## 8.4 M1 Engineering RC

- Installable and callable Skill.
- Unified Windows Round / Batch / Refresh / Reconcile / Finalization workflow.
- Complete synthetic Round outputs.
- Marked / Clean.
- Response Letter.
- PDF and location mapping.
- Release Package.
- RC test and quality reports.
- Blocked / Deferred capability list.
- Current effective code baseline and PR convergence record.

## 8.5 M2 User Acceptance / GA

- User Word GUI acceptance record.
- User real review-round acceptance record.
- User-approved formal Release Package.
- Final release version and corresponding code commit.

---

# 9. Release boundary

- [ ] Code/files only; do not publish.
- [x] Prepare an Engineering Release Candidate; wait for my approval before publishing.
- [ ] Other.

Rules:

1. Development occurs on an isolated branch with an explicit baseline.
2. Do not directly modify main.
3. Do not automatically merge PRs.
4. Do not automatically create a formal release.
5. Do not submit manuscripts or files to journals.
6. Passing M1 permits only the statement Engineering RC Ready.
7. The user must complete M2 before GA can be declared.
8. The user retains final merge and formal release authority.

---

# 10. User decisions already made

The following decisions are already established and must not be asked again or changed unilaterally.

## 10.1 Product and scientific boundary

1. Co_Reviewer is a complete multi-document academic peer-review revision system.
2. It does not conduct autonomous research or invent experiments, calculations, data or citations.
3. Scientific content must be grounded in user-provided evidence.
4. The user owns scientific decisions; models provide suggestions only.
5. Reviewer Comments, Manuscript, SI and Response Letter must remain explicitly traceable.

## 10.2 Architecture

6. V2.3.2 Frozen Engineering Contract is the normative baseline.
7. Frozen architecture does not mean every capability is implemented.
8. Do not redesign the overall architecture.
9. All writes must use the formal Patch / Marked / Verify / Publish / Release path.
10. Supported recognition and automatic writing support are different capabilities; unsupported structures must be safely blocked.

## 10.3 DOCX and scientific protection

11. Original DOCX is immutable.
12. Marked is the sole revision-bearing source of truth.
13. Clean is deterministically generated from Marked.
14. Historical revisions are inventoried first, and only target-overlapping history is resolved.
15. Every edit has explicit Authorized Change.
16. Protected Content must remain unchanged.
17. Numbers, units, formulas, citations and numbering are precisely protected.
18. Do not bypass official safety gates.
19. Do not directly repair Clean.
20. Phase 7 allows at most one bounded remediation.
21. A second QA failure blocks release.

## 10.4 Usability and human interaction

22. A safety block should provide precise location, reason and an unapplied suggestion where evidence allows, not merely an error code.
23. Ordinary low-risk edits may be approved as a batch with a precise immutable scope.
24. High-risk changes must be highlighted and must not be hidden among ordinary language edits.
25. Every batch approval is bound to exact content and hashes.
26. Workers must never be started invisibly.
27. Consecutive Worker subtasks within the same execution scope may share authorization; role, model, project or scope changes require renewed authorization.
28. The user owns scientific semantic review and final Word GUI acceptance.

## 10.5 Token and engineering efficiency

29. Filesystem artifacts and Ledgers are the sources of truth.
30. Model context should use local Evidence Packets and state summaries.
31. Do not repeatedly load complete Manuscript, SI and all JSON artifacts by default.
32. Dependency refresh uses TargetRef and incremental invalidation.
33. Word COM must not be required by core deterministic tests.
34. Do not run repetitive tests without new evidence value.
35. Do not represent estimated Token usage as actual usage.

## 10.6 Generality and tests

36. Skill and production code must remain cross-disciplinary.
37. Test-paper content must not be added to production rules.
38. The original 76 regression tests must not be deleted or weakened.
39. Each production-enabled Adapter requires independent structural tests.
40. Unsupported Adapters must be explicitly marked Deferred.
41. Historical test results such as 261/261 and 294/294 apply only to their exact HEADs.
42. Unit tests, CI, Word COM, Word GUI and real review-round acceptance are separate verification levels.

## 10.7 Release

43. M1 and M2 must be reported separately.
44. M1 is an Engineering RC, not user acceptance.
45. M2 includes user Word GUI and real review-round acceptance.
46. The user controls final merge and formal release.

---

# Global Plan Maintenance Rules

This document is the user-owned global requirement contract and must not be overwritten by execution status.

STATUS.md is the unique current execution-state snapshot and must contain:

1. Current effective branch and HEAD SHA.
2. Baseline parent commit and PR dependencies.
3. Skill, Contract and key Schema hashes.
4. Current implementation state.
5. Status and evidence for every AC.
6. M1 status.
7. M2 status.
8. Deferred Adapter list.
9. Current blockers and decisions pending from the user.
10. Exact HEAD of the most recent executed test evidence.

Allowed status values:

NOT_STARTED / IMPLEMENTED_UNTESTED / TESTED_PASS / BLOCKED / DEFERRED / USER_ACCEPTED.

Do not write merely "completed" without evidence.

Acceptance Criteria may be changed only through a formal plan revision explicitly approved by the user.

## Final Completion Definition

**Engineering RC Ready:**

All M1 Blocking criteria have passed, the complete synthetic workflow has passed, automated evidence is bound to exact code HEADs, and unsupported structures are correctly blocked with manual fallback.

**V2.3.2 GA Accepted:**

M1 has passed; all M2 user-acceptance criteria have passed; the real review round and final Release Package have been accepted by the user.

Each milestone must be reported honestly. A partial success must never be presented as full product completion.

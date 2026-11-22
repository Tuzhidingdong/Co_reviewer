# Document Semantic & Format Profile — Frozen Design

Status: frozen implementation direction, 2026-11-22.

## Purpose

Co_Reviewer must not depend on a flagship LLM to rediscover the formatting meaning of every inserted character. The system extracts a reusable occurrence-level archive during the first DOCX inventory pass. Later revision operations refer to a specific source occurrence and let deterministic code recover its supported character-format properties.

The profile is a source-bound evidence archive, not a universal style sheet. Two visually identical characters at different locations remain independent occurrences. The first manuscript analysis also creates a sparse semantic-annotation artifact, bound to the profile and source hashes, so contextual roles are recorded once and reused later.

## One-pass source intake

1. Start-CoReviewerRound.ps1 copies and hashes immutable source DOCX files.
2. The existing audit command parses each source DOCX once and caches its paragraph/Run inventory.
3. The starter runs scripts/co_reviewer/semantic_profile.py against that already-created inventory. The profile builder does not open DOCX files.
4. The profile is written to reports/document_semantic_format_profile.json and its path/hash is recorded in round_manifest.json.
5. Profile data is embedded in the corresponding document row in workspace_inventory.json so Workflow Preparation can validate format references without another source read.
6. During the first manuscript analysis, create reports/document_semantic_annotations.json using schemas/document-semantic-annotations.schema.json. Store sparse occurrence-level meanings and evidence, not a global character dictionary and not an annotation for every ordinary prose letter.

The inventory cache schema is bumped when the Run-format payload changes. A cache entry without the current format-span data must not be treated as a valid profile source.

## Two independent formatting zones

Every document profile separates:

### body_occurrences

Contains individual occurrences from the manuscript body: standalone Latin letters, Greek letters, script characters, selected mathematical symbols, and Runs that carry direct formatting. Every occurrence records:
- an occurrence ID bound to document role, source SHA-256, Word part, paragraph, offsets, kind and text;
- exact visible text and paragraph/Run location;
- before/after/sentence context;
- paragraph style and paragraph-format snapshot;
- supported direct character-format properties;
- unsupported direct-format properties which the current writer cannot safely reproduce;
- a placeholder for contextual semantic-role annotation.

Do not merge all occurrences of a, α, m, or any other symbol into one global rule. a as an article, variable, sample label or axis label must remain separate occurrences with their own contexts and formats.

### reference_entries and reference_occurrences

The bibliography is a separate format system. The profile stores each detected reference paragraph, its paragraph style/format, every Run-format span, and a style-pattern grouping. These groups are descriptive; they do not authorize choosing the largest group as the correct template.

When correcting one reference:
- use only entries from the same reference list;
- compare the corresponding bibliographic field (author, title, journal, year, volume, pages, DOI, etc.);
- use a comparable entry/field as the format source;
- never use body text or body symbol occurrences as a reference-list format source;
- never use a bibliography occurrence as a body format source.

The bibliography interval begins at a recognized References/Bibliography heading and ends at the next recognized section heading. Common end sections (Appendix, Supplementary Information, Acknowledgements, Funding, Data Availability, Conflicts of Interest, etc.) are recognized as boundaries. If a document uses a nonstandard layout, the zone detection needs an explicit generalizable rule, not a paper-specific hardcode.

## Format-source selection

The model's role is limited to understanding the new content in context and selecting the source occurrence with the same semantic use. It must select an occurrence ID from the current profile; it must not invent font/italic/bold/subscript property values.

The selected source must:
- belong to the same document and exact source SHA;
- belong to the same zone (body or references) as the target;
- use the same paragraph style as the target in this initial implementation;
- have directly-applied format properties that the deterministic FormatChange adapter can express;
- have no unsupported direct-format properties.

If no comparable occurrence exists, if the profile is stale, if zone/style differs, or if the source contains unsupported formatting, do not borrow it. The task can continue without adding an ungrounded special-format instruction only when the target's own paragraph/run inheritance already provides the intended appearance. Otherwise it must be treated as unresolved by the revision planner; no fallback to a dominant Run or neighboring text is allowed.

The profile's semantic_role is an occurrence-level annotation slot. It is not inferred from the character itself. The first manuscript analysis records contextual meanings in the separate semantic-annotation artifact; the same glyph can have different roles at different occurrence IDs. Reference field roles are separate from body symbol roles. The source occurrence ID remains the durable link from the revision operation to the actual format evidence.

## Workflow operation contract

For a new-text replacement that needs special formatting, the execution-scope PatchOperation uses the existing two-pass writer:

- two_pass: true performs the authorized text replacement first, then formats only the newly inserted substring;
- format_authorization binds the intent, exact per-document semantic-profile fingerprint, and target zone;
- each format_operations item identifies the exact replacement substring, its offset within the full replacement, and source_occurrence_id;
- the model-supplied properties value is not authoritative; Workflow Preparation replaces it with the properties read from the cited occurrence. The semantic_profile_sha256 value is the cited document's per-document profile_fingerprint, not the aggregate workspace profile_fingerprint.

The writer records native Word Track Changes for both text and formatting. A pure FormatChange on existing text remains a separate explicit operation. Text changes and format changes are never silently collapsed into a whole-paragraph rewrite.

The automatic mixed-format replacement mapper must not use a dominant-format fallback. If source-to-replacement mapping is not unambiguous, do not flatten the replacement.

## Post-edit boundary

Do not add an LLM pass that re-judges the scientific meaning or formatting decision after Apply, and do not build repeated AI-QA/remediation loops. The content plan and profile-backed format source are the decision inputs.

Keep only deterministic execution-integrity checks needed for the established package lifecycle: exact target/write scope, native Track Changes structures, source immutability, readable DOCX package, hashes/transaction receipts and deterministic Marked-to-Clean derivation. These are not an AI review of the earlier decision.

The frozen architecture still retains the formal Patch/Commit path and the single permitted Phase 7 remediation contract unless separately changed by an explicit engineering decision. This design does not authorize editing Clean directly or bypassing the existing package transaction.

## Current implementation limits

- Profile extraction records direct Run properties and paragraph-style identity. It does not yet implement a complete recursive Word style-cascade resolver for every property.
- The initial profile-backed FormatChange source is restricted to the same paragraph style. Cross-style inheritance, table/cell style resolution, fields, hyperlinks, drawings, OMML and other unsupported structures continue to use the existing structure-specific support/refusal policy.
- Only properties supported by the deterministic FormatChange adapter can be borrowed. Unsupported direct Run properties are recorded and rejected as a source rather than silently discarded.
- Reference section discovery is rule-based from recognized section headings. Unusual bibliography placement needs a reusable detection enhancement.
- A passing unit/integration test is not Microsoft Word GUI acceptance. Do not claim Word save/reopen, visual rendering, or author acceptance without actually performing those actions.

## Minimal implementation files

- scripts/co_reviewer/semantic_profile.py: profile construction from an existing inventory.
- scripts/co_reviewer_cli.py: initial Run/paragraph format capture and profile-source-aware plan validation.
- scripts/review_audit.py: cache schema invalidation for the expanded inventory.
- scripts/Start-CoReviewerRound.ps1: one-pass profile creation and manifest binding.
- scripts/co_reviewer/workflow_preparation.py: resolve occurrence IDs to exact source format properties.
- scripts/co_reviewer/revision_models.py and schemas/*.schema.json: typed two-pass operation contract.
- scripts/co_reviewer/document_engine.py: deterministic native Track Changes FormatChange support for supported direct properties.
- scripts/co_reviewer/plan_compiler.py: no dominant-format fallback.
- SKILL.md: usage contract and model boundary.

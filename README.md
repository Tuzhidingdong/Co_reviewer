# Co_Reviewer

**A traceable multi-document academic peer-review revision toolkit.**

Co_Reviewer coordinates revisions across reviewer comments, the manuscript, and Supporting Information (SI). It turns each reviewer comment into a revision item, links that item to one or more document edits, and produces auditable Word files with native Track Changes.

## What it does

- Inspects DOCX comments, paragraphs, tables, captions/drawings, placeholders, media and existing tracked changes.
- Produces a compact Markdown digest and a full machine-readable paragraph/comment inventory; ordinary prose in a Reviewer Comments DOCX is not mistaken for Word-native comments.
- Represents reviewer comments as stable revision items (for example, R2-C3) with linked targets in multiple documents.
- Requires explicit human approval before applying edits; a SHA-256 fingerprint binds approval to the exact reviewer comment, proposed response, document targets and (when preflighted) source DOCX versions.
- Applies every planned text change as native Word insertions/deletions (`w:ins`/`w:del`) while preserving unaffected run formatting and package parts. Direct text rewrites are not permitted in a Marked deliverable.
- Converts supported Unicode subscript/superscript characters in replacement text (for example `SO₄²⁻`) into native Word `w:vertAlign` subscript/superscript runs; verification checks inserted text and preservation of native script formatting after a Word save round-trip.
- Supports explicitly targeted body, header and footer Word XML parts; paragraph indices are local to the selected part.
- Preserves hyperlink relationships when an edit is wholly contained in a single simple hyperlink; fails closed when a target crosses a hyperlink boundary or unsupported Word structure.
- Validates the output ZIP package and XML before writing the revised DOCX.
- Verifies applied insertions/deletions against the actual output files and renders a Response Letter draft without inventing replies.

## Quick start

Python 3.10+ is required. Install the DOCX/XML dependencies with: python -m pip install -r requirements.txt.

PowerShell:

    python scripts/co_reviewer_cli.py inspect --rev .\input\Reviewers.docx --man .\input\Manuscript.docx --si .\input\SI.docx --out workspace_digest.md --json-out workspace_inventory.json

Validate the draft plan and preflight every target before approval:

    python scripts/co_reviewer_cli.py validate-plan --plan edits_plan.json --allow-unapproved
    python scripts/co_reviewer_cli.py preflight-plan --plan edits_plan.json --out preflight_report.json

Preflight checks all planned anchors without writing DOCX files, records the SHA-256 of each exact input DOCX, and fingerprints the complete target set in the plan. Review the preflight report with the proposed edits before asking the user to approve selected Revision Item IDs. Then record that approval with `approve-plan`. Its fingerprint binds the reviewer comment, proposed response, linked targets and preflighted source versions. Changes to any of these require renewed user approval. The fingerprint detects workflow drift; it is not a cryptographic signature or user-identity proof. Then validate and apply only the sealed items:

    python scripts/co_reviewer_cli.py approve-plan --plan edits_plan.json --ids R2-C3 R3-C1
    python scripts/co_reviewer_cli.py validate-plan --plan edits_plan.json
    python scripts/co_reviewer_cli.py apply --plan edits_plan.json --author "Author Name" --report revision_apply_report.json

The apply command automatically persists successfully applied items as `applied` in the plan. Reuse the same report path for subsequent incremental iterations; each run is appended so the full revision history remains available. Verify the exact Word revision IDs and text:

    python scripts/co_reviewer_cli.py verify --plan edits_plan.json --apply-report revision_apply_report.json --out revision_verification.json

After checking the revised Manuscript and SI scientifically and semantically, confirm only fully reviewed items with the guarded command (do not edit status fields manually):

    python scripts/co_reviewer_cli.py confirm-verified --plan edits_plan.json --apply-report revision_apply_report.json --ids R2-C3 R3-C1 --semantic-review-confirmed

This re-runs exact Track Changes verification, seals the current output DOCX SHA-256 hashes and requires the explicit semantic-review acknowledgement. A later change to a revised DOCX invalidates its verified state.

Render the Response Letter draft in Markdown:

    python scripts/co_reviewer_cli.py render-response --plan edits_plan.json --out Response_to_Reviewers.md

Generate an editable Word Response Letter with `python scripts/co_reviewer_cli.py render-response-docx --plan edits_plan.json --out Response_to_Reviewers.docx`. Inspect progress with `python scripts/co_reviewer_cli.py status --plan edits_plan.json`.

See examples/revision_plan.example.json and docs/WORKFLOW.md.

## Safe revision engine: supported scope

- A target must occur exactly once in its selected paragraph.
- Ordinary paragraph and table-cell text can be edited when the target is contained in a contiguous sequence of supported plain-text runs. Header/footer edits must identify their exact `word/headerN.xml` or `word/footerN.xml` part and use that part's own paragraph index.
- Text wholly contained in one simple hyperlink can be edited inside the hyperlink wrapper while preserving its relationship ID.
- Existing run properties on text before, inside and after the target are retained where supported. Native sub/superscript run properties in unchanged text remain untouched.
- Targets crossing hyperlink boundaries, fields, drawings, bookmarks, content controls or other unsupported complex structures are refused rather than guessed.
- Input files are never overwritten; output paths must be separate.
- Untouched DOCX package members are checked byte-for-byte, and the package member inventory must remain unchanged.

This is a conservative initial compatibility boundary, not support for every possible WordprocessingML construct. Unsupported cases are reported and should be handled through a separately reviewed route.

## Supervisor–Worker model

Antigravity acts as Supervisor: it interprets reviewer intent, drafts revision plans, resolves cross-document logic and requests human approval. OpenCode Workers perform bounded inspection, deterministic edits and verification. The Supervisor reads concise reports and selected evidence rather than loading entire DOCX files.

Batched two-stage processing is the default efficiency strategy, not a hard limit on iterations. Local requests (for example, only revising Figure 4) should touch only the relevant targets.

## Project boundaries

Co_Reviewer manages peer-review revision orchestration and document consistency. It does not invent scientific results, experiments, citations or reviewer responses. Scientific claims and new data must come from user-provided evidence or explicitly verified sources.

## Current limitations

- DOCX only; legacy DOC and PDF are not editable targets.
- The current revision backend supports conservative plain-text-run replacements, table-cell paragraphs and text wholly contained in one simple hyperlink; it does not support arbitrary edits across complex Word structures.
- `render-response` writes Markdown; `render-response-docx` creates an editable Word draft using the built-in clean layout. Journal-specific templates are not yet applied automatically.
- Cross-document semantic consistency is reviewed by the Supervisor/author; the CLI verifies exact Word revision IDs and their text, but does not judge scientific meaning.
- No Word COM backend or graphical interface is included yet.

## Microsoft Word compatibility acceptance

The automated tests verify the generated WordprocessingML, but they cannot prove how Microsoft Word displays and re-saves Track Changes. A separate synthetic acceptance fixture is provided for that purpose. It uses no manuscript or SI files.

Run from the repository root in PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\run_word_compatibility_check.ps1
```

The runner creates a local test folder under `artifacts/word-compatibility-check`, performs preflight, approves the synthetic test item, applies five native Track Changes replacements, and verifies exact deletion/insertion text plus the absence of untracked text changes before Word is opened. The fixture covers two body paragraphs (including a scientific formula with subscript and superscript), one table cell, the header and the footer. Generated files are ignored by Git.

Open `word_compatibility_revised.docx` in Microsoft Word. On the Review tab, set Display for Review to **All Markup** and ensure insertions and deletions are shown. Inspect all five locations: each old value should appear as a deletion and each new value as an insertion; the formula should display 4 as subscript and 2− as superscript using native Word formatting. Color is not an acceptance criterion. Do not accept or reject any changes. Save the same DOCX, close Word, and reopen it.

After saving in Word, rerun mechanical verification from the repository root:

```powershell
python scripts/co_reviewer_cli.py verify --plan artifacts/word-compatibility-check/word_compatibility_plan.json --apply-report artifacts/word-compatibility-check/word_compatibility_apply_report.json --out artifacts/word-compatibility-check/word_compatibility_verification_after_word.json
```

A successful post-Word verification means all four exact revision IDs and their deleted/inserted text survived the Word save round-trip. It does not replace the visual inspection. Do not run `confirm-verified` on this synthetic fixture; semantic confirmation is intended for actual manuscript/SI revisions after scientific review.

## Tests

Run the complete local checks on Windows from PowerShell:

    .\scripts\run_quality_checks.ps1

To install the declared dependencies into the currently active Python/Conda environment first:

    .\scripts\run_quality_checks.ps1 -InstallDependencies

The script compiles source and tests, runs the unit suite and checks the key CLI entry points. It does not replace opening the generated DOCX files in Microsoft Word.

Individual commands:

    python -m unittest discover -s tests -v
    python scripts/co_reviewer_cli.py --help
    python scripts/co_reviewer_cli.py approve-plan --help
    python scripts/co_reviewer_cli.py confirm-verified --help

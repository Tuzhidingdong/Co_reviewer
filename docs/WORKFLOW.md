# Co_Reviewer workflow guide

## Revision round structure

Keep original inputs immutable. Use a dedicated folder per round:

    revision_round_01/
      input/
        Reviewers.docx
        Manuscript.docx
        SI.docx
      plan/
        edits_plan.json
      output/
        Manuscript_revised.docx
        SI_revised.docx
        revision_apply_report.json
      reports/
        workspace_digest.md
        workspace_inventory.json

## Revision item identity

Use stable IDs such as R1-C1, R2-C4 or EDITOR-C2. Do not renumber an item merely because another item is added. If a reviewer comment contains several independent requests, split it into sub-items while retaining the original comment text.

## Cross-document mapping

One revision item may have multiple targets. A response is not complete until each promised target has an output or a documented reason it is not applicable. Check numbers, units, figure labels, sample names and calculation settings across Manuscript and SI.

## Local and iterative requests

For a local request, include only affected revision items and targets. Do not rebuild the entire plan or re-inspect all files when source files and paragraph indices have not changed. If an earlier edit changes paragraph structure, re-inspect the affected file before preparing further index-based edits.

## Safe text matching

Use paragraph indices from the latest inventory. A target must be unique within its paragraph. Do not shorten the anchor to a generic phrase. If a replacement crosses a hyperlink, field, drawing, bookmark or content control, the current backend refuses it. Report the limitation; do not disable validation.

## Completion states

- planned: proposed by Supervisor;
- approved: explicitly approved by the user;
- applied: CLI reports the edit was applied;
- verified: output was re-inspected and checked;
- blocked: evidence is missing, the target is ambiguous, or the current backend cannot safely edit it.

The response letter must not claim "we have revised/added/corrected" for a task that is only planned or approved.


## Final verification and response draft

Run the mechanical check after applying the plan:

    python scripts/co_reviewer_cli.py verify --plan plan/edits_plan.json --apply-report output/revision_apply_report.json --out reports/revision_verification.json

This verifies the exact Word revision IDs recorded by the apply report and checks that those specific deletion/insertion elements contain the expected text. It does **not** decide whether the new scientific explanation is correct.

Next, inspect the revised Manuscript and SI. Check all claims in the response against the actual edits, and compare numerical values, units, sample names, figure/table labels and cross-references between the two documents. After a successful apply, first change the affected plan items from `approved` to `applied`. Only after mechanical verification and semantic review pass, change the relevant plan item's status to `verified`.

Render the response draft:

    python scripts/co_reviewer_cli.py render-response --plan plan/edits_plan.json --out output/Response_to_Reviewers.md

The renderer uses only the `proposed_response` text already provided in the plan. Missing reply text remains a placeholder. Items not marked `verified` are labelled as drafts and must not be presented as completed responses.

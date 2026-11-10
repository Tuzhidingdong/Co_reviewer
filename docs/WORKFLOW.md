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

Use paragraph indices from the latest inventory. A target must be unique within its paragraph. Do not shorten the anchor to a generic phrase. A replacement wholly contained within one simple hyperlink is supported and retains the hyperlink wrapper and relationship. A replacement crossing the hyperlink boundary, field, drawing, bookmark or content control is refused. Report the limitation; do not disable validation.

## Incremental approval and application

A revision plan may contain a mixture of `planned`, `approved`, `applied`, `verified` and `blocked` items. Set the plan-level `approved` flag to `true` only after the user has approved the plan. The apply command processes **only** items whose status is `approved`; planned or blocked items remain untouched. After successful application, the CLI updates those items to `applied` in the plan file automatically. This permits a later iteration to approve and apply only the remaining items without reapplying completed edits.

Use `status --plan plan/edits_plan.json` to inspect the current task summary. After mechanical verification and a separate scientific/semantic review, the Supervisor may mark the corresponding items `verified` in the plan.

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

Next, inspect the revised Manuscript and SI. Check all claims in the response against the actual edits, and compare numerical values, units, sample names, figure/table labels and cross-references between the two documents. The apply command persists the `applied` state automatically. Only after mechanical verification and semantic review pass, change the relevant plan item's status to `verified`.

Render the response draft:

    python scripts/co_reviewer_cli.py render-response --plan plan/edits_plan.json --out output/Response_to_Reviewers.md

The renderers use only the `proposed_response` text already provided in the plan. Missing reply text remains a placeholder. Items not marked `verified` are labelled as drafts and must not be presented as completed responses. Generate a Word file with `python scripts/co_reviewer_cli.py render-response-docx --plan plan/edits_plan.json --out output/Response_to_Reviewers.docx`; the existing Markdown renderer remains available.

## Incremental task handling

A plan can hold completed and unfinished items together. The plan-level `approved` flag confirms that the user has approved the current plan, while each item status records its individual lifecycle. `apply` processes only items marked `approved`, then automatically persists those items as `applied`. This prevents completed items from being submitted for a second edit when the user later approves a new subset.

Inspect progress with:

    python scripts/co_reviewer_cli.py status --plan edits_plan.json

Mechanical verification checks the exact Word revision records. The Supervisor must still perform the scientific and cross-document review before changing an item to `verified`.

## Response Letter DOCX

Generate an editable Word response letter from the revision plan:

    python scripts/co_reviewer_cli.py render-response-docx --plan edits_plan.json --out Response_to_Reviewers.docx

The DOCX output preserves draft labels for every item not marked `verified`, includes linked document locations, and does not invent missing reply text. Existing output files are not overwritten; choose a new path for each generated version.


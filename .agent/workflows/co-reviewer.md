---
description: Run the Co_Reviewer multi-document peer-review revision workflow
---

1. Inspect whichever Reviewer Comments, Manuscript and SI DOCX files are available. Omit missing inputs rather than passing fake paths.
2. Read the compact Markdown digest and JSON inventory. Identify reviewer concerns and supporting evidence/locations.
3. Build a revision plan using schemas/revision-plan.schema.json. Give each concern a stable ID and link it to every affected document target.
4. Present the original reviewer comment, proposed response, evidence basis, exact Manuscript/SI edits and unresolved questions. Obtain the user's approval for the selected items; do not apply planned or unapproved items.
5. After the user approves selected items, run `approve-plan --plan edits_plan.json --ids ...`. This seals the exact reviewer comment, proposed response and target list with a SHA-256 fingerprint. If any of those fields later change, validation must fail until the revised item is presented and explicitly re-approved.
6. Run validate-plan, then apply. The CLI applies only items marked approved and automatically persists successfully applied items as applied in the plan. Do not manually mark them applied.
7. Run verify --plan ... --apply-report ... to check the exact Word deletion/insertion revision IDs. Use status --plan ... to summarize all planned, approved, applied, verified and blocked items.
8. Re-inspect affected outputs and check scientific meaning and cross-document consistency (Manuscript/SI values, units, sample names, figure/table numbering and every response claim). Mechanical verification alone is not semantic verification.
9. Mark only fully checked items as verified. Generate the Response Letter in Markdown with render-response --plan ... or in Word with render-response-docx --plan ... --out Response_to_Reviewers.docx. The renderer must retain draft labels for items not marked verified and must never invent missing replies.
10. Support incremental follow-up: process only newly approved items, reuse inventories when inputs are unchanged, and use the previous revised DOCX as input for later edits to that same document. Reuse the same apply-report path; the CLI appends each iteration so the full revision history remains verifiable. Re-inspect affected files when paragraph structure changes.
11. Return revised files, Response Letter, verification report, task-state summary and unresolved issues. Never claim an edit is complete without checking the output file.

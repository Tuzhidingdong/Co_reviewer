---
description: Run the Co_Reviewer multi-document peer-review revision workflow
---

1. Inspect whichever Reviewer Comments, Manuscript and SI DOCX files are available. Omit missing inputs rather than passing fake paths.
2. Read the compact Markdown digest and JSON inventory. Identify reviewer concerns and supporting evidence/locations.
3. Build a revision plan using schemas/revision-plan.schema.json. Link each concern to all affected document targets.
4. Present proposed responses and exact Manuscript/SI edits for user approval. Do not apply unapproved changes.
5. After approval, set the plan approval fields, run validate-plan, then apply. When the CLI succeeds, update the affected revision items to `applied` before verification.
6. Run `verify --plan ... --apply-report ...` to check the actual Word deletions/insertions, then re-inspect the output files.
7. Check scientific meaning and cross-document consistency (Manuscript/SI values, units, sample names, figure/table numbering and every response claim). Mechanical verification alone is not semantic verification.
8. Mark only fully checked items as `verified`, then run `render-response --plan ...` to generate the Response Letter draft. Items not marked verified must remain labelled as drafts.
9. Return revised files, Response Letter, verification report and unresolved issues. Never claim an edit is complete without checking the output file.

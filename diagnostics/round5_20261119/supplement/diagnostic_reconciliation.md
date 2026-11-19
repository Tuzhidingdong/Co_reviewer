# Round 5 diagnostic reconciliation (redacted)

Base commit: `9f94b4da4b08222b7054f2591d5a5a073eb59f79`
Evidence branch: `evidence/round5-redacted-20261119`

## 1. Instruction-level

- Total instructions: 80.
- APPLIED: 6 — M-01, M-02, M-03, M-04, M-44, S-24.
- BLOCKED: 74.
- DEFERRED: 0.
- Final reason_code distribution (BLOCKED only): {'GATE_BLOCKED_PARAGRAPH': 67, 'NO_EXACT_TARGET': 4, 'UNSAFE_FIND_WINDOW': 3}.

Reason codes: `GATE_BLOCKED_PARAGRAPH` = target paragraph blocked by the official structural gate;
`NO_EXACT_TARGET` = no exact native anchor; `UNSAFE_FIND_WINDOW` = no DOCX-safe uniform-format find window.

## 2. Target-level

- The final `revision_mapping.json` contains 80 instruction rows; 74 are BLOCKED.
- Target records emitted for BLOCKED instructions: 74 ({'MANUSCRIPT': 45, 'SI': 29}).
- All 74 blocked targets have `preflight_status = NOT_SUBMITTED_NO_MATERIALIZED_TARGET`: they were rejected during plan construction and never entered `revision_plan.draft.json`, so no preflight result exists for them. Gate stage and reason are reconstructed from `skipped_summary.json` and the structure reports.
- Blocked targets by gate stage: {'preflight_structural_gate': 67, 'audit_target_matching': 4, 'plan_find_window_selection': 3}.
- Blocked targets by specific blocker: {'BOOKMARK_RANGE_GUARD': 66, 'NO_EXACT_TARGET': 4, 'UNSAFE_FIND_WINDOW': 3, 'EMBEDDED_OBJECT': 1}.

### Partial success within one instruction

Yes. Two instructions appear both in `skipped_summary.json` and in the APPLIED mapping:

- `M-44`: `skipped_summary.json` has one rejected find attempt (`UNSAFE_FIND_WINDOW`, the radial-symbol replacement). During plan repair a different, DOCX-safe anchor was found for the subject–verb correction; that single operation was included in the plan, preflighted, applied and verified. The rejected operation was dropped, not forced through. Final state: 1 applied target, 1 blocked/dropped attempt.
- `S-24`: `skipped_summary.json` has two rejected attempts (`(vO2, …)` and `(Dinst, …)` forms). During repair both were replaced by one short, safe unit anchor applied at two paragraphs (P47 and P52). Final state: 2 applied targets from 2 originally rejected attempts.

### Why skipped_summary has 77 rows but only 74 BLOCKED instructions

`skipped_summary.json` records rejected *find attempts* during plan construction, not final instruction outcomes. It has 77 rows:
- 3 rows belong to instructions that are nevertheless APPLIED after repair: M-44 (1 row) and S-24 (2 rows).
- The remaining 74 rows correspond exactly to the 74 BLOCKED instructions: 67 gate-blocked paragraph rows, 4 no-exact-target rows, and 3 unsafe-find rows (M-41, M-43, M-50).
- Therefore 77 - 3 = 74. The 4 no-exact-target rows (M-28, M-42, S-07, S-28) and 3 unsafe rows are final BLOCKED outcomes; the 3 repaired rows are not.

## 3. Bookmark arithmetic (computed from bookmark_analysis.json, not from FINAL_REPORT)

- Manuscript `protected_ranges = ['2', '9-29', '32', '35-45']`: unique paragraphs = 1 (`2`) + 21 (`9-29`) + 1 (`32`) + 11 (`35-45`) = **34**.
- SI `protected_ranges = ['0-43', '57-80']`: unique paragraphs = 44 (`0-43`) + 24 (`57-80`) = **68**.
- The round-5 FINAL_REPORT figures 36 and 78 were computed from a flattened list rather than the range endpoints and are inconsistent with the range notation; the correct unique counts are **34** and **68**.

Balance sheet:

| Role | total | bookmark-protected | editable after fix | other gates not opened | check |
|---|---|---|---|---|---|
| Manuscript | 81 | 34 | 45 | 2 (indices [8, 30]) | 81 |
| SI | 81 | 68 | 4 | 9 (indices [44, 46, 48, 49, 50, 51, 54, 55, 56]) | 81 |

## 4. Root-cause classes and fixability (target-level, not instruction-level)

Classes below are counted from `specific_blocker_code`, one primary class per blocked target.
Structural intersections can overlap (one paragraph may contain several structure kinds), so
intersection counts are not additive with primary-class counts.

| Class | targets | instructions | Manuscript / SI | existing support | generic adapter? | fail-closed condition to keep | minimal regression test |
|---|---|---|---|---|---|---|---|
| BOOKMARK_RANGE_GUARD | 66 | 66 | 40 / 26 | bookmark-range classification with redacted stable IDs; true paragraph interval | yes: bookmark-aware range adapter | valid paired ranges stay guarded; orphans/misordered groups stay blocked | paired body-level end: start paragraph, interior, end paragraph guarded; paragraph after end editable |
| PROOFERR_RUN_BOUNDARY | 0 | 0 | 0 / 0 | proofing markers are SUPPORTED run boundaries | no: find must stay inside one run block | misplaced proofing anchors and cross-boundary replacements blocked | proofErr between two runs: same-block find passes, cross-block find fails |
| MIXED_RUN_FORMAT | 0 | 0 | 0 / 0 | replacement_format_map requires per-character source offsets | no: explicit character map required | unmapped mixed-format replacement must fail closed | two formatted runs: complete contiguous map passes, partial map fails |
| EMBEDDED_OBJECT | 1 | 1 | 0 / 1 | drawing/pict/object inventoried as DEFERRED | no: object adapter out of scope | object-bearing paragraphs remain blocked | drawing in paragraph: edit inside plain run passes, edit crossing object fails |
| SECTION_STRUCTURE | 0 | 0 | 0 / 0 | sectPr/pgSz/pgMar/cols are non-body structures | no: section-aware adapter out of scope | section structures preserved byte-identical | sectPr in pPr: body edit allowed only when not intersecting section node |
| FORMAT_CHANGE | 0 | 0 | 0 / 0 | FormatChange is a separate pure PatchOperation | yes: existing FormatChange path | text and format must never be combined in one operation | find==replace with format_authorization passes; combined text+format fails |
| NO_EXACT_TARGET | 4 | 4 | 2 / 2 | audit reports target_status and candidate counts | no: requires evidence, not an adapter | no guessed target from token overlap | ambiguous anchor: no exact match -> unresolved, never auto-mapped |
| UNSAFE_FIND_WINDOW | 3 | 3 | 3 / 0 | supported-block and uniform-format checks | partly: shorter safe window or format map | no find across proofing/run/format boundary | cross-run find rejected; narrowed same-block find accepted |
| OTHER_GUARD | 0 | 0 | 0 / 0 | structure classifier reports kind/path/reason | case-by-case only | unknown OOXML elements/extensions remain blocked | unknown element: recorded as BLOCKED, no silent allow |

Overlap note (not additive): 66 blocked targets have bookmark intersections and 7 have embedded-object intersections; these describe the same paragraphs from different angles. Run-level format signatures, numeric XML order for bookmarks, and specific unsafe-find categories are marked `unavailable_in_existing_reports` because the existing reports do not record them; no inference was added.

Fail-closed conditions that must be preserved: valid bookmark ranges remain guarded; unknown OOXML extensions and elements remain blocked; drawing/section structures remain unsupported by the P0 text adapter; orphans and misordered markers remain part/range blocking. No recommendation here removes valid bookmark protection or relaxes these gates.

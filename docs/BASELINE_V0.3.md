# Co_Reviewer v0.3 Regression Baseline

## Baseline identity

- Repository: `Tuzhidingdong/Co_reviewer`
- Source branch: `upgrade/execution-integrity-v0.3`
- Source commit: `7b0fef325a5f8282a39259d39bb3cc0118e24a68`
- Implementation branch: `upgrade/v0.5.1-p0-implementation-20261114`
- Baseline date: 2026-11-14
- Purpose: preserve the existing v0.3 behavior while introducing the v0.5.1 component boundaries.

## Existing regression suite inventory

The four existing test modules contain 76 test methods. They are retained as compatibility tests; none is to be deleted merely to accommodate the refactor.

### `tests/test_decision_plan.py` — 13 tests

- `test_valid_pending_plan_passes_provenance_and_approval_checks`
- `test_unknown_source_comment_id_is_rejected`
- `test_excerpt_must_be_grounded_in_cited_inventory_location`
- `test_target_must_resolve_to_an_inventory_paragraph`
- `test_dependency_cycle_is_rejected`
- `test_decision_plan_cannot_claim_approval_or_applied_state`
- `test_full_round_plan_must_cover_every_comment`
- `test_dependency_must_appear_before_dependent_task`
- `test_full_round_plan_cannot_silently_omit_a_comment`
- `test_plan_must_preserve_audit_source_hashes`
- `test_native_comment_body_can_be_cited_without_a_resolved_selection`
- `test_schema_file_is_valid_json_and_enforces_pending_approval`
- `test_malformed_ai_fields_are_reported_without_crashing`

### `tests/test_review_audit.py` — 15 tests

- `test_native_comment_anchor_recovers_selected_text_across_paragraphs`
- `test_comment_reference_without_range_is_not_misreported_as_selected_text`
- `test_comment_anchor_matches_one_target_document_exactly`
- `test_same_anchor_in_two_targets_is_ambiguous_not_confirmed`
- `test_unresolved_native_anchor_uses_paragraph_context_for_candidates_only`
- `test_numbering_audit_finds_gaps_duplicates_and_unresolved_references`
- `test_decision_queue_can_be_limited_to_one_word_comment_id`
- `test_cache_reuses_inventory_until_source_bytes_change`
- `test_inspection_outputs_cannot_overwrite_source_docx`
- `test_audit_cli_writes_bounded_decision_queue_and_uses_cache_on_second_run`
- `test_narrative_figure_sentence_is_not_misclassified_as_caption`
- `test_caption_style_can_identify_caption_without_punctuation`
- `test_subpanel_and_abbreviated_number_ranges_resolve_to_workspace_captions`
- `test_duplicate_caption_classification_separates_internal_conflict_and_cross_document_copy`
- `test_workspace_caption_absence_is_distinguished_from_cross_document_reference`

### `tests/test_revision_engine.py` — 19 tests

- `test_replacement_preserves_run_format_and_package_members`
- `test_trailing_runs_are_preserved_when_target_ends_mid_run`
- `test_apply_report_verifies_edits_and_response_letter_marks_draft`
- `test_verifier_requires_the_exact_revision_id`
- `test_multiple_edits_in_one_paragraph_keep_unique_revision_ids`
- `test_deletion_only_revision_is_verified`
- `test_missing_target_fails_without_output`
- `test_ambiguous_target_in_paragraph_fails`
- `test_text_inside_hyperlink_preserves_relationship_and_track_changes`
- `test_hyperlink_boundary_is_rejected`
- `test_human_approval_is_required`
- `test_multi_document_plan_preflights_before_writing`
- `test_table_cell_text_is_revised_without_flattening_table_structure`
- `test_real_python_docx_package_remains_openable_after_tracked_revision`
- `test_one_revision_item_tracks_and_verifies_body_header_and_footer`
- `test_inspector_indexes_plain_reviewer_paragraphs`
- `test_unicode_script_insertions_use_native_word_format`
- `test_verifier_detects_direct_untracked_text_changes`
- `test_inspector_reports_native_word_script_runs`

### `tests/test_revision_workflow.py` — 29 tests

- `test_approve_plan_parser_is_registered`
- `test_confirm_verified_parser_is_registered`
- `test_manually_setting_verified_without_confirmation_fields_is_rejected`
- `test_confirm_verified_refuses_incomplete_apply_report`
- `test_confirm_verified_refuses_direct_untracked_text_changes`
- `test_confirmed_verification_is_invalidated_when_output_changes`
- `test_approve_plan_cli_seals_only_selected_items`
- `test_approve_plan_reseals_an_edited_pending_approval`
- `test_approve_plan_refuses_unpreflighted_items`
- `test_approve_plan_refuses_source_changed_since_preflight`
- `test_approved_item_fails_closed_after_its_scope_is_changed`
- `test_apply_refuses_report_path_that_would_overwrite_docx_output`
- `test_apply_refuses_report_path_that_conflicts_with_source_docx`
- `test_apply_refuses_plan_and_report_path_collision`
- `test_approve_plan_validation_error_uses_readable_line_breaks`
- `test_source_docx_fingerprint_must_be_a_lowercase_sha256`
- `test_preflight_cli_records_source_hashes_and_does_not_write_docx_outputs`
- `test_approve_plan_refuses_target_changes_after_preflight_until_repreflight`
- `test_approved_plan_rejects_source_docx_changed_after_preflight`
- `test_repreflight_of_changed_approved_source_requires_renewed_approval`
- `test_failed_preflight_does_not_record_partial_source_fingerprints`
- `test_apply_only_approved_items_and_preserve_pending_items`
- `test_apply_refuses_when_no_item_is_approved`
- `test_cli_apply_persists_item_state_and_leaves_other_items_pending`
- `test_incremental_iterations_preserve_docx_changes_and_apply_report_history`
- `test_response_docx_contains_verified_and_draft_sections`
- `test_response_docx_does_not_overwrite_existing_file`
- `test_response_docx_rejects_non_docx_output`
- `test_end_to_end_one_reviewer_item_updates_manuscript_and_si`

## Baseline validation

The repository CI workflow `.github/workflows/tests.yml` runs on Windows with Python 3.10 and 3.13. It compiles `scripts` and `tests`, runs the complete unittest suite and checks the documented CLI entry points.

This branch's first CI run is the authoritative automated baseline for this implementation branch. Record the run URL, Python matrix result and commit SHA in the implementation log before changing application code.

The tests do not prove Microsoft Word GUI rendering, Track Changes display, Word save-round-trip, COM automation, pagination or PDF line-number extraction. Those require separate synthetic integration acceptance. Do not describe a successful unit suite as Word compatibility acceptance.

## Compatibility policy

1. Preserve all 76 tests throughout P0-02 refactoring.
2. Add new tests alongside the old tests; do not weaken assertions to make a refactor pass.
3. Keep the existing CLI commands and their argument behavior compatible unless a separately versioned migration is explicitly tested.
4. The first modularization commits must be behavior-preserving extractions only. New v0.5.1 behavior is introduced after the seams are covered by tests.
5. If a legacy test exposes an accidental behavior that conflicts with the v0.5.1 contract, document the conflict and add a new explicit contract test before changing the old behavior.

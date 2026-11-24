# -*- coding: utf-8 -*-
"""Atomic, graph-checked reference transactions built from ordinary PatchOperations."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .document_engine import normalize_script_characters
from .revision_models import Patch, PatchOperation, RevisionModelError
from .v232_contract import ContractError, narrow_revision


class ReferenceTransactionError(ValueError):
    """Raised when a bibliography/citation transaction is incomplete or ambiguous."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceTransactionError(f"{field} must be a non-empty string.")
    return value


def _require_index(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReferenceTransactionError(f"{field} must be a non-negative paragraph index.")
    return value


def _source_hash(path: str) -> str:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.suffix.casefold() != ".docx":
        raise ReferenceTransactionError(f"Reference transaction source DOCX does not exist: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _graph_record_index(graph: Mapping[str, Any], key: str) -> dict[str, dict[str, Any]]:
    rows = graph.get(key)
    if not isinstance(rows, list):
        raise ReferenceTransactionError(f"Reference Graph {key} must be an array.")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            raise ReferenceTransactionError(f"Reference Graph {key} contains an invalid record.")
        record_id = row["id"]
        if record_id in result:
            raise ReferenceTransactionError(f"Reference Graph {key} contains duplicate ID {record_id}.")
        result[record_id] = dict(row)
    return result


def _exact_role_position(
    row: Mapping[str, Any],
    *,
    document: str,
    part: str,
    p_idx: int,
    label: str,
) -> None:
    expected = (document, part, p_idx)
    actual = (row.get("document"), row.get("part"), row.get("paragraph_index"))
    if actual != expected:
        raise ReferenceTransactionError(
            f"{label} location differs from Reference Graph: expected {expected}, found {actual}."
        )


def _validate_operation_token_authorization(
    action: str,
    find_text: str,
    replace_text: str,
    authorization: Mapping[str, Any],
) -> None:
    if not isinstance(authorization, Mapping) or not isinstance(authorization.get("intent"), str) or not authorization.get("intent", "").strip():
        raise ReferenceTransactionError("Every reference operation requires an explicit Authorized Change intent.")
    try:
        result = narrow_revision(
            "" if action == "insert_paragraph" else find_text,
            replace_text,
            authorized_change=authorization,
        )
    except (ContractError, TypeError, ValueError) as exc:
        raise ReferenceTransactionError(f"Reference operation token authorization is invalid: {exc}") from exc
    if result.get("status") != "PASS":
        conflicts = result.get("token_lock", {}).get("conflicts", [])
        raise ReferenceTransactionError(
            "Reference operation changes protected citation/number/DOI content without exact token authorization: "
            + json.dumps(conflicts, ensure_ascii=False)
        )


def compile_reference_transaction(
    spec: Mapping[str, Any],
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile additions, bibliography edits and every linked citation edit into one pending Patch.

    The output is an ordinary Revision Plan. It must still pass preflight, explicit
    author approval, Apply, Verify and the existing delivery gates; this function
    never writes a DOCX or marks the plan approved.
    """
    if not isinstance(spec, Mapping) or not isinstance(graph, Mapping):
        raise ReferenceTransactionError("Reference transaction spec and graph must be JSON objects.")
    if graph.get("schema_version") not in {"1.0", "1.1", "1.2"}:
        raise ReferenceTransactionError("Unsupported or missing Reference Graph schema_version.")
    transaction_id = _require_text(spec.get("transaction_id"), "transaction_id")
    reviewer = str(spec.get("reviewer") or "Author").strip() or "Author"
    comment = _require_text(spec.get("comment"), "comment")
    proposed_response = str(spec.get("proposed_response") or "")
    documents = spec.get("documents")
    if not isinstance(documents, Mapping) or not documents:
        raise ReferenceTransactionError("documents must map document roles to input_file/output_file paths.")

    normalized_documents: dict[str, dict[str, str]] = {}
    for role, raw in documents.items():
        if role not in {"Manuscript", "SI", "Reviewers", "Other"} or not isinstance(raw, Mapping):
            raise ReferenceTransactionError(f"Unsupported or invalid document registry entry: {role!r}.")
        input_file = _require_text(raw.get("input_file"), f"documents.{role}.input_file")
        output_file = _require_text(raw.get("output_file"), f"documents.{role}.output_file")
        if Path(input_file).expanduser().resolve() == Path(output_file).expanduser().resolve():
            raise ReferenceTransactionError(f"documents.{role} input and output paths must be different.")
        normalized_documents[str(role)] = {
            "input_file": str(Path(input_file).expanduser().resolve()),
            "output_file": str(Path(output_file).expanduser().resolve()),
            "source_sha256": _source_hash(input_file),
        }

    definitions = _graph_record_index(graph, "definitions")
    mentions = _graph_record_index(graph, "mentions")
    additions = spec.get("additions", [])
    entry_updates = spec.get("entry_updates", [])
    citation_updates = spec.get("citation_updates", [])
    for name, rows in (("additions", additions), ("entry_updates", entry_updates), ("citation_updates", citation_updates)):
        if not isinstance(rows, list):
            raise ReferenceTransactionError(f"{name} must be an array.")

    # Identify original labels that this transaction will release before
    # evaluating additions. This is provisional; complete linked-citation and
    # final-namespace validation still runs before a Patch is emitted.
    moving_definitions: dict[str, dict[str, str]] = {}
    seen_entry_update_ids: set[str] = set()
    for index, row in enumerate(entry_updates):
        if not isinstance(row, Mapping):
            raise ReferenceTransactionError(f"entry_updates[{index}] must be an object.")
        definition_id = _require_text(row.get("definition_id"), f"entry_updates[{index}].definition_id")
        if definition_id in seen_entry_update_ids:
            raise ReferenceTransactionError(f"Bibliography definition {definition_id} is updated more than once.")
        seen_entry_update_ids.add(definition_id)
        definition = definitions.get(definition_id)
        if definition is None or definition.get("kind") != "citation":
            raise ReferenceTransactionError(f"Entry update references an unknown or unsupported citation definition: {definition_id}.")
        old_number = str(definition.get("number"))
        new_number = str(row.get("new_number", old_number))
        if new_number != old_number and not new_number.isdigit():
            raise ReferenceTransactionError(f"Entry update {definition_id} new_number must be a numeric citation label.")
        if new_number != old_number and row.get("document") == definition.get("document"):
            moving_definitions[definition_id] = {
                "document": str(definition.get("document")),
                "old_number": old_number,
                "new_number": new_number,
            }

    scheduled_citation_update_ids = {
        row.get("mention_id")
        for row in citation_updates
        if isinstance(row, Mapping) and isinstance(row.get("mention_id"), str)
    }

    graph_sources = graph.get("source_files")
    if not isinstance(graph_sources, Mapping):
        raise ReferenceTransactionError(
            "Reference Graph is not source-hash-bound. Rebuild it with build-package-reference-graph before compiling a transaction."
        )
    for role, document in normalized_documents.items():
        graph_source = graph_sources.get(role)
        if not isinstance(graph_source, Mapping):
            raise ReferenceTransactionError(f"Reference Graph has no source-file record for document role {role}.")
        graph_path = Path(_require_text(graph_source.get("path"), f"graph.source_files.{role}.path")).expanduser().resolve()
        input_path = Path(document["input_file"]).expanduser().resolve()
        if graph_path != input_path:
            raise ReferenceTransactionError(
                f"Reference Graph source path for {role} differs from the transaction input DOCX."
            )
        if graph_source.get("sha256") != document["source_sha256"]:
            raise ReferenceTransactionError(
                f"Reference Graph source SHA-256 for {role} is stale; rebuild the graph from the exact transaction input."
            )

    # A new definition can only be linked to the exact unresolved static mentions
    # that already carry the same citation number in that document. Native fields,
    # grouped/ranged citations and ambiguous mentions remain manual-review items.
    addition_links: dict[str, set[str]] = {}
    for index, row in enumerate(additions):
        if not isinstance(row, Mapping):
            raise ReferenceTransactionError(f"additions[{index}] must be an object.")
        role = _require_text(row.get("document"), f"additions[{index}].document")
        if role not in normalized_documents:
            raise ReferenceTransactionError(f"additions[{index}] references unknown document role {role}.")
        number = _require_text(str(row.get("reference_number") or ""), f"additions[{index}].reference_number")
        if not number.isdigit():
            raise ReferenceTransactionError(
                f"additions[{index}].reference_number must be a numeric citation label."
            )
        collisions = [
            item for item in definitions.values()
            if item.get("kind") == "citation"
            and item.get("document") == role
            and str(item.get("number")) == number
        ]
        unreleased_collisions = [
            item for item in collisions
            if (
                item.get("id") not in moving_definitions
                or moving_definitions[item.get("id")]["document"] != role
                or moving_definitions[item.get("id")]["old_number"] != number
                or moving_definitions[item.get("id")]["new_number"] == number
            )
        ]
        if unreleased_collisions:
            raise ReferenceTransactionError(
                f"Cannot add citation definition {role}::{number}; the Reference Graph already contains "
                "that label and the conflicting definition is not moved away in this transaction: "
                + ", ".join(sorted(str(item.get("id")) for item in unreleased_collisions))
            )
        # Numeric citations in SI or another managed document may resolve to
        # the newly added package bibliography definition. Inventory matches
        # across the complete graph, not only in the bibliography-owning file.
        matching_mentions = {
            item_id: item for item_id, item in mentions.items()
            if item.get("kind") == "citation"
            and (
                str(item.get("number")) == number
                or number in [str(value) for value in item.get("numbers", [])]
            )
        }
        def _targets_only_released_definitions(mention: Mapping[str, Any]) -> bool:
            target_ids = mention.get("target_definition_ids") or []
            return bool(target_ids) and all(
                definition_id in moving_definitions
                and moving_definitions[definition_id]["old_number"] == number
                and moving_definitions[definition_id]["new_number"] != number
                for definition_id in target_ids
            )

        blocked_mentions = {
            item_id: item for item_id, item in matching_mentions.items()
            if item.get("resolution") in {"ambiguous", "manual_review_required"}
            or (
                item.get("resolution") in {"resolved", "confirmed_by_user"}
                and item.get("resolution_scope") == "package"
                and not _targets_only_released_definitions(item)
            )
            or item.get("group_kind") is not None
            or item.get("panel_range") is not None
            or item.get("reference_mechanism") in {"native_word_field", "footnote_reference"}
        }
        if blocked_mentions:
            raise ReferenceTransactionError(
                f"Cannot add {role}::{number} while same-number citation mentions are ambiguous, grouped, ranged or field-generated: "
                + ", ".join(sorted(blocked_mentions))
            )
        required_unresolved_mentions = {
            item_id for item_id, item in matching_mentions.items()
            if item.get("resolution") == "unresolved"
        }
        # A mention which currently resolves to a definition being moved is
        # not automatically a citation for the new entry. It may still mean
        # the old work and must then be updated along with its old definition.
        # Only exact IDs supplied by the author are rebound to an addition.
        eligible_rebindings = {
            item_id for item_id, item in matching_mentions.items()
            if (
                item_id not in scheduled_citation_update_ids
                and item.get("resolution") in {"resolved", "confirmed_by_user"}
                and _targets_only_released_definitions(item)
            )
        }
        linked_raw = row.get("linked_mention_ids", [])
        if not isinstance(linked_raw, list) or any(not isinstance(x, str) for x in linked_raw):
            raise ReferenceTransactionError(f"additions[{index}].linked_mention_ids must be a string array.")
        linked = set(linked_raw)
        invalid_links = linked - required_unresolved_mentions - eligible_rebindings
        missing_unresolved = required_unresolved_mentions - linked
        if missing_unresolved or invalid_links:
            raise ReferenceTransactionError(
                f"Addition {role}::{number} must link every unresolved static mention and may rebind "
                "only explicitly identified occurrences released from a moved definition. "
                f"Missing unresolved={sorted(missing_unresolved)}; invalid links={sorted(invalid_links)}."
            )
        previously_linked = set().union(*addition_links.values()) if addition_links else set()
        duplicate_links = linked & previously_linked
        if duplicate_links:
            raise ReferenceTransactionError(
                "A citation mention cannot be rebound to more than one added definition: "
                + ", ".join(sorted(duplicate_links))
            )
        addition_links[f"additions[{index}]"] = linked

    # Mentions explicitly reassigned to additions are not old-definition
    # occurrences that must be renumbered in the same transaction.
    reassigned_mention_ids = (
        set().union(*addition_links.values()) if addition_links else set()
    )

    citation_rows_by_mention: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(citation_updates):
        if not isinstance(row, Mapping):
            raise ReferenceTransactionError(f"citation_updates[{index}] must be an object.")
        mention_id = _require_text(row.get("mention_id"), f"citation_updates[{index}].mention_id")
        if mention_id not in mentions:
            raise ReferenceTransactionError(f"Unknown Reference Graph mention ID: {mention_id}.")
        if mention_id in citation_rows_by_mention:
            raise ReferenceTransactionError(f"Citation mention {mention_id} is updated more than once.")
        mention = mentions[mention_id]
        if mention.get("resolution") not in {"resolved", "confirmed_by_user"}:
            raise ReferenceTransactionError(
                f"Citation mention {mention_id} is {mention.get('resolution')}; resolve it explicitly before a transaction."
            )
        if mention.get("group_kind") or mention.get("panel_range"):
            raise ReferenceTransactionError(
                f"Citation mention {mention_id} is grouped or ranged and needs explicit per-occurrence decomposition."
            )
        if mention.get("reference_mechanism") in {"native_word_field", "footnote_reference"}:
            raise ReferenceTransactionError(
                f"Citation mention {mention_id} uses a native field/footnote adapter that is not enabled."
            )
        if row.get("find") != mention.get("text"):
            raise ReferenceTransactionError(
                f"Citation update {mention_id} find must exactly equal its graph-grounded mention text."
            )
        expected_number = str(row.get("new_number", ""))
        replacement_surface = row.get("replace")
        if (
            not expected_number.isdigit()
            or not isinstance(replacement_surface, str)
            or not replacement_surface
            or replacement_surface.count(expected_number) != 1
        ):
            raise ReferenceTransactionError(
                f"Citation update {mention_id} replace must contain its new numeric label exactly once; "
                "the exact punctuation/surface form must be supplied, not inferred."
            )
        _exact_role_position(
            mention,
            document=str(row.get("document") or ""),
            part=str(row.get("part") or "word/document.xml"),
            p_idx=row.get("p_idx"),
            label=f"Citation update {mention_id}",
        )
        same_text_same_paragraph = [
            other_id for other_id, other in mentions.items()
            if other_id != mention_id
            and other.get("document") == mention.get("document")
            and other.get("part") == mention.get("part")
            and other.get("paragraph_index") == mention.get("paragraph_index")
            and other.get("text") == mention.get("text")
            and other.get("kind") == "citation"
        ]
        if same_text_same_paragraph:
            raise ReferenceTransactionError(
                f"Citation update {mention_id} shares the same exact find text with another occurrence in its paragraph; "
                "the current text adapter cannot distinguish those occurrences safely: "
                + ", ".join(sorted(same_text_same_paragraph))
            )
        citation_rows_by_mention[mention_id] = row

    changed_definition_ids: set[str] = set()
    for index, row in enumerate(entry_updates):
        if not isinstance(row, Mapping):
            raise ReferenceTransactionError(f"entry_updates[{index}] must be an object.")
        definition_id = _require_text(row.get("definition_id"), f"entry_updates[{index}].definition_id")
        definition = definitions.get(definition_id)
        if definition is None:
            raise ReferenceTransactionError(f"Unknown Reference Graph definition ID: {definition_id}.")
        if definition.get("kind") != "citation":
            raise ReferenceTransactionError(
                f"Definition {definition_id} is kind={definition.get('kind')}; this transaction supports bibliography citations only."
            )
        role = _require_text(row.get("document"), f"entry_updates[{index}].document")
        part = str(row.get("part") or "word/document.xml")
        p_idx = _require_index(row.get("p_idx"), f"entry_updates[{index}].p_idx")
        _exact_role_position(definition, document=role, part=part, p_idx=p_idx, label=f"Entry update {definition_id}")
        find_text = _require_text(row.get("find"), f"entry_updates[{index}].find")
        replace_text = _require_text(row.get("replace"), f"entry_updates[{index}].replace")
        if str(definition.get("text") or definition.get("surface_text") or "") not in find_text:
            raise ReferenceTransactionError(
                f"Entry update {definition_id} find does not contain its graph-grounded citation label."
            )
        old_number = str(definition.get("number"))
        unresolved_related_mentions = [
            mention_id
            for mention_id, mention in mentions.items()
            if mention.get("kind") == "citation"
            and (
                str(mention.get("number")) == old_number
                or old_number in [str(value) for value in mention.get("numbers", [])]
            )
            and (
                definition_id in (mention.get("candidate_definition_ids") or [])
                or mention.get("document") == role
            )
            and (
                mention.get("resolution") not in {"resolved", "confirmed_by_user"}
                or mention.get("group_kind") is not None
                or mention.get("panel_range") is not None
                or mention.get("reference_mechanism") in {"native_word_field", "footnote_reference"}
            )
        ]
        if unresolved_related_mentions:
            raise ReferenceTransactionError(
                f"Definition {definition_id} has citation occurrences that the Reference Graph cannot map safely: "
                + ", ".join(sorted(unresolved_related_mentions))
            )
        new_number = str(row.get("new_number", old_number))
        if not new_number.isdigit():
            raise ReferenceTransactionError(
                f"Entry update {definition_id} new_number must be a numeric citation label."
            )
        if new_number != old_number:
            old_surface = str(definition.get("surface_text") or definition.get("text") or "")
            if old_number not in old_surface:
                raise ReferenceTransactionError(
                    f"Reference Graph definition {definition_id} has no exact old-number surface to relabel."
                )
            expected_surface = old_surface.replace(old_number, new_number, 1)
            if expected_surface not in replace_text:
                raise ReferenceTransactionError(
                    f"Entry update {definition_id} replacement does not contain the graph-style relabelled definition "
                    f"{expected_surface!r}."
                )
            linked_mentions = {
                mention_id for mention_id, mention in mentions.items()
                if definition_id in (mention.get("target_definition_ids") or [])
                and mention_id not in reassigned_mention_ids
            }
            supplied_mentions = {
                mention_id for mention_id, mention_row in citation_rows_by_mention.items()
                if definition_id in (mentions[mention_id].get("target_definition_ids") or [])
            }
            if linked_mentions != supplied_mentions:
                raise ReferenceTransactionError(
                    f"Renumbering definition {definition_id} must update every graph-linked citation occurrence. "
                    f"Missing={sorted(linked_mentions - supplied_mentions)}; extra={sorted(supplied_mentions - linked_mentions)}."
                )
            for mention_id in linked_mentions:
                mention = mentions[mention_id]
                if mention.get("resolution") not in {"resolved", "confirmed_by_user"} or mention.get("group_kind"):
                    raise ReferenceTransactionError(
                        f"Definition {definition_id} has a non-static or grouped linked mention {mention_id}; transaction blocked."
                    )
                mention_row = citation_rows_by_mention[mention_id]
                if str(mention_row.get("new_number", "")) != new_number:
                    raise ReferenceTransactionError(
                        f"Citation update {mention_id} does not declare new_number={new_number}."
                    )
            changed_definition_ids.add(definition_id)

    # Validate the entire post-transaction label namespace, not only each local
    # edit. Duplicate labels would make the output graph ambiguous even if every
    # individual PatchOperation passed its text and token checks.
    update_by_definition = {
        str(row.get("definition_id")): str(row.get("new_number"))
        for row in entry_updates
        if isinstance(row, Mapping)
    }
    final_labels: dict[tuple[str, str], list[str]] = {}
    for definition_id, definition in definitions.items():
        if definition.get("kind") != "citation":
            continue
        role = str(definition.get("document"))
        number = update_by_definition.get(definition_id, str(definition.get("number")))
        final_labels.setdefault((role, number), []).append(definition_id)
    for index, row in enumerate(additions):
        key = (str(row.get("document")), str(row.get("reference_number")))
        final_labels.setdefault(key, []).append(f"addition:{index}")
    collisions_after_transaction = {
        key: identifiers for key, identifiers in final_labels.items()
        if len(identifiers) > 1
    }
    if collisions_after_transaction:
        detail = "; ".join(
            f"{role}::{number} -> {', '.join(sorted(ids))}"
            for (role, number), ids in sorted(collisions_after_transaction.items())
        )
        raise ReferenceTransactionError(
            "Reference transaction would produce duplicate bibliography labels: " + detail
        )

    operations: list[PatchOperation] = []
    operation_ids: set[str] = set()

    def add_operation(
        raw: Mapping[str, Any],
        *,
        action: str,
        label: str,
        expected_reference_number: str | None = None,
    ) -> None:
        role = _require_text(raw.get("document"), f"{label}.document")
        if role not in normalized_documents:
            raise ReferenceTransactionError(f"{label} references unknown document role {role}.")
        doc = normalized_documents[role]
        part = str(raw.get("part") or "word/document.xml")
        p_idx = _require_index(raw.get("p_idx"), f"{label}.p_idx")
        operation_id = _require_text(raw.get("operation_id"), f"{label}.operation_id")
        if operation_id in operation_ids:
            raise ReferenceTransactionError(f"Duplicate reference transaction operation_id: {operation_id}.")
        operation_ids.add(operation_id)
        authorization = raw.get("authorized_change")
        if not isinstance(authorization, Mapping):
            raise ReferenceTransactionError(f"{label}.authorized_change must be an object with an explicit intent.")
        if action == "insert_paragraph":
            anchor = _require_text(raw.get("anchor_text"), f"{label}.anchor_text")
            inserted = _require_text(raw.get("inserted_text"), f"{label}.inserted_text")
            position = raw.get("position")
            if position not in {"before", "after"}:
                raise ReferenceTransactionError(f"{label}.position must be before or after.")
            find_text, replace_text = anchor, inserted
            if expected_reference_number is not None:
                stripped_inserted = inserted.lstrip()
                bracketed_label = f"[{expected_reference_number}]"
                numbered_labels = (
                    f"{expected_reference_number}.",
                    f"{expected_reference_number})",
                )
                bracketed_match = (
                    stripped_inserted.startswith(bracketed_label)
                    and len(stripped_inserted) > len(bracketed_label)
                    and stripped_inserted[len(bracketed_label)].isspace()
                )
                numbered_match = any(
                    stripped_inserted.startswith(label)
                    and len(stripped_inserted) > len(label)
                    and stripped_inserted[len(label)].isspace()
                    for label in numbered_labels
                )
                if bracketed_match == numbered_match:
                    raise ReferenceTransactionError(
                        f"{label}.inserted_text must start with exactly one supported bibliography label "
                        f"for reference_number={expected_reference_number!r} ([n], n. or n)); "
                        "additional or missing labels require a separately scoped operation."
                    )
            # The bracketed form is a protected citation token; the n. / n)
            # forms are protected number tokens. All other numbers/DOIs in the
            # inserted entry (e.g. publication year, volume, pages) remain
            # separately protected and must each appear in
            # explicitly_authorized_insertions with their exact kind and text.
            # Do not infer authorization for a bare number merely because it
            # matches reference_number.
            _validate_operation_token_authorization(action, find_text, replace_text, authorization)
        else:
            find_text = _require_text(raw.get("find"), f"{label}.find")
            replace_text = _require_text(raw.get("replace"), f"{label}.replace")
            _validate_operation_token_authorization(action, find_text, replace_text, authorization)
        source_comment_ids = raw.get("source_comment_ids", spec.get("source_comment_ids", []))
        if not isinstance(source_comment_ids, list) or not source_comment_ids or any(not isinstance(x, str) or not x.strip() for x in source_comment_ids):
            raise ReferenceTransactionError(f"{label}.source_comment_ids must retain exact reviewer comment IDs.")
        evidence_ids = raw.get("evidence_ids", spec.get("evidence_ids", []))
        if not isinstance(evidence_ids, list) or any(not isinstance(x, str) or not x.strip() for x in evidence_ids):
            raise ReferenceTransactionError(f"{label}.evidence_ids must be a string array.")
        target_ref = raw.get("target_ref")
        requested_identity_mode = raw.get("target_identity_mode")
        if target_ref is not None:
            if requested_identity_mode not in {None, "stable_target_ref"}:
                raise ReferenceTransactionError(
                    f"{label}.target_ref requires target_identity_mode=stable_target_ref."
                )
            target_identity_mode = "stable_target_ref"
        else:
            if requested_identity_mode != "legacy_p_idx_explicit":
                raise ReferenceTransactionError(
                    f"{label} must supply a registered TargetRef or explicitly declare "
                    "target_identity_mode=legacy_p_idx_explicit for migration-only paragraph-index compatibility."
                )
            target_identity_mode = "legacy_p_idx_explicit"
        data: dict[str, Any] = {
            "operation_id": operation_id,
            "document": role,
            "input_file": doc["input_file"],
            "output_file": doc["output_file"],
            "part": part,
            "find": find_text,
            "replace": replace_text,
            "p_idx": p_idx,
            "location": str(raw.get("location") or ""),
            "source_sha256": doc["source_sha256"],
            "evidence_ids": evidence_ids,
            "source_comment_ids": source_comment_ids,
            "target_identity_mode": target_identity_mode,
            "target_ref": copy.deepcopy(dict(target_ref)) if isinstance(target_ref, Mapping) else None,
            "authorized_change": copy.deepcopy(dict(authorization)),
        }
        replacement_format_map = raw.get("replacement_format_map")
        if replacement_format_map is not None:
            if action != "replace":
                raise ReferenceTransactionError(
                    f"{label}.replacement_format_map is valid only for replace operations."
                )
            if not isinstance(replacement_format_map, list) or not replacement_format_map:
                raise ReferenceTransactionError(
                    f"{label}.replacement_format_map must be a non-empty array."
                )
            data["replacement_format_map"] = copy.deepcopy(replacement_format_map)
        inserted_format_map = raw.get("inserted_format_map")
        if inserted_format_map is not None:
            if action != "insert_paragraph":
                raise ReferenceTransactionError(
                    f"{label}.inserted_format_map is valid only for insert_paragraph operations."
                )
            if not isinstance(inserted_format_map, list) or not inserted_format_map:
                raise ReferenceTransactionError(
                    f"{label}.inserted_format_map must be a non-empty array."
                )
            data["inserted_format_map"] = copy.deepcopy(inserted_format_map)
        if action == "insert_paragraph":
            data.update({
                "action": "insert_paragraph",
                "anchor_text": find_text,
                "position": position,
                "inserted_text": replace_text,
            })
        operation = PatchOperation.from_mapping(data)
        operations.append(operation)

    for index, row in enumerate(additions):
        raw = dict(row)
        role = _require_text(raw.get("document"), f"additions[{index}].document")
        role_documents = graph.get("documents", [])
        if role not in role_documents:
            raise ReferenceTransactionError(f"Reference Graph does not contain document role {role}.")
        part = str(raw.get("part") or "word/document.xml")
        p_idx = _require_index(raw.get("p_idx"), f"additions[{index}].p_idx")
        document_segments = graph.get("document_segments")
        if not isinstance(document_segments, Mapping):
            raise ReferenceTransactionError(
                "Reference Graph lacks document_segments; rebuild the graph before adding bibliography paragraphs."
            )
        role_segments = document_segments.get(role)
        if not isinstance(role_segments, list):
            raise ReferenceTransactionError(f"Reference Graph has no paragraph-segment inventory for {role}.")
        reference_anchor_rows = [
            segment for segment in role_segments
            if isinstance(segment, Mapping)
            and segment.get("part") == part
            and segment.get("paragraph_index") == p_idx
            and segment.get("in_reference_section") is True
        ]
        if len(reference_anchor_rows) != 1 or part != "word/document.xml":
            raise ReferenceTransactionError(
                f"Bibliography insertion {role}/{part}/P[{p_idx}] is not uniquely inside a detected reference section."
            )
        add_operation(
            raw,
            action="insert_paragraph",
            label=f"additions[{index}]",
            expected_reference_number=str(raw.get("reference_number")),
        )

    for index, row in enumerate(entry_updates):
        add_operation(row, action="replace", label=f"entry_updates[{index}]")

    # All citation update IDs must belong to a definition whose number is being
    # changed in this same atomic Patch; standalone/partial citation renumbering
    # is intentionally not permitted by this coordinator.
    for index, row in enumerate(citation_updates):
        mention_id = row["mention_id"]
        linked_changed = set(mentions[mention_id].get("target_definition_ids") or []) & changed_definition_ids
        if not linked_changed:
            raise ReferenceTransactionError(
                f"Citation update {mention_id} is not linked to a bibliography definition renumbered in this transaction."
            )
        add_operation(row, action="replace", label=f"citation_updates[{index}]")

    if not operations:
        raise ReferenceTransactionError("Reference transaction contains no executable operations.")
    patch = Patch(
        patch_id=transaction_id,
        task_id=transaction_id,
        reviewer=reviewer,
        comment=comment,
        proposed_response=proposed_response,
        operations=operations,
    )
    item = patch.to_legacy_revision_item()
    graph_digest = hashlib.sha256(
        json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    transaction_record = {
        "transaction_id": transaction_id,
        "reference_graph_sha256": graph_digest,
        "definition_ids_added": [
            {
                "document": row["document"],
                "number": str(row["reference_number"]),
                "linked_mention_ids": sorted(addition_links.get(f"additions[{index}]", set())),
            }
            for index, row in enumerate(additions)
        ],
        "definition_ids_updated": sorted(changed_definition_ids),
        "citation_mention_ids_updated": sorted(citation_rows_by_mention),
        "operation_ids": [operation.operation_id for operation in operations],
        "documents": copy.deepcopy(normalized_documents),
        "added_definition_expectations": [
            {
                "document": row["document"],
                "number": str(row["reference_number"]),
                "operation_id": row["operation_id"],
                "part": row.get("part", "word/document.xml"),
                "anchor_p_idx": row["p_idx"],
                "position": row["position"],
                "inserted_text": row["inserted_text"],
                "visible_inserted_text": normalize_script_characters(row["inserted_text"]),
                "inserted_format_map": copy.deepcopy(row.get("inserted_format_map")),
                "linked_mention_ids": sorted(addition_links.get(f"additions[{index}]", set())),
                "linked_mention_count": len(addition_links.get(f"additions[{index}]", set())),
            }
            for index, row in enumerate(additions)
        ],
        "updated_definition_expectations": [
            {
                "document": row["document"],
                "operation_id": row["operation_id"],
                "part": row.get("part", "word/document.xml"),
                "source_p_idx": row["p_idx"],
                "find_text": row["find"],
                "old_number": str(definitions[row["definition_id"]].get("number")),
                "new_number": str(row.get("new_number", definitions[row["definition_id"]].get("number"))),
                "updated_entry_text": row["replace"],
            }
            for row in entry_updates
        ],
        "updated_mention_expectations": [
            {
                "document": row["document"],
                "operation_id": row["operation_id"],
                "part": row.get("part", "word/document.xml"),
                "source_p_idx": row["p_idx"],
                "source_offset": mentions[row["mention_id"]].get("offset"),
                "find_text": row["find"],
                "replace_text": row["replace"],
                "old_number": str(mentions[row["mention_id"]].get("number")),
                "new_number": str(row["new_number"]),
                "mention_id": row["mention_id"],
            }
            for row in citation_updates
        ],
        "status": "pending_author_approval",
    }
    transaction_fingerprint = hashlib.sha256(
        json.dumps(
            transaction_record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    transaction_record["transaction_fingerprint"] = transaction_fingerprint
    item["reference_transaction_sha256"] = transaction_fingerprint
    return {
        "schema_version": "1.0",
        "approved": False,
        "revision_items": [item],
        "reference_transaction": transaction_record,
    }


def verify_reference_transaction_outputs(
    plan: Mapping[str, Any],
    apply_reports: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rebuild the package Reference Graph from output DOCX files and verify the transaction."""
    transaction = plan.get("reference_transaction")
    if not isinstance(transaction, Mapping):
        return {"passed": True, "checked": False, "errors": []}
    try:
        from .v232_contract import build_package_reference_graph, extract_docx_reference_segments

        document_registry = transaction.get("documents")
        if not isinstance(document_registry, Mapping) or not document_registry:
            raise ReferenceTransactionError("Reference transaction has no document path registry.")
        reports_by_role: dict[str, list[Mapping[str, Any]]] = {}
        for report in apply_reports:
            if not isinstance(report, Mapping):
                continue
            roles_in_report = {
                row.get("document")
                for row in report.get("results", [])
                if isinstance(row, Mapping) and isinstance(row.get("document"), str)
            }
            for role in roles_in_report:
                reports_by_role.setdefault(role, []).append(report)
        extracted_documents: dict[str, Any] = {}
        selected_paths: dict[str, str] = {}
        for role, paths in document_registry.items():
            if not isinstance(paths, Mapping):
                raise ReferenceTransactionError(f"Reference transaction path entry for {role} is invalid.")
            input_path = Path(_require_text(paths.get("input_file"), f"transaction.documents.{role}.input_file")).expanduser().resolve()
            output_path = Path(_require_text(paths.get("output_file"), f"transaction.documents.{role}.output_file")).expanduser().resolve()
            expected_source_sha = _require_text(paths.get("source_sha256"), f"transaction.documents.{role}.source_sha256")
            if not input_path.is_file() or _source_hash(str(input_path)) != expected_source_sha:
                raise ReferenceTransactionError(
                    f"Reference transaction input DOCX for {role} no longer matches its graph-bound source SHA-256."
                )
            role_reports = reports_by_role.get(str(role), [])
            if role_reports:
                if len(role_reports) != 1:
                    raise ReferenceTransactionError(
                        f"Reference transaction role {role} maps to {len(role_reports)} Apply reports; expected exactly one."
                    )
                report = role_reports[0]
                reported_input = Path(_require_text(report.get("input_file"), f"Apply report {role}.input_file")).expanduser().resolve()
                reported_output = Path(_require_text(report.get("output_file"), f"Apply report {role}.output_file")).expanduser().resolve()
                if reported_input != input_path or reported_output != output_path:
                    raise ReferenceTransactionError(
                        f"Apply report paths for {role} differ from its graph-bound transaction paths."
                    )
                if report.get("input_sha256") != expected_source_sha:
                    raise ReferenceTransactionError(
                        f"Apply report input SHA-256 for {role} differs from the graph-bound transaction source."
                    )
                if not output_path.is_file() or report.get("output_sha256") != _source_hash(str(output_path)):
                    raise ReferenceTransactionError(
                        f"Applied output DOCX for {role} is missing or its SHA-256 differs from the Apply report."
                    )
                candidate = str(output_path)
            else:
                candidate = str(input_path)
            selected_paths[str(role)] = candidate
            extracted_documents[str(role)] = extract_docx_reference_segments(candidate)
        output_graph = build_package_reference_graph(extracted_documents)
        errors: list[str] = []
        applied_operations = [
            row
            for report in apply_reports
            for row in report.get("results", [])
            if isinstance(row, Mapping) and isinstance(row.get("operation_id"), str)
        ]
        segments_by_document = {
            role: {
                (row.get("part"), row.get("paragraph_index")): row
                for row in segments
                if isinstance(row, Mapping)
            }
            for role, segments in extracted_documents.items()
        }

        for expected in transaction.get("added_definition_expectations", []):
            role, number = expected.get("document"), str(expected.get("number"))
            definitions_found = [
                row for row in output_graph["definitions"]
                if row.get("document") == role and row.get("kind") == "citation"
                and str(row.get("number")) == number
            ]
            if len(definitions_found) != 1:
                errors.append(
                    f"Added bibliography definition {role}::{number} resolves to {len(definitions_found)} output definitions."
                )
                continue
            operation_id = expected.get("operation_id")
            operation_results = [
                row for row in applied_operations
                if row.get("operation_id") == operation_id
            ]
            if len(operation_results) != 1:
                errors.append(
                    f"Bibliography insertion operation {operation_id} has {len(operation_results)} Apply results; expected exactly one."
                )
                continue
            operation_result = operation_results[0]
            inserted_p_idx = operation_result.get("inserted_p_idx")
            inserted_part = operation_result.get("part", "word/document.xml")
            if operation_result.get("inserted_format_map") != expected.get("inserted_format_map"):
                errors.append(
                    f"Bibliography insertion operation {operation_id} inserted_format_map differs from the approved transaction."
                )
            if (
                operation_result.get("action") != "insert_paragraph"
                or operation_result.get("status") != "applied"
                or not isinstance(inserted_p_idx, int)
                or isinstance(inserted_p_idx, bool)
                or inserted_p_idx < 0
            ):
                errors.append(
                    f"Bibliography insertion operation {operation_id} lacks a verified inserted paragraph location."
                )
                continue
            definition = definitions_found[0]
            paragraph_segment = segments_by_document.get(role, {}).get((inserted_part, inserted_p_idx))
            if (
                definition.get("part") != inserted_part
                or definition.get("paragraph_index") != inserted_p_idx
                or not isinstance(paragraph_segment, Mapping)
                or paragraph_segment.get("text") != expected.get("visible_inserted_text", expected.get("inserted_text"))
            ):
                errors.append(
                    f"Added bibliography definition {role}::{number} is not at the exact inserted paragraph "
                    f"reported by operation {operation_id}."
                )
                continue
            expected_mention_ids = expected.get("linked_mention_ids", [])
            output_mentions_by_id = {
                row.get("id"): row for row in output_graph["mentions"]
                if isinstance(row, Mapping) and isinstance(row.get("id"), str)
            }
            unresolved_links = []
            for mention_id in expected_mention_ids:
                mention = output_mentions_by_id.get(mention_id)
                if (
                    mention is None
                    or mention.get("resolution") not in {"resolved", "confirmed_by_user"}
                    or definitions_found[0]["id"] not in (mention.get("target_definition_ids") or [])
                ):
                    unresolved_links.append(mention_id)
            if (
                len(expected_mention_ids) != expected.get("linked_mention_count", 0)
                or unresolved_links
            ):
                errors.append(
                    f"Added definition {role}::{number} did not resolve the exact expected mention IDs: "
                    + ", ".join(unresolved_links or ["expected mention ID/count mismatch"])
                )

        def _operation_result_for(expected: Mapping[str, Any], label: str) -> Mapping[str, Any] | None:
            operation_id = expected.get("operation_id")
            matches = [
                row for row in applied_operations
                if row.get("operation_id") == operation_id
            ]
            if len(matches) != 1:
                errors.append(
                    f"{label} operation {operation_id} has {len(matches)} Apply results; expected exactly one."
                )
                return None
            result = matches[0]
            expected_role = expected.get("document")
            expected_part = expected.get("part", "word/document.xml")
            source_p_idx = expected.get("source_p_idx")
            if (
                result.get("status") != "applied"
                or result.get("action", "replace") != "replace"
                or result.get("document") != expected_role
                or result.get("part", "word/document.xml") != expected_part
                or result.get("p_idx") != source_p_idx
                or result.get("find") != expected.get("find_text")
                or result.get("replace") != expected.get("updated_entry_text", expected.get("replace_text"))
            ):
                errors.append(
                    f"{label} operation {operation_id} Apply result does not match its approved source paragraph/find/replace scope."
                )
                return None
            return result

        insertion_results_by_role: dict[str, list[Mapping[str, Any]]] = {}
        for row in applied_operations:
            if row.get("action") == "insert_paragraph":
                insertion_results_by_role.setdefault(str(row.get("document")), []).append(row)

        def _output_paragraph_index(role: str, source_p_idx: Any) -> int:
            if not isinstance(source_p_idx, int) or isinstance(source_p_idx, bool) or source_p_idx < 0:
                raise ReferenceTransactionError(
                    f"Reference update for {role} has an invalid source paragraph index."
                )
            mapped = source_p_idx
            insertions = sorted(
                insertion_results_by_role.get(role, []),
                key=lambda row: int(row.get("inserted_p_idx", -1)),
            )
            for insertion in insertions:
                inserted_index = insertion.get("inserted_p_idx")
                if (
                    insertion.get("status") != "applied"
                    or not isinstance(inserted_index, int)
                    or isinstance(inserted_index, bool)
                    or inserted_index < 0
                ):
                    raise ReferenceTransactionError(
                        f"Paragraph insertion {insertion.get('operation_id')} lacks a final output index."
                    )
                if inserted_index <= mapped:
                    mapped += 1
            return mapped

        for expected in transaction.get("updated_definition_expectations", []):
            role = str(expected.get("document"))
            part = str(expected.get("part") or "word/document.xml")
            new_number = str(expected.get("new_number"))
            operation_result = _operation_result_for(expected, "Bibliography update")
            if operation_result is None:
                continue
            output_p_idx = _output_paragraph_index(role, expected.get("source_p_idx"))
            paragraph_segment = segments_by_document.get(role, {}).get((part, output_p_idx))
            if (
                not isinstance(paragraph_segment, Mapping)
                or paragraph_segment.get("text") != expected.get("updated_entry_text")
            ):
                errors.append(
                    f"Bibliography update {expected.get('operation_id')} did not produce its exact approved entry text "
                    f"at {role}/{part}/P[{output_p_idx}]."
                )
                continue
            definitions_found = [
                row for row in output_graph["definitions"]
                if row.get("document") == role
                and row.get("kind") == "citation"
                and str(row.get("number")) == new_number
                and row.get("part") == part
                and row.get("paragraph_index") == output_p_idx
            ]
            if len(definitions_found) != 1:
                errors.append(
                    f"Updated bibliography definition {role}::{new_number} has {len(definitions_found)} "
                    f"definitions at the exact updated entry paragraph; expected exactly one."
                )

        mention_expectations = transaction.get("updated_mention_expectations", [])
        for expected in mention_expectations:
            role = str(expected.get("document"))
            part = str(expected.get("part") or "word/document.xml")
            new_number = str(expected.get("new_number"))
            delegation_matches = []
            for prose_item in plan.get("revision_items", []):
                if not isinstance(prose_item, Mapping):
                    continue
                for prose_target_index, prose_target in enumerate(prose_item.get("targets", [])):
                    if not isinstance(prose_target, Mapping):
                        continue
                    for delegation in prose_target.get("reference_citation_delegations", []):
                        if (
                            isinstance(delegation, Mapping)
                            and delegation.get("citation_operation_id") == expected.get("operation_id")
                        ):
                            delegation_matches.append((
                                prose_item, prose_target_index, prose_target, delegation
                            ))
            if len(delegation_matches) > 1:
                errors.append(
                    f"In-text citation update {expected.get('operation_id')} has multiple delegated prose owners; expected exactly one."
                )
                continue

            delegated_owner = delegation_matches[0] if delegation_matches else None
            if delegated_owner is None:
                operation_result = _operation_result_for(expected, "In-text citation update")
                if operation_result is None:
                    continue
            else:
                duplicate_citation_results = [
                    row for row in applied_operations
                    if row.get("operation_id") == expected.get("operation_id")
                ]
                if duplicate_citation_results:
                    errors.append(
                        f"Delegated citation operation {expected.get('operation_id')} was also applied independently; duplicate Track Changes ownership is forbidden."
                    )
                    continue
                prose_item, prose_target_index, prose_target, delegation = delegated_owner
                if (
                    delegation.get("prose_revision_item_id") != prose_item.get("id")
                    or delegation.get("prose_target_index") != prose_target_index
                    or delegation.get("source_find") != prose_target.get("find")
                    or delegation.get("source_replace") != prose_target.get("replace")
                    or delegation.get("citation_find") != expected.get("find_text")
                    or delegation.get("citation_replace") != expected.get("replace_text")
                    or prose_target.get("document") != role
                    or prose_target.get("part", "word/document.xml") != part
                    or prose_target.get("p_idx") != expected.get("source_p_idx")
                ):
                    errors.append(
                        f"Delegated citation operation {expected.get('operation_id')} no longer matches its approved prose owner."
                    )
                    continue
                owner_operation_id = prose_target.get("operation_id")
                owner_results = [
                    row
                    for row in applied_operations
                    if row.get("status") == "applied"
                    and row.get("revision_item_id") == prose_item.get("id")
                    and row.get("document") == role
                    and row.get("part", "word/document.xml") == part
                    and row.get("p_idx") == expected.get("source_p_idx")
                    and row.get("find") == prose_target.get("find")
                    and row.get("replace") == prose_target.get("replace")
                    and row.get("action", "replace") == "replace"
                    and (
                        not isinstance(owner_operation_id, str)
                        or row.get("operation_id") == owner_operation_id
                    )
                ]
                if len(owner_results) != 1:
                    errors.append(
                        f"Delegated citation operation {expected.get('operation_id')} has {len(owner_results)} applied prose-owner results; expected exactly one."
                    )
                    continue
                operation_result = owner_results[0]

            output_p_idx = _output_paragraph_index(role, expected.get("source_p_idx"))
            source_offset = expected.get("source_offset")
            find_text = expected.get("find_text")
            replace_text = expected.get("replace_text")
            if (
                not isinstance(source_offset, int)
                or isinstance(source_offset, bool)
                or source_offset < 0
                or not isinstance(find_text, str)
                or not find_text
                or not isinstance(replace_text, str)
                or not replace_text
            ):
                errors.append(
                    f"In-text citation update {expected.get('operation_id')} lacks an exact graph offset/find/replace contract."
                )
                continue
            if delegated_owner is not None:
                _prose_item, _prose_index, prose_target, _delegation = delegated_owner
                paragraph_segment = segments_by_document.get(role, {}).get((part, output_p_idx))
                paragraph_text = paragraph_segment.get("text") if isinstance(paragraph_segment, Mapping) else None
                prose_replace = prose_target.get("replace")
                if (
                    not isinstance(paragraph_text, str)
                    or not isinstance(prose_replace, str)
                    or not prose_replace
                    or paragraph_text.count(prose_replace) != 1
                    or len(_exact_token_positions(prose_replace, replace_text)) != 1
                ):
                    errors.append(
                        f"Delegated citation operation {expected.get('operation_id')} cannot be mapped to one exact output prose replacement and citation occurrence."
                    )
                    continue
                output_offset = paragraph_text.find(prose_replace) + prose_replace.find(replace_text)
            else:
                output_offset = source_offset
            for earlier in mention_expectations:
                if delegated_owner is not None:
                    break
                if (
                    earlier.get("document") == role
                    and earlier.get("part", "word/document.xml") == part
                    and earlier.get("source_p_idx") == expected.get("source_p_idx")
                    and isinstance(earlier.get("source_offset"), int)
                    and earlier["source_offset"] < source_offset
                ):
                    earlier_find = earlier.get("find_text")
                    earlier_replace = earlier.get("replace_text")
                    if isinstance(earlier_find, str) and isinstance(earlier_replace, str):
                        output_offset += len(earlier_replace) - len(earlier_find)

            # A separately authorized prose target may have been narrowed by
            # Preflight to the exact changed prefix before this graph-linked
            # citation token. That prefix replacement shifts the output token
            # offset even though it is not itself a citation update. Reapply
            # the recorded source-relative delta here so Reference Graph
            # verification checks the true output occurrence, not the stale
            # pre-edit offset.
            for revision_item in plan.get("revision_items", []):
                if delegated_owner is not None:
                    break
                if not isinstance(revision_item, Mapping):
                    continue
                for prose_target in revision_item.get("targets", []):
                    if not isinstance(prose_target, Mapping):
                        continue
                    normalization = prose_target.get("reference_citation_normalization")
                    if not isinstance(normalization, Mapping):
                        continue
                    prose_target_p_idx = prose_target.get("p_idx")
                    if not isinstance(prose_target_p_idx, int) or isinstance(prose_target_p_idx, bool):
                        target_ref = prose_target.get("target_ref")
                        location_hint = target_ref.get("location_hint") if isinstance(target_ref, Mapping) else None
                        candidate = location_hint.get("p_idx") if isinstance(location_hint, Mapping) else None
                        prose_target_p_idx = candidate if isinstance(candidate, int) and not isinstance(candidate, bool) else None
                    if (
                        prose_target.get("document") != role
                        or prose_target.get("part", "word/document.xml") != part
                        or prose_target_p_idx != expected.get("source_p_idx")
                        or normalization.get("citation_operation_id") != expected.get("operation_id")
                        or normalization.get("citation_find") != find_text
                    ):
                        continue
                    if normalization.get("mode") != "prefix_before_citation":
                        continue

                    source_find = normalization.get("source_find")
                    normalized_find = normalization.get("normalized_find")
                    normalized_replace = normalization.get("normalized_replace")
                    if not all(isinstance(value, str) and value for value in (
                        source_find, normalized_find, normalized_replace
                    )):
                        errors.append(
                            f"Prose normalization for citation update {expected.get('operation_id')} "
                            "has incomplete offset provenance."
                        )
                        continue
                    if source_find.count(find_text) != 1:
                        errors.append(
                            f"Prose normalization for citation update {expected.get('operation_id')} "
                            "does not contain one exact source citation token."
                        )
                        continue
                    relative_citation_offset = source_find.find(find_text)
                    source_target_start = source_offset - relative_citation_offset
                    if source_target_start < 0:
                        errors.append(
                            f"Prose normalization for citation update {expected.get('operation_id')} "
                            "projects before the beginning of its source paragraph."
                        )
                        continue

                    normalized_find = prose_target.get("find")
                    normalized_replace = prose_target.get("replace")
                    if (
                        normalized_find != normalization.get("normalized_find")
                        or normalized_replace != normalization.get("normalized_replace")
                    ):
                        errors.append(
                            f"Prose normalization for citation update {expected.get('operation_id')} "
                            "differs from the approved normalized target."
                        )
                        continue
                    prose_reports = [
                        row
                        for report in apply_reports
                        for row in report.get("results", [])
                        if isinstance(row, Mapping)
                        and row.get("status") == "applied"
                        and row.get("revision_item_id") == revision_item.get("id")
                        and row.get("document") == role
                        and row.get("part", "word/document.xml") == part
                        and row.get("p_idx") == expected.get("source_p_idx")
                        and row.get("find") == normalized_find
                        and row.get("replace") == normalized_replace
                        and row.get("action", "replace") == "replace"
                    ]
                    if len(prose_reports) != 1:
                        errors.append(
                            f"Prose normalization for citation update {expected.get('operation_id')} "
                            f"has {len(prose_reports)} matching Apply results; expected exactly one."
                        )
                        continue
                    output_offset += len(normalized_replace) - len(normalized_find)
            paragraph_segment = segments_by_document.get(role, {}).get((part, output_p_idx))
            if (
                not isinstance(paragraph_segment, Mapping)
                or paragraph_segment.get("text", "")[
                    output_offset:output_offset + len(replace_text)
                ] != replace_text
            ):
                errors.append(
                    f"In-text citation update {expected.get('operation_id')} did not produce its exact replacement "
                    f"at {role}/{part}/P[{output_p_idx}] offset {output_offset}."
                )
                continue
            graph_mentions = [
                row for row in output_graph["mentions"]
                if row.get("document") == role
                and row.get("kind") == "citation"
                and row.get("part") == part
                and row.get("paragraph_index") == output_p_idx
                and row.get("offset") == output_offset
                and str(row.get("number")) == new_number
                and row.get("resolution") in {"resolved", "confirmed_by_user"}
            ]
            if len(graph_mentions) != 1:
                errors.append(
                    f"In-text citation update {expected.get('operation_id')} resolves to {len(graph_mentions)} "
                    f"Reference Graph mentions at its exact output occurrence; expected exactly one."
                )

        graph_digest = hashlib.sha256(
            json.dumps(output_graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return {
            "passed": not errors,
            "checked": True,
            "errors": errors,
            "output_reference_graph_sha256": graph_digest,
            "definition_count": len(output_graph["definitions"]),
            "mention_count": len(output_graph["mentions"]),
            "unresolved_count": output_graph["unresolved_count"],
            "documents": selected_paths,
        }
    except (OSError, KeyError, TypeError, ValueError, ContractError) as exc:
        return {"passed": False, "checked": True, "errors": [str(exc)]}

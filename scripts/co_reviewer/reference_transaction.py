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
        collisions = [
            item for item in definitions.values()
            if item.get("kind") == "citation"
            and item.get("document") == role
            and str(item.get("number")) == number
        ]
        if collisions:
            raise ReferenceTransactionError(
                f"Cannot add citation definition {role}::{number}; the Reference Graph already contains that label."
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
        blocked_mentions = {
            item_id: item for item_id, item in matching_mentions.items()
            if item.get("resolution") in {"ambiguous", "manual_review_required"}
            or (
                item.get("resolution") in {"resolved", "confirmed_by_user"}
                and item.get("resolution_scope") == "package"
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
        candidates = {
            item_id for item_id, item in matching_mentions.items()
            if item.get("resolution") == "unresolved"
        }
        linked_raw = row.get("linked_mention_ids", [])
        if not isinstance(linked_raw, list) or any(not isinstance(x, str) for x in linked_raw):
            raise ReferenceTransactionError(f"additions[{index}].linked_mention_ids must be a string array.")
        linked = set(linked_raw)
        if linked != candidates:
            raise ReferenceTransactionError(
                f"Addition {role}::{number} must explicitly link every unresolved static mention of that number. "
                f"Missing={sorted(candidates - linked)}; extra={sorted(linked - candidates)}."
            )
        addition_links[f"additions[{index}]"] = linked

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
        if not expected_number.isdigit() or row.get("replace") != expected_number:
            raise ReferenceTransactionError(
                f"Citation update {mention_id} replace must be the exact new numeric citation label."
            )
        _exact_role_position(
            mention,
            document=str(row.get("document") or ""),
            part=str(row.get("part") or "word/document.xml"),
            p_idx=row.get("p_idx"),
            label=f"Citation update {mention_id}",
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

    def add_operation(raw: Mapping[str, Any], *, action: str, label: str) -> None:
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
        target_identity_mode = "stable_target_ref" if target_ref is not None else "legacy_p_idx_explicit"
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
        add_operation(raw, action="insert_paragraph", label=f"additions[{index}]")

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
    return {
        "schema_version": "1.0",
        "approved": False,
        "revision_items": [item],
        "reference_transaction": {
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
                    "linked_mention_ids": sorted(addition_links.get(f"additions[{index}]", set())),
                    "linked_mention_count": len(addition_links.get(f"additions[{index}]", set())),
                }
                for index, row in enumerate(additions)
            ],
            "updated_definition_expectations": [
                {
                    "document": row["document"],
                    "old_number": str(definitions[row["definition_id"]].get("number")),
                    "new_number": str(row.get("new_number", definitions[row["definition_id"]].get("number"))),
                    "updated_entry_text": row["replace"],
                }
                for row in entry_updates
            ],
            "updated_mention_expectations": [
                {
                    "document": row["document"],
                    "old_number": str(mentions[row["mention_id"]].get("number")),
                    "new_number": str(row["new_number"]),
                    "mention_id": row["mention_id"],
                }
                for row in citation_updates
            ],
            "status": "pending_author_approval",
        },
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

        for expected in transaction.get("updated_definition_expectations", []):
            role = expected.get("document")
            new_number = str(expected.get("new_number"))
            definitions_found = [
                row for row in output_graph["definitions"]
                if row.get("document") == role and row.get("kind") == "citation"
                and str(row.get("number")) == new_number
            ]
            if len(definitions_found) != 1:
                errors.append(
                    f"Updated bibliography definition {role}::{new_number} resolves to {len(definitions_found)} output definitions."
                )

        expected_mentions_by_key: dict[tuple[str, str], int] = {}
        for expected in transaction.get("updated_mention_expectations", []):
            key = (str(expected.get("document")), str(expected.get("new_number")))
            expected_mentions_by_key[key] = expected_mentions_by_key.get(key, 0) + 1
        for (role, number), count in expected_mentions_by_key.items():
            actual = [
                row for row in output_graph["mentions"]
                if row.get("document") == role and row.get("kind") == "citation"
                and str(row.get("number")) == number
                and row.get("resolution") in {"resolved", "confirmed_by_user"}
            ]
            if len(actual) < count:
                errors.append(
                    f"Output Reference Graph has {len(actual)} resolved mention(s) for {role}::{number}; "
                    f"at least {count} were expected after the transaction."
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

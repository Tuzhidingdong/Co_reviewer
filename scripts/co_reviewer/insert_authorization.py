# -*- coding: utf-8 -*-
"""Instruction-backed authorization records for paragraph insertions.

Protected tokens found in inserted text are NOT authorization evidence.  The
only authorization source is the original instruction snapshot and its
atomic Coverage requirement.  This module builds and validates the minimal
link between them; it never accepts a bare token list as approval.

V2 adds two fail-closed checks:
  * the cited excerpt/locator is rebuilt from the original instruction file;
  * the requirement must carry an explicit affirmative INSERT action/polarity.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from .v232_contract import extract_immutable_tokens

PROVENANCE_FIELD = "instruction_requirement_id"
SOURCE_FIELD = "authorization_source"
AFFIRMATIVE_ACTIONS = frozenset({"insert", "add", "insert_paragraph"})
AFFIRMATIVE_POLARITY = frozenset({"affirmative", "positive", "accepted"})
NEGATIVE_POLARITY = frozenset({
    "negative", "prohibited", "rejected", "conditional", "not_adopted", "unknown",
})
_LOCATOR_RE = re.compile(r"^L(\d+)[–\-]L(\d+)")


def _source_instruction(coverage: Mapping[str, Any]) -> dict[str, str] | None:
    source = coverage.get("source_instruction") if isinstance(coverage, Mapping) else None
    if not isinstance(source, Mapping):
        return None
    file_value = source.get("file")
    sha_value = source.get("sha256")
    if not isinstance(file_value, str) or not isinstance(sha_value, str):
        return None
    return {"file": file_value, "sha256": sha_value}


def _lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        return None


def _locator_bounds(requirement: Mapping[str, Any]) -> tuple[int, int] | None:
    start = requirement.get("source_line_start")
    end = requirement.get("source_line_end")
    if (isinstance(start, int) and not isinstance(start, bool)
            and isinstance(end, int) and not isinstance(end, bool)
            and start > 0 and end >= start):
        return start, end
    locator = requirement.get("source_locator")
    if isinstance(locator, str):
        match = _LOCATOR_RE.match(locator.strip())
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def validate_instruction_source(
    coverage: Mapping[str, Any], requirement: Mapping[str, Any] | None = None
) -> list[str]:
    """Verify the immutable instruction snapshot and re-derive its cited excerpt."""
    errors: list[str] = []
    source = _source_instruction(coverage)
    if source is None:
        return ["INSERT_AUTHORIZATION_SOURCE_MISSING: instruction_coverage.source_instruction is absent or malformed"]
    path = Path(source["file"]).expanduser()
    if not path.is_absolute():
        errors.append("INSERT_AUTHORIZATION_SOURCE_NOT_ABSOLUTE: source_instruction.file must be absolute")
    if not path.is_file():
        errors.append(f"INSERT_AUTHORIZATION_SOURCE_MISSING: {path}")
        return errors
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != source["sha256"]:
        errors.append(
            "INSERT_AUTHORIZATION_SOURCE_SHA_MISMATCH: "
            f"expected {source['sha256']}, observed {actual}"
        )
    if requirement is None:
        return errors
    bounds = _locator_bounds(requirement)
    if bounds is None:
        errors.append("INSERT_AUTHORIZATION_LOCATOR_INVALID: requirement has no usable source span")
        return errors
    lines = _lines(path)
    if lines is None:
        errors.append(f"INSERT_AUTHORIZATION_SOURCE_UNREADABLE: {path}")
        return errors
    start, end = bounds
    if end > len(lines):
        errors.append(
            f"INSERT_AUTHORIZATION_LOCATOR_OUT_OF_RANGE: L{start}-L{end} exceeds {len(lines)} lines"
        )
        return errors
    rebuilt = "\n".join(lines[start - 1:end])
    cited = requirement.get("instruction_excerpt")
    # Normalization contract: line endings are normalized by splitlines(), and
    # trailing blank lines at the cited span boundary are ignored on both sides.
    if not isinstance(cited, str) or cited.rstrip("\n") != rebuilt.rstrip("\n"):
        errors.append(
            f"INSERT_AUTHORIZATION_EXCERPT_SOURCE_MISMATCH: cited excerpt differs from "
            f"original instruction lines L{start}-L{end}"
        )
    return errors


def _requirement(
    coverage: Mapping[str, Any], requirement_id: str, item_id: str, target_index: int
) -> dict[str, Any] | None:
    if not isinstance(coverage, Mapping):
        return None
    for row in coverage.get("requirements", []) if isinstance(coverage.get("requirements"), list) else []:
        if not isinstance(row, Mapping) or row.get("requirement_id") != requirement_id:
            continue
        if row.get("status") != "planned":
            continue
        refs = row.get("revision_targets", []) if isinstance(row.get("revision_targets"), list) else []
        linked = any(
            isinstance(ref, Mapping)
            and ref.get("revision_item_id") == item_id
            and ref.get("target_index") == target_index
            for ref in refs
        )
        if linked:
            return dict(row)
    return None


def validate_instruction_action(requirement: Mapping[str, Any]) -> list[str]:
    """Only an explicit, affirmative INSERT requirement can authorize an INSERT."""
    errors: list[str] = []
    action = str(requirement.get("instruction_action", "")).strip().lower()
    polarity = str(requirement.get("instruction_polarity", "")).strip().lower()
    if action not in AFFIRMATIVE_ACTIONS:
        errors.append(
            f"INSERT_AUTHORIZATION_ACTION_NOT_INSERT: instruction_action={action!r}"
        )
    if polarity in NEGATIVE_POLARITY:
        errors.append(
            f"INSERT_AUTHORIZATION_NEGATIVE_POLARITY: instruction_polarity={polarity!r}"
        )
    elif polarity not in AFFIRMATIVE_POLARITY:
        errors.append(
            "INSERT_AUTHORIZATION_POLARITY_UNKNOWN: requirement must declare an explicit "
            "affirmative insertion state"
        )
    return errors


def _source_record(
    coverage: Mapping[str, Any], requirement: Mapping[str, Any], excerpt: str
) -> dict[str, Any]:
    source = _source_instruction(coverage) or {}
    return {
        "source_file": source.get("file", ""),
        "source_sha256": source.get("sha256", ""),
        "requirement_id": str(requirement.get("requirement_id", "")),
        "source_locator": str(requirement.get("source_locator", "")),
        "instruction_excerpt": excerpt,
        "instruction_action": str(requirement.get("instruction_action", "")),
        "instruction_polarity": str(requirement.get("instruction_polarity", "")),
        "instruction_object": str(requirement.get("instruction_object", "")),
    }


def _rebuilt_excerpt(
    coverage: Mapping[str, Any], requirement: Mapping[str, Any]
) -> str:
    source = _source_instruction(coverage) or {}
    path = Path(source.get("file", "")).expanduser()
    bounds = _locator_bounds(requirement) or (0, 0)
    lines = _lines(path) or []
    start, end = bounds
    return "\n".join(lines[start - 1:end]) if start and end <= len(lines) else ""


def _token_rows(inserted_text: str) -> list[dict[str, str]]:
    return [
        {"kind": str(row["kind"]), "text": str(row["text"])}
        for row in extract_immutable_tokens(inserted_text)
    ]


def _instruction_authorizes_insert(
    requirement: Mapping[str, Any], inserted_text: str, excerpt: str
) -> bool:
    """Direct text, or a uniquely identified object named by the instruction."""
    if inserted_text in excerpt:
        return True
    obj = str(requirement.get("instruction_object", "")).strip()
    return (
        bool(obj)
        and inserted_text.count(obj) == 1
        and excerpt.count(obj) == 1
    )


def build_insert_authorization(
    *,
    item_id: str,
    target_index: int,
    inserted_text: str,
    instruction_coverage: Mapping[str, Any],
    requirement_id: str,
) -> dict[str, Any]:
    """Build an authorization record grounded in the original instruction."""
    requirement = _requirement(instruction_coverage, requirement_id, item_id, target_index)
    errors = validate_instruction_source(instruction_coverage, requirement)
    if requirement is None:
        errors.append(
            f"INSERT_AUTHORIZATION_REQUIREMENT_UNLINKED: {requirement_id} is not a planned "
            f"requirement linked to {item_id}[{target_index}]"
        )
    else:
        errors.extend(validate_instruction_action(requirement))
        excerpt = _rebuilt_excerpt(instruction_coverage, requirement)
        if not _instruction_authorizes_insert(requirement, inserted_text, excerpt):
            errors.append(
                f"INSERT_AUTHORIZATION_NOT_IN_INSTRUCTION: {item_id} inserted text or its "
                f"declared object is not traceable to {requirement_id}"
            )
    if errors:
        raise ValueError("Insert authorization blocked:\n- " + "\n- ".join(errors))
    assert requirement is not None
    source = _source_record(
        instruction_coverage, requirement, _rebuilt_excerpt(instruction_coverage, requirement)
    )
    rows = _token_rows(inserted_text)
    return {
        "intent": f"{item_id}: insert authorized by {requirement_id}",
        SOURCE_FIELD: source,
        "explicitly_authorized_insertions": [
            {**row, PROVENANCE_FIELD: requirement_id} for row in rows
        ],
    }


def validate_insert_authorization(
    *,
    item_id: str,
    target_index: int,
    inserted_text: str,
    authorized_change: Mapping[str, Any],
    instruction_coverage: Mapping[str, Any] | None = None,
) -> list[str]:
    """Validate an INSERT authorization against text, source and requirement."""
    errors: list[str] = []
    source = authorized_change.get(SOURCE_FIELD)
    if not isinstance(source, Mapping):
        return ["INSERT_AUTHORIZATION_SOURCE_FIELD_MISSING: no instruction-backed source record"]
    requirement_id = str(source.get("requirement_id", ""))
    requirement: Mapping[str, Any] | None = None
    if instruction_coverage is not None:
        requirement = _requirement(instruction_coverage, requirement_id, item_id, target_index)
        errors.extend(validate_instruction_source(instruction_coverage, requirement))
        if requirement is None:
            errors.append(
                f"INSERT_AUTHORIZATION_REQUIREMENT_UNLINKED: {requirement_id} is not a planned "
                f"requirement linked to {item_id}[{target_index}]"
            )
        else:
            expected_source = _source_record(
                instruction_coverage, requirement,
                _rebuilt_excerpt(instruction_coverage, requirement),
            )
            for key, value in expected_source.items():
                if source.get(key) != value:
                    errors.append(
                        f"INSERT_AUTHORIZATION_SOURCE_MISMATCH: {key} differs from Coverage source"
                    )
            errors.extend(validate_instruction_action(requirement))
    else:
        # Ledger/Workflow boundary: the embedded source record is a declaration;
        # re-read the original file and rebuild the excerpt from its locator.
        declaration = {
            "source_instruction": {
                "file": source.get("source_file"),
                "sha256": source.get("source_sha256"),
            },
            "requirements": [dict(source, requirement_id=requirement_id, status="planned")],
        }
        errors.extend(validate_instruction_source(declaration, declaration["requirements"][0]))
        errors.extend(validate_instruction_action(declaration["requirements"][0]))
    if errors:
        return errors
    excerpt = str(source.get("instruction_excerpt", ""))
    declaration_req = requirement or dict(source, requirement_id=requirement_id)
    if not _instruction_authorizes_insert(declaration_req, inserted_text, excerpt):
        errors.append(
            f"INSERT_AUTHORIZATION_NOT_IN_INSTRUCTION: {item_id} inserted text or its "
            f"declared object is not traceable to {requirement_id}"
        )
    expected = _token_rows(inserted_text)
    raw = authorized_change.get("explicitly_authorized_insertions", [])
    if not isinstance(raw, list):
        return errors + ["explicitly_authorized_insertions must be an array"]
    remaining: list[dict[str, str]] = []
    for index, row in enumerate(raw):
        if not isinstance(row, Mapping):
            errors.append(f"explicitly_authorized_insertions[{index}] must be an object")
            continue
        if row.get(PROVENANCE_FIELD) != requirement_id:
            errors.append(
                f"INSERT_AUTHORIZATION_TOKEN_UNLINKED: entry[{index}] lacks the exact "
                f"instruction requirement {requirement_id}"
            )
        remaining.append({"kind": str(row.get("kind", "")), "text": str(row.get("text", ""))})
    if sorted((r["kind"], r["text"]) for r in expected) != sorted(
        (r["kind"], r["text"]) for r in remaining
    ):
        errors.append("INSERT_AUTHORIZATION_TOKEN_SET_MISMATCH: authorized tokens differ from inserted text")
    return errors


def validate_workflow_insert_authorizations(workflow: Mapping[str, Any]) -> list[str]:
    """Shared boundary check for Workflow Ledger patch operations."""
    errors: list[str] = []
    tasks = workflow.get("tasks", []) if isinstance(workflow, Mapping) else []
    for task in tasks if isinstance(tasks, list) else []:
        if not isinstance(task, Mapping):
            continue
        operations = task.get("patch_operations", [])
        for index, operation in enumerate(operations if isinstance(operations, list) else []):
            if not isinstance(operation, Mapping) or operation.get("action") != "insert_paragraph":
                continue
            auth = operation.get("authorized_change")
            if not isinstance(auth, Mapping):
                errors.append(f"{task.get('task_id')}[{index}]: INSERT_AUTHORIZATION_SOURCE_FIELD_MISSING")
                continue
            for error in validate_insert_authorization(
                item_id=str(task.get("task_id", "")), target_index=index,
                inserted_text=str(operation.get("inserted_text", "")),
                authorized_change=auth,
            ):
                errors.append(f"{task.get('task_id')}[{index}]: {error}")
    return errors


def validate_plan_insert_authorizations(
    plan: Mapping[str, Any], *, instruction_coverage: Mapping[str, Any] | None = None
) -> list[str]:
    """Shared boundary check for every Plan that can be delivered for execution."""
    errors: list[str] = []
    items = plan.get("revision_items", []) if isinstance(plan, Mapping) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        for index, target in enumerate(item.get("targets", []) if isinstance(item.get("targets"), list) else []):
            if not isinstance(target, Mapping) or target.get("action") != "insert_paragraph":
                continue
            auth = target.get("authorized_change")
            if not isinstance(auth, Mapping):
                errors.append(f"{item.get('id')}[{index}]: INSERT_AUTHORIZATION_SOURCE_FIELD_MISSING")
                continue
            for error in validate_insert_authorization(
                item_id=str(item.get("id", "")), target_index=index,
                inserted_text=str(target.get("inserted_text", "")),
                authorized_change=auth, instruction_coverage=instruction_coverage,
            ):
                errors.append(f"{item.get('id')}[{index}]: {error}")
    return errors

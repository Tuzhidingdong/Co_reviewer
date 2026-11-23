from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class InstructionCoverageError(ValueError):
    """Raised when source-instruction coverage cannot be proven."""


def _canonical_sha256(payload: Mapping[str, Any], *, omit: str) -> str:
    data = {key: value for key, value in payload.items() if key != omit}
    encoded = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def instruction_coverage_sha256(coverage: Mapping[str, Any]) -> str:
    return _canonical_sha256(coverage, omit="coverage_sha256")


def instruction_coverage_approval_sha256(approval: Mapping[str, Any]) -> str:
    return _canonical_sha256(approval, omit="approval_sha256")


def _validate_manifest(coverage: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(coverage, Mapping):
        return ["Instruction coverage manifest is missing or is not a JSON object."]
    if coverage.get("schema_version") != "1.0":
        errors.append("Instruction coverage schema_version must be '1.0'.")
    source = coverage.get("source_instruction")
    if not isinstance(source, Mapping):
        errors.append("source_instruction must contain the original instruction file and SHA-256.")
    else:
        source_file = source.get("file")
        source_sha = source.get("sha256")
        if not isinstance(source_file, str) or not source_file.strip():
            errors.append("source_instruction.file must be a non-empty path.")
        if (
            not isinstance(source_sha, str)
            or len(source_sha) != 64
            or any(character not in "0123456789abcdef" for character in source_sha)
        ):
            errors.append("source_instruction.sha256 must be a lowercase SHA-256.")
        if isinstance(source_file, str) and source_file.strip() and not Path(source_file).expanduser().is_absolute():
            errors.append("source_instruction.file must be an absolute path so Final Gate resolves the exact instruction snapshot.")
    requirements = coverage.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements must be a non-empty array of atomic source requirements.")
        return errors
    seen: set[str] = set()
    allowed = {"planned", "no_change", "manual_completed", "not_applicable", "blocked", "deferred"}
    for index, row in enumerate(requirements):
        label = f"requirements[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{label} must be an object.")
            continue
        requirement_id = row.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id.strip():
            errors.append(f"{label}.requirement_id must be non-empty.")
        elif requirement_id in seen:
            errors.append(f"Duplicate requirement_id: {requirement_id}.")
        else:
            seen.add(requirement_id)
        for field in ("source_locator", "instruction_excerpt"):
            if not isinstance(row.get(field), str) or not row.get(field, "").strip():
                errors.append(f"{label}.{field} must preserve a traceable source instruction.")
        status = row.get("status")
        if status not in allowed:
            errors.append(f"{label}.status must be one of: {', '.join(sorted(allowed))}.")
        if status == "planned":
            refs = row.get("revision_targets")
            if not isinstance(refs, list) or not refs:
                errors.append(f"{label}.revision_targets must identify exact compiled plan targets.")
            else:
                seen_refs: set[tuple[str, int]] = set()
                for ref_index, target_ref in enumerate(refs):
                    if not isinstance(target_ref, Mapping):
                        errors.append(f"{label}.revision_targets[{ref_index}] must be an object.")
                        continue
                    item_id = target_ref.get("revision_item_id")
                    target_index = target_ref.get("target_index")
                    if not isinstance(item_id, str) or not item_id.strip():
                        errors.append(f"{label}.revision_targets[{ref_index}].revision_item_id must be non-empty.")
                    if not isinstance(target_index, int) or isinstance(target_index, bool) or target_index < 0:
                        errors.append(f"{label}.revision_targets[{ref_index}].target_index must be a non-negative integer.")
                    elif isinstance(item_id, str) and item_id.strip():
                        pair = (item_id, target_index)
                        if pair in seen_refs:
                            errors.append(f"{label}.revision_targets contains duplicate target reference {pair!r}.")
                        seen_refs.add(pair)
        elif status == "no_change":
            if not isinstance(row.get("evidence"), str) or not row.get("evidence", "").strip():
                errors.append(f"{label} no_change requires current-document evidence.")
        elif status == "manual_completed":
            if (
                not isinstance(row.get("evidence_file"), str) or not row.get("evidence_file").strip()
                or not isinstance(row.get("evidence_sha256"), str)
                or len(row.get("evidence_sha256", "")) != 64
                or any(character not in "0123456789abcdef" for character in row.get("evidence_sha256", ""))
            ):
                errors.append(f"{label} manual_completed requires an evidence file and lowercase SHA-256.")
        elif status == "not_applicable":
            if not isinstance(row.get("evidence"), str) or not row.get("evidence", "").strip():
                errors.append(f"{label} not_applicable requires explicit evidence.")
        elif status in {"blocked", "deferred"}:
            if not isinstance(row.get("reason"), str) or not row.get("reason", "").strip():
                errors.append(f"{label} {status} requires a specific reason.")
    declared_sha = coverage.get("coverage_sha256")
    if (
        not isinstance(declared_sha, str)
        or len(declared_sha) != 64
        or declared_sha != instruction_coverage_sha256(coverage)
    ):
        errors.append("coverage_sha256 does not match the exact instruction coverage manifest.")
    return errors


def authorize_instruction_coverage(
    coverage: Mapping[str, Any],
    approved_coverage_sha256: str,
    approved_by: str,
    *,
    approved_at_utc: str | None = None,
) -> dict[str, Any]:
    """Bind the author's approval to one exact, source-hash-bound coverage manifest."""
    errors = _validate_manifest(coverage)
    if errors:
        raise InstructionCoverageError("Instruction coverage is invalid:\n- " + "\n- ".join(errors))
    actual_sha = instruction_coverage_sha256(coverage)
    if approved_coverage_sha256 != actual_sha:
        raise InstructionCoverageError(
            "Instruction coverage approval rejected: supplied SHA-256 must exactly match the displayed manifest SHA-256."
        )
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise InstructionCoverageError("Instruction coverage approver must be non-empty.")
    timestamp = approved_at_utc or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InstructionCoverageError("approved_at_utc must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise InstructionCoverageError("approved_at_utc must include a timezone.")
    approval: dict[str, Any] = {
        "schema_version": "1.0",
        "status": "approved",
        "coverage_sha256": actual_sha,
        "approved_by": approved_by.strip(),
        "approved_at_utc": timestamp,
    }
    approval["approval_sha256"] = instruction_coverage_approval_sha256(approval)
    return approval


def validate_instruction_coverage(
    coverage: Any,
    approval: Any,
    plan: Any,
) -> dict[str, Any]:
    """Check exact source provenance, human approval, and one-to-one plan coverage."""
    errors = _validate_manifest(coverage)
    if not isinstance(coverage, Mapping):
        return {"passed": False, "errors": errors, "requirement_count": 0}

    source = coverage.get("source_instruction")
    if isinstance(source, Mapping):
        source_path_value = source.get("file")
        if isinstance(source_path_value, str) and source_path_value.strip():
            source_path = Path(source_path_value).expanduser()
            if not source_path.is_absolute():
                errors.append("Original instruction file path must be absolute.")
            elif not source_path.is_file():
                errors.append(f"Original instruction file is missing: {source_path}.")
            else:
                actual_source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
                if actual_source_sha != source.get("sha256"):
                    errors.append("Original instruction file SHA-256 differs from the coverage manifest.")

    if not isinstance(approval, Mapping):
        errors.append("A separate author approval receipt for instruction coverage is required.")
    else:
        if approval.get("schema_version") != "1.0" or approval.get("status") != "approved":
            errors.append("Instruction coverage approval receipt has an invalid schema or status.")
        if approval.get("coverage_sha256") != coverage.get("coverage_sha256"):
            errors.append("Instruction coverage approval is not bound to the current manifest SHA-256.")
        if not isinstance(approval.get("approved_by"), str) or not approval.get("approved_by", "").strip():
            errors.append("Instruction coverage approval is missing approved_by.")
        timestamp = approval.get("approved_at_utc")
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone missing")
        except (TypeError, ValueError):
            errors.append("Instruction coverage approval timestamp is invalid or lacks a timezone.")
        if (
            not isinstance(approval.get("approval_sha256"), str)
            or approval.get("approval_sha256") != instruction_coverage_approval_sha256(approval)
        ):
            errors.append("Instruction coverage approval SHA-256 is invalid.")

    plan_items = plan.get("revision_items", []) if isinstance(plan, Mapping) else []
    if not isinstance(plan_items, list):
        errors.append("Revision plan does not contain a revision_items array.")
        plan_items = []
    plan_ids: set[str] = set()
    for index, item in enumerate(plan_items):
        if not isinstance(item, Mapping):
            errors.append(f"Revision plan revision_items[{index}] is malformed.")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"Revision plan revision_items[{index}] has no stable item ID.")
            continue
        if item_id in plan_ids:
            errors.append(f"Revision plan contains duplicate item ID {item_id}.")
        plan_ids.add(item_id)
        if not isinstance(item.get("targets"), list) or not item.get("targets"):
            errors.append(f"Revision plan item {item_id} has no compiled targets.")
    linked_plan_ids: set[str] = set()
    linked_plan_targets: set[tuple[str, int]] = set()
    plan_items_by_id = {
        item["id"]: item for item in plan_items
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    requirements = coverage.get("requirements", [])
    planned_count = no_change_count = manual_count = unresolved_count = 0
    for index, row in enumerate(requirements if isinstance(requirements, list) else []):
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        req_id = row.get("requirement_id", f"requirements[{index}]")
        if status == "planned":
            planned_count += 1
            for target_ref in row.get("revision_targets", []):
                if not isinstance(target_ref, Mapping):
                    continue
                item_id = target_ref.get("revision_item_id")
                target_index = target_ref.get("target_index")
                if item_id not in plan_items_by_id:
                    errors.append(f"Requirement {req_id} refers to missing revision item {item_id}.")
                    continue
                linked_plan_ids.add(item_id)
                matching = plan_items_by_id[item_id]
                targets = matching.get("targets")
                if not isinstance(targets, list) or not targets:
                    errors.append(f"Requirement {req_id} maps to revision item {item_id} without targets.")
                    continue
                if not isinstance(target_index, int) or isinstance(target_index, bool) or not 0 <= target_index < len(targets):
                    errors.append(
                        f"Requirement {req_id} refers to invalid target index {target_index!r} in revision item {item_id}."
                    )
                    continue
                linked_plan_targets.add((item_id, target_index))
        elif status == "no_change":
            no_change_count += 1
        elif status == "manual_completed":
            manual_count += 1
            evidence_file = row.get("evidence_file")
            evidence_path = Path(str(evidence_file)).expanduser() if evidence_file else None
            if evidence_path is None or not evidence_path.is_absolute():
                errors.append(f"Requirement {req_id} manual completion evidence file must be an absolute path.")
            elif not evidence_path.is_file():
                errors.append(f"Requirement {req_id} manual completion evidence file is missing.")
            elif hashlib.sha256(evidence_path.read_bytes()).hexdigest() != row.get("evidence_sha256"):
                errors.append(f"Requirement {req_id} manual completion evidence SHA-256 differs.")
        elif status == "not_applicable":
            no_change_count += 1
        elif status in {"blocked", "deferred"}:
            unresolved_count += 1
            errors.append(
                f"Requirement {req_id} remains {status}: {row.get('reason', 'no reason supplied')}."
            )
    unlinked_plan_ids = sorted(plan_ids - linked_plan_ids)
    if unlinked_plan_ids:
        errors.append(
            "Revision plan item(s) are not linked to any planned source requirement: "
            + ", ".join(unlinked_plan_ids)
            + "."
        )
    unlinked_targets = sorted(
        (item_id, target_index)
        for item_id, item in plan_items_by_id.items()
        for target_index in range(len(item.get("targets", [])) if isinstance(item.get("targets"), list) else 0)
        if (item_id, target_index) not in linked_plan_targets
    )
    if unlinked_targets:
        errors.append(
            "Revision plan target(s) are not linked to any atomic source requirement: "
            + ", ".join(f"{item_id}[{target_index}]" for item_id, target_index in unlinked_targets)
            + "."
        )
    return {
        "passed": not errors,
        "coverage_sha256": coverage.get("coverage_sha256"),
        "source_instruction": dict(source) if isinstance(source, Mapping) else None,
        "requirement_count": len(requirements) if isinstance(requirements, list) else 0,
        "planned_count": planned_count,
        "no_change_count": no_change_count,
        "manual_completed_count": manual_count,
        "unresolved_count": unresolved_count,
        "linked_revision_item_count": len(linked_plan_ids),
        "linked_revision_target_count": len(linked_plan_targets),
        "unlinked_revision_item_ids": unlinked_plan_ids,
        "unlinked_revision_targets": [
            {"revision_item_id": item_id, "target_index": target_index}
            for item_id, target_index in unlinked_targets
        ],
        "errors": errors,
    }

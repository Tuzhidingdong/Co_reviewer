from __future__ import annotations

import hashlib
import re
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .identity import revision_targets_fingerprint, sha256_file


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


def instruction_based_authorization_sha256(
    authorization: Mapping[str, Any],
) -> str:
    return _canonical_sha256(authorization, omit="authorization_sha256")


def _target_scope_sha256(target: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        target, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coverage_plan_link_errors(
    coverage: Mapping[str, Any], plan: Mapping[str, Any]
) -> list[str]:
    """Check that every planned source requirement maps to every exact plan target."""
    errors: list[str] = []
    plan_items = plan.get("revision_items", [])
    if not isinstance(plan_items, list):
        return ["Revision plan does not contain a revision_items array."]
    plan_items_by_id: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(plan_items):
        if not isinstance(item, Mapping):
            errors.append(f"Revision plan revision_items[{index}] is malformed.")
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            errors.append(f"Revision plan revision_items[{index}] has no stable item ID.")
            continue
        if item_id in plan_items_by_id:
            errors.append(f"Revision plan contains duplicate item ID {item_id}.")
        plan_items_by_id[item_id] = item
        if not isinstance(item.get("targets"), list) or not item.get("targets"):
            errors.append(f"Revision plan item {item_id} has no compiled targets.")

    linked_targets: set[tuple[str, int]] = set()
    linked_items: set[str] = set()
    for index, row in enumerate(coverage.get("requirements", [])):
        if not isinstance(row, Mapping):
            continue
        status = row.get("status")
        req_id = row.get("requirement_id", f"requirements[{index}]")
        if status in {"blocked", "deferred"}:
            errors.append(
                f"Requirement {req_id} remains {status}: "
                f"{row.get('reason', 'no reason supplied')}."
            )
            continue
        if status != "planned":
            continue
        refs = row.get("revision_targets", [])
        if not isinstance(refs, list):
            continue
        for target_ref in refs:
            if not isinstance(target_ref, Mapping):
                continue
            item_id = target_ref.get("revision_item_id")
            target_index = target_ref.get("target_index")
            if item_id not in plan_items_by_id:
                errors.append(f"Requirement {req_id} refers to missing revision item {item_id}.")
                continue
            targets = plan_items_by_id[item_id].get("targets")
            if (
                not isinstance(targets, list)
                or not isinstance(target_index, int)
                or isinstance(target_index, bool)
                or not 0 <= target_index < len(targets)
            ):
                errors.append(
                    f"Requirement {req_id} refers to invalid target index "
                    f"{target_index!r} in revision item {item_id}."
                )
                continue
            linked_items.add(item_id)
            linked_targets.add((item_id, target_index))

    for item_id, item in plan_items_by_id.items():
        if item_id not in linked_items:
            errors.append(
                f"Revision plan item {item_id} is not linked to a planned source requirement."
            )
        targets = item.get("targets", [])
        if isinstance(targets, list):
            for target_index in range(len(targets)):
                if (item_id, target_index) not in linked_targets:
                    errors.append(
                        f"Revision plan target {item_id}[{target_index}] is not linked "
                        "to an atomic source requirement."
                    )
    return errors


def validate_instruction_coverage_scope(
    coverage: Any, plan: Any
) -> list[str]:
    """Validate source provenance and exact per-target instruction bindings."""
    errors = _validate_manifest(coverage)
    if not isinstance(coverage, Mapping) or not isinstance(plan, Mapping):
        return errors + ["Coverage and revision plan must both be JSON objects."]
    errors.extend(_coverage_plan_link_errors(coverage, plan))
    expected_by_target: dict[tuple[str, int], set[str]] = {}
    for row in coverage.get("requirements", []):
        if not isinstance(row, Mapping) or row.get("status") != "planned":
            continue
        requirement_id = row.get("requirement_id")
        for ref in row.get("revision_targets", []):
            if not isinstance(ref, Mapping):
                continue
            key = (ref.get("revision_item_id"), ref.get("target_index"))
            expected_by_target.setdefault(key, set()).add(str(requirement_id))
    for item in plan.get("revision_items", []):
        if not isinstance(item, Mapping) or not isinstance(item.get("targets"), list):
            continue
        for index, target in enumerate(item["targets"]):
            if not isinstance(target, Mapping):
                continue
            expected = expected_by_target.get((item.get("id"), index), set())
            declared = target.get("instruction_requirement_ids")
            if not isinstance(declared, list) or set(declared) != expected:
                errors.append(
                    f"Revision target {item.get('id')}[{index}] must declare exactly its "
                    "linked Coverage instruction_requirement_ids."
                )
    return errors


def _instruction_authorization_review_errors(
    coverage: Mapping[str, Any],
    plan: Mapping[str, Any],
    assessment: Any,
) -> list[str]:
    """Fail-closed validation of the agent's instruction-scope review record."""
    errors: list[str] = []
    if not isinstance(assessment, Mapping):
        return ["Instruction authorization assessment is missing or malformed."]
    if assessment.get("schema_version") != "1.0":
        errors.append("Instruction authorization assessment schema_version must be '1.0'.")
    if assessment.get("status") != "eligible":
        errors.append("Instruction authorization assessment status must be 'eligible'.")
    source = coverage.get("source_instruction")
    for field, expected in (
        ("source_instruction_sha256", source.get("sha256") if isinstance(source, Mapping) else None),
        ("coverage_sha256", coverage.get("coverage_sha256")),
    ):
        if assessment.get(field) != expected:
            errors.append(f"Instruction authorization assessment {field} is stale or mismatched.")

    for field in (
        "unresolved_conflicts",
        "uncovered_requirements",
        "out_of_scope_targets",
        "unresolved_questions",
    ):
        value = assessment.get(field)
        if not isinstance(value, list):
            errors.append(f"Instruction authorization assessment {field} must be an array.")
        elif value:
            errors.append(
                f"Instruction-based authorization is blocked by {field}: "
                + json.dumps(value, ensure_ascii=False)
            )

    requirement_ids = {
        row.get("requirement_id")
        for row in coverage.get("requirements", [])
        if isinstance(row, Mapping) and isinstance(row.get("requirement_id"), str)
    }
    reviewed_requirements = assessment.get("reviewed_requirement_ids")
    if (
        not isinstance(reviewed_requirements, list)
        or any(not isinstance(value, str) for value in reviewed_requirements)
        or set(reviewed_requirements) != requirement_ids
    ):
        errors.append(
            "Instruction authorization assessment must review every exact Coverage requirement ID."
        )

    planned_targets = {
        (item.get("id"), index)
        for item in plan.get("revision_items", [])
        if isinstance(item, Mapping) and isinstance(item.get("targets"), list)
        for index, _ in enumerate(item["targets"])
    }
    reviewed_targets = assessment.get("reviewed_target_refs")
    normalized_reviewed: set[tuple[str, int]] = set()
    if isinstance(reviewed_targets, list):
        for row in reviewed_targets:
            if (
                isinstance(row, Mapping)
                and isinstance(row.get("revision_item_id"), str)
                and isinstance(row.get("target_index"), int)
                and not isinstance(row.get("target_index"), bool)
            ):
                normalized_reviewed.add((row["revision_item_id"], row["target_index"]))
    if normalized_reviewed != planned_targets:
        errors.append(
            "Instruction authorization assessment must review every exact revision-plan target."
        )

    plan_input_sha = assessment.get("preflight_plan_sha256")
    if (
        not isinstance(plan_input_sha, str)
        or len(plan_input_sha) != 64
        or any(char not in "0123456789abcdef" for char in plan_input_sha)
    ):
        errors.append("Instruction authorization assessment needs the exact preflight plan SHA-256.")
    return errors


def validate_instruction_authorization_assessment(
    coverage: Any,
    plan: Any,
    assessment: Any,
) -> list[str]:
    """Validate eligibility before any approved-plan artifact is emitted."""
    errors = _validate_manifest(coverage)
    if not isinstance(coverage, Mapping):
        return errors
    if not isinstance(plan, Mapping):
        return errors + ["Revision plan must be a JSON object."]
    errors.extend(validate_instruction_coverage_scope(coverage, plan))
    errors.extend(_instruction_authorization_review_errors(coverage, plan, assessment))
    return errors


def authorize_instruction_based_plan(
    *,
    coverage: Mapping[str, Any],
    plan: Mapping[str, Any],
    assessment: Mapping[str, Any],
    plan_file: str | Path,
    coverage_file: str | Path,
    assessment_file: str | Path,
    analysis_file: str | Path | None = None,
    analysis_file_sha256: str | None = None,
    format_requirements_present: bool = False,
) -> dict[str, Any]:
    """Create a machine authorization; this is explicitly not human approval."""
    errors = validate_instruction_authorization_assessment(coverage, plan, assessment)
    if plan.get("approved") is not True or plan.get("authorization_mode") != "instruction_based":
        errors.append("Instruction-authorized output Plan must be approved in instruction_based mode.")
    if errors:
        raise InstructionCoverageError(
            "Instruction-based authorization blocked:\n- " + "\n- ".join(errors)
        )

    source = coverage.get("source_instruction")
    source_path = Path(str(source.get("file"))).expanduser().resolve()
    if not source_path.is_file() or sha256_file(source_path) != source.get("sha256"):
        raise InstructionCoverageError("Original author instruction is missing or its SHA-256 changed.")

    source_documents: dict[str, str] = {}
    authorized_targets: list[dict[str, Any]] = []
    for item in plan.get("revision_items", []):
        if not isinstance(item, Mapping):
            continue
        item_id = item.get("id")
        if item.get("authorization_mode") == "author_approval" or item.get("requires_author_approval") is True:
            raise InstructionCoverageError(
                f"{item_id} is explicitly marked as requiring human author approval."
            )
        if item.get("status") != "approved":
            raise InstructionCoverageError(
                f"{item_id} must be scope-checked and approved by the preflight authorization step."
            )
        if item.get("preflight_fingerprint") != revision_targets_fingerprint(dict(item)):
            raise InstructionCoverageError(
                f"{item_id} has a missing or stale Preflight fingerprint."
            )
        for index, target in enumerate(item.get("targets", [])):
            if not isinstance(target, Mapping):
                raise InstructionCoverageError(f"{item_id}[{index}] is malformed.")
            if target.get("authorization_mode") == "author_approval" or target.get("requires_author_approval") is True:
                raise InstructionCoverageError(
                    f"{item_id}[{index}] is explicitly marked as requiring human author approval."
                )
            filename = target.get("input_file")
            expected = target.get("source_sha256")
            if not isinstance(filename, str) or not filename.strip():
                raise InstructionCoverageError(f"{item_id}[{index}] has no exact source DOCX path.")
            source_doc = Path(filename).expanduser().resolve()
            if not source_doc.is_file():
                raise InstructionCoverageError(f"Source DOCX is missing: {source_doc}")
            actual = sha256_file(source_doc)
            if not isinstance(expected, str) or actual != expected:
                raise InstructionCoverageError(
                    f"Source DOCX SHA-256 is missing or stale for {item_id}[{index}]: {source_doc}"
                )
            source_documents[str(source_doc)] = actual
            authorized_targets.append({
                "revision_item_id": item_id,
                "target_index": index,
                "scope_sha256": _target_scope_sha256(target),
                "input_file": str(source_doc),
                "input_sha256": actual,
            })

    plan_path = Path(plan_file).expanduser().resolve()
    coverage_path = Path(coverage_file).expanduser().resolve()
    assessment_path = Path(assessment_file).expanduser().resolve()
    if not plan_path.is_file() or not coverage_path.is_file() or not assessment_path.is_file():
        raise InstructionCoverageError("Plan, Coverage and authorization assessment must be existing files.")
    anchored_analysis: dict[str, str] | None = None
    if format_requirements_present:
        if analysis_file is None or not str(analysis_file).strip():
            raise InstructionCoverageError("Format Requirements are present but no instruction_analysis file was supplied for external integrity anchoring.")
        analysis_path = Path(analysis_file).expanduser().resolve()
        if not analysis_path.is_file():
            raise InstructionCoverageError(f"instruction_analysis file is missing: {analysis_path}")
        actual = sha256_file(analysis_path)
        if not isinstance(analysis_file_sha256, str) or actual != analysis_file_sha256:
            raise InstructionCoverageError("instruction_analysis SHA-256 changed before authorization.")
        anchored_analysis = {
            "format_requirements_file": str(analysis_path),
            "format_requirements_file_sha256": actual,
            "format_requirements_present": True,
        }
    authorization: dict[str, Any] = {
        "schema_version": "1.0",
        "authorization_type": "instruction_based_authorization",
        "status": "authorized",
        "issued_by": "Co_Reviewer automated instruction-scope policy",
        **({"format_requirements_present": False} if anchored_analysis is None else anchored_analysis),
        "plan_file": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "coverage_file": str(coverage_path),
        "coverage_file_sha256": sha256_file(coverage_path),
        "coverage_sha256": instruction_coverage_sha256(coverage),
        "source_instruction": dict(source),
        "assessment_file": str(assessment_path),
        "assessment_file_sha256": sha256_file(assessment_path),
        "assessment": dict(assessment),
        "input_documents": [
            {"file": filename, "sha256": digest}
            for filename, digest in sorted(source_documents.items())
        ],
        "authorized_targets": authorized_targets,
        "authorized_requirement_ids": sorted(
            row["requirement_id"] for row in coverage["requirements"]
        ),
        "authorized_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    authorization["authorization_sha256"] = instruction_based_authorization_sha256(authorization)
    return authorization


def validate_instruction_based_authorization(
    *,
    authorization: Any,
    coverage: Any,
    plan: Any,
    plan_file: str | Path,
    coverage_file: str | Path,
    analysis_file: str | Path | None = None,
) -> list[str]:
    """Verify machine authorization against exact current plan, Coverage and inputs."""
    errors: list[str] = []
    if not isinstance(authorization, Mapping):
        return ["Instruction-based authorization receipt is missing or malformed."]
    if (
        authorization.get("schema_version") != "1.0"
        or authorization.get("authorization_type") != "instruction_based_authorization"
        or authorization.get("status") != "authorized"
    ):
        errors.append("Instruction-based authorization receipt has an invalid type or status.")
    if authorization.get("authorization_sha256") != instruction_based_authorization_sha256(authorization):
        errors.append("Instruction-based authorization receipt SHA-256 is invalid.")

    if authorization.get("format_requirements_present") is True:
        fr_file = authorization.get("format_requirements_file")
        fr_sha = authorization.get("format_requirements_file_sha256")
        if not isinstance(fr_file, str) or not fr_file:
            errors.append("Authorization records Format Requirements but has no bounded FR file path.")
        elif analysis_file is None or Path(str(analysis_file)).expanduser().resolve() != Path(fr_file).expanduser().resolve():
            errors.append("Bounded Format Requirements file was not supplied for integrity verification.")
        elif not Path(fr_file).expanduser().is_file():
            errors.append("Bounded Format Requirements file is missing.")
        elif sha256_file(Path(fr_file)) != fr_sha:
            errors.append("Format Requirements file SHA-256 changed after authorization.")

    plan_path = Path(plan_file).expanduser().resolve()
    coverage_path = Path(coverage_file).expanduser().resolve()
    if not plan_path.is_file() or authorization.get("plan_file") != str(plan_path):
        errors.append("Authorized revision plan path is missing or differs from the receipt.")
    elif sha256_file(plan_path) != authorization.get("plan_sha256"):
        errors.append("Revision plan SHA-256 changed after instruction-based authorization.")
    if not coverage_path.is_file() or authorization.get("coverage_file") != str(coverage_path):
        errors.append("Authorized Coverage path is missing or differs from the receipt.")
    elif sha256_file(coverage_path) != authorization.get("coverage_file_sha256"):
        errors.append("Coverage file SHA-256 changed after instruction-based authorization.")

    if not isinstance(coverage, Mapping):
        errors.append("Instruction Coverage is missing or malformed.")
        return errors
    errors.extend(_validate_manifest(coverage))
    if authorization.get("coverage_sha256") != instruction_coverage_sha256(coverage):
        errors.append("Coverage manifest SHA-256 differs from instruction-based authorization.")
    expected_requirement_ids = sorted(
        row.get("requirement_id")
        for row in coverage.get("requirements", [])
        if isinstance(row, Mapping) and isinstance(row.get("requirement_id"), str)
    )
    if authorization.get("authorized_requirement_ids") != expected_requirement_ids:
        errors.append("Authorized Coverage requirement IDs differ from the current manifest.")
    source = coverage.get("source_instruction")
    if authorization.get("source_instruction") != source:
        errors.append("Author instruction provenance differs from instruction-based authorization.")
    if isinstance(source, Mapping):
        source_path = Path(str(source.get("file", ""))).expanduser()
        if not source_path.is_file() or sha256_file(source_path) != source.get("sha256"):
            errors.append("Original author instruction file is missing or changed since authorization.")

    if not isinstance(plan, Mapping) or plan.get("authorization_mode") != "instruction_based":
        errors.append("Revision plan does not declare instruction_based authorization mode.")
    else:
        expected_targets = {
            (item.get("id"), index, _target_scope_sha256(target))
            for item in plan.get("revision_items", [])
            if isinstance(item, Mapping) and isinstance(item.get("targets"), list)
            for index, target in enumerate(item["targets"])
            if isinstance(target, Mapping)
        }
        receipt_targets = authorization.get("authorized_targets")
        actual_targets = {
            (row.get("revision_item_id"), row.get("target_index"), row.get("scope_sha256"))
            for row in receipt_targets if isinstance(row, Mapping)
        } if isinstance(receipt_targets, list) else set()
        if actual_targets != expected_targets:
            errors.append("Authorized revision target scope differs from the current plan.")
        for row in authorization.get("input_documents", []) if isinstance(authorization.get("input_documents"), list) else []:
            if not isinstance(row, Mapping):
                errors.append("Instruction authorization input document entry is malformed.")
                continue
            source_doc = Path(str(row.get("file", ""))).expanduser()
            if not source_doc.is_file() or sha256_file(source_doc) != row.get("sha256"):
                errors.append(f"Input DOCX changed after instruction-based authorization: {source_doc}")
        expected_inputs = {
            str(Path(str(target.get("input_file", ""))).expanduser().resolve()):
            target.get("source_sha256")
            for item in plan.get("revision_items", [])
            if isinstance(item, Mapping)
            for target in item.get("targets", [])
            if isinstance(target, Mapping)
        } if isinstance(plan, Mapping) else {}
        actual_inputs = {
            str(Path(str(row.get("file", ""))).expanduser().resolve()): row.get("sha256")
            for row in authorization.get("input_documents", [])
            if isinstance(row, Mapping)
        }
        if actual_inputs != expected_inputs:
            errors.append("Instruction authorization input-document hash set differs from the current plan.")

    errors.extend(_coverage_plan_link_errors(coverage, plan if isinstance(plan, Mapping) else {}))
    errors.extend(validate_instruction_coverage_scope(coverage, plan))
    errors.extend(
        _instruction_authorization_review_errors(
            coverage,
            plan if isinstance(plan, Mapping) else {},
            authorization.get("assessment"),
        )
    )
    assessment_file = authorization.get("assessment_file")
    if not isinstance(assessment_file, str) or not Path(assessment_file).is_file():
        errors.append("Instruction authorization assessment file is missing.")
    else:
        if sha256_file(assessment_file) != authorization.get("assessment_file_sha256"):
            errors.append("Instruction authorization assessment changed after authorization.")
        else:
            try:
                stored_assessment = json.loads(Path(assessment_file).read_text(encoding="utf-8"))
                if stored_assessment != authorization.get("assessment"):
                    errors.append("Embedded authorization assessment differs from its hash-bound file.")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"Instruction authorization assessment cannot be reread: {exc}")
    return errors


def _validate_manifest(coverage: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(coverage, Mapping):
        return ["Instruction coverage manifest is missing or is not a JSON object."]
    schema_version = coverage.get("schema_version")
    if schema_version not in {"1.0", "1.1"}:
        errors.append("Instruction coverage schema_version must be '1.0' or '1.1'.")
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
    strict_source_lines: list[str] | None = None
    if schema_version == "1.1" and isinstance(source, Mapping):
        source_file = source.get("file")
        try:
            instruction_path = Path(str(source_file)).expanduser().resolve()
            source_bytes = instruction_path.read_bytes()
            if hashlib.sha256(source_bytes).hexdigest() != source.get("sha256"):
                errors.append("source_instruction.sha256 does not match the current original instruction file.")
            strict_source_lines = source_bytes.decode("utf-8-sig").splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(f"Source instruction file cannot be read for exact Coverage 1.1 provenance: {exc}")
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
        if schema_version == "1.1":
            task_id = row.get("task_id")
            line_start = row.get("source_line_start")
            line_end = row.get("source_line_end")
            if not isinstance(task_id, str) or not task_id.strip():
                errors.append(f"{label}.task_id must identify the exact source task.")
            elif isinstance(requirement_id, str) and not requirement_id.startswith(f"REQ-{task_id}-"):
                errors.append(
                    f"{label}.requirement_id must use the task_id prefix REQ-{task_id}-; "
                    "requirement/task attribution is inconsistent."
                )
            valid_lines = (
                isinstance(line_start, int) and not isinstance(line_start, bool) and line_start > 0
                and isinstance(line_end, int) and not isinstance(line_end, bool)
                and line_end >= line_start
            )
            if not valid_lines:
                errors.append(f"{label}.source_line_start/source_line_end must be positive, ordered 1-based line numbers.")
            else:
                expected_locator = f"L{line_start}–L{line_end}"
                if row.get("source_locator") != expected_locator:
                    errors.append(f"{label}.source_locator must exactly equal {expected_locator!r}.")
                if strict_source_lines is not None:
                    if line_end > len(strict_source_lines):
                        errors.append(f"{label} source line range exceeds the original instruction file.")
                    else:
                        exact_excerpt = "\n".join(strict_source_lines[line_start - 1:line_end])
                        if row.get("instruction_excerpt") != exact_excerpt:
                            errors.append(
                                f"{label}.instruction_excerpt must exactly reproduce the original source lines "
                                f"{line_start}-{line_end}; generic or paraphrased excerpts are not valid provenance."
                            )
                        elif isinstance(task_id, str) and not re.search(
                            rf"(?<![A-Za-z0-9_-]){re.escape(task_id)}(?![A-Za-z0-9_-])",
                            exact_excerpt,
                        ):
                            errors.append(
                                f"{label}.task_id {task_id!r} does not appear in its cited original instruction lines; "
                                "task/line attribution is inconsistent."
                            )
        # Atomic script-format requirements: when a source instruction requires
        # restoring or preserving superscript/subscript, it must be its own
        # atomic, traceable requirement with an exact token and alignment.
        script_format = row.get("script_format")
        if script_format is not None:
            if not isinstance(script_format, Mapping):
                errors.append(f"{label}.script_format must be an object.")
            else:
                vert_align = script_format.get("vertAlign")
                token = script_format.get("token")
                if vert_align not in {"superscript", "subscript"}:
                    errors.append(
                        f"{label}.script_format.vertAlign must be 'superscript' or 'subscript'."
                    )
                if not isinstance(token, str) or not token.strip():
                    errors.append(f"{label}.script_format.token must be a non-empty exact script token.")
                extra_keys = set(script_format) - {"vertAlign", "token"}
                if extra_keys:
                    errors.append(
                        f"{label}.script_format has unsupported keys: {sorted(extra_keys)}."
                    )

        status = row.get("status")
        if status not in allowed:
            errors.append(f"{label}.status must be one of: {', '.join(sorted(allowed))}.")
        if status == "planned":
            refs = row.get("revision_targets")
            if script_format is not None and isinstance(refs, list) and len(refs) != 1:
                errors.append(
                    f"{label}: an atomic script-format requirement must map to exactly one revision target."
                )
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
    *,
    instruction_based_authorization: Any = None,
    plan_file: str | Path | None = None,
    coverage_file: str | Path | None = None,
) -> dict[str, Any]:
    """Check source provenance, plan coverage, and one explicit authorization mode."""
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

    if isinstance(approval, Mapping):
        if instruction_based_authorization is not None:
            errors.append(
                "Select exactly one authorization mode: author_approval or "
                "instruction_based_authorization, not both."
            )
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
    elif instruction_based_authorization is None:
        errors.append(
            "Either a separate human author approval receipt or a valid "
            "instruction_based_authorization receipt is required."
        )

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
    if instruction_based_authorization is not None:
        if plan_file is None or coverage_file is None:
            errors.append(
                "Plan and Coverage file paths are required to validate instruction-based authorization."
            )
        else:
            errors.extend(
                validate_instruction_based_authorization(
                    authorization=instruction_based_authorization,
                    coverage=coverage,
                    plan=plan,
                    plan_file=plan_file,
                    coverage_file=coverage_file,
                )
            )
    return {
        "passed": not errors,
        "authorization_mode": (
            "author_approval"
            if isinstance(approval, Mapping)
            else "instruction_based"
            if instruction_based_authorization is not None
            else None
        ),
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

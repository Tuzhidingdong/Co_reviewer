# -*- coding: utf-8 -*-
"""Deterministic risk tiers for review tasks and immutable Patch batches."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

RISK_POLICY_VERSION = "1.0"
RISK_TIERS = ("TIER_0", "TIER_1", "TIER_2")
_MUTATING_ACTIONS = {"replace", "add", "remove", "renumber", "reference_update"}
_HIGH_RISK_TYPES = {"cross_document_consistency", "global_numbering"}
_HIGH_RISK_TERMS = re.compile(
    r"\b(?:format(?:ting)?|font|typeface|style|bold|italic|underline|"
    r"superscript|subscript|formula|equation|citation|reference|figure|"
    r"table|scheme|numbering|renumber|unit|cross[- ]document|"
    r"sample|identifier|composition|concentration|temperature|pressure|"
    r"stoichiometry|electrochemical|chemical|scientific|kinetic|condition)\b",
    re.IGNORECASE,
)
_SCIENTIFIC_TOKEN = re.compile(
    r"(?:\d|\[[^\]]*\]|\b(?:Fig(?:ure)?|Table|Eq(?:uation)?|Scheme|Section)\.?\s*[A-Z0-9]"
    r"|\b(?:mg|g|kg|mL|L|mol|mmol|M|mM|eV|kJ|kcal|nm|um|μm|Å|K|°C|%)\b"
    r"|[α-ωΑ-Ω]|[A-Z][a-z]?\d+[A-Za-z0-9]*)",
    re.IGNORECASE,
)


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def assess_task_risk(task: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a task from deterministic evidence and operation features.

    A model-proposed tier is never trusted. This function recalculates the tier
    from task_type, targets, evidence, unresolved questions and exact operands.
    """
    if not isinstance(task, Mapping):
        raise TypeError("task must be a mapping.")

    reasons: list[str] = []
    task_type = task.get("task_type", "other")
    targets = _rows(task.get("targets"))
    evidence = _rows(task.get("evidence"))
    operations = _rows(task.get("patch_operations"))
    unresolved = task.get("unresolved_questions", [])
    readiness = str(task.get("readiness", "")).strip().casefold()
    target_documents = task.get("target_documents", [])

    if isinstance(unresolved, (list, tuple)) and any(
        isinstance(item, str) and item.strip() for item in unresolved
    ):
        reasons.append("unresolved_questions_present")
    if readiness in {"blocked", "clarification_needed", "ambiguous", "unresolved"}:
        reasons.append("task_readiness_not_executable")

    mutating_targets = [
        row for row in targets
        if row.get("action") in _MUTATING_ACTIONS
    ]
    has_mutating_scope = bool(mutating_targets or operations)

    if task_type == "evidence_request" and has_mutating_scope:
        reasons.append("evidence_request_cannot_authorize_a_document_edit")

    if has_mutating_scope:
        target_evidence_ids = {
            str(evidence_id)
            for row in mutating_targets
            for evidence_id in (row.get("evidence_ids") or [])
        }
        evidence_by_id = {
            str(row.get("evidence_id")): row
            for row in evidence
            if row.get("evidence_id") is not None
        }
        if mutating_targets and not target_evidence_ids:
            reasons.append("mutating_target_has_no_linked_evidence")
        for evidence_id in sorted(target_evidence_ids):
            row = evidence_by_id.get(evidence_id)
            if row is None:
                reasons.append(f"target_evidence_missing:{evidence_id}")
            elif row.get("match_status") != "confirmed":
                reasons.append(
                    f"target_evidence_not_confirmed:{evidence_id}:{row.get('match_status')}"
                )
        for row in evidence:
            if row.get("evidence_type") == "reviewer_comment":
                continue
            if row.get("match_status") in {"candidate", "ambiguous", "unresolved"}:
                reasons.append(
                    f"unresolved_evidence_present:{row.get('evidence_id', '<unknown>')}"
                )

    if reasons:
        tier = "TIER_2"
        return {
            "risk_tier": tier,
            "risk_policy_version": RISK_POLICY_VERSION,
            "risk_reasons": sorted(set(reasons)),
        }

    high_risk = False
    if task_type in _HIGH_RISK_TYPES:
        high_risk = True
        reasons.append(f"high_risk_task_type:{task_type}")

    if isinstance(target_documents, (list, tuple)) and len(set(
        item for item in target_documents if isinstance(item, str)
    )) > 1:
        high_risk = True
        reasons.append("multiple_document_roles")

    for row in mutating_targets:
        if row.get("action") in {"renumber", "reference_update"}:
            high_risk = True
            reasons.append(f"reference_or_numbering_operation:{row.get('action')}")

    for operation in operations:
        if operation.get("format_operations"):
            high_risk = True
            reasons.append("format_change_operation")
        for key in ("find", "replace"):
            value = operation.get(key)
            if isinstance(value, str) and _SCIENTIFIC_TOKEN.search(value):
                high_risk = True
                reasons.append("scientific_or_reference_token_in_exact_operands")

    text_fields = [
        task.get("proposed_action"),
        task.get("proposed_response"),
        *(row.get("source_excerpt") for row in evidence),
    ]
    if any(isinstance(value, str) and _SCIENTIFIC_TOKEN.search(value) for value in text_fields):
        high_risk = True
        reasons.append("scientific_or_reference_token_in_task_evidence")
    if any(isinstance(value, str) and _HIGH_RISK_TERMS.search(value) for value in text_fields):
        high_risk = True
        reasons.append("high_risk_format_formula_reference_or_numbering_language")

    if high_risk:
        tier = "TIER_1"
    else:
        tier = "TIER_0"
        reasons.append("single_scope_ordinary_language_or_non_mutating_task")

    return {
        "risk_tier": tier,
        "risk_policy_version": RISK_POLICY_VERSION,
        "risk_reasons": sorted(set(reasons)),
    }


def validate_batch_risk_tiers(tasks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reject Tier 2 and mixed-risk batches; never downgrade a task's tier."""
    if not isinstance(tasks, (list, tuple)) or not tasks:
        raise ValueError("A risk-reviewed batch must contain at least one task.")
    assessments = []
    for task in tasks:
        assessment = assess_task_risk(task)
        assessments.append({
            "task_id": task.get("task_id"),
            **assessment,
        })
    tier_set = {row["risk_tier"] for row in assessments}
    tier2 = [row for row in assessments if row["risk_tier"] == "TIER_2"]
    if tier2:
        details = "; ".join(
            f"{row.get('task_id')}: {', '.join(row['risk_reasons'])}"
            for row in tier2
        )
        raise ValueError(
            "Tier 2 task(s) cannot enter a Patch batch; clarify evidence/targets first. " + details
        )
    if len(tier_set) != 1:
        details = ", ".join(
            f"{row.get('task_id')}={row['risk_tier']}" for row in assessments
        )
        raise ValueError(
            "Mixed-risk batches are not permitted. Create separate immutable Tier 0 and Tier 1 batches: "
            + details
        )
    tier = next(iter(tier_set))
    return {
        "risk_policy_version": RISK_POLICY_VERSION,
        "risk_tier": tier,
        "high_risk_review_required": tier == "TIER_1",
        "tasks": assessments,
    }

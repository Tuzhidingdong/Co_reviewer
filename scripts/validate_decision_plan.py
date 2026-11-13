#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural and provenance validator for pending Co_Reviewer decision plans."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


def _role(value: str) -> str:
    value = (value or "").strip()
    return "Reviewers" if value.casefold() in {"reply", "reviewer", "reviewers"} else value


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", value).strip()


def _documents(inventory: Any) -> dict[str, dict[str, Any]]:
    rows = inventory if isinstance(inventory, list) else inventory.get("documents", [])
    return {_role(row.get("role", "")): row for row in rows if isinstance(row, dict)}


def _paragraph_index(inventory: Any) -> dict[tuple[str, str, int], dict[str, Any]]:
    result = {}
    for role, report in _documents(inventory).items():
        for paragraph in report.get("paragraphs", []):
            try:
                key = (role, paragraph.get("part", "word/document.xml"), int(paragraph["p_idx"]))
            except (KeyError, TypeError, ValueError):
                continue
            result[key] = paragraph
    return result


def validate_decision_plan(
    plan: Any,
    audit: dict[str, Any] | None = None,
    inventory: Any | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["Decision plan root must be a JSON object."]
    if plan.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'.")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks must be a non-empty array.")
        tasks = []

    audit = audit or {}
    inventory = inventory if inventory is not None else []
    docs = _documents(inventory)
    paragraphs = _paragraph_index(inventory)
    if not docs:
        errors.append("workspace_inventory must contain at least one parsed document report.")

    known_comments: set[str] = set()
    for match in audit.get("comment_matches", []):
        if match.get("comment_id") is not None:
            known_comments.add(str(match["comment_id"]))
    reviewer_report = docs.get("Reviewers", {})
    for comment in reviewer_report.get("comments", []):
        if comment.get("id") is not None:
            known_comments.add(str(comment["id"]))

    expected_sources = {
        (_role(row.get("role", "")), str(row.get("file", "")), str(row.get("sha256", "")))
        for row in audit.get("source_files", [])
        if isinstance(row, dict)
    }
    if inventory:
        expected_sources.update(
            (_role(row.get("role", "")), str(row.get("file", "")), str(row.get("source_sha256", "")))
            for row in _documents(inventory).values()
        )

    plan_sources = plan.get("source_files")
    if not isinstance(plan_sources, list):
        errors.append("source_files must be an array.")
        plan_sources = []
    seen_sources = set()
    for index, row in enumerate(plan_sources):
        if not isinstance(row, dict):
            errors.append(f"source_files[{index}] must be an object.")
            continue
        key = (_role(row.get("role", "")), str(row.get("file", "")), str(row.get("sha256", "")))
        if key[0] not in {"Reviewers", "Manuscript", "SI"} or not key[1] or not re.fullmatch(r"[a-f0-9]{64}", key[2]):
            errors.append(f"source_files[{index}] must contain a known role, file and a SHA-256 hash.")
        if key in seen_sources:
            errors.append(f"Duplicate source file entry: {key[1]}")
        seen_sources.add(key)
        if expected_sources and key not in expected_sources:
            errors.append(f"source_files[{index}] does not match the audit/inventory source identity: {key[1]}")
    if expected_sources and seen_sources != expected_sources:
        missing_source_rows = sorted(expected_sources - seen_sources)
        if missing_source_rows:
            errors.append("Decision plan omitted source file(s): " + ", ".join(row[1] for row in missing_source_rows))

    task_ids: set[str] = set()
    dependencies: dict[str, list[str]] = {}
    task_rows: dict[str, dict[str, Any]] = {}
    task_sequences: dict[str, int] = {}
    covered_comments: set[str] = set()
    required_fields = (
        "task_id", "sequence", "title", "task_type", "source_comment_ids", "target_documents",
        "evidence", "proposed_action", "targets", "dependencies", "readiness",
        "unresolved_questions", "confidence", "status", "requires_user_approval",
    )

    for index, task in enumerate(tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        for field in required_fields:
            if field not in task:
                errors.append(f"{prefix} is missing {field}.")
        task_id = str(task.get("task_id", "")).strip()
        if not task_id:
            errors.append(f"{prefix}.task_id must be non-empty.")
        elif task_id in task_ids:
            errors.append(f"Duplicate task_id: {task_id}")
        else:
            task_ids.add(task_id)
            task_rows[task_id] = task

        if not isinstance(task.get("title"), str) or not task.get("title", "").strip():
            errors.append(f"{prefix}.title must be a non-empty string.")
        task_types = {
            "local_revision", "cross_document_consistency", "global_numbering",
            "content_placement_decision", "response_only", "author_decision",
            "evidence_request", "other",
        }
        if task.get("task_type") not in task_types:
            errors.append(f"{prefix}.task_type has an unsupported value.")
        if not isinstance(task.get("proposed_action"), str) or not task.get("proposed_action", "").strip():
            errors.append(f"{prefix}.proposed_action must be a non-empty string.")
        target_documents = task.get("target_documents", [])
        allowed_documents = {"Reviewers", "Manuscript", "SI"}
        if (
            not isinstance(target_documents, list)
            or any(_role(value) not in allowed_documents for value in target_documents)
            or len({_role(value) for value in target_documents}) != len(target_documents)
        ):
            errors.append(f"{prefix}.target_documents must be a unique array of known document roles.")
        if task.get("status") != "pending":
            errors.append(f"{prefix}.status must remain 'pending'; decision plans cannot apply edits.")
        sequence = task.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            errors.append(f"{prefix}.sequence must be a positive integer.")
        elif sequence in task_sequences.values():
            errors.append(f"{prefix}.sequence values must be unique.")
        else:
            task_sequences[task_id] = sequence
        if task.get("requires_user_approval") is not True:
            errors.append(f"{prefix}.requires_user_approval must be true.")
        if task.get("readiness") not in {
            "ready_for_author_approval", "needs_clarification", "blocked_by_missing_evidence"
        }:
            errors.append(f"{prefix}.readiness has an unsupported value.")
        confidence = task.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            errors.append(f"{prefix}.confidence must be a number between 0 and 1.")

        comment_ids = task.get("source_comment_ids", [])
        if not isinstance(comment_ids, list):
            errors.append(f"{prefix}.source_comment_ids must be an array.")
            comment_ids = []
        if len({str(value) for value in comment_ids}) != len(comment_ids):
            errors.append(f"{prefix}.source_comment_ids contains duplicates.")
        for comment_id in comment_ids:
            comment_key = str(comment_id)
            covered_comments.add(comment_key)
            if known_comments and comment_key not in known_comments:
                errors.append(f"{prefix} refers to unknown reviewer comment ID {comment_id!r}.")

        dependency_list = task.get("dependencies", [])
        if not isinstance(dependency_list, list):
            errors.append(f"{prefix}.dependencies must be an array.")
            dependency_list = []
        if len({str(item) for item in dependency_list}) != len(dependency_list):
            errors.append(f"{prefix}.dependencies contains duplicates.")
        dependencies[task_id] = [str(item) for item in dependency_list]

        evidence = task.get("evidence", [])
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence must contain at least one traceable evidence item.")
            evidence = []
        evidence_ids = set()
        for e_index, item in enumerate(evidence):
            ep = f"{prefix}.evidence[{e_index}]"
            if not isinstance(item, dict):
                errors.append(f"{ep} must be an object.")
                continue
            evidence_id = str(item.get("evidence_id", "")).strip()
            if not evidence_id or evidence_id in evidence_ids:
                errors.append(f"{ep}.evidence_id must be non-empty and unique within the task.")
            evidence_ids.add(evidence_id)
            document = _role(item.get("document", ""))
            part = item.get("part")
            p_idx = item.get("p_idx")
            excerpt = item.get("source_excerpt")
            if document not in {"Reviewers", "Manuscript", "SI"} or not isinstance(part, str) or not part:
                errors.append(f"{ep} must identify a known document role and Word part.")
                continue
            if not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0:
                errors.append(f"{ep}.p_idx must be a non-negative integer.")
                continue
            if not isinstance(excerpt, str) or not excerpt.strip():
                errors.append(f"{ep}.source_excerpt must be non-empty.")
                continue
            if item.get("match_status") not in {
                "confirmed", "candidate", "ambiguous", "unresolved", "not_applicable"
            }:
                errors.append(f"{ep}.match_status has an unsupported value.")
            if item.get("evidence_type") not in {
                "reviewer_comment", "native_anchor", "paragraph", "caption",
                "reference", "table", "figure", "audit_finding",
            }:
                errors.append(f"{ep}.evidence_type has an unsupported value.")
            if inventory:
                comment = next(
                    (row for row in reviewer_report.get("comments", [])
                     if str(row.get("id")) == str(item.get("source_comment_id"))),
                    None,
                )
                if item.get("evidence_type") == "reviewer_comment":
                    if comment is None:
                        errors.append(f"{ep} references an unknown native reviewer comment.")
                        continue
                    if part != "word/comments.xml" or comment.get("comment_xml_p_idx") != p_idx:
                        errors.append(f"{ep} reviewer-comment coordinates must identify the comment's word/comments.xml entry.")
                    if _normalise(excerpt) not in _normalise(comment.get("comment", "")):
                        errors.append(f"{ep}.source_excerpt is not present in the cited native reviewer comment.")
                    continue

                source = paragraphs.get((document, part, p_idx))
                if source is None:
                    errors.append(f"{ep} points to a paragraph absent from workspace_inventory: {document}/{part}/P[{p_idx}].")
                    continue
                if item.get("para_id") and source.get("para_id") != item.get("para_id"):
                    errors.append(f"{ep}.para_id does not match workspace_inventory.")
                source_text = source.get("text", "")
                if _normalise(excerpt) not in _normalise(source_text):
                    errors.append(f"{ep}.source_excerpt is not present in its cited inventory paragraph.")



        targets = task.get("targets", [])
        if not isinstance(targets, list):
            errors.append(f"{prefix}.targets must be an array.")
            targets = []
        for t_index, target in enumerate(targets):
            tp = f"{prefix}.targets[{t_index}]"
            if not isinstance(target, dict):
                errors.append(f"{tp} must be an object.")
                continue
            document = _role(target.get("document", ""))
            part = target.get("part")
            p_idx = target.get("p_idx")
            if document not in {"Reviewers", "Manuscript", "SI"} or not isinstance(part, str) or not part:
                errors.append(f"{tp} must identify a known document role and Word part.")
            if target.get("action") not in {
                "replace", "add", "remove", "renumber", "reference_update",
                "keep", "inspect_only", "reply_only",
            }:
                errors.append(f"{tp}.action has an unsupported value.")
            if not isinstance(p_idx, int) or isinstance(p_idx, bool) or p_idx < 0:
                errors.append(f"{tp}.p_idx must be a non-negative integer.")
            elif inventory and (document, part, p_idx) not in paragraphs:
                errors.append(f"{tp} points to a target paragraph absent from workspace_inventory.")
            basis = target.get("evidence_ids", [])
            if (
                not isinstance(basis, list)
                or not basis
                or any(item not in evidence_ids for item in basis)
            ):
                errors.append(f"{tp}.evidence_ids must be non-empty and refer only to evidence IDs in the same task.")
            else:
                location_evidence = [
                    row for row in evidence
                    if isinstance(row, dict)
                    and _role(row.get("document", "")) == document
                    and row.get("part") == part
                    and row.get("p_idx") == p_idx
                ]
                if not location_evidence or not any(row.get("evidence_id") in basis for row in location_evidence):
                    errors.append(f"{tp} lacks evidence from its own target document/part/paragraph.")

    if not isinstance(plan.get("comment_scope"), str):
        errors.append("comment_scope must be explicitly declared.")
    scope = plan.get("comment_scope", "all_comments")
    included_ids_value = plan.get("included_comment_ids", [])
    if not isinstance(included_ids_value, list):
        errors.append("included_comment_ids must be an array.")
        included_ids_value = []
    declared_comment_ids = {str(value) for value in included_ids_value}
    if scope not in {"all_comments", "selected_comments"}:
        errors.append("comment_scope must be 'all_comments' or 'selected_comments'.")
    elif scope == "all_comments" and known_comments and covered_comments != known_comments:
        missing = sorted(known_comments - covered_comments)
        extra = sorted(covered_comments - known_comments)
        if missing:
            errors.append("Decision plan omitted reviewer comment ID(s): " + ", ".join(missing))
        if extra:
            errors.append("Decision plan includes unknown reviewer comment IDs: " + ", ".join(extra))
    elif scope == "selected_comments":
        if not declared_comment_ids:
            errors.append("selected_comments scope requires included_comment_ids.")
        if known_comments and not declared_comment_ids.issubset(known_comments):
            errors.append("included_comment_ids contains an unknown reviewer comment ID.")
        if declared_comment_ids != covered_comments:
            errors.append("included_comment_ids must exactly match source_comment_ids covered by tasks.")

    for task_id, deps in dependencies.items():
        for dep in deps:
            if dep not in task_ids:
                errors.append(f"Task {task_id} depends on unknown task {dep}.")
            if dep == task_id:
                errors.append(f"Task {task_id} cannot depend on itself.")
            if dep in task_sequences and task_id in task_sequences and task_sequences[dep] >= task_sequences[task_id]:
                errors.append(f"Task {task_id} must be sequenced after dependency {dep}.")

    # Detect cycles independently of the order in which tasks are presented.
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(task_id: str) -> None:
        if task_id in visiting:
            errors.append(f"Dependency cycle detected at task {task_id}.")
            return
        if task_id in visited:
            return
        visiting.add(task_id)
        for dep in dependencies.get(task_id, []):
            if dep in dependencies:
                visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in dependencies:
        visit(task_id)

    return list(dict.fromkeys(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Pending revision_decision_plan.json")
    parser.add_argument("--audit", required=True, help="review_audit_report.json used for this decision round")
    parser.add_argument("--inventory", required=True, help="workspace_inventory.json used for this decision round")
    args = parser.parse_args(argv)
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        audit = json.loads(Path(args.audit).read_text(encoding="utf-8")) if args.audit else None
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8")) if args.inventory else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[INVALID] Cannot read decision-plan inputs: {exc}", file=sys.stderr)
        return 2
    errors = validate_decision_plan(plan, audit=audit, inventory=inventory)
    if errors:
        print("[INVALID] Decision plan failed structural/provenance checks:")
        for error in errors:
            print(f"- {error}")
        return 2
    print(f"[VALID] {len(plan['tasks'])} pending task(s); user approval remains required.")
    print("[GUARD] Evidence locations/excerpts, comment IDs, source hashes and dependency graph passed.")
    print("[NOTE] This validates structure and provenance, not scientific correctness or the semantic quality of the proposed decisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

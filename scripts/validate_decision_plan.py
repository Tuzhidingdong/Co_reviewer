#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structural and provenance validator for pending Co_Reviewer decision plans."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from co_reviewer.docx_identity import (
    build_target_refs_by_task,
    validate_manifest_sources,
)
from co_reviewer.identity import sha256_file
from co_reviewer.workflow_engine import (
    build_workflow_ledger,
    build_workflow_schedule,
    validate_workflow_tasks,
    workflow_tasks_from_decision_plan,
)


def _role(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return "Reviewers" if value.casefold() in {"reply", "reviewer", "reviewers"} else value


def _normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", value).strip()


def _documents(inventory: Any) -> dict[str, dict[str, Any]]:
    if isinstance(inventory, list):
        rows = inventory
    elif isinstance(inventory, dict):
        rows = inventory.get("documents", [])
    else:
        return {}
    return {_role(row.get("role", "")): row for row in rows if isinstance(row, dict)}


def _paragraph_index(inventory: Any) -> dict[tuple[str, str, int], dict[str, Any]]:
    result = {}
    for role, report in _documents(inventory).items():
        for paragraph in report.get("paragraphs", []):
            if not isinstance(paragraph, dict):
                continue
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

    if audit is not None and not isinstance(audit, dict):
        errors.append("review_audit_report must be a JSON object.")
        audit = {}
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
        if not isinstance(task.get("task_type"), str) or task.get("task_type") not in task_types:
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
        if not isinstance(task.get("readiness"), str) or task.get("readiness") not in {
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

        if not isinstance(task.get("unresolved_questions"), list):
            errors.append(f"{prefix}.unresolved_questions must be an array.")

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
            if not isinstance(item.get("match_status"), str) or item.get("match_status") not in {
                "confirmed", "candidate", "ambiguous", "unresolved", "not_applicable"
            }:
                errors.append(f"{ep}.match_status has an unsupported value.")
            if not isinstance(item.get("evidence_type"), str) or item.get("evidence_type") not in {
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
            if not isinstance(target.get("action"), str) or target.get("action") not in {
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
                or any(not isinstance(item, str) or item not in evidence_ids for item in basis)
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
        scope = None
    else:
        scope = plan.get("comment_scope")
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

    # The workflow engine is now the single dependency-graph implementation.
    # Keep the legacy checks above for readable source-plan validation messages;
    # the adapter also derives provisional target groups for the quotient DAG.
    workflow_tasks = workflow_tasks_from_decision_plan(tasks)
    for workflow_error in validate_workflow_tasks(workflow_tasks):
        errors.append(workflow_error)

    return list(dict.fromkeys(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, help="Pending revision_decision_plan.json")
    parser.add_argument("--audit", required=True, help="review_audit_report.json used for this decision round")
    parser.add_argument("--inventory", required=True, help="workspace_inventory.json used for this decision round")
    parser.add_argument(
        "--identity-manifest",
        help="Optional parser-backed identity manifest; converts decision-plan p_idx/paraId anchors into stable TargetRefs.",
    )
    parser.add_argument(
        "--workflow-ledger-out",
        help="Optional path to persist the v0.5.1 Workflow Ledger JSON.",
    )
    parser.add_argument(
        "--revision-round-id",
        help="Optional stable round ID; generated when ledger output is requested and omitted.",
    )
    parser.add_argument(
        "--round-manifest",
        help="Optional round_manifest.json or refresh_manifest.json; binds the Workflow Ledger and audit/inventory files to one exact review-round source snapshot.",
    )
    parser.add_argument(
        "--round-root-manifest",
        help="Root round_manifest.json to register the newly generated Workflow Ledger path/hash; required when --round-manifest is a dedicated refresh_manifest.json.",
    )
    args = parser.parse_args(argv)
    if args.revision_round_id and not args.workflow_ledger_out:
        parser.error("--revision-round-id requires --workflow-ledger-out")
    if args.round_manifest and not args.workflow_ledger_out:
        parser.error("--round-manifest requires --workflow-ledger-out")
    if args.round_root_manifest and not args.workflow_ledger_out:
        parser.error("--round-root-manifest requires --workflow-ledger-out")
    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        audit = json.loads(Path(args.audit).read_text(encoding="utf-8")) if args.audit else None
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8")) if args.inventory else None
        identity_manifest = (
            json.loads(Path(args.identity_manifest).read_text(encoding="utf-8"))
            if args.identity_manifest else None
        )
        round_manifest = (
            json.loads(Path(args.round_manifest).read_text(encoding="utf-8"))
            if args.round_manifest else None
        )
        root_manifest_path = (
            Path(args.round_root_manifest).expanduser().resolve()
            if args.round_root_manifest else None
        )
        root_manifest_sha_at_load = None
        if root_manifest_path is not None:
            root_manifest_sha_at_load = sha256_file(root_manifest_path)
            root_round_manifest = json.loads(root_manifest_path.read_text(encoding="utf-8"))
            if sha256_file(root_manifest_path) != root_manifest_sha_at_load:
                raise ValueError("Root round manifest changed while it was being read.")
        else:
            root_round_manifest = None
        if round_manifest is None and root_round_manifest is not None:
            round_manifest = root_round_manifest
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[INVALID] Cannot read decision-plan inputs: {exc}", file=sys.stderr)
        return 2
    if round_manifest is not None:
        manifest_round_id = round_manifest.get("review_round_id")
        if not isinstance(manifest_round_id, str) or not manifest_round_id.strip():
            print("[INVALID] round/refresh manifest has no valid review_round_id.", file=sys.stderr)
            return 2
        if args.revision_round_id and args.revision_round_id != manifest_round_id:
            print("[INVALID] --revision-round-id conflicts with the supplied round/refresh manifest.", file=sys.stderr)
            return 2

        supplied_paths = {
            "review_audit_report": Path(args.audit).expanduser().resolve(),
            "workspace_inventory": Path(args.inventory).expanduser().resolve(),
        }
        candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        # A dedicated refresh_manifest.json carries audit_artifacts; the starter
        # round manifest carries artifacts and can additionally point at its
        # most recent refresh artifacts.
        refresh_artifacts = round_manifest.get("audit_artifacts")
        if isinstance(refresh_artifacts, dict):
            candidates.append((
                "refresh manifest",
                refresh_artifacts,
                round_manifest.get("artifact_sha256", {}),
            ))
        latest_refresh = {}
        if isinstance(round_manifest.get("latest_refresh_audit"), str):
            latest_refresh["review_audit_report"] = round_manifest["latest_refresh_audit"]
        if isinstance(round_manifest.get("latest_refresh_inventory"), str):
            latest_refresh["workspace_inventory"] = round_manifest["latest_refresh_inventory"]
        if set(latest_refresh) == set(supplied_paths):
            latest_hashes = round_manifest.get("latest_refresh_artifact_sha256", {})
            if not isinstance(latest_hashes, dict):
                latest_hashes = {}
            candidates.append(("latest refresh recorded in round manifest", latest_refresh, latest_hashes))
        initial_artifacts = round_manifest.get("artifacts")
        if isinstance(initial_artifacts, dict):
            candidates.append((
                "starter round manifest",
                initial_artifacts,
                round_manifest.get("artifact_sha256", {}),
            ))
        matched = None
        for label, artifacts, hashes in candidates:
            expected_paths = {
                "review_audit_report": artifacts.get("review_audit_report"),
                "workspace_inventory": artifacts.get("workspace_inventory"),
            }
            if all(
                isinstance(expected_paths[key], str)
                and supplied_paths[key] == Path(expected_paths[key]).expanduser().resolve()
                for key in supplied_paths
            ):
                matched = (label, expected_paths, hashes if isinstance(hashes, dict) else {})
                break
        if matched is None:
            print(
                "[INVALID] Supplied audit/inventory paths do not match the starter or latest-refresh artifacts "
                "recorded in the supplied manifest.",
                file=sys.stderr,
            )
            return 2
        binding_label, expected_paths, expected_hashes = matched
        for key, supplied_path in supplied_paths.items():
            expected_hash = expected_hashes.get(key)
            if expected_hash is not None:
                if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                    print(f"[INVALID] Manifest contains an invalid {key} SHA-256.", file=sys.stderr)
                    return 2
                if not supplied_path.is_file() or sha256_file(supplied_path) != expected_hash:
                    print(
                        f"[INVALID] {key} content hash does not match the {binding_label}: {supplied_path}",
                        file=sys.stderr,
                    )
                    return 2
            elif not supplied_path.is_file():
                print(f"[INVALID] {key} file does not exist: {supplied_path}", file=sys.stderr)
                return 2
        if any(key not in expected_hashes for key in supplied_paths):
            print(
                f"[LEGACY] {binding_label} has no complete artifact SHA-256 map; path binding was checked, "
                "but this older manifest cannot prove the audit/inventory file bytes.",
                file=sys.stderr,
            )
    if args.round_manifest and root_manifest_path is None:
        # A starter round manifest carries an artifacts map. A dedicated refresh manifest
        # carries audit_artifacts and must be paired with the root manifest for write-back.
        if isinstance(round_manifest, dict) and isinstance(round_manifest.get("artifacts"), dict) and not isinstance(round_manifest.get("audit_artifacts"), dict):
            root_manifest_path = Path(args.round_manifest).expanduser().resolve()
            if root_manifest_sha_at_load is None:
                root_manifest_sha_at_load = sha256_file(root_manifest_path)
            root_round_manifest = round_manifest
        else:
            print("[INVALID] A dedicated refresh_manifest.json must be paired with --round-root-manifest so the generated Workflow Ledger can be registered in the round.", file=sys.stderr)
            return 2
    if root_round_manifest is not None:
        if not isinstance(root_round_manifest, dict) or root_round_manifest.get("review_round_id") != round_manifest.get("review_round_id"):
            print("[INVALID] Root round manifest and audit/refresh manifest do not identify the same review round.", file=sys.stderr)
            return 2
        root_candidates = []
        latest_audit = root_round_manifest.get("latest_refresh_audit")
        latest_inventory = root_round_manifest.get("latest_refresh_inventory")
        if isinstance(latest_audit, str) and isinstance(latest_inventory, str):
            root_candidates.append((
                {"review_audit_report": latest_audit, "workspace_inventory": latest_inventory},
                root_round_manifest.get("latest_refresh_artifact_sha256", {}),
            ))
        initial_artifacts = root_round_manifest.get("artifacts")
        if isinstance(initial_artifacts, dict):
            root_candidates.append((initial_artifacts, root_round_manifest.get("artifact_sha256", {})))
        root_match = None
        for artifact_paths, artifact_hashes in root_candidates:
            if not isinstance(artifact_hashes, dict):
                artifact_hashes = {}
            try:
                matched_paths = (
                    Path(str(artifact_paths.get("review_audit_report", ""))).expanduser().resolve()
                    == Path(args.audit).expanduser().resolve()
                    and Path(str(artifact_paths.get("workspace_inventory", ""))).expanduser().resolve()
                    == Path(args.inventory).expanduser().resolve()
                )
            except (OSError, RuntimeError):
                matched_paths = False
            if matched_paths:
                root_match = artifact_hashes
                break
        if root_match is None:
            print("[INVALID] Audit/inventory paths do not match the starter or latest-refresh artifacts recorded in the root round manifest.", file=sys.stderr)
            return 2
        for key, supplied_path in (("review_audit_report", args.audit), ("workspace_inventory", args.inventory)):
            expected_hash = root_match.get(key)
            if expected_hash is not None:
                if (
                    not isinstance(expected_hash, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
                    or not Path(supplied_path).is_file()
                    or sha256_file(supplied_path) != expected_hash
                ):
                    print(f"[INVALID] {key} bytes do not match the root round manifest SHA-256.", file=sys.stderr)
                    return 2
    errors = validate_decision_plan(plan, audit=audit, inventory=inventory)
    target_refs_by_task: dict[str, list[dict[str, Any]]] = {}
    if identity_manifest is not None:
        errors.extend(validate_manifest_sources(identity_manifest, plan))
        if not errors:
            target_refs_by_task, target_errors = build_target_refs_by_task(
                plan.get("tasks", []), identity_manifest
            )
            errors.extend(target_errors)
    if errors:
        print("[INVALID] Decision plan failed structural/provenance checks:")
        for error in errors:
            print(f"- {error}")
        return 2
    workflow_schedule = build_workflow_schedule(
        workflow_tasks_from_decision_plan(
            plan["tasks"],
            target_refs_by_task=target_refs_by_task if identity_manifest is not None else None,
        )
    )
    print(f"[VALID] {len(plan['tasks'])} pending task(s); user approval remains required.")
    print("[GUARD] Evidence locations/excerpts, comment IDs, source hashes and task dependency graph passed.")
    print(
        f"[SCHEDULE] {len(workflow_schedule.task_order)} task(s); "
        f"{len(workflow_schedule.target_group_components)} target-group component(s)."
    )
    if identity_manifest is not None:
        print(
            "[IDENTITY] Stable TargetRefs mapped: "
            + str(sum(len(rows) for rows in target_refs_by_task.values()))
        )
    if args.workflow_ledger_out:
        output_path = Path(args.workflow_ledger_out).expanduser().resolve()
        source_paths = {
            Path(value).expanduser().resolve()
            for value in (args.plan, args.audit, args.inventory, args.identity_manifest)
            if value
        }
        if root_manifest_path is not None:
            source_paths.add(root_manifest_path)
        if args.round_manifest:
            source_paths.add(Path(args.round_manifest).expanduser().resolve())
        if output_path in source_paths:
            print("[INVALID] Workflow Ledger output must not overwrite a plan, audit, inventory or manifest input.", file=sys.stderr)
            return 2
        if output_path.exists():
            print(f"[INVALID] Workflow Ledger output already exists; choose a new path: {output_path}", file=sys.stderr)
            return 2
        round_id = (
            str(round_manifest["review_round_id"]).strip()
            if round_manifest is not None
            else args.revision_round_id or (
                "round-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                + "-" + uuid.uuid4().hex[:8]
            )
        )
        try:
            ledger = build_workflow_ledger(
                plan,
                revision_round_id=round_id,
                created_at_utc=datetime.now(timezone.utc).isoformat(),
                document_identities=(
                    identity_manifest.get("documents", [])
                    if identity_manifest is not None else None
                ),
                object_registry=(
                    identity_manifest.get("objects", [])
                    if identity_manifest is not None else None
                ),
                target_refs_by_task=(
                    target_refs_by_task if identity_manifest is not None else None
                ),
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if root_manifest_path is not None:
                if (
                    root_manifest_sha_at_load is None
                    or not root_manifest_path.is_file()
                    or sha256_file(root_manifest_path) != root_manifest_sha_at_load
                ):
                    raise ValueError("Root round manifest changed during Decision Plan validation; the new Workflow Ledger was retained but the manifest was not overwritten.")
                root_round_manifest.update({
                    "current_workflow_ledger": str(output_path),
                    "workflow_ledger_sha256": sha256_file(output_path),
                })
                temporary_manifest = root_manifest_path.with_name(
                    root_manifest_path.name + "." + uuid.uuid4().hex + ".tmp"
                )
                try:
                    temporary_manifest.write_text(
                        json.dumps(root_round_manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    temporary_manifest.replace(root_manifest_path)
                finally:
                    temporary_manifest.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError) as exc:
            print(f"[INVALID] Cannot write Workflow Ledger: {exc}", file=sys.stderr)
            return 2
        print(f"[LEDGER] Persisted Workflow Ledger: {output_path}")
        print(f"[LEDGER] Revision round ID: {round_id}")
    print("[NOTE] This validates structure and provenance, not scientific correctness or the semantic quality of the proposed decisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

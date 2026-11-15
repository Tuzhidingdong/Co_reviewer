# -*- coding: utf-8 -*-
"""Append-only, evidence-labelled usage and document-read audit ledger.

This module records observations; it never infers model-context reads or tokens
from filesystem access. Missing measurements remain unavailable, not zero.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STAGES = (
    "parse_inventory", "cross_document_audit", "decision_synthesis",
    "decision_plan_validation", "approval_preview", "patch_authoring",
    "patch_preflight", "patch_execution", "patch_verification",
    "semantic_review", "working_clean_projection", "word_layout_mapping",
    "response_letter_drafting", "global_consistency_review", "rework_retry",
    "other",
)
TOKEN_BUCKETS = (
    "input_tokens", "cache_read_tokens", "cache_write_tokens",
    "output_tokens", "reasoning_tokens",
)
EVENT_TYPES = {"stage", "document_read", "model_usage", "note"}
ACTORS = {"supervisor", "worker", "system", "external", "unknown"}
TOKEN_SOURCES = {"measured", "estimated", "unavailable"}
REASONING_RELATIONS = {"included_in_output", "separate", "unknown"}


class UsageAuditError(ValueError):
    """Raised for malformed or unsafe usage-audit events."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_nonnegative_int(value: Any, field: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise UsageAuditError(f"{field} must be a non-negative integer or null.")


def validate_usage_event(event: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(event, Mapping):
        raise UsageAuditError("Usage event must be a JSON object.")
    result = dict(event)
    result.setdefault("schema_version", "1.0")
    result.setdefault("event_id", "evt-" + uuid.uuid4().hex)
    result.setdefault("timestamp_utc", _utc_now())
    result.setdefault("event_type", "note")
    result.setdefault("stage", "other")
    result.setdefault("actor", "unknown")
    if result["schema_version"] != "1.0":
        raise UsageAuditError("Unsupported usage event schema_version.")
    if result["event_type"] not in EVENT_TYPES:
        raise UsageAuditError(f"Unsupported event_type: {result['event_type']!r}.")
    if result["stage"] not in STAGES:
        raise UsageAuditError(f"Unsupported stage: {result['stage']!r}.")
    if result["actor"] not in ACTORS:
        raise UsageAuditError(f"Unsupported actor: {result['actor']!r}.")
    try:
        datetime.fromisoformat(str(result["timestamp_utc"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise UsageAuditError("timestamp_utc must be an ISO-8601 timestamp.") from exc

    if result["event_type"] == "model_usage":
        usage = result.get("token_usage")
        if not isinstance(usage, Mapping):
            raise UsageAuditError("model_usage events require token_usage object.")
        usage = dict(usage)
        usage.setdefault("source", "unavailable")
        usage.setdefault("reasoning_relation", "unknown")
        result.setdefault("context_scope", "unknown")
        if result["context_scope"] not in {"none", "excerpt", "digest", "full_text", "unknown"}:
            raise UsageAuditError("model_usage.context_scope must be none/excerpt/digest/full_text/unknown.")
        if "call_status" in result and result["call_status"] not in {"succeeded", "failed"}:
            raise UsageAuditError("model_usage.call_status must be succeeded or failed.")
        if "duration_ms" in result:
            _validate_nonnegative_int(result["duration_ms"], "duration_ms")
        for field in ("error_type", "usage_extraction_error_type"):
            if field in result and (not isinstance(result[field], str) or not result[field].strip()):
                raise UsageAuditError(f"{field} must be a non-empty exception type name.")
        if usage["source"] not in TOKEN_SOURCES:
            raise UsageAuditError("token_usage.source must be measured, estimated or unavailable.")
        if usage["reasoning_relation"] not in REASONING_RELATIONS:
            raise UsageAuditError("Invalid reasoning_relation.")
        for bucket in TOKEN_BUCKETS:
            usage.setdefault(bucket, None)
            _validate_nonnegative_int(usage[bucket], bucket)
        if usage["source"] == "unavailable" and any(
            usage[bucket] is not None for bucket in TOKEN_BUCKETS
        ):
            raise UsageAuditError(
                "Unavailable token events cannot contain numeric token counts."
            )
        result["token_usage"] = usage

    if result["event_type"] == "document_read":
        read = result.get("document_read")
        if not isinstance(read, Mapping):
            raise UsageAuditError("document_read events require document_read object.")
        read = dict(read)
        sha = read.get("source_sha256")
        if sha is not None and not re.fullmatch(r"[0-9a-f]{64}", str(sha)):
            raise UsageAuditError("document_read.source_sha256 must be a SHA-256 hex digest.")
        if read.get("cache_status") not in {"hit", "miss", "bypass", "unknown"}:
            raise UsageAuditError("document_read.cache_status must be hit/miss/bypass/unknown.")
        if read.get("context_scope", "unknown") not in {
            "none", "excerpt", "digest", "full_text", "unknown"
        }:
            raise UsageAuditError("Invalid document_read.context_scope.")
        for field in ("paragraphs", "characters"):
            _validate_nonnegative_int(read.get(field), f"document_read.{field}")
        for field in ("parsed", "full_text_extracted"):
            if field in read and not isinstance(read[field], bool):
                raise UsageAuditError(f"document_read.{field} must be boolean.")
        result["document_read"] = read

    # Event records intentionally reject raw text payloads and prompt bodies at every depth.
    forbidden = {
        "prompt", "prompt_text", "document_text", "full_text", "request_body",
        "messages", "content", "input_text", "output_text", "body",
    }

    def assert_metadata_only(value: Any) -> None:
        if isinstance(value, Mapping):
            blocked = forbidden.intersection(str(key).casefold() for key in value)
            if blocked:
                raise UsageAuditError(
                    "Usage ledgers store metadata only; remove raw prompt/document content fields: "
                    + ", ".join(sorted(blocked))
                )
            for nested in value.values():
                assert_metadata_only(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_metadata_only(nested)

    assert_metadata_only(result)
    return result


def record_usage_event(
    ledger_path: str | Path, event: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and append one metadata-only JSONL event."""
    target = Path(ledger_path).expanduser().resolve()
    validated = validate_usage_event(event)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(validated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # One append write per record; readers detect and report incomplete/corrupt lines.
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
    return validated


def read_usage_events(ledger_path: str | Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target = Path(ledger_path).expanduser().resolve()
    if not target.is_file():
        return [], []
    events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                events.append(validate_usage_event(payload))
            except (json.JSONDecodeError, UsageAuditError) as exc:
                errors.append({"line": line_number, "error": str(exc)})
    return events, errors


def _bucket_totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for bucket in TOKEN_BUCKETS:
        known = [
            row["token_usage"][bucket]
            for row in events
            if isinstance(row.get("token_usage"), dict)
            and row["token_usage"].get(bucket) is not None
        ]
        result[bucket] = {
            "sum": sum(known) if known else None,
            "observed_events": len(known),
            "missing_events": sum(
                1 for row in events
                if isinstance(row.get("token_usage"), dict)
                and row["token_usage"].get(bucket) is None
            ),
        }
    return result


def build_usage_report(events: list[dict[str, Any]], errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    model_events = [row for row in events if row["event_type"] == "model_usage"]
    document_events = [row for row in events if row["event_type"] == "document_read"]
    by_stage: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_actor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_provider_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in model_events:
        by_stage[row["stage"]].append(row)
        by_actor[row["actor"]].append(row)
        key = f'{row.get("provider") or "unknown"} / {row.get("model") or "unknown"}'
        by_provider_model[key].append(row)

    full_reads: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in document_events:
        read = row["document_read"]
        sha = read.get("source_sha256")
        if sha and read.get("full_text_extracted") is True:
            full_reads[sha].append(row)

    doc_summary = {
        "events": len(document_events),
        "parsed_events": sum(1 for row in document_events if row["document_read"].get("parsed") is True),
        "cache_hits": sum(1 for row in document_events if row["document_read"].get("cache_status") == "hit"),
        "cache_misses": sum(1 for row in document_events if row["document_read"].get("cache_status") == "miss"),
        "full_text_extractions": sum(1 for row in document_events if row["document_read"].get("full_text_extracted") is True),
        "programmatic_read_context_scopes": {
            scope: sum(1 for row in document_events if row["document_read"].get("context_scope", "unknown") == scope)
            for scope in ("none", "excerpt", "digest", "full_text", "unknown")
        },
        "repeated_full_reads_by_sha256": [
            {"source_sha256": sha, "extraction_count": len(rows),
             "event_ids": [row["event_id"] for row in rows]}
            for sha, rows in sorted(full_reads.items()) if len(rows) > 1
        ],
    }

    def group_summary(groups: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
        result = {}
        for key, rows in sorted(groups.items()):
            measured = [row for row in rows if row["token_usage"]["source"] == "measured"]
            estimated = [row for row in rows if row["token_usage"]["source"] == "estimated"]
            unavailable = [row for row in rows if row["token_usage"]["source"] == "unavailable"]
            duration_values = [
                row["duration_ms"] for row in rows
                if isinstance(row.get("duration_ms"), int) and not isinstance(row.get("duration_ms"), bool)
            ]
            result[key] = {
                "events": len(rows),
                "measured_events": len(measured),
                "estimated_events": len(estimated),
                "unavailable_events": len(unavailable),
                "succeeded_calls": sum(1 for row in rows if row.get("call_status") == "succeeded"),
                "failed_calls": sum(1 for row in rows if row.get("call_status") == "failed"),
                "duration_ms": {
                    "sum": sum(duration_values) if duration_values else None,
                    "observed_events": len(duration_values),
                },
                "measured": _bucket_totals(measured),
                "estimated": _bucket_totals(estimated),
            }
        return result

    return {
        "schema_version": "1.0",
        "generated_at_utc": _utc_now(),
        "event_count": len(events),
        "model_usage_event_count": len(model_events),
        "model_context_scopes": {
            scope: sum(1 for row in model_events if row.get("context_scope", "unknown") == scope)
            for scope in ("none", "excerpt", "digest", "full_text", "unknown")
        },
        "model_call_outcomes": {
            outcome: sum(1 for row in model_events if row.get("call_status", "unknown") == outcome)
            for outcome in ("succeeded", "failed", "unknown")
        },
        "usage_extraction_error_events": sum(1 for row in model_events if row.get("usage_extraction_error_type")),
        "model_call_duration_ms": {
            "sum": sum(
                row["duration_ms"] for row in model_events
                if isinstance(row.get("duration_ms"), int) and not isinstance(row.get("duration_ms"), bool)
            ) if any(
                isinstance(row.get("duration_ms"), int) and not isinstance(row.get("duration_ms"), bool)
                for row in model_events
            ) else None,
            "observed_events": sum(
                1 for row in model_events
                if isinstance(row.get("duration_ms"), int) and not isinstance(row.get("duration_ms"), bool)
            ),
        },
        "invalid_lines": errors or [],
        "token_accounting_note": (
            "Token buckets are reported independently. Do not sum input/cache buckets or "
            "reasoning/output until the provider's overlap semantics are explicitly known. "
            "Unavailable values are null, never zero."
        ),
        "model_context_note": (
            "model_usage.context_scope is supplied by the actual model caller and describes its explicit dispatch scope. "
            "documents.programmatic_read_context_scopes describes Co_Reviewer file parsing only; it does not prove model input."
        ),
        "documents": doc_summary,
        "by_stage": group_summary(by_stage),
        "by_actor": group_summary(by_actor),
        "by_provider_model": group_summary(by_provider_model),
        "unallocated_model_usage_events": sum(1 for row in model_events if row["stage"] == "other"),
    }


def estimate_usage_cost(
    events: list[dict[str, Any]], pricing: Mapping[str, Any]
) -> dict[str, Any]:
    """Estimate cost only where explicit pricing and non-overlapping bucket policy exist."""
    rates = pricing.get("rates") if isinstance(pricing, Mapping) else None
    if not isinstance(rates, Mapping):
        raise UsageAuditError("Pricing file requires a rates object.")
    currency_value = pricing.get("currency")
    if not isinstance(currency_value, str) or not re.fullmatch(r"[A-Z]{3}", currency_value):
        raise UsageAuditError("Pricing currency must be an explicit three-letter uppercase code.")
    currency = currency_value
    estimates: list[dict[str, Any]] = []
    unpriced: list[str] = []
    for row in events:
        usage = row.get("token_usage")
        if row.get("event_type") != "model_usage" or not isinstance(usage, dict):
            continue
        if usage.get("source") == "unavailable":
            unpriced.append(row["event_id"])
            continue
        key = f'{row.get("provider") or "unknown"}/{row.get("model") or "unknown"}'
        rate = rates.get(key)
        if not isinstance(rate, Mapping):
            unpriced.append(row["event_id"])
            continue
        if rate.get("accounting_model") != "disjoint_buckets":
            unpriced.append(row["event_id"])
            continue
        if usage.get("reasoning_relation") == "unknown":
            unpriced.append(row["event_id"])
            continue
        total = 0.0
        components: dict[str, float] = {}
        required = {
            "input_tokens": "input_per_million",
            "cache_read_tokens": "cache_read_per_million",
            "cache_write_tokens": "cache_write_per_million",
            "output_tokens": "output_per_million",
        }
        complete = True
        for bucket, rate_key in required.items():
            count = usage.get(bucket)
            unit_rate = rate.get(rate_key)
            if (
                count is None or isinstance(unit_rate, bool)
                or not isinstance(unit_rate, (int, float))
                or not math.isfinite(float(unit_rate)) or float(unit_rate) < 0
            ):
                complete = False
                break
            amount = float(count) * float(unit_rate) / 1_000_000
            components[bucket] = amount
            total += amount
        if usage.get("reasoning_relation") == "separate":
            count = usage.get("reasoning_tokens")
            unit_rate = rate.get("reasoning_per_million")
            if (
                count is None or isinstance(unit_rate, bool)
                or not isinstance(unit_rate, (int, float))
                or not math.isfinite(float(unit_rate)) or float(unit_rate) < 0
            ):
                complete = False
            else:
                amount = float(count) * float(unit_rate) / 1_000_000
                components["reasoning_tokens"] = amount
                total += amount
        if not complete:
            unpriced.append(row["event_id"])
            continue
        estimates.append({
            "event_id": row["event_id"], "provider_model": key,
            "currency": currency, "estimated_cost": round(total, 10),
            "components": components, "source": usage["source"],
        })
    return {
        "schema_version": "1.0", "currency": currency,
        "estimated_events": len(estimates), "unpriced_event_ids": unpriced,
        "estimates": estimates,
        "total_estimated_cost": round(sum(row["estimated_cost"] for row in estimates), 10)
        if estimates else None,
        "note": "Cost is a rate-based estimate, not a billing statement; events without complete pricing/accounting evidence are not assigned zero.",
    }

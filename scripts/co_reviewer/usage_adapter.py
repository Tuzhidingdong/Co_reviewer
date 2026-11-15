# -*- coding: utf-8 -*-
"""Provider-neutral wrapper for recording actual model-call usage.

The caller supplies the real invocation and an explicit provider-specific usage
extractor. This module never inspects prompt text, response text or filesystem
activity to infer tokens or model-context exposure.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from .usage_audit import TOKEN_BUCKETS, TOKEN_SOURCES, UsageAuditError, record_usage_event

T = TypeVar("T")
_CONTEXT_SCOPES = {"none", "excerpt", "digest", "full_text", "unknown"}
_REASONING_RELATIONS = {"included_in_output", "separate", "unknown"}


class UsageAdapterError(RuntimeError):
    """A model call completed but its usage event could not be persisted."""

    def __init__(self, result: Any):
        super().__init__(
            "The model call completed successfully, but its usage event could not be written. "
            "Do not retry the model call blindly; the completed result is available as .result."
        )
        self.result = result
        self.call_completed = True


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise UsageAuditError(f"{field} must be a non-empty string.")
    return value.strip()


def _unavailable_usage(reasoning_relation: str) -> dict[str, Any]:
    return {
        "source": "unavailable",
        "reasoning_relation": reasoning_relation,
        **{bucket: None for bucket in TOKEN_BUCKETS},
    }


def _normalise_usage(raw_usage: Mapping[str, Any] | None, *, source: str, reasoning_relation: str) -> dict[str, Any]:
    if raw_usage is None:
        return _unavailable_usage(reasoning_relation)
    if not isinstance(raw_usage, Mapping):
        raise UsageAuditError("The usage extractor must return a mapping or None.")
    unknown = set(raw_usage) - set(TOKEN_BUCKETS)
    if unknown:
        raise UsageAuditError("Unsupported token field(s): " + ", ".join(sorted(str(x) for x in unknown)))
    values = {bucket: raw_usage.get(bucket) for bucket in TOKEN_BUCKETS}
    if not any(value is not None for value in values.values()):
        return _unavailable_usage(reasoning_relation)
    if source not in TOKEN_SOURCES - {"unavailable"}:
        raise UsageAuditError("token_source must be measured or estimated when counts are supplied.")
    return {"source": source, "reasoning_relation": reasoning_relation, **values}


def invoke_with_usage(
    ledger_path: str | Path,
    invoke: Callable[[], T],
    *,
    stage: str,
    actor: str,
    provider: str,
    model: str,
    context_scope: str = "unknown",
    reasoning_relation: str = "unknown",
    token_source: str = "measured",
    usage_extractor: Callable[[T], Mapping[str, Any] | None] | None = None,
    job_id: str | None = None,
    session_id: str | None = None,
) -> T:
    """Invoke one model call and append a metadata-only usage event.

    The extractor maps explicit provider usage metadata to canonical token buckets.
    Context scope is supplied by the caller, never inferred from reads or prompt size.
    """
    stage = _nonempty(stage, "stage")
    actor = _nonempty(actor, "actor")
    provider = _nonempty(provider, "provider")
    model = _nonempty(model, "model")
    if context_scope not in _CONTEXT_SCOPES:
        raise UsageAuditError("Invalid explicit context_scope.")
    if reasoning_relation not in _REASONING_RELATIONS:
        raise UsageAuditError("Invalid reasoning_relation.")
    if token_source not in {"measured", "estimated"}:
        raise UsageAuditError("token_source must be measured or estimated.")
    if job_id is not None:
        job_id = _nonempty(job_id, "job_id")
    if session_id is not None:
        session_id = _nonempty(session_id, "session_id")

    started = time.perf_counter()
    try:
        result = invoke()
    except Exception as call_error:
        event = {
            "event_type": "model_usage", "stage": stage, "actor": actor,
            "provider": provider, "model": model, "context_scope": context_scope,
            "call_status": "failed", "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
            "error_type": type(call_error).__name__,
            "token_usage": _unavailable_usage(reasoning_relation),
        }
        if job_id is not None: event["job_id"] = job_id
        if session_id is not None: event["session_id"] = session_id
        try:
            record_usage_event(ledger_path, event)
        except Exception as recording_error:
            try: setattr(call_error, "usage_recording_error_type", type(recording_error).__name__)
            except Exception: pass
        raise

    extractor_error_type = None
    try:
        raw_usage = usage_extractor(result) if usage_extractor is not None else None
        token_usage = _normalise_usage(raw_usage, source=token_source, reasoning_relation=reasoning_relation)
    except Exception as exc:
        token_usage = _unavailable_usage(reasoning_relation)
        extractor_error_type = type(exc).__name__
    event = {
        "event_type": "model_usage", "stage": stage, "actor": actor,
        "provider": provider, "model": model, "context_scope": context_scope,
        "call_status": "succeeded", "duration_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "token_usage": token_usage,
    }
    if job_id is not None: event["job_id"] = job_id
    if session_id is not None: event["session_id"] = session_id
    if extractor_error_type is not None: event["usage_extraction_error_type"] = extractor_error_type
    try:
        record_usage_event(ledger_path, event)
    except Exception as exc:
        raise UsageAdapterError(result) from exc
    return result

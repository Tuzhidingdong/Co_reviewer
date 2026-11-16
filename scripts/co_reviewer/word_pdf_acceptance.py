# -*- coding: utf-8 -*-
"""Create a synthetic, hash-bound RevisionLedger for Word COM/PDF acceptance.

The fixture consumes only the isolated five-target Word compatibility DOCX
created by create_word_compatibility_fixture.py and already verified after a
Microsoft Word save/reopen. It is never suitable for real manuscript data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .identity import sha256_file
from .revision_models import (
    Patch,
    PatchOperation,
    ResponseUnit,
    RevisionDocument,
    RevisionLedger,
)


def _load_verified_roundtrip_report(path: Path) -> dict[str, Any]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read post-Word verification report {path}: {exc}") from exc
    audit = report.get("untracked_text_audit")
    if (
        report.get("passed") is not True
        or report.get("targets_total") != 5
        or report.get("targets_verified") != 5
        or report.get("targets_failed") != 0
        or not isinstance(audit, dict)
        or audit.get("passed") is not True
        or audit.get("violations") != []
    ):
        raise ValueError(
            "Word/PDF acceptance fixture requires a passing five-target post-Word report "
            "with zero untracked-text violations."
        )
    return report


def build_word_pdf_acceptance_ledger(
    fixture_dir: str | Path,
    ledger_path: str | Path,
) -> dict[str, Any]:
    """Create a minimal valid verified RevisionLedger for a real Word COM export."""
    root = Path(fixture_dir).expanduser().resolve()
    source = root / "word_compatibility_input.docx"
    marked = root / "word_compatibility_revised.docx"
    verification_path = root / "word_compatibility_verification_after_word.json"
    ledger_file = Path(ledger_path).expanduser().resolve()

    for label, path in (
        ("synthetic source DOCX", source),
        ("Word-saved/reopened Marked DOCX", marked),
        ("post-Word verification report", verification_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"Required {label} is missing: {path}")
    _load_verified_roundtrip_report(verification_path)

    if source.suffix.casefold() != ".docx" or marked.suffix.casefold() != ".docx":
        raise ValueError("Synthetic Word acceptance inputs must both be DOCX files.")
    if source.resolve() == marked.resolve():
        raise ValueError("Synthetic source and Marked DOCX paths must be different.")
    if ledger_file.exists():
        raise FileExistsError(f"Refusing to overwrite an existing acceptance RevisionLedger: {ledger_file}")
    ledger_file.parent.mkdir(parents=True, exist_ok=True)

    source_sha = sha256_file(source)
    marked_sha = sha256_file(marked)
    comment_id = "WORD-QA-01-C1"
    patch_id = "WORD-QA-COM-P1"
    task_id = "WORD-QA-COM-T1"
    operation_id = "WORD-QA-COM-OP1"
    response_unit_id = "RU-WORD-QA-COM-01"

    operation = PatchOperation.from_mapping({
        "operation_id": operation_id,
        "document": "Manuscript",
        "input_file": str(source),
        "output_file": str(marked),
        "part": "word/document.xml",
        "p_idx": 0,
        "find": "0.5 M",
        "replace": "1.0 M",
        "location": "Synthetic body paragraph 1",
        "source_sha256": source_sha,
        "evidence_ids": ["WORD-QA-E1"],
        "source_comment_ids": [comment_id],
        "location_phrase": "1.0 M",
    })
    patch = Patch(
        patch_id=patch_id,
        task_id=task_id,
        reviewer="Synthetic Word Compatibility Fixture",
        comment="Synthetic acceptance only: verify Word COM final pagination and PDF corroboration.",
        proposed_response="The synthetic body target was mapped in the Word-generated final PDF.",
        operations=[operation],
        status="verified",
        preflight_fingerprint=hashlib.sha256(b"synthetic-word-com-preflight").hexdigest(),
        preflight_report_id="WORD-QA-COM-PREFLIGHT",
        approval_fingerprint=hashlib.sha256(b"synthetic-word-com-approval").hexdigest(),
        apply_report_ids=["WORD-QA-COM-APPLY"],
        verification_report_id="WORD-QA-COM-VERIFY-POST-WORD",
        verified_output_hashes=[{"path": str(marked), "sha256": marked_sha}],
    )
    document = RevisionDocument(
        document_id="word-compatibility-manuscript",
        revision_id="word-compatibility-round",
        role="Manuscript",
        input_file=str(source),
        output_file=str(marked),
        source_sha256=source_sha,
        output_sha256=marked_sha,
    )
    response_unit = ResponseUnit(
        response_unit_id=response_unit_id,
        reviewer="Synthetic Word Compatibility Fixture",
        comment_id=comment_id,
        comment_text="Synthetic acceptance only: confirm the final Word page and line for the revised body phrase.",
        response_text="The revised body phrase was located in the Word-generated final PDF.",
        linked_task_ids=[task_id],
        linked_patch_ids=[patch_id],
        linked_operation_ids=[operation_id],
        disposition="revised",
        location_requirement="required",
        status="verified",
        semantic_review_confirmed=True,
        verification_report_id="WORD-QA-COM-SEMANTIC-FIXTURE-ACK",
    )
    ledger = RevisionLedger(
        revision_round_id="round-word-com-pdf-acceptance",
        documents=[document],
        patches=[patch],
        response_units=[response_unit],
    )
    ledger.validate_or_raise()

    payload = json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2) + "\n"
    try:
        with ledger_file.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
    except FileExistsError:
        raise FileExistsError(
            f"Refusing to overwrite an existing acceptance RevisionLedger: {ledger_file}"
        )
    return {
        "ledger_file": str(ledger_file),
        "revision_round_id": ledger.revision_round_id,
        "patch_id": patch_id,
        "operation_id": operation_id,
        "response_unit_id": response_unit_id,
        "source_sha256": source_sha,
        "marked_sha256": marked_sha,
        "post_word_verification_report": str(verification_path),
    }

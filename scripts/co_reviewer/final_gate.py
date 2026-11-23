# -*- coding: utf-8 -*-
"""Final delivery hard gate engine for Co_Reviewer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from co_reviewer.docx_quality import DocxQualityError, audit_docx_quality
from co_reviewer.instruction_coverage import validate_instruction_coverage

_WD_EXPORT_FORMAT_PDF = 17
_CHECKLIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(?:\[[ xX ]\]\s*)?\S+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid_pdf(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 8:
        return False
    with path.open("rb") as handle:
        return handle.read(5) == b"%PDF-"


_DOCUMENT_ROLE_PROFILES = {
    "marked": "marked-manuscript",
    "marked-manuscript": "marked-manuscript",
    "clean": "clean-manuscript",
    "clean-manuscript": "clean-manuscript",
    "word-final-clean": "word-final-clean",
    "cover-letter": "response-letter",
    "response-letter": "response-letter",
    "si": "generic",
    "supplementary-information": "generic",
    "generic": "generic",
}


def _infer_quality_profile(
    docx_path: Path,
    explicit_profile: str | None,
    explicit_role: str | None = None,
) -> str:
    """Resolve QA profile from explicit document role before legacy filename hints."""
    if explicit_profile:
        return explicit_profile
    if explicit_role:
        role = explicit_role.strip().lower()
        if role not in _DOCUMENT_ROLE_PROFILES:
            raise ValueError(f"Unsupported document role: {explicit_role!r}")
        return _DOCUMENT_ROLE_PROFILES[role]
    stem = docx_path.stem.lower()
    if "marked" in stem:
        return "marked-manuscript"
    if "response" in stem:
        return "response-letter"
    if "clean" in stem:
        return "clean-manuscript"
    return "generic"


def export_docx_to_pdf_word_com(docx_path: str | Path, pdf_path: str | Path) -> dict[str, Any]:
    """Export a DOCX file to PDF using an isolated Microsoft Word COM instance."""
    source = Path(docx_path).expanduser().resolve()
    target = Path(pdf_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".docx":
        return {
            "status": "FAIL",
            "pdf_file": str(target),
            "error": f"Source DOCX does not exist or is not a .docx file: {source}",
        }
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        return {
            "status": "FAIL",
            "pdf_file": str(target),
            "error": f"Microsoft Word COM dependencies (pywin32) are unavailable: {exc}",
        }

    pythoncom.CoInitialize()
    word = None
    document = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        word.Options.SaveNormalPrompt = False
        word.Options.UpdateLinksAtOpen = False

        document = word.Documents.Open(
            str(source),
            ReadOnly=True,
            AddToRecentFiles=False,
            ConfirmConversions=False,
        )
        document.ExportAsFixedFormat(
            OutputFileName=str(target),
            ExportFormat=_WD_EXPORT_FORMAT_PDF,
        )
    except Exception as exc:
        return {
            "status": "FAIL",
            "pdf_file": str(target),
            "error": f"Word COM PDF export failed for {source}: {exc}",
        }
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if not _is_valid_pdf(target):
        return {
            "status": "FAIL",
            "pdf_file": str(target),
            "error": f"Exported PDF is missing, empty, or lacks %PDF- signature: {target}",
        }
    return {
        "status": "PASS",
        "pdf_file": str(target),
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _count_checklist_items(checklist_path: Path) -> int:
    text = checklist_path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    item_count = sum(1 for line in lines if _CHECKLIST_ITEM_RE.match(line))
    if item_count > 0:
        return item_count
    non_heading = [line for line in lines if not line.startswith("#") and not line.startswith("<!--")]
    return len(non_heading)


def _count_skipped_format_ops(container: dict[str, Any]) -> int:
    count = 0
    for key in ("format_operations", "format_changes"):
        ops = container.get(key)
        if isinstance(ops, list):
            for op in ops:
                if isinstance(op, dict):
                    op_status = str(op.get("status", "")).strip().lower()
                    if op_status in {"skipped", "not_applied"}:
                        count += 1
    return count


def collect_fallback_metrics(
    plan: dict[str, Any] | None,
    apply_reports: list[dict[str, Any]] | None,
    round_root: str | Path | None,
) -> dict[str, Any]:
    """Count manual fallbacks, blocked items, and skipped FormatChange operations."""
    manual_fallback_count = 0
    blocked_count = 0
    skipped_format_ops = 0
    details: list[str] = []

    if isinstance(plan, dict):
        for item in plan.get("revision_items", []):
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "<unknown>")
            item_status = str(item.get("status", "")).strip().lower()
            if item_status in {"manual_fallback", "manual_required_fallback"}:
                manual_fallback_count += 1
                details.append(f"plan item {item_id} has status={item_status}")
            elif item_status in {"blocked", "failed"}:
                blocked_count += 1
                details.append(f"plan item {item_id} has status={item_status}")

            for target in item.get("targets", []):
                if not isinstance(target, dict):
                    continue
                t_status = str(target.get("status", "")).strip().lower()
                if t_status in {"manual_fallback", "manual_required_fallback"}:
                    manual_fallback_count += 1
                    details.append(f"plan target in {item_id} has status={t_status}")
                elif t_status in {"blocked", "failed"}:
                    blocked_count += 1
                    details.append(f"plan target in {item_id} has status={t_status}")
                skipped = _count_skipped_format_ops(target)
                if skipped:
                    skipped_format_ops += skipped
                    details.append(f"plan target in {item_id} has {skipped} skipped format op(s)")

    if isinstance(apply_reports, list):
        for report in apply_reports:
            if not isinstance(report, dict):
                continue
            for entry in report.get("results", []):
                if not isinstance(entry, dict):
                    continue
                item_id = entry.get("revision_item_id", "<unknown>")
                e_status = str(entry.get("status", "")).strip().lower()
                if e_status in {"manual_fallback", "manual_required_fallback"}:
                    manual_fallback_count += 1
                    details.append(f"apply result {item_id} has status={e_status}")
                elif e_status in {"blocked", "failed"}:
                    blocked_count += 1
                    details.append(f"apply result {item_id} has status={e_status}")
                skipped = _count_skipped_format_ops(entry)
                if skipped:
                    skipped_format_ops += skipped
                    details.append(f"apply result {item_id} has {skipped} skipped format op(s)")

    if round_root is not None:
        root = Path(round_root).expanduser().resolve()
        manual_dir = root / "manual_suggestions"
        if manual_dir.is_dir():
            for json_file in sorted(manual_dir.glob("*.json")):
                manual_fallback_count += 1
                details.append(f"manual suggestion artifact: {json_file.name}")
        checklist = root / "manual_fallback_checklist.md"
        if checklist.is_file():
            checklist_count = _count_checklist_items(checklist)
            if checklist_count > 0:
                manual_fallback_count += checklist_count
                details.append(f"manual_fallback_checklist.md contains {checklist_count} item(s)")

    return {
        "manual_fallback_count": manual_fallback_count,
        "blocked_count": blocked_count,
        "skipped_format_ops": skipped_format_ops,
        "details": details,
    }


def _resolve_delivered_docs(
    docs: list[str | Path] | None,
    plan: dict[str, Any] | None,
    round_root: str | Path | None,
) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        norm = path.expanduser().resolve()
        if norm not in seen:
            seen.add(norm)
            resolved.append(norm)

    if docs:
        for item in docs:
            _add(Path(item))
        return resolved

    if isinstance(plan, dict):
        for item in plan.get("revision_items", []):
            if not isinstance(item, dict):
                continue
            for target in item.get("targets", []):
                if isinstance(target, dict) and target.get("output_file"):
                    _add(Path(str(target["output_file"])))
        if resolved:
            return resolved

    if round_root is not None:
        root = Path(round_root).expanduser().resolve()
        if root.is_dir():
            for candidate in sorted(root.glob("*.docx")):
                if not candidate.name.startswith("~$"):
                    _add(candidate)

    return resolved


def _is_format_change_entry(entry: dict[str, Any]) -> bool:
    find_text = entry.get("find")
    replace_text = entry.get("replace")
    has_format = bool(
        entry.get("format_operations")
        or entry.get("format_changes")
        or entry.get("format_change")
    )
    return find_text == replace_text and has_format


def _count_applied_edits(
    plan: dict[str, Any] | None,
    apply_reports: list[dict[str, Any]] | None,
    verify_report: dict[str, Any] | None,
) -> tuple[int, int]:
    text_edits = 0
    format_changes = 0

    if isinstance(apply_reports, list) and apply_reports:
        for report in apply_reports:
            if not isinstance(report, dict):
                continue
            for entry in report.get("results", []):
                if not isinstance(entry, dict) or entry.get("status") != "applied":
                    continue
                if _is_format_change_entry(entry):
                    format_changes += 1
                else:
                    text_edits += 1
        return text_edits, format_changes

    if isinstance(plan, dict):
        for item in plan.get("revision_items", []):
            if not isinstance(item, dict):
                continue
            if item.get("status") not in {"applied", "verified"}:
                continue
            for target in item.get("targets", []):
                if not isinstance(target, dict):
                    continue
                if _is_format_change_entry(target):
                    format_changes += 1
                else:
                    text_edits += 1
        return text_edits, format_changes

    if isinstance(verify_report, dict):
        for entry in verify_report.get("results", []):
            if not isinstance(entry, dict) or entry.get("status") != "verified":
                continue
            if _is_format_change_entry(entry):
                format_changes += 1
            else:
                text_edits += 1

    return text_edits, format_changes


def evaluate_final_gate(
    plan: dict[str, Any] | None = None,
    verify_report: dict[str, Any] | None = None,
    apply_reports: list[dict[str, Any]] | None = None,
    docs: list[str | Path] | None = None,
    round_root: str | Path | None = None,
    waive_fallback_reason: str | None = None,
    quality_profile: str | None = None,
    skip_word_com: bool = False,
    pdf_exporter: Callable[[Path, Path], dict[str, Any]] | None = None,
    instruction_coverage: dict[str, Any] | None = None,
    instruction_coverage_approval: dict[str, Any] | None = None,
    document_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate all Final Delivery Hard Gate checks and return structured report."""
    blockers: list[str] = []

    # Check 0: Original instruction coverage. This gate is intentionally
    # independent of the compiled plan so requirements excluded before
    # compilation remain visible and block a false READY.
    instruction_coverage_report = validate_instruction_coverage(
        instruction_coverage,
        instruction_coverage_approval,
        plan,
    )
    instruction_coverage_status = "PASS" if instruction_coverage_report["passed"] else "FAIL"
    if instruction_coverage_status != "PASS":
        blockers.extend(
            "Instruction coverage gate failed: " + str(error)
            for error in instruction_coverage_report.get("errors", [])
        )

    # Check A: Verify Gate
    if not isinstance(verify_report, dict):
        verify_gate_status = "FAIL"
        targets_failed = 0
        untracked_violations: list[Any] = []
        blockers.append("Verify gate failed: verify_report is missing.")
    else:
        targets_failed = int(
            verify_report.get("targets_failed", verify_report.get("failed", 0)) or 0
        )
        failed_alias = int(verify_report.get("failed", 0) or 0)
        untracked_audit = verify_report.get("untracked_text_audit") or {}
        untracked_violations = list(untracked_audit.get("violations") or [])
        untracked_passed = untracked_audit.get("passed", len(untracked_violations) == 0)
        passed_flag = verify_report.get("passed") is True

        if (
            passed_flag
            and targets_failed == 0
            and failed_alias == 0
            and len(untracked_violations) == 0
            and bool(untracked_passed)
        ):
            verify_gate_status = "PASS"
        else:
            verify_gate_status = "FAIL"
            blockers.append(
                "Verify gate failed: "
                f"passed={verify_report.get('passed')}, "
                f"targets_failed={max(targets_failed, failed_alias)}, "
                f"untracked_text_violations={len(untracked_violations)}."
            )

    # Check B: DOCX Quality & Word COM PDF Gate
    delivered_docs = _resolve_delivered_docs(docs, plan, round_root)
    doc_results: list[dict[str, Any]] = []
    if not delivered_docs:
        quality_status = "FAIL"
        pdf_render_status = "FAIL"
        blockers.append("DOCX quality & PDF gate failed: no delivered DOCX files found.")
    else:
        exporter = pdf_exporter or export_docx_to_pdf_word_com
        any_quality_fail = False
        any_pdf_fail = False
        any_pdf_skipped = False

        for docx_path in delivered_docs:
            role = None
            if document_roles:
                role = document_roles.get(str(docx_path))
                if role is None:
                    role = document_roles.get(docx_path.name)
            profile = _infer_quality_profile(docx_path, quality_profile, role)
            pdf_path = docx_path.with_suffix(".pdf")
            if not docx_path.is_file():
                any_quality_fail = True
                any_pdf_fail = True
                blockers.append(f"Delivered DOCX does not exist: {docx_path}")
                doc_results.append({
                    "docx_file": str(docx_path),
                    "profile": profile,
                    "quality_status": "FAIL",
                    "quality_failures": 1,
                    "pdf_render_status": "FAIL",
                    "pdf_file": str(pdf_path),
                    "errors": [f"Missing DOCX file: {docx_path}"],
                })
                continue

            doc_errors: list[str] = []
            pdf_for_quality: Path | None = None
            if skip_word_com:
                if pdf_path.exists():
                    if _is_valid_pdf(pdf_path):
                        doc_pdf_status = "PASS"
                        pdf_for_quality = pdf_path
                    else:
                        doc_pdf_status = "FAIL"
                        any_pdf_fail = True
                        msg = f"Existing PDF is invalid or empty: {pdf_path}"
                        doc_errors.append(msg)
                        blockers.append(msg)
                else:
                    doc_pdf_status = "SKIPPED_TEST_ONLY"
                    any_pdf_skipped = True
            else:
                try:
                    export_res = exporter(docx_path, pdf_path)
                except Exception as exc:
                    export_res = {"status": "FAIL", "error": str(exc)}
                if (
                    isinstance(export_res, dict)
                    and export_res.get("status") == "PASS"
                    and _is_valid_pdf(pdf_path)
                ):
                    doc_pdf_status = "PASS"
                    pdf_for_quality = pdf_path
                else:
                    doc_pdf_status = "FAIL"
                    any_pdf_fail = True
                    err_detail = (
                        export_res.get("error")
                        if isinstance(export_res, dict) and export_res.get("error")
                        else f"Invalid or missing PDF output: {pdf_path}"
                    )
                    doc_errors.append(str(err_detail))
                    blockers.append(f"Word COM PDF export failed for {docx_path.name}: {err_detail}")

            try:
                qa_report = audit_docx_quality(
                    docx_path,
                    profile=profile,
                    pdf_path=pdf_for_quality,
                )
                qa_failures = int(qa_report.get("summary", {}).get("failures", 0))
                qa_status = qa_report.get("status", "FAIL")
                if qa_status == "FAIL" or qa_failures > 0:
                    doc_quality_status = "FAIL"
                    any_quality_fail = True
                    failed_checks = [
                        f"{c['id']}: {c['summary']}"
                        for c in qa_report.get("checks", [])
                        if c.get("status") == "FAIL"
                    ]
                    doc_errors.extend(failed_checks)
                    blockers.append(
                        f"DOCX quality audit failed for {docx_path.name} "
                        f"({qa_failures} failure(s)): {'; '.join(failed_checks)}"
                    )
                else:
                    doc_quality_status = "PASS"
            except (DocxQualityError, OSError, ValueError) as exc:
                doc_quality_status = "FAIL"
                qa_failures = 1
                any_quality_fail = True
                doc_errors.append(str(exc))
                blockers.append(f"DOCX quality audit error for {docx_path.name}: {exc}")

            doc_results.append({
                "docx_file": str(docx_path),
                "profile": profile,
                "quality_status": doc_quality_status,
                "quality_failures": qa_failures,
                "pdf_render_status": doc_pdf_status,
                "pdf_file": str(pdf_path),
                "errors": doc_errors,
            })

        quality_status = "FAIL" if any_quality_fail else "PASS"
        if any_pdf_fail:
            pdf_render_status = "FAIL"
        elif any_pdf_skipped:
            pdf_render_status = "SKIPPED_TEST_ONLY"
        else:
            pdf_render_status = "PASS"

    # Check C: Zero-Silent-Fallback Gate
    fallback_metrics = collect_fallback_metrics(plan, apply_reports, round_root)
    total_fallbacks = (
        fallback_metrics["manual_fallback_count"]
        + fallback_metrics["blocked_count"]
        + fallback_metrics["skipped_format_ops"]
    )
    waive_reason = (waive_fallback_reason or "").strip()
    waived = False
    if total_fallbacks > 0:
        if waive_reason:
            fallback_gate_status = "WAIVED"
            waived = True
        else:
            fallback_gate_status = "FAIL"
            blockers.append(
                "Zero-Silent-Fallback gate failed: "
                f"manual_fallback={fallback_metrics['manual_fallback_count']}, "
                f"blocked={fallback_metrics['blocked_count']}, "
                f"skipped_format_ops={fallback_metrics['skipped_format_ops']}. "
                "Provide --waive-fallback-reason with explicit user approval to waive."
            )
    else:
        fallback_gate_status = "PASS"

    input_items = (
        len(plan.get("revision_items", []))
        if isinstance(plan, dict)
        else int((verify_report or {}).get("targets_total", 0) or 0)
    )
    applied_text_edits, applied_format_changes = _count_applied_edits(
        plan, apply_reports, verify_report
    )
    reconciliation = {
        "input_items": input_items,
        "applied_text_edits": applied_text_edits,
        "applied_format_changes": applied_format_changes,
        "failed": max(
            targets_failed,
            int((verify_report or {}).get("failed", 0) or 0) if isinstance(verify_report, dict) else 0,
        ),
        "manual_fallback": fallback_metrics["manual_fallback_count"],
        "blocked_count": fallback_metrics["blocked_count"],
        "skipped_format_ops": fallback_metrics["skipped_format_ops"],
        "quality_status": quality_status,
        "pdf_render_status": pdf_render_status,
        "instruction_coverage_status": instruction_coverage_status,
    }

    verdict = (
        "READY"
        if (
            instruction_coverage_status == "PASS"
            and verify_gate_status == "PASS"
            and quality_status == "PASS"
            and pdf_render_status in {"PASS", "SKIPPED_TEST_ONLY"}
            and fallback_gate_status in {"PASS", "WAIVED"}
            and not blockers
        )
        else "BLOCKED"
    )

    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "gates": {
            "instruction_coverage": instruction_coverage_status,
            "verify": verify_gate_status,
            "docx_quality": quality_status,
            "word_com_pdf": pdf_render_status,
            "zero_silent_fallback": fallback_gate_status,
        },
        "waived": waived,
        "waive_fallback_reason": waive_reason if waived else None,
        "reconciliation": reconciliation,
        "fallback_details": fallback_metrics["details"],
        "instruction_coverage": instruction_coverage_report,
        "documents": doc_results,
        "blockers": blockers,
    }


def render_final_gate_markdown(report: dict[str, Any]) -> str:
    """Render the Final Delivery Hard Gate report as Markdown."""
    rec = report.get("reconciliation", {})
    lines = [
        "# Final Delivery Hard Gate Report",
        "",
        f"**Verdict:** {report.get('verdict', 'BLOCKED')}",
        f"**Generated (UTC):** {report.get('generated_at_utc', '')}",
        "",
        "## Quantitative Reconciliation",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| input_items | {rec.get('input_items', 0)} |",
        f"| applied_text_edits | {rec.get('applied_text_edits', 0)} |",
        f"| applied_format_changes | {rec.get('applied_format_changes', 0)} |",
        f"| failed | {rec.get('failed', 0)} |",
        f"| manual_fallback | {rec.get('manual_fallback', 0)} |",
        f"| blocked_count | {rec.get('blocked_count', 0)} |",
        f"| skipped_format_ops | {rec.get('skipped_format_ops', 0)} |",
        f"| quality_status | {rec.get('quality_status', 'FAIL')} |",
        f"| pdf_render_status | {rec.get('pdf_render_status', 'FAIL')} |",
        f"| instruction_coverage_status | {rec.get('instruction_coverage_status', 'FAIL')} |",
        "",
        "## Original Instruction Coverage",
        "",
        f"- Status: {report.get('gates', {}).get('instruction_coverage', 'FAIL')}",
        f"- Requirements: {report.get('instruction_coverage', {}).get('requirement_count', 0)}",
        f"- Planned: {report.get('instruction_coverage', {}).get('planned_count', 0)}",
        f"- No change / not applicable: {report.get('instruction_coverage', {}).get('no_change_count', 0)}",
        f"- Manually completed: {report.get('instruction_coverage', {}).get('manual_completed_count', 0)}",
        f"- Unresolved: {report.get('instruction_coverage', {}).get('unresolved_count', 0)}",
        "",
        "## Document Quality & PDF Render",
        "",
        "| Document | Profile | Quality Status | Quality Failures | PDF Render Status | PDF File |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for doc in report.get("documents", []):
        lines.append(
            f"| {doc.get('docx_file', '')} | {doc.get('profile', '')} | "
            f"{doc.get('quality_status', '')} | {doc.get('quality_failures', 0)} | "
            f"{doc.get('pdf_render_status', '')} | {doc.get('pdf_file', '')} |"
        )
    if not report.get("documents"):
        lines.append("| *(none)* | - | FAIL | 0 | FAIL | - |")

    lines.extend(["", "## Blockers / Waivers", ""])
    if report.get("waived"):
        lines.append(f"- **Waiver Recorded:** {report.get('waive_fallback_reason')}")
    blockers = report.get("blockers") or []
    if blockers:
        for blocker in blockers:
            lines.append(f"- **Blocker:** {blocker}")
    elif not report.get("waived"):
        lines.append("- None.")

    return "\n".join(lines) + "\n"


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_final_gate_reports(
    report: dict[str, Any],
    out_json: str | Path,
    out_md: str | Path,
) -> None:
    """Write both JSON and Markdown final gate reports atomically."""
    json_path = Path(out_json).expanduser().resolve()
    md_path = Path(out_md).expanduser().resolve()
    _write_text_atomic(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    _write_text_atomic(md_path, render_final_gate_markdown(report))

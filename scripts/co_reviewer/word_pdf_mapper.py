# -*- coding: utf-8 -*-
"""Word-native final pagination, PDF export and ResponseUnit location mapping.

This module is intentionally Windows-only at execution time. It never edits the
ledger's Marked DOCX or Working Clean View source: Word opens the latter read-only
and saves a separate final Clean DOCX before exporting its PDF.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .identity import sha256_file
from .revision_models import RevisionLedger, RevisionModelError


class WordPdfMappingError(RuntimeError):
    """Raised when Word/PDF evidence is missing, ambiguous or inconsistent."""


@dataclass(frozen=True)
class MappingJob:
    response_unit_id: str
    patch_id: str
    operation_id: str
    document_role: str
    phrase: str
    source_clean_docx: str


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise_pdf_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _validate_mapping_phrase(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WordPdfMappingError("A Patch replacement must contain non-empty surviving text.")
    if len(value) > 255:
        raise WordPdfMappingError(
            "Word's exact Find operation is limited to 255 characters; split this Patch target "
            "into a shorter, independently verifiable phrase before mapping."
        )
    if any(char in value for char in "\r\n\t"):
        raise WordPdfMappingError(
            "Automatic mapping currently requires a single-paragraph phrase without tabs."
        )
    return value


def build_mapping_jobs(ledger: RevisionLedger) -> dict[str, list[MappingJob]]:
    """Resolve each verified ResponseUnit to exact text in the current Clean View."""
    if not isinstance(ledger, RevisionLedger):
        raise WordPdfMappingError("A validated RevisionLedger is required.")
    documents_by_role: dict[str, Any] = {}
    for document in ledger.documents:
        if document.role in documents_by_role:
            raise WordPdfMappingError(
                f"More than one current RevisionDocument has role {document.role!r}; "
                "resolve document-role identity before line mapping."
            )
        documents_by_role[document.role] = document

    patches_by_id = {patch.patch_id: patch for patch in ledger.patches}
    jobs: dict[str, list[MappingJob]] = {}
    for unit in ledger.response_units:
        if unit.status != "verified" or unit.semantic_review_confirmed is not True:
            raise WordPdfMappingError(
                f"ResponseUnit {unit.response_unit_id} must pass semantic review before Word/PDF mapping."
            )
        if not unit.linked_patch_ids:
            raise WordPdfMappingError(
                f"ResponseUnit {unit.response_unit_id} has no linked Patch; "
                "a surviving manuscript target cannot be inferred."
            )
        unit_job_count = 0
        for patch_id in unit.linked_patch_ids:
            patch = patches_by_id.get(patch_id)
            if patch is None:
                raise WordPdfMappingError(
                    f"ResponseUnit {unit.response_unit_id} links unknown Patch {patch_id}."
                )
            if patch.status != "verified":
                raise WordPdfMappingError(
                    f"Patch {patch_id} is {patch.status}, not verified; final lines are blocked."
                )
            for operation in patch.operations:
                if operation.part != "word/document.xml":
                    raise WordPdfMappingError(
                        f"Patch {patch_id}/{operation.operation_id} targets {operation.part}; "
                        "automatic mapping currently supports the main document body only."
                    )
                phrase = _validate_mapping_phrase(operation.replace)
                document = documents_by_role.get(operation.document)
                if document is None:
                    raise WordPdfMappingError(
                        f"No current RevisionDocument is registered for role {operation.document!r}."
                    )
                clean_path = document.working_clean_file
                if not clean_path or not document.working_clean_sha256:
                    raise WordPdfMappingError(
                        f"{operation.document} has no hash-bound Working Clean View. "
                        "Run create-working-clean-views after the final Patch batch."
                    )
                source = Path(clean_path).expanduser().resolve()
                if not source.is_file():
                    raise WordPdfMappingError(
                        f"Working Clean View does not exist: {source}"
                    )
                if sha256_file(source) != document.working_clean_sha256:
                    raise WordPdfMappingError(
                        f"Working Clean View changed after ledger validation: {source}"
                    )
                jobs.setdefault(operation.document, []).append(MappingJob(
                    response_unit_id=unit.response_unit_id,
                    patch_id=patch.patch_id,
                    operation_id=operation.operation_id,
                    document_role=operation.document,
                    phrase=phrase,
                    source_clean_docx=str(source),
                ))
                unit_job_count += 1
        if unit_job_count == 0:
            raise WordPdfMappingError(
                f"ResponseUnit {unit.response_unit_id} has no executable location target."
            )
    if not jobs:
        raise WordPdfMappingError("No verified Patch text is available for final location mapping.")
    return jobs


def _read_word_position(
    document: Any,
    start: int,
    end: int,
    *,
    label: str,
) -> tuple[int, int, int, int]:
    """Read physical PDF page and continuous line number for one matched phrase.

    WdInformation constants:
      3  = wdActiveEndPageNumber
      10 = wdFirstCharacterLineNumber
      12 = wdWithInTable (NOT a line-number constant)
    """
    if end <= start:
        raise WordPdfMappingError(f"Word returned an empty match for {label}.")
    first_character = document.Range(start, min(start + 1, end))
    last_character = document.Range(max(start, end - 1), end)
    if bool(first_character.Information(12)) or bool(last_character.Information(12)):
        raise WordPdfMappingError(
            f"{label} is inside a table. Word line numbering does not provide a reliable "
            "body line reference for table cells; use a separately reviewed table locator."
        )
    page_start = int(first_character.Information(3))  # wdActiveEndPageNumber
    page_end = int(last_character.Information(3))
    line_start = int(first_character.Information(10))  # wdFirstCharacterLineNumber
    line_end = int(last_character.Information(10))  # same constant on the last character
    if min(page_start, page_end, line_start, line_end) < 1:
        raise WordPdfMappingError(
            f"Word did not return valid page/line numbers for {label}; "
            "check print-layout view, section line-number settings and target placement."
        )
    if page_start != page_end:
        raise WordPdfMappingError(
            f"{label} crosses pages {page_start}-{page_end}. "
            "The current ResponseLocation schema requires one page per target."
        )
    if line_end < line_start:
        raise WordPdfMappingError(f"Word returned a reversed line range for {label}.")
    return page_start, line_start, line_end, page_end


def _find_unique_phrase(document: Any, phrase: str, *, label: str) -> tuple[int, int, int, int]:
    """Return (page, line_start, line_end, page_end) for exactly one body-text match."""
    search_text = phrase.replace("^", "^^")
    content_end = int(document.Content.End)
    cursor = int(document.Content.Start)
    matches: list[tuple[int, int]] = []
    while cursor < content_end:
        search_range = document.Range(cursor, content_end)
        finder = search_range.Find
        finder.ClearFormatting()
        finder.Text = search_text
        finder.Forward = True
        finder.Wrap = 0  # wdFindStop
        finder.Format = False
        finder.MatchCase = True
        finder.MatchWholeWord = False
        finder.MatchWildcards = False
        if not finder.Execute():
            break
        start, end = int(search_range.Start), int(search_range.End)
        if end <= start:
            raise WordPdfMappingError(f"Word returned an empty match for {label}.")
        matches.append((start, end))
        cursor = end
    if len(matches) != 1:
        raise WordPdfMappingError(
            f"{label} matched {len(matches)} times in the Word-final DOCX; "
            "automatic mapping requires exactly one occurrence."
        )
    start, end = matches[0]
    return _read_word_position(document, start, end, label=label)


def _export_one_document(
    word: Any,
    source_docx: Path,
    output_docx: Path,
    output_pdf: Path,
    jobs: Sequence[MappingJob],
) -> dict[tuple[str, str], tuple[int, int, int]]:
    if output_docx.exists() or output_pdf.exists():
        raise FileExistsError(
            f"Refusing to overwrite Word/PDF output for {source_docx.stem}."
        )
    document = None
    try:
        document = word.Documents.Open(
            FileName=str(source_docx),
            ConfirmConversions=False,
            ReadOnly=True,
            AddToRecentFiles=False,
            Visible=False,
        )
        if int(document.Revisions.Count) != 0:
            raise WordPdfMappingError(
                f"Working Clean View still contains tracked revisions: {source_docx}"
            )
        document.TrackRevisions = False
        # Word line numbering is exposed in Print Layout view.
        document.ActiveWindow.View.Type = 3  # wdPrintView
        for section_index in range(1, int(document.Sections.Count) + 1):
            numbering = document.Sections.Item(section_index).PageSetup.LineNumbering
            numbering.Active = True
            numbering.RestartMode = 0  # wdRestartContinuous
            numbering.CountBy = 1
            numbering.StartingNumber = 1
        document.Repaginate()
        document.SaveAs2(
            FileName=str(output_docx),
            FileFormat=16,  # wdFormatDocumentDefault (.docx)
            AddToRecentFiles=False,
        )
        document.Repaginate()

        positions: dict[tuple[str, str], tuple[int, int, int]] = {}
        unique_jobs: dict[tuple[str, str], MappingJob] = {}
        for job in jobs:
            unique_jobs.setdefault((job.patch_id, job.operation_id), job)
        for key, job in unique_jobs.items():
            page, line_start, line_end, _ = _find_unique_phrase(
                document,
                job.phrase,
                label=f"{job.patch_id}/{job.operation_id}",
            )
            positions[key] = (page, line_start, line_end)

        document.ExportAsFixedFormat(
            OutputFileName=str(output_pdf),
            ExportFormat=17,  # wdExportFormatPDF
            OpenAfterExport=False,
            OptimizeFor=0,  # wdExportOptimizeForPrint
        )
        if not output_docx.is_file() or not output_pdf.is_file():
            raise WordPdfMappingError(
                f"Word did not produce both final DOCX and PDF for {source_docx.stem}."
            )
        if output_docx.stat().st_size == 0 or output_pdf.stat().st_size == 0:
            raise WordPdfMappingError("Word produced an empty final DOCX or PDF.")
        return positions
    except WordPdfMappingError:
        raise
    except Exception as exc:
        raise WordPdfMappingError(
            f"Microsoft Word COM failed while processing {source_docx}: {exc}"
        ) from exc
    finally:
        if document is not None:
            try:
                document.Close(SaveChanges=0)
            except Exception:
                pass


def _verify_pdf_page_text(pdf_path: Path, phrase: str, page_number: int) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise WordPdfMappingError(
            "PyMuPDF is required to verify PDF page text. Install with "
            'pip install -e ".[word-pdf]".'
        ) from exc
    try:
        with fitz.open(str(pdf_path)) as pdf:
            if page_number < 1 or page_number > pdf.page_count:
                raise WordPdfMappingError(
                    f"Word page {page_number} is outside the exported PDF ({pdf.page_count} pages)."
                )
            actual_text = pdf.load_page(page_number - 1).get_text("text")
    except WordPdfMappingError:
        raise
    except Exception as exc:
        raise WordPdfMappingError(f"Cannot inspect exported PDF {pdf_path}: {exc}") from exc
    if _normalise_pdf_text(phrase) not in _normalise_pdf_text(actual_text):
        raise WordPdfMappingError(
            f"The target phrase is not extractable from PDF page {page_number}: {pdf_path}. "
            "The Word page/line result is not being recorded."
        )


def generate_word_pdf_location_map(
    ledger: RevisionLedger,
    *,
    ledger_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Create Word-final Clean DOCX/PDF artifacts and a hash-bound location map."""
    jobs_by_role = build_mapping_jobs(ledger)
    ledger_file = Path(ledger_path).expanduser().resolve()
    if not ledger_file.is_file():
        raise WordPdfMappingError(f"RevisionLedger file does not exist: {ledger_file}")
    ledger_sha256 = sha256_file(ledger_file)

    out_dir = Path(output_dir).expanduser().resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise WordPdfMappingError(
            f"Output directory must be new or empty; refusing to overwrite files in {out_dir}."
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    documents_by_role = {document.role: document for document in ledger.documents}
    safe_names: dict[str, str] = {}
    used_names: set[str] = set()
    for role in jobs_by_role:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", role).strip("._-") or "document"
        candidate = base
        suffix = 2
        while candidate.casefold() in used_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        safe_names[role] = candidate
        used_names.add(candidate.casefold())

    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise WordPdfMappingError(
            "Microsoft Word COM dependencies are unavailable. On Windows install "
            'the optional dependencies with pip install -e ".[word-pdf]".'
        ) from exc

    pythoncom.CoInitialize()
    word = None
    document_outputs: dict[str, dict[str, Any]] = {}
    positions_by_role: dict[str, dict[tuple[str, str], tuple[int, int, int]]] = {}
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        word.Options.SaveNormalPrompt = False
        word.Options.UpdateLinksAtOpen = False

        for role, jobs in jobs_by_role.items():
            ledger_document = documents_by_role[role]
            source_docx = Path(ledger_document.working_clean_file).expanduser().resolve()
            if sha256_file(source_docx) != ledger_document.working_clean_sha256:
                raise WordPdfMappingError(
                    f"Working Clean View changed immediately before Word opened it: {source_docx}"
                )
            stem = safe_names[role]
            output_docx = out_dir / f"{stem}.final-clean.docx"
            output_pdf = out_dir / f"{stem}.final-clean.pdf"
            positions = _export_one_document(
                word, source_docx, output_docx, output_pdf, jobs
            )
            output_sha256 = sha256_file(output_docx)
            pdf_sha256 = sha256_file(output_pdf)
            for job in jobs:
                page, _, _ = positions[(job.patch_id, job.operation_id)]
                _verify_pdf_page_text(output_pdf, job.phrase, page)
            document_outputs[role] = {
                "source_working_clean_file": str(source_docx),
                "source_working_clean_sha256": ledger_document.working_clean_sha256,
                "output_file": str(output_docx),
                "output_sha256": output_sha256,
                "pdf_file": str(output_pdf),
                "pdf_sha256": pdf_sha256,
            }
            positions_by_role[role] = positions
    finally:
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    location_map_id = "word-pdf-" + uuid.uuid4().hex
    locations_by_unit: dict[str, list[dict[str, Any]]] = {
        unit.response_unit_id: [] for unit in ledger.response_units
    }
    for role, jobs in jobs_by_role.items():
        document_output = document_outputs[role]
        positions = positions_by_role[role]
        for job in jobs:
            page, line_start, line_end = positions[(job.patch_id, job.operation_id)]
            locations_by_unit[job.response_unit_id].append({
                "document": role,
                "output_file": document_output["output_file"],
                "output_sha256": document_output["output_sha256"],
                "pdf_file": document_output["pdf_file"],
                "pdf_sha256": document_output["pdf_sha256"],
                "page": page,
                "line_start": line_start,
                "line_end": line_end,
                "target_sha256": hashlib.sha256(job.phrase.encode("utf-8")).hexdigest(),
                "patch_id": job.patch_id,
                "operation_id": job.operation_id,
            })

    response_rows: list[dict[str, Any]] = []
    for unit_id, rows in locations_by_unit.items():
        if not rows:
            raise WordPdfMappingError(
                f"ResponseUnit {unit_id} has no mapped final location."
            )
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            key = (
                row["document"], row["output_file"], row["pdf_file"],
                row["page"], row["line_start"], row["line_end"],
            )
            unique.setdefault(key, row)
        final_locations = list(unique.values())
        fingerprint = _fingerprint({
            "response_unit_id": unit_id,
            "location_map_id": location_map_id,
            "revision_ledger_sha256": ledger_sha256,
            "final_locations": final_locations,
            "line_numbering_policy": "continuous-per-document-v1",
        })
        response_rows.append({
            "response_unit_id": unit_id,
            "location_map_fingerprint": fingerprint,
            "final_locations": [
                {
                    **row,
                    "location_map_id": location_map_id,
                    "mapping_fingerprint": fingerprint,
                }
                for row in final_locations
            ],
        })

    return {
        "schema_version": "0.5.1",
        "location_map_id": location_map_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "generator": "co-reviewer-word-pdf-mapper-v1",
        "revision_ledger_sha256": ledger_sha256,
        "line_numbering_policy": "continuous-per-document-v1",
        "documents": [
            {"document": role, **document_outputs[role]}
            for role in sorted(document_outputs)
        ],
        "response_units": response_rows,
    }

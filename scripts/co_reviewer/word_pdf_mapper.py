# -*- coding: utf-8 -*-
"""Word-native pagination and document-role-scoped ResponseUnit mapping.

All physical page and line values come from Microsoft Word COM after field
refresh and repagination. The exported PDF is retained and hash-bound; no PDF
text-extraction engine is used to manufacture or second-guess Word line numbers.
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
from .revision_models import RevisionLedger


class WordPdfMappingError(RuntimeError):
    """Raised when Word pagination evidence is missing, stale or ambiguous."""


@dataclass(frozen=True)
class MappingJob:
    response_unit_id: str
    locator_id: str
    patch_id: str | None
    operation_id: str | None
    document_role: str
    phrase: str
    source_clean_docx: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.response_unit_id, self.locator_id)

    @property
    def label(self) -> str:
        return f"{self.response_unit_id}/{self.locator_id}"


def _fingerprint(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def assert_location_map_matches_ledger(
    location_map: Mapping[str, Any],
    ledger_path: str | Path,
) -> str:
    """Reject a location map generated from any other RevisionLedger version."""
    if not isinstance(location_map, Mapping):
        raise WordPdfMappingError("Location map must be an object.")
    ledger_file = Path(ledger_path).expanduser().resolve()
    if not ledger_file.is_file():
        raise WordPdfMappingError(f"RevisionLedger file does not exist: {ledger_file}")
    expected = location_map.get("revision_ledger_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise WordPdfMappingError(
            "Location map is missing a valid revision_ledger_sha256 binding."
        )
    actual = sha256_file(ledger_file)
    if actual != expected:
        raise WordPdfMappingError(
            "Location map was generated from a different RevisionLedger version; "
            "regenerate the Word/PDF mapping from the current ledger."
        )
    return actual


def _validate_mapping_phrase(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WordPdfMappingError("A final location phrase must contain surviving text.")
    if len(value) > 255:
        raise WordPdfMappingError(
            "Word's exact Find operation is limited to 255 characters; "
            "the deterministic location-phrase window must be shortened."
        )
    if any(char in value for char in "\r\n\t"):
        raise WordPdfMappingError(
            "Automatic Word mapping requires a single-paragraph phrase without tabs."
        )
    return value


def _normalise_pdf_text(value: str) -> str:
    """Normalise PDF-extracted text for page-level presence checks only."""
    value = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    value = value.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _verify_pdf_page_text(pdf_path: Path, phrase: str, page_number: int) -> None:
    """Confirm a Word-reported page contains the target phrase; never derive coordinates."""
    try:
        import fitz
    except ImportError as exc:
        raise WordPdfMappingError(
            "PyMuPDF is required to verify target text on the Word-reported PDF page. "
            'Install with pip install -e ".[word-pdf]".'
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


def _phrase_occurrences(paragraphs: Sequence[str], phrase: str) -> int:
    total = 0
    for paragraph in paragraphs:
        cursor = 0
        while True:
            position = paragraph.find(phrase, cursor)
            if position < 0:
                break
            total += 1
            cursor = position + 1
    return total


def build_unique_location_phrase(
    paragraphs: Sequence[str],
    paragraph_index: int,
    start: int | None = None,
    end: int | None = None,
) -> str:
    """Create a <=255-character unique Word Find phrase by expanding local word flanks.

    A zero-width span represents a deletion boundary and starts with the last
    four words on the left plus the first four words on the right. When no span
    is supplied, a compact anchor is chosen from the middle of an existing
    paragraph (used for no-change responses).
    """
    if not isinstance(paragraphs, (list, tuple)) or not 0 <= paragraph_index < len(paragraphs):
        raise WordPdfMappingError("The location-phrase paragraph anchor is outside the document.")
    paragraph = str(paragraphs[paragraph_index])
    tokens = list(re.finditer(r"\S+", paragraph))
    if not tokens:
        raise WordPdfMappingError("The selected paragraph has no mappable text.")
    if start is None and end is None:
        centre = len(tokens) // 2
        first = max(0, centre - 2)
        last = min(len(tokens) - 1, centre + 2)
    elif isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(paragraph):
        if start == end:
            left = [i for i, token in enumerate(tokens) if token.end() <= start]
            right = [i for i, token in enumerate(tokens) if token.start() >= end]
            first = max(0, (left[-1] - 3) if left else (right[0] if right else 0))
            last = min(len(tokens) - 1, (right[0] + 3) if right else (left[-1] if left else len(tokens) - 1))
        else:
            overlap = [
                i for i, token in enumerate(tokens)
                if token.end() > start and token.start() < end
            ]
            if not overlap:
                nearest = min(range(len(tokens)), key=lambda i: abs(tokens[i].start() - start))
                first = last = nearest
            else:
                first, last = overlap[0], overlap[-1]
    else:
        raise WordPdfMappingError("The location-phrase character span is invalid.")

    # A long replacement is represented by a local word window around its centre;
    # the resulting phrase still points to its exact final location.
    candidate = paragraph[tokens[first].start():tokens[last].end()]
    while len(candidate) > 255 and first < last:
        if (last - first) % 2:
            first += 1
        else:
            last -= 1
        candidate = paragraph[tokens[first].start():tokens[last].end()]
    if len(candidate) > 255:
        centre = (tokens[first].start() + tokens[last].end()) // 2
        left_bound = max(tokens[first].start(), centre - 110)
        right_bound = min(tokens[last].end(), left_bound + 220)
        candidate = paragraph[left_bound:right_bound].strip()
    for _ in range(len(tokens) * 2 + 1):
        if candidate and len(candidate) <= 255 and not any(ch in candidate for ch in "\r\n\t"):
            if _phrase_occurrences(paragraphs, candidate) == 1:
                return candidate
        can_left = first > 0
        can_right = last < len(tokens) - 1
        if not can_left and not can_right:
            break
        if can_left:
            proposed_first = first - 1
            proposed = paragraph[tokens[proposed_first].start():tokens[last].end()]
            if len(proposed) <= 255:
                first = proposed_first
                candidate = proposed
        if _phrase_occurrences(paragraphs, candidate) != 1 and can_right:
            proposed_last = last + 1
            proposed = paragraph[tokens[first].start():tokens[proposed_last].end()]
            if len(proposed) <= 255:
                last = proposed_last
                candidate = proposed
        if not can_left and not can_right:
            break
    raise WordPdfMappingError(
        f"Could not create a unique Word Find phrase for paragraph {paragraph_index}; "
        "the document contains indistinguishable repeated text within Word's 255-character limit."
    )


def build_mapping_jobs(ledger: RevisionLedger) -> dict[str, list[MappingJob]]:
    """Collect every location-required RU locator, grouped at Document Role granularity."""
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

    patch_by_id = {patch.patch_id: patch for patch in ledger.patches}
    operation_index: dict[str, tuple[Any, Any]] = {}
    for patch in ledger.patches:
        for operation in patch.operations:
            if operation.operation_id in operation_index:
                raise WordPdfMappingError(f"Duplicate PatchOperation ID: {operation.operation_id}.")
            operation_index[operation.operation_id] = (patch, operation)

    jobs: dict[str, list[MappingJob]] = {}
    for unit in ledger.response_units:
        if unit.disposition == "clarification_needed" or unit.status == "blocked":
            continue
        if unit.location_requirement == "not_required":
            continue
        if unit.location_requirement == "manual_required" and unit.status in {"location_mapped", "completed"}:
            continue
        if not unit.semantic_review_confirmed or unit.status not in {"verified", "location_mapped", "completed"}:
            continue

        if unit.disposition == "no_change":
            role = unit.anchor_document
            document = documents_by_role.get(role)
            if document is None:
                raise WordPdfMappingError(
                    f"No current RevisionDocument is registered for existing anchor role {role!r}."
                )
            clean_path = document.working_clean_file
            if not clean_path or not document.working_clean_sha256:
                raise WordPdfMappingError(
                    f"{role} has no hash-bound Working Clean View for existing-anchor mapping."
                )
            source = Path(clean_path).expanduser().resolve()
            if not source.is_file() or sha256_file(source) != document.working_clean_sha256:
                raise WordPdfMappingError(f"Working Clean View is missing or stale: {source}")
            if unit.location_requirement == "manual_required":
                if not unit.anchor_evidence_id or not unit.anchor_part or unit.anchor_p_idx is None:
                    raise WordPdfMappingError(
                        f"Manual no-change ResponseUnit {unit.response_unit_id} lost its confirmed Clean anchor evidence."
                    )
                # Word still exports this role's current final DOCX/PDF. The
                # anchor is verified in the current Clean part separately;
                # page/line is entered explicitly by the author.
                jobs.setdefault(role, [])
                continue
            phrase = _validate_mapping_phrase(unit.location_phrase)
            if unit.anchor_part != "word/document.xml":
                raise WordPdfMappingError(
                    f"Existing anchor {unit.response_unit_id} is in {unit.anchor_part}; "
                    "set manual_required for an explicit non-body locator."
                )
            locator_id = f"anchor:{unit.anchor_evidence_id}"
            jobs.setdefault(role, []).append(MappingJob(
                response_unit_id=unit.response_unit_id,
                locator_id=locator_id,
                patch_id=None,
                operation_id=None,
                document_role=role,
                phrase=phrase,
                source_clean_docx=str(source),
            ))
            continue

        if unit.disposition != "revised" or unit.location_requirement not in {"required", "manual_required"}:
            continue
        if not unit.linked_operation_ids:
            raise WordPdfMappingError(
                f"Verified revised ResponseUnit {unit.response_unit_id} has no exact linked_operation_ids."
            )
        for operation_id in unit.linked_operation_ids:
            pair = operation_index.get(operation_id)
            if pair is None:
                raise WordPdfMappingError(
                    f"ResponseUnit {unit.response_unit_id} links unknown PatchOperation {operation_id}."
                )
            patch, operation = pair
            if patch.patch_id not in unit.linked_patch_ids:
                raise WordPdfMappingError(
                    f"ResponseUnit {unit.response_unit_id} links operation {operation_id} "
                    f"without parent Patch {patch.patch_id}."
                )
            if unit.comment_id not in operation.source_comment_ids:
                raise WordPdfMappingError(
                    f"PatchOperation {operation_id} does not declare source comment {unit.comment_id}."
                )
            if patch.status != "verified":
                raise WordPdfMappingError(
                    f"Patch {patch.patch_id} is {patch.status}, not verified; "
                    f"mapping for {unit.response_unit_id} is blocked."
                )
            if unit.location_requirement == "manual_required":
                document = documents_by_role.get(operation.document)
                if document is None:
                    raise WordPdfMappingError(
                        f"No current RevisionDocument is registered for role {operation.document!r}."
                    )
                clean_path = document.working_clean_file
                if not clean_path or not document.working_clean_sha256:
                    raise WordPdfMappingError(
                        f"{operation.document} has no hash-bound Working Clean View for manual location export."
                    )
                source = Path(clean_path).expanduser().resolve()
                if not source.is_file() or sha256_file(source) != document.working_clean_sha256:
                    raise WordPdfMappingError(f"Working Clean View is missing or stale: {source}")
                # An empty role job list still asks Word to produce the final DOCX/PDF;
                # the author then records the non-body/table locator explicitly.
                jobs.setdefault(operation.document, [])
                continue
            if operation.part != "word/document.xml":
                raise WordPdfMappingError(
                    f"Patch {patch.patch_id}/{operation_id} targets {operation.part}; "
                    "automatic mapping currently supports main-body paragraphs only."
                )
            phrase = _validate_mapping_phrase(operation.location_phrase)
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
            if not source.is_file() or sha256_file(source) != document.working_clean_sha256:
                raise WordPdfMappingError(f"Working Clean View is missing or stale: {source}")
            jobs.setdefault(operation.document, []).append(MappingJob(
                response_unit_id=unit.response_unit_id,
                locator_id=operation_id,
                patch_id=patch.patch_id,
                operation_id=operation_id,
                document_role=operation.document,
                phrase=phrase,
                source_clean_docx=str(source),
            ))
    return jobs


def _read_word_position(
    document: Any,
    start: int,
    end: int,
    *,
    label: str,
) -> tuple[int, int, int, int]:
    """Read physical page and continuous line numbers from Word COM."""
    if end <= start:
        raise WordPdfMappingError(f"Word returned an empty match for {label}.")
    first_character = document.Range(start, min(start + 1, end))
    last_character = document.Range(max(start, end - 1), end)
    if bool(first_character.Information(12)) or bool(last_character.Information(12)):
        raise WordPdfMappingError(
            f"{label} is inside a table. Word line numbering is not reliable for table cells; "
            "use a separately reviewed table locator."
        )
    page_start = int(first_character.Information(3))
    page_end = int(last_character.Information(3))
    line_start = int(first_character.Information(10))
    line_end = int(last_character.Information(10))
    if min(page_start, page_end, line_start, line_end) < 1:
        raise WordPdfMappingError(
            f"Word did not return valid page/line numbers for {label}; "
            "check Print Layout and section line-number settings."
        )
    if page_start != page_end:
        raise WordPdfMappingError(
            f"{label} crosses pages {page_start}-{page_end}; "
            "the current ResponseLocation schema requires one page per target."
        )
    if line_end < line_start:
        raise WordPdfMappingError(f"Word returned a reversed line range for {label}.")
    return page_start, line_start, line_end, page_end


def _find_unique_phrase(document: Any, phrase: str, *, label: str) -> tuple[int, int, int, int]:
    """Return (page, line_start, line_end, page_end) for one exact Word Find match."""
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
        finder.Wrap = 0
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


def _refresh_builtin_fields(document: Any) -> dict[str, int]:
    """Refresh only Word PAGE/PAGEREF/SEQ/REF fields; leave citation plugins untouched."""
    updated = {"SEQ": 0, "REF": 0, "PAGE": 0, "PAGEREF": 0}
    story_ranges: list[Any] = []
    for story_type in range(1, 18):
        try:
            story = document.StoryRanges.Item(story_type)
        except Exception:
            continue
        while story is not None:
            story_ranges.append(story)
            try:
                story = story.NextStoryRange
            except Exception:
                story = None
    for target_codes in ({"SEQ", "REF"}, {"PAGE", "PAGEREF"}):
        for story in story_ranges:
            try:
                fields = story.Fields
                field_count = int(fields.Count)
            except Exception:
                continue
            for index in range(1, field_count + 1):
                field = fields.Item(index)
                code = str(field.Code.Text or "")
                match = re.match(r"^\s*(SEQ|REF|PAGE|PAGEREF)\b", code, re.IGNORECASE)
                if not match:
                    continue
                kind = match.group(1).upper()
                if kind not in target_codes:
                    continue
                field.Update()
                updated[kind] += 1
        document.Repaginate()
    return updated


def _audit_sequence_fields(document: Any) -> dict[str, Any]:
    """Check native SEQ Figure/Table fields after Word has refreshed them.

    Simple numeric sequences are checked for 1..N continuity. Chapter-reset,
    explicit-reset and non-numeric schemes are surfaced for manual review rather
    than being guessed or silently treated as continuous.
    """
    detected: dict[str, list[dict[str, Any]]] = {"Figure": [], "Table": []}
    unsupported: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    try:
        fields = document.Fields
        count = int(fields.Count)
    except Exception:
        try:
            fields = document.StoryRanges.Item(1).Fields
            count = int(fields.Count)
        except Exception as exc:
            return {
                "status": "manual_review",
                "detected": detected,
                "issues": [],
                "manual_review": [{"reason": f"Word main-story Fields collection is unavailable: {exc}"}],
            }

    for index in range(1, count + 1):
        try:
            field = fields.Item(index)
            code = str(field.Code.Text or "").strip()
            match = re.match(r'^SEQ\s+(?:"([^"]+)"|(\S+))', code, re.IGNORECASE)
            if not match:
                continue
            sequence_name = (match.group(1) or match.group(2)).casefold()
            if sequence_name not in {"figure", "table"}:
                continue
            switches = {
                token.casefold()
                for token in re.findall(r"\\([A-Za-z]+)", code)
            }
            if "c" in switches:
                # The Word \c switch repeats the current number and is not
                # another caption sequence item.
                continue
            if switches.intersection({"s", "r"}):
                unsupported.append({
                    "sequence": sequence_name.title(),
                    "field_index": index,
                    "code": code,
                    "reason": "Chapter-reset (\\s) or explicit-reset (\\r) numbering needs manual sequence review.",
                })
                continue
            value_text = str(field.Result.Text or "").strip()
            if not re.fullmatch(r"\d+", value_text):
                unsupported.append({
                    "sequence": sequence_name.title(),
                    "field_index": index,
                    "code": code,
                    "result": value_text,
                    "reason": "The refreshed SEQ result is not a plain integer.",
                })
                continue
            detected[sequence_name.title()].append({
                "field_index": index,
                "value": int(value_text),
                "code": code,
            })
        except Exception as exc:
            unsupported.append({
                "field_index": index,
                "reason": f"Word COM could not inspect this field: {exc}",
            })

    for sequence_name, records in detected.items():
        if not records:
            continue
        values = [record["value"] for record in records]
        expected = list(range(1, len(values) + 1))
        if values != expected:
            issues.append({
                "sequence": sequence_name,
                "observed": values,
                "expected": expected,
                "field_indices": [record["field_index"] for record in records],
                "reason": "Native Word SEQ values contain a gap, duplicate or unexpected starting number.",
            })

    missing_native_sequences = [
        name for name, records in detected.items() if not records
    ]
    manual_review = list(unsupported)
    if missing_native_sequences:
        manual_review.append({
            "reason": "No simple native numeric SEQ field was detected for: "
            + ", ".join(missing_native_sequences)
            + ". Manually typed captions or nonstandard numbering cannot be certified automatically."
        })
    if issues:
        status = "fail"
    elif manual_review:
        status = "manual_review"
    elif any(detected.values()):
        status = "pass"
    else:
        status = "manual_review"
    return {
        "status": status,
        "detected": detected,
        "issues": issues,
        "manual_review": manual_review,
    }


def _export_one_document(
    word: Any,
    source_docx: Path,
    output_docx: Path,
    output_pdf: Path,
    jobs: Sequence[MappingJob],
) -> tuple[dict[tuple[str, str], tuple[int, int, int]], dict[str, int], int, dict[str, Any]]:
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
        document.ActiveWindow.View.Type = 3
        for section_index in range(1, int(document.Sections.Count) + 1):
            numbering = document.Sections.Item(section_index).PageSetup.LineNumbering
            numbering.Active = True
            numbering.RestartMode = 0
            numbering.CountBy = 1
            numbering.StartingNumber = 1
        refreshed_fields = _refresh_builtin_fields(document)
        document.Repaginate()
        sequence_audit = _audit_sequence_fields(document)
        if sequence_audit["status"] == "fail":
            raise WordPdfMappingError(
                f"Word-native Figure/Table sequence continuity check failed for {source_docx}: "
                f"{sequence_audit['issues']}"
            )
        document.SaveAs2(
            FileName=str(output_docx),
            FileFormat=16,
            AddToRecentFiles=False,
        )
        document.Repaginate()
        word_page_count = int(document.ComputeStatistics(2))
        if word_page_count < 1:
            raise WordPdfMappingError("Word COM returned an invalid document page count after repagination.")

        positions: dict[tuple[str, str], tuple[int, int, int]] = {}
        unique_jobs: dict[tuple[str, str], MappingJob] = {}
        for job in jobs:
            unique_jobs.setdefault(job.key, job)
        for key, job in unique_jobs.items():
            page, line_start, line_end, _ = _find_unique_phrase(
                document, job.phrase, label=job.label
            )
            if page > word_page_count:
                raise WordPdfMappingError(
                    f"{job.label} resolved to page {page}, beyond Word's final page count {word_page_count}."
                )
            positions[key] = (page, line_start, line_end)

        document.ExportAsFixedFormat(
            OutputFileName=str(output_pdf),
            ExportFormat=17,
            OpenAfterExport=False,
            OptimizeFor=0,
        )
        if not output_docx.is_file() or not output_pdf.is_file():
            raise WordPdfMappingError(
                f"Word did not produce both final DOCX and PDF for {source_docx.stem}."
            )
        if output_docx.stat().st_size == 0 or output_pdf.stat().st_size == 0:
            raise WordPdfMappingError("Word produced an empty final DOCX or PDF.")
        return positions, refreshed_fields, word_page_count, sequence_audit
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


def _reusable_role_mapping(
    role: str,
    jobs: Sequence[MappingJob],
    units_by_id: Mapping[str, Any],
    source_clean_sha256: str,
) -> tuple[dict[tuple[str, str], tuple[int, int, int]], dict[str, Any]] | None:
    """Reuse only a complete, hash-verified mapping for the whole unchanged document role."""
    positions: dict[tuple[str, str], tuple[int, int, int]] = {}
    output_files: set[str] = set()
    pdf_files: set[str] = set()
    output_hashes: set[str] = set()
    pdf_hashes: set[str] = set()
    role_page_counts: set[int] = set()
    role_sequence_audits: list[dict[str, Any]] = []
    for job in jobs:
        unit = units_by_id[job.response_unit_id]
        matches = [
            location for location in unit.final_locations
            if location.document == role
            and location.locator_id == job.locator_id
            and location.source_clean_sha256 == source_clean_sha256
        ]
        if len(matches) != 1:
            return None
        map_file = Path(unit.location_map_file).expanduser().resolve() if unit.location_map_file else None
        if map_file is None or not map_file.is_file():
            return None
        try:
            prior_map = json.loads(map_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        role_rows = [row for row in prior_map.get("documents", []) if row.get("document") == role]
        if len(role_rows) != 1:
            return None
        role_page_count = role_rows[0].get("word_page_count")
        if not isinstance(role_page_count, int) or isinstance(role_page_count, bool) or role_page_count < 1:
            return None
        role_page_counts.add(role_page_count)
        prior_sequence_audit = role_rows[0].get("sequence_audit")
        if not isinstance(prior_sequence_audit, dict) or prior_sequence_audit.get("status") not in {"pass", "manual_review"}:
            return None
        role_sequence_audits.append(prior_sequence_audit)
        location = matches[0]
        output_path = Path(location.output_file).expanduser().resolve()
        pdf_path = Path(location.pdf_file).expanduser().resolve()
        if not output_path.is_file() or not pdf_path.is_file():
            return None
        if sha256_file(output_path) != location.output_sha256:
            return None
        if sha256_file(pdf_path) != location.pdf_sha256:
            return None
        if min(location.page, location.line_start, location.line_end) < 1:
            return None
        output_files.add(str(output_path))
        pdf_files.add(str(pdf_path))
        output_hashes.add(location.output_sha256)
        pdf_hashes.add(location.pdf_sha256)
        positions[job.key] = (location.page, location.line_start, location.line_end)
    if len(output_files) != 1 or len(pdf_files) != 1 or len(output_hashes) != 1 or len(pdf_hashes) != 1 or len(role_page_counts) != 1:
        return None
    if any(item != role_sequence_audits[0] for item in role_sequence_audits[1:]):
        return None
    output_file = next(iter(output_files))
    pdf_file = next(iter(pdf_files))
    return positions, {
        "output_file": output_file,
        "output_sha256": next(iter(output_hashes)),
        "pdf_file": pdf_file,
        "pdf_sha256": next(iter(pdf_hashes)),
        "word_page_count": next(iter(role_page_counts)),
        "sequence_audit": role_sequence_audits[0],
        "reused": True,
        "refreshed_fields": {"SEQ": 0, "REF": 0, "PAGE": 0, "PAGEREF": 0},
    }


def generate_word_pdf_location_map(
    ledger: RevisionLedger,
    *,
    ledger_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Refresh every eligible locator in a changed document role in one Word session.

    A role is reusable only when every required ResponseUnit locator for that role
    already has a source-clean-hash-bound position and its final DOCX/PDF hashes
    still match. One missing or stale locator forces a full role-level remap.
    """
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
    units_by_id = {unit.response_unit_id: unit for unit in ledger.response_units}
    used_names: set[str] = set()
    safe_names: dict[str, str] = {}
    for role in jobs_by_role:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", role).strip("._-") or "document"
        candidate = base
        suffix = 2
        while candidate.casefold() in used_names:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used_names.add(candidate.casefold())
        safe_names[role] = candidate

    document_outputs: dict[str, dict[str, Any]] = {}
    positions_by_role: dict[str, dict[tuple[str, str], tuple[int, int, int]]] = {}
    changed_roles: list[str] = []
    for role, jobs in jobs_by_role.items():
        ledger_document = documents_by_role.get(role)
        if ledger_document is None or not ledger_document.working_clean_file or not ledger_document.working_clean_sha256:
            raise WordPdfMappingError(f"Document role {role} has no current hash-bound Working Clean View.")
        source_docx = Path(ledger_document.working_clean_file).expanduser().resolve()
        if not source_docx.is_file() or sha256_file(source_docx) != ledger_document.working_clean_sha256:
            raise WordPdfMappingError(f"Working Clean View is missing or stale: {source_docx}")
        reusable = _reusable_role_mapping(
            role, jobs, units_by_id, ledger_document.working_clean_sha256
        )
        if reusable is not None:
            positions_by_role[role], reused = reusable
            document_outputs[role] = {
                "source_working_clean_file": str(source_docx),
                "source_working_clean_sha256": ledger_document.working_clean_sha256,
                **reused,
            }
        else:
            changed_roles.append(role)

    if changed_roles:
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise WordPdfMappingError(
                "Microsoft Word COM dependencies are unavailable. On Windows install "
                'pywin32 with pip install -e ".[word-pdf]".'
            ) from exc

        pythoncom.CoInitialize()
        word = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            word.AutomationSecurity = 3
            word.Options.SaveNormalPrompt = False
            word.Options.UpdateLinksAtOpen = False
            for role in changed_roles:
                ledger_document = documents_by_role[role]
                source_docx = Path(ledger_document.working_clean_file).expanduser().resolve()
                output_docx = out_dir / f"{safe_names[role]}.final-clean.docx"
                output_pdf = out_dir / f"{safe_names[role]}.final-clean.pdf"
                positions, refreshed_fields, word_page_count, sequence_audit = _export_one_document(
                    word, source_docx, output_docx, output_pdf, jobs_by_role[role]
                )
                for job in jobs_by_role[role]:
                    page_number, _, _ = positions[job.key]
                    _verify_pdf_page_text(output_pdf, job.phrase, page_number)
                positions_by_role[role] = positions
                document_outputs[role] = {
                    "source_working_clean_file": str(source_docx),
                    "source_working_clean_sha256": ledger_document.working_clean_sha256,
                    "output_file": str(output_docx),
                    "output_sha256": sha256_file(output_docx),
                    "pdf_file": str(output_pdf),
                    "pdf_sha256": sha256_file(output_pdf),
                    "word_page_count": word_page_count,
                    "sequence_audit": sequence_audit,
                    "reused": False,
                    "refreshed_fields": refreshed_fields,
                }
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

    # Build every ResponseUnit's complete location set. For an unchanged role,
    # the verified old page/line is reused; a changed role contributes a full
    # fresh pass over all of its current eligible ResponseUnit locators.
    locations_by_unit: dict[str, list[dict[str, Any]]] = {
        unit.response_unit_id: [] for unit in ledger.response_units
    }
    for role, jobs in jobs_by_role.items():
        document_output = document_outputs[role]
        positions = positions_by_role[role]
        ledger_document = documents_by_role[role]
        for job in jobs:
            page, line_start, line_end = positions[job.key]
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
                "locator_id": job.locator_id,
                "source_clean_sha256": ledger_document.working_clean_sha256,
                "mapping_method": "word_com",
                "locator_text": None,
            })

    location_map_id = "word-pdf-" + uuid.uuid4().hex
    response_rows: list[dict[str, Any]] = []
    for unit_id, rows in locations_by_unit.items():
        if not rows:
            continue
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            unique[(row["document"], row["locator_id"])] = row
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

    skipped = []
    mapped_ids = {row["response_unit_id"] for row in response_rows}
    for unit in ledger.response_units:
        if unit.response_unit_id in mapped_ids:
            continue
        if unit.location_requirement == "not_required":
            reason = "not_required"
        elif unit.location_requirement == "manual_required":
            reason = "manual_location_required"
        elif unit.disposition == "clarification_needed" or unit.status == "blocked":
            reason = "clarification_needed"
        elif not unit.semantic_review_confirmed or unit.status not in {"verified", "location_mapped", "completed"}:
            reason = "semantic_review_or_patch_verification_pending"
        else:
            reason = "no_eligible_location_locator"
        skipped.append({"response_unit_id": unit.response_unit_id, "reason": reason})

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
        "skipped_response_units": skipped,
        "document_refresh_policy": "full-role-remap-if-clean-hash-or-any-role-locator-changes-v1",
    }

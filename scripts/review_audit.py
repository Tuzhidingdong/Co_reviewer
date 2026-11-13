from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
CACHE_SCHEMA_VERSION = 1

_CAPTION_RE = re.compile(
    r"^\s*(?P<kind>figure|fig\.?|table)\s*(?P<label>S?\d+[a-z]?)\b",
    re.IGNORECASE,
)
_REFERENCE_RE = re.compile(
    r"\b(?P<kind>figures?|figs?\.?|tables?)\s*"
    r"(?P<first>S?\d+[a-z]?)"
    r"(?:\s*(?:-|–|—|to)\s*(?P<last>S?\d+[a-z]?))?",
    re.IGNORECASE,
)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:page|lines?)\s*[xX]+|\[\[(?:TODO|TBD|XX)[^\]]*\]\]",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "into", "onto", "please", "should", "would", "could", "figure", "fig",
    "table", "manuscript", "supporting", "information", "according", "order",
    "正文", "修改", "根据", "进行", "以及", "是否", "需要",
}


def _qn(local: str) -> str:
    return f"{{{W}}}{local}"


def extract_comment_ranges(root: Any, part: str) -> dict[str, list[dict[str, Any]]]:
    """Recover text selected by native Word commentRangeStart/commentRangeEnd markers."""
    paragraphs = root.findall(".//w:p", NS)
    paragraph_index = {node: index for index, node in enumerate(paragraphs)}
    active: dict[str, dict[str, Any]] = {}
    completed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    current_p_idx = -1

    def enter_paragraph(p_idx: int) -> None:
        for state in active.values():
            previous = state.get("last_p_idx")
            if previous is not None and previous != p_idx:
                if state["text"] and not state["text"][-1].endswith("\n"):
                    state["text"].append("\n")
            state["last_p_idx"] = p_idx
            if p_idx not in state["paragraphs"]:
                state["paragraphs"].append(p_idx)

    def finish(comment_id: str, state: dict[str, Any], end: dict[str, Any] | None) -> None:
        completed[comment_id].append({
            "part": part,
            "start": state["start"],
            "end": end,
            "paragraphs": state["paragraphs"],
            "text": "".join(state["text"]).strip(),
            "complete": end is not None,
        })

    for node in root.iter():
        if node.tag == _qn("p"):
            current_p_idx = paragraph_index.get(node, -1)
            if active:
                enter_paragraph(current_p_idx)
            continue

        if node.tag == _qn("commentRangeStart"):
            comment_id = node.get(_qn("id"), "")
            if not comment_id:
                continue
            prior = active.pop(comment_id, None)
            if prior is not None:
                finish(comment_id, prior, None)
            state = {
                "start": {"part": part, "p_idx": current_p_idx},
                "last_p_idx": None,
                "paragraphs": [],
                "text": [],
            }
            active[comment_id] = state
            enter_paragraph(current_p_idx)
            continue

        if node.tag == _qn("commentRangeEnd"):
            comment_id = node.get(_qn("id"), "")
            state = active.pop(comment_id, None)
            if state is not None:
                end = {"part": part, "p_idx": current_p_idx}
                finish(comment_id, state, end)
            continue

        if node.tag in {_qn("t"), _qn("delText")} and node.text:
            for state in active.values():
                state["text"].append(node.text)
        elif node.tag == _qn("tab"):
            for state in active.values():
                state["text"].append("\t")
        elif node.tag == _qn("br"):
            for state in active.values():
                state["text"].append("\n")

    for comment_id, state in active.items():
        finish(comment_id, state, None)

    return dict(completed)


def parse_caption(text: str, part: str, p_idx: int) -> dict[str, Any] | None:
    match = _CAPTION_RE.match(text or "")
    if not match:
        return None
    kind_text = match.group("kind").casefold()
    kind = "table" if kind_text.startswith("table") else "figure"
    label = match.group("label").upper()
    return {
        "kind": kind,
        "label": label,
        "part": part,
        "p_idx": p_idx,
        "text": text,
    }


def _normalise_kind(value: str) -> str:
    return "table" if value.casefold().startswith("table") else "figure"


def _expand_label_range(first: str, last: str | None) -> list[str]:
    first = first.upper()
    if not last:
        return [first]
    last = last.upper()
    first_match = re.fullmatch(r"(S?)(\d+)([A-Z]?)", first)
    last_match = re.fullmatch(r"(S?)(\d+)([A-Z]?)", last)
    if not first_match or not last_match:
        return [first, last]
    if first_match.group(1) != last_match.group(1):
        return [first, last]
    if first_match.group(3) or last_match.group(3):
        return [first, last]
    start, stop = int(first_match.group(2)), int(last_match.group(2))
    if stop < start or stop - start > 30:
        return [first, last]
    prefix = first_match.group(1)
    return [f"{prefix}{number}" for number in range(start, stop + 1)]


def extract_figure_table_references(text: str, part: str, p_idx: int) -> list[dict[str, Any]]:
    """Extract textual Figure/Fig./Table references; caption paragraphs are excluded."""
    if _CAPTION_RE.match(text or ""):
        return []
    records: list[dict[str, Any]] = []
    for match in _REFERENCE_RE.finditer(text or ""):
        kind = _normalise_kind(match.group("kind"))
        labels = _expand_label_range(match.group("first"), match.group("last"))
        records.append({
            "kind": kind,
            "labels": labels,
            "part": part,
            "p_idx": p_idx,
            "text": match.group(0),
        })
    return records


def normalize_match_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    text = text.replace("\u00a0", " ").replace("\u2010", "-").replace("\u2011", "-")
    text = text.replace("\u2012", "-").replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip()


def _tokens(text: str) -> set[str]:
    normal = normalize_match_text(text)
    tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normal))
    expanded: set[str] = set()
    for token in tokens:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 1:
                expanded.add(token)
            else:
                expanded.update(token[index:index + 2] for index in range(len(token) - 1))
        elif len(token) >= 3 and token not in _STOP_WORDS:
            expanded.add(token)
    return expanded


def _paragraphs_by_part(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for paragraph in report.get("paragraphs", []):
        if paragraph.get("text"):
            grouped[paragraph.get("part", "word/document.xml")].append(paragraph)
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("p_idx", 0)))
    return dict(grouped)


def _exact_anchor_matches(anchor_text: str, report: dict[str, Any]) -> list[dict[str, Any]]:
    needle = normalize_match_text(anchor_text)
    if len(needle) < 6:
        return []
    results: list[dict[str, Any]] = []
    for part, rows in _paragraphs_by_part(report).items():
        for row in rows:
            haystack = normalize_match_text(row.get("text", ""))
            if needle and needle in haystack:
                results.append({
                    "part": part,
                    "p_idx": row.get("p_idx"),
                    "para_id": row.get("para_id"),
                    "match_type": "exact_anchor_text",
                    "matched_text": row.get("text", "")[:1600],
                })
        # Also allow one selected range to span adjacent paragraphs.
        if len(rows) > 1 and not any(item["part"] == part for item in results):
            cumulative: list[tuple[int, int, dict[str, Any]]] = []
            pieces: list[str] = []
            offset = 0
            for row in rows:
                piece = normalize_match_text(row.get("text", ""))
                pieces.append(piece)
                cumulative.append((offset, offset + len(piece), row))
                offset += len(piece) + 1
            haystack = "\n".join(pieces)
            position = haystack.find(needle)
            if position >= 0:
                end = position + len(needle)
                touched = [
                    row for begin, finish, row in cumulative
                    if finish > position and begin < end
                ]
                if touched:
                    results.append({
                        "part": part,
                        "p_idx": touched[0].get("p_idx"),
                        "p_idx_end": touched[-1].get("p_idx"),
                        "para_id": touched[0].get("para_id"),
                        "match_type": "exact_anchor_span",
                        "matched_text": "\n".join(row.get("text", "") for row in touched)[:1600],
                    })
    return results


def _candidate_anchor_matches(anchor_text: str, report: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    anchor_tokens = _tokens(anchor_text)
    if len(anchor_tokens) < 3:
        return []
    candidates = []
    for paragraph in report.get("paragraphs", []):
        target_tokens = _tokens(paragraph.get("text", ""))
        if not target_tokens:
            continue
        score = len(anchor_tokens & target_tokens) / max(1, len(anchor_tokens))
        if score >= 0.45:
            candidates.append({
                "part": paragraph.get("part"),
                "p_idx": paragraph.get("p_idx"),
                "para_id": paragraph.get("para_id"),
                "match_type": "token_overlap_candidate",
                "match_score": round(score, 3),
                "matched_text": paragraph.get("text", "")[:1600],
            })
    candidates.sort(key=lambda item: (-item["match_score"], item["part"] or "", item["p_idx"] or 0))
    return candidates[:limit]


def match_comments_to_documents(reviewer_report: dict[str, Any], target_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for comment in reviewer_report.get("comments", []):
        anchor_text = comment.get("anchor_text") or ""
        selections = [
            item for item in comment.get("anchor_selections", [])
            if item.get("text")
        ]
        if not selections and anchor_text:
            selections = [{"text": anchor_text}]
        exact: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        for target_report in target_reports:
            role = target_report.get("role", "Document")
            role_has_exact = False
            role_candidates: list[dict[str, Any]] = []
            for selection_index, selection in enumerate(selections):
                selected_text = selection.get("text") or ""
                for item in _exact_anchor_matches(selected_text, target_report):
                    item["document"] = role
                    item["selection_index"] = selection_index
                    item["matched_anchor_text"] = selected_text[:1200]
                    exact.append(item)
                    role_has_exact = True
                if not role_has_exact:
                    for item in _candidate_anchor_matches(selected_text, target_report):
                        item["document"] = role
                        item["selection_index"] = selection_index
                        role_candidates.append(item)
            if not role_has_exact:
                candidates.extend(role_candidates)

        # Deduplicate exact results that can arise when multiple ranges overlap the
        # same target paragraph; preserve selection provenance in the audit record.
        unique_exact: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in exact:
            key = (
                item.get("document"), item.get("part"), item.get("p_idx"),
                item.get("p_idx_end"), item.get("matched_anchor_text"),
            )
            unique_exact[key] = item
        exact = list(unique_exact.values())

        anchor_status = comment.get("anchor_status", "markers_missing")
        if _PLACEHOLDER_RE.search(anchor_text):
            status = "placeholder_anchor"
        elif anchor_status != "range_resolved":
            status = "anchor_unresolved"
        elif len(exact) == 1:
            status = "exact_unique"
        elif len(exact) > 1:
            status = "ambiguous_exact_match"
        else:
            status = "no_exact_match"

        results.append({
            "comment_id": comment.get("id"),
            "reviewer": comment.get("author", ""),
            "comment": comment.get("comment", ""),
            "reply_anchor": {
                "status": anchor_status,
                "text": anchor_text,
                "selections": selections,
                "part": comment.get("part"),
                "p_idx": comment.get("p_idx"),
                "paragraphs": comment.get("anchor_paragraphs", []),
            },
            "target_status": status,
            "confirmed_matches": exact,
            "candidate_matches": candidates[:10],
            "note": (
                "A cross-document match is confirmed only when a resolved native Word selection "
                "matches exactly and uniquely. Comment text or token overlap is never promoted "
                "to a confirmed target."
            ),
        })
    return results

def _label_parts(label: str) -> tuple[str, int, str] | None:
    match = re.fullmatch(r"(S?)(\d+)([A-Z]?)", (label or "").upper())
    if not match:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def audit_figure_table_numbering(reports: list[dict[str, Any]]) -> dict[str, Any]:
    all_captions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    all_references: list[dict[str, Any]] = []
    per_document: dict[str, Any] = {}

    for report in reports:
        role = report.get("role", "Document")
        captions = sorted(
            report.get("captions_and_drawings", []),
            key=lambda item: (item.get("part", ""), int(item.get("p_idx", 0))),
        )
        captions = [item for item in captions if item.get("caption_kind") and item.get("caption_label")]
        references = sorted(
            report.get("figure_table_references", []),
            key=lambda item: (item.get("part", ""), int(item.get("p_idx", 0))),
        )
        for caption in captions:
            all_captions[(caption["caption_kind"], caption["caption_label"])].append({
                "document": role, **caption
            })
        for reference in references:
            for label in reference.get("labels", []):
                all_references.append({"document": role, **reference, "label": label})

        label_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for caption in captions:
            parts = _label_parts(caption["caption_label"])
            if parts:
                label_groups[(caption["caption_kind"], parts[0])].append(caption)

        duplicates = []
        gaps = []
        for (kind, prefix), rows in label_groups.items():
            counts = Counter(row["caption_label"] for row in rows)
            for label, count in counts.items():
                if count > 1:
                    duplicates.append({"kind": kind, "label": label, "count": count})
            numeric = sorted({parts[1] for row in rows if (parts := _label_parts(row["caption_label"]))})
            if len(numeric) >= 2:
                missing = [f"{prefix}{number}" for number in range(numeric[0], numeric[-1] + 1) if number not in numeric]
                if missing:
                    gaps.append({"kind": kind, "prefix": prefix, "missing_labels": missing})

        first_mentions: dict[str, list[str]] = {"figure": [], "table": []}
        for reference in references:
            kind = reference["kind"]
            for label in reference.get("labels", []):
                if label not in first_mentions[kind]:
                    first_mentions[kind].append(label)

        out_of_order = []
        for kind, labels in first_mentions.items():
            previous: dict[str, int] = {}
            for label in labels:
                parts = _label_parts(label)
                if not parts:
                    continue
                prefix, number, suffix = parts
                if prefix in previous and number < previous[prefix]:
                    out_of_order.append({
                        "kind": kind,
                        "label": label,
                        "previous_number": previous[prefix],
                        "reason": "first-mention sequence decreases",
                    })
                previous[prefix] = number

        per_document[role] = {
            "captions_in_document_order": [
                {"kind": row["caption_kind"], "label": row["caption_label"], "part": row["part"],
                 "p_idx": row["p_idx"], "text": row.get("text", "")[:500]}
                for row in captions
            ],
            "first_mention_order": first_mentions,
            "duplicate_captions": duplicates,
            "numbering_gaps": gaps,
            "out_of_order_first_mentions": out_of_order,
            "reference_count": len(references),
        }

    unresolved_references = []
    for reference in all_references:
        key = (reference["kind"], reference["label"])
        if not all_captions.get(key):
            unresolved_references.append({
                "document": reference["document"],
                "kind": reference["kind"],
                "label": reference["label"],
                "part": reference["part"],
                "p_idx": reference["p_idx"],
                "reference_text": reference["text"],
            })

    unreferenced_captions = []
    referenced_keys = {(row["kind"], row["label"]) for row in all_references}
    for key, rows in all_captions.items():
        if key not in referenced_keys:
            for row in rows:
                unreferenced_captions.append({
                    "document": row["document"],
                    "kind": key[0],
                    "label": key[1],
                    "part": row["part"],
                    "p_idx": row["p_idx"],
                    "caption_text": row.get("text", "")[:500],
                })

    duplicate_labels_across_documents = []
    for (kind, label), rows in all_captions.items():
        doc_roles = sorted({row["document"] for row in rows})
        if len(doc_roles) > 1:
            duplicate_labels_across_documents.append({
                "kind": kind, "label": label, "occurrences": rows,
                "document_roles": doc_roles,
            })

    return {
        "scope": "Caption paragraphs and textual Figure/Fig./Table references in inspected DOCX parts.",
        "automatic_renumbering": False,
        "by_document": per_document,
        "unresolved_references": unresolved_references,
        "unreferenced_captions": unreferenced_captions,
        "duplicate_labels_across_documents": duplicate_labels_across_documents,
        "limitations": [
            "This is a deterministic consistency audit, not an automatic renumbering operation.",
            "Captions embedded only as image text, floating shapes, or unsupported text boxes may not be represented as ordinary paragraphs.",
            "A reported gap or unreferenced caption is a review flag; it is not proof that a figure/table is missing.",
        ],
    }


def build_review_audit(reports: list[dict[str, Any]]) -> dict[str, Any]:
    reviewer_reports = [item for item in reports if item.get("role") == "Reviewers"]
    target_reports = [item for item in reports if item.get("role") in {"Manuscript", "SI"}]
    matches = []
    for reviewer_report in reviewer_reports:
        matches.extend(match_comments_to_documents(reviewer_report, target_reports))
    numbering = audit_figure_table_numbering(reports)
    counts = Counter(item["target_status"] for item in matches)
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_files": [
            {"role": item.get("role"), "file": item.get("file"), "sha256": item.get("source_sha256")}
            for item in reports
        ],
        "comment_count": len(matches),
        "comment_target_status_counts": dict(counts),
        "comment_matches": matches,
        "figure_table_numbering": numbering,
        "decision_boundary": (
            "This report is deterministic evidence only. It does not decide scientific disposition, "
            "invent edits, or modify any document."
        ),
    }


def _context_excerpts(report: dict[str, Any], match_rows: list[dict[str, Any]], max_chars: int = 1800) -> list[dict[str, Any]]:
    by_part = _paragraphs_by_part(report)
    selected: set[tuple[str, int]] = set()
    for match in match_rows:
        part = match.get("part")
        p_idx = match.get("p_idx")
        if part is None or p_idx is None:
            continue
        selected.update({(part, int(p_idx) - 1), (part, int(p_idx)), (part, int(p_idx) + 1)})
        if match.get("p_idx_end") is not None:
            selected.add((part, int(match["p_idx_end"])))
    excerpts = []
    for part, rows in by_part.items():
        for row in rows:
            key = (part, int(row.get("p_idx", -1)))
            if key not in selected:
                continue
            excerpts.append({
                "part": part,
                "p_idx": row.get("p_idx"),
                "para_id": row.get("para_id"),
                "text": (row.get("text") or "")[:max_chars],
            })
    return excerpts


def build_decision_queue(reports: list[dict[str, Any]], audit: dict[str, Any]) -> dict[str, Any]:
    reports_by_role = {item.get("role"): item for item in reports}
    items = []
    for match in audit.get("comment_matches", []):
        reviewer_report = reports_by_role.get("Reviewers", {})
        source_comment = next(
            (item for item in reviewer_report.get("comments", []) if str(item.get("id")) == str(match.get("comment_id"))),
            {},
        )
        evidence = []
        for document_match in match.get("confirmed_matches", []) + match.get("candidate_matches", [])[:3]:
            target_report = reports_by_role.get(document_match.get("document"), {})
            for excerpt in _context_excerpts(target_report, [document_match]):
                evidence.append({"document": document_match.get("document"), **excerpt})
        if source_comment.get("anchor_paragraph"):
            evidence.insert(0, {
                "document": "Reviewers",
                "part": source_comment.get("part"),
                "p_idx": source_comment.get("p_idx"),
                "text": source_comment.get("anchor_paragraph", "")[:1800],
                "evidence_role": "comment-anchor-source-paragraph",
            })
        items.append({
            "comment_id": match.get("comment_id"),
            "reviewer": match.get("reviewer"),
            "comment": match.get("comment"),
            "reply_anchor": match.get("reply_anchor"),
            "target_status": match.get("target_status"),
            "confirmed_matches": match.get("confirmed_matches", []),
            "candidate_matches": match.get("candidate_matches", [])[:3],
            "evidence_excerpts": evidence[:12],
            "decision": {
                "status": "pending",
                "disposition": None,
                "rationale": None,
                "proposed_targets": [],
                "response_outline": None,
                "supporting_evidence": [],
                "missing_evidence_or_questions": [],
                "confidence": None,
                "requires_user_approval": True,
            },
        })
    return {
        "schema_version": "1.0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_files": audit.get("source_files", []),
        "instructions": [
            "This is a compact evidence packet, not an automatic revision decision.",
            "Decide one reviewer comment at a time; use only the supplied excerpts and verified user-provided evidence.",
            "Do not infer scientific results, numerical values, causal claims, or missing experimental evidence.",
            "If evidence is insufficient, request only the exact additional paragraph/table/figure evidence needed.",
            "Propose whether the response belongs in the Manuscript, SI, Reply only, multiple documents, or needs clarification.",
            "Show exact target locations and a concise rationale; do not modify DOCX files.",
            "All proposed changes remain pending until the user explicitly approves the revision plan.",
        ],
        "items": items,
    }


def write_audit_markdown(audit: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# Co_Reviewer Deterministic Review Audit",
        "",
        f"Generated: {audit.get('generated_at_utc', '')}",
        f"Reviewer comments: {audit.get('comment_count', 0)}",
        "",
        "## Comment-to-document matching",
        "",
    ]
    for key, count in sorted(audit.get("comment_target_status_counts", {}).items()):
        lines.append(f"- {key}: {count}")
    for item in audit.get("comment_matches", []):
        lines.extend([
            "",
            f"### Comment {item.get('comment_id')} — {item.get('target_status')}",
            f"- Reviewer: {item.get('reviewer')}",
            f"- Comment: {item.get('comment')}",
            f"- Native selected text: {item.get('reply_anchor', {}).get('text') or '[not recoverable]'}",
        ])
        for match in item.get("confirmed_matches", []):
            lines.append(
                f"- Exact target: {match.get('document')} / {match.get('part')} / P[{match.get('p_idx')}]"
            )
        for match in item.get("candidate_matches", [])[:3]:
            lines.append(
                f"- Candidate only: {match.get('document')} / {match.get('part')} / P[{match.get('p_idx')}] "
                f"(overlap {match.get('match_score')})"
            )
    numbering = audit.get("figure_table_numbering", {})
    lines.extend(["", "## Figure/table numbering flags", ""])
    lines.append(f"- Unresolved references: {len(numbering.get('unresolved_references', []))}")
    lines.append(f"- Unreferenced captions: {len(numbering.get('unreferenced_captions', []))}")
    lines.append(f"- Duplicate labels across documents: {len(numbering.get('duplicate_labels_across_documents', []))}")
    for role, details in numbering.get("by_document", {}).items():
        lines.extend([
            "",
            f"### {role}",
            f"- Duplicate captions: {len(details.get('duplicate_captions', []))}",
            f"- Numbering gaps: {len(details.get('numbering_gaps', []))}",
            f"- Out-of-order first mentions: {len(details.get('out_of_order_first_mentions', []))}",
        ])
    lines.extend([
        "",
        "## Safety boundary",
        "",
        "This report flags evidence and consistency issues. It does not renumber figures/tables, "
        "choose scientific revisions, or modify any DOCX file.",
        "",
    ])
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cached_inspection(
    path: str | Path,
    comment_author: str | None,
    cache_dir: str | Path,
    inspector: Callable[[str, str | None], dict[str, Any]],
    refresh: bool = False,
) -> tuple[dict[str, Any], str]:
    """Reuse parsed DOCX inventory only when source SHA-256 and cache schema match."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"DOCX input does not exist: {source}")
    source_hash = sha256_file(source)
    author_key = hashlib.sha256((comment_author or "").casefold().encode("utf-8")).hexdigest()[:10]
    cache_path = Path(cache_dir) / f"docx-v{CACHE_SCHEMA_VERSION}-{source_hash}-{author_key}.json"

    if not refresh and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                cached.get("cache_schema_version") == CACHE_SCHEMA_VERSION
                and cached.get("source_sha256") == source_hash
                and isinstance(cached.get("report"), dict)
            ):
                report = cached["report"]
                report["file"] = str(source)
                report["source_sha256"] = source_hash
                return report, "hit"
        except (OSError, json.JSONDecodeError):
            pass

    report = inspector(str(source), comment_author)
    final_hash = sha256_file(source)
    if final_hash != source_hash:
        raise RuntimeError(f"Source DOCX changed while it was being inspected: {source}")
    report["file"] = str(source)
    report["source_sha256"] = source_hash
    payload = {
        "cache_schema_version": CACHE_SCHEMA_VERSION,
        "source_sha256": source_hash,
        "cached_at_utc": datetime.now(timezone.utc).isoformat(),
        "report": report,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n",
            prefix=f".{cache_path.name}.", suffix=".tmp",
            dir=cache_path.parent, delete=False,
        ) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, cache_path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return report, "miss"


def cache_summary(cache_results: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "hits": sum(1 for _, status in cache_results if status == "hit"),
        "misses": sum(1 for _, status in cache_results if status == "miss"),
        "files": [{"file": name, "status": status} for name, status in cache_results],
    }

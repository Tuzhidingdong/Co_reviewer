# -*- coding: utf-8 -*-
"""High-level edit-intent compiler producing validated revision_plan.json payloads."""
from __future__ import annotations

import difflib
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree as ET

from .document_engine import (
    NS,
    _compute_auto_format_map_for_paragraph,
    parse_xml,
    safe_part,
    visible_text,
)
from .v232_contract import extract_immutable_tokens


_MARKUP_TOKENS = (
    ("**", {"b": True}),
    ("*", {"i": True}),
    ("^", {"vertAlign": "superscript"}),
    ("~", {"vertAlign": "subscript"}),
)


def parse_inline_format_markup(
    text: str,
    *,
    include_offsets: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse deterministic inline markup (*italic*, **bold**, ^sup^, ~sub~).

    Supports backslash-escaped delimiters (`\\*`, `\\^`, `\\~`, `\\\\`) and leaves
    unmatched delimiters as literal characters. Returns `(plain_text, format_operations)`.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    out: list[str] = []
    operations: list[dict[str, Any]] = []
    out_len = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and text[i + 1] in {"\\", "*", "^", "~"}:
            out.append(text[i + 1])
            out_len += 1
            i += 2
            continue

        matched_delim = None
        matched_props = None
        for delim, props in _MARKUP_TOKENS:
            if text.startswith(delim, i):
                matched_delim = delim
                matched_props = props
                break

        if matched_delim is not None and matched_props is not None:
            close_idx = -1
            search_pos = i + len(matched_delim)
            while search_pos < n:
                if text[search_pos] == "\\" and search_pos + 1 < n:
                    search_pos += 2
                    continue
                if text.startswith(matched_delim, search_pos):
                    close_idx = search_pos
                    break
                search_pos += 1
            if close_idx > i + len(matched_delim):
                inner_raw = text[i + len(matched_delim):close_idx]
                inner_plain, _nested = parse_inline_format_markup(inner_raw)
                if inner_plain:
                    op: dict[str, Any] = {
                        "find": inner_plain,
                        "properties": dict(matched_props),
                    }
                    if include_offsets:
                        op["_offset"] = out_len
                    out.append(inner_plain)
                    out_len += len(inner_plain)
                    operations.append(op)
                    i = close_idx + len(matched_delim)
                    continue

        out.append(ch)
        out_len += 1
        i += 1

    return "".join(out), operations


def normalize_format_properties(spec: dict[str, Any]) -> dict[str, Any]:
    """Normalize shorthand format keys into OOXML FormatChange property dicts."""
    if not isinstance(spec, dict) or not spec:
        raise ValueError("Format properties specification must be a non-empty object.")

    normalized: dict[str, Any] = {}
    for key, value in spec.items():
        if key in {"italic", "i"}:
            normalized["i"] = bool(value)
        elif key in {"bold", "b"}:
            normalized["b"] = bool(value)
        elif key == "vertAlign":
            normalized["vertAlign"] = value
        elif key == "font":
            if isinstance(value, str):
                face = value.strip()
                if not face:
                    raise ValueError("font name must be a non-empty string.")
                normalized["rFonts"] = {"ascii": face, "hAnsi": face}
            elif isinstance(value, dict):
                normalized["rFonts"] = dict(value)
            else:
                raise ValueError("font must be a font family name string or rFonts mapping.")
        elif key == "rFonts":
            normalized["rFonts"] = dict(value) if isinstance(value, dict) else value
        elif key == "color":
            if not isinstance(value, str):
                raise ValueError("color must be a hex string or 'auto'.")
            cleaned = value.strip()
            if cleaned.startswith("#"):
                cleaned = cleaned[1:]
            normalized["color"] = cleaned
        elif key in {"size", "sz"}:
            normalized["sz"] = value
        elif key in {"underline", "u"}:
            if isinstance(value, bool):
                normalized["u"] = "single" if value else "none"
            else:
                normalized["u"] = value
        else:
            normalized[key] = value
    return normalized


def compute_token_authorizations(old: str, new: str) -> dict[str, Any]:
    """Compute exact token authorizations from old/new immutable token diffs."""
    before = extract_immutable_tokens(old)
    after = extract_immutable_tokens(new)
    before_keys = [(item["kind"], item["normalized"]) for item in before]
    after_keys = [(item["kind"], item["normalized"]) for item in after]

    matcher = difflib.SequenceMatcher(a=before_keys, b=after_keys, autojunk=False)
    tokens: list[dict[str, str]] = []
    insertions: list[dict[str, str]] = []
    deletions: list[dict[str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_group, new_group = before[i1:i2], after[j1:j2]
        if not old_group and new_group:
            for tok in new_group:
                insertions.append({"kind": tok["kind"], "text": tok["text"]})
        elif old_group and not new_group:
            for tok in old_group:
                deletions.append({"kind": tok["kind"], "text": tok["text"]})
        elif len(old_group) == len(new_group):
            for old_tok, new_tok in zip(old_group, new_group):
                tokens.append({
                    "kind": old_tok["kind"],
                    "text": old_tok["text"],
                    "replacement": new_tok["text"],
                })
        else:
            for tok in old_group:
                deletions.append({"kind": tok["kind"], "text": tok["text"]})
            for tok in new_group:
                insertions.append({"kind": tok["kind"], "text": tok["text"]})

    result: dict[str, Any] = {
        "intent": "Compiled authorization for modified protected tokens",
    }
    if tokens:
        result["explicitly_authorized_tokens"] = tokens
    if insertions:
        result["explicitly_authorized_insertions"] = insertions
    if deletions:
        result["explicitly_authorized_deletions"] = deletions
    return result


def locate_paragraph_in_docx(
    docx_path: Path,
    find_text: str,
    *,
    part: str = "word/document.xml",
    target_p_idx: int | None = None,
) -> tuple[int, ET.Element]:
    """Locate the unique paragraph containing `find_text` in `docx_path`."""
    if not isinstance(find_text, str) or not find_text.strip():
        raise ValueError("find text must be a non-empty string.")
    resolved_part = safe_part(part)
    resolved_path = Path(docx_path).expanduser().resolve()
    if not resolved_path.is_file():
        raise ValueError(f"Source DOCX does not exist: {resolved_path}")

    with zipfile.ZipFile(resolved_path, "r") as archive:
        if resolved_part not in archive.namelist():
            raise ValueError(f"Word XML part not present in {resolved_path}: {resolved_part}")
        root = parse_xml(archive.read(resolved_part))

    paras = (
        root.findall(".//w:body//w:p", NS)
        if resolved_part == "word/document.xml"
        else root.findall(".//w:p", NS)
    )
    if target_p_idx is not None:
        if not isinstance(target_p_idx, int) or isinstance(target_p_idx, bool) or target_p_idx < 0 or target_p_idx >= len(paras):
            raise ValueError(f"target_p_idx {target_p_idx!r} outside {resolved_part} paragraph range (0..{len(paras)-1}).")
        para = paras[target_p_idx]
        count = visible_text(para).count(find_text)
        if count != 1:
            raise ValueError(
                f"Target {find_text[:80]!r} occurred {count} times in {resolved_part} P[{target_p_idx}]; expected exactly 1."
            )
        return target_p_idx, para

    matches = [(idx, p) for idx, p in enumerate(paras) if find_text in visible_text(p)]
    if not matches:
        raise ValueError(f"Target {find_text[:80]!r} not found in {resolved_part}.")
    if len(matches) > 1:
        raise ValueError(f"Target {find_text[:80]!r} is ambiguous across {len(matches)} paragraphs in {resolved_part}.")
    idx, para = matches[0]
    if visible_text(para).count(find_text) != 1:
        raise ValueError(f"Target {find_text[:80]!r} occurs multiple times within {resolved_part} P[{idx}].")
    return idx, para


def _resolve_doc_paths(
    intent: dict[str, Any],
    doc_registry: dict[str, dict[str, str]],
    base_dir: Path | None,
) -> tuple[str, str, str]:
    doc_role = str(intent.get("doc_role") or intent.get("document") or "Manuscript")
    if doc_role not in {"Manuscript", "SI", "Reviewers", "Other"}:
        raise ValueError(f"Unsupported doc_role {doc_role!r}; expected Manuscript, SI, Reviewers, or Other.")

    role_entry = doc_registry.get(doc_role, {})
    input_file = intent.get("input_file") or role_entry.get("input_file")
    output_file = intent.get("output_file") or role_entry.get("output_file")
    if not isinstance(input_file, str) or not input_file.strip():
        raise ValueError(f"Missing input_file for doc_role {doc_role!r}.")
    if not isinstance(output_file, str) or not output_file.strip():
        raise ValueError(f"Missing output_file for doc_role {doc_role!r}.")

    def _resolve(raw: str) -> str:
        p = Path(raw).expanduser()
        if not p.is_absolute() and base_dir is not None:
            p = (base_dir / p).resolve()
        else:
            p = p.resolve()
        return str(p)

    return doc_role, _resolve(input_file), _resolve(output_file)


def _normalize_format_intent(
    format_intent: Any,
    default_find: str,
) -> list[dict[str, Any]]:
    if format_intent is None:
        return []
    items = format_intent if isinstance(format_intent, list) else [format_intent]
    ops: list[dict[str, Any]] = []
    for entry in items:
        if not isinstance(entry, dict):
            raise ValueError("format_intent entries must be objects.")
        if "properties" in entry:
            find_str = entry.get("find") or default_find
            raw_props = entry["properties"]
        else:
            find_str = entry.get("find") or default_find
            raw_props = {k: v for k, v in entry.items() if k != "find"}
        if not isinstance(find_str, str) or not find_str:
            raise ValueError("format_intent target find text must be non-empty.")
        ops.append({
            "find": find_str,
            "properties": normalize_format_properties(raw_props),
        })
    return ops


def compile_revision_plan(
    intents_payload: dict[str, Any] | list[dict[str, Any]],
    *,
    base_dir: Path | None = None,
    doc_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Compile concise high-level edit intents into a valid revision_plan.json dict."""
    doc_registry: dict[str, dict[str, str]] = {}
    if isinstance(intents_payload, dict):
        raw_docs = intents_payload.get("documents", {})
        if isinstance(raw_docs, dict):
            for role, entry in raw_docs.items():
                if isinstance(entry, dict):
                    doc_registry[role] = dict(entry)
        raw_intents = intents_payload.get("intents")
        if raw_intents is None:
            raw_intents = intents_payload.get("items")
        if raw_intents is None:
            raw_intents = intents_payload.get("edits")
    elif isinstance(intents_payload, list):
        raw_intents = intents_payload
    else:
        raise ValueError("intents_payload must be an object or an array of edit intents.")

    if doc_map:
        for role, entry in doc_map.items():
            if isinstance(entry, dict):
                merged = dict(doc_registry.get(role, {}))
                merged.update(entry)
                doc_registry[role] = merged

    if not isinstance(raw_intents, list) or not raw_intents:
        raise ValueError("intents_payload must contain at least one edit intent.")

    sha_cache: dict[str, str] = {}
    revision_items: list[dict[str, Any]] = []

    for index, intent in enumerate(raw_intents, start=1):
        if not isinstance(intent, dict):
            raise ValueError(f"intents[{index - 1}] must be an object.")
        item_id = str(intent.get("id") or f"REV-{index:03d}")
        reviewer = str(intent.get("reviewer") or "Author").strip() or "Author"
        action = str(intent.get("action") or "replace")
        if action not in {"replace", "insert_paragraph"}:
            raise ValueError(f"{item_id}: unsupported action {action!r}.")
        is_paragraph_insertion = action == "insert_paragraph"
        find_raw = (
            intent.get("anchor_text", intent.get("find"))
            if is_paragraph_insertion else intent.get("find")
        )
        if not isinstance(find_raw, str) or not find_raw.strip():
            raise ValueError(
                f"{item_id}: {'anchor_text' if is_paragraph_insertion else 'find'} must be non-empty."
            )
        replace_raw = (
            intent.get("inserted_text", intent.get("replace"))
            if is_paragraph_insertion else intent.get("replace")
        )
        if replace_raw is None and not is_paragraph_insertion:
            replace_raw = find_raw
        if not isinstance(replace_raw, str) or (is_paragraph_insertion and not replace_raw.strip()):
            raise ValueError(
                f"{item_id}: {'inserted_text' if is_paragraph_insertion else 'replace'} must be a "
                f"{'non-empty ' if is_paragraph_insertion else ''}string."
            )
        if is_paragraph_insertion:
            if intent.get("position") not in {"before", "after"}:
                raise ValueError(f"{item_id}: insert_paragraph requires position=before or after.")
            if intent.get("part", "word/document.xml") != "word/document.xml":
                raise ValueError(f"{item_id}: insert_paragraph currently supports word/document.xml only.")

        part = safe_part(str(intent.get("part", "word/document.xml")))
        doc_role, input_file, output_file = _resolve_doc_paths(intent, doc_registry, base_dir)
        target_p_idx = intent.get("target_p_idx") if "target_p_idx" in intent else intent.get("p_idx")

        p_idx, para = locate_paragraph_in_docx(
            Path(input_file), find_raw, part=part, target_p_idx=target_p_idx,
        )
        if input_file not in sha_cache:
            sha_cache[input_file] = hashlib.sha256(Path(input_file).read_bytes()).hexdigest()
        source_sha256 = sha_cache[input_file]

        if is_paragraph_insertion:
            plain_replace, inline_ops, explicit_ops = replace_raw, [], []
        else:
            plain_replace, inline_ops = parse_inline_format_markup(replace_raw, include_offsets=True)
            explicit_ops = _normalize_format_intent(intent.get("format_intent"), plain_replace)
        after_para_text = (
            visible_text(para).replace(find_raw, plain_replace, 1)
            if not is_paragraph_insertion else ""
        )
        scope_is_unique = after_para_text.count(plain_replace) == 1
        for op in inline_ops:
            offset = op.pop("_offset", None)
            if scope_is_unique and offset is not None:
                op["scope_find"] = plain_replace
                op["scope_offset"] = offset
        for op in explicit_ops:
            if scope_is_unique and plain_replace.count(op["find"]) == 1:
                op["scope_find"] = plain_replace
                op["scope_offset"] = plain_replace.index(op["find"])
        format_ops = inline_ops + explicit_ops

        if not is_paragraph_insertion and find_raw == plain_replace and not format_ops:
            raise ValueError(f"{item_id}: no-op intent (find equals replace with no format operations).")

        intent_desc = str(
            intent.get("intent")
            or intent.get("rationale")
            or intent.get("comment")
            or f"Compiled edit for {doc_role} P[{p_idx}]"
        ).strip()
        comment = str(intent.get("comment") or intent_desc).strip()

        targets: list[dict[str, Any]] = []
        if is_paragraph_insertion:
            raw_insertions = intent.get("explicitly_authorized_insertions", [])
            if not isinstance(raw_insertions, list):
                raise ValueError(f"{item_id}: explicitly_authorized_insertions must be an array.")
            insertion_auth = {
                "intent": intent_desc,
                "explicitly_authorized_insertions": raw_insertions,
            }
            targets.append({
                "document": doc_role,
                "input_file": input_file,
                "output_file": output_file,
                "part": part,
                "p_idx": p_idx,
                "source_sha256": source_sha256,
                "find": find_raw,
                "replace": plain_replace,
                "operation_id": str(intent.get("operation_id") or f"{item_id}-OP001"),
                "action": "insert_paragraph",
                "anchor_text": find_raw,
                "position": intent["position"],
                "inserted_text": plain_replace,
                "authorized_change": insertion_auth,
            })
        elif find_raw != plain_replace:
            token_auth = compute_token_authorizations(find_raw, plain_replace)
            token_auth["intent"] = intent_desc
            text_target: dict[str, Any] = {
                "document": doc_role,
                "input_file": input_file,
                "output_file": output_file,
                "part": part,
                "p_idx": p_idx,
                "source_sha256": source_sha256,
                "find": find_raw,
                "replace": plain_replace,
                "authorized_change": token_auth,
            }
            auto_map = _compute_auto_format_map_for_paragraph(
                para, find_raw, plain_replace, allow_dominant_replace_fallback=False,
            )
            if auto_map is not None:
                text_target["replacement_format_map"] = auto_map
            targets.append(text_target)

        if format_ops:
            format_target: dict[str, Any] = {
                "document": doc_role,
                "input_file": input_file,
                "output_file": output_file,
                "part": part,
                "p_idx": p_idx,
                "source_sha256": source_sha256,
                "find": plain_replace,
                "replace": plain_replace,
                "authorized_change": {"intent": intent_desc},
                "format_authorization": {"intent": intent_desc},
                "format_operations": format_ops,
            }
            targets.append(format_target)

        revision_items.append({
            "id": item_id,
            "reviewer": reviewer,
            "comment": comment,
            "status": "planned",
            "targets": targets,
        })

    return {
        "schema_version": "1.0",
        "approved": False,
        "revision_items": revision_items,
    }

# -*- coding: utf-8 -*-
"""High-level edit-intent compiler producing validated revision_plan.json payloads."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Mapping

from lxml import etree as ET

from .document_engine import (
    NS,
    _compute_auto_format_map_for_paragraph,
    parse_xml,
    validate_replacement_format_map_in_paragraph,
    _script_character,
    authorized_ranges_from_format_operations,
    safe_part,
    visible_text,
)
from .v232_contract import extract_immutable_tokens
from .insert_authorization import (
    build_insert_authorization,
    validate_insert_authorization,
)


def _has_script_character(character: str) -> bool:
    try:
        return _script_character(character) is not None
    except ValueError:
        return False


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
                inner_plain, nested_ops = parse_inline_format_markup(
                    inner_raw, include_offsets=True)
                if inner_plain:
                    op: dict[str, Any] = {
                        "find": inner_plain,
                        "properties": dict(matched_props),
                    }
                    if include_offsets:
                        op["_offset"] = out_len
                    operations.append(op)
                    # Inner markup is a refinement of the outer format, not a
                    # replacement for it. Merge both property sets onto the
                    # exact inner character range so bold+italic/sub/sup
                    # combinations survive serialization.
                    for nested in nested_ops:
                        merged_props = dict(matched_props)
                        merged_props.update(nested.get("properties", {}))
                        merged: dict[str, Any] = {
                            "find": nested["find"],
                            "properties": merged_props,
                        }
                        if include_offsets:
                            merged["_offset"] = out_len + int(nested.get("_offset", 0))
                        operations.append(merged)
                    out.append(inner_plain)
                    out_len += len(inner_plain)
                    i = close_idx + len(matched_delim)
                    continue

        out.append(ch)
        out_len += 1
        i += 1

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for operation in operations:
        key = (
            operation.get("find"),
            tuple(sorted((operation.get("properties") or {}).items())),
            operation.get("_offset"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(operation)
    return "".join(out), deduped


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


def canonicalize_explicit_nested_format_ops(intent: dict[str, Any]) -> list[dict[str, Any]]:
    """Promote side-channel nested format ops into canonical format_intent data."""
    raw = intent.get("explicit_nested_ops")
    if not isinstance(raw, list) or not raw:
        return []
    plain, _ = parse_inline_format_markup(intent.get("replace", ""))
    ops: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        find = entry.get("find")
        properties = entry.get("properties")
        if not isinstance(find, str) or not find or not isinstance(properties, dict) or not properties:
            continue
        normalized = normalize_format_properties(properties)
        key = (find, json.dumps(normalized, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        op: dict[str, Any] = {"find": find, "properties": normalized}
        if plain.count(find) == 1:
            op["scope_find"] = plain
            op["scope_offset"] = plain.index(find)
        ops.append(op)
    return ops


def _format_segments_from_ops(
    operations: list[dict[str, Any]],
    plain_replace: str,
) -> list[dict[str, Any]]:
    """Reduce format operations to non-overlapping character-range segments."""
    if not plain_replace:
        return []
    per_char: list[dict[str, Any]] = [{} for _ in plain_replace]
    for op in operations:
        if not isinstance(op, dict):
            continue
        find = op.get("find")
        properties = op.get("properties") or {}
        if not isinstance(find, str) or not find:
            continue
        offset = op.get("_offset", op.get("scope_offset"))
        if offset is None and plain_replace.count(find) == 1:
            offset = plain_replace.index(find)
        if not isinstance(offset, int):
            continue
        for index in range(offset, min(offset + len(find), len(plain_replace))):
            per_char[index].update(properties)
    segments: list[dict[str, Any]] = []
    start = 0
    current = per_char[0]
    for index in range(1, len(plain_replace) + 1):
        if index == len(plain_replace) or per_char[index] != current:
            if current:
                segments.append({
                    "start": start, "end": index, "properties": dict(current),
                })
            if index < len(plain_replace):
                start = index
                current = per_char[index]
    return segments


def _check_format_operation_contract(
    item_id: str,
    expected: list[dict[str, Any]],
    compiled: list[dict[str, Any]],
    plain_replace: str,
    *,
    scope_is_unique: bool,
) -> None:
    """Fail closed when a character-level format contract was altered."""
    seen_keys: set[tuple[Any, ...]] = set()
    for op in compiled:
        key = (
            op.get("find"),
            json.dumps(op.get("properties") or {}, sort_keys=True, ensure_ascii=False),
            op.get("scope_offset"),
        )
        if key in seen_keys:
            raise ValueError(
                f"{item_id}: FORMAT_OPERATION_CONTRACT_BLOCKED: duplicate format operation {key!r}"
            )
        seen_keys.add(key)
    actual = _format_segments_from_ops(compiled, plain_replace)
    expected_normalized = [
        {"start": row["start"], "end": row["end"],
         "properties": dict(row["properties"])}
        for row in expected
    ]
    if actual != expected_normalized:
        raise ValueError(
            f"{item_id}: FORMAT_OPERATION_CONTRACT_BLOCKED: "
            f"expected {expected_normalized!r}, compiled {actual!r}"
        )


def compile_revision_plan(
    intents_payload: dict[str, Any] | list[dict[str, Any]],
    *,
    base_dir: Path | None = None,
    doc_map: dict[str, dict[str, str]] | None = None,
    instruction_coverage: Mapping[str, Any] | None = None,
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
            if intent.get("replacement_format_map") is not None:
                raise ValueError(f"{item_id}: replacement_format_map is not valid for insert_paragraph.")
            plain_replace, inline_ops, explicit_ops = replace_raw, [], []
        else:
            plain_replace, inline_ops = parse_inline_format_markup(replace_raw, include_offsets=True)
            explicit_ops = _normalize_format_intent(intent.get("format_intent"), plain_replace)
            if intent.get("replacement_format_map") is not None and find_raw == plain_replace:
                raise ValueError(
                    f"{item_id}: replacement_format_map requires an actual text replacement; "
                    "use a separately scoped FormatChange for format-only edits."
                )
        side_ops = canonicalize_explicit_nested_format_ops(intent)
        explicit_ops = list(explicit_ops) + side_ops
        expected_inline_contract = _format_segments_from_ops(
            inline_ops + explicit_ops, plain_replace)
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
        if scope_is_unique and format_ops:
            # Overlapping inline refinements (for example italic inside bold)
            # must become one scoped operation per character range; otherwise
            # Pass-2 refuses a second revision on the same Run.
            format_ops = [
                {
                    "find": plain_replace[row["start"]:row["end"]],
                    "properties": dict(row["properties"]),
                    "scope_find": plain_replace,
                    "scope_offset": row["start"],
                }
                for row in _format_segments_from_ops(format_ops, plain_replace)
            ]
        authorized_ranges = authorized_ranges_from_format_operations(
            format_ops, plain_replace)
        if expected_inline_contract:
            _check_format_operation_contract(
                item_id, expected_inline_contract, format_ops, plain_replace,
                scope_is_unique=scope_is_unique,
            )

        if not is_paragraph_insertion and find_raw == plain_replace and not format_ops:
            raise ValueError(f"{item_id}: no-op intent (find equals replace with no format operations).")

        intent_desc = str(
            intent.get("intent")
            or intent.get("rationale")
            or intent.get("comment")
            or f"Compiled edit for {doc_role} P[{p_idx}]"
        ).strip()
        comment = str(intent.get("comment") or intent_desc).strip()
        instruction_requirement_ids = intent.get("instruction_requirement_ids", [])
        if (
            not isinstance(instruction_requirement_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in instruction_requirement_ids)
            or len(instruction_requirement_ids) != len(set(instruction_requirement_ids))
        ):
            raise ValueError(
                f"{item_id}: instruction_requirement_ids must be a unique list "
                "of exact source Coverage requirement IDs."
            )
        instruction_binding = (
            {"instruction_requirement_ids": list(instruction_requirement_ids)}
            if instruction_requirement_ids else {}
        )
        format_requirement_ids = intent.get(
            "format_instruction_requirement_ids", instruction_requirement_ids
        )
        if (
            not isinstance(format_requirement_ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in format_requirement_ids)
            or len(format_requirement_ids) != len(set(format_requirement_ids))
        ):
            raise ValueError(
                f"{item_id}: format_instruction_requirement_ids must be a unique list "
                "of exact source Coverage requirement IDs."
            )
        format_instruction_binding = (
            {"instruction_requirement_ids": list(format_requirement_ids)}
            if format_requirement_ids else {}
        )

        targets: list[dict[str, Any]] = []

        def stable_target_id(kind: str, ordinal: int) -> str:
            seed = "|".join([
                item_id, kind, str(ordinal), doc_role, part, str(p_idx), find_raw, plain_replace,
            ])
            return "TGT-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

        if is_paragraph_insertion:
            raw_insertions = intent.get("explicitly_authorized_insertions")
            if raw_insertions is not None and not isinstance(raw_insertions, list):
                raise ValueError(f"{item_id}: explicitly_authorized_insertions must be an array.")
            protected_rows = extract_immutable_tokens(plain_replace)
            if not protected_rows:
                insertion_auth = {
                    "intent": intent_desc,
                    "explicitly_authorized_insertions": raw_insertions or [],
                }
            else:
                if not isinstance(instruction_coverage, Mapping):
                    raise ValueError(
                        f"{item_id}: INSERT_AUTHORIZATION_SOURCE_REQUIRED: inserted text contains "
                        "protected tokens; compile-plan requires instruction Coverage"
                    )
                if not instruction_requirement_ids:
                    raise ValueError(
                        f"{item_id}: INSERT_AUTHORIZATION_REQUIREMENT_MISSING: no "
                        "instruction_requirement_ids for the inserted text"
                    )
                requirement_id = instruction_requirement_ids[0]
                if raw_insertions is None:
                    insertion_auth = build_insert_authorization(
                        item_id=item_id,
                        target_index=0,
                        inserted_text=plain_replace,
                        instruction_coverage=instruction_coverage,
                        requirement_id=requirement_id,
                    )
                else:
                    insertion_auth = {
                        "intent": intent_desc,
                        "explicitly_authorized_insertions": raw_insertions,
                    }
                    if "authorization_source" in intent:
                        insertion_auth["authorization_source"] = intent["authorization_source"]
                    auth_errors = validate_insert_authorization(
                        item_id=item_id,
                        target_index=0,
                        inserted_text=plain_replace,
                        authorized_change=insertion_auth,
                        instruction_coverage=instruction_coverage,
                    )
                    if auth_errors:
                        raise ValueError(
                            f"{item_id}: INSERT_AUTHORIZATION_CHECK_FAILED:\n- "
                            + "\n- ".join(auth_errors)
                        )
            targets.append({
                "target_id": stable_target_id("insert", 0),
                "document": doc_role,
                "input_file": input_file,
                "output_file": output_file,
                "part": part,
                "p_idx": p_idx,
                "source_sha256": source_sha256,
                "find": find_raw,
                "replace": plain_replace,
                "operation_id": str(intent.get("operation_id") or f"{item_id}-OP001"),
                "target_identity_mode": "legacy_p_idx_explicit",
                "action": "insert_paragraph",
                "anchor_text": find_raw,
                "position": intent["position"],
                "inserted_text": plain_replace,
                "authorized_change": insertion_auth,
                **instruction_binding,
            })
            inserted_format_map = intent.get("inserted_format_map")
            if inserted_format_map is not None:
                if not isinstance(inserted_format_map, list) or not inserted_format_map:
                    raise ValueError(f"{item_id}: inserted_format_map must be a non-empty array when supplied.")
                targets[-1]["inserted_format_map"] = inserted_format_map
        elif find_raw != plain_replace:
            token_auth = compute_token_authorizations(find_raw, plain_replace)
            token_auth["intent"] = intent_desc
            text_target: dict[str, Any] = {
                "target_id": stable_target_id("text", 0),
                "document": doc_role,
                "input_file": input_file,
                "output_file": output_file,
                "part": part,
                "p_idx": p_idx,
                "source_sha256": source_sha256,
                "find": find_raw,
                "replace": plain_replace,
                "authorized_change": token_auth,
                **instruction_binding,
            }
            script_authorized = any(
                isinstance(op.get("properties"), dict)
                and op["properties"].get("vertAlign") in {"baseline", "superscript", "subscript"}
                for op in format_ops
            ) or any(_has_script_character(ch) for ch in plain_replace)
            explicit_map = intent.get("replacement_format_map")
            if explicit_map is not None:
                if not isinstance(explicit_map, list) or not explicit_map:
                    raise ValueError(
                        f"{item_id}: replacement_format_map must be a non-empty array when explicitly supplied."
                    )
                validate_replacement_format_map_in_paragraph(
                    para, find_raw, plain_replace, explicit_map,
                    allow_script_inheritance=script_authorized,
                    authorized_ranges=authorized_ranges)
                text_target["replacement_format_map"] = explicit_map
            else:
                auto_map = _compute_auto_format_map_for_paragraph(
                    para, find_raw, plain_replace, allow_dominant_replace_fallback=False,
                )
                if auto_map is not None:
                    validate_replacement_format_map_in_paragraph(
                        para, find_raw, plain_replace, auto_map,
                        allow_script_inheritance=script_authorized,
                        authorized_ranges=authorized_ranges)
                    text_target["replacement_format_map"] = auto_map
            targets.append(text_target)

        if format_ops:
            format_target: dict[str, Any] = {
                "target_id": stable_target_id("format", len(targets)),
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
                **({"format_operation_contract": expected_inline_contract}
                   if expected_inline_contract else {}),
                **format_instruction_binding,
            }
            if find_raw != plain_replace and targets:
                format_target["applies_to_target_index"] = len(targets) - 1
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

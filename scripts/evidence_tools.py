#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Model-agnostic local evidence extraction and cache utilities for Co_Reviewer V0.3."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_VERSION = "0.5.1"
CACHE_SCHEMA = 2
LINE_NUMBER_RE = re.compile(r"^\d{1,4}$")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean_text(value: Any, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def write_markdown(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def cache_artifact_hashes(cache_dir: Path) -> dict[str, str]:
    """Hash every regular artifact in one cache entry except its manifest."""
    if not cache_dir.is_dir():
        return {}
    return {
        item.name: sha256_file(item)
        for item in sorted(cache_dir.iterdir(), key=lambda path: path.name)
        if item.is_file() and item.name != "manifest.json"
    }


def cache_manifest_matches(
    cache_dir: Path,
    manifest: Any,
    *,
    expected_key: str,
    expected_key_material: dict[str, Any],
    required_artifacts: tuple[str, ...],
) -> bool:
    """Accept a cache hit only when key, parameters and every artifact hash agree."""
    if not isinstance(manifest, dict):
        return False
    hashes = manifest.get("artifacts_sha256")
    if (
        manifest.get("key") != expected_key
        or manifest.get("schema_version") != CACHE_SCHEMA
        or manifest.get("key_material") != expected_key_material
        or not isinstance(hashes, dict)
        or any(not (cache_dir / name).is_file() for name in required_artifacts)
    ):
        return False
    actual = cache_artifact_hashes(cache_dir)
    return actual == hashes and all(name in hashes for name in required_artifacts)


def validate_pages(values: list[int] | None, page_count: int, option_name: str) -> list[int]:
    selected = sorted(set(values or []))
    invalid = [page for page in selected if page < 1 or page > page_count]
    if invalid:
        raise ValueError(
            f"{option_name} contains page(s) outside 1-{page_count}: "
            + ", ".join(str(page) for page in invalid)
        )
    return selected


def pdf_text_lines(page: Any) -> tuple[list[dict[str, Any]], int, int]:
    lines: list[dict[str, Any]] = []
    text_chars = 0
    data = page.get_text("dict")
    text_blocks = sum(1 for block in data.get("blocks", []) if block.get("type") == 0)
    for block_index, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block.get("lines", [])):
            spans = line.get("spans", [])
            value = "".join(span.get("text", "") for span in spans)
            text_chars += len(value)
            boxes = [span.get("bbox") for span in spans if span.get("bbox")]
            if not value.strip() or not boxes:
                continue
            lines.append({
                "text": value,
                "bbox": [
                    round(min(float(box[0]) for box in boxes), 2),
                    round(min(float(box[1]) for box in boxes), 2),
                    round(max(float(box[2]) for box in boxes), 2),
                    round(max(float(box[3]) for box in boxes), 2),
                ],
                "spans": spans,
            })
    return lines, text_chars, text_blocks


def find_line_numbers(
    lines: list[dict[str, Any]], page_width: float, page_height: float, page_number: int
) -> list[dict[str, Any]]:
    """Detect numeric spans in a left-margin band; candidates require human confirmation."""
    found: list[dict[str, Any]] = []
    for line in lines:
        for span in line["spans"]:
            value = re.sub(r"\s+", "", str(span.get("text", "")))
            box = span.get("bbox")
            if not box or not LINE_NUMBER_RE.fullmatch(value):
                continue
            x0, y0, x1, y1 = (float(item) for item in box)
            if x0 > page_width * 0.12 or y0 < page_height * 0.035 or y1 > page_height * 0.965:
                continue
            found.append({
                "page": page_number, "number": int(value),
                "x": round(x0, 2), "y": round(y0, 2), "text": value,
            })
    return sorted(found, key=lambda item: (item["y"], item["x"]))


def pdf_markdown(report: dict[str, Any]) -> list[str]:
    summary = report["summary"]
    lines = [
        "# PDF evidence summary",
        "",
        f"- Source SHA-256: {report['source']['sha256']}",
        f"- Pages: {summary['page_count']}",
        f"- Pages with selectable text: {summary['pages_with_text']}",
        f"- Pages without selectable text: {summary['pages_without_text']}",
        f"- Extracted text characters: {summary['text_characters']}",
        f"- Candidate line-number spans: {summary['line_number_candidates']}",
        f"- Pages with line-number candidates: {summary['pages_with_line_numbers']}",
        f"- Cache key: {report['cache']['key']}",
        "",
        "> Text-layer and geometry analysis only. This report is not visual layout approval. "
        "Image-only pages require a separate OCR or visual workflow.",
        "",
        "## Page map",
        "",
        "| Page | Size (pt) | Text chars | Blocks | Number candidates | Notes |",
        "|---:|---:|---:|---:|---:|---|",
    ]
    for page in report["pages"]:
        notes = ", ".join(page["findings"]) or "—"
        lines.append(
            f"| {page['page']} | {page['width_pt']} × {page['height_pt']} | "
            f"{page['text_characters']} | {page['text_blocks']} | "
            f"{page['line_number_candidates']} | {notes} |"
        )
    lines.extend(["", "## Findings", ""])
    if report["findings"]:
        for finding in report["findings"]:
            lines.append(
                f"- **{finding['severity']} / {finding['code']}** — page "
                f"{finding.get('page', '—')}: {finding['message']}"
            )
    else:
        lines.append("- No automatic text-layer/geometry findings.")
    lines.extend(["", "## Selected page text", ""])
    if report["selected_page_text"]:
        for page_number, text in report["selected_page_text"].items():
            lines.extend([f"### Page {page_number}", "", text, ""])
    else:
        lines.append("- None. Use --text-pages for bounded excerpts.")
    lines.extend(["", "## Rendered pages", ""])
    lines.extend([f"- {path}" for path in report["rendered_pages"]] or ["- None. Use --render-pages for targeted visual inspection."])
    return lines


def build_pdf_report(
    pdf_path: Path, cache_key: str, render_pages: list[int], text_pages: list[int],
    render_dpi: int, max_text_chars: int, check_line_numbers: bool, work_dir: Path,
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF inspection requires optional PyMuPDF. Install with: "
            "python -m pip install -r requirements-pdf.txt"
        ) from exc

    document = fitz.open(pdf_path)
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise ValueError("The PDF is encrypted and cannot be opened without a password.")
        page_count = document.page_count
        render_pages = validate_pages(render_pages, page_count, "--render-pages")
        text_pages = validate_pages(text_pages, page_count, "--text-pages")
        dimensions = [(float(page.rect.width), float(page.rect.height)) for page in document]
        median_width = statistics.median(x[0] for x in dimensions) if dimensions else 0.0
        median_height = statistics.median(x[1] for x in dimensions) if dimensions else 0.0
        pages: list[dict[str, Any]] = []
        candidates_all: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        selected_text: dict[str, str] = {}
        rendered: list[str] = []
        metadata = {
            key: clean_text(value, 240)
            for key, value in (document.metadata or {}).items() if value
        }

        for index, page in enumerate(document):
            page_number = index + 1
            width, height = dimensions[index]
            lines, text_chars, text_blocks = pdf_text_lines(page)
            candidates = find_line_numbers(lines, width, height, page_number) if check_line_numbers else []
            candidates_all.extend(candidates)
            page_findings: list[str] = []

            if text_chars == 0:
                page_findings.append("no_selectable_text")
                findings.append({
                    "severity": "warning", "code": "no_selectable_text", "page": page_number,
                    "message": "No selectable text detected; this run does not perform OCR.",
                })

            if median_width and median_height and (
                abs(width - median_width) / median_width > 0.15
                or abs(height - median_height) / median_height > 0.15
            ):
                page_findings.append("page_size_outlier")
                findings.append({
                    "severity": "review", "code": "page_size_outlier", "page": page_number,
                    "message": "Page dimensions differ materially from the document median.",
                })

            if any(
                x0 < -1 or y0 < -1 or x1 > width + 1 or y1 > height + 1
                for x0, y0, x1, y1 in (line["bbox"] for line in lines)
            ):
                page_findings.append("text_outside_page_bounds")
                findings.append({
                    "severity": "review", "code": "text_outside_page_bounds", "page": page_number,
                    "message": "At least one text-line bounding box extends beyond the page rectangle.",
                })

            if candidates:
                values = [item["number"] for item in candidates]
                counts = Counter(values)
                duplicates = sorted(number for number, count in counts.items() if count > 1)
                if duplicates:
                    page_findings.append("duplicate_line_number_candidates")
                    findings.append({
                        "severity": "review", "code": "duplicate_line_number_candidates",
                        "page": page_number,
                        "message": "Repeated candidate number(s): " + ", ".join(map(str, duplicates)),
                    })
                decreases = [
                    f"{values[pos - 1]}->{values[pos]}"
                    for pos in range(1, len(values)) if values[pos] <= values[pos - 1]
                ]
                if decreases:
                    page_findings.append("non_increasing_line_numbers")
                    findings.append({
                        "severity": "review", "code": "non_increasing_line_numbers",
                        "page": page_number,
                        "message": "Candidate numbers are not strictly increasing top-to-bottom: "
                        + ", ".join(decreases[:8]),
                    })
                gaps = [
                    f"{values[pos - 1]}->{values[pos]}"
                    for pos in range(1, len(values))
                    if values[pos] - values[pos - 1] > 1
                ]
                if gaps:
                    page_findings.append("line_number_gaps")
                    findings.append({
                        "severity": "review", "code": "line_number_gaps",
                        "page": page_number,
                        "message": "Possible gap(s) in candidate line numbers: " + ", ".join(gaps[:8]),
                    })

            pages.append({
                "page": page_number, "width_pt": round(width, 2), "height_pt": round(height, 2),
                "rotation": int(page.rotation), "text_characters": text_chars,
                "text_blocks": text_blocks,
                "text_lines": len(lines), "line_number_candidates": len(candidates),
                "first_line_number": candidates[0]["number"] if candidates else None,
                "last_line_number": candidates[-1]["number"] if candidates else None,
                "findings": page_findings,
            })

            if page_number in text_pages:
                selected_text[str(page_number)] = clean_text(
                    page.get_text("text", sort=True), max_text_chars
                )
            if page_number in render_pages:
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(render_dpi / 72.0, render_dpi / 72.0),
                    alpha=False, colorspace=fitz.csRGB,
                )
                name = f"page-{page_number:03d}.png"
                pixmap.save(work_dir / name)
                rendered.append(name)

        pages_with_text = sum(1 for page in pages if page["text_characters"] > 0)
        pages_with_line_numbers = sum(1 for page in pages if page["line_number_candidates"] > 0)
        if check_line_numbers and pages_with_line_numbers and pages_with_line_numbers < page_count:
            for page in pages:
                if page["line_number_candidates"] == 0:
                    page["findings"].append("no_line_number_candidates")
                    findings.append({
                        "severity": "warning",
                        "code": "no_line_number_candidates",
                        "page": page["page"],
                        "message": "No left-margin line-number candidate was detected on this page; confirm whether numbering is expected here.",
                    })
        return {
            "schema_version": CACHE_SCHEMA,
            "generated_at_utc": utc_now(),
            "source": {
                "path": str(pdf_path), "sha256": sha256_file(pdf_path),
                "size_bytes": pdf_path.stat().st_size,
            },
            "tool": {
                "name": "Co_Reviewer evidence tools", "version": TOOL_VERSION,
                "pymupdf_version": getattr(fitz, "VersionBind", "unknown"),
            },
            "cache": {"key": cache_key, "hit": False},
            "pdf": {"page_count": page_count, "metadata": metadata},
            "summary": {
                "page_count": page_count, "pages_with_text": pages_with_text,
                "pages_without_text": page_count - pages_with_text,
                "text_characters": sum(page["text_characters"] for page in pages),
                "line_number_candidates": len(candidates_all),
                "pages_with_line_numbers": pages_with_line_numbers,
                "automatic_findings": len(findings),
            },
            "pages": pages, "line_number_candidates": candidates_all,
            "findings": findings, "selected_page_text": selected_text,
            "rendered_pages": rendered,
            "limits": [
                "Line-number detection is heuristic and is not an accuracy guarantee.",
                "No OCR is performed; image-only pages require separate review.",
                "This report does not certify typography, clipping, figure legibility or visual layout.",
            ],
        }
    finally:
        document.close()


def cmd_pdf_inspect(args: argparse.Namespace) -> int:
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("--pdf must point to a PDF file.")
    if not 36 <= args.render_dpi <= 180:
        raise ValueError("--render-dpi must be between 36 and 180.")
    if args.max_text_chars < 100:
        raise ValueError("--max-text-chars must be at least 100.")
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF inspection requires optional PyMuPDF. Install with: "
            "python -m pip install -r requirements-pdf.txt"
        ) from exc

    params = {
        "tool_version": TOOL_VERSION, "cache_schema": CACHE_SCHEMA,
        "pymupdf_version": getattr(fitz, "VersionBind", "unknown"),
        "render_pages": sorted(set(args.render_pages or [])),
        "text_pages": sorted(set(args.text_pages or [])),
        "render_dpi": args.render_dpi, "max_text_chars": args.max_text_chars,
        "check_line_numbers": not args.no_line_number_check,
    }
    key_material = {
        "source_sha256": sha256_file(pdf_path), "operation": "pdf-inspect", "parameters": params,
    }
    cache_key = hashlib.sha256(canonical_json(key_material).encode("utf-8")).hexdigest()
    cache_root = Path(args.cache_dir).expanduser().resolve()
    cache_entry = cache_root / cache_key
    output_dir = Path(args.out_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = cache_entry / "manifest.json"
    cache_hit = False
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache_hit = cache_manifest_matches(
                cache_entry,
                manifest,
                expected_key=cache_key,
                expected_key_material=key_material,
                required_artifacts=("pdf_evidence.json", "pdf_evidence.md"),
            )
        except (OSError, json.JSONDecodeError):
            cache_hit = False
    if not cache_hit and cache_entry.exists():
        shutil.rmtree(cache_entry)

    if not cache_hit:
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{cache_key[:12]}-", dir=cache_root))
        try:
            report = build_pdf_report(
                pdf_path, cache_key, args.render_pages or [], args.text_pages or [],
                args.render_dpi, args.max_text_chars, not args.no_line_number_check, temporary_dir,
            )
            if report["source"]["sha256"] != key_material["source_sha256"]:
                raise RuntimeError("The source PDF changed during inspection; no cache entry was written.")
            write_json(temporary_dir / "pdf_evidence.json", report)
            write_markdown(temporary_dir / "pdf_evidence.md", pdf_markdown(report))
            write_json(temporary_dir / "manifest.json", {
                "schema_version": CACHE_SCHEMA, "key": cache_key,
                "key_material": key_material, "artifacts_sha256": cache_artifact_hashes(temporary_dir), "created_at_utc": utc_now(),
            })
            if cache_entry.exists():
                shutil.rmtree(cache_entry)
            os.replace(temporary_dir, cache_entry)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    report = json.loads((cache_entry / "pdf_evidence.json").read_text(encoding="utf-8"))
    report["source"]["path"] = str(pdf_path)
    report["cache"]["hit"] = cache_hit
    report["cache"]["entry"] = str(cache_entry)
    expected_renders = set(report["rendered_pages"])
    for stale_image in output_dir.glob("page-*.png"):
        if stale_image.name not in expected_renders:
            stale_image.unlink()
    for source in cache_entry.iterdir():
        if source.is_file() and source.name != "manifest.json":
            shutil.copy2(source, output_dir / source.name)
    write_json(output_dir / "pdf_evidence.json", report)
    print(
        f"[{'CACHE HIT' if cache_hit else 'CACHE MISS'}] PDF evidence: "
        f"{report['summary']['page_count']} page(s), "
        f"{report['summary']['automatic_findings']} finding(s)."
    )
    print(f"[SUMMARY] {output_dir / 'pdf_evidence.md'}")
    print(f"[DETAIL] {output_dir / 'pdf_evidence.json'}")
    if report["rendered_pages"]:
        print(f"[RENDER] {len(report['rendered_pages'])} selected page image(s) in {output_dir}")
    return 0


def bounded_value(value: Any, depth: int = 0, max_depth: int = 4, max_items: int = 4, max_chars: int = 400) -> Any:
    if depth >= max_depth:
        return f"<{type(value).__name__}: depth limit>"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                result["…"] = f"{len(value) - max_items} more key(s)"
                break
            result[str(key)] = bounded_value(item, depth + 1, max_depth, max_items, max_chars)
        return result
    if isinstance(value, list):
        result = [bounded_value(item, depth + 1, max_depth, max_items, max_chars) for item in value[:max_items]]
        if len(value) > max_items:
            result.append(f"<{len(value) - max_items} additional item(s)>")
        return result
    if isinstance(value, str):
        return clean_text(value, max_chars)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return clean_text(value, max_chars)


def json_pointer(value: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return value
    current = value
    for raw in pointer.lstrip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError):
                raise KeyError(pointer) from None
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise KeyError(pointer)
    return current


def summarize_json(data: Any, selected_paths: list[str], sample_items: int, max_chars: int) -> dict[str, Any]:
    types: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    arrays: list[dict[str, Any]] = []
    max_depth = 0

    def walk(value: Any, path: str, depth: int) -> None:
        nonlocal max_depth
        max_depth = max(max_depth, depth)
        if isinstance(value, dict):
            types["object"] += 1
            for key, child in value.items():
                keys[str(key)] += 1
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                walk(child, f"{path}/{escaped}", depth + 1)
        elif isinstance(value, list):
            types["array"] += 1
            if len(arrays) < 40:
                arrays.append({
                    "path": path or "/", "length": len(value),
                    "sample_shape": sorted(value[0].keys())[:20] if value and isinstance(value[0], dict) else None,
                })
            for index, child in enumerate(value[:sample_items]):
                walk(child, f"{path}/{index}", depth + 1)
            if len(value) > sample_items:
                types["omitted_array_items"] += len(value) - sample_items
        elif value is None:
            types["null"] += 1
        elif isinstance(value, bool):
            types["boolean"] += 1
        elif isinstance(value, (int, float)):
            types["number"] += 1
        else:
            types["string"] += 1

    walk(data, "", 0)
    selected: dict[str, Any] = {}
    for pointer in selected_paths:
        try:
            selected[pointer] = bounded_value(
                json_pointer(data, pointer), max_depth=5, max_items=sample_items, max_chars=max_chars
            )
        except KeyError:
            selected[pointer] = {"error": "JSON pointer not found"}
    return {
        "format": "json",
        "structure": {
            "node_counts": dict(types), "maximum_depth_sampled": max_depth,
            "frequent_keys": [{"key": key, "occurrences_in_sampled_tree": count} for key, count in keys.most_common(40)],
            "arrays": arrays,
        },
        "selected_paths": selected,
        "sample_policy": {
            "array_items_traversed_per_branch": sample_items,
            "selected_value_max_string_characters": max_chars,
            "full_source_preserved": True,
        },
    }


def summarize_jsonl(path: Path, sample_items: int, max_chars: int) -> dict[str, Any]:
    count = 0
    types: Counter[str] = Counter()
    keys: Counter[str] = Counter()
    samples: list[Any] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
            count += 1
            types[type(value).__name__] += 1
            if isinstance(value, dict):
                keys.update(str(key) for key in value.keys())
            if len(samples) < sample_items:
                samples.append(bounded_value(value, max_depth=4, max_items=4, max_chars=max_chars))
    return {
        "format": "jsonl",
        "structure": {
            "record_count": count, "record_types": dict(types),
            "frequent_keys": [{"key": key, "occurrences": n} for key, n in keys.most_common(40)],
            "first_records_bounded": samples,
        },
        "sample_policy": {"full_source_preserved": True},
    }


def summarize_xml(path: Path, sample_items: int, max_chars: int) -> dict[str, Any]:
    tags: Counter[str] = Counter()
    attributes: Counter[str] = Counter()
    samples: list[dict[str, str]] = []
    root_tag = None
    element_count = 0
    stack: list[ET.Element] = []
    try:
        for event, element in ET.iterparse(path, events=("start", "end")):
            if event == "start":
                stack.append(element)
                element_count += 1
                tag = str(element.tag).split("}")[-1]
                tags[tag] += 1
                if root_tag is None:
                    root_tag = tag
                attributes.update(str(key).split("}")[-1] for key in element.attrib)
            else:
                text_value = clean_text(element.text, max_chars)
                if text_value and len(samples) < sample_items:
                    samples.append({"tag": str(element.tag).split("}")[-1], "text": text_value})
                element.clear()
                if len(stack) > 1:
                    stack[-2].remove(element)
                stack.pop()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc
    return {
        "format": "xml",
        "structure": {
            "root_tag": root_tag, "element_count": element_count,
            "frequent_tags": [{"tag": tag, "occurrences": n} for tag, n in tags.most_common(50)],
            "frequent_attributes": [{"attribute": key, "occurrences": n} for key, n in attributes.most_common(30)],
            "bounded_text_samples": samples,
        },
        "sample_policy": {"full_source_preserved": True},
    }


def crossref_items(data: Any) -> tuple[list[dict[str, Any]], str]:
    if isinstance(data, list):
        items, pointer = data, ""
    elif isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("items"), list):
            items, pointer = message["items"], "/message/items"
        elif isinstance(data.get("items"), list):
            items, pointer = data["items"], "/items"
        else:
            raise ValueError("CrossRef records not found at /message/items or /items.")
    else:
        raise ValueError("CrossRef JSON must be an object or an array of records.")
    invalid = [index for index, item in enumerate(items) if not isinstance(item, dict)]
    if invalid:
        raise ValueError(
            "CrossRef item(s) are not objects at index: " + ", ".join(map(str, invalid[:20]))
        )
    return items, pointer


def crossref_first(value: Any) -> str | None:
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = clean_text(value, 500)
    return text or None


def crossref_year(item: dict[str, Any]) -> int | None:
    for field in ("published-print", "published-online", "issued", "published", "created"):
        value = item.get(field)
        date_parts = value.get("date-parts") if isinstance(value, dict) else None
        if isinstance(date_parts, list) and date_parts and isinstance(date_parts[0], list) and date_parts[0]:
            try:
                year = int(date_parts[0][0])
                if 1000 <= year <= 3000:
                    return year
            except (TypeError, ValueError):
                continue
    return None


def crossref_author(item: dict[str, Any]) -> str | None:
    authors = item.get("author")
    if not isinstance(authors, list) or not authors or not isinstance(authors[0], dict):
        return None
    first = authors[0]
    given = clean_text(first.get("given", ""), 100)
    family = clean_text(first.get("family", ""), 120)
    name = " ".join(part for part in (given, family) if part).strip()
    return name or clean_text(first.get("name", ""), 160) or None


def normalize_title(value: str | None) -> str:
    # Preserve Unicode letters and digits in non-English reference titles.
    return re.sub(r"[^\w]+", " ", (value or "").casefold(), flags=re.UNICODE).replace("_", " ").strip()


def build_crossref_index(data: Any) -> dict[str, Any]:
    raw_items, base_pointer = crossref_items(data)
    records: list[dict[str, Any]] = []
    doi_positions: dict[str, list[int]] = defaultdict(list)
    title_positions: dict[str, list[int]] = defaultdict(list)
    missing_doi: list[int] = []
    missing_title: list[int] = []

    for index, item in enumerate(raw_items):
        pointer = f"{base_pointer}/{index}" if base_pointer else f"/{index}"
        doi = clean_text(item.get("DOI", ""), 240).lower() or None
        if doi:
            doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
        title = crossref_first(item.get("title"))
        journal = crossref_first(item.get("container-title"))
        record = {
            "index": index,
            "evidence_pointer": pointer,
            "doi": doi,
            "title": title,
            "first_author": crossref_author(item),
            "year": crossref_year(item),
            "journal": journal,
            "volume": clean_text(item.get("volume", ""), 80) or None,
            "issue": clean_text(item.get("issue", ""), 80) or None,
            "page": clean_text(item.get("page", ""), 80) or None,
            "type": clean_text(item.get("type", ""), 80) or None,
        }
        records.append(record)
        if doi:
            doi_positions[doi].append(index)
        else:
            missing_doi.append(index)
        normalized = normalize_title(title)
        if normalized:
            title_positions[normalized].append(index)
        else:
            missing_title.append(index)

    duplicate_dois = [
        {"doi": doi, "indices": indices}
        for doi, indices in sorted(doi_positions.items()) if len(indices) > 1
    ]
    duplicate_titles = [
        {"normalized_title": title, "indices": indices}
        for title, indices in sorted(title_positions.items()) if len(indices) > 1
    ]
    anomalies = []
    for index in missing_doi:
        anomalies.append({"code": "missing_doi", "index": index, "message": "CrossRef item has no DOI."})
    for index in missing_title:
        anomalies.append({"code": "missing_title", "index": index, "message": "CrossRef item has no title."})
    for group in duplicate_dois:
        anomalies.append({
            "code": "duplicate_doi", "indices": group["indices"],
            "message": f"DOI occurs {len(group['indices'])} times: {group['doi']}",
        })
    for group in duplicate_titles:
        anomalies.append({
            "code": "duplicate_title", "indices": group["indices"],
            "message": f"Normalized title occurs {len(group['indices'])} times.",
        })
    return {
        "format": "crossref-reference-index",
        "summary": {
            "records": len(records),
            "with_doi": len(records) - len(missing_doi),
            "without_doi": len(missing_doi),
            "with_title": len(records) - len(missing_title),
            "without_title": len(missing_title),
            "duplicate_doi_groups": len(duplicate_dois),
            "duplicate_title_groups": len(duplicate_titles),
            "anomalies": len(anomalies),
        },
        "records": records,
        "anomalies": anomalies,
        "duplicate_dois": duplicate_dois,
        "duplicate_titles": duplicate_titles,
        "limits": [
            "This index extracts bibliographic fields; it does not prove that a DOI identifies the cited work.",
            "Duplicate title/DOI flags require human review and do not automatically mean a reference is invalid.",
            "The original CrossRef response remains the source of truth for exact metadata.",
        ],
    }


def crossref_markdown(report: dict[str, Any], digest: str) -> list[str]:
    summary = report["summary"]
    lines = [
        "# CrossRef compact reference index", "",
        f"- Source SHA-256: {digest}",
        f"- Records: {summary['records']}",
        f"- With DOI: {summary['with_doi']}; without DOI: {summary['without_doi']}",
        f"- With title: {summary['with_title']}; without title: {summary['without_title']}",
        f"- Duplicate DOI groups: {summary['duplicate_doi_groups']}",
        f"- Duplicate title groups: {summary['duplicate_title_groups']}",
        "", "## All records", "",
        "| # | Year | First author | Title | Journal | DOI | Source pointer |",
        "|---:|---:|---|---|---|---|---|",
    ]
    for record in report["records"]:
        def cell(value: Any) -> str:
            return clean_text(value, 180).replace("|", "\\|") if value else "—"
        lines.append(
            f"| {record['index'] + 1} | {cell(record['year'])} | {cell(record['first_author'])} | "
            f"{cell(record['title'])} | {cell(record['journal'])} | {cell(record['doi'])} | "
            f"{record['evidence_pointer']} |"
        )
    lines.extend(["", "## Anomalies", ""])
    if report["anomalies"]:
        for item in report["anomalies"]:
            indices = item.get("indices", [item.get("index")])
            lines.append(
                f"- **{item['code']}** — {item['message']} (indices: {', '.join(map(str, indices))})"
            )
    else:
        lines.append("- No missing-title/DOI or duplicate-title/DOI anomalies detected.")
    lines.extend([
        "", "> This is a compact field index, not DOI authenticity verification. "
        "Open the original CrossRef item at its JSON Pointer when checking a specific reference.",
    ])
    return lines


def cmd_crossref_index(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Input file not found: {source}")
    digest = sha256_file(source)
    parameters = {"tool_version": TOOL_VERSION, "cache_schema": CACHE_SCHEMA}
    key_material = {"source_sha256": digest, "operation": "crossref-index", "parameters": parameters}
    cache_key = hashlib.sha256(canonical_json(key_material).encode("utf-8")).hexdigest()
    cache_root = Path(args.cache_dir).expanduser().resolve()
    cache_entry = cache_root / "crossref" / cache_key
    manifest_path = cache_entry / "manifest.json"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_hit = False

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache_hit = cache_manifest_matches(
                cache_entry,
                manifest,
                expected_key=cache_key,
                expected_key_material=key_material,
                required_artifacts=("crossref_evidence.json", "crossref_evidence.md"),
            )
        except (OSError, json.JSONDecodeError):
            cache_hit = False
    if not cache_hit and cache_entry.exists():
        shutil.rmtree(cache_entry)

    if not cache_hit:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
        report = build_crossref_index(data)
        if sha256_file(source) != digest:
            raise RuntimeError("The CrossRef source changed during indexing; no cache entry was written.")
        report.update({
            "schema_version": CACHE_SCHEMA,
            "generated_at_utc": utc_now(),
            "source": {"path": str(source), "sha256": digest, "size_bytes": source.stat().st_size},
            "tool": {"name": "Co_Reviewer evidence tools", "version": TOOL_VERSION},
            "cache": {"key": cache_key, "hit": False},
        })
        crossref_cache_root = cache_root / "crossref"
        crossref_cache_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{cache_key[:12]}-", dir=crossref_cache_root))
        try:
            write_json(temporary_dir / "crossref_evidence.json", report)
            write_markdown(temporary_dir / "crossref_evidence.md", crossref_markdown(report, digest))
            write_json(temporary_dir / "manifest.json", {
                "schema_version": CACHE_SCHEMA, "key": cache_key,
                "key_material": key_material, "artifacts_sha256": cache_artifact_hashes(temporary_dir), "created_at_utc": utc_now(),
            })
            if cache_entry.exists():
                shutil.rmtree(cache_entry)
            os.replace(temporary_dir, cache_entry)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    report = json.loads((cache_entry / "crossref_evidence.json").read_text(encoding="utf-8"))
    report["source"]["path"] = str(source)
    report["cache"] = {"key": cache_key, "hit": cache_hit, "entry": str(cache_entry)}
    output_dir = Path(args.out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "crossref_evidence.json"
    markdown_path = output_dir / "crossref_evidence.md"
    write_json(json_path, report)
    write_markdown(markdown_path, crossref_markdown(report, digest))
    print(
        f"[{'CACHE HIT' if cache_hit else 'CACHE MISS'}] CrossRef index: "
        f"{report['summary']['records']} record(s), {report['summary']['anomalies']} anomaly item(s)."
    )
    print(f"[SUMMARY] {markdown_path}")
    print(f"[DETAIL] {json_path}")
    return 0


def structured_markdown(source: Path, digest: str, report: dict[str, Any]) -> list[str]:
    structure = report["structure"]
    lines = [
        "# Structured evidence summary", "",
        f"- Source: {source.name}", f"- Format: {report['format']}",
        f"- SHA-256: {digest}", "- Original source: preserved unchanged", "",
    ]
    if report["format"] == "json":
        lines.extend(["## Structure", ""])
        lines.extend(f"- {key}: {value}" for key, value in structure.get("node_counts", {}).items())
        lines.extend(["", "## Frequent keys", ""])
        lines.extend(
            f"- {item['key']}: {item['occurrences_in_sampled_tree']}"
            for item in structure.get("frequent_keys", [])[:20]
        )
        lines.extend(["", "## Arrays", ""])
        lines.extend(
            f"- {item['path']}: {item['length']} item(s)"
            + (f"; sample keys: {', '.join(item['sample_shape'])}" if item.get("sample_shape") else "")
            for item in structure.get("arrays", [])[:20]
        )
    elif report["format"] == "jsonl":
        lines.extend(["## Records", "", f"- Record count: {structure['record_count']}"])
        lines.extend(f"- {key}: {value}" for key, value in structure.get("record_types", {}).items())
        lines.extend(["", "## Frequent keys", ""])
        lines.extend(f"- {item['key']}: {item['occurrences']}" for item in structure.get("frequent_keys", [])[:20])
        lines.extend(["", "## Bounded record samples", "", json.dumps(structure.get("first_records_bounded", []), ensure_ascii=False, indent=2)])
    else:
        lines.extend(["## XML structure", "", f"- Root: {structure.get('root_tag')}", f"- Elements: {structure.get('element_count')}"])
        lines.extend(["", "## Frequent tags", ""])
        lines.extend(f"- {item['tag']}: {item['occurrences']}" for item in structure.get("frequent_tags", [])[:25])
        lines.extend(["", "## Bounded text samples", "", json.dumps(structure.get("bounded_text_samples", []), ensure_ascii=False, indent=2)])
    if report.get("selected_paths"):
        lines.extend(["", "## Requested JSON-pointer excerpts", "", json.dumps(report["selected_paths"], ensure_ascii=False, indent=2)])
    lines.extend([
        "", "> Structural index only, not semantic validation. Consult the original source at exact "
        "evidence pointers when a value or claim must be checked.",
    ])
    return lines


def cmd_structured_summary(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Input file not found: {source}")
    if not 1 <= args.sample_items <= 20:
        raise ValueError("--sample-items must be between 1 and 20.")
    if args.max_chars < 100:
        raise ValueError("--max-chars must be at least 100.")
    extension = source.suffix.lower()
    kind = args.format
    if kind == "auto":
        kind = "jsonl" if extension in {".jsonl", ".ndjson"} else "xml" if extension == ".xml" else "json"

    digest = sha256_file(source)
    parameters = {
        "tool_version": TOOL_VERSION,
        "cache_schema": CACHE_SCHEMA,
        "format": kind,
        "paths": args.path or [],
        "sample_items": args.sample_items,
        "max_chars": args.max_chars,
    }
    key_material = {"source_sha256": digest, "operation": "structured-summary", "parameters": parameters}
    cache_key = hashlib.sha256(canonical_json(key_material).encode("utf-8")).hexdigest()
    cache_root = Path(args.cache_dir).expanduser().resolve()
    cache_entry = cache_root / "structured" / cache_key
    manifest_path = cache_entry / "manifest.json"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_hit = False

    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            cache_hit = cache_manifest_matches(
                cache_entry,
                manifest,
                expected_key=cache_key,
                expected_key_material=key_material,
                required_artifacts=("structured_evidence.json", "structured_evidence.md"),
            )
        except (OSError, json.JSONDecodeError):
            cache_hit = False
    if not cache_hit and cache_entry.exists():
        shutil.rmtree(cache_entry)

    if not cache_hit:
        if kind == "json":
            data = json.loads(source.read_text(encoding="utf-8-sig"))
            report = summarize_json(data, args.path or [], args.sample_items, args.max_chars)
        elif kind == "jsonl":
            if args.path:
                raise ValueError("--path JSON pointers are supported only for JSON files.")
            report = summarize_jsonl(source, args.sample_items, args.max_chars)
        elif kind == "xml":
            if args.path:
                raise ValueError("--path JSON pointers are supported only for JSON files.")
            report = summarize_xml(source, args.sample_items, args.max_chars)
        else:
            raise ValueError(f"Unsupported format: {kind}")

        report.update({
            "schema_version": CACHE_SCHEMA, "generated_at_utc": utc_now(),
            "source": {"path": str(source), "sha256": digest, "size_bytes": source.stat().st_size},
            "tool": {"name": "Co_Reviewer evidence tools", "version": TOOL_VERSION},
            "cache": {"key": cache_key, "hit": False},
        })
        structured_cache_root = cache_root / "structured"
        structured_cache_root.mkdir(parents=True, exist_ok=True)
        temporary_dir = Path(tempfile.mkdtemp(prefix=f".{cache_key[:12]}-", dir=structured_cache_root))
        try:
            if sha256_file(source) != digest:
                raise RuntimeError("The source file changed during summarization; no cache entry was written.")
            write_json(temporary_dir / "structured_evidence.json", report)
            write_markdown(temporary_dir / "structured_evidence.md", structured_markdown(source, digest, report))
            write_json(temporary_dir / "manifest.json", {
                "schema_version": CACHE_SCHEMA, "key": cache_key,
                "key_material": key_material, "artifacts_sha256": cache_artifact_hashes(temporary_dir), "created_at_utc": utc_now(),
            })
            cache_entry.parent.mkdir(parents=True, exist_ok=True)
            if cache_entry.exists():
                shutil.rmtree(cache_entry)
            os.replace(temporary_dir, cache_entry)
        except Exception:
            shutil.rmtree(temporary_dir, ignore_errors=True)
            raise

    report = json.loads((cache_entry / "structured_evidence.json").read_text(encoding="utf-8"))
    report["source"]["path"] = str(source)
    report["cache"] = {"key": cache_key, "hit": cache_hit, "entry": str(cache_entry)}
    output_dir = Path(args.out_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "structured_evidence.json"
    markdown_path = output_dir / "structured_evidence.md"
    write_json(json_path, report)
    write_markdown(markdown_path, structured_markdown(source, digest, report))
    print(
        f"[{'CACHE HIT' if cache_hit else 'CACHE MISS'}] "
        f"{report['format']} structure summarized; SHA-256 {digest[:16]}…"
    )
    print(f"[DETAIL] {json_path}")
    print(f"[DIGEST] {markdown_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Model-agnostic local PDF and structured-evidence tools for Co_Reviewer."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    pdf = subparsers.add_parser("pdf-inspect", help="Inspect PDF text geometry and cache reusable evidence.")
    pdf.add_argument("--pdf", required=True)
    pdf.add_argument("--out-dir", required=True)
    pdf.add_argument("--cache-dir", default=".co-reviewer-cache")
    pdf.add_argument("--render-pages", type=int, nargs="*", default=[])
    pdf.add_argument("--text-pages", type=int, nargs="*", default=[])
    pdf.add_argument("--render-dpi", type=int, default=96)
    pdf.add_argument("--max-text-chars", type=int, default=2500)
    pdf.add_argument("--no-line-number-check", action="store_true")
    pdf.set_defaults(func=cmd_pdf_inspect)

    summary = subparsers.add_parser(
        "structured-summary", help="Summarize JSON, JSONL or XML locally without dumping the full source."
    )
    summary.add_argument("--input", required=True)
    summary.add_argument("--format", choices=("auto", "json", "jsonl", "xml"), default="auto")
    summary.add_argument("--out-dir", required=True)
    summary.add_argument("--cache-dir", default=".co-reviewer-cache")
    summary.add_argument("--path", action="append", default=[], help="JSON Pointer to include; repeat as needed.")
    summary.add_argument("--sample-items", type=int, default=3)
    summary.add_argument("--max-chars", type=int, default=400)
    summary.set_defaults(func=cmd_structured_summary)

    crossref = subparsers.add_parser(
        "crossref-index",
        help="Create a compact index of every CrossRef result and flag missing/duplicate identifiers.",
    )
    crossref.add_argument("--input", required=True)
    crossref.add_argument("--out-dir", required=True)
    crossref.add_argument("--cache-dir", default=".co-reviewer-cache")
    crossref.set_defaults(func=cmd_crossref_index)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

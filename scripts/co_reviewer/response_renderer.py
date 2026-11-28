# -*- coding: utf-8 -*-
"""Deterministic Markdown Response Letter renderer."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

def render_response_letter(plan: dict[str, Any]) -> str:
    """Render a Response Letter draft from response text already supplied in the plan."""
    items = plan.get("revision_items", [])
    counts = defaultdict(int)
    for item in items:
        counts[item.get("status", "planned")] += 1
    lines = [
        "# Response to Reviewers", "",
        "> Working draft generated from the current revision plan. No scientific claims or response text are invented.",
        f"> Revision items: {len(items)} | Verified: {counts['verified']} | Applied but not verified: {counts['applied']} | Other/open: {len(items) - counts['verified'] - counts['applied']}",
        ""
    ]
    for item in items:
        item_id = item.get("id", "UNASSIGNED")
        reviewer = item.get("reviewer", "Reviewer")
        status = item.get("status", "planned")
        lines.extend([f"## {reviewer} — {item_id}", "", "**Comment**", "", str(item.get("comment", "")).strip(), ""])
        response = str(item.get("proposed_response", "")).strip()
        if status == "verified":
            lines.extend(["**Response**", "", response or "[Response text required; not generated automatically.]", ""])
        elif status == "location_pending":
            lines.extend([
                "**Proposed response — SEMANTIC REVIEWED; FINAL PDF PAGE/LINE MAPPING PENDING**", "",
                response or "[Response text required; Co_Reviewer will not invent a reply.]",
                "",
                "> Semantic review is recorded. Do not submit until final PDF page and line locations are mapped.",
                ""
            ])
        else:
            lines.extend([
                f"**Proposed response — DRAFT ({status}; not yet verified)**", "",
                response or "[Response text required; Co_Reviewer will not invent a reply.]",
                "",
                "> Do not use completion claims such as “we have revised” until the corresponding document changes have been verified.",
                ""
            ])
        quoted_changes = item.get("quoted_changes", [])
        if quoted_changes:
            lines.extend([
                "**Manuscript excerpts from exact PatchOperations**",
                "",
                "> Excerpts below are copied directly from the verified PatchOperation operands; no model-generated replacement text is used.",
                "",
            ])
            for change in quoted_changes:
                action = change.get("action", "revised_text")
                if action == "existing_text":
                    label = "Existing text — no-change evidence"
                elif action == "removed_text":
                    label = "Removed manuscript text"
                else:
                    label = "Revised manuscript text"
                if change.get("verified") is not True:
                    label = "DRAFT — " + label
                location = change.get("location") or "final PDF location pending"
                document = change.get("document") or "Document"
                operation_id = change.get("operation_id")
                suffix = f" (PatchOperation {operation_id})" if operation_id else ""
                lines.append(f"**{label} — {document}, {location}{suffix}**")
                quote_text = str(change.get("quote_text") or "").strip()
                if quote_text:
                    lines.extend(
                        ["> " + part for part in quote_text.splitlines()]
                    )
                else:
                    lines.append("> [No replacement text was supplied for this operation.]")
                lines.append("")
        lines.extend(["**Linked document changes**", ""])
        for target in item.get("targets", []):
            location = target.get("location") or "location not specified"
            output_name = Path(target.get("output_file", "")).name or "output file not specified"
            lines.append(f"- **{target.get('document', 'Document')}** — {location} (output: {output_name})")
        lines.extend(["", "---", ""])
    return "\n".join(lines).rstrip() + "\n"

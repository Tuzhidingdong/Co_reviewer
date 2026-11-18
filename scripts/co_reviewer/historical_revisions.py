"""Target-scoped, hash-bound historical Word revision resolution.

Only well-formed native text wrappers and standard property snapshots are
resolved. Unsupported comment, table-topology, numbering and structural cases
fail closed. The source DOCX is never modified.
"""
from __future__ import annotations

import copy
import io
import json
import zipfile
from collections.abc import Mapping, Sequence
from typing import Any

from lxml import etree as ET

from .v232_contract import (
    ContractError, R, W, _sha, _xml, qn,
    targeted_revision_gate,
)


def _ref(item: Mapping[str, Any]) -> str:
    if item.get("structure_id"):
        return str(item["structure_id"])
    part, path = str(item.get("part") or ""), str(item.get("element_path") or "")
    if part and path:
        return part + "::" + path
    return str(item.get("id") or "")


def _unwrap(node: ET._Element, *, restore_deleted_text: bool = False) -> None:
    parent = node.getparent()
    if parent is None:
        raise ContractError("Historical revision wrapper has no parent")
    index = parent.index(node)
    children = list(node)
    if restore_deleted_text:
        for child in node.iter():
            if child.tag == qn("delText"):
                child.tag = qn("t")
            elif child.tag == qn("delInstrText"):
                child.tag = qn("instrText")
    for offset, child in enumerate(children):
        node.remove(child)
        parent.insert(index + offset, child)
    tail = node.tail
    parent.remove(node)
    if tail:
        if index > 0:
            previous = parent[index - 1]
            previous.tail = (previous.tail or "") + tail
        elif len(parent):
            parent[0].tail = tail + (parent[0].tail or "")


def _restore_property_snapshot(change: ET._Element) -> None:
    local = ET.QName(change).localname
    expected = local[:-6] if local.endswith("Change") else ""
    parent = change.getparent()
    if parent is None or not expected or ET.QName(parent).localname != expected:
        raise ContractError("UNSUPPORTED_FORMAT_REVISION_PARENT:" + local)
    snapshots = [child for child in change if child.tag == qn(expected)]
    if len(snapshots) != 1:
        raise ContractError("INVALID_FORMAT_REVISION_SNAPSHOT:" + local)
    snapshot = snapshots[0]
    parent.attrib.clear()
    parent.attrib.update(snapshot.attrib)
    for child in list(parent):
        parent.remove(child)
    for child in list(snapshot):
        parent.append(copy.deepcopy(child))


def resolve_historical_revisions(
    source_bytes: bytes,
    inventory: Mapping[str, Any],
    targets: Sequence[Mapping[str, Any]],
    *,
    policy: str,
    selectively_resolved_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Resolve target-overlapping historical revisions into a new DOCX byte stream."""
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise ContractError("Historical revision source must be non-empty DOCX bytes")
    source_sha = _sha(source_bytes)
    if inventory.get("source_sha256") != source_sha:
        raise ContractError("HISTORICAL_REVISION_BASELINE_STALE: source SHA-256 differs from inventory")
    if inventory.get("package_status") != "READY":
        raise ContractError("HISTORICAL_REVISION_BASELINE_NOT_READY")
    if not isinstance(targets, Sequence) or isinstance(targets, (str, bytes)):
        raise ContractError("Revision targets must be an array")
    if policy not in {"ACCEPT_EXISTING", "REJECT_EXISTING", "SELECTIVE_ACCEPT_CONFLICTS"}:
        raise ContractError("Unknown historical revision resolution policy")
    selected = [str(value) for value in selectively_resolved_ids]
    if len(selected) != len(set(selected)):
        raise ContractError("Duplicate historical revision selection")

    gate = targeted_revision_gate(
        inventory, targets, resolution_policy=policy,
        selectively_resolved_ids=selected,
    )
    overlaps = gate["overlapping_revisions"]
    if not overlaps:
        return {
            "status": "NO_OP", "source_sha256": source_sha,
            "output_sha256": source_sha, "policy": policy,
            "resolved": [], "preserved_unrelated": [], "docx_bytes": source_bytes,
            "gate": gate,
        }

    overlap_refs = {_ref(item) for item in overlaps}
    if policy == "SELECTIVE_ACCEPT_CONFLICTS":
        selected_refs = set(selected)
        unknown = selected_refs - overlap_refs
        if unknown:
            raise ContractError("SELECTED_REVISION_NOT_IN_TARGET_OVERLAP:" + ",".join(sorted(unknown)))
        pending = overlap_refs - selected_refs
        if pending:
            raise ContractError("UNRESOLVED_TARGET_OVERLAPPING_REVISIONS:" + ",".join(sorted(pending)))
        action = "ACCEPT_EXISTING"
    else:
        selected_refs = overlap_refs
        action = policy

    supported_text = {"ins", "del", "moveFrom", "moveTo"}
    supported_format = {
        "rPrChange", "pPrChange", "tblPrChange", "tblGridChange",
        "trPrChange", "tcPrChange", "sectPrChange",
    }
    unsupported = []
    for item in overlaps:
        if _ref(item) not in selected_refs:
            continue
        kind, local = str(item.get("kind") or ""), str(item.get("element") or "")
        if kind == "text_revision_wrapper" and local in supported_text:
            continue
        if kind == "format_revision" and local in supported_format:
            continue
        unsupported.append({
            "ref": _ref(item), "kind": kind, "element": local,
            "part": item.get("part"), "element_path": item.get("element_path"),
        })
    if unsupported:
        raise ContractError(
            "HISTORICAL_REVISION_REQUIRES_UNSUPPORTED_ADAPTER:"
            + json.dumps(unsupported, ensure_ascii=False, sort_keys=True)
        )

    with zipfile.ZipFile(io.BytesIO(source_bytes), "r") as source_zip:
        names = source_zip.namelist()
        if len(names) != len(set(names)):
            raise ContractError("DOCX contains duplicate ZIP member names")
        selected_items = [item for item in overlaps if _ref(item) in selected_refs]
        selected_by_part: dict[str, list[Mapping[str, Any]]] = {}
        for item in selected_items:
            part = str(item.get("part") or "")
            if not part.startswith("word/") or not part.endswith(".xml") or part not in names:
                raise ContractError("Historical revision points to missing/unsupported Word part: " + part)
            selected_by_part.setdefault(part, []).append(item)

        modified_parts: dict[str, bytes] = {}
        resolved: list[dict[str, Any]] = []
        preserved: list[dict[str, Any]] = []

        for part, part_selected in selected_by_part.items():
            root = _xml(source_zip.read(part))
            all_part_items = [x for x in inventory.get("revisions", []) if str(x.get("part")) == part]
            node_index: dict[str, tuple[Mapping[str, Any], ET._Element]] = {}
            for item in all_part_items:
                path = str(item.get("element_path") or "")
                if not path:
                    continue
                try:
                    matches = root.xpath(path, namespaces={"w": W, "r": R})
                except ET.XPathError as exc:
                    raise ContractError("INVALID_HISTORICAL_REVISION_PATH:" + path) from exc
                if len(matches) != 1:
                    raise ContractError("HISTORICAL_REVISION_PATH_NOT_UNIQUE:" + path)
                node_index[_ref(item)] = (item, matches[0])

            # Preserve a byte-semantic snapshot of every unrelated historical
            # structure, except the opposite half of a selected move pair.
            selected_nodes = {id(node) for ref, (_, node) in node_index.items() if ref in selected_refs}
            move_ids = {
                str(node.get(qn("id"))) for ref, (item, node) in node_index.items()
                if ref in selected_refs and ET.QName(node).localname in {"moveFrom", "moveTo"}
            }
            paired_moves: dict[str, tuple[ET._Element, ET._Element]] = {}
            for move_id in move_ids:
                if not move_id or move_id == "None":
                    raise ContractError("MOVE_REVISION_ID_MISSING")
                from_nodes = [n for n in root.iter(qn("moveFrom")) if n.get(qn("id")) == move_id]
                to_nodes = [n for n in root.iter(qn("moveTo")) if n.get(qn("id")) == move_id]
                if len(from_nodes) != 1 or len(to_nodes) != 1:
                    raise ContractError("MOVE_REVISION_PAIR_NOT_UNIQUE:" + move_id)
                paired_moves[move_id] = (from_nodes[0], to_nodes[0])

            paired_ids = set(move_ids)
            unrelated_snapshots = []
            for ref, (item, node) in node_index.items():
                if ref in selected_refs:
                    continue
                local = str(item.get("element") or "")
                detail_id = str((item.get("details") or {}).get("revision_id") or "")
                if local in {"moveFrom", "moveTo"} and detail_id in paired_ids:
                    continue
                unrelated_snapshots.append((item, node, ET.tostring(node, method="c14n")))

            operations: list[tuple[ET._Element, Mapping[str, Any], bool]] = []
            for item in part_selected:
                ref = _ref(item)
                if ref not in node_index:
                    raise ContractError("SELECTED_HISTORICAL_REVISION_NOT_FOUND:" + ref)
                indexed_item, node = node_index[ref]
                detail_id = (indexed_item.get("details") or {}).get("revision_id")
                observed_id = node.get(qn("id"))
                if detail_id is not None and str(observed_id) != str(detail_id):
                    raise ContractError("HISTORICAL_REVISION_ID_MISMATCH:" + ref)
                operations.append((node, item, False))
            for move_id, pair in paired_moves.items():
                for node in pair:
                    if id(node) not in selected_nodes:
                        operations.append((node, {
                            "structure_id": "paired-move:" + move_id,
                            "kind": "text_revision_wrapper",
                            "element": ET.QName(node).localname,
                            "part": part,
                            "details": {"revision_id": move_id},
                        }, True))

            seen_nodes: set[int] = set()
            operations.sort(key=lambda x: len(list(x[0].iterancestors())), reverse=True)
            for node, item, paired in operations:
                if id(node) in seen_nodes:
                    continue
                seen_nodes.add(id(node))
                if node.getparent() is None:
                    raise ContractError("HISTORICAL_REVISION_NODE_DETACHED")
                local = ET.QName(node).localname
                if local == "ins":
                    if action == "ACCEPT_EXISTING":
                        _unwrap(node)
                    else:
                        node.getparent().remove(node)
                elif local in {"del", "moveFrom"}:
                    if action == "ACCEPT_EXISTING":
                        node.getparent().remove(node)
                    else:
                        _unwrap(node, restore_deleted_text=True)
                elif local == "moveTo":
                    if action == "ACCEPT_EXISTING":
                        _unwrap(node)
                    else:
                        node.getparent().remove(node)
                elif local in supported_format:
                    if action == "REJECT_EXISTING":
                        _restore_property_snapshot(node)
                    else:
                        node.getparent().remove(node)
                else:
                    raise ContractError("UNSUPPORTED_HISTORICAL_REVISION:" + local)
                resolved.append({
                    "ref": _ref(item), "structure_id": item.get("structure_id"),
                    "kind": item.get("kind"), "element": local, "part": part,
                    "action": action, "paired_move": paired,
                })

            for item, node, before in unrelated_snapshots:
                if node.getparent() is None or ET.tostring(node, method="c14n") != before:
                    raise ContractError("UNRELATED_HISTORICAL_REVISION_CHANGED:" + _ref(item))
                preserved.append({"ref": _ref(item), "part": part, "element": item.get("element")})

            modified_parts[part] = ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)

        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target_zip:
            for info in source_zip.infolist():
                data = modified_parts.get(info.filename)
                if data is None:
                    data = source_zip.read(info.filename)
                target_zip.writestr(info, data)
        result_bytes = output.getvalue()

    with zipfile.ZipFile(io.BytesIO(result_bytes), "r") as check_zip:
        bad = check_zip.testzip()
        if bad:
            raise ContractError("Resolved DOCX failed ZIP CRC verification: " + bad)
        for part in modified_parts:
            _xml(check_zip.read(part))
    return {
        "status": "RESOLVED", "source_sha256": source_sha,
        "output_sha256": _sha(result_bytes), "policy": action,
        "resolved": resolved, "preserved_unrelated": preserved,
        "gate": gate, "docx_bytes": result_bytes,
    }

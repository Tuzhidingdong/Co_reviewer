# -*- coding: utf-8 -*-
"""Read-only OPC/WordprocessingML structure inventory and fail-closed edit gate.

The classifier never changes a package. It inventories parts, relationships and
high-risk structures, and exposes a paragraph-scoped guard for conservative
text editing. Locator paths are scoped to the exact source package hash.
"""
from __future__ import annotations

import hashlib
import io
import json
import posixpath
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from lxml import etree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"

SUPPORTED = "SUPPORTED"
GUARDED = "GUARDED"
DEFERRED = "DEFERRED_TO_P1_UNSUPPORTED"
BLOCKED = "BLOCKED"
_BLOCKING_STATUSES = {GUARDED, DEFERRED, BLOCKED}


class StructureClassificationError(ValueError):
    """Raised when a DOCX package or a proposed edit scope cannot be classified safely."""


_PREFIXES = {
    W: "w", M: "m", R: "r", PKG_REL: "pr", CT: "ct", MC: "mc",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing": "wp",
    "http://schemas.openxmlformats.org/drawingml/2006/picture": "pic",
    "http://schemas.microsoft.com/office/word/2010/wordml": "w14",
    "http://schemas.microsoft.com/office/word/2012/wordml": "w15",
    "http://schemas.microsoft.com/office/word/2018/wordml": "w16",
    "http://schemas.microsoft.com/office/word/2018/wordml/cex": "w16cex",
    "http://schemas.microsoft.com/office/word/2016/wordml/cid": "w16cid",
    "http://schemas.microsoft.com/office/word/2016/wordml/symex": "w16se",
    "http://schemas.microsoft.com/office/word/2016/wordml/cs": "w16cs",
    "http://schemas.microsoft.com/office/word/2016/wordml/du": "w16du",
    "http://schemas.openxmlformats.org/officeDocument/2006/math": "m",
    "http://schemas.microsoft.com/office/word/2010/wordml": "w14",
    "http://www.w3.org/XML/1998/namespace": "xml",
    "urn:schemas-microsoft-com:office:office": "o",
    "urn:schemas-microsoft-com:vml": "v",
}
_KNOWN_NAMESPACES = frozenset(_PREFIXES)

# Common WordprocessingML vocabulary. Unrecognized WML elements are surfaced
# as BLOCKED rather than being silently treated as ordinary text.
_KNOWN_WML_LOCALS = frozenset("""
document body background wrapper subDoc altChunk customXml customXmlPr
customXmlInsRangeStart customXmlInsRangeEnd customXmlDelRangeStart customXmlDelRangeEnd
customXmlMoveFromRangeStart customXmlMoveFromRangeEnd customXmlMoveToRangeStart customXmlMoveToRangeEnd
sdt sdtPr sdtEndPr sdtContent sdtContentDocPart sdtContentDocPartObj
sdtContentDocPartList sdtContentDropDownList sdtContentComboBox
sdtContentDate sdtContentPicture sdtContentRichText sdtContentText
p pPr r rPr t delText instrText delInstrText tab br cr
noBreakHyphen softHyphen lastRenderedPageBreak sym
pStyle rStyle sectPr sectPrChange
rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint
noProof snapToGrid vanish webHidden color spacing w kern position sz szCs
highlight u effect bdr shd fitText vertAlign rtl cs em lang eastAsianLayout
specVanish oMath
keepNext keepLines pageBreakBefore widowControl numPr tabs spacing ind jc
textDirection textAlignment outlineLvl divId cnfStyle
tbl tblPr tblPrEx tblStyle tblW jc tblInd tblBorders shd tblLayout
tblCellMar tblLook tblCaption tblDescription tblOverlap tblpPr tblGrid gridCol
pBdr pBdrBetween pBdrBar pBdrTop pBdrBottom pBdrLeft pBdrRight
tr trPr trHeight cantSplit tblHeader tblCellSpacing tblWAfter gridBefore
gridAfter wAfter wBefore tc tcPr tcW gridSpan hMerge vMerge tcBorders
noWrap tcMar textDirection fitText headers cellIns cellDel cellMerge
tblPrChange tblGridChange trPrChange tcPrChange
bookmarkStart bookmarkEnd commentRangeStart commentRangeEnd commentReference
comment comments
ins del moveFrom moveTo moveFromRangeStart moveFromRangeEnd moveToRangeStart moveToRangeEnd
pPrChange rPrChange numberingChange
customXmlInsRangeStart customXmlInsRangeEnd customXmlDelRangeStart customXmlDelRangeEnd
customXmlMoveFromRangeStart customXmlMoveFromRangeEnd customXmlMoveToRangeStart customXmlMoveToRangeEnd
conflictIns conflictDel
hyperlink smartTag proofErr permStart permEnd
drawing pict object
footnoteReference endnoteReference separator continuationSeparator
footnote endnote
fldChar fldSimple
trackRevisions revisionView
language themeFontLang docGrid
numbering abstractNum num numPicBullet numPr ilvl numId lvl lvlOverride startOverride
abstractNumId lvlText lvlJc numFmt lvlRestart suff isLgl
nsid multiLevelType tmpl start
lvlPicBulletId legacy indent left right hanging firstLine
style styles basedOn next link qFormat uiPriority semiHidden unhideWhenUsed
latentStyles lsdException tblStylePr tblStyleRowBandSize tblStyleColBandSize
name basedOn next link pPr rPr tblPr trPr tcPr
default docDefaults rPrDefault pPrDefault
settings compat documentProtection writeProtection autoFormatOverride doNotTrackMoves doNotTrackFormatting
zoom proofState defaultTabStop characterSpacingControl savePreviewPicture
clrSchemeMapping doNotAutoCompressPictures shapeDefaults decimalSymbol listSeparator
useFELayout compatSetting rsids rsid rsidRoot rsidR rsidRPr rsidP rsidSect
doNotUseHTMLParagraphAutoSpacing doNotUseIndentAsNumberingTabStop
mirrorIndents contextualSpacing autoSpaceDE autoSpaceDN adjustRightInd
suppressAutoHyphens kinsoku wordWrap overflowPunct topLinePunct leftLinePunct
rightLinePunct noCompress doNotCompress doNotUseEastAsianBreakRules
header footer hdr ftr footnotes endnotes
fontTable fonts font altName panose family pitch sig charset panose1
embedRegular embedBold embedItalic embedBoldItalic embed embedDataId embeddedFont
subsetted notTrueType
shapedefaults shapelayout idmap top bottom left right start end insideH insideV between bar
updateFields mirrorMargins evenAndOddHeaders
bordersDoNotSurroundHeader bordersDoNotSurroundFooter
drawingGridHorizontalSpacing drawingGridVerticalSpacing
displayHorizontalDrawingGridEvery displayVerticalDrawingGridEvery
noPunctuationKerning hdrShapeDefaults
fldData
""".split())

# These standard Word settings are document-level layout/display metadata. They are
# inventoried as preserved metadata only when they are direct children of w:settings;
# the production writer never edits or regenerates word/settings.xml.
_PRESERVED_SETTINGS_ROOT_ELEMENTS = frozenset({
    # Standard direct children of w:settings that do not alter editing permissions
    # or revision-generation policy. Their package part is never rewritten by
    # a body-text Patch.
    "bordersDoNotSurroundHeader",
    "bordersDoNotSurroundFooter",
    "drawingGridHorizontalSpacing",
    "drawingGridVerticalSpacing",
    "displayHorizontalDrawingGridEvery",
    "displayVerticalDrawingGridEvery",
    "noPunctuationKerning",
    "hdrShapeDefaults",
    "footnotePr",
    "endnotePr",
    "compat",
    "docVars",
    "doNotIncludeSubdocsInStats",
})
_PRESERVED_SETTINGS_CHILDREN = {
    "footnotePr": frozenset({"pos", "numFmt", "numStart", "numRestart", "footnote"}),
    "endnotePr": frozenset({"pos", "numFmt", "numStart", "numRestart", "endnote"}),
    "docVars": frozenset({"docVar"}),
}
# Word's compatibility container has a schema-defined vocabulary. Matching is
# parent-qualified: these are preserved only as direct w:compat children within
# word/settings.xml. Unknown future children remain BLOCKED.
_PRESERVED_COMPAT_CHILDREN = frozenset({
    "adjustLineHeightInTable",
    "alignTablesRowByRow",
    "allowSpaceOfSameStyleInTable",
    "applyBreakingRules",
    "autofitToFirstFixedWidthCell",
    "autoSpaceLikeWord95",
    "balanceSingleByteDoubleByteWidth",
    "cachedColBalance",
    "compatSetting",
    "convMailMergeEsc",
    "displayHangulFixedWidth",
    "doNotAutofitConstrainedTables",
    "doNotBreakConstrainedForcedTable",
    "doNotBreakWrappedTables",
    "doNotExpandShiftReturn",
    "doNotLeaveBackslashAlone",
    "doNotSnapToGridInCell",
    "doNotSuppressIndentation",
    "doNotSuppressParagraphBorders",
    "doNotUseEastAsianBreakRules",
    "doNotUseHTMLParagraphAutoSpacing",
    "doNotUseIndentAsNumberingTabStop",
    "doNotVertAlignCellWithSp",
    "doNotVertAlignInTxbx",
    "doNotWrapTextWithPunct",
    "footnoteLayoutLikeW8",
    "forgetLastTabAlignment",
    "growAutofit",
    "layoutRawTableWidth",
    "layoutTableRowsApart",
    "lineWrapLikeWord6",
    "mwSmallCaps",
    "noColumnBalance",
    "noExtraLineSpacing",
    "noLeading",
    "noSpaceRaiseLower",
    "noTabHangInd",
    "printBodyTextBeforeHeader",
    "printColBlack",
    "selectFldWithFirstOrLastChar",
    "shapeLayoutLikeW8",
    "showBreaksInFrames",
    "spaceForUL",
    "spacingInWholePoints",
    "splitPgBreakAndParaMark",
    "subFontBySize",
    "suppressBottomSpacing",
    "suppressSpacingAtTopOfPage",
    "suppressSpBfAfterPgBrk",
    "suppressTopSpacing",
    "suppressTopSpacingWP",
    "swapBordersFacingPages",
    "truncateFontHeightsLikeWP6",
    "underlineTabInNumList",
    "ulTrailSpace",
    "useAltKinsokuLineBreakRules",
    "useAnsiKerningPairs",
    "useFELayout",
    "useNormalStyleForList",
    "usePrinterMetrics",
    "useSingleBorderforContiguousCells",
    "useWord2002TableStyleRules",
    "useWord97LineBreakRules",
    "wpJustification",
    "wpSpaceWidth",
    "wrapTrailSpaces",
})
_GUARDED_SETTINGS_ELEMENTS = frozenset({
    "documentProtection",
    "writeProtection",
    "autoFormatOverride",
    "doNotTrackFormatting",
    "doNotTrackMoves",
})

def _preserved_standard_metadata_context(part: str, node: ET._Element) -> str | None:
    """Return the exact schema context for standard metadata that is preserved.

    The allow-list is intentionally parent-qualified. It does not bless unknown
    children merely because they occur somewhere inside word/settings.xml or
    word/styles.xml.
    """
    if not isinstance(node.tag, str) or _qname(node.tag).namespace != W:
        return None
    local = _qname(node.tag).localname
    parent = node.getparent()
    if parent is None or not isinstance(parent.tag, str):
        return None
    parent_local = _qname(parent.tag).localname

    if part == "word/settings.xml":
        if parent.tag == f"{{{W}}}settings" and local in _PRESERVED_SETTINGS_ROOT_ELEMENTS:
            return "word/settings.xml/w:settings"
        grandparent = parent.getparent()
        if (
            grandparent is not None
            and grandparent.tag == f"{{{W}}}settings"
            and local in _PRESERVED_SETTINGS_CHILDREN.get(parent_local, ())
        ):
            return f"word/settings.xml/w:settings/w:{parent_local}"
        if (
            parent.tag == f"{{{W}}}compat"
            and grandparent is not None
            and grandparent.tag == f"{{{W}}}settings"
            and local in _PRESERVED_COMPAT_CHILDREN
        ):
            return "word/settings.xml/w:settings/w:compat"

    if (
        part == "word/styles.xml"
        and parent.tag == f"{{{W}}}style"
        and local == "hidden"
    ):
        # w:hidden under w:style hides the style from Word's UI; it does not
        # hide the text formatted by that style.
        return "word/styles.xml/w:styles/w:style"

    return None


def _is_misplaced_standard_metadata(part: str, node: ET._Element) -> bool:
    """Reject registered settings vocabulary when it appears outside its schema context."""
    if not isinstance(node.tag, str) or _qname(node.tag).namespace != W:
        return False
    local = _qname(node.tag).localname
    parent = node.getparent()
    parent_local = _qname(parent.tag).localname if parent is not None and isinstance(parent.tag, str) else None
    grandparent = parent.getparent() if parent is not None else None
    grandparent_local = (
        _qname(grandparent.tag).localname
        if grandparent is not None and isinstance(grandparent.tag, str) else None
    )

    if local in _PRESERVED_SETTINGS_ROOT_ELEMENTS:
        if _preserved_standard_metadata_context(part, node) is not None:
            return False
        # footnotePr/endnotePr also have legitimate section-specific locations.
        if (
            local in {"footnotePr", "endnotePr"}
            and part == "word/document.xml"
            and parent_local == "sectPr"
        ):
            return False
        return True

    if local in {"footnote", "endnote", "pos", "numFmt", "numStart", "numRestart"}:
        if parent_local in {"footnotePr", "endnotePr"}:
            if _preserved_standard_metadata_context(part, node) is not None:
                return False
            if part == "word/document.xml" and grandparent_local == "sectPr":
                return False
            return True

    if local == "docVar" and _preserved_standard_metadata_context(part, node) is None:
        return True

    if local in _PRESERVED_COMPAT_CHILDREN:
        return _preserved_standard_metadata_context(part, node) is None

    return False


def _xml_parser() -> ET.XMLParser:
    return ET.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        remove_comments=False,
        huge_tree=False,
        recover=False,
    )


def _qname(tag: str) -> ET.QName:
    return ET.QName(tag)


def _path(node: ET._Element) -> str:
    """Return a deterministic 1-based same-tag sibling path for this package version."""
    chain: list[str] = []
    current = node
    while current is not None:
        q = _qname(current.tag)
        namespace = q.namespace or ""
        prefix = _PREFIXES.get(namespace)
        name = f"{prefix}:{q.localname}" if prefix else (
            f"{{{namespace}}}{q.localname}" if namespace else q.localname
        )
        parent = current.getparent()
        if parent is None:
            index = 1
        else:
            siblings = [child for child in parent if child.tag == current.tag]
            index = siblings.index(current) + 1
        chain.append(f"{name}[{index}]")
        current = parent
    return "/" + "/".join(reversed(chain))


def _nearest_paragraph(node: ET._Element, paragraph_indices: Mapping[ET._Element, int]) -> int | None:
    current = node
    while current is not None:
        if current.tag == f"{{{W}}}p":
            return paragraph_indices.get(current)
        current = current.getparent()
    return None


def _paragraph_span(node: ET._Element, paragraph_indices: Mapping[ET._Element, int]) -> list[int]:
    values: set[int] = set()
    nearest = _nearest_paragraph(node, paragraph_indices)
    if nearest is not None:
        values.add(nearest)
    for paragraph in node.iter(f"{{{W}}}p"):
        value = paragraph_indices.get(paragraph)
        if value is not None:
            values.add(value)
    return sorted(values)


def _ancestor_names(node: ET._Element) -> list[str]:
    return [_qname(item.tag).localname for item in reversed(list(node.iterancestors()))]


def _record(
    records: list[dict[str, Any]],
    *,
    source_sha256: str,
    part: str,
    node: ET._Element,
    kind: str,
    status: str,
    reason: str,
    paragraph_indices: Mapping[ET._Element, int],
    details: Mapping[str, Any] | None = None,
    paragraph_span: list[int] | None = None,
) -> dict[str, Any]:
    path = _path(node)
    namespace = _qname(node.tag).namespace or ""
    paragraph_index = _nearest_paragraph(node, paragraph_indices)
    span = paragraph_span if paragraph_span is not None else _paragraph_span(node, paragraph_indices)
    identity_source = "\0".join((source_sha256, part, path, kind))
    ancestors = _ancestor_names(node)
    item = {
        "structure_id": hashlib.sha256(identity_source.encode("utf-8")).hexdigest()[:24],
        "kind": kind,
        "element": _qname(node.tag).localname,
        "namespace": namespace,
        "part": part,
        "element_path": path,
        "paragraph_index": paragraph_index,
        "paragraph_indices": span,
        "ancestor_elements": ancestors,
        "nesting_depth": len(ancestors),
        "status": status,
        "reason": reason,
        "details": dict(details or {}),
    }
    records.append(item)
    return item


def _relationship_source(rels_name: str) -> str | None:
    if rels_name == "_rels/.rels":
        return None
    marker = "/_rels/"
    if marker not in rels_name or not rels_name.endswith(".rels"):
        return None
    directory, filename = rels_name.split(marker, 1)
    if not filename.endswith(".rels"):
        return None
    source_name = filename[:-5]
    return posixpath.join(directory, source_name) if directory else source_name


def _resolve_relationship_target(
    source_part: str | None, target: str, target_mode: str | None
) -> tuple[str | None, str | None]:
    if (target_mode or "").casefold() == "external":
        return None, None
    parsed = urlsplit(target)
    raw_path = unquote(parsed.path)
    if not raw_path:
        return None, "empty internal relationship target"
    if "\\" in raw_path:
        return None, "backslash in OPC relationship target"
    base = "" if raw_path.startswith("/") or source_part is None else posixpath.dirname(source_part)
    candidate = raw_path.lstrip("/") if raw_path.startswith("/") else posixpath.join(base, raw_path)
    normalized = posixpath.normpath(candidate)
    if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
        return None, "relationship target escapes the package root"
    return normalized, None


def _content_types(members: Mapping[str, bytes], issues: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    data = members.get("[Content_Types].xml")
    if data is None:
        issues.append({
            "code": "MISSING_CONTENT_TYPES",
            "severity": BLOCKED,
            "part": "[Content_Types].xml",
            "paragraph_index": None,
            "element_path": None,
            "message": "OPC package is missing [Content_Types].xml.",
        })
        return result
    try:
        root = ET.fromstring(data, parser=_xml_parser())
    except ET.XMLSyntaxError as exc:
        issues.append({
            "code": "MALFORMED_CONTENT_TYPES",
            "severity": BLOCKED,
            "part": "[Content_Types].xml",
            "paragraph_index": None,
            "element_path": None,
            "message": str(exc),
        })
        return result
    if _qname(root.tag).localname != "Types" or _qname(root.tag).namespace != CT:
        issues.append({
            "code": "INVALID_CONTENT_TYPES_ROOT", "severity": BLOCKED,
            "part": "[Content_Types].xml", "paragraph_index": None,
            "element_path": None, "message": "Content types part root element is not Types.",
        })
        return result
    defaults: dict[str, str] = {}
    overrides: dict[str, str] = {}
    for child in root:
        local = _qname(child.tag).localname
        if local == "Default":
            extension = child.get("Extension", "").casefold()
            content_type = child.get("ContentType", "")
            if extension and content_type:
                defaults[extension] = content_type
        elif local == "Override":
            part_name = child.get("PartName", "").lstrip("/")
            content_type = child.get("ContentType", "")
            if part_name and content_type:
                overrides[part_name] = content_type
    for name in members:
        extension = name.rsplit(".", 1)[-1].casefold() if "." in name else ""
        result[name] = overrides.get(name, defaults.get(extension, ""))
    return result


def _pair_ranges(
    *,
    part: str,
    root: ET._Element,
    start_local: str,
    end_local: str,
    kind: str,
    source_sha256: str,
    paragraph_indices: Mapping[ET._Element, int],
    records: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    start_tag, end_tag = f"{{{W}}}{start_local}", f"{{{W}}}{end_local}"
    starts: dict[str, list[ET._Element]] = defaultdict(list)
    ends: dict[str, list[ET._Element]] = defaultdict(list)
    for node in root.iter():
        if node.tag == start_tag:
            starts[node.get(f"{{{W}}}id", "")].append(node)
        elif node.tag == end_tag:
            ends[node.get(f"{{{W}}}id", "")].append(node)
    # Word range markers may legally be direct children of containers such
    # as w:body, not only descendants of w:p. Resolve their actual XML order
    # and intersect the marker interval with each paragraph's full subtree.
    # This avoids treating a valid body-level endpoint as an absent endpoint.
    ordered_nodes = list(root.iter())
    node_order = {node: index for index, node in enumerate(ordered_nodes)}
    paragraph_order: dict[int, tuple[int, int]] = {}
    for paragraph, paragraph_index in paragraph_indices.items():
        paragraph_nodes = list(paragraph.iter())
        paragraph_order[paragraph_index] = (
            node_order[paragraph],
            node_order[paragraph_nodes[-1]],
        )

    def paragraphs_intersecting_range(first_order: int, last_order: int) -> list[int]:
        lower, upper = sorted((first_order, last_order))
        return sorted(
            paragraph_index
            for paragraph_index, (paragraph_start, paragraph_end) in paragraph_order.items()
            if paragraph_end > lower and paragraph_start < upper
        )

    ids = sorted(set(starts) | set(ends))
    for marker_id in ids:
        left, right = starts.get(marker_id, []), ends.get(marker_id, [])
        nodes = left + right
        marker_positions = [node_order[node] for node in nodes]
        valid_pair = (
            len(left) == 1 and len(right) == 1 and bool(marker_id)
            and node_order[left[0]] < node_order[right[0]]
        )
        if valid_pair:
            start_order = node_order[left[0]]
            end_order = node_order[right[0]]
            span = paragraphs_intersecting_range(start_order, end_order)
            _record(
                records, source_sha256=source_sha256, part=part, node=left[0],
                kind=kind, status=GUARDED,
                reason=(
                    f"{kind} is a range-level structure; paragraphs intersecting "
                    "the actual XML document-order interval require a dedicated adapter."
                ),
                paragraph_indices=paragraph_indices, paragraph_span=span,
                details={
                    "range_id": marker_id,
                    "start_path": _path(left[0]),
                    "end_path": _path(right[0]),
                    "scope_mapping": "xml_document_order_paragraph_intersection",
                    "start_order": start_order,
                    "end_order": end_order,
                },
            )
        else:
            marker_indices = [
                value for value in (_nearest_paragraph(node, paragraph_indices) for node in nodes)
                if value is not None
            ]
            single_orphan_bookmark = (
                kind == "bookmark_range"
                and len(nodes) == 1
                and ((len(left) == 1 and not right) or (len(right) == 1 and not left))
            )
            if single_orphan_bookmark:
                # An unmatched bookmark marker is non-conformant and does not
                # establish an open-ended range. Gate only its containing
                # paragraph; if it is outside all paragraphs, the issue below
                # remains part-scoped because no paragraph anchor is available.
                span = sorted(set(marker_indices))
                scope_policy = (
                    "marker_paragraph_only" if marker_indices
                    else "part_scope_no_paragraph_anchor"
                )
            elif len(marker_positions) >= 2:
                # Duplicate, empty-ID or misordered marker groups remain
                # fail-closed, but only across the known outer marker interval.
                span = paragraphs_intersecting_range(
                    min(marker_positions), max(marker_positions)
                )
                scope_policy = "bounded_xml_document_order"
            elif kind != "bookmark_range" and marker_indices:
                # Retain the existing conservative directional treatment for
                # incomplete non-bookmark ranges; bookmark ranges never use it.
                all_indices = sorted(paragraph_indices.values())
                if left and not right:
                    span = [value for value in all_indices if value >= marker_indices[0]]
                elif right and not left:
                    span = [value for value in all_indices if value <= marker_indices[0]]
                else:
                    span = sorted(set(marker_indices))
                scope_policy = "legacy_directional"
            else:
                span = []
                scope_policy = "part_scope_no_paragraph_anchor" if nodes else "unmapped"

            for node in nodes:
                _record(
                    records, source_sha256=source_sha256, part=part, node=node,
                    kind=f"incomplete_{kind}", status=BLOCKED,
                    reason=(
                        "Unmatched bookmark marker is non-conformant and does not establish an editable range; "
                        "only its containing paragraph is gated, or the part is gated when no paragraph anchor exists."
                        if single_orphan_bookmark else
                        f"{kind} start/end markers are missing, duplicated, have an empty ID, or are out of document order."
                    ),
                    paragraph_indices=paragraph_indices, paragraph_span=span,
                    details={
                        "range_id": marker_id, "start_count": len(left), "end_count": len(right),
                        "scope_policy": scope_policy,
                        "marker_orders": sorted(marker_positions),
                    },
                )
            node = nodes[0] if nodes else None
            issue_paragraph = (
                _nearest_paragraph(node, paragraph_indices) if node is not None else None
            )
            if issue_paragraph is None and span:
                issue_paragraph = span[0]
            issues.append({
                "code": f"UNPAIRED_{kind.upper()}",
                "severity": BLOCKED,
                "part": part,
                "paragraph_index": issue_paragraph,
                "element_path": _path(node) if node is not None else None,
                "message": (
                    f"{kind} id {marker_id!r} has {len(left)} start and {len(right)} end marker(s). "
                    + ("No range is inferred from an unmatched bookmark marker."
                       if single_orphan_bookmark else "")
                ),
            })


def _field_groups(
    *,
    part: str,
    root: ET._Element,
    source_sha256: str,
    paragraph_indices: Mapping[ET._Element, int],
    records: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    stack: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    counter = 0
    for node in root.iter():
        if node.tag == f"{{{W}}}fldSimple":
            _record(
                records, source_sha256=source_sha256, part=part, node=node,
                kind="simple_field", status=DEFERRED,
                reason="Simple fields are not writable by the P0 text adapter.",
                paragraph_indices=paragraph_indices,
                details={"instruction": (node.get(f"{{{W}}}instr") or "")[:160]},
            )
        elif node.tag == f"{{{W}}}fldChar":
            field_type = (node.get(f"{{{W}}}fldCharType") or node.get(f"{{{W}}}type") or "").casefold()
            field_id = None
            if field_type == "begin":
                counter += 1
                field_id = f"{part}#field-{counter}"
                stack.append({
                    "field_id": field_id, "begin": _path(node), "separate": None,
                    "end": None, "instructions": [], "begin_node": node,
                })
            elif field_type == "separate":
                if stack:
                    field_id = stack[-1]["field_id"]
                    if stack[-1]["separate"] is not None:
                        issues.append({
                            "code": "DUPLICATE_FIELD_SEPARATOR", "severity": BLOCKED,
                            "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                            "element_path": _path(node),
                            "message": "A complex field contains more than one separate marker.",
                        })
                    stack[-1]["separate"] = _path(node)
                else:
                    issues.append({
                        "code": "ORPHAN_FIELD_SEPARATOR", "severity": BLOCKED,
                        "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                        "element_path": _path(node), "message": "Field separate marker has no open begin marker.",
                    })
            elif field_type == "end":
                if stack:
                    field_id = stack[-1]["field_id"]
                    group = stack.pop()
                    group["end"] = _path(node)
                    groups.append(group)
                else:
                    issues.append({
                        "code": "ORPHAN_FIELD_END", "severity": BLOCKED,
                        "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                        "element_path": _path(node), "message": "Field end marker has no open begin marker.",
                    })
            else:
                issues.append({
                    "code": "INVALID_FIELD_CHAR_TYPE", "severity": BLOCKED,
                    "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                    "element_path": _path(node), "message": f"Unrecognized fldCharType {field_type!r}.",
                })
            _record(
                records, source_sha256=source_sha256, part=part, node=node,
                kind="complex_field_marker", status=DEFERRED,
                reason="Complex field begin/separate/end semantics are not writable by the P0 text adapter.",
                paragraph_indices=paragraph_indices,
                details={"field_type": field_type, "field_id": field_id},
            )
        elif node.tag in {f"{{{W}}}instrText", f"{{{W}}}delInstrText"}:
            field_id = stack[-1]["field_id"] if stack else None
            if stack:
                stack[-1]["instructions"].append(_path(node))
            else:
                issues.append({
                    "code": "ORPHAN_FIELD_INSTRUCTION", "severity": BLOCKED,
                    "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                    "element_path": _path(node), "message": "Field instruction text is outside a complex field.",
                })
            _record(
                records, source_sha256=source_sha256, part=part, node=node,
                kind="field_instruction", status=DEFERRED,
                reason="Field instruction text is not ordinary editable prose.",
                paragraph_indices=paragraph_indices,
                details={"field_id": field_id, "instruction_preview": (node.text or "")[:120]},
            )
    for group in stack:
        groups.append(group)
        node = group["begin_node"]
        issues.append({
            "code": "UNCLOSED_FIELD", "severity": BLOCKED,
            "part": part, "paragraph_index": _nearest_paragraph(node, paragraph_indices),
            "element_path": group["begin"],
            "message": f"Complex field {group['field_id']} has no matching end marker.",
        })
    all_indices = sorted(paragraph_indices.values())
    for group in groups:
        begin_index = _nearest_paragraph(group["begin_node"], paragraph_indices)
        end_index = None
        if group["end"]:
            for candidate in root.iter(f"{{{W}}}fldChar"):
                if _path(candidate) == group["end"]:
                    end_index = _nearest_paragraph(candidate, paragraph_indices)
                    break
        if begin_index is not None and end_index is not None:
            span = [i for i in all_indices if min(begin_index, end_index) <= i <= max(begin_index, end_index)]
        elif begin_index is not None:
            span = [i for i in all_indices if i >= begin_index]
        else:
            span = []
        _record(
            records, source_sha256=source_sha256, part=part, node=group["begin_node"],
            kind="complex_field", status=DEFERRED if group["end"] else BLOCKED,
            reason="Complex field is a structured instruction/result range; editing requires a field-aware adapter.",
            paragraph_indices=paragraph_indices, paragraph_span=span,
            details={
                "field_id": group["field_id"], "begin_path": group["begin"],
                "separate_path": group["separate"], "end_path": group["end"],
                "instruction_paths": group["instructions"], "complete": bool(group["end"]),
            },
        )



def _build_structural_event_map(
    part: str,
    root: ET._Element,
    *,
    source_sha256: str,
    paragraph_indices: Mapping[ET._Element, int],
    records: list[dict[str, Any]],
    relationships: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expose parser-backed XML order and text/run coordinates for protected structures."""
    nodes = [node for node in root.iter() if isinstance(node.tag, str)]
    xml_order = {node: index for index, node in enumerate(nodes)}
    paragraph_for_node: dict[ET._Element, ET._Element] = {}
    run_for_node: dict[ET._Element, ET._Element] = {}
    run_index: dict[ET._Element, int] = {}
    text_index: dict[ET._Element, int] = {}
    text_offsets: dict[ET._Element, tuple[int, int]] = {}

    text_locals = {"t", "delText", "instrText", "delInstrText"}
    for paragraph in paragraph_indices:
        for descendant in paragraph.iter():
            paragraph_for_node[descendant] = paragraph
        runs = list(paragraph.iter(f"{{{W}}}r"))
        for index, run in enumerate(runs):
            run_index[run] = index
            for descendant in run.iter():
                run_for_node[descendant] = run
            cursor = 0
            text_nodes = [
                item for item in run.iter()
                if isinstance(item.tag, str)
                and _qname(item.tag).namespace == W
                and _qname(item.tag).localname in text_locals
            ]
            for text_node_index, text_node in enumerate(text_nodes):
                value = text_node.text or ""
                text_index[text_node] = text_node_index
                text_offsets[text_node] = (cursor, cursor + len(value))
                cursor += len(value)

    records_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("part") == part and isinstance(record.get("element_path"), str):
            records_by_path[record["element_path"]].append(record)

    range_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    range_links_by_order: dict[int, list[dict[str, Any]]] = defaultdict(list)
    field_links: dict[str, list[dict[str, Any]]] = defaultdict(list)
    field_links_by_order: dict[int, list[dict[str, Any]]] = defaultdict(list)
    order_by_path = {_path(node): order for node, order in xml_order.items()}
    for record in records:
        if record.get("part") != part:
            continue
        details = record.get("details")
        if not isinstance(details, Mapping):
            continue
        kind = record.get("kind")
        if kind in {"bookmark_range", "comment_range"}:
            start_path = details.get("start_path")
            end_path = details.get("end_path")
            link = {
                "range_kind": kind,
                "range_id": details.get("range_id"),
                "status": record.get("status"),
                "start_path": start_path,
                "end_path": end_path,
                "start_order": details.get("start_order"),
                "end_order": details.get("end_order"),
                "scope_mapping": details.get("scope_mapping"),
            }
            for marker_path in (start_path, end_path):
                if isinstance(marker_path, str):
                    range_links[marker_path].append(link)
            start_order = link.get("start_order")
            end_order = link.get("end_order")
            if (
                isinstance(start_order, int) and not isinstance(start_order, bool)
                and isinstance(end_order, int) and not isinstance(end_order, bool)
                and start_order <= end_order
            ):
                for order in range(start_order, end_order + 1):
                    range_links_by_order[order].append(link)
        elif kind == "complex_field":
            group = {
                "field_id": details.get("field_id"),
                "status": record.get("status"),
                "begin_path": details.get("begin_path"),
                "separate_path": details.get("separate_path"),
                "end_path": details.get("end_path"),
                "instruction_paths": details.get("instruction_paths", []),
                "complete": details.get("complete") is True,
            }
            paths = [
                details.get("begin_path"),
                details.get("separate_path"),
                details.get("end_path"),
                *(details.get("instruction_paths") or []),
            ]
            for marker_path in paths:
                if isinstance(marker_path, str):
                    field_links[marker_path].append(group)
            begin_order = order_by_path.get(details.get("begin_path"))
            end_order = order_by_path.get(details.get("end_path"))
            if isinstance(begin_order, int):
                upper = end_order if isinstance(end_order, int) else len(nodes) - 1
                if begin_order <= upper:
                    for order in range(begin_order, upper + 1):
                        field_links_by_order[order].append(group)

    events: list[dict[str, Any]] = []
    marker_locals = {
        "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd",
        "commentReference", "fldChar", "instrText", "delInstrText", "fldSimple",
        "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
        "customXmlInsRangeStart", "customXmlInsRangeEnd",
        "customXmlDelRangeStart", "customXmlDelRangeEnd",
        "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
        "permStart", "permEnd",
    }
    for node in nodes:
        q = _qname(node.tag)
        namespace, local = q.namespace or "", q.localname
        paragraph = paragraph_for_node.get(node)
        paragraph_index = paragraph_indices.get(paragraph) if paragraph is not None else None
        path = _path(node)
        attached_records = records_by_path.get(path, [])
        # Keep a complete event sequence for paragraph XML and every classified
        # structure outside paragraphs (for example a body-level range marker).
        if paragraph is None and not attached_records and not (
            namespace == W and local in {"tbl", "tr", "tc", "sectPr"}
        ):
            continue

        run = run_for_node.get(node)
        if namespace == W and local == "r":
            run = node
        event_kind = (
            "paragraph" if namespace == W and local == "p"
            else "run" if namespace == W and local == "r"
            else "text_node" if namespace == W and local in {"t", "delText"}
            else "field_instruction_text" if namespace == W and local in {"instrText", "delInstrText"}
            else "range_or_field_marker" if namespace == W and local in marker_locals
            else "xml_element"
        )
        if attached_records:
            event_kind = attached_records[0].get("kind", event_kind)

        attributes: dict[str, str] = {}
        relationship_refs: list[dict[str, Any]] = []
        for raw_name, value in node.attrib.items():
            attr = _qname(raw_name)
            prefix = _PREFIXES.get(attr.namespace or "")
            display_name = f"{prefix}:{attr.localname}" if prefix else attr.localname
            if attr.namespace in {W, R}:
                attributes[display_name] = value
            if attr.namespace == R and value:
                relationship = relationships.get(value)
                relationship_refs.append({
                    "attribute": display_name,
                    "relationship_id": value,
                    "status": relationship.get("status") if isinstance(relationship, Mapping) else "MISSING",
                    "target": relationship.get("target") if isinstance(relationship, Mapping) else None,
                    "resolved_target_part": (
                        relationship.get("resolved_target_part")
                        if isinstance(relationship, Mapping) else None
                    ),
                })

        row: dict[str, Any] = {
            "event_id": hashlib.sha256(
                "\\0".join((source_sha256, part, path, event_kind)).encode("utf-8")
            ).hexdigest()[:24],
            "event_kind": event_kind,
            "part": part,
            "element": local,
            "namespace": namespace,
            "element_path": path,
            "parent_path": _path(node.getparent()) if node.getparent() is not None else None,
            "xml_order": xml_order[node],
            "paragraph_index": paragraph_index,
            "paragraph_xml_order": xml_order.get(paragraph) if paragraph is not None else None,
            "run_index": run_index.get(run) if run is not None else None,
            "text_node_index_in_run": text_index.get(node),
            "text_offset_in_run": (
                {"start": text_offsets[node][0], "end": text_offsets[node][1]}
                if node in text_offsets else None
            ),
            "attributes": attributes,
            "relationship_refs": relationship_refs,
            "structure_records": [
                {
                    "structure_id": record.get("structure_id"),
                    "kind": record.get("kind"),
                    "status": record.get("status"),
                    "reason": record.get("reason"),
                    "details": dict(record.get("details") or {}),
                }
                for record in attached_records
            ],
            "paired_ranges": list(dict.fromkeys(
                range_links.get(path, []) + range_links_by_order.get(xml_order[node], [])
            )),
            "field_groups": list(dict.fromkeys(
                field_links.get(path, []) + field_links_by_order.get(xml_order[node], [])
            )),
        }
        if namespace == W and local in text_locals:
            value = node.text or ""
            row["text_length"] = len(value)
            row["text_content"] = value
        events.append(row)

    return events

def _classify_members(
    members: Mapping[str, bytes],
    *,
    source_sha256: str | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    digest = source_sha256 or hashlib.sha256(b"".join(
        name.encode("utf-8") + b"\0" + members[name] for name in sorted(members)
    )).hexdigest()
    issues: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    structural_events: list[dict[str, Any]] = []
    package_names = set(members)

    for name in sorted(package_names):
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or ".." in normalized.split("/") or "\\" in name:
            issues.append({
                "code": "UNSAFE_PACKAGE_MEMBER_PATH", "severity": BLOCKED,
                "part": name, "paragraph_index": None, "element_path": None,
                "message": "Package member path is absolute, traverses upward, or uses a backslash.",
            })
    if "word/document.xml" not in package_names:
        issues.append({
            "code": "MISSING_MAIN_DOCUMENT", "severity": BLOCKED,
            "part": "word/document.xml", "paragraph_index": None, "element_path": None,
            "message": "OPC package is missing word/document.xml.",
        })

    content_types = _content_types(members, issues)
    parts: list[dict[str, Any]] = []
    parsed_roots: dict[str, ET._Element] = {}
    for name in sorted(package_names):
        is_xml = name.casefold().endswith(".xml") or name.casefold().endswith(".rels")
        part_item: dict[str, Any] = {
            "name": name,
            "size_bytes": len(members[name]),
            "content_type": content_types.get(name, ""),
            "is_xml": is_xml,
            "parse_status": "NOT_XML",
            "root_element": None,
        }
        if is_xml:
            try:
                root = ET.fromstring(members[name], parser=_xml_parser())
                parsed_roots[name] = root
                part_item["parse_status"] = "PARSED"
                part_item["root_element"] = _qname(root.tag).localname
            except (ET.XMLSyntaxError, ValueError) as exc:
                part_item["parse_status"] = "MALFORMED_XML"
                issues.append({
                    "code": "MALFORMED_XML_PART", "severity": BLOCKED,
                    "part": name, "paragraph_index": None, "element_path": None,
                    "message": str(exc),
                })
        parts.append(part_item)

    if "[Content_Types].xml" not in parsed_roots:
        # _content_types already provided the precise missing/malformed issue.
        pass

    # Parse every relationship part and resolve internal targets without network access.
    relationships: list[dict[str, Any]] = []
    relationships_by_source: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for rels_name, root in parsed_roots.items():
        if not rels_name.endswith(".rels"):
            continue
        if _qname(root.tag).localname != "Relationships" or _qname(root.tag).namespace != PKG_REL:
            issues.append({
                "code": "INVALID_RELATIONSHIPS_ROOT", "severity": BLOCKED,
                "part": rels_name, "paragraph_index": None, "element_path": None,
                "message": "Relationship part root must be Relationships in the OPC relationships namespace.",
            })
            continue
        source_part = _relationship_source(rels_name)
        seen_relationship_ids: set[str] = set()
        for rel in root:
            if rel.tag != f"{{{PKG_REL}}}Relationship":
                issues.append({
                    "code": "UNKNOWN_RELATIONSHIP_ELEMENT", "severity": BLOCKED,
                    "part": rels_name, "paragraph_index": None, "element_path": _path(rel),
                    "message": "Relationship part contains an element outside the OPC Relationship vocabulary.",
                })
                continue
            rel_id = rel.get("Id", "")
            if not rel_id or rel_id in seen_relationship_ids:
                issues.append({
                    "code": "INVALID_OR_DUPLICATE_RELATIONSHIP_ID", "severity": BLOCKED,
                    "part": rels_name, "paragraph_index": None, "element_path": _path(rel),
                    "message": f"Relationship Id {rel_id!r} is empty or duplicated within its source part.",
                })
            seen_relationship_ids.add(rel_id)
            target = rel.get("Target", "")
            target_mode = rel.get("TargetMode", "Internal")
            resolved, error = _resolve_relationship_target(source_part, target, target_mode)
            item = {
                "relationship_part": rels_name,
                "source_part": source_part,
                "relationship_id": rel_id,
                "relationship_type": rel.get("Type", ""),
                "target": target,
                "target_mode": target_mode,
                "resolved_target_part": resolved,
                "status": "EXTERNAL" if target_mode.casefold() == "external" else (
                    "BLOCKED" if error else "RESOLVED" if resolved in package_names else "MISSING_TARGET"
                ),
            }
            relationships.append(item)
            if source_part and rel_id:
                relationships_by_source[source_part][rel_id] = item
            if error:
                issues.append({
                    "code": "UNSAFE_RELATIONSHIP_TARGET", "severity": BLOCKED,
                    "part": rels_name, "paragraph_index": None, "element_path": None,
                    "message": error,
                })
            elif target_mode.casefold() != "external" and resolved not in package_names:
                issues.append({
                    "code": "MISSING_RELATIONSHIP_TARGET", "severity": BLOCKED,
                    "part": rels_name, "paragraph_index": None, "element_path": None,
                    "message": f"{rel_id or '<missing Id>'} points to absent package part {resolved!r}.",
                })

    for part, root in parsed_roots.items():
        if part.endswith(".rels") or part == "[Content_Types].xml":
            continue
        paragraph_nodes = list(root.iter(f"{{{W}}}p"))
        paragraph_indices = {node: index for index, node in enumerate(paragraph_nodes)}
        table_counter = 0
        for node in root.iter():
            if not isinstance(node.tag, str):
                continue
            q = _qname(node.tag)
            local, namespace = q.localname, q.namespace or ""
            ancestors = _ancestor_names(node)
            status: str | None = None
            kind = ""
            reason = ""
            details: dict[str, Any] = {}

            if namespace == MC and local == "AlternateContent":
                kind, status, reason = "markup_compatibility_alternate_content", BLOCKED, "Markup-compatibility Choice/Fallback branches must be interpreted before editing the containing paragraph."
            elif namespace == W:
                if local in {"bookmarkStart", "bookmarkEnd"}:
                    kind, status, reason = "bookmark_marker", GUARDED, "Bookmark ranges must not be split or rebalanced without a bookmark-aware adapter."
                    details["bookmark_id"] = node.get(f"{{{W}}}id")
                    details["bookmark_name"] = node.get(f"{{{W}}}name")
                elif local in {"commentRangeStart", "commentRangeEnd"}:
                    kind, status, reason = "comment_range_marker", GUARDED, "Comment ranges must not be split or rebalanced without a comment-aware adapter."
                    details["comment_id"] = node.get(f"{{{W}}}id")
                elif local == "commentReference":
                    kind, status, reason = "comment_reference", GUARDED, "Comment anchors are protected structural content."
                    details["comment_id"] = node.get(f"{{{W}}}id")
                elif local in {"fldChar", "instrText", "delInstrText", "fldSimple"}:
                    # Detailed field grouping and pairing is performed below.
                    continue
                elif local in {"ins", "del", "moveFrom", "moveTo"}:
                    if local == "del" and "pPr" in ancestors and "rPr" in ancestors:
                        kind, status, reason = "paragraph_mark_deletion", GUARDED, "Deleting a paragraph mark changes paragraph topology and requires the paragraph revision adapter."
                    else:
                        kind, status, reason = "text_revision_wrapper", GUARDED, "Existing revision wrappers require explicit history policy before another edit intersects them."
                    details["revision_id"] = node.get(f"{{{W}}}id")
                    details["author"] = node.get(f"{{{W}}}author")
                elif local in {"cellIns", "cellDel", "cellMerge"}:
                    kind, status, reason = "table_structure_revision", DEFERRED, "Table structure revisions are not supported by the P0 writer."
                elif local.endswith("Change") or local == "numberingChange":
                    kind, status, reason = "format_revision", GUARDED, "Historical property-change snapshots require explicit Accept/Reject handling."
                    details["revision_id"] = node.get(f"{{{W}}}id")
                elif local in {"moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"}:
                    kind, status, reason = "move_range_marker", GUARDED, "Move range markers require paired range handling."
                elif local == "tbl":
                    table_counter += 1
                    depth = 1 + sum(1 for ancestor in node.iterancestors() if ancestor.tag == f"{{{W}}}tbl")
                    if depth > 1:
                        kind, status, reason = "nested_table", DEFERRED, "Nested tables are inventoried but not writable by the P0 text adapter."
                        details.update({"table_index": table_counter, "table_depth": depth})
                elif local == "hyperlink":
                    kind, status, reason = "hyperlink_container", SUPPORTED, "Only a target wholly contained in one simple hyperlink is supported; cross-container or ambiguous targets remain blocked."
                    details["relationship_id"] = node.get(f"{{{R}}}id")
                elif local in {"sdt", "customXml", "smartTag", "altChunk"}:
                    kind, status, reason = "structured_container", DEFERRED, f"{local} content requires a dedicated OOXML adapter."
                elif local in {"drawing", "pict", "object"}:
                    kind, status, reason = "embedded_object", DEFERRED, "Drawing/object content is not editable as ordinary paragraph text."
                elif _preserved_standard_metadata_context(part, node) is not None:
                    metadata_context = _preserved_standard_metadata_context(part, node)
                    kind, status, reason = (
                        "preserved_standard_metadata", SUPPORTED,
                        "Recognized schema-positioned Word settings/style metadata; retained byte-for-byte outside the body edit scope.",
                    )
                    details["preserve_unchanged"] = True
                    details["metadata_context"] = metadata_context
                elif _is_misplaced_standard_metadata(part, node):
                    kind, status, reason = (
                        "misplaced_standard_metadata", BLOCKED,
                        "A registered settings/style metadata element appears outside its schema-defined package-part and parent-child context.",
                    )
                elif local in _GUARDED_SETTINGS_ELEMENTS:
                    if (
                        part == "word/settings.xml"
                        and node.getparent() is not None
                        and node.getparent().tag == f"{{{W}}}settings"
                    ):
                        kind, status, reason = (
                            "document_editing_setting", GUARDED,
                            "Document protection or revision-generation settings require an explicit supported policy.",
                        )
                    else:
                        kind, status, reason = (
                            "misplaced_document_setting", BLOCKED,
                            "A settings-only WordprocessingML element is not in the direct-child position of word/settings.xml.",
                        )
                elif local == "proofErr":
                    details["proofing_type"] = node.get(f"{{{W}}}type")
                    if part != "word/settings.xml" and _nearest_paragraph(node, paragraph_indices) is not None:
                        kind, status, reason = (
                            "proofing_range_marker", SUPPORTED,
                            "Word spelling/grammar annotation anchor, not an editing-permission range; retained in place and treated as an ordinary-run boundary.",
                        )
                    else:
                        kind, status, reason = (
                            "proofing_range_marker", BLOCKED,
                            "A proofing anchor outside paragraph-bearing content is malformed or misplaced.",
                        )
                elif local in {"permStart", "permEnd"}:
                    kind, status, reason = (
                        "protected_range_marker", GUARDED,
                        "Editing-permission range markers remain protected and require a dedicated permission-aware adapter.",
                    )
                elif local in {"trackRevisions", "revisionView"}:
                    kind, status, reason = "revision_setting", GUARDED, "Document revision settings are retained but require explicit policy."
                elif local not in _KNOWN_WML_LOCALS:
                    kind, status, reason = "unknown_wordprocessingml_element", BLOCKED, "Unrecognized WordprocessingML element is not assumed to be ordinary text."
                    details["local_name"] = local
            elif namespace == M and local in {"oMath", "oMathPara"}:
                kind, status, reason = "omml_formula", DEFERRED, "OMML is read-only until the P1 equation adapter is implemented."
            elif namespace and namespace not in _KNOWN_NAMESPACES:
                kind, status, reason = "unknown_extension_element", BLOCKED, "Unrecognized extension namespace is preserved but cannot be edited safely."
                details["namespace_uri"] = namespace

            if status is not None:
                _record(
                    records, source_sha256=digest, part=part, node=node,
                    kind=kind, status=status, reason=reason,
                    paragraph_indices=paragraph_indices, details=details,
                )

            # Check relationship references embedded in XML elements (r:id, r:embed, r:link).
            for attr_name, attr_value in node.attrib.items():
                attr_q = _qname(attr_name)
                if attr_q.namespace == R and attr_value:
                    relationship = relationships_by_source.get(part, {}).get(attr_value)
                    if relationship is None:
                        issues.append({
                            "code": "MISSING_RELATIONSHIP_ID", "severity": BLOCKED,
                            "part": part,
                            "paragraph_index": _nearest_paragraph(node, paragraph_indices),
                            "element_path": _path(node),
                            "message": f"Attribute {attr_q.localname} references missing relationship {attr_value!r}.",
                        })

        _pair_ranges(
            part=part, root=root, start_local="bookmarkStart", end_local="bookmarkEnd",
            kind="bookmark_range", source_sha256=digest,
            paragraph_indices=paragraph_indices, records=records, issues=issues,
        )
        _pair_ranges(
            part=part, root=root, start_local="commentRangeStart", end_local="commentRangeEnd",
            kind="comment_range", source_sha256=digest,
            paragraph_indices=paragraph_indices, records=records, issues=issues,
        )
        _field_groups(
            part=part, root=root, source_sha256=digest,
            paragraph_indices=paragraph_indices, records=records, issues=issues,
        )
        structural_events.extend(_build_structural_event_map(
            part,
            root,
            source_sha256=digest,
            paragraph_indices=paragraph_indices,
            records=records,
            relationships=relationships_by_source.get(part, {}),
        ))

    # A relationship issue inside a specific paragraph is a local edit blocker.
    blocked_package_codes = {
        "MISSING_MAIN_DOCUMENT", "MISSING_CONTENT_TYPES", "MALFORMED_CONTENT_TYPES",
        "INVALID_CONTENT_TYPES_ROOT", "UNSAFE_PACKAGE_MEMBER_PATH", "MALFORMED_XML_PART",
        "INVALID_RELATIONSHIPS_ROOT", "UNKNOWN_RELATIONSHIP_ELEMENT",
        "INVALID_OR_DUPLICATE_RELATIONSHIP_ID", "UNSAFE_RELATIONSHIP_TARGET",
        "MISSING_RELATIONSHIP_TARGET", "MISSING_RELATIONSHIP_ID",
    }
    fatal_package_issues = [item for item in issues if item["code"] in blocked_package_codes]
    summary = {
        "package_parts": len(parts),
        "xml_parts": sum(1 for item in parts if item["is_xml"]),
        "parsed_xml_parts": sum(1 for item in parts if item["parse_status"] == "PARSED"),
        "malformed_xml_parts": sum(1 for item in parts if item["parse_status"] == "MALFORMED_XML"),
        "relationships": len(relationships),
        "structures": len(records),
        "structures_by_kind": dict(sorted(Counter(item["kind"] for item in records).items())),
        "structures_by_status": dict(sorted(Counter(item["status"] for item in records).items())),
        "structural_events": len(structural_events),
        "structural_events_by_kind": dict(sorted(
            Counter(item["event_kind"] for item in structural_events).items()
        )),
        "issues_by_code": dict(sorted(Counter(item["code"] for item in issues).items())),
    }
    return {
        "schema_version": "1.0",
        "source_name": source_name,
        "source_sha256": digest,
        "package_status": "BLOCKED" if fatal_package_issues else "READY",
        "classification_status": "COMPLETE" if not fatal_package_issues else "INCOMPLETE",
        "parts": parts,
        "relationships": relationships,
        "structures": records,
        "structural_events": structural_events,
        "issues": issues,
        "summary": summary,
    }


def classify_docx_package_members(
    members: Mapping[str, bytes],
    *,
    source_sha256: str | None = None,
    source_name: str | None = None,
) -> dict[str, Any]:
    """Classify an already-read package member map without changing any bytes."""
    if not isinstance(members, Mapping):
        raise TypeError("Package members must be a mapping of part names to bytes.")
    for name, data in members.items():
        if not isinstance(name, str) or not isinstance(data, bytes):
            raise TypeError("Every package member must map a string name to bytes.")
    return _classify_members(members, source_sha256=source_sha256, source_name=source_name)


def classify_docx_structure(source: str | Path | bytes) -> dict[str, Any]:
    """Read and classify a DOCX package. No output file is created or modified."""
    source_name: str | None = None
    payload: bytes
    if isinstance(source, (str, Path)):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise StructureClassificationError(f"DOCX file does not exist: {path}")
        payload = path.read_bytes()
        source_name = str(path)
    elif isinstance(source, bytes):
        payload = source
    else:
        raise TypeError("source must be a filesystem path or DOCX bytes.")
    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise StructureClassificationError(f"DOCX ZIP CRC failure in {bad_member}.")
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != len(set(names)):
                duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
                raise StructureClassificationError(
                    "DOCX contains duplicate ZIP member names: " + ", ".join(duplicates[:10])
                )
            members = {name: archive.read(name) for name in names}
    except (OSError, zipfile.BadZipFile) as exc:
        raise StructureClassificationError(f"Cannot read DOCX package: {exc}") from exc
    report = _classify_members(
        members, source_sha256=hashlib.sha256(payload).hexdigest(), source_name=source_name
    )
    if report["package_status"] != "READY":
        details = "; ".join(
            f"{item['code']}: {item['message']}" for item in report["issues"]
            if item["severity"] == BLOCKED
        )
        report["blocking_summary"] = details
    return report


_GLOBAL_CONTEXT_PARTS = frozenset({
    "word/styles.xml", "word/numbering.xml", "word/settings.xml", "word/fontTable.xml",
})


def assert_edit_target_supported(
    inventory: Mapping[str, Any],
    *,
    part: str,
    paragraph_index: int,
) -> None:
    """Fail closed if a target paragraph intersects a guarded or unsupported structure."""
    if inventory.get("package_status") != "READY":
        detail = inventory.get("blocking_summary") or "package classification is not READY"
        raise StructureClassificationError(
            f"Refusing edit in {part} P[{paragraph_index}]: {detail}"
        )
    blockers: list[str] = []
    for item in inventory.get("structures", []):
        item_part = item.get("part")
        if item.get("status") not in _BLOCKING_STATUSES:
            continue
        # Existing text revision wrappers are inventoried as guarded history.
        # The P0 text writer edits only contiguous ordinary w:r sequences; it
        # cannot match or cross a w:ins/w:del wrapper, so unrelated ordinary text
        # in the same paragraph remains editable without rewriting that history.
        if item.get("kind") == "text_revision_wrapper":
            continue
        span = item.get("paragraph_indices") or []
        target_match = (
            item_part == part
            and (item.get("paragraph_index") == paragraph_index or paragraph_index in span)
        )
        # Explicitly preserved settings metadata has SUPPORTED status and is
        # skipped above. Unknown/guarded settings remain global blockers.
        global_context_match = item_part in _GLOBAL_CONTEXT_PARTS and not span
        if target_match or global_context_match:
            blockers.append(
                f"{item.get('kind')} ({item.get('status')}) at {item.get('part')}:{item.get('element_path')}"
            )
    for issue in inventory.get("issues", []):
        issue_part = issue.get("part")
        target_match = (
            issue_part == part
            and issue.get("severity") == BLOCKED
            and issue.get("paragraph_index") in {None, paragraph_index}
        )
        global_context_match = (
            issue_part in _GLOBAL_CONTEXT_PARTS
            and issue.get("severity") == BLOCKED
        )
        if target_match or global_context_match:
            blockers.append(f"{issue.get('code')} at {issue_part}:{issue.get('element_path')}")
    if blockers:
        raise StructureClassificationError(
            f"Refusing edit in {part} P[{paragraph_index}] because the target scope intersects "
            "guarded/unsupported OOXML structure: " + "; ".join(blockers[:12])
        )

def assert_edit_target_supported_with_bounded_safe_span(
    inventory: Mapping[str, Any],
    *,
    part: str,
    paragraph_index: int,
    paragraph: ET._Element,
    part_root: ET._Element,
    target_text: str,
) -> dict[str, Any]:
    """Permit a precise ordinary-Run span outside bookmark ranges; keep all other gates.

    The fallback is intentionally narrow: only valid paired bookmark_range
    records may be reconsidered. Incomplete markers, Fields, comment ranges,
    hyperlinks, drawings, unknown structures and package-level issues remain
    blocked by the original paragraph gate.
    """
    gate_error: StructureClassificationError | None = None
    try:
        assert_edit_target_supported(
            inventory, part=part, paragraph_index=paragraph_index
        )
        return {
            "status": "ALLOW_CANDIDATE",
            "allowed": True,
            "method": "paragraph_scope",
            "authorization_note": (
                "Structural permission only; all authorization and verification gates remain mandatory."
            ),
        }
    except StructureClassificationError as exc:
        gate_error = exc

    if inventory.get("package_status") != "READY":
        raise gate_error

    relevant: list[Mapping[str, Any]] = []
    for item in inventory.get("structures", []):
        if item.get("status") not in _BLOCKING_STATUSES:
            continue
        item_part = item.get("part")
        span = item.get("paragraph_indices") or []
        target_match = (
            item_part == part
            and (item.get("paragraph_index") == paragraph_index or paragraph_index in span)
        )
        global_context_match = item_part in _GLOBAL_CONTEXT_PARTS and not span
        if target_match or global_context_match:
            relevant.append(item)

    blocking_issues = []
    for issue in inventory.get("issues", []):
        issue_part = issue.get("part")
        target_match = (
            issue_part == part
            and issue.get("severity") == BLOCKED
            and issue.get("paragraph_index") in {None, paragraph_index}
        )
        global_context_match = (
            issue_part in _GLOBAL_CONTEXT_PARTS
            and issue.get("severity") == BLOCKED
        )
        if target_match or global_context_match:
            blocking_issues.append(issue)

    if blocking_issues or not relevant or any(
        item.get("kind") not in {"bookmark_range", "bookmark_marker"}
        for item in relevant
    ):
        raise gate_error

    from .bounded_safe_span import analyze_bounded_safe_span

    analysis = analyze_bounded_safe_span(part_root, paragraph, target_text)
    if not analysis.get("allowed"):
        raise StructureClassificationError(
            f"Refusing edit in {part} P[{paragraph_index}]: bounded safe-span analysis blocked "
            f"{target_text[:80]!r}: {analysis.get('reason', 'no safe span was proven')}."
        )
    analysis["intersecting_bookmark_structure_ids"] = [
        item.get("structure_id") for item in relevant
    ]
    return analysis


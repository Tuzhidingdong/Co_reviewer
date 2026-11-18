# -*- coding: utf-8 -*-
"""Conservative V2.3.2 editing contracts.

This module supplies deterministic authorization, format inheritance, package
reference graph, structural operation guards and bounded remediation primitives.
It deliberately does not claim Microsoft Word interoperability without Word QA.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence
from lxml import etree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS = {"w": W, "r": R, "pr": PKG, "ct": CT, "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
def qn(local: str) -> str:
    return "{%s}%s" % (W, local)
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
def _xml(data: bytes) -> ET._Element:
    return ET.fromstring(data, parser=ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False))

class ContractError(ValueError):
    pass

# Token patterns are intentionally conservative. Ambiguous chemical formula-like
# strings are locked only when they contain a digit or an element sequence.
_TOKEN_RE = re.compile(
    r"(?P<citation>\[(?:\s*\d+(?:\s*[-,;]\s*\d+)*\s*)+\])"
    r"|(?P<figure>\b(?:Fig(?:ure)?|Table|Eq(?:uation)?|Scheme|Section)\.?\s*(?:S\s*)?\(?\d+(?:\.\d+)*[a-z]?\)?(?:\s*[-–]\s*\d+)?\b)"
    r"|(?P<stat>\b(?:p|P|R|r|n|N|t|F|D|Q|CI|OR|HR)\s*[=<]\s*[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?\b)"
    r"|(?P<number>(?<![A-Za-z])[-−+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?(?:\s*±\s*\d+(?:[.,]\d+)?(?:\s*%)?)?(?:\s*%)?)"
    r"|(?P<unit>\b(?:mAh|Ah|mL|µL|μL|nL|L|mg|µg|μg|ng|kg|g[-−]1|mg[-−]1|kg[-−]1|mL[-−]1|L[-−]1|mol[-−]1|mmol[-−]1|cm[-−]1|mol|mmol|µmol|μmol|nmol|mM|µM|μM|nM|M|Pa|kPa|MPa|bar|atm|J|kJ|eV|meV|W|mW|kW|V|mV|mA|A|mS|S|Hz|kHz|MHz|GHz|ms|min|h|day|d|K|°C|°F|nm|µm|μm|mm|cm|m|Å|angstrom|cm⁻¹|m²|cm²|m³|cm³)\b)"
    r"|(?P<formula>(?<![A-Za-z])(?:[A-Z][a-z]?[0-9₀-₉⁰-⁹]*)+(?:[·.](?:H2O|[A-Z][a-z]?[0-9₀-₉⁰-⁹]*)+)?(?:[⁰¹²³⁴⁵⁶⁷⁸⁹]+[⁺⁻]?|[⁺⁻])?(?=$|[^A-Za-z0-9]))"
)
_ELEMENT_SYMBOLS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba
La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb
Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs
Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
""".split())


def _is_chemical_formula(value: str) -> bool:
    normalized = value.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉⁰¹²³⁴⁵⁶⁷⁸⁹", "01234567890123456789"))
    normalized = normalized.replace("⁺", "+").replace("⁻", "-")
    normalized = re.sub(r"[+-]+$", "", normalized).replace("·", ".")
    pieces = normalized.split(".")
    symbol_count, has_count = 0, False
    for piece in pieces:
        tokens = re.findall(r"([A-Z][a-z]?)(\d*)", piece)
        if not tokens or "".join(symbol + count for symbol, count in tokens) != piece:
            return False
        for symbol, count in tokens:
            if symbol not in _ELEMENT_SYMBOLS:
                return False
            symbol_count += 1
            has_count = has_count or bool(count)
    return symbol_count >= 2 or has_count


def extract_immutable_tokens(text: str) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    found = []
    for match in _TOKEN_RE.finditer(text):
        kind = match.lastgroup or "token"
        value = match.group()
        if kind == "number" and not re.search(r"\d", value):
            continue
        if kind == "formula" and not _is_chemical_formula(value):
            continue
        if kind == "unit" and value in {"A", "S", "M", "m", "s", "d", "h", "K", "V"}:
            if not re.search(r"\d\s*$", text[:match.start()]):
                continue
        found.append({"kind": kind, "text": value, "start": match.start(), "end": match.end(),
                      "normalized": re.sub(r"\s+", "", value).replace("−", "-")})
    # Merge adjacent number + unit into a single protected semantic bundle.
    merged = []
    for item in found:
        if merged and item["kind"] == "unit" and merged[-1]["kind"] == "number" and not text[merged[-1]["end"]:item["start"]].strip():
            prev = merged.pop()
            merged.append({"kind": "quantity", "text": text[prev["start"]:item["end"]],
                           "start": prev["start"], "end": item["end"],
                           "normalized": re.sub(r"\s+", "", text[prev["start"]:item["end"]]).replace("−", "-")})
        else:
            merged.append(item)
    return merged

def _authorized_token_pairs(authorized: Mapping[str, Any] | None) -> set[tuple[str, str, str]]:
    """Return exact old->new token authorizations; free-form intent is never sufficient."""
    if authorized is None:
        return set()
    if not isinstance(authorized, Mapping):
        raise ContractError("Authorized Change must be an object")
    raw = authorized.get("explicitly_authorized_tokens", [])
    if not isinstance(raw, list):
        raise ContractError("explicitly_authorized_tokens must be an array")
    result: set[tuple[str, str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str) or not isinstance(item.get("replacement"), str):
            raise ContractError("Each authorized token requires exact text and replacement strings")
        kind = str(item.get("kind", "*"))
        normalize = lambda value: re.sub(r"\s+", "", value).replace("−", "-")
        result.add((kind, normalize(item["text"]), normalize(item["replacement"])))
    return result


def validate_immutable_token_lock(original: str, proposed: str,
                                  authorized: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Enforce ordered, exact old->new authorization for every protected-token change."""
    before, after = extract_immutable_tokens(original), extract_immutable_tokens(proposed)
    allowed = _authorized_token_pairs(authorized)
    before_keys = [(x["kind"], x["normalized"]) for x in before]
    after_keys = [(x["kind"], x["normalized"]) for x in after]
    matcher = difflib.SequenceMatcher(a=before_keys, b=after_keys, autojunk=False)
    conflicts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_group, new_group = before[i1:i2], after[j1:j2]
        if len(old_group) != 1 or len(new_group) != 1:
            conflicts.append({"old_tokens": old_group, "new_tokens": new_group, "reason": "NON_ATOMIC_TOKEN_CHANGE"})
            continue
        old_token, new_token = old_group[0], new_group[0]
        pair = (old_token["kind"], old_token["normalized"], new_token["normalized"])
        wildcard_pair = ("*", old_token["normalized"], new_token["normalized"])
        if pair not in allowed and wildcard_pair not in allowed:
            conflicts.append({"old_tokens": old_group, "new_tokens": new_group, "reason": "PAIR_NOT_AUTHORIZED"})
    if conflicts:
        return {"status": "UNPROTECTED_TOKEN_TOUCHED", "allowed": False,
                "conflicts": conflicts, "missing_tokens": [x for c in conflicts for x in c["old_tokens"]],
                "added_tokens": [x for c in conflicts for x in c["new_tokens"]]}
    return {"status": "PASS", "allowed": True, "conflicts": [], "missing_tokens": [], "added_tokens": []}


def _word_tokens(text: str) -> list[str]:
    return re.findall(r"\s+|[^\W_]+(?:['’][^\W_]+)?|_+|[^\w\s]", text, flags=re.UNICODE)

def myers_diff(a: Sequence[str], b: Sequence[str]) -> list[tuple[str, str]]:
    """Myers O((N+M)D) shortest edit script over token sequences."""
    n, m = len(a), len(b)
    if not n: return [("+", x) for x in b]
    if not m: return [("-", x) for x in a]
    v = {1: 0}
    trace = []
    for d in range(n + m + 1):
        trace.append(v.copy())
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v.get(k - 1, -1) < v.get(k + 1, -1)):
                x = v.get(k + 1, 0)
            else:
                x = v.get(k - 1, 0) + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1; y += 1
            v[k] = x
            if x >= n and y >= m:
                script = []
                cx, cy = n, m
                for dd in range(d, -1, -1):
                    vv = trace[dd]
                    kk = cx - cy
                    if kk == -dd or (kk != dd and vv.get(kk - 1, -1) < vv.get(kk + 1, -1)):
                        prev_k = kk + 1
                    else:
                        prev_k = kk - 1
                    prev_x = vv.get(prev_k, 0)
                    prev_y = prev_x - prev_k
                    while cx > prev_x and cy > prev_y:
                        script.append(("=", a[cx - 1])); cx -= 1; cy -= 1
                    if dd == 0: break
                    if cx == prev_x:
                        script.append(("+", b[cy - 1])); cy -= 1
                    else:
                        script.append(("-", a[cx - 1])); cx -= 1
                return list(reversed(script))
    raise ContractError("Myers diff failed to converge")

def narrow_revision(original: str, proposed: str, *,
                    authorized_change: Mapping[str, Any],
                    protected_spans: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Produce word-level minimal diff, refine changed blocks, and enforce authorization."""
    if not isinstance(authorized_change, Mapping) or not authorized_change.get("intent"):
        raise ContractError("An explicit Authorized Change intent is required")
    a, b = _word_tokens(original), _word_tokens(proposed)
    script = myers_diff(a, b)
    edits, old_buf, new_buf = [], [], []
    def flush():
        if old_buf or new_buf:
            old_text, new_text = "".join(old_buf), "".join(new_buf)
            edits.append({"find": old_text, "replace": new_text})
            old_buf.clear(); new_buf.clear()
    for op, token in script:
        if op == "=": flush()
        elif op == "-": old_buf.append(token)
        else: new_buf.append(token)
    flush()
    lock = validate_immutable_token_lock(original, proposed, authorized_change)
    # Protected spans are source-coordinate ranges. Authorization must be an
    # exact, separately recorded span approval; a boolean supplied on the span
    # itself is not an authorization signal.
    authorized_spans = set()
    raw_authorized_spans = authorized_change.get("explicitly_authorized_spans", [])
    if not isinstance(raw_authorized_spans, list):
        raise ContractError("explicitly_authorized_spans must be an array")
    for approved_span in raw_authorized_spans:
        if not isinstance(approved_span, Mapping):
            raise ContractError("Authorized span entries must be objects")
        a_start, a_end = approved_span.get("start"), approved_span.get("end")
        if (not isinstance(a_start, int) or isinstance(a_start, bool)
                or not isinstance(a_end, int) or isinstance(a_end, bool)
                or a_start < 0 or a_end <= a_start):
            raise ContractError("Invalid explicitly authorized span")
        authorized_spans.add((a_start, a_end))
    protected_hits = []
    edit_cursor = 0
    for edit in edits:
        find_text = edit["find"]
        pos = original.find(find_text, edit_cursor) if find_text else edit_cursor
        if pos < 0:
            protected_hits.append({"edit": edit, "reason": "DIFF_SOURCE_COORDINATE_UNRESOLVED"})
            continue
        edit_start, edit_end = pos, pos + len(find_text)
        edit_cursor = edit_end
        for span in protected_spans:
            start, end = span.get("start"), span.get("end")
            if (not isinstance(start, int) or isinstance(start, bool)
                    or not isinstance(end, int) or isinstance(end, bool)
                    or start < 0 or end <= start or end > len(original)):
                raise ContractError("Invalid protected span")
            overlaps = (
                start <= edit_start < end
                if edit_start == edit_end
                else edit_start < end and edit_end > start
            )
            if overlaps and (start, end) not in authorized_spans:
                protected_hits.append({"span": dict(span), "edit": edit, "reason": "PROTECTED_SPAN_NOT_AUTHORIZED"})
    return {"status": "PASS" if lock["allowed"] and not protected_hits else "BLOCKED",
            "edits": edits, "token_lock": lock, "protected_span_conflicts": protected_hits,
            "diff_algorithm": "Myers word-token SES with whitespace/punctuation tokens",
            "authorization_intent": str(authorized_change["intent"])}

# OOXML style resolution. Values are represented as raw OOXML attributes; this is
# an effective-property snapshot, not a claim to reproduce every Word theme rule.
def _style_map(styles_root: ET._Element) -> dict[str, ET._Element]:
    return {
        x.get(qn("styleId")): x
        for x in styles_root.findall("w:style", NS)
        if x.get(qn("styleId"))
    }


def _property_map(node: ET._Element | None) -> dict[str, dict[str, str | None]]:
    result: dict[str, dict[str, str | None]] = {}
    if node is None:
        return result
    for child in node:
        local_name = ET.QName(child).localname
        if local_name in {"rStyle", "pStyle"}:
            continue
        result[local_name] = dict(child.attrib)
    return result


def _style_chain(
    styles: Mapping[str, ET._Element],
    style_id: str | None,
    *,
    expected_type: str,
    label: str,
) -> list[ET._Element]:
    if not style_id:
        return []
    chain: list[ET._Element] = []
    seen: set[str] = set()
    current: str | None = style_id
    while current:
        if current in seen:
            raise ContractError(f"Cyclic {label} basedOn style chain at {current!r}")
        seen.add(current)
        style = styles.get(current)
        if style is None:
            raise ContractError(f"Missing {label} style referenced by {current!r}")
        style_type = style.get(qn("type"))
        if style_type and style_type != expected_type:
            raise ContractError(
                f"{label} style {current!r} has type {style_type!r}, expected {expected_type!r}"
            )
        chain.append(style)
        based = style.find("w:basedOn", NS)
        current = based.get(qn("val")) if based is not None else None
    return list(reversed(chain))


def _theme_font_map(theme_xml: bytes | None) -> dict[str, dict[str, str]]:
    """Resolve the Word theme's major/minor font faces by script slot."""
    if not theme_xml:
        return {}
    root = _xml(theme_xml)
    scheme = root.find(".//a:themeElements/a:fontScheme", NS)
    if scheme is None:
        raise ContractError("Theme XML has no a:fontScheme")
    result: dict[str, dict[str, str]] = {}
    for theme_name, slot_name in (("major", "majorFont"), ("minor", "minorFont")):
        slot = scheme.find(f"a:{slot_name}", NS)
        if slot is None:
            continue
        latin = slot.find("a:latin", NS)
        east_asia = slot.find("a:ea", NS)
        complex_script = slot.find("a:cs", NS)
        latin_face = latin.get("typeface", "") if latin is not None else ""
        east_face = east_asia.get("typeface", "") if east_asia is not None else ""
        complex_face = complex_script.get("typeface", "") if complex_script is not None else ""
        result[theme_name] = {
            "ascii": latin_face,
            "hAnsi": latin_face,
            "eastAsia": east_face or latin_face,
            "cs": complex_face or latin_face,
        }
    return result


def _resolve_theme_rfonts(
    properties: dict[str, dict[str, str | None]],
    theme_fonts: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, dict[str, str | None]], dict[str, str]]:
    resolved = copy.deepcopy(properties)
    provenance: dict[str, str] = {}
    fonts = resolved.get("rFonts")
    if fonts is None:
        return resolved, provenance
    slots = {
        "asciiTheme": "ascii",
        "hAnsiTheme": "hAnsi",
        "eastAsiaTheme": "eastAsia",
        "cstheme": "cs",
        "csTheme": "cs",
    }
    for theme_attribute, face_slot in slots.items():
        theme_key = qn(theme_attribute)
        token = fonts.get(theme_key)
        if not token:
            continue
        explicit_face = fonts.get(qn(face_slot))
        if explicit_face:
            # A lower-precedence theme selector inherited from a style must
            # never overwrite a directly specified face for the same script.
            fonts.pop(theme_key, None)
            continue
        normalized = token.casefold()
        match = None
        for theme_name in ("major", "minor"):
            if normalized.startswith(theme_name):
                match = theme_name
                break
        if match is None:
            raise ContractError(f"Unknown theme font token {token!r}")
        face = theme_fonts.get(match, {}).get(face_slot, "")
        if not face:
            raise ContractError(
                f"Theme font token {token!r} has no resolved {face_slot} typeface"
            )
        fonts[qn(face_slot)] = face
        provenance[face_slot] = "theme:" + match
    return resolved, provenance


def effective_run_properties(
    document_xml: bytes,
    styles_xml: bytes | None,
    paragraph_style_id: str | None,
    run_style_id: str | None,
    direct_rpr: ET._Element | None,
    *,
    paragraph_rpr: ET._Element | None = None,
    table_style_rpr: ET._Element | None = None,
    numbering_rpr: ET._Element | None = None,
    theme_xml: bytes | None = None,
) -> dict[str, Any]:
    """Resolve Word run-format precedence, style inheritance and theme font slots.

    Table-style and numbering-level rPr nodes are supplied by their structural
    adapters after the applicable conditional table/numbering level is resolved.
    """
    layers: list[tuple[str, dict[str, dict[str, str | None]]]] = []
    styles: dict[str, ET._Element] = {}
    if styles_xml:
        root = _xml(styles_xml)
        defaults = root.find("w:docDefaults/w:rPrDefault/w:rPr", NS)
        layers.append(("docDefaults", _property_map(defaults)))
        styles = _style_map(root)
        if paragraph_style_id is None:
            for style in root.findall("w:style", NS):
                if (
                    style.get(qn("type")) == "paragraph"
                    and style.get(qn("default")) in {"1", "true", "on"}
                ):
                    paragraph_style_id = style.get(qn("styleId"))
                    break
        paragraph_chain = _style_chain(
            styles, paragraph_style_id, expected_type="paragraph", label="paragraph"
        )
        for style in paragraph_chain:
            layers.append((
                "paragraphStyle:" + str(style.get(qn("styleId"))),
                _property_map(style.find("w:rPr", NS)),
            ))
    if table_style_rpr is not None:
        layers.append(("tableStyle", _property_map(table_style_rpr)))
    if numbering_rpr is not None:
        layers.append(("numberingLevel", _property_map(numbering_rpr)))
    if paragraph_rpr is not None:
        layers.append(("paragraphDirect", _property_map(paragraph_rpr)))
    if run_style_id is None and direct_rpr is not None:
        style_ref = direct_rpr.find("w:rStyle", NS)
        run_style_id = style_ref.get(qn("val")) if style_ref is not None else None
    if run_style_id:
        character_chain = _style_chain(
            styles, run_style_id, expected_type="character", label="character"
        )
        for style in character_chain:
            layers.append((
                "characterStyle:" + str(style.get(qn("styleId"))),
                _property_map(style.find("w:rPr", NS)),
            ))
    layers.append(("directRun", _property_map(direct_rpr)))
    effective: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, values in layers:
        for key, value in values.items():
            if key == "rFonts":
                merged = dict(effective.get(key, {}))
                merged.update(value)
                effective[key] = merged
            else:
                effective[key] = value
            provenance[key] = label
    theme_fonts = _theme_font_map(theme_xml)
    effective, theme_provenance = _resolve_theme_rfonts(effective, theme_fonts)
    for slot, label in theme_provenance.items():
        provenance["rFonts." + slot] = label
    return {
        "properties": effective,
        "provenance": provenance,
        "layers": [{"name": name, "properties": values} for name, values in layers],
        "theme_font_map": theme_fonts,
    }

def build_rpr_change_snapshot(effective_before: Mapping[str, Any],
                              changed_dimensions: Sequence[str]) -> ET._Element:
    """Create a non-empty old-property snapshot for modified dimensions."""
    old = ET.Element(qn("rPr"))
    properties = effective_before.get("properties", {})
    for name in changed_dimensions:
        if name not in {"rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps",
                        "strike", "dstrike", "color", "sz", "szCs", "u",
                        "vertAlign", "highlight", "shd", "spacing", "position",
                        "kern", "outline", "shadow", "emboss", "imprint"}:
            raise ContractError("Unsupported format-change dimension: " + name)
        value = properties.get(name)
        if value is None:
            # Explicit false is essential for inherited boolean properties.
            if name in {"b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike"}:
                child = ET.SubElement(old, qn(name))
                child.set(qn("val"), "0")
                continue
            raise ContractError(
                "Cannot snapshot an unknown effective old value for format dimension "
                + name
            )
        child = ET.SubElement(old, qn(name))
        for key, item in value.items():
            child.set(key, item)
    return old

def attach_format_change(run: ET._Element, effective_before: Mapping[str, Any],
                         changed_dimensions: Sequence[str], revision_id: int,
                         author: str, date: str) -> None:
    """Attach rPrChange only to an existing ordinary run, never to a w:ins run."""
    parent = run.getparent()
    ancestor = parent
    while ancestor is not None:
        if ancestor.tag == qn("ins"):
            raise ContractError("FORMAT_CHANGE_INS_NESTING_FORBIDDEN")
        ancestor = ancestor.getparent()
    if not changed_dimensions:
        raise ContractError("FormatChange requires at least one changed dimension")
    for node in run.iterancestors():
        if node.tag in {qn("del"), qn("moveFrom"), qn("moveTo")}:
            raise ContractError("FORMAT_CHANGE_REVISION_WRAPPER_NESTING_FORBIDDEN")
    rpr = run.find("w:rPr", NS)
    if rpr is None:
        rpr = ET.Element(qn("rPr")); run.insert(0, rpr)
    if rpr.find("w:rPrChange", NS) is not None:
        raise ContractError("Run already contains rPrChange; refusing nested history")
    snapshot = build_rpr_change_snapshot(effective_before, changed_dimensions)
    change = ET.SubElement(rpr, qn("rPrChange"))
    change.set(qn("id"), str(revision_id)); change.set(qn("author"), author); change.set(qn("date"), date)
    change.append(snapshot)

def apply_micro_typography(text: str, decisions: Sequence[Mapping[str, Any]]) -> str:
    """Apply only explicit typed, source-anchored typography decisions."""
    result = text
    for decision in sorted(decisions, key=lambda x: int(x.get("start", -1)), reverse=True):
        start, end = decision.get("start"), decision.get("end")
        replacement = decision.get("replacement")
        kind = decision.get("kind")
        if not isinstance(start, int) or not isinstance(end, int) or not isinstance(replacement, str):
            raise ContractError("Micro-typography decision requires integer span and replacement")
        if start < 0 or end < start or end > len(result):
            raise ContractError("Micro-typography span outside source text")
        if kind not in {"unit_spacing", "minus_sign", "range_dash", "nbsp", "unicode_symbol"}:
            raise ContractError("Unrecognized micro-typography decision kind")
        result = result[:start] + replacement + result[end:]
    return result

# Package-wide directed graph: each DOCX is a node; definitions and mentions
# are separate records. Ambiguous matches remain unresolved, never guessed.
_REFERENCE_LABELS = {
    "figure": "figure", "fig": "figure", "fig.": "figure", "figures": "figure", "figs": "figure", "figs.": "figure",
    "table": "table", "tables": "table",
    "equation": "equation", "eq": "equation", "eq.": "equation", "equations": "equation", "eqs": "equation",
    "scheme": "scheme", "schemes": "scheme",
    "section": "section", "sections": "section",
}
_REFERENCE_NUMBER = r"(S?\d+(?:\.\d+)*(?:[A-Za-z])?)"
_CAPTION = re.compile(
    rf"(?im)^\s*(Figure|Fig\.?|Table|Equation|Eq\.?|Scheme|Section)"
    rf"\s*\(?\s*({_REFERENCE_NUMBER})\s*\)?(?=\s|[.:)]|$)"
)
_MENTION = re.compile(
    rf"\b(Figures?|Figs?\.?|Tables?|Equations?|Eqs?\.?|Schemes?|Sections?)"
    rf"\s*\(?\s*({_REFERENCE_NUMBER})\s*\)?(?=$|[\s,.;:)\]])",
    re.I,
)


def _canonical_reference_kind(value: str) -> str:
    key = value.casefold()
    return _REFERENCE_LABELS.get(key, _REFERENCE_LABELS.get(key.rstrip("."), key.rstrip(".")))


def _reference_segments(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        return [
            {"part": "text", "paragraph_index": index, "text": line}
            for index, line in enumerate(value.splitlines() or [value])
        ]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        raise ContractError("Document reference content must be text or an array of paragraph segments")
    segments = []
    seen_locations = set()
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise ContractError("Reference paragraph segments must be objects")
        part = row.get("part", "word/document.xml")
        paragraph_index = row.get("paragraph_index", index)
        text_value = row.get("text")
        if not isinstance(part, str) or not part.strip():
            raise ContractError("Reference segment part must be a non-empty string")
        if not isinstance(paragraph_index, int) or isinstance(paragraph_index, bool) or paragraph_index < 0:
            raise ContractError("Reference segment paragraph_index must be a non-negative integer")
        if not isinstance(text_value, str):
            raise ContractError("Reference segment text must be a string")
        key = (part, paragraph_index)
        if key in seen_locations:
            raise ContractError("Duplicate reference segment location: " + repr(key))
        seen_locations.add(key)
        segments.append({"part": part, "paragraph_index": paragraph_index, "text": text_value})
    return segments


def extract_docx_reference_segments(path: str | Path) -> list[dict[str, Any]]:
    """Extract text with exact Word part and paragraph anchors for graph construction."""
    import zipfile
    source = Path(path)
    if not source.is_file() or source.suffix.casefold() != ".docx":
        raise ContractError("Reference graph input must be an existing DOCX")
    allowed = re.compile(r"^word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$")
    segments = []
    with zipfile.ZipFile(source, "r") as archive:
        for name in sorted(set(archive.namelist())):
            if not allowed.match(name):
                continue
            root = _xml(archive.read(name))
            for paragraph_index, paragraph in enumerate(root.findall(".//w:p", NS)):
                value = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
                if value.strip():
                    segments.append({"part": name, "paragraph_index": paragraph_index, "text": value})
    return segments


def build_package_reference_graph(documents: Mapping[str, Any]) -> dict[str, Any]:
    """Build a package-wide graph with Word part/paragraph anchors.

    Local definitions are preferred for same-document references. Cross-document
    resolution is allowed only when exactly one package definition matches;
    multiple candidates remain ambiguous and require explicit user mapping.
    """
    if not isinstance(documents, Mapping) or not documents:
        raise ContractError("Package graph requires at least one named document")
    definitions: list[dict[str, Any]] = []
    mentions: list[dict[str, Any]] = []
    normalized_documents: dict[str, list[dict[str, Any]]] = {}
    for doc_id, raw_content in documents.items():
        if not isinstance(doc_id, str) or not doc_id.strip():
            raise ContractError("Document IDs must be non-empty strings")
        segments = _reference_segments(raw_content)
        normalized_documents[doc_id] = segments
        for segment in segments:
            text_value = segment["text"]
            base = {
                "document": doc_id,
                "part": segment["part"],
                "paragraph_index": segment["paragraph_index"],
            }
            caption_spans = []
            for match in _CAPTION.finditer(text_value):
                kind = _canonical_reference_kind(match.group(1))
                number = match.group(2)
                record_id = "def-" + hashlib.sha1(
                    (doc_id + segment["part"] + str(segment["paragraph_index"])
                     + str(match.start()) + match.group()).encode("utf-8")
                ).hexdigest()[:16]
                definitions.append({
                    "id": record_id, **base, "kind": kind, "number": number,
                    "text": match.group(), "offset": match.start(), "end_offset": match.end(),
                })
                caption_spans.append(match.span())
            for match in _MENTION.finditer(text_value):
                start_offset, end_offset = match.span()
                if any(start_offset >= left and end_offset <= right for left, right in caption_spans):
                    continue
                kind = _canonical_reference_kind(match.group(1))
                number = match.group(2)
                record_id = "men-" + hashlib.sha1(
                    (doc_id + segment["part"] + str(segment["paragraph_index"])
                     + str(start_offset) + match.group()).encode("utf-8")
                ).hexdigest()[:16]
                mentions.append({
                    "id": record_id, **base, "kind": kind, "number": number,
                    "text": match.group(), "offset": start_offset, "end_offset": end_offset,
                    "target_definition_ids": [], "resolution": "unresolved",
                })
    definitions_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for definition in definitions:
        key = (definition["kind"], definition["number"].casefold())
        definitions_by_key.setdefault(key, []).append(definition)
    for mention in mentions:
        key = (mention["kind"], mention["number"].casefold())
        candidates = definitions_by_key.get(key, [])
        local_candidates = [item for item in candidates if item["document"] == mention["document"]]
        chosen_pool = local_candidates if local_candidates else candidates
        mention["resolution_scope"] = "document" if local_candidates else "package"
        if len(chosen_pool) == 1:
            mention["target_definition_ids"] = [chosen_pool[0]["id"]]
            mention["resolution"] = "resolved"
        elif chosen_pool:
            mention["resolution"] = "ambiguous"
            mention["candidate_definition_ids"] = [item["id"] for item in chosen_pool]
        else:
            mention["resolution"] = "unresolved"
    definitions_by_id = {item["id"]: item for item in definitions}
    edges = [{
        "source_document": mention["document"],
        "source_part": mention["part"],
        "source_paragraph_index": mention["paragraph_index"],
        "mention_id": mention["id"],
        "target_definition_id": definition_id,
        "target_document": definitions_by_id[definition_id]["document"],
        "target_part": definitions_by_id[definition_id]["part"],
        "target_paragraph_index": definitions_by_id[definition_id]["paragraph_index"],
    } for mention in mentions for definition_id in mention["target_definition_ids"]]
    return {
        "schema_version": "1.1",
        "documents": sorted(normalized_documents),
        "document_segments": {
            doc_id: [{"part": item["part"], "paragraph_index": item["paragraph_index"]} for item in segments]
            for doc_id, segments in sorted(normalized_documents.items())
        },
        "definitions": definitions,
        "mentions": mentions,
        "edges": edges,
        "unresolved_count": sum(item["resolution"] != "resolved" for item in mentions),
        "ambiguous_count": sum(item["resolution"] == "ambiguous" for item in mentions),
        "numbering_mutation_authorized": False,
    }


def extract_docx_text_for_reference_graph(path: str | Path) -> str:
    """Compatibility text view; graph construction should use segment extraction."""
    return "\n".join(item["text"] for item in extract_docx_reference_segments(path))


def renumber_reference_graph(graph: Mapping[str, Any],
                             number_map: Mapping[str, str],
                             *, explicit_authorization: bool,
                             confirmed_ambiguous_mappings: Mapping[str, str] | None = None) -> dict[str, Any]:
    if explicit_authorization is not True:
        raise ContractError("Cross-file renumbering requires explicit package-wide authorization")
    if not isinstance(number_map, Mapping) or any(
        not isinstance(k, str) or "::" not in k or not isinstance(v, str) or not v.strip()
        for k, v in number_map.items()
    ):
        raise ContractError("number_map must map DOCUMENT::OLD_NUMBER keys to non-empty new numbers")
    confirmed = dict(confirmed_ambiguous_mappings or {})
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in confirmed.items()):
        raise ContractError("Confirmed ambiguous mappings must map mention IDs to definition IDs")
    updated = copy.deepcopy(dict(graph))
    by_id = {d["id"]: d for d in updated.get("definitions", [])}
    changed_definition_ids = {
        d["id"] for d in updated.get("definitions", [])
        if d["document"] + "::" + d["number"] in number_map
    }
    mentions_by_id = {m["id"]: m for m in updated.get("mentions", [])}
    unknown_mentions = set(confirmed) - set(mentions_by_id)
    if unknown_mentions:
        raise ContractError("Ambiguous mapping references unknown mention IDs: " + ", ".join(sorted(unknown_mentions)))
    for mention_id, definition_id in confirmed.items():
        mention = mentions_by_id[mention_id]
        if mention.get("resolution") != "ambiguous" or definition_id not in mention.get("candidate_definition_ids", []):
            raise ContractError("Confirmed mapping must select one candidate for an ambiguous mention: " + mention_id)
    for mention in updated.get("mentions", []):
        candidates = (
            mention.get("candidate_definition_ids", [])
            if mention.get("resolution") == "ambiguous"
            else mention.get("target_definition_ids", [])
        )
        if mention.get("resolution") == "ambiguous" and changed_definition_ids.intersection(candidates):
            chosen = confirmed.get(mention["id"])
            if chosen is None:
                raise ContractError(
                    "AMBIGUOUS_REFERENCE_MAPPING_REQUIRES_CONFIRMATION: " + mention["id"]
                )
            if chosen not in candidates:
                raise ContractError("Confirmed definition is not a candidate for mention " + mention["id"])
            mention["target_definition_ids"] = [chosen]
            mention["resolution"] = "confirmed_by_user"
    by_id = {d["id"]: d for d in updated.get("definitions", [])}
    definition_updates = []
    for definition in updated.get("definitions", []):
        old_number = definition["number"]
        key = definition["document"] + "::" + old_number
        if key in number_map:
            definition["number"] = number_map[key]
            definition["text"] = definition["text"].replace(old_number, definition["number"], 1)
            definition_updates.append({"document": definition["document"], "definition_id": definition["id"],
                                      "offset": definition["offset"], "old_number": old_number,
                                      "new_number": definition["number"]})
    mention_updates = []
    for mention in updated.get("mentions", []):
        targets = mention.get("target_definition_ids", [])
        if len(targets) == 1 and targets[0] in by_id:
            definition = by_id[targets[0]]
            old_number = mention["number"]
            if old_number != definition["number"]:
                old_text = mention["text"]
                mention["number"] = definition["number"]
                mention["text"] = old_text.replace(old_number, mention["number"], 1)
                mention_updates.append({"document": mention["document"], "mention_id": mention["id"],
                                        "offset": mention["offset"], "old_text": old_text,
                                        "new_text": mention["text"]})
    updated["definition_updates"] = definition_updates
    updated["mention_updates"] = mention_updates
    updated["numbering_mutation_authorized"] = True
    updated["confirmed_ambiguous_mappings"] = confirmed
    return updated

# Structural topology guards. The P0 text adapter must not split/merge across
# fields, OMML, bookmark/comment range boundaries or paragraph list semantics.
_PROTECTED_TOPOLOGY = {qn("bookmarkStart"), qn("bookmarkEnd"), qn("commentRangeStart"),
                       qn("commentRangeEnd"), qn("fldChar"), qn("instrText")}
def assert_safe_paragraph_split(paragraph: ET._Element, child_index: int) -> None:
    children = list(paragraph)
    if child_index < 0 or child_index > len(children):
        raise ContractError("Split index outside paragraph")
    for node in paragraph.iter():
        if node is paragraph:
            continue
        if node.tag in _PROTECTED_TOPOLOGY or node.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}"):
            raise ContractError("PARAGRAPH_SPLIT_CROSSES_PROTECTED_OOXML_TOPOLOGY")
    # Any field code with begin but no end is invalid regardless of split point.
    depth = 0
    for node in paragraph.iter(qn("fldChar")):
        kind = node.get(qn("fldCharType"))
        if kind == "begin": depth += 1
        elif kind == "end": depth -= 1
        if depth < 0: raise ContractError("Malformed complex field")
    if depth: raise ContractError("Unclosed complex field")
def assert_safe_paragraph_merge(left: ET._Element, right: ET._Element) -> dict[str, Any]:
    for paragraph in (left, right):
        if any(node.tag in _PROTECTED_TOPOLOGY or node.tag.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/math}")
               for node in paragraph.iter()):
            raise ContractError("PARAGRAPH_MERGE_CROSSES_PROTECTED_OOXML_TOPOLOGY")
    def ppr(p):
        return p.find("w:pPr", NS)
    lp, rp = ppr(left), ppr(right)
    def value(node, path):
        found = node.find(path, NS) if node is not None else None
        return found.get(qn("val")) if found is not None else None
    left_num = (value(lp, "w:numPr/w:numId"), value(lp, "w:numPr/w:ilvl"))
    right_num = (value(rp, "w:numPr/w:numId"), value(rp, "w:numPr/w:ilvl"))
    if left_num != right_num and (left_num != (None, None) or right_num != (None, None)):
        raise ContractError("PARAGRAPH_MERGE_LIST_SEMANTICS_MISMATCH")
    if (lp is None) != (rp is None):
        raise ContractError("PARAGRAPH_MERGE_PARAGRAPH_PROPERTIES_MISMATCH")
    if lp is not None and rp is not None:
        left_bytes = ET.tostring(lp, method="c14n")
        right_bytes = ET.tostring(rp, method="c14n")
        if left_bytes != right_bytes:
            raise ContractError("PARAGRAPH_MERGE_PARAGRAPH_PROPERTIES_MISMATCH")
    return {"pPr_source": "left", "numPr": left_num, "right_pPr_discarded": True}

def build_revision_baseline_inventory(source: str | Path | bytes) -> dict[str, Any]:
    """Zero-model-call inventory of pre-existing native revisions and comments."""
    from .structure_classifier import classify_docx_structure
    report = classify_docx_structure(source)
    revision_kinds = {
        "text_revision_wrapper", "format_revision", "paragraph_revision",
        "paragraph_mark_deletion", "table_structure_revision", "move_range_marker",
        "comment_anchor", "comment_definition", "comment_range", "comment_range_marker",
        "comment_reference", "incomplete_comment_range",
    }
    records = [dict(item) for item in report.get("structures", [])
               if item.get("kind") in revision_kinds or "revision" in str(item.get("kind", ""))]
    return {"schema_version": "1.0", "source_sha256": report.get("source_sha256"),
            "package_status": report.get("package_status"), "existing_revision_count": len(records),
            "revisions": records, "inventory": report}

def targeted_revision_gate(inventory: Mapping[str, Any],
                           targets: Sequence[Mapping[str, Any]],
                           *, resolution_policy: str | None = None,
                           selectively_resolved_ids: Sequence[str] = ()) -> dict[str, Any]:
    """Classify history conflicts; this gate never accepts or rejects Word revisions."""
    if resolution_policy not in {None, "ACCEPT_EXISTING", "REJECT_EXISTING", "SELECTIVE_ACCEPT_CONFLICTS"}:
        raise ContractError("Unknown historical revision resolution policy")
    overlaps, unrelated = [], []
    target_keys = {(str(t.get("part", "word/document.xml")), t.get("paragraph_index", t.get("p_idx")))
                   for t in targets}
    selected = set(selectively_resolved_ids)
    for revision in inventory.get("revisions", []):
        key = (str(revision.get("part")), revision.get("paragraph_index"))
        span = revision.get("paragraph_indices") or []
        hit = key in target_keys or any((str(revision.get("part")), idx) in target_keys for idx in span)
        (overlaps if hit else unrelated).append(revision)
    unresolved = []
    for item in overlaps:
        rid = str(item.get("id", item.get("element_path", "")))
        if resolution_policy == "SELECTIVE_ACCEPT_CONFLICTS" and rid in selected:
            continue
        if resolution_policy in {"ACCEPT_EXISTING", "REJECT_EXISTING"}:
            continue
        unresolved.append(item)
    if not overlaps:
        status = "PASS"
    elif unresolved:
        status = "USER_DECISION_REQUIRED"
    else:
        # A selected policy is not an applied policy. Until a deterministic
        # Accept/Reject adapter exists, callers must resolve in Word and re-import.
        status = "RESOLUTION_ADAPTER_REQUIRED"
    return {
        "status": status,
        "resolution_policy": resolution_policy,
        "overlapping_revisions": overlaps,
        "unrelated_revisions_preserved": unrelated,
        "unresolved_conflicts": unresolved,
        "must_resolve_before_discovery_or_write": bool(overlaps),
        "resolution_applied": False,
    }

def extract_docx_text_for_reference_graph(path: str | Path) -> str:
    """Extract paragraph text from managed Word content parts without model calls."""
    import zipfile
    from pathlib import PurePosixPath
    source = Path(path)
    if not source.is_file() or source.suffix.casefold() != ".docx":
        raise ContractError("Reference graph input must be an existing DOCX")
    rows = []
    allowed = re.compile(r"^word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml$")
    with zipfile.ZipFile(source, "r") as archive:
        for name in sorted(archive.namelist()):
            if not allowed.match(name):
                continue
            root = _xml(archive.read(name))
            for paragraph in root.findall(".//w:p", NS):
                value = "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))
                if value.strip():
                    rows.append(value)
    return "\n".join(rows)

# Two-phase package transaction. Files are prepared in a private staging folder,
# validated, then published as an immutable version directory. The manifest is
# the commit marker; readers must ignore directories without it.
def commit_document_package(outputs: Mapping[str, bytes], release_root: str | Path,
                            *, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not outputs or any(not isinstance(k, str) or not isinstance(v, bytes) for k, v in outputs.items()):
        raise ContractError("Package transaction requires named DOCX byte outputs")
    root = Path(release_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    package_id = uuid.uuid4().hex
    staging = root / (".staging-" + package_id)
    version = root / ("package-" + package_id)
    staging.mkdir()
    manifest = {"schema_version": "1.0", "package_id": package_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "documents": [], "metadata": dict(metadata or {})}
    seen_paths: set[str] = set()
    try:
        for name, payload in outputs.items():
            safe = PurePosixPath(name)
            windows_safe = PureWindowsPath(name)
            if (not name or safe.is_absolute() or windows_safe.is_absolute()
                    or windows_safe.drive or "\\" in name or ".." in safe.parts
                    or safe.suffix.casefold() != ".docx" or not safe.parts):
                raise ContractError("Unsafe package output path: " + name)
            normalized_name = safe.as_posix()
            collision_key = normalized_name.casefold()
            if collision_key in seen_paths:
                raise ContractError("Duplicate or case-colliding package output path: " + name)
            seen_paths.add(collision_key)
            with zipfile.ZipFile(__import__("io").BytesIO(payload)) as z:
                member_names = z.namelist()
                if len(member_names) != len(set(member_names)):
                    raise ContractError("DOCX package contains duplicate ZIP member names: " + name)
                bad = z.testzip()
                if bad:
                    raise ContractError("Invalid DOCX ZIP member: " + bad)
                for member in member_names:
                    if member.endswith("/"):
                        continue
                    member_path = PurePosixPath(member)
                    if member_path.is_absolute() or ".." in member_path.parts or "\\" in member:
                        raise ContractError("Unsafe DOCX package member path: " + member)
                    if member.endswith((".xml", ".rels")):
                        _xml(z.read(member))
                if "[Content_Types].xml" not in member_names or "word/document.xml" not in member_names:
                    raise ContractError("DOCX package is missing required OPC parts: " + name)
                members = {member: z.read(member) for member in member_names if not member.endswith("/")}
            from .structure_classifier import classify_docx_package_members
            package_report = classify_docx_package_members(members, source_name=name)
            if package_report.get("package_status") != "READY":
                raise ContractError("DOCX package structure validation failed: " + name)
            target = staging / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            manifest["documents"].append({"path": normalized_name, "sha256": _sha(payload), "size": len(payload)})
        manifest_path = staging / "package_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Directory rename publishes the immutable candidate; the external
        # commit marker is written last and is the only release visibility signal.
        os.replace(staging, version)
        marker_tmp = root / (".commit-" + package_id + ".tmp")
        marker = root / ("COMMITTED-" + package_id + ".json")
        marker_tmp.write_text(json.dumps({"package_id": package_id, "manifest_sha256": _sha((version / "package_manifest.json").read_bytes())}, indent=2), encoding="utf-8")
        os.replace(marker_tmp, marker)
        return {"status": "COMMITTED", "package_id": package_id,
                "directory": str(version), "commit_marker": str(marker),
                "manifest": manifest}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise

# Bounded remediation is a formal state transition; patches must be applied to
# Marked and Clean is recompiled from Marked before the second QA pass.
def remediation_transition(state: Mapping[str, Any], patch: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw_round = state.get("remediation_round", 0)
    if not isinstance(raw_round, int) or isinstance(raw_round, bool) or raw_round < 0:
        raise ContractError("remediation_round must be a non-negative integer")
    round_no = raw_round
    phase = state.get("phase", "QA")
    if phase != "QA":
        raise ContractError("Remediation transition is legal only from QA")
    if round_no >= 1:
        return {"phase": "BLOCKED", "remediation_round": 1,
                "reason": "SECOND_QA_FAILURE_NO_FURTHER_REMEDIATION"}
    if not isinstance(patch, Mapping) or patch.get("target_artifact") != "Marked":
        raise ContractError("Remediation patch must target Marked as a formal revision")
    for field in ("operation_id", "find", "reason"):
        if not isinstance(patch.get(field), str) or not patch.get(field, "").strip():
            raise ContractError(f"Remediation patch {field} must be a non-empty string")
    if not isinstance(patch.get("replace"), str):
        raise ContractError("Remediation patch replace must be a string; empty string is valid for deletion")
    return {"phase": "PATCH_COMMIT", "next_phase": "COMPILE_CLEAN",
            "remediation_round": 1, "patch": dict(patch),
            "clean_must_be_recompiled": True}

def collect_revision_ids_from_lineage(records: Sequence[Mapping[str, Any]],
                                      marked_file: str | Path,
                                      marked_sha256: str) -> dict[str, Any]:
    """Collect current and inherited revision IDs through exact input/output hash links."""
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise ContractError("Revision lineage records must be an array")
    if not isinstance(marked_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", marked_sha256):
        raise ContractError("Marked SHA-256 must be a lowercase 64-character digest")
    current_path = Path(marked_file).expanduser().resolve()
    current_sha = marked_sha256
    seen: set[tuple[str, str]] = set()
    all_ids: set[str] = set()
    lineage = []
    while True:
        key = (str(current_path), current_sha)
        if key in seen:
            raise ContractError("Cycle detected in Marked revision lineage")
        seen.add(key)
        matches = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ContractError("Revision lineage entries must be objects")
            output_file = record.get("output_file")
            output_sha = record.get("output_sha256")
            if not isinstance(output_file, str) or not output_file.strip():
                raise ContractError("Revision lineage output_file is required")
            if not isinstance(output_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", output_sha):
                raise ContractError("Revision lineage output_sha256 is invalid")
            if Path(output_file).expanduser().resolve() == current_path and output_sha == current_sha:
                matches.append(record)
        if not matches:
            break
        input_pairs = set()
        for record in matches:
            input_file = record.get("input_file")
            input_sha = record.get("input_sha256")
            if not isinstance(input_file, str) or not input_file.strip():
                raise ContractError("Revision lineage input_file is required")
            if not isinstance(input_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", input_sha):
                raise ContractError("Revision lineage input_sha256 is invalid")
            input_pairs.add((str(Path(input_file).expanduser().resolve()), input_sha))
            revision_ids = record.get("revision_ids")
            if (not isinstance(revision_ids, list)
                    or any(not isinstance(value, str) or not value.strip() for value in revision_ids)):
                raise ContractError("Revision lineage revision_ids must be a string array")
            all_ids.update(revision_ids)
        if len(input_pairs) != 1:
            raise ContractError("Conflicting apply records share the same Marked output hash")
        input_path_text, input_sha = next(iter(input_pairs))
        lineage.append({
            "output_file": str(current_path), "output_sha256": current_sha,
            "revision_ids": sorted({value for record in matches for value in record.get("revision_ids", [])}),
            "input_file": input_path_text, "input_sha256": input_sha,
        })
        current_path, current_sha = Path(input_path_text), input_sha
    return {"revision_ids": sorted(all_ids), "lineage": lineage}

def compile_clean_from_marked(marked_bytes: bytes, *,
                              expected_revision_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Deterministically compile only revision IDs authorized for this Marked build.

    Historical markup cannot be silently accepted: every revision wrapper must
    carry an ID declared by the current revision ledger. A prior baseline gate
    must have resolved all historical revisions before the current Marked build.
    """
    if not isinstance(marked_bytes, bytes) or not marked_bytes:
        raise ContractError("Marked source must be non-empty DOCX bytes")
    allowed = set(str(x) for x in (expected_revision_ids or ()))
    revision_tags = {qn("ins"), qn("del"), qn("moveTo"), qn("moveFrom"),
                     qn("rPrChange"), qn("pPrChange"), qn("tblPrChange"),
                     qn("tblGridChange"), qn("trPrChange"), qn("tcPrChange"),
                     qn("numberingChange"), qn("sectPrChange")}
    observed = set()
    with zipfile.ZipFile(__import__("io").BytesIO(marked_bytes)) as archive:
        bad = archive.testzip()
        if bad: raise ContractError("Marked DOCX ZIP CRC failure: " + bad)
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                root = _xml(archive.read(name))
                for node in root.iter():
                    if node.tag in revision_tags:
                        rid = node.get(qn("id"))
                        if rid is None:
                            raise ContractError("Historical/current revision element without w:id: " + name)
                        observed.add(str(rid))
    unexpected = sorted(observed - allowed)
    if unexpected:
        raise ContractError("HISTORICAL_REVISION_POLICY_BLOCKED: unregistered revision ID(s): " + ", ".join(unexpected))
    from .working_view import accepted_view_docx_bytes
    clean = accepted_view_docx_bytes(marked_bytes)
    return {"marked_sha256": _sha(marked_bytes), "clean_sha256": _sha(clean),
            "clean_bytes": clean, "policy": "accept_registered_insertions_reject_registered_deletions",
            "expected_revision_ids": sorted(allowed), "observed_revision_ids": sorted(observed),
            "historical_revision_policy": "PASS_NO_UNREGISTERED_REVISION_IDS"}

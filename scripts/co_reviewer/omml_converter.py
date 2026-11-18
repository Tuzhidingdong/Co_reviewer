"""Deterministic LaTeX-subset to OMML conversion.

This module creates preview equations only. It does not modify an existing DOCX
or create tracked equation revisions. Formula editing remains disabled until a
separate Word-version save/reopen acceptance validates the revision wrapper.
"""
from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Iterable

from lxml import etree as ET

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
NS = {"m": M}


class OmmlConversionError(ValueError):
    """The expression is outside the explicitly supported deterministic subset."""


_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ϵ", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "varphi": "ϕ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_SYMBOLS = {
    "pm": "±", "mp": "∓", "times": "×", "cdot": "·",
    "ast": "∗", "div": "÷", "le": "≤", "leq": "≤",
    "ge": "≥", "geq": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "propto": "∝",
    "infty": "∞", "partial": "∂", "nabla": "∇",
    "to": "→", "rightarrow": "→", "leftarrow": "←",
    "leftrightarrow": "↔", "Rightarrow": "⇒",
    "forall": "∀", "exists": "∃", "in": "∈",
    "notin": "∉", "element": "∈", "degree": "°",
    "ell": "ℓ", "hbar": "ℏ",
}
_ACCENTS = {"hat": "̂", "bar": "¯", "overline": "¯", "tilde": "̃"}


def _tag(local: str) -> str:
    return f"{{{M}}}{local}"


def _node(local: str, children: Iterable[ET._Element] = ()) -> ET._Element:
    result = ET.Element(_tag(local))
    for child in children:
        result.append(child)
    return result


def _run(text: str, style: str | None = None) -> ET._Element:
    run = _node("r")
    if style is not None:
        properties = _node("rPr")
        ET.SubElement(properties, _tag("sty"), {_tag("val"): style})
        run.append(properties)
    text_node = ET.SubElement(run, _tag("t"))
    text_node.text = text
    if text[:1].isspace() or text[-1:].isspace():
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return run


def _container(local: str, slot: str, children: Iterable[ET._Element]) -> ET._Element:
    result = _node(local)
    result.append(_node(slot, children))
    return result


def _apply_style(nodes: Iterable[ET._Element], style: str) -> None:
    for root in nodes:
        for run in root.iter(_tag("r")):
            properties = run.find("m:rPr", NS)
            if properties is None:
                properties = _node("rPr")
                run.insert(0, properties)
            for old in list(properties.findall("m:sty", NS)):
                properties.remove(old)
            ET.SubElement(properties, _tag("sty"), {_tag("val"): style})


class _Parser:
    def __init__(self, expression: str):
        if not isinstance(expression, str) or not expression.strip():
            raise OmmlConversionError("Expression must be a non-empty string")
        self.expression = expression
        self.position = 0

    def parse(self) -> ET._Element:
        children = self._sequence()
        if self.position != len(self.expression):
            raise OmmlConversionError(f"Unexpected trailing input at character {self.position}")
        if not children:
            raise OmmlConversionError("Expression contains no mathematical content")
        return ET.Element(_tag("oMath"), nsmap={"m": M}) if False else self._root(children)

    def _root(self, children: list[ET._Element]) -> ET._Element:
        root = ET.Element(_tag("oMath"), nsmap={"m": M})
        for child in children:
            root.append(child)
        return root

    def _sequence(self, closing: str | None = None) -> list[ET._Element]:
        output: list[ET._Element] = []
        while self.position < len(self.expression):
            char = self.expression[self.position]
            if char.isspace():
                self.position += 1
                continue
            if closing and char == closing:
                return output
            if char == "}":
                raise OmmlConversionError(f"Unmatched closing brace at character {self.position}")
            if char in "^_":
                if not output:
                    raise OmmlConversionError(f"Script operator {char!r} has no base")
                base = output.pop()
                self.position += 1
                argument = self._argument()
                if not argument:
                    raise OmmlConversionError("A script argument cannot be empty")
                output.append(self._script(base, char, argument))
                continue
            output.extend(self._atom())
        if closing:
            raise OmmlConversionError(f"Missing closing {closing!r}")
        return output

    def _group(self) -> list[ET._Element]:
        if self.position >= len(self.expression) or self.expression[self.position] != "{":
            raise OmmlConversionError(f"Expected '{{' at character {self.position}")
        self.position += 1
        value = self._sequence("}")
        if self.position >= len(self.expression) or self.expression[self.position] != "}":
            raise OmmlConversionError("Missing closing brace")
        self.position += 1
        return value

    def _argument(self) -> list[ET._Element]:
        if self.position >= len(self.expression):
            raise OmmlConversionError("Missing script/function argument")
        if self.expression[self.position] == "{":
            return self._group()
        if self.expression[self.position] in "^_}":
            raise OmmlConversionError("Missing script/function argument")
        return self._atom()

    def _script(self, base: ET._Element, operator: str,
                argument: list[ET._Element]) -> ET._Element:
        local = ET.QName(base).localname
        if local in {"sSub", "sSup", "sSubSup"}:
            existing_sub = base.find("m:sub", NS)
            existing_sup = base.find("m:sup", NS)
            if operator == "_" and existing_sub is not None:
                raise OmmlConversionError("Duplicate subscript on one base")
            if operator == "^" and existing_sup is not None:
                raise OmmlConversionError("Duplicate superscript on one base")
            if local == "sSubSup" or ((operator == "^" and existing_sub is not None)
                                      or (operator == "_" and existing_sup is not None)):
                result = _node("sSubSup")
                for slot in ("e", "sub", "sup"):
                    source = base.find(f"m:{slot}", NS)
                    if source is not None:
                        result.append(copy.deepcopy(source))
                if operator == "_":
                    result.append(_node("sub", argument))
                else:
                    result.append(_node("sup", argument))
                return result
            result = copy.deepcopy(base)
            result.append(_node("sup" if operator == "^" else "sub", argument))
            return result
        result = _node("sSup" if operator == "^" else "sSub")
        result.append(_node("e", [base]))
        result.append(_node("sup" if operator == "^" else "sub", argument))
        return result

    def _atom(self) -> list[ET._Element]:
        if self.position >= len(self.expression):
            return []
        char = self.expression[self.position]
        if char == "{":
            return self._group()
        if char == "\\":
            return self._command()
        if char.isdigit():
            start = self.position
            self.position += 1
            while self.position < len(self.expression):
                nxt = self.expression[self.position]
                if nxt.isdigit() or nxt in ".,":
                    self.position += 1
                else:
                    break
            return [_run(self.expression[start:self.position])]
        if char.isalpha():
            self.position += 1
            return [_run(char)]
        if char in "+-=<>()[].,;:/|":
            self.position += 1
            return [_run(char)]
        if char in "'\"":
            self.position += 1
            return [_run(char)]
        raise OmmlConversionError(
            f"Unsupported character {char!r} at character {self.position}"
        )

    def _command(self) -> list[ET._Element]:
        self.position += 1
        if self.position >= len(self.expression):
            raise OmmlConversionError("Trailing backslash")
        if not self.expression[self.position].isalpha():
            escaped = self.expression[self.position]
            self.position += 1
            if escaped in "{}_%#$&":
                return [_run(escaped)]
            if escaped == ",":
                return [_run(" ")]
            raise OmmlConversionError(f"Unsupported escaped character: \\{escaped}")

        start = self.position
        while self.position < len(self.expression) and self.expression[self.position].isalpha():
            self.position += 1
        name = self.expression[start:self.position]

        if name == "frac":
            numerator = self._group()
            denominator = self._group()
            fraction = _node("f")
            fraction.append(_node("fPr", [_node("type")]))
            fraction.find("m:fPr", NS)[0].set(_tag("val"), "bar")
            fraction.append(_node("num", numerator))
            fraction.append(_node("den", denominator))
            return [fraction]
        if name == "sqrt":
            degree: list[ET._Element] = []
            if self.position < len(self.expression) and self.expression[self.position] == "[":
                self.position += 1
                start = self.position
                depth = 0
                while self.position < len(self.expression):
                    if self.expression[self.position] == "]" and depth == 0:
                        break
                    if self.expression[self.position] == "{":
                        depth += 1
                    elif self.expression[self.position] == "}":
                        depth -= 1
                    self.position += 1
                if self.position >= len(self.expression):
                    raise OmmlConversionError("Unclosed square-root degree")
                degree_text = self.expression[start:self.position]
                self.position += 1
                degree = _Parser(degree_text)._sequence()
            radicand = self._group()
            radical = _node("rad")
            properties = _node("radPr")
            if not degree:
                hide = ET.SubElement(properties, _tag("degHide"), {_tag("val"): "1"})
                del hide
            radical.append(properties)
            radical.append(_node("deg", degree))
            radical.append(_node("e", radicand))
            return [radical]
        if name in _ACCENTS:
            expression = self._group()
            accent = _node("acc")
            properties = _node("accPr")
            ET.SubElement(properties, _tag("chr"), {_tag("val"): _ACCENTS[name]})
            accent.append(properties)
            accent.append(_node("e", expression))
            return [accent]
        if name in {"mathrm", "text", "mathbf", "mathit", "boldsymbol"}:
            contents = self._group()
            style = {"mathrm": "p", "text": "p", "mathbf": "b",
                     "mathit": "i", "boldsymbol": "bi"}[name]
            _apply_style(contents, style)
            return contents
        if name in _GREEK:
            return [_run(_GREEK[name])]
        if name in _SYMBOLS:
            return [_run(_SYMBOLS[name])]
        if name in {"quad", "qquad", "enspace", "thinspace"}:
            return [_run({"quad": "  ", "qquad": "    ",
                          "enspace": " ", "thinspace": " "}[name])]
        if name in {"limits", "nolimits"}:
            raise OmmlConversionError(f"Command \\{name} requires a n-ary operator adapter")
        raise OmmlConversionError(f"Unsupported LaTeX command: \\{name}")


def convert_latex_to_omml(expression: str) -> bytes:
    """Convert a documented, deterministic LaTeX subset to standalone OMML XML."""
    root = _Parser(expression).parse()
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)


def create_omml_preview_docx(expression: str, output_path: str | Path,
                             *, title: str = "Co_Reviewer OMML preview") -> dict[str, str]:
    """Create a synthetic preview DOCX. This is not a revision of a user document."""
    from docx import Document

    output = Path(output_path).expanduser().resolve()
    if output.suffix.casefold() != ".docx":
        raise OmmlConversionError("OMML preview output must have a .docx extension")
    if output.exists():
        raise OmmlConversionError("OMML preview refuses to overwrite an existing file")
    xml_bytes = convert_latex_to_omml(expression)
    root = ET.fromstring(xml_bytes, parser=ET.XMLParser(resolve_entities=False, no_network=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading(title, level=1)
    document.add_paragraph("Synthetic OMML rendering preview. No manuscript was edited and no Track Changes are present.")
    paragraph = document.add_paragraph()
    paragraph._p.append(root)
    document.add_paragraph("This preview does not certify equation revision/Track Changes compatibility.")
    # Save to a sibling temporary file and publish only when the DOCX package is complete.
    fd, temporary_name = tempfile.mkstemp(prefix=".omml-preview-", suffix=".docx", dir=str(output.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        document.save(temporary)
        with temporary.open("rb") as stream:
            payload = stream.read()
        import zipfile
        with zipfile.ZipFile(temporary, "r") as archive:
            bad = archive.testzip()
            if bad:
                raise OmmlConversionError("OMML preview DOCX failed ZIP CRC check: " + bad)
            document_xml = ET.fromstring(archive.read("word/document.xml"))
            equations = document_xml.findall(".//m:oMath", NS)
            if not equations:
                raise OmmlConversionError("OMML preview equation is missing from DOCX")
        # Re-check destination immediately before publishing; never overwrite.
        if output.exists():
            raise OmmlConversionError("OMML preview output appeared during generation; refusing overwrite")
        os.replace(temporary, output)
        import hashlib
        return {
            "output_path": str(output),
            "output_sha256": hashlib.sha256(payload).hexdigest(),
            "expression": expression,
            "equation_count": str(len(equations)),
        }
    finally:
        temporary.unlink(missing_ok=True)

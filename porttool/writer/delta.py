"""The delta ARXML: one standalone file with every changed element, complete.

For import with DVImEx, or to hand to the owner of a model-owned component. Each
touched component type, composition, interface, data type (and compu method, data
constraint, mapping set) appears as the whole element after the change: the existing
text of the element, UUIDs included, plus the additions. Packages are rebuilt by name.

PENDING-DISCOVERY: what DVImEx needs in such a file (package attributes, ADMIN-DATA,
one element per file) is not known. The existing text of an element is only moved to
the indentation of the delta file; line endings follow the first source file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from porttool.core.results import Finding, Severity
from porttool.writer import codes
from porttool.writer.session import Session
from porttool.writer.xmltext import Document, Node, escape_text

_DEFAULT_ROOT = '<AUTOSAR xmlns="http://autosar.org/schema/r4.0">'


@dataclass(slots=True)
class _Package:
    elements: list[tuple[Document, Node]] = field(default_factory=list)
    children: dict[str, _Package] = field(default_factory=dict)


def _package_names(document: Document, top: Node) -> list[str] | None:
    """Names of the packages around ``top``, outermost first; None if it is not in a package."""
    names: list[str] = []
    node = top.parent
    while node is not None and node is not document.root:
        if node.tag == "AR-PACKAGE":
            name = document.name_of(node)
            if name is None:
                return None
            names.append(name)
        node = node.parent
    return names[::-1] or None


def _namespace(document: Document) -> str | None:
    prefix = document.prefix.rstrip(":")
    return document.attribute(document.root, f"xmlns:{prefix}" if prefix else "xmlns")


def _moved(document: Document, top: Node, indent: str, eol: str) -> str:
    """Text of the element after the change, moved to ``indent``, with ``eol`` line ends."""
    text = document.render_element(top).replace("\r\n", "\n")
    lines = text.split("\n")
    old = "" if top.virtual else document._line_indent(top.start)
    out = [indent + lines[0]]
    for line in lines[1:]:
        if line.startswith(old):
            out.append(indent + line[len(old) :])
        else:
            out.append(line)  # a line that is not indented like the element: left alone
    return eol.join(out) + eol


def render(session: Session) -> tuple[bytes, int, list[Finding]]:
    """(content of the delta file, number of elements, findings). No elements: empty bytes."""
    findings: list[Finding] = []
    tree = _Package()
    count = 0
    first: Document | None = None
    documents: list[Document] = []
    seen: set[int] = set()

    def add(document: Document, top: Node) -> None:
        nonlocal count, first
        if id(top) in seen:
            return
        seen.add(id(top))
        names = _package_names(document, top)
        if names is None:
            return
        package = tree
        for name in names:
            package = package.children.setdefault(name, _Package())
        package.elements.append((document, top))
        count += 1
        if document not in documents:
            documents.append(document)
        if first is None and not document.created:
            first = document

    for rel_path, tops in session.touched.items():
        document = session.docs[rel_path]
        for top in tops:
            add(document, top)
            if top.virtual:
                continue
            names = _package_names(document, top) or []
            path = "/" + "/".join([*names, document.name_of(top) or ""])
            parts = session.known_parts(path)
            if len(parts) > 1:
                for other_document, other_top in parts:
                    add(other_document, other_top)
                findings.append(
                    Finding(
                        codes.DELTA_SPLIT,
                        Severity.WARNING,
                        f"{path} is defined in {len(parts)} files "
                        f"({', '.join(d.rel_path for d, _ in parts)}): the delta holds one "
                        "element per file, not one merged element.",
                        path,
                    )
                )
    if count == 0:
        return b"", 0, findings

    style = first or documents[0]
    eol, unit, prefix = style.eol, style.unit, style.prefix
    root_tag = style.start_tag(style.root) if not style.created else _DEFAULT_ROOT
    if style.root.end_start < 0:  # <AUTOSAR .../>
        root_tag = root_tag[:-2].rstrip() + ">"
    uri = _namespace(style)
    for document in documents:  # elements written with another prefix need its declaration
        if document.prefix != prefix and uri is not None:
            other = document.prefix.rstrip(":")
            attribute = f"xmlns:{other}" if other else "xmlns"
            if style.attribute(style.root, attribute) is None:
                root_tag = f'{root_tag[:-1]} {attribute}="{uri}">'
    out = ['<?xml version="1.0" encoding="UTF-8"?>' + eol, root_tag + eol]

    def write(package: _Package, depth: int) -> None:
        pad = unit * depth
        out.append(f"{pad}<{prefix}AR-PACKAGES>{eol}")
        for name, child in package.children.items():
            out.append(f"{pad}{unit}<{prefix}AR-PACKAGE>{eol}")
            inner = pad + unit * 2
            out.append(f"{inner}<{prefix}SHORT-NAME>{escape_text(name)}</{prefix}SHORT-NAME>{eol}")
            if child.elements:
                out.append(f"{inner}<{prefix}ELEMENTS>{eol}")
                for document, top in child.elements:
                    out.append(_moved(document, top, inner + unit, eol))
                out.append(f"{inner}</{prefix}ELEMENTS>{eol}")
            if child.children:
                write(child, depth + 2)
            out.append(f"{pad}{unit}</{prefix}AR-PACKAGE>{eol}")
        out.append(f"{pad}</{prefix}AR-PACKAGES>{eol}")

    write(tree, 1)
    out.append(f"</{style.root.raw if not style.created else 'AUTOSAR'}>{eol}")
    return "".join(out).encode("utf-8"), count, findings

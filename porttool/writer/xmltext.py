"""Offset index of an XML text and insertion of new elements as text.

The writer never parses a file into a tree and serialises the tree again: that would
change quoting, entities, white space and the XML declaration. Instead this module

1. decodes the bytes (BOM and declared encoding are kept for writing back),
2. builds a small index of element offsets with a tolerant tokenizer,
3. lets the caller hang new ("virtual") elements into that index, and
4. renders the result as the original text plus the text of the new elements.

Every byte outside the inserted text stays as it was. The one exception is an empty
element written as ``<X/>`` that receives children: its ``/`` is removed.
"""

from __future__ import annotations

import codecs
import re
from collections.abc import Iterable, Iterator, Sequence

AUTOSAR_ROOT = "AUTOSAR"
SHORT_NAME = "SHORT-NAME"

_UNSET = "\x00unset"


class XmlIndexError(Exception):
    """The text is not XML this module can index (bad nesting, unknown encoding)."""


class Node:
    """One element. ``start`` is -1 for a new (virtual) element that is not in the text yet."""

    __slots__ = (
        "raw",
        "tag",
        "start",
        "start_end",
        "end_start",
        "end",
        "children",
        "parent",
        "text",
        "attrs",
        "_short",
    )

    def __init__(self, raw: str, start: int, start_end: int, parent: Node | None) -> None:
        self.raw = raw  # name as written, with namespace prefix
        self.tag = raw.rpartition(":")[2] if ":" in raw else raw  # local name
        self.start = start  # offset of "<" of the start tag
        self.start_end = start_end  # offset after ">" of the start tag
        self.end_start = -1  # offset of "<" of the end tag; -1: self-closing or virtual
        self.end = start_end  # offset after ">" of the end tag
        self.children: list[Node] = []
        self.parent = parent
        self.text: str | None = None  # virtual leaf: text content
        self.attrs: tuple[tuple[str, str], ...] = ()  # virtual: attributes
        self._short: str | None = _UNSET

    @property
    def virtual(self) -> bool:
        return self.start < 0

    def child(self, tag: str) -> Node | None:
        """First child with this local name."""
        for child in self.children:
            if child.tag == tag:
                return child
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Node {self.raw} at {self.start}>"


def element(
    tag: str,
    *children: Node | None,
    text: str | None = None,
    attrs: Sequence[tuple[str, str]] = (),
) -> Node:
    """Build a new element. ``None`` children are dropped, which keeps templates short."""
    node = Node(tag, -1, -1, None)
    node.text = text
    node.attrs = tuple(attrs)
    for child in children:
        if child is not None:
            child.parent = node
            node.children.append(child)
    return node


def named(tag: str, name: str, *children: Node | None, uuid: str | None = None) -> Node:
    """A new identifiable element: optional UUID attribute, SHORT-NAME first."""
    attrs = (("UUID", uuid),) if uuid else ()
    return element(tag, element(SHORT_NAME, text=name), *children, attrs=attrs)


def escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(text: str) -> str:
    return escape_text(text).replace('"', "&quot;")


# --------------------------------------------------------------------------- decoding

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
_DECLARED = re.compile(rb"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._\-]+)["']""")


def detect_encoding(data: bytes) -> tuple[str, bytes]:
    """Return (Python codec name, BOM bytes) of an XML file."""
    for bom, codec in _BOMS:
        if data.startswith(bom):
            return codec, bom
    if data.startswith(b"<\x00"):
        return "utf-16-le", b""
    if data.startswith(b"\x00<"):
        return "utf-16-be", b""
    match = _DECLARED.match(data[:200].lstrip())
    if match is None:
        return "utf-8", b""
    declared = match.group(1).decode("ascii")
    try:
        name = codecs.lookup(declared).name
    except LookupError as error:
        raise XmlIndexError(f"unknown encoding {declared!r} in the XML declaration") from error
    if name in ("utf-16", "utf-32"):
        raise XmlIndexError(f"{declared} without a byte order mark")
    return name, b""


def decode(data: bytes) -> tuple[str, str, bytes]:
    """Return (text, codec, BOM). ``encode(text, codec, bom)`` gives ``data`` back exactly."""
    codec, bom = detect_encoding(data)
    try:
        text = data[len(bom) :].decode(codec)
    except UnicodeDecodeError as error:
        raise XmlIndexError(f"the file is not valid {codec}: {error.reason}") from error
    return text, codec, bom


def encode(text: str, codec: str, bom: bytes) -> bytes:
    """Encode; characters the file's encoding lacks become character references."""
    return bom + text.encode(codec, errors="xmlcharrefreplace")


# --------------------------------------------------------------------------- tokenizer

# Comments, CDATA sections, processing instructions (also the XML declaration) and a
# DOCTYPE are matched so that nothing inside them is taken for a tag. Group 1: name of
# an end tag. Group 2: name of a start tag, group 3 its attributes: quoted values may
# contain ">". Possessive quantifiers: no backtracking on a broken file.
_TOKEN = re.compile(
    r"<(?:"
    r"!--.*?-->"
    r"|!\[CDATA\[.*?\]\]>"
    r"|\?.*?\?>"
    r"|!DOCTYPE(?:[^>\[\"']++|\"[^\"]*+\"|'[^']*+'|\[.*?\])*+>"
    r"|/([^\s>]+)\s*>"
    r"|([^\s/>!?][^\s/>]*)((?:[^>\"']++|\"[^\"]*+\"|'[^']*+')*+)>"
    r"|()"  # group 4: a "<" that starts nothing known (unterminated comment, stray "<")
    r")",
    re.DOTALL,
)


def build_index(text: str) -> Node:
    """Index every element of ``text``. Returns the document element.

    Tolerant about what is between the tags; strict about nesting, because an insertion
    into a file whose nesting is not understood could land anywhere.
    """
    top = Node("", 0, 0, None)
    current = top
    for match in _TOKEN.finditer(text):
        group = match.lastindex
        if group is None:
            continue
        if group == 4:
            raise XmlIndexError(f"\"<\" at offset {match.start()} does not start a complete tag")
        if group == 1:
            if current is top or current.raw != match.group(1):
                raise XmlIndexError(
                    f"end tag </{match.group(1)}> at offset {match.start()} does not close "
                    f"<{current.raw}>"
                )
            current.end_start = match.start()
            current.end = match.end()
            parent = current.parent
            assert parent is not None
            current = parent
        else:
            node = Node(match.group(2), match.start(), match.end(), current)
            current.children.append(node)
            if text[match.end() - 2] != "/":
                current = node
    if current is not top:
        raise XmlIndexError(f"<{current.raw}> at offset {current.start} is never closed")
    if len(top.children) != 1:
        raise XmlIndexError(f"expected one document element, found {len(top.children)}")
    root = top.children[0]
    root.parent = None
    return root


# --------------------------------------------------------------------------- document

_UUID_ATTR = re.compile(r"""\sUUID\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_ATTR = re.compile(r"""([^\s=/>"']+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


class Document:
    """One XML file in memory: original text, element index, pending insertions."""

    def __init__(
        self,
        rel_path: str,
        text: str,
        codec: str = "utf-8",
        bom: bytes = b"",
        created: bool = False,
    ) -> None:
        self.rel_path = rel_path
        self.text = text
        self.codec = codec
        self.bom = bom
        self.created = created  # True: the file does not exist yet
        self.root = build_index(text)
        raw = self.root.raw
        self.prefix = raw[: len(raw) - len(self.root.tag)]  # "" or "AR:"
        crlf = text.count("\r\n")
        self.eol = "\r\n" if crlf and crlf * 2 >= text.count("\n") else "\n"
        self.unit = self._detect_unit()
        self._dirty: list[Node] = []

    @classmethod
    def from_bytes(cls, rel_path: str, data: bytes) -> Document:
        text, codec, bom = decode(data)
        return cls(rel_path, text, codec, bom)

    # ---- reading the index

    def name_of(self, node: Node) -> str | None:
        """Text of the SHORT-NAME child, None for an element that is not identifiable."""
        cached = node._short
        if cached is not _UNSET:
            return cached
        name: str | None = None
        child = node.child(SHORT_NAME)
        if child is not None:
            if child.virtual:
                name = child.text
            elif child.end_start >= 0:
                name = self.text[child.start_end : child.end_start].strip()
        node._short = name
        return name

    def attribute(self, node: Node, name: str) -> str | None:
        """Value of one attribute of an element, as written (entities are not resolved)."""
        if node.virtual:
            return next((value for key, value in node.attrs if key == name), None)
        for match in _ATTR.finditer(self.text, node.start + 1 + len(node.raw), node.start_end):
            if match.group(1) == name:
                value = match.group(2)
                return value if value is not None else match.group(3)
        return None

    def start_tag(self, node: Node) -> str:
        return self.text[node.start : node.start_end]

    def identifiables(self, node: Node) -> Iterator[Node]:
        """Identifiable elements directly below ``node``: found through wrapper elements
        such as PORTS or ELEMENTS, never through another identifiable element."""
        stack = node.children[::-1]
        while stack:
            child = stack.pop()
            if child.tag == SHORT_NAME:
                continue
            if self.name_of(child) is not None:
                yield child
            elif child.children:
                stack.extend(child.children[::-1])

    def named_child(self, node: Node, name: str) -> Node | None:
        for child in self.identifiables(node):
            if self.name_of(child) == name:
                return child
        return None

    def find(self, path: str) -> Node | None:
        """The element with this absolute short-name path, or None."""
        parts = [part for part in path.split("/") if part]
        if not parts:
            return None
        return self._descend(self.root, parts, 0)

    def _descend(self, node: Node, parts: Sequence[str], index: int) -> Node | None:
        if index == len(parts):
            return node
        wanted = parts[index]
        for child in self.identifiables(node):
            if self.name_of(child) == wanted:
                found = self._descend(child, parts, index + 1)
                if found is not None:
                    return found
        return None

    def sample_uuid(self, nodes: Iterable[Node]) -> str | None:
        """UUID attribute of the first of ``nodes`` that has one."""
        for node in nodes:
            value = self.attribute(node, "UUID")
            if value is not None:
                return value
        return None

    def any_uuid(self) -> str | None:
        """Some UUID attribute value of the file, None if the file has none."""
        match = _UUID_ATTR.search(self.text)
        if match is None:
            return None
        value = match.group(1)
        return value if value is not None else match.group(2)

    # ---- layout of the text

    def _line_start(self, offset: int) -> int:
        return self.text.rfind("\n", 0, offset) + 1

    def _indent_before(self, offset: int) -> str | None:
        """White space between the line start and ``offset``; None if other text is there."""
        lead = self.text[self._line_start(offset) : offset]
        return lead if not lead.strip(" \t") else None

    def _line_indent(self, offset: int) -> str:
        """Leading white space of the line that contains ``offset``."""
        start = self._line_start(offset)
        line = self.text[start:offset]
        return line[: len(line) - len(line.lstrip(" \t"))]

    def _eol_at(self, offset: int) -> str:
        """Line ending of the line that contains ``offset`` (the file's main one at the end)."""
        index = self.text.find("\n", offset)
        if index < 0:
            index = self.text.rfind("\n", 0, offset)
        if index < 0:
            return self.eol
        return "\r\n" if index > 0 and self.text[index - 1] == "\r" else "\n"

    def _detect_unit(self) -> str:
        """Indentation step: what a child's line has more than its parent's line."""
        seen = 0
        stack = [self.root]
        while stack and seen < 200:
            node = stack.pop()
            seen += 1
            parent_indent = self._indent_before(node.start)
            for child in node.children:
                indent = self._indent_before(child.start)
                if (
                    parent_indent is not None
                    and indent is not None
                    and len(indent) > len(parent_indent)
                    and indent.startswith(parent_indent)
                ):
                    return indent[len(parent_indent) :]
            stack.extend(node.children[:4][::-1])
        return "  "

    # ---- editing

    def insert(self, parent: Node, node: Node, index: int | None = None) -> None:
        """Hang the new element ``node`` into ``parent`` (at the end, or before ``index``)."""
        node.parent = parent
        if index is None:
            parent.children.append(node)
        else:
            parent.children.insert(index, node)
        if not parent.virtual and parent not in self._dirty:
            self._dirty.append(parent)

    def reset(self) -> None:
        """Drop every pending insertion."""
        for parent in self._dirty:
            parent.children = [child for child in parent.children if not child.virtual]
        self._dirty.clear()

    @property
    def changed(self) -> bool:
        return self.created or bool(self._dirty)

    # ---- rendering

    def render_lines(self, node: Node, indent: str, out: list[str]) -> None:
        """Lines of a new element, each with its indentation and without line ending."""
        name = self.prefix + node.tag
        attrs = "".join(f' {key}="{escape_attr(value)}"' for key, value in node.attrs)
        if node.text is not None:
            out.append(f"{indent}<{name}{attrs}>{escape_text(node.text)}</{name}>")
        elif not node.children:
            out.append(f"{indent}<{name}{attrs}/>")
        else:
            out.append(f"{indent}<{name}{attrs}>")
            deeper = indent + self.unit
            for child in node.children:
                self.render_lines(child, deeper, out)
            out.append(f"{indent}</{name}>")

    def _child_indent(self, parent: Node) -> str:
        for child in parent.children:
            if not child.virtual:
                indent = self._indent_before(child.start)
                if indent is not None:
                    return indent
        return self._line_indent(parent.start) + self.unit

    def _splices(self) -> list[tuple[int, int, str]]:
        """(start, end, replacement) for every run of new children, in text order."""
        result: list[tuple[int, int, str]] = []
        for parent in self._dirty:
            indent = self._child_indent(parent)
            eol = self._eol_at(parent.start)
            run: list[Node] = []
            for child in parent.children:
                if child.virtual:
                    run.append(child)
                elif run:
                    result.append(self._before_child(child, run, indent, eol))
                    run = []
            if run:
                result.append(self._at_end(parent, run, indent, eol))
        result.sort()
        return result

    def _block(self, run: Sequence[Node], indent: str, eol: str) -> str:
        lines: list[str] = []
        for node in run:
            self.render_lines(node, indent, lines)
        return "".join(line + eol for line in lines)

    def _before_child(
        self, child: Node, run: Sequence[Node], indent: str, eol: str
    ) -> tuple[int, int, str]:
        block = self._block(run, indent, eol)
        if self._indent_before(child.start) is not None:
            anchor = self._line_start(child.start)
            return anchor, anchor, block
        return child.start, child.start, eol + block + indent

    def _at_end(
        self, parent: Node, run: Sequence[Node], indent: str, eol: str
    ) -> tuple[int, int, str]:
        block = self._block(run, indent, eol)
        if parent.end_start < 0:  # <X/> becomes <X> ... </X>
            slash = parent.start_end - 2
            closing = f"{self._line_indent(parent.start)}</{parent.raw}>"
            return slash, parent.start_end, ">" + eol + block + closing
        if self._indent_before(parent.end_start) is not None:
            anchor = self._line_start(parent.end_start)
            return anchor, anchor, block
        return parent.end_start, parent.end_start, eol + block + self._line_indent(parent.start)

    def render(self) -> str:
        """The whole text with every pending insertion."""
        return self._render_span(-1, len(self.text) + 1)

    def _render_span(self, start: int, stop: int) -> str:
        """Text of [start, stop) with the insertions that lie strictly inside the span."""
        out: list[str] = []
        position = max(start, 0)
        for splice_start, splice_end, replacement in self._splices():
            if splice_start <= start or splice_start >= stop:
                continue
            out.append(self.text[position:splice_start])
            out.append(replacement)
            position = splice_end
        out.append(self.text[position : min(stop, len(self.text))])
        return "".join(out)

    def render_element(self, node: Node) -> str:
        """Text of one element after the change, starting at its ``<``."""
        if node.virtual:
            lines: list[str] = []
            self.render_lines(node, "", lines)
            return self.eol.join(lines)
        return self._render_span(node.start, node.end)

    def to_bytes(self) -> bytes:
        return encode(self.render(), self.codec, self.bom)

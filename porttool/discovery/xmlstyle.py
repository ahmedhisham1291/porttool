"""Byte-level style of an XML file: what a writer must reproduce to look like the tool.

Nothing here parses XML. The facts come from the raw bytes: byte order mark, declared
encoding, line endings, indentation, the exact declaration and root start tag.
"""

from __future__ import annotations

import codecs
import re
from collections import Counter
from dataclasses import dataclass

HEAD_BYTES = 64 * 1024
_MAX_RAW_TAG = 1000

_BOMS: tuple[tuple[bytes, str, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le", "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32-be", "utf-32"),
    (codecs.BOM_UTF8, "utf-8", "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16"),
)

_DECLARATION_RE = re.compile(r"<\?xml\b[^>]*?\?>", re.DOTALL)
_ENCODING_RE = re.compile(r"""encoding\s*=\s*["']([A-Za-z0-9._\-]+)["']""")
_PROLOG_NOISE_RE = re.compile(r"<!--.*?-->|<\?.*?\?>|<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_START_TAG_RE = re.compile(
    r"""<(?:([A-Za-z_][\w.\-]*):)?([A-Za-z_][\w.\-]*)((?:\s+[\w:.\-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*/?>""",
    re.DOTALL,
)
_ATTRIBUTE_RE = re.compile(r"""([\w:.\-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.DOTALL)


@dataclass(frozen=True, slots=True)
class EncodingInfo:
    bom: str  # "none", "utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"
    declared: str | None  # encoding named in the XML declaration, as written
    codec: str  # Python codec used to decode the file
    is_utf8: bool  # True: the bytes can go to the parser unchanged


def sniff_encoding(head: bytes) -> EncodingInfo:
    """Work out the encoding from the first bytes of a file (BOM, then declaration)."""
    bom = "none"
    codec: str | None = None
    for mark, label, bom_codec in _BOMS:
        if head.startswith(mark):
            bom, codec = label, bom_codec
            break
    if codec is None:
        if head.startswith(b"<\x00"):
            codec = "utf-16-le"
        elif head.startswith(b"\x00<"):
            codec = "utf-16-be"
    if codec is not None and not codec.startswith("utf-8"):
        text = head[:4096].decode(codec, errors="replace")
    else:
        text = head[:4096].decode("latin-1")
    declared_match = _ENCODING_RE.search(_first_declaration(text) or "")
    declared = declared_match.group(1) if declared_match else None
    if codec is None:
        codec = declared.lower() if declared else "utf-8"
    is_utf8 = codec.replace("_", "-") in ("utf-8", "utf8", "utf-8-sig", "us-ascii", "ascii")
    return EncodingInfo(bom=bom, declared=declared, codec=codec, is_utf8=is_utf8)


def _first_declaration(text: str) -> str | None:
    match = _DECLARATION_RE.search(text[:2048])
    if match is None or text[: match.start()].strip("﻿ \t\r\n"):
        return None
    return match.group(0)


@dataclass(frozen=True, slots=True)
class StyleFacts:
    bom: str
    declared_encoding: str | None
    codec: str
    declaration: str | None  # exact text of the XML declaration
    line_endings: str  # "crlf", "lf", "cr", "mixed", "none"
    crlf: int
    lf: int
    cr: int
    indent: str  # "2 spaces", "1 tab", "mixed", "none"
    trailing_newline: str  # "crlf", "lf", "cr", "none"
    root_prefix: str | None  # "" when the root tag has no prefix
    root_tag: str | None
    root_start_tag: str | None  # exact text, shortened to 1000 characters
    namespace: str | None
    schema_location: str | None
    head_comments: int
    attribute_quote: str  # "double", "single", "mixed", "none"

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__slots__}


def detect_indent(text: str) -> str:
    """Name the indentation unit of pretty-printed XML: ``2 spaces``, ``1 tab``, ..."""
    lines = text.splitlines()
    if lines and not text.endswith(("\n", "\r")):
        lines = lines[:-1]  # the sample may end in the middle of a line
    indents: list[str] = []
    for line in lines:
        stripped = line.lstrip(" \t")
        if stripped.startswith("<"):
            indents.append(line[: len(line) - len(stripped)])
    used = [indent for indent in indents if indent]
    if not used:
        return "none"
    has_tab = any("\t" in indent for indent in used)
    has_space = any(" " in indent for indent in used)
    if has_tab and has_space:
        return "mixed"
    steps: Counter[int] = Counter()
    previous = 0
    for indent in indents:
        if len(indent) > previous:
            steps[len(indent) - previous] += 1
        previous = len(indent)
    if not steps:
        return "none"
    unit = steps.most_common(1)[0][0]
    if has_tab:
        return f"{unit} tab" + ("" if unit == 1 else "s")
    return f"{unit} space" + ("" if unit == 1 else "s")


def _line_ending_label(crlf: int, lf: int, cr: int) -> str:
    kinds = [name for name, count in (("crlf", crlf), ("lf", lf), ("cr", cr)) if count]
    if not kinds:
        return "none"
    return kinds[0] if len(kinds) == 1 else "mixed"


class StyleCollector:
    """Collects style facts while a file streams by. Feed every chunk, then ``result()``."""

    def __init__(self) -> None:
        self.encoding: EncodingInfo = EncodingInfo("none", None, "utf-8", True)
        self._started = False
        self._head_bytes = b""
        self._head_text: list[str] = []
        self._head_chars = 0
        self._crlf = 0
        self._lf_total = 0
        self._cr_total = 0
        self._pending_cr = False
        self._last = ""

    def start(self, head: bytes) -> EncodingInfo:
        self.encoding = sniff_encoding(head)
        self._started = True
        return self.encoding

    def add_bytes(self, raw: bytes) -> None:
        """Feed a chunk of an ASCII-compatible file (UTF-8 and single-byte encodings)."""
        if len(self._head_bytes) < HEAD_BYTES:
            self._head_bytes += raw[: HEAD_BYTES - len(self._head_bytes)]
        self._count(
            raw.count(b"\r\n"),
            raw.count(b"\n"),
            raw.count(b"\r"),
            raw.startswith(b"\n"),
            raw.endswith(b"\r"),
        )
        if raw:
            self._last = (self._last + raw[-2:].decode("latin-1"))[-2:]

    def add_text(self, text: str) -> None:
        """Feed a decoded chunk (UTF-16 and UTF-32 files)."""
        if self._head_chars < HEAD_BYTES:
            piece = text[: HEAD_BYTES - self._head_chars]
            self._head_text.append(piece)
            self._head_chars += len(piece)
        self._count(
            text.count("\r\n"),
            text.count("\n"),
            text.count("\r"),
            text.startswith("\n"),
            text.endswith("\r"),
        )
        if text:
            self._last = (self._last + text[-2:])[-2:]

    def _count(self, crlf: int, lf: int, cr: int, starts_lf: bool, ends_cr: bool) -> None:
        if self._pending_cr and starts_lf:
            crlf += 1  # a CR LF pair split across two chunks
        self._crlf += crlf
        self._lf_total += lf
        self._cr_total += cr
        self._pending_cr = ends_cr

    def head(self) -> str:
        """The decoded start of the file (at most 64 KB)."""
        if self._head_text:
            return "".join(self._head_text).lstrip("﻿")
        try:
            text = self._head_bytes.decode(self.encoding.codec, errors="replace")
        except LookupError:
            text = self._head_bytes.decode("latin-1")
        return text.lstrip("﻿")

    def result(self) -> StyleFacts:
        head = self.head()
        declaration = _first_declaration(head)
        body = head[len(declaration) :] if declaration and head.startswith(declaration) else head
        head_comments = 0
        root_prefix: str | None = None
        root_tag: str | None = None
        root_start: str | None = None
        namespace: str | None = None
        schema_location: str | None = None
        quotes: Counter[str] = Counter()
        cleaned = _PROLOG_NOISE_RE.sub(lambda m: " " * len(m.group(0)), body)
        match = _START_TAG_RE.search(cleaned)
        if match is not None:
            head_comments = len(_COMMENT_RE.findall(body[: match.start()]))
            root_prefix = match.group(1) or ""
            root_tag = match.group(2)
            raw = body[match.start() : match.end()]
            root_start = raw if len(raw) <= _MAX_RAW_TAG else raw[:_MAX_RAW_TAG] + "..."
            for attribute in _ATTRIBUTE_RE.finditer(match.group(3) or ""):
                name = attribute.group(1)
                double, single = attribute.group(2), attribute.group(3)
                quotes["double" if double is not None else "single"] += 1
                value = double if double is not None else single
                if name == ("xmlns:" + root_prefix if root_prefix else "xmlns"):
                    namespace = value
                elif name.endswith("schemaLocation"):
                    schema_location = value
        else:
            head_comments = len(_COMMENT_RE.findall(body))
        crlf = self._crlf
        lf = self._lf_total - crlf
        cr = self._cr_total - crlf
        if self._last.endswith("\r\n"):
            trailing = "crlf"
        elif self._last.endswith("\n"):
            trailing = "lf"
        elif self._last.endswith("\r"):
            trailing = "cr"
        else:
            trailing = "none"
        if not quotes:
            quote = "none"
        elif len(quotes) == 1:
            quote = next(iter(quotes))
        else:
            quote = "mixed"
        return StyleFacts(
            bom=self.encoding.bom,
            declared_encoding=self.encoding.declared,
            codec=self.encoding.codec,
            declaration=declaration,
            line_endings=_line_ending_label(crlf, lf, cr),
            crlf=crlf,
            lf=lf,
            cr=cr,
            indent=detect_indent(head),
            trailing_newline=trailing,
            root_prefix=root_prefix,
            root_tag=root_tag,
            root_start_tag=root_start,
            namespace=namespace,
            schema_location=schema_location,
            head_comments=head_comments,
            attribute_quote=quote,
        )


def decode_xml(data: bytes) -> tuple[str, EncodingInfo]:
    """Decode a whole XML document. Raises UnicodeDecodeError or LookupError."""
    info = sniff_encoding(data[:4096])
    codec = "utf-8-sig" if info.is_utf8 else info.codec
    return data.decode(codec).lstrip("﻿"), info


def analyze_style(data: bytes) -> StyleFacts:
    """Style facts of a document held in memory (used for the two sides of a golden diff)."""
    collector = StyleCollector()
    info = collector.start(data[:4096])
    if info.is_utf8:
        collector.add_bytes(data)
    else:
        try:
            collector.add_text(data.decode(info.codec, errors="replace"))
        except LookupError:
            collector.add_bytes(data)
    return collector.result()

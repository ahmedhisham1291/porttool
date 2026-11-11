"""Streaming XML reading with the standard library's expat parser.

No tree is ever built: a sink object receives start, end, text and comment events while
the file streams through in 1 MB chunks. expat reports byte offsets and its error
messages come from a fixed list, so they never contain file content.
"""

from __future__ import annotations

import codecs
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from xml.parsers import expat

from porttool.discovery.xmlstyle import StyleCollector

_TAIL_KEEP = 8


@dataclass(frozen=True, slots=True)
class XmlProblem:
    """Why a file could not be read completely."""

    kind: str  # "empty", "malformed", "encoding", "unreadable"
    message: str
    line: int | None = None
    column: int | None = None

    def describe(self) -> str:
        where = f" (line {self.line}, column {self.column})" if self.line is not None else ""
        return f"{self.kind}: {self.message}{where}"


class XmlSink:
    """Base class of event receivers. Subclasses override the four event methods."""

    def __init__(self) -> None:
        self.parser: expat.XMLParserType | None = None
        self._chunk: bytes = b""
        self._chunk_base: int = 0
        self._previous_tail: bytes = b""

    def start(self, name: str, attrs: list[str]) -> None:
        """Element start. ``attrs`` is a flat list: name, value, name, value, ..."""

    def end(self, name: str) -> None:
        """Element end."""

    def data(self, text: str) -> None:
        """Character data (may arrive in several pieces)."""

    def comment(self, text: str) -> None:
        """A comment, anywhere in the document."""

    def bytes_before(self, offset: int, count: int = 2) -> bytes | None:
        """The ``count`` bytes that precede stream offset ``offset``, if still in reach."""
        position = offset - self._chunk_base
        if position >= count:
            return self._chunk[position - count : position]
        joined = self._previous_tail + self._chunk[: max(position, 0)]
        wanted = len(self._previous_tail) + position
        if wanted < count or wanted > len(joined):
            return None
        return joined[wanted - count : wanted]

    def _advance(self, chunk: bytes) -> None:
        self._chunk_base += len(self._chunk)
        self._previous_tail = (self._previous_tail + self._chunk)[-_TAIL_KEEP:]
        self._chunk = chunk


def feed_xml(
    chunks: Iterable[bytes], sink: XmlSink, style: StyleCollector | None = None
) -> XmlProblem | None:
    """Push a document through ``sink``. Returns None on success, else what went wrong.

    The sink keeps whatever it saw before the problem, so a truncated file still yields
    partial facts. Reading errors of the chunk source (OSError) are not caught here.
    """
    collector = style if style is not None else StyleCollector()
    parser: expat.XMLParserType | None = None
    decoder: codecs.IncrementalDecoder | None = None
    total = 0
    try:
        for raw in _with_full_head(chunks):
            if not raw:
                continue
            if parser is None:
                info = collector.start(raw[:4096])
                if info.is_utf8:
                    parser = expat.ParserCreate()
                else:
                    decoder = codecs.getincrementaldecoder(info.codec)(errors="strict")
                    parser = expat.ParserCreate("utf-8")
                _attach(parser, sink)
            total += len(raw)
            if decoder is None:
                collector.add_bytes(raw)
                data = raw
            else:
                text = decoder.decode(raw)
                if sink._chunk_base == 0 and not sink._chunk:
                    text = text.lstrip("﻿")
                collector.add_text(text)
                data = text.encode("utf-8")
            sink._advance(data)
            parser.Parse(data, False)
        if parser is None or total == 0:
            return XmlProblem("empty", "the file has no content")
        if decoder is not None:
            decoder.decode(b"", final=True)
        sink._advance(b"")
        parser.Parse(b"", True)
    except expat.ExpatError as error:
        message = expat.errors.messages[error.code] if error.code else "XML syntax error"
        return XmlProblem("malformed", message, error.lineno, error.offset)
    except UnicodeDecodeError as error:
        return XmlProblem("encoding", f"bytes do not match the encoding {error.encoding}")
    except LookupError:
        return XmlProblem("encoding", "the declared encoding is not known")
    finally:
        sink.parser = None
    return None


def _with_full_head(chunks: Iterable[bytes], minimum: int = 4096) -> Iterator[bytes]:
    """Join leading chunks until the first one is long enough to sniff the encoding."""
    iterator = iter(chunks)
    head = b""
    for raw in iterator:
        head += raw
        if len(head) >= minimum:
            break
    if head:
        yield head
    yield from iterator


def _attach(parser: expat.XMLParserType, sink: XmlSink) -> None:
    parser.buffer_text = True
    parser.ordered_attributes = True
    parser.StartElementHandler = sink.start
    parser.EndElementHandler = sink.end
    parser.CharacterDataHandler = sink.data
    parser.CommentHandler = sink.comment
    sink.parser = parser

"""The DaVinci Developer workspace file (.dcf): which ARXML files does it list?

PENDING-DISCOVERY: the .dcf format is not documented in this repository yet
(docs/discovery.md does not exist). Everything that depends on the format lives in
``list_arxml_references``. The provisional rule: the .dcf is XML, and every attribute
value or element text that looks like a relative path ending in ``.arxml`` is a listed
file, relative to the folder of the .dcf. Replace this one function when Agent 0 has
documented the real format; nothing else in the reader knows the format.
"""

from __future__ import annotations

import ntpath
import posixpath
from pathlib import Path
from typing import Any

from lxml import etree

_ARXML_SUFFIX = ".arxml"


class DcfFormatError(Exception):
    """The .dcf could not be interpreted. Turned into a finding by the caller."""


def _looks_like_relative_arxml(text: str) -> bool:
    if not text.lower().endswith(_ARXML_SUFFIX):
        return False
    if any(ch in text for ch in '\r\n\t<>|"*?'):
        return False
    # Absolute paths (drive letter, UNC, leading slash) and URLs are not workspace-relative.
    if ntpath.isabs(text) or posixpath.isabs(text) or ntpath.splitdrive(text)[0]:
        return False
    return "://" not in text


def _candidates(root: Any) -> list[str]:
    found: list[str] = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        text = el.text
        if text is not None and text.strip():
            found.append(text.strip())
        found.extend(str(value).strip() for value in el.attrib.values())
    return found


def list_arxml_references(dcf_file: Path) -> tuple[str, ...]:
    """Relative ARXML paths the .dcf lists, as written but with forward slashes.

    PENDING-DISCOVERY: provisional implementation, see the module docstring.
    Raises ``OSError`` if the file cannot be read and ``DcfFormatError`` if it is not XML.
    """
    data = dcf_file.read_bytes()
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        remove_comments=True,
        remove_pis=True,
        collect_ids=False,
    )
    try:
        root = etree.fromstring(data, parser)
    except etree.LxmlError as error:
        raise DcfFormatError(str(error)) from error
    seen: dict[str, None] = {}
    for text in _candidates(root):
        if _looks_like_relative_arxml(text):
            seen.setdefault(text.replace("\\", "/"), None)
    return tuple(seen)

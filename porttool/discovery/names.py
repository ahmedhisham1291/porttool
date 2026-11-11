"""Name mapping for discovery reports: identity, or stable anonymous tokens.

Every string that comes from the owner's repo (short names, file names, folder names,
sample values) passes through one ``Names`` object before it is written to a report.
Without ``--anonymize`` the object returns its input. With ``--anonymize`` it returns a
token such as ``N1a2b3c``: the same input gives the same token for the same key, and the
key lives in a private folder that is never sent back.
"""

from __future__ import annotations

import hashlib
import hmac
import re

#: Longest first: the part of a file name that survives anonymization.
KNOWN_SUFFIXES: tuple[str, ...] = (
    ".ecuc.arxml",
    ".arxml",
    ".dcf",
    ".dpa",
    ".xml",
    ".xsd",
    ".exe",
    ".dll",
    ".lock",
    ".lck",
    ".tmp",
    ".bak",
)

#: Extensions that mark a bare value (no folder separator) as a file name.
FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        "arxml", "dcf", "dpa", "xml", "xsd", "exe", "dll", "bat", "cmd", "c", "h", "a2l",
        "dbc", "ldf", "cdd", "odx", "json", "txt", "csv", "ini", "cfg", "gpj", "mak", "log",
        "vtt", "bswmd", "ecuc", "sysdesc", "groovy", "jar", "zip", "lic", "htm", "html", "pdf",
    }
)  # fmt: skip

#: AUTOSAR parameter definitions below these roots are standard or Vector names, not
#: project names. They stay readable in every mode.
STANDARD_DEFINITION_ROOTS: tuple[str, ...] = ("/MICROSAR/", "/AUTOSAR/")

#: Namespaces and schema hints that stay readable in every mode.
STANDARD_URL_PREFIXES: tuple[str, ...] = (
    "http://autosar.org/",
    "http://www.autosar.org/",
    "http://www.w3.org/",
)

#: Folder and file names of the Vector tool installation. They are product names, so an
#: anonymized report keeps them in the paths of the executables it found.
_TOOL_SEGMENT_RE = re.compile(
    r"^(program files( \(x86\))?|vector( davinci.*)?|davinci.*|core|bin|bin64|"
    r"dv(imex|wspchecker|swcgen|cfgcmd)(\.exe)?)$",
    re.IGNORECASE,
)

_NUMBER_RE = re.compile(
    r"^[+-]?(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+(\.\d+)?([eE][+-]?\d+)?|\.\d+)$"
)
_UUID_RE = re.compile(
    r"^\{?([A-Za-z]{2,5}:)?[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}\}?$"
)
_VERSION_RE = re.compile(r"^[vVrR]?\d+([.\-_]\d+){1,5}$")
_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)
_AR_PATH_RE = re.compile(r"^(/[A-Za-z_][A-Za-z0-9_]*)+$")
_ENUM_RE = re.compile(r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
_DRIVE_RE = re.compile(r"^[A-Za-z]:$")
_VARIABLE_RE = re.compile(r"\$\(([^)]*)\)|\$\{([^}]*)\}|%([^%\\/]+)%")
_SEPARATOR_RE = re.compile(r"([\\/]+)")


def split_extension(name: str) -> tuple[str, str]:
    """Split a file name into (stem, extension). ``Foo.ecuc.arxml`` -> ``Foo``, ``.ecuc.arxml``."""
    lower = name.lower()
    for suffix in KNOWN_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    stem, dot, extension = name.rpartition(".")
    if dot and stem and 1 <= len(extension) <= 10 and extension.isalnum():
        return stem, "." + extension
    return name, ""


def is_number(text: str) -> bool:
    return _NUMBER_RE.match(text) is not None


def is_uuid(text: str) -> bool:
    return _UUID_RE.match(text) is not None


def is_autosar_path(text: str) -> bool:
    return _AR_PATH_RE.match(text) is not None


def is_enum_literal(text: str) -> bool:
    """True for values shaped like AUTOSAR enumeration literals, e.g. ``NOT-ACCESSIBLE``."""
    return _ENUM_RE.match(text) is not None


def has_variable(text: str) -> bool:
    """True if the value holds a placeholder such as ``$(Name)``, ``${Name}`` or ``%NAME%``."""
    return _VARIABLE_RE.search(text) is not None


def looks_like_path(value: str) -> bool:
    """Heuristic: does this attribute or text value name a file or a folder?"""
    text = value.strip()
    if not text or len(text) > 1024 or "\n" in text or "\r" in text:
        return False
    if _URL_RE.match(text) or text.startswith(("urn:", "<")):
        return False
    lower = text.lower()
    if lower in {"n/a", "n.a.", "and/or"}:
        return False
    has_separator = "\\" in text or "/" in text
    extension_match = re.search(r"\.([A-Za-z0-9_]{1,8})$", text)
    extension = extension_match.group(1).lower() if extension_match else ""
    known_extension = extension in FILE_EXTENSIONS
    if text.startswith("/"):
        # An AUTOSAR path such as /DataTypes/uint8 is not a file; /a/b/c.arxml is.
        return known_extension
    if re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith(("\\\\", ".\\", "./", "..")):
        return True
    if has_variable(text) and (has_separator or known_extension):
        return True
    if has_separator:
        segments = [segment for segment in _SEPARATOR_RE.split(text)[::2] if segment]
        if len(segments) < 2 and not known_extension:
            return False
        return any(any(char.isalpha() for char in segment) for segment in segments)
    return known_extension and " " not in text.strip()


def text_shape(text: str, limit: int = 80) -> str:
    """Describe a value without revealing it: letters become a/A, digits become 9."""
    out: list[str] = []
    for char in text[:limit]:
        if char.isdigit():
            out.append("9")
        elif char.isalpha():
            out.append("A" if char.isupper() else "a")
        else:
            out.append(char)
    shape = "".join(out)
    return shape + ("..." if len(text) > limit else "")


class Names:
    """Maps repo names to report names. One instance per run."""

    def __init__(self, *, anonymize: bool = False, key: bytes = b"") -> None:
        self.anonymized: bool = anonymize
        self._key = key
        self._by_text: dict[str, str] = {}
        self._by_token: dict[str, str] = {}

    # ---- primitive

    def token(self, text: str) -> str:
        """Return the stable token of ``text`` (or ``text`` itself when not anonymizing)."""
        if not self.anonymized or text == "":
            return text
        known = self._by_text.get(text)
        if known is not None:
            return known
        digest = hmac.new(
            self._key, text.encode("utf-8", "surrogatepass"), hashlib.sha256
        ).hexdigest()
        length = 6
        token = "N" + digest[:length]
        while self._by_token.get(token, text) != text and length < len(digest):
            length += 1
            token = "N" + digest[:length]
        self._by_text[text] = token
        self._by_token[token] = text
        return token

    def mapping(self) -> dict[str, str]:
        """Token -> original text, for the private map file."""
        return dict(self._by_token)

    # ---- AUTOSAR names

    def name(self, short_name: str) -> str:
        return self.token(short_name)

    def ar_path(self, path: str) -> str:
        """Anonymize an AUTOSAR path such as ``/ComponentTypes/SwcA`` segment by segment."""
        if not self.anonymized:
            return path
        return "/".join(self.token(segment) for segment in path.split("/"))

    def definition_ref(self, path: str) -> str:
        """ECUC definition paths below the standard roots are kept; others are anonymized."""
        if not self.anonymized or path.startswith(STANDARD_DEFINITION_ROOTS):
            return path
        return self.ar_path(path)

    # ---- file system names

    def file_name(self, name: str) -> str:
        """Anonymize one file name: the stem becomes a token, the extension stays."""
        if not self.anonymized or name in ("", ".", ".."):
            return name
        marker = ""
        for prefix in ("~$", "~"):
            if name.startswith(prefix) and len(name) > len(prefix):
                marker, name = prefix, name[len(prefix) :]
                break
        stem, extension = split_extension(name)
        return marker + self.token(stem) + extension

    def rel_path(self, rel: str, *, is_dir: bool = False) -> str:
        """Anonymize a repo-relative path written with forward slashes."""
        if not self.anonymized or rel in ("", "."):
            return rel
        segments = rel.split("/")
        last = len(segments) - 1
        out: list[str] = []
        for index, segment in enumerate(segments):
            if segment in ("", ".", ".."):
                out.append(segment)
            elif index == last and not is_dir:
                out.append(self.file_name(segment))
            else:
                out.append(self.token(segment))
        return "/".join(out)

    def fs_path(self, raw: str) -> str:
        """Anonymize a path exactly as a tool wrote it: separators, drive and dots stay."""
        if not self.anonymized:
            return raw
        parts = _SEPARATOR_RE.split(raw)
        last = max((i for i in range(0, len(parts), 2) if parts[i]), default=-1)
        out: list[str] = []
        for index, part in enumerate(parts):
            if index % 2 == 1 or part in ("", ".", "..") or _DRIVE_RE.match(part):
                out.append(part)
            elif _VARIABLE_RE.search(part):
                out.append(self._segment_with_variables(part))
            elif index == last:
                out.append(self.file_name(part))
            else:
                out.append(self.token(part))
        return "".join(out)

    def _segment_with_variables(self, part: str) -> str:
        out: list[str] = []
        position = 0
        for match in _VARIABLE_RE.finditer(part):
            literal = part[position : match.start()]
            if literal:
                out.append(self.token(literal))
            whole = match.group(0)
            inner = next(group for group in match.groups() if group is not None)
            out.append(whole.replace(inner, self.token(inner), 1) if inner else whole)
            position = match.end()
        rest = part[position:]
        if rest:
            out.append(self.file_name(rest))
        return "".join(out)

    def tool_path(self, path: str) -> str:
        """Path of a Vector executable: product folder and file names stay readable."""
        if not self.anonymized:
            return path
        parts = _SEPARATOR_RE.split(path)
        out: list[str] = []
        for index, part in enumerate(parts):
            keep = (
                index % 2 == 1
                or part in ("", ".", "..")
                or _DRIVE_RE.match(part) is not None
                or _TOOL_SEGMENT_RE.match(part) is not None
            )
            out.append(part if keep else self.token(part))
        return "".join(out)

    # ---- arbitrary values

    def value(self, text: str, *, allow_enum: bool = False) -> str:
        """Anonymize a sample value. Numbers, booleans, UUIDs, versions and dates stay.

        ``allow_enum`` also keeps values shaped like AUTOSAR enumeration literals; use it
        only for ARXML content, where the caller has excluded name-like elements.
        """
        if not self.anonymized:
            return text
        stripped = text.strip()
        if stripped == "":
            return text
        if (
            is_number(stripped)
            or stripped.lower() in ("true", "false")
            or is_uuid(stripped)
            or _VERSION_RE.match(stripped)
            or _DATETIME_RE.match(stripped)
        ):
            return text
        if _URL_RE.match(stripped) or stripped.startswith("urn:"):
            if stripped.startswith(STANDARD_URL_PREFIXES):
                return text
            return self.token(stripped)
        if is_autosar_path(stripped):
            return self.ar_path(stripped)
        if looks_like_path(stripped):
            return self.fs_path(stripped)
        if allow_enum and is_enum_literal(stripped) and stripped not in self._by_text:
            return text
        return self.token(stripped)

    def external(self, text: str) -> str:
        """Free text produced outside our control (git errors, exception messages)."""
        if not self.anonymized:
            return text
        return f"[text removed by --anonymize: {len(text)} characters]"

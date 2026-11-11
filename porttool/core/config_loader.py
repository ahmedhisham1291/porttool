"""Load, validate, save and discover the project configuration.

Files, all in the repo root unless ``config_file`` says otherwise:

* ``porttool.toml``  paths, naming rules, packages, timeouts (tables ``[paths]``,
  ``[naming]``, ``[packages]``, ``[timeouts]``, ``[generate]``, ``[pending]``, ``[limits]``)
* ``owners.csv``     next to porttool.toml; columns SWC, team, arxml_owner,
  authoring_tool, export_folder
* ``locked.txt``     next to porttool.toml; one SWC name per line, ``#`` starts a comment

Relative paths in porttool.toml are relative to the repo root.

A broken configuration raises ``ConfigError`` with a message that names the file, the
key and what is wrong. Problems PortTool can live with (an unknown key, an optional
folder that moved) are ``Finding`` objects in ``LoadedConfig.findings``.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import logging
import math
import os
import re
import string
import tomllib
import xml.etree.ElementTree as ElementTree
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from porttool.core.config import (
    NamingRules,
    OwnerEntry,
    PackagePaths,
    ProjectConfig,
    ToolTimeouts,
)
from porttool.core.errors import ConfigError
from porttool.core.fsutil import atomic_write_text, default_cache_dir
from porttool.core.models import ArxmlOwner
from porttool.core.results import Finding, Severity

_logger = logging.getLogger(__name__)

CONFIG_FILE_NAME = "porttool.toml"
OWNERS_FILE_NAME = "owners.csv"
LOCKED_FILE_NAME = "locked.txt"
FORMAT_VERSION = 1

#: AUTOSAR short name (AUTOSAR_TPS_GenericStructureTemplate: Identifier).
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_PACKAGE_PATH = re.compile(r"(/[A-Za-z][A-Za-z0-9_]{0,127})+")

_FILE_KEYS = ("dpa_file", "dcf_file", "ecuc_file", "flat_extract", "flat_map", "xsd_file")
_DIR_KEYS = ("sip_root", "developer_bin", "gen_data")
_NAMING_REGEX_KEYS = ("port", "interface", "data_type", "runnable", "connector")
_NAMING_PATTERNS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "server_runnable_pattern": frozenset({"port", "operation"}),
        "connector_pattern": frozenset(
            {"provider", "provider_port", "requester", "requester_port"}
        ),
    }
)
_OWNER_COLUMNS = ("swc", "team", "arxml_owner", "authoring_tool", "export_folder")
_KNOWN_TOP_LEVEL = frozenset(
    {"format_version", "paths", "naming", "packages", "timeouts", "generate", "pending", "limits"}
)
#: Tables owned by other packages. They are passed through untouched in ProjectConfig.extras.
PACKAGE_TABLES = ("davinci", "writer", "pipeline", "gui", "reader", "workbook", "discovery")


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """Result of :func:`load_config`."""

    config: ProjectConfig
    findings: tuple[Finding, ...]
    config_file: Path

    @property
    def has_errors(self) -> bool:
        return any(finding.severity is Severity.ERROR for finding in self.findings)


# --------------------------------------------------------------------------- problem collection


class _Problems:
    """Collects everything that is wrong with one file, then raises one ConfigError."""

    def __init__(self, file: Path) -> None:
        self.file = file
        self.errors: list[str] = []
        self.findings: list[Finding] = []

    def error(self, text: str) -> None:
        self.errors.append(text)

    def note(self, code: str, message: str, severity: Severity = Severity.WARNING) -> None:
        self.findings.append(
            Finding(code=code, severity=severity, message=message, file=str(self.file))
        )

    def raise_if_any(self) -> None:
        if not self.errors:
            return
        if len(self.errors) == 1:
            message = f"{self.file}: {self.errors[0]}"
        else:
            listed = "\n".join(f"  - {text}" for text in self.errors)
            message = f"{self.file} has {len(self.errors)} problems:\n{listed}"
        raise ConfigError(message, detail=str(self.file))


def _describe(value: object) -> str:
    if isinstance(value, bool):
        return f"the truth value {str(value).lower()}"
    if isinstance(value, str):
        return f'the text "{value}"'
    if isinstance(value, int | float):
        return f"the number {value}"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "a table"
    return f"a {type(value).__name__}"


class _Table:
    """Typed, checked access to one TOML table. Keys nobody asked for become findings."""

    def __init__(self, name: str, data: Mapping[str, object], problems: _Problems) -> None:
        self._name = name
        self._data = data
        self._problems = problems
        self._asked: set[str] = set()

    def label(self, key: str) -> str:
        return f"[{self._name}] {key}" if self._name else key

    def _take(self, key: str) -> object | None:
        self._asked.add(key)
        return self._data.get(key)

    def _wrong(self, key: str, expected: str, value: object) -> None:
        self._problems.error(f"{self.label(key)} must be {expected}, but it is {_describe(value)}.")

    def text(self, key: str, *, allow_empty: bool = True) -> str | None:
        value = self._take(key)
        if value is None:
            return None
        if not isinstance(value, str):
            self._wrong(key, "a text in quotes", value)
            return None
        if not allow_empty and not value.strip():
            self._problems.error(f"{self.label(key)} must not be empty.")
            return None
        return value

    def integer(self, key: str, *, minimum: int) -> int | None:
        value = self._take(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self._wrong(key, "a whole number", value)
            return None
        if value < minimum:
            self._problems.error(
                f"{self.label(key)} must be at least {minimum}, but it is {value}."
            )
            return None
        return value

    def positive_number(self, key: str) -> float | None:
        value = self._take(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            self._wrong(key, "a number", value)
            return None
        if not math.isfinite(value) or value <= 0:
            self._problems.error(f"{self.label(key)} must be greater than 0, but it is {value}.")
            return None
        return float(value)

    def text_list(self, key: str) -> tuple[str, ...] | None:
        value = self._take(key)
        if value is None:
            return None
        if not isinstance(value, list):
            self._wrong(key, 'a list of texts, for example ["a", "b"]', value)
            return None
        result: list[str] = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, str) or not item.strip():
                self._problems.error(
                    f"{self.label(key)}: entry {index} must be a non-empty text, "
                    f"but it is {_describe(item)}."
                )
                return None
            result.append(item)
        return tuple(result)

    def finish(self) -> None:
        for key in self._data:
            if key not in self._asked:
                self._problems.note(
                    "CFG-KEY-001",
                    f"Unknown setting {self.label(key)} is ignored. Check the spelling.",
                )


# --------------------------------------------------------------------------- reading files


def _read_text(path: Path, what: str) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except FileNotFoundError as error:
        raise ConfigError(f"{what} {path} does not exist.", detail=str(path)) from error
    except OSError as error:
        raise ConfigError(
            f"{what} {path} cannot be read: {error.strerror or error}", detail=repr(error)
        ) from error
    except UnicodeDecodeError as error:
        raise ConfigError(
            f"{what} {path} is not UTF-8 text. Save it with the encoding UTF-8.",
            detail=repr(error),
        ) from error


def _leaves_repo(text: str) -> bool:
    """True for an absolute path (``/x``, ``C:/x``, ``C:x``, ``//server/x``) or one with ``..``."""
    return text.startswith("/") or bool(re.match(r"[A-Za-z]:", text)) or ".." in text.split("/")


def _absolute(repo_root: Path, text: str) -> Path:
    """Resolve a configured path against the repo root, without touching the disk."""
    return Path(os.path.normpath(os.path.join(repo_root, text)))


def _subtable(document: Mapping[str, object], name: str, problems: _Problems) -> _Table:
    value = document.get(name, {})
    if not isinstance(value, dict):
        problems.error(f"[{name}] must be a table, but it is {_describe(value)}.")
        value = {}
    return _Table(name, value, problems)


def load_config(
    repo_root: str | os.PathLike[str],
    *,
    config_file: str | os.PathLike[str] | None = None,
    strict: bool = True,
) -> LoadedConfig:
    """Load ``porttool.toml``, ``owners.csv`` and ``locked.txt`` into a ``ProjectConfig``.

    ``config_file`` defaults to ``<repo_root>/porttool.toml``; owners.csv and locked.txt
    are read from the folder of the config file. Raises ``ConfigError`` for a missing or
    malformed file, a wrong type or value, and a configured ARXML folder that does not
    exist. With ``strict=False`` a missing ARXML folder is an error ``Finding`` instead,
    so a settings screen can still show the configuration and let the user repair it.
    """
    root = Path(os.path.abspath(repo_root))
    if not root.is_dir():
        raise ConfigError(f"The repository folder {root} does not exist.", detail=str(root))
    file = (
        Path(os.path.abspath(config_file)) if config_file is not None else root / CONFIG_FILE_NAME
    )
    if not file.is_file():
        raise ConfigError(
            f"{file} does not exist. Create it with the setup dialog or with "
            "'porttool config discover'.",
            detail=str(file),
        )
    text = _read_text(file, "The configuration file")
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{file} is not valid TOML: {error}", detail=str(file)) from error

    problems = _Problems(file)
    top = _Table("", document, problems)
    version = top.integer("format_version", minimum=1)
    if version is not None and version > FORMAT_VERSION:
        raise ConfigError(
            f"{file} has format_version {version}, but this PortTool reads format "
            f"{FORMAT_VERSION}. Update PortTool.",
            detail=str(file),
        )

    paths = _subtable(document, "paths", problems)
    single: dict[str, Path] = {}
    for key in (*_FILE_KEYS, *_DIR_KEYS, "cache_dir"):
        value = paths.text(key, allow_empty=False)
        if value is not None:
            single[key] = _absolute(root, value)
    errors_before = len(problems.errors)
    root_texts = paths.text_list("arxml_roots")
    roots_ok = len(problems.errors) == errors_before
    exclude_globs = paths.text_list("exclude_globs") or ()
    paths.finish()
    if not root_texts and roots_ok:
        problems.error(
            "[paths] arxml_roots is missing or empty. It must list the folder of the "
            "DaVinci Developer workspace ARXML files."
        )
    arxml_roots = tuple(_absolute(root, entry) for entry in root_texts or ())

    naming = _read_naming(_subtable(document, "naming", problems), problems)
    packages = _read_packages(_subtable(document, "packages", problems), problems)
    timeouts = _read_timeouts(_subtable(document, "timeouts", problems))

    generate = _subtable(document, "generate", problems)
    modules = generate.text_list("modules")
    generate.finish()

    pending = _subtable(document, "pending", problems)
    pending_file = pending.text("file", allow_empty=False)
    if pending_file is not None:
        normal = pending_file.replace("\\", "/")
        if _leaves_repo(normal):
            problems.error(
                f'[pending] file must be a path inside the repository, but it is "{pending_file}".'
            )
        pending_file = normal
    pending_days = pending.integer("max_age_days", minimum=0)
    pending.finish()

    limits = _subtable(document, "limits", problems)
    large_file_bytes = limits.integer("large_file_bytes", minimum=1)
    min_free_disk_bytes = limits.integer("min_free_disk_bytes", minimum=0)
    limits.finish()

    package_tables: dict[str, Mapping[str, object]] = {}
    for key in PACKAGE_TABLES:
        value = document.get(key)
        if isinstance(value, dict):
            package_tables[key] = MappingProxyType(dict(value))
        elif value is not None:
            problems.error(f"[{key}] must be a table.")

    for key in document:
        if key not in _KNOWN_TOP_LEVEL and key not in PACKAGE_TABLES:
            kind = "table" if isinstance(document[key], dict) else "setting"
            where = f"[{key}]" if kind == "table" else key
            problems.note("CFG-KEY-001", f"Unknown {kind} {where} is ignored. Check the spelling.")

    for path in arxml_roots:
        if not path.exists():
            message = (
                f"[paths] arxml_roots points to {path}, which does not exist. If the folder "
                "was moved, correct the path."
            )
            if strict:
                problems.error(message)
            else:
                problems.note("CFG-PATH-003", message, Severity.ERROR)
    problems.raise_if_any()

    for key in _FILE_KEYS:
        _check_optional(problems, key, single.get(key), want_file=True)
    for key in _DIR_KEYS:
        _check_optional(problems, key, single.get(key), want_file=False)

    owners, owner_findings = _load_owners_if_present(file.parent / OWNERS_FILE_NAME, root)
    locked, locked_findings = _load_locked_if_present(file.parent / LOCKED_FILE_NAME)

    config = ProjectConfig(
        repo_root=root,
        dpa_file=single.get("dpa_file"),
        dcf_file=single.get("dcf_file"),
        arxml_roots=arxml_roots,
        exclude_globs=exclude_globs,
        ecuc_file=single.get("ecuc_file"),
        flat_extract=single.get("flat_extract"),
        flat_map=single.get("flat_map"),
        sip_root=single.get("sip_root"),
        developer_bin=single.get("developer_bin"),
        gen_data=single.get("gen_data"),
        xsd_file=single.get("xsd_file"),
        cache_dir=single.get("cache_dir", default_cache_dir(root)),
        owners=owners,
        locked_swcs=locked,
        naming=naming,
        packages=packages,
        timeouts=timeouts,
    )
    extra: dict[str, Any] = {}
    if modules is not None:
        extra["generate_modules"] = modules
    if pending_file is not None:
        extra["pending_file"] = pending_file
    if pending_days is not None:
        extra["pending_max_age_days"] = pending_days
    if large_file_bytes is not None:
        extra["large_file_bytes"] = large_file_bytes
    if min_free_disk_bytes is not None:
        extra["min_free_disk_bytes"] = min_free_disk_bytes
    if package_tables:
        extra["extras"] = MappingProxyType(package_tables)
    if extra:
        config = dataclasses.replace(config, **extra)
    findings = (*problems.findings, *owner_findings, *locked_findings)
    for finding in findings:
        _logger.info("config finding %s: %s", finding.code, finding.message)
    return LoadedConfig(config=config, findings=findings, config_file=file)


def _check_optional(problems: _Problems, key: str, path: Path | None, *, want_file: bool) -> None:
    if path is None:
        return
    kind = "file" if want_file else "folder"
    if not path.exists():
        problems.note(
            "CFG-PATH-001",
            f"[paths] {key} points to {path}, which does not exist. If the {kind} was "
            "moved, correct the path.",
        )
    elif path.is_file() != want_file:
        other = "folder" if want_file else "file"
        problems.note("CFG-PATH-002", f"[paths] {key} must be a {kind}, but {path} is a {other}.")


def _read_naming(table: _Table, problems: _Problems) -> NamingRules:
    changes: dict[str, Any] = {}
    max_length = table.integer("max_length", minimum=1)
    if max_length is not None:
        changes["max_length"] = max_length
    for key in _NAMING_REGEX_KEYS:
        value = table.text(key)
        if value is None:
            continue
        try:
            re.compile(value)
        except re.error as error:
            problems.error(f"{table.label(key)} is not a valid regular expression: {error}.")
            continue
        changes[key] = value
    for key, allowed in _NAMING_PATTERNS.items():
        value = table.text(key, allow_empty=False)
        if value is None:
            continue
        try:
            fields = {name for _, name, _, _ in string.Formatter().parse(value) if name is not None}
        except ValueError as error:
            problems.error(f"{table.label(key)} is not a valid pattern: {error}.")
            continue
        unknown = sorted(fields - allowed)
        if unknown:
            problems.error(
                f"{table.label(key)} uses the unknown placeholder "
                f"{', '.join('{' + name + '}' for name in unknown)}. Allowed: "
                f"{', '.join('{' + name + '}' for name in sorted(allowed))}."
            )
            continue
        changes[key] = value
    table.finish()
    return dataclasses.replace(NamingRules(), **changes)


def _read_packages(table: _Table, problems: _Problems) -> PackagePaths:
    changes: dict[str, Any] = {}
    for field in dataclasses.fields(PackagePaths):
        value = table.text(field.name)
        if value is None:
            continue
        if not _PACKAGE_PATH.fullmatch(value):
            problems.error(
                f"{table.label(field.name)} must be an AUTOSAR package path such as "
                f'/DataTypes/CompuMethods, but it is "{value}".'
            )
            continue
        changes[field.name] = value
    table.finish()
    return dataclasses.replace(PackagePaths(), **changes)


def _read_timeouts(table: _Table) -> ToolTimeouts:
    changes: dict[str, Any] = {}
    for field in dataclasses.fields(ToolTimeouts):
        value = table.positive_number(field.name)
        if value is not None:
            changes[field.name] = value
    table.finish()
    return dataclasses.replace(ToolTimeouts(), **changes)


# --------------------------------------------------------------------------- owners.csv, locked.txt


def _load_owners_if_present(
    path: Path, repo_root: Path
) -> tuple[Mapping[str, OwnerEntry], tuple[Finding, ...]]:
    if not path.is_file():
        finding = Finding(
            code="CFG-OWNERS-001",
            severity=Severity.WARNING,
            message=f"{path} does not exist: every SWC is treated as DaVinci-owned and may "
            "be edited. List model-owned SWCs there.",
            file=str(path),
        )
        return MappingProxyType({}), (finding,)
    return load_owners(path, repo_root)


def _load_locked_if_present(path: Path) -> tuple[frozenset[str], tuple[Finding, ...]]:
    if not path.is_file():
        finding = Finding(
            code="CFG-LOCKED-001",
            severity=Severity.INFO,
            message=f"{path} does not exist: no SWC is locked.",
            file=str(path),
        )
        return frozenset(), (finding,)
    return load_locked(path), ()


def _delimiter(header_line: str) -> str:
    counts = {char: header_line.count(char) for char in (",", ";", "\t")}
    best = max(counts, key=lambda char: counts[char])
    return best if counts[best] else ","


def load_owners(
    path: str | os.PathLike[str], repo_root: str | os.PathLike[str] | None = None
) -> tuple[Mapping[str, OwnerEntry], tuple[Finding, ...]]:
    """Read owners.csv. Returns the entries by SWC name plus non-fatal findings.

    The header row names the columns (any order, any case): SWC and arxml_owner are
    required; team, authoring_tool and export_folder are optional. Comma, semicolon and
    tab are accepted as separator. ``arxml_owner`` is davinci, model or locked; empty
    means davinci. Raises ``ConfigError`` with line numbers for anything ambiguous: a
    wrong ownership would let PortTool edit a file it must not touch.
    """
    file = Path(os.path.abspath(path))
    problems = _Problems(file)
    text = _read_text(file, "The owner list")
    content = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not content:
        problems.error("the file is empty. It needs a header row: " + ",".join(_OWNER_COLUMNS))
        problems.raise_if_any()
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(content[0]))
    header: list[str] | None = None
    entries: dict[str, OwnerEntry] = {}
    first_line: dict[str, int] = {}
    rows = iter(reader)
    while True:
        try:
            row = next(rows)
        except StopIteration:
            break
        except csv.Error as error:
            problems.error(f"line {reader.line_num} cannot be read as CSV: {error}.")
            break
        cells = [cell.strip() for cell in row]
        if not any(cells) or cells[0].startswith("#"):
            continue
        line = reader.line_num
        if header is None:
            header = [cell.lower() for cell in cells]
            _check_owner_header(header, problems)
            problems.raise_if_any()
            continue
        if len(cells) > len(header) and any(cells[len(header) :]):
            problems.error(
                f"line {line} has {len(cells)} values, but the header has {len(header)} columns."
            )
            continue
        values = dict(zip(header, cells, strict=False))
        entry = _owner_entry(values, line, problems)
        if entry is None:
            continue
        previous = entries.get(entry.swc)
        if previous is None:
            entries[entry.swc] = entry
            first_line[entry.swc] = line
        elif previous == entry:
            problems.note(
                "CFG-OWNERS-003",
                f"SWC {entry.swc} is listed twice with the same values (lines "
                f"{first_line[entry.swc]} and {line}).",
            )
        else:
            problems.error(
                f"SWC {entry.swc} is listed twice with different values (lines "
                f"{first_line[entry.swc]} and {line})."
            )
    problems.raise_if_any()
    for entry in entries.values():
        if entry.arxml_owner is not ArxmlOwner.MODEL:
            continue
        if entry.export_folder is None:
            problems.note(
                "CFG-OWNERS-004",
                f"Model-owned SWC {entry.swc} has no export_folder: PortTool cannot tell "
                "where its ARXML export lands.",
            )
        elif repo_root is not None and not _absolute(Path(repo_root), entry.export_folder).is_dir():
            problems.note(
                "CFG-OWNERS-005",
                f"The export_folder {entry.export_folder} of SWC {entry.swc} does not exist.",
            )
    return MappingProxyType(entries), tuple(problems.findings)


def _check_owner_header(header: list[str], problems: _Problems) -> None:
    for column in ("swc", "arxml_owner"):
        if column not in header:
            problems.error(
                f"the header row has no column {column!r}. Expected columns: "
                + ", ".join(_OWNER_COLUMNS)
            )
    for column in sorted(set(header)):
        if column and header.count(column) > 1:
            problems.error(f"the header row has the column {column!r} more than once.")
        elif column and column not in _OWNER_COLUMNS:
            problems.note("CFG-OWNERS-002", f"Unknown column {column!r} is ignored.")


def _owner_entry(values: Mapping[str, str], line: int, problems: _Problems) -> OwnerEntry | None:
    swc = values.get("swc", "")
    if not _IDENTIFIER.fullmatch(swc):
        problems.error(f'line {line}: "{swc}" is not a valid SWC name.')
        return None
    owner_text = values.get("arxml_owner", "").lower()
    try:
        owner = ArxmlOwner(owner_text) if owner_text else ArxmlOwner.DAVINCI
    except ValueError:
        allowed = ", ".join(member.value for member in ArxmlOwner)
        problems.error(
            f'line {line}: arxml_owner "{values.get("arxml_owner", "")}" of SWC {swc} is '
            f"not one of {allowed}."
        )
        return None
    folder = values.get("export_folder", "").replace("\\", "/").strip("/")
    return OwnerEntry(
        swc=swc,
        team=values.get("team") or None,
        arxml_owner=owner,
        authoring_tool=values.get("authoring_tool") or None,
        export_folder=folder or None,
    )


def load_locked(path: str | os.PathLike[str]) -> frozenset[str]:
    """Read locked.txt: one SWC name per line; ``#`` starts a comment. Raises ``ConfigError``."""
    file = Path(os.path.abspath(path))
    problems = _Problems(file)
    names: set[str] = set()
    for number, raw in enumerate(_read_text(file, "The lock list").splitlines(), start=1):
        name = raw.split("#", 1)[0].strip()
        if not name:
            continue
        if not _IDENTIFIER.fullmatch(name):
            problems.error(
                f'line {number}: "{name}" is not a valid SWC name. Write one SWC name per line.'
            )
            continue
        names.add(name)
    problems.raise_if_any()
    return frozenset(names)


# --------------------------------------------------------------------------- saving


def _toml_text(value: str) -> str:
    plain = all(char >= " " and char != "\x7f" for char in value)
    if "\\" in value and "'" not in value and plain:
        return f"'{value}'"  # literal string: regular expressions stay readable
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = "".join(
        char if char >= " " and char != "\x7f" else f"\\u{ord(char):04x}" for char in escaped
    )
    return f'"{escaped}"'


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return _toml_text(value)
    if isinstance(value, tuple | list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ConfigError(f"A value of type {type(value).__name__} cannot be written to TOML.")


def _path_text(path: Path, repo_root: Path) -> str:
    absolute = Path(os.path.abspath(path))
    if absolute.is_relative_to(repo_root):
        return absolute.relative_to(repo_root).as_posix() or "."
    return absolute.as_posix()


def render_config(config: ProjectConfig) -> str:
    """Return the porttool.toml text for ``config`` (owners and locked SWCs are not part of it)."""
    root = Path(os.path.abspath(config.repo_root))
    lines = [
        "# PortTool project configuration.",
        "# Relative paths are relative to the repository root. Use forward slashes.",
        f"format_version = {FORMAT_VERSION}",
        "",
        "[paths]",
    ]
    for key in (*_FILE_KEYS, *_DIR_KEYS):
        value = getattr(config, key)
        if value is not None:
            lines.append(f"{key} = {_toml_text(_path_text(value, root))}")
    if config.cache_dir is not None and config.cache_dir != default_cache_dir(root):
        lines.append(f"cache_dir = {_toml_text(_path_text(config.cache_dir, root))}")
    roots = [_path_text(path, root) for path in config.arxml_roots]
    lines.append(f"arxml_roots = {_toml_value(roots)}")
    lines.append(f"exclude_globs = {_toml_value(config.exclude_globs)}")
    for title, section in (
        ("naming", config.naming),
        ("packages", config.packages),
        ("timeouts", config.timeouts),
    ):
        lines += ["", f"[{title}]"]
        lines += [
            f"{field.name} = {_toml_value(getattr(section, field.name))}"
            for field in dataclasses.fields(section)
        ]
    lines += [
        "",
        "[generate]",
        f"modules = {_toml_value(config.generate_modules)}",
        "",
        "[pending]",
        f"file = {_toml_value(config.pending_file)}",
        f"max_age_days = {_toml_value(config.pending_max_age_days)}",
        "",
        "[limits]",
        f"large_file_bytes = {_toml_value(config.large_file_bytes)}",
        f"min_free_disk_bytes = {_toml_value(config.min_free_disk_bytes)}",
        "",
    ]
    for name in sorted(config.extras):
        lines += _render_table(name, config.extras[name])
    return "\n".join(lines)


def _render_table(name: str, table: Mapping[str, object]) -> list[str]:
    """Render one package table (and its sub-tables) of ProjectConfig.extras."""
    lines = [f"[{name}]"]
    nested: list[tuple[str, Mapping[str, object]]] = []
    for key, value in table.items():
        if isinstance(value, Mapping):
            nested.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    lines.append("")
    for key, value in nested:
        lines += _render_table(f"{name}.{key}", value)
    return lines


def save_config(config: ProjectConfig, config_file: str | os.PathLike[str] | None = None) -> Path:
    """Write porttool.toml atomically and return its path.

    The default target is ``<repo_root>/porttool.toml``. owners.csv and locked.txt are
    maintained by hand and are not written. Raises ``WriteError`` when the file cannot
    be written.
    """
    target = (
        Path(os.path.abspath(config_file))
        if config_file is not None
        else Path(os.path.abspath(config.repo_root)) / CONFIG_FILE_NAME
    )
    atomic_write_text(target, render_config(config), newline="\n")
    _logger.info("configuration written to %s", target)
    return target


# --------------------------------------------------------------------------- discovery

_SNIFF_BYTES = 8192
_ECUC_MARKER = b"ECUC-MODULE-CONFIGURATION-VALUES"


@dataclass(frozen=True, slots=True)
class DiscoveredPaths:
    """Path proposals for a new porttool.toml. Every tuple is ordered: best candidate first.

    These are proposals for the user to confirm, not facts: see the PENDING-DISCOVERY
    notes in :func:`discover_paths`.
    """

    repo_root: Path
    dpa_files: tuple[Path, ...] = ()
    dcf_files: tuple[Path, ...] = ()
    ecuc_files: tuple[Path, ...] = ()
    flat_extracts: tuple[Path, ...] = ()
    flat_maps: tuple[Path, ...] = ()
    arxml_roots: tuple[Path, ...] = ()
    gen_data_dirs: tuple[Path, ...] = ()
    findings: tuple[Finding, ...] = ()

    def propose(self) -> ProjectConfig:
        """A ``ProjectConfig`` from the best candidate of each kind; everything else default."""

        def first(paths: tuple[Path, ...]) -> Path | None:
            return paths[0] if paths else None

        gen_data = first(self.gen_data_dirs)
        excludes: tuple[str, ...] = ()
        if gen_data is not None and gen_data.is_relative_to(self.repo_root):
            excludes = (gen_data.relative_to(self.repo_root).as_posix() + "/**",)
        return ProjectConfig(
            repo_root=self.repo_root,
            dpa_file=first(self.dpa_files),
            dcf_file=first(self.dcf_files),
            arxml_roots=self.arxml_roots,
            exclude_globs=excludes,
            ecuc_file=first(self.ecuc_files),
            flat_extract=first(self.flat_extracts),
            flat_map=first(self.flat_maps),
            gen_data=gen_data,
            cache_dir=default_cache_dir(self.repo_root),
        )


@dataclass(slots=True)
class _Candidates:
    dpa: list[Path] = dataclasses.field(default_factory=list)
    dcf: list[Path] = dataclasses.field(default_factory=list)
    ecuc: list[Path] = dataclasses.field(default_factory=list)
    flat_extract: list[Path] = dataclasses.field(default_factory=list)
    flat_map: list[Path] = dataclasses.field(default_factory=list)
    gen_data: list[Path] = dataclasses.field(default_factory=list)
    arxml_dirs: list[Path] = dataclasses.field(default_factory=list)

    def add_file(self, path: Path) -> None:
        """Sort one existing file into its kind, by name and (for ECUC) by content."""
        name = path.name.lower()
        if name.endswith(".dpa"):
            _append(self.dpa, path)
        elif name.endswith(".dcf"):
            _append(self.dcf, path)
        elif name.endswith(".arxml"):
            # PENDING-DISCOVERY: the flat extract and flat map are recognised by their file
            # name only ("FlatExtract", "FlatMap"); the real project may name them differently.
            if "flatextract" in name:
                _append(self.flat_extract, path)
            elif "flatmap" in name:
                _append(self.flat_map, path)
            elif _is_ecuc_file(path):
                _append(self.ecuc, path)
            else:
                _append(self.arxml_dirs, path.parent)

    def add_dir(self, path: Path) -> None:
        # PENDING-DISCOVERY: the generated-code folder is recognised by the name "GenData".
        if path.name.lower() == "gendata":
            _append(self.gen_data, path)


def _append(target: list[Path], path: Path) -> None:
    if path not in target:
        target.append(path)


def _is_ecuc_file(path: Path) -> bool:
    """AUTOSAR standard, not DaVinci-specific: an ECUC value file holds this element."""
    try:
        with open(path, "rb") as stream:
            return _ECUC_MARKER in stream.read(_SNIFF_BYTES)
    except OSError as error:
        _logger.info("discovery: %s cannot be read (%s)", path, error)
        return False


def _paths_in_dpa(dpa_file: Path) -> list[Path]:
    """Existing files and folders that the .dpa project file mentions.

    PENDING-DISCOVERY: the element names of the .dpa format are not known yet. This reads
    the file as XML and treats every element text and attribute value as a possible path,
    relative to the folder of the .dpa or absolute; whatever exists on disk is kept. No
    element name is assumed. Raises ``ElementTree.ParseError`` if the file is not XML.
    """
    tree = ElementTree.parse(dpa_file)
    texts: list[str] = []
    for element in tree.iter():
        if element.text:
            texts.append(element.text)
        texts.extend(element.attrib.values())
    found: list[Path] = []
    for text in texts:
        candidate = text.strip().replace("\\", "/")
        if not candidate or len(candidate) > 520 or "\n" in candidate or candidate in {".", ".."}:
            continue
        if not any(char in candidate for char in "./"):
            continue
        try:
            path = _absolute(dpa_file.parent, candidate)
            exists = path.exists()
        except (OSError, ValueError):
            continue  # not a path at all (for example a URL with characters Windows rejects)
        if exists:
            _append(found, path)
    return found


def _collect_from_dpa(dpa_file: Path, candidates: _Candidates) -> None:
    """Sort what the .dpa mentions: files directly, and the files inside mentioned folders."""
    for path in _paths_in_dpa(dpa_file):
        if not path.is_dir():
            candidates.add_file(path)
        elif path.name.lower() == "gendata":
            candidates.add_dir(path)
        else:
            for child in sorted(path.iterdir()):
                if child.is_file():
                    candidates.add_file(child)


def _scan_repo(repo_root: Path, candidates: _Candidates) -> None:
    for folder, dir_names, file_names in os.walk(repo_root):
        here = Path(folder)
        kept: list[str] = []
        for name in sorted(dir_names):
            if name.startswith("."):
                continue  # .git and other tool folders
            if name.lower() == "gendata":
                candidates.add_dir(here / name)
                continue  # generated code: never a source of the model, do not descend
            kept.append(name)
        dir_names[:] = kept
        for name in sorted(file_names):
            if name.lower().endswith((".arxml", ".dcf", ".dpa")):
                candidates.add_file(here / name)


def _topmost(folders: Iterable[Path]) -> tuple[Path, ...]:
    """Drop every folder that lies below another folder of the list."""
    ordered = sorted(set(folders), key=lambda path: (len(path.parts), str(path).casefold()))
    kept: list[Path] = []
    for folder in ordered:
        if not any(folder.is_relative_to(parent) for parent in kept):
            kept.append(folder)
    return tuple(sorted(kept, key=lambda path: str(path).casefold()))


def discover_paths(
    repo_root: str | os.PathLike[str], dpa_file: str | os.PathLike[str] | None = None
) -> DiscoveredPaths:
    """Propose the paths of a new porttool.toml by reading the .dpa file and scanning the repo.

    Candidates named by the .dpa come first, then what the scan found. Nothing is
    written. Raises ``ConfigError`` only when ``repo_root`` or the given ``dpa_file``
    does not exist; everything else is a ``Finding``. Blocking (walks the repo): call it
    through ``JobContext.run_blocking``.

    PENDING-DISCOVERY, to be confirmed against docs/discovery.md:

    * how the .dpa file names the .dcf workspace, the ECUC file and the folders
      (see ``_paths_in_dpa``);
    * that the workspace ARXML files lie in the folder of the .dcf file or below it:
      that folder is proposed as the only ``arxml_roots`` entry;
    * the file-name conventions for the flat extract, the flat map and GenData.
    """
    root = Path(os.path.abspath(repo_root))
    if not root.is_dir():
        raise ConfigError(f"The repository folder {root} does not exist.", detail=str(root))
    findings: list[Finding] = []

    def note(code: str, message: str, severity: Severity = Severity.WARNING) -> None:
        findings.append(Finding(code=code, severity=severity, message=message))

    from_dpa = _Candidates()
    scanned = _Candidates()
    _scan_repo(root, scanned)

    chosen_dpa: Path | None = None
    if dpa_file is not None:
        chosen_dpa = Path(os.path.abspath(dpa_file))
        if not chosen_dpa.is_file():
            raise ConfigError(
                f"The project file {chosen_dpa} does not exist.", detail=str(chosen_dpa)
            )
    elif len(scanned.dpa) == 1:
        chosen_dpa = scanned.dpa[0]
    elif len(scanned.dpa) > 1:
        note(
            "DISC-DPA-002",
            f"{len(scanned.dpa)} .dpa project files were found. Choose one; the first is "
            "proposed and none was read.",
        )
    else:
        note("DISC-DPA-003", f"No .dpa project file was found below {root}.")

    if chosen_dpa is not None:
        _append(from_dpa.dpa, chosen_dpa)
        try:
            _collect_from_dpa(chosen_dpa, from_dpa)
        except (ElementTree.ParseError, OSError) as error:
            note(
                "DISC-DPA-001",
                f"{chosen_dpa} could not be read as XML ({error}). The proposals come "
                "from the folder scan only.",
            )

    def merged(first: list[Path], second: list[Path]) -> tuple[Path, ...]:
        result = list(first)
        for path in second:
            _append(result, path)
        return tuple(result)

    dcf_files = merged(from_dpa.dcf, scanned.dcf)
    ecuc_files = merged(from_dpa.ecuc, scanned.ecuc)
    if not dcf_files:
        note("DISC-DCF-001", f"No DaVinci Developer workspace (.dcf) was found below {root}.")
    elif len(dcf_files) > 1 and not from_dpa.dcf:
        note(
            "DISC-DCF-002",
            f"{len(dcf_files)} .dcf workspace files were found. Choose one; the first is proposed.",
        )
    if not ecuc_files:
        note("DISC-ECUC-001", f"No ECUC file was found below {root}.")
    elif len(ecuc_files) > 1 and not from_dpa.ecuc:
        note(
            "DISC-ECUC-002",
            f"{len(ecuc_files)} ECUC files were found. Choose one; the first is proposed.",
        )

    if dcf_files:
        arxml_roots: tuple[Path, ...] = (dcf_files[0].parent,)
        note(
            "DISC-ARXML-001",
            f"Proposed ARXML folder: {arxml_roots[0]} (the folder of the .dcf workspace). "
            "Confirm that every workspace ARXML file lies in it.",
            Severity.INFO,
        )
    else:
        arxml_roots = _topmost(scanned.arxml_dirs)
        if not arxml_roots:
            note("DISC-ARXML-002", f"No ARXML file was found below {root}.")
    return DiscoveredPaths(
        repo_root=root,
        dpa_files=merged(from_dpa.dpa, scanned.dpa),
        dcf_files=dcf_files,
        ecuc_files=ecuc_files,
        flat_extracts=merged(from_dpa.flat_extract, scanned.flat_extract),
        flat_maps=merged(from_dpa.flat_map, scanned.flat_map),
        arxml_roots=arxml_roots,
        gen_data_dirs=merged(from_dpa.gen_data, scanned.gen_data),
        findings=tuple(findings),
    )

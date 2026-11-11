"""Finding the executables of the Vector tools and reading their file version.

PENDING-DISCOVERY: the file names (``<Tool>.exe``) and the folders searched are the same
assumptions the discovery package makes. A path written in ``[davinci.executables]`` of
porttool.toml always wins over the search.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.davinci.settings import DaVinciSettings

EXECUTABLE_SUFFIX = ".exe"
#: Folders tried below ``developer_bin`` and ``sip_root``, in this order.
SEARCH_SUBFOLDERS: tuple[str, ...] = ("DaVinciConfigurator/Core", "Core", "")
_MAX_VERSION_BYTES = 64 * 1024 * 1024
_FIXED_INFO_SIGNATURE = b"\xbd\x04\xef\xfe"


@dataclass(frozen=True, slots=True)
class ExecutableLookup:
    tool: str
    path: Path | None
    source: str  # "porttool.toml", "developer_bin", "sip_root" or ""
    configured: str  # the text of [davinci.executables], "" when not set
    searched: tuple[str, ...]  # every place that was looked at

    @property
    def key(self) -> str:
        return self.tool.lower()


def _file_in(folder: Path, name: str) -> Path | None:
    """``folder/name`` if it is a file, comparing the name without regard to case."""
    direct = folder / name
    if direct.is_file():
        return direct
    try:
        with os.scandir(folder) as scan:
            for entry in scan:
                if entry.name.casefold() == name.casefold() and entry.is_file():
                    return Path(entry.path)
    except OSError:
        return None
    return None


def find_executable(
    tool: str, settings: DaVinciSettings, config: ProjectConfig
) -> ExecutableLookup:
    """Where the executable of ``tool`` is, or where it was looked for."""
    file_name = tool + EXECUTABLE_SUFFIX
    configured = settings.executables.get(tool, "")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = config.repo_root / path
        if path.is_file():
            return ExecutableLookup(tool, path, "porttool.toml", configured, (str(path),))
        if path.is_dir():
            found = _file_in(path, file_name)
            return ExecutableLookup(
                tool, found, "porttool.toml" if found else "", configured, (str(path / file_name),)
            )
        return ExecutableLookup(tool, None, "", configured, (str(path),))
    searched: list[str] = []
    for source, root in (("developer_bin", config.developer_bin), ("sip_root", config.sip_root)):
        if root is None:
            continue
        for sub in SEARCH_SUBFOLDERS:
            folder = root.joinpath(*sub.split("/")) if sub else root
            searched.append(str(folder / file_name))
            found = _file_in(folder, file_name)
            if found is not None:
                return ExecutableLookup(tool, found, source, "", tuple(searched))
    return ExecutableLookup(tool, None, "", "", tuple(searched))


def parse_file_version(data: bytes) -> str | None:
    """The file version (``a.b.c.d``) in the version resource of a Windows executable."""
    position = data.find(_FIXED_INFO_SIGNATURE)
    while position >= 0:
        block = data[position : position + 16]
        if len(block) == 16:
            _, struct_version, most, least = struct.unpack("<4I", block)
            if struct_version == 0x00010000:
                return f"{most >> 16}.{most & 0xFFFF}.{least >> 16}.{least & 0xFFFF}"
        position = data.find(_FIXED_INFO_SIGNATURE, position + 4)
    return None


def read_file_version(path: Path) -> str | None:
    """The file version of an executable, or None when it has none or cannot be read."""
    try:
        if path.stat().st_size > _MAX_VERSION_BYTES:
            return None
        return parse_file_version(path.read_bytes())
    except OSError:
        return None

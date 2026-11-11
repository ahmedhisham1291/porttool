"""Configuration types. CONTRACT FILE (types only).

The loader that fills these from ``porttool.toml`` (including its ``[naming]`` table),
``owners.csv`` and ``locked.txt`` lives in ``porttool/core/config_loader.py`` (Agent 1).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from porttool.core.models import ArxmlOwner


@dataclass(frozen=True, slots=True)
class OwnerEntry:
    """One row of owners.csv."""

    swc: str  # SWC short name
    team: str | None = None
    arxml_owner: ArxmlOwner = ArxmlOwner.DAVINCI
    authoring_tool: str | None = None  # e.g. "Simulink", "TargetLink"
    export_folder: str | None = None  # where the model's ARXML export lands, repo-relative


@dataclass(frozen=True, slots=True)
class NamingRules:
    """Regular expressions per element kind (naming.yaml). Empty string = no rule."""

    max_length: int = 127
    port: str = ""
    interface: str = ""
    data_type: str = ""
    runnable: str = ""
    connector: str = ""
    server_runnable_pattern: str = "{port}_{operation}"
    connector_pattern: str = "{provider}_{provider_port}_{requester}_{requester_port}"


@dataclass(frozen=True, slots=True)
class PackagePaths:
    """AUTOSAR packages for new objects. Confirmed by discovery; defaults are placeholders."""

    data_types: str = "/DataTypes"
    interfaces: str = "/PortInterfaces"
    constants: str = "/Constants"
    compu_methods: str = "/DataTypes/CompuMethods"
    constraints: str = "/DataTypes/DataConstrs"


@dataclass(frozen=True, slots=True)
class ToolTimeouts:
    workspace_check_s: float = 600.0
    script_task_s: float = 900.0
    validate_s: float = 1800.0
    generate_s: float = 3600.0
    import_s: float = 900.0
    git_s: float = 120.0


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Everything PortTool knows about one ECU repo. All paths are absolute."""

    repo_root: Path
    dpa_file: Path | None = None
    dcf_file: Path | None = None
    #: Folders (or single files) whose ARXML content is loaded into the model.
    arxml_roots: tuple[Path, ...] = ()
    #: Glob patterns, relative to repo_root, never loaded (generated folders, SIP, backups).
    exclude_globs: tuple[str, ...] = ()
    ecuc_file: Path | None = None
    flat_extract: Path | None = None
    flat_map: Path | None = None
    sip_root: Path | None = None
    developer_bin: Path | None = None
    gen_data: Path | None = None
    xsd_file: Path | None = None
    cache_dir: Path | None = None  # default: %LOCALAPPDATA%\PortTool\cache\<repo id>
    owners: Mapping[str, OwnerEntry] = field(default_factory=lambda: MappingProxyType({}))
    locked_swcs: frozenset[str] = frozenset()
    naming: NamingRules = NamingRules()
    packages: PackagePaths = PackagePaths()
    timeouts: ToolTimeouts = ToolTimeouts()
    generate_modules: tuple[str, ...] = ("Rte", "Os")
    pending_file: str = "porttool/pending.json"  # repo-relative
    pending_max_age_days: int = 14
    large_file_bytes: int = 50 * 1024 * 1024  # above this, the reader streams the file
    min_free_disk_bytes: int = 2 * 1024 * 1024 * 1024
    #: Raw package tables of porttool.toml, read by the package that owns them:
    #: [davinci], [writer], [pipeline], [gui], [reader], [workbook], [discovery].
    extras: Mapping[str, Mapping[str, object]] = field(default_factory=lambda: MappingProxyType({}))

    def extra(self, table: str) -> Mapping[str, object]:
        """The raw [table] of porttool.toml, or an empty mapping."""
        return self.extras.get(table, MappingProxyType({}))

    def owner_of(self, swc_name: str) -> ArxmlOwner:
        if swc_name in self.locked_swcs:
            return ArxmlOwner.LOCKED
        entry = self.owners.get(swc_name)
        return entry.arxml_owner if entry is not None else ArxmlOwner.DAVINCI

    def team_of(self, swc_name: str) -> str | None:
        entry = self.owners.get(swc_name)
        return entry.team if entry is not None else None

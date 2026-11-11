"""Signs that DaVinci Developer or DaVinci Configurator has the workspace or project open.

Two signs, both only hints: a GUI process is running (it may have another project
open), or a lock file lies next to the .dcf or .dpa (it may be left over from a crash).
PENDING-DISCOVERY: the process names and the lock file names are guesses; both are
settings (``[davinci] gui_process_names`` and ``lock_file_globs``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import psutil

from porttool.core.config import ProjectConfig
from porttool.core.results import Finding, Severity
from porttool.davinci.settings import load_settings

ProcessLister = Callable[[], Iterable[tuple[str, int]]]
MAX_LOCK_FILES = 20


def running_processes() -> list[tuple[str, int]]:
    """(name, pid) of every process this user may see."""
    found: list[tuple[str, int]] = []
    for process in psutil.process_iter(["name", "pid"]):
        name = process.info.get("name")
        pid = process.info.get("pid")
        if name and pid is not None:
            found.append((str(name), int(pid)))
    return found


def _same_program(name: str, wanted: str) -> bool:
    left, right = name.casefold(), wanted.casefold()
    return left == right or left.removesuffix(".exe") == right.removesuffix(".exe")


def davinci_gui_findings(
    config: ProjectConfig, *, processes: ProcessLister | None = None
) -> tuple[Finding, ...]:
    """Findings when a DaVinci GUI seems to have the workspace or the project open.

    An empty result means no sign was found. ``processes`` replaces the process scan in
    tests.
    """
    settings, _ = load_settings(config)
    findings: list[Finding] = []
    try:
        listing = list((processes or running_processes)())
    except (psutil.Error, OSError) as error:
        listing = []
        findings.append(
            Finding(
                "DVR-GUI-SCAN-FAILED",
                Severity.WARNING,
                f"The running programs could not be listed ({type(error).__name__}: "
                f"{error}), so PortTool cannot tell whether DaVinci Developer or DaVinci "
                "Configurator is open. Close both before you continue.",
            )
        )
    for name, pid in listing:
        if any(_same_program(name, wanted) for wanted in settings.gui_process_names):
            findings.append(
                Finding(
                    "DVR-GUI-RUNNING",
                    Severity.WARNING,
                    f"{name} is running (pid {pid}). The workspace or the project may be "
                    "open in it: save your work and close it before PortTool changes "
                    "files, otherwise changes can be lost.",
                    object_path=name,
                )
            )
    seen: set[Path] = set()
    for what, anchor in (("workspace", config.dcf_file), ("project", config.dpa_file)):
        if anchor is None or not anchor.parent.is_dir():
            continue
        for pattern in settings.lock_file_globs:
            try:
                matches = sorted(anchor.parent.glob(pattern))
            except (OSError, ValueError):
                continue  # a pattern this folder cannot be searched with
            for path in matches:
                if path in seen or path == anchor or not path.is_file():
                    continue
                if len(seen) >= MAX_LOCK_FILES:
                    break
                seen.add(path)
                findings.append(
                    Finding(
                        "DVR-GUI-LOCKFILE",
                        Severity.WARNING,
                        f"The lock file {path.name} lies next to the {what} file "
                        f"{anchor.name}. A DaVinci tool may have the {what} open, or it "
                        "crashed earlier. Close the tool; if none is open, delete the "
                        "lock file.",
                        file=str(path),
                    )
                )
    return tuple(findings)

"""The one place that would register a new ARXML file in the DaVinci workspace index."""

from __future__ import annotations

from porttool.core.config import ProjectConfig
from porttool.core.results import Finding, Severity
from porttool.writer import codes


def register_new_file_in_dcf(config: ProjectConfig, rel_path: str) -> tuple[Finding, ...]:
    """Called after a new ARXML file was written.

    PENDING-DISCOVERY: the format of the ``.dcf`` file is not known, so nothing is
    written to it. The caller gets a warning that names the file to add by hand (or
    with DaVinci Developer). Replace the body once ``porttool discover`` has shown how
    the ``.dcf`` lists its files; record the ``.dcf`` in the journal before editing it.
    """
    dcf = config.dcf_file.name if config.dcf_file is not None else "the .dcf file"
    return (
        Finding(
            codes.DCF_NOT_UPDATED,
            Severity.WARNING,
            f"{rel_path} was created, but {dcf} was not updated: add the file to the "
            "DaVinci Developer workspace.",
            file=rel_path,
        ),
    )

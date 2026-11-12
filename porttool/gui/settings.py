"""Settings of the web interface: the ``[gui]`` table of porttool.toml."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, fields

from porttool.core.config import ProjectConfig

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GuiSettings:
    """Every value has a safe default; a wrong value in porttool.toml is logged and ignored."""

    #: Fixed port of the server (0: pick a free one). Used when ``--port`` is not given.
    port: int = 0
    #: Largest accepted upload request (all workbooks of one import together), in megabytes.
    max_upload_mb: int = 50
    #: Most workbooks in one import.
    max_upload_files: int = 20
    #: How often the server asks the service whether files changed on disk, in seconds.
    stale_check_interval_s: float = 2.0
    #: Most names listed in the "Compatible partner ports" column of the Unconnected table.
    partner_limit: int = 10
    #: Most requests accepted from the request form in one validation.
    max_form_rows: int = 5000

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


_MINIMUM: Mapping[str, float] = {
    "port": 0,
    "max_upload_mb": 1,
    "max_upload_files": 1,
    "stale_check_interval_s": 0.05,
    "partner_limit": 0,
    "max_form_rows": 1,
}


def settings_from(table: Mapping[str, object]) -> GuiSettings:
    """Read the raw ``[gui]`` table. Unknown keys and unusable values are logged, not fatal."""
    defaults = GuiSettings()
    values: dict[str, int | float] = {}
    known = {field.name: field.type for field in fields(GuiSettings)}
    for key, raw in table.items():
        if key not in known:
            _logger.warning("[gui] %s is not a known setting and is ignored", key)
            continue
        default = getattr(defaults, key)
        wants_int = isinstance(default, int)
        usable = isinstance(raw, int) if wants_int else isinstance(raw, int | float)
        if isinstance(raw, bool) or not usable:
            _logger.warning("[gui] %s = %r is not a number; using %r", key, raw, default)
            continue
        assert isinstance(raw, int | float)
        if raw < _MINIMUM[key] or (key == "port" and raw > 65535):
            _logger.warning("[gui] %s = %r is out of range; using %r", key, raw, default)
            continue
        values[key] = raw
    return GuiSettings(
        port=int(values.get("port", defaults.port)),
        max_upload_mb=int(values.get("max_upload_mb", defaults.max_upload_mb)),
        max_upload_files=int(values.get("max_upload_files", defaults.max_upload_files)),
        stale_check_interval_s=float(
            values.get("stale_check_interval_s", defaults.stale_check_interval_s)
        ),
        partner_limit=int(values.get("partner_limit", defaults.partner_limit)),
        max_form_rows=int(values.get("max_form_rows", defaults.max_form_rows)),
    )


def settings_of(config: ProjectConfig | None) -> GuiSettings:
    """Settings of the open repo, or the defaults when no repo is open."""
    if config is None:
        return GuiSettings()
    return settings_from(config.extra("gui"))

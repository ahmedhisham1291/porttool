"""``XlsxWorkbookIO``: the workbook package behind the ``WorkbookIO`` contract."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.interfaces import ImportedWorkbooks
from porttool.core.models import Model
from porttool.core.results import Finding, RequestResult
from porttool.core.sheets import WorkbookMeta
from porttool.workbook.exporter import ExportOptions, export_workbook
from porttool.workbook.importer import import_workbooks


class XlsxWorkbookIO:
    """Implements ``porttool.core.interfaces.WorkbookIO``.

    Both methods block: the engine calls them through ``JobContext.run_blocking``. The
    object keeps no state between calls, so one instance can serve several threads.
    """

    def __init__(
        self,
        options: ExportOptions | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._options = options or ExportOptions()
        self._sleep = sleep

    @property
    def options(self) -> ExportOptions:
        return self._options

    def export(
        self,
        model: Model,
        config: ProjectConfig,
        target: Path,
        meta: WorkbookMeta,
        results: Sequence[RequestResult] = (),
    ) -> tuple[Finding, ...]:
        """Write the workbook atomically. Problems with the target come back as findings."""
        return export_workbook(model, config, target, meta, results, self._options, self._sleep)

    def import_rows(self, files: Sequence[Path], current: WorkbookMeta) -> ImportedWorkbooks:
        """Read the request rows of ``files``. Problems with a file come back as findings."""
        return import_workbooks(files, current, self._options.retry_delays_s, self._sleep)

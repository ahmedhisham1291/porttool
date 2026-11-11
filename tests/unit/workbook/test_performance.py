"""Export and import of a workbook with more than 50,000 rows, against the budgets."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from porttool.core.sheets import REQUEST_SHEETS
from porttool.workbook import XlsxWorkbookIO
from porttool.workbook.rows import build_all_rows, build_labels
from tests.helpers.sample_model import build_sample_config
from tests.unit.workbook.helpers import make_meta
from tests.unit.workbook.large_model import build_large_model

EXPORT_BUDGET_S = 10.0
IMPORT_BUDGET_S = 5.0


@pytest.mark.slow
def test_large_workbook_export_and_import_stay_inside_the_budgets(tmp_path: Path) -> None:
    model = build_large_model()
    assert len(model.ports) == 20_200
    assert len(model.connectors) == 15_000
    assert len(model.access_points) == 15_000
    config = build_sample_config(tmp_path)
    meta = make_meta(model)
    sheets = build_all_rows(model, config, (), build_labels(model), partner_limit=10)
    row_count = sum(len(data.rows) for data in sheets.values())
    assert row_count >= 50_000

    workbook_io = XlsxWorkbookIO()
    target = tmp_path / "large.xlsx"
    started = time.perf_counter()
    findings = workbook_io.export(model, config, target, meta)
    export_s = time.perf_counter() - started
    assert findings == ()

    started = time.perf_counter()
    imported = workbook_io.import_rows([target], meta)
    import_s = time.perf_counter() - started
    assert imported.rows == () and imported.findings == ()
    assert imported.metas == ((str(target), meta),)

    request_rows = sum(len(sheets[sheet].rows) for sheet in REQUEST_SHEETS)
    print(
        f"\nlarge workbook: {row_count} rows ({request_rows} on request sheets), "
        f"{target.stat().st_size / 1e6:.2f} MB, export {export_s:.2f} s, import {import_s:.2f} s"
    )
    assert export_s < EXPORT_BUDGET_S, f"export took {export_s:.2f} s"
    assert import_s < IMPORT_BUDGET_S, f"import took {import_s:.2f} s"

# HANDOFF: porttool/workbook (Agent 3)

Branch `agent/workbook`. Owned folders: `porttool/workbook/`, `tests/unit/workbook/`.
Nothing outside these folders was changed.

**The exported workbook was never opened in real Excel.** Everything below was verified
by reading the file back with openpyxl and by inspecting the XML inside the .xlsx. Section
"What a human must check in Excel" lists what that cannot prove.

## Status

Done
- Export (XlsxWriter) of all 13 sheets in the required order, locking, sheet protection
  without password, frozen header, autofilter, column widths, dropdowns that reference
  defined names on the hidden Lists sheet, README, Meta, Results, 500 blank unlocked rows
  per request sheet (configurable), atomic save, byte-identical output for the same input.
- Import (openpyxl, `read_only=True, data_only=True`) of one or more workbooks: columns
  matched by header text, `RawRequestRow` per request row, `RowRef` on every row and
  finding, Meta sheet returned and compared with the current project.
- Round trip test: export, scripted edit with openpyxl, import, exact comparison.
- Every corner case of the task has a named test (list below).
- `register_cli(app)` for click with the command `workbook-check`, tested with
  `click.testing.CliRunner`.

Not done
- No export command in this package. An export needs a loaded `Model`, and loading is
  the reader's job; the pipeline package has to own that command.
- Nothing was tried on Windows or in Excel, LibreOffice or Google Sheets.
- Progress reporting and cancellation: the `WorkbookIO` contract passes no `JobContext`.

## Public API

```python
from porttool.workbook import XlsxWorkbookIO, ExportOptions, codes, register_cli

io = XlsxWorkbookIO()  # implements porttool.core.interfaces.WorkbookIO
findings = io.export(model, config, target, meta, results=())  # -> tuple[Finding, ...]
imported = io.import_rows(files, current)  # -> ImportedWorkbooks
```

- Both methods block; call them through `JobContext.run_blocking`. The object holds no
  state between calls, so one instance may be used from several threads.
- `XlsxWorkbookIO(options=ExportOptions(...), sleep=time.sleep)`.
  `ExportOptions`: `blank_rows=500`, `partner_limit=10`, `retry_delays_s=(0.1, 0.2, 0.4)`,
  `max_rows=1_048_576`, `max_cell_chars=32_767` (the last two are lowered only in tests).
- Neither method raises for an expected problem. `WorkbookError` is raised only when
  XlsxWriter refuses a call or raises, or when `sheets.py` names a dropdown list this
  package cannot fill (contract drift).
- `register_cli(app: click.Group)` adds `workbook-check FILES... [--baseline-commit X]
  [--model-hash X] [--json]`; exit code 1 when a finding of severity error was reported.
- Modules: `layout.py` (sheet order, RowID patterns, defined-name prefix), `rows.py`
  (model to rows, pure), `lists.py` (dropdown content), `readme.py`, `exporter.py`,
  `importer.py`, `io.py`, `cli.py`, `codes.py`.

### Export behaviour
- An ERROR finding means no file was written and an existing target is unchanged.
- The workbook is built in memory, written to `<stem>.<random>.tmp` in the target folder,
  flushed, fsynced, then moved with `os.replace`. A `PermissionError` on the move is
  retried three times (0.1, 0.2, 0.4 s), then reported. The temporary file is removed on
  every path; if that fails, a warning names it.
- The export never creates the target folder.
- Blank rows are unlocked in every column, RowID included (task wording). A pasted RowID
  is caught on import (WBK-ROW-004).
- Unlocked cells have the number format Text (`@`), so that Excel keeps `007`, `1-2`,
  `MAR1` and `=x` as typed. Not verified in Excel.
- Dropdowns of closed lists (actions, directions, kinds, levels, categories, Y/N) reject
  other values (error style stop). Dropdowns filled from the model (SWCs, prototypes,
  interfaces, data types, base types, units, tasks, compositions) only show an
  information box, because a request may name an object that another row of the same
  workbook creates.
- Defined names are `pt_<list name>`, e.g. `pt_swcs` = `Lists!$M$2:$M$5`. An empty list
  points at one blank cell.
- Text is written with `write_string`. Numbers (Partners, Period (ms), limits, factor,
  offset, array size) are written as numbers.

### Cell text formats (the validator sees these texts in `RawRequestRow.values`)
| Cell | Text |
| --- | --- |
| SWC, Interface, Data type, Base type, Unit, Composition | short name; the absolute path (starts with `/`) when the short name is not unique in the model; a dangling reference shows its path |
| Provider SWC, Requester SWC (Connections), SWC (Unconnected) | prototype short name; empty for the composition's own port |
| Runnable, Port | short name |
| Enumeration values | `0=OFF;1=ON` |
| Record elements | `name:Type;name2:Type2` |
| Init value | `element=value;element2=value2`, only elements that have an init value |
| Queued, Connected | `Y` / `N`; Queued is empty on operation rows |
| Period (ms) | `period_s * 1000`, rounded to 6 decimals |
| Unit | the data type's unit, else the unit of its compu method |
| Compatible partner ports | `Proto.Port; Proto2.Port2 (+N more)`: same composition, same interface, opposite direction (PR matches all), at most 10 names |
| Results: RowID | `RequestResult.request_id` (see contract change request 1) |
| Results: Reason | finding message plus ` [file.xlsx, row N]` when the result has a RowRef |

Row order: Components, DataTypes, Interfaces by (name, path); InterfaceElements by
interface, then declaration order; Ports by (SWC name, SWC path, port name); access
points and triggers by (SWC, runnable, ...); Connections by (composition, connector
name); Unconnected by (prototype, port). The order does not depend on the order in which
the model was built.

### Import behaviour
- A request row is a row with an Action, or a row without RowID that has content in a
  layout column. A new row without Action is returned with `action=""` and WBK-ROW-001.
- `values` always holds every column key of the sheet, in layout order; an empty or
  missing cell is `""`. `values["action"]` is upper-cased like `action`.
- **A row with an ERROR finding is still returned.** The finding carries the same
  `RowRef`. The consumer must reject rows whose `RowRef` appears in an error finding.
- Rows come in the order: file, then sheet in `REQUEST_SHEETS` order, then row number.
- Sheet names are matched trimmed and case-insensitive; `RawRequestRow.sheet` and
  `RowRef.sheet` hold the canonical name. `RowRef.file` is `str(path)` as given.
- The header must be in row 1. Without the RowID or the Action column a sheet is skipped.
- `metas` holds one entry per workbook that could be opened (`None` when its Meta sheet
  is missing or incomplete). Lock files, wrong formats and damaged files have no entry.
- A damaged file contributes no rows at all, also not from sheets read before the damage.
- The file is read into memory once and closed, so the import holds no lock on it.
- Formulas and merged cells are invisible to openpyxl's read-only mode with
  `data_only=True`. The importer therefore also reads the raw XML of each request sheet:
  a regular-expression search, and an `iterparse` pass only when a formula is present.
- The stored sheet dimension is not trusted (`reset_dimensions()`).

## Finding codes (all in `porttool/workbook/codes.py`)
| Code | Severity | Meaning |
| --- | --- | --- |
| WBK-EXPORT-001 | error | target folder missing or not a folder |
| WBK-EXPORT-002 | error | no temporary file can be created in the folder (read-only) |
| WBK-EXPORT-003 | error | target open in Excel or read-only (`PermissionError` on replace) |
| WBK-EXPORT-004 | error | disk full (`ENOSPC`) |
| WBK-EXPORT-005 | error | other operating-system error while saving |
| WBK-EXPORT-006 | error | a sheet needs more rows than Excel allows; nothing written |
| WBK-EXPORT-007 | warning | fewer blank rows than configured (row limit) |
| WBK-EXPORT-008 | warning | cell text above 32,767 characters was cut |
| WBK-EXPORT-009 | error | target is not `*.xlsx` |
| WBK-EXPORT-010 | warning | temporary file could not be removed |
| WBK-FILE-001 | error | file missing, or it is a folder |
| WBK-FILE-002 | info | Excel lock file `~$name.xlsx` skipped |
| WBK-FILE-003 | error | `.xls`, `.xlsm`, `.csv` or any other extension |
| WBK-FILE-004 | error | `PermissionError` while reading, after three retries |
| WBK-FILE-005 | error | password-protected (OLE container or encrypted zip entries) |
| WBK-FILE-006 | error | not a zip, damaged zip, damaged content |
| WBK-FILE-007 | info | same file given twice |
| WBK-FILE-008 | error | other operating-system error while reading |
| WBK-SHEET-001 | warning | request sheet missing |
| WBK-SHEET-002 | warning | sheet outside the layout, ignored |
| WBK-SHEET-003 | error | unknown sheet whose header row matches a missing sheet: renamed, not imported |
| WBK-SHEET-004 | warning | two sheets with the same name apart from case and spaces |
| WBK-SHEET-005 | warning | sheet could not be checked for formulas and merged cells |
| WBK-COLUMN-001 | error | required column missing (RowID or Action missing: sheet skipped) |
| WBK-COLUMN-002 | warning | unknown column, ignored |
| WBK-COLUMN-003 | warning | header twice, first one read |
| WBK-COLUMN-004 | warning | Requester or Comment column missing |
| WBK-ROW-001 | error | new row with content but no Action |
| WBK-ROW-002 | error | Action outside ADD, MODIFY, DELETE |
| WBK-ROW-003 | error | RowID does not match `<prefix of the sheet>-<12 hex digits>` |
| WBK-ROW-004 | error | RowID used twice on one sheet of one workbook |
| WBK-ROW-005 | info | new row equal to an existing row of the same sheet in every data column |
| WBK-CELL-001 | error | Excel turned the entry into a date or time |
| WBK-CELL-002 | warning | formula; the stored result was used |
| WBK-CELL-003 | error | formula without stored result; the cell reads as empty |
| WBK-CELL-004 | error / warning | merged cells (error when a request row is affected) |
| WBK-META-001 | warning | Meta sheet missing or incomplete |
| WBK-META-002 | warning | other baseline commit or model hash |
| WBK-META-003 | warning | older layout version |
| WBK-META-004 | error | newer layout version |

## Corner cases and their tests
| Corner case | Test |
| --- | --- |
| open in Excel, read | `test_import.py::test_workbook_open_in_excel_on_read_returns_finding_after_retries`, `::test_locked_workbook_is_read_once_it_is_released` |
| open in Excel, write | `test_export.py::test_workbook_open_in_excel_on_write_returns_finding_and_keeps_the_old_file`, `::test_locked_target_is_retried_and_the_export_succeeds_when_it_is_released` |
| `~$` lock file | `test_import.py::test_excel_lock_file_in_the_list_is_skipped` |
| .xls, .xlsm, .csv | `::test_xls_xlsm_and_csv_are_rejected_with_a_clear_finding` |
| password-protected or encrypted | `::test_password_protected_or_encrypted_file_returns_finding`, `::test_zip_with_encrypted_entries_returns_finding` |
| corrupt zip | `::test_corrupt_zip_returns_finding_and_nothing_is_imported` (10 kinds of damage), `::test_rows_read_before_the_damage_showed_are_not_returned` |
| missing sheet | `::test_missing_sheet_returns_finding_and_the_other_sheets_are_imported` |
| renamed sheet | `::test_renamed_sheet_is_reported_and_not_imported` |
| reordered, deleted, added columns | `::test_reordered_columns_are_matched_by_header`, `::test_deleted_column_returns_finding_and_its_cells_read_as_empty`, `::test_sheet_without_row_id_or_action_column_is_not_imported`, `::test_added_column_is_ignored_with_finding` |
| headers with spaces or other case | `::test_headers_with_extra_spaces_or_other_case_are_matched` |
| hidden and filtered rows | `::test_hidden_rows_and_filtered_rows_are_still_imported` |
| merged cells | `::test_merged_cells_are_reported` |
| empty rows between data | `::test_empty_rows_between_data_are_skipped_and_row_numbers_stay_right` |
| leading and trailing spaces | `::test_leading_and_trailing_spaces_are_stripped` |
| non-breaking spaces | `::test_non_breaking_spaces_are_normalised` |
| numbers as text, text as numbers | `::test_numbers_stored_as_text_and_text_stored_as_numbers` |
| auto-converted dates | `::test_value_auto_converted_to_a_date_returns_finding` |
| formulas | `::test_formula_without_cached_value_returns_finding_and_reads_as_empty`, `::test_formula_with_cached_value_is_used_and_reported` |
| Action outside the list | `::test_action_outside_the_list_returns_finding_and_the_row` |
| same RowID twice | `::test_same_row_id_twice_returns_finding` |
| new row identical to an existing object | `::test_new_row_identical_to_an_existing_object_is_returned_with_a_note` |
| workbook written by openpyxl | `::test_workbook_written_by_openpyxl_itself_is_imported` |
| older layout_version | `::test_workbook_with_an_older_layout_version_returns_finding_and_is_imported` |
| more rows than Excel allows | `test_export.py::test_more_rows_than_excel_allows_returns_finding_and_writes_nothing` |
| cell above 32,767 characters | `::test_cell_above_32767_characters_is_truncated_with_finding` |
| non-ASCII names | `::test_non_ascii_names_are_written_unchanged`, `test_roundtrip.py::test_non_ascii_names_and_leading_formula_characters_survive_the_round_trip` |
| leading = + - @ | `test_export.py::test_a_leading_formula_character_in_a_name_stays_text` |
| export folder missing or read-only | `::test_export_folder_missing_returns_finding`, `::test_export_folder_read_only_returns_finding` |
| disk full | `::test_disk_full_during_save_returns_finding_and_removes_the_partial_file`, `::test_disk_full_while_flushing_to_disk_returns_finding` |
| round trip | `test_roundtrip.py::test_round_trip_returns_exactly_the_edited_rows` |

How honest the simulations are:
- The row limit is tested with `ExportOptions(max_rows=6)`, not with a million objects.
- The 32,767-character limit is tested with the real limit.
- The encrypted file is 8 bytes of OLE signature plus zeros, not a workbook encrypted
  by Excel. No tool to encrypt one is installed here.
- "Formula with a cached value" is an openpyxl-written formula cell whose `<v>` element
  was patched into the XML, because openpyxl never writes a cached result.
- "Open in Excel", "read-only folder" and "disk full" are monkeypatched `PermissionError`
  and `OSError(ENOSPC)` at the three seams `_open_temp`, `_replace`, `_read_bytes`.
- "Written by openpyxl" stands in for LibreOffice and Google Sheets. No file from those
  programs was imported.

## How to run the tests

```
python3 -m pytest tests/unit/workbook -q
mypy --strict porttool/workbook tests/unit/workbook
ruff check porttool/workbook tests/unit/workbook
```

Tails, last run on this branch (Linux sandbox, Python 3.13.18):

```
$ python3 -m pytest tests/unit/workbook -q
..............................................s......................... [ 48%]
........................................................................ [ 97%]
...                                                                      [100%]
(pyproject already sets -q, so the count line is suppressed; with -o addopts="":
 146 passed, 1 skipped in 44.58s)

$ mypy --strict porttool/workbook tests/unit/workbook
Success: no issues found in 20 source files

$ ruff check porttool/workbook tests/unit/workbook
All checks passed!
```

The skipped test is `test_read_only_target_file_returns_finding_on_windows`
(`@pytest.mark.windows_only`). The whole repository suite (`python3 -m pytest tests -q`)
also passes. The performance test is marked `slow` and takes about 8 s.

Coverage: pytest-cov is not installed. One run of
`python3 -m trace --count --missing --module pytest tests/unit/workbook -m "not slow"`
reported 6 lines of `porttool/workbook` as never executed: the descriptor clean-up when
`os.fdopen` fails (`exporter.py`), a damaged compressed stream that shows first in the
raw XML scan (`importer.py`), and the Windows branch for a folder opened as a file (a
test for that branch was added afterwards). `trace` wrote no report for `layout.py` and
`__init__.py`; I did not find out why, so I claim no percentage for the package.

## Measured timings

Large synthetic model (`tests/unit/workbook/large_model.py`): 20,200 ports, 15,000
connectors, 15,000 access points, 2,500 interfaces, 400 SWCs. The workbook has 57,701
data rows (57,100 on request sheets) and is 3.14 MB. Linux sandbox, 2 cores, one run.

| Configuration | Export | Import | Budget |
| --- | --- | --- | --- |
| sandbox as it is (defusedxml 0.7.1 installed) | 3.09 s | 3.37 s | 10 s / 5 s |
| `OPENPYXL_DEFUSEDXML=False` | 2.99 s | 2.65 s | 10 s / 5 s |

Both budgets are met here. The numbers were not measured on Windows.

openpyxl switches to a pure-Python XML parser when the package `defusedxml` is
importable. defusedxml is not among the allowed runtime dependencies, so the second row
is the configuration of the product. If someone installs defusedxml on the target, the
import gets about 30 percent slower.

Where the time goes (earlier profile and three variants, same machine):
- Export: about 625,000 cells. Shares in the profile: XlsxWriter's XML assembly about
  45 percent, the `write_*` calls about a third, building the rows from the model about
  15 percent (0.4 s without the profiler).
- Import: openpyxl is nearly all of it: parsing about 618,000 cells and a shared-strings
  table with about 103,000 strings. The importer's own row handling is a few percent.
- The 500 blank rows cost about 0.12 s on export and 0.2 s on import (7 sheets, 39,000
  empty cells). With 5,000 blank rows: export 3.95 s, import 4.61 s (defusedxml on).
- Protection itself costs nothing measurable. What costs is the unlocked Action,
  Requester and Comment cell on each existing row: 165,000 explicit empty cells,
  about 0.46 s on export and 0.63 s on import. They could be replaced by a column format,
  but then those three columns would be coloured and unlocked down to row 1,048,576. I
  kept the explicit cells because both budgets are met.

## What a human must check in Excel

1. The file opens without a repair prompt (sample export and the 57,000-row export).
2. Sheet order; Lists and Meta are hidden; README is the active sheet and reads well.
3. Protection: a locked cell refuses typing; Action, Requester and Comment of an existing
   row accept it; every cell of the 500 blank rows accepts it; the row after them refuses.
4. Review > Unprotect Sheet works without asking for a password.
5. Dropdowns: every column with a list shows the arrow and the entries of the Lists
   sheet, also for lists far above 255 characters (2,500 interfaces in the large file).
   Closed lists refuse another value; model lists show an information box and accept it.
6. The dropdown of an empty list (Unit in the sample model) does not block typing.
7. Autofilter arrows work on the protected sheet. Sorting is expected to be refused by
   Excel because the range holds locked cells.
8. The header row stays frozen; column widths are sensible; columns can be resized.
9. Text format: type `007`, `1-2`, `MAR1`, `=1+1` into a blank row; each must stay as
   typed. Then import the file and compare.
10. No green "number stored as text" triangles on the request sheets.
11. Copy a whole existing row into a blank row, set Action, import: WBK-ROW-004 expected.
12. Save from Excel (Excel rewrites the whole file) and import: same rows as after a
    scripted edit. Repeat with LibreOffice and with a Google Sheets download.
13. Filter a sheet, hide some rows, save, import: hidden rows are imported.
14. Names that start with `=`, `+`, `-` or `@` show as text.
15. Scrolling and filtering in the 57,000-row workbook is usable; a dropdown with
    thousands of entries is usable.
16. Password-protect a workbook in Excel (File > Info > Encrypt) and import it:
    WBK-FILE-005 expected.
17. Import a workbook while it is open in Excel: note what is read (the last saved
    state is expected) and whether the `~$` lock file appears in the file dialog.

## Unverified on Windows

- `os.replace` onto a file that Excel has open raises `PermissionError`: assumed, only
  simulated. Same for a read-only target file (test marked `windows_only`, skipped here).
- Reading a workbook that Excel has open: Excel normally allows reading, so the
  `PermissionError` path may be rare. Only simulated.
- `ENOSPC` is the errno of a full disk on Windows too: assumed from CPython's error
  mapping, only simulated.
- A read-only or missing folder was simulated by raising from `_open_temp`; the tests
  here run as root, where real permissions do not bite.
- `tempfile.mkstemp` plus `os.replace` inside OneDrive-synced folders and on network
  shares.
- Paths longer than 260 characters: no special handling.
- Duplicate detection of input files uses `os.path.normcase`, which is case-insensitive
  only on Windows.
- The three retry waits were chosen, not measured against a real sharing violation.
- All timings.

## PENDING-DISCOVERY assumptions

None. This package reads the `Model` and writes Excel; nothing DaVinci-specific is in it.

## Contract change requests

1. **Results sheet.** `RequestResult` has no RowID, and the Results layout has no column
   for the request id, the file and the row. Workaround in place: the RowID cell shows
   `request_id`, and ` [file.xlsx, row N]` is appended to Reason. Wanted: `row_id` on
   `RequestResult`, and the columns Request, File, Row on `Sheet.RESULTS`.
2. **Cell text formats are not part of the contract.** The validator has to parse the
   texts in the table above (init values, name or path, prototype names, Y/N). If it also
   wants to compare the locked cells of an existing row with the model, it needs exactly
   my formatting. Wanted: the formats in `sheets.py`, or `rows.py` moved into core.
3. **MODIFY cannot carry new values.** Only Action, Requester and Comment are editable on
   an existing row, so a MODIFY on that row says "modify" without saying what. Either a
   MODIFY is a new row without RowID, or some columns need `editable=True`.
4. **`Column` has no "required" and no "computed" flag.** I treat everything except
   Requester and Comment as required. Interface kind, Connected and Partners are computed
   and nobody fills them in a new row; the validator should not expect them.
5. **No `JobContext` in `WorkbookIO`.** Export and import of 57,000 rows take about 3 s
   each without progress and without a cancel point.
6. **Unconnected: header "Port RowID".** `rowid.py` defines a `UC-` id over
   `<prototype path>|<port path>`. I follow `rowid.py`; the header suggests the `PT-` id.
7. **Rows with ERROR findings are returned.** Please state in `ImportedWorkbooks` whether
   the consumer or the workbook package drops them. Today: the consumer.
8. **Sheet order.** `Sheet` lists Results after Meta; the task wants Results before
   Lists. The order lives in `porttool/workbook/layout.py::SHEET_ORDER`.
9. **Atomic write helper.** core has no file utilities yet. `exporter._save` does the
   temporary file, fsync, `os.replace` and the retry itself; replace it with the core
   helper once it exists.

## Decisions I took where the task was silent

- "Connected" and "Partners" count connectors at prototypes of the type only, as the
  task says. A composition's own port that is delegated inwards therefore shows N / 0.
- The `swcs` list holds every component, compositions included. The enum lists hold
  every member, also `other`.
- The component owner is the model's value unless that is `davinci`; then
  `config.owner_of()` decides. The team is the model's value, else `config.team_of()`.
- A cell that Excel turned into a date is returned as ISO text together with the error.
- A formula with a stored result is a warning, without one an error.
- A renamed sheet is reported, not imported under its guessed name.
- A stale baseline and an older layout are warnings; a newer layout is an error. Rows
  are returned in all three cases.
- The CLI uses click, not Typer (decision of the coordinator during the work). The
  package itself imports only openpyxl, XlsxWriter, click, the standard library and
  `porttool.core`.

## Open questions

- Should the import drop the explicit dependence on openpyxl's parser choice by setting
  `OPENPYXL_DEFUSEDXML`? I did not touch process-wide settings.
- Workbooks come from colleagues, but they are still files from outside. Without
  defusedxml the XML is parsed by expat with its built-in limits. Is that enough?
- Should the README sheet be translated?

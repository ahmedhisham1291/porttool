# porttool/gui hand-off (Agent 8)

Local web interface: an HTTP server on 127.0.0.1 (standard library only) plus one page
of plain HTML, CSS and JavaScript. No framework, no CDN, no build step, no external
request. Screenshots of the result: `docs/ports_table.png`, `docs/dry_run_results.png`.

## Status

Done
- Server with every endpoint of the task, security checks, table cache, uploads, job starts.
- Page: header, banners, job strip, setup view, one tab per table (virtualised), Requests
  tab (export, import, request form, dry-run results, apply).
- `porttool gui [--repo PATH] [--port N] [--no-browser]`.
- Fake service (`tests/unit/gui/fake_service.py`) with sample, 20,000-port and hostile-name models.
- 187 tests pass, 1 skipped (the screenshot writer, which runs only on request).
  Every corner case of the task has a named test (list below).

Not done, or limited
- Never run against the real service: `porttool.pipeline` was not in this worktree. All
  behaviour is tested against the fake. See "Assumptions about the real service".
- JavaScript has no unit tests (as the task says) and no coverage figure. It is exercised
  only through the 33 Playwright tests.
- Drag and drop of workbooks is implemented but not exercised by a test (Playwright sets
  files through the file picker). The file-picker path is tested.
- The request form creates ADD requests only. MODIFY and DELETE need the workbook.
- No "change repo" button. To open another repo, restart with `--repo`.
- Reloading the browser page (F5) drops the session token, because the task asks for the
  token to be held in memory only. The page then explains how to reopen it. See
  "Decisions to confirm" item 1.

## Public API

```python
from porttool.gui import serve, run_forever, ServerHandle, GuiSettings, GuiError, TOKEN_HEADER

handle = serve(service, *, host="127.0.0.1", port=0, open_browser=True, token=None,
               settings=None)          # returns at once; raises GuiError (PT-E700)
handle.url        # http://127.0.0.1:<port>/?token=<token>   (open this one)
handle.base_url   # http://127.0.0.1:<port>/                  (safe to log)
handle.port, handle.token, handle.temp_dir, handle.closed
handle.wait(timeout=None) -> bool      # True once closed
handle.close()                         # idempotent; does NOT close the service
run_forever(handle)                    # blocks until Ctrl+C or close(), then closes
```

- `service` is any `porttool.core.interfaces.AppService`.
- `settings` is an addition to the signature in the task: it fixes the `[gui]` settings
  (used by tests). When omitted they are read from `service.config().extra("gui")`.
- `host` accepts only `127.0.0.1` or `localhost`; the socket always binds 127.0.0.1.
- CLI: `porttool/gui/cli.py`, `register_cli(group)` adds `gui`.
  `make_gui_command(service_factory=None, block=None)` is the seam for tests. The real
  factory imports `porttool.pipeline.build_service` only when the command runs.

Modules: `server.py` (HTTP, routing, `GuiApp`, `TableCache`, uploads), `tables.py` (row
builders, filter/sort/page), `forms.py` (form schema, `RawRequestRow` building),
`views.py` (JSON shapes), `settings.py`, `errors.py`, `cli.py`,
`static/{index.html,app.css,api.js,table.js,requests.js,app.js}`.

## Endpoints

All under `/api`. Every call needs the header `X-PortTool-Token`. Errors are
`{"error": {"code", "message"}}`; never a traceback.

| Method and path | Purpose |
| --- | --- |
| GET `state` | repo, `config_present`, `model_loaded`, `model` (hash, counts), `tables` (`no-model`/`preparing`/`ready`/`failed`), `stale`, `recovery_needed`, `jobs` (queued or running), `validation` (`accepted`, `can_apply`, `blockers`), `report_present`, `default_export_target`, `log_file`, upload limits |
| POST `open-repo` `{repo, config_file?}` | `service.open_repo`; returns findings and the new state |
| POST `load` | starts the load job |
| POST `export` `{target}` | target must be an absolute `.xlsx` path in an existing folder |
| POST `dry-run/upload` | `multipart/form-data` with one or more files, or one raw `.xlsx` body with `?name=`; saves them and starts the dry run |
| POST `dry-run/rows` `{rows: [{sheet, values, requester?, comment?, action?}]}` | builds `RawRequestRow`s (action defaults to ADD) and starts the dry run |
| POST `apply` | starts apply; refused unless the last dry run has accepted requests, the model is not stale and no recovery is needed |
| POST `rollback` | only when `recovery_needed()` |
| POST `doctor` | starts the doctor job |
| POST `jobs/<id>/cancel` | `{cancelled: bool, job}`; a second cancel answers `false`, not an error |
| GET `jobs/<id>`, GET `jobs` | job views; `elapsed_s` is measured by the server; `log_file` is added to failed jobs |
| GET `tables` | table list with column definitions and row counts |
| GET `tables/<name>?offset=&limit=&sort=&dir=&q=&f.<col>=&only=unconnected` | rows (arrays in column order), `total`, `filtered`. `limit` is capped at 500 (default 200). `only=unconnected` exists for `ports` |
| GET `validation[?status=<status>]` | last dry run: results with status, reasons, source row; counts; general findings; plan preview and planned files; `apply` |
| GET `report` | last apply report: `ok`, `branch`, `report_file`, counts, findings |
| GET `lists[?swc=<name>][&prototype=<name>]` | dropdown values under the list names of `core/sheets.py`; with `swc`/`prototype`, only the port and runnable names of that one |
| GET `form` | schema of the request form, derived from the sheet columns (addition) |

Tables: components, ports, runnable_access, triggers, connections, unconnected,
interfaces, interface_elements, data_types, pending.

Statuses: 400 bad input (`bad-request`, `bad-query`, `bad-json`, `bad-upload`, `not-xlsx`,
`too-many-files`), 403 (`bad-token`, `bad-host`, `bad-origin`), 404, 405, 409 (`no-repo`,
`no-model`, `job-running`, `stale`, `recovery-needed`, `nothing-to-apply`, `no-recovery`),
411, 413 (`too-large`), 500 (`internal`, `tables-failed`), 503 (`stopped`).
`PortToolError` from the service: `ConfigError`/`WorkbookError` 400, `RepoStateError` 409,
others 500, each with its own code and user message.

## `[gui]` settings (porttool.toml)

| Key | Default | Meaning |
| --- | --- | --- |
| `port` | 0 | fixed port when `--port` is not given (0: a free port) |
| `max_upload_mb` | 50 | size limit of one import request (all workbooks together) |
| `max_upload_files` | 20 | workbooks per import |
| `stale_check_interval_s` | 2.0 | how often the server asks the service for stale and recovery |
| `partner_limit` | 10 | names listed in "Compatible partner ports" |
| `max_form_rows` | 5000 | requests per form validation |

A wrong or unknown value is logged and the default is used.

## Security

- Binds 127.0.0.1 only. Random free port by default. `SO_REUSEADDR` is off.
- Token: `secrets.token_urlsafe(32)`, compared with `compare_digest`, accepted in the
  header only. The page reads it from the address once and removes it from the address bar.
- `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, also for static files.
  A foreign `Origin` is refused. No CORS headers are ever sent.
- Static files: one flat folder, names must match `[A-Za-z0-9_-]+\.(html|css|js)`.
- Headers: `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a strict Content-Security-Policy on the page
  (no inline script or style).
- Uploads go to a `mkdtemp` folder, into a random subfolder, under generated names
  (`01_<letters, digits, _ and ->.xlsx`). Content is checked (zip with `xl/workbook.xml`).
  The folder is removed on `close()`.
- The page writes all data with `textContent`, never as HTML.

## Tests

```
$ python3 -m pytest tests/unit/gui -q -o addopts=""
........................................................................ [ 76%]
............................................                             [100%]
187 passed, 1 skipped in 97.16s (0:01:37)

$ mypy --strict porttool/gui tests/unit/gui
Success: no issues found in 16 source files

$ ruff check porttool/gui tests/unit/gui
All checks passed!
```

- The skipped test writes the screenshots:
  `PORTTOOL_GUI_SCREENSHOTS=porttool/gui/docs python3 -m pytest tests/unit/gui/test_browser.py -k screenshots`.
- Browser tests use the system Chrome headless and skip when it cannot start. Each one
  fails on any console error or page error; a test that provokes an HTTP error declares it.
- Coverage: measured once with the stdlib `trace` module on the non-browser tests only:
  97 % of the traced lines of `cli`, `forms`, `server`, `settings` and `tables`. `trace`
  wrote no file for `views.py`, `errors.py` and `__init__.py`; I did not find out why, so
  they are not in that figure. JavaScript coverage was not measured.

Corner cases and their tests (`test_corner_cases.py` unless another file is named):

| Corner case | Test |
| --- | --- |
| Empty model | `test_empty_model_through_the_api`, `test_empty_model_in_the_page` |
| 100,000-row table through the API | `test_table_with_100000_rows_through_the_api` |
| Rapid filter typing | `test_rapid_filter_typing_shows_only_the_last_response` |
| Sort during load | `test_sort_during_load_shows_the_sorted_rows` |
| Double click on Apply | `test_double_click_on_apply_starts_one_job`, `test_two_apply_calls_at_once_start_one_job` |
| Cancel twice | `test_cancel_twice` |
| Closing the server during a job | `test_closing_the_server_during_a_job`, `test_server.py::test_requests_on_an_open_connection_are_refused_after_close` |
| Model swapped while the page is open | `test_model_swapped_while_the_page_is_open` |
| Long, non-ASCII, right-to-left and `<script>` names | `test_long_non_ascii_rtl_and_script_names_render_safely` |
| Service raises `PortToolError` | `test_server.py::test_port_tool_error_becomes_a_json_error` |
| Service raises something else | `test_server.py::test_unexpected_error_is_a_generic_500_with_the_traceback_in_the_log` |
| Port already in use | `test_port_already_in_use`, `test_port_taken_by_another_program_is_reported`, `test_cli.py::test_root_command_line_reports_a_port_in_use_with_exit_code_2` |
| Wrong token | `test_server.py::test_api_call_with_wrong_token_is_refused` |
| Upload of a non-xlsx file | `test_upload_of_a_non_xlsx_file_is_refused`, `test_upload_of_a_non_xlsx_file_is_refused_in_the_page` |

## Measured timings

Sandbox: 2 shared cores, so the numbers are noisy and pessimistic. Client-side times
through `http.client` on loopback, one new connection per call.

| What | Measured | Budget |
| --- | --- | --- |
| Any listed call on the 20,000-port model, cache warm (13 addresses, 9 calls each) | median 0.8 to 2.1 ms, worst single call 5.0 ms | 50 ms |
| First call of a new sort or filter, 20,000 ports | 1 to 17 ms | 200 ms |
| 100,000-port table: first call of a new sort or filter | 1 to 76 ms | 200 ms |
| 100,000-port table: same query again | 0.8 to 3.9 ms | 200 ms |
| Preparing the tables (worker thread, once per model) | 0.23 s for 20,000 ports, 1.8 s for 100,000 ports | none set |
| Page: jump to the last of 20,000 rows until the row is drawn | 114 ms, 6 fetches in the whole test | none set |
| `handle.close()` while a job runs | under 3 s asserted; about 0.1 s seen | none set |

The 50 ms test asserts the median per address, not the worst call, because of the noise.

## Unverified on Windows

Nothing here was run on Windows. All tests ran on Linux with Chrome 153 headless.

- Opening the default browser (`webbrowser.open` with an address that has a query string).
- Edge. Only Chrome was tested.
- Windows Firewall: a listener on 127.0.0.1 normally causes no prompt; not confirmed.
- Antivirus or endpoint protection that inspects or blocks loopback HTTP or a listening
  `python.exe`.
- The `SO_EXCLUSIVEADDRUSE` line in `_HttpServer.server_bind` (never executed here) and
  the error number and text Windows gives for a port in use. The message does not depend on
  the error number.
- Ctrl+C in the console window (`run_forever` waits in 0.5 s steps for that reason).
- Removing the temp folder while another program still holds an uploaded workbook open
  (a warning is logged if it cannot be removed).
- Windows paths typed into the repo and export fields (backslashes, UNC, long paths): the
  checks use `Path.is_absolute()` and `Path.is_dir()` with Windows semantics.
- A browser proxy configuration that sends 127.0.0.1 through a proxy.
- Real browser zoom and display scaling. The 200 % case was tested as a 640 x 400 viewport.
- Dragging files from Explorer onto the drop zone.

## PENDING-DISCOVERY assumptions

None. This package holds nothing DaVinci-specific.

## Assumptions about the real service

The fake does these; the real `build_service()` may differ.
- `open_repo` may be called once at start; the page never calls it while a repo is open.
- `model_is_stale()` and `recovery_needed()` are safe to call from a background thread
  every 2 s. Handlers only read the last answer, so they may be slow.
- `jobs()` lists finished jobs too; the GUI filters on `state`.
- `JobView.summary` of an export has the key `target` (else the typed path is shown).
  Other summaries are shown as `key: value`.
- `RowRef.file` of an imported row is the saved path or its file name; both are mapped
  back to the name the user chose.

## Decisions to confirm

1. Token in memory only, so F5 ends the session. Keeping it in `sessionStorage` would
   survive F5 at a small cost in strictness. One line in `api.js`. I followed the task.
2. The GUI server allows one job at a time (409 `job-running`). The contract does not say so.
3. Rollback is refused unless `recovery_needed()` is true.
4. After any apply attempt the same dry run cannot be applied again: validate again first.
5. With `recovery_needed`, load, export and dry run are refused too ("only rollback is allowed").
6. The page starts a load by itself, once, when a repo is open but not loaded.
7. A "Check tools" button (doctor) is in the header; the task lists the endpoint but no button.
8. `mimetypes` is not used for static files: the Windows registry can map `.js` to
   `text/plain`, which the browser refuses under `nosniff`. Types are a fixed table.
9. An oversized upload up to 8 times the limit is read and dropped, so the browser gets
   the 413 message instead of a broken connection. Larger ones end the connection.
10. The pending table shows the queue of the last dry run, or else the repo's pending file,
    which the GUI reads itself (see contract request 1).

## Contract change requests

1. `AppService.pending() -> PendingQueue`. Today the GUI reads
   `<repo>/<config.pending_file>` itself when no dry run has happened.
2. `JobView.started_utc` or `elapsed_s`. Today elapsed time counts from when the GUI
   server first saw the job.
3. `JobView.log_file` for the tool log of a failed job. Today the page shows the
   application log from `current_log_file()`.
4. Document the keys of `JobView.summary` per job name.
5. Reserve `PT-E700` for `GuiError` in `core/errors.py` (now defined in `gui/errors.py`).
6. Say whether `last_validation()` is cleared after an apply. The GUI guards either way.
7. Display names for uploads: `start_dry_run` gets generated file names such as
   `01_Team_A_requests.xlsx`, so the pipeline's HTML report shows those. The page maps
   them back; the report cannot.
8. `gui/tables.py` mirrors the label and dropdown rules of `workbook/rows.py` and
   `workbook/lists.py` (short name, or path where a name repeats) because packages may not
   import each other. Moving those two helpers to `core` would remove the copy.
9. Packaging (Agent 10): `pyproject.toml` needs
   `[tool.setuptools.package-data] porttool = ["gui/static/*"]`, or the page is missing
   from a built wheel. Running from the source tree or a zip of it works as is.

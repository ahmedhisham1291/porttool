# HANDOFF: porttool/core, porttool/cli.py, tests/fixtures, tests/fakes (Agent 1, Foundation)

The v0 contract files (models, operations, results, errors, interfaces, sheets, pending,
rowid, config) were not edited. This hand-off covers what was added around them.
The engine has its own file: porttool/engine/HANDOFF.md.

## Done
| File | Content |
| --- | --- |
| `porttool/core/fsutil.py` | atomic writes with retry, long-path helper, fingerprints, `RepoLock`, app folders |
| `porttool/core/log.py` | rotating file log, console handler, exception hooks |
| `porttool/core/config_loader.py` | load / validate / save `porttool.toml`, `owners.csv`, `locked.txt`; `discover_paths` |
| `porttool/cli.py` | root `click.Group` named `main`, command discovery, exit codes |
| `porttool/core/cli.py` | `porttool config check`, `porttool config discover [--write] [--force]` |
| `tests/fixtures/generator/` | deterministic synthetic AUTOSAR 4.x repo, presets `SMALL` and `LARGE`, `GroundTruth` |
| `tests/fakes/` | `fake_davinci.py`, four `.cmd` wrappers, `fake_command()`, `fake_env()` |

## Not done / deviations
- **naming.yaml is not read.** The naming rules come from a `[naming]` table in
  porttool.toml (the task said PyYAML is unavailable; it happens to be importable in this
  sandbox, but it is not a declared dependency, so it is not used).
- **click instead of Typer** (orchestrator change). No Typer import anywhere.
- The generated ARXML was **not validated against the AUTOSAR XSD** (no schema file in
  the sandbox). It is checked for well-formedness, unique paths and reference integrity
  (every `*-REF` resolves and its DEST matches the target's element). To validate on a
  machine with the schema: `PORTTOOL_AUTOSAR_XSD=<AUTOSAR_4-x-x.xsd> python3 -m pytest
  tests/unit/fixtures/test_generator.py -k schema`. Do this first when the XSD is at hand.
- The LARGE preset is 65 MB, not the 500 MB of the cold-load budget. There is no padding
  option; say so if the benchmarks need one.
- The generator does not produce: mode-switch, parameter, NV-data or trigger interfaces,
  queued sender-receiver, asynchronous server calls, PR ports, delegation connectors,
  several prototypes of one type, application data types with mapping sets, constants,
  SWC-IMPLEMENTATION, a flat extract or flat map. Readers need their own samples for those.
- `pyproject.toml` still lists `typer` and `PySide6` and has no console script. It is not
  one of my files: see "Contract change requests".

## Public API

### fsutil
```python
atomic_write_bytes(path, data, *, retries=3, backoff_s=0.1)
atomic_write_text(path, text, *, encoding="utf-8", newline=None, retries=3, backoff_s=0.1)
long_path(path) -> str                      # pure string logic
fingerprint(path, repo_root=None, *, with_sha256=False) -> SourceFile   # raises OSError
is_unchanged(source: SourceFile, repo_root) -> bool
sha256_file(path) -> str
repo_relative(path, repo_root=None) -> str  # forward slashes; absolute if outside the repo
RepoLock(repo_root, *, lock_dir=None)       # .acquire() .release() .held .holder(); context manager
read_lock(lock_file) -> LockInfo | None
local_app_dir(), default_cache_dir(repo_root), default_lock_dir(), repo_id(repo_root)
```
- Atomic write: temporary file `.porttool-<pid>-<random>.tmp` in the target folder,
  flush + fsync, `os.replace`. `PermissionError` from `os.replace` is retried three times
  (0.1 s, 0.2 s, 0.4 s), so four attempts. The target folder must exist.
- Errors (all `WriteError`): `FileWriteError`, `ReadOnlyFileError` (never retried, the
  protection is never removed), `FileLockedError`. Each has `.path` and
  `.to_finding(repo_root)` with codes `FS-WRITE-001`, `FS-READONLY-001`, `FS-LOCKED-001`,
  so callers can report a locked file as a `Finding`.
- A crash can leave a `.porttool-*.tmp` file (constants `TEMP_PREFIX`, `TEMP_SUFFIX`):
  the pipeline's recovery should delete them, they would make the git tree dirty.
- `is_unchanged`: with a recorded sha256 the content decides; without it size + mtime_ns.
- `RepoLock`: lock file `%LOCALAPPDATA%\PortTool\locks\<repo id>.lock` (outside the repo
  on purpose: a lock file in the repo would make the working tree dirty). Content: pid,
  process start time, repo, time, token. Stale = pid gone, or pid alive with another
  start time (pid reuse). `acquire()` raises `RepoLockedError` (a `RepoStateError`, with
  `.holder`) for a live holder, including a second lock in the same process.

### log
```python
setup_logging(log_dir=None, *, console=False, level=INFO, console_level=WARNING,
              max_bytes=5 MB, backup_count=5) -> Path      # the log file
shutdown_logging(); current_log_file() -> Path | None; default_log_dir()
install_exception_hooks(loop=None)       # sys.excepthook, threading.excepthook, loop handler
loop_exception_handler(loop, context)    # the engine installs it on its own loop
```
- File: `%LOCALAPPDATA%\PortTool\logs\porttool.log`, `RotatingFileHandler`.
  Every module logs through `logging.getLogger(__name__)` (children of `porttool`).
- The console handler prints `LEVEL: message` only; tracebacks go to the file.
- The hooks log the traceback and print one line with the log path on stderr. Ctrl+C in
  the main thread keeps its normal behaviour.
- Use the logger name `_logger` (or `logger`): ruff's BLE001 accepts a broad `except`
  at a boundary only when it sees `<that name>.exception(...)`.

### config_loader
```python
load_config(repo_root, *, config_file=None, strict=True) -> LoadedConfig   # .config .findings .config_file .has_errors
load_owners(path, repo_root=None) -> (Mapping[str, OwnerEntry], findings)
load_locked(path) -> frozenset[str]
render_config(config) -> str;  save_config(config, config_file=None) -> Path   # atomic
discover_paths(repo_root, dpa_file=None) -> DiscoveredPaths                    # .propose() -> ProjectConfig
```
porttool.toml (all keys optional except `[paths] arxml_roots`):
```toml
format_version = 1
[paths]      # relative to the repo root, or absolute
dpa_file, dcf_file, ecuc_file, flat_extract, flat_map, xsd_file      # files
sip_root, developer_bin, gen_data, cache_dir                          # folders
arxml_roots = ["Config/Developer"]
exclude_globs = ["Appl/GenData/**"]
[naming]     max_length, port, interface, data_type, runnable, connector (regular expressions),
             server_runnable_pattern, connector_pattern
[packages]   data_types, interfaces, constants, compu_methods, constraints
[timeouts]   workspace_check_s, script_task_s, validate_s, generate_s, import_s, git_s
[generate]   modules = ["Rte", "Os"]
[pending]    file, max_age_days
[limits]     large_file_bytes, min_free_disk_bytes
```
- `owners.csv` and `locked.txt` are read from the folder of porttool.toml.
- owners.csv: header row with SWC and arxml_owner (required), team, authoring_tool,
  export_folder; any column order and case; comma, semicolon or tab; UTF-8 with or
  without BOM. `arxml_owner` is davinci, model or locked; empty means davinci.
- `cache_dir` defaults to `%LOCALAPPDATA%\PortTool\cache\<repo id>`.
- **ConfigError** (message names file, key and problem; all problems of one file in one
  error): missing or malformed porttool.toml, wrong type or value, invalid regular
  expression or pattern placeholder, missing `arxml_roots`, an ARXML folder that does
  not exist (`strict=True`), any ambiguity in owners.csv or locked.txt (invalid owner,
  invalid SWC name, conflicting duplicate), non-UTF-8 file, newer `format_version`.
- **Findings** (non-fatal): `CFG-KEY-001` unknown key or table; `CFG-PATH-001` optional
  path missing; `CFG-PATH-002` file/folder mix-up; `CFG-PATH-003` ARXML folder missing
  (severity error, only with `strict=False`); `CFG-OWNERS-001` no owners.csv (every SWC
  counts as DaVinci-owned); `-002` unknown column; `-003` identical duplicate row; `-004`
  model-owned SWC without export_folder; `-005` export folder missing; `CFG-LOCKED-001`
  no locked.txt (info). Discovery: `DISC-DPA-001/002/003`, `DISC-DCF-001/002`,
  `DISC-ECUC-001/002`, `DISC-ARXML-001/002`.

### Command line
`porttool.cli:main` is the root `click.Group`. It calls `register_cli(group: click.Group)`
of every `porttool.<package>.cli` module (found without importing packages that have no
`cli` module). An `ImportError` in a package's cli is logged and skipped.
- Options: `--log-dir DIR`, `-v/--verbose`, `--version`, `-h/--help`.
- Exit codes: 0 success; 1 findings of severity error; 2 a `PortToolError` (stderr:
  `[code] message`, then `Log file: <path>`, no traceback) and click usage errors;
  **3 an unexpected exception** (my choice, the orchestrator named only 0 to 2).
- For package authors: `from porttool.cli import echo_findings, exit_code_for`; end a
  command with `raise click.exceptions.Exit(echo_findings(findings))`; raise
  `PortToolError` subclasses for fatal problems and let the root group print them.
- Run it: `python3 -m porttool.cli --help` (no console script yet, see change requests).

### Test fixtures
```python
from tests.fixtures.generator import SMALL, LARGE, GeneratorSpec, FileLayout, generate, render
truth = generate(tmp_path / "repo", SMALL)   # writes the repo; folder must be empty
files, truth = render(spec)                  # in memory only: {rel path: bytes}
truth.counts                                 # swcs, ports, p_ports, r_ports, interfaces, data_types, runnables,
                                             # access_points, triggers, task_mappings, prototypes, connectors,
                                             # unconnected, files, arxml_files, bytes, model_owned_swcs, locked_swcs
truth.swc_paths, .port_paths, .runnable_paths, .access_point_paths, .trigger_paths, .connector_paths
truth.unconnected                            # sorted (prototype path, port path)
truth.components / ports / interfaces / runnables / access_points / triggers / task_mappings /
      prototypes / connectors / data_types / base_types / compu_methods / constraints / units
truth.to_model()                             # a porttool.core.models.Model, no reader needed
truth.arxml_root, .arxml_files, .ecuc_file, .dcf_file, .dpa_file, .files, .repo_root
```
`python3 -m tests.fixtures.generator <folder> --preset small|large [--seed N]`.
- Parameters: `swc_count`, `ports_per_swc` (or exact `port_count`), `connector_count`,
  `layout` (grouped / per-object), `model_owned_share`, `seed`, `interface_count`,
  `data_type_count`, `locked_count`, `client_server_share`, `with_uuids`,
  `ecuc_definition_root`, `project_name`. Impossible specs raise `ValueError`.
- SMALL: 3 SWCs (1 model-owned), 12 ports, 5 connectors, 16 files.
  LARGE: 300 SWCs (60 model-owned, 5 locked), 20,000 ports (4,000 P, 16,000 R), 15,000
  connectors, 1,400 interfaces, 290 data types, 2,368 runnables, 26,466 access points,
  2,368 triggers, 1,452 task mappings, 1,407 unconnected port instances, 2,060 files
  (1,993 workspace ARXML), 65.0 MB.
- The truth objects are the contract dataclasses. Exact by the standard: paths, names,
  kinds, directions, references, periods, task mappings, connectors, unconnected.
  By this generator's convention only (a reader may differ, compare loosely):
  `Origin.line` is None; init value = text of the VALUE element; `Operation.possible_errors`
  holds short names; a VALUE type with a TEXTTABLE compu method has category ENUMERATION;
  `DataType.unit_path` is None (the unit is on the compu method); `Component.owner` and
  `.team` come from owners.csv / locked.txt.
- The layout changes only the files: the same spec with another layout gives the same model.

```python
from tests.fakes import fake_command, fake_env, cmd_wrapper, TOOLS

subprocess.run([*fake_command("DVCfgCmd"), "any", "args"], env=fake_env(exit_code=3, stdout=["a"]))
```
`fake_env` options: `exit_code`, `stdout`, `stderr` (lists of lines), `line_delay_s`,
`delay_s`, `hang`, `crash` (+ `crash_code`), `license_error` (True or a text),
`output_mb`, `non_utf8`, `spawn_child_s` (+ `child_pid_file`, `child_inherits_pipes`),
`report_file` (+ `report_text`), `record_file` (JSON lines: tool, args, cwd, pid).
The tool name comes from `--tool NAME` or the script/wrapper name.

## How to run the tests
```
python3 -m pytest tests/unit/core -q          # also covers porttool/cli.py
python3 -m pytest tests/unit/fixtures -q      # generator and fakes
mypy --strict porttool/core tests/unit/core
ruff check porttool/core tests/unit/core
```
Tails (this sandbox, Python 3.13):
```
tests/unit/core:      150 passed, 1 skipped in 1.01s
tests/unit/fixtures:  40 passed, 2 skipped in 9.66s
tests/unit (all):     228 passed, 3 skipped in 14.04s
mypy --strict porttool/core tests/unit/core:  Success: no issues found in 20 source files
mypy --strict porttool tests:                 Success: no issues found in 44 source files
ruff check porttool tests:                    All checks passed!
ruff format --check porttool tests:           44 files already formatted
```
Skipped: the Windows long-path write, the `.cmd` wrapper run (both `windows_only`), and
the XSD validation (needs `PORTTOOL_AUTOSAR_XSD`).

## Measured (2 CPU cores shared with other agents: treat as rough)
- LARGE preset, generate and write 2,060 files / 65.0 MB: 0.88 s, 1.02 s, 1.14 s, 1.17 s
  and 1.53 s in five runs. Budget: 60 s.
- Whole unit suite: 14 s.
- Coverage, measured once with the stdlib (`python3 -m trace --count --missing`, without
  the two LARGE tests; not coverage.py, so the line basis differs slightly):
  `porttool/cli.py` 99.0 %, `core/cli.py` 100 %, `core/config_loader.py` 97.9 %,
  `core/fsutil.py` 97.0 %, `engine/engine.py` 96.2 %. `core/log.py`: trace wrote no
  report for it, so **not measured** (it has 15 tests). The tests-only code
  (generator, fakes) was not measured.

## Unverified on Windows
- `long_path`: the string logic is tested; that `open`, `os.stat` and `os.replace` accept
  the `\\?\` form is not (test marked `windows_only`, skipped here).
- Sharing violation: simulated by making `os.replace` raise `PermissionError`. That
  Windows reports a real sharing violation this way, and that 0.7 s of retries is
  enough for a virus scanner, is not proven.
- Read-only detection uses `st_mode & S_IWRITE`; tested here with `chmod`, not with the
  Windows read-only attribute.
- `%LOCALAPPDATA%` defaults: the variable is injected in tests.
- `RepoLock`: exclusive creation (`O_EXCL`) on NTFS, `psutil` start times and
  `AccessDenied` for another user's process were only exercised on Linux.
- Log rotation while a second PortTool instance has `porttool.log` open: Windows refuses
  the rename; Python then reports the error on stderr and keeps writing without rotating.
- Console output of non-ASCII messages on a cp1252 console.
- The four `.cmd` wrappers, the fake's crash exit code 0xC0000005, and how a child
  process inherits pipes on Windows.

## PENDING-DISCOVERY assumptions (19 comments in the code)
config_loader (`discover_paths`):
- how the .dpa names the .dcf, the ECUC file and folders: the code assumes only "XML whose
  texts or attributes contain paths relative to the .dpa folder or absolute";
- the workspace ARXML files lie in the folder of the .dcf or below it;
- file-name conventions: `FlatExtract`, `FlatMap`, folder `GenData`.
  (ECUC files are recognised by content, which is standard.)

Generator (`tests/fixtures/generator/davinci_seam.py`, `spec.py`, `plan.py`, `arxml.py`):
- repo layout: `Config/Developer`, `Config/ECUC`, `Appl/GenData`, `Models/<Swc>/arxml`;
- the `.dcf` format and the `.dpa` format (both guessed, marked PROVISIONAL in the files);
- ECUC: package `/ActiveEcuC`, definition root `/MICROSAR` (parameter
  `ecuc_definition_root`), container short names; readers should match the definition
  path suffix `.../Rte/RteSwComponentInstance/RteEventToTaskMapping`;
- schema version 4.2.2 in `xsi:schemaLocation`;
- which elements carry a UUID, and its letter case (`with_uuids=False` leaves them out);
- one file per object (`FileLayout.PER_OBJECT`) as the DaVinci workspace layout;
- a model-owned SWC exists in the workspace and as a copy in its export folder;
- files are UTF-8 without BOM with LF line ends; short names of events and access points
  are this generator's own convention.

Fakes: flags, output texts, exit codes and license messages of the real tools.

## Contract change requests
1. `porttool/core/config.py`: the module docstring and the `NamingRules` docstring name
   `naming.yaml`; the rules are read from `[naming]` in porttool.toml. Please update the text.
2. `pyproject.toml`: replace `typer` by `click` and remove `PySide6` (orchestrator
   change), and add `[project.scripts] porttool = "porttool.cli:main"`. A
   `porttool/__main__.py` would make `python -m porttool` work.
3. `porttool/core/errors.py` (optional): `FileWriteError`, `ReadOnlyFileError`,
   `FileLockedError` and `RepoLockedError` live in fsutil.py and inherit the codes
   PT-E400 / PT-E410. Move them into errors.py with own codes if other packages want to
   catch them without importing fsutil.

## Open questions
- porttool.toml is committed to the repo, but `sip_root` and `developer_bin` are usually
  machine-specific. A local override file or environment variables in paths were not
  built because nothing asked for them.
- Fixed locations: porttool.toml in the repo root, owners.csv and locked.txt next to it.
  Is a `porttool/` sub-folder preferred (pending.json already lives there)?
- The repo lock is per Windows user (it is under `%LOCALAPPDATA%`): two users working in
  the same folder on one machine are not excluded from each other.
- Stale-lock removal has a very small race when two instances start at the same moment
  right after a crash (compare-then-delete is not atomic).
- Exit code 3 for unexpected errors: confirm or change.

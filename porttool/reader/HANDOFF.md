# HANDOFF: porttool/reader (Agent 2)

Branch `agent/reader`. Owned folders: `porttool/reader/`, `tests/unit/reader/`. Nothing else
was touched. Built and tested in the Linux sandbox (Python 3.13, lxml 6.1, 2 CPU cores,
7 GB RAM shared with other agents). Not run on Windows, not run against a real ECU repo,
not run with the real engine (porttool/engine is not in this worktree).

## Status

Done:

- `ArxmlReader` implements `porttool.core.interfaces.Reader` (`load`, `is_stale`).
- File discovery, exclude patterns, `.dcf` comparison (behind one seam).
- Parallel parsing, one task per file through `ctx.run_blocking`, one lxml parser per
  thread, no entities, no network, no DTD. Streaming path for files above
  `config.large_file_bytes`; both paths give identical records (tested).
- Extraction of every element kind listed in the task prompt (list below).
- Model build: reference resolution by absolute path, reference bases, owners and teams,
  ECUC task mapping and OS tasks, fingerprints, findings with stable `RDR-` codes.
- Record cache (SQLite), `is_stale`, click commands `load` and `list`.
- Every corner case of the prompt has a named test (table below).
- Scale generator, one full-scale measurement, profile.

Not done / limits (by design or for lack of facts):

- Nothing was verified on Windows or with DaVinci files. See "Unverified on Windows" and
  "PENDING-DISCOVERY".
- Pass-through connectors are reported (`RDR-KIND-002`) and left out: the model has no
  kind for them.
- Not read at all, because the model has no place for them: SWC-IMPLEMENTATION,
  inter-runnable variables, per-instance memory, NV block descriptors, port API options,
  wait points, asynchronous server call result points, application error codes.
  Accesses to local variables and local parameters are skipped (they are not port accesses).
- Variation points are detected inside the objects the reader extracts (and reported, the
  object is kept). Variation points inside element kinds the reader ignores are not reported.
- A compu method with several rational scales reports only the first as factor/offset.
- A reference base whose PACKAGE-REF is itself relative is ignored with a finding.
- The CLI takes its `ProjectConfig` from the click context or from `--repo` options; how
  the application's root command provides the config is not defined yet (see API below).

## Public API

```python
from porttool.reader import (
    ArxmlReader,
    ReaderOptions,
    LoadStats,
    EcucLayout,
    load_blocking,
    PoolJobContext,
    register_cli,
    codes,
)
```

| Name | What it is |
| --- | --- |
| `ArxmlReader(options: ReaderOptions \| None = None)` | The `Reader`. Stateless between loads except `last_stats`. |
| `async ArxmlReader.load(config, ctx) -> Model` | Raises only `JobCancelled` (and what `ctx` raises). Every file problem is in `Model.findings`. |
| `ArxmlReader.is_stale(config, model) -> bool` | Blocking. Lists the folders and stats the files (about 2,000 `os.stat`), reads no file content. Call it through `run_blocking`. |
| `ArxmlReader.last_stats: LoadStats \| None` | Counts, bytes, seconds per phase, `schemas` (XSD names found), `cache_enabled`. |
| `ReaderOptions` | `use_cache`, `ecuc_layout`, `generated_globs`, `dom_budget_bytes`. |
| `EcucLayout` | ECUC definition-path suffixes (PENDING-DISCOVERY seam). |
| `load_blocking(config, *, options, workers, on_progress) -> (Model, LoadStats)` | Runs a load with its own event loop and thread pool. For the CLI, scripts, benchmarks. Not for the engine or GUI thread. |
| `PoolJobContext` | The small `JobContext` behind `load_blocking`. |
| `register_cli(group: click.Group)` | Adds `load` and `list swc\|ports\|connectors\|unconnected`. |
| `porttool.reader.codes` | The finding codes (below). |

Command line (click, tested with `CliRunner`):

```
porttool load   [--repo DIR --arxml-root PATH... --ecuc FILE --dcf FILE --exclude PATTERN...
                 --cache-dir DIR --no-cache] [--max-findings N]
porttool list swc|ports|connectors|unconnected  [same repository options] [--format table|tsv]
porttool list ports --swc NAME_OR_PATH
```

Exit codes: 0 success, 1 the model has findings of severity error, 2 a `PortToolError`
(printed as `[code] message`, no traceback). If a parent command stored a `ProjectConfig`
in the click context (`ctx.obj`, found with `ctx.find_object(ProjectConfig)`), it is used
and `--repo` is not needed. This convention is my assumption: Agent 1 must confirm it or
tell me what the root command provides.

### Module map

| Module | Content |
| --- | --- |
| `reader.py` | `ArxmlReader`: orchestration only, no parsing on the event loop |
| `discovery.py` | folder scan, exclude patterns, `.dcf` comparison, fingerprints |
| `dcf.py` | the one function that knows the `.dcf` format (provisional) |
| `parse.py` | per-file worker: stable read, DOM walk, streaming walk, `ByteBudget` |
| `extract.py` | lxml elements -> plain-tuple records |
| `records.py` | record layouts, `FileRecords`, `CACHE_FORMAT` |
| `cache.py` | SQLite record cache |
| `build.py` | records -> `Model`, reference resolution, findings |
| `ecuc.py` | `EcucLayout` (provisional definition names) |
| `report.py`, `cli.py`, `standalone.py` | text tables, click wiring, engine-less runner |

### What is extracted

Component types: application, sensor-actuator, complex device driver, ECU abstraction,
service, service proxy, NV block, parameter, composition; any other `*-SW-COMPONENT-TYPE`
becomes `ComponentKind.OTHER` with `RDR-KIND-001`. P/R/PR port prototypes with interface
TREF and com specs (init value, queue length). Sender-receiver, client-server, mode-switch,
parameter, NV-data and trigger interfaces. SWC-INTERNAL-BEHAVIOR with runnables; all event
kinds of `TriggerKind` (others become `OTHER`); data read/write access, data send points,
data receive points by argument and by value, synchronous and asynchronous server call
points, mode access and mode switch points, parameter access, external triggering points
(`AccessKind.OTHER`). Compositions with component prototypes, assembly and delegation
connectors. Implementation and application data types, base types, compu methods (linear,
text table), data constraints, units, data type mapping sets, constant specifications.
Mode declaration groups are read only to resolve references. ECUC: OS tasks and RTE
event-to-task mappings.

### Conventions the other packages must know

- Unresolved reference: the path is kept as written (`Port.interface_path` etc.), plus a
  finding. `None` means "no reference in the file" or "relative reference without base".
- `ComSpec.init_value` / `Constant.value` / `DataElement.init_value` text: the VALUE text
  for numerical and text values; the constant's path for a CONSTANT-REFERENCE; `[a, b]` for
  arrays; `{a, b}` for records; `&/path` for a reference value; `<ELEMENT-NAME>` for kinds
  the reader does not interpret.
- PR port: `com_specs` holds the provided-side specs first, then the required-side specs.
- Mode-switch interface: the mode group prototype is one `DataElement` in `elements`
  (its `type_path` is the mode declaration group) and `mode_group_path` is that group.
- `DataType` of category TYPE_REFERENCE: `element_type_paths` has one entry, the referenced
  type, and `element_names` is empty. An element without type reference is `""` so that
  `element_type_paths` stays parallel to `element_names`.
- `DataTypeCategory`: implementation VALUE with a BOOLEAN base type is BOOLEAN; VALUE with a
  text-table compu method is ENUMERATION; STRUCTURE is RECORD.
- Delegation connector: the side with `prototype_path None` is the composition's own port.
  Outer R port to inner R port: provider = outer, requester = inner. Inner P port to outer
  P port: provider = inner, requester = outer.
- `AccessKind.QUEUED_RECEIVE`: a data receive point (by argument or by value) whose data
  element has SW-IMPL-POLICY QUEUED, or whose port has a QUEUED-RECEIVER-COM-SPEC for it.
- A MODE-ACCESS-POINT without SHORT-NAME gets the stable name
  `<MODE-ACCESS-POINT:Port.Group>` (the angle brackets cannot occur in a real short name).
- Trigger target: element, operation or mode name. A mode transition gives `A->B`.
  A data-send-completed event takes port and element from the send point it refers to.
- Duplicates: files are merged in repo-relative path order; the first definition of a
  path wins (`RDR-DUP-001`, one finding per duplicated object, children stay silent).
  A component type or internal behavior defined in several files with the same element
  kind and no overlapping children is a split element and is merged (`RDR-DUP-002`, info);
  `Component.origin` is then the first file.
- Component owner: `config.owner_of(name)`; LOCKED additionally for every SERVICE component
  and for every component in a file matching `ReaderOptions.generated_globs`.
- Task mappings: one per event path. If instances of one type map the same event to
  different tasks, the first is kept and `RDR-ECUC-003` reports it.
- `Model.files` holds every discovered file that still exists, including files that failed
  to parse (so the model is not stale forever because of a broken file) and the `.dcf`.
  `sha256` is set for every file that was read completely.
- `Origin.line` is exact up to line 65,534. Beyond that libxml2 reports the line of the
  first text inside the element (here: the SHORT-NAME line), tested with a 300,000-line file.
- Exclude patterns: see the docstring of `discovery.py` (`**`, `*`, `?`, `[..]`, case
  ignored, a pattern without `/` matches at any depth, a matching folder excludes its
  content). Never loaded from the roots: `config.gen_data`, `config.sip_root`,
  `config.flat_extract`, `config.flat_map`, `.git`, `.svn`, `.hg`. `config.ecuc_file` is
  always loaded.

### Finding codes

| Code | Severity | Meaning |
| --- | --- | --- |
| RDR-FILE-001 | error | file vanished between scan and read |
| RDR-FILE-002 | error | file could not be read (permission, sharing violation) |
| RDR-FILE-003 | warning | file kept changing during three read attempts (never cached) |
| RDR-FILE-004 | error | configured ARXML root or ECUC file does not exist |
| RDR-FILE-005 | error | a folder could not be listed |
| RDR-PARSE-001 | error | not well-formed XML, also truncated (file not loaded) |
| RDR-PARSE-002 | error | empty file |
| RDR-PARSE-003 | warning | DOCTYPE present, entities are not expanded |
| RDR-SCHEMA-001 | error | root element is not AUTOSAR 4.x (file not loaded) |
| RDR-SCHEMA-002 | warning | loaded files use more than one schema version |
| RDR-REF-001 | warning | referenced object is not in the loaded files |
| RDR-REF-002 | warning | DEST names another element kind than the target has |
| RDR-REF-003 | warning | target exists but is of a kind that cannot be used there |
| RDR-REF-004 | warning | relative reference without usable reference base |
| RDR-DUP-001 | error | duplicate short-name path |
| RDR-DUP-002 | info | splitable element defined over several files, merged |
| RDR-NAME-001 | warning | short names differ only by letter case |
| RDR-NAME-002 | warning | object without SHORT-NAME skipped |
| RDR-VAR-001 | warning | variation point: unsupported, object kept as always present |
| RDR-KIND-001 | warning | unknown component kind |
| RDR-KIND-002 | warning | unsupported connector kind (pass-through), left out |
| RDR-KIND-003 | warning | more than one internal behavior in a component |
| RDR-DCF-001 | error | `.dcf` missing or not readable |
| RDR-DCF-002 | error | listed in the `.dcf`, missing on disk |
| RDR-DCF-003 | warning | on disk under a root, not listed in the `.dcf` |
| RDR-DCF-004 | warning | listed in the `.dcf`, on disk, but not loaded |
| RDR-CACHE-001 | warning | cache folder unusable, load ran without cache |
| RDR-CACHE-002 | info | one cache entry unreadable, deleted and rebuilt |
| RDR-CACHE-003 | info | cache file unreadable, deleted and rebuilt |
| RDR-ECUC-001 | warning | task mapping of an event that is not in the model |
| RDR-ECUC-002 | warning | task mapping to a task that is not an OS task |
| RDR-ECUC-003 | warning | one event mapped to different tasks per instance |
| RDR-INTERNAL-001 | error | unexpected error in one file (traceback in the log) |

Line numbers are appended to the message as `(line N)` because `Finding` has no line field.

## Design notes

- Concurrency: the coroutine never parses and never loops over records. Scan, cache read,
  each file parse, cache write and model build are `run_blocking` tasks. Parse tasks are
  handed to the pool in a window of 64 so that a cache write or another job never waits
  behind 2,000 queued tasks and a cancel has little to unwind.
- Cancel: `ctx.check_cancelled()` is called by every worker before it reads, between
  stream chunks (1 MiB), while waiting for the DOM byte budget, between files in the
  build, and in the coroutine after every completed file. A DOM parse in flight (at most
  50 MB, about 0.4 s) finishes; nothing waits for it.
- Memory: `ByteBudget` limits the source bytes held as DOM trees at once (128 MB default;
  a DOM needs about 5 times the file size, measured). Files above `large_file_bytes` are
  streamed with a pull parser and every finished element is dropped.
- Cache: one SQLite file `records.sqlite3` in `config.cache_dir` (default
  `%LOCALAPPDATA%\PortTool\cache\<folder name>-<sha1 of the repo path, 16 hex>`). One row
  per file, valid for its exact `(size, mtime_ns)`. Version = cache format, tool version,
  Python version, ECUC layout; a change empties the cache. Blobs are pickles of builtin
  tuples read with an unpickler that refuses every global (tested with a malicious blob),
  plus a CRC. Parse failures (malformed, empty, wrong schema) are cached too; I/O failures
  are not. New entries are written in batches during the load, so a cancelled cold load
  keeps what it parsed. Why SQLite and not one file per entry: 2,000 file creations per
  cold load and 2,000 opens per warm load are slow on Windows; this is my judgement, not
  measured on Windows.
- lxml: `remove_blank_text=True` (measured 20 % faster, half the nodes),
  `remove_comments`/`remove_pis` (no walker has to skip them), `collect_ids=False`,
  `huge_tree` off (a 60 MB file streams without it).

## How to run

```
python3 -m pytest tests/unit/reader -q
mypy --strict porttool/reader tests/unit/reader
ruff check porttool/reader tests/unit/reader
python3 -m tests.unit.reader.coverage_trace          # statement coverage without pytest-cov
python3 -m tests.unit.reader.bench_scale --out /tmp/porttool-scale --target-mb 500 --profile
```

Tails, run in the sandbox on the final commit:

```
$ python3 -m pytest tests/unit/reader -q
........................................................................ [ 30%]
........................................................................ [ 60%]
.....s.................................................................. [ 90%]
.......................                                                  [100%]
238 passed, 1 skipped in 7.42s        (summary line from a run without the second -q)

$ mypy --strict porttool/reader tests/unit/reader
Success: no issues found in 28 source files

$ ruff check porttool/reader tests/unit/reader
All checks passed!
```

The skipped test is `test_permission_denied_by_the_file_system` (chmod): the sandbox runs
as root, and root can read any file. The permission-denied handling itself is tested by
`test_permission_denied` with an injected `PermissionError`.

Coverage: 96.9 % of 2,345 statements of `porttool/reader` executed, measured with
`tests/unit/reader/coverage_trace.py` (a `sys.settrace` counter, because pytest-cov is not
installed). It counts statements, not branches. Lowest modules: `__init__.py` 90.9 %,
`build.py` 93.4 %, `standalone.py` 93.8 %.

## Corner cases and their tests

| Corner case | Test |
| --- | --- |
| malformed XML | `test_files.py::test_malformed_xml` |
| truncated file | `test_files.py::test_truncated_file` |
| empty file | `test_files.py::test_empty_file` |
| wrong schema version | `test_wrong_schema_autosar_3`, `test_wrong_schema_not_autosar_at_all` |
| mixed schema versions | `test_files.py::test_mixed_schema_versions` |
| UTF-8 with/without BOM, UTF-16 | `test_files.py::test_encodings_utf8_bom_and_utf16` |
| CRLF and LF | `test_files.py::test_crlf_and_lf_line_endings_give_the_same_model` |
| comments and processing instructions | `test_files.py::test_xml_comments_and_processing_instructions` |
| one package split over several files | `test_model_corner_cases.py::test_one_package_split_over_several_files` |
| duplicate short-name paths in two files | `test_duplicate_short_name_paths_in_two_files` (+ three variants) |
| references to objects that do not exist | `test_reference_to_an_object_that_does_not_exist` |
| reference with the wrong DEST | `test_reference_with_the_wrong_dest_type` |
| relative references, reference bases | `test_relative_references_and_reference_bases` (+ two variants) |
| names differing only by case | `test_short_names_that_differ_only_by_letter_case` |
| PR ports | `test_sample_repo.py::test_pr_port_has_provided_and_required_com_specs` |
| ports without ComSpec | `test_sample_repo.py::test_port_without_com_spec` |
| SWC without internal behavior | `test_sample_repo.py::test_swc_without_internal_behavior` |
| nested compositions, delegation | `test_sample_repo.py::test_nested_compositions_and_delegation_connectors` |
| one type instantiated twice | `test_sample_repo.py::test_one_component_type_instantiated_twice` |
| variation points | `test_variation_points_are_reported_and_nothing_is_dropped` |
| file deleted during the load | `test_files.py::test_file_deleted_during_the_load` |
| file changed during the load | `test_file_changed_during_the_load_is_read_again`, `test_file_that_keeps_changing_is_reported_and_not_cached` |
| listed in the `.dcf`, missing on disk | `test_discovery.py::test_dcf_lists_a_file_that_is_missing_on_disk` |
| on disk, not in the `.dcf` | `test_discovery.py::test_file_on_disk_that_the_dcf_does_not_list` |
| very large file (61 MB, slow) | `test_files.py::test_very_large_file_uses_the_streaming_path` |
| permission denied | `test_files.py::test_permission_denied` |
| cache folder not writable | `test_cache.py::test_cache_folder_not_writable_disables_the_cache` |
| corrupt cache entry | `test_cache.py::test_corrupt_cache_entry_is_deleted_and_rebuilt` (6 kinds of damage) |
| cancel in the middle of a load | `test_files.py::test_cancel_in_the_middle_of_a_load` (+ before start, + inside a streamed file) |

Most file and model tests run twice, once per parsing strategy (fixture `any_repo`).
`test_sample_repo.py` asserts exact counts and attributes on hand-written ARXML
(`samples.py`), independent of the scale generator.

## Measured timings

One run, sandbox, 2 worker threads on 2 CPU cores shared with other agents, files in the
OS page cache, Linux file system. Repository from `scale_generator.py`: 2,000 files,
556.0 MB, 300 SWCs, 20,000 ports, 15,000 connectors, 2,000 interfaces, 20,000 access
points, one streamed ECUC file of 57 MB.

| Measurement | Result | Budget |
| --- | --- | --- |
| cold load (2,000 files parsed) | 6.06 s | 30 s |
| warm load (0 files parsed) | 0.50 s | 3 s |
| load after a one-file change | 0.51 s | (none) |
| peak resident memory of the process | 221 MB cold, 228 MB warm | 2 GB |

Phases as reported by `LoadStats`: cold = scan 0.01, cache read 0.02, parse 4.31, cache
write 1.32, build 0.39. Warm = scan 0.01, cache read 0.13, build 0.35. "Cache write"
includes waiting for a pool thread and competes with parsing for the two cores.

These numbers say nothing about Windows (NTFS, antivirus scanning, another core count).
The padding of the synthetic repository (system signals, ADMIN-DATA, ECUC containers) may
parse faster or slower than real content. Treat them as "the budgets are met with a wide
margin on two cores", not as a prediction.

Where the time goes (cold path on one thread under cProfile, which inflates the many small
Python calls; 9.45 s parse and extract, 0.83 s build in that run):

- `_extract_dom` own time 3.3 s: this is lxml parsing (`etree.fromstring`), GIL-free.
- streaming the 57 MB ECUC file 1.46 s (230,000 pull-parser events), mostly GIL-bound.
- walking packages and dispatching 889,000 package elements, of which most are skipped:
  about 1.1 s under the profiler, GIL-bound.
- SHA-256 of every file 0.43 s, file reads 0.17 s, GIL-free.
- extraction of the objects the model needs: about 1 s under the profiler, GIL-bound.
- model build 0.4 s without profiler (7 % of the cold load).

Process pool: not added and not proposed. Python-side model building does not dominate:
0.39 s of 6.06 s cold. It is most of the warm load (0.35 of 0.50 s), but the warm load is
six times under its budget, and a process pool would have to pickle the model back, which
costs more than building it. If the cold load ever needs to be faster, the GIL-bound part
to look at is the walk over unknown elements and the ECUC stream, not the build.

Other measurements made while building: lxml parses about 106 MB/s per thread with blank
text kept and 133 MB/s with `remove_blank_text`; two threads parsing in parallel took
0.52 s against 0.45 s for one (the GIL is released); a DOM tree takes about 5.3 times the
file size; streaming a 43 MB file kept the process at 32 MB against 250 MB for the DOM.

## Unverified on Windows

Nothing below was run on Windows. Logic is tested here only where the table says so.

| Item | State |
| --- | --- |
| Default cache folder `%LOCALAPPDATA%\PortTool\cache\<repo id>` | path logic tested with a monkeypatched environment variable; the real variable and folder rights are not |
| Sharing violation (WinError 32) retried three times with backoff when a file is opened | logic tested with a fabricated `PermissionError` carrying `winerror = 32`; a real lock by DaVinci is not |
| Case-insensitive file names (`os.path.normcase` for duplicates of roots, `.dcf` comparison, never-load folders) | not testable on Linux |
| `os.stat` size and `st_mtime_ns` on NTFS agreeing between the scan, the parse and `is_stale` | not verified; a mismatch would only cause extra parsing or a false "stale" |
| Paths longer than 260 characters | not handled specially, not tested |
| Junctions and symbolic links to folders | not followed (`is_dir(follow_symlinks=False)`); behaviour of junctions not verified |
| SQLite cache file with antivirus, with two PortTool processes, on a network drive | not verified; every SQLite error turns into `RDR-CACHE-001` and a load without cache |
| Performance: all timings above | Linux only |
| Reading a file while DaVinci Developer writes it | the size/mtime check and the three attempts are tested with a simulated writer only |
| click commands in a Windows console (encoding of non-ASCII names) | not verified |

## PENDING-DISCOVERY assumptions

Each is marked in the code with `PENDING-DISCOVERY:` and sits behind one seam.

1. `.dcf` format (`dcf.py::list_arxml_references`). Assumed: the file is XML, and every
   attribute value or element text that is a relative path ending in `.arxml` is a
   workspace file, relative to the folder of the `.dcf`. Backslashes are accepted.
2. ECUC definition names (`ecuc.py::EcucLayout`). Assumed: OS task containers end with
   `/Os/OsTask` and the task name is the container's SHORT-NAME; event mappings end with
   `/Rte/RteSwComponentInstance/RteEventToTaskMapping` with `RteEventRef` (absolute event
   path), `RteMappedToTaskRef` (last path segment = task name) and `RtePositionInTask`.
   Matched as suffixes, so `/MICROSAR/...` and `/AUTOSAR/EcucDefs/...` both work. These
   are the AUTOSAR standard names, not confirmed for the Vector SIP in use.
3. ECUC split over several files (`discovery.py::_Scan.add_ecuc`). `config.ecuc_file` may
   name a folder; every ARXML file below it is then read as ECUC.
4. BSW-generated component files (`ReaderOptions.generated_globs`, default
   `**/ServiceComponents/**`). Components in matching files become LOCKED. The folder name
   is a guess; a wrong guess only locks too much or too little, both visible in
   `list swc`.
5. One-file-per-object layout, UUID style, package conventions: the reader assumes none of
   them. It reads any package layout and keeps UUIDs as text.

## Contract change requests (porttool/core is frozen for me; all worked around)

1. `Model.autosar_schema` (or similar). `WorkbookMeta.autosar_schema` needs it. Today:
   `ArxmlReader.last_stats.schemas`.
2. `Finding.line: int | None`. Today the line is appended to the message.
3. `ProjectConfig.ecuc_file` as several files or a folder. Today a folder is accepted.
4. A config field for "generated, never edit" patterns. Today `ReaderOptions.generated_globs`.
5. `ComSpec` needs the side (provided or required) for PR ports. Today: order.
6. `DataType.referenced_type_path` for TYPE_REFERENCE. Today `element_type_paths[0]`.
7. `Trigger`: activation of a mode switch event (on entry, on exit, on transition) and a
   second mode. Today a transition is the text `A->B` in `target`, the activation is lost.
8. `TaskMapping` per component instance (prototype path). Today one mapping per event.
9. Origin of the internal behavior. If a SWC is split over files, the writer cannot find
   the behavior's file from `Component` when the behavior has no runnable yet.
10. `ConnectorKind.PASS_THROUGH`, or confirmation that leaving them out is right.
11. Mode declaration groups and their modes are not in the model. The validator may need
    the mode names to check a mode switch trigger.
12. `Reader.is_stale` is synchronous and touches the disk. The protocol should say that
    callers run it through `run_blocking`.
13. Nothing in `ProjectConfig` carries reader settings (cache on/off, ECUC layout).

## Open questions

- Agent 1: how does the root command hand the `ProjectConfig` to `register_cli` commands?
  I assumed `ctx.obj`.
- Agent 1: does `JobContext.run_blocking` run tasks in parallel, and how many threads does
  the pool have? The reader submits at most 64 parse tasks at a time.
- Agent 0: the five PENDING-DISCOVERY items above.
- Agent 5 (writer): is `Origin.line` used for edits? It is approximate beyond line 65,534.

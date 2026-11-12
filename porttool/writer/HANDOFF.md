# HANDOFF: porttool/writer (Agent 5)

Branch `agent/writer`. Owned folders: `porttool/writer/`, `tests/unit/writer/`. Nothing else
was touched. Built and tested in the Linux sandbox (Python 3.13, lxml 6.1, 2 CPU cores shared
with other agents). Not run on Windows, not run against a real ECU repo, not checked against
the AUTOSAR XSD (not available here), not checked against a file saved by DaVinci Developer.

The proof of correctness in this sandbox is the reader: after every write the tests reload
the repository with `ArxmlReader` and require exactly the requested objects, no change to any
other object, and no new reader finding. That proves "standard AUTOSAR XML the reader
understands". It does not prove "what DaVinci Developer would have saved".

## Status

Done:

- `ArxmlWriter` implements `porttool.core.interfaces.Writer`: `plan`, `apply`, `build_delta`.
- Text splicing, no re-serialisation. A tolerant tokenizer builds an offset index; new
  elements are inserted as text in the file's own line ending, indentation unit, namespace
  prefix, encoding and BOM. One pass and one atomic write per file.
- All seven Add operations. Modify and Delete give `WRT-UNSUPPORTED-001`.
- References inside one change set (port on a new interface, interface on a new data type,
  connector on a new port, trigger and access point on a new server runnable).
- Placement of new data types and interfaces: `existing_file` and `new_file`.
- Safety: fingerprint check, journal before each write, owner check, well-formedness and
  optional XSD check before a file is replaced, cancel, idempotency.
- `build_delta`.
- Every corner case of the task has a named test (table below).
- Extra, small: `created_events()` for the pipeline, three click commands.

Not done / limits:

- Modify and Delete operations; mode-switch events; new interfaces other than
  sender-receiver and client-server; new data types of category string or other; type
  reference at application level. Each gives `WRT-UNSUPPORTED-001`.
- A SWC without internal behavior gets none: `WRT-BEHAVIOR-001`. No SWC-IMPLEMENTATION,
  inter-runnable variables, port API options or mode handling are written.
- The `.dcf` is never written (`WRT-DCF-001` warning for each new file).
- No task mapping (by design: DaVinci Configurator through the pipeline).
- A file held open by another program is only noticed when the file is replaced, not in
  the dry run. Files written before it stay written; the outcome says which
  (`WRT-LOCKED-001` + `WRT-ABORT-001`) and the journal has them. A read-only file is seen
  in the dry run already.
- Deviation from the task text: the XSD / well-formedness check runs in memory on the exact
  bytes that are then written, not on a temporary copy on disk. `fsutil.atomic_write_bytes`
  has no hook between its temporary file and the replace, and I was told not to
  reimplement it. The effect is the same: bytes that fail the check never reach the target.
- One exception to "only insertions": a container written as `<PORTS/>` that receives a
  child loses its `/` (it becomes `<PORTS>...</PORTS>`).

## Public API

```python
from porttool.writer import ArxmlWriter, WriterSettings, CreatedEvent, created_events, codes, register_cli
```

| Name | What it is |
| --- | --- |
| `ArxmlWriter(*, uuid_factory=None, retries=3, backoff_s=0.1)` | The `Writer`. No required argument. `uuid_factory` is for tests, the other two go to `fsutil.atomic_write_bytes`. |
| `plan(model, config, change_set) -> WritePlan` | Blocking, reads files, writes nothing: call it through `run_blocking`. Only operations with route `apply`. `files` sorted by path, `preview` one line per planned operation in change-set order, `findings` = problems. |
| `async apply(plan, model, config, journal, ctx) -> WriteOutcome` | Recomputes the change from `plan.change_set` (same code as `plan`), then writes file by file in path order. Raises `RepoStateError` (changed file, plan that does not match), `JobCancelled`. |
| `build_delta(model, config, change_set, target) -> tuple[Finding, ...]` | Blocking. Operations of every route, owners not checked. Writes only `target` (parent folders are created). |
| `ArxmlWriter.last_outcome` | The `WriteOutcome` of the last `apply`, also when it raised (cancel, error): what was written so far. |
| `created_events(findings) -> tuple[CreatedEvent, ...]` | Reads the `WRT-INFO-EVENT` findings: operation id, event path, element name, runnable, requested task, `position_after_event`, file. |
| `WriterSettings.from_config(config)` | The `[writer]` table, see below. |
| `register_cli(group)` | `writer-plan [--repo DIR] CHANGESET.json`, `writer-delta [--repo DIR] CHANGESET.json OUT.arxml`, `writer-codes`. Exit codes 0 / 1 (error findings) / 2 (`PortToolError`). No apply command: that needs the pipeline. |

Conventions the pipeline must know:

- Every finding about one operation starts with `[op <id>] ` and carries `row` when the
  operation has `source_file`, `source_sheet` and `source_row`.
- `WriteOutcome.applied_operation_ids`: operations whose files were all written. An
  operation whose object already existed is NOT in it; it has a `WRT-INFO-EXISTS` finding
  (map it to `RequestStatus.ALREADY_APPLIED`).
- `journal.record(rel_path, "modify" | "create")` is called on the event loop thread, before
  the write of that file. If the write is then refused, the journal names a file that did
  not change: harmless for a rollback.
- After a cancel: `JobCancelled.detail` is `written: a, b`, and `last_outcome` has the same.
- After any `apply` that wrote something the model is stale: reload before the next plan. A
  second `apply` without reload raises `RepoStateError`.
- A refused file (read-only, not valid, not well-formed) drops the operations that needed
  it (`WRT-DEP-001`) and, as a consequence, operations that refer to their objects
  (`WRT-REF-001`). Everything else is still written. The pipeline decides whether an error
  finding means rollback.
- New events are reported only in `WriteOutcome.findings` (`WRT-INFO-EVENT`), never in the
  plan.

### Module map

| Module | Content |
| --- | --- |
| `xmltext.py` | tokenizer, offset index, `Document` (decode/encode, style detection, insertion, rendering) |
| `order.py` | child order tables of the elements that receive new containers |
| `templates.py` | the XML of every new element |
| `settings.py` | `[writer]` table |
| `session.py` | one computation: file checks, lookups in model plus change set, one handler per operation, placement |
| `writer.py` | `ArxmlWriter`: pre-flight checks, plan, apply, build_delta |
| `delta.py` | text of the delta file |
| `dcf.py` | the seam for registering a new file in the `.dcf` |
| `events.py`, `codes.py`, `cli.py` | event findings, finding codes, click commands |

## `[writer]` settings (porttool.toml)

A wrong value gives `WRT-SETTING-001` (warning) and the default is used.

| Key | Default | Meaning |
| --- | --- | --- |
| `new_object_placement` | `"existing_file"` | `"existing_file"`: into the file that holds the package with the most objects of that kind; else the file of the nearest existing parent package (the missing packages are created); else the file with the most objects of that kind. `"new_file"`: `<folder of that file>/<Name>.arxml`, with declaration, root tag, encoding, BOM, line ending and indentation of that file; a data type's compu method and constraint go into the same new file. |
| `uuid` | `"auto"` | `"auto"`: like the siblings in the container (the first and last five are looked at); none if they have none; in an empty or new container like any UUID in the file. New packages follow the first package of the file. `"upper"`, `"lower"`, `"none"`. Values are random (uuid4). |
| `access_point_pattern` | `"{prefix}_{port}_{target}"` | Short name of a new access point; also `{runnable}`. Made unique inside the runnable with `_1`, `_2`. |
| `access_point_prefixes` | implicit read `DRA`, implicit write `DWA`, explicit send `DSP`, explicit read and queued receive `DRP`, synchronous server call `SSCP`, asynchronous `ASCP` | Table, keys are the `AccessKind` texts. |
| `event_patterns` | periodic `TMT_{runnable}`, on data reception `DRT_{runnable}_{port}_{target}`, on data reception error `DRET_{runnable}_{port}_{target}`, on operation invocation `OIT_{runnable}_{port}_{target}`, init `IT_{runnable}` | Table, keys are the `TriggerKind` texts. Made unique inside the behavior. |
| `com_spec_for_every_element` | `true` | Com spec for every data element; `false`: only for elements with an init value. |
| `receiver_com_spec_defaults` | `true` | ALIVE-TIMEOUT 0, ENABLE-UPDATE false, HANDLE-NEVER-RECEIVED false, HANDLE-TIMEOUT-TYPE NONE in a new NONQUEUED-RECEIVER-COM-SPEC. |
| `client_com_spec` | `true` | CLIENT-COM-SPEC per operation in a new client port. |
| `queue_length` | `1` | QUEUE-LENGTH of QUEUED-RECEIVER-COM-SPEC and SERVER-COM-SPEC. |
| `server_call_timeout` | `"0"` | TIMEOUT of server call points. |
| `standard_impl_policy` | `false` | Write SW-IMPL-POLICY STANDARD for data elements that are not queued. |
| `compu_method_pattern` | `"CM_{name}"` | Name of the compu method of a new data type (package `config.packages.compu_methods`). |
| `constraint_pattern` | `"DC_{name}"` | Name of the data constraint (package `config.packages.constraints`). |
| `array_element_name` | `"Element"` | Sub-element of a new array type. |
| `data_type_mapping_set` | `""` | Path of a DATA-TYPE-MAPPING-SET. If set, a new application type whose `base_type_path` is an implementation data type gets a DATA-TYPE-MAP there. Empty: no map is written. |

## Finding codes

| Code | Severity | Meaning |
| --- | --- | --- |
| WRT-UNSUPPORTED-001 | error | operation or variant not supported yet |
| WRT-REF-001 | error | referenced object does not exist (component, port, runnable, interface, element, type, unit, prototype) |
| WRT-REF-002 | error | the object cannot be used that way (direction, interface kind, queued, level, base type) |
| WRT-REQ-001 | error | the request lacks a value the element needs (period, port, base type, size, ...) |
| WRT-NAME-001 | error | the short name is used by a different object |
| WRT-OWNER-001 | error | target component is model-owned or locked; nothing written |
| WRT-BEHAVIOR-001 | error | the SWC has no internal behavior |
| WRT-STATE-001 | error | file changed since the load (dry run; `apply` raises `RepoStateError`) |
| WRT-STATE-002 | error | file is not in the model or cannot be read (dry run; `apply` raises) |
| WRT-PARSE-001 | error | file cannot be indexed (nesting, encoding) |
| WRT-LOCATE-001 | error | an object of the model was not found in its file |
| WRT-PLACE-001 | error | no file for a new object |
| WRT-FILE-001 | error | new file would collide with an existing file name, also by case only |
| WRT-FILE-002 | error | the name is a Windows device name (NUL, CON, ...) |
| WRT-READONLY-001 | error | target file is read-only |
| WRT-LOCKED-001 | error | target file is held open by another program (after 3 retries) |
| WRT-WRITE-001 | error | any other refused write |
| WRT-XML-001 | error | result would not be well-formed; file left as it is |
| WRT-XSD-001 | error | result not valid against `config.xsd_file`; file left as it is |
| WRT-XSD-002 | warning | the XSD cannot be loaded; only well-formedness is checked |
| WRT-ABORT-001 | error | the write stopped; lists the files written and not written |
| WRT-DEP-001 | error | operation dropped because one of its files was refused |
| WRT-DELTA-001 | error | the delta file could not be written |
| WRT-SETTING-001 | warning | wrong `[writer]` setting |
| WRT-ORDER-001 | warning | position of a new container uncertain: a sibling is not in the order table |
| WRT-DCF-001 | warning | new file was not added to the `.dcf` |
| WRT-VALUE-001 | warning | part of the request was ignored (init value of a queued element, existing compu method reused, no type map) |
| WRT-DELTA-002 | warning | delta holds a component that is split over files as several elements |
| WRT-INFO-EXISTS | info | the object exists as requested; nothing to do |
| WRT-INFO-EVENT | info | an RTE event was created: path, runnable, requested task, position |
| WRT-INFO-DELTA | info | delta written (or nothing to write) |

## How to run

```
python3 -m pytest tests/unit/writer -q
mypy --strict porttool/writer tests/unit/writer
ruff check porttool/writer tests/unit/writer
python3 -m tests.unit.writer.coverage_trace [--missing]     # statement coverage without pytest-cov
python3 -m pytest tests/unit/writer/test_performance.py -q -s   # prints the timing
```

Tails, run in the sandbox on the final commit:

```
$ python3 -m pytest tests/unit/writer -q
........................................................................ [ 52%]
................................................................         [100%]
136 passed in 15.95s        (summary line from a run without the second -q)

$ mypy --strict porttool/writer tests/unit/writer
Success: no issues found in 26 source files

$ ruff check porttool/writer tests/unit/writer
All checks passed!
```

Coverage: 96.6 % of 1,594 statements of `porttool/writer` executed, measured with
`tests/unit/writer/coverage_trace.py` (a `sys.settrace` counter, same method as the reader's;
pytest-cov is not installed). Statements, not branches; the slow test is left out of that
run. Lowest modules: `__init__.py` 90.9 %, `events.py` 92.3 %, `cli.py` 94.7 %.

## Verification on the SMALL generated repo

`test_small_one_of_each.py::test_one_of_each_add_operation_on_the_small_repo`: 13 operations
(one data type, two interfaces, three ports, a server runnable, three access points, two
triggers, one connector, each using objects created earlier in the same change set, passed
through `ChangeSet.to_json` / `from_json`). After the apply and a reload: the model has
exactly 14 new objects (the 13 plus the data constraint), with direction, interface, com
specs, access kind (including queued receive), trigger kind, connector ends and element types
as requested; every other object is equal; the reader reports no finding; the five modified
files only gained bytes (the old bytes are a subsequence of the new ones).

The no-change round trip is byte-identical for every generated file and for ten style
variants (`test_xmltext.py`), and the index has the same elements as lxml.

## Corner cases and their tests

| Corner case | Test |
| --- | --- |
| read-only target | `test_safety.py::test_read_only_target` |
| locked target (PermissionError from the atomic write) | `test_safety.py::test_locked_target_permission_error_from_the_atomic_write` |
| file changed since load | `test_safety.py::test_file_changed_since_load` (+ `_is_seen_without_a_recorded_hash`, `test_file_deleted_or_unknown_since_load`, `test_file_changed_between_computation_and_write`) |
| SWC split over two files | `test_corner_cases.py::test_swc_split_over_two_files_ports_in_one_internal_behavior_in_another` (+ `test_swc_split_behavior_without_runnables_is_found_by_scanning`) |
| SWC with no PORTS element | `test_corner_cases.py::test_swc_with_no_ports_element` (+ `test_container_written_as_an_empty_element_is_opened`) |
| SWC without internal behavior | `test_corner_cases.py::test_swc_without_internal_behavior_gives_a_finding_and_the_port_is_still_added` (+ `test_trigger_on_swc_without_internal_behavior_names_the_missing_behavior`) |
| internal behavior without EVENTS / access container | `test_corner_cases.py::test_internal_behavior_without_events_and_without_the_access_container` |
| missing package for new objects | `test_corner_cases.py::test_missing_package_for_new_objects` (+ `test_no_file_for_a_kind_that_the_repository_does_not_have`) |
| name already used | `test_safety.py::test_name_already_used` |
| file-name collision, also case-only | `test_corner_cases.py::test_file_name_collision_for_a_new_file_including_case_only_difference` (+ `test_new_file_appears_between_plan_and_apply`) |
| non-ASCII names | `test_corner_cases.py::test_non_ascii_names` (+ `test_non_ascii_name_as_a_new_file_name`) |
| CRLF file | `test_corner_cases.py::test_crlf_file` |
| mixed line endings | `test_corner_cases.py::test_mixed_line_endings_use_the_one_of_the_containers_line` |
| tabs versus spaces | `test_corner_cases.py::test_tabs_versus_spaces` (+ `test_four_space_indentation_is_followed`) |
| no XML declaration | `test_corner_cases.py::test_no_xml_declaration` |
| UTF-8 BOM | `test_corner_cases.py::test_utf8_bom` |
| UTF-16 | `test_corner_cases.py::test_utf16` (little and big endian) |
| non-default namespace prefix | `test_corner_cases.py::test_non_default_namespace_prefix` |
| comments and CDATA near the insertion point | `test_corner_cases.py::test_comments_and_cdata_near_the_insertion_point` |
| attribute values containing `>` | `test_corner_cases.py::test_attribute_values_containing_a_greater_than_sign` |
| cancel between two files | `test_safety.py::test_cancel_between_two_files` (+ `test_cancel_before_anything_is_written`) |
| failure after some files are written | `test_safety.py::test_failure_after_some_files_are_written` (+ `test_unexpected_error_after_some_files_are_written_keeps_the_outcome`) |
| applying twice | `test_safety.py::test_applying_twice` (+ `test_same_request_twice_in_one_change_set_is_written_once`) |

Also tested: model-owned and locked components, schema error with a small stand-in XSD,
well-formedness check, journal order, that all text work runs in the pool, a file written on
one line, an unknown sibling element.

## Measured timings

One sandbox, 2 shared cores, files in the OS page cache, Linux file system. Noisy; three
runs are given as measured.

| Measurement | Result | Budget |
| --- | --- | --- |
| LARGE repo, 200 operations (100 ports + 100 triggers) in 100 SWC files, 13.1 MB: `plan()` | 0.76 s, 0.69 s, 0.83 s | (none) |
| same: `apply()` (recomputes, checks, writes 100 files) | 1.08 s, 1.05 s, 1.01 s | 5 s |
| indexing 100 SWC files of LARGE (13.2 MB) with the tokenizer alone | 0.48 s | (none) |

`apply` does the computation of `plan` again, so most of its time is indexing (pure Python,
GIL-bound, about 27 MB/s here), then SHA-256 of each file, the lxml well-formedness check and
100 atomic writes with fsync. With `xsd_file` set, compiling the real AUTOSAR XSD and
validating every touched file will add time that could not be measured here; the compiled
schema is cached in the writer object. Nothing is said about Windows (NTFS, antivirus).

## Unverified on Windows

| Item | State |
| --- | --- |
| Read-only attribute seen in the dry run (`st_mode & S_IWRITE`) | tested here with `chmod`; the Windows attribute is not |
| Sharing violation while replacing a file | tested with a fabricated `PermissionError`; the retry and backoff are `fsutil`'s; a real lock by DaVinci Developer is not |
| A file locked by another program is not noticed before the first write | by design today; a probe (open for writing without changing) would need Windows to verify |
| Case-insensitive file names for new files | compared with `casefold()` against the model, the session and the folder listing; tested on a case-sensitive file system |
| Windows device names (NUL, CON, COM1, ...) as file names | refused by a pattern; not verified that the list is complete |
| Paths longer than 260 characters | reads and stats go through `fsutil.long_path`; not tested |
| Fingerprint by size and mtime when the model has no SHA-256 | tested here; NTFS time stamp behaviour is not |
| `os.replace` over a file that DaVinci Developer has open for reading | not verified |
| git `autocrlf` rewriting line endings after the write | outside the writer; the writer keeps what is on disk |
| All timings | Linux only |

## PENDING-DISCOVERY assumptions

Each is marked in the code. Everything below follows the AUTOSAR 4.x standard as I know it;
none of it was compared with a golden diff.

1. **Order tables** (`order.py`): children of AUTOSAR, AR-PACKAGE, the component types,
   SWC-INTERNAL-BEHAVIOR, RUNNABLE-ENTITY, the two interfaces, DATA-TYPE-MAPPING-SET. Written
   from memory of the schema. The generated fixtures agree with them
   (`test_fixture_files_follow_the_order_tables`), but the fixtures were also written from
   memory. VARIATION-POINT of packaged elements is not in the tables (it only causes
   `WRT-ORDER-001`). Confirm with `xsd_file`.
2. **Port template**: P/R/PR-PORT-PROTOTYPE; PROVIDED-COM-SPECS before REQUIRED-COM-SPECS before
   the interface TREF; NONQUEUED-SENDER-COM-SPEC (DATA-ELEMENT-REF, INIT-VALUE);
   NONQUEUED-RECEIVER-COM-SPEC with the four default children; QUEUED-SENDER-COM-SPEC;
   QUEUED-RECEIVER-COM-SPEC with QUEUE-LENGTH; SERVER-COM-SPEC with QUEUE-LENGTH;
   CLIENT-COM-SPEC. Which of these DaVinci writes, and with which extra children
   (USES-END-TO-END-PROTECTION, FILTER, ...), is unknown.
3. **Init values**: NUMERICAL-VALUE-SPECIFICATION for numbers, TEXT-VALUE-SPECIFICATION
   otherwise, CONSTANT-REFERENCE for the path of a known constant, ARRAY/RECORD for `[..]` /
   `{..}` (the reader's textual forms). No SHORT-LABEL. DaVinci may prefer constants.
4. **Connector template**: ASSEMBLY-SW-CONNECTOR with PROVIDER-IREF / REQUESTER-IREF;
   TARGET-P-PORT-REF / TARGET-R-PORT-REF with DEST PR-PORT-PROTOTYPE for PR ports.
5. **Runnable template**: MINIMUM-START-INTERVAL 0, CAN-BE-INVOKED-CONCURRENTLY false, SYMBOL.
6. **Access point templates**: VARIABLE-ACCESS with ACCESSED-VARIABLE/AUTOSAR-VARIABLE-IREF in
   DATA-READ-ACCESSS, DATA-WRITE-ACCESSS, DATA-SEND-POINTS, DATA-RECEIVE-POINT-BY-ARGUMENTS
   (explicit read and queued receive; never BY-VALUES); SYNCHRONOUS- / ASYNCHRONOUS-
   SERVER-CALL-POINT with OPERATION-IREF and TIMEOUT.
7. **Event templates**: TIMING-EVENT (PERIOD), DATA-RECEIVED-EVENT and DATA-RECEIVE-ERROR-EVENT
   (DATA-IREF), OPERATION-INVOKED-EVENT (OPERATION-IREF), INIT-EVENT; START-ON-EVENT-REF first.
8. **Short names** of access points and events (`access_point_pattern`, prefixes,
   `event_patterns`): guesses that match the fixture generator, not DaVinci.
9. **Interface templates**: IS-SERVICE false; VARIABLE-DATA-PROTOTYPE with SW-DATA-DEF-PROPS only
   for queued elements; CLIENT-SERVER-OPERATION with ARGUMENTS (TYPE-TREF, DIRECTION); no
   POSSIBLE-ERRORS; no SERVER-ARGUMENT-IMPL-POLICY.
10. **Data type templates**: implementation value (CATEGORY VALUE, BASE-TYPE-REF,
    COMPU-METHOD-REF, DATA-CONSTR-REF); boolean = VALUE on a base type with encoding BOOLEAN;
    array and record with IMPLEMENTATION-DATA-TYPE-ELEMENT (TYPE_REFERENCE, or VALUE for a base
    type; ARRAY-SIZE + ARRAY-SIZE-SEMANTICS FIXED-SIZE); type reference. Application primitive
    (CATEGORY VALUE or BOOLEAN), array (ELEMENT with MAX-NUMBER-OF-ELEMENTS), record. No
    SW-CALIBRATION-ACCESS, no SW-IMPL-POLICY, no TYPE-EMITTER.
11. **Compu method and constraint**: TEXTTABLE with one closed scale per value and COMPU-CONST/VT;
    LINEAR with numerator (offset, factor) and denominator 1; a unit without scaling at
    implementation level gives a LINEAR 1:0 method that carries the unit; DATA-CONSTR with
    INTERNAL-CONSTRS (implementation) or PHYS-CONSTRS (application). Names `CM_<type>` /
    `DC_<type>`.
12. **UUIDs**: which elements carry one and in which letter case is copied from the siblings;
    nested new identifiables (data elements, arguments, sub-elements) follow their parent.
13. **New packages**: AR-PACKAGE with SHORT-NAME only, appended to AR-PACKAGES.
14. **Placement** rules of `existing_file` and the folder rule of `new_file`; one object per
    new file, compu method and constraint in the data type's file.
15. **`.dcf`** (`dcf.py::register_new_file_in_dcf`): not written, warning only.
16. **Position inside a container**: new elements are appended at the end. If DaVinci sorts
    (by name, by kind), a later save in DaVinci will reorder them.
17. **Data type map**: DATA-TYPE-MAP in DATA-TYPE-MAPS, only with the setting.
18. **Delta file** (`delta.py`): packages by SHORT-NAME only, whole elements, UTF-8; what DVImEx
    accepts is unknown. Existing element text is moved to the delta's indentation and takes
    the line ending of the first source file (white space inside multi-line text content can
    change).
19. **Nothing else is updated**: no ADMIN-DATA, no time stamp, no revision label. If DaVinci
    keeps such data per file or per element, the golden diffs will show it.

## Contract change requests (porttool/core is frozen for me; all worked around)

1. `Finding.operation_id`. Today: the message prefix `[op <id>]` plus `row`.
2. A structured list of created events in `WriteOutcome` (path, runnable, task, position).
   Today: `WRT-INFO-EVENT` findings and `created_events()`.
3. Per-operation status in `WritePlan` and `WriteOutcome` (planned, already there, failed).
   Today: derived from `applied_operation_ids` and finding codes.
4. `Writer.apply` cannot return what it wrote when it is cancelled or fails with an
   exception. Today: `ArxmlWriter.last_outcome` and `JobCancelled.detail`.
5. The `Writer` protocol should say that `plan` and `build_delta` block and read files (run
   them through `run_blocking`), and that the model is stale after `apply`.
6. Origin of the internal behavior (reader's request 9). Today the writer looks into the
   files of the SWC's runnables, events and ports and, if needed, scans the other files.
7. `fsutil.atomic_write_bytes(..., check=callable)` called with the temporary file before
   the replace, so that the XSD check can run on the copy on disk as the task asked.
8. `AddDataType`: a field for the implementation type an application type maps to. Today
   `base_type_path` is used for that when `data_type_mapping_set` is set.
9. `AddDataType.unit`: say whether it is a path or a name. Today both are accepted (a name
   must be unique among the units, SHORT-NAME or DISPLAY-NAME).
10. `InitValueSpec.value`: say which textual forms are allowed. Today: the reader's forms.
11. `PackagePaths`: no place for a mapping set or for a "new files" folder. Today `[writer]`.
12. `AddTrigger.position_after_event` and `task` are passed through untouched; the contract
    should say that the writer ignores them.

## Open questions

- Agent 0 / owner: every PENDING-DISCOVERY item, first of all a golden diff for "add one
  port", "add one access point", "add one event" and "add one connector" saved by DaVinci
  Developer, and the AUTOSAR XSD file name for `xsd_file`.
- Agent 7 (pipeline): is an error finding in `WriteOutcome` always a rollback? The writer
  keeps going after a refused file (see conventions) so that the report can name every
  problem at once.
- Agent 4 (validator): the writer repeats a few checks (direction against access kind,
  target in the interface, owner) as defence in depth; they should never fire after a
  validated change set.

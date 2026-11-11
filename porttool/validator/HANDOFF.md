# porttool/validator - hand-off

Branch `agent/validator`. Owner: Agent 4. Everything below was run in the Linux build
sandbox (Python 3.13); the code is pure Python 3.11-compatible logic with no platform
branch.

## What is done

- `ChangeValidator.validate(model, config, rows, pending, now_utc) -> ValidationOutcome`,
  exactly the `porttool.core.interfaces.Validator` protocol. Pure: no file, no tool, no
  clock, no exception for a bad request (12 x 400 random rows and all table rows in one
  import are part of the tests).
- Rows to operations for every request sheet: `AddDataType`, one `AddInterface` per
  interface (Interfaces row plus its InterfaceElements rows), `AddPort`, `ModifyPort`,
  `DeletePort`, `AddServerRunnable`, `AddAccessPoint`, `DeleteAccessPoint`, `AddTrigger`,
  `DeleteTrigger`, `AddConnector`, `DeleteConnector`.
- The full rule set of the task (names, data types, interfaces, ports, connectors,
  multiplicity, access points, triggers, ownership), each failure with its own code.
- Server runnables and task defaults; routing (apply / model / hold); pending queue
  review (close, mismatch, release, age) before new rows are judged.
- Conflict detection between rows, references between rows regardless of row order,
  ALREADY_APPLIED for requests the repo fulfils.
- ChangeSet ordered by `APPLY_ORDER` (stable inside a kind), deterministic ids,
  `baseline_commit`, `model_hash`, `created_utc` set.
- `register_cli(app: click.Group)` adds `validator-codes` (prints the table below).

## What is not done (v1 limits, all rejected with a clear Finding)

- MODIFY/DELETE of data types, interfaces and interface elements; MODIFY of runnable
  access, triggers and connections (`VAL-ROW-008`). The contract has no operation for them.
- Adding an element to an existing interface (`VAL-IF-004`).
- Delegation connectors and connectors across compositions (`VAL-CON-005`).
- Creating units, mode declaration groups, parameter/NV-data/trigger interfaces,
  runnables other than server runnables, parameter access, data-send-completed triggers.
- Init values of array, record and string types (only `none`, `VAL-PORT-008`).
- Names of compu methods / data constraints that the writer derives for a new data type
  are not checked for clashes (the validator does not know the writer's naming).
- Mode names of mode-switch triggers are not checked (the model has no mode declarations).
- Held connectors that are still on hold are not counted in the multiplicity of new
  rows; a conflict shows up when the held connector is released (`VAL-PEND-005`).

## Public API

```python
from porttool.validator import ChangeValidator, validate, register_cli, codes
from porttool.validator import ALL_CODES, CODES, describe, format_code_table, request_id_of

outcome = validate(model, config, rows, pending, now_utc)  # pure function
outcome = ChangeValidator().validate(model, config, rows, pending, now_utc)
```

- `outcome.change_set`: operations of this import with route apply, model and hold, plus
  held connectors released by this run (route apply).
- `outcome.results`: exactly one `RequestResult` per row, in row order.
  `request_id` is `<workbook path>#<sheet>#<row number>` (`request_id_of(row)`).
  `operation_ids` are the ids of the operations in `change_set` that carry the request
  (empty for REJECTED and ALREADY_APPLIED). Merged duplicates share the ids of the first row.
- `outcome.pending`: entries that stay, then one new entry per model/hold operation.
- `outcome.findings`: findings without a row (pending queue, configuration).
- `porttool.validator.codes`: one constant per code, `CODES[code].severity/.description`.
- Modules starting with `_` are internal.

Operation ids: `<Sheet>:<op>:<names>`, e.g. `Ports:add_port:/ComponentTypes/SwcA/RpNew`,
`Connections:add_connector:<composition path>/<provider>.<port>/<requester>.<port>`,
`Ports:add_server_runnable:<swc path>/<runnable>` (runnable created for a Ports row) or
`RunnableTriggers:add_server_runnable:...` (runnable named by a trigger row).

## How to run the tests

```
python3 -m pytest tests/unit/validator
mypy --strict porttool/validator tests/unit/validator
ruff check porttool/validator tests/unit/validator
```

Tails of the last run:

```
$ python3 -m pytest tests/unit/validator
347 passed in 2.17s
$ python3 -m mypy --strict porttool/validator tests/unit/validator
Success: no issues found in 29 source files
$ ruff check porttool/validator tests/unit/validator
All checks passed!
```

Test files (all under `tests/unit/validator`):

| File | Content |
| --- | --- |
| `rule_cases.py`, `test_rule_table.py` | 194 table cases; meta-tests prove every one of the 105 codes is reported by at least one case and exercised without being reported by at least one case |
| `test_corner_cases.py` | one named test per corner case of the task (28 tests) |
| `test_changes_and_pending.py` | RowID-first / names-second lookup, pending review per operation kind |
| `test_outcome.py` | apply order, id determinism, purity, protocol, CLI |
| `test_parsers.py` | cell parsers, name rules, type ranges |
| `test_robustness.py` | random rows, all table rows in one import |
| `test_performance.py` | large-model timings |
| `support.py` | richer test model, row builders, invariants checked on every outcome |

Coverage: pytest-cov is not installed. Measured with the standard library
(`python3 -m trace --count --summary`, performance tests deselected): 97.1 % of 4,279
counted lines of `porttool/validator`; lowest module `_ports.py` 95 %. This is `trace`'s
line counting, not pytest-cov's.

## Measured timings (sandbox, 2 cores, Python 3.13)

Large synthetic model built with the `Model` constructors: 300 SWCs, 20,100 ports,
15,000 connectors, 500 interfaces. Mixed import (data types, interfaces, elements, ports,
connections, access points, triggers, connector deletes).

| Import | Time | Budget |
| --- | --- | --- |
| 500 requests | 16 ms | 2 s |
| 10,000 requests | 0.37 s | (to be reported) |
| 10,000 requests against the small model, including the test invariants | 0.46 s | - |

No request scans all ports or connectors. Extra indexes (names, prototypes, scopes) are
built once per `validate()` call on first use. The RowID is checked against the object
named by the row's cells (one hash); the reverse RowID index over all objects of a sheet
is built only when a row's RowID does not match its names.

## Finding codes

Severity is the default; `error` rejects the request, `warning` and `info` do not.

| Code | Severity | Meaning |
| --- | --- | --- |
| VAL-ROW-001 | error | The row comes from a sheet that takes no requests. |
| VAL-ROW-002 | error | The Action cell is empty or not ADD, MODIFY or DELETE. |
| VAL-ROW-003 | error | A required cell is empty. |
| VAL-ROW-004 | error | Action ADD on a row that describes an existing object. |
| VAL-ROW-005 | error | Action MODIFY or DELETE on a new row (no RowID). |
| VAL-ROW-006 | error | The object of this row no longer exists in the repo (stale workbook baseline). |
| VAL-ROW-007 | warning | The RowID is outdated; the object was found by its names instead. |
| VAL-ROW-008 | error | MODIFY or DELETE is not supported for this sheet in v1. |
| VAL-ROW-009 | error | Identity cells differ from the object of the RowID (rename or move). |
| VAL-ROW-010 | error | A cell does not have the expected format (number, flag, list). |
| VAL-NAME-001 | error | The name is not a valid AUTOSAR short name. |
| VAL-NAME-002 | error | The name is longer than the configured maximum length. |
| VAL-NAME-003 | error | The name does not match the project naming rule. |
| VAL-NAME-004 | error | The name is a C keyword. |
| VAL-NAME-005 | error | An object with this name already exists in the same scope. |
| VAL-NAME-006 | error | The name differs only by letter case from another name in the same scope. |
| VAL-REF-001 | error | The SWC does not exist. |
| VAL-REF-002 | error | Several SWCs have this short name. |
| VAL-REF-003 | error | The port interface does not exist. |
| VAL-REF-004 | error | Several port interfaces have this short name. |
| VAL-REF-005 | error | The data type does not exist. |
| VAL-REF-006 | error | Several data types have this short name. |
| VAL-REF-007 | error | The composition does not exist. |
| VAL-REF-008 | error | Several compositions have this short name. |
| VAL-REF-009 | error | The composition has no component with this name. |
| VAL-REF-010 | error | The SWC has no port with this name. |
| VAL-REF-011 | error | The SWC has no runnable with this name. |
| VAL-REF-012 | error | The element, operation or argument is not part of the port's interface. |
| VAL-REF-013 | error | The request needs another request that was rejected. |
| VAL-REF-014 | error | The interface of the port is missing in the repo, so the port cannot be checked. |
| VAL-OWN-001 | error | The SWC is locked and is never edited. |
| VAL-OWN-002 | error | Service SWCs are read-only. |
| VAL-OWN-003 | error | A composition used as SWC is read-only. |
| VAL-OWN-004 | error | Connectors of this composition cannot be edited (locked or model-owned). |
| VAL-DT-001 | error | The level is not application or implementation. |
| VAL-DT-002 | error | The data type category is unknown or not supported in v1. |
| VAL-DT-003 | error | The base type does not exist. |
| VAL-DT-004 | error | The lower limit is greater than the upper limit. |
| VAL-DT-005 | error | The range or an enumeration value does not fit the base type. |
| VAL-DT-006 | error | An enumeration value is used twice. |
| VAL-DT-007 | error | An enumeration label is used twice. |
| VAL-DT-008 | error | The array size is not a positive whole number. |
| VAL-DT-009 | error | A record element name is used twice. |
| VAL-DT-010 | error | The data type contains itself (circular definition). |
| VAL-DT-011 | error | A cell is filled that does not apply to this category. |
| VAL-DT-012 | error | The unit does not exist. |
| VAL-DT-013 | error | The linear scaling is invalid (factor zero). |
| VAL-DT-014 | error | An element type has another level than the type that uses it. |
| VAL-IF-001 | error | The interface has no element and no operation. |
| VAL-IF-002 | error | The interface kind is unknown or cannot be created in v1. |
| VAL-IF-003 | error | The mode group of the mode-switch interface cannot be resolved. |
| VAL-IF-004 | error | An existing interface cannot be changed in v1. |
| VAL-IF-005 | error | Two rows define the same element differently. |
| VAL-IF-006 | error | Two rows define the same argument differently. |
| VAL-IF-007 | error | The argument direction is not IN, OUT or INOUT. |
| VAL-IF-008 | error | A cell is filled that does not apply to this interface kind. |
| VAL-IF-009 | error | The interface is not created because another row of it was rejected. |
| VAL-PORT-001 | error | The port direction is not P, R or PR. |
| VAL-PORT-002 | error | PR ports need a sender-receiver style interface. |
| VAL-PORT-003 | error | A non-queued data element has neither an init value nor an explicit none. |
| VAL-PORT-004 | error | The init value cell cannot be matched to the elements. |
| VAL-PORT-005 | error | The init value is not a value of the element's data type. |
| VAL-PORT-006 | error | The init value is outside the range of the data type. |
| VAL-PORT-007 | error | An init value is given where none can be stored. |
| VAL-PORT-008 | error | Init values of array and record types are not supported in v1 (use none). |
| VAL-PORT-009 | error | The port is still used and cannot be deleted. |
| VAL-PORT-010 | error | The new interface does not fit the existing use. |
| VAL-PORT-011 | warning | The port exists as requested, but with another init value. |
| VAL-CON-001 | error | A connector needs a P or PR port as provider and an R or PR port. |
| VAL-CON-002 | error | A port cannot be connected to itself. |
| VAL-CON-003 | error | The interfaces of the two ports are not compatible. |
| VAL-CON-004 | error | The two ports are already connected by another connector. |
| VAL-CON-005 | error | No composition contains both components. |
| VAL-CON-006 | error | More than one composition contains both components. |
| VAL-MUL-001 | error | Sender-receiver communication would become n:m. |
| VAL-MUL-002 | error | A client port can be connected to one server only. |
| VAL-MUL-003 | error | This kind of requester port accepts one provider only. |
| VAL-MUL-004 | warning | Several senders feed one receiver (n:1). |
| VAL-ACC-001 | error | The access kind is unknown or not supported in v1. |
| VAL-ACC-002 | error | The access kind does not fit the port direction or interface kind. |
| VAL-ACC-003 | error | The access kind does not fit the queued setting of the element. |
| VAL-ACC-004 | error | The runnable already accesses this target in another way. |
| VAL-TRG-001 | error | The trigger kind is unknown or not supported in v1. |
| VAL-TRG-002 | error | A periodic trigger needs a period greater than zero. |
| VAL-TRG-003 | error | A cell is filled that does not apply to this trigger kind. |
| VAL-TRG-004 | error | The trigger kind does not fit the port. |
| VAL-TRG-005 | error | The task does not exist. |
| VAL-TRG-006 | error | The server operation already has a runnable. |
| VAL-TRG-007 | error | The runnable runs in several tasks; the Task cell must name one. |
| VAL-TRG-008 | error | Deleting the trigger would leave a server operation without a runnable. |
| VAL-TRG-009 | warning | The trigger exists as requested, but is mapped to another task. |
| VAL-CFL-001 | info | The same request appears more than once and was merged. |
| VAL-CFL-002 | error | Several rows request the same object differently. |
| VAL-CFL-003 | error | One object is added or changed and deleted in the same import. |
| VAL-CFL-004 | error | A request uses a port that another row deletes. |
| VAL-CFL-005 | error | The request differs from a request in the pending queue. |
| VAL-PEND-001 | info | A pending request is fulfilled by the repo and closed. |
| VAL-PEND-002 | error | The awaited object exists, but not as requested; the entry is kept. |
| VAL-PEND-003 | info | A held connector is released because its ports exist. |
| VAL-PEND-004 | warning | A pending request is older than the configured limit. |
| VAL-PEND-005 | error | A held connector cannot be released because it is no longer valid. |
| VAL-PEND-006 | warning | A pending request cannot be checked because its target is gone. |
| VAL-PEND-007 | info | The request is already in the pending queue. |
| VAL-CFG-001 | error | A naming rule of the project is not a valid regular expression. |
| VAL-CFG-002 | error | A name pattern of the project cannot be filled in. |

## Decisions where the task was silent

Rows and results
1. `request_id` = `<workbook path>#<sheet>#<row>`; unique per row (a second row at the
   same position gets `~2`).
2. Exactly one result per row. Pending-queue events (closed, mismatch, released, old)
   are in `outcome.findings`, not in `results`.
3. The SWC cell also accepts an absolute path (the only way to address one of two SWCs
   with the same short name). Informational cells (interface kind, connected, partners)
   are ignored.
4. Numbers use a dot as decimal separator; hexadecimal whole numbers are accepted.
   Kind texts are read case-insensitively, with spaces, hyphens and underscores folded.

Ownership
5. Owner = the stricter of `Component.owner` and `config.owner_of(name)`. Read-only:
   LOCKED, `ComponentKind.SERVICE`, compositions. Connectors inside a composition that is
   locked or model-owned are rejected (`VAL-OWN-004`). Connectors to ports of locked or
   service SWCs are allowed (the composition is edited, not the SWC).

ALREADY_APPLIED
6. Port: same name, direction and interface (another init value gives the warning
   `VAL-PORT-011`). Connector: the two port instances are connected (a row that asks for
   another connector name is rejected, `VAL-CON-004`). Access point and trigger: an
   identical one exists. Data type / interface: exists with the requested definition.
   Element row for an existing interface: the element exists as requested. MODIFY that
   changes nothing.

Conflicts
7. Add plus delete of one object, and a request (connector, access, trigger) on a port
   that another row deletes: all rows involved are rejected.
8. DELETE never cascades. A port can only be deleted when the same import deletes every
   connector, access point and trigger on it (`VAL-PORT-009`).
9. A RowID that no longer resolves, with names that do not resolve either, is REJECTED
   for DELETE too (renamed and removed cannot be told apart). RowID outdated but names
   resolve: accepted with warning `VAL-ROW-007`. RowID resolves but identity cells differ:
   rejected (`VAL-ROW-009`).
10. Merged duplicates: the first row carries the operation (requester, comment, source);
    the others get the same status and operation ids and an info `VAL-CFL-001`. When the
    first row is rejected, its error findings are copied to the others.
11. Interface rows: all or nothing. If one row of an interface is rejected, every row of
    it is (`VAL-IF-009`). Two workbooks that both hold the Interfaces row must define the
    same elements.

Data types and init values
12. Level and category are required. An implementation value/boolean/enumeration type
    needs a base type. A filled cell that does not belong to the category is rejected.
13. Range fit: limits are converted to internal values when a factor is given. With an
    unknown encoding only the sign-independent limits are checked; 32/64-bit types
    without encoding and unknown encodings are not checked.
14. Units must exist (short name, display name or path); `AddDataType.unit` carries the
    absolute path of the existing unit.
15. Array and record element types must have the level of the type that uses them.
16. Init values are required for SENDER_RECEIVER only; NV-data ports may have them. A
    bare `none` covers every non-queued element. A queued element takes none. For a type
    that is not loaded, any number or symbolic name is accepted.
17. PR ports: sender-receiver and NV-data interfaces.

Behavior
18. A runnable cannot have two access points of the same class (read: implicit read,
    explicit read, queued receive; write: implicit write, explicit send; call: sync,
    async) on one target (`VAL-ACC-004`); delete the old one in the same import to replace.
19. NV-data ports take the read/write access kinds. A mode switch point needs a P/PR port.
20. Trigger kinds init and background are accepted (no port, no period).
21. Server runnables: the pattern knows `{port}`, `{operation}`, `{swc}`. A trigger row
    with kind "on operation invocation" on a NEW server port creates the runnable under
    the given name, unless that runnable exists. One new server runnable serves one
    operation. If the row that names the runnable is rejected, the port is rejected too
    (the port must not end up without a runnable). On a port that waits in the pending
    queue such a row is rejected (`VAL-TRG-006`).
22. Task default: the new event is placed after the mapped event with the highest
    position in that task. Events deleted by the same import do not count.
23. Deleting the only operation-invoked trigger of a server operation is rejected unless
    the port is deleted or another row assigns a runnable (`VAL-TRG-008`).

Connectors
24. Compatible = same interface, or same kind and everything the requester has exists on
    the provider side with the same type (operations: same arguments; mode-switch: same
    mode group).
25. Multiplicity counts the model, minus accepted deletes, plus accepted new connectors
    (apply, hold and released). Kinds other than sender-receiver/NV-data: one provider
    per requester.
26. A default connector name that is taken is rejected, not altered. A name becomes free
    when the same import deletes the connector that has it.

Routing and pending queue
27. The ChangeSet holds the model and hold operations of this import, also when the same
    request is already queued (info `VAL-PEND-007`, not queued twice). Entries that still
    wait from earlier runs are not emitted again.
28. A released connector leaves the queue and enters the ChangeSet with route apply and
    its old id. The pipeline must store `outcome.pending` only together with a
    successful apply of `outcome.change_set`.
29. `depends_on`: held connector = paths of its ports that do not exist yet; waiting
    port = interface created by the same import; waiting access/trigger/server runnable =
    the port if it does not exist yet.
30. Closing: port by name, direction and interface; server runnable and its trigger when
    the operation has any runnable (the message names it); other kinds when the requested
    state is in the repo. Entries whose SWC, composition or component is gone are kept
    and reported (`VAL-PEND-006`). Age is reported on every run; an unreadable
    `created_utc` is reported; an unreadable `now_utc` disables the age check.
31. A new row that differs from a queued request with the same id is rejected
    (`VAL-CFL-005`).
32. Deletes are validated and emitted whether or not the configuration allows deletes;
    the pipeline decides.

Configuration
33. A naming regex that does not compile gives one `VAL-CFG-001` and rejects every name
    of that kind (`VAL-NAME-003`). A name pattern that cannot be filled gives `VAL-CFG-002`.

## Contract change requests (porttool/core is unchanged)

1. `AddInterface`: add `mode_group_path`; `Model`: add the mode declaration groups with
   their modes. Today a new mode-switch interface is encoded as one `ElementSpec` whose
   `type_path` is the mode group path (see PENDING-DISCOVERY).
2. `AddTrigger`: mode-switch events need the mode and the activation (entry/exit).
3. `AddDataType.unit`: say whether it is a path or a name (the validator emits the path)
   and whether the writer creates missing units.
4. `InitValueSpec`: constant reference and composite values for arrays and records.
5. `RequestResult.request_id`: define its meaning (see decision 1) and what the Results
   sheet shows in its RowID column for new rows.
6. `ProjectConfig`: a setting that allows deletes (the task refers to one; none exists).
7. `DataType`: a field for the target of a TYPE_REFERENCE.
8. `PortInterface` (mode-switch): say whether `elements` holds the mode group prototype.
9. `AddServerRunnable`: one runnable for several operations cannot be expressed.
10. `Model`: lookups `find_prototype(composition_path, name)` and triggers by port would
    remove two indexes the validator builds itself.

## PENDING-DISCOVERY assumptions (marked in the code)

- `_interfaces.py`: encoding of a new mode-switch interface (request 1).
- `_typeinfo.py`: base type encoding texts `2C`, `NONE`, `IEEE754`, `BOOLEAN`; the target
  of a TYPE_REFERENCE is read from `element_type_paths[0]`.
- Package paths for new objects come from `config.packages` (placeholders in the contract).

## Unverified on Windows

Nothing in this package depends on the platform. Workbook paths with backslashes are
only shortened for messages (tested here with Windows-style path strings).

## Open questions

- Should a connector to a port whose request was rejected say "port request rejected"
  instead of `VAL-REF-010` (unknown port)? Today it reports `VAL-REF-010`.
- Should model/hold operations that are already queued be left out of the ChangeSet
  (decision 27)?

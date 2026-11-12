# PortTool 0.1.0: what is verified and what is not

## Verified in the build sandbox (Linux, Python 3.13, generated repositories, fake tools)
- 1,773 automated tests pass; mypy --strict and ruff are clean; all code compiles on
  Python 3.11.
- Reader against an independently written repository generator: identical objects on a
  3-SWC and a 300-SWC / 20,000-port / 15,000-connector repository.
- End to end with all real parts: export workbook, edit, import, validate, plan, apply on a
  new git branch, reload and verify, for one request of every kind (data type, interface
  with element, ports, explicit and implicit access, trigger, connector).
- A failure injected after the writer changed files restores every byte.
- Crash recovery from the journal alone (process killed at each step, with a fake writer).
- DaVinci steps check / validate / generate through the real runner with fake tools.
- The web interface on the real service: full cycle through its API, and the page in
  headless Chrome without console errors.

## Measured here (2 shared CPU cores; treat as rough)
| Operation | Measured | Budget |
| --- | --- | --- |
| Cold load, 2,000 files, 556 MB | 6.1 s | 30 s |
| Warm load from cache | 0.5 s | 3 s |
| Excel export / import, 57,700 rows | 3.1 s / 3.4 s | 10 s / 5 s |
| Validation of 500 requests | 16 ms | 2 s |
| Write of 200 operations in 100 files | 1.1 s | 5 s |
| GUI table page, 20,000 ports, warm | 1 to 5 ms | 200 ms |

## NOT verified
- Nothing has run on Windows: file locking, long paths, console encoding, process-tree
  kill, default browser launch, git for Windows line-ending settings.
- Nothing has been checked against the AUTOSAR XSD (none was available) or against a file
  saved by DaVinci Developer. The XML the writer produces follows the AUTOSAR 4.x schema
  from memory; 19 assumptions are listed in porttool/writer/HANDOFF.md.
- The real formats of the .dcf and .dpa files are unknown. New data types and interfaces
  go into an existing file by default, so the .dcf is not touched.
- No real Vector tool has been started. All command lines are UNVERIFIED templates.
- The Groovy script for task mapping has never been executed.
- The exported workbook has never been opened in real Excel.

## Not built yet
- Modify and delete requests (rejected with a clear message).
- Mode-switch triggers; parameter, NV-data and trigger interfaces.
- Connections across nested compositions (delegation ports).
- Bus signal data mapping, service port mapping.
- A separate QA pass: soak test, cross-package corner-case matrix, benchmark history.
- A frozen .exe package; the tool runs from a Python virtual environment.

Each package has a HANDOFF.md with its public API, settings, finding codes, assumptions
marked PENDING-DISCOVERY and what is unverified on Windows.

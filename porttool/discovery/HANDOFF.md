# HANDOFF: porttool/discovery (Agent 0-TOOL, `porttool discover`)

Branch `agent/discovery`. Owned folders: `porttool/discovery/`, `tests/unit/discovery/`.
Nothing else was touched; `porttool/core` is used as it is.

## What is done

`porttool discover run` and `porttool discover golden-diff`, as click commands backed by
tested functions. Both are read-only on the ECU repo: the only thing written is the
report folder (and, with `--anonymize`, a private folder next to it).

    porttool discover run --repo <path> [--dpa <file>] [--sip <folder>]
        [--developer-bin <folder>] [--out <folder>] [--anonymize] [--no-tools]
        [--exclude <glob>]... [--git <git.exe>]
    porttool discover golden-diff --repo <path> --base <commit> --head <commit>
        [--out <folder>] [--anonymize] [--git <git.exe>]

Until the main CLI exists the same commands run as
`python -m porttool.discovery run ...` and `python -m porttool.discovery golden-diff ...`.

Report folder (default `<repo>/porttool_discovery`):

| File | Content |
| --- | --- |
| `discovery.json` | complete report: `inventory`, `storage_map`, `workspace`, `xml_style`, `ecuc`, `composition`, `owners`, `lock_signs`, `git`, `tools`, `steps`, `step_errors`, `findings`, `mode` |
| `discovery.md` | readable summary, built only from the JSON |
| `cli/<tool>_<flag>.txt` | stdout, stderr and exit code of every help attempt (`dashdash-help`, `dash-h`, `dash-question`, `slash-question`, `help`) |
| `owners_proposed.csv`, `locked_proposed.txt` | proposal for owners.csv (model / davinci) and the locked candidates |
| `golden_diff_<base8>_<head8>.json` / `.md` | one pair per golden-diff run, so several reference changes can sit side by side |
| `manifest.json`, `README.txt` | which reports are in the folder, their mode, what to send back and what each mode contains |

The ten steps of `run` (each isolated; a failing step becomes finding `DISC-STEP-FAILED`,
the rest still runs; one bad file never stops the inventory):

1. Inventory: every `*.arxml`, `*.ecuc.arxml`, `*.dcf`, `*.dpa`; count, size, largest files;
   per ARXML file namespace, schemaLocation, top-level element kinds, selected nested tags;
   repo-wide tag histogram; list of empty, malformed, unreadable files.
2. Storage map: per element kind the folders, AR-PACKAGE paths, one-object-per-file or
   many, file naming pattern relative to the short name, child elements, kinds that share a
   file, objects defined in more than one file.
3. Workspace index: generic XML summary of every .dcf and .dpa (tag tree, attribute names,
   counts, up to three samples per leaf), every path-like value resolved against the file's
   folder (and, for comparison, against the repo root), listed-but-missing and
   on-disk-but-not-listed ARXML files, case differences. A file that is not XML is described
   by the shapes of its lines. No tag name is assumed.
4. XML style per folder: declared encoding, BOM, line endings, indentation unit, declaration
   text, root prefix and root start tag, trailing newline, attribute quote, empty-element
   style, UUID format and version digit, ADMIN-DATA parents and SDG/SD GID names.
5. ECUC: all module configurations; for Rte and Os every DEFINITION-REF suffix with element
   tag, definition kind and count; DEST of the VALUE-REFs; counts of mapped events and tasks.
6. Composition shape: count, nesting, connectors and their files, root candidates.
7. Model-owned and locked candidates, with the evidence, plus the proposed files.
8. Lock and temporary files with timestamps.
9. Git: branch, HEAD, clean or dirty (counts only), autocrlf and related config, attributes
   of one sample file per type (text, eol, filter=lfs), generated folders ignored or tracked.
10. Tools: search, size, time, file version, then help flags only.

## Public API

```python
from porttool.discovery import (
    register_cli,  # register_cli(group: click.Group) -> None
    run_discovery,  # run_discovery(options: DiscoveryOptions, progress=None) -> DiscoveryResult
    run_golden_diff,  # run_golden_diff(options: GoldenDiffOptions, progress=None) -> DiscoveryResult
    DiscoveryOptions,  # repo, dpa, sip, developer_bin, out, anonymize, no_tools, exclude,
    # git_executable, tool_timeout_s, tool_suffixes, tool_launcher, environ
    GoldenDiffOptions,  # repo, base, head, out, anonymize, git_executable
    DiscoveryResult,  # out_dir, private_dir, report (the JSON as a dict), files_written, summary
)
```

`run_discovery` and `run_golden_diff` raise only `ConfigError` (repo folder missing),
`WriteError` (report folder unusable) and, for golden-diff, `GitError` (git missing, not a
repo, unknown commit). Everything else is a `Finding` in the report.
Exit codes: 0 when the report was written, even with findings of severity error (this is
what the task asks for and differs from the general "1 on error findings" rule); 2 on a
`PortToolError` or an unexpected failure (message plus the path of a crash log under
`%LOCALAPPDATA%\PortTool\logs`, never a traceback).

Modules: `names` (identity or tokens), `fsio` (long paths, walk, atomic write), `xmlstyle`
(byte-level style), `xmlfeed` (streaming expat driver), `arxml_scan` (one pass per ARXML
file), `sections` (steps 1, 2, 4 to 8), `workspace` (step 3), `gitinfo` (step 9),
`proc` and `tools` (step 10), `golden` (golden-diff), `render` (Markdown, README, summary),
`runner` (orchestration), `cli`.

Finding codes: `DISC-STEP-FAILED`, `DISC-FILE-UNREADABLE`, `DISC-FILE-FAILED`,
`DISC-XML-EMPTY`, `DISC-XML-MALFORMED`, `DISC-XML-ENCODING`, `DISC-XML-MANY`,
`DISC-WALK-ERROR`, `DISC-WALK-DEPTH`, `DISC-NO-DCF`, `DISC-NO-DPA`, `DISC-MANY-DCF`,
`DISC-MANY-DPA`, `DISC-DPA-OPTION`, `DISC-WS-MALFORMED`, `DISC-WS-MISSING`,
`DISC-ECUC-NO-RTE`, `DISC-ECUC-NO-OS`, `DISC-LOCK-FOUND`, `DISC-GIT-MISSING`,
`DISC-GIT-NOT-REPO`, `DISC-GIT-SUBFOLDER`, `DISC-GIT-ERROR`, `DISC-GIT-DIRTY`,
`DISC-TOOLS-SKIPPED`, `DISC-TOOL-NOT-FOUND`, `DISC-TOOL-FOLDER`, `DISC-TOOL-VERSION`,
`DISC-TOOL-SPAWN`, `DISC-TOOL-TIMEOUT`, `DISC-TOOL-EXIT`, `DISC-OUT-WRITE`,
`DISC-PRIVATE-WRITE`, `DISC-GOLDEN-READ`, `DISC-GOLDEN-LFS`, `DISC-GOLDEN-XML`,
`DISC-GOLDEN-DEPTH`.

## Decisions I took where the task left room (please confirm or correct)

1. Parser: the standard library's expat in streaming mode, not `lxml.iterparse`. Same
   guarantee (no tree is ever built for `run`), plus exact byte offsets, which give the
   exact source text of added elements in golden-diff, and error messages from a fixed list
   that never contain file content. Measured on a 70 MB file with a minimal handler:
   expat 55 MB/s, lxml target parser 59 MB/s.
2. Output folder not writable: checked before any work starts; the command stops with exit
   code 2 and a clear message (`--out`). A folder that already holds files discovery did not
   write is refused too, so the report can never land inside a build folder. A single report
   file that cannot be written later is a finding; only `discovery.json` is fatal.
3. `--anonymize` writes a second folder `<out>_private` (key, `token_map.csv`, the proposal
   files with real names). Not in the task. Reason: without it the owner cannot translate our
   answers ("put new data types in folder N3fa2c") back into real names, and tokens would
   differ between `run` and `golden-diff`. The folder is named in the summary and in
   README.txt as "do NOT send". Say so if it should go.
4. Golden-diff, changed elements: only the shape of the old and new value is reported
   (letters as a, digits as 9), because the task allows values only for added elements. If
   you want the values of changed elements too, it is a two-line change in `golden._Differ`.
5. Extra options `--exclude` (the task says "configurable globs") and `--git`.
6. golden-diff git errors (git missing, not a repo, unknown commit) end with exit code 2
   and no report, because there is nothing to report.
7. At most three installed copies of the same tool are started; at most 500 changed XML
   files are compared; an added element is cut at 20,000 characters (an added file at
   200,000); XML blobs above 200 MB are not compared (finding).
8. Tools run through `subprocess.Popen` (argument list, no shell, stdin closed, output to
   temporary files, working folder = fresh temporary folder outside the repo), not through
   the engine's asyncio runner: discovery has to work before the engine exists.
9. `git status` runs with `--no-optional-locks` so that it does not rewrite `.git/index`.
10. Anonymized mode keeps some things readable on purpose; the list is printed in
    README.txt and at the top of the Markdown: ECUC definition paths below `/MICROSAR/` and
    `/AUTOSAR/`, GID names, UUID values, `AUTOSAR_*.xsd`, Vector folder and file names, the
    tools' help text (with repo path, user, computer and home folder replaced).

## What is not done

- Nothing was run on Windows, with a real DaVinci tool or on a real repo. See below.
- Line coverage was not measured (pytest-cov and coverage are not installed, and the stdlib
  trace module would disturb the timeout tests). No percentage is claimed.
- The one `windows_only` test is a skipped placeholder; it lists what has to be checked.
- Renames are reported as one deleted and one added file (`git diff --no-renames`).
- golden-diff builds an in-memory tree of both versions of each changed file (needed for
  the element comparison). `run` never does. A changed 60 MB file would need roughly
  0.5 GB; this was not measured.

## How to run the tests

    python3 -m pytest tests/unit/discovery -q
    mypy --strict porttool/discovery tests/unit/discovery
    ruff check porttool/discovery tests/unit/discovery

Result in this sandbox: 119 tests, 118 passed, 1 skipped (the Windows placeholder).

    $ python3 -m pytest tests/unit/discovery -q
    ........................................................................ [ 60%]
    ...........................s...................                          [100%]
    $ mypy --strict porttool/discovery tests/unit/discovery
    Success: no issues found in 25 source files
    $ ruff check porttool/discovery tests/unit/discovery
    All checks passed!

Named tests for the corner cases of the task (all in `tests/unit/discovery`):

| Corner case | Test |
| --- | --- |
| repo without .dcf or .dpa | `test_run.py::test_repo_without_dcf_or_dpa_still_gives_a_report` |
| several .dcf files | `test_run.py::test_several_dcf_files_are_each_summarised` |
| malformed .dcf | `test_run.py::test_malformed_dcf_gives_partial_structure_text_summary_and_finding`, `..._not_xml_at_all_is_described_by_line_shapes` |
| ARXML with BOM, UTF-16, CRLF | `test_run.py::test_arxml_with_bom_utf16_and_crlf_is_read_and_style_is_reported` |
| empty and truncated files | `test_run.py::test_empty_and_truncated_files_are_listed_never_fatal` |
| 60 MB file (slow) | `test_units.py::test_60_mb_file_is_streamed_within_bounded_memory` |
| non-ASCII and very long paths | `test_run.py::test_non_ascii_and_very_long_paths_work` |
| no git | `test_run.py::test_no_git_executable_is_a_finding_not_a_failure`, `test_folder_that_is_not_a_git_repo_is_reported` |
| dirty git tree | `test_run.py::test_dirty_git_tree_is_counted_without_our_own_output_folder` |
| tool not found | `test_tools.py::test_tool_not_found_is_a_finding_for_each_missing_tool` |
| tool that hangs, killed with its child | `test_tools.py::test_hanging_tool_is_killed_on_timeout_together_with_its_child`, `test_hanging_tool_in_a_full_run_...` |
| tool that prints non-UTF-8 bytes | `test_tools.py::test_tool_that_prints_non_utf8_bytes_is_saved_without_losing_bytes` |
| non-zero exit code for help | `test_tools.py::test_tool_that_returns_non_zero_for_help_is_recorded_not_failed` |
| `--no-tools` | `test_run.py::test_no_tools_starts_nothing` |
| `--anonymize` stability | `test_anonymize.py::test_anonymize_tokens_are_stable_within_a_run_and_across_runs` |
| no original short name in anonymized output | `test_anonymize.py::test_anonymize_no_original_name_appears_anywhere_in_the_output`, `..._golden_diff_has_no_original_name_either`, and the detector's own check `test_the_leak_detector_itself_finds_names_...` |
| golden-diff: added port | `test_golden.py::test_golden_diff_added_port_gives_paths_position_and_exact_xml_text` |
| golden-diff: added file | `test_golden.py::test_golden_diff_added_file_holds_the_whole_file_as_template` |
| golden-diff: deleted element | `test_golden.py::test_golden_diff_deleted_element_and_deleted_file_are_listed_without_text` |
| golden-diff: only line endings changed | `test_golden.py::test_golden_diff_file_whose_only_change_is_line_endings` |
| output folder not writable | `test_run.py::test_output_folder_not_writable_fails_before_any_work`, `test_output_folder_with_foreign_files_is_refused` |
| help flags only, no project path | `test_tools.py::test_a_tool_is_only_ever_started_with_one_help_flag`, `test_no_project_or_workspace_path_is_ever_passed_to_a_tool` |
| step isolation | `test_run.py::test_a_failing_step_becomes_a_finding_and_the_other_steps_still_run` |

The leak test found no leak. Its first run reported four words; all four were collisions
between names of the synthetic repo and my own text (a table header "Missing", the product
name "Vector DaVinci Developer", two made-up .dpa tag names), not names taken from the repo.
I renamed the synthetic folders so that the test cannot be fooled that way again.

## Measured timings (2 shared cores, noisy, pessimistic)

- Whole discovery test suite: about 31 s, of which 19 s are the two hanging-tool tests.
- `run` on the synthetic repo (12 ARXML files) without tools: 0.1 to 0.3 s.
- 62.9 MB ARXML file (roughly 0.9 million elements, estimated from the generator): 1.6 s
  for the whole `run`, 1.9 s including writing the test file; peak resident memory of the
  process grew by 6 MB. That is roughly 35 to 40 MB/s. Extrapolated, not measured: 500 MB
  of ARXML in 15 to 30 s here. Nothing was measured on 2,000 files.
- A hung tool costs the timeout (30 s) per help flag: 2.5 minutes per executable.

## Unverified on Windows

Each item is isolated in one small function; the logic is tested here, the behaviour on a
real machine is not.

| Item | Where | What was tested here | What remains |
| --- | --- | --- | --- |
| File version reading | `tools.parse_pe_version`, `read_file_version` | parsing of a hand-built VS_FIXEDFILEINFO block and its strings | never read from a real Vector .exe; a packed executable would give no version |
| Program Files search | `tools._program_files_folders`, `find_executables` | with the environment variables pointed at temporary folders | real install layout, `ProgramFiles(x86)` and `ProgramW6432` values |
| Hidden window flag | `proc.hidden_window_options` | with the Windows-only names monkeypatched in | `CREATE_NO_WINDOW` and `STARTUPINFO/SW_HIDE` on a real desktop; a GUI tool may still show a dialog and then runs into the timeout |
| Process-tree kill | `proc.kill_process_tree` | on Linux with a fake tool that starts a child: both are gone | psutil on Windows; a child started after the snapshot would survive |
| Long paths | `fsio.io_path` | the text logic for drive and UNC paths; real paths above 300 characters on Linux | the `\\?\` prefix on Windows, on network drives and in special file systems |
| Walk and links | `fsio.walk_repo` | symbolic links are not followed | NTFS junctions (treated as folders or skipped depending on the Python version; depth is capped at 64) |
| Atomic write retry | `fsio.atomic_write_bytes` | with `os.replace` monkeypatched to raise `PermissionError` | a real sharing violation |
| Console output | `cli.safe_echo` | through `CliRunner` | a cp1252 or cp850 console with non-ASCII file names |
| Crash log folder | `cli._crash_log` | with `LOCALAPPDATA` set to a temporary folder | the real `%LOCALAPPDATA%` |
| git | `gitinfo.Git` | git 2.43 on Linux | Git for Windows; `--no-optional-locks` needs git 2.15 or newer; "dubious ownership" refusals show up as `DISC-GIT-NOT-REPO` |
| Tool output files | `proc.run_captured` | redirecting a Python child's output to temporary files | a .NET or native console tool writing to redirected handles |

## PENDING-DISCOVERY assumptions

Discovery itself assumes almost nothing, which is its purpose. What it does assume:

- Tool file names `DVImEx.exe`, `DVWspChecker.exe`, `DVSwcGen.exe`, `DVCfgCmd.exe`
  (`tools.ALL_TOOLS`), compared without case.
- Install folders `Vector DaVinci Developer*` below the Program Files variables, and
  DVCfgCmd in `<folder>`, `<folder>/Core` or `<folder>/DaVinciConfigurator/Core` for every
  folder named in the .dpa and its three parents (`tools.CONFIGURATOR_SUBFOLDERS`).
- Help flags `--help`, `-h`, `-?`, `/?`, `help`. A tool that understands none of them gives
  five useless attempts; the report shows that.
- Folder names of generated or service components: `servicecomponent*`, `gendata*`,
  `generated*` (`sections.LOCKED_FOLDER_PATTERNS`); generated folders for the git check:
  `gendata*`, `generated*`, `*output*`, `log`, `logs` (`gitinfo.GENERATED_FOLDER_PATTERNS`).
- Rte and Os module configurations are recognised by the last segment of their
  DEFINITION-REF (`Rte`, `Os`); the mapped-event and task counts use the AUTOSAR standard
  names `RteEventToTaskMapping`, `RteMappedToTaskRef`, `RteEventRef`, `RtePositionInTask`,
  `OsTask`. If Vector stores the mapping differently the counts are zero and the definition
  table still shows what is there.
- Authoring-tool words: Simulink, MATLAB, Embedded Coder, TargetLink, dSPACE, searched in
  comments, ADMIN-DATA, DESC, LONG-NAME, INTRODUCTION, ANNOTATIONS, USED-CODE-GENERATOR,
  GID names and file paths; an SWC is also marked when the file of its SWC-IMPLEMENTATION
  (found through BEHAVIOR-REF) mentions one of them.
- The .dcf and .dpa used in the tests are invented. Whatever the real ones look like, the
  report describes them; nothing in the code depends on the invented format.

## Contract change requests

None is blocking. For the orchestrator:

1. `porttool/core` has no file or process utilities yet, so discovery carries its own
   (`fsio.atomic_write_bytes`, `fsio.io_path`, `proc.run_captured`,
   `proc.kill_process_tree`). When Agent 1's utilities exist, either replace these or adopt
   them; `io_path` (the long-path prefix) and the temporary-file capture are worth sharing
   with the davinci package.
2. Someone has to call `porttool.discovery.register_cli(group)` from the main click group.
   Until then `python -m porttool.discovery` works on its own.
3. `ProjectConfig.exclude_globs` does not say how a glob is matched. Discovery's
   `fsio.ExcludeMatcher` reads a pattern without `/` as a name at any depth and a pattern
   with `/` as anchored at the repo root, without regard to case. The reader should use the
   same rule; consider stating it in the contract.
4. `docs/SHARED_CONTEXT.md` says exit code 1 on findings of severity error; the discovery
   task says exit code 0 whenever the report was written. I followed the task.
5. The report format (`format_version: 1`) is plain JSON without a schema class in core. If
   other packages are to read `discovery.json` with types, say so and I will add models.

## Example: discovery.md produced on the synthetic repo

Shortened where marked. Produced by `run_discovery` on `tests/unit/discovery/synth.py`
with two fake tools (one prints help and returns 0, one prints to stderr and returns 3).
The repo was at `/tmp/example/repo`; every name in it is invented.

````markdown
# PortTool discovery report

Mode: NOT anonymized. This report contains:
- counts, sizes, XML tag names, attribute names and schema versions;
- file and folder names below the repo root, written relative to the repo root (the absolute repo path, your user name and your computer name are not written);
- AUTOSAR short names and package paths of component types, compositions, port interfaces, data types and other top-level elements, and the git branch name;
- up to three sample values per element and attribute of the .dcf and .dpa files (typically file paths, versions and tool settings), as written in those files;
- GID names of ADMIN-DATA/SDG blocks and DEFINITION-REF paths of the ECUC file;
- the paths of the Vector tools that were found and the help text they printed.
It does NOT contain: init values, constants, descriptions, comment texts, ECUC parameter values, ECUC container names or source code.

Created (UTC): 2026-11-11T06:53:35Z  
Tool: porttool discover 0.0.1, report format 1  
Python 3.13.18 on Linux-6.18.44-fc-v378-x86_64-with-glibc2.39

## Steps

| Step | OK | Seconds | Findings |
| --- | --- | --- | --- |
| Inventory of ARXML, DCF and DPA files | yes | 0.0 | 0 |
| Storage map | yes | 0.0 | 0 |
| Workspace index (.dcf and .dpa) | yes | 0.0 | 1 |
| XML style per folder | yes | 0.0 | 0 |
| ECUC structure (Rte, Os) | yes | 0.0 | 0 |
| Composition shape | yes | 0.0 | 0 |
| Model-owned and locked candidates | yes | 0.0 | 0 |
| Lock and temporary files | yes | 0.0 | 1 |
| Git state | yes | 0.04 | 0 |
| Vector command-line tools (help flags only) | yes | 0.17 | 7 |

## Findings (9)

| Severity | Code | Step | File | Message |
| --- | --- | --- | --- | --- |
| warning | DISC-WS-MISSING | workspace | EcuProj.dcf | 1 ARXML file(s) named in this dcf file do not exist on disk. |
| warning | DISC-LOCK-FOUND | lock_signs | - | 1 lock or temporary file(s) lie next to workspace files. A DaVinci tool may be open, or it crashed earlier. |
| info | DISC-TOOL-EXIT | tools | - | DVImEx returned exit code 3 for --help. |
| info | DISC-TOOL-EXIT | tools | - | DVImEx returned exit code 3 for -h. |
| info | DISC-TOOL-EXIT | tools | - | DVImEx returned exit code 3 for -?. |
[... shortened for HANDOFF.md ...]

## 1 Inventory

| Type | Files | Size |
| --- | --- | --- |
| *.arxml | 12 | 0.02 MB |
| *.ecuc.arxml | 1 | 0.01 MB |
| *.dcf | 1 | 0.00 MB |
| *.dpa | 1 | 0.00 MB |

AUTOSAR schema versions:

| Namespace | schemaLocation | Version hint | Files |
| --- | --- | --- | --- |
| http://autosar.org/schema/r4.0 | http://autosar.org/schema/r4.0 AUTOSAR_4-2-2.xsd | 4-2-2 | 12 |

Top-level element kinds (children of AR-PACKAGE/ELEMENTS):

| Kind | Category | Objects | Files |
| --- | --- | --- | --- |
| APPLICATION-SW-COMPONENT-TYPE | component_type | 3 | 3 |
| ECUC-MODULE-CONFIGURATION-VALUES | ecuc | 3 | 1 |
| COMPOSITION-SW-COMPONENT-TYPE | composition | 2 | 2 |
| SENDER-RECEIVER-INTERFACE | port_interface | 2 | 2 |
| COMPU-METHOD | data_type | 1 | 1 |
| IMPLEMENTATION-DATA-TYPE | data_type | 1 | 1 |
| SERVICE-SW-COMPONENT-TYPE | component_type | 1 | 1 |
| SW-BASE-TYPE | data_type | 1 | 1 |
| SYSTEM | system | 1 | 1 |

Internal behaviors: 3

## 2 Storage map

File layout: files_with_one_top_level_object (9); files_without_top_level_object (1); files_with_several_top_level_objects (2)

### APPLICATION-SW-COMPONENT-TYPE

- 3 objects in 3 files; layout: **one-object-per-file** (3 files with one, 0 with several)
- folders: `Config/DevWs/ComponentTypes` (3)
- packages: `/ComponentTypes` (3)
- file name pattern: `{name}` (3)
- child elements: INTERNAL-BEHAVIORS (3); PORTS (3); SHORT-NAME (3); ADMIN-DATA (1)

[... one block like this per element kind ...]

Kinds stored together in one file:

| Kinds in the file | Files | Samples |
| --- | --- | --- |
| APPLICATION-SW-COMPONENT-TYPE | 3 | `Config/DevWs/ComponentTypes/SwcAlpha.arxml`, `Config/DevWs/ComponentTypes/SwcBeta.arxml`, `Config/DevWs/ComponentTypes/SwcModel.arxml` |
| COMPOSITION-SW-COMPONENT-TYPE | 2 | `Config/DevWs/ComponentTypes/EcuComposition.arxml`, `Config/DevWs/ComponentTypes/SubComp.arxml` |
| SENDER-RECEIVER-INTERFACE | 2 | `Config/DevWs/PortInterfaces/IfSpeed.arxml`, `Config/DevWs/PortInterfaces/IfTorque.arxml` |
| COMPU-METHOD + IMPLEMENTATION-DATA-TYPE + SW-BASE-TYPE | 1 | `Config/DevWs/DataTypes/DataTypes.arxml` |
| ECUC-MODULE-CONFIGURATION-VALUES | 1 | `Config/EcucCfg/EcuProj.ecuc.arxml` |
[... shortened for HANDOFF.md ...]

## 3 Workspace index

### .dcf files: 1 found

#### `EcuProj.dcf` (1028 bytes)

- well-formed XML: yes
- declaration: `<?xml version="1.0" encoding="UTF-8"?>`; BOM: none; line endings: lf; indentation: 2 spaces; trailing newline: lf
- root start tag: `<DCF ARXMLVERSION="4.2.2">`
- values that look like paths: 9 (relative 9, absolute 0, with variables 0); separators: backslash (9)
- relative paths that exist when resolved against the file's own folder: 8, against the repo root: 8
- ARXML files listed: 9, of these on disk: 8, listed but missing: 1
- ARXML files on disk but not listed, in folders that hold listed files: 1; anywhere below this file's folder: 4

Where the path values are:

| Location | Count | Files | Folders | Missing | Extensions | Samples |
| --- | --- | --- | --- | --- | --- | --- |
| `/DCF/FILEREF/ARXML#text` | 9 | 8 | 0 | 1 | .arxml (9) | `Config\DevWs\ComponentTypes\SwcAlpha.arxml`, `Config\DevWs\ComponentTypes\SwcBeta.arxml`, `Config\DevWs\ComponentTypes\SwcModel.arxml` |

Listed but missing: `Config\DevWs\ComponentTypes\GoneAway.arxml`

On disk but not listed: `Config/DevWs/ComponentTypes/Unlisted.arxml`

Structure (tag, occurrences, attributes, sample values):

- `DCF` x1; @ARXMLVERSION (1x, 1 distinct: `4.2.2`)
  - `NAME` x1; text (1x, 1 distinct: `EcuProj`)
  - `ECUGUID` x1; text (1x, 1 distinct: `3F2504E0-4F89-41D3-9A0C-0305E82C3301`)
  - `FILEREF` x1
    - `ARXML` x9; @ROOTITEM (9x, 3 distinct: `COMPONENTTYPE`, `PORTINTERFACE`, `DATATYPE`); @TYPE (9x, 1 distinct: ``); text (9x, 9 distinct: `Config\DevWs\ComponentTypes\SwcAlpha.arxml`, `Config\DevWs\ComponentTypes\SwcBeta.arxml`, `Config\DevWs\ComponentTypes\SwcModel.arxml`)

## 4 XML style

All 12 ARXML files together:

- declaration: `<?xml version="1.0" encoding="UTF-8"?>` (12)
- declared encoding: UTF-8 (12); BOM: none (12)
- line endings: lf (12); trailing newline: lf (12)
- indentation: 2 spaces (12); attribute quotes: double (12)
- root prefix: (no prefix) (12)
- root start tag: `<AUTOSAR xmlns="http://autosar.org/schema/r4.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://autosar.org/schema/r4.0 AUTOSAR_4-2-2.xsd">` (12)
- UUID format: 8-4-4-4-12 lower (8); 8-4-4-4-12 digits (1); {}8-4-4-4-12 upper (1); UUID version digit: 4 (10)
- empty elements: self_closing (1); open_close (0)
- ADMIN-DATA in 1 files, below: APPLICATION-SW-COMPONENT-TYPE (1)
- SDG GID names: DV:DEV (1)
- SD GID names: DV:CREATED (1)

## 5 ECUC

- module configurations: 3 (EcuC (1); Os (1); Rte (1))
- Rte module configurations: 1; events mapped to a task: 2; mapping containers without a task: 1
- Os module configurations: 1; tasks: 2
- Counts use the AUTOSAR standard definition names RteEventToTaskMapping, RteMappedToTaskRef, RteEventRef, RtePositionInTask and OsTask. If they are zero, read the definition tables: they list what the file really holds.

### Rte in `Config/EcucCfg/EcuProj.ecuc.arxml`

Definition: `/MICROSAR/Rte`; containers: 4

mapping_containers: 3, with_task_reference: 2, without_task_reference: 1, with_event_reference: 3, distinct_tasks_referenced: 1, position_in_task_parameters: 3

| Element | Definition kind | Definition path below the module | Count |
| --- | --- | --- | --- |
| ECUC-CONTAINER-VALUE | ECUC-PARAM-CONF-CONTAINER-DEF | `RteSwComponentInstance` | 1 |
| ECUC-CONTAINER-VALUE | ECUC-PARAM-CONF-CONTAINER-DEF | `RteSwComponentInstance/RteEventToTaskMapping` | 3 |
| ECUC-REFERENCE-VALUE | ECUC-FOREIGN-REFERENCE-DEF | `RteSwComponentInstance/RteEventToTaskMapping/RteEventRef` | 3 |
| ECUC-REFERENCE-VALUE | ECUC-REFERENCE-DEF | `RteSwComponentInstance/RteEventToTaskMapping/RteMappedToTaskRef` | 2 |
| ECUC-NUMERICAL-PARAM-VALUE | ECUC-INTEGER-PARAM-DEF | `RteSwComponentInstance/RteEventToTaskMapping/RtePositionInTask` | 3 |

Targets of reference values:

| Definition path below the module | DEST of VALUE-REF | Count |
| --- | --- | --- |
| `RteSwComponentInstance/RteEventToTaskMapping/RteEventRef` | TIMING-EVENT | 3 |
| `RteSwComponentInstance/RteEventToTaskMapping/RteMappedToTaskRef` | ECUC-CONTAINER-VALUE | 2 |

## 6 Composition shape

- compositions: 2; nested: yes; deepest nesting: 2
- assembly connectors: 2; delegation connectors: 1

| Composition | File | Prototypes | Of compositions | Assembly | Delegation |
| --- | --- | --- | --- | --- | --- |
| `/ComponentTypes/EcuComposition` | `Config/DevWs/ComponentTypes/EcuComposition.arxml` | 4 | 1 | 2 | 1 |
| `/ComponentTypes/SubComp` | `Config/DevWs/ComponentTypes/SubComp.arxml` | 0 | 0 | 0 | 0 |

Root (ECU) composition candidates:

| Composition | File | Why |
| --- | --- | --- |
| `/ComponentTypes/EcuComposition` | `Config/DevWs/ComponentTypes/EcuComposition.arxml` | referenced by SOFTWARE-COMPOSITION-TREF (root of a SYSTEM); not used as a prototype in any other composition |

## 7 Model-owned and locked candidates

Component types: 6; proposed davinci: 4; proposed model: 1; locked candidates: 1. The proposal is in owners_proposed.csv and locked_proposed.txt: check it by hand.

| SWC | File | Authoring tool | Evidence |
| --- | --- | --- | --- |
| `SwcModel` | `Config/DevWs/ComponentTypes/SwcModel.arxml` | Simulink | Embedded Coder in comment; Simulink in comment |

Locked candidates:

| SWC | File | Why |
| --- | --- | --- |
| `DetService` | `Config/ServiceComponents/Det_swc.arxml` | service component type |

## 9 Git state

- git version 2.43.0; branch: `main`; HEAD: 46e90e1563bc6213edc0b1057698f76b5559eae6
- clean: yes; changes: staged (0); not_staged (0); untracked (0)
- config: core.autocrlf=-, core.eol=-, core.safecrlf=-, core.longpaths=-, core.ignorecase=-
- attributes of one sample file per type: arxml: text=unspecified, eol=unspecified, filter=unspecified, diff=unspecified, merge=unspecified; dpa: text=unspecified, eol=unspecified, filter=unspecified, diff=unspecified, merge=unspecified; dcf: text=unspecified, eol=unspecified, filter=unspecified, diff=unspecified, merge=unspecified

| Generated folder | Ignored by git | Tracked files |
| --- | --- | --- |
| `Appl/GenData` | yes | 0 |

## 10 Vector command-line tools

Help flags tried, one at a time: --help, -h, -?, /?, help; timeout per start: 10 s.
Not found: DVWspChecker, DVSwcGen.

### DVImEx: `/tmp/example/devbin/DVImEx.py`

Found via --developer-bin; 101 bytes; modified 2026-11-11T06:53:35Z; file version -; product version -; -

| Flag | Exit code | Timed out | Seconds | stdout bytes | stderr bytes | Looks like help | Output file |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `--help` | 3 | no | 0.016 | 0 | 54 | yes | cli/DVImEx_dashdash-help.txt |
| `-h` | 3 | no | 0.016 | 0 | 54 | yes | cli/DVImEx_dash-h.txt |
| `-?` | 3 | no | 0.016 | 0 | 54 | yes | cli/DVImEx_dash-question.txt |
| `/?` | 3 | no | 0.016 | 0 | 54 | yes | cli/DVImEx_slash-question.txt |
| `help` | 3 | no | 0.016 | 0 | 54 | yes | cli/DVImEx_help.txt |

````

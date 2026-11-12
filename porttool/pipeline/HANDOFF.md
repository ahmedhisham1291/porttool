# HANDOFF: porttool/pipeline (Agent 7)

Branch `agent/pipeline`. Everything below was built and run in the Linux build sandbox
(Python 3.13, git 2.43). Nothing was run on Windows, with DaVinci tools, with the real
writer (`porttool.writer`) or with the real DaVinci runner (`porttool.davinci`): those two
packages are not in this worktree. They are imported lazily under the fixed names; the
tests use a fake writer and a fake runner behind the core protocols, together with the
real reader, workbook package, validator, engine and real git on generated repositories.

## Done

- `PortToolService` implements `porttool.core.interfaces.AppService` exactly, plus a few
  extra methods for the CLI (`settings`, `job_log`, `wait`, `git_for`, `journal_folder`).
  `build_service(...)` creates the engine and wires reader, workbook, validator, writer,
  DaVinci runner and `davinci_gui_findings`; every part can be injected.
- Jobs: load, export, dry run (workbooks or `RawRequestRow`s), apply, rollback, doctor.
  Each `start_*` returns the job id at once (slowest call measured: 14 ms with six
  polling threads); `job(id)` gives state, progress, a bounded log tail and a small
  JSON-able summary.
- The apply transaction with journal, branch, pending queue, writer, optional DaVinci
  steps with fallback import, verification by reload, request packages, HTML report,
  commit, journal archive.
- Rollback on failure or cancel at every step, and recovery after a crash from the
  journal alone, with the same restoration code.
- Git helper (argument lists, timeout, no shell, no config, no push, no history rewrite,
  no repo-wide reset or clean).
- HTML report: one file, standard library only, everything escaped.
- CLI: `init`, `export`, `dry-run`, `apply`, `rollback`, `status` (click, `register_cli`).
- Every corner case of the task has a named test (table below).

## Not done / limits

- **Not integrated with the real writer and DaVinci runner.** See "What the orchestrator
  must check when integrating".
- After a rollback the model is not reloaded automatically. git rewrote the restored
  files, so the reader calls the old model stale and an apply of the old dry run is
  refused (`PIP-PRE-005`). A new dry run reloads by itself.
- No command that undoes a *finished* apply. `rollback` only undoes an unfinished one;
  for a finished apply it says: switch back to your branch and delete the
  `porttool/...` branch with git.
- `generate_contract_headers` of the runner is never called.
- The pipeline's own file operations (report folder, journal, removal of created files)
  do not add the `\\?\` long-path prefix. `atomic_write_*` of core does it for its target.
- No JSON output mode in the CLI.
- A writer that *deletes* a workspace file is handled by design (the path is journaled,
  git puts the file back) but no test does it: the fake writer only creates and modifies.

## Public API

```python
from porttool.pipeline import (
    PortToolService, build_service,          # the AppService
    PipelineSettings, read_settings, DAVINCI_STEPS,
    PipelineError, RecoveryNeededError, ApplyFailedError,
    apply_summary, codes,
)
from porttool.pipeline.cli import register_cli
```

```python
service = build_service(
    engine=None, reader=None, workbook=None, validator=None, writer=None,
    runner_factory=None,        # Callable[[ProjectConfig], DaVinciRunner]
    gui_findings=None,          # Callable[[ProjectConfig], Sequence[Finding]]
    journal_dir=None,           # base folder of the journals (default below)
    lock_dir=None,              # folder of the RepoLock files (default: core)
    now=None,                   # Callable[[], datetime], for the branch name and report
    step_hook=None,             # Callable[[step, phase], None]; tests inject faults here
    git_env=None,               # process environment for git (tests isolate the config)
)
```

Parts that are not given are imported on first use: `porttool.reader.ArxmlReader()`,
`porttool.workbook.XlsxWorkbookIO()`, `porttool.validator.ChangeValidator()`,
`porttool.writer.ArxmlWriter()`, `porttool.davinci.DaVinciToolRunner(config)`,
`porttool.davinci.davinci_gui_findings(config)`. A missing package gives a
`PipelineError` ("... is not available in this installation"), not an `ImportError`.

### Behaviour of the service (each line has a test)

| Call | Behaviour |
| --- | --- |
| `open_repo` | `load_config` (raises `ConfigError`), reads `[pipeline]`, resets model, dry run and report. Returns config findings, `[pipeline]` findings, and `PIP-REC-001` when recovery is needed. Refused while an apply or rollback runs. |
| `model_is_stale` | `reader.is_stale`, cached for 2 s per model (the reader stats every file). `False` without a model. |
| `recovery_needed` | An open journal exists for this repo and no apply/rollback of this service is running. |
| `start_load` | Loads, swaps the model reference. |
| `start_export` | Loads first if there is no model or it is stale. `WorkbookMeta` from the model (schema, hash), git HEAD and `porttool.__version__`. The Results sheet gets the results of the last dry run or apply. |
| `start_dry_run*` | Loads first if needed -> reads `config.pending_file` -> import -> validate -> `writer.plan` for the route "apply" operations only. Rows whose `RowRef` carries an import error are rejected here and never reach the validator. Changes nothing in the repo; the pending file is not written. |
| `start_apply` | The transaction below, for the last dry run. A dry run can be applied once. |
| `start_rollback` | Recovery from the journal. Without an open journal: `PIP-RB-004`, nothing happens. |
| `start_doctor` | git checks, `[pipeline]` findings, journal state, `runner.doctor`. Allowed during recovery. |
| `cancel` | Engine cancel. An apply rolls back first; a rollback cannot be interrupted. |
| `close` | Shuts the engine down (cancels jobs) and waits until a running restoration has finished. |

Refusals (a `PipelineError`, subclass of `RepoStateError`, raised by `start_*`):
no repository open; an apply or rollback is running (also refuses `start_load`,
`start_dry_run*`, `start_export`, `open_repo`); recovery needed (`RecoveryNeededError`:
everything except `start_rollback` and `start_doctor`); service closed.

### Job states

| Outcome | `JobView.state` | Where the details are |
| --- | --- | --- |
| Finished | `done` | `summary` |
| Apply refused by preflight (dirty tree, ...) | `done` | `summary["ok"] is False`, `last_report().findings` |
| Apply failed for an expected reason (tool step failed, verification, commit refused) | `done` | `summary["ok"] is False`, `summary["rolled_back"] is True` |
| Apply stopped by an exception (writer raised, git broke, bug) | `failed` | `error` (says "rolled back" and the report path), `last_report()` |
| Cancelled | `cancelled` | `last_report()` (rolled back, `PIP-APPLY-002`) |
| Cancel or error after the commit | `done` | the apply is complete; `PIP-APPLY-008` |

Summaries: load `{components, ports, connectors, files, findings, model_hash, elapsed_s}`;
export `{ok, target, baseline_commit, findings, messages}`; dry run `{requests, by_status,
operations: {apply, model, hold}, planned_files, pending_entries, findings, nothing_to_do,
can_apply}`; apply and rollback `{ok, branch, commit, report, rolled_back, by_status,
findings, errors, skipped_steps}`; doctor `{ok, findings, items}`. `findings` is always
`{"info": n, "warning": n, "error": n}`.

## The apply transaction

Steps, in this order; each is reported through `ctx.progress`, the job log, the journal
and the timing table of the report: `preflight`, `branch`, `pending`, `write`, `check`,
`script`, `validate`, `generate`, `verify`, `packages`, `report`, `commit`, `close`.

1. **preflight** (nothing is changed before it passes): repo lock; journal of a crashed
   run; a dry run exists, was made against the current model, the model is not stale;
   the write plan has no error; the pending file is readable and unchanged since the dry
   run; deletes allowed; then, only if there is something to do: git present, work tree,
   a commit exists, no merge/rebase/cherry-pick/revert/bisect, clean tree, identity
   (`git var GIT_AUTHOR_IDENT`), free disk space, and when a DaVinci step is enabled and
   something is written to the workspace: runner available and `davinci_gui_findings`
   empty. Untracked files inside `arxml_roots` or the folder of the `.dcf` are an error;
   other untracked files are a warning and are never touched.
2. **branch**: the journal is opened first, then the name `porttool/<yyyymmdd>-<n>`
   (highest existing number of that day plus one, local and remote-tracking branches
   counted) is journaled, then `git checkout -b`.
3. **pending**: `outcome.pending` is written to `config.pending_file` (journaled) only
   when it differs from the queue on disk.
4. **write**: `writer.apply(plan, model, config, journal, ctx)`. Skipped when no
   operation has route "apply" (then DaVinci steps and verify are skipped too).
5. **DaVinci steps**, only those in `[pipeline] davinci_steps`, always in the order
   check, script, validate, generate. Before the first tool starts, the journal records
   the folders the tools may write (tool scopes).
6. **verify**: reload with the reader; every applied operation's object exists as
   requested; nothing that existed is gone (except what a delete names); the reload has
   no new error finding; no file changed that the journal does not cover.
7. **packages**, **report**: files go to `<report_dir>/<branch name>/`.
8. **commit**: `git add` of exactly the changed paths the journal covers, `git commit`
   with a message that lists the requests. Never `--no-verify`.
9. **close**: the report is written again with the commit hash, the journal is archived.

Results: `ACCEPTED` becomes `APPLIED` for requests whose "apply" operations were all
applied. `WAITING_FOR_MODEL_OWNER` and `ON_HOLD` stay. The model is swapped to the
reloaded one after the commit.

Default (no DaVinci step enabled): edit ARXML on the new branch, verify by reload, commit,
stop. The report, the job log and finding `PIP-APPLY-007` name the skipped steps and say
how to enable them.

### Rollback and recovery

Until the commit exists, any failure or cancel: restore every journaled path (git
checkout of the original commit for files that commit holds; removal of files the run
created; a byte copy for files that existed without being in git), remove folders the
run created if they are empty, return to the original branch (or the original commit for
a detached HEAD), delete the new branch if nothing was committed on it, archive the
journal as `rolled_back`, release the lock. Paths that are not in the journal are never
touched. The failed run's report is written outside the repository (below).

Recovery (`start_rollback`, `porttool rollback`) reads the journal file and runs the same
function (`restore.rollback_from_journal`). If the dead process had already committed,
the working tree is put back, the original branch is checked out and the new branch is
**kept** with its commit (`PIP-RB-002`). A rollback that cannot finish (git or a locked
file) leaves the journal open and reports `PIP-RB-006`; run it again.

## `[pipeline]` settings (porttool.toml)

| Key | Default | Meaning |
| --- | --- | --- |
| `davinci_steps` | `[]` | Any of `"check"`, `"script"`, `"validate"`, `"generate"`. Run order is fixed. |
| `fallback_import` | `false` | When the check fails: undo the writer's files, import `writer.build_delta` through `import_arxml`, check again. |
| `allow_delete` | `false` | Delete requests are refused unless true (`PIP-PRE-008`). |
| `report_dir` | `"porttool_reports"` | Base folder of the reports, repo-relative or absolute. A run writes into `<report_dir>/<branch name>/`. |
| `commit_reports` | `false` | Commit the report folder with the change. |
| `commit_generated` | `true` | Commit changes of tracked files inside `gen_data`. |
| `tool_write_scopes` | `[]` | Folders or files the DaVinci tools may write, repo-relative; `"."` is the whole repo. Empty: `arxml_roots`, the folder of the ECUC file, the `.dpa`, the `.dcf`, `gen_data`. |
| `journal_dir` | `""` | Base folder of the journals. Empty: `%LOCALAPPDATA%\PortTool\journals`. |
| `log_tail_lines` | `200` | Lines kept in `JobView.log_tail`. |

A value of the wrong type is an error finding (`PIP-CFG-001`, the default is used, apply
refused), an unknown key a warning (`PIP-CFG-002`), an unknown step an error
(`PIP-CFG-003`). Other settings the pipeline reads: `config.pending_file`,
`config.min_free_disk_bytes`, `config.timeouts.git_s`, `config.generate_modules`,
`config.arxml_roots`, `config.dcf_file`, `config.ecuc_file`, `config.dpa_file`,
`config.gen_data`.

## Journal format

Folder `<journal_dir>/<repo id>/` (`repo_id` of core):

```
apply.journal.jsonl          the journal of the running or crashed apply ("open")
archive/<yyyymmdd-hhmmss>-<committed|rolled_back|empty>.jsonl
backup/00001 ...             copies of files that existed but are not in git
work/                        delta ARXML for the fallback import, script task request/result
reports/<branch>-failed-<hhmmss>/report.html    reports of failed applies
```

"An open journal exists" means: `apply.journal.jsonl` exists and has no `closed` record.
One JSON object per line, UTF-8, appended, flushed and fsynced before the action it
announces. Every record has `"t"` (UTC time). A torn last line is ignored.

| `type` | Fields |
| --- | --- |
| `begin` | `format` (1), `repo_root`, `original_branch` (null: detached HEAD), `original_commit`, `tool_version`, `pid` |
| `branch` | `name`; written before the branch is created |
| `step` | `name`, `state`: `started`, `finished` |
| `path` | `rel_path`, `action` (what the writer passed: `create`, `modify`; `late` if the writer forgot), `step`, `tracked` (in the original commit), `existed`, `new_dirs` (folders that did not exist yet, deepest first), `backup` (name in `backup/` or null). Written BEFORE the path is touched. Only the first record of a path counts. |
| `scope` | `rel_path` (folder, file or `"."`), `step`, `untracked_before`. A place DaVinci tools may write; written before the first tool starts. On rollback git lists what changed inside. |
| `commit` | `sha` |
| `note` | free fields (`event: "rolled_back"`, counts) |
| `closed` | `outcome`: `committed`, `rolled_back`, `empty` |

`ApplyJournal.record(rel_path, action)` is the `Journal` protocol the writer gets. It is
thread-safe, accepts `/` or `\`, and raises `RepoStateError` for a path outside the repo.

## Finding codes (`porttool/pipeline/codes.py`)

| Code | Meaning |
| --- | --- |
| `PIP-APPLY-001` | Nothing to apply (info; no branch is created). |
| `PIP-APPLY-002` | The apply was cancelled and rolled back. |
| `PIP-APPLY-003` | The apply failed (exception) and was rolled back. |
| `PIP-APPLY-004` | The writer reported an error. |
| `PIP-APPLY-005` | The writer changed a file without journaling it first. |
| `PIP-APPLY-006` | The writer did not apply a planned operation. |
| `PIP-APPLY-007` | DaVinci steps were skipped; how to enable them (warning). |
| `PIP-APPLY-008` | Cancel or error came after the commit; the apply is complete. |
| `PIP-APPLY-009` | No request is applied to the workspace now (info). |
| `PIP-CFG-001` | A `[pipeline]` setting has the wrong type; the default is used. |
| `PIP-CFG-002` | Unknown key in `[pipeline]` (warning). |
| `PIP-CFG-003` | Unknown name in `davinci_steps`. |
| `PIP-COMMIT-001` | git refused the commit: user.name / user.email is not set. |
| `PIP-COMMIT-002` | git refused the commit (hook or other reason); the message holds the output. |
| `PIP-COMMIT-003` | Nothing changed on disk; no commit was made. |
| `PIP-DOC-001` | git problem found by doctor. |
| `PIP-DOC-002` | The DaVinci runner is not available (warning; error when a step is enabled). |
| `PIP-DOC-003` | doctor information. |
| `PIP-DV-001` | The workspace check failed. |
| `PIP-DV-002` | The check refused the direct edit; the fallback import was used (warning). |
| `PIP-DV-003` | The fallback import failed too. |
| `PIP-DV-004` | The script task (task mapping, solving actions) failed. |
| `PIP-DV-005` | Project validation failed. |
| `PIP-DV-006` | Generation failed. |
| `PIP-DV-007` | New triggers need a manual task mapping (warning). |
| `PIP-DV-008` | The generated folder is ignored by git (info). |
| `PIP-PEND-001` | The pending file is not a valid pending queue. |
| `PIP-PEND-002` | The pending file holds git merge-conflict markers. |
| `PIP-PEND-003` | The pending file cannot be read. |
| `PIP-PKG-001` | A request package for a model owner was written (info). |
| `PIP-PKG-002` | The delta ARXML of a request package could not be built (warning). |
| `PIP-PRE-001` | Another PortTool instance works on this repository. |
| `PIP-PRE-002` | The journal of an unfinished apply exists; roll back first. |
| `PIP-PRE-003` | No dry run to apply (none, or it was applied already). |
| `PIP-PRE-004` | The model was reloaded after the dry run. |
| `PIP-PRE-005` | Files changed on disk since the dry run (stale model). |
| `PIP-PRE-006` | The write plan of the dry run has errors. |
| `PIP-PRE-007` | The pending file cannot be used or changed since the dry run. |
| `PIP-PRE-008` | Delete requests are refused unless `allow_delete = true`. |
| `PIP-PRE-009` | Not enough free disk space. |
| `PIP-PRE-010` | DaVinci Developer or Configurator seems to have the project open. |
| `PIP-PRE-011` | The git executable was not found on PATH. |
| `PIP-PRE-012` | The repository folder is not inside a git work tree. |
| `PIP-PRE-013` | The git repository has no commit yet. |
| `PIP-PRE-014` | A merge, rebase, cherry-pick, revert or bisect is in progress. |
| `PIP-PRE-015` | Tracked files have uncommitted changes. |
| `PIP-PRE-016` | Untracked files inside the workspace folders. |
| `PIP-PRE-017` | Untracked files outside the workspace folders (warning). |
| `PIP-PRE-018` | git user.name / user.email is not set (the message holds the two commands). |
| `PIP-PRE-019` | A DaVinci step is enabled but the runner is not available. |
| `PIP-RB-001` | The repository was restored (info, with counts). |
| `PIP-RB-002` | The branch of the interrupted apply holds a commit and was kept (warning). |
| `PIP-RB-003` | A folder written by DaVinci tools is ignored by git; not restored (warning). |
| `PIP-RB-004` | Nothing to roll back (info). |
| `PIP-RB-005` | Journaled paths still differ from the original commit. |
| `PIP-RB-006` | The rollback could not finish; the journal stays open. |
| `PIP-RB-007` | The original branch no longer exists; HEAD is detached (warning). |
| `PIP-REC-001` | An earlier apply did not finish; only rollback is allowed. |
| `PIP-REP-001` | The report folder is not ignored by git (info). |
| `PIP-VER-001` | An applied object is missing after the reload. |
| `PIP-VER-002` | An applied object differs from the request after the reload. |
| `PIP-VER-003` | Objects that existed before the apply are gone. |
| `PIP-VER-004` | The reloaded workspace has new error findings. |
| `PIP-VER-005` | Files changed that are not in the journal (they are left alone). |
| `PIP-VER-006` | A deleted object still exists after the reload. |

Exceptions: `PipelineError`, `RecoveryNeededError`, `ApplyFailedError` (all
`RepoStateError`, code `PT-E410`); `GitMissingError`, `GitTimeoutError`,
`GitCommandError` (`GitError`, `PT-E500`).

## Command line

```
porttool init     [--repo DIR] [--dpa FILE] [--force]   discover_paths, show, write porttool.toml
porttool export   [--repo DIR] --out FILE
porttool dry-run  [--repo DIR] WORKBOOK...
porttool apply    [--repo DIR] [--yes] WORKBOOK...      dry run, plan, confirmation, apply
porttool rollback [--repo DIR]
porttool status   [--repo DIR]
```

Each prints the job's progress lines, a request table (status, request, detail), the
findings and the report path. Exit codes: 0 success; 1 an error finding was reported (a
rejected request, a refused or rolled-back apply, recovery needed in `status`); 2 a
`PortToolError` (printed as `[code] message`, no traceback) or a cancel. Ctrl+C during
`apply` cancels the job, waits for the rollback, then exits with 2. `apply` exits with 1
when the apply worked but a request of the workbook was rejected.

## What the orchestrator must check when integrating

Writer (`porttool.writer.ArxmlWriter`):
- It must call `journal.record(rel_path, action)` with a repo-relative path before it
  creates, changes or removes each file. A path in `WriteOutcome.written` that was not
  journaled fails the apply (`PIP-APPLY-005`); a changed file that is neither journaled
  nor in `written` fails it with `PIP-VER-005` and is NOT restored (the rule "never touch
  a path that is not in the journal").
- `WriteOutcome.applied_operation_ids` must hold every operation of the plan, else
  `PIP-APPLY-006`. An error finding in `WriteOutcome.findings` fails the apply.
- `writer.plan` gets a `ChangeSet` that holds only the route "apply" operations.
- An error finding of `build_delta` is fatal for the fallback import, but only a warning
  for a model owner's package (the CSV request sheet is the binding part).
- Verification compares with the reader's model: check that the reader finds what the
  writer writes for every operation kind (`verify.py` lists the checks).

DaVinci runner (`porttool.davinci.DaVinciToolRunner`):
- Tool output reaches `JobView.log_tail` only if the runner reports lines through
  `ctx.progress(done, total, line)`. `ToolResult.log_file`, when set, is read (last 40
  lines, 8 kB) into the report.
- `run_script_task(request_json, result_json, ctx)`: the pipeline writes the request
  file. Its format is defined here because no contract defines it (see contract change
  request 2). `result_json`, if the runner writes it, is quoted in the report as text.
- Manual mapping instructions: the protocol has no method. The pipeline calls
  `runner.manual_mapping_instructions(mappings)` if the runner has such a method
  (a sequence of text lines), else it uses a built-in text.
- `ToolResult.ok is False` of an enabled step fails the apply; its error findings are
  shown. An exception from the runner also rolls back (job state `failed`).

Script task request (`work/script_request.json`):

```json
{
  "format_version": 1,
  "generated_by": "porttool 0.0.1",
  "apply_solving_actions": true,
  "task_mappings": [
    {"operation_id": "...", "swc_path": "/ComponentTypes/Swc1", "runnable": "Run10ms",
     "trigger_kind": "periodic", "port": null, "target": null, "period_s": 0.02,
     "task": "Task_10ms", "position_after_event": "/.../TMT_Run10ms",
     "event_path": "/.../TMT_Run10ms_20ms", "event_name": "TMT_Run10ms_20ms"}
  ]
}
```

One entry per applied `AddTrigger` whose `task` is not None. `event_path` is found by
reloading the model before the script step (the event the writer created).

## Decisions where the task was silent

1. The journal is opened before the branch is created (the task lists the branch first):
   a crash between the two must be recoverable.
2. Reports of failed applies are written outside the repository
   (`<journals>/<repo id>/reports/`), or below `report_dir` when that is an absolute
   path outside the repo. Otherwise a failed apply would leave the repo different from
   its start state.
3. Files that existed before the run but are not in git (an untracked pending file) are
   copied into the journal folder before they are changed and restored from that copy.
4. DaVinci tools write files the pipeline cannot name in advance (ECUC, generated code,
   workspace files during the fallback import). The journal records *scopes* before the
   first tool runs; on rollback everything git reports as changed inside a scope is
   restored, and untracked files that were not there before are removed.
5. The commit holds the workspace files, the pending file, and what the tools changed
   inside the scopes (ECUC, tracked generated files unless `commit_generated = false`).
   Otherwise the tree would stay dirty after a successful apply.
6. With no "apply" operation (only model/hold requests, or only pending queue changes)
   the branch is still created and the pending file committed; the writer, the DaVinci
   steps and the reload are skipped. With no operation and an unchanged queue nothing
   happens at all.
7. Job states: see the table above. An expected failure is data (`done`, `ok: false`), an
   exception is `failed`.
8. Recovery of an apply that had already committed keeps the branch (nothing committed
   is destroyed) and returns to the original branch.
9. While an apply or rollback runs, load and dry run are refused too (they would read a
   half-written workspace). While recovery is needed everything but rollback and doctor
   is refused.
10. The pending file is re-read in preflight. If it differs from what the dry run saw,
    the apply is refused; the validator's `outcome.pending` is stored only by the apply.
11. git is called with `subprocess.Popen` (argument list) from a worker thread, not with
    `asyncio.create_subprocess_exec`: the same code must run in the recovery path, and it
    does not depend on the Proactor loop. Environment set for git: `GIT_TERMINAL_PROMPT=0`,
    `LANGUAGE=C`, `LC_MESSAGES=C`. No `git config` is ever run.
12. Restoration never goes through `ctx.run_blocking` (the engine refuses work for a
    cancelled job). It runs on a dedicated single thread of the service and absorbs
    further cancels until it has finished.
13. `apply` exit code 1 when a request was rejected, even though the rest was applied.
14. Request packages: `<report folder>/model_requests/<team>/requests.csv` (semicolon,
    UTF-8 with BOM) and `delta.arxml`; held requests in `held_requests.csv`. The team is
    the pending entry's `owner_team`, else `config.team_of`, else `unassigned`.

## Corner cases and their tests (`tests/unit/pipeline`)

| Case | Test |
| --- | --- |
| Dirty tree | `test_preflight.py::test_dirty_tree_is_refused` |
| Untracked files in the workspace folders | `...::test_untracked_files_in_the_workspace_folders_are_refused` |
| Detached HEAD | `test_apply_happy.py::test_detached_head_is_allowed`, `test_rollback.py::test_detached_head_returns_to_the_commit_after_a_failure` |
| Merge in progress | `test_preflight.py::test_merge_in_progress_is_refused` |
| Branch name already exists | `test_apply_happy.py::test_branch_name_already_exists_takes_the_next_number` |
| git missing | `test_preflight.py::test_git_missing_path_without_git` |
| git user name not set | `...::test_git_user_name_not_set_is_refused_with_the_two_commands`, `...::test_identity_lost_before_the_commit_is_caught_and_rolled_back` |
| Commit hook fails | `...::test_a_commit_hook_that_fails_rolls_back_and_reports_the_hook_output` |
| Generated folder ignored by git | `test_davinci_steps.py::test_generated_folder_ignored_by_git`, `...::test_rollback_says_that_an_ignored_generated_folder_cannot_be_restored` |
| Kill after each step | `test_rollback.py::test_kill_after_each_step_then_recovery_from_the_journal_alone` (8 points, incl. the middle of the write), `...::test_kill_after_the_commit_keeps_the_branch_and_restores_the_tree`, `...::test_kill_after_close_needs_no_recovery`, `test_davinci_steps.py::test_kill_during_the_davinci_steps_is_recovered_from_the_journal_scopes` |
| Cancel at each step | `test_rollback.py::test_cancel_at_each_step_restores_the_repo`, `...::test_cancel_in_the_middle_of_the_write_restores_the_repo`, `test_davinci_steps.py::test_failure_or_cancel_at_each_davinci_step_restores_the_repo` |
| Failure at each step | `test_rollback.py::test_failure_at_each_step_restores_the_repo` (start and end of 7 steps), `...::test_failure_in_preflight_changes_nothing`, the DaVinci test above |
| Fallback import succeeds / also fails | `test_davinci_steps.py::test_fallback_import_succeeds`, `...::test_fallback_also_fails_and_everything_is_rolled_back` |
| Verification finds a missing object | `test_corner_cases.py::test_verification_finds_a_missing_object_and_rolls_back`, `...::test_verification_finds_an_object_that_was_lost_and_rolls_back`, `test_verify.py` |
| Pending file malformed / conflict markers | `test_service_basics.py::test_pending_file_malformed_or_with_conflict_markers_gives_a_finding`, `test_preflight.py::test_pending_file_malformed_or_with_conflict_markers_refuses_the_apply` |
| Empty change set | `test_apply_happy.py::test_empty_change_set_does_nothing_and_creates_no_branch` |
| Only model / hold operations | `...::test_change_set_with_only_model_and_hold_operations_updates_pending_and_packages` |
| Second apply while one runs | `test_rollback.py::test_second_apply_while_one_runs_is_refused` |
| Stale model | `test_preflight.py::test_stale_model_is_refused` |
| Path with spaces and non-ASCII | `test_apply_happy.py::test_repo_path_with_spaces_and_non_ascii_characters` |
| Full cycle on the SMALL repo | `test_apply_happy.py::test_full_cycle_export_edit_dry_run_apply_verify_on_the_small_repo` |

The kill tests start `tests/unit/pipeline/crash_child.py` as a separate process that
calls `os._exit` at the chosen point; a new service then recovers from the journal file
and the repository is compared with its start state: every file byte for byte, HEAD,
current branch, branch list and `git status`.

## How to run the tests

```
python3 -m pytest tests/unit/pipeline -q
mypy --strict porttool/pipeline tests/unit/pipeline
ruff check porttool/pipeline tests/unit/pipeline
```

Tails (this sandbox):

```
194 passed in 37.49s          (python3 -m pytest tests/unit/pipeline; three runs: 35.7 s, 37.5 s, 37.8 s)
Success: no issues found in 33 source files
All checks passed!
```

Coverage, measured once with the standard library (`python3 -m trace --count --missing`,
not coverage.py; child processes of the kill tests are not counted):
3,636 lines counted in `porttool/pipeline`, 96.5 % executed (weighted over the modules).
Per module: cli 92 %, codes 100 %, describe 100 %, doctor 97 %, dryrun 97 %, errors 100 %,
gitrepo 95 %, jobs 98 %, journal 99 %, packages 98 %, pendingio 100 %, report 99 %,
restore 100 %, service 98 %, settings 100 %, transaction 95 %, verify 91 %.

## Measured timings (2 shared CPU cores: rough numbers)

Fake writer, real reader, validator, engine and git. The numbers are the pipeline's own
share; the real writer and the DaVinci tools come on top.

| Repo | Load | Dry run | Apply total | Steps |
| --- | --- | --- | --- | --- |
| SMALL (8 ARXML files), 3 requests | 0.09 s | 0.07 s | 0.16 s | preflight 0.04, branch 0.02, verify 0.03, report 0.02, commit 0.01 |
| 60 SWCs, 1,800 ports, 65 files, 200 requests | 0.34 s | 0.01 s | 0.59 s | preflight 0.06, branch 0.01, write 0.06, verify 0.22, report 0.16, commit 0.07 |

- `start_*` call with six threads polling the service: slowest 14 ms.
- `reader.is_stale` behind `model_is_stale`: 0.3 ms (SMALL), 0.8 ms (65 files).
- Test suite: 194 tests in 36 to 38 s.
- Not measured: the LARGE repo (2,000 files), real git on Windows (process start is much
  slower there: a default apply of one request made 18 git calls here).

## Unverified on Windows

Nothing here ran on Windows. In particular:

- Starting `git.exe` (found with `shutil.which`), `CREATE_NO_WINDOW`, UTF-8 decoding of
  its output, non-ASCII and space-holding paths as arguments (tested on Linux only).
- Killing the process tree of a git command that timed out (psutil; a hook may start
  children). Tested here with a shell script as fake git.
- Commit hooks under Git for Windows, and git's wording for a missing identity (the
  preflight check does not depend on the wording, the commit-time check does).
- `core.autocrlf`: restoration uses `git checkout <commit> -- <path>`, so a restored file
  has the bytes a checkout produces. If files were checked out under another autocrlf
  setting than the current one, line ends may differ from the start state. The tests
  run with `autocrlf = false`.
- Case-insensitive file names: journal keys use `os.path.normcase`; a writer that spells
  a path in another letter case than git was not tried.
- Removing a read-only file (chmod then unlink) and a file that DaVinci or a virus
  scanner keeps open (expected: `PIP-RB-006`, journal kept, run rollback again).
- `os.fsync` and `os.replace` on the journal (archive move while another program reads
  it), the `%LOCALAPPDATA%` default, paths longer than 248 characters.
- Stale `RepoLock` detection after a kill (core's code, exercised here on Linux).
- Ctrl+C in the CLI (simulated by raising `KeyboardInterrupt` in a test).
- Hard kill by the Task Manager or power loss instead of `os._exit` in the child process.

## PENDING-DISCOVERY assumptions

- Which files DVWspChecker, DVImEx and DVCfgCmd write. Default scopes: `arxml_roots`, the
  folder of the ECUC file, the `.dpa`, the `.dcf`, `gen_data` (`transaction._tool_scopes`,
  overridable with `tool_write_scopes`). A file a tool changes outside the scopes fails
  the apply with `PIP-VER-005` and is not restored.
- "Workspace folders" for the untracked-file rule: `arxml_roots` plus the folder of the
  `.dcf` file.
- The script task request format above, and that one script task both maps events to
  tasks and applies solving actions.
- That DaVinci Configurator sees the edited workspace ARXML without a separate update
  step between "check" and "script". If an update step is needed, the runner protocol
  has no method for it.
- The built-in manual mapping text names a menu path that is a guess (marked UNVERIFIED
  in the text itself).
- Step order check, script, validate, generate.

## Contract change requests (porttool/core is unchanged)

1. `DaVinciRunner.manual_mapping_instructions(mappings) -> Sequence[str]`. Today an
   optional method found with `getattr`.
2. A contract for the script task request and result files (types in core). Today the
   request format is defined in `transaction.py` and the result is quoted as text.
3. `WriteOutcome`: the events and runnables the writer created (paths). Today the
   pipeline reloads the model before the script step to find the new events.
4. `Writer.apply` / `Journal.record`: state the rule "repo-relative path, before the file
   is touched" and the allowed `action` values in the protocol docstring.
5. `ApplyReport`: `commit: str | None` and `skipped_steps`. Today only in the job summary.
6. `AppService`: `job_log(job_id, since)` for incremental log polling and `settings()`.
   Both exist on `PortToolService`.
7. `ToolResult`: the files the tool changed, if the runner can know them.
8. `ProjectConfig`: typed pipeline settings instead of `config.extra("pipeline")`.
9. `Reader.is_stale` blocks; `AppService.model_is_stale` says "no method blocks". The
   service caches the answer for 2 s.
10. The `Pipeline` protocol in `interfaces.py` is not implemented by anything; the
    `AppService` replaced it.
11. `DaVinciRunner`: say in the protocol that tool output lines go through
    `ctx.progress(message=...)`, since that is the only way they reach `JobView.log_tail`.
12. The engine: a way to run clean-up work for a cancelled job (`run_blocking` refuses).
    Worked around with the service's own thread.

## Open questions

- Should a rollback reload the model so that the old dry run can be applied again?
- Should `rollback` offer to undo a finished apply (delete the branch, go back)?
- Should the report of a failed apply also be copied into `report_dir` inside the repo
  when that folder is ignored by git?
- Is exit code 1 right for an apply that worked but had rejected rows?

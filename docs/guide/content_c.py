"""Engineer guide, part C: chapter 10 (debugging), chapter 11 (limits), appendices."""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.platypus import Flowable

from guide_lib import bullets, code, h1, h2, h3, need, note, p, steps, table

REPO = Path(__file__).resolve().parents[2]


def chapter_10() -> list[Flowable]:
    story = h1("10. Debugging guide")
    story.append(
        p(
            "PortTool is built to say what is wrong rather than to fail silently. Expected "
            "problems (a bad name, a file open in Excel, a dirty working tree) are reported as "
            "findings with a code and a sentence that says what to do. Only unexpected "
            "situations stop the tool with an error. This chapter shows how to get from a "
            "symptom to its cause."
        )
    )
    story.append(h2("10.1 The method"))
    story += steps(
        [
            "**Read the whole message.** It names the code, the object and usually the fix. "
            "Look the code up in appendix A if the sentence is not enough.",
            "**Find the phase.** The prefix of the code names the part of the tool (10.3).",
            "**Check the state.** `porttool status --repo ...` and `git status`. Is an apply "
            "unfinished? Which branch are you on? Is the working tree clean?",
            "**Open the evidence** (10.2): the log file, the report of the run, the tool log.",
            "**Repeat with more detail:** `porttool -v --log-dir C:\\temp\\ptlog <command> ...` "
            "writes a debug-level log into a folder of its own, which is easy to send.",
        ]
    )
    story.append(
        note(
            "ok",
            "A failed apply is not an emergency.",
            "Until the commit exists, every failure and every cancel restores all files and "
            "returns to your branch. If the process itself died, `porttool rollback` does the "
            "same from the journal. Your repository is never left half edited without the "
            "tool knowing it.",
        )
    )
    story.append(h2("10.2 Where the evidence is"))
    story += table(
        ["Evidence", "Where", "What it tells you"],
        [
            [
                "Console output",
                "The command prompt or, for the GUI, the job strip and the Requests tab",
                "Findings as `SEVERITY CODE: message`, the status of every request, the "
                "branch and report path.",
            ],
            [
                "Log file",
                "`%LOCALAPPDATA%\\PortTool\\logs\\porttool.log` (or `--log-dir`)",
                "Every job with its steps, every finding, and the full traceback of an "
                "unexpected error. With `-v` also the debug detail.",
            ],
            [
                "Run report",
                "`<repo>\\porttool_reports\\<branch>\\report.html`",
                "Requests and results, findings, step timings, tool log excerpts, changed "
                "files, tasks to map.",
            ],
            [
                "Report of a failed apply",
                "`%LOCALAPPDATA%\\PortTool\\journals\\<repo id>\\reports\\<branch>-failed-"
                "<time>\\report.html`",
                "The same, for a run that was rolled back. Kept outside the repository so the "
                "repository is exactly as before.",
            ],
            [
                "Journal",
                "`...\\journals\\<repo id>\\apply.journal.jsonl` while an apply is open; "
                "afterwards `archive\\<time>-committed.jsonl` or `-rolled_back.jsonl`",
                "One JSON line per action, in order: start state, branch, steps, every path "
                "before it was changed, commit. The exact history of an apply.",
            ],
            [
                "Tool logs",
                "`%LOCALAPPDATA%\\PortTool\\logs\\tools\\`",
                "The complete output of every DaVinci tool run. Findings of a run give the "
                "line number in this file.",
            ],
            [
                "git",
                "`git status`, `git log -3`, `git diff <base>..porttool/<date>-<n>`",
                "What was really changed. The commit message lists the requests.",
            ],
            [
                "Self-check log",
                "`selfcheck_log.txt` in the PortTool folder",
                "Which automated tests fail on this machine.",
            ],
        ],
        [1.8, 4.2, 4],
    )
    story.append(h2("10.3 Reading a code"))
    story.append(
        code(
            """
ERROR   RDR-DUP-001 Config/Developer/ComponentTypes/Swc3.arxml: Swc3 is defined more than once ...
^       ^           ^                                           ^
|       |           the file, when one is involved              what is wrong and what to do
|       code: prefix = part of the tool, then group, then number
severity: INFO (nothing to do), WARNING (it worked, but look at this),
          ERROR (this request or this step did not happen)
"""
        )
    )
    story += table(
        ["Prefix", "Comes from", "Typical subject", "Details"],
        [
            ["`CFG-`", "Configuration loading", "`porttool.toml`, `owners.csv`, `locked.txt`", "A.1"],
            ["`RDR-`", "Reader (load)", "ARXML files, references, cache, ECUC", "A.2"],
            ["`WBK-`", "Workbook export and import", "The `.xlsx` file, sheets, rows, cells", "A.3"],
            ["`VAL-`", "Validator (dry run)", "One request against the rules", "A.4"],
            ["`WRT-`", "Writer (plan and write)", "ARXML edits", "A.5"],
            ["`PIP-`", "Pipeline (apply, rollback)", "Pre-checks, git, verify, commit, report", "A.6"],
            ["`DVR-`", "DaVinci runner", "Starting Vector tools and reading their output", "A.7"],
            ["`DISC-`", "Discovery", "`porttool discover`", "`porttool\\discovery\\HANDOFF.md`"],
            ["`PT-E...`", "An error that stopped the tool", "See the next table", ""],
        ],
        [1.1, 2.7, 4.2, 2],
    )
    story += table(
        ["Error", "Class", "Means", "First thing to do"],
        [
            [
                "`PT-E100`",
                "ConfigError",
                "`porttool.toml`, `owners.csv` or `locked.txt` is missing, malformed or "
                "contradictory.",
                "The message names file, key and problem. Fix it; `porttool config check`.",
            ],
            [
                "`PT-E200`",
                "ArxmlParseError",
                "An ARXML file could not be parsed at a point where the tool cannot go on.",
                "Open the named file; look for a damaged end or merge-conflict markers.",
            ],
            [
                "`PT-E300`",
                "WorkbookError",
                "The workbook could not be written for an internal reason.",
                "Send the log (chapter 10.9).",
            ],
            [
                "`PT-E400`",
                "WriteError",
                "A file could not be written.",
                "Check that the file is not read-only or open elsewhere, and the disk space.",
            ],
            [
                "`PT-E410`",
                "RepoStateError",
                "The repository is not in the state the step needs: files changed since the "
                "load, another PortTool holds the lock, an apply is unfinished.",
                "`porttool status`; reload; dry run again; or `porttool rollback`.",
            ],
            [
                "`PT-E500`",
                "GitError",
                "git is missing or a git command failed.",
                "The message holds git's own text. Run the same in a command prompt.",
            ],
            [
                "`PT-E600`",
                "ToolRunError",
                "A DaVinci tool could not be run to its end, or its log could not be written.",
                "Open the tool log named in the message.",
            ],
            [
                "`PT-E610`",
                "ToolTimeoutError",
                "A DaVinci tool ran longer than its timeout and was stopped with its child "
                "processes.",
                "Check the tool log for a question the tool was waiting on; raise the value "
                "in `[timeouts]`.",
            ],
            [
                "`PT-E620`",
                "LicenseError",
                "The tool's output says a license is missing.",
                "Check the license server or dongle; start the tool by hand once.",
            ],
            [
                "`PT-E900`",
                "JobCancelled",
                "You cancelled the job.",
                "Nothing. An apply was rolled back.",
            ],
            [
                "exit code 3",
                "any other exception",
                "A bug.",
                "Send the log; the traceback is in it.",
            ],
        ],
        [1.1, 1.7, 3.7, 3.5],
    )

    story.append(h2("10.4 Problems by phase"))
    story.append(h3("Installation and start"))
    story += table(
        ["Symptom", "Cause", "Fix"],
        [
            [
                "`install.bat` cannot find Python",
                "Python is not on PATH, or it is older than 3.11, or 32-bit.",
                "Open a new command prompt: `python --version` must print 3.11 or newer. "
                "Reinstall from python.org with \"Add python.exe to PATH\".",
            ],
            [
                "`install.bat` cannot download packages",
                "No route to pypi.org (company proxy).",
                "Set the proxy for pip (`set HTTPS_PROXY=...`) or use your internal package "
                "mirror, then run `install.bat` again.",
            ],
            ["\"Run install.bat first.\"", "The `.venv` folder does not exist.", "Run `install.bat`."],
            [
                "`porttool` is not recognised",
                "The command prompt is not in the PortTool folder.",
                "`cd` to the PortTool folder, or add it to PATH.",
            ],
            [
                "`selfcheck.bat` reports problems",
                "First run on Windows: differences in file locking, paths, console encoding.",
                "Send `selfcheck_log.txt`. The read-only steps of chapter 4 can still be done.",
            ],
            [
                "Strange characters on the console",
                "Console code page.",
                "Use Windows Terminal, or `chcp 65001` before the command. The log file is "
                "always UTF-8.",
            ],
        ],
        [2.6, 3.2, 4.2],
    )
    story.append(h3("Configuration"))
    story += table(
        ["Symptom", "Cause", "Fix"],
        [
            [
                "`[PT-E100]` porttool.toml not found",
                "Wrong `--repo`, or `init` was never run.",
                "Point `--repo` at the folder that holds `porttool.toml`, or run `porttool init`.",
            ],
            [
                "`init` stops because of several `.dpa` files",
                "The repository holds more than one Configurator project.",
                "`porttool init --dpa Config\\MyEcu.dpa`.",
            ],
            ["`CFG-KEY-001` unknown key", "Typing mistake in a key or table name.", "Correct the spelling; the key was ignored."],
            ["`CFG-PATH-003` ARXML folder missing", "`arxml_roots` names a folder that does not exist.", "Correct `[paths] arxml_roots`."],
            [
                "`CFG-OWNERS-004`, `-005`",
                "A model-owned SWC has no `export_folder`, or the folder does not exist.",
                "Fill the column in `owners.csv`.",
            ],
        ],
        [2.8, 3.4, 3.8],
    )
    story.append(h3("Load"))
    story += table(
        ["Symptom", "Cause", "Fix"],
        [
            [
                "Fewer SWCs, ports or connectors than expected",
                "A workspace folder is not in `arxml_roots`, or `exclude_globs` is too wide. "
                "See also `RDR-DCF-002` to `-004`: files the `.dcf` lists but the load did "
                "not find, and the other way round.",
                "Compare `[paths]` with the folders the `.dcf` refers to. `porttool list swc` "
                "shows what was loaded.",
            ],
            [
                "`RDR-DUP-001` defined more than once",
                "The same object is in two loaded files: often a model export folder or a "
                "backup copy below `arxml_roots`.",
                "Exclude one copy with `exclude_globs`.",
            ],
            [
                "`RDR-REF-001` referenced object not loaded",
                "The file that defines it (platform types, service interfaces of the BSW) is "
                "outside `arxml_roots`.",
                "Add its folder to `arxml_roots`. Until then requests that need the object "
                "are rejected (`VAL-REF-014`).",
            ],
            [
                "`RDR-PARSE-001` not well-formed",
                "A damaged file, often with git merge-conflict markers.",
                "Open the file at the reported line; repair it in git. The file was not loaded.",
            ],
            [
                "`RDR-FILE-002` cannot be read",
                "Another program holds the file (DaVinci, an editor, the virus scanner).",
                "Close the program; load again.",
            ],
            [
                "`OS tasks: 0, mapped events: 0`",
                "`ecuc_file` is not set or points to the wrong file.",
                "Set `[paths] ecuc_file`. Without it no task default can be found "
                "(`VAL-TRG-005`, `-007`).",
            ],
            [
                "Every load is slow; `RDR-CACHE-001`",
                "The cache folder cannot be used.",
                "Make `%LOCALAPPDATA%\\PortTool` writable or set `[paths] cache_dir`. The "
                "first load after a large `git pull` is slow by nature.",
            ],
            [
                "\"Files changed on disk since the model was loaded\"",
                "Somebody (you, git, DaVinci) changed workspace files after the load.",
                "Reload, then dry run again.",
            ],
        ],
        [2.8, 3.6, 3.6],
    )
    story.append(h3("Workbook"))
    story += table(
        ["Symptom", "Cause", "Fix"],
        [
            ["`WBK-EXPORT-003`", "The target file is open in Excel or read-only.", "Close it. The old file is untouched."],
            ["`WBK-FILE-004`", "A workbook to import is open in Excel.", "Close it and import again."],
            ["`WBK-FILE-003`, `-005`, `-006`", "Not an `.xlsx`, password-protected, or damaged.", "Save as Excel Workbook without password."],
            [
                "\"My rows were ignored\"",
                "No Action on the row; or the sheet was renamed (`WBK-SHEET-003`); or a "
                "column header was changed (`WBK-COLUMN-001`).",
                "`porttool workbook-check file.xlsx` prints exactly the rows the tool reads, "
                "without needing the repository.",
            ],
            [
                "`WBK-CELL-001` date or time",
                "Excel turned an entry such as `1-2` or `MAR1` into a date.",
                "Format the cell as Text and type the value again.",
            ],
            ["`WBK-CELL-002`, `-003`, `-004`", "A formula or merged cells in a request row.", "Replace by plain values; unmerge."],
            [
                "`WBK-META-002`",
                "The workbook was exported from another commit.",
                "Only a warning. Every request is checked against the project as it is now.",
            ],
            ["`WBK-META-004`", "The workbook comes from a newer PortTool.", "Update PortTool."],
        ],
        [2.6, 3.8, 3.6],
    )
    story.append(need(60))
    story.append(h3("Validation: the rejections you will see most"))
    story += table(
        ["Code", "Means", "Fix in the workbook"],
        [
            ["`VAL-NAME-001` to `-004`", "Not a valid short name, too long, against the project naming rule, or a C keyword.", "Rename. Letters, digits, underscore; starts with a letter."],
            ["`VAL-NAME-005`, `-006`", "The name exists already in that SWC or package, or differs only by case.", "Choose another name; or the object exists and the row is not needed."],
            ["`VAL-REF-001`, `-003`, `-005`", "The SWC, interface or data type does not exist.", "Check spelling and case. If another row creates it, see whether that row was rejected (`VAL-REF-013`)."],
            ["`VAL-REF-002`, `-004`, `-006`", "Several objects have this short name.", "Type the full path, starting with `/`."],
            ["`VAL-REF-010`, `-011`, `-012`", "The SWC has no such port or runnable; the interface has no such element.", "Correct the name; create the port in the same round."],
            ["`VAL-OWN-001` to `-004`", "The SWC is locked, a service SWC or a composition; or the composition's connectors cannot be edited.", "Not possible through PortTool. Talk to the owner."],
            ["`VAL-PORT-003`", "A data element has no init value.", "Enter `Element=value`, or `none` on purpose."],
            ["`VAL-PORT-005`, `-006`", "The init value is not a value of the data type, or outside its range.", "Correct the value."],
            ["`VAL-CON-001`", "Provider is not P/PR or requester is not R/PR.", "Swap the two sides."],
            ["`VAL-CON-003`", "The interfaces of the two ports are not compatible.", "Use ports of the same interface."],
            ["`VAL-CON-005`, `-006`", "No composition, or more than one, contains both components.", "Fill the Composition cell. Connections across compositions are not supported."],
            ["`VAL-MUL-001` to `-003`", "The connection would break a multiplicity rule (n:m, two servers for a client).", "Remove one of the connections."],
            ["`VAL-ACC-002`, `-003`", "The access kind does not fit the port direction, interface kind or queued setting.", "See the table in chapter 6.3 F."],
            ["`VAL-TRG-005`, `-007`", "The task does not exist; or the runnable runs in several tasks.", "Name an existing task in the Task cell."],
            ["`VAL-TRG-006`", "The server operation already has a runnable.", "Remove the row; one runnable per operation."],
            ["`VAL-ROW-006`", "The object of this row no longer exists in the repository.", "Take a fresh export."],
            ["`VAL-ROW-008`, `VAL-IF-004`", "MODIFY, DELETE or adding to an existing interface is not supported here.", "Do it in DaVinci Developer."],
            ["`VAL-CFL-002`", "Two rows ask for the same object in different ways.", "The two requesters agree on one definition."],
            ["`VAL-IF-009`", "Another row of the same interface was rejected.", "Fix that row; the interface is created as a whole."],
        ],
        [2.4, 4.2, 3.4],
    )
    story.append(need(60))
    story.append(h3("Apply refused before it starts (pre-checks)"))
    story += table(
        ["Code", "Means", "Fix"],
        [
            ["`PIP-PRE-001`", "Another PortTool works on this repository.", "Close the other one (often a forgotten GUI window). A lock left by a crashed process is detected and replaced by itself."],
            ["`PIP-PRE-002`", "The journal of an unfinished apply exists.", "`porttool rollback`."],
            ["`PIP-PRE-003`, `-004`, `-005`", "No dry run; or the model was reloaded or files changed since the dry run.", "Reload if needed, then dry run again."],
            ["`PIP-PRE-006`", "The dry run reported errors that block the apply (plan or settings).", "Fix those findings first."],
            ["`PIP-PRE-007`", "The pending file changed since the dry run, or is unreadable (`PIP-PEND-001` to `-003`, for example merge-conflict markers).", "Repair `porttool\\pending.json` in git; dry run again."],
            ["`PIP-PRE-008`", "The round contains DELETE requests.", "Remove them, or set `[pipeline] allow_delete = true` (the writer still rejects deletes in 0.1.0)."],
            ["`PIP-PRE-009`", "Less free disk space than `[limits] min_free_disk_bytes`.", "Free space."],
            ["`PIP-PRE-010`", "DaVinci Developer or Configurator seems to have the project open (`DVR-GUI-RUNNING`), or a lock file lies next to the `.dcf` or `.dpa` (`DVR-GUI-LOCKFILE`).", "Close DaVinci. Delete a lock file left by a crash. If the process names differ on your machine, set `[davinci] gui_process_names`."],
            ["`PIP-PRE-011`, `-012`, `-013`", "git not on PATH; the folder is not a git work tree; the repository has no commit.", "Install Git for Windows; use the right folder."],
            ["`PIP-PRE-014`", "A merge, rebase, cherry-pick, revert or bisect is in progress.", "Finish or abort it."],
            ["`PIP-PRE-015`", "Tracked files have uncommitted changes.", "Commit or stash them. PortTool never mixes its change with yours."],
            ["`PIP-PRE-016`", "Untracked files inside the workspace folders.", "Add and commit them, or move them away."],
            ["`PIP-PRE-018`", "git `user.name` / `user.email` is not set.", "`git config user.name \"...\"` and `git config user.email \"...\"`."],
            ["`PIP-PRE-019`", "A DaVinci step is enabled but the runner cannot be used.", "`porttool davinci doctor`."],
        ],
        [2.2, 4.2, 3.6],
    )
    story.append(need(60))
    story.append(h3("During write and verify (the apply rolls back by itself)"))
    story += table(
        ["Code", "Means", "Fix"],
        [
            ["`WRT-UNSUPPORTED-001`", "The writer has no template for this request yet.", "Shown in the dry run. Do this one in DaVinci Developer."],
            ["`WRT-BEHAVIOR-001`", "The SWC has no internal behavior.", "Create it in DaVinci Developer (one runnable is enough)."],
            ["`WRT-PLACE-001`", "No file holds the package for a new data type or interface.", "Correct `[packages]`, or set `[writer] new_object_placement = \"new_file\"`."],
            ["`WRT-DCF-001`", "A new file was created but not added to the `.dcf`.", "Add the file to the workspace in DaVinci Developer."],
            ["`WRT-READONLY-001`", "The target file is read-only.", "Clear the attribute (or check the file out)."],
            ["`WRT-LOCKED-001` with `WRT-ABORT-001`", "A file was held open by another program while it was replaced.", "Close DaVinci, editors, compare tools; apply again."],
            ["`WRT-STATE-001`, `-002`", "A file changed since the load, or is not part of the model.", "Reload; dry run again."],
            ["`WRT-XML-001`, `WRT-XSD-001`", "The result would not be well-formed or not valid. The file was left as it is.", "A writer problem. Report it with the request and the file (10.9)."],
            ["`PIP-VER-001`, `-002`", "After the reload a written object is missing or differs from the request.", "A writer or reader problem. Report it."],
            ["`PIP-VER-003`, `-004`", "Objects that existed are gone, or the reload has new errors.", "A writer problem. Report it."],
            ["`PIP-VER-005`", "Files changed that the tool did not write.", "Another program wrote into the repository during the apply (DaVinci, a sync tool). Close it."],
            ["`PIP-COMMIT-002`", "git refused the commit (a hook). Hooks are never bypassed.", "Read the hook's message in the report; fix the cause."],
        ],
        [2.6, 4, 3.4],
    )
    story.append(need(60))
    story.append(h3("DaVinci steps"))
    story += table(
        ["Code", "Means", "Fix"],
        [
            ["`DVR-EXE-MISSING`", "The executable was not found.", "Set `[paths] sip_root` / `developer_bin`, or the full path in `[davinci.executables]`. The message holds the line to add."],
            ["`DVR-TPL-UNVERIFIED`", "The command line is still the built-in guess.", "Expected until you set it in `[davinci.commands]` (chapter 9.4)."],
            ["`DVR-TPL-UNRESOLVED`, `DVR-PATH-NOT-SET`", "A placeholder has no value, for example `dcf_file` is not set.", "Set the path in `[paths]`."],
            ["`DVR-RUN-EXIT`", "The tool ended with an exit code that is not a success code.", "Read the tool log. Wrong flags are the usual cause while templates are unverified. If the tool uses another success code, set `[davinci.results.success_exit_codes]`."],
            ["`DVR-RUN-ERROR-LINE`", "An output line matched an error pattern.", "Read the line in the tool log. If it is harmless (\"0 errors\"), add it to `[davinci.results] ignore_patterns`."],
            ["`[PT-E610]`", "Timeout.", "The tool may wait for an answer or a dialog: look at the end of the tool log. Otherwise raise `[timeouts]`."],
            ["`[PT-E620]`", "License problem.", "Check the license; start the tool by hand."],
            ["`PIP-DV-001`", "The workspace check failed after the edits.", "The apply was rolled back. Apply once with `davinci_steps = []`, open the branch in DaVinci Developer and see what it objects to (10.7)."],
            ["`PIP-DV-004`, `DVR-SCRIPT-...`", "The task-mapping script failed.", "Expected: it is experimental. Remove `\"script\"` from `davinci_steps` and map by hand."],
            ["`PIP-DV-005`", "Project validation failed.", "Open Configurator. New events without task mapping are the usual cause."],
            ["`PIP-DV-006`", "Generation failed.", "Read the tool log; generate by hand to see the tool's own messages."],
            ["`PIP-DV-007`", "New triggers need a manual task mapping.", "Not an error: follow the instructions in the report."],
        ],
        [2.6, 3.4, 4],
    )
    story.append(need(55))
    story.append(h3("GUI"))
    story += table(
        ["Symptom", "Cause", "Fix"],
        [
            ["The browser does not open", "No default browser could be started.", "Open the address printed in the console window by hand."],
            ["\"This page was opened without its session token\"", "The page was reloaded (F5), bookmarked, or opened without the token.", "Open the printed address (it ends with `?token=...`) again."],
            ["The page stops answering", "The console window was closed, so the server is gone.", "Start `PortTool-GUI.bat` again."],
            ["\"job-running\"", "One job runs at a time.", "Wait, or cancel the running job."],
            ["Apply button disabled", "No accepted request in the last validation, the model is stale, or recovery is needed.", "Validate again; reload; or roll back."],
            ["Upload refused", "Not an `.xlsx`; larger than `[gui] max_upload_mb`; more than `max_upload_files` files.", "Save as `.xlsx`; raise the limits in `[gui]`."],
            ["The port is in use", "A fixed `--port` or `[gui] port` is taken.", "Use another port, or 0 for a free one."],
        ],
        [3, 3.6, 3.4],
    )

    story.append(h2("10.5 Recovery playbook"))
    story.append(p("When an apply was interrupted (crash, kill, power loss, laptop closed):"))
    story += steps(
        [
            "`porttool status --repo C:\\ecu`. It prints `RECOVERY NEEDED` when a journal is "
            "open. Do not edit files or switch branches by hand.",
            "Close DaVinci Developer, Configurator, Excel and editors that may hold workspace "
            "files.",
            "`porttool rollback --repo C:\\ecu` (or **Roll back** in the GUI). It restores "
            "every journaled file, removes files the run created, returns to your branch and "
            "deletes the new branch if nothing was committed on it (`PIP-RB-001`).",
            "If it reports `PIP-RB-006` (could not finish, usually a locked file): close the "
            "program that holds the file and run `rollback` again. The journal stays open "
            "until a rollback succeeds.",
            "`PIP-RB-002` means the interrupted run had already committed. The branch is kept "
            "with its commit and is complete; you are back on your branch. Inspect the branch "
            "and merge or delete it.",
            "`porttool status` must end with `No unfinished apply.` Then load and dry run "
            "again.",
        ]
    )
    story.append(
        note(
            "warn",
            "Last resort, by hand.",
            "Only if `rollback` keeps failing: copy the folder "
            "`%LOCALAPPDATA%\\PortTool\\journals\\<repo id>` somewhere safe. The first line of "
            "`apply.journal.jsonl` holds `original_branch` and `original_commit`; the `path` "
            "lines name every touched file. Restore them with `git checkout <original_commit> "
            "-- <path>`, delete files the run created, `git checkout <original_branch>`, "
            "`git branch -D porttool/<date>-<n>`, then move `apply.journal.jsonl` out of the "
            "journal folder. Report the case with the copied folder.",
        )
    )

    story.append(h2("10.6 Reading the journal"))
    story.append(
        code(
            r"""
{"type":"begin","original_branch":"develop","original_commit":"f08454f2...","tool_version":"0.1.0", ...}
{"type":"branch","name":"porttool/20261112-1", ...}
{"type":"step","name":"write","state":"started", ...}
{"type":"path","rel_path":"Config/Developer/ComponentTypes/Swc1.arxml","action":"modify", ...}
{"type":"step","name":"write","state":"finished", ...}
{"type":"step","name":"verify","state":"started", ...}
{"type":"commit","sha":"d6f4bb92...", ...}
{"type":"closed","outcome":"committed", ...}
"""
        )
    )
    story.append(
        p(
            "Each line is written and flushed to disk **before** the action it announces. The "
            "last `step ... started` without a matching `finished` is where a run stopped. "
            "`scope` lines name the folders DaVinci tools were allowed to write. A journal "
            "without a `closed` line is an open journal: that is what \"recovery needed\" "
            "means."
        )
    )

    story.append(need(70))
    story.append(h2("10.7 When DaVinci Developer does not like what was written"))
    story.append(
        p(
            "This is the most likely real problem of the first weeks, because the writer's XML "
            "was never compared with Developer's own output. Typical signs: Developer reports "
            "an error when opening the workspace, shows the new object incompletely, or "
            "rewrites it on save."
        )
    )
    story += steps(
        [
            "Apply one single request with `davinci_steps = []`, so the branch holds exactly "
            "one small change.",
            "`git show` on that commit: this is what PortTool wrote.",
            "Make the same change by hand in DaVinci Developer on another branch and commit "
            "it. `porttool discover golden-diff --repo ... --base <before> --head <after>` "
            "shows what Developer writes.",
            "Compare the two. Differences in naming or optional elements are usually covered "
            "by a `[writer]` setting; see the table below. Structural differences need a "
            "change in the writer: report them with both diffs.",
        ]
    )
    story += table(
        ["Difference you see", "Setting in `[writer]`"],
        [
            ["UUID attribute missing, present, or in another letter case", "`uuid` = `auto` (follow the file), `upper`, `lower`, `none`"],
            ["Names of access points (`DRA_...`, `DSP_...`)", "`access_point_pattern`, `access_point_prefixes`"],
            ["Names of RTE events (`TMT_...`, `DRT_...`, `OIT_...`)", "`event_patterns`"],
            ["Com specs: one per data element or not; default values on receiver side; client com spec", "`com_spec_for_every_element`, `receiver_com_spec_defaults`, `client_com_spec`"],
            ["Queue length, server call timeout", "`queue_length`, `server_call_timeout`"],
            ["Names of compu methods and data constraints", "`compu_method_pattern`, `constraint_pattern`"],
            ["Name of the array element", "`array_element_name`"],
            ["New data types and interfaces in an existing file or in a file of their own", "`new_object_placement`"],
            ["Data type mapping set not updated", "`data_type_mapping_set`"],
        ],
        [5.4, 4.6],
    )
    story.append(
        p(
            "The writer's 19 assumptions about the file format are listed, numbered, in "
            "`porttool\\writer\\HANDOFF.md` under \"PENDING-DISCOVERY assumptions\". Setting "
            "`[paths] xsd_file` to the AUTOSAR schema of your release makes the writer validate "
            "every file before it replaces it (`WRT-XSD-001`)."
        )
    )

    story.append(h2("10.8 Looking inside (for maintainers)"))
    story += table(
        ["To do this", "Use"],
        [
            ["See exactly what the workbook import reads", "`porttool workbook-check file.xlsx --json`"],
            ["See every validator rule", "`porttool validator-codes`; source in `porttool\\validator\\`, one module per subject (`_ports.py`, `_connectors.py`, `_behavior.py`, ...)"],
            ["See what the writer would do, without the pipeline", "`porttool writer-plan changeset.json`, `porttool writer-delta changeset.json out.arxml`"],
            ["See the command lines of the Vector tools", "`porttool davinci show-commands`"],
            ["Get a debug log", "`porttool -v --log-dir C:\\temp\\ptlog <command>`"],
            ["Run all tests", "`.venv\\Scripts\\python -m pytest -q` in the PortTool folder (1,773 tests; a few minutes)"],
            ["Run the tests of one package", "`.venv\\Scripts\\python -m pytest tests\\unit\\writer -q`"],
            ["Make a test repository to experiment on", "`.venv\\Scripts\\python -m tests.fixtures.generator C:\\temp\\gen --preset small` (or `large`: 300 SWCs, 20,000 ports), then `git init` and commit in it"],
            ["Understand a package", "Its `HANDOFF.md`: public API, settings, codes, decisions, corner cases with the name of the test for each, and what is unverified on Windows"],
        ],
        [3.6, 6.4],
    )
    story.append(
        p(
            "Code map: `porttool\\core` (contracts every package uses), `engine`, `reader`, "
            "`workbook`, `validator`, `writer`, `davinci`, `pipeline` (start reading at "
            "`service.py` and `transaction.py`), `gui` (`server.py` and `static\\`), "
            "`discovery`. Tests mirror the packages under `tests\\unit\\<package>`; "
            "`tests\\integration` runs all real parts together."
        )
    )
    story.append(h2("10.9 Reporting a problem"))
    story.append(p("Send these, and the problem can usually be found without a second question:"))
    story += bullets(
        [
            "The PortTool version (`porttool --version`) and the exact command or the GUI step.",
            "The complete console text, or a screenshot of the GUI.",
            "The log: run the command again with `-v --log-dir C:\\temp\\ptlog` and send that "
            "folder.",
            "For an apply: `report.html` and the journal file from `archive\\` (or the open "
            "`apply.journal.jsonl`).",
            "For a DaVinci step: the tool log from `logs\\tools\\`.",
            "For a wrong ARXML result: the two diffs of chapter 10.7.",
        ]
    )
    story.append(
        note(
            "warn",
            "Mind what you send.",
            "Logs, reports and journals contain names of SWCs, ports and files of your "
            "project, and reports contain ARXML fragments. Check your NDA before sending them "
            "outside the company. `porttool discover run --anonymize` produces a report with "
            "every name replaced by a token.",
        )
    )
    return story


def chapter_11() -> list[Flowable]:
    story = h1("11. Limits and open points of version 0.1.0")
    story.append(h2("11.1 Not verified yet"))
    story += table(
        ["Area", "State", "How it gets closed"],
        [
            ["Windows", "Nothing has run on Windows: file locking, long paths, console encoding, stopping process trees, opening the browser, git line-ending settings.", "`selfcheck.bat` and the first-run checklist (chapter 4)."],
            ["ARXML output", "Follows the AUTOSAR 4.x schema from memory. Not checked against the XSD or against a file saved by DaVinci Developer.", "Step 9 of chapter 4, golden diffs, `xsd_file`."],
            ["`.dcf` and `.dpa`", "The real file formats are unknown. The `.dcf` is never written; new objects go into existing files by default.", "`porttool discover run` report."],
            ["Vector command lines", "All are UNVERIFIED templates. No real tool has been started.", "The `cli` folder of the discover report, then `[davinci.commands]`."],
            ["Task-mapping script", "The Groovy script has never been executed.", "Leave `script` off; map by hand."],
            ["Excel", "The workbook was only read back by a library, never opened in Excel.", "Step 6 of chapter 4."],
            ["Timings", "Measured on Linux with generated data.", "`porttool load` prints its timings on your machine."],
        ],
        [1.8, 5, 3.2],
    )
    story.append(h2("11.2 Not built yet"))
    story += bullets(
        [
            "MODIFY and DELETE requests.",
            "Mode-switch triggers; parameter, NV-data and trigger interfaces; string data types.",
            "Connections across nested compositions (delegation connectors).",
            "Writing the task mapping into the ECUC file (by design it is left to Configurator).",
            "Bus signal data mapping and service port mapping.",
            "Runnables other than server runnables; inter-runnable variables; SWC implementation.",
            "An installer or a single `.exe`. The tool runs from a Python environment.",
            "A separate quality pass: long-running soak test, cross-package corner-case matrix.",
        ]
    )
    story.append(h2("11.3 Things to keep in mind"))
    story += bullets(
        [
            "PortTool edits the same files DaVinci Developer edits. Never have Developer open "
            "on the workspace while applying, and reload in Developer after a merge.",
            "The tool validates against the AUTOSAR rules it knows. DaVinci's own checks and "
            "the generators remain the final judge. Keep generating and building before "
            "merging.",
            "One integrator applies at a time per clone. Two people can apply in two clones; "
            "git then merges the branches like any other change, and conflicts in ARXML are "
            "resolved as today.",
        ]
    )
    return story


# ------------------------------------------------------------------ appendix


def _rows_from_markdown(file: Path, prefix: str) -> list[list[str]]:
    """Rows of the markdown tables in `file` whose first cell is a code with `prefix`."""
    rows: list[list[str]] = []
    for line in file.read_text(encoding="utf-8").splitlines():
        if not re.match(rf"^\|\s*`?{prefix}", line):
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        cells = [cell.replace("\\|", "|") for cell in cells]
        cells[0] = "`" + cells[0].strip("`") + "`"
        rows.append(cells)
    return rows


def _three(rows: list[list[str]]) -> list[list[str]]:
    return [[row[0], row[1] if len(row) > 2 else "", row[-1]] for row in rows]


def appendix_a() -> list[Flowable]:
    from porttool.validator import codes as validator_codes

    story = h1("Appendix A. All codes")
    story.append(
        p(
            "The tables are taken from the code and from the `HANDOFF.md` files of the "
            "packages at the time this guide was built. `porttool validator-codes` and "
            "`porttool writer-codes` print the current lists of an installed version."
        )
    )
    story.append(h2("A.1 CFG: configuration"))
    story += table(
        ["Code", "Severity", "Meaning"],
        [
            ["`CFG-KEY-001`", "warning", "Unknown key or table in `porttool.toml`; ignored. Check the spelling."],
            ["`CFG-PATH-001`", "warning", "An optional path of `[paths]` does not exist."],
            ["`CFG-PATH-002`", "error", "A file was expected but a folder was given, or the other way round."],
            ["`CFG-PATH-003`", "error", "A folder of `arxml_roots` does not exist."],
            ["`CFG-OWNERS-001`", "info", "No `owners.csv`: every SWC counts as DaVinci-owned."],
            ["`CFG-OWNERS-002`", "warning", "Unknown column in `owners.csv`; ignored."],
            ["`CFG-OWNERS-003`", "info", "The same row twice."],
            ["`CFG-OWNERS-004`", "warning", "A model-owned SWC without `export_folder`."],
            ["`CFG-OWNERS-005`", "warning", "The export folder does not exist."],
            ["`CFG-LOCKED-001`", "info", "No `locked.txt`: no SWC is locked."],
        ],
        [2.2, 1.2, 6.6],
    )
    story.append(
        p(
            "Problems that make the configuration unusable are not findings but the error "
            "`[PT-E100]`: missing or malformed `porttool.toml`, wrong type or value, invalid "
            "regular expression, missing `arxml_roots`, invalid owner, contradictory rows, a "
            "newer `format_version`."
        )
    )
    story.append(h2("A.2 RDR: reader"))
    story += table(
        ["Code", "Severity", "Meaning"],
        _three(_rows_from_markdown(REPO / "porttool/reader/HANDOFF.md", "RDR-")),
        [2.2, 1.2, 6.6],
    )
    story.append(h2("A.3 WBK: workbook"))
    story += table(
        ["Code", "Severity", "Meaning"],
        _three(_rows_from_markdown(REPO / "porttool/workbook/HANDOFF.md", "WBK-")),
        [2.2, 1.4, 6.4],
    )
    story.append(h2("A.4 VAL: validator"))
    story += table(
        ["Code", "Severity", "Meaning"],
        [
            [f"`{info.code}`", info.severity.value, info.description]
            for info in validator_codes.CODES.values()
        ],
        [2.2, 1.2, 6.6],
    )
    story.append(h2("A.5 WRT: writer"))
    story += table(
        ["Code", "Severity", "Meaning"],
        _three(_rows_from_markdown(REPO / "porttool/writer/HANDOFF.md", "WRT-")),
        [2.6, 1.2, 6.2],
    )
    story.append(h2("A.6 PIP: pipeline"))
    story += table(
        ["Code", "Meaning"],
        [[row[0], row[-1]] for row in _rows_from_markdown(REPO / "porttool/pipeline/HANDOFF.md", "PIP-")],
        [2.2, 7.8],
    )
    story.append(h2("A.7 DVR: DaVinci runner"))
    story += table(
        ["Code", "Severity", "Meaning"],
        _three(_rows_from_markdown(REPO / "porttool/davinci/HANDOFF.md", "DVR-")),
        [3.2, 2, 4.8],
    )
    return story


def appendix_b() -> list[Flowable]:
    story = h1("Appendix B. Glossary")
    story.append(h2("B.1 AUTOSAR and Vector terms"))
    story += table(
        ["Term", "Meaning in this guide"],
        [
            ["SWC, component type", "A software component as a type: it owns ports and an internal behavior."],
            ["Component prototype", "An instance of a component type inside a composition. Connectors connect prototypes."],
            ["Composition", "A component that contains prototypes and the connectors between them."],
            ["Port (P, R, PR)", "Provide, require, or provide-require port of a component type, typed by a port interface."],
            ["Port interface", "Sender-receiver (data elements) or client-server (operations with arguments); others exist."],
            ["Com spec, init value", "Communication settings of a port per data element, including the initial value."],
            ["Internal behavior", "The part of a SWC that holds runnables, RTE events and access points."],
            ["Runnable", "A function of the SWC that the RTE calls."],
            ["RTE event (trigger)", "What makes the RTE call a runnable: timing, data received, operation invoked, init, ..."],
            ["Access point", "The declaration that a runnable reads, writes or calls a port element. Implicit access works on a copy for the runnable's whole run; explicit access calls the RTE at that moment."],
            ["Assembly connector", "Connects a provider port and a requester port of two prototypes in one composition."],
            ["Delegation connector", "Connects an inner port to a port of the composition itself. Not created by PortTool."],
            ["Application / implementation data type", "The physical view of a value (with unit, scaling, limits) and its C representation."],
            ["Compu method, data constraint", "Scaling or enumeration texts, and limits, of an application data type."],
            ["ECUC, task mapping", "ECU configuration. The RTE configuration there maps each RTE event to an OS task and position."],
            ["Flat extract", "The ECU extract of the system description, with all compositions flattened."],
            ["`.dcf`", "The DaVinci Developer workspace file. Lists the ARXML files of the workspace."],
            ["`.dpa`", "The DaVinci Configurator project file."],
            ["SIP", "Vector's delivery of the MICROSAR stack with its tools."],
            ["DVWspChecker, DVImEx, DVSwcGen", "Command-line tools of DaVinci Developer: workspace check, import/export, component code generation."],
            ["DVCfgCmd", "The command line of DaVinci Configurator: script tasks, validation, generation."],
        ],
        [3, 7],
    )
    story.append(h2("B.2 PortTool terms"))
    story += table(
        ["Term", "Meaning"],
        [
            ["Baseline", "The git commit a workbook was exported from."],
            ["Model hash", "A fingerprint of the loaded model. Tells whether a dry run still matches the model."],
            ["RowID", "The identifier of an existing object in the workbook (a sheet prefix and 12 hex digits). New rows have none."],
            ["Operation", "One edit the writer can make: add data type, add interface, add port, add server runnable, add access point, add trigger, add connector."],
            ["Change set", "The ordered list of operations of one dry run."],
            ["Plan", "What the writer would change in which file, computed without writing."],
            ["Pre-checks (preflight)", "The checks before the first change of an apply."],
            ["Journal", "The record of an apply, written ahead of each change, used for rollback."],
            ["Pending queue", "Requests that wait: for a model team, or (connectors) for a port."],
            ["Request package", "`requests.csv` and `delta.arxml` for a model team."],
            ["Delta ARXML", "An ARXML file that holds only the elements of a change."],
            ["Stale", "Workspace files changed on disk after the model was loaded."],
            ["Write scope", "A folder DaVinci tools may write during an apply; restored on rollback."],
            ["Golden diff", "The difference DaVinci Developer itself produces for one hand-made change."],
            ["UNVERIFIED", "A command line or script nobody has checked against the real Vector tool."],
        ],
        [3, 7],
    )
    return story

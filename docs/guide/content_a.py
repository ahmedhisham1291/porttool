"""Engineer guide, part A: cover, contents, chapters 1 to 5."""

from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph

from guide_lib import (
    H1,
    NextPageTemplate,
    PageBreak,
    Spacer,
    apply_diagram,
    architecture_diagram,
    bullets,
    caption,
    code,
    contents,
    cycle_diagram,
    gap,
    h1,
    h2,
    h3,
    need,
    note,
    p,
    steps,
    table,
)

TOC_TITLE = ParagraphStyle("TocTitle", parent=H1)


def cover() -> list[Flowable]:
    story: list[Flowable] = [Spacer(1, 88 * mm)]
    story.append(
        p(
            "PortTool reads the DaVinci Developer workspace of an AUTOSAR Classic project and "
            "shows every software component with its ports, runnable access and connections. "
            "Teams enter change requests in an Excel workbook. PortTool validates the requests, "
            "edits the ARXML files on a git branch, checks the result and, when you switch "
            "that on, runs the DaVinci command-line tools to check the workspace and generate "
            "RTE and OS."
        )
    )
    story.append(gap(4))
    story += table(
        ["If you ...", "Read"],
        [
            ["fill in the workbook for your team", "Chapter 1 and chapter 6"],
            [
                "run the tool for the project (integrator)",
                "Chapters 3 to 9, then keep chapter 10 at hand",
            ],
            [
                "have a problem to solve",
                "Chapter 10 (debugging), then appendix A for the meaning of a code",
            ],
            [
                "maintain or extend the tool",
                "Chapter 2, chapter 10.8, chapter 11 and the `HANDOFF.md` file of each package",
            ],
        ],
        [4, 6],
    )
    story.append(
        note(
            "danger",
            "Status of version 0.1.0.",
            "It was built and tested on Linux with generated test repositories and stand-in "
            "DaVinci tools: 1,773 automated tests pass. It has not yet run on Windows, on a "
            "real ECU repository, in real Excel, or with a real Vector tool. Do the first runs "
            "on a copy of the repository and follow chapter 4. Chapter 11 lists what is "
            "unverified.",
        )
    )
    story.append(
        note(
            "info",
            "Out of the box, PortTool starts no DaVinci tool.",
            "By default an apply edits the ARXML, verifies it by reading it again, commits on "
            "a new branch and stops. You then open DaVinci Developer and Configurator as you "
            "do today. The automatic DaVinci steps are switched on one by one in "
            "`porttool.toml` after their command lines were checked on your machine "
            "(chapter 9.4).",
        )
    )
    story += [NextPageTemplate("body"), PageBreak(), Paragraph("Contents", TOC_TITLE), contents()]
    return story


def chapter_1() -> list[Flowable]:
    story = h1("1. What PortTool is")
    story.append(h2("1.1 The job it takes over"))
    story.append(
        p(
            "Today an engineer who needs a new signal between two software components (SWCs) "
            "opens DaVinci Developer, creates the data type, the port interface and the port "
            "prototypes, gives a runnable access to the port (implicit or explicit), adds a "
            "trigger if needed, draws the connector to the other SWC, saves, then opens "
            "DaVinci Configurator and generates RTE and OS. Several teams do this every day on "
            "the same workspace, one after the other, by hand."
        )
    )
    story.append(
        p(
            "PortTool turns that into a request list. It shows the current state of the "
            "workspace as tables (in a browser page and in an Excel workbook). Each team adds "
            "rows that say what it needs. One person, the integrator, feeds all workbooks back "
            "to the tool. The tool checks every row against the project and against the "
            "AUTOSAR rules, tells each requester what was accepted or rejected and why, writes "
            "the accepted changes into the ARXML files, and records everything in git."
        )
    )
    story.append(h2("1.2 By hand and with PortTool"))
    story += table(
        ["Step in DaVinci today", "With PortTool", "What is written"],
        [
            [
                "Create a data type",
                "Row on sheet `DataTypes`",
                "Application or implementation data type; compu method and data constraint "
                "when limits, scaling or enumeration values are given",
            ],
            [
                "Create a port interface",
                "Row on `Interfaces` plus rows on `InterfaceElements`",
                "Sender-receiver interface with data elements, or client-server interface "
                "with operations and arguments",
            ],
            [
                "Create a port prototype",
                "Row on `Ports`",
                "P-, R- or PR-port with its com specs (init value)",
            ],
            [
                "Give a runnable access to a port",
                "Row on `RunnableAccess`",
                "Implicit read or write, explicit read or send, queued receive, synchronous "
                "or asynchronous server call",
            ],
            [
                "Add a trigger to a runnable",
                "Row on `RunnableTriggers`",
                "RTE event: timing, data received, data receive error, operation invoked, init",
            ],
            [
                "Create the runnable of a new server port",
                "Row on `RunnableTriggers` with kind `on operation invocation`",
                "The runnable and its operation-invoked event",
            ],
            [
                "Connect two ports",
                "Row on `Connections`",
                "Assembly connector in the composition",
            ],
            [
                "Map the new event to a task",
                "Column `Task` of the trigger row (default: the task of the runnable's "
                "existing trigger)",
                "Not written into ARXML by PortTool. It is done in Configurator: by the "
                "experimental script step, or by hand from the instructions in the report "
                "(chapter 5.4)",
            ],
            [
                "Generate RTE and OS",
                "DaVinci step `generate` (off by default), or by hand",
                "Generated code, by DaVinci Configurator",
            ],
        ],
        [3, 3.4, 5],
    )
    story.append(h2("1.3 What version 0.1.0 can and cannot do"))
    story += table(
        ["Can do (action ADD)", "Cannot do yet (the row is rejected with a message)"],
        [
            [
                "Data types of category value, boolean, enumeration, array, record",
                "String data types; creating units; creating mode declaration groups",
            ],
            [
                "Sender-receiver and client-server interfaces",
                "Parameter, NV-data and trigger interfaces; adding an element to an existing "
                "interface",
            ],
            [
                "P, R and PR ports with init values for value, boolean and enumeration types",
                "Init values of array, record and string types (enter `none`)",
            ],
            [
                "Runnable access: implicit read/write, explicit read/send, queued receive, "
                "server call",
                "Parameter access; inter-runnable variables",
            ],
            [
                "Triggers: periodic, on data reception, on data reception error, on operation "
                "invocation, init",
                "Mode-switch triggers, background and data-send-completion triggers",
            ],
            [
                "Server runnable for a new server port",
                "Other new runnables (create them in DaVinci Developer first)",
            ],
            [
                "Assembly connectors between two components of one composition",
                "Delegation connectors and connections across nested compositions",
            ],
            [
                "Requests on DaVinci-owned SWCs are written; requests on model-owned SWCs "
                "become a package for the model team (chapter 5.5)",
                "MODIFY and DELETE. The writer rejects both in this version, and delete "
                "requests are refused unless `allow_delete` is set",
            ],
        ],
        [5, 5],
    )
    story.append(
        note(
            "info",
            "Two layers of checking.",
            "The validator judges whether a request is correct AUTOSAR and fits the project. "
            "The writer then plans the file edits. A request the validator accepts can still "
            "be refused by the writer when the writer has no template for it yet "
            "(`WRT-UNSUPPORTED-001`). Both happen in the dry run, before any file changes.",
        )
    )
    story.append(h2("1.4 What the tool never does"))
    story += bullets(
        [
            "It never uses the network. No telemetry, no update check. It reads and writes "
            "local files and starts local programs (git, and DaVinci tools when enabled).",
            "It never changes a file before all pre-checks have passed, and never applies "
            "requests that were not validated against the model as it is now.",
            "It never edits a SWC marked `locked`, a service SWC, a composition used as SWC, "
            "or a model-owned SWC (Simulink, TargetLink). Those are read-only for the tool.",
            "It never rewrites an ARXML file. It inserts text at the right place and leaves "
            "every other byte of the file as it was (line endings, indentation, encoding).",
            "It never touches a file it did not record in its journal first, and it never "
            "touches untracked files of yours.",
            "It never pushes, merges, changes git configuration or skips commit hooks. It "
            "creates one local branch with one commit. Merging is your decision.",
            "It never deletes anything in the project unless delete requests were explicitly "
            "allowed in the configuration.",
        ]
    )
    story.append(h2("1.5 Six words used all through this guide"))
    story += table(
        ["Word", "Meaning"],
        [
            ["Request", "One row of a workbook (or of the request form) with an Action."],
            [
                "Finding",
                "A message with a code (for example `VAL-NAME-001`), a severity (info, "
                "warning, error) and a text that says what to do. Findings are the normal way "
                "the tool reports problems.",
            ],
            [
                "Model",
                "What the tool has in memory after loading the workspace: SWCs, ports, "
                "interfaces, data types, runnables, events, connectors, task mappings.",
            ],
            [
                "Dry run",
                "Import plus validation plus write plan. Shows the status of every request "
                "and the files that would change. Changes nothing.",
            ],
            [
                "Apply",
                "Carries out the accepted requests of the last dry run on a new git branch.",
            ],
            [
                "Route",
                "Where an accepted request goes: `apply` (written now), `model` (handed to "
                "the model team), `hold` (waits until what it needs exists).",
            ],
        ],
        [1.4, 8.6],
    )
    return story


def chapter_2() -> list[Flowable]:
    story = h1("2. How it works")
    story.append(h2("2.1 One change cycle"))
    story.append(cycle_diagram())
    story.append(
        caption(
            "Figure 1. One cycle. Blue steps only read. The amber step is done by people. The "
            "green step changes files. The dashed step runs Vector tools and is off by default."
        )
    )
    story += table(
        ["Step", "What happens", "Result"],
        [
            [
                "1 Load",
                "Every ARXML file below the configured workspace folders and the ECUC file "
                "are parsed in parallel. Unchanged files come from a cache.",
                "The model, plus findings about files that could not be read or references "
                "that do not resolve.",
            ],
            [
                "2 Export",
                "The model is written into an Excel workbook: one sheet per kind of object, "
                "locked cells for what exists, free rows for requests.",
                "`requests.xlsx`, stamped with the git commit and a hash of the model.",
            ],
            [
                "3 Teams edit",
                "Each team copies the workbook, adds its rows, sends it back.",
                "One workbook per team.",
            ],
            [
                "4 Import",
                "All workbooks are read together. Sheets, columns, RowIDs and cell contents "
                "are checked.",
                "A list of request rows, each with its file, sheet and row number.",
            ],
            [
                "5 Validate",
                "Every row is checked against the model and the rules. Rows may depend on "
                "each other (a port on an interface created by another row). The writer "
                "plans the edits without writing.",
                "A status per request, the reasons, and the list of files that would change.",
            ],
            [
                "6 Apply",
                "New git branch, ARXML edits, reload to verify, commit. Any failure restores "
                "every file.",
                "One commit on branch `porttool/<date>-<n>`.",
            ],
            [
                "7 DaVinci steps",
                "Only those you enabled: workspace check, task-mapping script, project "
                "validation, generation.",
                "Tool logs; generated code when `generate` is enabled.",
            ],
            [
                "8 Report",
                "An HTML report and a Results sheet say what happened to every request. "
                "Requests for model-owned SWCs are packed for the model team.",
                "`porttool_reports\\<branch>\\report.html`.",
            ],
        ],
        [1.4, 4.4, 4.6],
    )
    story.append(need(95))
    story.append(h2("2.2 The parts"))
    story.append(architecture_diagram())
    story.append(
        caption(
            "Figure 2. The packages of the tool (left) and what they work with (right). GUI and "
            "command line use the same service, so both behave the same."
        )
    )
    story += table(
        ["Package", "Job", "Its codes"],
        [
            [
                "`core`",
                "Shared definitions: the model, the operations, findings, error classes, "
                "configuration loading, logging, atomic file writes.",
                "`CFG-`, `PT-E...`",
            ],
            [
                "`engine`",
                "Runs every job on one asyncio loop in a background thread, with a thread "
                "pool for blocking work. Progress, cancel, timeouts.",
                "`PT-E900`",
            ],
            [
                "`reader`",
                "Parses ARXML in parallel, resolves references, builds the model, keeps a "
                "cache.",
                "`RDR-`",
            ],
            [
                "`workbook`",
                "Writes the Excel workbook and reads workbooks back.",
                "`WBK-`",
            ],
            [
                "`validator`",
                "Judges every request, decides its route, keeps the pending queue.",
                "`VAL-`",
            ],
            [
                "`writer`",
                "Plans and makes the ARXML edits by inserting text; builds delta ARXML.",
                "`WRT-`",
            ],
            [
                "`davinci`",
                "Starts the Vector command-line tools from templates, reads their output, "
                "enforces timeouts, detects license problems and an open DaVinci GUI.",
                "`DVR-`",
            ],
            [
                "`pipeline`",
                "The service behind GUI and command line. Owns the apply transaction: "
                "pre-checks, branch, journal, verify, commit, rollback, report.",
                "`PIP-`",
            ],
            [
                "`gui`",
                "A small web server on this computer and the page it serves.",
                "HTTP error codes",
            ],
            [
                "`discovery`",
                "`porttool discover`: studies a real repository without changing it.",
                "`DISC-`",
            ],
        ],
        [1.4, 6.8, 1.8],
    )
    story.append(
        note(
            "info",
            "The code prefix tells you where to look.",
            "Every finding starts with the prefix of the package that produced it. When "
            "something goes wrong, the prefix names the package, and that package's notes "
            "(`porttool\\<package>\\HANDOFF.md`) list the code, the cause and the test that "
            "covers it.",
        )
    )
    story.append(h2("2.3 Who owns a SWC, and the three routes"))
    story.append(
        p(
            "DaVinci Developer is not the source of truth for every SWC. A SWC whose ARXML is "
            "exported from Simulink or TargetLink would be overwritten at the next model "
            "export if the tool edited it. So every SWC has an owner, set in `owners.csv` "
            "(chapter 9.2), and the owner decides the route of a request."
        )
    )
    story += table(
        ["Owner of the SWC", "Route", "What happens", "Status shown"],
        [
            [
                "`davinci` (default)",
                "`apply`",
                "The ARXML is edited now.",
                "accepted, then applied",
            ],
            [
                "`model`",
                "`model`",
                "Nothing is written to the SWC. The request goes into a package for the "
                "model team (`requests.csv` and `delta.arxml`) and into the pending queue. "
                "It closes by itself when a later load finds the object in the repository.",
                "waiting for model owner",
            ],
            [
                "any, for a connector whose port does not exist yet",
                "`hold`",
                "The connector waits in the pending queue. It is released and written when "
                "both ports exist.",
                "on hold",
            ],
            [
                "`locked`, service SWC, composition",
                "none",
                "The request is rejected (`VAL-OWN-001` to `-003`).",
                "rejected",
            ],
        ],
        [2.2, 0.9, 5.4, 2],
    )
    story.append(
        p(
            "Connectors live in the composition, not in the SWC. A connector to a port of a "
            "model-owned or locked SWC is therefore written normally, as long as the "
            "composition itself is DaVinci-owned."
        )
    )
    story.append(h2("2.4 The safety net"))
    story += bullets(
        [
            "**Dry run first.** Apply only carries out the last dry run, and only if the model "
            "has not changed since.",
            "**Pre-checks.** Before the first change: no other PortTool on this repository, no "
            "unfinished earlier apply, clean git working tree, no merge or rebase in progress, "
            "git identity set, enough disk space, DaVinci Developer and Configurator closed.",
            "**A new branch.** Changes are made on `porttool/<yyyymmdd>-<n>`, never on your "
            "branch.",
            "**A journal.** Before a file is changed, its path is written to a journal file "
            "outside the repository and flushed to disk. After a crash or a power loss the "
            "journal alone is enough to restore the repository (`porttool rollback`).",
            "**Atomic writes.** A file is written to a temporary file and then moved in place, "
            "so a file is never half written. The new content is checked for well-formed XML "
            "(and against the AUTOSAR XSD when one is configured) before it replaces the old.",
            "**Verification.** After writing, the workspace is loaded again. Every requested "
            "object must exist as requested, nothing that existed may be gone, and no file "
            "outside the journal may have changed.",
            "**Rollback.** Until the commit exists, any failure or a cancel restores every "
            "journaled file, returns to your branch and deletes the new branch.",
        ]
    )
    story.append(h2("2.5 Speed, threads and why the window does not freeze"))
    story.append(
        p(
            "All work runs as jobs on one asyncio event loop in a background thread. Blocking "
            "work (parsing XML, reading and writing Excel, git) is handed to a thread pool; the "
            "DaVinci tools run as child processes that the loop watches. The GUI and the "
            "command line only start jobs and poll their progress, so the page stays "
            "responsive and every job can be cancelled. Only one job that changes the "
            "repository runs at a time."
        )
    )
    story += table(
        ["Operation (build machine, 2 shared cores)", "Measured", "Budget"],
        [
            ["First load, 2,000 files, 556 MB", "6.1 s", "30 s"],
            ["Load with warm cache", "0.5 s", "3 s"],
            ["Excel export / import, 57,700 rows", "3.1 s / 3.4 s", "10 s / 5 s"],
            ["Validation of 500 requests", "16 ms", "2 s"],
            ["Writing 200 operations into 100 files", "1.1 s", "5 s"],
            ["GUI table page, 20,000 ports", "1 to 5 ms", "200 ms"],
        ],
        [6, 2, 2],
    )
    story.append(
        p(
            "These numbers come from generated repositories on Linux. Expect different values "
            "on a Windows laptop with a virus scanner; the first load after a `git pull` that "
            "touched many files is the slowest step."
        )
    )
    return story


def chapter_3() -> list[Flowable]:
    story = h1("3. Installing on Windows")
    story.append(h2("3.1 What you need"))
    story += bullets(
        [
            "Windows 10 or 11, 64-bit.",
            "Python 3.11 or newer, 64-bit, from python.org. Tick **Add python.exe to PATH** in "
            "the installer.",
            "Git for Windows, with `git` on PATH.",
            "Internet access to pypi.org once, for the installation. The tool itself never "
            "uses the network.",
            "Microsoft Excel for the workbooks. The GUI works without Excel.",
            "For the DaVinci steps only: DaVinci Developer Classic, DaVinci Configurator 5 "
            "from the SIP, and their licenses.",
        ]
    )
    story.append(h2("3.2 Install"))
    story += steps(
        [
            "Unzip `PortTool-0.1.0.zip` to a folder with a short path without spaces, for "
            "example `C:\\Tools\\PortTool`. (Or clone the GitHub repository there.)",
            "Double-click `install.bat`. It creates a private Python environment in `.venv` "
            "inside that folder and installs the libraries: lxml, openpyxl, XlsxWriter, "
            "pydantic, click, psutil, and pytest for the self-check.",
            "Double-click `selfcheck.bat` (2 to 10 minutes). It works on a generated test "
            "repository in a temporary folder, never on your project. It ends with "
            "`SELF-CHECK PASSED` or with a list of problems in `selfcheck_log.txt`.",
        ]
    )
    story.append(h2("3.3 What is in the folder"))
    story += table(
        ["File or folder", "Purpose"],
        [
            ["`install.bat`", "Creates `.venv` and installs the libraries. Run once."],
            [
                "`selfcheck.bat`",
                "Runs the core tests on this machine and writes `selfcheck_log.txt`.",
            ],
            [
                "`porttool.bat`",
                "The command line. `porttool <command> ...` from a command prompt opened in "
                "this folder (or add the folder to PATH).",
            ],
            [
                "`PortTool-GUI.bat`",
                "Starts the page in your browser. Pass the repository folder: "
                "`PortTool-GUI.bat C:\\path\\to\\ecu_repo`.",
            ],
            ["`porttool\\`", "The source code, one sub-folder per package."],
            ["`tests\\`", "The automated tests and the generator of test repositories."],
            [
                "`docs\\`",
                "`docs\\user\\FIRST_RUN.md` (first-run checklist), `docs\\STATUS.md` (what is "
                "verified), this guide.",
            ],
            ["`requirements.txt`", "The libraries `install.bat` installs."],
        ],
        [2.4, 7.6],
    )
    story.append(h2("3.4 Where the tool keeps its own files"))
    story += table(
        ["What", "Where", "Safe to delete?"],
        [
            [
                "Log file",
                "`%LOCALAPPDATA%\\PortTool\\logs\\porttool.log` (5 MB, five older files kept)",
                "Yes",
            ],
            [
                "Logs of DaVinci tool runs",
                "`%LOCALAPPDATA%\\PortTool\\logs\\tools\\` (the last 200)",
                "Yes",
            ],
            [
                "Cache of parsed files",
                "`%LOCALAPPDATA%\\PortTool\\cache\\<repo id>\\` (SQLite)",
                "Yes. The next load is slow once.",
            ],
            [
                "Journal of applies",
                "`%LOCALAPPDATA%\\PortTool\\journals\\<repo id>\\`",
                "**Not** while `apply.journal.jsonl` exists there: it is needed for rollback.",
            ],
            [
                "Lock files",
                "`%LOCALAPPDATA%\\PortTool\\locks\\`",
                "Only when no PortTool is running.",
            ],
            [
                "Project configuration",
                "`porttool.toml`, `owners.csv`, `locked.txt` in the root of the ECU repository",
                "No. Commit them.",
            ],
            [
                "Pending queue",
                "`porttool\\pending.json` in the ECU repository",
                "No. It is committed with each apply.",
            ],
            [
                "Reports",
                "`porttool_reports\\<branch>\\` in the ECU repository. Add `porttool_reports/` "
                "to `.gitignore`.",
                "Yes",
            ],
        ],
        [2, 5.6, 2.6],
    )
    story.append(
        p(
            "`<repo id>` is the repository folder name plus a short hash of its full path, for "
            "example `ecu-ef46f753cbfb18d7`. `porttool status` prints the journal folder."
        )
    )
    story.append(h2("3.5 Updating and removing"))
    story += bullets(
        [
            "**Update:** unzip the new version into a new folder and run `install.bat` there. "
            "The project configuration lives in the ECU repository, so nothing is lost.",
            "**Remove:** delete the PortTool folder and `%LOCALAPPDATA%\\PortTool`. Nothing is "
            "written to the registry.",
        ]
    )
    return story


def chapter_4() -> list[Flowable]:
    story = h1("4. First run on a real project")
    story.append(
        p(
            "The tool has never seen a real Vector workspace. The first run is therefore a "
            "careful, mostly read-only sequence that finds the gaps before anything is "
            "generated from tool-written ARXML. Do all of it on a fresh clone of the ECU "
            "repository, not on your working copy."
        )
    )
    story.append(
        note(
            "warn",
            "Check your agreements first.",
            "The discover report describes your repository (names of SWCs, ports, files). "
            "Before any report leaves the company, check the NDA with the OEM and with Vector, "
            "and use `--anonymize`.",
        )
    )
    story.append(h2("4.1 The sequence"))
    story += table(
        ["#", "Step", "Command", "Changes the repository?"],
        [
            ["1", "Install and self-check", "`install.bat`, `selfcheck.bat`", "No"],
            [
                "2",
                "Discover",
                "`porttool discover run --repo C:\\ecu --sip C:\\SIP`",
                "No. Writes a report to `porttool_discovery\\`.",
            ],
            ["3", "Create the configuration", "`porttool init --repo C:\\ecu`", "Adds `porttool.toml`"],
            ["4", "Load", "`porttool load --repo C:\\ecu`", "No"],
            ["5", "Look at it", "`PortTool-GUI.bat C:\\ecu`", "No"],
            [
                "6",
                "Export and open in Excel",
                "`porttool export --out C:\\temp\\requests.xlsx --repo C:\\ecu`",
                "No",
            ],
            [
                "7",
                "One simple request, dry run",
                "`porttool dry-run C:\\temp\\requests.xlsx --repo C:\\ecu`",
                "No",
            ],
            [
                "8",
                "First apply",
                "`porttool apply C:\\temp\\requests.xlsx --repo C:\\ecu`",
                "Yes: one commit on a new branch",
            ],
            [
                "9",
                "Open the workspace in DaVinci Developer, save, look at `git diff`",
                "by hand",
                "Shows what Developer rewrites",
            ],
            ["10", "Generate RTE and OS in Configurator", "by hand", "As today"],
        ],
        [0.5, 3.2, 4.6, 2.6],
    )
    story.append(h2("4.2 What each step tells you"))
    story.append(h3("Step 2: discover"))
    story.append(
        p(
            "`discover` reads the repository and starts the Vector command-line tools with "
            "their help flag only. It never passes them your project. Add `--no-tools` to skip "
            "the tools completely. The report folder holds `discovery.json`, `discovery.md`, a "
            "`cli` folder with the help texts, a proposed `owners_proposed.csv`, and a "
            "`README.txt` that says which files to send back. The help texts are what is "
            "needed to correct the DaVinci command lines (chapter 9.4)."
        )
    )
    story.append(h3("Steps 3 to 5: configuration and load"))
    story.append(
        p(
            "`init` finds the `.dpa` file, the workspace and the ECUC file and writes "
            "`porttool.toml`. Open it and check the paths. `load` prints the counts and the "
            "findings. Compare the number of SWCs, ports and connectors with what you know, "
            "and compare the Unconnected tab of the GUI with the unconnected ports DaVinci "
            "Developer shows. A difference here means the reader misreads something in your "
            "workspace; stop and report it (chapter 10.9)."
        )
    )
    story.append(h3("Step 6: the workbook in real Excel"))
    story += bullets(
        [
            "Dropdowns open and hold the right values.",
            "Only Action, Requester, Comment and the blank rows can be edited.",
            "Autofilter and frozen header work; names such as `007` or `MAR1` stay text.",
        ]
    )
    story.append(h3("Step 9: the most important check"))
    story.append(
        p(
            "The XML the writer produces follows the AUTOSAR 4.x schema, but it was never "
            "compared with a file saved by DaVinci Developer. Open the workspace in Developer. "
            "It must open without errors and show the new port. Then save in Developer and run "
            "`git diff`. If Developer rewrites what PortTool wrote (other element order, other "
            "attributes, added defaults), that diff is exactly the information needed to "
            "correct the writer. Send it back."
        )
    )
    story.append(h3("Optional, and very valuable: golden diffs"))
    story.append(
        p(
            "On a branch, make each of these by hand in DaVinci Developer and commit each one "
            "separately: a data type, a port interface, a port, an implicit access, an "
            "explicit access, a trigger, a server port with its runnable, a connector. Then "
            "for each commit:"
        )
    )
    story.append(
        code(
            "porttool discover golden-diff --repo C:\\ecu --base <commit before> --head <commit after>"
        )
    )
    story.append(
        p(
            "Each report shows, element by element, what Developer writes for that action. "
            "That is the reference the writer has to reproduce."
        )
    )
    story.append(h2("4.3 What to send back after the first run"))
    story += bullets(
        [
            "`selfcheck_log.txt` if the self-check found problems.",
            "From `porttool_discovery\\`: `discovery.json`, `discovery.md`, the `cli` folder, "
            "and the golden-diff reports.",
            "The `git diff` after saving in DaVinci Developer (step 9).",
            "Anything odd in Excel, with a screenshot.",
        ]
    )
    return story


def chapter_5() -> list[Flowable]:
    story = h1("5. Daily use")
    story.append(h2("5.1 Who does what"))
    story += table(
        ["Role", "Does", "Needs"],
        [
            [
                "Team engineer",
                "Opens the shared workbook, adds request rows for the team, saves, sends it "
                "back.",
                "Excel only. No PortTool, no DaVinci, no repository.",
            ],
            [
                "Integrator",
                "Exports the workbook, collects the team files, runs dry run and apply, "
                "checks the branch, maps tasks, generates, merges, sends the results back.",
                "PortTool, the repository, git, DaVinci tools.",
            ],
            [
                "Model team",
                "Receives a request package for its model-owned SWC, changes the model, "
                "exports the ARXML.",
                "Simulink or TargetLink.",
            ],
        ],
        [1.6, 5.2, 3.2],
    )
    story.append(h2("5.2 The cycle, step by step"))
    story.append(
        p(
            "Every step exists on the command line and in the GUI (Requests tab, chapter 7). "
            "The examples use the command line because it is easier to quote. Open a command "
            "prompt in the PortTool folder, or add that folder to PATH. `--repo` can be left "
            "out when the command prompt is inside the ECU repository."
        )
    )
    story += steps(
        [
            "**Update the repository.** `git pull` on the integration branch. Close DaVinci "
            "Developer and Configurator.",
            "**Export.** `porttool export --out \\\\share\\ports\\requests.xlsx --repo C:\\ecu`. "
            "The workbook is stamped with the current commit.",
            "**Share.** Each team works on its own copy (`requests_teamA.xlsx`, ...). Chapter 6 "
            "explains how to fill it.",
            "**Dry run.** `porttool dry-run requests_teamA.xlsx requests_teamB.xlsx --repo "
            "C:\\ecu`. All files are judged together, so a port of team A can use an interface "
            "that team B requests in the same round.",
            "**Read the result.** Send rejected rows back to their requester with the reason. "
            "Rejected rows do not block the accepted ones.",
            "**Apply.** `porttool apply requests_teamA.xlsx requests_teamB.xlsx --repo C:\\ecu`. "
            "It repeats the dry run, asks for confirmation (`--yes` skips the question) and "
            "applies.",
            "**Check and finish** (chapter 5.4): open the workspace in DaVinci Developer, map "
            "new events to tasks, generate, build, then merge the branch.",
        ]
    )
    story.append(need(70))
    story.append(h2("5.3 A real example"))
    story.append(
        p(
            "This is the output of a run on a small generated repository; long lines are "
            "shortened here with `...`. The workbook holds three new rows: a new R-port on "
            "`Swc1`, a connector from `Swc3.PpSig1` to that port, and a port with an invalid "
            "name."
        )
    )
    story.append(
        code(
            r"""
> porttool dry-run requests_edit.xlsx --repo C:\ecu
Dry run of 1 workbook(s):
STATUS    REQUEST                               DETAIL
accepted  requests_edit.xlsx Ports row 14       add port /ComponentTypes/Swc1/RpDemoSpeed (R, ...IfSig1)
rejected  requests_edit.xlsx Ports row 15       VAL-NAME-001: Port name 'bad name' is not a valid
                                                AUTOSAR short name: it must start with a letter and
                                                contain only letters, digits and underscores.
accepted  requests_edit.xlsx Connections row 7  add connector .../Swc3_PpSig1_Swc1_RpDemoSpeed
                                                (Swc3.PpSig1 -> Swc1.RpDemoSpeed)
Total: 2 accepted, 1 rejected
Plan: 2 file(s) would change:
  modify  Config/Developer/ComponentTypes/EcuComposition.arxml  (add connector Swc3_PpSig1_Swc1_...)
  modify  Config/Developer/ComponentTypes/Swc1.arxml  (add port RpDemoSpeed)
Nothing was changed.
"""
        )
    )
    story.append(
        code(
            r"""
> porttool apply requests_edit.xlsx --repo C:\ecu --yes
  Branch porttool/20261112-1 created from master
  Writing 2 workspace file(s)
  check: skipped (not enabled)
  script: skipped (not enabled)
  validate: skipped (not enabled)
  generate: skipped (not enabled)
  Reloading the workspace to verify the result
  Writing request packages for model owners
  Writing the report
  Committed d6f4bb923d46 on porttool/20261112-1
  Closing the journal
STATUS    REQUEST                               DETAIL
applied   requests_edit.xlsx Ports row 14       add port /ComponentTypes/Swc1/RpDemoSpeed (R, ...IfSig1)
rejected  requests_edit.xlsx Ports row 15       VAL-NAME-001: Port name 'bad name' is not a valid ...
applied   requests_edit.xlsx Connections row 7  add connector .../Swc3_PpSig1_Swc1_RpDemoSpeed ...
Total: 2 applied, 1 rejected
WARNING PIP-APPLY-007: DaVinci steps skipped: check, script, validate, generate. The ARXML was
        edited and verified by reloading it only. ...
Applied on branch: porttool/20261112-1
Report: C:\ecu\porttool_reports\porttool\20261112-1\report.html
"""
        )
    )
    story.append(p("What the writer inserted into `Swc1.arxml` for the new port (from `git show`):"))
    story.append(
        code(
            """
<R-PORT-PROTOTYPE UUID="767c1d5a-f18d-42b7-b3fd-616e18979e1a">
  <SHORT-NAME>RpDemoSpeed</SHORT-NAME>
  <REQUIRED-COM-SPECS>
    <NONQUEUED-RECEIVER-COM-SPEC>
      <DATA-ELEMENT-REF DEST="VARIABLE-DATA-PROTOTYPE">/PortInterfaces/IfSig1/De1</DATA-ELEMENT-REF>
      <ALIVE-TIMEOUT>0</ALIVE-TIMEOUT>
      <ENABLE-UPDATE>false</ENABLE-UPDATE>
      <HANDLE-NEVER-RECEIVED>false</HANDLE-NEVER-RECEIVED>
      <HANDLE-TIMEOUT-TYPE>NONE</HANDLE-TIMEOUT-TYPE>
    </NONQUEUED-RECEIVER-COM-SPEC>
  </REQUIRED-COM-SPECS>
  <REQUIRED-INTERFACE-TREF DEST="SENDER-RECEIVER-INTERFACE">/PortInterfaces/IfSig1
  </REQUIRED-INTERFACE-TREF>      (one line in the file; wrapped here)
</R-PORT-PROTOTYPE>
"""
        )
    )
    story.append(
        p(
            "The commit message lists every request with its requester and source row, so "
            "`git log` answers who asked for what (each request is one line in the real "
            "message):"
        )
    )
    story.append(
        code(
            """
PortTool: 2 request(s) on porttool/20261112-1

- add port /ComponentTypes/Swc1/RpDemoSpeed (R, /PortInterfaces/IfSig1)
  [demo, requests_edit.xlsx Ports row 14]
- add connector /ComponentTypes/EcuComposition/Swc3_PpSig1_Swc1_RpDemoSpeed
  (Swc3.PpSig1 -> Swc1.RpDemoSpeed) [demo, requests_edit.xlsx Connections row 7]

Base: f08454f21435 (master)
"""
        )
    )
    story.append(h2("5.4 After the apply"))
    story.append(apply_diagram())
    story.append(caption("Figure 3. The steps of one apply, in the order they run."))
    story.append(
        p("With the default settings the apply stops after the commit. Then, on the new branch:")
    )
    story += steps(
        [
            "**Read the report** (`report.html`). Its sections: summary, requests, created "
            "runnables, triggers that need a task mapping, findings, request packages for "
            "model owners, step timings, tool log excerpts, `git diff --stat`, changed files.",
            "**Open the workspace in DaVinci Developer.** It must load without errors. Check "
            "the new objects. For a new server runnable, generate or write its implementation "
            "as usual.",
            "**Map new events to tasks in DaVinci Configurator.** The writer creates the RTE "
            "event but not its task mapping. The report section \"Triggers that need a task "
            "mapping\" lists each new event with the task and position the request asked for, "
            "as step-by-step instructions (`PIP-DV-007`).",
            "**Validate and generate** RTE and OS in Configurator, then build.",
            "**Merge** the branch `porttool/<date>-<n>` into your integration branch the way "
            "your project does it (pull request or `git merge`). Delete the branch afterwards.",
            "**Send results back.** The report lists every request with its status and "
            "reason. In the GUI, a workbook exported after the dry run or the apply in the "
            "same session also carries them on its `Results` sheet.",
        ]
    )
    story.append(
        note(
            "warn",
            "Do not leave branches unmerged for long.",
            "The next dry run is made against the branch you have checked out. If the last "
            "PortTool branch is not merged, the next export does not contain its changes, and "
            "teams will request the same things again.",
        )
    )
    story.append(h2("5.5 Model-owned SWCs and the pending queue"))
    story.append(
        p(
            "A request that touches a model-owned SWC is correct but cannot be written by the "
            "tool. It gets the status **waiting for model owner** and two things happen:"
        )
    )
    story += bullets(
        [
            "A **request package** is written to "
            "`porttool_reports\\<branch>\\model_requests\\<team>\\`: `requests.csv` (opens in "
            "Excel) lists what the team has to add to its model, and `delta.arxml` holds the "
            "same elements as ARXML for import into the modelling tool. Connectors that wait "
            "for such a port are listed in `held_requests.csv`.",
            "The request is stored in the **pending queue** (`porttool\\pending.json`, "
            "committed with the apply).",
        ]
    )
    story.append(
        p(
            "At every later dry run the queue is reviewed first. When the model team has "
            "delivered and the object is in the repository as requested, the entry is closed "
            "(`VAL-PEND-001`). A connector that was **on hold** because one of its ports was "
            "missing is released (`VAL-PEND-003`) and written by that apply. If the delivered "
            "object differs from the request (other interface, other direction), the entry is "
            "kept and reported (`VAL-PEND-002`). Entries older than `[pending] max_age_days` "
            "(default 14) are reported at every run (`VAL-PEND-004`)."
        )
    )
    story.append(p("The GUI shows the queue on its Pending tab. `porttool status` prints its size."))
    story.append(h2("5.6 Undo"))
    story += table(
        ["Situation", "What to do"],
        [
            [
                "The apply failed or was cancelled",
                "Nothing. It already restored every file and returned to your branch. The "
                "report of the failed run is outside the repository; its path is printed.",
            ],
            [
                "The tool or the laptop died during an apply",
                "`porttool status` prints `RECOVERY NEEDED: an apply did not finish`. Run "
                "`porttool rollback`. Until then load, export, dry run and apply are refused.",
            ],
            [
                "The apply succeeded but you do not want it",
                "It is one commit on its own branch. Switch back (`git checkout <your "
                "branch>`) and delete the branch (`git branch -D porttool/<date>-<n>`).",
            ],
            [
                "You already merged it",
                "`git revert <merge or commit>`, as for any other change.",
            ],
        ],
        [3.2, 6.8],
    )
    return story

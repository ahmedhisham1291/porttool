# PortTool: first run on the Windows laptop

PortTool 0.1.0 was built and tested on Linux against a generated test repository and
fake DaVinci tools. It has never run on Windows, never seen your ECU repository, and
never started a real Vector tool. This checklist finds the gaps safely and tells you
what to send back.

Work on a COPY of the ECU repository (a fresh clone) for the first runs.

## 1. Install (once)

You need 64-bit Python 3.11 or newer (python.org, tick "Add python.exe to PATH"), Git
for Windows, and internet access to pypi.org for the installation.

1. Unzip `PortTool` to a folder with a short path without spaces, e.g. `C:\Tools\PortTool`.
2. Double-click `install.bat`. It creates `.venv` inside that folder and installs
   lxml, openpyxl, XlsxWriter, pydantic, click, psutil and pytest.

## 2. Self-check (5 minutes, does not touch your project)

Double-click `selfcheck.bat`. It generates a small test repository in a temporary folder
and runs load, Excel export and import, validation, apply, rollback and the DaVinci
steps with fake tools.

- `SELF-CHECK PASSED`: continue.
- Problems found: this is the first run on Windows, so some are likely. Send
  `selfcheck_log.txt` back. You can still continue with steps 3 and 4.

## 3. Discover (read-only; this is what unblocks the DaVinci steps)

Open a command prompt in the PortTool folder:

    porttool discover run --repo C:\path\to\ecu_repo --sip C:\path\to\SIP

- It reads the repository and starts the Vector command-line tools ONLY with help flags.
  It never passes them your project. Add `--no-tools` to skip that part.
- Add `--anonymize` to replace every SWC, port, file and folder name by a token before
  the report leaves your company. Check your NDA with the OEM and Vector first.
- The report is written to `<repo>\porttool_discovery`. Read `README.txt` there: it says
  what the files contain and which to send back.

Send back: `discovery.json`, `discovery.md` and the `cli` folder.

Optional but very valuable, "golden diffs": in DaVinci Developer, on a branch, make each
of these by hand and commit each one separately: a data type, a port interface, a port,
an implicit access, an explicit access, a trigger, a server port with its server
runnable, a connector. Then run for each commit:

    porttool discover golden-diff --repo C:\path\to\ecu_repo --base <commit before> --head <commit after>

and send those reports too. They show exactly what DaVinci Developer writes, which is
what the PortTool writer must reproduce.

## 4. Configure and look at your project (read-only)

    porttool init --repo C:\path\to\ecu_repo        (writes porttool.toml; check the paths in it)
    porttool load --repo C:\path\to\ecu_repo        (prints counts and any findings)
    PortTool-GUI.bat C:\path\to\ecu_repo            (opens the page in your browser)

Check in the GUI that the numbers of SWCs, ports and connections look right, and that the
Unconnected tab matches what you know. Then on the Requests tab export a workbook, open
it in Excel and check: dropdowns work, only Action / Requester / Comment and the blank
rows can be edited, autofilter works. Report anything odd.

`owners.csv` next to `porttool.toml` marks model-owned SWCs (Simulink, TargetLink):
columns `SWC,team,arxml_owner,authoring_tool,export_folder`, with `arxml_owner` = `model`
for those. `discover` proposes one in `owners_proposed.csv`.

## 5. First apply (on the copy)

1. Enter one simple request in the workbook, e.g. a new R-port on a DaVinci-owned SWC.
2. `porttool dry-run requests.xlsx --repo ...` or "Validate" in the GUI. Read the plan.
3. `porttool apply requests.xlsx --repo ...` or "Apply" in the GUI.

What apply does by default: it creates a git branch `porttool/<date>-<n>`, edits the
workspace ARXML files, reloads to verify, commits, and stops. It does NOT start any
DaVinci tool. A failure rolls the files back.

4. Open the workspace in DaVinci Developer and check that it opens without errors and
   shows the new port. Save in Developer and look at `git diff`: if Developer rewrites
   what PortTool wrote, send that diff back. This is the most important check of all.
5. Generate RTE and OS in DaVinci Configurator by hand, as you do today.

## 6. Switching on the DaVinci steps (only after step 3 was sent back and answered)

    porttool davinci doctor --repo ...           (says what is missing; starts no tool)
    porttool davinci show-commands --repo ...    (prints the command lines; runs nothing)

Every built-in command line is a guess labelled UNVERIFIED. Correct them in
`porttool.toml` under `[davinci.commands]` using the help output from step 3, then enable
steps one at a time:

    [pipeline]
    davinci_steps = ["check"]        # later: "check", "validate", "generate"

The "script" step (automatic task mapping of new triggers) uses a Groovy script that has
never been executed. Leave it off. PortTool prints step-by-step instructions for mapping
new triggers by hand in Configurator instead.

## If something goes wrong

- `porttool status --repo ...` shows the state; `porttool rollback --repo ...` undoes an
  apply that did not finish.
- Logs: `%LOCALAPPDATA%\PortTool\logs`. Messages carry a code such as `[PT-E410]`.
- Send back: the message, the log file, and what you did.

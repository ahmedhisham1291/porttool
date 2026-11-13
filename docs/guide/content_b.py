"""Engineer guide, part B: chapters 6 to 9 (workbook, GUI, commands, configuration)."""

from __future__ import annotations

from pathlib import Path

from reportlab.platypus import Flowable

from guide_lib import bullets, code, h1, h2, h3, need, note, p, picture, steps, table

SCREENSHOTS = Path(__file__).resolve().parents[2] / "porttool" / "gui" / "docs"


def chapter_6() -> list[Flowable]:
    story = h1("6. The workbook")
    story.append(
        p(
            "The workbook is a request form. It shows what exists in the project and lets a "
            "team ask for changes. Nothing changes in the project until the integrator has "
            "imported the file and every request has passed validation."
        )
    )
    story.append(h2("6.1 The sheets"))
    story += table(
        ["Sheet", "Shows", "Requests?"],
        [
            ["`README`", "Short instructions, the baseline commit and the legend.", "No"],
            [
                "`Components`",
                "Every SWC: kind, package path, source file, owner team, ARXML owner.",
                "No",
            ],
            ["`DataTypes`", "Data types with limits, unit, scaling, enumeration values.", "Yes"],
            ["`Interfaces`", "Port interfaces and their kind.", "Yes"],
            [
                "`InterfaceElements`",
                "Data elements of each interface; for client-server interfaces one row per "
                "argument of each operation.",
                "Yes",
            ],
            [
                "`Ports`",
                "Ports of each SWC type: direction, interface, connected or not, number of "
                "partners, init value.",
                "Yes",
            ],
            [
                "`RunnableAccess`",
                "Which runnable reads, writes or calls which port element, and how.",
                "Yes",
            ],
            ["`RunnableTriggers`", "What starts each runnable, and the task it runs in.", "Yes"],
            ["`Connections`", "Connectors between component prototypes of a composition.", "Yes"],
            [
                "`Unconnected`",
                "Port instances without a connector, each with the ports it could be "
                "connected to (same composition, same interface, opposite direction).",
                "No",
            ],
            ["`Results`", "Status and reason of every request of the last import.", "No"],
            ["`Meta`", "Tool version, layout version, AUTOSAR schema, commit, model hash.", "No"],
            ["`Lists` (hidden)", "The values behind the dropdowns.", "No"],
        ],
        [2, 6.6, 1.2],
    )
    story.append(h2("6.2 The rules"))
    story += table(
        ["Rule", "If you break it"],
        [
            [
                "Existing rows are locked. On an existing row you can only fill **Action**, "
                "**Requester** and **Comment**.",
                "Excel refuses the edit (the sheets are protected, without a password).",
            ],
            [
                "New things go into the coloured blank rows below the data (500 per sheet). "
                "Leave **RowID** empty and set Action to `ADD`.",
                "A new row with content but no Action is reported (`WBK-ROW-001`). A typed or "
                "pasted RowID is rejected (`WBK-ROW-003`, `-004`).",
            ],
            [
                "Do not rename, move, add or delete sheets or columns.",
                "A renamed sheet is not imported (`WBK-SHEET-003`); a missing column skips "
                "the sheet (`WBK-COLUMN-001`).",
            ],
            [
                "No formulas, no merged cells. Type plain values.",
                "`WBK-CELL-002`, `-003` (formula), `WBK-CELL-004` (merged cells).",
            ],
            [
                "Type names exactly as the project writes them. Upper and lower case matter.",
                "The object is not found (`VAL-REF-...`), or the name clashes by case "
                "(`VAL-NAME-006`).",
            ],
            [
                "Save as Excel Workbook (`.xlsx`), without a password.",
                "`.xls`, `.xlsm`, `.csv` are refused (`WBK-FILE-003`), protected files too "
                "(`WBK-FILE-005`).",
            ],
            [
                "Close the file in Excel before handing it back.",
                "The import retries three times, then reports `WBK-FILE-004`.",
            ],
        ],
        [5.4, 4.6],
    )
    story += bullets(
        [
            "Filtering, sorting by filter and hiding rows are fine: hidden rows are still "
            "imported.",
            "A dropdown filled from the project (SWCs, interfaces, data types, tasks) accepts "
            "a name that is not in the list, because another row of the same round may create "
            "it. Closed lists (Action, Direction, kinds, Y/N) accept only their values.",
            "When two objects in the project share a short name, the cell shows the full path "
            "starting with `/`. You may type a full path in the same way.",
            "Numbers use a dot as decimal separator. Hexadecimal whole numbers (`0xFF`) are "
            "accepted.",
        ]
    )
    story.append(h2("6.3 How to request each thing"))
    story.append(
        p(
            "Every recipe below uses a blank row at the bottom of the named sheet, with Action "
            "`ADD` and your name in Requester. Columns not mentioned stay empty."
        )
    )

    story.append(h3("A. Connect two existing ports"))
    story += steps(
        [
            "On `Unconnected`, find the port. The column **Compatible partner ports** lists up "
            "to ten candidates as `Prototype.Port`.",
            "On `Connections`, fill **Provider SWC** and **Provider port** (the P or PR side), "
            "**Requester SWC** and **Requester port** (the R or PR side).",
        ]
    )
    story += table(
        ["Provider SWC", "Provider port", "Requester SWC", "Requester port", "Composition", "Action"],
        [["Swc3", "PpSig1", "Swc1", "RpDemoSpeed", "", "ADD"]],
        [2, 2, 2, 2.2, 1.8, 1],
    )
    story += bullets(
        [
            "The SWC cells here hold the name of the **component prototype** (the instance "
            "inside the composition), not the component type.",
            "**Composition** may stay empty when exactly one composition contains both "
            "components. **Connector name** may stay empty: the default is "
            "`<provider>_<provider port>_<requester>_<requester port>` (setting `[naming] "
            "connector_pattern`).",
            "Checked: directions, interface compatibility (same interface, or same kind and "
            "everything the requester needs exists on the provider with the same type), and "
            "multiplicity (no n:m sender-receiver, one server per client).",
        ]
    )

    story.append(need(60))
    story.append(h3("B. New port on an existing interface"))
    story += table(
        ["SWC", "Port", "Direction", "Interface", "Init value", "Action"],
        [
            ["Swc1", "RpVehicleSpeed", "R", "IfVehicleSpeed", "Speed=0", "ADD"],
            ["Swc1", "PpStatus", "P", "IfStatus", "State=1;Valid=0", "ADD"],
            ["Swc2", "RpDiagService", "R", "IfDiagService", "", "ADD"],
        ],
        [1.4, 2.4, 1.3, 2.4, 2.4, 1],
    )
    story += bullets(
        [
            "**SWC** is the component type. **Direction** is `P`, `R` or `PR` (PR only with "
            "sender-receiver style interfaces).",
            "**Init value** is required for every non-queued data element of a "
            "sender-receiver interface: `Element=value` pairs separated by semicolons. For an "
            "interface with one element the bare value is enough. Type `none` to leave the "
            "port without init value on purpose. Queued elements and client-server ports take "
            "no init value.",
            "The value must fit the data type: inside its range, or one of the enumeration "
            "names. For array and record types only `none` is possible in this version.",
            "Leave **Interface kind**, **Connected** and **Partners** empty; they are filled "
            "by the export.",
        ]
    )

    story.append(need(70))
    story.append(h3("C. New data type"))
    story.append(
        p(
            "Sheet `DataTypes`. **Data type** (the name), **Level** (`application` or "
            "`implementation`) and **Category** are always required. The category decides "
            "which other cells apply; a filled cell that does not apply is rejected "
            "(`VAL-DT-011`)."
        )
    )
    story += table(
        ["Category", "Cells that apply", "Example"],
        [
            [
                "`value`",
                "Base type, Lower limit, Upper limit, Unit, Factor, Offset",
                "Base type `uint16`, limits `0` and `250`, Unit `km_h`, Factor `0.01`",
            ],
            ["`boolean`", "Base type, Lower limit, Upper limit", "Base type `boolean`"],
            [
                "`enumeration`",
                "Base type, Enumeration values, limits",
                "Enumeration values `0=OFF;1=ON;2=ERROR`",
            ],
            [
                "`array`",
                "Array element type, Array size",
                "Element type `uint8`, size `8`",
            ],
            [
                "`record`",
                "Record elements",
                "`speed:uint16;valid:boolean`",
            ],
        ],
        [1.6, 4.2, 4.2],
    )
    story += bullets(
        [
            "An implementation type of category value, boolean or enumeration needs a base "
            "type. Limits and enumeration values must fit the base type (`VAL-DT-005`).",
            "The unit must already exist in the project (`VAL-DT-012`). Element types of "
            "arrays and records must have the same level as the type that uses them "
            "(`VAL-DT-014`).",
            "For an application type the writer also creates a compu method `CM_<name>` and "
            "a data constraint `DC_<name>` when scaling, enumeration values or limits are "
            "given (name patterns in `[writer]`).",
            "A data type mapping (application to implementation) is written only when "
            "`[writer] data_type_mapping_set` names the mapping set. Otherwise add the "
            "mapping in DaVinci Developer.",
        ]
    )

    story.append(need(70))
    story.append(h3("D. New sender-receiver interface"))
    story.append(p("One row on `Interfaces`, then one row per data element on `InterfaceElements`:"))
    story += table(
        ["Sheet", "Interface", "Kind / Element", "Data type", "Queued (Y/N)", "Action"],
        [
            ["Interfaces", "IfVehicleSpeed", "sender-receiver", "", "", "ADD"],
            ["InterfaceElements", "IfVehicleSpeed", "Speed", "VehicleSpeed_T", "N", "ADD"],
            ["InterfaceElements", "IfVehicleSpeed", "Valid", "boolean", "N", "ADD"],
        ],
        [2.2, 2.2, 2, 2, 1.5, 1],
    )
    story += bullets(
        [
            "An interface is created completely or not at all: if one of its rows is "
            "rejected, all are (`VAL-IF-009`). An interface without any element is rejected "
            "(`VAL-IF-001`).",
            "An empty **Queued** cell means N.",
            "Adding an element to an interface that already exists is not possible in this "
            "version (`VAL-IF-004`).",
        ]
    )

    story.append(need(70))
    story.append(h3("E. New client-server interface"))
    story.append(
        p(
            "One row on `Interfaces` with kind `client-server`, then on `InterfaceElements` "
            "one row **per argument**. The operation name goes into **Element or operation** "
            "and repeats on each argument row. An operation without arguments is one row with "
            "only the operation name."
        )
    )
    story += table(
        ["Interface", "Element or operation", "Argument", "Argument direction", "Data type", "Action"],
        [
            ["IfDiagService", "ReadData", "Id", "IN", "uint16", "ADD"],
            ["IfDiagService", "ReadData", "Value", "OUT", "uint32", "ADD"],
            ["IfDiagService", "Reset", "", "", "", "ADD"],
        ],
        [2, 2.3, 1.4, 2, 1.5, 1],
    )

    story.append(need(75))
    story.append(h3("F. Runnable access (implicit or explicit)"))
    story.append(
        p(
            "Sheet `RunnableAccess`: **SWC**, **Runnable**, **Port**, **Element or operation**, "
            "**Access kind**. The runnable must exist (or be a server runnable created in the "
            "same round)."
        )
    )
    story += table(
        ["Access kind", "Port needed", "RTE API it leads to"],
        [
            ["`implicit read`", "R or PR, sender-receiver, element not queued", "`Rte_IRead`"],
            ["`implicit write`", "P or PR, sender-receiver, element not queued", "`Rte_IWrite`"],
            ["`explicit read`", "R or PR, sender-receiver, element not queued", "`Rte_Read`"],
            ["`explicit send`", "P or PR, sender-receiver", "`Rte_Write` / `Rte_Send`"],
            ["`queued receive`", "R or PR, sender-receiver, element queued", "`Rte_Receive`"],
            [
                "`synchronous server call`, `asynchronous server call`",
                "R port with a client-server interface (a client port)",
                "`Rte_Call`",
            ],
        ],
        [3.4, 4.4, 2.2],
    )
    story.append(
        p(
            "A runnable can have only one read access, one write access and one call per "
            "target (`VAL-ACC-004`). To change implicit into explicit, the old access has to "
            "be removed in DaVinci Developer first, because DELETE is not available yet."
        )
    )

    story.append(need(75))
    story.append(h3("G. Trigger for a runnable"))
    story.append(p("Sheet `RunnableTriggers`: **SWC**, **Runnable**, **Trigger kind**, then by kind:"))
    story += table(
        ["Trigger kind", "Also fill", "Leave empty"],
        [
            ["`periodic`", "Period (ms), greater than zero", "Port, Element"],
            [
                "`on data reception`, `on data reception error`",
                "Port (R or PR) and Element",
                "Period",
            ],
            ["`on operation invocation`", "Port (the server port) and the operation", "Period"],
            ["`init`", "nothing", "Port, Element, Period"],
        ],
        [3.6, 3.8, 2.6],
    )
    story += bullets(
        [
            "**Task** may stay empty. The new event then goes to the task of the runnable's "
            "existing trigger, after the last event already mapped there. If the runnable "
            "runs in several tasks, the cell must name one (`VAL-TRG-007`). A named task must "
            "exist (`VAL-TRG-005`).",
            "The task is not written into the ECUC file by PortTool. It appears in the report "
            "as a mapping to make in DaVinci Configurator (chapter 5.4).",
        ]
    )

    story.append(need(60))
    story.append(h3("H. New server port, with its server runnable"))
    story.append(
        p(
            "Add the port on `Ports` with direction `P` and a client-server interface. That is "
            "enough: for every operation of the interface the tool creates a server runnable "
            "named `<port>_<operation>` (setting `[naming] server_runnable_pattern`) and its "
            "operation-invoked trigger, because a server port without runnable is not "
            "generatable."
        )
    )
    story.append(
        p(
            "To choose the runnable name yourself, add a row on `RunnableTriggers` with kind "
            "`on operation invocation`, the new port, the operation and your runnable name. "
            "If that runnable already exists in the SWC, it is used and only the trigger is "
            "added. If the row that names the runnable is rejected, the port is rejected too."
        )
    )
    story.append(h2("6.4 Cell formats"))
    story += table(
        ["Cell", "Format", "Example"],
        [
            ["Enumeration values", "`value=NAME` pairs, semicolon between pairs", "`0=OFF;1=ON`"],
            ["Record elements", "`name:Type` pairs, semicolon between pairs", "`speed:uint16;valid:boolean`"],
            [
                "Init value",
                "`element=value` pairs; a bare value for a one-element interface; or `none`",
                "`Speed=0`, `0`, `none`",
            ],
            ["Queued, Connected", "`Y` or `N`", "`N`"],
            ["Period (ms)", "Number in milliseconds", "`10`, `2.5`"],
            ["Limits, Factor, Offset", "Number with decimal dot; hexadecimal allowed for whole numbers", "`-40`, `0.01`, `0xFF`"],
            ["Array size", "Positive whole number", "`8`"],
            ["Argument direction", "`IN`, `OUT`, `INOUT`", "`IN`"],
            ["Names of existing objects", "Short name, or the full path when the short name is not unique", "`/PortInterfaces/IfSig1`"],
        ],
        [2.4, 4.8, 2.8],
    )
    story.append(h2("6.5 The status of a request"))
    story += table(
        ["Status", "Meaning", "What the requester does"],
        [
            ["accepted", "Passed validation in a dry run. Not written yet.", "Nothing."],
            ["applied", "Written, verified and committed.", "Nothing."],
            [
                "already applied",
                "The project already contains exactly this. Nothing to do.",
                "Nothing. Often a request that two teams made.",
            ],
            [
                "rejected",
                "Broke a rule. The reason carries a code and says what to change.",
                "Correct the row and hand the workbook in again.",
            ],
            [
                "waiting for model owner",
                "Correct, but the SWC is model-owned. A package went to the model team.",
                "Wait for the model team; it closes by itself.",
            ],
            [
                "on hold",
                "A connector whose port does not exist yet (usually on a model-owned SWC).",
                "Nothing. It is written when the port arrives.",
            ],
        ],
        [2.2, 4.6, 3.2],
    )
    story.append(h2("6.6 Several workbooks, old workbooks"))
    story += bullets(
        [
            "All workbooks of one dry run are judged together. The same request in two files "
            "is merged (`VAL-CFL-001`). Two rows that ask for the same object differently are "
            "both rejected (`VAL-CFL-002`), so nobody wins silently.",
            "A workbook exported from an older commit still works. The import warns "
            "(`WBK-META-002`) and every request is checked against the project as it is now. "
            "A row whose object is gone is rejected (`VAL-ROW-006`).",
            "A workbook from a newer PortTool layout is refused (`WBK-META-004`); from an "
            "older layout it is read with a warning (`WBK-META-003`).",
        ]
    )
    return story


def chapter_7() -> list[Flowable]:
    story = h1("7. The GUI")
    story.append(h2("7.1 Start and stop"))
    story.append(
        code(
            r"""
PortTool-GUI.bat C:\path\to\ecu_repo
        or
porttool gui --repo C:\path\to\ecu_repo [--port 8765] [--no-browser]

PortTool is running at http://127.0.0.1:53124/?token=...
Keep this window open. Press Ctrl+C to stop PortTool.
"""
        )
    )
    story += bullets(
        [
            "The GUI is a page in your default browser, served by a small server on this "
            "computer only (`127.0.0.1`, a random free port). Nobody else on the network can "
            "reach it, and every call needs the secret token from the address.",
            "The console window is the server. Closing it stops PortTool.",
            "The token is kept in memory only. **Reloading the page (F5) ends the session**: "
            "open the address printed in the console window again.",
            "To work on another repository, stop and start again with the other `--repo`.",
        ]
    )
    story.append(h2("7.2 The page"))
    story += picture(
        str(SCREENSHOTS / "ports_table.png"),
        "Figure 4. The Ports tab with 20,000 rows (sample data). Search over all columns, a "
        "filter per column, sorting by a click on the header, and \"Only unconnected\".",
    )
    story += table(
        ["Area", "What it does"],
        [
            [
                "Header",
                "Repository path and counts. **Reload** loads the repository again. **Check "
                "tools** runs the doctor (chapter 9.4) without starting any DaVinci tool.",
            ],
            [
                "Banners",
                "A warning banner when files changed on disk since the load (with a Reload "
                "button). An alert banner when an earlier apply did not finish (with a Roll "
                "back button; everything else is disabled until then).",
            ],
            [
                "Job strip",
                "The running job with its progress, elapsed time and a Cancel button. One "
                "job runs at a time.",
            ],
            [
                "Table tabs",
                "Components, Ports, Runnable access, Triggers, Connections, Unconnected, "
                "Interfaces, Interface elements, Data types, Pending. Read-only. Tables load "
                "rows as you scroll, so 20,000 rows stay fast.",
            ],
            ["Requests tab", "The five steps of a change round (below)."],
        ],
        [1.6, 8.4],
    )
    story.append(h2("7.3 The Requests tab"))
    story += picture(
        str(SCREENSHOTS / "dry_run_results.png"),
        "Figure 5. Requests tab after a validation (sample data; the codes in the picture come "
        "from the sample, not from the real code table).",
    )
    story += table(
        ["Section", "Use"],
        [
            [
                "1. Export workbook",
                "Type the full path of the `.xlsx` to write (in an existing folder).",
            ],
            [
                "2. Import workbooks",
                "Pick or drop one or more workbooks (up to 20 files, 50 MB each by default). "
                "The dry run starts at once.",
            ],
            [
                "3. Request form",
                "Enter single ADD requests without Excel: choose the kind, fill the fields "
                "(with the same dropdowns as the workbook), add to the list, **Validate "
                "list**. MODIFY and DELETE need the workbook.",
            ],
            [
                "4. Results of the last validation",
                "Every request with status, source row and reasons; filter by status. Below "
                "it the general findings and the plan: which files would change.",
            ],
            [
                "5. Apply",
                "**Apply accepted requests** is enabled when the last validation has accepted "
                "requests, the model is not stale and no recovery is needed. After an apply "
                "attempt, validate again before applying again.",
            ],
        ],
        [2.6, 7.4],
    )
    return story


def chapter_8() -> list[Flowable]:
    story = h1("8. Command reference")
    story.append(
        p(
            "`porttool <command> [options]`. Most commands take `--repo DIR` (default: the "
            "current folder). `porttool <command> --help` prints the options."
        )
    )
    story.append(h2("8.1 Commands for every day"))
    story += table(
        ["Command", "What it does", "Changes files?"],
        [
            ["`init [--dpa FILE] [--force]`", "Finds the project files and writes `porttool.toml`.", "`porttool.toml`"],
            ["`config check`", "Loads `porttool.toml`, `owners.csv`, `locked.txt` and reports problems.", "No"],
            ["`load`", "Loads the repository; prints counts, timings and findings.", "Cache only"],
            ["`status`", "Branch, commit, working tree, pending queue, enabled DaVinci steps, journal folder, unfinished apply.", "No"],
            ["`list swc | ports | connectors | unconnected`", "Prints the tables on the console.", "No"],
            ["`export --out FILE`", "Loads and writes the request workbook.", "The workbook"],
            ["`workbook-check FILES... [--json]`", "Reads workbooks and prints the request rows and import findings, without a repository.", "No"],
            ["`dry-run WORKBOOKS...`", "Imports, validates, plans. Prints the status of every request and the files that would change.", "No"],
            ["`apply WORKBOOKS... [--yes]`", "Dry run, confirmation, then apply on a new branch.", "Yes"],
            ["`rollback`", "Undoes an apply that did not finish.", "Restores"],
            ["`gui [--port N] [--no-browser]`", "Starts the page.", "No"],
        ],
        [3.6, 5, 1.4],
    )
    story.append(h2("8.2 Commands for the Vector tools"))
    story += table(
        ["Command", "What it does"],
        [
            ["`davinci doctor`", "Says what must be fixed before a DaVinci step can run: executables, paths, command templates, log folder. Starts no tool."],
            ["`davinci show-commands`", "Prints the exact command line of every action and whether it is still UNVERIFIED. Runs nothing."],
            ["`davinci check`", "Runs the workspace check (DVWspChecker) once, outside an apply."],
            ["`davinci validate`", "Runs the project validation (DVCfgCmd) once."],
            ["`davinci generate [--modules Rte,Os]`", "Runs the generation (DVCfgCmd) once."],
        ],
        [3.2, 6.8],
    )
    story.append(h2("8.3 Commands for setup and diagnosis"))
    story += table(
        ["Command", "What it does"],
        [
            ["`discover run --repo DIR [--dpa] [--sip] [--developer-bin] [--out] [--anonymize] [--no-tools] [--exclude GLOB] [--git PATH]`", "Read-only study of a real repository; writes the report folder `porttool_discovery`."],
            ["`discover golden-diff --repo DIR --base COMMIT --head COMMIT`", "Compares two commits element by element: what DaVinci Developer wrote for one hand-made change."],
            ["`config discover`", "Proposes the paths for `porttool.toml` from the `.dpa` file."],
            ["`validator-codes`", "Prints every validator code with severity and meaning."],
            ["`writer-codes`", "Prints the writer codes."],
            ["`writer-plan CHANGE_SET`", "Shows what a change set (JSON) would write. For debugging the writer."],
            ["`writer-delta CHANGE_SET TARGET`", "Writes a delta ARXML with every element a change set touches."],
        ],
        [4.6, 5.4],
    )
    story.append(h2("8.4 Global options and exit codes"))
    story += table(
        ["Option", "Effect"],
        [
            ["`-v`, `--verbose`", "More detail on the console and in the log (debug level)."],
            ["`--log-dir DIR`", "Write the log file to this folder instead of `%LOCALAPPDATA%\\PortTool\\logs`."],
            ["`--version`", "Print the version."],
        ],
        [2.4, 7.6],
    )
    story.append(p("Global options go before the command: `porttool -v --log-dir C:\\temp\\ptlog dry-run ...`"))
    story += table(
        ["Exit code", "Meaning"],
        [
            ["0", "Success."],
            ["1", "Finished, but at least one finding of severity error (for `apply`: also when a request was rejected although the rest was applied)."],
            ["2", "A `PortToolError`: the tool stopped on purpose. The console shows `[PT-Exxx] message` and the log file path. Also wrong command-line usage."],
            ["3", "An unexpected exception (a bug). The traceback is in the log file."],
        ],
        [1.4, 8.6],
    )
    return story


def chapter_9() -> list[Flowable]:
    story = h1("9. Configuration")
    story.append(
        p(
            "The project configuration lives in the root of the ECU repository and is meant to "
            "be committed, so the whole project uses the same settings: `porttool.toml`, "
            "`owners.csv`, `locked.txt`. Paths are relative to the repository root, or "
            "absolute. `porttool config check` reports every problem of all three files."
        )
    )
    story.append(h2("9.1 porttool.toml"))
    story.append(
        code(
            r"""
format_version = 1

[paths]
dpa_file      = "Config/MyEcu.dpa"                    # DaVinci Configurator project
dcf_file      = "Config/Developer/MyEcu.dcf"          # DaVinci Developer workspace
ecuc_file     = "Config/ECUC/MyEcu_ecuc.arxml"        # task mapping is read from here
arxml_roots   = ["Config/Developer"]                  # folders with the workspace ARXML (required)
exclude_globs = ["Appl/GenData/**"]                   # never read these
gen_data      = "Appl/GenData"                        # generated code
sip_root      = "C:/Vector/SIP"                       # only for the DaVinci steps
developer_bin = "C:/Program Files/Vector/DaVinci Developer/Bin"
# xsd_file    = "C:/Vector/AUTOSAR_4-2-2.xsd"         # optional: validate written files

[naming]
max_length = 127
server_runnable_pattern = "{port}_{operation}"
connector_pattern = "{provider}_{provider_port}_{requester}_{requester_port}"
# port = "^(Pp|Rp|PRp)[A-Z][A-Za-z0-9_]*$"            # optional regular expressions per kind

[generate]
modules = ["Rte", "Os"]

[pipeline]
davinci_steps = []                                    # see 9.4
"""
        )
    )
    story += table(
        ["Table", "Keys", "Notes"],
        [
            [
                "`[paths]`",
                "`dpa_file`, `dcf_file`, `ecuc_file`, `arxml_roots`, `exclude_globs`, "
                "`flat_extract`, `flat_map`, `sip_root`, `developer_bin`, `gen_data`, "
                "`xsd_file`, `cache_dir`",
                "Only `arxml_roots` is required. A missing optional path is a finding "
                "(`CFG-PATH-001`), a missing ARXML folder an error.",
            ],
            [
                "`[naming]`",
                "`max_length` (127), regular expressions `port`, `interface`, `data_type`, "
                "`runnable`, `connector`; `server_runnable_pattern`; `connector_pattern`",
                "A name that breaks a rule is rejected with `VAL-NAME-003`.",
            ],
            [
                "`[packages]`",
                "`data_types`, `interfaces`, `constants`, `compu_methods`, `constraints`",
                "AUTOSAR package paths where new objects are created.",
            ],
            [
                "`[timeouts]`",
                "`workspace_check_s` (600), `script_task_s` (900), `validate_s` (1800), "
                "`generate_s` (3600), `import_s` (900), `git_s` (120)",
                "A tool that runs longer is stopped with its child processes (`PT-E610`).",
            ],
            ["`[generate]`", "`modules` (Rte, Os)", "Modules for the generate step."],
            [
                "`[pending]`",
                "`file` (`porttool/pending.json`), `max_age_days` (14)",
                "The pending queue.",
            ],
            [
                "`[limits]`",
                "`large_file_bytes` (50 MB), `min_free_disk_bytes` (2 GB)",
                "Apply is refused below the free-space limit (`PIP-PRE-009`).",
            ],
            [
                "`[pipeline]`",
                "`davinci_steps` ([]), `fallback_import` (false), `allow_delete` (false), "
                "`report_dir` (`porttool_reports`), `commit_reports` (false), "
                "`commit_generated` (true), `tool_write_scopes` ([]), `journal_dir`, "
                "`log_tail_lines` (200)",
                "How apply behaves. A wrong type is an error and blocks apply (`PIP-CFG-001`).",
            ],
            [
                "`[writer]`",
                "`new_object_placement` (`existing_file` or `new_file`), `uuid` (`auto`, "
                "`upper`, `lower`, `none`), `access_point_pattern`, `access_point_prefixes`, "
                "`event_patterns`, `com_spec_for_every_element`, `receiver_com_spec_defaults`, "
                "`client_com_spec`, `queue_length`, `server_call_timeout`, "
                "`compu_method_pattern`, `constraint_pattern`, `array_element_name`, "
                "`data_type_mapping_set`",
                "How the written XML looks. These are the knobs for matching what DaVinci "
                "Developer writes (chapter 10.7).",
            ],
            [
                "`[davinci]`",
                "`[davinci.executables]`, `[davinci.commands]`, `[davinci.results]`, "
                "`[davinci.env]`, `gui_process_names`, `lock_file_globs`, `script_dir`, "
                "`script_task`, `encoding`, `log_dir`, `max_tool_logs`, `leftover_children`",
                "How the Vector tools are started and how their output is judged (9.4).",
            ],
            [
                "`[gui]`",
                "`port` (0 = random), `max_upload_mb` (50), `max_upload_files` (20), "
                "`stale_check_interval_s` (2.0), `partner_limit` (10), `max_form_rows` (5000)",
                "The page.",
            ],
        ],
        [1.3, 5.2, 3.5],
    )
    story.append(
        p(
            "An unknown key or table is reported and ignored (`CFG-KEY-001`, `PIP-CFG-002`, "
            "`DVR-CFG-UNKNOWN`), which catches typing mistakes. A malformed file or a value of "
            "the wrong type stops the tool with `[PT-E100]` and a message that names the file, "
            "the key and the problem. The complete list of keys with defaults is in the "
            "`HANDOFF.md` of the package that reads the table."
        )
    )
    story.append(h2("9.2 owners.csv"))
    story.append(
        code(
            """
SWC,team,arxml_owner,authoring_tool,export_folder
Swc1,TeamChassis,davinci,,
Swc2,TeamChassis,davinci,,
Swc3,TeamPowertrain,model,Simulink,Models/Swc3/arxml
"""
        )
    )
    story += bullets(
        [
            "`SWC` and `arxml_owner` are required; the others are optional. Column order and "
            "letter case do not matter; comma, semicolon or tab; UTF-8.",
            "`arxml_owner` is `davinci`, `model` or `locked`. Empty means `davinci`. A SWC "
            "not listed is DaVinci-owned.",
            "`team` is used for the request packages and shown in the workbook. "
            "`export_folder` is where the model team's ARXML export lands.",
            "Without `owners.csv` every SWC counts as DaVinci-owned (`CFG-OWNERS-001`). "
            "`porttool discover` proposes a file as `owners_proposed.csv`.",
            "An invalid owner, an invalid SWC name or two rows that contradict each other "
            "stop the tool (`[PT-E100]`): ownership must never be ambiguous.",
        ]
    )
    story.append(
        note(
            "danger",
            "Mark every model-owned SWC before the first apply.",
            "If a Simulink or TargetLink SWC is left as `davinci`, the tool edits its ARXML, "
            "and the next model export silently overwrites the change.",
        )
    )
    story.append(h2("9.3 locked.txt"))
    story.append(
        p(
            "One SWC name per line; `#` starts a comment. A locked SWC is never edited, "
            "whatever the request says "
            "(`VAL-OWN-001`). Use it for supplier components and anything under release "
            "freeze."
        )
    )
    story.append(need(80))
    story.append(h2("9.4 Switching on the DaVinci steps"))
    story.append(
        p(
            "PortTool starts the Vector tools from **command templates**. The built-in "
            "templates are guesses that nobody has checked against the real tools; each one "
            "is labelled UNVERIFIED until you replace it. That is why all steps are off by "
            "default."
        )
    )
    story += table(
        ["Step", "Tool", "What it does"],
        [
            ["`check`", "DVWspChecker (DaVinci Developer)", "Checks the workspace after the ARXML edits."],
            [
                "`script`",
                "DVCfgCmd with a Groovy script task",
                "Maps the new RTE events to their tasks and runs solving actions in "
                "Configurator. **Experimental: the script has never been executed.** Leave it "
                "off and map by hand.",
            ],
            ["`validate`", "DVCfgCmd", "Validates the Configurator project."],
            ["`generate`", "DVCfgCmd", "Generates the modules of `[generate] modules`."],
        ],
        [1.2, 3.4, 5.4],
    )
    story += steps(
        [
            "Set `sip_root` and `developer_bin` in `[paths]`, or the full paths in "
            "`[davinci.executables]` (`dvwspchecker`, `dvimex`, `dvswcgen`, `dvcfgcmd`).",
            "Run `porttool davinci doctor`. It lists what is missing, with the exact line to "
            "add to `porttool.toml`. It starts no tool.",
            "Run `porttool davinci show-commands` and compare each line with the tool's own "
            "help text (collected by `porttool discover run` in the `cli` folder).",
            "Write the corrected command lines into `[davinci.commands]`. A template is a list "
            "of arguments with placeholders. A template written in `porttool.toml` counts as "
            "verified:",
        ]
    )
    story.append(
        code(
            r"""
[davinci.commands]
check    = ['{exe}', '-w', '{dcf}']                          # built-in guess, UNVERIFIED
generate = ['{exe}', '-p', '{dpa}', '-g', '-m', '{modules_csv}']

# placeholders: {exe} {dcf} {dpa} {arxml} {modules} {modules_csv} {swcs} {swcs_csv}
#               {script_dir} {script_task} {request_json} {result_json} {log_dir}

[davinci.results]
error_patterns   = ["(?i)error"]          # a matching output line becomes an error finding
warning_patterns = ["(?i)warning"]
# license_patterns has a built-in default; a matching line stops the run with PT-E620

[davinci.results.success_exit_codes]
check = [0]                               # exit codes that mean success, per action
"""
        )
    )
    story += steps(
        [
            "Try the step alone: `porttool davinci check`. Read the tool log it names.",
            "Enable the step: `[pipeline] davinci_steps = [\"check\"]`. Later add "
            "`\"validate\"`, then `\"generate\"`. The run order is always check, script, "
            "validate, generate.",
        ],
        start=5,
    )
    story += bullets(
        [
            "When a DaVinci step is enabled, apply refuses to start while DaVinci Developer or "
            "Configurator has the project open (`PIP-PRE-010`).",
            "With `generate` enabled, tracked generated files are committed with the change "
            "(`commit_generated`). If a tool fails, the whole apply is rolled back, including "
            "what the tools wrote inside their write scopes.",
            "`fallback_import = true` adds a second chance when the workspace check fails: the "
            "direct edits are undone and the same change is imported through DVImEx as a delta "
            "ARXML. Leave it off until the direct edits are confirmed.",
        ]
    )
    return story

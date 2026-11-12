# PortToolTasks.dvgroovy: EXPERIMENTAL / UNVERIFIED

This folder holds one Groovy script for DaVinci Configurator 5. PortTool runs it through
DVCfgCmd to map new RTE events to OS tasks, apply preferred solving actions for RTE and OS
validation messages, and save the project.

**The script has never been run.** It was written without DaVinci Configurator and without
the automation interface documentation. Treat every call into the Configurator as a guess
until you have checked it. PortTool keeps the script step switched off by default
(`[pipeline] davinci_steps = []`), and whenever the step is off or fails it prints the
same work as steps to do by hand in DaVinci Configurator.

## Before you enable the script step

1. Open the automation interface documentation that ships with your SIP (look for
   "AutomationInterface" in the documentation folder of DaVinci Configurator).
2. Go through the list `API CALLS THAT MUST BE CHECKED` at the top of the script, tags
   `[A1]` to `[A14]`. Each tag marks one small closure in the section `CONFIGURATOR API`.
   Correct a closure where the documentation says something else. Nothing outside that
   section calls the Configurator.
3. Check how your DVCfgCmd starts a script task and where it looks for script files, and
   put the confirmed command line into porttool.toml:

   ```toml
   [davinci.commands]
   script_task = ['{exe}', '-p', '{dpa}', '-s', '{script_task}', '--scriptLocations', '{script_dir}']
   ```

   The line above is the built-in guess, and it is UNVERIFIED too. `porttool davinci
   show-commands` prints what would be run; `porttool davinci doctor` says what is missing.
4. Try it on a **copy** of the project first. Compare the ECUC file before and after with
   a mapping you made by hand for the same event.
5. Only then add `"script"` to `[pipeline] davinci_steps`.

## How PortTool calls it

PortTool starts DVCfgCmd in the repo root with these environment variables:

| Variable | Meaning |
| --- | --- |
| `PORTTOOL_REQUEST_JSON` | request file to read |
| `PORTTOOL_RESULT_JSON` | result file to write (PortTool deletes an old one first) |
| `PORTTOOL_LOG_DIR` | folder of PortTool's tool logs |

The paths are passed through the environment, not as script arguments, because the way
DVCfgCmd passes arguments to a script task is not known. The placeholders
`{request_json}` and `{result_json}` exist if you prefer arguments.

Script tasks in the file:

- `PortToolMapEvents`: the mapping task described here (`[davinci] script_task`).
- `PortToolValidate`: logs every validation message of severity error as a line with the
  word `ERROR` and changes nothing. The built-in guess for the `validate` action uses it.

## Request file (written by PortTool)

```json
{
  "format": "porttool-script-request",
  "format_version": 1,
  "created_utc": "2026-11-12T08:00:00+00:00",
  "options": {
    "apply_solving_actions": true,
    "solving_action_origins": ["RTE", "OS"],
    "save_project": true
  },
  "events": [
    {
      "swc": "SwcA",
      "runnable": "RunA_OnSpeed",
      "event_path": "/ComponentTypes/SwcA/SwcA_InternalBehavior/DRE_RunA_OnSpeed",
      "task": "Task_10ms",
      "position": null,
      "position_after_event": "/ComponentTypes/SwcA/SwcA_InternalBehavior/TE_RunA_10ms"
    }
  ]
}
```

`position` is the wanted position in the task. If it is null, the event goes directly
after `position_after_event`; if that is null too, after the last event of the task.

## Result file (written by the script)

```json
{
  "format": "porttool-script-result",
  "format_version": 1,
  "complete": true,
  "saved": true,
  "steps": [
    {"name": "read-request", "ok": true, "message": ""},
    {"name": "map-events", "ok": true, "message": ""}
  ],
  "events": [
    {"event_path": "/ComponentTypes/SwcA/...", "task": "Task_10ms",
     "status": "mapped", "message": "mapped at position 3"}
  ],
  "solving_actions": {"attempted": 2, "applied": 2, "failed": 0,
                      "remaining_errors": 0, "messages": []}
}
```

- `status` is `mapped`, `already_mapped` or `failed`.
- The file is rewritten after every step. `complete` stays `false` until the last line of
  the script, so a script that stops early leaves a file PortTool reports as partial.
- A step that throws is recorded with `"ok": false` and its message; the script goes on
  with what still makes sense and never ends with an exception on purpose.

## How PortTool judges the result

`porttool.davinci.read_script_result` turns each of these into a finding and makes the
script step fail: no result file, a file that is not valid JSON or not in this format,
`complete` not true, a failed step, an event with status `failed`, an event of the
request that the result does not mention, an event mapped to another task than asked,
events mapped but the project not saved. Remaining validation errors are a warning.

For every event that is not confirmed, PortTool prints how to map it by hand
(`porttool.davinci.manual_mapping_instructions`).

## What the mapping part assumes (check `[A10]`)

The script works on the ECUC model with the AUTOSAR standard names of the RTE definition:
container `RteSwComponentInstance`, sub-container `RteEventToTaskMapping` with
`RteEventRef`, `RteMappedToTaskRef` and `RtePositionInTask`, and `OsTask` containers in
the `Os` module below `/ActiveEcuC`. If your automation interface offers a dedicated call
for task mapping (a runtime system domain), prefer it: replace the body of `mapOneEvent`
and keep the entry it returns (`event_path`, `task`, `status`, `message`).

Log lines of level info must not contain the words "error" or "warning": PortTool counts
such lines in the output of DVCfgCmd as failures or warnings of the tool.

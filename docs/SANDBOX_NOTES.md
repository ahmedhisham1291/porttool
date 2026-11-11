# Notes for agents working in this build sandbox

Read docs/SHARED_CONTEXT.md first. These notes say how this sandbox differs from the
target machine. They override nothing in the shared context; they tell you what you can
and cannot verify here.

## The sandbox
- Linux container, Python 3.13, no network. Never run pip, never try to install anything.
- Installed: lxml 6.1, openpyxl 3.1.5, XlsxWriter 3.2.9, pydantic 2.13, click, pytest 9,
  mypy, ruff, psutil, PyYAML, jinja2, git, Node 22, Google Chrome (/usr/bin/google-chrome)
  and Playwright for Python (use the system Chrome: channel="chrome" or executable_path).
- Only 2 CPU cores and 7 GB RAM, shared with other agents: timings measured here are
  pessimistic and noisy. Report them as measured, with that caveat.
- Not installed: typer, PySide6 or any Qt binding, tkinter, hypothesis, pytest-cov,
  coverage, nox, PyInstaller. Do not use them. Runtime dependencies are limited to lxml,
  openpyxl, XlsxWriter, pydantic, click, psutil and the standard library.
- No Windows, no DaVinci tools, no real ECU repo, and docs/discovery.md does not exist yet.

## What that means for your code
- The product targets Windows. Do not add `sys.platform` branches or portability layers
  to product code. Write code that is correct on Windows; pure logic will also run here.
- Your tests must pass here. Anything that can only be proven on Windows (sharing
  violations, long-path prefix behaviour, %LOCALAPPDATA% defaults, Proactor loop,
  process-tree kill) goes into small isolated functions. Test the logic with
  monkeypatching where that is honest, mark what remains with
  `@pytest.mark.windows_only`, and list it in HANDOFF.md under "Unverified on Windows".
  Never present an untested Windows behaviour as tested.
- Keep code compatible with Python 3.11 (no 3.12+ syntax).
- Build to the AUTOSAR 4.x standard XML. Everything DaVinci-specific (how the .dcf file
  lists files, one-file-per-object layout, UUID style, package conventions) goes behind
  one small seam with a provisional implementation. Mark each assumption with a
  `PENDING-DISCOVERY:` comment and list it in HANDOFF.md.
- CLI: use click. Each package has a `cli.py` with `register_cli(group: click.Group)`.
  Test commands with `click.testing.CliRunner`. Keep commands thin: parse arguments, call
  tested functions, print results; exit code 0 on success, 1 on findings of severity
  error, 2 on a PortToolError.
- Coverage: pytest-cov is missing. Use the stdlib if you want a number
  (`python -m trace --count` is acceptable), or state plainly that coverage was not
  measured. Do not invent a percentage.

## Contracts
- porttool/core was written by the orchestrator as v0. These files are frozen for you:
  models.py, operations.py, results.py, errors.py, interfaces.py, sheets.py, pending.py, rowid.py,
  config.py. Read them before you write anything.
- If a contract is missing something, do NOT edit it. Work around it inside your own
  package and list the change you want in HANDOFF.md under "Contract change requests".
- tests/helpers/sample_model.py gives you a small ready-made Model and ProjectConfig.

## Working rules
- Work only inside your worktree directory and only in the folders your task names.
- Commit to your branch in small commits. Never push, never touch other branches.
- Before you finish, run and paste the tail of each into HANDOFF.md:
  `python3 -m pytest tests/unit/<package> -q`,
  `mypy --strict porttool/<package> tests/unit/<package>`,
  `ruff check porttool/<package> tests/unit/<package>`.
- Report honestly. If a test fails or a budget is missed, say so with the numbers.
- Your final message must list: what is done, what is not done, test counts, measured
  timings, items unverified on Windows, PENDING-DISCOVERY assumptions, and contract
  change requests.

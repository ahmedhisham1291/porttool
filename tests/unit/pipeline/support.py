"""Test support for the pipeline: generated git repos, a fake writer and a fake DaVinci runner.

The reader, the workbook package and the validator are the real ones. The writer and
the DaVinci runner are fakes behind the core protocols (the real ones are built in
parallel and are not in this worktree).
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from porttool.core.config import ProjectConfig
from porttool.core.errors import PortToolError
from porttool.core.interfaces import (
    JobContext,
    JobView,
    Journal,
    PlannedFile,
    ToolResult,
    WriteOutcome,
    WritePlan,
)
from porttool.core.models import InterfaceKind, Model, PortDirection
from porttool.core.operations import (
    AddConnector,
    AddInterface,
    AddPort,
    AnyOperation,
    ChangeSet,
    DeletePort,
)
from porttool.core.results import Finding, RawRequestRow, RowRef, Severity
from porttool.core.sheets import COLUMNS, Sheet
from porttool.pipeline import PortToolService, build_service
from tests.fixtures.generator import SMALL, GeneratorSpec, GroundTruth, generate

GIT_NAME = "Test Owner"
GIT_EMAIL = "owner@example.invalid"


# --------------------------------------------------------------------------- git and repos


def git_env(home: Path, *, identity: bool = True) -> dict[str, str]:
    """A process environment in which git sees only the test's own global configuration."""
    home.mkdir(parents=True, exist_ok=True)
    config = home / "gitconfig"
    lines = ["[init]", "\tdefaultBranch = main", "[core]", "\tautocrlf = false"]
    if identity:
        lines += ["[user]", f"\tname = {GIT_NAME}", f"\temail = {GIT_EMAIL}"]
    else:
        lines += ["[user]", "\tuseConfigOnly = true"]
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = dict(os.environ)
    for key in [k for k in env if k.startswith("GIT_")]:
        del env[key]
    env["GIT_CONFIG_GLOBAL"] = str(config)
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["HOME"] = str(home)
    return env


def git(repo: Path, *args: str, env: Mapping[str, str], check: bool = True) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, env=dict(env), capture_output=True, text=True, check=False
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stdout}{done.stderr}")
    return done.stdout


@dataclass
class Repo:
    root: Path
    env: dict[str, str]
    truth: GroundTruth

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.root, *args, env=self.env, check=check)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def branches(self) -> list[str]:
        return sorted(self.git("for-each-ref", "--format=%(refname:short)", "refs/heads").split())

    def status(self) -> str:
        return self.git("status", "--porcelain", "--untracked-files=all")

    def snapshot(self) -> dict[str, object]:
        """Every file outside .git byte for byte, plus HEAD, branch, branches and status."""
        files: dict[str, bytes] = {}
        for folder, dirs, names in os.walk(self.root):
            if ".git" in dirs:
                dirs.remove(".git")
            for name in names:
                path = Path(folder) / name
                files[path.relative_to(self.root).as_posix()] = path.read_bytes()
        return {
            "files": files,
            "head": self.head(),
            "branch": self.branch(),
            "branches": self.branches(),
            "status": self.status(),
        }


def make_repo(
    root: Path,
    home: Path,
    *,
    spec: GeneratorSpec = SMALL,
    identity: bool = True,
    extra_toml: str = "",
    gitignore: str | None = None,
) -> Repo:
    """Generate the synthetic repo, make it a git repository and commit everything."""
    truth = generate(root, spec)
    with open(root / "porttool.toml", "a", encoding="utf-8") as stream:
        # The sandbox disk is small and shared: do not let the free-space check decide.
        stream.write("\n[limits]\nmin_free_disk_bytes = 1\n" + extra_toml)
    if gitignore is not None:
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    env = git_env(home, identity=True)
    git(root, "init", "--quiet", env=env)
    git(root, "add", "--all", env=env)
    git(root, "commit", "--quiet", "-m", "initial", env=env)
    return Repo(root=root, env=git_env(home, identity=identity), truth=truth)


# --------------------------------------------------------------------------- rows


def row(sheet: Sheet, number: int = 1, file: str = "gui", **values: str) -> RawRequestRow:
    """A request row as the workbook import (or the GUI) would deliver it."""
    cells = {column.key: "" for column in COLUMNS[sheet]}
    cells.update(values)
    action = cells.get("action", "") or "ADD"
    cells["action"] = action
    return RawRequestRow(
        sheet=sheet.value,
        row=RowRef(file=file, sheet=sheet.value, row=number),
        row_id=cells.get("row_id") or None,
        action=action.upper(),
        requester=cells.get("requester", "") or "Team1",
        comment=cells.get("comment", ""),
        values=cells,
    )


def port_row(
    swc: str, port: str, direction: str, interface: str, number: int = 1, action: str = "ADD"
) -> RawRequestRow:
    return row(
        Sheet.PORTS,
        number,
        swc=swc,
        port=port,
        direction=direction,
        interface=interface,
        init_value="none",
        action=action,
        requester="Team1",
    )


def connector_row(
    provider: str, provider_port: str, requester: str, requester_port: str, number: int = 1
) -> RawRequestRow:
    return row(
        Sheet.CONNECTIONS,
        number,
        provider_swc=provider,
        provider_port=provider_port,
        requester_swc=requester,
        requester_port=requester_port,
        composition="EcuComposition",
        requester="Team1",
    )


# --------------------------------------------------------------------------- fake writer

_PORT_XML = """\
            <{tag}>
              <SHORT-NAME>{name}</SHORT-NAME>
              <{ref} DEST="{dest}">{interface}</{ref}>
            </{tag}>
"""
_CONNECTOR_XML = """\
            <ASSEMBLY-SW-CONNECTOR>
              <SHORT-NAME>{name}</SHORT-NAME>
              <PROVIDER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">{pproto}{ccr}
                <TARGET-P-PORT-REF DEST="P-PORT-PROTOTYPE">{pport}</TARGET-P-PORT-REF>
              </PROVIDER-IREF>
              <REQUESTER-IREF>
                <CONTEXT-COMPONENT-REF DEST="SW-COMPONENT-PROTOTYPE">{rproto}{ccr}
                <TARGET-R-PORT-REF DEST="R-PORT-PROTOTYPE">{rport}</TARGET-R-PORT-REF>
              </REQUESTER-IREF>
            </ASSEMBLY-SW-CONNECTOR>
"""
_INTERFACE_FILE = """\
<?xml version="1.0" encoding="UTF-8"?>
<AUTOSAR xmlns="http://autosar.org/schema/r4.0">
  <AR-PACKAGES>
    <AR-PACKAGE>
      <SHORT-NAME>{package}</SHORT-NAME>
      <ELEMENTS>
        <SENDER-RECEIVER-INTERFACE>
          <SHORT-NAME>{name}</SHORT-NAME>
          <IS-SERVICE>false</IS-SERVICE>
          <DATA-ELEMENTS>
{elements}          </DATA-ELEMENTS>
        </SENDER-RECEIVER-INTERFACE>
      </ELEMENTS>
    </AR-PACKAGE>
  </AR-PACKAGES>
</AUTOSAR>
"""
_ELEMENT_XML = """\
            <VARIABLE-DATA-PROTOTYPE>
              <SHORT-NAME>{name}</SHORT-NAME>
              <TYPE-TREF DEST="IMPLEMENTATION-DATA-TYPE">{type}</TYPE-TREF>
            </VARIABLE-DATA-PROTOTYPE>
"""


def _interface_dest(model: Model, path: str) -> str:
    interface = model.interfaces.get(path)
    if interface is not None and interface.kind is InterfaceKind.CLIENT_SERVER:
        return "CLIENT-SERVER-INTERFACE"
    return "SENDER-RECEIVER-INTERFACE"


@dataclass
class FakeWriter:
    """Edits the generated ARXML with plain text operations, journaling every file first.

    Knows ``add_port``, ``delete_port``, ``add_interface`` (a new file) and
    ``add_connector``: enough to drive every pipeline path with the real reader.
    """

    #: Raised by ``apply`` after ``fail_after_files`` files were written.
    fail_with: BaseException | None = None
    fail_after_files: int = 0
    #: Error finding returned by ``apply``.
    error_finding: Finding | None = None
    #: Operation ids that are reported as applied but not written (verification must notice).
    skip_ids: frozenset[str] = frozenset()
    #: Write these files without journaling them (contract violation).
    unjournaled: frozenset[str] = frozenset()
    #: Extra objects to destroy while writing: SHORT-NAMEs of ports removed from their file.
    destroy_ports: tuple[tuple[str, str], ...] = ()
    #: Called once per written file, e.g. to cancel the job in the middle of the write.
    on_file: Callable[[str], None] | None = None
    plan_findings: tuple[Finding, ...] = ()
    delta_findings: tuple[Finding, ...] = ()
    applied: list[str] = field(default_factory=list)
    deltas: list[Path] = field(default_factory=list)
    apply_calls: int = 0

    # ---- the edits

    def _target(self, model: Model, config: ProjectConfig, op: AnyOperation) -> tuple[str, str]:
        """(repo-relative file, action) of one operation."""
        if isinstance(op, AddPort | DeletePort):
            return model.components[op.swc_path].origin.file, "modify"
        if isinstance(op, AddConnector):
            return model.components[op.composition_path].origin.file, "modify"
        if isinstance(op, AddInterface):
            folder = config.arxml_roots[0].relative_to(config.repo_root).as_posix()
            return f"{folder}/PortInterfaces/New/{op.name}.arxml", "create"
        raise PortToolError(f"FakeWriter cannot write {op.op}")

    def _edit(self, model: Model, op: AnyOperation, text: str) -> str:
        if isinstance(op, AddPort):
            provided = op.direction is PortDirection.P
            snippet = _PORT_XML.format(
                tag="P-PORT-PROTOTYPE" if provided else "R-PORT-PROTOTYPE",
                name=op.name,
                ref="PROVIDED-INTERFACE-TREF" if provided else "REQUIRED-INTERFACE-TREF",
                dest=_interface_dest(model, op.interface_path),
                interface=op.interface_path,
            )
            return text.replace("          </PORTS>\n", snippet + "          </PORTS>\n", 1)
        if isinstance(op, DeletePort):
            return _remove_port(text, op.name)
        if isinstance(op, AddConnector):
            provider = model.find_prototype(op.composition_path, op.provider_prototype)
            requester = model.find_prototype(op.composition_path, op.requester_prototype)
            assert provider is not None and requester is not None
            snippet = _CONNECTOR_XML.format(
                ccr="</CONTEXT-COMPONENT-REF>",
                name=op.name,
                pproto=provider.path,
                pport=f"{provider.type_path}/{op.provider_port}",
                rproto=requester.path,
                rport=f"{requester.type_path}/{op.requester_port}",
            )
            return text.replace(
                "          </CONNECTORS>\n", snippet + "          </CONNECTORS>\n", 1
            )
        if isinstance(op, AddInterface):
            elements = "".join(
                _ELEMENT_XML.format(name=e.name, type=e.type_path) for e in op.elements
            )
            return _INTERFACE_FILE.format(
                package=op.package_path.strip("/"), name=op.name, elements=elements
            )
        raise PortToolError(f"FakeWriter cannot write {op.op}")

    # ---- Writer protocol

    def plan(self, model: Model, config: ProjectConfig, change_set: ChangeSet) -> WritePlan:
        files: dict[str, PlannedFile] = {}
        preview: list[str] = []
        for op in change_set.operations:
            rel, action = self._target(model, config, op)
            files.setdefault(rel, PlannedFile(rel_path=rel, action=action, summary=op.op))
            preview.append(f"{op.op} {op.id} -> {rel}")
        return WritePlan(
            change_set=change_set,
            files=tuple(files.values()),
            preview=tuple(preview),
            findings=self.plan_findings,
        )

    async def apply(
        self,
        plan: WritePlan,
        model: Model,
        config: ProjectConfig,
        journal: Journal,
        ctx: JobContext,
    ) -> WriteOutcome:
        self.apply_calls += 1
        written = self.write_files(plan, model, config, journal, ctx)
        if self.error_finding is not None:
            return WriteOutcome(
                written=tuple(written), applied_operation_ids=(), findings=(self.error_finding,)
            )
        ids = tuple(op.id for op in plan.change_set.operations)
        self.applied.extend(ids)
        return WriteOutcome(written=tuple(written), applied_operation_ids=ids)

    def write_files(
        self,
        plan: WritePlan,
        model: Model,
        config: ProjectConfig,
        journal: Journal | None,
        ctx: JobContext | None,
    ) -> list[str]:
        """The edit itself (also used by the fake fallback import)."""
        by_file: dict[str, list[AnyOperation]] = {}
        actions: dict[str, str] = {}
        for op in plan.change_set.operations:
            rel, action = self._target(model, config, op)
            by_file.setdefault(rel, []).append(op)
            actions[rel] = action
        for swc_path, _port in self.destroy_ports:
            rel = model.components[swc_path].origin.file
            by_file.setdefault(rel, [])
            actions.setdefault(rel, "modify")
        written: list[str] = []
        for rel, ops in by_file.items():
            if ctx is not None:
                ctx.check_cancelled()
            if self.fail_with is not None and len(written) >= self.fail_after_files:
                raise self.fail_with
            path = config.repo_root / rel
            if journal is not None and rel not in self.unjournaled:
                journal.record(rel, actions[rel])
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            for op in ops:
                if op.id not in self.skip_ids:
                    text = self._edit(model, op, text)
            for swc_path, port in self.destroy_ports:
                if model.components[swc_path].origin.file == rel:
                    text = _remove_port(text, port)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
            written.append(rel)
            if self.on_file is not None:
                self.on_file(rel)
        return written

    def build_delta(
        self, model: Model, config: ProjectConfig, change_set: ChangeSet, target: Path
    ) -> tuple[Finding, ...]:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<DELTA>"]
        lines += [f'  <OP ID="{op.id}" KIND="{op.op}"/>' for op in change_set.operations]
        target.write_text("\n".join([*lines, "</DELTA>"]) + "\n", encoding="utf-8")
        self.deltas.append(target)
        return self.delta_findings


def _remove_port(text: str, name: str) -> str:
    pattern = re.compile(
        r" *<([PR]-PORT-PROTOTYPE)[^>]*>\s*<SHORT-NAME>"
        + re.escape(name)
        + r"</SHORT-NAME>.*?</\1>\n",
        re.DOTALL,
    )
    return pattern.sub("", text, count=1)


# --------------------------------------------------------------------------- fake runner


def tool_result(tool: str, ok: bool = True, findings: tuple[Finding, ...] = ()) -> ToolResult:
    return ToolResult(
        tool=tool, ok=ok, exit_code=0 if ok else 1, elapsed_s=0.01, log_file=None, findings=findings
    )


@dataclass
class FakeRunner:
    """A ``DaVinciRunner`` that returns scripted ``ToolResult`` objects and records its calls.

    ``results[name]`` is a list consumed call by call; when it is used up (or missing)
    the call succeeds. ``on_call(name)`` runs before a result is returned.
    """

    results: dict[str, list[ToolResult]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    on_call: Callable[[str], None] | None = None
    on_import: Callable[[Path], None] | None = None
    log_file: Path | None = None
    doctor_findings: tuple[Finding, ...] = ()
    script_requests: list[str] = field(default_factory=list)

    def _next(self, name: str, tool: str, ctx: JobContext) -> ToolResult:
        self.calls.append(name)
        ctx.progress(0, 0, f"[{tool}] fake output of {name}")
        if self.on_call is not None:
            self.on_call(name)
        queue = self.results.get(name)
        result = queue.pop(0) if queue else tool_result(tool)
        if self.log_file is not None:
            self.log_file.write_text(f"log of {name}\nline 2 of {name}\n", encoding="utf-8")
            result = ToolResult(
                tool=result.tool,
                ok=result.ok,
                exit_code=result.exit_code,
                elapsed_s=result.elapsed_s,
                log_file=str(self.log_file),
                findings=result.findings,
            )
        return result

    async def doctor(self, ctx: JobContext) -> tuple[Finding, ...]:
        self.calls.append("doctor")
        return self.doctor_findings

    async def check_workspace(self, ctx: JobContext) -> ToolResult:
        return self._next("check", "DVWspChecker", ctx)

    async def import_arxml(self, arxml: Path, ctx: JobContext) -> ToolResult:
        result = self._next("import", "DVImEx", ctx)
        if result.ok and self.on_import is not None:
            self.on_import(arxml)
        return result

    async def run_script_task(
        self, request_json: Path, result_json: Path, ctx: JobContext
    ) -> ToolResult:
        return self._script(request_json, result_json, ctx)

    def _script(self, request_json: Path, result_json: Path, ctx: JobContext) -> ToolResult:
        self.script_requests.append(request_json.read_text(encoding="utf-8"))
        result = self._next("script", "DVCfgCmd", ctx)
        if result.ok:
            result_json.write_text('{"mapped": [], "failed": []}', encoding="utf-8")
        return result

    async def validate_project(self, ctx: JobContext) -> ToolResult:
        return self._next("validate", "DVCfgCmd", ctx)

    async def generate(self, modules: Sequence[str], ctx: JobContext) -> ToolResult:
        return self._next("generate", "DVCfgCmd", ctx)

    async def generate_contract_headers(
        self, swc_names: Sequence[str], ctx: JobContext
    ) -> ToolResult:
        return self._next("headers", "DVSwcGen", ctx)

    def manual_mapping_instructions(self, mappings: Sequence[Mapping[str, object]]) -> list[str]:
        return [f"FAKE: map {item.get('runnable')} to {item.get('task')}" for item in mappings]


def error_finding(code: str = "FAKE-001", message: str = "fake problem") -> Finding:
    return Finding(code=code, severity=Severity.ERROR, message=message)


# --------------------------------------------------------------------------- service helpers


@dataclass
class Harness:
    repo: Repo
    service: PortToolService
    writer: FakeWriter
    runner: FakeRunner
    journal_dir: Path
    lock_dir: Path

    def run(self, job_id: str, timeout: float = 120.0) -> JobView:
        return self.service.wait(job_id, timeout)

    def dry_run(self, rows: Sequence[RawRequestRow]) -> JobView:
        view = self.run(self.service.start_dry_run_rows(rows))
        assert view.state == "done", view
        return view

    def apply(self) -> JobView:
        return self.run(self.service.start_apply())

    def journal_file(self) -> Path:
        from porttool.core.fsutil import repo_id

        return self.journal_dir / repo_id(self.repo.root) / "apply.journal.jsonl"


def make_service(
    repo: Repo,
    base: Path,
    *,
    writer: FakeWriter | None = None,
    runner: FakeRunner | None = None,
    gui_findings: Callable[[ProjectConfig], Sequence[Finding]] | None = None,
    step_hook: Callable[[str, str], None] | None = None,
    open_repo: bool = True,
) -> Harness:
    writer = writer or FakeWriter()
    runner = runner or FakeRunner()
    used_runner = runner
    service = build_service(
        writer=writer,
        runner_factory=lambda _config: used_runner,
        gui_findings=gui_findings or (lambda _config: ()),
        journal_dir=base / "journals",
        lock_dir=base / "locks",
        step_hook=step_hook,
        git_env=repo.env,
    )
    if open_repo:
        service.open_repo(repo.root)
    return Harness(repo, service, writer, runner, base / "journals", base / "locks")


def wait_until(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition was not met in time")
        time.sleep(0.01)

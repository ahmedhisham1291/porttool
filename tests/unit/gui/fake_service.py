"""An in-memory ``AppService`` for the GUI tests.

Jobs advance on a timer in their own thread. Switches make the next job fail or hang,
make the next service call raise, and flip the stale and recovery flags.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from porttool.core.config import ProjectConfig
from porttool.core.errors import ConfigError
from porttool.core.interfaces import (
    ApplyReport,
    JobView,
    PlannedFile,
    ValidationOutcome,
    WritePlan,
)
from porttool.core.models import (
    AccessKind,
    AccessPoint,
    Component,
    ComponentKind,
    ComponentPrototype,
    Composition,
    Connector,
    ConnectorEnd,
    ConnectorKind,
    DataElement,
    DataType,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    Model,
    Origin,
    Port,
    PortDirection,
    PortInterface,
    Runnable,
    SourceFile,
    Trigger,
    TriggerKind,
)
from porttool.core.operations import AddPort, ChangeSet, Route
from porttool.core.pending import PendingEntry, PendingQueue, PendingState
from porttool.core.results import (
    Finding,
    RawRequestRow,
    RequestResult,
    RequestStatus,
    RowRef,
    Severity,
)
from tests.helpers.sample_model import build_sample_config, build_sample_model

CT = "/ComponentTypes"
SCRIPT_NAME = "<script>window.hacked=1</script>"
LONG_NAME = "Pp" + "VeryLongName" * 25  # 302 characters
ARABIC_NAME = "منفذ_السرعة"
HEBREW_NAME = "יציאה_מהירות"


def build_large_model(swcs: int = 400, ports_per_swc: int = 50, connectors: int = 15000) -> Model:
    """A synthetic model: ``swcs * ports_per_swc`` ports in one composition.

    Each SWC has one P port per five ports and R ports otherwise. R ports are connected
    to P ports of other SWCs until ``connectors`` connectors exist; the rest stays open.
    """
    composition = f"{CT}/BigComposition"
    provided = max(1, ports_per_swc // 5)
    interfaces = [
        PortInterface(
            path=f"/PortInterfaces/If_{n:04d}",
            name=f"If_{n:04d}",
            kind=InterfaceKind.SENDER_RECEIVER,
            elements=(DataElement(name="Value", type_path=f"/DataTypes/Type_{n % 200:03d}"),),
        )
        for n in range(provided * 50)
    ]
    data_types = [
        DataType(
            path=f"/DataTypes/Type_{n:03d}",
            name=f"Type_{n:03d}",
            level=DataTypeLevel.IMPLEMENTATION,
            category=DataTypeCategory.VALUE,
        )
        for n in range(200)
    ]
    components: list[Component] = []
    prototypes: list[ComponentPrototype] = []
    ports: list[Port] = []
    runnables: list[Runnable] = []
    triggers: list[Trigger] = []
    access: list[AccessPoint] = []
    links: list[Connector] = []
    files: list[SourceFile] = []
    for i in range(swcs):
        name = f"Swc_{i:05d}"
        swc_path = f"{CT}/{name}"
        origin = Origin(file=f"Config/Developer/ComponentTypes/{name}.arxml")
        files.append(SourceFile(rel_path=origin.file, size=swcs + i, mtime_ns=ports_per_swc + i))
        components.append(
            Component(path=swc_path, name=name, kind=ComponentKind.APPLICATION, team=f"T{i % 7}")
        )
        prototypes.append(
            ComponentPrototype(
                path=f"{composition}/{name}",
                name=name,
                composition_path=composition,
                type_path=swc_path,
            )
        )
        behavior = f"{swc_path}/{name}_InternalBehavior"
        runnables.append(Runnable(path=f"{behavior}/Run_10ms", name="Run_10ms", swc_path=swc_path))
        triggers.append(
            Trigger(
                path=f"{behavior}/TMT_Run_10ms",
                name="TMT_Run_10ms",
                swc_path=swc_path,
                runnable_path=f"{behavior}/Run_10ms",
                kind=TriggerKind.PERIODIC,
                period_s=0.01,
            )
        )
        for j in range(ports_per_swc):
            is_provider = j < provided
            port_name = f"{'Pp' if is_provider else 'Rp'}_{j:03d}"
            source = i if is_provider else (i + 1 + j // provided) % swcs
            interface = interfaces[(source * provided + j % provided) % len(interfaces)]
            port_path = f"{swc_path}/{port_name}"
            ports.append(
                Port(
                    path=port_path,
                    name=port_name,
                    swc_path=swc_path,
                    direction=PortDirection.P if is_provider else PortDirection.R,
                    interface_path=interface.path,
                    origin=origin,
                )
            )
            if j < 2:
                access.append(
                    AccessPoint(
                        path=f"{behavior}/Run_10ms/AP_{port_name}",
                        swc_path=swc_path,
                        runnable_path=f"{behavior}/Run_10ms",
                        port_path=port_path,
                        target="Value",
                        kind=AccessKind.IMPLICIT_WRITE if is_provider else AccessKind.IMPLICIT_READ,
                    )
                )
            if not is_provider and len(links) < connectors:
                other = f"Swc_{source:05d}"
                other_port = f"Pp_{j % provided:03d}"
                link_name = f"{other}_{other_port}_{name}_{port_name}"
                links.append(
                    Connector(
                        path=f"{composition}/{link_name}",
                        name=link_name,
                        composition_path=composition,
                        kind=ConnectorKind.ASSEMBLY,
                        provider=ConnectorEnd(
                            prototype_path=f"{composition}/{other}",
                            port_path=f"{CT}/{other}/{other_port}",
                        ),
                        requester=ConnectorEnd(
                            prototype_path=f"{composition}/{name}", port_path=port_path
                        ),
                    )
                )
    return Model(
        components=components,
        ports=ports,
        interfaces=interfaces,
        runnables=runnables,
        access_points=access,
        triggers=triggers,
        tasks=("Task_10ms", "Task_Event"),
        compositions=[Composition(path=composition, name="BigComposition")],
        prototypes=prototypes,
        connectors=links,
        data_types=data_types,
        files=files,
        baseline_commit="f" * 40,
    )


def build_tricky_model() -> Model:
    """Ports whose names try to break the page: markup, 300 characters, right-to-left text."""
    swc_path = f"{CT}/SwcTricky"
    names = (SCRIPT_NAME, LONG_NAME, ARABIC_NAME, HEBREW_NAME, "Größe_°C", "a&b\"c'd")
    ports = [
        Port(
            path=f"{swc_path}/{name}",
            name=name,
            swc_path=swc_path,
            direction=PortDirection.P,
            interface_path="/PortInterfaces/<b>IfBold</b>",
        )
        for name in names
    ]
    return Model(
        components=[Component(path=swc_path, name="SwcTricky", kind=ComponentKind.APPLICATION)],
        ports=ports,
        interfaces=[
            PortInterface(
                path="/PortInterfaces/<b>IfBold</b>",
                name="<b>IfBold</b>",
                kind=InterfaceKind.SENDER_RECEIVER,
            )
        ],
        files=[SourceFile(rel_path="tricky.arxml", size=7, mtime_ns=7)],
    )


@dataclasses.dataclass
class _Job:
    id: str
    name: str
    state: str = "queued"
    done: int = 0
    total: int = 0
    message: str = ""
    log: list[str] = dataclasses.field(default_factory=list)
    error: str | None = None
    summary: Mapping[str, object] | None = None
    cancel: threading.Event = dataclasses.field(default_factory=threading.Event)

    def view(self) -> JobView:
        return JobView(
            id=self.id,
            name=self.name,
            state=self.state,
            done=self.done,
            total=self.total,
            message=self.message,
            log_tail=tuple(self.log[-200:]),
            error=self.error,
            summary=self.summary,
        )


class FakeService:
    """Implements ``AppService`` in memory. Every public attribute is a test switch."""

    def __init__(
        self,
        *,
        model: Model | None = None,
        loaded: bool = True,
        repo: Path | None = None,
        steps: int = 3,
        step_s: float = 0.03,
        gui: Mapping[str, object] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._threads: list[threading.Thread] = []
        self._gui: Mapping[str, object] = dict(gui or {"stale_check_interval_s": 0.1})
        self._config: ProjectConfig | None = None
        self._model: Model | None = None
        self._validation: ValidationOutcome | None = None
        self._plan: WritePlan | None = None
        self._report: ApplyReport | None = None
        self.steps = steps
        self.step_s = step_s
        #: Model the next load makes current.
        self.next_model: Model = model if model is not None else build_sample_model()
        self.stale = False
        self.recovery = False
        self.fail_next: str | None = None  # the next job fails with this message
        self.hang_next = False  # the next job runs until it is cancelled
        self.raise_next: BaseException | None = None  # the next service call raises this
        self.stale_error: BaseException | None = None  # model_is_stale raises this
        self.calls: list[str] = []
        self.dry_run_files: list[tuple[str, bytes]] = []  # (file name, content) as received
        self.dry_run_rows: list[RawRequestRow] = []
        self.export_targets: list[Path] = []
        self.report_file = "C:\\ecu\\porttool\\reports\\apply_20261111_120000.html"
        self.closed = False
        if repo is not None or loaded:
            self._config = self._make_config(repo or Path("C:/ecu/repo"))
        if loaded:
            self._model = self.next_model

    def _make_config(self, repo: Path) -> ProjectConfig:
        config = build_sample_config(repo)
        return dataclasses.replace(config, extras=MappingProxyType({"gui": self._gui}))

    # ---- helpers of the tests

    def set_model(self, model: Model | None) -> None:
        """Swap the model as a finished load would."""
        with self._lock:
            self._model = model
            self.stale = False

    def wait_idle(self, timeout: float = 10.0) -> None:
        for thread in list(self._threads):
            thread.join(timeout)

    def _call(self, name: str) -> None:
        with self._lock:
            self.calls.append(name)
            error, self.raise_next = self.raise_next, None
        if error is not None:
            raise error

    def _start(self, name: str, finish: Callable[[_Job], Mapping[str, object]]) -> str:
        self._call(f"start_{name}")
        with self._lock:
            job = _Job(id=f"job-{len(self._jobs) + 1}", name=name, total=self.steps)
            self._jobs[job.id] = job
            fail, self.fail_next = self.fail_next, None
            hang, self.hang_next = self.hang_next, False
        thread = threading.Thread(
            target=self._run, args=(job, finish, fail, hang), name=f"fake-{job.id}", daemon=True
        )
        self._threads.append(thread)
        thread.start()
        return job.id

    def _run(
        self,
        job: _Job,
        finish: Callable[[_Job], Mapping[str, object]],
        fail: str | None,
        hang: bool,
    ) -> None:
        with self._lock:
            job.state = "running"
            job.message = f"{job.name}: starting"
            job.log.append(f"{job.name} started")
        for step in range(1, job.total + 1):
            if job.cancel.wait(self.step_s):
                break
            with self._lock:
                job.done = step
                job.message = f"{job.name}: step {step} of {job.total}"
                job.log.append(f"step {step} of {job.total} finished")
        if hang:
            job.cancel.wait()
        with self._lock:
            if job.cancel.is_set():
                job.state = "cancelled"
                job.message = "cancelled"
                job.log.append("cancelled by the user")
            elif fail is not None:
                job.state = "failed"
                job.error = fail
                job.log.append(f"failed: {fail}")
            else:
                job.summary = finish(job)
                job.state = "done"
                job.message = f"{job.name}: finished"
                job.log.append(f"{job.name} finished")

    # ---- AppService

    def open_repo(self, repo_root: Path, config_file: Path | None = None) -> tuple[Finding, ...]:
        self._call("open_repo")
        if not repo_root.is_dir():
            raise ConfigError(f"The folder {repo_root} does not exist.")
        with self._lock:
            self._config = self._make_config(repo_root)
            self._model = None
            self._validation = None
            self._plan = None
        return (Finding("CFG-I001", Severity.INFO, "porttool.toml was read."),)

    def config(self) -> ProjectConfig | None:
        return self._config

    def model(self) -> Model | None:
        return self._model

    def model_is_stale(self) -> bool:
        if self.stale_error is not None:
            raise self.stale_error
        return self.stale

    def recovery_needed(self) -> bool:
        return self.recovery

    def start_load(self) -> str:
        def finish(job: _Job) -> Mapping[str, object]:
            self._model = self.next_model
            self.stale = False
            return {"components": len(self.next_model.components), "ports": len(self._model.ports)}

        return self._start("load", finish)

    def start_export(self, target: Path) -> str:
        def finish(job: _Job) -> Mapping[str, object]:
            self.export_targets.append(target)
            return {"target": str(target), "rows": len(self.next_model.ports)}

        return self._start("export", finish)

    def start_dry_run(self, workbooks: Sequence[Path]) -> str:
        received = [(path.name, path.read_bytes()) for path in workbooks]
        first = str(workbooks[0]) if workbooks else "workbook.xlsx"

        def finish(job: _Job) -> Mapping[str, object]:
            self.dry_run_files = received
            rows = [
                self._row("Ports", first, 12, {"swc": "SwcA", "port": "PpNew"}),
                self._row("Ports", first, 13, {"swc": "SwcA", "port": "reject me"}),
                self._row("Ports", first, 14, {"swc": "SwcM", "port": "PpModel"}, "model owner"),
            ]
            return self._judge(rows)

        return self._start("dry-run", finish)

    def start_dry_run_rows(self, rows: Sequence[RawRequestRow]) -> str:
        received = list(rows)

        def finish(job: _Job) -> Mapping[str, object]:
            self.dry_run_rows = received
            return self._judge(received)

        return self._start("dry-run", finish)

    @staticmethod
    def _row(
        sheet: str, file: str, number: int, values: Mapping[str, str], comment: str = ""
    ) -> RawRequestRow:
        return RawRequestRow(
            sheet=sheet,
            row=RowRef(file=file, sheet=sheet, row=number),
            row_id=None,
            action="ADD",
            requester="tester",
            comment=comment,
            values=dict(values),
        )

    def _judge(self, rows: Sequence[RawRequestRow]) -> Mapping[str, object]:
        """Canned verdicts: 'reject' in a value rejects, 'model' in the comment waits."""
        results: list[RequestResult] = []
        operations: list[AddPort] = []
        pending: list[PendingEntry] = []
        for index, row in enumerate(rows, start=1):
            request_id = f"REQ-{index:04d}"
            text = " ".join(row.values.values()).lower()
            operation = AddPort(
                id=f"op-{index}",
                swc_path=f"{CT}/{row.values.get('swc') or 'SwcA'}",
                name=row.values.get("port") or row.values.get("name") or f"New{index}",
                direction=PortDirection.P,
                interface_path="/PortInterfaces/IfSpeed",
                requester=row.requester,
            )
            if "reject" in text:
                finding = Finding(
                    "VAL-NAME-001",
                    Severity.ERROR,
                    f"The name '{row.values.get('port', '')}' does not follow the naming rule.",
                    row=row.row,
                )
                results.append(
                    RequestResult(request_id, RequestStatus.REJECTED, row.row, (finding,))
                )
            elif "model" in row.comment.lower():
                finding = Finding(
                    "VAL-ROUTE-001", Severity.INFO, "SwcM is model-owned: sent to team Gamma."
                )
                results.append(
                    RequestResult(
                        request_id, RequestStatus.WAITING_FOR_MODEL_OWNER, row.row, (finding,)
                    )
                )
                waiting = operation.model_copy(update={"route": Route.MODEL})
                pending.append(
                    PendingEntry(
                        operation=waiting,
                        state=PendingState.WAITING_FOR_MODEL_OWNER,
                        created_utc="2026-11-11T12:00:00Z",
                        owner_team="Gamma",
                    )
                )
            else:
                operations.append(operation)
                results.append(
                    RequestResult(
                        request_id, RequestStatus.ACCEPTED, row.row, operation_ids=(operation.id,)
                    )
                )
        change_set = ChangeSet(
            model_hash=self._model.model_hash if self._model is not None else None,
            operations=tuple(operations),
        )
        self._validation = ValidationOutcome(
            change_set=change_set,
            results=tuple(results),
            pending=PendingQueue(entries=tuple(pending)),
            findings=(Finding("VAL-INFO-001", Severity.INFO, "No conflicting requests."),),
        )
        self._plan = WritePlan(
            change_set=change_set,
            files=tuple(
                PlannedFile(
                    rel_path="Config/Developer/ComponentTypes/SwcA.arxml",
                    action="modify",
                    summary=f"{len(operations)} port(s) added",
                )
                for _ in range(1 if operations else 0)
            ),
            preview=tuple(f"add port {op.name} to {op.swc_path}" for op in operations),
        )
        return {"accepted": len(operations), "requests": len(results)}

    def last_validation(self) -> ValidationOutcome | None:
        return self._validation

    def last_plan(self) -> WritePlan | None:
        return self._plan

    def start_apply(self) -> str:
        validation = self._validation

        def finish(job: _Job) -> Mapping[str, object]:
            results = tuple(
                dataclasses.replace(result, status=RequestStatus.APPLIED)
                if result.status is RequestStatus.ACCEPTED
                else result
                for result in (validation.results if validation is not None else ())
            )
            self._report = ApplyReport(
                ok=True,
                branch="porttool/apply-20261111-120000",
                results=results,
                findings=(),
                report_file=self.report_file,
            )
            return {"branch": self._report.branch, "report_file": self.report_file}

        return self._start("apply", finish)

    def last_report(self) -> ApplyReport | None:
        return self._report

    def start_rollback(self) -> str:
        def finish(job: _Job) -> Mapping[str, object]:
            self.recovery = False
            self._report = ApplyReport(
                ok=True, branch=None, results=(), findings=(), rolled_back=True
            )
            return {"rolled_back": True}

        return self._start("rollback", finish)

    def start_doctor(self) -> str:
        return self._start("doctor", lambda job: {"findings": 0, "tools": "UNVERIFIED"})

    def job(self, job_id: str) -> JobView | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.view() if job is not None else None

    def jobs(self) -> tuple[JobView, ...]:
        with self._lock:
            return tuple(job.view() for job in self._jobs.values())

    def cancel(self, job_id: str) -> bool:
        self._call("cancel")
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state not in ("queued", "running") or job.cancel.is_set():
                return False
            job.cancel.set()
            return True

    def close(self) -> None:
        with self._lock:
            self.closed = True
            for job in self._jobs.values():
                job.cancel.set()

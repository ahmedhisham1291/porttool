"""One computation of a change set: resolve every target and edit the files in memory.

A ``Session`` reads the files it needs, checks them against the fingerprints of the
model, and hangs the new elements into the documents. It writes nothing. ``plan``,
``apply`` and ``build_delta`` all run the same session code, so the dry run shows
exactly what the apply does.
"""

from __future__ import annotations

import hashlib
import uuid as uuidlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from porttool.core import fsutil
from porttool.core.config import ProjectConfig
from porttool.core.errors import RepoStateError
from porttool.core.models import (
    AccessKind,
    ArxmlOwner,
    Component,
    DataType,
    InterfaceKind,
    Model,
    Port,
    PortDirection,
    PortInterface,
    Runnable,
    SourceFile,
    TriggerKind,
)
from porttool.core.operations import (
    APPLY_ORDER,
    AddAccessPoint,
    AddConnector,
    AddPort,
    AddServerRunnable,
    AddTrigger,
    AnyOperation,
)
from porttool.core.results import Finding, RowRef, Severity
from porttool.writer import codes, order, templates
from porttool.writer.settings import WriterSettings
from porttool.writer.xmltext import Document, Node, XmlIndexError, element

#: Container of a runnable and element kind per access kind.
_ACCESS: dict[AccessKind, tuple[str, str]] = {
    AccessKind.IMPLICIT_READ: ("DATA-READ-ACCESSS", "VARIABLE-ACCESS"),
    AccessKind.IMPLICIT_WRITE: ("DATA-WRITE-ACCESSS", "VARIABLE-ACCESS"),
    AccessKind.EXPLICIT_SEND: ("DATA-SEND-POINTS", "VARIABLE-ACCESS"),
    AccessKind.EXPLICIT_READ: ("DATA-RECEIVE-POINT-BY-ARGUMENTS", "VARIABLE-ACCESS"),
    AccessKind.QUEUED_RECEIVE: ("DATA-RECEIVE-POINT-BY-ARGUMENTS", "VARIABLE-ACCESS"),
    AccessKind.SYNC_SERVER_CALL: ("SERVER-CALL-POINTS", "SYNCHRONOUS-SERVER-CALL-POINT"),
    AccessKind.ASYNC_SERVER_CALL: ("SERVER-CALL-POINTS", "ASYNCHRONOUS-SERVER-CALL-POINT"),
}
_SENDING = (AccessKind.IMPLICIT_WRITE, AccessKind.EXPLICIT_SEND)
_CALLING = (AccessKind.SYNC_SERVER_CALL, AccessKind.ASYNC_SERVER_CALL)
_IMPLICIT = (AccessKind.IMPLICIT_READ, AccessKind.IMPLICIT_WRITE)
_EVENTS: dict[TriggerKind, str] = {
    TriggerKind.PERIODIC: "TIMING-EVENT",
    TriggerKind.DATA_RECEIVED: "DATA-RECEIVED-EVENT",
    TriggerKind.DATA_RECEIVE_ERROR: "DATA-RECEIVE-ERROR-EVENT",
    TriggerKind.OPERATION_INVOKED: "OPERATION-INVOKED-EVENT",
    TriggerKind.INIT: "INIT-EVENT",
}

PLANNED = "planned"
EXISTS = "exists"
FAILED = "failed"


class Skip(Exception):
    """The operation cannot be planned. Becomes an error finding; nothing was changed."""

    def __init__(
        self, code: str, message: str, object_path: str | None = None, file: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.object_path = object_path
        self.file = file


class AlreadyThere(Exception):
    """The requested object exists as requested. Becomes an info finding."""

    def __init__(self, message: str, object_path: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.object_path = object_path


@dataclass(slots=True)
class OpState:
    op: AnyOperation
    status: str = FAILED
    files: set[str] = field(default_factory=set)
    preview: str = ""
    infos: list[Finding] = field(default_factory=list)  # reported only if the op is written


def row_of(op: AnyOperation) -> RowRef | None:
    if op.source_file is None or op.source_sheet is None or op.source_row is None:
        return None
    return RowRef(op.source_file, op.source_sheet, op.source_row)


def op_finding(
    op: AnyOperation,
    code: str,
    severity: Severity,
    message: str,
    object_path: str | None = None,
    file: str | None = None,
) -> Finding:
    """Finding about one operation. The message always starts with ``[op <id>]``."""
    return Finding(code, severity, f"[op {op.id}] {message}", object_path, file, row_of(op))


def _last(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


class Session:
    def __init__(
        self,
        model: Model,
        config: ProjectConfig,
        settings: WriterSettings,
        *,
        strict: bool,
        delta: bool = False,
        uuid_factory: Callable[[], str] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> None:
        self.model = model
        self.config = config
        self.settings = settings
        self.root = Path(config.repo_root)
        #: apply: a changed file raises RepoStateError. dry run: it is a finding.
        self.strict = strict
        #: delta: owners are not checked (the delta goes to the owner of the model).
        self.delta = delta
        self._new_uuid = uuid_factory or (lambda: str(uuidlib.uuid4()))
        self._check_cancelled = check_cancelled or (lambda: None)
        self.docs: dict[str, Document] = {}
        #: stat of each file at the moment it was read, to detect a change before the write
        self.read_stats: dict[str, SourceFile] = {}
        self._bad: dict[str, Skip] = {}
        self._reset_state()

    def _reset_state(self) -> None:
        self.states: dict[str, OpState] = {}
        self.findings: list[Finding] = []
        #: per file: what was added, for the one-line summary of the plan
        self.notes: dict[str, list[str]] = {}
        #: per file: the package elements (or new elements) that were changed, for the delta
        self.touched: dict[str, list[Node]] = {}
        self._new_ports: dict[tuple[str, str], Port] = {}
        self._new_interfaces: dict[str, PortInterface] = {}
        self._new_runnables: dict[tuple[str, str], Runnable] = {}
        self._new_types: dict[str, DataType] = {}
        self._new_accesses: set[tuple[str, str, str, str]] = set()
        self._new_triggers: set[tuple[str, str, str | None, str | None, float | None]] = set()
        self._parts_cache: dict[str, list[tuple[Document, Node]]] = {}
        self._uuid_cache: dict[int, Callable[[], str | None]] = {}

    def reset(self) -> None:
        """Forget every edit, keep the files that were read."""
        for rel_path in [rel for rel, doc in self.docs.items() if doc.created]:
            del self.docs[rel_path]
        for doc in self.docs.values():
            doc.reset()
        self._reset_state()

    # ---- running

    def run(self, operations: Sequence[AnyOperation]) -> None:
        ranked = sorted(operations, key=lambda op: APPLY_ORDER.index(op.op))
        for op in ranked:
            self._check_cancelled()
            state = OpState(op)
            self.states[op.id] = state
            handler = _HANDLERS.get(op.op)
            try:
                if handler is None:
                    raise Skip(codes.UNSUPPORTED, f"'{op.op}' is not supported yet.")
                handler(self, op, state)
                state.status = PLANNED
            except AlreadyThere as there:
                state.status = EXISTS
                self.findings.append(
                    op_finding(
                        op, codes.INFO_EXISTS, Severity.INFO, there.message, there.object_path
                    )
                )
            except Skip as skip:
                state.status = FAILED
                self.findings.append(
                    op_finding(
                        op, skip.code, Severity.ERROR, skip.message, skip.object_path, skip.file
                    )
                )

    def changed_docs(self) -> list[Document]:
        return [self.docs[rel] for rel in sorted(self.docs) if self.docs[rel].changed]

    def warn(self, op: AnyOperation, code: str, message: str, path: str | None = None) -> None:
        self.findings.append(op_finding(op, code, Severity.WARNING, message, path))

    # ---- files

    def doc(self, rel_path: str) -> Document:
        """The indexed file. Raises ``Skip`` (or ``RepoStateError`` in an apply)."""
        document = self.docs.get(rel_path)
        if document is not None:
            return document
        bad = self._bad.get(rel_path)
        if bad is not None:
            raise bad
        try:
            document = self._load(rel_path)
        except Skip as skip:
            self._bad[rel_path] = skip
            raise
        self.docs[rel_path] = document
        return document

    def _state_problem(self, code: str, rel_path: str, message: str) -> Skip:
        if self.strict:
            raise RepoStateError(message, detail=rel_path)
        return Skip(code, message, file=rel_path)

    def _load(self, rel_path: str) -> Document:
        source = self.model.files.get(rel_path)
        path = self.root / rel_path
        if source is None:
            raise self._state_problem(
                codes.STATE_UNKNOWN,
                rel_path,
                f"{rel_path} is not part of the loaded model. Reload the repository.",
            )
        try:
            stat = fsutil.fingerprint(path, self.root)
            with open(fsutil.long_path(path), "rb") as stream:
                data = stream.read()
        except OSError as error:
            raise self._state_problem(
                codes.STATE_UNKNOWN,
                rel_path,
                f"{rel_path} cannot be read ({error.strerror or error}). Reload the repository.",
            ) from error
        if source.sha256 is not None:
            same = len(data) == source.size and hashlib.sha256(data).hexdigest() == source.sha256
        else:
            same = (stat.size, stat.mtime_ns) == (source.size, source.mtime_ns)
        if not same:
            raise self._state_problem(
                codes.STATE_CHANGED,
                rel_path,
                f"{rel_path} changed since the repository was loaded. Reload and try again.",
            )
        try:
            document = Document.from_bytes(rel_path, data)
        except XmlIndexError as error:
            message = f"{rel_path} cannot be edited: {error}"
            raise Skip(codes.PARSE, message, file=rel_path) from None
        self.read_stats[rel_path] = stat
        return document

    # ---- lookups: the model plus what this change set creates

    def interface(self, path: str) -> PortInterface:
        found = self._new_interfaces.get(path) or self.model.interfaces.get(path)
        if found is None:
            raise Skip(codes.REF_MISSING, f"Port interface {path} does not exist.", path)
        return found

    def port(self, swc_path: str, name: str) -> Port | None:
        return self._new_ports.get((swc_path, name)) or self.model.find_port(swc_path, name)

    def need_port(self, swc_path: str, name: str) -> Port:
        found = self.port(swc_path, name)
        if found is None:
            raise Skip(
                codes.REF_MISSING,
                f"Port {name} does not exist in {_last(swc_path)}.",
                f"{swc_path}/{name}",
            )
        return found

    def runnable(self, swc_path: str, name: str) -> Runnable:
        found = self._new_runnables.get((swc_path, name)) or self.model.find_runnable(
            swc_path, name
        )
        if found is None:
            raise Skip(codes.REF_MISSING, f"{_last(swc_path)} has no runnable {name}.", swc_path)
        return found

    def port_with(
        self, swc_path: str, port_name: str, kind: InterfaceKind, target: str, *, provided: bool
    ) -> tuple[Port, PortInterface]:
        """The port, checked: interface kind, direction, and that ``target`` is in the interface."""
        port = self.need_port(swc_path, port_name)
        if port.interface_path is None:
            raise Skip(codes.REF_MISSING, f"Port {port_name} has no interface.", port.path)
        interface = self.interface(port.interface_path)
        wrong = PortDirection.R if provided else PortDirection.P
        if interface.kind is not kind or port.direction is wrong:
            side = "provided" if provided else "required"
            raise Skip(
                codes.REF_KIND,
                f"Port {port_name} ({port.direction.value}, {interface.kind.value}) cannot be "
                f"used here: a {side} {kind.value} port is needed.",
                port.path,
            )
        if kind is InterfaceKind.CLIENT_SERVER:
            known = interface.operation(target) is not None
        else:
            known = interface.element(target) is not None
        if not known:
            what = "operation" if kind is InterfaceKind.CLIENT_SERVER else "data element"
            raise Skip(
                codes.REF_MISSING,
                f"{interface.name} has no {what} {target}.",
                f"{interface.path}/{target}",
            )
        return port, interface

    def component(self, path: str) -> Component:
        found = self.model.components.get(path)
        if found is None:
            raise Skip(codes.REF_MISSING, f"Component type {path} does not exist.", path)
        if not self.delta:
            owners = {found.owner, self.config.owner_of(found.name)}
            for owner in (ArxmlOwner.LOCKED, ArxmlOwner.MODEL):
                if owner in owners:
                    reason = "locked" if owner is ArxmlOwner.LOCKED else "model-owned"
                    raise Skip(
                        codes.OWNER,
                        f"{found.name} is {reason}: PortTool never writes into it.",
                        path,
                    )
        return found

    def is_constant(self, path: str) -> bool:
        return path in self.model.constants

    # ---- locating elements in the files

    def parts(self, path: str) -> list[tuple[Document, Node]]:
        """Every (file, element) that defines the component type ``path`` (split types: several)."""
        cached = self._parts_cache.get(path)
        if cached is not None:
            return cached
        model = self.model
        owner = model.components.get(path) or model.compositions.get(path)
        files = _unique(
            [
                owner.origin.file if owner is not None else "",
                *(item.origin.file for item in model.ports_of(path)),
                *(item.origin.file for item in model.runnables_of(path)),
                *(item.origin.file for item in model.triggers_in(path)),
                *(item.origin.file for item in model.prototypes_in(path)),
                *(item.origin.file for item in model.connectors_in(path)),
            ]
        )
        found: list[tuple[Document, Node]] = []
        for rel_path in files:
            document = self.doc(rel_path)
            node = document.find(path)
            if node is not None:
                found.append((document, node))
        if not found:
            found = self._scan(path, files)
        if not found:
            raise Skip(codes.LOCATE, f"{path} was not found in the files of the model.", path)
        self._parts_cache[path] = found
        return found

    def _scan(self, path: str, tried: Sequence[str]) -> list[tuple[Document, Node]]:
        """Look for ``path`` in every other ARXML file of the model (rare fallback)."""
        name = _last(path)
        needles = (name.encode("utf-8"), name.encode("utf-16-le"), name.encode("utf-16-be"))
        found: list[tuple[Document, Node]] = []
        for rel_path in sorted(self.model.files):
            if rel_path in tried or not rel_path.lower().endswith(".arxml"):
                continue
            self._check_cancelled()
            if rel_path not in self.docs:
                try:
                    with open(fsutil.long_path(self.root / rel_path), "rb") as stream:
                        data = stream.read()
                except OSError:
                    continue  # judged by doc() if the file turns out to be needed
                if not any(needle in data for needle in needles):
                    continue
            try:
                document = self.doc(rel_path)
            except Skip:
                continue  # a file that cannot be read cannot hold the target of this edit
            node = document.find(path)
            if node is not None:
                found.append((document, node))
        return found

    def behavior(self, swc: Component) -> tuple[Document, Node, Node]:
        """(file, component element, SWC-INTERNAL-BEHAVIOR element) of ``swc``."""
        wanted = _last(swc.behavior_path) if swc.behavior_path else None
        parts = self.parts(swc.path)
        candidates = parts
        for attempt in range(2):
            if attempt == 1:
                if swc.behavior_path is None:
                    break
                # The model knows a behavior, but not in which file of a split component.
                candidates = self._scan(swc.path, [document.rel_path for document, _ in parts])
                parts.extend(candidates)
            for document, node in candidates:
                holder = node.child("INTERNAL-BEHAVIORS")
                if holder is None:
                    continue
                for child in holder.children:
                    if child.tag == "SWC-INTERNAL-BEHAVIOR" and (
                        wanted is None or document.name_of(child) == wanted
                    ):
                        return document, node, child
        raise Skip(
            codes.NO_BEHAVIOR,
            f"{swc.name} has no internal behavior. Create it in DaVinci Developer first.",
            swc.path,
        )

    @staticmethod
    def free_name(document: Document, scope: Node, base: str) -> str:
        """``base``, or ``base_1``, ``base_2``, ... if the name is taken inside ``scope``."""
        taken = {document.name_of(node) for node in document.identifiables(scope)}
        name = base
        count = 0
        while name in taken:
            count += 1
            name = f"{base}_{count}"
        return name

    @staticmethod
    def pick(parts: Sequence[tuple[Document, Node]], *tags: str) -> tuple[Document, Node]:
        """The part that already has one of the containers ``tags``; else the first part."""
        for tag in tags:
            for document, node in parts:
                if node.child(tag) is not None:
                    return document, node
        return parts[0]

    def ensure(self, op: AnyOperation, document: Document, parent: Node, tag: str) -> Node:
        """The container ``tag`` of ``parent``; created at its schema position if missing."""
        existing = parent.child(tag)
        if existing is not None:
            return existing
        index, certain = order.position(parent.tag, tag, [child.tag for child in parent.children])
        if not certain:
            self.warn(
                op,
                codes.ORDER_UNKNOWN,
                f"The position of the new <{tag}> inside <{parent.tag}> of "
                f"{document.name_of(parent) or parent.tag} is uncertain: check "
                f"{document.rel_path}.",
            )
        node = element(tag)
        document.insert(parent, node, index)
        return node

    def check_free(self, document: Document, scope: Node, name: str, path: str) -> None:
        if document.named_child(scope, name) is not None:
            raise Skip(
                codes.NAME_USED,
                f"The name {name} is already used inside {document.name_of(scope)} "
                f"in {document.rel_path}.",
                path,
                document.rel_path,
            )

    def uuid_maker(self, document: Document, container: Node) -> Callable[[], str | None]:
        """UUIDs for new children of ``container``, in the style of its existing children."""
        cached = self._uuid_cache.get(id(container))
        if cached is not None:
            return cached
        mode = self.settings.uuid
        maker: Callable[[], str | None]
        if mode == "auto":
            originals = [child for child in container.children if not child.virtual]
            if originals:
                sample = document.sample_uuid(originals[:5] + originals[-5:])
            else:
                sample = document.any_uuid()
            mode = "none" if sample is None else "lower" if sample == sample.lower() else "upper"
        new = self._new_uuid
        if mode == "none":
            maker = _no_uuid
        elif mode == "upper":
            maker = lambda: new().upper()  # noqa: E731
        else:
            maker = lambda: new().lower()  # noqa: E731
        self._uuid_cache[id(container)] = maker
        return maker

    def touch(self, state: OpState, document: Document, top: Node, note: str) -> None:
        """Remember that ``top`` (an element of a package) was changed for ``state``."""
        rel_path = document.rel_path
        state.files.add(rel_path)
        self.notes.setdefault(rel_path, []).append(note)
        tops = self.touched.setdefault(rel_path, [])
        if top not in tops:
            tops.append(top)

    # ---- operations

    def add_port(self, op: AddPort, state: OpState) -> None:
        swc = self.component(op.swc_path)
        path = f"{op.swc_path}/{op.name}"
        existing = self.port(op.swc_path, op.name)
        if existing is not None:
            if (existing.direction, existing.interface_path) == (op.direction, op.interface_path):
                raise AlreadyThere(f"Port {op.name} of {swc.name} already exists.", path)
            raise Skip(
                codes.NAME_USED,
                f"{swc.name} already has a port {op.name} with another direction or interface.",
                path,
            )
        interface = self.interface(op.interface_path)
        inits: dict[str, str | None] = {}
        for init in op.init_values:
            data = interface.element(init.element)
            if interface.kind is not InterfaceKind.SENDER_RECEIVER or data is None:
                raise Skip(
                    codes.REF_MISSING,
                    f"Init value for {init.element}: {interface.name} has no such data element.",
                    path,
                )
            if data.is_queued and init.value is not None:
                self.warn(
                    op,
                    codes.VALUE_IGNORED,
                    f"Init value of {init.element} ignored: a queued element has none.",
                    path,
                )
                continue
            inits[init.element] = init.value
        document, node = self.pick(self.parts(op.swc_path), "PORTS")
        self.check_free(document, node, op.name, path)

        holder = self.ensure(op, document, node, "PORTS")
        new = templates.port(
            op.name,
            op.direction,
            interface,
            inits,
            self.settings,
            self.uuid_maker(document, holder),
            self.is_constant,
        )
        document.insert(holder, new)
        self._new_ports[(op.swc_path, op.name)] = Port(
            path=path,
            name=op.name,
            swc_path=op.swc_path,
            direction=op.direction,
            interface_path=op.interface_path,
        )
        self.touch(state, document, node, f"port {op.name}")
        shown = [f"{name}={value}" for name, value in inits.items() if value is not None]
        state.preview = (
            f"{swc.name}: add {op.direction.value}-port {op.name} ({interface.name})"
            + (", init " + ", ".join(shown) if shown else "")
        )

    def add_connector(self, op: AddConnector, state: OpState) -> None:
        composition = self.component(op.composition_path)
        path = f"{op.composition_path}/{op.name}"
        ends: list[tuple[str, Port]] = []
        for prototype_name, port_name in (
            (op.provider_prototype, op.provider_port),
            (op.requester_prototype, op.requester_port),
        ):
            prototype = self.model.find_prototype(op.composition_path, prototype_name)
            if prototype is None or prototype.type_path is None:
                raise Skip(
                    codes.REF_MISSING,
                    f"{composition.name} has no component prototype {prototype_name}.",
                    f"{op.composition_path}/{prototype_name}",
                )
            ends.append((prototype.path, self.need_port(prototype.type_path, port_name)))
        (provider_prototype, provider), (requester_prototype, requester) = ends
        if provider.direction is PortDirection.R or requester.direction is PortDirection.P:
            raise Skip(
                codes.REF_KIND,
                f"Connector {op.name}: the provider must be a P or PR port and the requester "
                "an R or PR port.",
                path,
            )
        existing = self.model.connectors.get(path)
        if existing is not None:
            same = (
                existing.provider.prototype_path == provider_prototype
                and existing.provider.port_path == provider.path
                and existing.requester.prototype_path == requester_prototype
                and existing.requester.port_path == requester.path
            )
            if same:
                raise AlreadyThere(f"Connector {op.name} already exists.", path)
            raise Skip(
                codes.NAME_USED,
                f"{composition.name} already has a connector {op.name} with other ends.",
                path,
            )
        document, node = self.pick(self.parts(op.composition_path), "CONNECTORS", "COMPONENTS")
        self.check_free(document, node, op.name, path)

        holder = self.ensure(op, document, node, "CONNECTORS")
        new = templates.connector(
            op.name,
            provider_prototype,
            provider,
            requester_prototype,
            requester,
            self.uuid_maker(document, holder),
        )
        document.insert(holder, new)
        self.touch(state, document, node, f"connector {op.name}")
        state.preview = (
            f"{composition.name}: connect {op.provider_prototype}.{op.provider_port} -> "
            f"{op.requester_prototype}.{op.requester_port} ({op.name})"
        )

    def add_server_runnable(self, op: AddServerRunnable, state: OpState) -> None:
        swc = self.component(op.swc_path)
        existing = self._new_runnables.get((op.swc_path, op.name)) or self.model.find_runnable(
            op.swc_path, op.name
        )
        if existing is not None:
            if existing.symbol == op.symbol:
                raise AlreadyThere(
                    f"Runnable {op.name} of {swc.name} already exists.", existing.path
                )
            raise Skip(
                codes.NAME_USED,
                f"{swc.name} already has a runnable {op.name} with another symbol.",
                existing.path,
            )
        self.port_with(
            op.swc_path, op.port_name, InterfaceKind.CLIENT_SERVER, op.operation, provided=True
        )
        document, top, behavior = self.behavior(swc)
        path = f"{op.swc_path}/{document.name_of(behavior)}/{op.name}"
        self.check_free(document, behavior, op.name, path)

        holder = self.ensure(op, document, behavior, "RUNNABLES")
        document.insert(
            holder, templates.runnable(op.name, op.symbol, self.uuid_maker(document, holder))
        )
        self._new_runnables[(op.swc_path, op.name)] = Runnable(
            path=path, name=op.name, swc_path=op.swc_path, symbol=op.symbol
        )
        self.touch(state, document, top, f"runnable {op.name}")
        state.preview = (
            f"{swc.name}: add server runnable {op.name} (symbol {op.symbol}) "
            f"for {op.port_name}.{op.operation}"
        )

    def add_access_point(self, op: AddAccessPoint, state: OpState) -> None:
        swc = self.component(op.swc_path)
        shape = _ACCESS.get(op.kind)
        if shape is None:
            raise Skip(codes.UNSUPPORTED, f"Access kind '{op.kind.value}' is not supported yet.")
        container, tag = shape
        runnable = self.runnable(op.swc_path, op.runnable_name)
        calling = op.kind in _CALLING
        port, interface = self.port_with(
            op.swc_path,
            op.port_name,
            InterfaceKind.CLIENT_SERVER if calling else InterfaceKind.SENDER_RECEIVER,
            op.target,
            provided=op.kind in _SENDING,
        )
        if not calling:
            data = interface.element(op.target)
            queued = data is not None and data.is_queued
            if queued and op.kind in _IMPLICIT:
                raise Skip(
                    codes.REF_KIND,
                    f"{op.target} is a queued element: it cannot be accessed implicitly.",
                    port.path,
                )
            if op.kind is AccessKind.QUEUED_RECEIVE and not queued:
                raise Skip(
                    codes.REF_KIND,
                    f"{op.target} is not a queued element: use 'explicit read'.",
                    port.path,
                )
        key = (runnable.path, port.path, op.target, container + tag)
        there = key in self._new_accesses or any(
            access.port_path == port.path
            and access.target == op.target
            and _ACCESS.get(access.kind) == shape
            for access in self.model.access_points_of_runnable(runnable.path)
        )
        if there:
            raise AlreadyThere(
                f"{runnable.name} already has the access '{op.kind.value}' to "
                f"{op.port_name}.{op.target}.",
                runnable.path,
            )
        document, top, behavior = self.behavior(swc)
        node = document.named_child(behavior, op.runnable_name)
        if node is None or node.tag != "RUNNABLE-ENTITY":
            raise Skip(
                codes.LOCATE,
                f"Runnable {op.runnable_name} was not found in {document.rel_path}.",
                runnable.path,
                document.rel_path,
            )
        name = self.free_name(
            document,
            node,
            self.settings.access_point_pattern.format(
                prefix=self.settings.access_point_prefixes.get(op.kind.value, "AP"),
                runnable=op.runnable_name,
                port=op.port_name,
                target=op.target,
            ),
        )

        holder = self.ensure(op, document, node, container)
        uuid = self.uuid_maker(document, holder)
        target_path = f"{interface.path}/{op.target}"
        if calling:
            new = templates.server_call_point(
                tag, name, port, target_path, self.settings.server_call_timeout, uuid
            )
        else:
            new = templates.variable_access(name, port, target_path, uuid)
        document.insert(holder, new)
        self._new_accesses.add(key)
        self.touch(state, document, top, f"access {name}")
        state.preview = (
            f"{swc.name}: {op.runnable_name} gets {op.kind.value} of "
            f"{op.port_name}.{op.target} ({name})"
        )

    def add_trigger(self, op: AddTrigger, state: OpState) -> None:
        swc = self.component(op.swc_path)
        tag = _EVENTS.get(op.kind)
        if tag is None:
            raise Skip(codes.UNSUPPORTED, f"Trigger kind '{op.kind.value}' is not supported yet.")
        runnable = self.runnable(op.swc_path, op.runnable_name)
        port: Port | None = None
        target: str | None = None
        period: float | None = None
        if op.kind is TriggerKind.PERIODIC:
            if op.period_s is None or op.period_s <= 0:
                raise Skip(codes.INCOMPLETE, "A periodic trigger needs a period above 0 s.")
            period = op.period_s
        elif op.kind is not TriggerKind.INIT:
            if op.port_name is None or op.target is None:
                raise Skip(codes.INCOMPLETE, f"A trigger '{op.kind.value}' needs port and target.")
            served = op.kind is TriggerKind.OPERATION_INVOKED
            target = op.target
            port, interface = self.port_with(
                op.swc_path,
                op.port_name,
                InterfaceKind.CLIENT_SERVER if served else InterfaceKind.SENDER_RECEIVER,
                target,
                provided=served,
            )
        port_path = port.path if port is not None else None
        key = (runnable.path, tag, port_path, target, period)
        there = key in self._new_triggers or any(
            trigger.kind is op.kind
            and trigger.port_path == port_path
            and trigger.target == target
            and _same_period(trigger.period_s, period)
            for trigger in self.model.triggers_of(runnable.path)
        )
        if there:
            raise AlreadyThere(
                f"{runnable.name} already has this '{op.kind.value}' trigger.", runnable.path
            )
        document, top, behavior = self.behavior(swc)
        pattern = self.settings.event_patterns.get(op.kind.value, "EV_{runnable}")
        name = self.free_name(
            document,
            behavior,
            pattern.format(runnable=op.runnable_name, port=op.port_name, target=op.target),
        )
        path = f"{op.swc_path}/{document.name_of(behavior)}/{name}"

        holder = self.ensure(op, document, behavior, "EVENTS")
        uuid = self.uuid_maker(document, holder)
        details: Node | None = None
        if period is not None:
            details = element("PERIOD", text=templates.number(period))
        elif port is not None and target is not None:
            target_path = f"{interface.path}/{target}"
            if op.kind is TriggerKind.OPERATION_INVOKED:
                details = templates.operation_iref(port, target_path)
            else:
                details = templates.data_iref(port, target_path)
        document.insert(holder, templates.event(tag, name, runnable.path, uuid, details))
        self._new_triggers.add(key)
        self.touch(state, document, top, f"event {name}")
        state.infos.append(
            op_finding(
                op,
                codes.INFO_EVENT,
                Severity.INFO,
                f"Created {tag} {path} for runnable {runnable.name}; requested task: "
                f"{op.task or 'none'}; after event: {op.position_after_event or 'none'}.",
                path,
                document.rel_path,
            )
        )
        state.preview = (
            f"{swc.name}: {op.runnable_name} gets trigger '{op.kind.value}' ({name})"
            + (f", period {templates.number(period)} s" if period is not None else "")
            + (f", {op.port_name}.{target}" if port is not None else "")
            + (f", task {op.task}" if op.task else "")
        )


def _same_period(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is right
    return abs(left - right) <= 1e-9 * max(abs(left), abs(right), 1.0)


def _no_uuid() -> str | None:
    return None


_HANDLERS: dict[str, Callable[[Session, AnyOperation, OpState], None]] = {
    "add_port": Session.add_port,  # type: ignore[dict-item]
    "add_connector": Session.add_connector,  # type: ignore[dict-item]
    "add_server_runnable": Session.add_server_runnable,  # type: ignore[dict-item]
    "add_access_point": Session.add_access_point,  # type: ignore[dict-item]
    "add_trigger": Session.add_trigger,  # type: ignore[dict-item]
}

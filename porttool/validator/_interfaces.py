"""Interfaces and InterfaceElements sheets: one ``AddInterface`` per new interface. Internal."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from porttool.core.models import (
    Argument,
    ArgumentDirection,
    DataElement,
    InterfaceKind,
    Operation,
    PortInterface,
)
from porttool.core.operations import (
    AddInterface,
    ArgumentSpec,
    ElementSpec,
    OperationSpec,
    Route,
)
from porttool.core.sheets import Sheet
from porttool.validator import _ids, codes
from porttool.validator._common import (
    bad_cell,
    blank_required,
    header,
    list_rows,
    reject_case_clashes,
    require,
)
from porttool.validator._state import Req, short_file
from porttool.validator._world import World, scope_clash
from porttool.validator.cells import parse_argument_direction, parse_flag, parse_interface_kind
from porttool.validator.names import NameKind

_HEAD = Sheet.INTERFACES
_ROWS = Sheet.INTERFACE_ELEMENTS

_CREATABLE = (InterfaceKind.SENDER_RECEIVER, InterfaceKind.CLIENT_SERVER, InterfaceKind.MODE_SWITCH)


@dataclass(slots=True)
class _Group:
    """All rows of the import that describe one interface that does not exist yet."""

    name: str
    path: str
    headers: list[tuple[Req, InterfaceKind | None]] = field(default_factory=list)
    rows: list[Req] = field(default_factory=list)


@dataclass(slots=True)
class _Item:
    """One InterfaceElements row, read for the kind of its interface."""

    req: Req
    element: str
    argument: str = ""
    type_path: str | None = None
    queued: bool = False
    direction: ArgumentDirection | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.element, self.argument)

    @property
    def signature(self) -> tuple[object, ...]:
        return (self.type_path, self.queued, self.direction)


# ---------------------------------------------------------------------- existing interfaces


def _quiet_type(world: World, text: str) -> str | None:
    if text.startswith("/"):
        return text if world.type_exists(text) else None
    paths = world.types_by_name.get(text, [])
    return paths[0] if len(paths) == 1 else None


def _fulfilled_by(world: World, req: Req, interface: PortInterface) -> bool:
    """True when the existing interface already has what the element row asks for."""
    element = req.cell("element")
    argument = req.cell("argument")
    type_text = req.cell("data_type")
    type_path = _quiet_type(world, type_text) if type_text else None
    if type_text and type_path is None:
        return False
    if interface.kind is InterfaceKind.CLIENT_SERVER:
        operation = interface.operation(element)
        if operation is None:
            return False
        if not argument:
            return not type_text and not req.cell("direction")
        found = next((a for a in operation.arguments if a.name == argument), None)
        direction = parse_argument_direction(req.cell("direction"))
        return (
            found is not None
            and (not req.cell("direction") or found.direction is direction)
            and (type_path is None or found.type_path == type_path)
        )
    if argument or req.cell("direction"):
        return False
    if interface.kind is InterfaceKind.MODE_SWITCH and not interface.elements:
        return bool(type_text) and type_text in (
            interface.mode_group_path,
            (interface.mode_group_path or "").rsplit("/", 1)[-1],
        )
    existing = interface.element(element)
    if existing is None:
        return False
    queued = parse_flag(req.cell("queued"))
    if req.cell("queued") and queued is not existing.is_queued:
        return False
    if interface.kind is InterfaceKind.MODE_SWITCH:
        return True
    return type_path is None or existing.type_path == type_path


def _judge_existing(world: World, req: Req, interface: PortInterface) -> None:
    if _fulfilled_by(world, req, interface):
        req.already = True
        return
    users = len(world.model.ports_using(interface.path))
    req.error(
        codes.IF_EXISTING,
        f"Interface {interface.name} already exists and is used by {users} port(s). Adding or "
        "changing an element, operation or argument of an existing interface changes every "
        "one of these ports and is not supported in v1. Create a new interface, or change "
        "this one in DaVinci Developer.",
        interface.path,
    )


# ---------------------------------------------------------------------- element rows


def _must_be_empty(req: Req, kind: InterfaceKind, *keys: str) -> None:
    for key in keys:
        if req.cell(key) and not (key == "queued" and parse_flag(req.cell(key)) is False):
            req.error(
                codes.IF_CELL_NOT_USED,
                f"Cell '{header(_ROWS, key)}' is filled, but it does not apply to a "
                f"{kind.value} interface. Clear the cell.",
            )


def _mode_group(world: World, req: Req, interface_name: str) -> str | None:
    text = req.cell("data_type")
    if not text:
        blank_required(
            req, _ROWS, "data_type", "For a mode-switch interface it names the mode group."
        )
        return None
    known = world.mode_groups_by_name
    if text.startswith("/"):
        paths = [text] if any(text in group for group in known.values()) else []
    else:
        paths = known.get(text, [])
    if len(paths) == 1:
        return paths[0]
    req.error(
        codes.IF_MODE_GROUP,
        f"Mode-switch interface {interface_name}: mode declaration group '{text}' "
        + (
            "is not unique; enter its absolute path."
            if paths
            else "cannot be resolved. v1 can only reuse a mode group that an existing "
            "mode-switch interface of the repo already uses; it does not create mode groups."
        ),
    )
    return None


def _parse_item(world: World, req: Req, kind: InterfaceKind, interface_name: str) -> _Item | None:
    element = req.cell("element")
    item = _Item(req=req, element=element, argument=req.cell("argument"))
    if kind is InterfaceKind.CLIENT_SERVER:
        req.problems(world.names.check(element, NameKind.OPERATION))
        _must_be_empty(req, kind, "queued")
        if not item.argument:
            if req.cell("direction") or req.cell("data_type"):
                blank_required(
                    req,
                    _ROWS,
                    "argument",
                    f"A direction or data type is given for operation {element}, but no "
                    "argument name.",
                )
            return item
        req.problems(world.names.check(item.argument, NameKind.ARGUMENT))
        if require(req, _ROWS, "direction"):
            item.direction = parse_argument_direction(req.cell("direction"))
            if item.direction is None:
                req.error(
                    codes.IF_ARGUMENT_DIRECTION,
                    f"Direction '{req.cell('direction')}' of argument {item.argument} of "
                    f"operation {element} is not IN, OUT or INOUT.",
                )
        if require(req, _ROWS, "data_type"):
            what = f"argument {item.argument} of operation {element}"
            item.type_path = world.resolve_type(req, req.cell("data_type"), what)
        return item
    req.problems(world.names.check(element, NameKind.ELEMENT))
    _must_be_empty(req, kind, "argument", "direction")
    if kind is InterfaceKind.MODE_SWITCH:
        _must_be_empty(req, kind, "queued")
        item.type_path = _mode_group(world, req, interface_name)
        return item
    if require(req, _ROWS, "data_type"):
        item.type_path = world.resolve_type(req, req.cell("data_type"), f"element {element}")
    queued = parse_flag(req.cell("queued"))
    if queued is None:
        bad_cell(req, _ROWS, "queued", "Y or N")
    else:
        item.queued = queued
    return item


def _merge_items(items: Sequence[_Item], kind: InterfaceKind, interface_name: str) -> list[_Item]:
    """Merge repeated rows, reject rows that define the same thing differently."""
    by_key: dict[tuple[str, str], list[_Item]] = {}
    for item in items:
        by_key.setdefault(item.key, []).append(item)
    unique: list[_Item] = []
    for (element, argument), members in by_key.items():
        first = members[0]
        if any(member.signature != first.signature for member in members[1:]):
            rows = [member.req for member in members]
            if argument:
                code = codes.IF_ARGUMENT_DUPLICATE
                what = f"Argument {argument} of operation {element}"
            else:
                code = codes.IF_ELEMENT_DUPLICATE
                what = f"Element {element}"
            for member in members:
                member.req.error(
                    code,
                    f"{what} of interface {interface_name} is defined more than once with "
                    f"different content (also in {list_rows(rows, skip=member.req)}). Names "
                    "must be unique.",
                )
            continue
        for member in members[1:]:
            member.req.merge_into(first.req)
        unique.append(first)
    if kind is InterfaceKind.CLIENT_SERVER:
        operations: dict[str, list[Req]] = {}
        arguments: dict[str, dict[str, list[Req]]] = {}
        for item in unique:
            operations.setdefault(item.element, []).append(item.req)
            if item.argument:
                arguments.setdefault(item.element, {}).setdefault(item.argument, []).append(
                    item.req
                )
        reject_case_clashes(operations, f"Interface {interface_name}: operation name")
        for operation, names in arguments.items():
            reject_case_clashes(
                names, f"Interface {interface_name}, operation {operation}: argument name"
            )
    else:
        reject_case_clashes(
            {item.element: [item.req] for item in unique},
            f"Interface {interface_name}: element name",
        )
    return unique


# ---------------------------------------------------------------------- groups


def _settle_headers(world: World, group: _Group) -> tuple[Req, InterfaceKind] | None:
    """Pick the header row that carries the operation, or None when no header is usable."""
    usable = [(req, kind) for req, kind in group.headers if kind is not None and not req.rejected]
    if not usable:
        return None
    if len({kind for _, kind in usable}) > 1:
        rows = [req for req, _ in usable]
        for req, _ in usable:
            req.error(
                codes.CFL_DIFFERENT,
                f"Interface {group.name} is requested with different kinds (also in "
                f"{list_rows(rows, skip=req)}). All of these rows are rejected; agree on one "
                "definition.",
            )
        return None
    leader, kind = usable[0]
    for req, _ in usable[1:]:
        req.merge_into(leader)
    clash = scope_clash(world.package_scope(world.config.packages.interfaces), group.name)
    if clash is not None:
        code, other = clash
        leader.error(
            code,
            f"Interface name '{group.name}' is not free in package "
            f"{world.config.packages.interfaces}: "
            + (
                "another object already has this name."
                if code == codes.NAME_DUPLICATE
                else f"'{other}' differs only by letter case."
            ),
            group.path,
        )
        return None
    return leader, kind


def _files_agree(group: _Group, items: Sequence[_Item]) -> bool:
    """Two workbooks that both define the interface must define the same content."""
    files = {req.raw.row.file for req, kind in group.headers if kind is not None}
    if len(files) < 2:
        return True
    per_file: dict[str, set[tuple[tuple[str, str], tuple[object, ...]]]] = {f: set() for f in files}
    for item in items:
        if item.req.raw.row.file in per_file:
            per_file[item.req.raw.row.file].add((item.key, item.signature))
    definitions = list(per_file.values())
    if all(definition == definitions[0] for definition in definitions[1:]):
        return True
    names = ", ".join(sorted(short_file(name) for name in files))
    for req, _ in group.headers:
        req.error(
            codes.CFL_DIFFERENT,
            f"Interface {group.name} is defined in several workbooks ({names}) with different "
            "elements. All of these rows are rejected; agree on one definition.",
        )
    return False


def _build(
    world: World, group: _Group, leader: Req, kind: InterfaceKind, items: Sequence[_Item]
) -> None:
    elements: tuple[ElementSpec, ...] = ()
    operations: tuple[OperationSpec, ...] = ()
    view_elements: tuple[DataElement, ...] = ()
    view_operations: tuple[Operation, ...] = ()
    mode_group: str | None = None
    if kind is InterfaceKind.CLIENT_SERVER:
        arguments: dict[str, list[_Item]] = {}
        for item in items:
            bucket = arguments.setdefault(item.element, [])
            if item.argument:
                bucket.append(item)
        operations = tuple(
            OperationSpec(
                name=name,
                arguments=tuple(
                    ArgumentSpec(
                        name=item.argument,
                        direction=item.direction or ArgumentDirection.IN,
                        type_path=item.type_path or "",
                    )
                    for item in members
                ),
            )
            for name, members in arguments.items()
        )
        view_operations = tuple(
            Operation(
                name=spec.name,
                arguments=tuple(
                    Argument(name=a.name, direction=a.direction, type_path=a.type_path)
                    for a in spec.arguments
                ),
            )
            for spec in operations
        )
    else:
        elements = tuple(
            ElementSpec(name=item.element, type_path=item.type_path or "", is_queued=item.queued)
            for item in items
        )
        view_elements = tuple(
            DataElement(name=spec.name, type_path=spec.type_path, is_queued=spec.is_queued)
            for spec in elements
        )
        if kind is InterfaceKind.MODE_SWITCH:
            mode_group = items[0].type_path
    leader.emit(
        AddInterface(
            id=_ids.add_interface(group.path),
            **leader.base(Route.APPLY),
            package_path=world.config.packages.interfaces,
            name=group.name,
            kind=kind,
            elements=elements,
            operations=operations,
        )
    )
    for row in group.rows:
        leader.depends.extend(row.depends)
        if row.leader is None:
            row.follow(leader)
    world.new_interface_reqs[group.path] = leader
    world.new_interfaces[group.path] = PortInterface(
        path=group.path,
        name=group.name,
        kind=kind,
        elements=view_elements,
        operations=view_operations,
        mode_group_path=mode_group,
    )


def _reject_group(world: World, group: _Group) -> None:
    """Reject every row of an interface that cannot be created as a whole."""
    members = [req for req, _ in group.headers] + group.rows
    culprits = [req for req in members if req.rejected]
    for req in members:
        if not req.rejected and req.leader is None:
            req.error(
                codes.IF_GROUP_REJECTED,
                f"Interface {group.name} is not created because of a problem in "
                f"{list_rows(culprits)}. An interface is created with all of its rows or "
                "not at all.",
                group.path,
            )
    if members:
        world.failed_interfaces[group.path] = group.headers[0][0] if group.headers else members[0]


def _process_group(world: World, group: _Group) -> None:
    existing = world.model.interfaces.get(group.path)
    if existing is not None:
        for req, kind in group.headers:
            if kind is None or req.rejected:
                continue
            if kind is existing.kind:
                req.already = True
            else:
                req.error(
                    codes.NAME_DUPLICATE,
                    f"Interface {group.name} already exists as a {existing.kind.value} "
                    "interface. Changing an interface is not supported in v1.",
                    group.path,
                )
        return
    settled = _settle_headers(world, group)
    if settled is None:
        _reject_group(world, group)
        return
    leader, kind = settled
    parsed = [_parse_item(world, row, kind, group.name) for row in group.rows]
    items = [item for item in parsed if item is not None and not item.req.rejected]
    unique = _merge_items(items, kind, group.name)
    agree = _files_agree(group, items)
    if not group.rows:
        leader.error(
            codes.IF_EMPTY,
            f"Interface {group.name} has no element and no operation. Add at least one row "
            "for it on the InterfaceElements sheet.",
            group.path,
        )
    elif kind is InterfaceKind.MODE_SWITCH and len(unique) > 1:
        for item in unique:
            item.req.error(
                codes.IF_MODE_GROUP,
                f"Mode-switch interface {group.name} has {len(unique)} rows on the "
                "InterfaceElements sheet. A mode-switch interface has exactly one mode group.",
            )
    members = [req for req, _ in group.headers] + group.rows
    if not agree or any(req.rejected for req in members):
        _reject_group(world, group)
        return
    _build(world, group, leader, kind, unique)


def process_interfaces(world: World, headers: Sequence[Req], rows: Sequence[Req]) -> None:
    """Validate the ADD rows of the Interfaces and InterfaceElements sheets."""
    package = world.config.packages.interfaces
    groups: dict[str, _Group] = {}
    for req in headers:
        complete = require(req, _HEAD, "name", "kind")
        name = req.cell("name")
        if not name:
            continue
        kind = parse_interface_kind(req.cell("kind")) if req.cell("kind") else None
        if req.cell("kind") and (kind is None or kind not in _CREATABLE):
            req.error(
                codes.IF_KIND,
                f"Interface {name}: kind '{req.cell('kind')}' cannot be created in v1. "
                "Supported: sender-receiver, client-server, and mode-switch with an existing "
                "mode group.",
            )
            kind = None
        if complete:
            req.problems(world.names.check(name, NameKind.INTERFACE))
        group = groups.setdefault(f"{package}/{name}", _Group(name=name, path=f"{package}/{name}"))
        group.headers.append((req, kind))
    reject_case_clashes(
        {group.name: [req for req, _ in group.headers] for group in groups.values()},
        "Interface name",
    )

    for req in rows:
        if not require(req, _ROWS, "interface", "element"):
            continue
        text = req.cell("interface")
        if text.startswith("/"):
            paths = [text] if text in world.model.interfaces or text in groups else []
        else:
            new_path = f"{package}/{text}"
            paths = list(world.interfaces_by_name.get(text, ()))
            if new_path in groups and new_path not in paths:
                paths.append(new_path)
        if not paths:
            req.error(
                codes.REF_UNKNOWN_INTERFACE,
                f"Interface '{text}' does not exist in the repo and has no ADD row on the "
                "Interfaces sheet of this import.",
            )
        elif len(paths) > 1:
            req.error(
                codes.REF_AMBIGUOUS_INTERFACE,
                f"Several interfaces are named '{text}' ({', '.join(sorted(paths))}). Enter "
                "the absolute path instead.",
            )
        elif paths[0] in world.model.interfaces:
            _judge_existing(world, req, world.model.interfaces[paths[0]])
        else:
            groups[paths[0]].rows.append(req)

    for group in groups.values():
        _process_group(world, group)

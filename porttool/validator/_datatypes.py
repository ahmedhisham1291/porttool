"""DataTypes sheet: ADD rows become ``AddDataType`` operations. Internal."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field

from porttool.core.models import BaseType, DataType, DataTypeCategory, DataTypeLevel
from porttool.core.operations import AddDataType, RecordElementSpec, Route
from porttool.core.sheets import Sheet
from porttool.validator import _ids, codes
from porttool.validator._common import (
    bad_cell,
    blank_required,
    header,
    reject_case_clashes,
    require,
    settle,
)
from porttool.validator._state import Req
from porttool.validator._typeinfo import (
    Number,
    describe_value_type,
    fit_problem,
    format_number,
    parse_exact,
)
from porttool.validator._world import World, scope_clash
from porttool.validator.cells import (
    parse_category,
    parse_enum_values,
    parse_int,
    parse_level,
    parse_number,
    parse_record_elements,
)
from porttool.validator.names import NameKind

_SHEET = Sheet.DATA_TYPES

_VALUE_LIKE = (DataTypeCategory.VALUE, DataTypeCategory.BOOLEAN, DataTypeCategory.ENUMERATION)

#: Cells that may be filled per category. Any other category cell must be empty.
_USED_CELLS: dict[DataTypeCategory, frozenset[str]] = {
    DataTypeCategory.VALUE: frozenset(
        {"base_type", "lower_limit", "upper_limit", "unit", "factor", "offset"}
    ),
    DataTypeCategory.BOOLEAN: frozenset({"base_type", "lower_limit", "upper_limit"}),
    DataTypeCategory.ENUMERATION: frozenset(
        {"base_type", "lower_limit", "upper_limit", "enum_values"}
    ),
    DataTypeCategory.ARRAY: frozenset({"element_type", "array_size"}),
    DataTypeCategory.RECORD: frozenset({"record_elements"}),
}
_CATEGORY_CELLS = (
    "base_type",
    "lower_limit",
    "upper_limit",
    "unit",
    "factor",
    "offset",
    "enum_values",
    "element_type",
    "array_size",
    "record_elements",
)


@dataclass(slots=True)
class _Candidate:
    req: Req
    name: str
    path: str
    level: DataTypeLevel
    category: DataTypeCategory
    lower: Number | None = None
    upper: Number | None = None
    factor: float | None = None
    offset: float | None = None
    enum_values: tuple[tuple[int, str], ...] = ()
    array_size: int | None = None
    record: tuple[tuple[str, str], ...] = ()
    base: BaseType | None = None
    unit_path: str | None = None
    element_path: str | None = None
    record_paths: list[str] = field(default_factory=list)
    #: Other candidates of this import that this type is built from.
    uses: list[_Candidate] = field(default_factory=list)


def _parse(world: World, req: Req) -> _Candidate | None:
    """Read one row. Problems that need no other row are reported here."""
    if not require(req, _SHEET, "name", "level", "category"):
        return None
    name = req.cell("name")
    req.problems(world.names.check(name, NameKind.DATA_TYPE))
    level = parse_level(req.cell("level"))
    if level is None:
        req.error(
            codes.DT_LEVEL,
            f"Level '{req.cell('level')}' of data type {name} is not known; use application "
            "or implementation.",
        )
    category = parse_category(req.cell("category"))
    if category is None or category not in _USED_CELLS:
        req.error(
            codes.DT_CATEGORY,
            f"Category '{req.cell('category')}' of data type {name} cannot be created in v1. "
            "Supported: value, boolean, enumeration, array, record.",
        )
        return None
    if level is None:
        return None
    used = _USED_CELLS[category]
    for key in _CATEGORY_CELLS:
        if key not in used and req.cell(key):
            req.error(
                codes.DT_CELL_NOT_USED,
                f"Cell '{header(_SHEET, key)}' is filled, but it does not apply to a data "
                f"type of category {category.value}. Clear the cell.",
            )
    candidate = _Candidate(
        req=req,
        name=name,
        path=f"{world.config.packages.data_types}/{name}",
        level=level,
        category=category,
    )
    if category in _VALUE_LIKE:
        _parse_value_cells(world, candidate)
    elif category is DataTypeCategory.ARRAY:
        _parse_array_cells(candidate)
    else:
        _parse_record_cells(world, candidate)
    return candidate


def _number(req: Req, key: str) -> Number | None:
    text = req.cell(key)
    if not text:
        return None
    value = parse_exact(text)
    if value is None:
        bad_cell(req, _SHEET, key, "a number with a dot as decimal separator")
    return value


def _parse_value_cells(world: World, candidate: _Candidate) -> None:
    req = candidate.req
    candidate.lower = _number(req, "lower_limit")
    candidate.upper = _number(req, "upper_limit")
    if (
        candidate.lower is not None
        and candidate.upper is not None
        and candidate.lower > candidate.upper
    ):
        req.error(
            codes.DT_RANGE_ORDER,
            f"Data type {candidate.name}: the lower limit {format_number(candidate.lower)} is "
            f"greater than the upper limit {format_number(candidate.upper)}.",
        )
    if candidate.category is DataTypeCategory.VALUE:
        for key in ("factor", "offset"):
            text = req.cell(key)
            if not text:
                continue
            value = parse_number(text)
            if value is None:
                bad_cell(req, _SHEET, key, "a number with a dot as decimal separator")
            elif key == "factor":
                candidate.factor = value
            else:
                candidate.offset = value
        if candidate.factor == 0.0:
            req.error(
                codes.DT_SCALING,
                f"Data type {candidate.name}: the factor of a linear scaling must not be 0.",
            )
    if candidate.category is DataTypeCategory.ENUMERATION:
        if not req.cell("enum_values"):
            blank_required(req, _SHEET, "enum_values", "An enumeration needs at least one value.")
            return
        pairs, reason = parse_enum_values(req.cell("enum_values"))
        if pairs is None:
            bad_cell(req, _SHEET, "enum_values", f"the form 0=OFF;1=ON ({reason})")
            return
        candidate.enum_values = pairs
        seen_values: set[int] = set()
        seen_labels: set[str] = set()
        for value, label in pairs:
            if value in seen_values:
                req.error(
                    codes.DT_ENUM_VALUE_DUPLICATE,
                    f"Enumeration {candidate.name} uses the value {value} more than once.",
                )
            if label in seen_labels:
                req.error(
                    codes.DT_ENUM_LABEL_DUPLICATE,
                    f"Enumeration {candidate.name} uses the label {label} more than once.",
                )
            else:
                req.problems(world.names.check(label, NameKind.ENUM_LABEL))
            seen_values.add(value)
            seen_labels.add(label)


def _parse_array_cells(candidate: _Candidate) -> None:
    req = candidate.req
    require(req, _SHEET, "element_type", "array_size")
    size_text = req.cell("array_size")
    if size_text:
        size = parse_int(size_text)
        if size is None or size <= 0:
            req.error(
                codes.DT_ARRAY_SIZE,
                f"Array size '{size_text}' of data type {candidate.name} is not a positive "
                "whole number.",
            )
        else:
            candidate.array_size = size


def _parse_record_cells(world: World, candidate: _Candidate) -> None:
    req = candidate.req
    if not require(req, _SHEET, "record_elements"):
        return
    pairs, reason = parse_record_elements(req.cell("record_elements"))
    if pairs is None:
        bad_cell(req, _SHEET, "record_elements", f"the form name:Type;name2:Type2 ({reason})")
        return
    candidate.record = pairs
    seen: dict[str, str] = {}
    for element, _ in pairs:
        earlier = seen.get(element.lower())
        if earlier == element:
            req.error(
                codes.DT_RECORD_DUPLICATE,
                f"Record {candidate.name} has more than one element named {element}.",
            )
            continue
        if earlier is not None:
            req.error(
                codes.NAME_CASE_CLASH,
                f"Record {candidate.name}: element names '{earlier}' and '{element}' differ "
                "only by letter case.",
            )
        seen[element.lower()] = element
        req.problems(world.names.check(element, NameKind.RECORD_ELEMENT))


def _signature(candidate: _Candidate) -> tuple[object, ...]:
    req = candidate.req
    return (
        candidate.level,
        candidate.category,
        req.cell("base_type"),
        candidate.lower,
        candidate.upper,
        req.cell("unit"),
        candidate.factor,
        candidate.offset,
        candidate.enum_values,
        req.cell("element_type"),
        candidate.array_size,
        candidate.record,
    )


# ---------------------------------------------------------------------- references


def _resolve_reference(
    world: World,
    candidate: _Candidate,
    text: str,
    what: str,
    leaders: dict[str, _Candidate],
    failed: dict[str, Req],
) -> str | None:
    """Path of a type that ``candidate`` is built from: existing, or requested in this import."""
    req = candidate.req
    new_path = text if text.startswith("/") else f"{world.config.packages.data_types}/{text}"
    if text.startswith("/"):
        paths = [text] if text in world.model.data_types or text in leaders else []
    else:
        paths = list(world.types_by_name.get(text, ()))
        if new_path in leaders and new_path not in paths:
            paths.append(new_path)
    if not paths:
        rejected = failed.get(new_path)
        if rejected is not None:
            req.error(
                codes.REF_REJECTED_DEPENDENCY,
                f"Data type '{text}' of {what} is requested in {rejected.where}, but that "
                "request was rejected.",
            )
        else:
            req.error(
                codes.REF_UNKNOWN_DATA_TYPE,
                f"Data type '{text}' of {what} does not exist in the repo and is not created "
                "by this import.",
            )
        return None
    if len(paths) > 1:
        req.error(
            codes.REF_AMBIGUOUS_DATA_TYPE,
            f"Several data types are named '{text}' ({', '.join(sorted(paths))}), so the type "
            f"of {what} is not clear. Enter the absolute path instead.",
        )
        return None
    path = paths[0]
    existing = world.model.data_types.get(path)
    other_level = existing.level if existing is not None else leaders[path].level
    if other_level is not candidate.level:
        req.error(
            codes.DT_LEVEL_MIX,
            f"Data type {candidate.name} is an {candidate.level.value} type, but '{text}' "
            f"({what}) is an {other_level.value} type. Both must have the same level.",
        )
    if existing is None:
        candidate.uses.append(leaders[path])
    return path


def _internal(value: Number | None, candidate: _Candidate) -> Number | None:
    """Physical limit to internal value when the type has a linear scaling."""
    if value is None or candidate.factor is None or candidate.factor == 0.0:
        return value
    return (value - (candidate.offset or 0.0)) / candidate.factor


def _resolve(
    world: World,
    candidate: _Candidate,
    leaders: dict[str, _Candidate],
    failed: dict[str, Req],
) -> None:
    req = candidate.req
    name = candidate.name
    if candidate.category in _VALUE_LIKE:
        base_text = req.cell("base_type")
        if base_text:
            base, count = world.base_type(base_text)
            if base is None:
                req.error(
                    codes.DT_BASE_TYPE,
                    f"Base type '{base_text}' of data type {name} "
                    + (
                        "does not exist in the repo."
                        if count == 0
                        else "is not unique; enter its absolute path."
                    ),
                )
            candidate.base = base
        elif candidate.level is DataTypeLevel.IMPLEMENTATION:
            blank_required(
                req, _SHEET, "base_type", "An implementation data type needs a base type."
            )
        unit_text = req.cell("unit")
        if unit_text and candidate.category is DataTypeCategory.VALUE:
            unit, count = world.unit(unit_text)
            if unit is None:
                req.error(
                    codes.DT_UNIT,
                    f"Unit '{unit_text}' of data type {name} "
                    + (
                        "does not exist in the repo. v1 does not create units; pick an "
                        "existing one."
                        if count == 0
                        else "is not unique; enter its absolute path."
                    ),
                )
            else:
                candidate.unit_path = unit.path
        if candidate.base is not None:
            ends = (_internal(candidate.lower, candidate), _internal(candidate.upper, candidate))
            limits = sorted(value for value in ends if value is not None)
            values = [value for value, _ in candidate.enum_values]
            problem = fit_problem(
                limits[0] if limits else None, limits[-1] if limits else None, candidate.base
            ) or (fit_problem(min(values), max(values), candidate.base) if values else "")
            if problem:
                req.error(codes.DT_RANGE_FIT, f"Data type {name}: {problem}.")
    elif candidate.category is DataTypeCategory.ARRAY:
        element_text = req.cell("element_type")
        if element_text:
            candidate.element_path = _resolve_reference(
                world, candidate, element_text, f"the elements of array {name}", leaders, failed
            )
    else:
        for element, type_text in candidate.record:
            what = f"element {element} of record {name}"
            path = _resolve_reference(world, candidate, type_text, what, leaders, failed)
            candidate.record_paths.append(path or "")


# ---------------------------------------------------------------------- existing types


def _same_number(left: Number | None, right: Number | None) -> bool:
    return left is not None and right is not None and float(left) == float(right)


def _fulfilled_by(world: World, candidate: _Candidate, existing: DataType) -> bool:
    """True when the existing type already is what the row asks for."""
    model = world.model
    if existing.level is not candidate.level or existing.category is not candidate.category:
        return False
    if candidate.category is DataTypeCategory.ARRAY:
        return existing.array_size == candidate.array_size and existing.element_type_paths == (
            candidate.element_path,
        )
    if candidate.category is DataTypeCategory.RECORD:
        return existing.element_names == tuple(
            element for element, _ in candidate.record
        ) and existing.element_type_paths == tuple(candidate.record_paths)
    if candidate.base is not None and existing.base_type_path != candidate.base.path:
        return False
    constraint = model.constraints.get(existing.constraint_path or "")
    compu = model.compu_methods.get(existing.compu_method_path or "")
    if candidate.lower is not None and not _same_number(
        candidate.lower, constraint.lower if constraint else None
    ):
        return False
    if candidate.upper is not None and not _same_number(
        candidate.upper, constraint.upper if constraint else None
    ):
        return False
    if candidate.factor is not None and not _same_number(
        candidate.factor, compu.factor if compu else None
    ):
        return False
    if candidate.offset is not None and not _same_number(
        candidate.offset, (compu.offset or 0.0) if compu else None
    ):
        return False
    if candidate.enum_values and sorted(candidate.enum_values) != sorted(
        compu.enum_values if compu else ()
    ):
        return False
    return candidate.unit_path is None or candidate.unit_path in (
        existing.unit_path,
        compu.unit_path if compu else None,
    )


def _check_existing(world: World, candidate: _Candidate) -> None:
    req = candidate.req
    existing = world.model.data_types.get(candidate.path)
    if existing is not None:
        if not req.rejected and _fulfilled_by(world, candidate, existing):
            req.already = True
        else:
            req.error(
                codes.NAME_DUPLICATE,
                f"Data type {candidate.name} already exists in package "
                f"{world.config.packages.data_types} with another definition. Changing a data "
                "type is not supported in v1.",
                candidate.path,
            )
        return
    clash = scope_clash(world.package_scope(world.config.packages.data_types), candidate.name)
    if clash is not None:
        code, other = clash
        req.error(
            code,
            f"Data type name '{candidate.name}' is not free in package "
            f"{world.config.packages.data_types}: "
            + (
                "another object already has this name."
                if code == codes.NAME_DUPLICATE
                else f"'{other}' differs only by letter case."
            ),
            candidate.path,
        )


# ---------------------------------------------------------------------- cycles


def _reject_cycles(leaders: Sequence[_Candidate]) -> None:
    """Reject every requested type that is part of a circular definition."""
    remaining = {id(candidate): candidate for candidate in leaders}
    uses = {id(c): {id(u) for u in c.uses if id(u) in remaining} for c in leaders}
    used_by: dict[int, set[int]] = {key: set() for key in remaining}
    for key, targets in uses.items():
        for target in targets:
            used_by[target].add(key)
    # Peel types that use nothing (or are used by nothing) until only cycles are left.
    queue = deque(key for key in remaining if not uses[key] or not used_by[key])
    while queue:
        key = queue.popleft()
        if key not in remaining:
            continue
        del remaining[key]
        for other in used_by[key]:
            uses[other].discard(key)
            if other in remaining and not uses[other]:
                queue.append(other)
        for other in uses[key]:
            used_by[other].discard(key)
            if other in remaining and not used_by[other]:
                queue.append(other)
    members = list(remaining.values())
    names = ", ".join(sorted(candidate.name for candidate in members)[:6])
    for candidate in members:
        candidate.req.error(
            codes.DT_CYCLE,
            f"Data type {candidate.name} is part of a circular definition ({names}): a type "
            "cannot contain itself, directly or through other types.",
            candidate.path,
        )


def _reject_dependents(leaders: Sequence[_Candidate]) -> None:
    """A type built from a rejected type of this import is rejected too."""
    used_by: dict[int, list[_Candidate]] = {}
    for candidate in leaders:
        for used in candidate.uses:
            used_by.setdefault(id(used), []).append(candidate)
    queue = deque(candidate for candidate in leaders if candidate.req.rejected)
    while queue:
        failed = queue.popleft()
        for dependent in used_by.get(id(failed), ()):
            if dependent.req.rejected:
                continue
            dependent.req.error(
                codes.REF_REJECTED_DEPENDENCY,
                f"Data type {dependent.name} uses data type {failed.name}, which is requested "
                f"in {failed.req.where} but was rejected.",
                dependent.path,
            )
            queue.append(dependent)


# ---------------------------------------------------------------------- phase


def _emit(world: World, candidate: _Candidate) -> None:
    req = candidate.req
    op = AddDataType(
        id=_ids.add_data_type(candidate.path),
        **req.base(Route.APPLY),
        package_path=world.config.packages.data_types,
        name=candidate.name,
        level=candidate.level,
        category=candidate.category,
        base_type_path=candidate.base.path if candidate.base is not None else None,
        lower_limit=float(candidate.lower) if candidate.lower is not None else None,
        upper_limit=float(candidate.upper) if candidate.upper is not None else None,
        unit=candidate.unit_path,
        factor=candidate.factor,
        offset=candidate.offset,
        enum_values=candidate.enum_values,
        element_type_path=candidate.element_path,
        array_size=candidate.array_size,
        record_elements=tuple(
            RecordElementSpec(name=element, type_path=path)
            for (element, _), path in zip(candidate.record, candidate.record_paths, strict=True)
        ),
    )
    req.emit(op)
    req.depends.extend(used.req for used in candidate.uses)
    world.new_type_reqs[candidate.path] = req
    world.new_types[candidate.path] = describe_value_type(
        path=candidate.path,
        name=candidate.name,
        level=candidate.level,
        category=candidate.category,
        base=candidate.base,
        lower=candidate.lower,
        upper=candidate.upper,
        factor=candidate.factor,
        offset=candidate.offset,
        enum_values=candidate.enum_values,
    )


def process_data_types(world: World, reqs: Sequence[Req]) -> None:
    """Validate the ADD rows of the DataTypes sheet."""
    groups: dict[str, list[_Candidate]] = {}
    failed_rows: dict[str, Req] = {}
    for req in reqs:
        candidate = _parse(world, req)
        if candidate is None or req.rejected:
            name = req.cell("name")
            if name:
                failed_rows.setdefault(f"{world.config.packages.data_types}/{name}", req)
            continue
        groups.setdefault(candidate.path, []).append(candidate)

    leaders: dict[str, _Candidate] = {}
    for path, members in groups.items():
        leader_req = settle(
            [(member.req, _signature(member)) for member in members],
            f"Data type {members[0].name}",
        )
        if leader_req is None:
            failed_rows.setdefault(path, members[0].req)
        else:
            leaders[path] = members[0]
    reject_case_clashes(
        {candidate.name: [candidate.req] for candidate in leaders.values()}, "Data type name"
    )

    ordered = list(leaders.values())
    for candidate in ordered:
        _resolve(world, candidate, leaders, failed_rows)
        _check_existing(world, candidate)
    _reject_cycles(ordered)
    _reject_dependents(ordered)

    for path, req in failed_rows.items():
        if path not in world.model.data_types:
            world.failed_types.setdefault(path, req)
    for candidate in ordered:
        if candidate.req.rejected:
            if candidate.path not in world.model.data_types:
                world.failed_types[candidate.path] = candidate.req
        elif not candidate.req.already:
            _emit(world, candidate)

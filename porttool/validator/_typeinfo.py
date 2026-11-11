"""What the validator needs to know about a data type to judge ranges and init values. Internal."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from porttool.core.models import BaseType, DataType, DataTypeCategory, DataTypeLevel, Model
from porttool.validator import codes
from porttool.validator.cells import parse_int, parse_number

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FLOAT32_MAX = 3.4028234663852886e38
_FLOAT64_MAX = 1.7976931348623157e308
_MAX_REFERENCE_DEPTH = 8

Number = int | float


def parse_exact(text: str) -> Number | None:
    """Whole numbers as ``int`` (exact for 64-bit limits), everything else as ``float``."""
    whole = parse_int(text)
    if whole is not None:
        return whole
    return parse_number(text)


def format_number(value: Number) -> str:
    if isinstance(value, float) and value == math.floor(value) and abs(value) < 1e15:
        return str(int(value))
    return str(value)


@dataclass(frozen=True, slots=True)
class BaseRange:
    """Natural value range of a base type. ``signed`` is None when the encoding is unknown."""

    lower: Number
    upper: Number
    integral: bool
    signed: bool | None
    span: Number | None  # largest allowed ``upper - lower`` when the sign is unknown


def base_range(base: BaseType) -> BaseRange | None:
    """Range of ``base`` from its size and encoding, or None when that is not known."""
    bits = base.size_bits
    if bits is None or bits <= 0:
        return None
    encoding = (base.encoding or "").strip().upper().replace("-", "")
    if encoding == "BOOLEAN":
        return BaseRange(0, 1, integral=True, signed=False, span=None)
    if encoding == "IEEE754":
        if bits == 32:
            return BaseRange(-_FLOAT32_MAX, _FLOAT32_MAX, integral=False, signed=True, span=None)
        if bits == 64:
            return BaseRange(-_FLOAT64_MAX, _FLOAT64_MAX, integral=False, signed=True, span=None)
        return None
    if encoding == "2C":
        return BaseRange(-(2 ** (bits - 1)), 2 ** (bits - 1) - 1, True, True, None)
    if encoding == "NONE":
        return BaseRange(0, 2**bits - 1, True, False, None)
    if encoding == "" and bits not in (32, 64):
        # Sign unknown: nothing below the signed minimum or above the unsigned maximum fits.
        # (A 32 or 64 bit type without encoding could be a float: nothing is known then.)
        return BaseRange(-(2 ** (bits - 1)), 2**bits - 1, True, None, 2**bits - 1)
    return None


def fit_problem(lower: Number | None, upper: Number | None, base: BaseType) -> str:
    """Why ``lower..upper`` does not fit ``base``; empty string when it fits or is unknown."""
    limits = base_range(base)
    if limits is None:
        return ""
    described = (
        f"{base.name} ({base.size_bits} bit"
        + ("" if limits.signed is None else ", signed" if limits.signed else ", unsigned")
        + f", {format_number(limits.lower)}..{format_number(limits.upper)})"
    )
    for value in (lower, upper):
        if value is None:
            continue
        if value < limits.lower or value > limits.upper:
            return f"{format_number(value)} does not fit base type {described}"
    if (
        limits.span is not None
        and lower is not None
        and upper is not None
        and upper - lower > limits.span
    ):
        return f"the range {format_number(lower)}..{format_number(upper)} is wider than {described}"
    return ""


@dataclass(frozen=True, slots=True)
class TypeInfo:
    """Category, value range and enumeration of one data type (existing or requested)."""

    path: str
    name: str
    level: DataTypeLevel
    category: DataTypeCategory
    integral: bool | None = None  # True: whole numbers only. None: not known.
    lower: Number | None = None
    upper: Number | None = None
    enum_values: tuple[tuple[int, str], ...] = ()


def _scaled(factor: float | None, offset: float | None) -> bool:
    return factor is not None and (factor != 1.0 or (offset or 0.0) != 0.0)


def describe_value_type(
    *,
    path: str,
    name: str,
    level: DataTypeLevel,
    category: DataTypeCategory,
    base: BaseType | None,
    lower: Number | None,
    upper: Number | None,
    factor: float | None,
    offset: float | None,
    enum_values: tuple[tuple[int, str], ...],
) -> TypeInfo:
    """Build a ``TypeInfo`` from the parts of a type: limits win over the base type's range."""
    limits = base_range(base) if base is not None else None
    scaled = _scaled(factor, offset)
    integral: bool | None = None
    if scaled:
        integral = False
    elif limits is not None:
        integral = limits.integral
    if limits is not None and (lower is None or upper is None):
        natural_lower: Number = limits.lower
        natural_upper: Number = limits.upper
        if scaled and factor is not None and factor != 0.0:
            shift = offset or 0.0
            ends = sorted((shift + factor * natural_lower, shift + factor * natural_upper))
            natural_lower, natural_upper = ends[0], ends[1]
        lower = natural_lower if lower is None else lower
        upper = natural_upper if upper is None else upper
    return TypeInfo(
        path=path,
        name=name,
        level=level,
        category=category,
        integral=integral,
        lower=lower,
        upper=upper,
        enum_values=enum_values,
    )


def info_from_model(model: Model, path: str, _depth: int = 0) -> TypeInfo | None:
    """``TypeInfo`` of an existing data type; None when ``path`` is not a loaded data type."""
    data_type = model.data_types.get(path)
    if data_type is None:
        return None
    if data_type.category is DataTypeCategory.TYPE_REFERENCE:
        if _depth < _MAX_REFERENCE_DEPTH and data_type.element_type_paths:
            target = info_from_model(model, data_type.element_type_paths[0], _depth + 1)
            if target is not None:
                return replace(
                    target, path=data_type.path, name=data_type.name, level=data_type.level
                )
        return _plain(data_type)
    if data_type.category not in (
        DataTypeCategory.VALUE,
        DataTypeCategory.BOOLEAN,
        DataTypeCategory.ENUMERATION,
    ):
        return _plain(data_type)
    constraint = (
        model.constraints.get(data_type.constraint_path) if data_type.constraint_path else None
    )
    compu = (
        model.compu_methods.get(data_type.compu_method_path)
        if data_type.compu_method_path
        else None
    )
    base = model.base_types.get(data_type.base_type_path) if data_type.base_type_path else None
    return describe_value_type(
        path=data_type.path,
        name=data_type.name,
        level=data_type.level,
        category=data_type.category,
        base=base,
        lower=constraint.lower if constraint is not None else None,
        upper=constraint.upper if constraint is not None else None,
        factor=compu.factor if compu is not None else None,
        offset=compu.offset if compu is not None else None,
        enum_values=compu.enum_values if compu is not None else (),
    )


def _plain(data_type: DataType) -> TypeInfo:
    return TypeInfo(
        path=data_type.path,
        name=data_type.name,
        level=data_type.level,
        category=data_type.category,
    )


_COMPOSITE = (DataTypeCategory.ARRAY, DataTypeCategory.RECORD, DataTypeCategory.STRING)
_BOOLEAN_TEXTS = frozenset({"0", "1", "true", "false"})


def init_value_problem(info: TypeInfo | None, text: str) -> tuple[str, str]:
    """Check init value ``text`` against a data type.

    Returns ``("", "")`` when the value is fine, else ``(code, reason)``. ``info`` None
    means the type is not loaded: then any number or symbolic name is accepted.
    """
    if info is None or info.category in (DataTypeCategory.TYPE_REFERENCE, DataTypeCategory.OTHER):
        if parse_number(text) is not None or _IDENTIFIER.fullmatch(text):
            return "", ""
        return codes.PORT_INIT_PARSE, "it is neither a number nor a symbolic name"
    if info.category in _COMPOSITE:
        return (
            codes.PORT_INIT_COMPOSITE,
            f"data type {info.name} is of category {info.category.value}; v1 writes init values "
            "of plain values only, so enter none and set the value in DaVinci Developer",
        )
    if info.category is DataTypeCategory.BOOLEAN:
        if text.lower() in _BOOLEAN_TEXTS:
            return "", ""
        return codes.PORT_INIT_PARSE, f"boolean type {info.name} takes 0, 1, true or false"
    if info.enum_values and text in {label for _, label in info.enum_values}:
        return "", ""
    number = parse_exact(text)
    if info.category is DataTypeCategory.ENUMERATION:
        if not info.enum_values:
            if number is not None or _IDENTIFIER.fullmatch(text):
                return "", ""
            return codes.PORT_INIT_PARSE, "it is neither a number nor an enumeration label"
        if number is not None and any(number == value for value, _ in info.enum_values):
            return "", ""
        labels = ", ".join(f"{value}={label}" for value, label in info.enum_values[:8])
        return (
            codes.PORT_INIT_PARSE,
            f"it is not a value or label of enumeration {info.name} ({labels})",
        )
    if number is None:
        return codes.PORT_INIT_PARSE, f"data type {info.name} takes a number"
    if info.integral is True and number != math.floor(number):
        return codes.PORT_INIT_PARSE, f"data type {info.name} takes whole numbers only"
    if info.lower is not None and number < info.lower:
        return (
            codes.PORT_INIT_RANGE,
            f"it is below the lower limit {format_number(info.lower)} of data type {info.name}",
        )
    if info.upper is not None and number > info.upper:
        return (
            codes.PORT_INIT_RANGE,
            f"it is above the upper limit {format_number(info.upper)} of data type {info.name}",
        )
    return "", ""

"""Cell parsers, name rules and type information: the small pure helpers."""

from __future__ import annotations

import pytest

from porttool.core.config import NamingRules
from porttool.core.models import (
    AccessKind,
    BaseType,
    DataTypeCategory,
    DataTypeLevel,
    InterfaceKind,
    PortDirection,
    TriggerKind,
)
from porttool.validator import codes as c
from porttool.validator._typeinfo import (
    TypeInfo,
    base_range,
    fit_problem,
    info_from_model,
    init_value_problem,
    parse_exact,
)
from porttool.validator.cells import (
    parse_access_kind,
    parse_enum_values,
    parse_flag,
    parse_init_cell,
    parse_int,
    parse_interface_kind,
    parse_number,
    parse_port_direction,
    parse_record_elements,
    parse_trigger_kind,
)
from porttool.validator.names import C_KEYWORDS, NameChecker, NameKind
from tests.helpers.sample_model import DT
from tests.unit.validator.support import MODEL


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Y", True),
        ("yes", True),
        ("TRUE", True),
        ("1", True),
        ("x", True),
        ("", False),
        ("n", False),
        ("No", False),
        ("0", False),
        ("maybe", None),
        ("2", None),
    ],
)
def test_parse_flag(text: str, expected: bool | None) -> None:
    assert parse_flag(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5", 5),
        (" -7 ", -7),
        ("+3", 3),
        ("0x1F", 31),
        ("007", 7),
        ("5.0", 5),
        ("1e3", 1000),
        ("5.5", None),
        ("", None),
        ("abc", None),
        ("nan", None),
        ("1e400", None),
    ],
)
def test_parse_int(text: str, expected: int | None) -> None:
    assert parse_int(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.5", 0.5),
        ("-1e-3", -0.001),
        ("0x10", 16.0),
        ("0,5", None),
        ("inf", None),
        ("-Infinity", None),
        ("NaN", None),
        ("", None),
    ],
)
def test_parse_number(text: str, expected: float | None) -> None:
    assert parse_number(text) == expected


def test_parse_exact_keeps_64_bit_limits_exact() -> None:
    assert parse_exact("18446744073709551615") == 2**64 - 1
    assert isinstance(parse_exact("18446744073709551615"), int)
    assert parse_exact("0.25") == 0.25
    assert parse_exact("x") is None


def test_parse_enum_values() -> None:
    assert parse_enum_values("0=OFF;1=ON") == (((0, "OFF"), (1, "ON")), "")
    assert parse_enum_values(" 0x2 = A ; ") == (((2, "A"),), "")
    assert parse_enum_values("")[0] == ()
    for bad in ("OFF", "a=OFF", "1=", "1.5=X"):
        pairs, reason = parse_enum_values(bad)
        assert pairs is None and reason


def test_parse_record_elements() -> None:
    assert parse_record_elements("a:uint8; b:/DataTypes/uint16") == (
        (("a", "uint8"), ("b", "/DataTypes/uint16")),
        "",
    )
    for bad in ("a", "a:", ":uint8"):
        assert parse_record_elements(bad)[0] is None


def test_parse_init_cell() -> None:
    assert parse_init_cell("") == (None, None, "")
    assert parse_init_cell(" 5 ") == ("5", None, "")
    assert parse_init_cell("none") == ("none", None, "")
    assert parse_init_cell("A=1; B=none") == (None, (("A", "1"), ("B", "none")), "")
    assert parse_init_cell("1;2")[2]
    assert parse_init_cell("A=;B=1")[2]


def test_kind_texts_are_read_tolerantly() -> None:
    assert parse_port_direction(" pr ") is PortDirection.PR
    assert parse_port_direction("in") is None
    assert parse_interface_kind("Sender_Receiver") is InterfaceKind.SENDER_RECEIVER
    assert parse_interface_kind("client server") is InterfaceKind.CLIENT_SERVER
    assert parse_access_kind("Implicit  Read") is AccessKind.IMPLICIT_READ
    assert parse_access_kind("SYNC_SERVER_CALL") is AccessKind.SYNC_SERVER_CALL
    assert parse_trigger_kind("On Data Reception") is TriggerKind.DATA_RECEIVED
    assert parse_trigger_kind("") is None


def test_name_checker_reports_every_broken_rule() -> None:
    checker = NameChecker(NamingRules(max_length=5, port="Rp.*"))
    assert checker.check("RpOk", NameKind.PORT) == []
    assert [code for code, _ in checker.check("9toolong", NameKind.PORT)] == [
        c.NAME_SYNTAX,
        c.NAME_TOO_LONG,
        c.NAME_PATTERN,
    ]
    assert [code for code, _ in checker.check("while", NameKind.ELEMENT)] == [c.NAME_C_KEYWORD]
    assert checker.check("Pp", NameKind.INTERFACE) == []  # the port pattern is for ports only
    assert not checker.config_problems
    assert {"int", "while", "restrict"} <= C_KEYWORDS and "Int" not in C_KEYWORDS


def test_name_checker_survives_a_broken_project_pattern() -> None:
    checker = NameChecker(NamingRules(runnable="[unclosed"))
    assert [code for code, _ in checker.config_problems] == [c.CFG_NAME_REGEX]
    assert [code for code, _ in checker.check("RunX", NameKind.RUNNABLE)] == [c.NAME_PATTERN]
    assert checker.check("RpX", NameKind.PORT) == []


@pytest.mark.parametrize(
    ("bits", "encoding", "lower", "upper"),
    [
        (8, "NONE", 0, 255),
        (8, "2C", -128, 127),
        (16, None, -32768, 65535),
        (8, "BOOLEAN", 0, 1),
        (64, "none", 0, 2**64 - 1),
    ],
)
def test_base_range(bits: int, encoding: str | None, lower: int, upper: int) -> None:
    limits = base_range(BaseType("/b", "b", bits, encoding))
    assert limits is not None and (limits.lower, limits.upper) == (lower, upper)


def test_base_range_unknown_cases() -> None:
    assert base_range(BaseType("/b", "b", None, "NONE")) is None
    assert base_range(BaseType("/b", "b", 8, "BCD-P")) is None
    assert base_range(BaseType("/b", "b", 32, None)) is None  # could be a float
    assert base_range(BaseType("/b", "b", 16, "IEEE754")) is None
    assert fit_problem(-5, 10**30, BaseType("/b", "b", 8, "BCD-P")) == ""
    assert fit_problem(0, 2**64 - 1, BaseType("/b", "u64", 64, "NONE")) == ""
    assert "does not fit" in fit_problem(0, 2**64, BaseType("/b", "u64", 64, "NONE"))
    assert "wider" in fit_problem(-1, 65535, BaseType("/b", "u16", 16, None))
    assert fit_problem(-1e300, 1e300, BaseType("/b", "f64", 64, "IEEE-754")) == ""


def test_type_info_of_existing_types() -> None:
    percent = info_from_model(MODEL, f"{DT}/Percent")
    assert percent is not None
    assert (percent.lower, percent.upper, percent.integral) == (0.0, 100.0, False)
    limited = info_from_model(MODEL, f"{DT}/Limited")
    assert limited is not None and (limited.lower, limited.upper, limited.integral) == (
        10.0,
        20.0,
        True,
    )
    reference = info_from_model(MODEL, f"{DT}/TypeRef")
    assert reference is not None
    assert (reference.name, reference.category, reference.upper) == (
        "TypeRef",
        DataTypeCategory.VALUE,
        255,
    )
    state = info_from_model(MODEL, f"{DT}/EngineState")
    assert state is not None and state.enum_values == ((0, "OFF"), (1, "RUN"), (2, "FAULT"))
    assert info_from_model(MODEL, "/Missing/Type") is None


def test_init_value_problem_without_type_information() -> None:
    assert init_value_problem(None, "12") == ("", "")
    assert init_value_problem(None, "C_INIT") == ("", "")
    assert init_value_problem(None, "1 2")[0] == c.PORT_INIT_PARSE
    other = TypeInfo("/t", "t", DataTypeLevel.IMPLEMENTATION, DataTypeCategory.OTHER)
    assert init_value_problem(other, "0x1")[0] == ""
    text = TypeInfo("/t", "t", DataTypeLevel.IMPLEMENTATION, DataTypeCategory.STRING)
    assert init_value_problem(text, "abc")[0] == c.PORT_INIT_COMPOSITE
    bare_enum = TypeInfo("/t", "t", DataTypeLevel.IMPLEMENTATION, DataTypeCategory.ENUMERATION)
    assert init_value_problem(bare_enum, "ON")[0] == ""
    assert init_value_problem(bare_enum, "?")[0] == c.PORT_INIT_PARSE
    wide = TypeInfo(
        "/t", "u64", DataTypeLevel.IMPLEMENTATION, DataTypeCategory.VALUE, True, 0, 2**64 - 1
    )
    assert init_value_problem(wide, "18446744073709551615")[0] == ""
    assert init_value_problem(wide, "18446744073709551616")[0] == c.PORT_INIT_RANGE

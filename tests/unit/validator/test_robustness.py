"""No request, however malformed, may raise: every problem must come back as a Finding."""

from __future__ import annotations

import random
from types import MappingProxyType

import pytest

from porttool.core.pending import PendingQueue
from porttool.core.results import RawRequestRow, RowRef
from porttool.core.rowid import row_id
from porttool.core.sheets import COLUMNS, REQUEST_SHEETS, Sheet
from tests.unit.validator.rule_cases import CASES
from tests.unit.validator.support import ALPHA, BETA, MODEL, run

_WORDS = [
    "",
    "",
    "SwcA",
    "SwcB",
    "SwcC",
    "SwcD",
    "SwcM",
    "SwcL",
    "SwcSrv",
    "SwcDup",
    "EcuComposition",
    "PpSpeed",
    "RpSpeed",
    "RpTorque",
    "PpDiag",
    "RpDiag",
    "PrSpeed",
    "RpFree",
    "PpNew",
    "RpNew",
    "PpMode",
    "RpMode",
    "RpQueuedC",
    "PpDiagAlt",
    "RpMulti",
    "PpUnused",
    "RpGhost",
    "IfSpeed",
    "IfTorque",
    "IfDiag",
    "IfDiag2",
    "IfMode",
    "IfQueued",
    "IfMulti",
    "IfNew",
    "IfDup",
    "uint8",
    "uint16",
    "sint8",
    "float32",
    "boolean",
    "EngineState",
    "NewT",
    "RecA",
    "RecB",
    "Dup",
    "RunA_10ms",
    "RunB_OnSpeed",
    "RunC",
    "RunD",
    "RunM",
    "PpDiag_ReadDtc",
    "RunNew",
    "Speed",
    "Torque",
    "ReadDtc",
    "Clear",
    "Event",
    "A",
    "B",
    "currentMode",
    "Value",
    "P",
    "R",
    "PR",
    "X",
    "IN",
    "OUT",
    "INOUT",
    "Y",
    "N",
    "maybe",
    "sender-receiver",
    "client-server",
    "mode-switch",
    "parameter",
    "implementation",
    "application",
    "value",
    "enumeration",
    "array",
    "record",
    "boolean",
    "string",
    "implicit read",
    "implicit write",
    "explicit read",
    "explicit send",
    "queued receive",
    "synchronous server call",
    "mode access",
    "mode switch",
    "parameter access",
    "periodic",
    "on data reception",
    "on operation invocation",
    "on mode switch",
    "init",
    "Task_10ms",
    "Task_Event",
    "Task_Nope",
    "0",
    "1",
    "10",
    "-5",
    "0.5",
    "1e400",
    "0x10",
    "abc",
    "none",
    "A=1;B=2",
    "Speed=0",
    "0=OFF;1=ON",
    "0=A;0=B",
    "a:uint8;b:RecA",
    "x:RecB",
    "a:",
    "=;=",
    "EcuModes",
    "/Modes/EcuModes",
    "/ComponentTypes/SwcA",
    "/PortInterfaces/IfSpeed",
    "/nope",
    "SwcA_PpSpeed_SwcB_RpSpeed",
    "1Bad",
    "switch",
    "Ünïcode",
    " ",
    "{port}",
    "%s",
    "x" * 200,
]
_ACTIONS = ["ADD", "ADD", "ADD", "MODIFY", "DELETE", "delete", "", "UPSERT"]
_SHEETS = [sheet.value for sheet in REQUEST_SHEETS] * 3 + ["Components", "Results", "?"]


def _row_ids() -> list[str | None]:
    ids: list[str | None] = [None, None, None, "", "PT-000000000000", "garbage"]
    ids += [row_id(Sheet.PORTS, path) for path in list(MODEL.ports)[:12]]
    ids += [row_id(Sheet.CONNECTIONS, path) for path in MODEL.connectors]
    ids += [row_id(Sheet.RUNNABLE_ACCESS, path) for path in MODEL.access_points]
    ids += [row_id(Sheet.RUNNABLE_TRIGGERS, path) for path in MODEL.triggers]
    return ids


def _random_rows(seed: int, count: int) -> list[RawRequestRow]:
    rng = random.Random(seed)
    ids = _row_ids()
    rows: list[RawRequestRow] = []
    for number in range(count):
        sheet = rng.choice(_SHEETS)
        try:
            keys = [column.key for column in COLUMNS[Sheet(sheet)]]
        except (ValueError, KeyError):
            keys = ["swc", "port", "name"]
        values = {key: rng.choice(_WORDS) for key in keys if rng.random() < 0.85}
        rows.append(
            RawRequestRow(
                sheet=sheet,
                row=RowRef(rng.choice([ALPHA, BETA]), sheet, number + 2),
                row_id=rng.choice(ids),
                action=rng.choice(_ACTIONS),
                requester=rng.choice(["alice", "bob", ""]),
                comment="",
                values=MappingProxyType(values),
            )
        )
    return rows


@pytest.mark.parametrize("seed", range(12))
def test_random_rows_never_raise_and_keep_the_invariants(seed: int) -> None:
    rows = _random_rows(seed, 400)
    outcome = run(*rows)  # checks the invariants of the outcome
    assert len(outcome.results) == 400


def test_all_table_rows_in_one_import_never_raise() -> None:
    rows = [row for case in CASES for row in case.rows]
    unique = {entry.operation.id: entry for case in CASES for entry in case.pending}
    pending = PendingQueue(entries=tuple(unique.values()))
    outcome = run(*rows, pending=pending)
    assert len(outcome.results) == len(rows)
    assert run(*reversed(rows), pending=pending).change_set.created_utc

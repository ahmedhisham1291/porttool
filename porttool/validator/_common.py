"""Helpers shared by the sheet modules. Internal."""

from __future__ import annotations

from collections.abc import Hashable, Sequence

from porttool.core.sheets import COLUMNS, Sheet
from porttool.validator import codes
from porttool.validator._state import Req

_HEADERS: dict[Sheet, dict[str, str]] = {
    sheet: {column.key: column.header for column in columns} for sheet, columns in COLUMNS.items()
}

_MAX_LISTED = 3


def header(sheet: Sheet, key: str) -> str:
    """Header text of a column, as the user sees it in row 1."""
    return _HEADERS.get(sheet, {}).get(key, key)


def require(req: Req, sheet: Sheet, *keys: str) -> bool:
    """Reject the request for every empty cell among ``keys``. True when all are filled."""
    complete = True
    for key in keys:
        if not req.cell(key):
            req.error(
                codes.ROW_BLANK_CELL,
                f"Cell '{header(sheet, key)}' is empty. It is required for this request.",
            )
            complete = False
    return complete


def blank_required(req: Req, sheet: Sheet, key: str, reason: str) -> None:
    req.error(codes.ROW_BLANK_CELL, f"Cell '{header(sheet, key)}' is empty. {reason}")


def bad_cell(req: Req, sheet: Sheet, key: str, expected: str) -> None:
    req.error(
        codes.ROW_BAD_CELL,
        f"Cell '{header(sheet, key)}' contains '{req.cell(key)}'; expected {expected}.",
    )


def list_rows(reqs: Sequence[Req], skip: Req | None = None) -> str:
    """Up to three row descriptions, then a count of the rest."""
    others = [req for req in reqs if req is not skip]
    listed = "; ".join(req.where for req in others[:_MAX_LISTED])
    if len(others) > _MAX_LISTED:
        listed += f"; and {len(others) - _MAX_LISTED} more"
    return listed


def settle(members: Sequence[tuple[Req, Hashable]], what: str) -> Req | None:
    """Decide rows that request the same object.

    Identical requests are merged into the first row (returned as the leader). When the
    requests differ, every row is rejected and None is returned.
    """
    leader, signature = members[0]
    if len(members) == 1:
        return leader
    if any(other != signature for _, other in members[1:]):
        rows = [req for req, _ in members]
        for req in rows:
            req.error(
                codes.CFL_DIFFERENT,
                f"{what} is requested differently in {list_rows(rows, skip=req)}. All of "
                "these rows are rejected; agree on one definition.",
            )
        return None
    for req, _ in members[1:]:
        req.merge_into(leader)
    return leader


def reject_case_clashes(names: dict[str, list[Req]], what: str) -> None:
    """Reject rows whose new names differ only by letter case (``names``: name -> rows)."""
    by_lower: dict[str, list[str]] = {}
    for name in names:
        by_lower.setdefault(name.lower(), []).append(name)
    for spellings in by_lower.values():
        if len(spellings) < 2:
            continue
        for name in spellings:
            others = ", ".join(f"'{other}'" for other in spellings if other != name)
            for req in names[name]:
                req.error(
                    codes.NAME_CASE_CLASH,
                    f"{what} '{name}' differs only by letter case from {others}, which is "
                    "requested in the same import.",
                )

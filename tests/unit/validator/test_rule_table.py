"""Table-driven suite: every rule of the validator, at least once failing and once passing."""

from __future__ import annotations

import pytest

from porttool.core.pending import PendingQueue
from porttool.validator import ALL_CODES, CODES
from tests.unit.validator.rule_cases import CASES, Case
from tests.unit.validator.support import NOW, build_rich_config, codes_of, run, statuses


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_rule(case: Case) -> None:
    config = build_rich_config(**case.config)
    outcome = run(
        *case.rows,
        config=config,
        pending=PendingQueue(entries=case.pending),
        now=case.now or NOW,
    )
    found = codes_of(outcome)
    assert statuses(outcome) == case.expect, sorted(found)
    for code in case.fail:
        assert code in found, (
            f"{code} ({CODES[code].description}) was not reported: {sorted(found)}"
        )
    for code in case.ok:
        assert code not in found, f"{code} ({CODES[code].description}) was reported"


def test_table_has_at_least_150_cases_with_unique_names() -> None:
    names = [case.name for case in CASES]
    assert len(names) >= 150
    assert len(set(names)) == len(names)


def test_every_code_fails_at_least_once() -> None:
    failing = {code for case in CASES for code in case.fail}
    assert sorted(set(ALL_CODES) - failing) == []


def test_every_code_passes_at_least_once() -> None:
    passing = {code for case in CASES for code in case.ok}
    assert sorted(set(ALL_CODES) - passing) == []


def test_table_names_only_known_codes() -> None:
    used = {code for case in CASES for code in (*case.fail, *case.ok)}
    assert used <= set(ALL_CODES)

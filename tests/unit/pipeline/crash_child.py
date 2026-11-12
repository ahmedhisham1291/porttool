"""Child process of the kill tests: runs an apply and exits hard at a chosen point.

``python -m tests.unit.pipeline.crash_child <repo> <state dir> <home> <step> <phase>``

``phase`` is "start" or "end" of ``step``, or "file" to die in the middle of the write
(after the first file was written). Exit code 77 means "died where asked".
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tests.unit.pipeline.support import (
    FakeWriter,
    Repo,
    connector_row,
    git_env,
    make_service,
    port_row,
)

DIED = 77
ROWS = [
    port_row("Swc1", "PpCrash", "P", "IfSig1", 1),
    port_row("Swc2", "RpCrash", "R", "IfSig1", 2),
    connector_row("Swc1", "PpCrash", "Swc2", "RpCrash", 3),
    port_row("Swc3", "RpModel", "R", "IfSig1", 4),  # model-owned: pending file and package
]


def main(argv: list[str]) -> int:
    root, state, home, step, phase = Path(argv[0]), Path(argv[1]), Path(argv[2]), argv[3], argv[4]
    os.environ["LOCALAPPDATA"] = str(state / "appdata")

    def hook(name: str, moment: str) -> None:
        if name == step and moment == phase:
            os._exit(DIED)

    writer = FakeWriter()
    if phase == "file":
        writer.on_file = lambda _rel: os._exit(DIED)
    repo = Repo(root=root, env=git_env(home), truth=None)  # type: ignore[arg-type]
    harness = make_service(repo, state, writer=writer, step_hook=hook)
    harness.dry_run(ROWS)
    view = harness.apply()
    print(view.state, view.summary, view.error)
    harness.service.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

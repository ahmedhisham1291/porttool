"""Shared helpers of the davinci tests: a job context, configs and fake-tool templates."""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable, Coroutine, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from porttool.core.config import ProjectConfig, ToolTimeouts
from porttool.core.errors import JobCancelled
from tests.fakes import FAKE_SCRIPT, fake_env

_R = TypeVar("_R")
WAIT = 30.0  # generous: the sandbox has two shared cores


class FakeCtx:
    """A JobContext for tests: records progress, cancels on request."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.cancel = threading.Event()
        self.cancel_after_messages: int | None = None

    def progress(self, done: int, total: int, message: str = "") -> None:
        self.messages.append(message)
        if (
            self.cancel_after_messages is not None
            and len(self.messages) >= self.cancel_after_messages
        ):
            self.cancel.set()

    def check_cancelled(self) -> None:
        if self.cancel.is_set():
            raise JobCancelled()

    async def run_blocking(self, fn: Callable[[], _R]) -> _R:
        return await asyncio.get_running_loop().run_in_executor(None, fn)


def run(coroutine: Coroutine[Any, Any, _R]) -> _R:
    return asyncio.run(asyncio.wait_for(coroutine, WAIT))


def fake_vars(**options: Any) -> dict[str, str]:
    """Only the FAKE_DV_* variables of ``fake_env(...)``, for the [davinci.env] table."""
    env = fake_env(base={}, **options)
    return {key: value for key, value in env.items() if key.startswith("FAKE_DV_")}


def fake_template(tool: str, *args: str) -> list[str]:
    """A command template that starts the fake ``tool`` with the running Python."""
    return [sys.executable, str(FAKE_SCRIPT), "--tool", tool, *args]


def fake_commands() -> dict[str, list[str]]:
    """Templates for every action, each one running the matching fake tool."""
    return {
        "check": fake_template("DVWspChecker", "-w", "{dcf}"),
        "import": fake_template("DVImEx", "-w", "{dcf}", "-i", "{arxml}"),
        "contract_headers": fake_template("DVSwcGen", "-w", "{dcf}", "-c", "{swcs}"),
        "script_task": fake_template(
            "DVCfgCmd", "-p", "{dpa}", "-s", "{script_task}", "--req", "{request_json}"
        ),
        "validate": fake_template("DVCfgCmd", "-p", "{dpa}", "--validate"),
        "generate": fake_template("DVCfgCmd", "-p", "{dpa}", "-g", "-m", "{modules_csv}"),
    }


def make_repo(tmp_path: Path) -> Path:
    """A repo folder with an (empty) workspace file and project file."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / "Workspace.dcf").write_text("dcf", encoding="utf-8")
    (repo / "Project.dpa").write_text("dpa", encoding="utf-8")
    return repo


def make_config(
    tmp_path: Path,
    davinci: Mapping[str, object] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    commands: Mapping[str, Sequence[str]] | None = None,
    timeouts: ToolTimeouts | None = None,
    pipeline: Mapping[str, object] | None = None,
    **config_fields: Any,
) -> ProjectConfig:
    """A ProjectConfig whose [davinci] table runs the fake tools unless told otherwise."""
    repo = make_repo(tmp_path)
    table: dict[str, object] = {"log_dir": str(tmp_path / "logs")}
    table["commands"] = dict(fake_commands() if commands is None else commands)
    if env is not None:
        table["env"] = dict(env)
    if davinci is not None:
        table.update(davinci)
    extras: dict[str, Mapping[str, object]] = {"davinci": table}
    if pipeline is not None:
        extras["pipeline"] = pipeline
    fields: dict[str, Any] = {
        "repo_root": repo,
        "dcf_file": repo / "Workspace.dcf",
        "dpa_file": repo / "Project.dpa",
        "timeouts": timeouts or ToolTimeouts(),
        "extras": MappingProxyType(extras),
    }
    fields.update(config_fields)
    return ProjectConfig(**fields)


def codes(findings: Sequence[Any]) -> list[str]:
    return [finding.code for finding in findings]

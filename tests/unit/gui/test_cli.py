"""``porttool gui`` with an injected fake service factory."""

from __future__ import annotations

import re
import socket
import threading
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import porttool.cli
import porttool.gui.cli as gui_cli
from porttool.gui import GuiError, ServerHandle, run_forever, serve
from tests.unit.gui.conftest import Client
from tests.unit.gui.fake_service import FakeService


class Recorder:
    """Stands in for ``run_forever``: looks at the running server, then returns."""

    def __init__(self) -> None:
        self.handle: ServerHandle | None = None
        self.state: dict[str, object] = {}
        self.page_status = 0

    def __call__(self, handle: ServerHandle) -> None:
        self.handle = handle
        client = Client(handle)
        self.state = client.ok("/api/state")
        self.page_status = client.get("/", token=None).status


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The addresses the command tried to open in a browser (no browser is started)."""
    urls: list[str] = []

    def fake_open(url: str) -> bool:
        urls.append(url)
        return True

    monkeypatch.setattr(gui_cli, "open_in_browser", fake_open)
    return urls


def run(service: FakeService, recorder: Recorder, *args: str) -> click.testing.Result:
    group = click.Group()
    group.add_command(gui_cli.make_gui_command(lambda: service, recorder))
    return CliRunner().invoke(group, ["gui", *args])


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_gui_command_serves_prints_the_address_and_cleans_up(opened: list[str]) -> None:
    service = FakeService(loaded=False)
    recorder = Recorder()
    result = run(service, recorder)
    assert result.exit_code == 0, result.output
    handle = recorder.handle
    assert handle is not None
    assert f"PortTool is running at {handle.url}" in result.output
    assert re.search(r"http://127\.0\.0\.1:\d+/\?token=\S{20,}", result.output)
    assert "Ctrl+C" in result.output and "PortTool stopped." in result.output
    assert opened == [handle.url]
    assert recorder.page_status == 200 and recorder.state["config_present"] is False
    assert handle.closed and not handle.temp_dir.exists()
    assert service.closed
    assert "open_repo" not in service.calls


def test_gui_command_no_browser(opened: list[str]) -> None:
    result = run(FakeService(loaded=False), Recorder(), "--no-browser")
    assert result.exit_code == 0 and opened == []


def test_gui_command_says_when_the_browser_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gui_cli, "open_in_browser", lambda url: False)
    result = run(FakeService(loaded=False), Recorder())
    assert result.exit_code == 0
    assert "open the address above by hand" in result.output


def test_gui_command_opens_the_repo_and_prints_findings(opened: list[str], tmp_path: Path) -> None:
    service = FakeService(loaded=False)
    recorder = Recorder()
    result = run(service, recorder, "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert "INFO CFG-I001: porttool.toml was read." in result.output
    assert service.calls[0] == "open_repo"
    assert recorder.state["repo"] == str(tmp_path) and recorder.state["config_present"] is True


def test_gui_command_uses_the_given_port(opened: list[str]) -> None:
    port = free_port()
    recorder = Recorder()
    result = run(FakeService(loaded=False), recorder, "--port", str(port))
    assert result.exit_code == 0, result.output
    assert recorder.handle is not None and recorder.handle.port == port
    assert f"http://127.0.0.1:{port}/?token=" in result.output


def test_gui_command_uses_the_port_of_the_gui_settings(opened: list[str], tmp_path: Path) -> None:
    port = free_port()
    recorder = Recorder()
    service = FakeService(loaded=False, gui={"port": port})
    result = run(service, recorder, "--repo", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert recorder.handle is not None and recorder.handle.port == port


def test_gui_command_rejects_a_bad_port_option() -> None:
    result = run(FakeService(loaded=False), Recorder(), "--port", "70000")
    assert result.exit_code == 2 and "70000" in result.output


def test_gui_command_closes_the_service_when_the_repo_cannot_be_opened(tmp_path: Path) -> None:
    service = FakeService(loaded=False)
    recorder = Recorder()
    result = run(service, recorder, "--repo", str(tmp_path / "missing"))
    assert result.exit_code != 0 and recorder.handle is None
    assert service.closed


def test_root_command_line_reports_a_port_in_use_with_exit_code_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, opened: list[str]
) -> None:
    service = FakeService(loaded=False)
    monkeypatch.setattr(gui_cli, "_real_service", lambda: service)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        port = busy.getsockname()[1]
        arguments = ["--log-dir", str(tmp_path), "gui", "--port", str(port), "--no-browser"]
        result = CliRunner().invoke(porttool.cli.main, arguments)
    assert result.exit_code == 2, result.output
    assert "[PT-E700]" in result.output and f"127.0.0.1:{port}" in result.output
    assert "Traceback" not in result.output
    assert service.closed


def test_root_command_line_lists_the_gui_command() -> None:
    result = CliRunner().invoke(porttool.cli.main, ["--help"])
    assert result.exit_code == 0 and re.search(r"^\s+gui\s", result.output, re.MULTILINE)
    result = CliRunner().invoke(porttool.cli.main, ["gui", "--help"])
    assert "--repo" in result.output and "--port" in result.output
    assert "--no-browser" in result.output


def test_real_service_needs_the_pipeline_package(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_pipeline(name: str) -> object:
        raise ImportError(f"No module named '{name}'")

    monkeypatch.setattr(gui_cli, "_import_module", no_pipeline)
    with pytest.raises(GuiError, match="pipeline package"):
        gui_cli._real_service()


def test_real_service_calls_build_service(monkeypatch: pytest.MonkeyPatch) -> None:
    service = FakeService(loaded=False)

    class Pipeline:
        @staticmethod
        def build_service() -> FakeService:
            return service

    monkeypatch.setattr(gui_cli, "_import_module", lambda name: Pipeline)
    assert gui_cli._real_service() is service
    monkeypatch.setattr(gui_cli, "_import_module", lambda name: object())
    with pytest.raises(GuiError, match="build_service"):
        gui_cli._real_service()


def test_run_forever_returns_when_the_server_is_closed_elsewhere() -> None:
    service = FakeService(loaded=False)
    handle = serve(service, open_browser=False)
    threading.Timer(0.2, handle.close).start()
    run_forever(handle)
    assert handle.closed


def test_run_forever_stops_on_ctrl_c(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = serve(FakeService(loaded=False), open_browser=False)

    def interrupted(timeout: float | None = None) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(handle, "wait", interrupted)
    run_forever(handle)  # must not raise
    assert handle.closed and not handle.temp_dir.exists()
